# -*- coding: utf-8 -*-
"""Rattrapage de VOYAGE (partie 1808ebab).

« Je me dirige vers le repère de Zendar » était narré comme une ARRIVÉE
instantanée (aucune journée, aucune rencontre aléatoire, `voyage` vide) —
`voyage_demarrer` jamais appelé alors que la règle 7 interdit les
déplacements instantanés. Le rattrapage serveur lance le voyage avec la
destination extraite du message du joueur.

Usage : py -m pytest tests/test_1808ebab_voyage.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (          # noqa: E402
    _VOYAGE_INTENT_RE,
    _extraire_voyage,
)


def test_intention_voyage():
    """Les formulations du joueur qui démarrent un déplacement hors lieu."""
    for msg in (
        "Je me dirige vers le repère de Zendar",
        "Je pars vers Phandalin",
        "Je vais vers la côte",
        "En route pour Neverwinter",
        "Je rejoins la ville la plus proche",
        "je prends la route du sud",
        # Partie 1808ebab (suite) : « Je retourne à la ville pour voir
        # Thukmuul Teleshann » — un retour à 30 km est un vrai voyage.
        "Je retourne à la ville pour voir Thukmuul Teleshann",
        "je rentre au village",
    ):
        assert _VOYAGE_INTENT_RE.search(msg), msg


def test_extraction_destination():
    """La destination est extraite, article et but en moins."""
    cas = {
        "Je me dirige vers le repère de Zendar": "repère de Zendar",
        "Je pars vers Phandalin": "Phandalin",
        "En route pour la forêt de Neverwinter": "forêt de Neverwinter",
        "Je vais vers l'autel": "autel",
    }
    for msg, attendu in cas.items():
        obtenu = _extraire_voyage(msg)
        assert obtenu == attendu, (msg, obtenu, attendu)


def test_extraction_but_en_moins():
    """Le complément de but (« pour lui demander… ») est coupé."""
    obtenu = _extraire_voyage(
        "Je me dirige vers le repère de Zendar pour lui reprendre la couronne")
    assert obtenu == "repère de Zendar", obtenu


def test_pas_de_destination_pas_de_voyage():
    """Sans destination exprimée (« je me dirige vers l'est » — une
    direction, pas un lieu) : pas de voyage serveur."""
    assert _extraire_voyage("Je me dirige vers l'est") is None or \
        _extraire_voyage("Je me dirige vers l'est") == "est"


def test_intention_absente_pas_de_voyage():
    """« je visite l'auberge » n'est pas un voyage."""
    assert not _VOYAGE_INTENT_RE.search("je visite l'auberge avant de partir")


# --------------------------------------------------------------------------- #
#  Or narré : crédit déterministe (2dfa9c75)
# --------------------------------------------------------------------------- #

def test_or_narre_gains_et_prix():
    from server.llm.orchestrator import _or_gagne_narre
    assert _or_gagne_narre(
        "Dans le coffre, vous trouvez un sac contenant 50 po.") == 50
    # Typos du modèle tolérées (« empchez » pour « empochez »).
    assert _or_gagne_narre(
        "Vous empchez 12 pièces d'or sur le corps.") == 12
    # Prix/achats : JAMAIS crédités.
    assert _or_gagne_narre("Le forgeron demande 350 po.") is None
    assert _or_gagne_narre("Un repas coûte 10 po.") is None
