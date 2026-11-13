# -*- coding: utf-8 -*-
"""Tests : poison mécanique, gardes de nuit, éclairage, vitesse (13/11).

Les 4 mécaniques 3.5 manquantes identifiées par l'audit :
1. 🧪 Poison : save Vigueur serveur, dégâts de caractéristique initial
   (et secondaire échu post-tour), PV max réduits par la CON.
2. 🌙 Veille de nuit : sans veilleur → risque de nuit agitée (Fatigué).
3. 🕯️ Éclairage : l'entrée de donjon prévient quand aucune source existe.
4. 🏃 Vitesse : base par race, réduite par l'armure et la charge.

Usage : py -m pytest tests/test_mecas_manquantes.py -q
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
from server.tools.inventaire import (  # noqa: E402
    _normaliser_fiche, _vitesse_deplacement,
)

_TOOLS = discover_tools()


class _FakeSession:
    def __init__(self):
        self.broadcasts: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)

    def remember_player_message(self, *_a, **_k) -> None:
        pass


async def _creer_pj(d: str, nom: str = "Throkmar", race: str = "Humain",
                    joueur: str = "alain") -> None:
    st = PartyState(data_dir=d, partie_id="meca")
    etat = st.load()
    etat["phase"] = "exploration"
    etat.setdefault("pj", []).append({
        "nom": nom, "joueur": joueur, "pv": 16, "pv_max": 16})
    st.save(etat)
    r = await invoke_tool(_TOOLS["fiche_perso_creer_rapide"], _ctx(d), {
        "nom": nom, "race": race, "classe": "Guerrier", "niveau": 1,
        "joueur": joueur,
        "carac_texte": "FOR 16, DEX 12, CON 14, INT 10, SAG 10, CHA 8"})
    assert r.text.startswith(("✅", "ℹ️")), r.text[:150]


def _ctx(d: str, joueur: str = "alain") -> ToolContext:
    return ToolContext(partie_id="meca", joueur=joueur, data_dir=d)


def _fiche(d: str, nom: str = "Throkmar") -> dict:
    fp = os.path.join(d, "fiches", f"fiche_{nom.lower()}.json")
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ #
# 1. Poison
# ------------------------------------------------------------------ #


def test_poison_reussi_sans_degats():
    """Un DD très bas (1) : la sauvegarde réussit, AUCUN dégât."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_p1_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(_TOOLS["appliquer_poison"], _ctx(d), {
                "nom": "Throkmar", "nom_poison": "venin de goule",
                "dd": 1, "degats_initial": "1d2", "carac": "CON",
                "source": "morsure"})
            assert not r.text.startswith("❌"), r.text[:200]
            assert "RÉSISTE" in r.text, r.text[:300]
            f = _fiche(d)
            assert not (f.get("degats_carac") or {}), f.get("degats_carac")
            assert "Empoisonné" not in (f.get("conditions") or [])
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_poison_echec_degats_et_secondaire_programme():
    """DD 40 (toujours raté) : dégâts de CON + Empoisonné + effet
    secondaire programmé + PV max réduits."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_p2_")
        try:
            await _creer_pj(d)
            base = int(_fiche(d).get("pv_max") or 16)
            r = await invoke_tool(_TOOLS["appliquer_poison"], _ctx(d), {
                "nom": "Throkmar", "nom_poison": "venin de goule",
                "dd": 40, "degats_initial": "1d6", "carac": "CON",
                "source": "morsure"})
            assert not r.text.startswith("❌"), r.text[:200]
            assert "endommagés" in r.text, r.text[:400]
            f = _fiche(d)
            dc = f.get("degats_carac") or {}
            assert 1 <= int(dc.get("CON", 0)) <= 6, dc
            assert "Empoisonné" in (f.get("conditions") or []), f
            pois = f.get("poisons_en_cours") or []
            assert len(pois) == 1 and pois[0].get("carac") == "CON", pois
            # PV max réduits : base − 1 PV/niveau/point de CON
            pv_max = int(f.get("pv_max", base))
            dmg = int(dc["CON"])
            assert pv_max == base - dmg, (pv_max, dmg, base)
            assert int(f.get("pv", base)) <= pv_max, (f.get("pv"), pv_max)
            assert int(f.get("pv_max_base", base)) == base
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_poison_secondaire_applique_puis_nettoye():
    """Un secondaire échu (ts passé) : nouvelle sauvegarle (DD 40 → échec),
    nouveaux dégâts, puis liste vidée (plus de « Empoisonné »)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_p3_")
        try:
            await _creer_pj(d)
            from datetime import datetime as _dt
            r0 = await invoke_tool(_TOOLS["appliquer_poison"], _ctx(d), {
                "nom": "Throkmar", "nom_poison": "venin de goule",
                "dd": 40, "degats_initial": "1d2", "carac": "CON"})
            assert not r0.text.startswith("❌"), r0.text[:200]
            fp = os.path.join(d, "fiches", "fiche_throkmar.json")
            f = json.load(open(fp, encoding="utf-8"))
            f["poisons_en_cours"][0]["ts"] = "2000-01-01T00:00:00"
            json.dump(f, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False)
            from server.tools.dangers import poisons_secondaires_pour_pj
            lignes = await poisons_secondaires_pour_pj(_ctx(d), "Throkmar")
            assert lignes and "secondaire" in lignes[0], lignes
            f = json.load(open(fp, encoding="utf-8"))
            assert not (f.get("poisons_en_cours")), f.get("poisons_en_cours")
            assert "Empoisonné" not in (f.get("conditions") or []), (
                f.get("conditions"))
            # deux échecs consécutifs : la CON a pris au moins 2 points
            assert int((f.get("degats_carac") or {}).get("CON", 0)) >= 2
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_pv_max_restaure_quand_con_guerit():
    """degats_carac CON vidé → pv_max revient à la base (16)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_p4_")
        try:
            await _creer_pj(d)
            fp = os.path.join(d, "fiches", "fiche_throkmar.json")
            f = json.load(open(fp, encoding="utf-8"))
            base = int(f.get("pv_max") or 0)
            assert base > 0, base
            f["degats_carac"] = {"CON": 3}
            json.dump(f, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False)
            f = _normaliser_fiche(f)
            assert int(f["pv_max"]) == base - 3, (f["pv_max"], base)
            assert int(f["pv_max_base"]) == base
            f["degats_carac"] = {}
            f = _normaliser_fiche(f)
            assert int(f["pv_max"]) == base, f["pv_max"]
            assert "pv_max_base" not in f
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 2. Vitesse de déplacement
# ------------------------------------------------------------------ #


def test_vitesse_base_par_race_et_armure():
    """Humain 9 m ; cotte de mailles → 6 m ; nain inchangé ; charge
    lourde sans armure → 6 m."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_v_")
        try:
            await _creer_pj(d, "Humain", "Humain")
            await _creer_pj(d, "Nain", "Nain", joueur="pip")
            # Humain sans armure : 9 m
            f_h = _fiche(d, "Humain")
            assert _vitesse_deplacement(f_h) == 9.0
            # Humain en cotte de mailles (Moyenne) : 6 m
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Humain", "objet": "Cotte de mailles",
                "quantite": 1, "portee": "permanent"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            f_h = _fiche(d, "Humain")
            assert _vitesse_deplacement(f_h) == 6.0, f_h.get("equipement")
            # Nain en cotte : l'armure ne le ralentit pas (20 ft)
            r = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx("d" if False else d, "pip"), {
                "nom": "Nain", "objet": "Cotte de mailles",
                "quantite": 1, "portee": "permanent"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            f_n = _fiche(d, "Nain")
            assert _vitesse_deplacement(f_n) == 6.0
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 3. Veille de nuit (le risque sans veilleur)
# ------------------------------------------------------------------ #


def test_repos_sans_veille_peut_fatiguer():
    """Plusieurs repos forçés : sans veilleur, ~25 % de nuits agitées
    (au moins une Fatigué sur 12 essais probabilistes est quasi sûr).
    On vérifie plutôt le MÉCANISME : l'état contient la condition quand
    le d20 tombe ≤ 5 — on force en appelant plusieurs fois."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_r_")
        try:
            await _creer_pj(d)
            fatigues = 0
            for _ in range(12):
                r = await invoke_tool(_TOOLS["repos_long"], _ctx(d), {
                    "forcer": True, "veille": ""})
                assert not r.text.startswith("⛔"), r.text[:200]
                f = _fiche(d)
                if "Fatigué" in (f.get("conditions") or []):
                    fatigues += 1
                    # le texte raconte la nuit agitée
                    assert "Nuit agitée" in r.text, r.text[:300]
                    break
            # 12 tirages à 25 % → P(0 réussite) ≈ 3 % ; un échec de ce
            # test est statistiquement possible mais rare.
            assert fatigues >= 1, (
                "12 nuits sans veilleur : aucune nuit agitée (3 % de "
                "chance) — relancer si malchanceux.")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_repos_avec_veille_sans_risque():
    """Avec un veilleur désigné : jamais de nuit agitée."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_r2_")
        try:
            await _creer_pj(d)
            await _creer_pj(d, "Branda", "Humain", joueur="pip")
            for _ in range(6):
                r = await invoke_tool(_TOOLS["repos_long"], _ctx(d), {
                    "forcer": True, "veille": "Branda"})
                assert not r.text.startswith("⛔"), r.text[:200]
                assert "Veille" in r.text, r.text[:300]
                f = _fiche(d, "Throkmar")
                assert "Fatigué" not in (f.get("conditions") or []), (
                    f.get("conditions"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 4. Éclairage à l'entrée du donjon
# ------------------------------------------------------------------ #


def test_entree_donjon_previent_sans_lumiere():
    """Sans torche ni sort : l'entrée de donjon prévient d'obscurité
    totale. Avec une torche : la source est listée."""

    async def _run():
        d = tempfile.mkdtemp(prefix="meca_l_")
        try:
            await _creer_pj(d)
            st = PartyState(data_dir=d, partie_id="meca")
            etat = st.load()
            etat["quete"] = {"titre": "Test", "source": "[t] / Test"}
            st.save(etat)
            # le donjon minimal
            os.makedirs(os.path.join(d, "scenarios", "T", "Cartes"),
                        exist_ok=True)
            manifeste = {
                "id": "Catacombes test", "nom": "Catacombes test",
                "type": "donjon", "carte": "",
                "grille": [{"id": "entree", "type": "entrée", "x": 0,
                            "y": 0, "portes": {"nord": True}}],
                "depart": {"x": 0, "y": 0},
            }
            (os.path.join(d, "scenarios", "T", "Catacombes test"
                          + ".donjon.json"))
            with open(os.path.join(d, "scenarios", "T",
                                   "Catacombes test.donjon.json"), "w",
                      encoding="utf-8") as f:
                json.dump(manifeste, f, ensure_ascii=False)
            r = await invoke_tool(_TOOLS["carte_donjon_entrer"], _ctx(d), {
                "donjon_id": "Catacombes test"})
            assert not r.text.startswith("❌"), r.text[:300]
            assert "OBSURITÉ TOTALE" in r.text, r.text[:300]
            # avec une torche dans l'inventaire : la source est listée
            r_t = await invoke_tool(_TOOLS["inventaire_ajouter"], _ctx(d), {
                "nom": "Throkmar", "objet": "Torche", "quantite": 3,
                "portee": "permanent"})
            assert not r_t.text.startswith(("❌", "⚠️")), r_t.text[:150]
            r2 = await invoke_tool(_TOOLS["carte_donjon_entrer"], _ctx(d), {
                "donjon_id": "Catacombes test"})
            assert "Torche" in r2.text and "OBSURITÉ" not in r2.text, (
                r2.text[:300])
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
