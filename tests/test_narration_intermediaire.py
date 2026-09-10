"""Préservation des narrations produites EN MÊME TEMPS que des appels d'outils.

Cas réel observé (partie 43234a00) : au message « débute la partie », le MJ
narrait la scène d'ouverture (arrivée au cimetière, présentation de Thalric,
son offre) PUIS appelait `memoire_lieu`/`memoire_personnage` dans la MÊME
réponse. La prose narrée à côté des tool_calls entrait dans le contexte du
LLM mais n'était jamais diffusée : le joueur ne voyait que la queue
(« Thalric attend votre réponse… ») sans l'intro de scène.

Ces tests couvrent la collecte (_preserve_narration) en mode natif et en
mode prompt, la préfixation à la narration finale, la diffusion streaming
et l'ignorance des transitions courtes.

Usage : py -m pytest tests/test_narration_intermediaire.py -q
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
PID = "test_narr_inter"

# Scène d'ouverture « perdue » (style de la partie 43234a00).
INTRO = (
    "La porte du cimetière de Valhingen grince sur vos épaules. Au-delà des "
    "murailles moussues, les stèles dessinent des allées silenced par la "
    "bruine du matin, et un clocher penché signale le sanctuaire de "
    "Kelemvor. Un jeune homme en robe grise s'approche à pas rapides : il se "
    "nomme Thalric, clerc de Kelemvor, et il vous tend un parchemin scellé "
    "où l'on devine une liste de noms barrés — les morts récemment "
    "réveillés. « J'ai besoin de lames courageuses », murmure-t-il."
)

# Queue effectivement reçue par le joueur (seule la narration FINALE était
# diffusée avant le correctif).
FINAL = (
    "**Thalric attend votre réponse.**\n\n"
    "Il vous regarde avec une espérance teintée d'inquiétude. Derrière lui, "
    "le vent souffle à travers les tombes. **Que faites-vous ?**"
)


class _ClientOutillage:
    """Client scripté : 1er chat = narration + tool_calls, suivants = FINAL.

    `mode` reproduit les deux canaux d'appel observés en production :
    - "natif"  : tool_calls OpenAI natifs (llama.cpp --jinja) ;
    - "prompt" : balise <tool name="..."> dans le content (mode prompt).
    """

    def __init__(self, mode: str = "native", premiere_prose: str = INTRO):
        self.mode = mode
        self.premiere_prose = premiere_prose
        self.appels_chat = 0

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        self.appels_chat += 1
        if self.appels_chat == 1:
            if self.mode == "natif":
                return ChatResult(
                    content=self.premiere_prose,
                    tool_calls=[{
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "memoire_lieu",
                            "arguments": {"nom": "Phlan",
                                          "notes": "Cimetière de Valhingen."},
                        },
                    }],
                    finish_reason="tool_calls",
                    raw={},
                )
            # Mode prompt : balise <tool> noyée dans la narration.
            return ChatResult(
                content=(
                    self.premiere_prose
                    + "\n\n"
                    + "<tool name=\"memoire_lieu\" nom=\"Phlan\" "
                    + "notes=\"Cimetière de Valhingen.\">\n"
                ),
                tool_calls=[],
                finish_reason="stop",
                raw={},
            )
        return ChatResult(content=FINAL, tool_calls=[],
                          finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        for morceau in FINAL.split(" "):
            yield morceau + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def _orch(client, mode: str) -> Orchestrator:
    return Orchestrator(
        client=client,
        tools=TOOLS,
        tool_mode=mode,
        detect_simulation=True,
        max_iterations=6,
    )


def _run(mode: str, premiere_prose: str = INTRO):
    client = _ClientOutillage(mode=mode, premiere_prose=premiere_prose)
    orch = _orch(client, mode)
    messages = [
        Message(role="system", content="Tu es le Maître du Jeu."),
        Message(role="user", content="**[alain]** : débute la partie"),
    ]
    ctx = ToolContext(partie_id=PID, joueur="alain",
                      data_dir=tempfile.mkdtemp())
    stream: list[str] = []

    async def on_delta(tok: str) -> None:
        stream.append(tok)

    result = asyncio.run(orch.run(messages, ctx, on_delta=on_delta))
    return result, stream


def test_natif_narration_accolée_aux_tools_préservée():
    """Mode natif : l'intro narrée avec le tool_call précède la narration
    finale (dm + historique) au lieu d'être jetée."""
    result, stream = _run("native")
    # La narration finale démarre par l'intro de scène…
    assert result.narration.startswith(INTRO), result.narration[:200]
    # …et se termine par la queue réellement générée en dernier.
    assert FINAL in result.narration
    # L'intro a été conservée comme narration intermédiaire.
    assert result.narrations_intermediaires == [INTRO]
    # Le tool a réellement tourné.
    assert any(tc.get("name") == "memoire_lieu"
               for tc in result.tool_calls_trace)
    # L'intro a aussi été diffusée en direct (aperçu streaming).
    assert INTRO in "".join(stream)


def test_prompt_balise_tool_narration_préservée():
    """Mode prompt : même comportement quand l'appel passe par <tool ...>."""
    result, stream = _run("prompt")
    assert result.narration.startswith(INTRO)
    assert FINAL in result.narration
    assert any(tc.get("name") == "memoire_lieu"
               for tc in result.tool_calls_trace)
    assert INTRO in "".join(stream)


def test_transition_courte_non_préservée():
    """Une prose de transition (< 200 chars) autour d'un appel ne doit PAS
    être montrée ni préfixée : seule la vraie narration l'est."""
    transition = "Je consulte d'abord l'état de la partie."
    result, _stream = _run("native", premiere_prose=transition)
    assert result.narrations_intermediaires == []
    assert result.narration == FINAL


def test_pas_de_doublon_si_intro_répétée():
    """Le même passage collecté deux fois (modèle qui répète son intro en
    ré-enrobant un appel) n'est préfixé qu'une seule fois."""
    client = _ClientOutillage(mode="natif")
    balise = ("<tool name=\"memoire_personnage\" nom=\"Thalric\" "
              "notes=\"Clerc de Kelemvor.\">\n")

    async def chat(messages, tools=None, tool_choice=None, temperature=None):
        client.appels_chat += 1
        if client.appels_chat == 1:
            return ChatResult(
                content=INTRO + "\n\n" + balise,
                tool_calls=[{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "memoire_lieu",
                        "arguments": {"nom": "Phlan"},
                    },
                }],
                finish_reason="tool_calls",
                raw={},
            )
        if client.appels_chat == 2:
            # 2e itération : le modèle répète son intro + un autre appel.
            return ChatResult(
                content=INTRO + "\n\n" + balise,
                tool_calls=[{
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "memoire_personnage",
                        "arguments": {"nom": "Thalric"},
                    },
                }],
                finish_reason="tool_calls",
                raw={},
            )
        return ChatResult(content=FINAL, tool_calls=[],
                          finish_reason="stop", raw={})

    client.chat = chat  # type: ignore[method-assign]
    orch = _orch(client, "native")
    messages = [
        Message(role="system", content="Tu es le Maître du Jeu."),
        Message(role="user", content="**[alain]** : débute la partie"),
    ]
    ctx = ToolContext(partie_id=PID, joueur="alain",
                      data_dir=tempfile.mkdtemp())
    result = asyncio.run(orch.run(messages, ctx))
    assert result.narrations_intermediaires == [INTRO]
    assert result.narration.count(INTRO) == 1
