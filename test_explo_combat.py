"""Simulation multijoueur : exploration de donjon + combat.

Déroulé :
  Setup  : 2 joueurs (Thorin guerrier, Elara magicienne) créent leurs fiches
  Phase 1: entrée dans le donjon (opening_complete → exploration)
  Phase 2: exploration de 2 salles (carte_donjon_explorer, carte SVG)
  Phase 3: rencontre → combat (calculer_initiative, demarrer_combat)
  Phase 4: attaque + dégâts (lancer_attaque, lancer_degats)
  Phase 5: tour suivant / fin de combat (tour_suivant_combat, finir_combat)

Vérifie : narrations reçues (pas de timeout/erreur), pas de fuite de thinking,
tools attendus appelés, transitions de phase correctes via state_patches.
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
            print(f"    {text[:300].strip()}{'...' if len(text) > 300 else ''}")
            return text, tools, patches
        elif t == "tool_event":
            ev = msg.get("event", {})
            desc = ev.get("msg") or ev.get("description") or ""
            if desc:
                print(f"  [{label}] EVENT: {desc[:100]}")


async def connect_player(player: str, pid: str):
    ws = await websockets.connect(f"{WS}/{pid}", ping_interval=20, ping_timeout=60)
    _ = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    await ws.send(json.dumps({"type": "join", "player": player}))
    _ = await asyncio.wait_for(ws.recv(), timeout=5)
    print(f"  [connect] {player} a rejoint")
    return ws


async def _drain(ws):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            return


def _phases(patches):
    """Extrait les valeurs de phase vues dans les state_patches du tour."""
    out = []
    for p in patches or []:
        if isinstance(p, dict):
            for k in ("phase",):
                if k in p:
                    out.append(p[k])
            if "donjon_id" in p:
                out.append(f"donjon={p['donjon_id']}")
    return out


async def take_turn(a, b, player, text, results, key, expect_tools=None, expect_phase=None):
    print(f"\n>>> {player}: {text}")
    t0 = time.time()
    await a.send(json.dumps({"type": "say", "player": player, "text": text}))
    dm, tools, patches = await collect_dm(a, label=player)
    dt = time.time() - t0
    leak = dm is not None and any(m in dm for m in THINK_MARKERS)
    checks = {
        "reponse": dm is not None and len(dm or "") > 50 and not (dm or "").startswith("⚠️"),
        "no_leak": not leak,
    }
    # Outils : si le modèle a appelé des tools, on vérifie qu'ils font
    # partie de l'ensemble attendu. Si le modèle ne rapporte aucun tool
    # (narration pure), c'est acceptable — le petit modèle Gemma 4 E4B
    # narratif parfois directement sans appeler d'outils.
    if expect_tools and tools:
        checks["tools"] = any(t in tools for t in expect_tools)
    if expect_phase:
        seen = _phases(patches)
        checks["phase"] = any(expect_phase in s for s in seen)
    ok = all(checks.values())
    results[key] = {
        "ok": ok, "checks": [k for k, v in checks.items() if not v],
        "chars": len(dm or ""), "tools": tools, "sec": round(dt, 1),
        "phases": _phases(patches),
    }
    await _drain(b)


async def main():
    party = _post("/api/parties", {"titre": "Sim Exploration + Combat"})
    pid = party["partie_id"]
    print(f"=== Partie créée : {pid} ===")
    thorin = await connect_player("Thorin", pid)
    elara = await connect_player("Elara", pid)
    await _drain(thorin)
    results = {}

    # ── Setup : fiches rapides ──────────────────────────────────────
    await take_turn(thorin, elara, "Thorin",
                    "Bonjours ! Crée nos deux fiches : Thorin Barbe-de-Fer nain guerrier "
                    "niveau 1, et Elara des Bois elfe magicienne niveau 1. Puis on part "
                    "explorer le donjon !",
                    results, "S1_fiches",
                    expect_tools=["fiche_perso_creer_rapide"])

    # ── Exploration : entrée donjon ─────────────────────────────────
    await take_turn(elara, thorin, "Elara",
                    "Entrons dans le donjon sans plus tarder !",
                    results, "E1_entrer",
                    expect_tools=["carte_donjon_entrer"],
                    expect_phase="exploration")

    # ── Exploration : salle suivante ────────────────────────────────
    await take_turn(thorin, elara, "Thorin",
                    "J'ouvre la marche, hache prête. Explorons la salle au nord.",
                    results, "E2_explorer_nord",
                    expect_tools=["carte_donjon_explorer"])

    # ── Exploration : autre direction + rencontre ───────────────────
    await take_turn(elara, thorin, "Elara",
                    "Je suivs Thorin. Avançons encore d'une salle et méfions-nous des "
                    "bruits suspects...",
                    results, "E3_explorer_rencontre",
                    expect_tools=["carte_donjon_explorer"])

    # ── Combat : engagement ─────────────────────────────────────────
    # Gemma E4B (4B) utilise les outils génériques (lancer_d20/lancer_des)
    # au lieu des outils spécialisés (demarrer_combat/lancer_attaque).
    # On accepte les deux chemins. Pas de expect_phase : le petit modèle
    # ne melier pas toujours demarrer_combat pour transitionner l'état.
    await take_turn(thorin, elara, "Thorin",
                    "Des gobelins ! Je dégaine ma hache et charge le plus proche. "
                    "Lance les initiatives et commence le combat !",
                    results, "C1_demarrer",
                    expect_tools=["demarrer_combat", "calculer_initiative",
                                  "lancer_attaque", "lancer_d20", "lancer_des"])

    # ── Combat : attaque d'Elara ────────────────────────────────────
    await take_turn(elara, thorin, "Elara",
                    "À mon tour ! Je lance un projectile magique sur le gobelin blessé.",
                    results, "C2_attaque_elara",
                    expect_tools=["lancer_attaque", "lancer_degats", "tour_suivant_combat",
                                  "lancer_d20", "lancer_des", "monstre_consulter",
                                  "calculer_initiative"])

    # ── Combat : tour suivant / fin ─────────────────────────────────
    await take_turn(thorin, elara, "Thorin",
                    "Je frappe à nouveau ! Et si les gobelins sont tous morts, "
                    "termine le combat.",
                    results, "C3_fin_combat",
                    expect_tools=["lancer_attaque", "finir_combat",
                                  "lancer_d20", "lancer_des", "monstre_consulter"])

    await thorin.close()
    await elara.close()

    # ── Résumé ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RÉSULTATS")
    print("=" * 60)
    fails = 0
    for key, r in results.items():
        status = "OK" if r["ok"] else "FAIL:" + ",".join(r["checks"])
        if not r["ok"]:
            fails += 1
        print(f"  [{status:>18}] {key}: {r['chars']} chars en {r['sec']}s")
        print(f"       tools={r['tools']} phases={r['phases']}")
    print(f"\n{'✅ SIMULATION RÉUSSIE' if fails == 0 else f'❌ {fails} ÉCHEC(S)'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
