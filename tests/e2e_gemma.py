import asyncio, json, time, sys, httpx, websockets

SERVER = "http://127.0.0.1:8000"
WS_URL  = "ws://127.0.0.1:8000"

async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{SERVER}/api/parties", json={"titre":"Test Reglage Gemma 2"})
        d = r.json()
        partie_id = d["partie_id"]
        print(f"Partie: {partie_id}")

    async with websockets.connect(f"{WS_URL}/ws/{partie_id}", max_size=2**22) as ws:
        await ws.send(json.dumps({"type":"join","player":"Alain"}))
        await asyncio.wait_for(ws.recv(), timeout=5)

        # Message qui donne TOUT (prénom + race + classe) → Gemma doit enchaîner PATCH x3 + lancer_caracteristiques
        msg = "Bonjour. Je m'appelle Alain et je veux jouer un nain guerrier nommé Groth. Lance mes caractéristiques."
        t0 = time.time()
        print(f"Envoi: {msg}")
        print(f"Attente réponse Gemma 12B (jusqu'à 4 min)...")
        await ws.send(json.dumps({"type":"say","player":"Alain","text":msg}))

        narration = None
        deadline = time.time() + 240
        last_log = t0
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except asyncio.TimeoutError:
                elapsed = time.time() - t0
                if elapsed - last_log > 30:
                    print(f"  ... encore en cours ({elapsed:.0f}s)")
                    last_log = elapsed
                continue
            m = json.loads(raw)
            t = m.get("type")
            if t == "status":
                if m.get("description"):
                    print(f"  [status] {m['description']}")
            elif t == "tool_event":
                print(f"  [tool_event] {json.dumps(m.get('event', m))[:160]}")
            elif t == "dm":
                narration = m
                break

        if not narration:
            print("❌ Pas de réponse MJ dans le délai.")
            sys.exit(2)

        trace = narration.get('tool_calls_trace') or []
        print(f"\nDurée: {time.time()-t0:.1f}s")
        print(f"iterations        : {narration.get('iterations')}")
        print(f"corrections       : {narration.get('corrections')}")
        print(f"simulation tentée : {narration.get('simulation_attempted')}")
        print(f"tool_calls total  : {len(trace)}")
        print("\n=== TRACE DES APPELS D'OUTILS ===")
        for i, c in enumerate(trace):
            args = json.dumps(c.get('args', {}), ensure_ascii=False)
            print(f"  {i+1}. {c['name']}({args[:120]}) → ok={c.get('ok')}")
            txt = (c.get('text') or '').replace('\n', ' ')[:120]
            print(f"     ⟹ {txt}")

        names_called = [c['name'] for c in trace]
        called_patch = 'etat_partie_patch' in names_called
        called_dice = 'lancer_caracteristiques' in names_called

        print("\n=== VERDICT ===")
        print(f"A appelé etat_partie_patch     : {'✅ OUI' if called_patch else '❌ NON'}")
        print(f"A appelé lancer_caracteristiques: {'✅ OUI' if called_dice else '❌ NON (dés inventés!)'}")

        print("\n=== Narration finale (800 char) ===")
        print((narration.get('text',''))[:800])

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{SERVER}/api/parties/{partie_id}")
            etat = r.json().get('etat', {})
            pjs = etat.get('pj', [])
            phase = etat.get('phase')
            print(f"\n=== État persistant ===")
            print(f"phase: {phase}")
            print(f"PJ créés: {len(pjs) if isinstance(pjs, list) else 'DICT (BUG!)'}")
            if isinstance(pjs, list):
                for p in pjs:
                    if isinstance(p, dict):
                        print(f"  - {p.get('nom')} ({p.get('race')} {p.get('classe')})")
                        if p.get('carac'):
                            print(f"    carac: {p.get('carac')}")
                    else:
                        print(f"  - (entrée string/brute: {p!r})")
            elif isinstance(pjs, dict):
                print(f"  pj est un dict (BUG): {json.dumps(pjs, ensure_ascii=False)[:200]}")

asyncio.run(main())
