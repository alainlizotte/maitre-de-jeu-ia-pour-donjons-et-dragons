# -*- coding: utf-8 -*-
"""Régressions des parties 241ece89 / 451746d7 (tests navigateur 2026-10-04/05).

Les parties sur d'autres scénarios que Crown of Mystra ont dérapé à
l'ouverture :
1. **Dead of Night** (241ece89) : le module démarre dans une TAVERNE — le MJ
   n'a jamais appelé `carte_donjon_entrer`, donc le manifeste de scénario
   (riche : « Karragen, barde mystérieux », doppelgangers, antidote, jet de
   sauvegarde vs poison) n'a JAMAIS été chargé dans l'état. L'ancre
   « LIEU DE DÉPART CANONIQUE » exigeait un donjon déjà initialisé
   (`etat["donjon"]`) : elle est restée muette et le modèle a improvisé
   depuis le résumé anglais — fusion PJ/PNJ (« Margoth, barde aux soieries
   étranges… murmure Margoth » = le PJ barbare doté du rôle du barde PNJ),
   contradiction interne (tavernier versant des fioles ET ses « corps
   taillés et déchiquetés »).
2. **Dues for the Dead** (451746d7) : même cause — manifeste jamais chargé
   (l'intro précède `carte_donjon_entrer`) ; intro tronquée (352 car.,
   début de scène perdu).

Preuve croisée : les parties Crown AVEC donjon chargé dès l'intro
(7177d819, ae358455) ont des intros ancrées (« Thukmuul Teleshann ») ;
les parties Crown SANS donjon chargé (ad077e82, e55cc855) ont des intros
improvisées. Les 31 manifestes `.donjon.json` sont déjà riches (descriptions
remplies à 100 %, validation `scripts/valider_manifestes.py` OK) — le fossé
était bien l'ancre d'intro muette hors donjon.

Correctifs (server/llm/prompt_builder.py) :
- `_lieu_depart_canonique` lit la salle d'entrée du manifeste FROIDE du
  disque (`_entree_manifeste_disque`, découverte par id de scénario/chapitre
  — même logique que `objectifs._manifest_etapes`) quand le donjon n'est
  pas encore initialisé ;
- l'ancre liste les PNJ du module de la salle d'entrée (noms propres →
  désambiguïsation PJ/PNJ : « Karragen, barde » n'est pas le PJ) et la
  note du module (doppelgangers, antidote, jets de sauvegarde).

Usage : py -m pytest tests/test_correctifs_intro_ancre.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.prompt_builder import (  # noqa: E402
    _entree_manifeste_disque,
    _lieu_depart_canonique,
)

PID = "test_intro_ancre"

# Racine data réelle du dépôt (manifestes enrichis).
DATA_REEL = Path(__file__).resolve().parent.parent / "server" / "data"

MANIFEST_TAVERNE = {
    "id": "ro_test_intro_ancre_donjon",
    "scenario": ["ro_test_intro_ancre", "ro_test_intro_ancre_ch01"],
    "donjon_id": "La taverne du test",
    "titre": "Scénario de test — taverne",
    "etages": [
        {
            "nom": "Le village à la nuit tombée",
            "entree": [0, 0],
            "salles": [
                {
                    "x": 0,
                    "y": 0,
                    "type": "entrée",
                    "portes": {"nord": False, "sud": False, "est": True, "ouest": False},
                    "description": (
                        "La taverne du village au cœur de la nuit : l'ale coule "
                        "à flots, un barde aux soieries étranges conte des "
                        "légendes, tandis que le tavernier chauve et replet "
                        "verse discrètement un sédatif dans vos chopes."
                    ),
                    "pnj": ["Karragen, barde mystérieux (neutre)"],
                    "note": (
                        "Le tavernier et ses deux servantes sont des "
                        "doppelgangers. Karragen vend l'antidote pour 30 po. "
                        "Sédatifs dans les chopes : jet de sauvegarde vs "
                        "poison (-5 si l'on ignore l'avertissement)."
                    ),
                },
                {
                    "x": 1,
                    "y": 0,
                    "type": "couloir",
                    "portes": {"nord": False, "sud": False, "est": False, "ouest": True},
                    "description": "L'arrière-salle de la taverne, plongée dans la pénombre.",
                },
            ],
        }
    ],
    "etapes": [],
}


def _data_dir_tmp(tmp_path: Path) -> Path:
    """data_dir de test avec le manifeste de la taverne sous scenarios/."""
    d = tmp_path / "scenarios" / "Test"
    d.mkdir(parents=True)
    (d / "test_intro_ancre.donjon.json").write_text(
        json.dumps(MANIFEST_TAVERNE, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return tmp_path


def _etat(quete_source: str = "[ro_test_intro_ancre] /data/scenarios/x.pdf",
         histoire=None, donjon=None) -> dict:
    return {
        "histoire": histoire or [],
        "donjon": donjon or {},
        "quete": {"source": quete_source, "titre": "Scénario de test"},
    }


# --------------------------------------------------------------------------- #
#  Ancre d'intro lue du manifeste quand le donjon n'est pas initialisé
# --------------------------------------------------------------------------- #

def test_ancre_intro_sans_donjon_depuis_disque(tmp_path):
    """Donjon ABSENT de l'état + manifeste au disque → l'ancre décrit la
    salle d'entrée du manifeste (avant : '' — intro improvisée)."""
    dd = _data_dir_tmp(tmp_path)
    ancre = _lieu_depart_canonique(_etat(), str(dd))
    assert ancre, "l'ancre doit décrire la salle d'entrée du manifeste"
    assert "taverne du village" in ancre
    assert "LIEU DE DÉPART CANONIQUE" in ancre


def test_ancre_intro_sans_donjon_matche_chapitre(tmp_path):
    """`quete.source` portant l'id de CHAPITRE (`…_ch01`) — les manifestes
    listent les ids de tous les chapitres : le match doit réussir."""
    dd = _data_dir_tmp(tmp_path)
    ancre = _lieu_depart_canonique(
        _etat(quete_source="[ro_test_intro_ancre_ch01] /data/scenarios/x.pdf"),
        str(dd),
    )
    assert ancre and "taverne du village" in ancre


def test_ancre_intro_salle_entree_non_zero(tmp_path):
    """`etages[0].entree` != (0,0) : la salle d'entrée canonique est celle
    des coords de `entree`, pas (0,0) par défaut."""
    man = json.loads(json.dumps(MANIFEST_TAVERNE))
    man["etages"][0]["entree"] = [1, 0]
    man["etages"][0]["salles"][1]["description"] = "Le porche du cimetière, sous la pluie."
    d = tmp_path / "scenarios" / "Test"
    d.mkdir(parents=True)
    (d / "test_intro_ancre.donjon.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    ancre = _lieu_depart_canonique(_etat(), str(tmp_path))
    assert "porche du cimetière" in ancre


# --------------------------------------------------------------------------- #
#  PNJ + note du module injectés (désambiguïsation PJ/PNJ)
# --------------------------------------------------------------------------- #

def test_ancre_intro_liste_pnj_module(tmp_path):
    """Les PNJ du (0,0) sont listés avec l'interdit de confusion PJ/PNJ :
    « Karragen, barde » doit apparaître comme PNJ, jamais attribué au PJ."""
    dd = _data_dir_tmp(tmp_path)
    ancre = _lieu_depart_canonique(_etat(), str(dd))
    assert "Karragen, barde mystérieux" in ancre
    assert "PNJ PRÉSENTS ICI" in ancre
    assert "ne les confonds JAMAIS avec un personnage-joueur" in ancre


def test_ancre_intro_note_module(tmp_path):
    """La note du module (doppelgangers, antidote, jet de sauvegarde) est
    injectée dans l'ancre — le MJ connaît les règles du module dès l'intro."""
    dd = _data_dir_tmp(tmp_path)
    ancre = _lieu_depart_canonique(_etat(), str(dd))
    assert "NOTE DU MODULE" in ancre
    assert "doppelgangers" in ancre
    assert "jet de sauvegarde vs poison" in ancre


def test_ancre_intro_sans_pnj_n_echoue_pas(tmp_path):
    """Salle d'entrée SANS pnj (entrée de donjon légitime) : l'ancre reste
    produite (description + note), sans la ligne PNJ."""
    man = json.loads(json.dumps(MANIFEST_TAVERNE))
    man["etages"][0]["salles"][0].pop("pnj")
    man["etages"][0]["salles"][0].pop("note")
    d = tmp_path / "scenarios" / "Test"
    d.mkdir(parents=True)
    (d / "test_intro_ancre.donjon.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    ancre = _lieu_depart_canonique(_etat(), str(tmp_path))
    assert ancre and "taverne du village" in ancre
    assert "PNJ PRÉSENTS ICI" not in ancre


# --------------------------------------------------------------------------- #
#  Comportements conservés (donjon initialisé, pas d'intro, pas de manifeste)
# --------------------------------------------------------------------------- #

def test_ancre_intro_donjon_initialise_conserve():
    """Donjon DÉJÀ initialisé dans l'état : comportement inchangé — l'ancre
    vient de la grille d'état (salle visitee=True), pas du disque."""
    donjon = {
        "id": "La taverne du test",
        "grille": [
            {"x": 0, "y": 0, "type": "entrée", "visitee": True,
             "description": "Salle d'audience de l'état initialisé.",
             "portes": {"est": True}},
        ],
    }
    ancre = _lieu_depart_canonique(_etat(donjon=donjon), str(DATA_REEL))
    assert "Salle d'audience de l'état initialisé." in ancre
    # La grille d'état n'a pas de pnj : pas de ligne PNJ (pas de lecture disque).
    assert "PNJ PRÉSENTS ICI" not in ancre


def test_ancre_intro_apres_ouverture_muette():
    """Ouverture déjà jouée (histoire non vide) : l'ancre reste muette —
    jamais re-narrer l'intro (partie 7177d819)."""
    etat = _etat(histoire=[{"evenement": "Début de l'aventure : …"}])
    assert _lieu_depart_canonique(etat, str(DATA_REEL)) == ""


def test_ancre_intro_sans_manifeste_muette():
    """Aucun manifeste au disque (quete sans source) : ancre muette, pas
    d'invention."""
    assert _lieu_depart_canonique(_etat(quete_source=""), str(DATA_REEL)) == ""
    etat = _etat(quete_source="[id_absent] /data/x.pdf")
    assert _lieu_depart_canonique(etat, str(DATA_REEL)) == ""


def test_entree_manifeste_disque_cache(tmp_path):
    """Le cache TTL : deux lectures rapprochées renvoient la même salle
    sans re-walk, et une édition passe au terme du TTL."""
    dd = _data_dir_tmp(tmp_path)
    etat = _etat()
    s1 = _entree_manifeste_disque(etat, str(dd))
    s2 = _entree_manifeste_disque(etat, str(dd))
    assert s1 is s2  # même objet (cache hit)
    # Édition du manifeste (description changée) → cache expiré → re-lecture.
    import time as _t
    _t.sleep(2.1)
    man = json.loads(json.dumps(MANIFEST_TAVERNE))
    man["etages"][0]["salles"][0]["description"] = "Description rééditée après coup."
    (dd / "scenarios" / "Test" / "test_intro_ancre.donjon.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    s3 = _entree_manifeste_disque(etat, str(dd))
    assert s3 is not s1 and "rééditée" in (s3 or {}).get("description", "")


# --------------------------------------------------------------------------- #
#  Garantie « manifeste d'entrée riche » pour TOUS les scénarios du dépôt
# --------------------------------------------------------------------------- #

def test_tous_les_scenarios_ont_une_entre_riche():
    """Chaque manifeste `.donjon.json` du dépôt fournit une ancre d'intro
    RICHE : description non vide (l'ancre n'est jamais muette) — la salle
    d'entrée canonique existe et ancre l'ouverture pour TOUS les scénarios
    (Crown of Mystra comme les autres)."""
    import glob
    manifestes = sorted(
        glob.glob(str(DATA_REEL / "scenarios" / "**" / "*.donjon.json"),
                  recursive=True)
    )
    assert manifestes, "manifestes de scénarios introuvables"
    for chemin in manifestes:
        with open(chemin, encoding="utf-8") as fh:
            man = json.load(fh)
        ids = man.get("scenario")
        ids = ids if isinstance(ids, list) else [ids]
        ids = [str(x).strip() for x in ids if x]
        stem = os.path.basename(chemin)
        for sid in ids[:1]:  # l'id principal suffit (les chapitres matchent aussi)
            etat = _etat(quete_source=f"[{sid}] /data/scenarios/x.pdf")
            ancre = _lieu_depart_canonique(etat, str(DATA_REEL))
            assert ancre, (
                f"{stem} : salle d'entrée sans description — l'intro du "
                f"scénario « {sid} » ne serait pas ancrée"
            )
            assert "LIEU DE DÉPART CANONIQUE" in ancre
            assert len(ancre) > 120, f"{stem} : ancre trop pauvre ({len(ancre)} car.)"
