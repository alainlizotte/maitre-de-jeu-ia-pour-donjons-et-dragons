"""Détection de répétition narrative (écho d'une scène déjà narrée).

Cas réel observé (partie 38b48d0a) : le joueur choisit une option proposée
et le MJ re-narre mot pour mot un tour précédent — l'action est perdue et
le fil de l'histoire casse. Ces tests couvrent la détection (`trouve_repetition`)
et la relance corrective de l'orchestrateur.

Usage : py -m pytest tests/test_repetition_narrative.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    Orchestrator,
    trouve_repetition,
)
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_repet"

# Scène d'origine (narrée au tour N — issue réelle de la partie 38b48d0a).
SCENE_ESCALIERS = (
    "Vous avancez au nord dans le corridor froid, la torche dans la main. "
    "Les pierres vibrent légèrement sous vos pas, comme si le sol lui-même "
    "respirait.\n\nArrivé devant la salle suivante, vous découvrez une "
    "**grande salle d'escaliers** en pierre grise, où deux marches menent "
    "vers le haut et deux vers le bas. Une rampe en bois usé serpente le "
    "long d'un mur, mais elle semble... instable, fissurée.\n\n"
    "**Au centre de la salle**, une **lune de croissant** de pierre trône "
    "sur un piédestal, émettant une lueur verte et malade. Autour d'elle, "
    "des inscriptions latines sont gravées, partiellement effacées par le "
    "temps et... quelque chose de plus récent.\n\n**Que faites-vous ?**"
)

# Écho quasi verbatim (réponse du tour N+2 — issue réelle de la partie).
ECHO_VERBATIM = SCENE_ESCALIERS


def test_verbatim_detecte():
    """Rejouer mot pour mot une scène précédente est détecté."""
    hist = [
        Message(role="user", content="**[alain]** : je vais au nord"),
        Message(role="assistant", content=SCENE_ESCALIERS),
        Message(role="user", content="**[alain]** : je lance un projectile"),
    ]
    assert trouve_repetition(ECHO_VERBATIM, hist) is not None


def test_paraphrase_detectee():
    """Une paraphrase quasi intégrale (mêmes passages réordonnés) est détectée."""
    paraphrase = (
        "Vous avancez au nord dans le corridor froid, la torche à la main. "
        "Les pierres vibrent sous vos pas. Devant vous, une grande salle "
        "d'escaliers en pierre grise : deux marches montent, deux descendent, "
        "une rampe de bois fissurée longe le mur. Au centre, une lune de "
        "croissant de pierre sur un piédestal émet une lueur verte et malade, "
        "entourée d'inscriptions latines partiellement effacées. Que faites-vous ?"
    )
    hist = [Message(role="assistant", content=SCENE_ESCALIERS)]
    assert trouve_repetition(paraphrase, hist) is not None


def test_narration_nouvelle_non_detectee():
    """Une narration inédite qui répond à l'action n'est PAS signalée."""
    nouvelle = (
        "Vous lancez vos projectiles magiques : trois dards scintillants "
        "filent vers les statues animées. L'une d'elles s'effrite sous "
        "l'impact tandis que Thaddeus poursuit son rituel, la voix tremblante. "
        "Les deux autres gardiens avancent, leurs lames de pierre grincantes "
        "résonnent dans la salle. Que faites-vous ?"
    )
    hist = [Message(role="assistant", content=SCENE_ESCALIERS)]
    assert trouve_repetition(nouvelle, hist) is None


def test_narration_courte_ignoree():
    """Les narrations très courtes (roleplay, question) ne sont pas jugées."""
    hist = [Message(role="assistant", content=SCENE_ESCALIERS)]
    assert trouve_repetition("Bien sûr. Que faites-vous ?", hist) is None


def test_message_user_ou_system_ignores():
    """Seules les narrations assistant comptent comme référence."""
    hist = [Message(role="user", content=SCENE_ESCALIERS),
            Message(role="system", content=SCENE_ESCALIERS)]
    assert trouve_repetition(ECHO_VERBATIM, hist) is None


class ScriptedClient:
    """Première réponse = écho verbatim ; deuxième = vraie narration."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.system_recus: list[str] = []

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        for m in messages:
            if m.role == "system" and "CORRECTION" in (m.content or ""):
                self.system_recus.append(m.content)
        content = self.replies.pop(0) if self.replies else "(fin)"
        from server.llm.client import ChatResult
        return ChatResult(content=content, tool_calls=[], finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        for tok in (self.replies[-1] if self.replies else "(fin)").split(" "):
            yield tok + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def test_orchestrateur_relance_apres_repetition():
    """L'orchestrateur détecte l'écho, relance, et retient la NOUVELLE narration."""
    vrai_narration = (
        "Trois dards de force magique jaillissent de vos doigts et percutent "
        "la statue la plus proche : la pierre vole en éclats. Thaddeus achève "
        "son rituel pendant que les gardiens restants s'ébranlent avec un "
        "grincement funèbre. Que faites-vous ?"
    )
    client = ScriptedClient([ECHO_VERBATIM, vrai_narration])
    orch = Orchestrator(
        client=client,
        tools=TOOLS,
        tool_mode="prompt",
        detect_simulation=True,
        max_iterations=6,
    )
    messages = [
        Message(role="system", content="Tu es le MJ."),
        Message(role="assistant", content=SCENE_ESCALIERS),
        Message(role="user", content="**[alain]** : je lance un projectile"),
    ]
    ctx = ToolContext(partie_id=PID, joueur="alain",
                      data_dir=tempfile.mkdtemp())
    result = asyncio.run(orch.run(messages, ctx))
    assert result.narration == vrai_narration
    assert result.corrections == 1
    # Le correctif cite l'action du joueur à laquelle répondre.
    assert client.system_recus, "un message de correction devait être injecté"
    assert "je lance un projectile" in client.system_recus[0]
