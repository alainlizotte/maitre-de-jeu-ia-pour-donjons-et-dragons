"""Validation des dés de dégâts contre la FICHE du monstre attaquant.

Bug réel : `lancer_degats` validait tout jet contre le catalogue du JOUEUR.
Le Géant (froid) de taille G — « grande hache » 3d6+13 sur sa fiche
bestiaire — était rejeté au motif que la « Grande hache » du catalogue fait
1d12 (taille M) : son attaque automatique se terminait donc SANS AUCUN
dégât appliqué.

Règle implémentée : si `attaquant` est un monstre du bestiaire et que
`arme_ou_sort` nomme SON arme, les dés de SA FICHE font foi (tailles
spéciales). Sinon — attaquant absent, PJ, ou arme différente — le catalogue
s'applique comme par le passé. Un nom de sort échappe aux deux validations.

Usage : py -m pytest tests/test_validation_fiche_monstre.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.combat import ResultatBoucle, _attaque_auto  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.dice import lancer_degats  # noqa: E402

PID = "test_fiche_monstre"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEANT = "Géant (froid) de taille G"
_GEANT_DMG = 31  # 3d6 max (18) + 13 (fiche bestiaire)


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_fiche_monstre_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d, tour_id="t1")


def _bestiaire(d: str) -> None:
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )


def _etat_combat(d: str) -> None:
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["pj"] = [{
        "nom": "Brunhild", "joueur": "alain", "race": "Humaine",
        "classe": "Guerrier", "niveau": 2, "pv": 100, "pv_max": 100,
        "ca": 16, "conditions": [],
    }]
    etat["initiative"] = [{"nom": GEANT, "init": 15},
                          {"nom": "Brunhild", "init": 10}]
    etat["courant_tour_pour"] = GEANT
    etat["monstres_combat"] = [{
        "nom": GEANT, "pv": 95, "pv_max": 95, "ca": 19, "conditions": [],
        "fp": "7",
    }]
    st.save(etat)
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_brunhild.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Brunhild", "classe": "Guerrier", "niveau": 2,
                   "xp": 0, "pv": 100, "pv_max": 100, "ca": 16, "bab": 2,
                   "carac": {"FOR": 16, "DEX": 12}, "conditions": []}, f,
                  ensure_ascii=False)


async def _deg(d: str, nb_des: int, faces: int, bonus: int,
               arme: str, cible: str = "Brunhild",
               attaquant: str = "") -> str:
    r = await lancer_degats(_ctx(d), nb_des, faces, bonus, arme, cible,
                            attaquant=attaquant)
    return r.text


def test_fiche_monstre_prime_sur_catalogue():
    d = _fresh_dir()
    try:
        _bestiaire(d)
        # 3d6 conforme à la fiche du géant → accepté.
        assert (asyncio.run(_deg(d, 3, 6, 13, "grande hache",
                                attaquant=GEANT))
                .startswith("💥"))
        # Même jet SANS attaquant → rejeté par le catalogue (1d12 taille M).
        r = asyncio.run(_deg(d, 3, 6, 13, "grande hache"))
        assert r.startswith("⛔") and "catalogue" in r, r
        # Les dés du catalogue (1d12) sont rejetés contre la fiche.
        r = asyncio.run(_deg(d, 1, 12, 13, "grande hache", attaquant=GEANT))
        assert r.startswith("⛔") and "fiche du monstre" in r, r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_attaquant_pj_valide_par_catalogue():
    d = _fresh_dir()
    try:
        _bestiaire(d)
        # Un PJ n'est pas un monstre : le catalogue prime comme avant.
        r = asyncio.run(_deg(d, 1, 12, 3, "Espadon", attaquant="Brunhild"))
        assert r.startswith("⛔") and "catalogue" in r, r
        assert asyncio.run(_deg(d, 2, 6, 3, "Espadon",
                                attaquant="Brunhild")).startswith("💥")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sort_non_valide_contre_fiche():
    d = _fresh_dir()
    try:
        _bestiaire(d)
        # Un sort lancé par un monstre échappe à la fiche d'arme.
        assert (asyncio.run(_deg(d, 5, 6, 0, "boule de feu",
                                attaquant=GEANT))
                .startswith("💥"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_arme_hors_fiche_replie_sur_catalogue():
    d = _fresh_dir()
    try:
        _bestiaire(d)
        # Le géant avec une arme qui n'est pas celle de SA fiche → catalogue.
        assert asyncio.run(_deg(d, 2, 6, 0, "Espadon",
                                attaquant=GEANT)).startswith("💥")
        r = asyncio.run(_deg(d, 1, 12, 0, "Espadon", attaquant=GEANT))
        assert r.startswith("⛔") and "catalogue" in r, r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_attaque_auto_geant_inflige_degats(monkeypatch):
    d = _fresh_dir()
    try:
        _bestiaire(d)
        _etat_combat(d)
        # Jet d'attaque sûr (20 naturel) + dés max → dégâts = 3d6 max + 13.
        monkeypatch.setattr(random, "randint", lambda a, b: b)
        res = ResultatBoucle()
        asyncio.run(_attaque_auto(_ctx(d), res, GEANT, ennemi=True))
        assert not any("⛔" in e for e in res.events), res.events
        assert any("Dégâts infligés" in e for e in res.events), res.events
        assert any(f"Dégâts infligés : {_GEANT_DMG}" in e
                   for e in res.events), res.events
        with open(os.path.join(d, "fiches", "fiche_brunhild.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["pv"] == 100 - _GEANT_DMG, fiche
    finally:
        shutil.rmtree(d, ignore_errors=True)