# -*- coding: utf-8 -*-
"""Transcription d'affichage complète (run 090ec0af, demande joueur).

`session.history` est plafonné à 50 événements (contexte LLM) — mais le
payload `joined` le rejouait tel quel à chaque remontée du client (F5,
consultation de la fiche, reconnexion) : `setMessages` remplaçait le fil
accumulé en direct et tout ce qui dépassait 50 événements disparaissait
de l'écran (les actions des combats notamment). Désormais :
- `session.transcript` porte la transcription INTÉGRALE (persistée),
- `hydrate_history` recharge transcript complet + fenêtre LLM,
- `payload_historique()` (payload du `joined`) sert la transcription.

Usage : py -m pytest tests/test_transcript_affichage.py -q
"""

from __future__ import annotations

import json
import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.session import PartySession  # noqa: E402


def _remplir(s: PartySession, n: int) -> None:
    """n tours joueur + MJ (avec mécanique sur un tour sur deux)."""
    for i in range(n):
        s.remember_player_message("Alain", f"action {i}")
        s.remember_assistant(
            f"narration {i}",
            mecanique=f"🎲 mécanique {i}" if i % 2 == 0 else "",
        )


def test_transcript_complete_history_fenetree(tmp_path):
    s = PartySession(partie_id="tr_t", data_dir=str(tmp_path),
                     max_history_events=50, max_transcript_events=400)
    _remplir(s, 60)
    assert len(s.transcript) == 120          # 60 user + 60 assistant
    assert len(s.history) == 50              # fenêtre LLM (50 MESSAGES)
    # La fenêtre LLM = la FIN de la transcription.
    assert s.history[-1].content == "narration 59"
    assert s.history[0].content == "**[Alain]** : action 35"


def test_transcript_persistee_et_rechargee(tmp_path):
    s = PartySession(partie_id="tr_t", data_dir=str(tmp_path),
                     max_history_events=50, max_transcript_events=400)
    _remplir(s, 60)
    # Le FICHIER porte la transcription intégrale (120 entrées).
    fichier = json.load(open(
        tmp_path / "chat_tr_t.json", encoding="utf-8"))
    assert len(fichier) == 120

    # Nouvelle session (rechargement) : transcript complet, fenêtre LLM.
    s2 = PartySession(partie_id="tr_t", data_dir=str(tmp_path),
                      max_history_events=50, max_transcript_events=400)
    s2.hydrate_history()
    assert len(s2.transcript) == 120
    assert len(s2.history) == 50
    assert s2.transcript[0].content == "**[Alain]** : action 0"


def test_mecanique_survit_partout_dans_la_transcription(tmp_path):
    s = PartySession(partie_id="tr_m", data_dir=str(tmp_path),
                     max_history_events=50, max_transcript_events=400)
    _remplir(s, 60)
    s2 = PartySession(partie_id="tr_m", data_dir=str(tmp_path),
                      max_history_events=50, max_transcript_events=400)
    s2.hydrate_history()
    # Tous les tours pairs (0..58) portent leur mécanique — y compris les
    # plus anciens, hors fenêtre LLM.
    mec = [m for m in s2.transcript
           if m.role == "assistant" and m.mecanique]
    assert len(mec) == 30
    assert mec[0].mecanique == "🎲 mécanique 0"
    assert mec[-1].mecanique == "🎲 mécanique 58"


def test_payload_historique_complet_avec_mecanique(tmp_path):
    """Le payload du `joined` : la transcription ENTIÈRE (c'était la
    fenêtre 50 avant — cause de la disparition à l'écran)."""
    s = PartySession(partie_id="tr_p", data_dir=str(tmp_path),
                     max_history_events=50, max_transcript_events=400)
    _remplir(s, 60)
    payload = s.payload_historique()
    assert len(payload) == 120
    assert payload[0]["content"] == "**[Alain]** : action 0"
    assert payload[1].get("mecanique") == "🎲 mécanique 0"
    # Aucun message tool (contexte LLM) dans le payload d'affichage.
    assert all(p["role"] in ("user", "assistant") for p in payload)


def test_oublier_message_retire_des_deux(tmp_path):
    s = PartySession(partie_id="tr_o", data_dir=str(tmp_path))
    _remplir(s, 3)
    avant_t, avant_h = len(s.transcript), len(s.history)
    s.oublier_dernier_message_joueur()
    assert len(s.transcript) == avant_t - 1
    assert len(s.history) == avant_h - 1


def test_plafond_transcript_borne_la_memoire(tmp_path):
    s = PartySession(partie_id="tr_c", data_dir=str(tmp_path),
                     max_history_events=10, max_transcript_events=20)
    _remplir(s, 30)                          # 60 messages
    assert len(s.transcript) == 20
    assert len(s.history) == 10
    # Les plus RÉCENTS sont conservés.
    assert s.transcript[-1].content == "narration 29"
