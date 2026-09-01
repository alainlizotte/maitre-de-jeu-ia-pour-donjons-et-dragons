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
    memoire_resume, memoire_intrigue, memoire_evenement, _MAX_EVENEMENTS,
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


# --------------------------------------------------------------------------- #
def test_intrigue_met_a_jour_resume_et_objectif():
    d = _fresh_dir()
    try:
        tr = asyncio.run(memoire_intrigue(
            _ctx(d), "Le groupe a délivré la prisonnière du culte de Xvim.",
            "Se rendre à la citadelle de Myth Drannor."))
        assert "résumé" in tr.text and "objectif" in tr.text
        m = _mem(d)
        assert "prisonnière" in m["intrigue_resume"]
        assert m["objectif_courant"] == "Se rendre à la citadelle de Myth Drannor."
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_intrigue_met_a_jour_un_seul_champ():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_intrigue(_ctx(d), resume="Résumé de départ"))
        m = _mem(d)
        assert m["intrigue_resume"] == "Résumé de départ"
        assert m["objectif_courant"] == ""
        # Ne met à jour que l'objectif : le résumé doit rester.
        asyncio.run(memoire_intrigue(_ctx(d), objectif="Nouvel objectif"))
        m = _mem(d)
        assert m["intrigue_resume"] == "Résumé de départ"
        assert m["objectif_courant"] == "Nouvel objectif"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_evenement_horodate_et_borne():
    d = _fresh_dir()
    try:
        for i in range(_MAX_EVENEMENTS + 5):
            asyncio.run(memoire_evenement(_ctx(d), f"Événement {i}"))
        ev = _mem(d)["evenements_rencents"]
        assert len(ev) == _MAX_EVENEMENTS
        assert "ts" in ev[0]
        # Les plus récents sont conservés, les plus anciens purgés.
        assert ev[0]["texte"] == f"Événement {5}"
        assert ev[-1]["texte"] == f"Événement {_MAX_EVENEMENTS + 4}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_resume_inclut_intrigue_objectif_evenements():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_intrigue(
            _ctx(d), "Résumé d'intrigue de test.", "Objectif de test"))
        asyncio.run(memoire_evenement(_ctx(d), "Un événement marquant."))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        resume = memoire_resume(etat)
        assert "Objectif courant : Objectif de test" in resume
        assert "Résumé de l'intrigue : Résumé d'intrigue de test." in resume
        assert "Événements récents" in resume
        assert "Un événement marquant." in resume
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_resume_vide_si_seuls_champs_vides():
    d = _fresh_dir()
    try:
        asyncio.run(memoire_intrigue(_ctx(d)))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert memoire_resume(etat) == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)
