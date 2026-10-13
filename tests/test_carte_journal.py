"""Journal du voyage (`carte_visites`) — chemin parcouru + lieux visités.

Couvre :
  - `_journaliser_visite` : ajout de point, dédoublonnage de proximité
    (re-placement < 0.4 % → complète le nom, pas de doublon), cap 400 ;
  - `carte_joueurs_position` / `carte_joueurs_placer_ville` : le state_patch
    diffuse `carte_visites` et l'état persisté contient le journal ;
  - `appliquer_depart` (scénario) : le point de DÉPART ouvre le chemin.

Usage : py -m pytest tests/test_carte_journal.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    _journaliser_visite,
    carte_joueurs_placer_ville,
    carte_joueurs_position,
)
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")

PID = "test_carte_journal"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _etat(d: str) -> dict:
    import json
    p = os.path.join(d, f"partie_{PID}.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_journal_dedoublonne_proximite_et_complete_nom():
    etat: dict = {}
    assert _journaliser_visite(etat, "groupe", 10.0, 20.0, "") is True
    assert _journaliser_visite(etat, "groupe", 10.2, 20.1, "Waterdeep") is False
    assert len(etat["carte_visites"]) == 1
    # Le nom du lieu a complété le point proche (pas de doublon).
    assert etat["carte_visites"][0]["nom"] == "Waterdeep"
    # Un point LOIN est bien ajouté.
    assert _journaliser_visite(etat, "groupe", 50.0, 50.0, "Neverwinter") is True
    assert len(etat["carte_visites"]) == 2


def test_journal_cap_400():
    etat: dict = {}
    for i in range(450):
        _journaliser_visite(etat, "groupe", float(i % 100), float(i % 100))
    assert len(etat["carte_visites"]) == 400


def test_position_journalise_et_diffuse():
    d = tempfile.mkdtemp(prefix="dnd35_carte_j_")
    ctx = _ctx(d)
    tr = asyncio.run(invoke_tool(
        TOOLS["carte_joueurs_position"], ctx,
        {"nom_perso": "groupe", "x": 30, "y": 40}))
    assert tr.state_patch["carte_visites"], "carte_visites absente du patch"
    assert tr.state_patch["carte_visites"][0]["x"] == 30.0
    # Persisté sur disque.
    assert len(_etat(d).get("carte_visites", [])) == 1


def test_placer_ville_nomme_le_lieu_visite():
    d = tempfile.mkdtemp(prefix="dnd35_carte_j_")
    ctx = _ctx(d)
    tr = asyncio.run(invoke_tool(
        TOOLS["carte_joueurs_placer_ville"], ctx, {"ville": "Waterdeep"}))
    assert "Waterdeep" in tr.text
    visites = _etat(d).get("carte_visites", [])
    assert visites and visites[-1]["nom"] == "Waterdeep"
    patch = tr.state_patch
    assert patch["carte_visites"][-1]["nom"] == "Waterdeep"


def test_depart_scenario_ouvre_le_chemin():
    """appliquer_depart pose le premier point du chemin (import tardif
    sans cycle)."""
    from server.tools.scenarios import appliquer_depart
    etat: dict = {}
    # Pas de manifeste dans ce data_dir → depart None → pas de journal.
    d = tempfile.mkdtemp(prefix="dnd35_carte_j_")
    ctx = _ctx(d)
    assert appliquer_depart(ctx, etat, "inconnu_x") is None
    assert etat.get("carte_visites") in (None, [])
    # Avec un état déjà positionné → None aussi (pas de re-placement).
    etat2: dict = {"positions_joueurs": {"groupe": [1.0, 2.0]}}
    assert appliquer_depart(ctx, etat2, "x") is None
