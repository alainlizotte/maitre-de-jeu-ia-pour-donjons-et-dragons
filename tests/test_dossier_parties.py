# -*- coding: utf-8 -*-
"""Test : les fichiers de partie vivent dans un sous-dossier dédié parties/.

Le dossier de données ne doit pas mélanger les parties enregistrées avec
les autres fichiers importants (bestiaire, fiches, cartes, scénarios…).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.session import PartySession  # noqa: E402
from server.game.state import PartyState  # noqa: E402


def test_etat_partie_dans_sous_dossier_parties():
    """PartyState.save écrit dans data_dir/parties/ (pas à la racine)."""
    d = tempfile.mkdtemp(prefix="dossier_parties_")
    st = PartyState(data_dir=d, partie_id="abc123")
    err = st.save({"phase": "exploration", "pj": []})
    assert err is None, err
    cible = os.path.join(d, "parties", "partie_abc123.json")
    assert os.path.isfile(cible), cible
    # Rien à la racine du dossier de données.
    racine = os.path.join(d, "partie_abc123.json")
    assert not os.path.exists(racine), racine
    # Round-trip.
    st2 = PartyState(data_dir=d, partie_id="abc123")
    assert st2.load()["phase"] == "exploration"
    # Le tmp atomique vit à côté de sa cible.
    residues = [f for f in os.listdir(os.path.join(d, "parties"))
                if f.endswith(".tmp")]
    assert not residues, residues


def test_chat_dans_sous_dossier_parties():
    """PartySession persiste chat_*.json dans data_dir/parties/."""
    d = tempfile.mkdtemp(prefix="dossier_chat_")
    sess = PartySession(partie_id="abc123", data_dir=d)
    sess.transcript.clear()
    sess._persist_history()
    cible = os.path.join(d, "parties", "chat_abc123.json")
    assert os.path.isfile(cible), cible
    assert not os.path.exists(os.path.join(d, "chat_abc123.json"))
    # Rechargement : le chat est lu depuis le sous-dossier.
    sess2 = PartySession(partie_id="abc123", data_dir=d)
    sess2.hydrate_history()
    assert os.path.isfile(
        sess2._team_chat_path) is False  # jamais écrit : OK


def test_chemins_coherents_entre_objets():
    """state/session/main s'accordent sur le même sous-dossier."""
    d = tempfile.mkdtemp(prefix="dossier_coherent_")
    st = PartyState(data_dir=d, partie_id="x")
    sess = PartySession(partie_id="x", data_dir=d)
    parent = os.path.join(str(d), "parties")
    assert str(st.path.parent) == parent
    assert str(sess._chat_path.parent) == parent
    assert str(sess._team_chat_path.parent) == parent
    # Le fichier d'état porte toujours le nom partie_<id>.json.
    assert st.path.name == "partie_x.json"
    assert sess._chat_path.name == "chat_x.json"
    assert sess._team_chat_path.name == "team_chat_x.json"
    # JSON valide côté lecture (fichier absent -> état neuf, pas d'erreur).
    assert PartyState(data_dir=d, partie_id="x").load().get(
        "meta", {}).get("date_creation")
