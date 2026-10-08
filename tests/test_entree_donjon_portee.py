# -*- coding: utf-8 -*-
"""Garde « entrée de donjon narrée » : portée PHRASE (partie c21d0734).

Le scénario redouté par le joueur : quitter le donjon, retourner en ville
se reposer à l'auberge — et le garde 5bis-f re-forçait `carte_donjon_entrer`
parce que le LIEU (« repaire », souvenir de la mission) était dans une
phrase et le VERBE (« Bienvenue dans l'auberge », « entrer dans son
atelier ») dans une autre.

Usage : py -m pytest tests/test_entree_donjon_portee.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.main import _entree_donjon_narree  # noqa: E402


def test_entree_legitime_same_phrase():
    """L'entrée VRAIE (lieu + verbe dans la même phrase) est détectée."""
    n = ("Vous vous dirigez vers l'entrée des catacombes. Vous entrez "
         "dans les catacombes, l'obscurité vous avale.")
    assert _entree_donjon_narree(n) is True


def test_souvenir_du_repaire_plus_auberge_pas_de_reentrée():
    """Le scénario exact du joueur : souvenir du REPAIRE dans une phrase,
    entrée à l'AUBERGE dans une autre → pas de ré-entrée du donjon."""
    n = ("Morgoth repense au repaire de Nulentok en traversant les rues "
         "de Silverymoon. Il entre dans l'auberge du Mort-à-l'Aube et "
         "monte se reposer.")
    assert _entree_donjon_narree(n) is False


def test_bienvenue_auberge_pas_de_reentree():
    """L'accueil de l'aubergiste (« Bienvenue dans l'auberge ») ne déclenche
    pas l'entrée de donjon, même après une mention du repaire."""
    n = ("La Couronne se trouve dans le repaire souterrain de Nulentok. "
         "Bienvenue dans l'auberge du Mort-à-l'Aube, voyageur.")
    assert _entree_donjon_narree(n) is False


def test_entree_repaire_same_phrase_detectee():
    """L'entrée VRAIE du repaire (même phrase) reste détectée."""
    n = ("Le groupe s'engage dans le repaire de Nulentok. L'air y est "
         "lourd d'odeur de terre humide.")
    assert _entree_donjon_narree(n) is True


def test_multiligne_separe():
    """Un saut de ligne sépare les phrases : le souvenir ne contamine pas
    l'entrée suivante."""
    n = ("Il repense au donjon de Nulentok.\n"
         "Il entre dans l'atelier du forgeron.")
    assert _entree_donjon_narree(n) is False
