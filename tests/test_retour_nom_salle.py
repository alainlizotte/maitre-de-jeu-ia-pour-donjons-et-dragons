# -*- coding: utf-8 -*-
# Tests unitaires du pathfinding « retour par nom de salle »
# (_pas_vers_salle_nommee, main.py) — le live est bloque par un combat
# hallucine dans la partie 60446e2f (initiative sans pv), ces tests
# verifient la mecanique BFS deterministe.

from __future__ import annotations

import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)


def _donjon() -> dict:
    """Étage 0 de la Unknown Tower (partie 60446e2f) : hall (0,0) ↔ écurie
    (1,0) ↔ salle sud (1,1), toutes visitées, portes ouvertes entre elles."""
    return {
        "id": "ro_unknown_tower",
        "etage": 0,
        "courant": [1, 0],
        "salles_visitees": ["0,0", "1,0", "1,1"],
        "grille": [
            {"x": 0, "y": 0, "type": "entrée", "visitee": True,
             "description": "Le hall d'entrée de la tour, pavé de pierre.",
             "portes": {"nord": False, "sud": False, "est": True,
                        "ouest": False}},
            {"x": 1, "y": 0, "type": "écurie", "visitee": True,
             "description": "L'écurie sombre et sèche, longue de 15 m.",
             "portes": {"nord": False, "sud": True, "est": False,
                        "ouest": True}},
            {"x": 1, "y": 1, "type": "garde", "visitee": True,
             "description": "Une salle de garde effondrée.",
             "portes": {"nord": True, "sud": False, "est": False,
                        "ouest": False}},
        ],
    }


def test_retour_type_entree_depuis_ecurie():
    """« je retourne à l'entrée de la tour » depuis l'écurie (1,0) :
    le TYPE « entrée » matche, le premier pas est l'OUEST (le hall est
    adjacent). C'est exactement le cas bloqué en live (partie 60446e2f)."""
    from server.main import _pas_vers_salle_nommee
    res = _pas_vers_salle_nommee(_donjon(),
                                 "je retourne a l entree de la tour")
    assert res is not None, "aucune cible trouvée"
    direction, libelle, distance = res
    assert direction == "ouest", direction
    assert distance == 1, distance


def test_retour_mot_description_hall():
    """« je retourne au hall » : « hall » (4 lettres) matche la description
    du hall (« Le hall d'entrée... ») → premier pas ouest."""
    from server.main import _pas_vers_salle_nommee
    res = _pas_vers_salle_nommee(_donjon(), "je retourne au hall")
    assert res is not None, "aucune cible trouvée"
    direction, libelle, distance = res
    assert direction == "ouest", direction


def test_retour_depuis_salle_lointaine_premier_pas():
    """Depuis la salle de garde (1,1), retour vers le hall (0,0) :
    le chemin passe par l'écurie (1,0) → premier pas NORD, distance 2."""
    from server.main import _pas_vers_salle_nommee
    dj = _donjon()
    dj["courant"] = [1, 1]
    res = _pas_vers_salle_nommee(dj, "je retourne a l entree de la tour")
    assert res is not None
    direction, libelle, distance = res
    assert direction == "nord", direction
    assert distance == 2, distance


def test_pas_de_direction_cardinale():
    """Un texte AVEC direction cardinale relève de 5bis-e → None ici."""
    from server.main import _pas_vers_salle_nommee
    assert _pas_vers_salle_nommee(_donjon(),
                                  "je retourne a l entree puis vers l ouest") \
        is None


def test_aucune_cible():
    """Aucune salle visitée ne correspond (« je retourne a la cuisine ») →
    None (la narration du LLM reste maître)."""
    from server.main import _pas_vers_salle_nommee
    assert _pas_vers_salle_nommee(_donjon(),
                                  "je retourne a la cuisine de la tour") is None


def test_deja_sur_place():
    """Le groupe est DÉJÀ dans la salle visée → None (pas de mouvement)."""
    from server.main import _pas_vers_salle_nommee
    dj = _donjon()
    dj["courant"] = [0, 0]  # dans le hall
    res = _pas_vers_salle_nommee(dj, "je reste dans l entree de la tour")
    # « entree » matche le type de la salle COURANTE → None
    assert res is None
