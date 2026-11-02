# -*- coding: utf-8 -*-
"""R7 : les blessures persistent entre retraite et re-engagement.

La boucle infinie (campagne shadows) : l'équipe infligeait ~12 dégâts
au Loup terrible, la retraite tirait à 3 rounds de malchance, et le
re-engagement REMETTAIT le loup à plein (18 PV allégés) — la rencontre
était invincible (4 combats, 4 retraites, le loup jamais mort).

Le fix : `_clore_combat_etat(retraite)` sauvegarde les PV restants des
survivants dans `blessures_en_cours` (clé d'espèce) ; `engager_combat`
applique la blessure à la créature re-engageée (entrée consommée).

Usage : py -m pytest tests/test_blessures_persistantes.py -q
"""

from __future__ import annotations

import asyncio
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


def _monte(niveaux: list[int]) -> tuple[str, ToolContext]:
    d = tempfile.mkdtemp(prefix="bless_")
    shutil.copy2(os.path.join(_REPO, "server", "data", "bestiaire.json"),
                 os.path.join(d, "bestiaire.json"))
    st = PartyState(data_dir=d, partie_id="bless")
    etat = st.load()
    etat["phase"] = "exploration"
    etat["pj"] = [
        {"nom": f"PJ{i}", "joueur": f"j{i}", "race": "Humain",
         "classe": "Guerrier", "niveau": niv, "pv": 13, "pv_max": 13,
         "ca": 16, "conditions": [],
         "carac": {"FOR": 16, "DEX": 10, "CON": 14, "INT": 10, "SAG": 10,
                   "CHA": 8}}
        for i, niv in enumerate(niveaux)]
    etat["quete"] = {"bible": {"joueurs_recommandes": "3-4"}}
    etat["donjon"] = {"id": "divers_test",
                      "grille": [{"x": 0, "y": 0,
                                  "ennemis": ["Loup terrible ×1"]}],
                      "courant": [0, 0]}
    st.save(etat)
    return d, ToolContext(partie_id="bless", joueur="j0", data_dir=d)


def test_blesse_reste_apres_retraite_et_re_engagement():
    """Combat → blessure (6 PV restants) → retraite → re-engagement : le
    loup repart de ses PV restants (PAS pleins)."""
    d, ctx = _monte([1, 1, 1])
    # Combat 1 : engagement du loup (allégé ×0.4 → 18 PV).
    r1 = asyncio.run(invoke_tool(_TOOLS["engager_combat"], ctx,
                                 {"monstres": "Loup terrible"}))
    assert not r1.text.startswith(("⛔", "❌")), r1.text[:200]
    st = PartyState(data_dir=d, partie_id="bless")
    etat = st.load()
    pv1 = int(etat["monstres_combat"][0]["pv"])
    assert 0 < pv1 <= 18, pv1
    # L'équipe blesse le loup : 6 PV restants (simulation des dégâts).
    etat["monstres_combat"][0]["pv"] = 6
    st.save(etat)
    # Retraite : le loup SURVIT blessé.
    r2 = asyncio.run(invoke_tool(_TOOLS["retraite_combat"], ctx, {}))
    assert "Retraite" in r2.text, r2.text[:120]
    etat = PartyState(data_dir=d, partie_id="bless").load()
    assert etat.get("phase") == "exploration"
    assert int((etat.get("blessures_en_cours") or {}).get(
        "loup terrible", 99)) == 6, etat.get("blessures_en_cours")
    # Le garde dédup fait fenêtre (120 s) : on simule l'écoulement du
    # temps (comme dans la campagne réelle où les re-engagements ont eu
    # lieu après déplacement/attente).
    import time as _time
    etat = st.load()
    for h in (etat.get("historique_engagements") or []):
        h["ts"] = _time.time() - 121.0
    st.save(etat)
    # Re-engagement : le loup repart BLESSE (pas plein).
    r3 = asyncio.run(invoke_tool(_TOOLS["engager_combat"], ctx,
                                 {"monstres": "Loup terrible"}))
    assert not r3.text.startswith(("⛔", "❌")), r3.text[:200]
    assert "deja blesse" in r3.text, r3.text[:300]
    etat = PartyState(data_dir=d, partie_id="bless").load()
    pv2 = int(etat["monstres_combat"][0]["pv"])
    assert pv2 == 6, (pv1, pv2)
    # L'entrée est consommée (une creature morte n'en laisse pas).
    assert (etat.get("blessures_en_cours") or {}) == {}, (
        etat.get("blessures_en_cours"))
