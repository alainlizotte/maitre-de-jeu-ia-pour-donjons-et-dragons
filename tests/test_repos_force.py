# -*- coding: utf-8 -*-
"""Garde-fou « repos demandé » — `repos_long` forcé si ignoré par le modèle.

Régression observée en partie abf74a77 : le joueur dit « je me repose »
DEUX FOIS — le modèle narre des déplacements et des salles hallucinées à
la place, jamais l'appel `repos_long` : PV jamais restaurés (berglish
restait à 0 PV « Invalide »). Désormais l'intention repos dans le message
joueur force l'appel (toute l'équipe, hors combat), comme le garde-fou
escalier force `carte_donjon_etage`.

Usage : py -m pytest tests/test_repos_force.py -q
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

PID = "test_repos_force"


class _ClientFaux:
    """Itération 1 : ignore le repos (prose seule) ; itération 2 : final."""

    def __init__(self):
        self.appels = 0

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        self.appels += 1
        if self.appels == 1:
            return ChatResult(
                content=(
                    "Vous vous asseyez un instant contre le mur humide des "
                    "catacombes, la hache sur les genoux, et fermez les yeux."
                ),
                tool_calls=[], finish_reason="stop", raw={},
            )
        return ChatResult(
            content="Après un repos de huit heures, vous vous sentez mieux.",
            tool_calls=[], finish_reason="stop", raw={},
        )


def _setup(d: str) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "berglish", "pv": 0, "pv_max": 8, "joueur": "alain"}],
        "pnj": [],
        "histoire": [],
    })
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_berglish.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "berglish", "pv": 0, "pv_max": 8, "niveau": 1,
            "classe": "Guerrier", "conditions": ["Invalide"],
        }, f, ensure_ascii=False)


def test_repos_demande_force_repos_long():
    d = tempfile.mkdtemp(prefix="dnd35_repos_")
    try:
        _setup(d)
        orch = Orchestrator(
            client=_ClientFaux(), tools=discover_tools(), tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="je me repose")],
            ctx,
        ))
        # repos_long a été forcé et exécuté.
        assert any(
            tc.get("name") == "repos_long" and tc.get("ok")
            for tc in result.tool_calls_trace
        ), result.tool_calls_trace
        # PV restaurés : 0 + 1/niveau (niv 1) = 1.
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_berglish.json"),
            encoding="utf-8"))
        assert fiche.get("pv") == 1, fiche.get("pv")
    finally:
        pass


def test_hors_repos_aucun_appel_force():
    """Sans intention repos dans le message, AUCUN repos_long forcé (une
    simple exploration ne déclenche pas un repos)."""
    d = tempfile.mkdtemp(prefix="dnd35_repos_")
    try:
        _setup(d)
        orch = Orchestrator(
            client=_ClientFaux(), tools=discover_tools(), tool_mode="native",
        )
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="je vais au nord")],
            ctx,
        ))
        assert not any(
            tc.get("name") == "repos_long" for tc in result.tool_calls_trace
        ), result.tool_calls_trace
    finally:
        pass
