"""Tests de la mémoire de campagne (`tools/memoire.py`) : missions, lieux,
personnages rencontrés, position du groupe, et du résumé injecté dans le
prompt du MJ (`memoire_resume`).

Usage : py -m pytest tests/test_memoire.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.memoire import (  # noqa: E402
    memoire_lieu, memoire_mission, memoire_personnage, memoire_position,
    memoire_resume,
)

PID = "test_memoire"


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_memoire_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _mem(d: str) -> dict:
    return PartyState(data_dir=d, partie_id=PID).load().get("memoire", {})


# --------------------------------------------------------------------------- #
def test_mission_create_et_update():
    d = _fresh_dir()
    try:
        tr = asyncio.run(memoire_mission(
            _ctx(d), "Retrouver l'épée du roi", "active", "Dans les ruines"))
        assert tr.text.startswith("✅")
        tr2 = asyncio.run(memoire_mission(
            _ctx(d), "Retrouver l'épée du roi", "terminee"))
        miss = _mem(d)["missions"]
        assert len(miss) == 1
        assert miss[0]["statut"] == "terminee"
        assert miss[0]["notes"] == "Dans les ruines"  # notes conservées
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lieu_upsert():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_lieu(_ctx(d), "L'Auberge du Pendu", "salle commune"))
        asyncio.run(memoire_lieu(_ctx(d), "L'Auberge du Pendu", "chambre n°3"))
        lieux = _mem(d)["lieux_visites"]
        assert len(lieux) == 1
        assert lieux[0]["notes"] == "chambre n°3"
        # Un second lieu s'ajoute.
        asyncio.run(memoire_lieu(_ctx(d), "Crypte des Ombres"))
        assert len(_mem(d)["lieux_visites"]) == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_personnage_upsert():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_personnage(_ctx(d), "Merlin le Boiteux", "forgeron"))
        asyncio.run(memoire_personnage(_ctx(d), "Merlin le Boiteux", "allié"))
        pnjs = _mem(d)["personnages_rencontres"]
        assert len(pnjs) == 1
        assert pnjs[0]["notes"] == "allié"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_position_set():
    d = _fresh_dir()
    try:
        tr = asyncio.run(memoire_position(
            _ctx(d), "Donjon des Ombres", "Salle du trône", "près du puits"))
        pos = _mem(d)["position"]
        assert pos["lieu"] == "Donjon des Ombres"
        assert "Donjon des Ombres" in tr.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_memoire_resume_compile_les_blocs():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_mission(_ctx(d), "Quête A", "active", "objectif A"))
        asyncio.run(memoire_mission(_ctx(d), "Quête B", "terminee"))
        asyncio.run(memoire_lieu(_ctx(d), "Auberge"))
        asyncio.run(memoire_personnage(_ctx(d), "PNJ 1"))
        asyncio.run(memoire_position(_ctx(d), "Donjon"))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        resume = memoire_resume(etat)
        assert "Position actuelle : Donjon" in resume
        assert "Quête A" in resume and "Missions actives" in resume
        assert "Auberge" in resume and "PNJ 1" in resume
        assert "Peut tronquer" not in resume
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_memoire_resume_vide_si_aucune_donnee():
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save({})
        assert memoire_resume({}) == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)
