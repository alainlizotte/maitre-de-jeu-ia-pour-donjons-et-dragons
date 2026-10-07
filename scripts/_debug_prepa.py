# -*- coding: utf-8 -*-
"""Debug préparations de sorts (le format attendu)."""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools       # noqa: E402

DATA = tempfile.mkdtemp()
os.makedirs(os.path.join(DATA, "fiches"), exist_ok=True)
fiche = {"nom": "Auditeur", "race": "Humain", "classe": "Magicien",
         "niveau": 3, "pv": 18, "pv_max": 18, "ca": 12, "or": 100,
         "inventaire": [],
         "sorts": {"niveau0": ["Rayon de givre"], "niveau1": ["Sommeil"],
                   "niveau2": ["Invisibilité"]},
         "emplacements": {"niveau0": 4, "niveau1": 3, "niveau2": 2},
         "conditions": []}
open(os.path.join(DATA, "fiches", "fiche_auditeur.json"), "w",
     encoding="utf-8").write(json.dumps(fiche, ensure_ascii=False))

ctx = ToolContext(partie_id="t", joueur="t", data_dir=DATA)
tools = discover_tools()


async def main():
    for prep in ('{"Sommeil": 2}',
                 '{"niveau1": {"Sommeil": 2}}',
                 '["Sommeil", "Sommeil"]'):
        r = await invoke_tool(tools["preparer_sorts"], ctx,
                              {"nom_personnage": "Auditeur",
                               "preparations_json": prep})
        print("prep", prep, "→", r.text[:220].replace("\n", " ¶ "))


asyncio.run(main())
