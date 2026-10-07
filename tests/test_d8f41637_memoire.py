# -*- coding: utf-8 -*-
"""Alimentation de la mémoire de campagne hors donjon (partie d8f41637).

« La mémoire fonctionne bien ? Elle enregistre bien le lieu actuel et les
autres informations ? » — NON avant ce correctif : hors donjon,
`actualiser_objectifs` sortait avant le bloc mémoire → lieux_visites,
personnages_rencontres, evenements_rencents et intrigue_resume VIDES, et un
`objectif_courant` réduit au pitch de la quête.

Usage : py -m pytest tests/test_d8f41637_memoire.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.objectifs import alimenter_memoire   # noqa: E402

_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "data")

_ETAT_HORS_DONJON = {
    "phase": "exploration",
    "lieu": {"nom": "Silverymoon", "type": "ville",
             "position_x": 53.0, "position_y": 13.0},
    "quete": {"titre": "The Crown Of Mystra",
              "source": "[ro_the_crown_of_mystra_ch01] /data/x.pdf",
              "pitch": "Retrouver la Couronne de Mystra dérobée par les "
                       "adorateurs de Cyric.",
              "bible": {}},
    "donjon": None,
    "pj": [{"nom": "Morgoth"}],
    "histoire": [
        {"evenement": "Début de l'aventure : la salle d'audience de la "
                      "Tour de l'Équilibre…"},
        {"evenement": "Entrée dans l'auberge du Mort-à-l'Aube."},
    ],
    "memoire": {},
}


def test_memoire_alimentee_hors_donjon():
    etat = json_copy(_ETAT_HORS_DONJON)
    alimenter_memoire(etat, _DATA)
    mem = etat["memoire"]
    # Lieu courant enregistré + lieux visités.
    assert mem["position"]["lieu"] == "Silverymoon"
    assert "Silverymoon" in mem["lieux_visites"]
    # PNJ du manifeste (donneur de quête).
    noms = {str(x.get("nom") or x).casefold()
            for x in mem["personnages_rencontres"]}
    assert any("teleshann" in n for n in noms), noms
    # Événements récents : miroir de l'histoire.
    assert len(mem["evenements_rencents"]) == 2
    # Objectif courant : le TITRE de la première étape de la trame, pas le
    # pitch.
    assert mem["objectif_courant"].startswith(
        "Récupérer la Couronne de Mystra"), mem["objectif_courant"]
    # Intrigue : le pitch par défaut.
    assert "Couronne" in mem["intrigue_resume"]


def test_memoire_lieux_dedupliques():
    etat = json_copy(_ETAT_HORS_DONJON)
    alimenter_memoire(etat, _DATA)
    alimenter_memoire(etat, _DATA)   # deuxième tour, même lieu
    lv = etat["memoire"]["lieux_visites"]
    assert lv.count("Silverymoon") == 1, lv


def test_memoire_sans_quete_ne_plante_pas():
    etat = {"phase": "exploration", "lieu": {"nom": "Caelbrin"},
            "histoire": [], "memoire": {}}
    alimenter_memoire(etat, _DATA)
    mem = etat["memoire"]
    assert mem["lieux_visites"] == ["Caelbrin"]
    assert mem["personnages_rencontres"] == []
    assert "objectif_courant" not in mem  # ni trame ni pitch : rien d'inventé


def json_copy(o):
    import json
    return json.loads(json.dumps(o, ensure_ascii=False))
