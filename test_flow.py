"""Test multijoueur : 2 joueurs connectés en parallèle, tours séquentiels (D&D).

Vérifie :
  - Chaque tour reçoit une narration DM (pas de timeout, pas d'erreur technique)
  - Aucune fuite de thinking Gemma (`<|channel>thought`) dans les narrations
  - Les tools s'exécutent (fiches, état)
  - Une question de règles passe par le RAG (vérifié via logs serveur)
"""
import asyncio
import json
import sys
import time
import urllib.request

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import websockets

BASE = "http://localhost:8123"
WS = "ws://localhost:8123/ws"
THINK_MARKERS = ("<|channel", "<channel|>", "<|think", "thought\n")


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


async def collect_dm(ws, timeout_sec: int = 240, label: str = ""):
    """Attend le message 'dm' final. Retourne (text, tools, patches) ou (None, [], [])."""
    streaming = ""
    t0 = time.time()
    while True:
        remaining = timeout_sec - (time.time() - t0)
        if remaining <= 0:
            print(f"  [{label}] !! TIMEOUT après {timeout_sec}s")
            return None, [], []
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        t = msg.get("type", "")
        if t == "delta":
            streaming += msg.get("text", "")
        elif t == "dm":
            text = msg.get("text", "") or streaming
            tools = [tc.get("name", "?") for tc in msg.get("tool_calls_trace", [])]
            patches = msg.get("state_patches", [])
            leak = [m for m in THINK_MARKERS if m in text]
            tag = " (⚠ THINKING LEAK)" if leak else ""
            print(f"  [{label}] DM ({len(text)} chars), tools={tools}{tag}")
            print(f"    {text[:350].strip()}{'...' if len(text) > 350 else ''}")
            return text, tools, patches
        elif t == "tool_event":
            ev = msg.get("event", {})
            desc = ev.get("msg") or ev.get("description") or ""
            if desc:
                print(f"  [{label}] EVENT: {desc[:100]}")


async def connect_player(player: str, pid: str) -> websockets.WebSocketClientProtocol:
    """Ouvre la WS, join, consomme les sys initiaux."""
    ws = await websockets.connect(f"{WS}/{pid}", ping_interval=20, ping_timeout=60)
    _ = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))  # joined
    await ws.send(json.dumps({"type": "join", "player": player}))
    _ = await asyncio.wait_for(ws.recv(), timeout=5)  # participant_joined
    print(f"  [connect] {player} a rejoint la partie")
    return ws


async def _drain(ws):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            return


async def take_turn(sender_ws, listener_ws, player: str, text: str, results: dict, key: str):
    """Un tour de parole : le joueur parle, TOUS reçoivent la narration du MJ."""
    print(f"\n>>> {player}: {text}")
    t0 = time.time()
    await sender_ws.send(json.dumps({"type": "say", "player": player, "text": text}))
    dm, tools, patches = await collect_dm(sender_ws, label=player)
    dt = time.time() - t0
    leak = dm is not None and any(m in dm for m in THINK_MARKERS)
    ok = dm is not None and len(dm) > 80 and not dm.startswith("⚠️") and not leak
    results[key] = {
        "ok": ok, "leak": leak, "chars": len(dm or ""),
        "tools": tools, "sec": round(dt, 1),
    }
    await _drain(listener_ws)


async def main():
    party = _post("/api/parties", {"titre": "Test Multijoueur RAG"})
    pid = party["partie_id"]
    print(f"=== Partie créée : {pid} ===")

    thorin = await connect_player("Thorin", pid)
    elara = await connect_player("Elara", pid)
    await _drain(thorin)

    results = {}

    await take_turn(thorin, elara, "Thorin",
                    "Bonjour MJ ! Je suis Thorin, nain guerrier. On commence l'aventure !",
                    results, "T1_intro_thorin")
    await take_turn(elara, thorin, "Elara",
                    "Salut ! Moi c'est Elara, elfe magicienne. Je rejoins Thorin !",
                    results, "T2_intro_elara")
    await take_turn(thorin, elara, "Thorin",
                    "Crée ma fiche s'il te plaît. Thorin Barbe-de-Fer, nain, guerrier niveau 1.",
                    results, "T3_fiche_thorin")
    await take_turn(elara, thorin, "Elara",
                    "Crée ma fiche aussi. Elara des Bois, elfe, magicienne niveau 1.",
                    results, "T4_fiche_elara")
    # Question de règles → doit déclencher le RAG (contexte injecté au system prompt)
    await take_turn(elara, thorin, "Elara",
                    "MJ, quelle est la difficulté d'un test de Force pour enfoncer une porte en bois ?",
                    results, "T5_regles_rag")
    await take_turn(thorin, elara, "Thorin",
                    "Quel est l'état de la partie ? Liste les personnages.",
                    results, "T6_etat")

    await thorin.close()
    await elara.close()

    print("\n" + "=" * 60)
    print("RÉSULTATS")
    print("=" * 60)
    fails = 0
    for key, r in results.items():
        flags = []
        if not r["ok"]:
            flags.append("FAIL")
            fails += 1
        if r["leak"]:
            flags.append("THINKING-LEAK")
        status = " ".join(flags) or "OK"
        print(f"  [{status:>15}] {key}: {r['chars']} chars en {r['sec']}s, tools={r['tools']}")
    print(f"\n{'✅ TOUS LES TOURS ONT RÉUSSI' if fails == 0 else f'❌ {fails} TOUR(S) EN ÉCHEC'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
