"""E2E ciblé : invoquation de monstre en cours de combat (LLM réel).

Setup déterministe (état préparé sur disque, sans LLM) :
  - PJ Mireille (magicienne 1) en combat contre un Gobelin ;
  - tour courant = Mireille (elle peut parler) ;
  - historique vierge.

Tour testé : « J'invoque un loup pour qu'il combatte à mes côtés ! »
  → le MJ DOIT appeler combat_ajouter_combattant (pas engager_combat,
     qui réinitialiserait le combat) et l'initiative doit contenir le Loup.

Usage : py tests/e2e_invoque.py  (conteneur dnd35 démarré sur :8123)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request

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


async def collect_dm(ws, timeout_sec: int = 240):
    while True:
        remaining = timeout_sec - 0
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        t = msg.get("type", "")
        if t == "dm":
            return msg


async def main() -> None:
    party = _post("/api/parties", {"titre": "E2E Invoque"})
    pid = party["partie_id"]
    print(f"=== Partie créée : {pid} ===")

    # --- Setup déterministe de l'état (combat en cours, tour de Mireille) --- #
    # NB : PartyState écrit à la RACINE de data_dir (partie_<id>.json),
    # pas dans le sous-dossier parties/.
    spath = os.path.join(DATA, f"partie_{pid}.json")
    from datetime import datetime as _dt
    now = _dt.now().isoformat()
    etat = {
        "meta": {"titre": "E2E Invoque", "cadre": "Côte des Épées (Faerûn)",
                 "regles": "D&D 3.5", "date_creation": now, "date_maj": now},
        "phase": "combat",
        "tour": 2,
        "courant_tour_pour": "Mireille",
        "initiative": [
            {"nom": "Mireille", "init": 15, "jet_brut": 14, "mod": 1},
            {"nom": "Gobelin", "init": 10, "jet_brut": 9, "mod": 1},
        ],
        "pj": [{
            "nom": "Mireille", "joueur": "mireille", "race": "Humaine",
            "classe": "Magicienne", "niveau": 1, "pv": 6, "pv_max": 6,
            "ca": 12,
            "carac": {"FOR": 9, "DEX": 14, "CON": 12, "INT": 16, "SAG": 11, "CHA": 13},
            "alignement": "Neutre Bon",
        }],
        "pnj": [],
        "lieu": {"nom": "Crypte", "type": "donjon", "description": "",
                 "position_x": 0, "position_y": 0},
        "donjon": {"id": None, "salles_visitees": [], "portes_bloquees": [],
                   "grille": []},
        "donjons_exploreres": {},
        "quete": {"titre": "", "pitch": "", "source": ""},
        "histoire": [],
        "derniere_narration": "",
        "monstres_combat": [{
            "nom": "Gobelin", "pv": 5, "pv_max": 5, "ca": 15,
            "fp": "1/3", "conditions": [],
        }],
    }
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)
    # Fiche JSON complète (pour les tools de fiches).
    fpath = os.path.join(DATA, "fiches", "fiche_mireille.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "nom": "Mireille", "joueur": "mireille", "race": "Humaine",
            "classe": "Magicienne", "niveau": 1,
            "carac": {"FOR": 9, "DEX": 14, "CON": 12, "INT": 16, "SAG": 11, "CHA": 13},
            "pv": 6, "pv_max": 6, "ca": 12,
            "sauvegardes": {"Vigueur": 1, "Reflexes": 3, "Volonte": 3},
            "bab": 0, "competences": {}, "dons": [], "equipement": [],
            "or": 0, "alignement": "Neutre Bon", "histoire": "",
            "conditions": [],
        }, f, ensure_ascii=False)
    print("État préparé : combat tour 2, c'est le tour de Mireille.")

    # --- Tour de jeu : invoquation ---------------------------------------- #
    ws = await websockets.connect(f"{WS}/{pid}", ping_interval=20, ping_timeout=60)
    # Partie ouverte : le serveur envoie `joined` immédiatement à la
    # connexion. Pas de join (l'état pj est déjà préparé sur disque, et le
    # rattachement de fiche réécrirait l'entrée pj).
    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert m.get("event") == "joined", f"attendu joined, reçu : {m}"

    print(">>> mireille: J'invoque un loup avec mon sort d'invocation "
          "d'allié naturel pour qu'il combatte à mes côtés contre le gobelin !")
    t0 = time.time()
    await ws.send(json.dumps({
        "type": "say", "player": "mireille",
        "text": "J'invoque un loup avec mon sort d'invocation d'allié naturel "
                "pour qu'il combatte à mes côtés contre le gobelin !",
    }))
    dm = await collect_dm(ws)
    dt = time.time() - t0

    tools = [tc.get("name", "?") for tc in dm.get("tool_calls_trace", [])]
    patches = dm.get("state_patches", [])
    text = dm.get("text", "")
    print(f"  DM ({len(text)} chars en {dt:.0f}s), tools={tools}")
    print(f"    {text[:400].strip()}{'...' if len(text) > 400 else ''}")

    fails = 0
    def check(label, cond):
        nonlocal fails
        print(("✅ " if cond else "❌ ") + label)
        if not cond:
            fails += 1

    check("combat_ajouter_combattant appelé par le MJ",
          "combat_ajouter_combattant" in tools)
    check("engager_combat PAS rappelé (pas de reset)",
          "engager_combat" not in tools)

    # Vérifie l'état final sur disque.
    with open(spath, encoding="utf-8") as f:
        etat_final = json.load(f)
    noms = [e["nom"] for e in etat_final.get("initiative") or []]
    check("Loup présent dans l'initiative persistée", "Loup" in noms)
    check("Gobelin toujours dans l'initiative", "Gobelin" in noms)
    check("Mireille toujours dans l'initiative", "Mireille" in noms)
    check("combat toujours en cours (phase combat)",
          etat_final.get("phase") == "combat")
    loup = next((m for m in etat_final.get("monstres_combat") or []
                 if "Loup" in m.get("nom", "")), None)
    check("Loup suivi dans monstres_combat", loup is not None)
    check("round/tour NON réinitialisé (tour >= 2)",
          int(etat_final.get("tour") or 0) >= 2)

    await ws.close()

    # --- Nettoyage ---------------------------------------------------------- #
    for p in (spath, fpath, os.path.join(DATA, f"chat_{pid}.json")):
        if os.path.isfile(p):
            os.unlink(p)

    print(f"\n{'✅ E2E INVOQUE RÉUSSI' if fails == 0 else f'❌ {fails} ÉCHEC(S)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
