# -*- coding: utf-8 -*-
"""S1/S2 — mise en cohérence victoire narrée + ciblage du plus blessé."""
import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = Path(__file__).resolve().parents[1]


def test_regex_victoire_narree():
    from server.main import _RE_VICTOIRE_NARREE
    # Déclarations explicites de fin → détection.
    assert _RE_VICTOIRE_NARREE.search(
        "Victoire ! Le combat est terminé. Les ennemis sont tous hors de "
        "combat.")
    assert _RE_VICTOIRE_NARREE.search(
        "Le combat s'achève avec la chute du dernier bandit.")
    assert _RE_VICTOIRE_NARREE.search(
        "Tous les ennemis gisent inanimés sur le sol froid.")
    assert _RE_VICTOIRE_NARREE.search("combat terminé — les corps reposent.")
    # Faux positifs à éviter.
    assert not _RE_VICTOIRE_NARREE.search(
        "Le combat commence ! Les bandits lèvent leurs armes.")
    assert not _RE_VICTOIRE_NARREE.search(
        "Vous vous préparez à l'affrontement, hache levée.")


def test_s1_cloture_victoire_sans_degats_zero_xp():
    """Chaîne S1 : ennemis marqués Détruit (par le garde de mise en
    cohérence) → clôture automatique en exploration, mais 0 XP car
    AUCUN dégât mécanique n'a été tracé (garde P15/P20)."""
    from server.game.state import PartyState
    from server.game.combat import boucle_auto
    from server.tools.base import ToolContext

    d = tempfile.mkdtemp(prefix="gruumsh_s1_")
    try:
        st = PartyState(data_dir=d, partie_id="s1")
        st.save({
            "phase": "combat",
            "tour": 3,
            "courant_tour_pour": "Brunhild",
            "initiative": [{"nom": "Brunhild", "init": 10},
                           {"nom": "Bandit", "init": 5}],
            "monstres_combat": [
                {"nom": "Bandit", "pv": 0, "pv_max": 11, "ca": 12,
                 "fp": "1/4", "conditions": ["Détruit"], "degats_recus": 0},
            ],
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 10,
                    "pv_max": 12}],
        })
        fdir = Path(d) / "fiches"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "fiche_brunhild.json").write_text(
            json.dumps({"nom": "Brunhild", "classe": "Guerrier",
                        "niveau": 1, "xp": 0, "pv": 10, "pv_max": 12,
                        "ca": 15, "carac": {"FOR": 14, "DEX": 12},
                        "equipement": []}, ensure_ascii=False),
            encoding="utf-8")

        res = asyncio.run(boucle_auto(ToolContext(
            partie_id="s1", joueur="J1", data_dir=d)))
        assert res.combat_termine == "victoire"
        etat = st.load()
        assert etat["phase"] == "exploration"
        fiche = json.loads(
            (fdir / "fiche_brunhild.json").read_text(encoding="utf-8"))
        assert fiche["xp"] == 0, fiche["xp"]
        assert any("Aucun dégât infligé" in e for e in res.events), res.events
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _FakeOrch:
    """Orch minimal FIDÈLE : execute_tool_direct → _run_one_tool_corps
    incluant l'auto-application des dégâts sur un monstre suivi."""

    def __init__(self, ctx, result):
        self._ctx = ctx
        self._result = result
        from server.tools.registry import discover_tools
        self._tools = discover_tools("server.tools")

    async def execute_tool_direct(self, nom, args, ctx, on_event, result):
        from server.tools.base import invoke_tool
        tr = await invoke_tool(self._tools[nom], ctx, args)
        result.state_patches.extend(
            [tr.state_patch] if getattr(tr, "state_patch", None) else [])
        # Réplique l'auto-application (orchestrator._auto_appliquer_degats).
        if nom == "lancer_degats" and not tr.text.startswith("❌"):
            import re as _re
            m = _re.search(r"Dégâts infligés\s*:\s*(\d+)", tr.text or "")
            if m:
                from server.tools.fiches import (
                    fiche_perso_infliger_degats as _infl)
                tr_inf = await _infl(ctx, str(args.get("cible") or ""),
                                     int(m.group(1)))
                result.state_patches.extend(
                    [tr_inf.state_patch]
                    if getattr(tr_inf, "state_patch", None) else [])
                note_inf = f"💥 {tr_inf.text}"
                tr = type(tr)(text=tr.text + "\n" + note_inf,
                              state_patch=tr.state_patch)
        return tr


def test_s2_filet_attaque_cible_le_plus_blesse():
    """Filet attaque sans cible identifiable (« frappe un ennemi ») → le
    PLUS BLESSÉ est visé et tombe."""
    from server.tools.base import ToolContext
    from server.game.state import PartyState
    from types import SimpleNamespace

    d = tempfile.mkdtemp(prefix="gruumsh_s2_")
    try:
        st = PartyState(data_dir=d, partie_id="s2")
        st.save({
            "phase": "combat",
            "monstres_combat": [
                {"nom": "Bandit", "pv": 11, "pv_max": 11, "ca": 12,
                 "fp": "1/4", "conditions": [], "degats_recus": 0},
                {"nom": "Bandit (2)", "pv": 2, "pv_max": 11, "ca": 12,
                 "fp": "1/4", "conditions": [], "degats_recus": 9},
            ],
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 10,
                    "pv_max": 12}],
        })
        fdir = Path(d) / "fiches"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "fiche_brunhild.json").write_text(
            json.dumps({"nom": "Brunhild", "classe": "Guerrier",
                        "niveau": 1, "xp": 0, "pv": 10, "pv_max": 12,
                        "ca": 15, "bab": 1,
                        "carac": {"FOR": 16, "DEX": 12}, "conditions": [],
                        "equipement": [{"nom": "Épée longue", "qte": 1,
                                        "poids": 1.81}]}, ensure_ascii=False),
            encoding="utf-8")

        from server.main import _attaque_pj_sans_jet
        ctx = ToolContext(partie_id="s2", joueur="J1", data_dir=d)
        result = SimpleNamespace(state_patches=[], narration="")
        orch = _FakeOrch(ctx, result)
        random.seed(3)
        note = asyncio.run(_attaque_pj_sans_jet(
            orch, ctx, None, result, "Brunhild",
            texte_declare="Brunhild frappe un ennemi !"))
        etat = st.load()
        cibles = {m["nom"]: m["pv"] for m in etat["monstres_combat"]}
        assert cibles["Bandit (2)"] <= 0, (note, cibles)
        assert "Bandit (2)" in note
    finally:
        shutil.rmtree(d, ignore_errors=True)
