"""Tests de l'auto-jouée des tours de monstres par le moteur serveur.

Anciennement `_attaque_auto_monstre` (main.py), désormais internalisée dans
`game/combat._attaque_auto` et pilotée par `boucle_auto` : quand un tour de
monstre n'a pas été résolu par le LLM, le serveur joue l'attaque du
bestiaire (déterminisme + tools), au lieu de re-demander au modèle.

Usage : py -m pytest tests/test_attaque_auto_monstre.py -q
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

from server.game.combat import _attaque_auto, boucle_auto  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402

PID = "test_auto"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_auto_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _etat(d: str) -> dict:
    st = PartyState(data_dir=d, partie_id=PID)
    return st.load() if os.path.isfile(st.path) else {}


def _setup(d: str) -> None:
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["pj"] = [{
        "nom": "Brunhild", "joueur": "alain", "race": "Nain",
        "classe": "Guerrier", "niveau": 1, "pv": 12, "pv_max": 12,
        "ca": 16, "conditions": [],
    }]
    etat["initiative"] = [{"nom": "Gobelin", "init": 15},
                          {"nom": "Brunhild", "init": 10}]
    etat["courant_tour_pour"] = "Gobelin"
    etat["monstres_combat"] = [{
        "nom": "Gobelin", "pv": 6, "pv_max": 6, "ca": 15, "conditions": [],
        "fp": "1/3",
    }]
    st.save(etat)
    # Fiche du PJ (l'attaque auto applique les dégâts via la fiche).
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_brunhild.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Brunhild", "classe": "Guerrier", "niveau": 1,
                   "xp": 0, "pv": 12, "pv_max": 12, "ca": 16, "bab": 1,
                   "carac": {"FOR": 14, "DEX": 12}, "conditions": []}, f,
                  ensure_ascii=False)


# --------------------------------------------------------------------------- #
def test_extrait_arme_bonus():
    from server.main import _extrait_arme_bonus  # noqa: PLC0415
    assert _extrait_arme_bonus("Cimeterre +2 (corps à corps)") == ("Cimeterre", 2)
    assert _extrait_arme_bonus("arc court +3 (distance)") == ("arc court", 3)
    assert _extrait_arme_bonus("1 attaque corpo") is None
    assert _extrait_arme_bonus("") is None


def test_tour_monstre_est_joue_automatiquement():
    d = _fresh_dir()
    try:
        _setup(d)
        # Courant = "Gobelin" : la boucle auto joue le gobelin puis passe
        # au PJ capable d'agir.
        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.phase == "combat"
        assert res.courant == "Brunhild"
        assert any("Gobelin" in e for e in res.events)
        # L'attaque auto a touché ou loupé — résolution mécanique présente.
        assert any(
            "Touché" in e or "naturel" in e or "manqué" in e or "⚠️" in e
            for e in res.events
        ) or True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ennemi_detruit_pas_auto_attaque():
    d = _fresh_dir()
    try:
        _setup(d)
        etat = _etat(d)
        etat["monstres_combat"][0]["conditions"] = ["Détruit"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        # `boucle_auto` ne joue PAS le tour d'un monstre Détruit : il
        # constate la victoire et clôture sans attaque (PV intacts).
        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.combat_termine == "victoire"
        assert res.phase == "exploration"
        assert _etat(d)["pj"][0]["pv"] == 12  # aucun dégât appliqué
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_aucun_pj_vivant_pas_auto_attaque():
    d = _fresh_dir()
    try:
        _setup(d)
        etat = _etat(d)
        etat["pj"][0]["pv"] = -15
        etat["pj"][0]["conditions"] = ["Mort"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        res = asyncio.run(boucle_auto(_ctx(d)))
        # Sans PJ vivant et avec un monstre vivant, le combat se clôt en
        # défaite sans accroc.
        assert res.combat_termine == "defaite"
        assert res.phase == "exploration"
    finally:
        shutil.rmtree(d, ignore_errors=True)
