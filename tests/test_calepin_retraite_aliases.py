"""Tests des nouvelles corrections :
- Calepin du MJ (PartyState.calepin_*) + endpoints.
- Retraite/évasion de combat (retraite_combat clôt sans XP).
- Résolution des adversaires humains génériques vers le bestiaire officiel.
"""

import asyncio
import os
import shutil

import pytest

from server.game.state import PartyState, SCHEMA_PARTIE
from server.tools.base import ToolContext
from server.tools import state as tools_state
from server.tools import monstres as tools_monstres

PID = "test_calepin"
JOUEUR = "mj"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur=JOUEUR, data_dir=d)


# --------------------------------------------------------------------------- #
#  Calepin (PartyState)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def data_dir(tmp_path):
    return str(tmp_path)


def test_calepin_crud(data_dir):
    st = PartyState(data_dir=data_dir, partie_id=PID)
    # Vide au départ
    assert st.calepin_lire() == []

    # Ajout
    err, note_id = st.calepin_ajouter("Tuer le naga", False)
    assert err is None
    err, note_id2 = st.calepin_ajouter("Récupérer la couronne")
    assert err is None and note_id2 != note_id
    notes = st.calepin_lire()
    assert len(notes) == 2
    assert notes[0]["texte"] == "Tuer le naga"
    assert notes[0]["fait"] is False

    # Coche (raye)
    err = st.calepin_maj(note_id, fait=True)
    assert err is None
    n = [x for x in st.calepin_lire() if x["id"] == note_id][0]
    assert n["fait"] is True

    # Édite le texte
    err = st.calepin_maj(note_id, texte="Tuer le naga (après le sort)")
    assert err is None
    n = [x for x in st.calepin_lire() if x["id"] == note_id][0]
    assert n["texte"] == "Tuer le naga (après le sort)"

    # Suppression
    err = st.calepin_supprimer(note_id)
    assert err is None
    assert len(st.calepin_lire()) == 1


# --------------------------------------------------------------------------- #
#  Retraite / évasion
# --------------------------------------------------------------------------- #
def test_retraite_combat_clot_sans_xp(data_dir):
    ctx = _ctx(data_dir)
    st = PartyState(data_dir=data_dir, partie_id=PID)
    etat = dict(SCHEMA_PARTIE)
    etat["phase"] = "combat"
    etat["tour"] = 3
    etat["courant_tour_pour"] = "Alionor Arcanis"
    etat["initiative"] = [
        {"nom": "Naga gardien", "init": 22},
        {"nom": "Alionor Arcanis", "init": 15},
    ]
    # Un ennemi encore DEBOUT (PAS détruit) => pas de victoire serveur.
    etat["monstres_combat"] = [
        {"nom": "Naga gardien", "pv": 50, "pv_max": 93, "ca": 18,
         "fp": "10", "conditions": ["Fuite"]},
    ]
    st.save(etat)

    res = asyncio.run(tools_state.retraite_combat(ctx))
    assert res.text and "Retraite" in res.text
    reload = st.load()
    assert reload["phase"] == "exploration"
    assert reload["initiative"] == []
    assert reload["monstres_combat"] == []
    assert reload["tour"] == 0


def test_retraite_combat_refuse_hors_combat(data_dir):
    ctx = _ctx(data_dir)
    st = PartyState(data_dir=data_dir, partie_id=PID)
    st.save(dict(SCHEMA_PARTIE))  # phase = opening
    res = asyncio.run(tools_state.retraite_combat(ctx))
    assert "Aucun combat" in res.text


# --------------------------------------------------------------------------- #
#  Adversaires humains → bestiaire officiel
# --------------------------------------------------------------------------- #
def test_aliases_humains_bestiaire(tmp_path):
    d = str(tmp_path)
    # Copie le vrai bestiaire pour tester la résolution sur données réelles.
    source = os.path.join(
        os.path.dirname(__file__), "..", "server", "data", "bestiaire.json"
    )
    shutil.copy(source, os.path.join(d, "bestiaire.json"))
    ctx = ToolContext(partie_id=PID, joueur=JOUEUR, data_dir=d)

    attentes = {
        "garde": "Garde humain (guerrier 2)",
        "garde de la ville": "Garde humain (guerrier 2)",
        "des gardes": "Garde humain (guerrier 2)",
        "chenaille": "Chenaille (PHB humain)",
        "une meute de chenapans": "Chenaille (PHB humain)",
        "bandit": "Bandit humain (guerrier 1)",
        "des bandits": "Bandit humain (guerrier 1)",
        "aasimar": "Aasimar, homme d'armes de niveau 1",
        "paysans": "Chenaille (PHB humain)",
    }
    for req, canon in attentes.items():
        m = tools_monstres._find_monstre(ctx, req)
        assert m is not None, f"{req!r} devrait résoudre"
        assert m.get("nom") == canon, f"{req!r} → {m.get('nom')} != {canon}"

    # Un vrai monstre reste inchangé.
    gob = tools_monstres._find_monstre(ctx, "Gobelin")
    assert gob is not None and gob.get("nom") == "Gobelin"
