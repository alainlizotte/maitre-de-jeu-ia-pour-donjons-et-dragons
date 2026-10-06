# -*- coding: utf-8 -*-
"""Régressions de la partie 0e615b81.

1. **Loup (Lycanthrope)** : « un Loup (Lycanthrope) d'environ 2 mètres »
   narré, `engager_combat(monstres="Loup")` engageait un simple LOUP
   (FP 1, 11 PV) au lieu du Loup-garou (FP 3, 32 PV) — le mot
   « Lycanthrope » ne figure dans AUCUN nom du bestiaire. Correctifs :
   - table `_ALIASES_VARIANTE` (lycanthrope/garou → loup_garou…) consultée
     par `_find_monstre_strict` (appel direct) ;
   - `_maj_variante_specifique` : si une entrée du bestiaire est un
     SUR-ENSEMBLE de mots de la créature résolue et que la DERNIÈRE
     NARRATION mentionne le qualificatif ou un alias → la variante remplace
     la créature simple (l'équilibre est arbitré après) ;
   - `_ennemis_annonces` : les alias comptent comme mention de créature
     (engagement forcé « le lycanthrope attaque »).
2. **« ♻️ Doublon ignoré »** : le message de dédoublonnage des dégâts,
   verbeux, fuyait dans le texte visible du MJ — raccourci en note
   mécanique compacte.

Usage : py -m pytest tests/test_0e615b81_lycanthrope.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.monstres import (            # noqa: E402
    _ALIASES_VARIANTE,
    _maj_variante_specifique,
    _normalise_nom,
)


def _best_loup():
    """Bestiaire minimal loup / variantes."""
    return {"monstres": {
        "loup": {"nom": "Loup", "fp": "1", "pv": 11},
        "loup_garou": {"nom": "Loup-garou (humain)", "fp": "3", "pv": 32},
        "loup_arctique": {"nom": "Loup arctique", "fp": "5", "pv": 51},
        "gobelin": {"nom": "Gobelin", "fp": "1/3", "pv": 7},
    }}


def test_alias_table_contient_lycanthrope():
    """« lycanthrope » → loup_garou (le mot narré, absent des noms)."""
    assert _ALIASES_VARIANTE.get("lycanthrope") == "loup_garou"


def test_find_monstre_strict_resout_alias():
    """`_find_monstre_strict(ctx, "Lycanthrope")` → Loup-garou (et non
    None ni le simple loup)."""
    from server.tools.base import ToolContext
    from server.tools.monstres import _find_monstre_strict

    ctx = ToolContext(partie_id="t", joueur="t",
                      data_dir=os.path.join(
                          os.path.dirname(os.path.dirname(
                              os.path.abspath(__file__))),
                          "server", "data"))
    m = _find_monstre_strict(ctx, "Lycanthrope")
    assert m is not None
    assert "loup-garou" in str(m.get("nom", "")).lower(), m.get("nom")


def test_maj_variante_lycanthrope():
    """Narration « un Loup (Lycanthrope) d'environ 2 mètres » + résolu
    « Loup » → la variante Loup-garou remplace le loup simple."""
    best = _best_loup()
    mons = [dict(best["monstres"]["loup"])]
    notes = _maj_variante_specifique(
        mons, best,
        "Une créature émerge des ombres : un Loup (Lycanthrope) d'environ "
        "2 mètres, aux yeux brillants. Le loup charge !")
    assert notes, "la montée de variante doit s'appliquer"
    assert mons[0]["nom"] == "Loup-garou (humain)", mons[0]
    assert "Loup-garou" in notes[0]


def test_maj_variante_pas_de_faux_positif_loup_simple():
    """Un loup ORDINAIRE (aucun qualificatif dans la narration) reste un
    loup — pas de montée arbitraire vers une variante."""
    best = _best_loup()
    mons = [dict(best["monstres"]["loup"])]
    notes = _maj_variante_specifique(
        mons, best,
        "Un loup émerge des ombres, fourrure grise, et charge le groupe.")
    assert notes == []
    assert mons[0]["nom"] == "Loup"


def test_maj_variante_qualificatif_nominal():
    """« un loup arctique, plus grand qu'un cheval » → Loup arctique."""
    best = _best_loup()
    mons = [dict(best["monstres"]["loup"])]
    notes = _maj_variante_specifique(
        mons, best,
        "Un loup arctique immenses aux flancs blancs surgit de la neige.")
    assert mons[0]["nom"] == "Loup arctique", mons[0]


def test_maj_variante_rien_si_texte_vide():
    best = _best_loup()
    mons = [dict(best["monstres"]["loup"])]
    assert _maj_variante_specifique(mons, best, "") == []
    assert mons[0]["nom"] == "Loup"


def test_dedup_degats_message_compact():
    """Le refus de re-narration est une NOTE MÉCANIQUE COMPACTE (une ligne,
    pas un pavé qui fuit dans la prose du MJ)."""
    import inspect

    from server.tools import fiches
    src = inspect.getsource(fiches)
    assert "Doublon ignoré" not in src, "l'ancien libellé verbeux subsiste"
    assert "Re-narration ignorée" in src
