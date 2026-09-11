# -*- coding: utf-8 -*-
"""Dédoublonnage des narrations « brouillon supplanté » (54de40ed).

Régression observée en partie 54de40ed : le modèle narre la scène avec
« Trois Ghouls », appelle `engager_combat` — REFUSÉ (rencontre écrasante) —
puis re-narre la même scène avec « Deux Ghouls ». `_preserve_narration`
conserve les deux versions (le dédoublonnage existant ne couvrait que les
correspondances exactes) et le joueur lisait TOUTES les versions à la
suite. `_assemble_narrations` abandonne désormais tout bloc intermédiaire
fortement recouvert par un bloc ULTÉRIEUR (la version corrigée gagne).

Usage : py -m pytest tests/test_dedup_narrations.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import _assemble_narrations  # noqa: E402

# Réplication 54de40ed : même scène, seul le nombre de goules change.
BROUILLON_TROIS = (
    "Le livre ancien se laisse prendre entre vos mains, ses pages froides "
    "et humides. Cassyt s'approche, son visage illuminé par une lueur "
    "sacrée : « Laisse-moi voir... » Soudain, les symboles sur les pages "
    "commencent à briller d'une lueur rouge sang. Une voix résonne dans "
    "votre esprit, rauque et menaçante. Au même instant, les ombres dans "
    "la pièce se détachent des murs et prennent forme. Trois créatures "
    "surgissent des ténèbres : des Ghouls, leurs ossements brillant d'une "
    "lueur rougeâtre, leurs yeux injectés de sang. Que souhaitez-vous "
    "faire ? Attaquer les Ghouls ou utiliser la protection de Cassyt ?"
)
VERSION_CORRIGEE = (
    "Le livre ancien se laisse prendre entre vos mains, ses pages froides "
    "et humides. Cassyt s'approche, son visage illuminé par une lueur "
    "sacrée : « Laisse-moi voir... » Soudain, les symboles sur les pages "
    "commencent à briller d'une lueur rouge sang. Une voix résonne dans "
    "votre esprit, rauque et menaçante. Au même instant, les ombres dans "
    "la pièce se détachent des murs et prennent forme. Deux créatures "
    "surgissent des ténèbres : des Ghouls plus petits, leurs ossements "
    "brillant d'une lueur rougeâtre, leurs yeux injectés de sang. Que "
    "souhaitez-vous faire ? Attaquer les Ghouls ou utiliser la protection "
    "de Cassyt ?"
)
NARRATION_COMBAT = (
    "Vous êtes face à deux Ghouls dans la chambre de rituel, le livre "
    "ancien pulsant d'une lueur rouge sang entre vos mains. L'un d'eux "
    "bondit sur vous avec une griffe tranchante : -3 dégâts. Cassyt, pâle "
    "et tremblante, vous regarde avec inquiétude : « Ils doivent être "
    "détruits ! »"
)


def test_brouillon_54de40ed_supprime():
    """La première narration (3 goules) est supplantée par la version
    corrigée (2 goules) : seule cette dernière + le combat restent."""
    gardes = _assemble_narrations(
        [BROUILLON_TROIS, VERSION_CORRIGEE], NARRATION_COMBAT)
    assert len(gardes) == 2, gardes
    assert gardes[0] == VERSION_CORRIGEE, gardes
    assert gardes[1] == NARRATION_COMBAT, gardes
    assert "Trois" not in "\n\n".join(gardes)


def test_brouillon_dans_le_dm_final_supprime():
    """Le DM final qui reprend largement l'intermédiaire ne doit PAS
    doubler la scène : l'intermédiaire est abandonné."""
    gardes = _assemble_narrations([BROUILLON_TROIS], VERSION_CORRIGEE)
    assert len(gardes) == 1, gardes
    assert gardes[0] == VERSION_CORRIGEE


def test_intro_et_continuation_distinctes_conservees():
    """Intro de scène (avant outil) + continuation (après résultat) : deux
    contenus DIFFÉRENTS → les deux sont conservés, aucun dédoublonnage."""
    intro = (
        "Vous poussez la porte de la bibliothèque. Des rayonnages effondrés "
        "bloquent la moitié de la pièce et la poussière danse dans le "
        "faisceau de votre torche. Un byrrus intact repose sur un pupitre, "
        "clos d'un sceau de cire noire. Cassyt s'agenouille pour examiner "
        "les traces au sol près du meuble."
    )
    continuation = (
        "Le sceau cède sans résistance. À l'intérieur, un pacte signé de "
        "douze mains décrit le rituel d'éveil — et le nom du signataire "
        "principal : Rorreth. Derrière vous, la porte se referme d'un "
        "coup sec."
    )
    gardes = _assemble_narrations([intro], continuation)
    assert len(gardes) == 2, gardes
    assert gardes[0] == intro and gardes[1] == continuation


def test_transitions_courtes_jamais_supprimees():
    """Les résidus courts (sous le seuil de candidature) ne sont jamais
    supprimés, même si un bloc ultérieur les recouvre."""
    gardes = _assemble_narrations(
        ["Un instant..."], "Un instant... puis la scène bascule devant vous "
        "et les ombres se mettent à hurler en silence.")
    assert len(gardes) == 2, gardes


def test_plusieurs_brouillons_successifs():
    """Trois versions de la même scène (2 refus d'engager_combat) : seules
    la version finale corrigée + le DM restent."""
    v2 = VERSION_CORRIGEE.replace("Deux créatures", "Quatre créatures")
    gardes = _assemble_narrations(
        [BROUILLON_TROIS, VERSION_CORRIGEE, v2], NARRATION_COMBAT)
    assert len(gardes) == 2, gardes
    assert gardes[0] == v2, gardes
    assert gardes[1] == NARRATION_COMBAT, gardes
