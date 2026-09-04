# -*- coding: utf-8 -*-
"""E2E réel : complète le scénario « Dues for the Dead » sur le serveur :8123.

Scénario : univers « Divers », id `divers_dues_for_the_dead`. Aventure
d'initiation courte (crypte profanée par des morts-vivants).

Phases REPRISABLES :
  py scripts/dues_for_the_dead_e2e.py setup     # partie + personnages + scénario
  py scripts/dues_for_the_dead_e2e.py load      # charger le scénario (quête)
  py scripts/dues_for_the_dead_e2e.py explore   # exploration + rencontres
  py scripts/dues_for_the_dead_e2e.py c1        # combat 1 → N
  py scripts/dues_for_the_dead_e2e.py finale    # résolution + bilan
  py scripts/dues_for_the_dead_e2e.py rapport   # synthèse des vérifs

Le transcript complet est sauvegardé dans TEMP/dues/transcript.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tests"))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dues")
os.makedirs(_TMP, exist_ok=True)
PID_FILE = os.path.join(_TMP, "pid.txt")
BILAN_FILE = os.path.join(_TMP, "bilan.json")
TRANSCRIPT_FILE = os.path.join(_TMP, "transcript.json")

import e2e_combats_reels as _c  # noqa: E402
_c.BILAN_FILE = BILAN_FILE
from e2e_combats_reels import (  # noqa: E402
    Bilan, _blesse_a_soigner, _monstre_a_frapper, _pj_a_terre,
    connecter, etat_partie, fiche_pj, snapshot, tour_dm, vider,
)
import websockets  # noqa: E402

BASE = "http://localhost:8123"
WSS = "ws://localhost:8123/ws"
SCENARIO_ID = "divers_dues_for_the_dead"

PLAYERS = ["Guerrier", "Rôdeur", "Clerc", "Magicien"]
CHAR_TO_PLAYER = {"Gunnar": "Guerrier", "Lyra": "Rôdeur",
                  "Aurora": "Clerc", "Miro": "Magicien"}
PLAYER_TO_CHAR = {v: k for k, v in CHAR_TO_PLAYER.items()}
CHARS = list(CHAR_TO_PLAYER.keys())


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = __import__("urllib.request").request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"})
    return json.loads(__import__("urllib.request").request.urlopen(
        req, timeout=20).read())


def append_transcript(entry: dict):
    tr = []
    if os.path.isfile(TRANSCRIPT_FILE):
        with open(TRANSCRIPT_FILE, encoding="utf-8") as f:
            tr = json.load(f)
    tr.append(entry)
    with open(TRANSCRIPT_FILE, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1)


async def _ouvrir(pid: str) -> dict:
    socks = {}
    for p in PLAYERS:
        socks[p] = await connecter(pid, p)
        await asyncio.sleep(0.2)
    return socks


async def _fermer(socks: dict):
    for w in socks.values():
        try:
            await w.close()
        except Exception:
            pass


async def _tour(socks: dict, joueur: str, texte: str, bilan: Bilan,
                label: str = "") -> dict | None:
    autres = [w for n, w in socks.items() if n != joueur]
    rep = await tour_dm(socks[joueur], autres, joueur, texte, bilan)
    append_transcript({
        "phase": label, "joueur": joueur, "input": texte,
        "dm_text": (rep or {}).get("text", ""),
        "trace": (rep or {}).get("trace", []),
    })
    return rep


def _trace_outils(rep: dict | None) -> list[str]:
    if not rep:
        return []
    return [tc.get("name", "?") for tc in rep.get("trace", [])]


def _cible(snap: dict) -> str | None:
    return _monstre_a_frapper(snap)


async def phase_setup():
    """Nouvelle partie + personnages + scénario chargé (quête posée)."""
    for f in (PID_FILE, BILAN_FILE, TRANSCRIPT_FILE):
        if os.path.isfile(f):
            os.remove(f)
    bilan = Bilan()
    pid = _post("/api/parties",
                {"titre": "Dues for the Dead (E2E)"})["partie_id"]
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie : {pid} ===")
    socks = await _ouvrir(pid)
    demandes = {
        "Guerrier": ("MJ ! Crée ma fiche via le tool fiche_perso_creer_rapide "
                     "nom Gunnar, race Nain, classe Guerrier. Je frappe à la "
                     "grande hache."),
        "Rôdeur": ("Crée ma fiche via le tool fiche_perso_creer_rapide : nom "
                   "Lyra, race Elfe, classe Rôdeur, avec un arc long et 20 "
                   "flèches."),
        "Clerc": ("Crée ma fiche via le tool fiche_perso_creer_rapide : nom "
                  "Aurora, race Humain, classe Clerc, avec une masse et le "
                  "sort soins légers."),
        "Magicien": ("Crée ma fiche via le tool fiche_perso_creer_rapide : "
                     "nom Miro, race Humain, classe Magicien, avec le sort "
                     "projectile magique."),
    }
    for p, texte in demandes.items():
        for _ in range(5):
            if PLAYER_TO_CHAR[p] in (snapshot(pid) or {}).get("pj", []):
                break
            rep = await _tour(socks, p, texte, bilan, "setup")
            if rep is None:
                break
    snap = snapshot(pid)
    for p in PLAYERS:
        bilan.check(f"[setup] {PLAYER_TO_CHAR[p]} créé",
                    PLAYER_TO_CHAR[p] in snap["pj"],
                    f"pj={list(snap['pj'])}")
    await _fermer(socks)
    bilan.sauver()
    print(f"\nPersonnages créés : {[c for c in CHARS if c in snap['pj']]}")


async def phase_load():
    pid = lire_pid()
    bilan = Bilan.charger()
    socks = await _ouvrir(pid)
    rep = await _tour(
        socks, "Guerrier",
        f"Quelles missions proposez-vous ? Chronique nous parle d'un jeune "
        f"clerc qui cherche des aventuriers pour purifier un sanctuaire "
        f"profané. (MJ : liste les scénarios avec scenarios_laelith_lister.)",
        bilan, "catalogue")
    # Demande explicite de charger le scénario Divers « Dues for the Dead »
    rep = await _tour(
        socks, "Magicien",
        f"Nous choisissons le scénario d'initiation « Dues for the Dead » "
        f"(univers Divers). (MJ : OBLIGATOIRE — appelle le tool "
        f"scenarios_laelith_charger avec scenario_id=\"{SCENARIO_ID}\" pour "
        f"charger son texte, puis pose la quête via etat_partie_patch "
        f"quete.titre/quete.pitch.)",
        bilan, "charger scénario")
    out = _trace_outils(rep)
    bilan.check("[load] scenarios_laelith_charger appelé",
                "scenarios_laelith_charger" in out, f"outils={out}")
    st = etat_partie(pid) or {}
    quete = st.get("quete") or {}
    import re as _re
    titre = _re.sub(r"[^A-Za-z0-9]", " ", str(quete.get("titre") or "")).lower()
    bilan.check("[load] quête 'Dues for the Dead' posée",
                "dues" in titre, f"quete={quete}")
    await _fermer(socks)
    bilan.sauver()


async def _jour_combat(pid, socks, bilan, titre, max_tours=14):
    print(f"\n=== ⚔️  {titre} ===")
    detruits: set = set()
    vu = False
    echecs = 0
    for i in range(max_tours):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            if vu:
                print(f"  [combat] terminé (phase={snap['phase']})")
                bilan.event(f"{titre} : détruits={sorted(detruits)}")
                break
            print(f"  [combat] non engagé (phase={snap['phase']})")
            break
        vu = True
        detruits |= {m for m, st in snap["monstres"].items()
                     if "Détruit" in st["conds"]}
        courant = str(snap.get("courant") or "")
        cible = _cible(snap)
        if cible is None:
            await _tour(socks, "Guerrier",
                        "Tous les ennemis sont détruits — termine le combat "
                        "(finir_combat).", bilan, f"{titre}:clôture")
            continue
        joueur = CHAR_TO_PLAYER.get(courant)
        if joueur is None:
            victime = min(
                (n for n in snap["pj"]
                 if snap["pj"][n]["pv"] and snap["pj"][n]["pv"] > 0),
                key=lambda n: snap["pj"][n]["pv"], default=None)
            action = f"{courant} attaque {victime or 'le PJ le plus proche'} !"
            ferme = (f"(MJ : résous l'attaque de {courant} contre {victime} "
                     f"avec lancer_attaque, lancer_degats, "
                     f"fiche_perso_infliger_degats puis tour_suivant_combat)")
            joueur = "Guerrier"
        else:
            self_name = courant
            if _pj_a_terre(snap, courant):
                action = f"{courant} est à terre — passe son tour."
                ferme = (f"(MJ : tour_suivant_combat) {courant} est inconscient."
                         )
            elif courant == "Lyra":
                action = (f"Lyra décoche une flèche sur {cible} ! (MJ : "
                          f"inventaire_consommer_munition munition=flèche sur "
                          f"Lyra puis lancer_attaque/lancer_degats/"
                          f"fiche_perso_infliger_degats sur {cible})")
                ferme = (f"(OBLIGATOIRE : inventaire_consommer_munition "
                         f"munition=flèche, lancer_attaque, lancer_degats, "
                         f"fiche_perso_infliger_degats sur {cible}, "
                         f"tour_suivant_combat) Lyra tire !")
            elif courant == "Aurora":
                blessé = _blesse_a_soigner(snap, frozenset())
                if blessé:
                    action = (f"Aurora lance soins légers sur {blessé} ! "
                              f"(MJ : lancer_des 1d8+1 puis "
                              f"fiche_perso_soigner nom={blessé})")
                    ferme = (f"(OBLIGATOIRE : lancer_des, "
                             f"fiche_perso_soigner nom={blessé}, "
                             f"tour_suivant_combat) Aurora soigne {blessé} !")
                else:
                    action = "Aurora se met en garde, masse prête."
                    ferme = "(MJ : tour_suivant_combat) Aurora en garde."
            elif courant == "Miro":
                action = (f"Miro lance projectile magique sur {cible} (touche "
                          f"automatique). (MJ : lancer_degats puis "
                          f"fiche_perso_infliger_degats sur {cible})")
                ferme = (f"(OBLIGATOIRE : lancer_degats, "
                         f"fiche_perso_infliger_degats sur {cible}, "
                         f"tour_suivant_combat) Les projectiles frappent "
                         f"{cible} !")
            else:  # Gunnar
                action = (f"Gunnar frappe {cible} de sa grande hache ! "
                          f"(MJ : lancer_attaque puis lancer_degats puis "
                          f"fiche_perso_infliger_degats sur {cible})")
                ferme = (f"(OBLIGATOIRE : lancer_attaque, lancer_degats, "
                         f"fiche_perso_infliger_degats sur {cible}, "
                         f"tour_suivant_combat) Gunnar attaque {cible} !")
        avant = snapshot(pid)
        rep = await _tour(socks, joueur, action, bilan, titre)
        if rep is not None and not rep["trace"] and \
                snapshot(pid).get("courant") == courant and \
                snapshot(pid)["phase"] == "combat":
            print("    ↻ tour sans tool → relance ferme")
            rep = await _tour(socks, joueur, ferme, bilan, titre)
        if rep is None:
            echecs += 1
            if echecs >= 3:
                print("  [combat] 3 échecs consécutifs — abandon")
                break
            continue
        echecs = 0
    return detruits


async def _engager(pid: str, socks: dict, bilan: Bilan, joueur: str,
                   ennemis: str, label: str) -> bool:
    """Relance l'appel à `engager_combat` jusqu'à ce que la phase passe en
    combat avec des monstres, ou abandon après 3 essais."""
    messages = [
        f"{ennemis} surgissent et nous attaquent ! (MJ : appelle "
        f"MAINTENANT le tool engager_combat avec monstres=\"{ennemis}\") "
        f"{joueur} engage le combat !",
        f"(OBLIGATOIRE : tool engager_combat, monstres=\"{ennemis}\") "
        f"AU SECOURS, ils nous attaquent, engage le combat !",
        f"(STOP : le combat n'est PAS engagé. Appelle engager_combat avec "
        f"monstres=\"{ennemis}\" avant toute narration.)",
    ]
    for m in messages:
        snap = snapshot(pid)
        if snap["phase"] == "combat" and snap["monstres"]:
            return True
        await _tour(socks, joueur, m, bilan, label)
    snap = snapshot(pid)
    return snap["phase"] == "combat" and bool(snap["monstres"])


async def phase_c1():
    pid = lire_pid()
    bilan = Bilan.charger()
    socks = await _ouvrir(pid)
    rep = await _tour(
        socks, "Guerrier",
        "Nous pénétrons dans le cimetière Valhingen pour porter secours au "
        "clerc et purifier les lieux. Deux ZOMBIES se dressent sous l'arche "
        "de la crypte ! Gunnar engage le combat contre ce qui s'agite dans "
        "les ombres !",
        bilan, "c1:mise en place")
    ok = await _engager(pid, socks, bilan, "Guerrier",
                        "Zombie, Zombie", "c1:engager")
    if not ok:
        bilan.check("[c1] combat engagé", False, "pas engagé après 3 essais")
        await _fermer(socks)
        bilan.sauver()
        return
    detruits = await _jour_combat(pid, socks, bilan, "Combat 1 : Zombies", 10)
    bilan.check("[c1] combat résolu", snapshot(pid)["phase"] != "combat",
                f"détruits={sorted(detruits)}")
    await _fermer(socks)
    bilan.sauver()
    print(f"=== Combat 1 : détruits {sorted(detruits)} ===")


async def phase_finale():
    pid = lire_pid()
    bilan = Bilan.charger()
    st = etat_partie(pid) or {}
    print(f"Phase={st.get('phase')} | quete={st.get('quete')} | "
          f"tour={st.get('tour')}")
    for p in CHARS:
        f = fiche_pj(p)
        print(f"  🛡️ {p}: pv={f.get('pv')}/{f.get('pv_max')} "
              f"xp={f.get('xp')}")
    for m in (st.get("monstres_combat") or []):
        print(f"  👹 {m.get('nom')}: {m.get('pv')}/{m.get('pv_max')} "
              f"{m.get('conditions')}")
    bilan.sauver()


async def phase_rapport():
    bilan = Bilan.charger()
    print(f"{len(bilan.oks)} vérifs OK, {len(bilan.fails)} échecs")
    for f in bilan.fails:
        print("  " + f)
    print("Événements :")
    for e in bilan.evenements:
        print("  ⚑ " + e)
    sys.exit(1 if bilan.fails else 0)


PHASES = {"setup": phase_setup, "load": phase_load, "c1": phase_c1,
          "finale": phase_finale, "rapport": phase_rapport}


async def main():
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    await fn()


if __name__ == "__main__":
    asyncio.run(main())
