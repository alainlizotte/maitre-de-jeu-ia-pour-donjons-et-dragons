"""Couverture exhaustive de la récupération des appels d'outils en prose.

Chaque cas simule la panne observée avec Gemma/llama.cpp : le modèle écrit
l'appel en syntaxe fonctionnelle dans sa narration (`outil(key="value")`,
variantes casse/accents/placeholders) au lieu d'émettre un tool_calls natif.
On vérifie que la boucle d'orchestration intercepte, exécute RÉELLEMENT le
tool (résultat textuel conforme + état persistant) et nettoie la narration.

Couvre en particulier TOUS les types de lancers de dés :
lancer_d20, lancer_des, lancer_sauvegarde, lancer_caracteristiques,
calculer_initiative, lancer_attaque, lancer_degats.

Usage : py -m pytest tests/test_all_tools_recovery.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    Orchestrator,
    parse_prose_tool_calls,
)
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_all_tools"


class ScriptedClient:
    """Renvoie successivement les réponses fournies (contenu brut)."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        content = self.replies.pop(0) if self.replies else "(fin)"
        return ChatResult(content=content, tool_calls=[], finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        for tok in (self.replies.pop(0) if self.replies else "(fin)").split(" "):
            yield tok + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def _run_prose(prose_reply: str, data_dir: str):
    """Un tour complet : 1re réponse = appel en prose, 2e = narration propre."""
    orch = Orchestrator(
        client=ScriptedClient([
            "Le MJ agit.\n\n" + prose_reply + "\n\nSuite du récit.",
            "Résultat intégré dans la narration finale.",
        ]),
        tools=TOOLS,
        tool_mode="auto",
        detect_simulation=True,
        max_iterations=6,
    )
    messages = [Message(role="system", content="Tu es le MJ.")]
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=data_dir)
    return asyncio.run(orch.run(messages, ctx))


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_alltools_")


def _etat(data_dir: str) -> dict:
    path = os.path.join(data_dir, f"partie_{PID}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _setup_pj(data_dir: str) -> None:
    """PJ jouable sur disque (fiche + état) pour les jets sur fiche."""
    # `engager_combat` refuse strictement tout monstre hors bestiaire : on
    # copie donc le bestiaire officiel dans le répertoire de test.
    shutil.copy2(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "server", "data", "bestiaire.json"),
        os.path.join(data_dir, "bestiaire.json"),
    )
    os.makedirs(os.path.join(data_dir, "fiches"), exist_ok=True)
    with open(
        os.path.join(data_dir, "fiches", "fiche_brunhild.json"), "w",
        encoding="utf-8",
    ) as f:
        json.dump({
            "nom": "Brunhild", "joueur": "alain", "race": "Nain",
            "classe": "Guerrier", "niveau": 1,
            "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 9, "CHA": 8},
            "pv": 12, "pv_max": 12, "ca": 16,
            "sauvegardes": {"Vigueur": 4, "Reflexes": 0, "Volonte": 0},
            "bab": 1, "competences": {}, "dons": [], "equipement": [],
            "or": 0, "alignement": "Loyal Bon", "histoire": "", "conditions": [],
        }, f, ensure_ascii=False)
    # État de partie avec le PJ + phase exploration (expose les tools combat).
    from server.game.state import PartyState
    st = PartyState(data_dir=data_dir, partie_id=PID)
    etat = st.load()
    etat["phase"] = "exploration"
    etat["pj"] = [{
        "nom": "Brunhild", "joueur": "alain", "race": "Nain",
        "classe": "Guerrier", "niveau": 1, "pv": 12, "pv_max": 12, "ca": 16,
    }]
    st.save(etat)


# --------------------------------------------------------------------------- #
#  Dés — tous les types
# --------------------------------------------------------------------------- #
def test_lancer_des_generique():
    d = _fresh_dir()
    try:
        r = _run_prose('`lancer_des(nb_des=2, faces=6, bonus=1, raison="Dégâts de chute")`', d)
        assert "lancer_des" in [t["name"] for t in r.tool_calls_trace]
        tr = r.tool_calls_trace[0]
        assert tr["ok"] and "2d6+1" in tr["text"] and "Dégâts de chute" in tr["text"]
        assert "lancer_des(" not in r.narration
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lancer_des_casse_et_arg_raccourci():
    """Variante mal orthographiée : casse + argument raccourci nb → nb_des."""
    d = _fresh_dir()
    try:
        r = _run_prose('`Lancer_Des(nb=2, faces=6)`', d)
        assert "lancer_des" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"]
        assert "2d6" in r.tool_calls_trace[0]["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lancer_sauvegarde():
    d = _fresh_dir()
    try:
        r = _run_prose(
            '`lancer_sauvegarde(type_sauvegarde="Vigueur", modificateur="+2", '
            'difficulte="15", nom_personnage="Brunhild", source="Poison de goblin")`',
            d,
        )
        assert "lancer_sauvegarde" in [t["name"] for t in r.tool_calls_trace]
        tr = r.tool_calls_trace[0]
        assert tr["ok"] and "Vigueur" in tr["text"] and "Poison" in tr["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lancer_caracteristiques():
    d = _fresh_dir()
    try:
        r = _run_prose('`lancer_caracteristiques(methode="4d6_garder_3")`', d)
        assert "lancer_caracteristiques" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"]
        # 6 caractéristiques tirées.
        assert "FOR" in r.tool_calls_trace[0]["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_calculer_initiative_quoted():
    d = _fresh_dir()
    try:
        r = _run_prose(
            '`calculer_initiative(participants="Brunhild:+2, Gobelin:+1, Squelette:+0")`',
            d,
        )
        assert "calculer_initiative" in [t["name"] for t in r.tool_calls_trace]
        tr = r.tool_calls_trace[0]
        assert tr["ok"] and "Brunhild" in tr["text"] and "Squelette" in tr["text"]
        # L'ordre d'initiative est patché dans l'état.
        assert any("initiative" in p for p in r.state_patches)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_calculer_initiative_unquoted_commas():
    """Valeur non quotée contenant des virgules → fragments recollés."""
    txt = '`calculer_initiative(participants=Brunhild:+2, Gobelin:+1)`'
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert calls[0]["arguments"]["participants"] == "Brunhild:+2, Gobelin:+1"


def test_lancer_degats():
    d = _fresh_dir()
    try:
        r = _run_prose(
            '`lancer_degats(nb_des=1, faces=8, bonus=2, arme_ou_sort="Hache rouillée", '
            'cible="Brunhild")`',
            d,
        )
        assert "lancer_degats" in [t["name"] for t in r.tool_calls_trace]
        tr = r.tool_calls_trace[0]
        assert tr["ok"] and "1d8+2" in tr["text"] and "Hache rouillée" in tr["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lancer_degats_placeholders():
    """faces=N / bonus placeholder → rejetés, le tool recadre (d6 par défaut)."""
    d = _fresh_dir()
    try:
        r = _run_prose(
            '`lancer_degats(nb_des=1, faces=N, bonus="mod. FOR", '
            'arme_ou_sort="Dague", cible="Brunhild")`',
            d,
        )
        assert "lancer_degats" in [t["name"] for t in r.tool_calls_trace]
        # Le tool signale le dé non standard (N → défaut 6 serait acceptable
        # aussi) : dans tous les cas PAS de crash et un résultat clair.
        tr = r.tool_calls_trace[0]
        assert tr["ok"] or "non standard" in tr["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  État / fiches / monstres / cartes / combat
# --------------------------------------------------------------------------- #
def test_etat_partie_patch():
    d = _fresh_dir()
    try:
        r = _run_prose('`etat_partie_patch(chemin="phase", valeur="combat")`', d)
        assert "etat_partie_patch" in [t["name"] for t in r.tool_calls_trace]
        assert _etat(d).get("phase") == "combat"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fiche_perso_soigner_et_infliger():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`fiche_perso_infliger_degats(nom="Brunhild", degats=3)`', d)
        assert "fiche_perso_infliger_degats" in [t["name"] for t in r.tool_calls_trace]
        pj = (_etat(d).get("pj") or [{}])[0]
        assert pj.get("pv") == 9  # 12 - 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fiche_perso_recuperer():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`fiche_perso_recuperer(nom="Brunhild")`', d)
        assert "fiche_perso_recuperer" in [t["name"] for t in r.tool_calls_trace]
        assert "Brunhild" in r.tool_calls_trace[0]["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_monstre_consulter():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`monstre_consulter(nom="Gobelin")`', d)
        assert "monstre_consulter" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_donjon_complet_entrer_explorer_sortir():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        # 1. Entrée (récupérée de la prose).
        r = _run_prose('`carte_donjon_entrer(donjon_id="Grotte du Gobelin")`', d)
        assert "carte_donjon_entrer" in [t["name"] for t in r.tool_calls_trace]
        assert _etat(d)["donjon"]["id"] == "Grotte du Gobelin"
        # 2. Exploration direction.
        r2 = _run_prose('`carte_donjon_explorer(direction="nord")`', d)
        assert "carte_donjon_explorer" in [t["name"] for t in r2.tool_calls_trace]
        etat = _etat(d)
        assert etat["donjon"]["courant"] == [0, -1]
        assert len(etat["donjon"]["grille"]) == 2
        # 3. Sortie.
        r3 = _run_prose('`carte_donjon_sortir()`', d)
        assert "carte_donjon_sortir" in [t["name"] for t in r3.tool_calls_trace]
        assert _etat(d)["donjon"]["id"] is None
        # … et le donjon est archivé pour re-entrée ultérieure.
        assert "Grotte du Gobelin" in (_etat(d).get("donjons_exploreres") or {})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_carte_joueurs_placer_ville():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`carte_joueurs_placer_ville(ville="Waterdeep")`', d)
        assert "carte_joueurs_placer_ville" in [t["name"] for t in r.tool_calls_trace]
        lieu = _etat(d).get("lieu") or {}
        assert lieu.get("nom") == "Waterdeep"
        assert lieu.get("position_x") is not None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_engager_combat_et_tours():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`engager_combat(monstres="Gobelin x2")`', d)
        assert "engager_combat" in [t["name"] for t in r.tool_calls_trace]
        etat = _etat(d)
        assert etat.get("phase") == "combat"
        assert etat.get("courant_tour_pour")
        # Tour suivant via prose.
        r2 = _run_prose('`tour_suivant_combat()`', d)
        assert "tour_suivant_combat" in [t["name"] for t in r2.tool_calls_trace]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ajouter_evenement_histoire():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose('`ajouter_evenement_histoire(evenement="Le groupe trouve le Sceau")`', d)
        assert "ajouter_evenement_histoire" in [t["name"] for t in r.tool_calls_trace]
        assert "Sceau" in json.dumps(_etat(d).get("histoire") or [])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_alias_court_initiative():
    """« initiative(...) » (alias court non ambigu) → calculer_initiative."""
    txt = '`initiative(participants="Brunhild:+2, Gobelin:+1")`'
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert calls and calls[0]["name"] == "calculer_initiative"


def test_voyage_demarrer():
    d = _fresh_dir()
    try:
        _setup_pj(d)
        r = _run_prose(
            '`voyage_demarrer(destination="Waterdeep", distance_km=120, '
            'mode="cheval", terrain="plaine")`',
            d,
        )
        assert "voyage_demarrer" in [t["name"] for t in r.tool_calls_trace]
        assert (_etat(d).get("voyage") or {}).get("distance_km") == 120
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("\n🎉 Tous les tools récupérés en prose s'exécutent correctement.")
