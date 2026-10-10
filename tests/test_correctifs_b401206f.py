# -*- coding: utf-8 -*-
"""Régressions de la partie b401206f (Crown of Mystra, intro solo Morgoth).

Le joueur dit « débute la partie » — l'intro affichée était « cassée » :
la belle scène d'ouverture re-narrée était suivie du TEXTE BRUT de l'outil
`carte_donjon_entrer` (instructions MJ « Suis FIDÈLEMENT… », « tu ne sais
RIEN de ce qu'il y a derrière », contenu canonique répété, lien carte), et
un combat INVENTÉ fut engagé dans la salle d'audience paisible de
l'ouverture (initiative Fanatique/Cultiste, phase=combat).

Chaîne du bug (logs `[dnd35] Ouverture re-narrée… (5bis-f)` + `Combat prose
rattrapé : engager_combat(Fanatique, Cultiste)`) :

1. l'ouverture est re-narrée canoniquement (5bis-f, correctif 3f52e912 :
   signal `histoire` vide — fonctionne) puis `result.narration =
   _nar_ouv + tr_ent.text` : la prose du joueur se terminait sur ~2000
   caractères de sortie d'outil MJ-facing (le ⚙️ note d'entrée, lui,
   était remplacé — privant `_narration_prose_seule` de son marqueur de
   coupe, d'où le texte mécanique stocké dans le chat) ;
2. `_HOSTILITE_IMMINENTE_RE` matchait l'EXPOSITION de mission
   (« la profondeur du **danger qui vous menace** » — narration
   abstraite, pas une créature) → `forcer_declencheur=True` ;
3. `_detecter_combat_prose` matchait les noms du bestiaire dans les
   mêmes phrases d'exposition (« Ces **fanatiques** cherchent… », « les
   repaires secrets des **cultistes** ») → `engager_combat("Fanatique,
   Cultiste")` : un combat contre des ennemis inventés dans la scène
   d'ouverture, alors que le design l'interdit (« Aucun monstre, combat
   ou image à ce tour »).

Correctifs couverts ici :
- `_hostilite_imminente` : l'exposition abstraite de mission (« danger /
  péril qui vous menace / vous guette », singulier ET pluriel) ne
  déclenche PLUS l'hostilité ; une vraie créature (« Le loup gris vous
  menace », « ils vous menacent ») déclenche toujours ;
- la garde d'ouverture du rattrapage combat et le retrait du texte d'outil
  de la prose (inline dans `_handle_say`, documentés ci-dessous) :
  l'AUTO-déclencheur (hostilité) est désarmé au tour d'ouverture SAUF si
  la salle courante a des ennemis canoniques (13/31 manifestes à entrée
  combattante) — les signaux EXPLICITES (marqueurs, dégâts, attaque
  déclarée du joueur) restent armés pour tous les scénarios.

Usage : py -m pytest tests/test_correctifs_b401206f.py -q
"""

from __future__ import annotations

import json
import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.main import (  # noqa: E402
    _hostilite_imminente,
    _HOSTILITE_IMMINENTE_RE,
)


# --------------------------------------------------------------------------- #
#  1. L'exposition de mission ne déclenche PLUS l'hostilité imminente
# --------------------------------------------------------------------------- #
def test_exposition_danger_menace_pas_de_declencheur():
    """Le déclencheur EXACT de b401206f : « la profondeur du danger qui
    vous menace » est de la narration abstraite — la créature n'existe
    pas, aucune hostilité imminente."""
    assert _hostilite_imminente(
        "La magesteresse vous observe avec un regard grave, son visage "
        "ridé reflétant la profondeur du danger qui vous menace. Elle ne "
        "sourit pas."
    ) is False


def test_exposition_peril_guette_pas_de_declencheur():
    """Autre forme d'exposition abstraite : « le péril qui vous guette »."""
    assert _hostilite_imminente(
        "Le péril qui vous guette est réel, mais la route est encore libre."
    ) is False


def test_creature_legitime_menace_declenche_encore():
    """Une VRAIE créature qui menace déclenche toujours l'hostilité
    (comportement 5b4e2bbe préservé)."""
    assert _hostilite_imminente(
        "Le loup gris vous menace, crocs baves, prêt à bondir."
    ) is True


def test_creature_legitime_guette_declenche_encore():
    assert _hostilite_imminente(
        "Un orque vous guette derrière le rocher."
    ) is True


def test_creature_legitime_apprete_declenche_encore():
    assert _hostilite_imminente(
        "Le gobelin s'apprête à attaquer, cimeterre levé."
    ) is True


def test_mixte_exposition_et_creature_declenche():
    """Une phrase mêlant exposition abstraite ET créature réelle : la
    créature suffit à déclencher."""
    assert _hostilite_imminente(
        "Le danger qui vous menace est immense, et le gobelin vous vise."
    ) is True


def test_pluriel_vous_menacent_declenche():
    """🔧 Pluriel (« ils vous menacent ») : jamais couvert avant b401206f —
    indispensable aux 13/31 manifestes dont la salle d'entrée contient des
    ennemis (« À la chasse aux gobs » : 2 gobelins, « surprise possible au
    premier round »)."""
    assert _hostilite_imminente(
        "Deux gobelins somnolent près du feu. Ils vous menacent, prêts à "
        "bondir."
    ) is True
    assert _hostilite_imminente(
        "Les molosses vous guettent derrière la cabane."
    ) is True


def test_dangers_abstraits_pluriels_pas_de_declencheur():
    """Exposition abstraite au PLURIEL (« les dangers qui vous menacent ») :
    toujours pas de créature → pas d'hostilité."""
    assert _hostilite_imminente(
        "Les dangers qui vous menacent sont réels, mais la route est libre."
    ) is False


def test_regex_base_ne_change_pas_seule():
    """Le regex brut matche toujours (« vous menace » y reste) : c'est la
    FONCTION qui filtre l'exposition abstraite — les autres call-sites ne
    sont pas cassés."""
    assert _HOSTILITE_IMMINENTE_RE.search(
        "le danger qui vous menace") is not None


def test_texte_vide_et_sans_menace():
    assert _hostilite_imminente("") is False
    assert _hostilite_imminente("Le vent souffle sur les landes.") is False


# --------------------------------------------------------------------------- #
#  2. Chaîne complète sur la vraie partie b401206f : plus aucun combat inventé
# --------------------------------------------------------------------------- #
def test_intro_b401206f_plus_aucun_combat_invente():
    """Le texte exact de l'intro b401206f : l'exposition ne déclenche plus
    l'hostilité → `forcer_declencheur=False` → la détection combat-prose
    retombe sur sa porte marqueurs/dégâts (aucun dans cette prose) →
    AUCUN type détecté, AUCUN engager_combat inventé à l'ouverture."""
    from server.config import load_config
    from server.main import _detecter_combat_prose

    data_dir = str(load_config().abs(load_config().paths.data_dir))
    chat_path = os.path.join(data_dir, "chat_b401206f.json")
    if not os.path.exists(chat_path):
        import pytest
        pytest.skip("partie b401206f absente de ce data_dir")
    with open(chat_path, encoding="utf-8") as f:
        nar = json.load(f)[1]["content"]

    assert _hostilite_imminente(nar) is False

    etat = {"phase": "exploration", "pj": [{"nom": "Morgoth"}],
            "histoire": [{"evenement": "Début de l'aventure : …"}],
            "memoire": {}}
    types = _detecter_combat_prose(
        data_dir, nar, etat,
        forcer_declencheur=_hostilite_imminente(nar),
        exclure=["Morgoth"],
    )
    assert types == [], types
