# -*- coding: utf-8 -*-
"""Régression (run shadows #4) : auto-équilibrage de DIFFICULTÉ.

Une rencontre MORTELLE pour la table réelle (FP ≥ APL + 2 pour ≤4 PJ)
est allégée TEMPORAIREMENT même quand le nombre de joueurs est dans la
fourchette recommandée — le Loup terrible (FP 3, Mordre +8, 1d8+6) a
tué 3 PJ niveau 1 en ~4 rounds malgré des touches déterministes.

Usage : py -m pytest tests/test_equilibre_difficulte.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = discover_tools()


def _monte(niveaux: list[int]) -> tuple[str, ToolContext]:
    d = tempfile.mkdtemp(prefix="eq_diff_")
    shutil.copy2(os.path.join(_REPO, "server", "data", "bestiaire.json"),
                 os.path.join(d, "bestiaire.json"))
    st = PartyState(data_dir=d, partie_id="eqdiff")
    etat = st.load()
    etat["phase"] = "exploration"
    etat["pj"] = [
        {"nom": f"PJ{i}", "joueur": f"j{i}", "race": "Humain",
         "classe": "Guerrier", "niveau": niv, "pv": 13, "pv_max": 13,
         "ca": 16, "conditions": [],
         "carac": {"FOR": 16, "DEX": 10, "CON": 14, "INT": 10, "SAG": 10,
                   "CHA": 8}}
        for i, niv in enumerate(niveaux)]
    # Module « 3-4 joueurs » (ratio 1.0 → PAS d'ajustement joueur) avec
    # une salle canonique contenant le Loup terrible (FP 3).
    etat["quete"] = {"bible": {"joueurs_recommandes": "3-4"}}
    etat["donjon"] = {"id": "divers_test",
                      "grille": [{"x": 0, "y": 0,
                                  "ennemis": ["Loup terrible ×1"]}],
                      "courant": [0, 0]}
    st.save(etat)
    return d, ToolContext(partie_id="eqdiff", joueur="j0", data_dir=d)


def _engage(d: str):
    return asyncio.run(invoke_tool(
        _TOOLS["engager_combat"],
        ToolContext(partie_id="eqdiff", joueur="j0", data_dir=d),
        {"monstres": "Loup terrible"}))


def test_rencontre_mortelle_allegee():
    """FP 3 vs APL 1 (3 PJ) → allégement TEMPORAIRE annoncé, PV réduits."""
    d, _ = _monte([1, 1, 1])
    r = _engage(d)
    assert "Ajustement de difficulté" in r.text, r.text[:300]
    etat = PartyState(data_dir=d, partie_id="eqdiff").load()
    mcb = etat.get("monstres_combat") or []
    assert mcb, "le combat doit s'engager"
    assert int(mcb[0].get("pv", 0)) < 45, mcb[0]
    assert int(mcb[0].get("pv", 0)) >= 20, mcb[0]  # allégé, pas anéanti


def test_table_niveau_suffisant_pas_d_ajustement():
    """APL 4 (module 1-4) : FP 3 < APL + 2 → combat canonique INTACT."""
    d, _ = _monte([4, 4, 4])
    r = _engage(d)
    assert "Ajustement de difficulté" not in r.text, r.text[:300]
    etat = PartyState(data_dir=d, partie_id="eqdiff").load()
    mcb = etat.get("monstres_combat") or []
    assert mcb and int(mcb[0].get("pv", 0)) == 45, mcb


def test_rencontre_faible_pas_d_ajustement():
    """Kobold (FP 1/4) vs APL 1 : aucune allégation."""
    d, ctx = _monte([1, 1, 1])
    # Salle canonique = Kobold pour que le garde B25 accepte.
    st = PartyState(data_dir=d, partie_id="eqdiff")
    etat = st.load()
    etat["donjon"]["grille"] = [{"x": 0, "y": 0,
                                 "ennemis": ["Kobold ×3"]}]
    st.save(etat)
    r = asyncio.run(invoke_tool(
        _TOOLS["engager_combat"], ctx, {"monstres": "Kobold"}))
    assert "Ajustement de difficulté" not in r.text, r.text[:300]
    assert not r.text.startswith(("⛔", "❌")), r.text[:200]
