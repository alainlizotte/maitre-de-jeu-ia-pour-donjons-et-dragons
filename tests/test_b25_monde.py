# -*- coding: utf-8 -*-
"""Régressions (run shadows #2) : le garde de fidélité B25 s'applique HORS
DONJON.

Avant : le modèle engager directement « Loup-garou (humain) » (32 PV, DR
10/argent) à la place du « Loup terrible » du manifeste, AVANT même d'entrer
dans le donjon — injouable pour un groupe niveau 1 sans arme en argent (3
retraites « aucun dégât » enchaînées). Canon du MONDE = ennemis du
manifeste (toutes salles) ∪ bible.ennemis — borne l'ESPÈCE seulement
(M6/P9 quantité/auto-add jamais hors donjon).

Usage : py -m pytest tests/test_b25_monde.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = discover_tools()

_MANIFESTE = [
    {"x": 0, "y": 0, "ennemis": ["Loup terrible ×1"]},
    {"x": 1, "y": 2, "ennemis": ["Kobold ×6", "Bandit ×5"]},
    {"x": 1, "y": 3, "ennemis": []},
]
_BIBLE = {"bible": {"ennemis": ["Acolyte", "Pirate"]}}
_PJS = [
    {"nom": "Brandt", "joueur": "bruno", "race": "Nain", "classe": "Guerrier",
     "niveau": 1, "pv": 13, "pv_max": 13, "ca": 16, "conditions": [],
     "carac": {"FOR": 16, "DEX": 10, "CON": 14, "INT": 10, "SAG": 10,
               "CHA": 8}},
    {"nom": "Aurora", "joueur": "alys", "race": "Humaine", "classe": "Clerc",
     "niveau": 1, "pv": 10, "pv_max": 10, "ca": 14, "conditions": [],
     "carac": {"FOR": 12, "DEX": 10, "CON": 14, "INT": 10, "SAG": 16,
               "CHA": 12}},
    {"nom": "Flick", "joueur": "pip", "race": "Halfelin", "classe": "Roublard",
     "niveau": 1, "pv": 7, "pv_max": 7, "ca": 14, "conditions": [],
     "carac": {"FOR": 10, "DEX": 16, "CON": 12, "INT": 14, "SAG": 10,
               "CHA": 12}},
]


def _monte(manche: dict) -> str:
    """Data dir frais : bestiaire réel + état hors donjon (courant vide)."""
    d = tempfile.mkdtemp(prefix="b25_monde_")
    shutil.copy2(os.path.join(_REPO, "server", "data", "bestiaire.json"),
                 os.path.join(d, "bestiaire.json"))
    st = PartyState(data_dir=d, partie_id="b25monde")
    etat = st.load()
    etat["phase"] = "exploration"
    etat["pj"] = [dict(p) for p in _PJS]
    etat["donjon"] = {"id": "divers_test", "grille": manche["grille"],
                      "courant": []}  # HORS DONJON : pas encore entré
    etat["quete"] = manche["quete"]
    st.save(etat)
    return d


def _engager(d: str, monstres: str):
    ctx = ToolContext(partie_id="b25monde", joueur="bruno", data_dir=d)
    return asyncio.run(invoke_tool(
        _TOOLS["engager_combat"], ctx, {"monstres": monstres}))


def test_monde_refuse_variante_hors_module():
    """Hors donjon, engager « Loup-garou (humain) » est refusé : l'espèce
    n'est NI dans le manifeste NI dans la bible — le refus liste le canon
    du SCÉNARIO (le Loup terrible du manifeste y figure)."""
    d = _monte({"grille": _MANIFESTE, "quete": _BIBLE})
    r = _engager(d, "Loup-garou (humain)")
    assert r.text.startswith("⛔"), r.text[:200]
    assert "SCÉNARIO (hors donjon)" in r.text, r.text[:200]
    assert "Loup terrible" in r.text, r.text[:200]


def test_monde_accepte_espece_du_manifeste():
    """Hors donjon, le « Loup terrible » du manifeste s'engage — et SEUL :
    M6/P9 (auto-add des autres espèces du manifeste) ne doit PAS invoquer
    Kobold/Bandit/Pirate/Dracolich. 🛡️ (run shadows #4) : FP 3 vs APL 1 →
    l'équilibrage de difficulté allège TEMPORAIREMENT les PV (le canon du
    bestiaire reste 45)."""
    d = _monte({"grille": _MANIFESTE, "quete": _BIBLE})
    r = _engager(d, "Loup terrible")
    assert "Ajustement de difficulté" in r.text, r.text[:300]
    st = PartyState(data_dir=d, partie_id="b25monde")
    etat = st.load()
    assert etat.get("phase") == "combat"
    mcb = [(str(m.get("nom")), int(m.get("pv", 0)))
           for m in (etat.get("monstres_combat") or [])]
    assert len(mcb) == 1 and mcb[0][0] == "Loup terrible", mcb
    assert 20 <= mcb[0][1] < 45, mcb  # allégé (×0.75), pas anéanti


def test_monde_bible_complete_le_manifeste():
    """Une espèce absente du manifeste mais DÉCLARÉE dans bible.ennemis
    s'engage hors donjon (l'union, pas le manifeste seul)."""
    grille = [{"x": 0, "y": 0, "ennemis": ["Loup terrible ×1"]}]
    d = _monte({"grille": grille, "quete": _BIBLE})
    r = _engager(d, "Pirate")
    assert not r.text.startswith(("⛔", "❌")), r.text[:200]
    st = PartyState(data_dir=d, partie_id="b25monde")
    etat = st.load()
    noms = [str(m.get("nom")) for m in (etat.get("monstres_combat") or [])]
    assert noms and all("Pirate" in n for n in noms), noms


def test_monde_sans_canon_aucune_borne():
    """Sans manifeste ennemi NI bible (scénario libre), le comportement
    historique s'applique (rencontres de voyage libres)."""
    d = _monte({"grille": [], "quete": {"bible": {}}})
    r = _engager(d, "Loup-garou (humain)")
    assert not r.text.startswith("⛔"), r.text[:200]
