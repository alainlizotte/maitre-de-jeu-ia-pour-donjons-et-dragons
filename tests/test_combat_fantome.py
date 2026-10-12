# -*- coding: utf-8 -*-
# Combat fantôme (partie 60446e2f) : le 9B collait `phase=combat` via
# etat_partie_patch après un calcul d'initiative cosmétique — un combat sans
# monstre ni PV, injouable (tours « expirent par défaut » en boucle).
# Gardes : etat_partie_patch (phase=combat, initiative, courant_tour_pour
# refusés), etat_partie_save (bascule VERS combat refusée). Le seul poseur
# légitime reste `demarrer_combat`.

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402


def _partie(d: str, phase: str = "exploration") -> None:
    PartyState(data_dir=d, partie_id="t_fantome").save({
        "meta": {"titre": "test"},
        "phase": phase,
        "pj": [{"nom": "Faelan", "pv": 9, "pv_max": 9, "joueur": "Testeur",
                "niveau": 1}],
        "pnj": [],
        "histoire": [],
    })


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id="t_fantome", joueur="Testeur", data_dir=d)


def _appeler(d: str, outil: str, args: dict):
    tools = discover_tools()
    spec = tools.get(outil)
    assert spec is not None, f"{outil} introuvable"
    return asyncio.run(invoke_tool(spec, _ctx(d), args))


def test_patch_phase_combat_refuse():
    """`etat_partie_patch("phase", "combat")` → REFUSÉ : l'état de combat
    appartient au moteur (engager_combat → demarrer_combat)."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d)
    tr = _appeler(d, "etat_partie_patch",
                  {"chemin": "phase", "valeur": "combat"})
    assert tr.text.startswith("🚫 INTERDIT"), tr.text
    etat = PartyState(data_dir=d, partie_id="t_fantome").load()
    assert etat["phase"] == "exploration", etat["phase"]


def test_patch_phase_combat_json_quoted_refuse():
    """Valeur JSON quotée (`"combat"`) → refusée aussi (normalisation)."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d)
    tr = _appeler(d, "etat_partie_patch",
                  {"chemin": "phase", "valeur": '"combat"'})
    assert tr.text.startswith("🚫 INTERDIT"), tr.text


def test_patch_phase_exploration_autorisee():
    """Les phases NON combat restent patchables (flow narratif légitime)."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d, phase="opening")
    tr = _appeler(d, "etat_partie_patch",
                  {"chemin": "phase", "valeur": "exploration"})
    assert not tr.text.startswith("🚫 INTERDIT"), tr.text
    etat = PartyState(data_dir=d, partie_id="t_fantome").load()
    assert etat["phase"] == "exploration"


def test_patch_initiative_et_courant_tour_refuses():
    """`initiative` / `courant_tour_pour` : propriété du moteur → refusés."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d)
    tr1 = _appeler(d, "etat_partie_patch",
                   {"chemin": "initiative",
                    "valeur": '[{"nom":"Drow","init":2}]'})
    assert tr1.text.startswith("🚫"), tr1.text
    tr2 = _appeler(d, "etat_partie_patch",
                   {"chemin": "courant_tour_pour", "valeur": "Drow"})
    assert tr2.text.startswith("🚫"), tr2.text
    etat = PartyState(data_dir=d, partie_id="t_fantome").load()
    assert not etat.get("initiative"), etat.get("initiative")


def test_save_bascule_vers_combat_refusee():
    """`etat_partie_save` qui bascule exploration → combat → REFUSÉ."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d)
    etat = PartyState(data_dir=d, partie_id="t_fantome").load()
    etat["phase"] = "combat"
    import json as _json
    tr = _appeler(d, "etat_partie_save",
                  {"nouveau_etat": _json.dumps(etat, ensure_ascii=False)})
    assert tr.text.startswith("🚫 INTERDIT"), tr.text
    etat2 = PartyState(data_dir=d, partie_id="t_fantome").load()
    assert etat2["phase"] == "exploration", etat2["phase"]


def test_save_hors_combat_autorise():
    """Un save qui NE bascule PAS vers combat reste légitime (fin de scène…)."""
    d = tempfile.mkdtemp(prefix="dnd35_fantome_")
    _partie(d)
    etat = PartyState(data_dir=d, partie_id="t_fantome").load()
    etat["meta"]["titre"] = "sauvegarde de contrôle"
    import json as _json
    tr = _appeler(d, "etat_partie_save",
                  {"nouveau_etat": _json.dumps(etat, ensure_ascii=False)})
    assert not tr.text.startswith("🚫 INTERDIT"), tr.text


def test_demarrer_combat_restera_le_seul_poseur():
    """Source : le garde du save compare la phase AVANT — `demarrer_combat`
    pose la phase directement (hors etat_partie_save) et reste fonctionnel ;
    vérif par source que la garde ne cible QUE etat_partie_save/patch."""
    src = open(os.path.join(sys_path, "server", "tools", "state.py"),
               encoding="utf-8").read()
    i = src.find("async def demarrer_combat")
    j = src.find("async def finir_combat")
    bloc = src[i:j]
    assert "INTERDIT" not in bloc, \
        "demarrer_combat ne doit PAS être bridé (c'est le poseur légitime)"


def test_filet_reversion_combat_fantome_present():
    """main.py : le filet post-tour de réversion existe (phase sans
    engagement → réversion + note de transparence)."""
    src = open(os.path.join(sys_path, "server", "main.py"),
               encoding="utf-8").read()
    assert "_phase_pretour" in src
    assert "Combat fantôme" in src
    assert '"engager_combat",\n                                           "demarrer_combat"' \
        in src or ('"engager_combat"' in src and '"demarrer_combat"' in src)
