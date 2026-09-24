# -*- coding: utf-8 -*-
"""Régressions de l'audit eb46aeef (MJ soigneur improvisé, incohérences).

Correctifs déployés (commit a0ed3bc + suite) :

- F1 : `fiche_perso_soigner` refuse un soin > 4 PV SANS source nommée en
      tour réel (le MJ s'auto-soignait +11 ; pas de clerc dans le groupe).
      Les appels serveur légitimes passent par `interne=True`.
- F2 : `fiche_perso_mettre_a_jour` refuse d'écrire `pv_max` (le MJ avait
      écrasé Elara 5→1, Groth 14→10).
- F3 : mémoire `donjon.salles_nettoyees` : une salle vidée ne se re-peuple
      pas (`engager_combat` refuse la même espèce dans la salle courante).
- F4 : `finir_combat` refuse si des ennemis sont ENCORE VIVANTS (le MJ
      fermait le combat par décret sans tuer les gobelins).
- F5 : quotas outils resserrés (`scenario_etape` 1, `etat_partie_patch` 2,
      `incanter_sort` 2, `fiche_perso_mettre_a_jour` 2, `fiche_perso_soigner` 3).
- F6 : la localité monde d'entrée d'un donjon est mémorisée
      (`donjon.localite_entree`) et restaurée à la sortie / au voyage —
      le MJ restait coincé « Où êtes-vous ? » sans position monde.
- F7 : « Potion de soins légers » ajoutée au catalogue PHB (p. 416) pour
      qu'une dose soit ACHETABLE (sinon `source= potion` n'avait aucun
      consommable en jeu).

Usage : py -m pytest tests/test_correctifs_eb46aeef.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    carte_donjon_entrer,
    carte_donjon_sortir,
)
from server.tools.fiches import (  # noqa: E402
    _espece_cle,
    _norm_nom_simple,
    fiche_perso_infliger_degats,
    fiche_perso_mettre_a_jour,
    fiche_perso_soigner,
)
from server.tools.state import engager_combat, finir_combat  # noqa: E402

PID = "test_eb46aeef"


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_eb46aeef_")


def _ctx(d: str, tour: str = "tour-1") -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d, tour_id=tour)


def _ecrire_fiche(d: str, nom: str, pv: int = 8, pv_max: int = 16,
                  inventaire: list | None = None) -> None:
    fiche = {
        "nom": nom, "joueur": "alain", "proprietaire": "alain",
        "race": "Demi-orc", "classe": "Barbare", "niveau": 1,
        "pv": pv, "pv_max": pv_max, "ca": 14, "bab": 1,
        "carac": {"FOR": 19, "DEX": 10, "CON": 13, "INT": 9, "SAG": 12,
                  "CHA": 9},
        "conditions": [], "xp": 0, "or": 0,
        "inventaire": inventaire or [],
        "equipement": [{"nom": o["nom"], "qte": o["qte"]}
                       for o in (inventaire or [])],
    }
    from server.persos import chemin_fiche
    with open(chemin_fiche(d, nom), "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=2)


def _etat_base() -> dict:
    return {
        "partie": "Test", "tour": 1, "phase": "exploration",
        "lieu": {"nom": "Laelith", "type": "ville", "description": "",
                 "position_x": "", "position_y": ""},
        "memoire": {}, "histoire": [], "pj": [], "monstres_combat": [],
        "donjons_exploreres": {},
    }


def _etat_combat_squelettes() -> dict:
    st = _etat_base()
    st.update({
        "phase": "combat", "tour": 2,
        "initiative": [
            {"nom": "Squelette", "init": 9},
            {"nom": "Barouk", "init": 5},
        ],
        "courant_tour_pour": "Barouk",
        "pj": [{"nom": "Barouk", "pv": 12, "pv_max": 18}],
        "monstres_combat": [
            {"nom": "Squelette", "pv": 0, "pv_max": 3, "ca": 15,
             "conditions": ["Détruit"]},
            {"nom": "Squelette (2)", "pv": 3, "pv_max": 3, "ca": 15,
             "conditions": []},
        ],
        "donjon": {"id": "Crypte du Verrou", "courant": [0, 0],
                   "localite_entree": "Vallée de l'Ombre"},
    })
    return st


# --------------------------------------------------------------------------- #
#  F1 — soin > 4 PV sans source refusé (tour réel)
# --------------------------------------------------------------------------- #
def test_f1_soin_sans_source_sur_6pv_refuse():
    d = _fresh_dir()
    _ecrire_fiche(d, "Barouk", pv=9, pv_max=16)
    ctx = _ctx(d)
    r = asyncio.run(fiche_perso_soigner(ctx, "Barouk", 6))
    assert r.text.startswith("⛔"), r.text
    assert "SOURCE" in r.text
    # Aucun PV appliqué.
    fiche = json.load(open(os.path.join(
        d, "fiches", "fiche_barouk.json"), encoding="utf-8"))
    assert fiche["pv"] == 9


def test_f1_soin_sans_source_small_ok():
    """≤ 4 PV sans source = convention kit (chargé auto si légal)."""
    d = _fresh_dir()
    _ecrire_fiche(d, "Milo", pv=9, pv_max=16)
    ctx = _ctx(d)
    r = asyncio.run(fiche_perso_soigner(ctx, "Milo", 4))
    assert not r.text.startswith("⛔"), r.text


def test_f1_soin_avec_source_potion_ok():
    d = _fresh_dir()
    _ecrire_fiche(d, "Barouk", pv=9, pv_max=16,
                  inventaire=[{"nom": "potion de soins légers", "qte": 2,
                               "poids": 0.5}])
    ctx = _ctx(d)
    r = asyncio.run(fiche_perso_soigner(
        ctx, "Barouk", 8, source="potion de soins légers"))
    assert "ré" in r.text, r.text
    fiche = json.load(open(os.path.join(
        d, "fiches", "fiche_barouk.json"), encoding="utf-8"))
    assert fiche["pv"] == 16      # 9 + 8, mais plafonné à pv_max
    pot = next(e for e in fiche["inventaire"] if "potion" in e["nom"])
    assert pot["qte"] == 1


# --------------------------------------------------------------------------- #
#  F2 — pv_max verrouillé
# --------------------------------------------------------------------------- #
def test_f2_pv_max_refuse():
    d = _fresh_dir()
    _ecrire_fiche(d, "Barouk", pv=12, pv_max=16)
    ctx = _ctx(d)
    r = asyncio.run(fiche_perso_mettre_a_jour(
        ctx, "Barouk", "pv_max", "5"))
    assert "⛔" in r.text and "pv_max" in r.text, r.text
    fiche = json.load(open(os.path.join(
        d, "fiches", "fiche_barouk.json"), encoding="utf-8"))
    assert fiche["pv_max"] == 16


def test_f2_autres_champs_toujours_modifiables():
    d = _fresh_dir()
    _ecrire_fiche(d, "Barouk", pv=12, pv_max=16)
    ctx = _ctx(d)
    r = asyncio.run(fiche_perso_mettre_a_jour(
        ctx, "Barouk", "carac.CON", "15"))
    assert not r.text.startswith("⛔"), r.text


# --------------------------------------------------------------------------- #
#  F3 — salle nettoyée ≠ re-population
# --------------------------------------------------------------------------- #
def test_f3_espece_cle_strip_les_doublons():
    assert _espece_cle("Gobelin (2)") == "gobelin"
    assert _espece_cle("Gobelin") == "gobelin"
    assert _espece_cle("Harpie") == "harpie"
    assert _norm_nom_simple("Gobelin (2)") != _norm_nom_simple("Gobelin")


def test_f3_mort_du_monstre_marque_la_salle_nettoyee():
    d = _fresh_dir()
    st = _etat_combat_squelettes()
    PartyState(data_dir=d, partie_id=PID).save(st)
    ctx = _ctx(d)
    # Tue le squelette (2) → il meurt, salle marquée nettoyée.
    r = asyncio.run(fiche_perso_infliger_degats(ctx, "Squelette (2)", 5))
    assert "DÉTRUIT" in (r.text or ""), r.text
    etat = PartyState(data_dir=d, partie_id=PID).load()
    net = (etat.get("donjon") or {}).get("salles_nettoyees") or {}
    assert "squelette" in net.get("0,0", []), net


def test_f3_re_engagement_dans_salle_nettoyee_refuse():
    d = _fresh_dir()
    # Le bestiaire est lu dans le data_dir de la partie (ici vide par
    # défaut) : on écrit un bestiaire minimal pour que les noms résolvent.
    goule = {
        "nom": "Squelette", "cle": "squelette", "type": "mort-vivant",
        "taille": "M", "fp": "1/3", "pv": 6, "pv_max": 6, "ca": 12,
    }
    gob = {
        "nom": "Gobelin", "cle": "gobelin", "type": "humanoïde",
        "taille": "P", "fp": "1/4", "pv": 5, "pv_max": 5, "ca": 15,
    }
    zm = {
        "nom": "Zombie", "cle": "zombie", "type": "mort-vivant",
        "taille": "M", "fp": "1/2", "pv": 16, "pv_max": 16, "ca": 11,
    }
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "squelette": goule, "gobelin": gob,
                   "zombie": zm}, f, ensure_ascii=False)
    st = _etat_base()
    st["donjon"] = {"id": "Crypte du Verrou", "courant": [0, 0],
                    "salles_nettoyees": {"0,0": ["gobelin", "squelette"]}}
    PartyState(data_dir=d, partie_id=PID).save(st)
    ctx = _ctx(d)
    r = asyncio.run(engager_combat(ctx, "Squelette"))
    assert "Salle déjà vidée" in (r.text or ""), r.text
    r2 = asyncio.run(engager_combat(ctx, "Gobelin"))
    assert "Salle déjà vidée" in (r2.text or ""), r2.text
    # Espèce DIFFÉRENTE (jamais nettoyée ici) : permis.
    r3 = asyncio.run(engager_combat(ctx, "Zombie"))
    assert not r3.text.startswith("⛔"), r3.text


# --------------------------------------------------------------------------- #
#  F4 — finir_combat refuse les ennemis vivants
# --------------------------------------------------------------------------- #
def test_f4_finir_combat_refuse_si_vivants():
    d = _fresh_dir()
    PartyState(data_dir=d, partie_id=PID).save(_etat_combat_squelettes())
    ctx = _ctx(d)
    r = asyncio.run(finir_combat(ctx))
    assert "⛔" in (r.text or ""), r.text
    assert "Squelette (2)" in (r.text or "")
    # Le combat reste OUVERT.
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat.get("phase") == "combat"
    assert etat.get("monstres_combat")


def test_f4_finir_combat_ok_si_tous_morts():
    d = _fresh_dir()
    st = _etat_combat_squelettes()
    # Les DEUX squelettes en terre.
    st["monstres_combat"] = [
        {"nom": "Squelette", "pv": -5, "pv_max": 3, "ca": 15,
         "conditions": ["Détruit"]},
        {"nom": "Squelette (2)", "pv": -2, "pv_max": 3, "ca": 15,
         "conditions": ["Détruit"]},
    ]
    PartyState(data_dir=d, partie_id=PID).save(st)
    ctx = _ctx(d)
    r = asyncio.run(finir_combat(ctx))
    assert "terminé" in (r.text or ""), r.text
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat.get("phase") == "exploration"
    assert not etat.get("monstres_combat")


# --------------------------------------------------------------------------- #
#  F6 — localité monde restaurée à la sortie du donjon
# --------------------------------------------------------------------------- #
def test_f6_entrer_donjon_memorise_localite():
    d = _fresh_dir()
    st = _etat_base()
    PartyState(data_dir=d, partie_id=PID).save(st)
    ctx = _ctx(d, tour="tour-6a")
    r = asyncio.run(carte_donjon_entrer(ctx, "Crypte du Verrou"))
    assert not r.text.startswith("⛔"), r.text
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert (etat.get("donjon") or {}).get("localite_entree") == "Laelith"


def test_f6_sortir_donjon_restaure_localite():
    d = _fresh_dir()
    st = _etat_combat_squelettes()   # lieu actuel = donjon (via parcours)
    st["lieu"] = {"nom": "Crypte du Verrou", "type": "donjon",
                  "description": "salle (0,0)", "position_x": 0,
                  "position_y": 0}
    st["phase"] = "exploration"
    st["monstres_combat"] = []
    st["initiative"] = []
    PartyState(data_dir=d, partie_id=PID).save(st)
    ctx = _ctx(d, tour="tour-6b")
    r = asyncio.run(carte_donjon_sortir(ctx))
    assert "quittez" in (r.text or ""), r.text
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat.get("lieu", {}).get("nom") == "Vallée de l'Ombre"
    assert etat.get("lieu", {}).get("type") == "localite"