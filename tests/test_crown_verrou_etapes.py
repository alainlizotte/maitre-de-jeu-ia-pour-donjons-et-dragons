# -*- coding: utf-8 -*-
"""Verrou de voyage, étape par étape, sur la trame Crown of Mystra.

Chaque phase du scénario doit BLOQUER et GUIDER correctement :
- étape 1 (la Couronne chez Nulentok, salle (4,0)) : un voyage vers les
  sites-gemmes est REFUSÉ avec le renvoi vers l'exploration de la salle
  (partie 2dfa9c75 : le modèle bouclait voyage→refus sans savoir où aller) ;
- étape 2 (la collecte des huit gemmes) : les voyages sont AUTORISÉS (la
  chasse se fait EN VOYAGEANT — bloquer rendrait la collecte impossible) ;
- étape 3 (la restauration à (0,0), déjà visitée) : les voyages restent
  libres (la scène de restauration se joue sur place).

Usage : py -m pytest tests/test_crown_verrou_etapes.py -q
"""

from __future__ import annotations

import json
import os

import pytest

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys = __import__("sys")
sys.path.insert(0, sys_path)

from server.game.objectifs import verrou_voyage  # noqa: E402

_MANIFEST = {
    "id": "crown_etapes_donjon",
    "scenario": ["crown_etapes"],
    "donjon_id": "La Couronne de Mystra",
    "etages": [{"nom": "Parcours", "entree": [0, 0], "salles": [
        {"x": 0, "y": 0, "type": "entrée", "portes": {"est": True},
         "visitee": True, "description": "La Tour de l'Équilibre."},
        {"x": 4, "y": 0, "type": "salle du trône", "portes": {"ouest": True},
         "description": "Le groove de Nulentok."},
        {"x": 4, "y": 1, "type": "trésor", "portes": {"nord": True},
         "description": "Les ruines de Sarr (Beljuril)."},
    ]}],
    "etapes": [
        {"cle": "couronne", "titre": "Récupérer la Couronne de Mystra chez "
         "Zendar Nulentok", "type": "objet", "salle": "4,0", "gate": True,
         "requis": [{"nom": "La Couronne de Mystra", "portee": "quete"}]},
        {"cle": "huit_gemmes", "titre": "Réunir les huit gemmes dispersées "
         "sur Faerûn", "type": "objet", "gate": True,
         "requis": [{"nom": "La baguette de téléportation",
                     "portee": "quete"},
                    {"nom": "Le Beljuril", "portee": "quete"},
                    {"nom": "Le Diamant", "portee": "quete"}]},
        {"cle": "restauration", "titre": "Restaurer la Couronne et la "
         "rendre à Mystra", "type": "etape", "salle": "0,0", "gate": True,
         "requis": [{"nom": "La Couronne de Mystra", "portee": "quete"},
                    {"nom": "Le Beljuril", "portee": "quete"},
                    {"nom": "Le Diamant", "portee": "quete"}]},
    ],
}

_GEMMES = ["La baguette de téléportation", "Le Beljuril", "Le Diamant"]


def _etat(inventaire_quete: list[str]) -> dict:
    return {
        "phase": "exploration",
        "quete": {"source": "[crown_etapes] /data/x.pdf",
                  "titre": "Crown", "pitch": "Test."},
        "donjon": {
            "id": "La Couronne de Mystra",
            "courant": [0, 0],
            "grille": _MANIFEST["etages"][0]["salles"],
        },
        "pj": [{"nom": "Morgoth"}],
        "memoire": {},
        "inventaire_test": inventaire_quete,
    }


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """data_dir : manifeste + fiche dont l'inventaire de quête est piloté
    par la variable `INV_TEST` (mutée par les tests)."""
    data = tmp_path / "data"
    (data / "scenarios" / "T").mkdir(parents=True)
    (data / "fiches").mkdir(parents=True)
    (data / "scenarios" / "T" / "crown_etapes.donjon.json").write_text(
        json.dumps(_MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    fiche = {"nom": "Morgoth", "inventaire": [], "or": 1000}
    (data / "fiches" / "fiche_morgoth.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")

    import server.game.objectifs as obj
    import server.tools.inventaire as inv_mod

    def _inventaire_fake(fiche_):
        objs = []
        for nom in globals().get("INV_TEST", []):
            objs.append({"nom": nom, "qte": 1, "poids": 0.1,
                         "portee": "quete", "partie": "test_crown"})
        return objs

    def _inv_quete_fake(etat, dd, partie_id=""):
        noms = list(globals().get("INV_TEST", []))
        # Le vrai tool renvoie les CLÉS normalisées (consommables par
        # `_accompli` : `_cle_objet(n) in inv_possede`).
        return ([obj._cle_objet(n) for n in noms],
                [dict(nom=n) for n in noms])

    monkeypatch.setattr(obj, "inventaire_quete", _inv_quete_fake)
    monkeypatch.setattr(inv_mod, "_inventaire", _inventaire_fake)
    return str(data)


def test_etape1_refuse_et_guide_vers_salle(data_dir):
    """Étape 1 courante (Couronne manquante) : voyage vers les sites-gemmes
    REFUSÉ + guidance salle (4,0) + salle/portes courantes (2dfa9c75)."""
    globals()["INV_TEST"] = ["Le parchemin de la route",
                             "La fiole de vérité"]
    refus = verrou_voyage(_etat(["Le parchemin de la route"]), data_dir,
                          "test_crown", "Beljuril")
    assert refus, "le voyage vers Beljuril doit être refusé (étape 1)"
    assert "salle 4,0" in refus
    assert "carte_donjon_explorer" in refus
    assert "(0,0)" in refus and "est" in refus  # position + porte courante


def test_etape2_collecte_voyages_autorises(data_dir):
    """Étape 2 courante (Couronne en poche, gemmes manquantes) : type
    « objet » SANS salle = collecte mondiale → les voyages ne sont PAS
    bloqués (la chasse se fait en voyageant)."""
    globals()["INV_TEST"] = ["La Couronne de Mystra"]
    refus = verrou_voyage(_etat(["La Couronne de Mystra"]), data_dir,
                          "test_crown", "Ruines de Sarr")
    assert refus is None, refus
    refus2 = verrou_voyage(_etat(["La Couronne de Mystra"]), data_dir,
                           "test_crown", "Waterdeep")
    assert refus2 is None, refus2


def test_etape3_salle_visitee_voyages_libres(data_dir):
    """Étape 3 (restauration à (0,0) — DÉJÀ visitée) : le voyage n'est pas
    bloqué, la scène de restauration se joue sur place."""
    globals()["INV_TEST"] = _GEMMES + ["La Couronne de Mystra"]
    refus = verrou_voyage(
        _etat(_GEMMES + ["La Couronne de Mystra"]), data_dir,
        "test_crown", "Waterdeep")
    assert refus is None, refus


def test_etape2_gate_exige_quand_meme_les_requis_pour_la_salle(data_dir):
    """L'objet de l'étape 2 (Beljuril) reste soumis au verrou de
    DÉPLACEMENT du donjon (zone scellée) — hors périmètre de ce test, mais
    la salle du trésor ne devient visitable qu'après l'étape 1."""
    # (La vérification fine du verrou_deplacement est couverte par
    # objectifs.py : `zone_etapes` + verrou_deplacement.)
    from server.game.objectifs import _zone_etapes
    etat = _etat(["La Couronne de Mystra"])
    etat["donjon"]["grille"][2]["visitee"] = False
    zone, etapes = _zone_etapes(etat, data_dir, "test_crown")
    # (4,1) = Sarr : verrouillée tant que l'étape 2 n'est pas courante…
    # en fait ici l'étape 2 EST courante (couronne en poche) → aucune zone
    # active ne doit contenir Sarr.
    assert (4, 1) not in zone, zone
