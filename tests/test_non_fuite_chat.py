# -*- coding: utf-8 -*-
"""R6 : test de NON-FUITE - le filtre joueur neutralise les textes MJ.

Les blocs reels observes en campagne (shadows ch02-ch07) qui fuyaient
vers le chat : plomberie de rattrapage, refus de gardes avec listes
canoniques, consignes de narration. Le filtre `_assainir_texte_joueur`
doit les neutraliser (version joueur neutre).

Usage : py -m pytest tests/test_non_fuite_chat.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.main import _assainir_texte_joueur  # noqa: E402
from server.main import _REFUS_MJ_PREFIXES  # noqa: E402

# Les refus REELS (formes accentuees telles que diffusees par les gardes)
_REFUS_REELS = [
    ("salle videe",
     "⛔ **Salle déjà vidée** : Kobold ont DÉJÀ été éliminés dans CETTE "
     "salle — pas de re-population. Narre les cadavres/les traces de "
     "l'affrontement précédent, ou engage d'AUTRES créatures JUSTIFIÉES "
     "par la fiction.",
     ["Narre les cadavres", "DÉJÀ été éliminés", "re-population"]),
    ("hors module",
     "⛔ **Rencontre hors module refusée** : la salle contient "
     "canoniquement [Loup terrible ×1], pas Kobold. Engage UNIQUEMENT "
     "les créatures listées ci-dessus. Si la fiction réclame autre "
     "chose, narre la rencontre SANS combat.",
     ["canoniquement", "Engage UNIQUEMENT", "SANS combat"]),
    ("ecrasante",
     "🚫 **Rencontre écrasante refusée** : 182 PV cumulés contre 40 PV "
     "pour le groupe (plafond : 100, 2,5×). NE remplace PAS la "
     "créature.",
     ["NE remplace PAS", "plafond"]),
]


def test_prefixes_couvrnent_les_refus_reels():
    """Chaque refus reel est couvert par UN prefixe de la liste MJ."""
    for nom, texte, _ in _REFUS_REELS:
        couvert = any(texte.startswith(p) for p in _REFUS_MJ_PREFIXES)
        assert couvert, (nom, texte[:60])


def test_filtre_neutralise_les_refus_de_gardes():
    for nom, texte, interdits in _REFUS_REELS:
        apres = _assainir_texte_joueur(texte)
        for mot in interdits:
            assert mot not in apres, (nom, mot, apres[:200])
        assert "ne peut pas se dérouler ainsi" in apres, (
            nom, apres[:200])


def test_plomberie_rattrapage_retiree():
    texte = ("Combat engagé : Kobold. ⚙️ _Le serveur n'a pas pu engager "
             "ce combat narré en prose._ Suite de la scène.")
    apres = _assainir_texte_joueur(texte)
    assert "pas pu engager" not in apres, apres[:200]
    assert "Suite de la scène" in apres, apres[:200]


def test_filtre_conserve_le_contenu_joueur():
    avant = ("Initiative du combat - Brandt initiative 15. "
             "Gobelin (monstre) subit 6 dégâts -> PV 0/7. "
             "Fiole ajoutée à l'inventaire.")
    apres = _assainir_texte_joueur(avant)
    assert "initiative 15" in apres
    assert "subit 6 dégâts" in apres
