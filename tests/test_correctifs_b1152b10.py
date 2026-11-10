# -*- coding: utf-8 -*-
"""Tests des 5 correctifs issus de la partie b1152b10 (10/11).

1. La mort posée via fiche_perso_mettre_a_jour(pv=-10) transporte les
   conditions dérivées vers l'état (avant : PJ décédé aux barres pleines).
2. La réconciliation post-tour aligne l'état sur la fiche + game_over.
2bis. La mort DÉCLARÉE en prose sans dégâts réels → mise en cohérence.
4. L'article narratif part à l'ajout d'inventaire (« un parchemin »).
5. Une monture ne s'empile pas (achat + re-ajout = x2 refusé).
3. Le verdict du jet arbitré est mémorisé (fait foi au tour suivant).

Usage : py -m pytest tests/test_correctifs_b1152b10.py -q
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
from server.main import _morts_hallucinees  # noqa: E402
from server.main import _reconcilier_fiches_etat  # noqa: E402
from server.main import _jet_resoudre  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_TOOLS = discover_tools()


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id="b1152", joueur="alain", data_dir=d)


async def _creer_pj(d: str, pv: int = 16, conditions: list | None = None) -> None:
    """Partie + fiche Throk'mar (proprietaire alain)."""
    st = PartyState(data_dir=d, partie_id="b1152")
    etat = st.load()
    etat["phase"] = "exploration"
    etat["pj"] = [{"nom": "Throk'mar", "joueur": "alain", "pv": pv,
                   "pv_max": 16, "conditions": conditions or []}]
    st.save(etat)
    r = await invoke_tool(_TOOLS["fiche_perso_creer_rapide"], _ctx(d), {
        "nom": "Throk'mar", "race": "Demi-orc", "classe": "Guerrier",
        "niveau": 1, "joueur": "alain",
        "carac_texte": "FOR 17, DEX 10, CON 14, INT 8, SAG 12, CHA 8"})
    assert r.text.startswith(("✅", "ℹ️")), r.text[:150]
    if conditions or pv != 16:
        fp = os.path.join(d, "fiches", "fiche_throk_mar.json")
        with open(fp, "r", encoding="utf-8") as f:
            fiche = json.load(f)
        fiche["pv"] = pv
        fiche["conditions"] = conditions or []
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)


class _FakeSession:
    def __init__(self):
        self.broadcasts: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)

    def remember_player_message(self, *_a, **_k) -> None:
        pass


# ------------------------------------------------------------------ #
# 1. La mort via mettre_a_jour transporte les conditions dérivées
# ------------------------------------------------------------------ #


def test_mettre_a_jour_pv_negatif_sync_mort():
    """pv=-10 via mettre_a_jour : l'état reçoit pv=-10 ET conditions Mort
    (avant : le PJ décédé restait 'vivants' dans l'état)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152a_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(
                _TOOLS["fiche_perso_mettre_a_jour"], _ctx(d),
                {"nom": "Throk'mar", "champ": "pv", "valeur": "-10",
                 "interne": True})
            assert not r.text.startswith("❌"), r.text[:150]
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            pj = (etat.get("pj") or [])[0]
            assert int(pj.get("pv")) == -10, pj
            assert "Mort" in (pj.get("conditions") or []), pj
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 2. La réconciliation post-tour
# ------------------------------------------------------------------ #


def test_reconcilie_etat_sur_fiche_et_declare_game_over():
    """État 16/16 vivant + fiche -10/Mort → l'état est réaligné ; si TOUS
    les PJ sont morts → phase game_over."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152b_")
        try:
            await _creer_pj(d, pv=16)
            # la fiche est morte (état divergent)
            fp = os.path.join(d, "fiches", "fiche_throk_mar.json")
            with open(fp, "r", encoding="utf-8") as f:
                fiche = json.load(f)
            fiche["pv"] = -10
            fiche["conditions"] = ["Mort"]
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(fiche, f, ensure_ascii=False)
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            lignes = _reconcilier_fiches_etat(etat, d)
            assert lignes and any("Réconciliation" in l for l in lignes), (
                lignes)
            assert any("game over" in l for l in lignes), lignes
            assert etat["phase"] == "game_over", etat["phase"]
            assert etat.get("game_over") is True
            pj = (etat.get("pj") or [])[0]
            assert int(pj.get("pv")) == -10
            assert "Mort" in (pj.get("conditions") or [])
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_reconciliation_ne_touche_pas_un_pj_coherent():
    """Fiche et état d'accord → aucune ligne, pas de game_over."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152c_")
        try:
            await _creer_pj(d, pv=16)
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            lignes = _reconcilier_fiches_etat(etat, d)
            assert lignes == [], lignes
            assert etat["phase"] == "exploration"
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 2bis. La mort déclarée en prose sans dégâts réels
# ------------------------------------------------------------------ #


def test_mort_hallucinee_detectee():
    """« s'effondre, inconscient… PV à 0/16 » sans tool de dégâts →
    ligne de mise en cohérence."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152d_")
        try:
            await _creer_pj(d, pv=16)
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            narration = (
                "Throk'mar est submergé par une odeur de pourriture… "
                "Throk'mar s'effondre, inconscient sous l'asphyxie. Son "
                "corps gît au sol, les PV à 0/16, tandis que le gardien "
                "observe d'un air triomphant.")
            lignes = _morts_hallucinees(narration, etat, trace=[])
            assert lignes, "aucune détection"
            assert "aucun dégât" in lignes[0], lignes[0]
            assert "Throk'mar" in lignes[0]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_mort_reelle_non_signalee():
    """Un tool de dégâts a tourné pour le PJ → la prose a le droit de
    narrer la chute (aucune ligne)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152e_")
        try:
            await _creer_pj(d, pv=16)
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            narration = "Throk'mar s'effondre, inconscient, les PV à 0/16."
            trace = [{"name": "fiche_perso_infliger_degats", "ok": True,
                      "args": {"nom": "Throk'mar", "degats": 26}}]
            lignes = _morts_hallucinees(narration, etat, trace=trace)
            assert lignes == [], lignes
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 4+5. Anti-article + monture non empilable
# ------------------------------------------------------------------ #


def test_anti_article_a_l_ajout():
    """« un parchemin » est rangé sous le nom propre « Parchemin »."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152f_")
        try:
            await _creer_pj(d)
            r = await invoke_tool(
                _TOOLS["inventaire_ajouter"], _ctx(d),
                {"nom": "Throk'mar", "objet": "un parchemin",
                 "quantite": 1, "portee": "quete"})
            assert not r.text.startswith(("❌", "⚠️")), r.text[:150]
            fp = os.path.join(d, "fiches", "fiche_throk_mar.json")
            with open(fp, "r", encoding="utf-8") as f:
                fiche = json.load(f)
            noms = [str(i.get("nom")) for i in (fiche.get("inventaire") or [])]
            assert "un parchemin" not in noms, noms
            assert any(n.lower() == "parchemin" for n in noms), noms
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_monture_ne_s_empile_pas():
    """Le mulet déjà possédé (permanent) : un 2e ajout (portée quête —
    l'exact scénario du x2 de b1152b10) est refusé."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152g_")
        try:
            await _creer_pj(d)
            r1 = await invoke_tool(
                _TOOLS["inventaire_ajouter"], _ctx(d),
                {"nom": "Throk'mar", "objet": "Âne ou mulet",
                 "quantite": 1, "portee": "permanent"})
            assert not r1.text.startswith(("❌", "⚠️")), r1.text[:150]
            r2 = await invoke_tool(
                _TOOLS["inventaire_ajouter"], _ctx(d),
                {"nom": "Throk'mar", "objet": "Âne ou mulet",
                 "quantite": 1, "portee": "quete"})
            assert "ne s'empile pas" in r2.text, r2.text[:200]
            fp = os.path.join(d, "fiches", "fiche_throk_mar.json")
            with open(fp, "r", encoding="utf-8") as f:
                fiche = json.load(f)
            mules = [i for i in (fiche.get("inventaire") or [])
                     if "mulet" in str(i.get("nom", "")).lower()
                     or "âne" in str(i.get("nom", "")).lower()]
            total = sum(int(m.get("qte", 1) or 1) for m in mules)
            assert total == 1, [(m.get("nom"), m.get("qte")) for m in mules]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 3. Le verdict du jet arbitré est mémorisé
# ------------------------------------------------------------------ #


def test_verdict_jet_memoire():
    """_jet_resoudre stocke dernier_jet (texte + verdict) dans l'état."""

    async def _run():
        d = tempfile.mkdtemp(prefix="b1152h_")
        try:
            await _creer_pj(d)
            st = PartyState(data_dir=d, partie_id="b1152")
            etat = st.load()
            etat["jet_attendu"] = {
                "personnage": "Throk'mar", "joueur": "alain",
                "categorie": "competence", "type": "Survie",
                "difficulte": 15,
            }
            st.save(etat)
            sess = _FakeSession()
            ok = await _jet_resoudre(sess, "b1152", "alain", data_dir=d)
            assert ok
            etat = PartyState(data_dir=d, partie_id="b1152").load()
            dj = etat.get("dernier_jet") or {}
            assert dj.get("personnage") == "Throk'mar", dj
            assert "test de Survie" in str(dj.get("texte")), dj
            assert dj.get("verdict") in ("reussite", "echec"), dj
            # l'attente est consommée
            assert not etat.get("jet_attendu")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
