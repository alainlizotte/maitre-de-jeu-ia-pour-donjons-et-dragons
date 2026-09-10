"""Correctifs robustesse multi-modèles (batterie e2e 2026-09).

1. `_joueur_valide` : le joueur de la session WS fait AUTORITÉ — sinon un
   LLM qui remplit `joueur="Utilisateur"` attribue le PJ à un joueur fantôme
   et le verrou de tour de combat exclut le vrai joueur (partie bloquée).
2. `_normaliser_messages` : fusion des messages consécutifs de même rôle —
   les templates Jinja strictes (Ministral) lèvent une exception 500 sinon.
3. `tronquer_degeneration` : coupe les boucles intra-réponse (même phrase
   recopiée N fois) avant parsing/narration/réinjection.

Usage : py -m pytest tests/test_correctifs_modeles.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import Message, _normaliser_messages  # noqa: E402
from server.llm.orchestrator import tronquer_degeneration  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.fiches import _joueur_valide  # noqa: E402


# --------------------------------------------------------------------------- #
#  1. Autorité du joueur de session
# --------------------------------------------------------------------------- #
def test_joueur_session_ecrase_placeholder():
    ctx = ToolContext(partie_id="t", joueur="Elara", data_dir="x")
    assert _joueur_valide("<pseudo_joueur>", ctx) == "Elara"
    assert _joueur_valide("", ctx) == "Elara"


def test_joueur_session_ecrase_valeur_generique():
    """« Utilisateur » passé par le LLM NE doit PAS devenir le propriétaire
    du PJ — sinon le verrou de tour exclut le vrai joueur."""
    ctx = ToolContext(partie_id="t", joueur="Zarkon", data_dir="x")
    assert _joueur_valide("Utilisateur", ctx) == "Zarkon"


def test_joueur_session_ecrase_autre_nom():
    ctx = ToolContext(partie_id="t", joueur="Elara", data_dir="x")
    assert _joueur_valide("Zarkon", ctx) == "Elara"


def test_joueur_generique_rejete_hors_session():
    """Sans session (REST/tests) : les génériques → chaîne vide (tour libre)."""
    ctx = ToolContext(partie_id="t", joueur="", data_dir="x")
    assert _joueur_valide("Utilisateur", ctx) == ""
    assert _joueur_valide("user", ctx) == ""
    assert _joueur_valide("<pseudo_joueur>", ctx) == ""


def test_joueur_reel_conserve_hors_session():
    ctx = ToolContext(partie_id="t", joueur="", data_dir="x")
    assert _joueur_valide("Alice", ctx) == "Alice"


# --------------------------------------------------------------------------- #
#  2. Alternance user/assistant (templates strictes)
# --------------------------------------------------------------------------- #
def test_users_consecutifs_fusionnes():
    out = _normaliser_messages([
        Message(role="system", content="sys"),
        Message(role="user", content="a"),
        Message(role="user", content="b"),
    ])
    assert [(m.role, m.content) for m in out] == [
        ("system", "sys"), ("user", "a\n\nb"),
    ]


def test_system_mid_converti_puis_fusionne():
    """Le cas réel Ministral : correctifs système injectés en fin de
    conversation → requalifiés user → fusionnés avec le user précédent."""
    out = _normaliser_messages([
        Message(role="system", content="sys"),
        Message(role="user", content="action"),
        Message(role="assistant", content="réponse"),
        Message(role="system", content="corrige"),
        Message(role="user", content="action 2"),
    ])
    roles = [m.role for m in out]
    assert roles == ["system", "user", "assistant", "user"]
    assert out[-1].content.endswith("action 2")
    assert "corrige" in out[-1].content


def test_assistants_consecutifs_fusionnes():
    out = _normaliser_messages([
        Message(role="user", content="q"),
        Message(role="assistant", content="x"),
        Message(role="assistant", content="y"),
    ])
    assert [(m.role, m.content) for m in out] == [
        ("user", "q"), ("assistant", "x\n\ny"),
    ]


def test_vide_jete_mais_tool_calls_conserves():
    out = _normaliser_messages([
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[{"id": "1"}]),
        Message(role="assistant", content="  \n"),
    ])
    assert len(out) == 2
    assert out[1].tool_calls == [{"id": "1"}]


def test_tool_messages_groupes_intacts():
    """assistant(tool_calls) + plusieurs résultats tool : ne fusionne PAS
    (les templates autorisent les résultats groupés après tool_calls)."""
    out = _normaliser_messages([
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[{"id": "1"}]),
        Message(role="tool", tool_call_id="1", content="r1"),
        Message(role="tool", tool_call_id="1", content="r2"),
    ])
    assert [m.role for m in out] == ["user", "assistant", "tool", "tool"]


# --------------------------------------------------------------------------- #
#  3. Troncature de dégénérescence intra-réponse
# --------------------------------------------------------------------------- #
def test_boucle_tronquee():
    phrase = ("La grande hache de Groth s'abat avec toute sa force "
              "sur le zombie.")
    texte = "Intro correcte. " + " ".join([phrase] * 4)
    coupe, motif = tronquer_degeneration(texte)
    assert motif
    assert coupe.count("grande hache") == 2          # 1re + 2e occurrence
    assert "Intro correcte" in coupe


def test_texte_normal_intact():
    texte = (
        "Le couloir s'enfonce dans l'obscurité. Une porte closue apparaît. "
        "Groth la pousse d'une épaule. L'odeur de moisi les accueille. "
        "Que faites-vous ?"
    )
    coupe, motif = tronquer_degeneration(texte)
    assert motif == ""
    assert coupe == texte


def test_phrases_courtes_ignorees():
    """« Et puis. » répété n'est pas une boucle significative (trop court)."""
    texte = "Et puis. " * 6 + "Fin de la scène."
    coupe, motif = tronquer_degeneration(texte)
    assert motif == ""
    assert coupe == texte
