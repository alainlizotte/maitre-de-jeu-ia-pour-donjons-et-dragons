# -*- coding: utf-8 -*-
"""⚡ Attaque sournoise (règle 3.5) dans le chemin déterministe.

- `lancer_degats(sournoise_des=N)` ajoute N d6 NON multipliés par un
  critique (« extra damage dice are not multiplied »), affichés dans un
  bloc ⚡ dédié.
- Le chemin déterministe (`/combat/action` + interception du chat)
  applique les dés de sournoise du roublard ((niveau+1)//2) quand la
  cible est privée de DEX : prise au dépourvu (`surpris`) ou FLANC
  déclaré (mots-clés chat : « dans le dos », « de flanc », …).

Usage : py -m pytest tests/test_attaque_sournoise.py -q
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.dice import lancer_degats  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402
from server.main import _INTENTION_FLANC_RE  # noqa: E402

_TOOLS = discover_tools()


def _ctx() -> ToolContext:
    return ToolContext(partie_id="sournoise", joueur="e2e",
                       data_dir=tempfile.mkdtemp(prefix="sournoise_"))


def _extraire_total(texte: str) -> int:
    m = re.search(r"Dégâts infligés : (\d+)", texte)
    assert m, texte[:300]
    return int(m.group(1))


def test_sans_sournoise_pas_de_bloc():
    async def _run():
        r0 = await invoke_tool(_TOOLS["lancer_degats"], _ctx(), {
            "nb_des": 1, "faces": 6, "bonus": 0,
            "arme_ou_sort": "Épée courte", "cible": "Gobelin"})
        assert "Attaque sournoise" not in r0.text
        assert 1 <= _extraire_total(r0.text) <= 6, r0.text[:200]
    asyncio.run(_run())


def test_sournoise_ajoute_des_d6():
    async def _run():
        r1 = await invoke_tool(_TOOLS["lancer_degats"], _ctx(), {
            "nb_des": 1, "faces": 6, "bonus": 0,
            "arme_ou_sort": "Épée courte", "cible": "Gobelin",
            "sournoise_des": 1})
        assert "⚡ **Attaque sournoise** : +1d6" in r1.text, r1.text[:300]
        # 1d6 (arme) + 1d6 (sournoise) → 2..12.
        assert 2 <= _extraire_total(r1.text) <= 12, r1.text[:200]
    asyncio.run(_run())


def test_sournoise_jamais_multipliee_par_critique():
    """×2 multiplie les dés d'ARME (+1 dé), pas les d6 de sournoise."""
    async def _run():
        r = await invoke_tool(_TOOLS["lancer_degats"], _ctx(), {
            "nb_des": 1, "faces": 6, "bonus": 3,
            "arme_ou_sort": "Épée courte", "cible": "Gobelin",
            "multiplicateur": 2, "sournoise_des": 2})
        assert "critique : +1 dés" in r.text, r.text[:300]
        assert "+2d6" in r.text, r.text[:300]
        # dés arme ×2 (2..12) + sournoise 2d6 (2..12) + bonus 3 → 7..27.
        t = _extraire_total(r.text)
        assert 7 <= t <= 27, (t, r.text[:300])
    asyncio.run(_run())


def test_mots_cles_de_flanc():
    oui = ("Flick frappe le gobelin dans le dos",
           "Aurora frappe le kobold de flanc",
           "Flick surgit en embuscade et frappe le bandit",
           "Flick attaque le loup par derrière")
    non = ("Brandt frappe le gobelin de sa hache",
           "Aurora soigne Brandt",
           "Flick regarde derrière la porte")
    for t in oui:
        assert _INTENTION_FLANC_RE.search(t), t
    for t in non:
        assert not _INTENTION_FLANC_RE.search(t), t
