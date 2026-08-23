# -*- coding: utf-8 -*-
"""Simulation multiplayer réaliste — création de persos VIA LE FORMULAIRE (API
/api/auth + /api/persos exactement comme le frontend), puis partie complète.

Usage: python simul_form_game.py <phase>
Phases: form | party | explore | combat | finale
État: /tmp/form_state.json (tokens, party_id, transcript)
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import quote

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000"
STATE_FILE = "/tmp/form_state.json"
TURN_TIMEOUT = 300

ISSUES = []
OKS = []


def check(cond, ok_msg, ko_msg):
    if cond:
        OKS.append(ok_msg)
        print(f"  ✅ {ok_msg}")
    else:
        ISSUES.append(ko_msg)
        print(f"  ❌ {ko_msg}")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"players": {}, "party_id": None, "transcript": []}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
#  Règles 3.5 de référence (calcul local indépendant pour recouper le serveur)
# --------------------------------------------------------------------------- #
def mod(v):
    return (int(v) - 10) // 2


RACE_MODS = {
    "Elfe": {"DEX": 2, "CON": -2}, "Nain": {"CON": 2, "CHA": -2},
    "Halfelin": {"DEX": 2, "FOR": -2}, "Gnome": {"CON": 2, "FOR": -2},
    "Demi-orc": {"FOR": 2, "INT": -2, "CHA": -2},
}
CLASSES_35 = {  # (de_vie, bab_prog, sauves_bonnes)
    "Barbare": (12, "bon", ["Vigueur"]),
    "Barde": (6, "moyen", ["Reflexes", "Volonte"]),
    "Clerc": (8, "moyen", ["Vigueur", "Volonte"]),
    "Druide": (8, "moyen", ["Vigueur", "Volonte"]),
    "Guerrier": (10, "bon", ["Vigueur"]),
    "Magicien": (4, "mauvais", ["Volonte"]),
    "Moine": (8, "moyen", ["Vigueur", "Reflexes", "Volonte"]),
    "Paladin": (10, "bon", ["Vigueur"]),
    "Rodeur": (8, "moyen", ["Vigueur", "Reflexes"]),
    "Sorcier": (4, "mauvais", ["Volonte"]),
    "Voleur": (6, "moyen", ["Reflexes"]),
}
ARMURES_35 = {  # nom -> (bonus, dex_max)
    "Armure rembourrée": (1, 8), "Armure de cuir": (2, 6), "Cuir clouté": (3, 5),
    "Chemise de mailles": (4, 4), "Cuir épais": (3, 4), "Armure d'écailles": (4, 3),
    "Cotte de mailles": (5, 2), "Plastron": (5, 3), "Harnois complet": (8, 1),
}
BOUCLIERS_35 = {"Targe": 1, "Bouclier bois léger": 1, "Bouclier bois lourd": 2}


def bab_niv(prog, n):
    return n if prog == "bon" else (n * 3) // 4 if prog == "moyen" else n // 2


def save_base(bonne, n):
    return 2 + n // 2 if bonne else n // 3


def attendu_35(base, race, classe, niv, noms_armures):
    """Calcule la fiche attendue selon le PHB 3.5 (référence indépendante)."""
    final = {}
    for c in ("FOR", "DEX", "CON", "INT", "SAG", "CHA"):
        final[c] = max(1, base[c] + RACE_MODS.get(race, {}).get(c, 0))
    dv, prog, bonnes = CLASSES_35[classe]
    mcon, mdex, msag = mod(final["CON"]), mod(final["DEX"]), mod(final["SAG"])
    pv = max(1, dv + mcon + (niv - 1) * (dv // 2 + 1 + mcon))
    corps = [(ARMURES_35[n][0], ARMURES_35[n][1]) for n in noms_armures if n in ARMURES_35]
    bouclier = max((BOUCLIERS_35[n] for n in noms_armures if n in BOUCLIERS_35), default=0)
    if corps:
        b_ca, dex_max = max(corps, key=lambda x: x[0])
    else:
        b_ca, dex_max = 0, 99
    ca = 10 + b_ca + bouclier + min(mdex, dex_max)
    return {
        "carac_final": final,
        "pv_max": pv,
        "ca": ca,
        "bab": bab_niv(prog, niv),
        "sauvegardes": {
            "Vigueur": save_base("Vigueur" in bonnes, niv) + mcon,
            "Reflexes": save_base("Reflexes" in bonnes, niv) + mdex,
            "Volonte": save_base("Volonte" in bonnes, niv) + msag,
        },
    }


# --------------------------------------------------------------------------- #
#  Choix « humains » de chaque joueur (comme au formulaire)
# --------------------------------------------------------------------------- #
CHOIX_JOUEURS = [
    {
        "user": "Alice", "mdp": "alice123",
        "nom": "Brunhild", "race": "Naine", "classe": "Guerrière",
        "alignement": "Loyal Bon", "dieu": "Moradin",
        "armes": ["Épée longue", "Javelot"],
        "armures": ["Cotte de mailles", "Bouclier bois lourd"],
        "equip": ["Sac à dos", "Corde en chanvre (15 m)", "Torche", "Rations de voyage (1 jour)"],
        "dons": ["Science de l'initiative"],
        "competences": {"Escalade": 4, "Saut": 4, "Natation": 4, "Premiers secours": 1},
        "apparence": {"sexe": "F", "age": "62 ans", "taille": "1,32 m, trapue",
                      "poids": "68 kg", "yeux": "noirs", "cheveux": "roux tressés",
                      "peau": "mate", "description": "barbe tressée aux anneaux d'or, cicatrice sur l'avant-bras"},
        "histoire": "Vétéran des gardes de la forteresse de Pierre-Feu, partie chercher la hache runique de son clan.",
    },
    {
        "user": "Bob", "mdp": "bob12345",
        "nom": "Zephyr", "race": "Elfe", "classe": "Magicien",
        "alignement": "Chaotique Bon", "dieu": "Boccob",
        "armes": ["Bâton"],
        "armures": [],
        "equip": ["Sac à dos", "Grimoire (sorts)", "Sacoche à composantes"],
        "dons": ["Science de l'initiative"],
        "competences": {"Connaissances (mystères)": 4, "Détection": 4, "Art de la magie": 4, "Lecture sur les lèvres": 1},
        "apparence": {"sexe": "M", "age": "177 ans", "taille": "1,78 m, élancé",
                      "poids": "59 kg", "yeux": "verts", "cheveux": "argent",
                      "peau": "pâle", "description": "robe étoilée d'azur, un anneau de laiton au pouce"},
        "histoire": "Ancien apprenti de la tour d'Eauprofonde, obsédé par les runes anciennes de la côte.",
    },
    {
        "user": "Carol", "mdp": "carol123",
        "nom": "Merissa", "race": "Halfeline", "classe": "Voleuse",
        "alignement": "Neutre Bon", "dieu": "Yondalla",
        "armes": ["Dague", "Fronde"],
        "armures": ["Armure de cuir"],
        "equip": ["Sac à dos", "Outils de voleur", "Corde en chanvre (15 m)"],
        "dons": ["Ambidextrie"],
        "competences": {"Discrétion": 4, "Détection": 4, "Fouille": 4, "Crochetage": 4, "Escamotage": 4},
        "apparence": {"sexe": "F", "age": "29 ans", "taille": "1,05 m, menue",
                      "poids": "19 kg", "yeux": "noisette", "cheveux": "bruns bouclés",
                      "peau": "hâlée", "description": "poches multiples cousues dans sa cape, sourire espiègle"},
        "histoire": "Née dans les collines de Luiren, partie « emprunter » les trésors des grandes cités.",
    },
]


# --------------------------------------------------------------------------- #
#  Client HTTP avec token
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self, token=""):
        self.h = {"Content-Type": "application/json"}
        if token:
            self.h["Authorization"] = f"Bearer {token}"

    def req(self, method, path, payload=None):
        r = urllib.request.Request(
            BASE + path, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=self.h,
        )
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} {path}: {detail[:300]}")


async def phase_form():
    st = load_state()
    st["players"] = {}
    modele = Api().req("GET", "/api/persos/modele")
    print(f"Catalogue: {len(modele['races'])} races, {len(modele['classes'])} classes, "
          f"{len(modele['armes'])} armes, {len(modele['armures'])} armures, "
          f"{len(modele['dons'])} dons, {len(modele['competences'])} compétences")

    for choix in CHOIX_JOUEURS:
        print(f"\n{'='*66}\n[{choix['user']}] création de {choix['nom']} "
              f"({choix['race']} {choix['classe']}) via le formulaire\n{'='*66}")
        # 1. Inscription (ou connexion si compte existant)
        try:
            auth = Api().req("POST", "/api/auth/inscription",
                             {"nom": choix["user"], "mot_de_passe": choix["mdp"]})
        except RuntimeError:
            auth = Api().req("POST", "/api/auth/connexion",
                             {"nom": choix["user"], "mot_de_passe": choix["mdp"]})
        api = Api(auth["token"])
        check(True, f"{choix['user']}: compte + token OK", "")

        # 2. Tirage aléatoire 4d6 (bouton du formulaire)
        tirage = api.req("POST", "/api/persos/stats-aleatoires")
        check(all(3 <= v <= 18 for v in tirage["carac"].values()),
              f"{choix['nom']}: tirage 4d6 plausible {tirage['carac']}",
              f"{choix['nom']}: tirage hors plage {tirage['carac']}")
        # Un humain répartit ses valeurs : la meilleure là où la classe en a
        # besoin (ordre de priorité par classe).
        prio = {
            "Guerrière": ["FOR", "CON", "DEX", "SAG", "CHA", "INT"],
            "Magicien": ["INT", "DEX", "CON", "SAG", "CHA", "FOR"],
            "Voleuse": ["DEX", "INT", "CON", "CHA", "SAG", "FOR"],
        }[choix["classe"]]
        vals = sorted(tirage["carac"].values(), reverse=True)
        base = dict(zip(prio, vals))
        print(f"  tirage {tirage['carac']} → réparti {base}")

        # 3. Or de départ (bouton « Tirer » du formulaire)
        orrep = api.req("POST", "/api/persos/or-depart",
                        {"classe": choix["classe"], "mode": "tirage"})
        print(f"  or de départ: {orrep['or']} po ({orrep['formule']})")

        # 4. Soumission du formulaire (payload identique au frontend)
        payload = {
            "nom": choix["nom"], "race": choix["race"], "classe": choix["classe"],
            "niveau": 1, "carac": base, "alignement": choix["alignement"],
            "dieu": choix["dieu"], "or": orrep["or"],
            "equipement": [{"nom": n, "qte": 1} for n in choix["armes"] + choix["armures"] + choix["equip"]],
            "dons": choix["dons"], "competences": choix["competences"],
            "histoire": choix["histoire"], "apparence": choix["apparence"],
        }
        rep = api.req("POST", "/api/persos", payload)
        fiche = rep["fiche"]
        print(f"  serveur → PV {fiche['pv']}/{fiche['pv_max']}, CA {fiche['ca']}, "
              f"BBA {fiche['bab']:+d}, saves {fiche['sauvegardes']}")

        # 5. Vérification conformité 3.5 (référence indépendante)
        att = attendu_35(base, fiche["race"], fiche["classe"], 1, choix["armures"])
        print(f"  attendu → PV {att['pv_max']}, CA {att['ca']}, BBA {att['bab']:+d}, "
              f"saves {att['sauvegardes']}")
        check(fiche["carac"] == att["carac_final"],
              f"{choix['nom']}: caracs finales = base + mods raciaux",
              f"{choix['nom']}: caracs {fiche['carac']} ≠ attendu {att['carac_final']}")
        check(fiche["pv_max"] == att["pv_max"],
              f"{choix['nom']}: PV niv.1 = max(dv)+CON = {att['pv_max']}",
              f"{choix['nom']}: PV {fiche['pv_max']} ≠ {att['pv_max']}")
        check(fiche["ca"] == att["ca"],
              f"{choix['nom']}: CA armure+bouclier+Dex plafonnée = {att['ca']}",
              f"{choix['nom']}: CA {fiche['ca']} ≠ attendu {att['ca']} (armure ignorée ?)")
        check(fiche["bab"] == att["bab"],
              f"{choix['nom']}: BBA {fiche['bab']:+d}",
              f"{choix['nom']}: BBA {fiche['bab']} ≠ {att['bab']}")
        check(fiche["sauvegardes"] == att["sauvegardes"],
              f"{choix['nom']}: sauvegardes conformes",
              f"{choix['nom']}: saves {fiche['sauvegardes']} ≠ {att['sauvegardes']}")

        with open(f"/tmp/fiche_attendue_{choix['nom']}.json", "w", encoding="utf-8") as f:
            json.dump({"pv_max": att["pv_max"], "ca": att["ca"],
                       "carac": fiche["carac"]}, f, ensure_ascii=False)

        st["players"][choix["user"]] = {
            "token": auth["token"], "nom": choix["nom"], "mdp": choix["mdp"],
        }

    save_state(st)
    bilan_partiel("FORMULAIRE")


def bilan_partiel(label):
    print(f"\n{'#'*66}\n# BILAN {label}: {len(OKS)} OK, {len(ISSUES)} problèmes")
    for i in ISSUES:
        print(f"#   ❌ {i}")
    print("#" * 66)


# --------------------------------------------------------------------------- #
#  WS (identique à simul_game.py, avec personnage au join)
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, name):
        self.name = name
        self.ws = None
        self.inbox = asyncio.Queue()

    async def connect(self, party_id, personnage):
        self.ws = await websockets.connect(
            f"{WS_BASE}/ws/{party_id}", max_size=2 ** 23, ping_interval=20)
        await self.ws.send(json.dumps({
            "type": "join", "player": self.name, "personnage": personnage}))
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


async def connect_all(st):
    clients = {}
    for user, info in st["players"].items():
        c = Client(user)
        await c.connect(st["party_id"], info["nom"])
        clients[user] = c
        await asyncio.sleep(0.3)
    await asyncio.sleep(2)
    for c in clients.values():
        c.drain()
    return clients


async def mj_turn(st, clients, speaker, text, label=""):
    watcher = clients[next(iter(clients))]
    watcher.drain()
    t0 = time.time()
    await clients[speaker].say(text)
    print(f"\n{'='*66}\n[{label}] {speaker}: {text[:110]}\n{'='*66}", flush=True)
    dm = None
    deadline = time.time() + TURN_TIMEOUT
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
            raise RuntimeError(f"Erreur serveur: {msg}")
    if dm is None:
        raise TimeoutError(f"Pas de réponse dm — {label}")
    dur = time.time() - t0
    print(f"--- MJ ({dur:.0f}s, corr={dm.get('corrections', 0)}) ---", flush=True)
    print((dm.get("text") or "")[:500], flush=True)
    for tc in dm.get("tool_calls_trace", []):
        args = json.dumps(tc.get("args", tc.get("arguments", {})), ensure_ascii=False)[:160]
        print(f"  [tool] {tc.get('name')}({args})", flush=True)
    for p in dm.get("state_patches", []):
        print(f"  [patch] {json.dumps(p, ensure_ascii=False)[:200]}", flush=True)
    st["transcript"].append({
        "label": label, "player": speaker, "input": text,
        "dm_text": dm.get("text", ""), "tool_calls_trace": dm.get("tool_calls_trace", []),
        "state_patches": dm.get("state_patches", []),
    })
    save_state(st)
    return dm


async def phase_party():
    st = load_state()
    # 1. Créer la partie (comme le bouton frontend)
    rep = Api().req("POST", "/api/parties", {"titre": "Session Formulaire — Les Trois Compagnons"})
    st["party_id"] = rep["partie_id"]
    save_state(st)
    print(f"Partie créée: {st['party_id']}")

    # 2. Choix de la quête via le sélecteur (POST /quest — comme le front)
    scen = Api().req("GET", "/api/scenarios")
    p5 = next(s for s in scen if s["id"] == "P5")
    q = Api().req("POST", f"/api/parties/{st['party_id']}/quest", {
        "titre": p5["titre"], "pitch": p5["pitch"], "source": p5.get("source", "PDF local")})
    print(f"Quête posée: {q['quete']['titre']}")

    # 3. Connexion des 3 joueurs avec leur personnage
    clients = await connect_all(st)

    # 4. Vérifier l'état : les 3 PJ doivent y être avec leurs stats du formulaire
    etat = Api().req("GET", f"/api/parties/{st['party_id']}")["etat"]
    print(f"\nPhase: {etat['phase']} | PJ: {len(etat['pj'])}")
    for pj in etat["pj"]:
        print(f"  - {pj['nom']} ({pj['race']}, {pj['classe']}) pv {pj['pv']}/{pj['pv_max']} "
              f"ca {pj['ca']} bab {pj.get('bab')} joueur={pj['joueur']}")
    check(len(etat["pj"]) == 3, "3 PJ rattachés à la partie via le join",
          f"{len(etat['pj'])} PJ au lieu de 3")
    # Recoupe avec les fiches attendues (re-calculées depuis le formulaire)
    for choix in CHOIX_JOUEURS:
        with open(f"/tmp/fiche_attendue_{choix['nom']}.json", encoding="utf-8") as f:
            att = json.load(f)
        pj = next((p for p in etat["pj"] if p["nom"] == choix["nom"]), None)
        if pj:
            check(pj["pv_max"] == att["pv_max"] and pj["ca"] == att["ca"],
                  f"{choix['nom']}: stats partie = fiche formulaire",
                  f"{choix['nom']}: partie pv {pj['pv_max']}/ca {pj['ca']} ≠ fiche {att['pv_max']}/{att['ca']}")
    check(etat["quete"]["titre"] == p5["titre"], "quête dans l'état", "quête absente de l'état")
    save_state(st)
    bilan_partiel("PARTIE")


async def phase_explore():
    st = load_state()
    clients = await connect_all(st)
    await mj_turn(st, clients, "Alice",
        "Bonsoir Maître du Jeu ! Nous sommes Brunhild la naine guerrière, Zephyr "
        "le magicien elfe et Merissa la halfeline voleuse. Où commençons-nous ?",
        "accueil + brief")
    await mj_turn(st, clients, "Carol",
        "Merissa examine les alentours discrètement : y a-t-il des passages, "
        "des traces ou des dangers visibles ? Je reste dans l'ombre.",
        "reconnaissance")
    await mj_turn(st, clients, "Bob",
        "Zephyr consulte son grimoire et jette un Detect Magic sur la zone. "
        "Que perçoit-il ?",
        "détection magique")
    await mj_turn(st, clients, "Alice",
        "En avant ! Nous nous dirigeons vers l'entrée principale, Brunhild "
        "en tête bouclier levé. On entre dans les profondeurs !",
        "entrer donjon")


async def phase_combat():
    st = load_state()
    clients = await connect_all(st)
    await mj_turn(st, clients, "Carol",
        "Merissa avance en silence dans le couloir, dague prête, à l'affût du "
        "moindre mouvement. Je cherche les pièges au sol.",
        "progression prudente")
    await mj_turn(st, clients, "Alice",
        "Si une créature apparaît, Brunhild l'attaque immédiatement à l'épée "
        "longue ! Montre-toi, monstre !",
        "provocation")
    await mj_turn(st, clients, "Bob",
        "Zephyr lance un projectile magique sur la créature dès qu'elle est "
        "en vue !",
        "sort offensive")
    await mj_turn(st, clients, "Alice",
        "Brunhild frappe la créature de toutes ses forces avec son épée "
        "longue ! Attaque !",
        "attaque Brunhild")
    await mj_turn(st, clients, "Carol",
        "Merissa profite de la confusion pour frapper dans le dos de la "
        "créature avec sa dague — attaque sournoise !",
        "attaque sournoise Merissa")


async def phase_finale():
    st = load_state()
    clients = await connect_all(st)
    await mj_turn(st, clients, "Carol",
        "Fouillons la créature et la pièce ! Merissa cherche trésors, pièges "
        "et passages secrets.",
        "fouille")
    await mj_turn(st, clients, "Alice",
        "Victoire ! Nous ressortons, nous faisons notre rapport au village et "
        "nous reposons. Bravo à tous !",
        "épilogue")


async def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "form"
    fn = {
        "form": phase_form, "party": phase_party, "explore": phase_explore,
        "combat": phase_combat, "finale": phase_finale,
    }[phase]
    await fn()


if __name__ == "__main__":
    asyncio.run(main())
