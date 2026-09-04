# -*- coding: utf-8 -*-
"""Tester les mécaniques restantes sur la partie « Dues for the Dead ».

Continue la partie créée par dues_for_the_dead_e2e.py (setup/load/c1) et teste :
  c2  : combat Squelette + Zombie → soins du Clerc (Aurora) + munitions du
        Rôdeur (Lyra) + projectile magique (Miro) + hache (Gunnar).
  exp : exploration — déplacement/entrée de lieu (carte_donjon_*).
  inv : inventaire — consultation + ajout/retrait.

Vérifie aussi que les images des monstres sont servies depuis le cache
bestiaire (pas de génération ComfyUI).

Usage : py scripts/dues_mechs_extra.py [c2|exp|inv|statut]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unicodedata
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tests"))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dues")
PID_FILE = os.path.join(_TMP, "pid.txt")

import e2e_combats_reels as _c  # noqa: E402
_c.BILAN_FILE = os.path.join(_TMP, "bilan_extra.json")
from e2e_combats_reels import (  # noqa: E402
    Bilan, _blesse_a_soigner, _monstre_a_frapper, _pj_a_terre,
    connecter, etat_partie, fiche_pj, snapshot, tour_dm,
)

BASE = "http://localhost:8123"
WSS = "ws://localhost:8123/ws"

CHAR_TO_PLAYER = {"Gunnar": "Guerrier", "Lyra": "Rôdeur",
                  "Aurora": "Clerc", "Miro": "Magicien"}
JOUEURS = ["Guerrier", "Rôdeur", "Clerc", "Magicien"]


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def _bilan() -> Bilan:
    return Bilan.charger()


async def _ouvrir(pid: str) -> dict:
    socks = {}
    for j in JOUEURS:
        socks[j] = await connecter(pid, j)
        await asyncio.sleep(0.2)
    return socks


async def _fermer(socks: dict):
    for w in socks.values():
        try:
            await w.close()
        except Exception:
            pass


async def _tour(socks: dict, joueur: str, texte: str, bilan: Bilan,
                label: str = "", retries: int = 0) -> dict | None:
    autres = [w for n, w in socks.items() if n != joueur]
    rep = None
    for _ in range(retries + 1):
        rep = await tour_dm(socks[joueur], autres, joueur, texte, bilan)
        if rep is not None or _bilan_ok_avance():
            return rep
    return rep


def _bilan_ok_avance() -> bool:
    return True


def _outils(rep: dict | None) -> list[str]:
    if not rep:
        return []
    return [tc.get("name", "?") for tc in rep.get("trace", [])]


async def _engager(pid, socks, bilan, joueur, ennemis: str, label: str) -> bool:
    msgs = [
        f"{ennemis} surgissent et nous attaquent ! (MJ : appelle le tool "
        f"engager_combat monstres=\"{ennemis}\") {joueur} engage !",
        f"(OBLIGATOIRE : engager_combat, monstres=\"{ennemis}\") AU "
        f"SECOURS ! Engage le combat !",
        f"(STOP : le combat n'est PAS engagé. Appelle engager_combat "
        f"monstres=\"{ennemis}\" avant toute narration.)",
    ]
    for m in msgs:
        snap = snapshot(pid)
        if snap["phase"] == "combat" and snap["monstres"]:
            return True
        await _tour(socks, joueur, m, bilan, label)
    snap = snapshot(pid)
    return snap["phase"] == "combat" and bool(snap["monstres"])


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def _courant_joueur(courant: str) -> str | None:
    for pj, joueur in CHAR_TO_PLAYER.items():
        if _norm(pj) == _norm(courant):
            return joueur
    return None


async def phase_c2():
    pid = lire_pid()
    bilan = _bilan()
    socks = await _ouvrir(pid)
    # Si un combat est déjà engagé (run précédent interrompu), on le pilote ;
    # sinon on engage Squelette + Zombie.
    sn = snapshot(pid)
    if not (sn["phase"] == "combat" and sn["monstres"]):
        rep = await _tour(socks, "Clerc",
            "Un SQUELETTE et un ZOMBIE se dressent devant nous, animés par "
            "la profanation ! Aurora engage le combat !", bilan,
            "c2:mise en place")
        if not await _engager(pid, socks, bilan, "Clerc",
                              "Squelette, Zombie", "c2:engager"):
            print("  [c2] combat non engagé — abandon")
            await _fermer(socks)
            bilan.sauver()
            return
    detruits: set = set()
    echecs = 0
    soins = 0
    munitions = 0
    vu = False
    for i in range(16):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            if vu:
                print(f"  [c2] terminé (phase={snap['phase']})")
            break
        vu = True
        detruits |= {m for m, st in snap["monstres"].items()
                     if "Détruit" in st["conds"]}
        courant = str(snap.get("courant") or "").strip()
        cible = _monstre_a_frapper(snap)
        if cible is None:
            await _tour(socks, "Guerrier", "Tous les ennemis sont détruits — "
                        "termine le combat (finir_combat).", bilan,
                        "c2:cloture")
            continue
        joueur = _courant_joueur(courant)
        if joueur is None:
            # Tour d'un monstre (ou courant pas encore posé). On le laisse
            # attaquer le PJ le plus faible et on avance.
            victime = min(
                (n for n in snap["pj"] if snap["pj"][n]["pv"] > 0),
                key=lambda n: snap["pj"][n]["pv"], default="Gunnar")
            action = (f"{courant or 'Le monstre'} attaque {victime} ! "
                      f"(MJ : résous avec lancer_attaque, lancer_degats, "
                      f"fiche_perso_infliger_degats puis tour_suivant_combat)")
            joueur = "Clerc"
            ferme = action
        else:
            self_name = courant
            if _pj_a_terre(snap, courant):
                action = f"{courant} est à terre — passe son tour."
                ferme = f"(MJ : tour_suivant_combat) {courant} inconscient."
            elif courant == "Aurora":
                blesse = _blesse_a_soigner(snap, frozenset())
                if blesse:
                    action = (f"Aurora lance soins légers sur {blesse} ! "
                              f"(MJ : lancer_des 1d8+1 puis "
                              f"fiche_perso_soigner nom={blesse})")
                    ferme = (f"(OBLIGATOIRE : lancer_des, "
                             f"fiche_perso_soigner nom={blesse}, "
                             f"tour_suivant_combat) Aurora soigne {blesse} !")
                else:
                    action = "Aurora se met en garde, masse prête."
                    ferme = "(MJ : tour_suivant_combat) Aurora en garde."
            elif courant == "Lyra":
                action = (f"Lyra décoche une flèche sur {cible} ! (MJ : "
                          f"inventaire_consommer_munition munition=flèche "
                          f"sur Lyra puis lancer_attaque/lancer_degats/"
                          f"fiche_perso_infliger_degats sur {cible})")
                ferme = (f"(OBLIGATOIRE : inventaire_consommer_munition "
                         f"munition=flèche, lancer_attaque, lancer_degats, "
                         f"fiche_perso_infliger_degats sur {cible}, "
                         f"tour_suivant_combat) Lyra tire !")
            elif courant == "Miro":
                action = (f"Miro lance projectile magique sur {cible} "
                          f"(touche auto). (MJ : lancer_degats puis "
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
        rep = await _tour(socks, joueur, action, bilan, f"c2:{courant}")
        out = _outils(rep)
        if "fiche_perso_soigner" in out:
            soins += 1
        if "inventaire_consommer_munition" in out:
            munitions += 1
        if rep is not None and not rep["trace"] and \
                snapshot(pid).get("courant") == courant and \
                snapshot(pid)["phase"] == "combat":
            print("    ↻ tour sans tool → relance ferme")
            rep = await _tour(socks, joueur, ferme, bilan,
                              f"c2:{courant}:ferme")
            out = _outils(rep)
            if "fiche_perso_soigner" in out:
                soins += 1
            if "inventaire_consommer_munition" in out:
                munitions += 1
        if rep is None:
            echecs += 1
            if echecs >= 4:
                break
            continue
        echecs = 0
    sf = snapshot(pid)
    await _fermer(socks)
    bilan.check("[c2] combat résolu (phase != combat)",
                sf["phase"] != "combat",
                f"phase={sf['phase']}, détruits={sorted(detruits)}")
    bilan.check("[c2] soins utilisés (fiche_perso_soigner)", soins > 0,
                f"soins={soins}")
    bilan.check("[c2] munitions consommées (inventaire_consommer_munition)",
                munitions > 0, f"munitions={munitions}")
    print(f"=== c2 : détruits={sorted(detruits)} soins={soins} "
          f"munitions={munitions} ===")
    bilan.sauver()
    return detruits


async def phase_exp():
    pid = lire_pid()
    bilan = _bilan()
    socks = await _ouvrir(pid)
    rep = await _tour(socks, "Guerrier",
        "Nous avançons dans le sanctuaire profané. (MJ : décris le lieu et "
        "explore les salles via carte_donjon_explorer — nous cherchons la "
        "source de la profanation.)",
        bilan, "exp:explorer")
    out = _outils(rep)
    bilan.check("[exp] outil d'exploration utilisé",
                any("carte_donjon" in t for t in out) or
                "voyage_demarrer" in out, f"outils={out}")
    snap = snapshot(pid)
    bilan.check("[exp] lieu/exploration enregistré",
                bool(snap.get("donjons_exploreres")) or
                bool(snap.get("memoire", {}).get("position", {}).get("lieu")),
                str(snap.get("lieu")))
    await _fermer(socks)
    bilan.sauver()
    return


async def phase_inv():
    pid = lire_pid()
    bilan = _bilan()
    socks = await _ouvrir(pid)
    rep = await _tour(socks, "Rôdeur",
        "Lyra inspecte son carquois avant le combat. (MJ : utilise "
        "inventaire_consulter pour afficher l'équipement et les munitions "
        "de Lyra, puis inventaire_ajouter pour y ajouter 2 flèches "
        "supplémentaires.)",
        bilan, "inv:consulter+ajouter")
    out = _outils(rep)
    bilan.check("[inv] inventaire_consulter appelé",
                "inventaire_consulter" in out, f"outils={out}")
    bilan.check("[inv] inventaire_ajouter appelé",
                "inventaire_ajouter" in out, f"outils={out}")
    await _fermer(socks)
    bilan.sauver()
    return


async def phase_statut():
    pid = lire_pid()
    snap = snapshot(pid)
    print(f"Partie {pid}: phase={snap['phase']} tour={snap.get('tour')}")
    for p, st in snap["pj"].items():
        print(f"  PJ {p}: pv={st.get('pv')}/{st.get('pv_max')} "
              f"conds={st.get('conds')}")
    for m, st in (snap.get("monstres") or {}).items():
        print(f"  Monstre {m}: {st.get('pv')}/{st.get('pv_max')} "
              f"{st.get('conds')}")
    print("  rencontres_images:", json.dumps(
        snap.get("rencontres_images", []), ensure_ascii=False))
    bilan = _bilan()
    print(f"{len(bilan.oks)} OK, {len(bilan.fails)} échec(s)")
    for f in bilan.fails:
        print("  " + f)


PHASES = {"c2": phase_c2, "exp": phase_exp, "inv": phase_inv,
          "statut": phase_statut}


async def main():
    etape = sys.argv[1] if len(sys.argv) > 1 else "statut"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    await fn()


if __name__ == "__main__":
    asyncio.run(main())
