# -*- coding: utf-8 -*-
"""Garde-fou « combat narré sans engager » — engager_combat forcé.

Régression observée en partie 77e2862b : le modèle narre l'embuscade
(« cinq squelettes se libèrent du plâtre et s'écrasent au sol pour vous
attaquer ») en PUR PROSE, sans aucun tool call — ni initiative, ni PV
officiels, ni calibration d'équilibre (l'outil n'a « pas tourné »).
Désormais, en phase exploration, une narration contenant un signal
d'attaque ennemie + des noms du bestiaire déclenche l'engagement forcé ;
le plafond d'équilibre interne arbitre ensuite la quantité.

Usage : py -m pytest tests/test_engage_force.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

PID = "test_engage_force"


class _ClientFaux:
    """Itération 1 : narration (paramétrable) sans outil ; itération 2 : final."""

    def __init__(self, contenu1: str = (
            "Soudain, avec un bruit de craquement effrayant, cinq "
            "squelettes se libèrent du plâtre et s'écrasent au sol "
            "pour vous attaquer. Leurs yeux brûlent d'une lueur "
            "rouge sang.")):
        self.contenu1 = contenu1
        self.appels = 0

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        self.appels += 1
        if self.appels == 1:
            return ChatResult(
                content=self.contenu1,
                tool_calls=[], finish_reason="stop", raw={},
            )
        return ChatResult(
            content="Les ossements se disloquent sous vos coups.",
            tool_calls=[], finish_reason="stop", raw={},
        )


def _setup(d: str, pv: int) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Test", "pv": pv, "pv_max": pv, "joueur": "alain"}],
        "pnj": [],
        "monstres_combat": [],
        "histoire": [],
    })
    goule = {
        "nom": "Squelette", "cle": "squelette", "type": "mort-vivant",
        "taille": "M", "fp": "1/3", "pv": 6, "pv_max": 6, "ca": 12,
    }
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "squelette": goule}, f, ensure_ascii=False)


def test_embuscade_narree_force_engager():
    d = tempfile.mkdtemp(prefix="dnd35_engage_")
    try:
        _setup(d, pv=60)   # 60 PV → plafond 2,5× = 150 ≥ 5×6=30 : passe
        orch = Orchestrator(
            client=_ClientFaux(), tools=discover_tools(), tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="je vais au nord")],
            ctx,
        ))
        # engager_combat a été forcé et exécuté.
        assert any(
            tc.get("name") == "engager_combat" and tc.get("ok")
            for tc in result.tool_calls_trace
        ), result.tool_calls_trace
        # Le combat est actif avec les 5 squelettes.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") == "combat", etat.get("phase")
        mons = etat.get("monstres_combat") or []
        assert len(mons) == 5, [(m.get("nom"), m.get("pv")) for m in mons]
    finally:
        pass


def test_narration_sans_attaque_n_engage_pas():
    """Décrire une salle sans signal d'attaque ennemie ne doit PAS
    engager de combat (faux positif = combat fantôme)."""
    d = tempfile.mkdtemp(prefix="dnd35_engage_")
    try:
        _setup(d, pv=60)
        orch = Orchestrator(
            client=_ClientFaux(contenu1=(
                "Des ossements jonchent le sol de la crypte et des "
                "squelettes décoratifs ornent les niches murales."
            )),
            tools=discover_tools(), tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="je vais au nord")],
            ctx,
        ))
        assert not any(
            tc.get("name") == "engager_combat"
            for tc in result.tool_calls_trace
        ), result.tool_calls_trace
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") == "exploration", etat.get("phase")
    finally:
        pass
