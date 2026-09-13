# -*- coding: utf-8 -*-
"""Correctifs combat 63f0838a : conditions cohérentes + alignement narration.

1. Une fiche qui passe à ≤ -10 PV doit porter « Mort » SEULE — plus de
   « Stabilisé » résiduel (conditions contradictoires observées :
   ['Stabilisé', 'Mort'] sur la fiche de Balrog).
2. `engager_combat` impose l'alignement de la narration sur le nombre de
   créatures réellement engagées (plafond d'équilibre) — « trois
   silhouettes » narrées pour une seule goule engagée créait des ennemis
   fantômes.

Usage : py -m pytest tests/test_conditions_mort.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_SPECS = discover_tools()


def _tool(nom: str):
    return _SPECS[nom]

PID = "test_conditions_mort"


def _partie(d: str, pv: int, conditions: list[str]) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Test", "pv": pv, "conditions": conditions}],
        "pnj": [],
        "histoire": [],
    })


def _fiche(d: str, pv: int, conditions: list[str]) -> None:
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    path = os.path.join(d, "fiches")
    os.makedirs(path, exist_ok=True)
    # _chemin(ctx, nom) → fiches/fiche_<slug>.json
    from server.tools.fiches import _chemin
    with open(_chemin(ctx, "Test"), "w", encoding="utf-8") as f:
        json.dump({
            "nom": "Test", "pv": pv, "pv_max": 14, "conditions": conditions,
        }, f, ensure_ascii=False)


def test_mort_efface_stabilise():
    """PV → -10 avec « Stabilisé » sur la fiche : conditions = ['Mort']
    SEULE (plus de contradiction)."""
    d = tempfile.mkdtemp(prefix="dnd35_cond_")
    try:
        _partie(d, pv=9, conditions=["Stabilisé"])
        _fiche(d, pv=9, conditions=["Stabilisé"])
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        tr = asyncio_run(invoke_tool(
            _tool("fiche_perso_infliger_degats"),
            ctx, {"nom": "Test", "degats": 20},
        ))
        assert not tr.text.startswith("❌"), tr.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_test.json"), encoding="utf-8"))
        conds = fiche.get("conditions") or []
        assert "Mort" in conds, conds
        assert "Stabilisé" not in conds and "Stabilise" not in conds, conds
    finally:
        pass


def test_guerison_efface_stabilise():
    """Remis en positifs : « Stabilisé » disparaît aussi (plus mourant)."""
    d = tempfile.mkdtemp(prefix="dnd35_cond_")
    try:
        _partie(d, pv=2, conditions=["Stabilisé"])
        _fiche(d, pv=2, conditions=["Stabilisé"])
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        tr = asyncio_run(invoke_tool(
            _tool("fiche_perso_soigner"),
            ctx, {"nom": "Test", "soin": 5},
        ))
        assert not tr.text.startswith("❌"), tr.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_test.json"), encoding="utf-8"))
        conds = fiche.get("conditions") or []
        assert "Stabilisé" not in conds, conds
    finally:
        pass


def test_engager_impose_alignement_narration():
    """Le résultat d'engager_combat impose le nombre exact de créatures à
    narrer (pas d'ennemis fantômes)."""
    d = tempfile.mkdtemp(prefix="dnd35_cond_")
    try:
        PartyState(data_dir=d, partie_id=PID).save({
            "meta": {"titre": "test"},
            "phase": "exploration",
            "pj": [{"nom": "Test", "pv": 30, "pv_max": 30, "joueur": "a"}],
            "pnj": [],
            "histoire": [],
        })
        goule = {
            "nom": "Goule", "cle": "goule", "type": "mort-vivant",
            "taille": "M", "fp": 1, "pv": 16, "pv_max": 16, "ca": 14,
        }
        with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
            json.dump({"_meta": {}, "goule": goule}, f, ensure_ascii=False)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        tr = asyncio_run(invoke_tool(
            _tool("engager_combat"),
            ctx, {"monstres": "Goule, Goule"},
        ))
        assert not tr.text.startswith(("❌", "🚫")), tr.text
        assert "EXACTEMENT 2" in tr.text, tr.text
    finally:
        pass


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
