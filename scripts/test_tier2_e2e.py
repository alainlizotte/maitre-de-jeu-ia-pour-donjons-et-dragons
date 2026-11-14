# -*- coding: utf-8 -*-
"""E2E réel : test des nouveaux tools (taverne, monture, poison, veille,
éclairage, identification, combat monté) dans une VRAIE partie LLM.

Usage : py scripts/test_tier2_e2e.py
Prérequis : le serveur dnd35 tourne (localhost:8123) + le LLM chargé.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8123"
WS_BASE = "ws://127.0.0.1:8123"
TURN_TIMEOUT = 150.0
FICHE_TRANSFERT = r"C:\Users\alain\AppData\Local\Temp\scenario_runs\tier2_state.json"


class Client:
    def __init__(self, name):
        self.name = name
        self.ws = None
        self.inbox = asyncio.Queue()

    async def connect(self, party_id):
        self.ws = await websockets.connect(
            f"{WS_BASE}/ws/{party_id}", max_size=2 ** 23, ping_interval=20)
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
        await self.ws.send(json.dumps({
            "type": "say", "player": self.name, "text": text}))


class Journal:
    def __init__(self):
        self.tours: list[dict] = []

    def record(self, label, speaker, texte, dm):
        outils = [tc.get("name") for tc in (dm or {}).get(
            "tool_calls_trace", [])]
        self.tours.append({
            "label": label, "speaker": speaker, "input": texte,
            "mj": (dm or {}).get("text", "")[:400], "outils": outils,
        })
        print(f"   → outils: {outils}", flush=True)


JOURNAL = Journal()


async def tour(clients, speaker, texte, label, attendu=None):
    """Un tour : say → attendre le dm final. Journalise les tools."""
    watcher = clients["Alain"]
    watcher.drain()
    t0 = time.time()
    await clients[speaker].say(texte)
    print(f"\n{'=' * 68}\n[{label}] {speaker} : {texte[:160]}\n{'=' * 68}",
          flush=True)
    deadline = time.time() + TURN_TIMEOUT
    dm = None
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(
                watcher.inbox.get(), timeout=max(1, deadline - time.time()))
        except asyncio.TimeoutError:
            break
        if msg.get("type") == "dm":
            dm = msg
            break
        if msg.get("type") == "sys" and msg.get("event") == "error":
            print(f"!! erreur serveur : {msg}", flush=True)
            break
    duree = time.time() - t0
    texte_out = (dm or {}).get("text", "")
    outils = [tc.get("name") for tc in (dm or {}).get(
        "tool_calls_trace", [])]
    print(f"--- MJ ({duree:.0f}s) ---", flush=True)
    print(texte_out[:400], flush=True)
    print(f"   → outils: {outils}", flush=True)
    JOURNAL.record(label, speaker, texte, dm)
    if attendu:
        trouves = [a for a in attendu if a in outils]
        ok = bool(trouves)
        JOURNAL.tours[-1]["attendu"] = attendu
        JOURNAL.tours[-1]["ok"] = ok
        print(f"   {'✅' if ok else '⚠️'} attendu {attendu} → "
              f"{'trouvé ' + str(trouves) if ok else 'ABSENT'}", flush=True)
    return dm


async def etat(pid):
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.get(f"{BASE}/api/parties/{pid}")
        return r.json().get("etat") or {}


async def main():
    bilan = {"ok": 0, "ko": 0, "tours": []}
    # Authentification (sécurité multi-comptes) : compte E2E dédié.
    compte, mdp = "e2e_tier2", "tier2-2026!"
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(f"{BASE}/api/auth/inscription", json={
            "nom": compte, "mot_de_passe": mdp})
        if "token" not in r.json():
            # compte déjà pris → connexion
            r = await h.post(f"{BASE}/api/auth/connexion", json={
                "nom": compte, "mot_de_passe": mdp})
        token = r.json()["token"]
        r = await h.post(f"{BASE}/api/parties", json={
            "titre": "E2E Tier 2 — saveur et profondeur",
            "cadre": "Côte des Épées (Faerûn)",
        }, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        pid = r.json()["partie_id"]
    print(f"Partie créée : {pid}", flush=True)
    # Les fiches sont GLOBALES (data/fiches/) : un nom déjà joué
    # refuse la création -> l'état n'a jamais de PJ -> la phase est
    # forcée à "opening" et les tools de jeu restent invisibles.
    nom_pj = f"Throk{pid[:4]}"
    print(f"Nom du PJ : {nom_pj}", flush=True)

    alain = Client("Alain")
    await alain.connect(pid)
    clients = {"Alain": alain}

    # ── Création du personnage (TOUS les détails : âge, taille,
    #    apparence, sexe — sinon le MJ demande et n'appelle jamais
    #    fiche_perso_creer_rapide) ──
    await tour(clients, "Alain",
               f"Bonjour ! Je suis Alain et je joue {nom_pj}, un guerrier "
               "humain de 34 ans, 1,88 m, musclé et râblé. Sexe masculin. "
               "Crâne rasé, barbe noire courte, un sourcil fendu par une "
               "vieille cicatrice, mains calleuses de convoyeur. Il porte "
               "une cotte de cuir usée et une hache sur le dos. Crée sa "
               "fiche complète maintenant avec ces informations.",
               "création PJ")

    # ── Une quête (matière à rumeurs) ──
    await tour(clients, "Alain",
               "Le prêtre Cassyt nous confie une mission : purifier le "
               "sanctuaire profane des catacombes au nord de Phlan, où un "
               "nécromancien nommé Vargan élève des morts. Nous partons "
               "demain à l'aube.", "quête (matière à rumeurs)")

    # 💰 (fixture) : l'avance de Cassyt — la création ne donne AUCUN or,
    # et sans argent les achats échouent (le 9B improvise alors). On
    # crédite la fiche à la main : 500 po = 5 000 pc.
    try:
        fiche_p = os.path.join(
            r"F:\projets\d&d app\server\data\fiches",
            f"fiche_{nom_pj.lower()}.json")
        with open(fiche_p, "r", encoding="utf-8") as f:
            fiche_d = json.load(f)
        fiche_d["or"] = 5000
        with open(fiche_p, "w", encoding="utf-8") as f:
            json.dump(fiche_d, f, ensure_ascii=False)
        print(f"💰 Avance de Cassyt : 500 po créditées à {nom_pj}", flush=True)
    except Exception as e_or:
        print(f"⚠️ Crédit d'or impossible : {e_or}", flush=True)

    # ── 1. 🍺 Rumeurs de taverne ──
    await tour(clients, "Alain",
               "Ce soir, nous poussons la porte de la taverne La Chope "
               "Refendue. Nous payons des tournées et écoutons les "
               "rumeurs.", "🍺 rumeurs",
               attendu=["rumeurs_taverne"])

    # ── 2. 🐴 Acheter la monture ──
    await tour(clients, "Alain",
               "Le lendemain matin, nous allons à l'écurie : j'achète un "
               "mulet et une lance de voyage.", "🐴 achat monture")

    # ── 3. 🧪 Poison (araignée) ──
    await tour(clients, "Alain",
               "En vidant une caisse de la remise, une araignée des "
               "cryptes saute sur ma main et me mord : son venin est "
               "connu pour être virulent (sauvegarde DD 13, 1d2 points "
               "de Constitution).", "🧪 poison",
               attendu=["appliquer_poison"])

    # ── 5. 🔍 Identification ──
    await tour(clients, "Alain",
               "Sur le bandit, une dague gravée de runes luisantes "
               "m'intrigue : je l'ajoute à mon inventaire puis je passe "
               "une heure à l'étudier, essai d'Art de la magie.",
               "🔍 identification",
               attendu=["identifier_objet"])

    # ── 6. 🌙 Repos + veille ──
    await tour(clients, "Alain",
               "La nuit tombe : nous campons au bord de la route. Je "
               "monte la garde pendant que Throk dort.", "🌙 repos + veille")

    # ── 6. 🕯️ Entrer dans le donjon (éclairage) ──
    await tour(clients, "Alain",
               "À l'aube, nous atteignons l'entrée des catacombes et nous "
               "descendons dans le sanctuaire profane.", "🕯️ entrée donjon",
               attendu=["carte_donjon_entrer"])

    # ── 7. ⚔️ CHARGE MONTÉE sur le premier monstre ──
    await tour(clients, "Alain",
               "Je remonte sur mon mulet et je CHARGE à pleine vitesse le "
               "premier ennemi qui apparaît, lance au poing, au galop !",
               "⚔️ charge montée au donjon",
               attendu=["attaque_montee"])

    # ── Bilan ──
    print(f"\n{'#' * 68}\nBILAN E2E TIER 2 — partie {pid}", flush=True)
    for t in JOURNAL.tours:
        if "attendu" in t:
            etoile = "✅" if t.get("ok") else "⚠️"
            bilan["ok" if t.get("ok") else "ko"] += 1
            print(f"  {etoile} {t['label']}: attendu {t['attendu']} "
                  f"→ {t['outils']}", flush=True)
    print(f"  ok={bilan['ok']} ko={bilan['ko']}", flush=True)

    st = await etat(pid)
    print(f"\nÉtat final : phase={st.get('phase')} "
          f"PJ={[(p.get('nom'), p.get('pv'), p.get('conditions'))
                 for p in st.get('pj', [])]}", flush=True)
    try:
        import io as _io
        _io.open(r"C:\Users\alain\AppData\Local\Temp\scenario_runs\pid_tier2.txt",
                 "w", encoding="utf-8").write(pid)
    except Exception:
        pass
    with open(FICHE_TRANSFERT, "w", encoding="utf-8") as f:
        json.dump({"party_id": pid, "tours": JOURNAL.tours,
                   "etat_final": {k: st.get(k) for k in (
                       "phase", "pj", "lieu")}}, f,
                  ensure_ascii=False, indent=1)
    print(f"Transcript : {FICHE_TRANSFERT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
