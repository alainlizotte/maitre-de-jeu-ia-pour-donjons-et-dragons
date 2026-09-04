"""Prompts d'illustration de salle : traduction FR→EN obligatoire.

Le template `lieu_prompt` est anglais ; Qwen-Image lit le mot à mot. Un
« entrée » français y donnait un PLAT de nourriture (false friend) et un
slug de donjon français cassait la phrase. Vérifié ici :

- types de salle FR mappés en anglais (avec accents, apostrophes, composés) ;
- noms/slugs de donjons traduits mot à mot, repli générique si mot inconnu ;
- articles a/an corrects, aucun terme français ne fuit dans le prompt.

Usage : py -m pytest tests/test_lieu_prompt_traduction.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.image.helpers import lieu_prompt  # noqa: E402


def test_entree_ne_donne_pas_un_plat():
    """Le bug d'origine : « entrée » doit donner une ENTRANCE HALL, pas un plat."""
    p = lieu_prompt("entrée", "sanctuaire_des_ombres")
    assert "entrance hall" in p
    assert "entrée" not in p
    assert "sanctuary of the shadows" in p
    assert "sanctuaire" not in p


def test_types_salles_mappes():
    assert "throne room" in lieu_prompt("salle du trône", "crypte")
    assert "treasure vault" in lieu_prompt("trésor", "crypte")
    assert "staircase" in lieu_prompt("escaliers", "crypte")
    assert "altar room" in lieu_prompt("autel", "crypte")
    assert "library" in lieu_prompt("bibliothèque", "crypte")


def test_slug_donjon_traduit():
    p = lieu_prompt("crypte", "la_tombe_des_rois_serpents")
    assert "tomb of the kings serpents" in p
    assert "la_" not in p and "serpents," not in p.split("tomb of the kings")[0]


def test_mot_inconnu_repli_generique():
    """Un nom de donjon intraduisible → « ancient dungeon » (pas de franglais)."""
    p = lieu_prompt("salle vide", "donjon_zkwxyv_inconnu")
    assert "ancient dungeon" in p
    assert "zkwxyv" not in p


def test_articles_an():
    assert "an entrance hall" in lieu_prompt("entrée", "crypte")
    assert "an ossuary" in lieu_prompt("ossuaire", "crypte")
    assert "a throne room" in lieu_prompt("salle du trône", "crypte")
    assert "an ancient dungeon" in lieu_prompt("salle vide", "")


def test_vide_et_deja_anglais():
    p = lieu_prompt("", "")
    assert p.startswith("interior illustration of a room in an ancient dungeon")
    # « dungeon » seul est enrichi en « ancient dungeon » (descripteur visuel).
    assert "a room in an ancient dungeon" in lieu_prompt("room", "dungeon")