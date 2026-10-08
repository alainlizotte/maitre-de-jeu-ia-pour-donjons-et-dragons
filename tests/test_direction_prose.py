# -*- coding: utf-8 -*-
"""2e étage du garde D1 (partie 89174f50) : le serveur applique l'exploration.

Au 2e déplacement narré consécutif sans tool (la relance a déjà échoué une
fois), le serveur applique LUI-MÊME `carte_donjon_explorer` dans la
direction exprimée par la prose — restreinte aux portes ouvertes de la
salle courante.

Usage : py -m pytest tests/test_direction_prose.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import _direction_depuis_prose  # noqa: E402

_ETAT = {
    "donjon": {
        "id": "La Couronne de Mystra",
        "courant": [0, 0],
        "grille": [
            {"x": 0, "y": 0, "type": "entrée",
             "portes": {"est": True, "sud": False}},
        ],
    },
}


def test_direction_est_unique_porte_ouverte():
    assert _direction_depuis_prose(
        "Morgoth franchit la porte et s'engage vers l'est.", _ETAT) == "est"


def test_direction_ambigue_sans_porte():
    """Le nord est mentionné mais la porte nord est fermée → None."""
    n = "Morgoth hésite entre le nord et la rivière à l'ouest."
    assert _direction_depuis_prose(n, _ETAT) is None


def test_sans_direction_none():
    assert _direction_depuis_prose(
        "Morgoth fouille la pièce.", _ETAT) is None


def test_direction_deux_portes_ouvertes_une_seule_narree():
    etat2 = {"donjon": {"id": "D", "courant": [0, 0], "grille": [
        {"x": 0, "y": 0, "type": "entrée",
         "portes": {"est": True, "sud": True}}]}}
    assert _direction_depuis_prose(
        "Il prend le couloir sud.", etat2) == "sud"
