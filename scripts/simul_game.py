# -*- coding: utf-8 -*-
"""Simulation multiplayer réaliste — 3 joueurs (Alice, Bob, Carol) via WebSocket.

Usage: python simul_game.py <phase>
Phases: setup | quest | explore | combat | finale
État persisté dans /tmp/sim_state.json, transcript dans /tmp/simul_transcript.json
"""
import asyncio, json, sys, time, os
import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000"
STATE_FILE = "/tmp/sim_state.json"
TRANSCRIPT_FILE = "/tmp/simul_transcript.json"
TURN_TIMEOUT = 300  # LLM local lent — 5 min max par tour MJ

PLAYERS = ["Alice", "Bob", "Carol"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"party_id": None, "transcript": []}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def append_transcript(st, entry):
    st["transcript"].append(entry)
    save_state(st)


class Client:
    def __init__(self, name):
        self.name = name
        self.ws = None
        self.inbox = asyncio.Queue()

    async def connect(self, party_id):
        self.ws = await websockets.connect(
            f"{WS_BASE}/ws/{party_id}", max_size=2 ** 23, ping_interval=20
        )
        await self.ws.send(json.dumps({"type": "join", "player": self.name}))
        asyncio.create_task(self._reader())

    async def _reader(self):
        try:
            async for msg in self.ws:
                await self.inbox.put(json.loads(msg))
        except Exception:
            pass

    def drain(self):
        while not self.inbox.empty():
            self.inbox.get_nowait()

    async def say(self, text):
        await self.ws.send(json.dumps({"type": "say", "player": self.name, "text": text}))

    async def team_say(self, text):
        await self.ws.send(json.dumps({"type": "team_say", "player": self.name, "text": text}))


async def connect_all(st):
    clients = {n: Client(n) for n in PLAYERS}
    for c in clients.values():
        await c.connect(st["party_id"])
        await asyncio.sleep(0.3)
    # Attendre les confirmations join
    await asyncio.sleep(2)
    for c in clients.values():
        c.drain()
    return clients


async def mj_turn(st, clients, speaker, text, label=""):
    """Un tour MJ complet : say → status → dm. Retourne le message dm."""
    watcher = clients["Alice"]
    watcher.drain()
    t0 = time.time()
    await clients[speaker].say(text)
    print(f"\n{'='*70}\n[{label or 'tour'}] {speaker}: {text}\n{'='*70}", flush=True)
    deadline = time.time() + TURN_TIMEOUT
    dm = None
    while time.time() < deadline:
        try:
            remaining = deadline - time.time()
            msg = await asyncio.wait_for(watcher.inbox.get(), timeout=max(1, remaining))
        except asyncio.TimeoutError:
            break
        mtype = msg.get("type")
        if mtype == "dm":
            dm = msg
            break
        if mtype == "sys" and msg.get("event") == "error":
            print(f"!! ERREUR SERVEUR: {msg}", flush=True)
            raise RuntimeError(f"Erreur serveur: {msg}")
    if dm is None:
        raise TimeoutError(f"Pas de réponse dm après {TURN_TIMEOUT}s — tour: {label}")
    dur = time.time() - t0
    text_out = dm.get("text", "")
    trace = dm.get("tool_calls_trace", [])
    patches = dm.get("state_patches", [])
    print(f"--- MJ ({dur:.0f}s, {dm.get('iterations', '?')} itérations, "
          f"corrections={dm.get('corrections', 0)}) ---", flush=True)
    print(text_out[:600], flush=True)
    for tc in trace:
        args = json.dumps(tc.get("arguments", {}), ensure_ascii=False)[:200]
        res = str(tc.get("result", ""))[:300].replace("\n", " ")
        print(f"  [tool] {tc.get('name')}({args})\n         → {res}", flush=True)
    for p in patches:
        print(f"  [patch] {json.dumps(p, ensure_ascii=False)[:250]}", flush=True)
    append_transcript(st, {
        "label": label, "player": speaker, "input": text, "duration_s": round(dur),
        "dm_text": text_out, "tool_calls_trace": trace, "state_patches": patches,
        "iterations": dm.get("iterations"), "corrections": dm.get("corrections"),
    })
    return dm


async def phase_setup():
    st = load_state()
    if st.get("party_id"):
        print(f"Partie existante: {st['party_id']} — nouvelle partie demandée.", flush=True)
    # Créer la partie
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(f"{BASE}/api/parties", json={
            "titre": "Session Simulation Multi",
            "cadre": "Côte des Épées (Faerûn)",
        })
        r.raise_for_status()
        st["party_id"] = r.json()["partie_id"]
        st["transcript"] = []
        save_state(st)
    pid = st["party_id"]
    print(f"Partie créée: {pid}", flush=True)

    clients = await connect_all(st)
    # Team chat humain — petit bavardage
    await clients["Bob"].team_say("Salut à tous ! Prêts pour l'aventure ?")
    await clients["Carol"].team_say("Toujours prête ! J'espère qu'il y aura des trésors...")
    await asyncio.sleep(1)

    # Création des personnages, un joueur à la fois, comme des humains
    await mj_turn(st, clients, "Alice",
        "Bonjour Maître du Jeu ! Je suis Alice et je voudrais jouer Aline, "
        "une guerrière humaine de 28 ans, vétéran de la garde de Memnon. "
        "Cheveux noirs en tresse, armure usée, une cicatrice sur la joue gauche.",
        "création Aline")

    await mj_turn(st, clients, "Bob",
        "Bonsoir ! Moi c'est Bob, je joue Elrindel, un magicien elfe de 120 ans, "
        "fin et curieux, robe bleu nuit ornée d'étoiles argentées, "
        "il vient d'Eauprofonde pour étudier les ruines de la côte.",
        "création Elrindel")

    await mj_turn(st, clients, "Carol",
        "Hello ! Carol à la rapport. Ma perso c'est Pippa Rousseline, une "
        "halfeline roublarde de 34 ans, toujours souriante, avec ses tresses "
        "et ses innombrables poches remplies d'objets 'empruntés'. Elle "
        "cherche l'aventure pour la gloire... et l'argent surtout !",
        "création Pippa")

    # Vérification des fiches côté REST
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.get(f"{BASE}/api/parties/{pid}")
        etat = r.json()["etat"]
        print(f"\nPhase: {etat.get('phase')} | PJ: {len(etat.get('pj', []))}", flush=True)
        for pj in etat.get("pj", []):
            print(f"  - {pj.get('nom')} ({pj.get('race')}, {pj.get('classe')}) "
                  f"joueur={pj.get('joueur')}", flush=True)
    print("\n=== PHASE SETUP TERMINÉE ===", flush=True)


async def phase_quest():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Nous sommes trois aventuriers prêts à partir. Quelles missions "
        "pourrais-tu nous proposer, Maître du Jeu ? Nous débordons d'audace "
        "mais nous sommes encore novices.",
        "demande scénarios")

    await mj_turn(st, clients, "Bob",
        "Le scénario d'initiation pour débutants me semble parfait pour "
        "commencer. On choisit celui-là ! Raconte-nous le début de l'histoire.",
        "choix scénario")


async def phase_explore():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Carol",
        "Écoutez bien aux portes... Je fais le tour du mur d'enceinte pour "
        "repérer les entrées et d'éventuels gardes. Je reste discrète dans "
        "les ombres.",
        "approche prudente")

    await mj_turn(st, clients, "Alice",
        "Bien vu Pippa. Nous passons par l'entrée principale, armes dégaines. "
        "Aline ouvre la marche en bouclier levé. On entre !",
        "entrer donjon")

    await mj_turn(st, clients, "Bob",
        "Je lance un regard prudent autour de nous. Y a-t-il des inscriptions, "
        "des pièges ou des passages secrets visibles dans cette première salle ?",
        "examen salle 1")


async def phase_combat():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Carol",
        "Je m'approche du manuscrit ancien sur la table et le lis à voix "
        "basse. Que raconte-t-il ? Y a-t-il un danger qui sommeille sous ce "
        "monastère ?",
        "lecture manuscrit")

    await mj_turn(st, clients, "Alice",
        "Assez tergiverser. Nous descendons dans les sous-sols du monastère "
        "pour en découdre avec ce qui menace la vallée. Aline ouvre la marche "
        "en tête, bouclier levé, épée dégainer. On y va !",
        "descente sous-sols")

    await mj_turn(st, clients, "Bob",
        "Je prépare mon sort de projectile magique et je reste derrière "
        "Aline. Si une créature apparaît, je lance un test pour détecter sa "
        "présence avant qu'elle ne nous tombe dessus.",
        "avance prudente")

    await mj_turn(st, clients, "Alice",
        "Au combat ! Aline brandit son épée longue et charge la créature ! "
        "Attaque !",
        "attaque Aline")

    await mj_turn(st, clients, "Bob",
        "Elrindel recule d'un pas et lance un projectile magique sur la "
        "créature ! Je l'attaque avec mon sort.",
        "attaque Elrindel")


async def phase_combat2():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Aline attaque de nouveau la Créature de la Brume avec son épée "
        "longue ! Frappe-la !",
        "attaque Aline 2")

    await mj_turn(st, clients, "Carol",
        "Pippa profite de la distraction pour frapper la créature dans le "
        "dos avec sa dague ! Attaque sournoise !",
        "attaque Pippa")


async def phase_combat3():
    """Cycle combat complet POST-fix : reset le transcript pour une analyse propre."""
    st = load_state()
    st["transcript"] = []
    save_state(st)
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Le danger n'est pas passé, je le sens. Nous reprenons l'exploration "
        "des sous-sols : nous suivons le passage Est derrière l'autel brisé, "
        "armes prêtes.",
        "exploration passage Est")

    await mj_turn(st, clients, "Bob",
        "Si une créature surgit, j'attaque immédiatement avec un projectile "
        "magique !",
        "rencontre 2")

    await mj_turn(st, clients, "Carol",
        "Pippa se met en position et frappe la créature avec sa dague dès "
        "qu'elle est à portée !",
        "attaque Pippa 2")

    await mj_turn(st, clients, "Alice",
        "Aline enchaîne : elle porte un grand coup d'épée longue sur la "
        "créature !",
        "attaque Aline 3")


async def phase_combat4():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Nous forçons cette porte runique tous ensemble ! Aline donne un "
        "violent coup d'épaule, Pippa cherche le mécanisme, Elrindel examine "
        "les runes. On ouvre cette porte, coûte que coûte !",
        "forçage porte")

    await mj_turn(st, clients, "Carol",
        "Attention ! Une créature surgit de l'ouverture ! Je l'attaque "
        "immédiatement avec ma dague avant qu'elle ne réagisse !",
        "embuscade — attaque Pippa")

    await mj_turn(st, clients, "Alice",
        "Aline bondit devant ses compagnons et frappe la créature de toutes "
        "ses forces avec son épée longue ! Frappe !",
        "embuscade — attaque Aline")


async def phase_combat5():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Aline s'élance sur le Gardien de la Porte et lui porte un grand "
        "coup d'épée longue ! Frappe maintenant !",
        "attaque Aline vs Gardien")


async def phase_combat6():
    st = load_state()
    st["transcript"] = []
    save_state(st)
    clients = await connect_all(st)

    await mj_turn(st, clients, "Alice",
        "Aline attaque de nouveau le Gardien de la Porte à l'épée longue ! "
        "Frappe avec toutes tes forces !",
        "attaque Aline vs Gardien 2")

    await mj_turn(st, clients, "Carol",
        "Pippa contourne la créature par la gauche et frappe ses runes "
        "faibles avec sa dague !",
        "attaque Pippa vs Gardien")

    await mj_turn(st, clients, "Bob",
        "Elrindel canalyse la structure : il lance un projectile magique "
        "entre les plaques de pierre du Gardien !",
        "attaque Elrindel vs Gardien")


async def phase_finale():
    st = load_state()
    clients = await connect_all(st)

    await mj_turn(st, clients, "Carol",
        "Vite, fouillons le corps et la salle pendant que le chemin est libre. "
        "Je cherche les trésors et les objets de valeur.",
        "fouille")

    await mj_turn(st, clients, "Alice",
        "Nous ressortons victorieux. Retournons au village faire notre rapport "
        "et nous reposer. Bravo à tous !",
        "sortie + bilan")


async def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = {
        "setup": phase_setup, "quest": phase_quest, "explore": phase_explore,
        "combat": phase_combat, "combat2": phase_combat2, "combat3": phase_combat3,
        "combat4": phase_combat4, "combat5": phase_combat5, "combat6": phase_combat6,
        "finale": phase_finale,
    }[phase]
    await fn()


if __name__ == "__main__":
    asyncio.run(main())
