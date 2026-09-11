# -*- coding: utf-8 -*-
"""Re-filtrage des outils quand la phase change EN COURS de tour.

Régression observée en partie 54de40ed : le tour démarre en phase
exploration (34 tools, sans lancer_attaque/lancer_degats) ; le modèle
appelle `engager_combat` en cours de tour → phase combat — mais les
schémas restaient figés sur l'ensemble exploration. Le modèle ne pouvait
physiquement pas résoudre son attaque : il simulait les dés en prose
(« inflige 7 dégâts »), 3 corrections puis abandon — dégâts jamais
appliqués aux goules (restées 16/16 malgré les coups narrés).

Désormais `_filter_tools_by_phase` est réévalué À CHAQUE itération de la
boucle et la doc des nouveaux outils est injectée en message system.

Usage : py -m pytest tests/test_outils_phase_changeante.py -q
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

PID = "test_phase_changeante"


def _noms_tools(tools_arg: Any) -> set:
    """Noms d'outils d'un payload `tools` OpenAI (liste de schémas)."""
    noms = set()
    for t in tools_arg or []:
        fn = t.get("function") or {}
        noms.add(fn.get("name") or t.get("name"))
    return noms


class _ClientFaux:
    """Client LLM fictif : script d'appels + capture des tools proposés."""

    def __init__(self, script: list[ChatResult]):
        self.script = script
        self.tools_vus: list[set] = []
        self.appels = 0

    async def chat(self, work, tools=None, tool_choice=None, temperature=None):
        self.tools_vus.append(_noms_tools(tools))
        reponse = self.script[min(self.appels, len(self.script) - 1)]
        self.appels += 1
        return reponse


def _partie(d: str) -> None:
    """État exploration + bestiaire minimal contenant la Goule (sinon
    engager_combat refuse « hors bestiaire »)."""
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "BBB", "pv": 30, "pv_max": 30, "joueur": "alain"}],
        "pnj": [],
        "monstres_combat": [],
        "histoire": [],
    })
    goule = {
        "nom": "Goule", "cle": "goule", "type": "mort-vivant",
        "taille": "M", "fp": 1, "dv": "2d12", "pv": 16, "pv_max": 16,
        "ca": 14, "vitesse": "9m", "init": "+2", "bab": "+2",
        "attaques": "Griffe +3 (1d6+1), mordre +3",
        "degs": "1d6+1", "sauvegardes": "Vig +3, Réf +2, Vol +1",
        "carac": "For 13, Dex 15, Con —, Int 8, Sag 10, Cha 6",
        "comp": "", "dons": "", "capacites": "paralysie",
        "faiblesses": "", "alignement": "Chaotique Mauvais",
    }
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "goule": goule}, f, ensure_ascii=False)


def test_outils_combat_disponibles_apres_engager_en_cours_de_tour():
    d = tempfile.mkdtemp(prefix="dnd35_phase_")
    try:
        _partie(d)
        outils = discover_tools()
        assert "engager_combat" in outils and "lancer_attaque" in outils

        client = _ClientFaux([
            # Itération 1 : le modèle engage le combat (exploration → combat).
            ChatResult(content="", tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "engager_combat",
                             "arguments": json.dumps({"monstres": "Goule, Goule"})},
            }], finish_reason="tool_calls", raw={}),
            # Itération 2 : le modèle attaque — il DOIT pouvoir.
            ChatResult(content="", tool_calls=[{
                "id": "c2", "type": "function",
                "function": {"name": "lancer_attaque",
                             "arguments": json.dumps({
                                 "nom_attaquant": "BBB",
                                 "nom_cible": "Goule",
                                 "ca_cible": "14",
                                 "bonus_attaque": "+5",
                                 "arme": "Hache lourde"})},
            }], finish_reason="tool_calls", raw={}),
            # Itération 3 : narration finale.
            ChatResult(content="Vous abattez la hache sur la goule.",
                       tool_calls=[], finish_reason="stop", raw={}),
        ])
        orch = Orchestrator(client=client, tools=outils, tool_mode="native")
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="j'Attaque un goule avec ma hache")],
            ctx,
        ))

        # Appel 1 : schémas EXPLORATION (sans lancer_attaque).
        assert "engager_combat" in client.tools_vus[0]
        assert "lancer_attaque" not in client.tools_vus[0]
        # Appel 2 : schémas COMBAT — lancer_attaque doit être proposé.
        assert "lancer_attaque" in client.tools_vus[1], client.tools_vus[1]
        # L'attaque a été exécutée réellement (trace, succès).
        assert any(
            tc.get("name") == "lancer_attaque" and tc.get("ok")
            for tc in result.tool_calls_trace
        ), result.tool_calls_trace
        # Le combat est bien actif côté état.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") == "combat", etat.get("phase")
    finally:
        pass
