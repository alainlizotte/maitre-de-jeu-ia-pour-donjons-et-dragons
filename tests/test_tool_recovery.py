"""Tests de la couche de récupération des appels d'outils (sans LLM réel).

Reproduit les pannes observées dans les parties a1a80eb3 et 7754279d :
- Gemma « narre » l'appel en syntaxe fonctionnelle (`outil(...)` en backticks
  ou en gras) au lieu d'émettre un tool_calls natif → le tour se termine sans
  que le jet/donjon n'ait jamais existé (partie bloquée, carte muette).
- Placeholders non numériques (« Calculé sur la fiche », « N »).
- Blocs <tool_call> texte (llama.cpp sans --jinja).

Usage : py -m pytest tests/test_tool_recovery.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    Orchestrator,
    extract_toolcall_attr_calls,
    extract_toolcall_blocks,
    looks_like_simulation,
    parse_prose_tool_calls,
    resolve_tool_name,
    strip_narration_artifacts,
)
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")


# --------------------------------------------------------------------------- #
#  Faux client LLM : rejoue les comportements pathologiques observés
# --------------------------------------------------------------------------- #
class ScriptedClient:
    """Renvoie successivement les réponses fournies (contenu OU tool_calls)."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        content = self.replies.pop(0) if self.replies else "(fin)"
        self.calls += 1
        return ChatResult(
            content=content, tool_calls=[], finish_reason="stop", raw={},
        )

    async def stream_chat(self, messages, tools=None, temperature=None):
        for tok in (self.replies.pop(0) if self.replies else "(fin)").split(" "):
            yield tok + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def _ctx(data_dir: str) -> ToolContext:
    return ToolContext(partie_id="test_recup", joueur="alain", data_dir=data_dir)


# --------------------------------------------------------------------------- #
#  Parsers
# --------------------------------------------------------------------------- #
def test_prose_call_bold_gemma():
    txt = (
        "Le jet de compétence est nécessaire. "
        '**Lancer_d20(nom_personnage="Sylvaris Arcanor", competence="Langue Ancienne", '
        'difficulte="15", modificateur="5", raison="Déchiffrer les glyphes")**\n\n'
        "*(Attente du résultat du jet de dés)*"
    )
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert len(calls) == 1
    assert calls[0]["name"] == "lancer_d20"
    assert calls[0]["arguments"]["nom_personnage"] == "Sylvaris Arcanor"
    # difficulte/modificateur restent des chaînes numériques valides.
    assert calls[0]["arguments"]["difficulte"] == "15"


def test_prose_call_backticks_donjon():
    txt = 'Vous entrez dans la grotte.\n\n`carte_donjon_entrer(donjon_id="Grotte du Gobelin")`\n\nLe passage est étroit.'
    calls, cleaned = parse_prose_tool_calls(txt, TOOLS)
    assert len(calls) == 1
    assert calls[0]["name"] == "carte_donjon_entrer"
    assert calls[0]["arguments"] == {"donjon_id": "Grotte du Gobelin"}
    assert "carte_donjon_entrer" not in cleaned
    assert "Le passage est étroit." in cleaned


def test_prose_call_placeholder_args_dropped():
    txt = (
        '`lancer_attaque(nom_attaquant="Sylvaris", nom_cible="Gobelin", '
        'arme="Dague", bonus_attaque="Calculé sur la fiche", ca_cible="Calculée")`'
    )
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert calls[0]["arguments"].get("nom_cible") == "Gobelin"
    assert "bonus_attaque" not in calls[0]["arguments"]
    assert "ca_cible" not in calls[0]["arguments"]


def test_prose_call_placeholder_N_dropped():
    calls, _ = parse_prose_tool_calls(
        '`fiche_perso_infliger_degats(nom="Sylvaris Arcanor", degats=N)`', TOOLS
    )
    assert "degats" not in calls[0]["arguments"]


def test_prose_call_escaped_quotes_and_parens():
    txt = (
        '`lancer_d20(nom_personnage="Brunhild", raison="Escalade d\'un mur (de 6 m) \\"glissant\\"", '
        'difficulte=15, modificateur=3)`'
    )
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert len(calls) == 1
    assert calls[0]["name"] == "lancer_d20"
    assert calls[0]["arguments"]["raison"] == 'Escalade d\'un mur (de 6 m) "glissant"'
    assert calls[0]["arguments"]["difficulte"] == 15


def test_prose_no_false_positive():
    for txt in (
        "Le prêtre lance un sort de soins (guérison rapide).",
        "Il ouvre la porte (qui grince) et avance.",
        "Roll20 est un site web connu.",
        "La fonction principale (du jeu) est de s'amuser.",
    ):
        assert parse_prose_tool_calls(txt, TOOLS)[0] == []


def test_resolve_fuzzy_names():
    assert resolve_tool_name("Lancer_d20", TOOLS) == "lancer_d20"
    assert resolve_tool_name("Lancer_Attaque", TOOLS) == "lancer_attaque"
    assert resolve_tool_name("lancer_dés", TOOLS) == "lancer_des"
    assert resolve_tool_name("infliger_degats", TOOLS) == "fiche_perso_infliger_degats"
    assert resolve_tool_name("n_importe_quoi_xyz", TOOLS) is None


def test_toolcall_blocks_llamacpp():
    txt = (
        'Je lance le jet.\n<tool_call>\n'
        '{"name": "lancer_d20", "arguments": {"modificateur": 2, "raison": "Fouille"}}\n'
        "</tool_call>"
    )
    calls, cleaned = extract_toolcall_blocks(txt)
    assert calls == [{
        "name": "lancer_d20",
        "arguments": {"modificateur": 2, "raison": "Fouille"},
    }]
    assert "<tool_call>" not in cleaned


def test_toolcall_attr_style_parsed():
    # Panne observée en partie réelle : balise auto-fermée avec arguments en
    # attributs XML, qui fuyait telle quelle dans la narration du MJ.
    txt = (
        "Vous sortez votre kit de cambrioleur, prenant le temps d'inspecter "
        "la serrure et les gonds de la porte.\n\n"
        "Vous lancez un test de Crochetage pour tenter de neutraliser la serrure.\n\n"
        '<tool_call name="lancer_d20" competence="Crochetage" difficulte="30" '
        'modificateur="carac.DEX + rang.Crochetage" nom_personnage="Sylvaris Arcanor" '
        'raison="Crocheter la serrure de la porte rouillée" />'
    )
    calls, cleaned = extract_toolcall_attr_calls(txt)
    assert len(calls) == 1
    assert calls[0]["name"] == "lancer_d20"
    assert calls[0]["arguments"]["competence"] == "Crochetage"
    assert calls[0]["arguments"]["difficulte"] == "30"
    assert calls[0]["arguments"]["nom_personnage"] == "Sylvaris Arcanor"
    assert calls[0]["arguments"]["raison"] == "Crocheter la serrure de la porte rouillée"
    assert "<tool_call" not in cleaned
    assert "kit de cambrioleur" in cleaned


def test_toolcall_attr_style_pair_and_orphan():
    # Variante avec fermeture explicite (corps vide).
    txt = '<tool_call name="lancer_des" nb="2" faces="6" raison="Dégâts" ></tool_call>'
    calls, cleaned = extract_toolcall_attr_calls(txt)
    assert len(calls) == 1
    assert calls[0]["name"] == "lancer_des"
    assert "<tool_call" not in cleaned
    # Filet strip : fermeture orpheline seule.
    out = strip_narration_artifacts("Texte puis </tool_call> et voilà.")
    assert "tool_call" not in out
    assert "et voilà" in out


def test_simulation_new_patterns():
    for s in (
        "*(Appel au tool lancer_attaque pour la dague)*",
        "*(Attente du résultat du jet de dés)*",
        "*(Le résultat du jet est appliqué et les dégâts sont calculés.)*",
        "*(Appel au sort)*",
    ):
        assert looks_like_simulation(s), s


def test_strip_narration_artifacts():
    full = (
        "Le Gobelin charge ! `lancer_attaque(nom_attaquant=\"Gobelin\", "
        'nom_cible="Sylvaris", arme="Hache")`\n\n'
        "*(Le jet d'attaque est lancé. Le Gobelin frappe.)*\n\n"
        "La lame s'abat contre votre garde."
    )
    out = strip_narration_artifacts(full, TOOLS)
    assert "lancer_attaque" not in out
    assert "jet d'attaque est lancé" not in out
    assert "La lame s'abat contre votre garde." in out
    # Balise <tool_call .../> résiduelle : jamais visible par le joueur.
    full2 = (
        "La porte rouillée cède enfin.\n\n"
        '<tool_call name="lancer_d20" competence="Crochetage" difficulte="30" />'
    )
    out2 = strip_narration_artifacts(full2, TOOLS)
    assert "<tool_call" not in out2
    assert "porte rouillée" in out2


# --------------------------------------------------------------------------- #
#  Boucle complète : prose → exécution réelle → narration propre
# --------------------------------------------------------------------------- #
def _run_scripted(replies: list[str], data_dir: str, mode: str = "auto"):
    orch = Orchestrator(
        client=ScriptedClient(replies),
        tools=TOOLS,
        tool_mode=mode,
        detect_simulation=True,
        max_iterations=6,
    )
    messages = [Message(role="system", content="Tu es le MJ.")]
    return asyncio.run(orch.run(messages, _ctx(data_dir)))


def test_loop_recovers_donjon_enter(tmp_path=None):
    data_dir = tempfile.mkdtemp(prefix="dnd35_recup_")
    try:
        # 1er appel : le modèle « narre » l'entrée du donjon (cas 7754279d).
        # 2e appel : narration finale propre après le résultat du tool.
        result = _run_scripted([
            'Vous entrez dans la grotte.\n\n`carte_donjon_entrer(donjon_id="Grotte du Gobelin")`\n\n'
            "Le passage est étroit et humide.",
            "Vous pénétrez dans la Grotte du Gobelin. L'obscurité vous avale.",
        ], data_dir)
        noms = [t["name"] for t in result.tool_calls_trace]
        assert "carte_donjon_entrer" in noms
        assert result.tool_calls_trace[0]["ok"]
        assert "carte_donjon_entrer(" not in result.narration
        # Le donjon a VRAIMENT été ouvert dans l'état persistant.
        import json
        etat = json.load(open(
            os.path.join(data_dir, "partie_test_recup.json"), encoding="utf-8",
        ))
        assert etat["donjon"]["id"] == "Grotte du Gobelin"
        assert any(
            p.get("donjon") for p in result.state_patches
        )
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_loop_recovers_d20_and_cleans(tmp_path=None):
    data_dir = tempfile.mkdtemp(prefix="dnd35_recup2_")
    try:
        result = _run_scripted([
            "Vous examinez le cercle de pierres.\n\n"
            '**Lancer_d20(nom_personnage="Sylvaris Arcanor", competence="Langue Ancienne", '
            'difficulte="15", modificateur="5", raison="Déchiffrer")**\n\n'
            "*(Attente du résultat du jet de dés)*",
            "Les glyphes s'éclairent brièvement : la magie opère.",
        ], data_dir)
        noms = [t["name"] for t in result.tool_calls_trace]
        assert "lancer_d20" in noms
        assert result.tool_calls_trace[0]["ok"]
        assert "Lancer_d20(" not in result.narration
        assert "Attente" not in result.narration
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_loop_recovers_toolcall_attr_style(tmp_path=None):
    data_dir = tempfile.mkdtemp(prefix="dnd35_recup4_")
    try:
        # Mode "prompt" = défaut de prod (config.py). 1er appel : le modèle
        # émet la balise attributs XML au lieu du format documenté. 2e : la
        # narration finale propre à partir du VRAI résultat du tool.
        result = _run_scripted([
            "Vous sortez votre kit de cambrioleur, prenant le temps d'inspecter "
            "la serrure et les gonds de la porte.\n\n"
            "Vous lancez un test de Crochetage pour tenter de neutraliser la serrure.\n\n"
            '<tool_call name="lancer_d20" competence="Crochetage" difficulte="30" '
            'modificateur="carac.DEX + rang.Crochetage" nom_personnage="Sylvaris Arcanor" '
            'raison="Crocheter la serrure de la porte rouillée" />',
            "Le crochet glisse dans le mécanisme : les déclics se succèdent "
            "et la serrure finit par céder.",
        ], data_dir, mode="prompt")
        noms = [t["name"] for t in result.tool_calls_trace]
        assert "lancer_d20" in noms
        assert result.tool_calls_trace[0]["ok"]
        # Le placeholder non numérique « carac.DEX + rang.Crochetage » est
        # droppé par sanitize, le tool recalcule depuis la fiche.
        assert "carac.DEX" not in str(result.tool_calls_trace[0]["args"])
        assert "<tool_call" not in result.narration
        assert "crochet" in result.narration.lower()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_loop_recovers_placeholder_attack():
    data_dir = tempfile.mkdtemp(prefix="dnd35_recup3_")
    try:
        result = _run_scripted([
            'Vous frappez ! `lancer_attaque(nom_attaquant="Brunhild", nom_cible="Gobelin", '
            'arme="Dague", bonus_attaque="Calculé sur la fiche de Brunhild", '
            'ca_cible="Calculée sur la fiche du Gobelin")`',
            "Votre dague vole vers la créature.",
        ], data_dir)
        assert "lancer_attaque" in [t["name"] for t in result.tool_calls_trace]
        assert result.tool_calls_trace[0]["ok"]
        # Le tool a tourné SANS crasher malgré les placeholders.
        assert "Attaque" in result.tool_calls_trace[0]["text"]
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            if "tmp_path" in fn.__code__.co_varnames:
                fn(tmp_path=None)
            else:
                fn()
            print(f"✅ {name}")
    print("\n🎉 Tous les tests de récupération passent.")
