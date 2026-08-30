"""E2E réel : partie complète sur le serveur :8123 (LLM Gemma vrai).

Scénario du groupe multi-classes couvrant les objectifs de la session :
corps-à-corps / distance / magie / soins, exploration de donjon, plusieurs
types de monstres, et inventaire/encombrement D&D 3.5 évolutif (flèches
consommées, objets ramassés, charge suivie).

La partie cible (PID) est celle créée par `setup` ; les phases sont
REPRISABLES. Le mapping joueur↔personnage gère le cas où le joueur (WS) et
le nom de personnage diffèrent.

  py tests/e2e_partie_complete.py setup
  py tests/e2e_partie_complete.py explo
  py tests/e2e_partie_complete.py c1
  py tests/e2e_partie_complete.py c2
  py tests/e2e_partie_complete.py c3
  py tests/e2e_partie_complete.py refus
  py tests/e2e_partie_complete.py inv
  py tests/e2e_partie_complete.py statut
  py tests/e2e_partie_complete.py rapport
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_combats_reels as _c  # noqa: E402

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_partie_complete")
os.makedirs(_TMP, exist_ok=True)
PID_FILE = os.path.join(_TMP, "pid.txt")
BILAN_FILE = os.path.join(_TMP, "bilan.json")
# Isoler le bilan de CETTE session : `Bilan.charger()/sauver()` lisent le
# global `BILAN_FILE` du module — on le rebinde vers notre fichier dédié.
_c.BILAN_FILE = BILAN_FILE

from e2e_combats_reels import (  # noqa: E402
    Bilan, _blesse_a_soigner, _monstre_a_frapper, _norm, _pj_a_terre,
    connecter, etat_partie, fiche_pj, snapshot, tour_dm, vider,
)
import websockets  # noqa: E402

BASE = "http://localhost:8123"
WSS = "ws://localhost:8123/ws"

# Joueurs (identifiants WS) → nom de personnage.
PLAYERS = ["Brunhild", "Rôdeur", "Clerc", "Magicien"]
PLAYER_TO_CHAR = {"Brunhild": "Brunhild", "Rôdeur": "Aelin",
                  "Clerc": "Thalia", "Magicien": "Miro"}
CHAR_TO_PLAYER = {v: k for k, v in PLAYER_TO_CHAR.items()}
CHARS = list(PLAYER_TO_CHAR.values())


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


async def _ouvrir(pid: str) -> dict:
    return {p: await connecter(pid, p) for p in PLAYERS}


def _cible(snap: dict) -> str | None:
    return _monstre_a_frapper(snap)


# --------------------------------------------------------------------------- #
#  Charge / inventaire sur fiches
# --------------------------------------------------------------------------- #
def charge_fiche(nom: str) -> dict:
    f = fiche_pj(nom) or {}
    return {
        "poids_transporte": f.get("poids_transporte"),
        "etat_encumbrance": f.get("etat_encumbrance"),
        "charge_max": f.get("charge_max"),
        "inventaire": f.get("inventaire") or [],
    }


def _categorie(poids: float, maxi: float) -> str:
    if poids > maxi:
        return "Depassee"
    if poids > maxi * 2 / 3:
        return "Lourde"
    if poids > maxi / 3:
        return "Moyenne"
    return "Legere"


def verifier_charge(bilan: Bilan, nom: str) -> None:
    c = charge_fiche(nom)
    ok_champs = (isinstance(c["poids_transporte"], (int, float))
                 and c["etat_encumbrance"] in ("Legere", "Moyenne", "Lourde",
                                               "Depassee")
                 and isinstance(c["charge_max"], (int, float)))
    bilan.check(
        f"[charge {nom}] champs inventaire/encombrement présents", ok_champs,
        f"poids={c['poids_transporte']}, état={c['etat_encumbrance']}, "
        f"max={c['charge_max']}")
    if ok_champs:
        attendu = _categorie(float(c["poids_transporte"]),
                             float(c["charge_max"]))
        bilan.check(
            f"[charge {nom}] catégorie PHB 3.5 exacte",
            c["etat_encumbrance"] == attendu,
            f"reçu={c['etat_encumbrance']}, PHB={attendu}")


# --------------------------------------------------------------------------- #
#  Phases
# --------------------------------------------------------------------------- #
async def phase_setup():
    os.makedirs(_TMP, exist_ok=True)
    bilan = Bilan()
    pid = None
    if os.path.isfile(PID_FILE):
        p0 = lire_pid()
        if etat_partie(p0):
            pid = p0
    if pid is None:
        pid = _post("/api/parties",
                    {"titre": "E2E Partie complète (inventaire)"})["partie_id"]
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(pid)
    print(f"=== Partie : {pid} ===")
    sockets = await _ouvrir(pid)
    demandes = {
        "Brunhild": ("Bonjour MJ ! Crée ma fiche via le tool "
                     "fiche_perso_creer_rapide : nom Brunhild, race Nain, "
                     "classe Guerrier. On part explorer un donjon !"),
        "Rôdeur": ("Crée ma fiche via le tool fiche_perso_creer_rapide : "
                   "nom Aelin, race Elfe, classe Rôdeur, avec un arc long "
                   "et 20 flèches."),
        "Clerc": ("Crée ma fiche via le tool fiche_perso_creer_rapide : "
                  "nom Thalia, race Humain, classe Clerc, avec une masse "
                  "et le sort soins légers."),
        "Magicien": ("Crée ma fiche via le tool fiche_perso_creer_rapide : "
                     "nom Miro, race Humain, classe Magicien, avec le "
                     "sort projectile magique."),
    }
    for p, texte in demandes.items():
        for essai in range(5):
            if PLAYER_TO_CHAR[p] in (snapshot(pid) or {}).get("pj", []):
                break
            rep = await tour_dm(sockets[p],
                                [w for n, w in sockets.items() if n != p],
                                p, texte, bilan)
            if rep is None and essai == 0:
                break
    snap = snapshot(pid)
    for p in PLAYERS:
        bilan.check(f"[setup] {PLAYER_TO_CHAR[p]} créé",
                    PLAYER_TO_CHAR[p] in snap["pj"],
                    f"pj={list(snap['pj'])}")
    bilan.sauver()
    for w in sockets.values():
        await w.close()


async def phase_explo():
    pid = lire_pid()
    bilan = Bilan.charger()
    socks = await _ouvrir(pid)
    rep = await tour_dm(socks["Brunhild"],
                        [w for n, w in socks.items() if n != "Brunhild"],
                        "Brunhild",
                        "On explore le donjon. Je pousse la porte et "
                        "découvre une salle au trésor gardée. Décris la "
                        "pièce.", bilan)
    rep = await tour_dm(socks["Rôdeur"],
                        [w for n, w in socks.items() if n != "Rôdeur"],
                        "Rôdeur",
                        "Je ramasse la clé de bronze et le sachet de 10 "
                        "gemmes posés sur le piédestal. (MJ : ajoute-les à "
                        "mon inventaire avec le tool inventaire_ramasser.)",
                        bilan)
    if "Aelin" in (snapshot(pid) or {}).get("pj", []):
        verifier_charge(bilan, "Aelin")
    bilan.sauver()
    for w in socks.values():
        await w.close()


async def _engager(pid, bilan, intro, ennemis, joueur):
    for msg in (intro,
                f"(MJ : appelle MAINTENANT le tool engager_combat avec "
                f"monstres=\"{ennemis}\") Les ennemis attaquent !",
                f"(OBLIGATOIRE : tool engager_combat, monstres=\"{ennemis}\") "
                "AU SECOURS, ils nous attaquent !"):
        snap = snapshot(pid)
        if snap["phase"] == "combat" and snap["monstres"]:
            return True
        socks = await _ouvrir(pid)
        await tour_dm(socks[joueur],
                      [w for n, w in socks.items() if n != joueur],
                      joueur, msg, bilan)
        for w in socks.values():
            await w.close()
    return snapshot(pid)["phase"] == "combat" and bool(snapshot(pid)["monstres"])


async def _jeu(pid: str, sockets: dict, courant: str, cible: str,
               bilan: Bilan):
    """Fait jouer le tour du `courant` (PJ OU monstre) via le WS adéquat.

    - PJ : envoie son action (arc → flèche consommée, clerc → soins, mage →
      missile, guerrière → hache) sur son propre socket.
    - Monstre : envoie l'attaque du monstre via le socket MJ (premier PJ).
    Retourne (joueur, action, ferme) ou None si on ne doit pas parler.
    """
    snap = snapshot(pid)
    joueur = CHAR_TO_PLAYER.get(courant)
    if joueur is not None:  # courant EST un PJ
        if _pj_a_terre(snap, courant):
            return joueur, f"{courant} est à terre — passe son tour.", \
                f"(MJ : tour_suivant_combat) {courant} est inconscient."
        action, ferme = "", ""
        if courant == "Aelin":
            action = (f"Aelin décoche une flèche sur {cible} ! (MJ : "
                      f"inventaire_consommer_munition munition=flèche sur "
                      f"Aelin puis lancer_attaque/lancer_degats/"
                      f"fiche_perso_infliger_degats sur {cible})")
            ferme = (f"(OBLIGATOIRE : inventaire_consommer_munition "
                     f"munition=flèche, lancer_attaque, lancer_degats, "
                     f"fiche_perso_infliger_degats sur {cible}, "
                     f"tour_suivant_combat) Aelin tire !")
        elif courant == "Thalia":
            blessé = _blesse_a_soigner(snap, frozenset())
            if blessé:
                action = (f"Thalia lance soins légers sur {blessé} ! (MJ : "
                          f"lancer_des 1d8+1 puis fiche_perso_soigner sur "
                          f"{blessé})")
                ferme = (f"(OBLIGATOIRE : lancer_des, fiche_perso_soigner "
                         f"nom={blessé}, tour_suivant_combat) Thalia soigne "
                         f"{blessé} !")
            else:
                action = "Thalia garde, prête sa masse."
                ferme = "(MJ : tour_suivant_combat) Thalia se met en garde."
        elif courant == "Miro":
            action = (f"Miro lance projectile magique sur {cible} (touche "
                      f"automatique). (MJ : lancer_degats puis "
                      f"fiche_perso_infliger_degats sur {cible})")
            ferme = (f"(OBLIGATOIRE : lancer_degats, "
                     f"fiche_perso_infliger_degats sur {cible}, "
                     f"tour_suivant_combat) Les projectiles frappent {cible} !")
        else:  # Brunhild
            action = (f"Brunhild frappe {cible} de sa grande hache ! (MJ : "
                      f"lancer_attaque puis lancer_degats puis "
                      f"fiche_perso_infliger_degats sur {cible})")
            ferme = (f"(OBLIGATOIRE : lancer_attaque, lancer_degats, "
                     f"fiche_perso_infliger_degats sur {cible}, "
                     f"tour_suivant_combat) Brunhild attaque {cible} !")
        return joueur, action, ferme
    # courant EST un monstre → on fait jouer son attaque via le MJ.
    victime = min(
        (n for n in snap["pj"]
         if snap["pj"][n]["pv"] and snap["pj"][n]["pv"] > 0),
        key=lambda n: snap["pj"][n]["pv"], default=None)
    action = (f"{courant} attaque {victime or 'le plus proche aventurier'} !")
    ferme = (f"(MJ : résous l'attaque de {courant} contre {victime} avec "
             f"lancer_attaque, lancer_degats, fiche_perso_infliger_degats "
             f"puis tour_suivant_combat)")
    return "Brunhild", action, ferme


async def _jour_combat(pid, bilan, titre, max_tours=12) -> set:
    print(f"\n=== ⚔️  {titre} ===")
    sockets = await _ouvrir(pid)
    detruits: set = set()
    vu = False
    echecs = 0
    for i in range(max_tours):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            if vu:
                print(f"  [combat] terminé (phase={snap['phase']})")
                break
            print(f"  [combat] PAS engagé (phase={snap['phase']})")
            break
        vu = True
        detruits |= {m for m, st in snap["monstres"].items()
                     if "Détruit" in st["conds"]}
        courant = str(snap.get("courant") or "")
        cible = _cible(snap)
        if cible is None:
            await tour_dm(sockets["Brunhild"],
                          [w for n, w in sockets.items() if n != "Brunhild"],
                          "Brunhild",
                          "Tous les ennemis sont à terre — termine le combat "
                          "(finir_combat).", bilan)
            continue
        res = await _jeu(pid, sockets, courant, cible, bilan)
        if res is None:
            continue
        joueur, action, ferme = res
        sender = sockets[joueur]
        autres = [w for n, w in sockets.items() if n != joueur]
        avant = snapshot(pid)
        rep = await tour_dm(sender, autres, joueur, action, bilan)
        if (rep is not None and not rep["trace"]
                and snapshot(pid).get("courant") == courant):
            print("    ↻ tour sans tool → relance ferme")
            rep = await tour_dm(sender, autres, joueur, ferme, bilan)
        if rep is None:
            echecs += 1
            if echecs >= 3:
                break
            continue
        echecs = 0
    for w in sockets.values():
        await w.close()
    return detruits


async def phase_c1():
    pid = lire_pid()
    bilan = Bilan.charger()
    ok = await _engager(
        pid, bilan,
        "Deux GOBELINS surgissent et nous attaquent, javelots levés ! "
        "Aelin vise le premier et décoche une flèche. Engage le combat !",
        "Gobelin, Gobelin", "Brunhild")
    detruits = await _jour_combat(pid, bilan, "Combat 1 : Gobelins", 8)
    bilan.check("[combat 1] gobelins détruits", len(detruits) >= 2,
                f"détruits={sorted(detruits)}")
    verifier_charge(bilan, "Aelin")
    bilan.sauver()


async def phase_c2():
    pid = lire_pid()
    bilan = Bilan.charger()
    await _engager(
        pid, bilan,
        "Un SQUELETTE et un ZOMBIE sortent des niches et attaquent ! Miro "
        "lance projectile magique, Thalia soignera. Engage le combat !",
        "Squelette, Zombie", "Miro")
    await _jour_combat(pid, bilan, "Combat 2 : Squelette + Zombie", 8)
    bilan.sauver()


async def phase_c3():
    pid = lire_pid()
    bilan = Bilan.charger()
    await _engager(
        pid, bilan,
        "Un ÉNORME OGRE bloque le seuil, massue prête ! Thalia engage et "
        "soignera les blessés. Engage le combat !", "Ogre", "Thalia")
    await _jour_combat(pid, bilan, "Combat 3 : Ogre", 8)
    sockets = await _ouvrir(pid)
    await tour_dm(sockets["Magicien"],
                  [w for n, w in sockets.items() if n != "Magicien"],
                  "Magicien",
                  "Je charge le coffre de l'ogre : 30 kg d'objets. (MJ : "
                  "inventaire_ajouter 30 kg de masse d'armes à ma fiche et "
                  "indique mon encombrement.)", bilan)
    for w in sockets.values():
        await w.close()
    verifier_charge(bilan, "Miro")
    bilan.sauver()


async def phase_refus():
    pid = lire_pid()
    bilan = Bilan.charger()
    if snapshot(pid)["phase"] == "combat":
        bilan.event("Phase refus : combat déjà en cours — ignoré.")
        bilan.sauver()
        return
    socks = await _ouvrir(pid)
    rep = await tour_dm(socks["Brunhild"],
                        [w for n, w in socks.items() if n != "Brunhild"],
                        "Brunhild",
                        "Un ARCHER GOBELIN SINISTRE jaillit des ombres et "
                        "nous vise ! Brunhild, engage le combat contre "
                        "lui !", bilan)
    ok = rep is not None and snapshot(pid)["phase"] != "combat" and (
        "⛔" in rep["text"] or "refusé" in rep["text"].lower()
        or "introuvable dans le bestiaire" in rep["text"].lower())
    bilan.check("[refus] « Archer gobelin » (hors bestiaire) refusé", ok,
                (rep["text"][:140] if rep else "aucune réponse"))
    for w in socks.values():
        await w.close()
    bilan.sauver()


async def phase_inv():
    pid = lire_pid()
    bilan = Bilan.charger()
    for nom in CHARS:
        verifier_charge(bilan, nom)
    c = charge_fiche("Aelin")
    fleche = next((i for i in c["inventaire"]
                   if i.get("nom", "").lower().startswith("fleche")), None)
    bilan.check("[inv Aelin] flèches suivies dans l'inventaire",
                fleche is not None and fleche.get("qte", 0) > 0,
                f"inventaire={c['inventaire']}")
    bilan.event(f"Inventaire Aelin : {c['inventaire']}")
    bilan.event(f"Charge Miro : {charge_fiche('Miro')}")
    bilan.sauver()
    print(f"=== Inventaire Aelin : {charge_fiche('Aelin')['inventaire']}")
    print(f"=== Charge Miro : {charge_fiche('Miro')}")
    print(f"=== Charge Thalia : {charge_fiche('Thalia')}")


async def phase_statut():
    pid = lire_pid()
    s = snapshot(pid)
    print(f"Partie {pid} — phase={s['phase']} tour={s['tour']} "
          f"courant={s['courant']}")
    for m, st in s["monstres"].items():
        print(f"  👹 {m}: {st['pv']}/{st['pv_max']} {st['conds']}")
    for p, st in s["pj"].items():
        cc = charge_fiche(p)
        print(f"  🛡️ {p}: {st['pv']}/{st['pv_max']} {st['conds']} "
              f"— charge {cc['poids_transporte']}kg/{cc['charge_max']}kg "
              f"({cc['etat_encumbrance']})")


async def phase_rapport():
    bilan = Bilan.charger()
    print(f"{len(bilan.oks)} vérifs OK, {len(bilan.fails)} échecs")
    for f in bilan.fails:
        print("  " + f)
    print("Événements :")
    for e in bilan.evenements:
        print("  ⚑ " + e)
    sys.exit(1 if bilan.fails else 0)


PHASES = {"setup": phase_setup, "explo": phase_explo, "c1": phase_c1,
          "c2": phase_c2, "c3": phase_c3, "refus": phase_refus,
          "inv": phase_inv, "statut": phase_statut, "rapport": phase_rapport}

if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    coro = fn()
    if asyncio.iscoroutine(coro):
        sys.exit(asyncio.run(coro) or 0)
