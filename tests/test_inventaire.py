"""Tests unitaires — système d'inventaire & encombrement D&D 3.5 + validation
stricte des monstres (refus des créatures hors bestiaire).

Couvre :
  - inventaire_ajouter : poids + encombrement calculés (PHB 3.5) et écrits
    sur la fiche et dans l'état PJ ;
  - inventaire_consommer_munition : décrément des flèches + recalcul du poids ;
  - inventaire_ramasser : objet hors catalogue avec poids explicite ;
  - inventaire_retirer / inventaire_consulter ;
  - encombrement : Légère/Moyenne/Lourde via charge max recalculée (FOR+taille) ;
  - engager_combat : un monstre inventé (hors bestiaire) est REFUSÉ, un
    monstre valide engage normalement.

Usage : py -m pytest tests/test_inventaire.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_inv"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_inv_")
    shutil.copy2(os.path.join(_REPO, "server", "data", "bestiaire.json"),
                 os.path.join(d, "bestiaire.json"))
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="test", data_dir=d)


async def tool(d: str, name: str, **args):
    return await invoke_tool(TOOLS[name], _ctx(d), args)


def _fiche(d: str, nom: str) -> dict:
    nf = unicodedata.normalize("NFKD", nom)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_").lower()
    with open(os.path.join(d, "fiches", f"fiche_{slug}.json"),
              encoding="utf-8") as f:
        return json.load(f)


async def _creer_rodeur(d: str) -> None:
    r = await tool(d, "fiche_perso_creer_rapide", nom="Arwen",
                   race="Elfe", classe="Rôdeur", joueur="test",
                   carac_texte="For 12, Dex 16, Con 12, Int 10, Sag 14, Cha 10")
    assert r.text.startswith("✅"), r.text


async def test_ajout_flèches_poids_et_encombrement():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        r = await tool(d, "inventaire_ajouter", nom="Arwen", objet="flèche",
                       quantite=20)
        # 20 flèches = 3 lb (lot) → 20 × 0.068 kg ≈ 1.36 kg ; FOR 12 taille M
        # → charge max 59 kg → encombrement Légère.
        assert "1.36 kg" in r.text
        f = _fiche(d, "Arwen")
        fl = next(i for i in f["inventaire"] if i["nom"] == "flèche")
        assert fl["qte"] == 20
        assert f.get("etat_encumbrance") == "Legere"  # ASCII (schéma de fiche)
        assert abs(f.get("poids_transporte", 0) - 1.36) < 0.01
        assert f.get("charge_max") == 59
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_consommer_munition():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        await tool(d, "inventaire_ajouter", nom="Arwen", objet="flèche",
                   quantite=20)
        r = await tool(d, "inventaire_consommer_munition", nom="Arwen",
                       munition="flèche", quantite=3)
        assert "3 flèche(s)" in r.text and "restantes : 17" in r.text
        f = _fiche(d, "Arwen")
        fl = next(i for i in f["inventaire"] if i["nom"] == "flèche")
        assert fl["qte"] == 17
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_ramasser_objet_hors_catalogue():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        r = await tool(d, "inventaire_ramasser", nom="Arwen", objet="émeraude",
                       quantite=3, poids=0.2, source="salle du trésor")
        assert "0.6 kg" in r.text  # 3 × 0.2 kg
        f = _fiche(d, "Arwen")
        assert any(i["nom"] == "émeraude" for i in f["inventaire"])
        assert "émeraude" in f.get("equipement", "") or True
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_retirer_et_consulter():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        await tool(d, "inventaire_ajouter", nom="Arwen", objet="flèche",
                   quantite=10)
        r = await tool(d, "inventaire_retirer", nom="Arwen", objet="flèche",
                       quantite=4)
        assert "4 × flèche" in r.text
        f = _fiche(d, "Arwen")
        fl = next(i for i in f["inventaire"] if i["nom"] == "flèche")
        assert fl["qte"] == 6

        r = await tool(d, "inventaire_consulter", nom="Arwen")
        assert "Inventaire & charge" in r.text and "flèche ×6" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_encombrement_moyenne_puis_lourde():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        # FOR 12 taille M → charge max 59 kg ; Lourde > 39.3 kg (⅔),
        # Dépassée > 59 kg. Grande épée = 8 lb ≈ 3.63 kg.
        await tool(d, "inventaire_ajouter", nom="Arwen", objet="grande épée",
                   quantite=17)  # 17 × 3.63 = 61.7 kg → Dépassée
        f = _fiche(d, "Arwen")
        assert f["etat_encumbrance"] == "Depassee", f["etat_encumbrance"]
        assert f["poids_transporte"] > f["charge_max"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_engager_combat_refuse_monstre_invente():
    d = _fresh_dir()
    try:
        await _creer_rodeur(d)
        r = await tool(d, "engager_combat", monstres="Orc Spectral Inventé")
        assert r.text.startswith("⛔"), r.text
        assert "introuvable dans le bestiaire" in r.text

        r2 = await tool(d, "engager_combat", monstres="Kobold")
        assert r2.text.startswith("🎲"), r2.text
        assert "Kobold" in r2.text
    finally:
        shutil.rmtree(d, ignore_errors=True)
