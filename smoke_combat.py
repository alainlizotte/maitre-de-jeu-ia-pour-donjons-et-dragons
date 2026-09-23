# -*- coding: utf-8 -*-
import asyncio, json, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, '.')
from server.tools.base import invoke_tool
from server.tools.registry import discover_tools
from server.game.state import PartyState

TOOLS = discover_tools("server.tools")
PID = "t_comb"

CIMETIERRE = {
    "nom": "Gauran", "race": "Humain", "classe": "Guerrier", "niveau": 1,
    "carac": {"FOR": 16, "DEX": 14, "CON": 15, "INT": 10, "SAG": 10, "CHA": 8},
    "pv": 23, "pv_max": 23, "ca": 18, "bab": 3, "or": 1000,
    "sauvegardes": {"Vigueur": 3, "Reflexes": 3, "Volonte": 3},
    "inventaire": [],
}


def _ctx(d):
    from server.tools.base import ToolContext
    return ToolContext(partie_id=PID, joueur="Test", data_dir=d)


async def _invoke(d, nom, args):
    return await invoke_tool(TOOLS[nom], _ctx(d), args)


async def main():
    d = tempfile.mkdtemp(prefix="dnd_combat_")
    try:
        Path(d).joinpath("fiches").mkdir(parents=True, exist_ok=True)
        fiche = dict(CIMETIERRE)
        fiche["nom"] = "Gauran"
        (Path(d) / "fiches" / "fiche_gauran.json").write_text(
            json.dumps(fiche, ensure_ascii=False), encoding="utf-8")

        from server.game.state import PartyState
        mou = {"type": "Mou", "nom": "Elandor", "pv": 8, "pv_max": 8,
               "forcer": True}
        etat = {
            "phase": "combat", "tour": 1, "courant_tour_pour": "Gauran",
            "initiative": [{"nom": "Gauran", "init": 10}],
            "pj": [{"nom": "Gauran", "pv": 23, "pv_max": 23}],
            "monstres_combat": [{"nom": "Kobold", "pv": 4, "pv_max": 4,
                                 "ca": 20, "conditions": []}],
        }
        PartyState(data_dir=d, partie_id=PID).save(etat)

        r = await _invoke(d, "lancer_degats",
                          {"nom_personnage": "Gauran", "nb_des": 1, "faces": 8,
                           "bonus": 3, "arme_ou_sort": "Grande hache",
                           "cible": "Kobold"})
        print("=== lancer_degats (Grande hache) ===")
        print(repr(r.text))
    finally:
        shutil.rmtree(d, ignore_errors=True)

asyncio.run(main())
