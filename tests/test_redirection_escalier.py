"""Garde-fou « confusion escalier » — indépendant du modèle LLM.

Régression observée en partie réelle (modèle RP finetuné) : le joueur dit
« nous descendons l'escalier », le MJ appelle `carte_donjon_explorer(sud)`
depuis la salle escaliers — le groupe RESSORTAIT au lieu de changer d'étage.
Le rattrapage serveur (`Orchestrator._rediriger_escalier`) réécrit l'appel
en `carte_donjon_etage(direction=…)` avant exécution.

Usage : py -m pytest tests/test_redirection_escalier.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import Orchestrator, OrchestratedResult  # noqa: E402
from server.tools.base import ToolContext, _TOOL_REGISTRY, invoke_tool  # noqa: E402
from server.tools.cartes import _MOUVEMENTS_TOUR  # noqa: E402

PID = "test_redir_escalier"

_SALLES = [
    {
        "x": 0, "y": 0, "type": "entrée", "visitee": True,
        "portes": {"nord": False, "sud": True, "est": False, "ouest": False},
    },
    {
        "x": 0, "y": -1, "type": "crypte", "visitee": True,
        "portes": {"nord": True, "sud": True, "est": False, "ouest": False},
    },
    {
        "x": 0, "y": -2, "type": "escaliers", "visitee": True,
        "portes": {"nord": False, "sud": True, "est": False, "ouest": False},
    },
]


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_redir_")


def _partie(d: str) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Barbouk"}],
        "pnj": [],
        "donjon": {
            "id": "Donjon Test",
            "grille": _SALLES,
            "salles_visitees": ["0,0", "0,-1", "0,-2"],
            "portes_bloquees": [],
            "courant": [0, -2],
            "etage": 0,
            "etages": {},
            "arrivee_par": "nord",
        },
        "histoire": [],
    })


def _position(d: str) -> list:
    donjon = PartyState(data_dir=d, partie_id=PID).load().get("donjon", {})
    return list(donjon.get("courant", [0, 0]))


def _orch() -> Orchestrator:
    return Orchestrator(client=None, tools={})


def _work(msg: str) -> list[Message]:
    return [Message(role="user", content=f"**[alain]** : {msg}")]


def setup_function(func) -> None:
    _MOUVEMENTS_TOUR.pop(PID, None)


def test_descendre_reecrit_en_etage():
    """« Nous descendons l'escalier » + explorer(sud) depuis la salle
    escaliers → réécrit en carte_donjon_etage(descendre)."""
    d = _fresh_dir()
    try:
        _partie(d)
        orch = _orch()
        resolved, args, note = asyncio.run(orch._rediriger_escalier(
            "carte_donjon_explorer", {"direction": "sud"},
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            _work("Nous descendons l'escalier vers l'étage inférieur"),
        ))
        assert resolved == "carte_donjon_etage", resolved
        assert args == {"direction": "descendre"}, args
        assert note and "RÉÉCRIT" in note
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_monter_reecrit_en_etage_monter():
    """« On remonte » + explorer depuis une salle escaliers → etage(monter)."""
    d = _fresh_dir()
    try:
        _partie(d)
        # Le groupe descend d'abord à l'étage 1 (arrivée : salle escaliers).
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_etage"], ctx,
            {"direction": "descendre"},
        ))
        assert PartyState(data_dir=d, partie_id=PID).load()["donjon"]["etage"] == 1

        orch = _orch()
        resolved, args, note = asyncio.run(orch._rediriger_escalier(
            "carte_donjon_explorer", {"direction": "nord"}, ctx,
            _work("Nous remontons par l'escalier"),
        ))
        assert resolved == "carte_donjon_etage", resolved
        assert args == {"direction": "monter"}, args
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_demi_tour_sans_intention_non_touche():
    """Sans intention escalier dans le message joueur, l'appel explorer
    reste intact (demi-tour légitime)."""
    d = _fresh_dir()
    try:
        _partie(d)
        orch = _orch()
        resolved, args, note = asyncio.run(orch._rediriger_escalier(
            "carte_donjon_explorer", {"direction": "sud"},
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            _work("Nous retournons en arrière"),
        ))
        assert resolved == "carte_donjon_explorer", resolved
        assert note is None
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_hors_salle_escaliers_non_touche():
    """Hors d'une salle escaliers, même avec « descendre » dans le message,
    explorer n'est pas réécrit."""
    d = _fresh_dir()
    try:
        _partie(d)
        # Place le groupe dans la crypte (0,-1), pas l'escalier.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        etat["donjon"]["courant"] = [0, -1]
        etat["donjon"]["arrivee_par"] = "sud"
        PartyState(data_dir=d, partie_id=PID).save(etat)

        orch = _orch()
        resolved, args, note = asyncio.run(orch._rediriger_escalier(
            "carte_donjon_explorer", {"direction": "nord"},
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            _work("Nous descendons au niveau inférieur"),
        ))
        assert resolved == "carte_donjon_explorer", resolved
        assert note is None
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_redirection_execute_la_descente():
    """Bout en bout : l'appel réécrit exécuté via invoke_tool fait bien
    passer le groupe à l'étage 1 sans déplacement cardinal."""
    d = _fresh_dir()
    try:
        _partie(d)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        orch = _orch()
        resolved, args, _note = asyncio.run(orch._rediriger_escalier(
            "carte_donjon_explorer", {"direction": "sud"}, ctx,
            _work("Nous descendons l'escalier"),
        ))
        tr = asyncio.run(invoke_tool(_TOOL_REGISTRY[resolved], ctx, args))
        assert "Les Bas Tombeaux" in tr.text or "étage" in tr.text.lower() \
            or "sous-sol" in tr.text.lower(), tr.text
        donjon = PartyState(data_dir=d, partie_id=PID).load()["donjon"]
        assert donjon["etage"] == 1, donjon["etage"]
        assert donjon["courant"] == [0, 0], donjon["courant"]
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_etage_directions_variantes():
    """carte_donjon_etage accepte les variantes de direction du LLM."""
    d = _fresh_dir()
    try:
        _partie(d)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        for variante in ("vers le bas", "sous-sol", "descendre"):
            _partie(d)  # état frais à chaque itération
            tr = asyncio.run(invoke_tool(
                _TOOL_REGISTRY["carte_donjon_etage"], ctx,
                {"direction": variante},
            ))
            assert not tr.text.startswith(("❌", "🚫")), (variante, tr.text)
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


# --------------------------------------------------------------------------- #
#  Deuxième garde-fou : le modèle n'appelle AUCUN outil (pur récit « vous
#  êtes dans la salle des escaliers… »). Observé en partie 54de40ed : le tour
#  se terminait en description, le groupe ne changeait jamais d'étage.
# --------------------------------------------------------------------------- #

def test_groupe_dans_escalier_helper():
    """Le helper détecte la salle escaliers courante (et son absence)."""
    d = _fresh_dir()
    try:
        _partie(d)
        orch = _orch()
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        assert asyncio.run(orch._groupe_dans_escalier(ctx)) is True
        etat = PartyState(data_dir=d, partie_id=PID).load()
        etat["donjon"]["courant"] = [0, -1]   # crypte, pas escalier
        PartyState(data_dir=d, partie_id=PID).save(etat)
        assert asyncio.run(orch._groupe_dans_escalier(ctx)) is False
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_appel_force_etage_sans_tool_call():
    """Réplication 54de40ed : intention « descendre » + groupe dans la salle
    escaliers + AUCUNE tool call du modèle → l'appel forcé de
    `carte_donjon_etage(descendre)` fait bien changer d'étage."""
    d = _fresh_dir()
    try:
        _partie(d)
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        orch = Orchestrator(client=None, tools=dict(_TOOL_REGISTRY))
        work = _work("Nous descendons l'escalier vers l'étage inférieur")
        assert Orchestrator._intention_escalier(work) == "descendre"
        assert asyncio.run(orch._groupe_dans_escalier(ctx)) is True
        result = OrchestratedResult()
        assert not any(
            tc.get("name") == "carte_donjon_etage"
            for tc in result.tool_calls_trace
        )
        asyncio.run(orch._exec_tool_calls_prompt(
            [{"name": "carte_donjon_etage",
              "arguments": {"direction": "descendre"}}],
            ctx, work, result, None,
        ))
        donjon = PartyState(data_dir=d, partie_id=PID).load()["donjon"]
        assert donjon["etage"] == 1, donjon["etage"]
        assert donjon["courant"] == [0, 0], donjon["courant"]
        # La trace contient l'appel forcé → le garde-fou ne reforcera pas.
        assert any(
            tc.get("name") == "carte_donjon_etage"
            for tc in result.tool_calls_trace
        )
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)
