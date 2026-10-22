# -*- coding: utf-8 -*-
"""Tests du tool `reprouver_morts_vivants` (option B, demande joueur).

Le Réprouver était résolu EN PROSE par le LLM — qui roulait le Charisme
du MAUVAIS personnage (partie 09f9f8a6 : « jet de CHA de Flick » pour le
sort d'Aurora). Le tool serveur rend la mécanique déterministe :
classe/niveau/CHA lus sur la fiche, DV des morts-vivants engagés lus sur
le bestiaire, table 3.5 et dégâts de répulsion appliqués serveur.

Usage : py -m pytest tests/test_reprouver.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_reprouver"

_BESTIAIRE = {
    "_meta": {"source": "test"},
    "squelette": {"cle": "squelette", "nom": "Squelette",
                  "type": "Mort-vivant", "dv": "1/2 d8", "pv": 3,
                  "pv_max": 3, "ca": 13, "init": 1, "fp": "1/3"},
    "zombie": {"cle": "zombie", "nom": "Zombie", "type": "Mort-vivant",
               "dv": "2d12+3", "pv": 16, "pv_max": 16, "ca": 12,
               "init": -1, "fp": "1"},
    "gobelin": {"cle": "gobelin", "nom": "Gobelin",
                "type": "Humanoïde (gobelinoid)", "dv": "1d8+1", "pv": 5,
                "pv_max": 5, "ca": 15, "init": 1, "fp": "1/3"},
}


def _env(tmp_path, monstres=None, classe="Clerc", niveau=1, cha=16):
    """data_dir : clerc Aurora (niveau/CHA paramétrables) + combat engagé
    contre les morts-vivants demandés (défaut : 2 squelettes)."""
    data = tmp_path / "data"
    (data / "fiches").mkdir(parents=True)
    (data / "bestiaire.json").write_text(
        json.dumps(_BESTIAIRE, ensure_ascii=False), encoding="utf-8")
    carac = {"FOR": 10, "DEX": 12, "CON": 14, "INT": 10, "SAG": 14,
             "CHA": cha}
    (data / "fiches" / "fiche_aurora.json").write_text(json.dumps({
        "nom": "Aurora", "classe": classe, "niveau": niveau,
        "carac": carac, "pv": 8, "pv_max": 8,
    }, ensure_ascii=False), encoding="utf-8")
    mons = []
    for i, m in enumerate(monstres if monstres is not None
                          else ["Squelette", "Squelette"]):
        base = _BESTIAIRE[m.lower()]
        mons.append({"nom": m + (f" ({i + 1})" if i else ""), "pv": base["pv"],
                     "pv_max": base["pv"], "ca": base["ca"],
                     "fp": base["fp"], "conditions": [], "degats_recus": 1})
    (data / f"partie_{PID}.json").write_text(json.dumps({
        "phase": "combat", "tour": 1,
        "initiative": [{"nom": "Aurora", "init": 5}] + [
            {"nom": m["nom"], "init": 2} for m in mons],
        "monstres_combat": mons,
        "pj": [{"nom": "Aurora", "classe": classe, "niveau": niveau,
                "carac": carac}],
    }, ensure_ascii=False), encoding="utf-8")
    return str(data)


def _reprouver(dd: str, qui: str = "Aurora") -> str:
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=dd)
    r = asyncio.run(invoke_tool(TOOLS["reprouver_morts_vivants"], ctx,
                                {"nom_personnage": qui}))
    return r.text


def _piper_des(monkeypatch, jet: int, degats_repulsion: int = 12):
    """Pipe les dés du tool : jet de répulsion et 2d6 de dégâts."""
    import server.tools.sorts as sorts_mod

    class _DesPipes:
        @staticmethod
        def randint(a, b):
            if a == 1 and b == 20:
                return jet
            if a == 1 and b == 6:
                return degats_repulsion // 2
            return a

    monkeypatch.setattr(sorts_mod, "random", _DesPipes)


# --------------------------------------------------------------------------- #
#  Refus légitimes
# --------------------------------------------------------------------------- #
def test_refus_classe_non_clerc(tmp_path, monkeypatch):
    _piper_des(monkeypatch, jet=20)
    dd = _env(tmp_path, classe="Guerrier")
    refus = _reprouver(dd)
    assert "n'a PAS le pouvoir" in refus, refus


def test_refus_paladin_trop_bas_niveau(tmp_path, monkeypatch):
    _piper_des(monkeypatch, jet=20)
    dd = _env(tmp_path, classe="Paladin", niveau=1)
    refus = _reprouver(dd)
    assert "niveau 3" in refus, refus


def test_refus_hors_combat(tmp_path, monkeypatch):
    _piper_des(monkeypatch, jet=20)
    dd = _env(tmp_path)
    etat = PartyState(data_dir=dd, partie_id=PID).load()
    etat["phase"] = "exploration"
    PartyState(data_dir=dd, partie_id=PID).save(etat)
    refus = _reprouver(dd)
    assert "EN COMBAT" in refus, refus


def test_refus_aucun_mort_vivant(tmp_path, monkeypatch):
    _piper_des(monkeypatch, jet=20)
    dd = _env(tmp_path, monstres=["Gobelin"])
    refus = _reprouver(dd)
    assert "Aucun mort-vivant" in refus, refus


def test_paladin_niveau_3_reprouve(tmp_path, monkeypatch):
    """Paladin 3 = niveau effectif 1 (niveau − 2) : le pouvoir s'ouvre."""
    _piper_des(monkeypatch, jet=20)
    dd = _env(tmp_path, classe="Paladin", niveau=3)
    rep = _reprouver(dd)
    assert "Réussite" in rep, rep


# --------------------------------------------------------------------------- #
#  Jet DÉTERMINISTE — le bon modificateur, la bonne cible
# --------------------------------------------------------------------------- #
def test_jet_roule_avec_le_cha_du_clerc(tmp_path, monkeypatch):
    """LE bug 09f9f8a6 : le jet doit utiliser le CHA du CLERC (16 → +3),
    pas celui d'un autre PJ — et contre DD = 10 + DV du plus fort
    (Squelette = 1/2 DV → plafonné à 1 DV → DD 11)."""
    _piper_des(monkeypatch, jet=8)          # 8 + 3 = 11 ≥ DD 11 → réussite
    dd = _env(tmp_path, cha=16)
    rep = _reprouver(dd)
    assert "d20=8" in rep and "+3 (CHA 16)" in rep, rep
    assert "DD **11**" in rep, rep
    assert "Réussite" in rep, rep


def test_echec_du_jet_aucun_effet(tmp_path, monkeypatch):
    _piper_des(monkeypatch, jet=2)          # 2 + 3 = 5 < 11
    dd = _env(tmp_path)
    rep = _reprouver(dd)
    assert "Échec" in rep, rep
    etat = PartyState(data_dir=dd, partie_id=PID).load()
    for m in etat["monstres_combat"]:
        assert "Repoussé" not in (m["conditions"] or []), m
        assert "Détruit" not in (m["conditions"] or []), m


# --------------------------------------------------------------------------- #
#  Effets : répulsion / destruction / plafond de DV
# --------------------------------------------------------------------------- #
def test_reussite_repousse_les_zombies(tmp_path, monkeypatch):
    """Clerc 1 vs Zombies (2 DV) : plafond de répulsion 2 DV (jet 15) →
    REPoussés (le niveau 1 < 2×2 DV, donc pas détruits)."""
    _piper_des(monkeypatch, jet=15)
    dd = _env(tmp_path, monstres=["Zombie"])
    rep = _reprouver(dd)
    assert "REPOUSSÉ" in rep, rep
    etat = PartyState(data_dir=dd, partie_id=PID).load()
    for m in etat["monstres_combat"]:
        assert "Repoussé" in (m["conditions"] or []), m


def test_clerc1_detruit_les_squelettes(tmp_path, monkeypatch):
    """Règle 3.5 : un Clerc dont le niveau vaut au moins le double des DV
    DÉTRUIT au lieu de repousser — niveau 1 ≥ 2 × 0,5 DV (squelettes)."""
    _piper_des(monkeypatch, jet=15)
    dd = _env(tmp_path)
    rep = _reprouver(dd)
    assert "DÉTRUIT" in rep, rep
    etat = PartyState(data_dir=dd, partie_id=PID).load()
    for m in etat["monstres_combat"]:
        assert "Détruit" in (m["conditions"] or []), m


def test_mort_vivant_trop_puissant_resiste(tmp_path, monkeypatch):
    """Zombie (2 DV) vs Clerc 1 avec jet faible : plafond de répulsion
    0,5 DV < 2 DV → résiste même si le jet « réussit » au DD."""
    _piper_des(monkeypatch, jet=8)          # 8 + 3 = 11 ≥ DD 12? NON — DD
    # du zombie = 10 + 2 DV = 12 → 11 < 12 : ÉCHEC du jet lui-même.
    # On monte le jet pour réussir le DD mais rester bas sur la table :
    _piper_des(monkeypatch, jet=9)          # 9 + 3 = 12 ≥ DD 12 (réussite),
    # plafond table (resultat 12 → +0) = 1 DV < 2 DV du zombie → résiste.
    dd = _env(tmp_path, monstres=["Zombie"])
    rep = _reprouver(dd)
    assert "Réussite" in rep and "résistent" in rep, rep
    etat = PartyState(data_dir=dd, partie_id=PID).load()
    assert "Repoussé" not in (etat["monstres_combat"][0]["conditions"] or [])


# --------------------------------------------------------------------------- #
#  Limite quotidienne (3 + mod. CHA)
# --------------------------------------------------------------------------- #
def test_limite_quotidienne_epuisee(tmp_path, monkeypatch):
    """Zombies REPoussés (pas détruits : Clerc 1 < 2×2 DV) : 6 tentatives
    (3 + CHA 16), la 7ᵉ est refusée."""
    _piper_des(monkeypatch, jet=15)
    dd = _env(tmp_path, monstres=["Zombie"], cha=16)
    for i in range(6):
        rep = _reprouver(dd)
        assert "épuisé" not in rep, f"tentative {i + 1} refusée à tort"
    rep7 = _reprouver(dd)
    assert "épuisé" in rep7, rep7
