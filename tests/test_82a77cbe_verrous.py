# -*- coding: utf-8 -*-
"""Régressions de la partie 82a77cbe.

L'intro remettait « le parchemin, la fiole, la baguette de téléportation »
ET LA COURONNE elle-même :
1. « elle confie la quête de la Couronne de Mystra » — le verbe « confie »
   transformait une MENTION de mission en remise d'objet (idem « accepte » :
   « Si vous acceptez cette mission, vous devrez récupérer la couronne »).
2. La baguette (requis de l'ÉTAPE 2) entrait en inventaire dès l'intro alors
   que le module ne la confie qu'au retour de la Couronne.
3. La note du rattrapage annonçait « couronne ajoutée » pour un ajout
   REFUSÉ (ok=True + 🚫).

Usage : py -m pytest tests/test_82a77cbe_verrous.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.objectifs import (           # noqa: E402
    _cle_objet,
    etape_future_pour_objet,
)
from server.llm.orchestrator import (         # noqa: E402
    _objets_remettes_narration,
)
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools      # noqa: E402

_DATA_REEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "data")

_MANIFEST = {
    "id": "test_82a_donjon",
    "scenario": ["test_82a"],
    "donjon_id": "Le test 82a",
    "etages": [{"nom": "E1", "entree": [0, 0], "salles": [
        {"x": 0, "y": 0, "type": "entrée", "portes": {"est": True},
         "description": "Le départ."},
        {"x": 1, "y": 0, "type": "salle du trône", "portes": {"ouest": True},
         "description": "L'antre du boss."},
    ]}],
    "etapes": [
        {"cle": "objet", "titre": "Reprendre le Trophée chez le boss",
         "type": "objet", "salle": "1,0", "gate": True,
         "requis": [{"nom": "Le Trophée du boss", "portee": "quete"}]},
        {"cle": "outils", "titre": "Équiper le groupe pour la suite",
         "type": "objet", "gate": True,
         "requis": [{"nom": "La Clé d'argent", "portee": "quete"}]},
    ],
}


def _setup(tmp_path, inventaire_quete=None) -> ToolContext:
    data = tmp_path / "data"
    (data / "scenarios" / "T").mkdir(parents=True, exist_ok=True)
    (data / "fiches").mkdir(parents=True, exist_ok=True)
    (data / "scenarios" / "T" / "test_82a.donjon.json").write_text(
        json.dumps(_MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / "partie_test_82a.json").write_text(json.dumps({
        "phase": "exploration",
        "quete": {"source": "[test_82a] /data/x.pdf", "titre": "Test 82a"},
        "lieu": {"nom": "Silverymoon", "type": "ville"},
        "donjon": None,
        "pj": [{"nom": "Morgoth"}],
        "memoire": {},
    }, ensure_ascii=False), encoding="utf-8")
    (data / "fiches" / "fiche_morgoth.json").write_text(json.dumps({
        "nom": "Morgoth", "race": "Demi-orc", "classe": "Barbare",
        "niveau": 1, "pv": 15, "pv_max": 15, "or": 500,
        "inventaire": inventaire_quete or [],
    }, ensure_ascii=False), encoding="utf-8")
    return ToolContext(partie_id="test_82a", joueur="Alain",
                       data_dir=str(data))


# --------------------------------------------------------------------------- #
#  1. Mentions de mission ≠ remises
# --------------------------------------------------------------------------- #

def test_mentions_mission_pas_des_remises():
    intro = ("La magesteresse Thukmuul Teleshann y reçoit le groupe, et lui "
             "confie la quête de la Couronne de Mystra dérobée par les "
             "adorateurs de Cyric. « Si vous acceptez cette mission, vous "
             "devrez vaincre Nulentok pour récupérer la couronne. »")
    assert _objets_remettes_narration(intro) == [], (
        _objets_remettes_narration(intro))


def test_remises_legitimes_toujours_detectees():
    intro = ("Thukmuul tend un parchemin et une fiole à Morgoth. "
             "Elle remet une clé de fer au gardien.")
    extraits = _objets_remettes_narration(intro)
    assert any("parchemin" in o.lower() for o in extraits), extraits
    assert any("fiole" in o.lower() for o in extraits), extraits


# --------------------------------------------------------------------------- #
#  2. Objet d'une étape À VENIR refusé
# --------------------------------------------------------------------------- #

def test_objet_etape_future_refuse(tmp_path):
    """« La Clé d'argent » (requis de l'ÉTAPE 2) refusée tant que l'étape 1
    (le Trophée) n'est pas accomplie — la baguette de 82a77cbe."""
    ctx = _setup(tmp_path)
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Morgoth", "objet": "La Clé d'argent", "portee": "quete"}))
    assert "ÉTAPE À VENIR" in r.text, r.text
    fiche = json.loads((tmp_path / "data" / "fiches" / "fiche_morgoth.json")
                       .read_text(encoding="utf-8"))
    assert fiche.get("inventaire") == [], fiche.get("inventaire")


def test_objet_etape_courante_autorise(tmp_path):
    """Une fois le Trophée en inventaire (étape 1 accomplie), la Clé
    d'argent (étape 2, désormais courante) est acceptée."""
    ctx = _setup(tmp_path, inventaire_quete=[
        {"nom": "Le Trophée du boss", "qte": 1, "poids": 0.1,
         "portee": "quete", "partie": "test_82a"}])
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Morgoth", "objet": "La Clé d'argent", "portee": "quete"}))
    assert "ÉTAPE À VENIR" not in r.text, r.text


def test_etape_future_helper_direct(tmp_path):
    """`etape_future_pour_objet` : la Clé (étape 2) est future quand le
    Trophée (étape 1) manque ; OK quand il est dans les clés."""
    d = os.path.join(tmp_path, "scenarios", "T")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "test_82a.donjon.json"), "w", encoding="utf-8")\
        .write(json.dumps(_MANIFEST, ensure_ascii=False))
    etat = {"quete": {"source": "[test_82a] /data/x.pdf"}}
    refus = etape_future_pour_objet(etat, str(tmp_path),
                                    "La Clé d'argent", set())
    assert refus and "ÉTAPE À VENIR" in refus, refus
    refus2 = etape_future_pour_objet(
        etat, str(tmp_path), "La Clé d'argent",
        {_cle_objet("Le Trophée du boss")})
    assert refus2 is None, refus2


# --------------------------------------------------------------------------- #
#  3. Rattrapage : seuls les ✅ comptent comme ajoutés
# --------------------------------------------------------------------------- #

def test_rattrapage_compte_les_transferts_reels_seuls():
    from server.llm import orchestrator as orch
    src = open(orch.__file__, encoding="utf-8").read()
    i = src.find("_ajoutes: list[str] = []")
    assert i > 0, "bloc rattrapage introuvable"
    bloc = src[i:i + 1200]
    assert 'in _txt_r[:20] and "DÉJÀ" not in' in bloc, (
        "le comptage ✅/DÉJÀ a disparu du rattrapage")
