# -*- coding: utf-8 -*-
"""Rattrapage d'achat (partie 1808ebab).

Le joueur demandait explicitement (« j'achète Repas médiocre », « je veux
acheter des flèches ») : le modèle narrait la transaction en prose avec des
prix INVENTÉS (repas 5-20 PO au lieu de 3-15 pc ; flèche 5 po au lieu de
1 po les 10) sans JAMAIS appeler `marche_acheter`/`auberge_commander` —
or non débité, objet non ajouté. Le rattrapage serveur applique l'achat aux
tarifs officiels ; ces tests couvrent l'extraction intention → article.

Usage : py -m pytest tests/test_1808ebab_achats.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (          # noqa: E402
    _extraire_achat,
    _intention_achat,
)
from server import equipement_phb              # noqa: E402

_ARTICLES = list(equipement_phb.articles())


def test_intention_achat_explicite():
    """Les formulations du joueur qui ENGAGENT un achat."""
    for msg in (
        "j'Achète Repas médiocre",
        "je veux acheter des flèches",
        "Achète-moi une corde",
        "je commande un repas convenable",
        "je prends un repas avant de partir",
        "Repas médiocre",                     # choix de menu seul (2e message)
    ):
        assert _intention_achat([msg]) is not None, msg


def test_intention_achat_pas_de_faux_positif():
    """Visiter / quitter / regarder ne sont PAS des achats (partie 1808ebab :
    « je visite l'auberge » déclenchait une re-narration, pas un menu)."""
    for msg in (
        "je visite l'auberge avant de partir",
        "merci. je quitte le forgeron",
        "je visite le forgeron",
        "que fais-je ?",
    ):
        assert _intention_achat([msg]) is None, msg


def test_extraction_repas_qualite():
    """« j'Achète Repas médiocre » → auberge_commander(repas='mediocre')."""
    a = _extraire_achat("j'Achète Repas médiocre", _ARTICLES)
    assert a.get("type") == "auberge", a
    assert a.get("repas") == "mediocre", a
    assert a.get("logement") == ""


def test_extraction_chambre_defaut():
    """« une chambre pour la nuit » sans qualité → logement mediocre."""
    a = _extraire_achat("je commande une chambre pour la nuit", _ARTICLES)
    assert a.get("type") == "auberge", a
    assert a.get("logement") == "mediocre", a
    assert int(a.get("nuits") or 0) == 1


def test_extraction_fleches_quantite_lots():
    """« j'achète 50 flèches » → article officiel « Flèches (10) », 5 lots
    (le tool facture le lot officiel ; plus de « 5 po la flèche »)."""
    a = _extraire_achat("j'achète 50 flèches", _ARTICLES)
    assert a.get("type") == "marche", a
    assert a.get("article") == "Flèches (10)", a
    assert int(a.get("quantite")) == 5, a    # ceil(50/10) lots


def test_extraction_corde_catalogue():
    """« j'achète une corde » → l'article du catalogue (corde de chanvre)."""
    a = _extraire_achat("j'achète une corde", _ARTICLES)
    assert a.get("type") == "marche", a
    assert "corde" in str(a.get("article", "")).lower(), a


def test_extraction_sans_article_inconnu():
    """Intention sans article reconnu du catalogue → dict vide (le rattrapage
    ne fait rien, le MJ garde la main)."""
    assert _extraire_achat("j'achète la lune", _ARTICLES) == {}
