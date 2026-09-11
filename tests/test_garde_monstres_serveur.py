# -*- coding: utf-8 -*-
"""Garde-fou « le serveur joue les monstres » après `terminer_mon_tour`.

Régression observée en partie 54de40ed : après `terminer_mon_tour`,
`combat.boucle_auto` joue SYNCHRONIQUEMENT les tours de goules (BBB
9→6→4 PV, résultats officiels). Le modèle a REJOUÉ ces attaques lui-même
(`lancer_attaque` de Goule (2) + `fiche_perso_infliger_degats(bbb, 3)`)
→ dégâts appliqués deux fois (4→1 PV), lus par le joueur comme
« les dégâts me soignent ». Le garde-fou refuse ces appels redondants.

Usage : py -m pytest tests/test_garde_monstres_serveur.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import Orchestrator, OrchestratedResult  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402

PID = "test_garde_monstres"

_REFUS = "⛔ Le serveur joue DÉJÀ"


def _partie(d: str) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "combat",
        "pj": [{"nom": "BBB", "pv": 9, "joueur": "alain"}],
        "pnj": [],
        "monstres_combat": [
            {"nom": "Goule", "pv": 9, "pv_max": 16},
            {"nom": "Goule (2)", "pv": 16, "pv_max": 16},
        ],
        "histoire": [],
    })


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _result_avec_tour_termine() -> OrchestratedResult:
    r = OrchestratedResult()
    r.tool_calls_trace.append({"name": "terminer_mon_tour", "ok": True})
    return r


def test_attaque_monstre_refusee_apres_tour_termine():
    """Après terminer_mon_tour, `lancer_attaque` d'un MONSTRE est refusée."""
    d = tempfile.mkdtemp(prefix="dnd35_garde_")
    try:
        _partie(d)
        orch = Orchestrator(client=None, tools={})
        refus = asyncio.run(orch._garder_monstres_serveur(
            "lancer_attaque",
            {"nom_attaquant": "Goule (2)", "nom_cible": "BBB",
             "ca_cible": "16", "bonus_attaque": "+3", "arme": "Griffes"},
            _ctx(d), _result_avec_tour_termine(),
        ))
        assert refus is not None and refus.startswith(_REFUS), refus
    finally:
        pass


def test_degats_a_un_pj_refuses_apres_tour_termine():
    d = tempfile.mkdtemp(prefix="dnd35_garde_")
    try:
        _partie(d)
        orch = Orchestrator(client=None, tools={})
        for appel in (
            ("lancer_degats", {"cible": "BBB", "nb_des": 1, "faces": 6}),
            ("fiche_perso_infliger_degats", {"nom": "bbb", "degats": 3}),
        ):
            refus = asyncio.run(orch._garder_monstres_serveur(
                appel[0], appel[1], _ctx(d), _result_avec_tour_termine(),
            ))
            assert refus is not None and refus.startswith(_REFUS), (appel, refus)
    finally:
        pass


def test_attaques_du_joueur_autorisees():
    """Les attaques du PJ contre les monstres restent toujours possibles."""
    d = tempfile.mkdtemp(prefix="dnd35_garde_")
    try:
        _partie(d)
        orch = Orchestrator(client=None, tools={})
        res = _result_avec_tour_termine()
        assert asyncio.run(orch._garder_monstres_serveur(
            "lancer_attaque",
            {"nom_attaquant": "BBB", "nom_cible": "Goule",
             "ca_cible": "14", "bonus_attaque": "+5", "arme": "Hache"},
            _ctx(d), res,
        )) is None
        assert asyncio.run(orch._garder_monstres_serveur(
            "lancer_degats",
            {"cible": "Goule", "nb_des": 1, "faces": 8, "bonus": 2},
            _ctx(d), res,
        )) is None
        assert asyncio.run(orch._garder_monstres_serveur(
            "fiche_perso_infliger_degats", {"nom": "Goule", "degats": 7},
            _ctx(d), res,
        )) is None
    finally:
        pass


def test_sans_terminer_mon_tour_aucun_refus():
    """Hors boucle auto (pas de terminer_mon_tour dans le tour), le modèle
    conserve sa mécanique habituelle — aucun changement de comportement."""
    d = tempfile.mkdtemp(prefix="dnd35_garde_")
    try:
        _partie(d)
        orch = Orchestrator(client=None, tools={})
        res = OrchestratedResult()   # trace vide
        assert asyncio.run(orch._garder_monstres_serveur(
            "lancer_attaque",
            {"nom_attaquant": "Goule (2)", "nom_cible": "BBB"},
            _ctx(d), res,
        )) is None
        assert asyncio.run(orch._garder_monstres_serveur(
            "fiche_perso_infliger_degats", {"nom": "BBB", "degats": 3},
            _ctx(d), res,
        )) is None
    finally:
        pass


def test_trace_avec_echec_terminer_ne_refuse_pas():
    """Un terminer_mon_tour en ÉCHEC (refus « pas ton tour ») ne déclenche
    pas le garde-fou — la boucle auto n'a pas tourné."""
    d = tempfile.mkdtemp(prefix="dnd35_garde_")
    try:
        _partie(d)
        orch = Orchestrator(client=None, tools={})
        res = OrchestratedResult()
        res.tool_calls_trace.append({"name": "terminer_mon_tour", "ok": False})
        assert asyncio.run(orch._garder_monstres_serveur(
            "lancer_attaque",
            {"nom_attaquant": "Goule (2)", "nom_cible": "BBB"},
            _ctx(d), res,
        )) is None
    finally:
        pass
