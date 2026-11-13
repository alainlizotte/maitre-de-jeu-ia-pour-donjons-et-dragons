# -*- coding: utf-8 -*-
"""Tests Tier 2 : rumeurs de taverne, combat monté, identification,
maladie (progression quotidienne).

Usage : py -m pytest tests/test_tier2_saveur.py -q
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
from server.main import _assainir_texte_joueur  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_TOOLS = discover_tools()


async def _creer_pj(d: str, nom: str = "Throkmar", joueur: str = "alain",
                    classe: str = "Guerrier") -> None:
    st = PartyState(data_dir=d, partie_id="tier2")
    etat = st.load()
    etat["phase"] = "exploration"
    etat.setdefault("pj", []).append({
        "nom": nom, "joueur": joueur, "pv": 16, "pv_max": 16})
    st.save(etat)
    r = await invoke_tool(_TOOLS["fiche_perso_creer_rapide"], _ctx(d, joueur), {
        "nom": nom, "race": "Humain", "classe": classe, "niveau": 1,
        "joueur": joueur,
        "carac_texte": "FOR 16, DEX 12, CON 14, INT 14, SAG 10, CHA 8"})
    assert r.text.startswith(("✅", "ℹ️")), r.text[:150]


def _ctx(d: str, joueur: str = "alain") -> ToolContext:
    return ToolContext(partie_id="tier2", joueur=joueur, data_dir=d)


def _fiche(d: str, nom: str = "Throkmar") -> dict:
    fp = os.path.join(d, "fiches", f"fiche_{nom.lower()}.json")
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ #
# 5. 🎲 Rumeurs de taverne
# ------------------------------------------------------------------ #


def test_rumeurs_tavernes_melange_vraies_et_fausses():
    """Avec une intrigue en mémoire : des rumeurs mélangées, marquées
    [VRAIE]/[FAUSSE], déterministes sur le même jour."""

    async def _run():
        d = tempfile.mkdtemp(prefix="t2_tav_")
        try:
            await _creer_pj(d)
            st = PartyState(data_dir=d, partie_id="tier2")
            etat = st.load()
            etat["memoire"] = {
                "missions": [{
                    "titre": "Dues For The Dead", "statut": "active",
                    "notes": "Purifier le sanctuaire profane des "
                             "catacombes de Valhingen au nord de Phlan."}],
                "objectif_courant": "Retrouver la tombe profanée.",
            }
            st.save(etat)
            r = await invoke_tool(_TOOLS["rumeurs_taverne"], _ctx(d),
                                  {"lieu": "La Chope Refendue"})
            assert not r.text.startswith("❌"), r.text[:200]
            assert "[VRAIE]" in r.text and "[FAUSSE]" in r.text, r.text[:400]
            # déterministe : même jour → mêmes rumeurs
            r2 = await invoke_tool(_TOOLS["rumeurs_taverne"], _ctx(d),
                                   {"lieu": "La Chope Refendue"})
            assert r2.text == r.text, "rumeurs non déterministes"
            # le filtre anti-fuite neutralise la liste recopiée
            net = _assainir_texte_joueur(r.text)
            assert "[VRAIE]" not in net and "[FAUSSE]" not in net, net[:200]
            assert "Rumeurs de taverne" not in net, net[:200]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_rumeurs_sans_intrigue():
    async def _run():
        d = tempfile.mkdtemp(prefix="t2_tav2_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(_TOOLS["rumeurs_taverne"], _ctx(d), {})
            assert "Aucune intrigue" in r.text, r.text[:200]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 6. ⚔️ Combat monté
# ------------------------------------------------------------------ #


def test_attaque_montee_refusee_sans_monture():
    async def _run():
        d = tempfile.mkdtemp(prefix="t2_cav_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(_TOOLS["attaque_montee"], _ctx(d), {
                "nom": "Throkmar", "arme": "Épée longue",
                "nom_cible": "Gobelin", "ca_cible": 15})
            assert "n'a pas de monture" in r.text, r.text[:200]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_charge_montee_bonus_consomme():
    """La charge pose +2 dans combat_effets et lancer_attaque le
    consomme (le texte de l'attaque le mentionne)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="t2_cav2_")
        try:
            await _creer_pj(d)
            await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Âne ou mulet", "quantite": 1,
                "portee": "permanent"})
            # 5 rangs d'Équitation : contrôle par habitude (pas de d20
            # de contrôle — le test ne doit pas être aléatoire).
            fp = os.path.join(d, "fiches", "fiche_throkmar.json")
            f_eq = json.load(open(fp, encoding="utf-8"))
            f_eq.setdefault("competences", {})["Équitation"] = 5
            json.dump(f_eq, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False)
            r = await invoke_tool(_TOOLS["attaque_montee"], _ctx(d), {
                "nom": "Throkmar", "arme": "Lance courte",
                "nom_cible": "Gobelin d'entraînement", "ca_cible": 12,
                "charge": True})
            assert not r.text.startswith("❌"), r.text[:250]
            assert "CHARGE MONTÉE" in r.text, r.text[:300]
            assert "frappe DOUBLE" in r.text, r.text[:400]
            # le bonus a été consommé par l'attaque
            etat = PartyState(data_dir=d, partie_id="tier2").load()
            assert not (etat.get("combat_effets") or {}).get(
                "charges_montees"), etat.get("combat_effets")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 7. 🔍 Identification
# ------------------------------------------------------------------ #


def test_identification_essai_reussi_marque_lobject():
    """L'essai d'Art de la magie (INT 14, DD 16 — réussite probable sur
    plusieurs tentatives) marque l'objet identifié."""

    async def _run():
        d = tempfile.mkdtemp(prefix="t2_id_")
        try:
            await _creer_pj(d)
            await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Épée courte luisante",
                "quantite": 1, "poids": 1.0, "portee": "permanent"})
            resultats = []
            for _ in range(8):
                r = await invoke_tool(_TOOLS["identifier_objet"], _ctx(d), {
                    "nom": "Throkmar", "objet": "épée courte luisante",
                    "methode": "essai"})
                resultats.append(r.text)
                if "identifié (marqué" in r.text:
                    break
            assert any("identifié (marqué" in t for t in resultats), (
                resultats[-1][:250])
            f = _fiche(d)
            e_ids = [i for i in (f.get("inventaire") or [])
                     if i.get("identifie")]
            assert e_ids, f.get("inventaire")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_identification_sort_exige_emplacement():
    """methode='sort' sans Identification préparé → refus honnête."""

    async def _run():
        d = tempfile.mkdtemp(prefix="t2_id2_")
        try:
            await _creer_pj(d, "Cassyt", "alain", classe="Clerc")
            await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Cassyt", "objet": "Fiole luisante", "quantite": 1,
                "portee": "quete"})
            r = await invoke_tool(_TOOLS["identifier_objet"], _ctx(d), {
                "nom": "Cassyt", "objet": "fiole", "methode": "sort",
                "lanceur": "Cassyt"})
            assert "n'a PAS le sort Identification" in r.text, r.text[:250]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 8. 🦠 Maladie
# ------------------------------------------------------------------ #


def test_maladie_infection_et_progression_quotidienne():
    """Infection (DD 40 → échec), puis 3 jours : dégâts quotidiens à
    chaque échec, guérison après 3 succès (DD 1)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="t2_mal_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(_TOOLS["appliquer_maladie"], _ctx(d), {
                "nom": "Throkmar", "nom_maladie": "fièvre des marais",
                "dd": 40, "degats_quotidiens": "1d4", "carac": "CON",
                "source": "eau souillée"})
            assert not r.text.startswith("❌"), r.text[:250]
            assert "CONTAMINÉ" in r.text, r.text[:300]
            f = _fiche(d)
            assert "Malade" in (f.get("conditions") or []), f
            assert len(f.get("maladies_en_cours") or []) == 1
            # double infection refusée
            r_dup = await invoke_tool(_TOOLS["appliquer_maladie"], _ctx(d), {
                "nom": "Throkmar", "nom_maladie": "fièvre des marais",
                "dd": 40})
            assert "DÉJÀ" in r_dup.text, r_dup.text[:200]
            # jour 1 : échec forcé (DD 40) → dégâts
            from server.tools.dangers import maladies_quotidiennes_pour_pj
            l1 = await maladies_quotidiennes_pour_pj(_ctx(d), "Throkmar")
            assert l1 and "endommagés" in l1[0], l1
            # 3 jours avec DD 1 → 3 succès → guérison
            fp = os.path.join(d, "fiches", "fiche_throkmar.json")
            for _ in range(3):
                f = json.load(open(fp, encoding="utf-8"))
                for m in (f.get("maladies_en_cours") or []):
                    m["dd"] = 1
                json.dump(f, open(fp, "w", encoding="utf-8"),
                          ensure_ascii=False)
                await maladies_quotidiennes_pour_pj(_ctx(d), "Throkmar")
            f = json.load(open(fp, encoding="utf-8"))
            assert not (f.get("maladies_en_cours")), f.get("maladies")
            assert "Malade" not in (f.get("conditions") or []), (
                f.get("conditions"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
