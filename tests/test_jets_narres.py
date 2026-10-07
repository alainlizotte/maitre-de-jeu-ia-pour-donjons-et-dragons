# -*- coding: utf-8 -*-
"""Garde roll-to-confirm : jets de compétence RÉUSSIS narrés sans dé.

« Il fouille la pièce et réussit sa Perception » — la réussite ouvrait un
verrou, un piège ou un trésor SANS mécanique (règle 2 : le dé précède la
prose). Le garde relance une fois : `lancer_d20` d'abord, puis la
narration du résultat officiel — réussite OU échec.

Usage : py -m pytest tests/test_jets_narres.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import _jet_reusse_narre  # noqa: E402


def test_reussite_discretion_detectee():
    n = ("Morgoth fouille la pièce, examine les recoins et réussit sa "
         "Discrétion. Il se glisse derrière le tonneau.")
    assert _jet_reusse_narre(n), "la réussite narrée doit être détectée"


def test_reussite_perception_avant_competence():
    """L'ordre inverse (« Perception réussie ») est aussi détecté."""
    n = ("Perception réussie : Morgoth repère la fissure dans le mur et "
         "la porte s'ouvre.")
    assert _jet_reusse_narre(n), "Perception réussie doit être détectée"


def test_jet_avec_de_legitime_pas_relançe():
    """Le jet CITÉ avec son dé (résultat officiel rejoué) n'est pas une
    violation — la garde ne relance pas en boucle."""
    n = ("Le lancer_d20 donne 14 au d20 : le jet de Discrétion réussit, "
         "la porte s'ouvre.")
    assert _jet_reusse_narre(n) is None


def test_echec_narre_hors_fenetre_pas_detecte():
    """Un ÉCHEC narré loin de toute compétence nommée : pas de relance
    (l'échec narré ne corrompt pas l'état mécanique)."""
    n = ("Morgoth tente sa chance contre le coffre-fort, mais la "
         "gâchette bloque et rien ne bouge.")
    assert _jet_reusse_narre(n) is None


def test_nom_pnj_avec_competence_pas_un_jet():
    """Un PNJ nommé d'après une compétence (« Maître des animaux ») sans
    mot de réussite : pas de détection."""
    n = ("Le groupe rencontre un maître des animaux itinérant près de "
         "l'auberge.")
    assert _jet_reusse_narre(n) is None


def test_tiroir_coffre_competence_detecte():
    """La famille de cas réelle : fouille + compétence + issue mécanique."""
    cas = (
        "Il escalade la paroi mouillée et réussit son Escalade.",
        "Réussite en Diplomatie : le gardien laisse passer le groupe.",
        "Morgoth réussit à forcer la serrure (Escamotage).",
    )
    for n in cas:
        assert _jet_reusse_narre(n), n
