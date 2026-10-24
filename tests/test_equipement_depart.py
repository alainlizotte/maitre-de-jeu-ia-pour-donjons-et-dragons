# -*- coding: utf-8 -*-
"""Régression (suivi correctif #1) : `fiche_perso_creer_rapide` applique
`equipement_texte`.

Bug constaté : le paramètre était documenté dans la docstring (lue par le
LLM) mais JAMAIS utilisé — la fiche naissait avec `equipement: []` en dur.
Conséquence en chaîne : l'attaque déterministe au chat (correctif #1)
lisant CE champ pour choisir l'arme du catalogue, tout PJ créé par le
LLM combattait à « arme improvisée 1d6 ».

Usage : py -m pytest tests/test_equipement_depart.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.fiches import _load_fiche  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="eq_depart_")
_TOOLS = discover_tools()


def _ctx(nom: str) -> ToolContext:
    return ToolContext(partie_id="eqdepart", joueur="e2e", data_dir=_TMP)


async def _creer(nom: str, equipement_texte: str = ""):
    return await invoke_tool(
        _TOOLS["fiche_perso_creer_rapide"], _ctx(nom),
        {"nom": nom, "race": "Nain", "classe": "Guerrier", "niveau": 1,
         "joueur": "e2e",
         "carac_texte": "FOR 16, DEX 10, CON 14, INT 10, SAG 10, CHA 8",
         **({"equipement_texte": equipement_texte} if equipement_texte
            else {})})


def test_equipement_texte_apparie_le_catalogue():
    async def _run():
        # « cotte de maille » (sans s) → canonisé « Cotte de mailles »
        # (variante singulier/pluriel, un seul candidat au catalogue).
        r = await _creer("EqCatalog", "Hache d'arme, cotte de maille")
        assert r.text.startswith("✅"), r.text[:120]
        fiche = _load_fiche(_ctx("EqCatalog"), "EqCatalog")
        noms = [e.get("nom") for e in (fiche.get("equipement") or [])]
        # Noms CANONIQUES du catalogue (pas le texte brut).
        assert "Hache d'arme" in noms, noms
        assert "Cotte de mailles" in noms, noms
    asyncio.run(_run())


def test_equipement_ambigu_reste_generique():
    async def _run():
        # « arc » correspond à « Arc court » ET « Arc long » (dés
        # différents) : aucune supposition → entrée générique.
        r = await _creer("EqAmbigu", "arc")
        assert r.text.startswith("✅"), r.text[:120]
        fiche = _load_fiche(_ctx("EqAmbigu"), "EqAmbigu")
        noms = [e.get("nom") for e in (fiche.get("equipement") or [])]
        assert "arc" in noms, noms
    asyncio.run(_run())


def test_equipement_texte_inconnu_reste_en_entree_generique():
    async def _run():
        r = await _creer("EqInconnu", "Hache d'arme, relique du clan")
        assert r.text.startswith("✅"), r.text[:120]
        fiche = _load_fiche(_ctx("EqInconnu"), "EqInconnu")
        noms = [e.get("nom") for e in (fiche.get("equipement") or [])]
        assert "Hache d'arme" in noms, noms
        assert "relique du clan" in noms, noms  # conservé tel quel
    asyncio.run(_run())


def test_sans_equipement_texte_fiche_vide_comme_avant():
    async def _run():
        r = await _creer("EqVide")
        assert r.text.startswith("✅"), r.text[:120]
        fiche = _load_fiche(_ctx("EqVide"), "EqVide")
        assert fiche.get("equipement") == []
    asyncio.run(_run())


def test_attaque_deterministe_trouve_larme_de_la_fiche():
    """Chaîne complète : equipement_texte → fiche → l'arme du catalogue
    est bien la PREMIÈRE arme portée (celle que `/combat/action` et
    `_arme_du_texte_chat` utilisent)."""
    async def _run():
        await _creer("EqChaine", "Hache d'arme, cotte de maille")
        from server import catalogue as _cat
        fiche = _load_fiche(_ctx("EqChaine"), "EqChaine")
        armes_cat = {a["nom"] for a in _cat.ARMES}
        porte = [str(e.get("nom") or "").strip()
                 for e in (fiche.get("equipement") or [])]
        premiere = next((n for n in porte if n in armes_cat), None)
        assert premiere == "Hache d'arme", (porte, premiere)
    asyncio.run(_run())
