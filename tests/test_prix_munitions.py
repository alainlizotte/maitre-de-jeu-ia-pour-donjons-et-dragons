# -*- coding: utf-8 -*-
"""Prix officiels PHB 3.5 des munitions (partie d9f65ed2).

Le forgeron proposait « Flèches (50 unités) — Prix de base : 350 po » —
70× le prix officiel (50 × 7 po = tarif de flèches de MAÎTRE, +6 po/pièce,
sans le dire). Le catalogue officiel de l'app affichait « Flèches (20) —
1 po » : taille de lot FAUSSE (PHB : flèches (10) = 1 po) et poids erroné
(1,36 kg pour 20 au lieu de 0,45 kg pour 10). Le modèle, confronté à un
paquet non officiel, improvisait l'arithmétique.

Usage : py -m pytest tests/test_prix_munitions.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.equipement_phb import articles  # noqa: E402


def _article(nom: str) -> dict:
    for a in articles():
        if a.get("nom") == nom:
            return a
    raise AssertionError(f"article absent du catalogue : {nom}")


def test_fleches_prix_et_lot_officiels():
    """Flèches (10) = 1 po = 10 pc, 0,45 kg — PHB 3.5, table 7–5."""
    a = _article("Flèches (10)")
    assert int(a["cout_pc"]) == 10, a      # 1 po = 10 pc
    assert a.get("categorie") == "munition"


def test_carreaux_prix_officiel():
    """Carreaux (10) = 1 po — PHB 3.5."""
    a = _article("Carreaux (10)")
    assert int(a["cout_pc"]) == 10, a


def test_balles_fronde_prix_officiel():
    """Balles de fronde (10) = 1 pa = 1 pc — PHB 3.5."""
    a = _article("Balles de fronde (10)")
    assert int(a["cout_pc"]) == 1, a


def test_poids_officiel_fleches_inventaire():
    """Le poids d'inventaire : 10 flèches = 0,45 kg (1 lb) — plus de lot
    « 3 lb pour 20 » gonflant la charge."""
    from server.tools.inventaire import _poids_unitaire
    from server.tools.base import ToolContext

    ctx = ToolContext(partie_id="test_prix", joueur="t",
                      data_dir=os.path.join(
                          os.path.dirname(os.path.dirname(
                              os.path.abspath(__file__))),
                          "server", "data"))
    pu = _poids_unitaire("Flèches (10)", None)
    assert pu is not None
    assert abs(float(pu) - 0.045) < 0.005, pu   # 0,45 kg / 10 flèches
