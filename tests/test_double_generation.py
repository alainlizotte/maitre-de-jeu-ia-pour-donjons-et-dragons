"""Suppression de la double génération de la narration finale.

Avant : quelle que soit la config, l'orchestrateur re-générait la narration
finale en streaming (2e appel LLM complet) même quand `stream_to_clients:
false` — main.py passait un `on_delta` qui jetait les tokens. Désormais,
sans streaming, main.py passe `on_delta=None` et l'orchestrateur réutilise
le contenu de l'appel non-streamé (`chat.content`).

Usage : py -m pytest tests/test_double_generation.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")

NARRATION = (
    "Le squelette s'ébranle vers vous, ses os cliquetant à chaque pas. "
    "La salle sent la poussière et le fer rouillé. **Que faites-vous ?**"
)


class _ClientCompte:
    """Compte les appels chat() / stream_chat()."""

    def __init__(self):
        self.chats = 0
        self.streams = 0

    async def chat(self, messages, tools=None, tool_choice=None,
                   temperature=None):
        self.chats += 1
        return ChatResult(content=NARRATION, tool_calls=[],
                          finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        self.streams += 1
        for m in NARRATION.split(" "):
            yield m + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def _messages() -> list[Message]:
    return [
        Message(role="system", content="Tu es le MJ."),
        Message(role="user", content="**[alain]** : je regarde la salle"),
    ]


def test_streaming_coupé_pas_de_seconde_génération():
    """on_delta=None → le final réutilise chat.content, zéro stream_chat."""
    client = _ClientCompte()
    orch = Orchestrator(client=client, tools=TOOLS, tool_mode="prompt")
    ctx = ToolContext(partie_id="t_dbl", joueur="alain",
                      data_dir=tempfile.mkdtemp())
    result = asyncio.run(orch.run(_messages(), ctx, on_delta=None))
    assert client.streams == 0, "sans streaming, le final ne doit PAS être re-généré"
    assert client.chats >= 1
    assert result.narration == NARRATION


def test_streaming_actif_le_final_est_streamé():
    """on_delta fourni → le final part en streaming (aperçu live conservé)."""
    client = _ClientCompte()
    orch = Orchestrator(client=client, tools=TOOLS, tool_mode="prompt")
    ctx = ToolContext(partie_id="t_dbl", joueur="alain",
                      data_dir=tempfile.mkdtemp())
    reçus: list[str] = []

    async def on_delta(tok: str) -> None:
        reçus.append(tok)

    result = asyncio.run(orch.run(_messages(), ctx, on_delta=on_delta))
    assert client.streams == 1
    assert result.narration == NARRATION
    assert NARRATION in "".join(reçus)
