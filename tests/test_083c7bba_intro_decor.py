# -*- coding: utf-8 -*-
"""Garde « décor d'intro non ancré » (partie 083c7bba).

L'intro faisait 1331 caractères (au-dessus du seuil de longueur) mais
SAUTAIT le décor : « Vous avez accepté la mission » avant le premier mot —
l'acceptation déjà faite, zéro description, l'ancre du manifeste ignorée.
La garde doit vérifier le CONTENU : le décor canonique de la salle d'entrée
doit être ancré dans la narration.

Usage : py -m pytest tests/test_083c7bba_intro_decor.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import _decor_ancre_absent  # noqa: E402

_ANCRE = (
    "LIEU DE DÉPART CANONIQUE — la scène d'ouverture s'y déroule : "
    "entrée (0,0) — « Haut lieu de Silverymoon, la salle d'audience de la "
    "Tour de l'Équilibre s'ouvre sur la cité par de hautes baies de verre "
    "dépoli : marbre laiteux, tapis célestes et parfum d'encens. » …"
)

_INTRO_DECOR_SAUTE = (
    "Thukmuul Teleshann observe Morgoth avec une lueur d'approbation dans "
    "ses yeux. « Vous avez accepté la mission, je le vois bien. Voici donc "
    "les objets qui vous seront nécessaires pour accomplir cette quête. » "
    "Elle tend une fiole de verre finement gravé à Morgoth. "
    "Thukmuul attend que Morgoth examine les objets."
)

_INTRO_ANCRE = (
    "La salle d'audience de la Tour de l'Équilibre, haut lieu de "
    "Silverymoon, s'ouvre sur la cité par de hautes baies de verre dépoli : "
    "marbre laiteux, tapis célestes et parfum d'encens. La magesteresse "
    "Thukmuul Teleshann observe Morgoth. « Vous avez accepté la mission. »"
)


def test_decor_saute_detecte():
    """L'intro qui démarre sur la remise d'objets SANS décor → relance."""
    assert _decor_ancre_absent(_INTRO_DECOR_SAUTE, _ANCRE) is True


def test_decor_ancre_valide():
    """L'intro ancrée dans le décor canonique → pas de relance."""
    assert _decor_ancre_absent(_INTRO_ANCRE, _ANCRE) is False


def test_sans_ancre_pas_de_controle():
    """Pas d'ancre (manifeste sans description) → jamais de relance décor."""
    assert _decor_ancre_absent(_INTRO_DECOR_SAUTE, "") is False


def test_seuil_tolerant_un_tiers_des_mots():
    """Une intro qui REFORMULE en gardant quelques mots forts du décor
    (« salle d'audience », « Silverymoon »…) n'est pas rejetée."""
    reformulee = (
        "Au premier étage de Silverymoon, la salle d'audience baigne dans la "
        "lumière du matin. Teleshann y attend Morgoth pour la mission.")
    assert _decor_ancre_absent(reformulee, _ANCRE) is False
