# -*- coding: utf-8 -*-
"""Régressions de la partie 33d8f18e.

1. **Garde intro mal tirée** : au tour « j'achète un repas de bonne
   qualité » (histoire encore courte), la garde « intro trop courte » a
   relancé la « scène complète » → le modèle a RE-NARRÉ le briefing avec un
   SECOND parchemin. La garde doit être autorisée par `ouverture_tour`
   (phase opening + aucune narration antérieure), pas par la longueur de
   l'histoire.
2. **Args corrompus** : `auberge_commander(repas=true, logement=false,
   nuits=0)` → « Qualité « true » inconnue ». Sanitation côté tool.
3. **Rattrapage d'achat bloqué par un refus** : l'appel refusé portait
   ok=True → le rattrapage se croyait inutile. Seul un ✅ (transaction
   réelle) doit le bloquer.

Usage : py -m pytest tests/test_33d8f18e_achats_garde.py -q
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools       # noqa: E402


def _data_dir_ville(tmp_path) -> str:
    """data_dir minimal : partie à Silverymoon (ville), fiche Morgoth."""
    data = tmp_path / "data"
    (data / "fiches").mkdir(parents=True)
    (data / "partie_test_33d8f18e.json").write_text(json.dumps({
        "phase": "exploration",
        "lieu": {"nom": "Silverymoon", "type": "ville"},
        "pj": [{"nom": "Morgoth"}],
    }, ensure_ascii=False), encoding="utf-8")
    (data / "fiches" / "fiche_morgoth.json").write_text(json.dumps({
        "nom": "Morgoth", "race": "Demi-orc", "classe": "Barbare",
        "niveau": 1, "pv": 15, "pv_max": 15, "or": 500,
        "inventaire": [],
    }, ensure_ascii=False), encoding="utf-8")
    return str(data)


def test_auberge_args_booleens_sinisés(tmp_path):
    """`repas=true, logement=false, nuits=0` ne produit PLUS l'erreur
    « Qualité « true » inconnue » : sanitation → aucun plat précisé, la
    réponse liste le menu (le rattrapage prend ensuite le relais)."""
    dd = _data_dir_ville(tmp_path)
    ctx = ToolContext(partie_id="test_33d8f18e", joueur="Alain",
                      data_dir=dd)
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["auberge_commander"], ctx,
        {"nom": "Morgoth", "repas": True, "logement": False, "nuits": 0}))
    assert "« true »" not in r.text, r.text
    assert "inconnue" not in r.text, r.text


def test_auberge_bonne_qualite_passee_en_str_fonctionne(tmp_path):
    """Le cas métier : repas « bonne » à Silverymoon (Cité, bonne seule) →
    commande acceptée, or débité."""
    dd = _data_dir_ville(tmp_path)
    ctx = ToolContext(partie_id="test_33d8f18e", joueur="Alain",
                      data_dir=dd)
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["auberge_commander"], ctx,
        {"nom": "Morgoth", "repas": "bonne", "logement": "", "nuits": 1}))
    assert "❌" not in r.text, r.text
    fiche = json.loads(
        (tmp_path / "data" / "fiches" / "fiche_morgoth.json")
        .read_text(encoding="utf-8"))
    assert int(fiche.get("or", 0)) < 500, fiche.get("or")  # or débité


def test_garde_intro_autorisee_par_ouverture_tour():
    """La garde « intro trop courte » est subordonnée à `ouverture_tour` :
    plus de relance « scène complète » au tour d'un ACHAT (33d8f18e)."""
    from server.llm import orchestrator as orch
    src = inspect.getsource(orch)
    i = src.find("_hist_intro")
    assert i > 0
    bloc = src[i:i + 1200]
    assert "ouverture_tour" in bloc, (
        "la garde n'est plus subordonnée à ouverture_tour")


def test_rattrapage_achat_bloque_par_transaction_reelle_seule():
    """Le rattrapage d'achat ignore un appel refusé (pas de ✅) et ne se
    bloque que sur une transaction réelle (✅)."""
    from server.llm import orchestrator as orch
    src = inspect.getsource(orch)
    i = src.find("rattrapage DÉTERMINISTE des ACHATS")
    assert i > 0
    bloc = src[i:i + 4200]
    assert ('and "✅" in (tc.get("text") or "")' in bloc), (
        "la condition de skip ne distingue plus le ✅ du refus")
    # et le bloc garde bien les deux outils du skip
    assert "marche_acheter" in bloc and "auberge_commander" in bloc
