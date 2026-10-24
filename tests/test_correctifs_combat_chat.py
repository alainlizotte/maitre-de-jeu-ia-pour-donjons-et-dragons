# -*- coding: utf-8 -*-
"""Correctifs #1/#3 (demande joueur — post-runs hurl/shadows) :

1. ⚔️ COMBAT DÉTERMINISTE AU CHAT : un message d'attaque du joueur actif
   est résolu par le serveur (`combat_action`, même cœur que le bouton ⚔️)
   SANS appeler le LLM. `_intention_attaque_chat` reconnaît verbe + cible
   engagée vivante et refuse les faux positifs (préparation d'action,
   esquive, futur, alliés, ennemis morts, sorts, dialogue).
2. ⚔️ CONTEXTE RÉDUIT EN COMBAT : le résumé de bible est resserré
   (600 car. au lieu de 2000) quand la phase est combat ; le budget
   historique est plafonné à 12 000 caractères (même phase).

Usage : py -m pytest tests/test_correctifs_combat_chat.py -q
"""

from __future__ import annotations

import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.llm.prompt_builder import _scenario_bible_bloc  # noqa: E402
from server.main import (  # noqa: E402
    _intention_attaque_chat,
    _norm_nom_cible_chat,
)


def _mo(nom="Gobelin", pv=5, conds=None, allie=False):
    return {"nom": nom, "pv": pv, "conditions": conds or [],
            "allie": allie}


# ------------------------------------------------------------------ #
# 1. Parseur d'intention d'attaque
# ------------------------------------------------------------------ #

def test_attaque_verbe_et_cible():
    assert _intention_attaque_chat(
        "Brandt frappe Gobelin de sa hache !",
        [_mo("Gobelin"), _mo("Kobold")]) == "Gobelin"


def test_attaque_je_prefixe():
    assert _intention_attaque_chat(
        "j'attaque le Kobold", [_mo("Kobold")]) == "Kobold"


def test_tir_distance():
    vivants = [_mo("Kobold"), _mo("Kobold archer")]
    # Le plus long appariement gagne : « kobold archer » > « kobold ».
    assert _intention_attaque_chat(
        "je tire une flèche sur le Kobold archer", vivants) == "Kobold archer"


def test_sans_nom_ennemi_unique():
    assert _intention_attaque_chat(
        "j'attaque !", [_mo("Gobelin")]) == "Gobelin"


def test_sans_nom_plusieurs_ennemis_ambigu():
    assert _intention_attaque_chat(
        "j'attaque !", [_mo("Gobelin"), _mo("Kobold")]) == ""


def test_preparation_refusee():
    assert _intention_attaque_chat(
        "je me prépare à attaquer le gobelin qui sortira",
        [_mo("Gobelin")]) == ""


def test_esquive_refusee():
    assert _intention_attaque_chat(
        "j'esquive l'attaque du loup", [_mo("Loup")]) == ""


def test_futur_refuse():
    assert _intention_attaque_chat(
        "je frapperai le gobelin au prochain tour", [_mo("Gobelin")]) == ""


def test_ennemi_mort_ignore():
    assert _intention_attaque_chat(
        "j'attaque le gobelin", [_mo("Gobelin", pv=0)]) == ""


def test_detruit_ignore():
    assert _intention_attaque_chat(
        "j'attaque le squelette",
        [_mo("Squelette", conds=["Détruit"])]) == ""


def test_allie_ignore():
    assert _intention_attaque_chat(
        "j'attaque le gobelin", [_mo("Gobelin", allie=True)]) == ""


def test_accents_et_casse_normalises():
    assert _norm_nom_cible_chat("Loup-garou (Humain)") == "loup-garou (humain)"
    assert _intention_attaque_chat(
        "je frappe le loup-garou humain",
        [_mo("Loup-garou (humain)")]) == "Loup-garou (humain)"


def test_sort_passe_au_llm():
    assert _intention_attaque_chat(
        "je lance un sort de soin sur Brandt", [_mo("Gobelin")]) == ""


def test_dialogue_passe_au_llm():
    assert _intention_attaque_chat(
        "je demande au gobelin de se rendre", [_mo("Gobelin")]) == ""


def test_message_vide_sans_ennemis():
    assert _intention_attaque_chat("", []) == ""
    assert _intention_attaque_chat("bonjour", None) == ""


# ------------------------------------------------------------------ #
# 2. Contexte réduit en combat (résumé de bible)
# ------------------------------------------------------------------ #

def test_bible_resume_resserre_en_combat():
    quete = {"bible": {"resume": "R" * 3000}}
    bloc = _scenario_bible_bloc(quete, etat={"phase": "combat"})
    ligne = [l for l in bloc.splitlines() if "Résumé du scénario" in l][0]
    assert ("R" * 601) not in ligne
    assert ("R" * 600) in ligne


def test_bible_resume_complet_hors_combat():
    quete = {"bible": {"resume": "R" * 1500}}
    bloc = _scenario_bible_bloc(quete, etat={"phase": "exploration"})
    ligne = [l for l in bloc.splitlines() if "Résumé du scénario" in l][0]
    assert ("R" * 1500) in ligne
