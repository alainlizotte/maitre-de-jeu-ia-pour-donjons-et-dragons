# -*- coding: utf-8 -*-
"""Client WebSocket de validation — streaming, mécanique, latence.

Usage :
    py scripts/test_ws_streaming.py [partie_id] [message] [timeout_s]

Se connecte à /ws/{partie_id}, rejoint la partie avec le personnage du compte
de test, envoie un message joueur puis compte les messages reçus :
- `delta`      : le streaming token par token fonctionne ;
- `state_patches` : les patches d'état (PV, XP…) poussés en direct ;
- `dm`         : la narration finale (latence totale).
Chronomètre la latence perçue (1er delta) et la latence totale.
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://localhost:8000"
PARTIE = sys.argv[1] if len(sys.argv) > 1 else "29580aff"
TEXTE = (sys.argv[2] if len(sys.argv) > 2 else
         "Un gobelin surgit de l'ombre et je l'attaque immédiatement !")
TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 150.0


async def main() -> None:
    s = httpx.Client(base_url=BASE, timeout=10)
    r = s.post("/api/auth/connexion",
               json={"nom": "BetaTesteur", "mot_de_passe": "Test!2026beta"})
    joueur = r.json().get("utilisateur", "BetaTesteur")

    stats: dict[str, int] = {}
    statuts: list[str] = []
    premier_delta = None
    t0 = time.time()
    fin_dm = None
    texte_dm = ""
    fin_ok = False

    uri = f"ws://localhost:8000/ws/{PARTIE}"
    async with websockets.connect(uri, max_size=2 ** 22) as ws:
        # joined → join
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            stats[msg.get("type")] = stats.get(msg.get("type"), 0) + 1
            if msg.get("type") == "sys" and msg.get("event") == "joined":
                await ws.send(json.dumps({
                    "type": "join", "player": joueur,
                    "personnage": "Kaelen le Rouge",
                }))
                break
        # say → tour complet
        await ws.send(json.dumps({"type": "say", "player": joueur,
                                  "text": TEXTE}))
        while time.time() - t0 < TIMEOUT:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            except asyncio.TimeoutError:
                if fin_dm:
                    break
                continue
            t = msg.get("type")
            stats[t] = stats.get(t, 0) + 1
            if t == "status":
                desc = msg.get("description") or ""
                if desc and (not statuts or statuts[-1] != desc):
                    statuts.append(desc)
            if t == "delta":
                if premier_delta is None:
                    premier_delta = time.time() - t0
            elif t == "dm":
                fin_dm = time.time() - t0
                texte_dm = msg.get("text", "")
            elif t == "status" and msg.get("done"):
                fin_ok = True
                if fin_dm:
                    break
    print(f"latence 1er delta : {premier_delta:.1f}s" if premier_delta
          else "latence 1er delta : AUCUN DELTA (streaming inactif)")
    print(f"latence dm final  : {fin_dm:.1f}s" if fin_dm
          else "latence dm final  : TIMEOUT")
    print("types reçus :", json.dumps(stats, ensure_ascii=False))
    print("statuts reçus :")
    for st in statuts:
        print("  •", st)
    print("status done reçu :", fin_ok)
    print("--- extrait dm ---")
    print(texte_dm[-600:])


asyncio.run(main())
