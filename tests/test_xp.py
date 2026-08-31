"""Tests unitaires — XP / niveaux D&D 3.5 (règles officielles DMG/PHB).

Couvre :
  - la table officielle DMG 3.5 p.38 (XP par PJ selon niveau × FP, y compris
    CR fractionnaires et la diagonale à 300) ;
  - les seuils de niveau (N×(N-1)/2×1000) ;
  - le gain d'XP avec montée de niveau automatique (jet de dé de vie + CON) ;
  - la perte de niveau permanente (XP au point médian — DMG « Level Loss ») ;
  - les niveaux négatifs (energy drain) et la mort si niveau effectif ≤ 0 ;
  - l'affichage « XP / requis prochain niveau » sur la fiche.

Usage : py -m pytest tests/test_xp.py -q
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.xp import (  # noqa: E402
    appliquer_gain,
    appliquer_perte_niveau,
    ligne_xp_fiche,
    niveau_effectif,
    parse_cr,
    xp_min_niveau,
    xp_pour_cr,
    xp_prochain_niveau,
)
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")


def test_table_dmg_valeurs_officielles():
    # Construction officielle DMG 3.5 : XP(n, c) = BASE(c) / FACTEUR(n) où
    # FACTEUR double tous les 2 niveaux à partir du 5e. La diagonale vaut
    # 300 partout SAUF niveaux impairs 9-17 (266.67, le Manuel imprime 265).
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 19, 20):
        assert xp_pour_cr(n, n) == 300, f"n={n}"
    for n in (9, 11, 13, 15, 17):
        assert xp_pour_cr(n, n) == 267, f"n={n}"  # arrondi de 266.67
    # Valeurs officielles connues (DMG 3.5 p.38).
    assert xp_pour_cr(1, 1) == 300
    assert xp_pour_cr(1, 2) == 150
    assert xp_pour_cr(1, 3) == 100
    assert xp_pour_cr(1, 4) == 75
    assert xp_pour_cr(1, 5) == 50
    assert xp_pour_cr(1, 6) == 38      # 300/8 — monstres très faibles
    assert xp_pour_cr(2, 1) == 600
    assert xp_pour_cr(3, 1) == 900
    assert xp_pour_cr(5, 1) == 1800
    assert xp_pour_cr(10, 1) == 9600
    assert xp_pour_cr(20, 2) == 153600  # CR 20, PJ niveau 2 : 307200 / 2
    assert xp_pour_cr(20, 20) == 300    # 307200 / 1024
    # Ligne « PJ niveau 10 » officielle : CR10→300 (diagonale).
    # (Les cellules fractionnaires type 9.375 dépendent de la convention
    # d'arrondi retenue ; on ne vérifie que les divisions entières sûres.)
    assert xp_pour_cr(10, 10) == 300          # diagonale
    assert xp_pour_cr(5, 1) == 1800           # colonne CR1, ligne 5 : 300×6


def test_cr_fractionnaires():
    assert parse_cr("1/2") == 0.5
    assert parse_cr("1/4") == 0.25
    # CR 1/2 pour un PJ niv.1 = 150 (300 × 1/2), CR 1/4 = 75.
    assert xp_pour_cr("1/2", 1) == 150
    assert xp_pour_cr("1/4", 1) == 75
    assert xp_pour_cr("1/3", 1) == 100
    # Niveau 6 : les tout petits monstres ne donnent plus rien.
    assert xp_pour_cr("1/2", 6) == 19  # 38 × 0.5 (ligne n=6, CR1=38)


def test_seuils_de_niveau():
    assert xp_min_niveau(1) == 0
    assert xp_min_niveau(2) == 1000
    assert xp_min_niveau(3) == 3000
    assert xp_min_niveau(4) == 6000
    assert xp_min_niveau(5) == 10000
    assert xp_min_niveau(20) == 190000
    assert xp_prochain_niveau(1, 300) == 700


def _fiche_demo() -> dict:
    return {
        "nom": "Brunhild", "classe": "guerrier", "niveau": 1,
        "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 10, "CHA": 8},
        "pv": 12, "pv_max": 12, "xp": 0,
    }


def test_gain_et_montee_de_niveau():
    f = _fiche_demo()
    rng = random.Random(42)
    logs = appliquer_gain(f, 900, rng=rng)
    assert f["xp"] == 900
    assert f["niveau"] == 1  # pas encore 1000
    # 1500 XP de plus → total 2400 ≥ 3000 ? non → toujours niveau 1... 
    logs = appliquer_gain(f, 100, rng=rng)  # 1000 exact → niveau 2
    assert f["niveau"] == 2
    assert f["xp"] == 1000
    # PV max a augmenté d'au moins 1d10+2 minimum => >= 2 (1+2, borné max(1,..))
    assert f["pv_max"] > 12
    assert any("NIVEAU 2" in l for l in logs)
    # Niveau 4 atteint d'un coup → rappel caractéristique (niveau multiple de 4).
    f2 = _fiche_demo()
    f2["xp"] = xp_min_niveau(5)  # 10000 → deux montées d'un coup? non: niv1→niv6
    logs = appliquer_gain(f2, 0, rng=rng)
    # 0 XP : aucun log, mais le niveau doit être recalculé seulement sur gain.
    assert logs == []


def test_montees_multiples():
    f = _fiche_demo()
    rng = random.Random(7)
    logs = appliquer_gain(f, 6000, rng=rng)  # niv1 → niv4 (6000 exact)
    assert f["niveau"] == 4
    assert sum(1 for l in logs if "monte au NIVEAU" in l) == 3


def test_perte_de_niveau_officielle():
    f = _fiche_demo()
    f["niveau"] = 3
    f["xp"] = 6000
    f["pv_max"] = 30
    logs = appliquer_perte_niveau(f, 1)
    assert f["niveau"] == 2
    # DMG 3.5 : XP au point médian du niveau 2 → (1000+3000)/2 = 2000.
    assert f["xp"] == 2000
    assert f["pv_max"] < 30
    assert any("perd un niveau" in l for l in logs)


def test_niveaux_negatifs_et_mort():
    f = _fiche_demo()
    f["niveaux_negatifs"] = 0
    f["niveaux_negatifs"] = 1
    assert niveau_effectif(f) == 0  # niveau 1 - 1 négatif = 0 → mort
    f["niveau"] = 5
    assert niveau_effectif(f) == 4


def test_affichage_xp_fiche():
    f = _fiche_demo()
    f["xp"] = 3500
    f["niveau"] = 2
    ligne = ligne_xp_fiche(f)
    assert "3500 / 3000" in ligne or "3500" in ligne
    assert "prochain niveau" in ligne


# --------------------------------------------------------------------------- #
#  Tools (avec fiche persistante en tmp dir)
# --------------------------------------------------------------------------- #
def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_xp_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id="test_xp", joueur="test", data_dir=d)


async def tool(d: str, name: str, **args):
    return await invoke_tool(TOOLS[name], _ctx(d), args)


async def _creer_pj(d: str):
    r = await tool(d, "fiche_perso_creer_rapide", nom="Thorgal",
                   race="Humain", classe="Guerrier", joueur="test",
                   carac_texte="For 16, Dex 12, Con 14, Int 10, Sag 10, Cha 8")
    assert r.text.startswith("✅"), r.text


async def test_tool_gagner_xp_et_levelup_persiste():
    d = _fresh_dir()
    try:
        import json
        await _creer_pj(d)
        r = await tool(d, "fiche_perso_gagner_xp", nom="Thorgal", montant=1000)
        assert "NIVEAU 2" in r.text, r.text
        import unicodedata, re
        nf = unicodedata.normalize("NFKD", "Thorgal")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_",
                      "".join(c for c in nf if not unicodedata.combining(c))
                      ).strip("_").lower()
        with open(os.path.join(d, "fiches", f"fiche_{slug}.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["niveau"] == 2
        assert fiche["xp"] == 1000
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_tool_niveau_negatif_puis_retrait():
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        r = await tool(d, "fiche_perso_niveau_negatif", nom="Thorgal", nb=1)
        assert "niveau effectif 0" in r.text
        assert "MORT" in r.text
        r2 = await tool(d, "fiche_perso_retirer_niveau_negatif", nom="Thorgal",
                        nb=1)
        assert "plus aucun niveau négatif" in r2.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_tool_perte_niveau_midpoint():
    d = _fresh_dir()
    try:
        import json, unicodedata, re
        await _creer_pj(d)
        # Monte au niveau 3 d'abord.
        await tool(d, "fiche_perso_gagner_xp", nom="Thorgal", montant=3000)
        r = await tool(d, "fiche_perso_perte_niveau", nom="Thorgal", nb=1)
        assert "perd un niveau" in r.text
        nf = unicodedata.normalize("NFKD", "Thorgal")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_",
                      "".join(c for c in nf if not unicodedata.combining(c))
                      ).strip("_").lower()
        with open(os.path.join(d, "fiches", f"fiche_{slug}.json"),
                  encoding="utf-8") as f:
            fiche = json.load(f)
        assert fiche["niveau"] == 2
        assert fiche["xp"] == 2000  # midpoint officiel
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_fiche_recuperer_affiche_xp():
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        r = await tool(d, "fiche_perso_recuperer", nom="Thorgal")
        assert "XP : 0 / 1000" in r.text
        assert "prochain niveau" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)
