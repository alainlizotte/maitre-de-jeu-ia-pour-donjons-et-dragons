"""Vérification E2E : persistance des images après un tour réel.

Prépare un combat (tour de Mireille), envoie un message, attend le DM,
puis vérifie partie_<id>.json : monstres_combat[i].image_url persisté +
rencontres_images alimenté (le hook post-tour appelle image_pour pour
chaque monstre, placeholder SVG si ComfyUI désactivé).

Usage : py tests\e2e_persist_images.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets

BASE = "http://localhost:8123"
WS = "ws://localhost:8123/ws"
DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "data",
)


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


async def main() -> None:
    pid = _post("/api/parties", {"titre": "E2E Persist"})["partie_id"]
    spath = os.path.join(DATA, f"partie_{pid}.json")
    now = datetime.now().isoformat()
    etat = {
        "meta": {"titre": "E2E Persist", "cadre": "Côte des Épées (Faerûn)",
                 "regles": "D&D 3.5", "date_creation": now, "date_maj": now},
        "phase": "combat", "tour": 1, "courant_tour_pour": "Mireille",
        "initiative": [
            {"nom": "Mireille", "init": 15, "jet_brut": 14, "mod": 1},
            {"nom": "Gobelin", "init": 10, "jet_brut": 9, "mod": 1},
        ],
        "pj": [{
            "nom": "Mireille", "joueur": "mireille", "race": "Humaine",
            "classe": "Magicienne", "niveau": 1, "pv": 6, "pv_max": 6,
            "ca": 12, "carac": {"FOR": 9, "DEX": 14, "CON": 12, "INT": 16,
                                "SAG": 11, "CHA": 13},
        }],
        "pnj": [],
        "lieu": {"nom": "Crypte", "type": "donjon", "description": "",
                 "position_x": 0, "position_y": 0},
        "donjon": {"id": None, "salles_visitees": [], "portes_bloquees": [],
                   "grille": [], "courant": [0, 0]},
        "donjons_exploreres": {},
        "quete": {"titre": "", "pitch": "", "source": ""},
        "histoire": [], "derniere_narration": "",
        "monstres_combat": [
            {"nom": "Gobelin", "pv": 5, "pv_max": 5, "ca": 15, "fp": "1/3",
             "conditions": []},
        ],
        "rencontres_images": [],
    }
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)

    ws = await websockets.connect(f"{WS}/{pid}", ping_interval=20, ping_timeout=60)
    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert m.get("event") == "joined", m
    print(">>> mireille: Je passe mon tour.")
    await ws.send(json.dumps({
        "type": "say", "player": "mireille", "text": "Je passe mon tour.",
    }))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
        if msg.get("type") == "dm":
            print("DM reçu :", msg.get("text", "")[:150].replace("\n", " "))
            break
    await ws.close()

    with open(spath, encoding="utf-8") as f:
        final = json.load(f)

    fails = 0
    def check(label, cond):
        nonlocal fails
        print(("✅ " if cond else "❌ ") + label)
        if not cond:
            fails += 1

    gob = next((m2 for m2 in final.get("monstres_combat", [])
                if m2["nom"] == "Gobelin"), None)
    check("monstres_combat[Gobelin].image_url persisté",
          gob is not None and bool(gob.get("image_url")))
    journal = final.get("rencontres_images") or []
    check("rencontres_images contient le Gobelin",
          any("gobelin" in (r.get("nom") or "").lower() for r in journal))
    # La vue REST expose bien les mêmes données (réhydratation front).
    vue = json.loads(urllib.request.urlopen(
        f"{BASE}/api/parties/{pid}", timeout=10).read())["etat"]
    check("API REST expose image_url + rencontres_images",
          vue.get("monstres_combat", [{}])[0].get("image_url")
          and vue.get("rencontres_images"))

    for p in (spath, os.path.join(DATA, f"chat_{pid}.json")):
        if os.path.isfile(p):
            os.unlink(p)
    print(f"\n{'✅ PERSIST IMAGES OK' if fails == 0 else f'❌ {fails} ÉCHEC(S)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
