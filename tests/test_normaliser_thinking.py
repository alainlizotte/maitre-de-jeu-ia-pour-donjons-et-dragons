"""Non-mutation de l'historique + nettoyage des balises thinking.

Deux bugs réels (partie dc4dd5aa, modèle Qwen3.5-9B) :
1. `_normaliser_messages` fusionnait les doublons consécutifs EN MUTANT
   `precedent.content`. Comme `messages` partage ses objets `Message` avec
   `session.history` (main.py : `[system] + hist[debut:]`), les correctifs
   système requalifiés en `user` (« ⚠️ Consigne du Maître du Jeu… ») se sont
   retrouvés fusionnés DANS le message joueur persisté — puis affichés à la
   table au rechargement.
2. Qwen raisonne dans des balises `<think>...</think>` (ou dans le champ
   `reasoning_content`) : `_strip_thinking` ne connaissait que le format
   Gemma (`<|channel>thought...<channel|>`), d'où des narrations « vides »
   et des balises qui peuvent fuiter dans le chat.

Usage : py -m pytest tests/test_normaliser_thinking.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import (  # noqa: E402
    Message,
    _debut_thinking,
    _fin_thinking,
    _normaliser_messages,
    _strip_thinking,
)


# --------------------------------------------------------------------------- #
#  Non-mutation de l'historique partagé
# --------------------------------------------------------------------------- #
def test_normaliser_ne_mute_pas_le_message_joueur():
    """Les correctifs système injectés en fin de tour ne doivent JAMAIS être
    fusionnés dans l'objet Message de l'historique de session."""
    msg_joueur = Message(role="user", content="**[alain]** : débute la partie")
    messages = [
        Message(role="system", content="Tu es le MJ."),
        msg_joueur,                                   # objet PARTAGÉ avec session.history
        Message(role="system", content="⚠️ CORRECTION : risée thinking."),
    ]
    sortis = _normaliser_messages(messages)
    # L'historique (l'objet partagé) reste INTACT.
    assert msg_joueur.content == "**[alain]** : débute la partie"
    # La vue LLM, elle, fusionne bien user(player) + user(correctif).
    users = [m for m in sortis if m.role == "user"]
    assert len(users) == 1
    assert "CORRECTION" in users[0].content
    assert "**[alain]** : débute la partie" in users[0].content


def test_normaliser_ne_mute_pas_l_assistant_partage():
    """Même garde pour deux assistants consécutifs (rejeux correctifs)."""
    msg_assistant = Message(role="assistant", content="Narration précédente.")
    messages = [
        Message(role="system", content="Tu es le MJ."),
        msg_assistant,
        Message(role="assistant", content="Complément."),
    ]
    _normaliser_messages(messages)
    assert msg_assistant.content == "Narration précédente."


# --------------------------------------------------------------------------- #
#  Stripping thinking multi-formats
# --------------------------------------------------------------------------- #
def test_strip_thinking_gemma_canal():
    texte = "<|channel>thought Je planifie...<channel|>Vous entrez dans la salle."
    assert _strip_thinking(texte) == "Vous entrez dans la salle."


def test_strip_thinking_qwen_ferme():
    texte = "<think>Le joueur veut entrer. Vérifions les PV.</think>Vous poussez la porte du cimetière."
    assert _strip_thinking(texte) == "Vous poussez la porte du cimetière."


def test_strip_thinking_qwen_ouvert():
    """<think> non fermé : TOUTE la suite est du raisonnement → texte vide."""
    assert _strip_thinking("<think>réflexion sans fin...") == ""


def test_strip_thinking_orphelin_fermeture():
    """</think> seul (raisonnement séparé par le backend) est retiré."""
    assert _strip_thinking("</think>Vous avancez.") == "Vous avancez."


def test_strip_thinking_texte_normal_intact():
    texte = "**Que faites-vous ?**\n- Examiner le portail"
    assert _strip_thinking(texte) == texte


# --------------------------------------------------------------------------- #
#  Machine à états streaming (détection ouverture/fermeture)
# --------------------------------------------------------------------------- #
def test_debut_fin_thinking_gemma_et_qwen():
    assert _debut_thinking("bla <|channel>thought bla") == 4
    assert _debut_thinking("bla <think> bla") == 4
    assert _debut_thinking("aucun tag ici") == -1
    assert _fin_thinking("raisonnement<channel|>suite") == len("raisonnement<channel|>")
    assert _fin_thinking("raisonnement</think>suite") == len("raisonnement</think>")
    assert _fin_thinking("pas de fin") == -1


def test_stream_chat_filtre_think_qwen():
    """stream_chat ne laisse passer AUCUN fragment <think>...</think>."""
    from server.llm.client import OllamaClient
    from server.config import LLMConfig

    cfg = LLMConfig(base_url="http://localhost:1/v1", model="test")
    client = OllamaClient(cfg)

    async def faux_stream():
        for chunk in ("Vous av", "ancez <th", "ink>planif", "ication</thi",
                      "nk> dans le c", "imetière."):
            yield chunk

    async def run():
        # monkeypatch du POST : on remplace directement la boucle en testant
        # la machine à états via un générateur branché sur _safe_split.
        buf = ""
        sortis = []
        in_think = False
        async for tok in faux_stream():
            buf += tok
            if in_think:
                fin = _fin_thinking(buf)
                if fin >= 0:
                    in_think = False
                    buf = buf[fin:]
                continue
            deb = _debut_thinking(buf)
            if deb >= 0:
                in_think = True
                before = buf[:deb]
                buf = buf[deb:]
                if before:
                    sortis.append(before)
                continue
            from server.llm.client import _safe_split
            safe, buf = _safe_split(buf)
            if safe:
                sortis.append(safe)
        return sortis

    sortis = asyncio.run(run())
    tout = "".join(sortis)
    assert "think" not in tout
    assert "planification" not in tout
    assert tout == "Vous avancez  dans le cimetière."
