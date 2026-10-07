# -*- coding: utf-8 -*-
"""Rattrapage butin non-quête + ventes (partie 2dfa9c75).

1. « vous trouvez une épée longue » dans la salle dont le `tresor` du
   manifeste mentionne cet objet, sans tool → l'objet n'atteignait JAMAIS
   l'inventaire. Rattrapage : `inventaire_ajouter(portee="auto")` sur
   rapprochement prose ↔ trésor canonique de la salle.
2. « je vends mes rations » → `marche_vendre` (revente officielle 50 %,
   or crédité).

Usage : py -m pytest tests/test_butin_et_ventes.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (           # noqa: E402
    _extraire_achat,
    _intention_achat,
    _butin_salle_courante,
)
from server import equipement_phb               # noqa: E402

_ARTICLES = list(equipement_phb.articles())


def test_intention_vente_detectee():
    for msg in ("je vends mes rations", "Je vends la corde",
                "je vendrais bien mon arc"):
        assert _intention_achat([msg]) is not None, msg


def test_extraction_vente_rations():
    a = _extraire_achat("je vends mes rations", _ARTICLES)
    assert a.get("type") == "vente", a
    assert "ration" in str(a.get("article", "")).lower(), a
    assert int(a.get("quantite") or 0) >= 1


def test_butin_salle_courante_rapproche_tresor():
    """« vous trouvez une épée longue » + `tresor` de la salle mentionnant
    une épée longue → l'objet est renvoyé pour l'ajout."""
    etat = {
        "donjon": {
            "id": "Le donjon d'audit",
            "courant": [2, 1],
            "grille": [
                {"x": 2, "y": 1, "type": "trésor",
                 "tresor": "Une épée longue fine et une dague de cérémonie "
                           "repose sur un socle de velours."},
            ],
        },
        "pj": [{"nom": "Morgoth"}],
    }
    narration = ("Le coffre s'ouvre : vous trouvez une épée longue au "
                 "fourreau noir.")
    objet = _butin_salle_courante(narration, etat)
    assert objet and "épée" in objet.lower(), objet


def test_butin_hors_tresor_pas_de_rapprochement():
    """Un objet qui ne recoupe PAS le trésor canonique de la salle → None
    (pas d'objet inventé)."""
    etat = {
        "donjon": {
            "id": "Le donjon d'audit",
            "courant": [2, 1],
            "grille": [
                {"x": 2, "y": 1, "type": "couloir",
                 "tresor": "Une épée longue fine sur un socle de velours."},
            ],
        },
        "pj": [{"nom": "Morgoth"}],
    }
    narration = "Vous trouvez une licence de pêche dans le tiroir."
    assert _butin_salle_courante(narration, etat) is None


def test_butin_salle_sans_tresor_none():
    etat = {
        "donjon": {
            "id": "D", "courant": [0, 0],
            "grille": [{"x": 0, "y": 0, "type": "couloir"}],
        },
        "pj": [{"nom": "Morgoth"}],
    }
    assert _butin_salle_courante(
        "Vous trouvez une épée longue.", etat) is None
