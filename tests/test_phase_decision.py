# -*- coding: utf-8 -*-
"""Phase de décision contrainte (json_schema) — correctif abd81275.

Le 9B narratif « oubliait » d'appeler les outils mécaniques (repos,
inventaire, déplacement narrés en prose — 15 rejeux correctifs en 15 min).
La décision est désormais ANTICIPÉE dans un appel LLM court contraint par
`response_format: json_schema` (llama.cpp masque les logits : un nom hors
`enum` ou une prose est structurellement impossible), exécutée serveur
AVANT la narration.

Ces tests valident, avec un client stub :
1. décision « outils » → l'outil est exécuté AVANT la narration, l'état a
   bougé, et l'appel de narration reçoit le résultat officiel ;
2. décision « narrer » → aucun appel ;
3. backend sans support (exception) → repli transparent sur la boucle
   normale (zéro régression) ;
4. nom d'outil hors liste (backend défaillant) → filtré, jamais exécuté ;
5. `decision_phase=False` → aucun appel de décision émis.

Usage : py -m pytest tests/test_phase_decision.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

PID = "test_phase_decision"


class _ClientDecision:
    """Client stub : 1er appel (avec response_format) = décision ;
    appels suivants = narration. Enregistre chaque appel pour inspection."""

    def __init__(self, decision, narration="Vous avancez prudemment."):
        self.decision = decision          # dict | Exception
        self.narration = narration
        self.appels: list[dict] = []

    async def chat(self, messages, tools=None, tool_choice=None,
                   temperature=None, response_format=None):
        self.appels.append({
            "messages": messages,
            "response_format": response_format,
            "tools": tools,
        })
        if response_format is not None:
            if isinstance(self.decision, Exception):
                raise self.decision
            return ChatResult(
                content=json.dumps(self.decision, ensure_ascii=False),
                tool_calls=[], finish_reason="stop", raw={},
            )
        return ChatResult(
            content=self.narration,
            tool_calls=[], finish_reason="stop", raw={},
        )


def _setup(d: str, decision: bool = True) -> None:
    etat = {
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Utturgut", "pv": 8, "pv_max": 16, "joueur": "alain",
                "classe": "Barbare", "niveau": 1}],
        "pnj": [],
        "histoire": [],
    }
    if decision:
        etat["donjon"] = {
            "id": "Grotte de test",
            "courant": [0, 0],
            "salles_visitees": ["0,0"],
            "etage": 0,
            "etages": {},
            "grille": [
                {"x": 0, "y": 0, "type": "entrée", "visitee": True,
                 "description": "L'entrée.",
                 "portes": {"nord": False, "sud": False, "est": True,
                            "ouest": False}},
                {"x": 1, "y": 0, "type": "salle vide", "visitee": False,
                 "description": "Une salle vide.",
                 "portes": {"nord": False, "sud": False, "est": False,
                            "ouest": True}},
            ],
        }
    PartyState(data_dir=d, partie_id=PID).save(etat)


def _run(d: str, client, **kw):
    orch = Orchestrator(
        client=client, tools=discover_tools(), tool_mode="native", **kw,
    )
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    ctx.tour_id = "tour-test-1"
    return asyncio.run(orch.run(
        [Message(role="system", content="Tu es le MJ."),
         Message(role="user", content="je vais a l'est")],
        ctx,
    ))


def test_decision_outil_executee_avant_narration():
    d = tempfile.mkdtemp(prefix="dnd35_decp1_")
    _setup(d)
    client = _ClientDecision({
        "action": "outils",
        "outils": [{"nom": "carte_donjon_explorer",
                    "arguments": {"direction": "est"}}],
    })
    result = _run(d, client)
    # 1er appel = décision avec response_format json_schema (enum d'outils).
    assert len(client.appels) >= 2
    premier = client.appels[0]
    assert premier["response_format"] is not None
    schema = premier["response_format"]["json_schema"]["schema"]
    enum_noms = schema["properties"]["outils"]["items"]["properties"]["nom"]["enum"]
    assert "carte_donjon_explorer" in enum_noms
    assert "reset_partie" not in enum_noms  # périmètre mécanique uniquement
    # L'outil a été exécuté par la phase de décision.
    assert any(
        tc.get("name") == "carte_donjon_explorer" and tc.get("ok")
        for tc in result.tool_calls_trace
    ), result.tool_calls_trace
    # L'état a bougé : salle courante (1,0).
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat["donjon"]["courant"] == [1, 0]
    # L'appel de NARRATION contient le résultat officiel de l'outil.
    dernier = client.appels[-1]
    contenus = [m.content or "" for m in dernier["messages"]]
    assert any("salle (1,0)" in c or "1,0" in c for c in contenus)


def test_decision_narrer_aucun_outil():
    d = tempfile.mkdtemp(prefix="dnd35_decp2_")
    _setup(d)
    client = _ClientDecision({"action": "narrer"})
    result = _run(d, client)
    assert not any(
        tc.get("name") == "carte_donjon_explorer"
        for tc in result.tool_calls_trace
    )
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat["donjon"]["courant"] == [0, 0]


def test_decision_echec_backend_repli_transparent():
    d = tempfile.mkdtemp(prefix="dnd35_decp3_")
    _setup(d)
    client = _ClientDecision(RuntimeError("response_format non supporté"),
                             narration="Narration de repli.")
    result = _run(d, client)
    # La boucle normale a produit la narration, sans crash.
    assert result.narration == "Narration de repli."


def test_decision_nom_hors_enum_filtre():
    d = tempfile.mkdtemp(prefix="dnd35_decp4_")
    _setup(d)
    # Backend défaillant qui laisserait passer un outil hors périmètre :
    # la re-vérification stricte doit le filtrer (jamais exécuté).
    client = _ClientDecision({
        "action": "outils",
        "outils": [{"nom": "reset_partie", "arguments": {}},
                   {"nom": "inventaire_ajouter",
                    "arguments": {"nom": "Utturgut", "objet": "torche"}}],
    })
    result = _run(d, client)
    trace = [tc.get("name") for tc in result.tool_calls_trace]
    assert "reset_partie" not in trace
    assert "inventaire_ajouter" in trace


def test_decision_desactive_aucun_appel():
    d = tempfile.mkdtemp(prefix="dnd35_decp5_")
    _setup(d)
    client = _ClientDecision({"action": "outils", "outils": []})
    _run(d, client, decision_phase=False)
    # Un seul appel : la narration directe — pas d'appel de décision.
    assert len(client.appels) == 1
    assert client.appels[0]["response_format"] is None
