# -*- coding: utf-8 -*-
"""Exemption micro-déplacement (partie 15aa0b6f).

« Je quitte les lieux et me dirige chez le marchand » — le groupe est DÉJÀ
à Silverymoon : marcher jusqu'à la boutique est un micro-déplacement LIBRE
(règle 7). D1 relançait (aucun tool de déplacement) et le modèle, sans
bonne option (explorer(est) = la route du repère, voyage = l'inter-cités),
bouclait sur la copie exacte de [5] — le joueur était bloqué dans la tour.

Usage : py -m pytest tests/test_15aa0b6f_micro.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import _deplacement_local  # noqa: E402

_ETAT_VILLE = {
    "lieu": {"nom": "Silverymoon", "type": "ville"},
    "donjon": {"id": "La Couronne de Mystra", "courant": [0, 0]},
}
_ETAT_DONJON = {
    "lieu": {"nom": "La Couronne de Mystra", "type": "donjon"},
    "donjon": {"id": "La Couronne de Mystra", "courant": [0, 0]},
}


def test_sortir_vers_le_marche_est_local():
    """Quitter la tour pour le marché de Silverymoon : micro-move libre."""
    n = ("Morgoth quitte la Tour de l'Équilibre et se dirige vers le "
         "marché de Silverymoon.")
    assert _deplacement_local(n, _ETAT_VILLE) is True


def test_chez_le_marchand_est_local():
    n = "Je quitte les lieux et me dirige chez le marchand."
    assert _deplacement_local(n, _ETAT_VILLE) is True


def test_autre_ville_connue_pas_local():
    """« Je me dirige vers le repère de Zendar » / une autre cité : vrai
    voyage → PAS local (le garde/relance s'applique)."""
    n = "Je me dirige vers le repère de Zendar, au nord-est."
    assert _deplacement_local(n, _ETAT_VILLE) is False
    n2 = "Je pars vers Waterdeep."
    assert _deplacement_local(n2, _ETAT_VILLE) is False


def test_dans_un_donjon_jamais_local():
    """Dans un donjon, l'exploration reste TOUJOURS mécanique — même une
    narration mentionnant la ville voisine ne doit pas exempter."""
    n = ("Morgoth quitte la salle et pense à Silverymoon tout proche, "
         "puis explore la galerie est.")
    assert _deplacement_local(n, _ETAT_DONJON) is False


def test_vers_le_repaire_pas_local_meme_en_ville():
    n = "Je me dirige vers le repère de Zendar."
    assert _deplacement_local(n, _ETAT_VILLE) is False
