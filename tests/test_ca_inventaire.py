# -*- coding: utf-8 -*-
"""Test : la CA est recalculee quand l'armure est ajoutée."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_TOOLS = discover_tools()


def test_ca_recalculee_quand_armure_ajoutee():
    """Le fix Crown of Mystra : la CA passe de 11 (sans armure) à 15-16
    quand la Cotte de maille est ajoutée via inventaire_ajouter."""
    async def _run():
        d = tempfile.mkdtemp(prefix="ca_inv_")
        try:
            ctx = ToolContext(partie_id="cainv", joueur="e2e", data_dir=d)
            r = await invoke_tool(
                _TOOLS["fiche_perso_creer_rapide"], ctx, {
                    "nom": "Cavalier", "race": "Humain", "classe": "Guerrier",
                    "niveau": 1, "joueur": "e2e",
                    "carac_texte": "FOR 16, DEX 12, CON 14, INT 10, "
                                   "SAG 10, CHA 8"})
            assert r.text.startswith("✅"), r.text[:120]
            # CA avant armure : 10 + DEX +1 = 11.
            import io as _io
            import json as _json
            fp = os.path.join(d, "fiches", "fiche_cavalier.json")
            f = _json.load(_io.open(fp, encoding="utf-8"))
            ca_avant = int(f.get("ca", 0))
            assert ca_avant == 11, ca_avant
            # L'armure est ajoutée.
            r2 = await invoke_tool(_TOOLS["inventaire_ajouter"], ctx, {
                "nom": "Cavalier", "objet": "Cotte de maille",
                "quantite": 1, "portee": "permanent"})
            assert not r2.text.startswith(("❌", "⚠️")), r2.text[:120]
            f = _json.load(_io.open(fp, encoding="utf-8"))
            ca_apres = int(f.get("ca", 0))
            # 10 + Cotte de mailles (+5) + min(DEX +1, dex_max 2) = 16.
            assert ca_apres == 16, ca_apres
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
