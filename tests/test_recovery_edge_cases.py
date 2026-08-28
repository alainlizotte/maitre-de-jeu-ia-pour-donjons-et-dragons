"""Tests des chemins non couverts : args JSON, booléens/flottants en prose,
appels multi-lignes — les formats que Gemma peut produire mais que le
balayage minimal n'a pas exercés.

Usage : py -m pytest tests/test_recovery_edge_cases.py -q
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
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_edge"


class ScriptedClient:
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
    orch = Orchestrator(
        client=ScriptedClient([
            "Le MJ agit.\n\n" + prose_reply + "\n\nSuite.",
            "Narration finale.",
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
    return tempfile.mkdtemp(prefix="dnd35_edge_")


def _etat(data_dir: str) -> dict:
    path = os.path.join(data_dir, f"partie_{PID}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _setup(data_dir: str) -> None:
    from server.game.state import PartyState
    st = PartyState(data_dir=data_dir, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["pj"] = [{"nom": "Brunhild", "joueur": "alain", "race": "Nain",
                   "classe": "Guerrier", "niveau": 1, "pv": 12, "pv_max": 12,
                   "ca": 16}]
    etat["initiative"] = [{"nom": "Brunhild", "init": 15}]
    etat["courant_tour_pour"] = "Brunhild"
    etat["monstres_combat"] = [{"nom": "Gobelin", "pv": 6, "pv_max": 6, "ca": 15}]
    st.save(etat)


# --------------------------------------------------------------------------- #
def test_demarrer_combat_json_quoted():
    """Arg JSON structuré passé en chaîne quotée (format recommandé)."""
    d = _fresh_dir()
    try:
        _setup(d)
        r = _run_prose(
            '`demarrer_combat(initiative_liste="[{\\"nom\\": \\"Brunhild\\", '
            '\\"init\\": 15}, {\\"nom\\": \\"Gobelin\\", \\"init\\": 10}]")`',
            d,
        )
        assert "demarrer_combat" in [t["name"] for t in r.tool_calls_trace]
        tr = r.tool_calls_trace[0]
        assert tr["ok"], tr["text"]
        ordre = _etat(d).get("initiative") or []
        assert [e["nom"] for e in ordre][:2] == ["Brunhild", "Gobelin"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_etat_partie_save_json():
    d = _fresh_dir()
    try:
        _setup(d)
        r = _run_prose(
            '`etat_partie_save(nouveau_etat="{\\"phase\\": \\"exploration\\", '
            '\\"pj\\": []}")`',
            d,
        )
        assert "etat_partie_save" in [t["name"] for t in r.tool_calls_trace]
        print("TOOL:", r.tool_calls_trace[0]["text"][:200])
        print("ARGS:", r.tool_calls_trace[0]["args"])
        assert _etat(d).get("phase") == "exploration"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bool_bare_et_francais():
    """`allie=vrai` / `allie=true` (jetons nus) → convertis en bool."""
    for bare in ("allie=true", "allie=vrai"):
        txt = f'`combat_ajouter_combattant(nom="Loup", {bare})`'
        calls, _ = parse_prose_tool_calls(txt, TOOLS)
        assert calls and calls[0]["name"] == "combat_ajouter_combattant"
        assert calls[0]["arguments"].get("allie") is True, bare


def test_floats_position():
    d = _fresh_dir()
    try:
        _setup(d)
        r = _run_prose(
            '`carte_joueurs_position(nom_perso="groupe", x=27.5, y=30.25)`', d,
        )
        assert "carte_joueurs_position" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"]
        pos = (_etat(d).get("positions_joueurs") or {}).get("groupe")
        assert pos == [27.5, 30.25]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_appel_multiligne():
    """Gemma écrit parfois l'appel sur plusieurs lignes indentées."""
    txt = (
        "Le MJ attaque.\n\n```\nlancer_attaque(\n"
        '  nom_attaquant="Brunhild",\n'
        '  nom_cible="Gobelin",\n'
        '  arme="Hache de guerre",\n'
        "  bonus_attaque=4,\n"
        "  ca_cible=15\n"
        ")\n```\n\nLa lame s'abat."
    )
    calls, cleaned = parse_prose_tool_calls(txt, TOOLS)
    assert len(calls) == 1
    assert calls[0]["name"] == "lancer_attaque"
    assert calls[0]["arguments"]["nom_cible"] == "Gobelin"
    assert calls[0]["arguments"]["bonus_attaque"] == 4
    assert "lancer_attaque" not in cleaned
    assert "La lame s'abat." in cleaned


def test_appel_multiligne_end_to_end():
    d = _fresh_dir()
    try:
        _setup(d)
        r = _run_prose(
            'lancer_attaque(\n  nom_attaquant="Brunhild",\n  nom_cible="Gobelin",\n'
            '  arme="Hache de guerre"\n)',
            d,
        )
        assert "lancer_attaque" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"]
        assert "lancer_attaque(" not in r.narration
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_carac_texte_non_quoted_avec_virgules():
    """carac_texte=FOR 16, DEX 12, CON 14 (non quoté, virgules) → recollé."""
    txt = '`fiche_perso_creer_rapide(nom="Thorgal", race="Humain", classe="Barde", carac_texte=FOR 16, DEX 12, CON 14)`'
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert calls[0]["arguments"].get("carac_texte") == "FOR 16, DEX 12, CON 14"


def test_fiche_perso_creer_json_complet():
    """Le tool complet (17 args, plusieurs JSON) via prose."""
    d = _fresh_dir()
    try:
        _setup(d)
        r = _run_prose(
            '`fiche_perso_creer(nom="Thorgal", race="Humain", classe="Barde", '
            'niveau=1, carac_json="{\\"FOR\\": 12, \\"DEX\\": 14, \\"CON\\": 13, '
            '\\"INT\\": 10, \\"SAG\\": 8, \\"CHA\\": 15}", pv=6, pv_max=6, ca=12, '
            'sauvegardes_json="{\\"Vigueur\\": 0, \\"Reflexes\\": 2, \\"Volonte\\": 0}", '
            'bab=0, competences_json="{}", dons_json="[]", equipement_json="[]", '
            'or_total=100, alignement="Neutre", joueur="alain")`',
            d,
        )
        assert "fiche_perso_creer" in [t["name"] for t in r.tool_calls_trace]
        assert r.tool_calls_trace[0]["ok"], r.tool_calls_trace[0]["text"]
        path = os.path.join(d, "fiches", "fiche_thorgal.json")
        assert os.path.isfile(path)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_placeholder_float_rejete():
    """Placeholder sur un paramètre float → rejeté aussi (pas seulement int)."""
    txt = '`carte_joueurs_position(nom_perso="groupe", x="quelque part", y=50)`'
    calls, _ = parse_prose_tool_calls(txt, TOOLS)
    assert "x" not in calls[0]["arguments"]
    assert calls[0]["arguments"].get("y") == 50


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("\n🎉 Tous les cas limites passent.")
