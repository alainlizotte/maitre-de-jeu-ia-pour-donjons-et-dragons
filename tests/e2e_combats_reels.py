"""E2E réel : combats complets sur l'application démarrée (LLM Gemma vrai).

Joue une VRAIE partie multijoueur via WebSocket contre le serveur :8123 :
  Setup   : 4 joueurs (Groth barbare, Mélodie barde, Elara magicienne,
            Zarkon sorcier) créent leurs fiches par le chat.
  Combat 1: 2 Gobelins — attaques directes (grande hache), mort des ennemis.
  Combat 2: Squelette + Zombie — magie (projectile magique, mains brûlantes
            avec sauvegarde Réflexes), soins du barde (soins légers).
  Combat 3: Ogre — contre-attaques brutales, mort d'un PJ par étapes
            (Invalide → Mourant → Mort), pas de soin pour Zarkon.
  Combat 4: Rat géant — le PJ mort doit être EXCLU de l'initiative.

Chaque phase est REPRISABLE (l'état persiste côté serveur) :
  py tests/e2e_combats_reels.py setup   # partie + 4 fiches
  py tests/e2e_combats_reels.py c1      # combat gobelins
  py tests/e2e_combats_reels.py c2      # combat squelette + zombie
  py tests/e2e_combats_reels.py c3      # combat ogre (mort de Zarkon)
  py tests/e2e_combats_reels.py c4      # combat rat + jalons + nettoyage
  py tests/e2e_combats_reels.py statut  # état courant de la partie
  py tests/e2e_combats_reels.py rapport # rapport final (bilan cumulé)

Vérifications automatiques après CHAQUE tour :
  - narration DM reçue (pas de timeout/erreur), sans fuite de thinking ;
  - chaque fiche_perso_infliger_degats OK → PV de la cible recalculés
    exactement (monstres : -D ; PJ : max(pv-D, -10)) et montant indiqué ;
  - chaque fiche_perso_soigner OK → PV = min(pv_max, pv+S) et montant indiqué ;
  - chaque lancer_degats OK → les dégâts sont bien APPLIQUÉS (infliger ou
    baisse de PV effective — rattrapage serveur) ;
  - morts conformes : monstre ≤ 0 PV → « Détruit » ; PJ 0 → « Invalide »,
    < 0 → « Mourant », ≤ -10 → « Mort ».
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request

if sys.platform == "win32" and not getattr(sys.stdout, "_dnd35_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace")
    try:
        sys.stdout._dnd35_utf8 = True  # type: ignore[attr-defined]
        sys.stderr._dnd35_utf8 = True  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

import websockets  # noqa: E402

BASE = "http://localhost:8123"
WSS = "ws://localhost:8123/ws"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "server", "data")
TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_combats")
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
THINK_MARKERS = ("<|channel", "<channel|>", "<|think", "thought\n")

JOUEURS = ["Groth", "Mélodie", "Elara", "Zarkon"]

TOUR_TIMEOUT = 280  # un tour MJ (LLM + images éventuelles)


# --------------------------------------------------------------------------- #
#  Utilitaires disque / HTTP
# --------------------------------------------------------------------------- #
def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def etat_partie(pid: str) -> dict | None:
    path = os.path.join(DATA, f"partie_{pid}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fiche_pj(nom: str) -> dict | None:
    nf = unicodedata.normalize("NFKD", nom)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_",
                  "".join(c for c in nf if not unicodedata.combining(c))
                  ).strip("_").lower()
    path = os.path.join(DATA, "fiches", f"fiche_{slug}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


# --------------------------------------------------------------------------- #
#  Bilan persistant (cumulé entre les phases)
# --------------------------------------------------------------------------- #
class Bilan:
    def __init__(self):
        self.fails: list[str] = []
        self.oks: list[str] = []
        self.evenements: list[str] = []

    @staticmethod
    def charger() -> "Bilan":
        b = Bilan()
        try:
            with open(BILAN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            b.fails = list(data.get("fails", []))
            b.oks = list(data.get("oks", []))
            b.evenements = list(data.get("evenements", []))
        except FileNotFoundError:
            pass
        return b

    @staticmethod
    def neuf() -> "Bilan":
        """Repart d'un bilan vierge : efface l'ancien fichier (nouveau
        setup = nouveau scénario complet, on ne cumule pas les résidus
        des runs précédents/bugués)."""
        b = Bilan()
        try:
            if os.path.exists(BILAN_FILE):
                os.remove(BILAN_FILE)
        except OSError:
            pass
        return b

    def sauver(self):
        os.makedirs(TMP, exist_ok=True)
        with open(BILAN_FILE, "w", encoding="utf-8") as f:
            json.dump({"fails": self.fails, "oks": self.oks,
                       "evenements": self.evenements}, f,
                      ensure_ascii=False, indent=1)

    def check(self, label: str, cond: bool, detail: str = "") -> bool:
        line = ("✅" if cond else "❌") + f" {label}" + (
            f" — {detail}" if detail and not cond else "")
        print("    " + line)
        (self.oks if cond else self.fails).append(line)
        return cond

    def event(self, txt: str):
        print(f"    ⚑ {txt}")
        self.evenements.append(txt)


# --------------------------------------------------------------------------- #
#  Snapshots & invariants par tour
# --------------------------------------------------------------------------- #
def snapshot(pid: str) -> dict:
    etat = etat_partie(pid) or {}
    mons = {}
    for m in etat.get("monstres_combat") or []:
        mons[m.get("nom", "?")] = {
            "pv": m.get("pv"), "pv_max": m.get("pv_max"),
            "conds": list(m.get("conditions") or []),
        }
    pjs = {}
    for p in etat.get("pj") or []:
        f = fiche_pj(p.get("nom", "")) or {}
        pjs[p.get("nom", "?")] = {
            "pv": f.get("pv", p.get("pv")),
            "pv_max": f.get("pv_max", p.get("pv_max")),
            "conds": list(f.get("conditions") or []),
        }
    return {"phase": etat.get("phase"), "tour": etat.get("tour"),
            "courant": etat.get("courant_tour_pour"), "monstres": mons,
            "pj": pjs}


def _resoudre(cible: str, snap: dict) -> tuple[str, str | None]:
    cn = _norm(cible)
    for nom in snap["monstres"]:
        if _norm(nom) == cn:
            return nom, "monstre"
    for nom in snap["pj"]:
        if _norm(nom) == cn:
            return nom, "pj"
    for nom in snap["monstres"]:
        if len(cn) >= 4 and _norm(nom).startswith(cn):
            return nom, "monstre"
    for nom in snap["pj"]:
        if len(cn) >= 4 and _norm(nom).startswith(cn):
            return nom, "pj"
    return cible, None


def _par_genre(snap: dict, genre: str) -> dict:
    """Carte genre (monstre/pj) → section correspondante du snapshot.
    Le snapshot stocke les monstres sous la clé `monstres` (pluriel)."""
    return snap["monstres"] if genre == "monstre" else snap.get("pj", {})


def verifier_tour(avant: dict, apres: dict, trace: list, bilan: Bilan):
    """Application EXACTE des dégâts/soins + morts conformes + montants
    toujours indiqués. Les PV attendus sont SIMULÉS en rejouant la trace
    (plusieurs applications peuvent s'enchaîner dans un même tour), puis
    comparés à l'état final."""
    # PV attendus : on part de l'instant d'avant et on rejoue les opérations.
    attendus = {"monstre": {k: dict(v) for k, v in avant["monstres"].items()},
                "pj": {k: dict(v) for k, v in avant["pj"].items()}}
    touches: set[str] = set()

    def _attendu_apres(genre: str, cle: str):
        st = attendus[genre].get(cle)
        return st["pv"] if st else None

    for tc in trace:
        if not tc.get("ok"):
            continue
        nom_outil, args = tc.get("name"), tc.get("args") or {}
        texte = tc.get("text") or ""

        if nom_outil == "fiche_perso_infliger_degats":
            try:
                d = max(0, int(float(str(args.get("degats") or "0").strip())))
            except (TypeError, ValueError):
                d = 0
            cible = str(args.get("nom") or "")
            cle, genre = _resoudre(cible, apres)
            bilan.check(
                f"[infliger] montant indiqué ({cible}, {d} dg)",
                f"subit {d} dégâts" in texte and "PV" in texte, texte[:120])
            if genre is None:
                continue
            st = attendus[genre].get(cle)
            if st is None:
                continue
            touches.add(f"{genre}:{cle}")
            if genre == "monstre":
                st["pv"] = st["pv"] - d
            else:
                st["pv"] = max(st["pv"] - d, -10)
                if st["pv"] <= -10:
                    if "Mort" not in st["conds"]:
                        st["conds"].append("Mort")
                elif st["pv"] < 0:
                    st["conds"] = [c for c in st["conds"]
                                   if c not in ("Mort", "Mourant", "Invalide")]
                    st["conds"].append("Mourant")
                elif st["pv"] == 0:
                    st["conds"] = [c for c in st["conds"]
                                   if c not in ("Mort", "Mourant", "Invalide")]
                    st["conds"].append("Invalide")

        elif nom_outil == "fiche_perso_soigner":
            try:
                s = max(0, int(float(str(args.get("soin") or "0").strip())))
            except (TypeError, ValueError):
                s = 0
            cible = str(args.get("nom") or "")
            cle, genre = _resoudre(cible, apres)
            bilan.check(
                f"[soigner] montant indiqué ({cible}, +{s} PV)",
                f"récupère {s} PV" in texte and "PV" in texte, texte[:120])
            if genre != "pj":
                continue
            st = attendus["pj"].get(cle)
            if st is None:
                continue
            touches.add(f"pj:{cle}")
            st["pv"] = min(st["pv_max"], st["pv"] + s)
            if st["pv"] > 0:
                st["conds"] = [c for c in st["conds"]
                               if c not in ("Mourant", "Invalide")]

    # La simulation rejouée doit coïncider avec l'état final réel.
    for genre in ("monstre", "pj"):
        for cle in list(attendus[genre]):
            if f"{genre}:{cle}" not in touches:
                continue  # cible non touchée ce tour : hors périmètre
            st_ap = _par_genre(apres, genre).get(cle)
            if st_ap is None:
                continue  # purgée (finir_combat) : rien à comparer
            bilan.check(
                f"[application] PV finaux de {cle} = "
                f"{attendus[genre][cle]['pv']} (reçu {st_ap['pv']})",
                st_ap["pv"] == attendus[genre][cle]["pv"])

    # Tout lancer_degats réussi doit se traduire par une baisse effective.
    for tc in trace:
        if tc.get("name") != "lancer_degats" or not tc.get("ok"):
            continue
        cible = str((tc.get("args") or {}).get("cible") or "")
        cle, genre = _resoudre(cible, apres)
        if genre is None:
            continue
        st_av, st_ap = (_par_genre(avant, genre).get(cle),
                        _par_genre(apres, genre).get(cle))
        baisse = (st_av is not None and st_ap is not None
                  and st_ap["pv"] < st_av["pv"])
        bilan.check(
            f"[lancer_degats→application] dégâts sur {cible} appliqués",
            baisse or f"{genre}:{cle}" in touches)

    for nom, st in apres["monstres"].items():
        if st["pv"] is not None and st["pv"] <= 0:
            bilan.check(
                f"[mort monstre] {nom} ({st['pv']} PV) → Détruit",
                "Détruit" in st["conds"])
    for nom, st in apres["pj"].items():
        if st["pv"] is None:
            continue
        if st["pv"] <= -10:
            bilan.check(f"[mort PJ] {nom} ({st['pv']} PV) → Mort",
                        "Mort" in st["conds"])
        elif st["pv"] < 0:
            bilan.check(f"[mourant PJ] {nom} ({st['pv']} PV) → Mourant",
                        "Mourant" in st["conds"])
        elif st["pv"] == 0:
            bilan.check(f"[invalide PJ] {nom} (0 PV) → Invalide",
                        "Invalide" in st["conds"])


# --------------------------------------------------------------------------- #
#  Couche WebSocket + pilote
# --------------------------------------------------------------------------- #
async def connecter(pid: str, joueur: str):
    # ping_timeout=None : les longs tours MJ (>60 s sans trafic) ne doivent
    # pas faire mourir la connexion côté client.
    ws = await websockets.connect(f"{WSS}/{pid}", ping_interval=20,
                                  ping_timeout=None, close_timeout=5)
    _ = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    return ws


async def vider(ws):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.4)
        except asyncio.TimeoutError:
            return
        except Exception:
            return  # socket fermée pendant un long tour : sans gravité


def _est_fermee(ws) -> bool:
    """Compat websockets ancien (.closed) et nouveau (.state)."""
    closed = getattr(ws, "closed", None)
    if closed is not None:
        return bool(closed)
    state = getattr(ws, "state", None)
    return state is not None and getattr(state, "name", "OPEN") != "OPEN"


async def sante_sockets(pid: str, sockets: dict):
    """Remplace les connexions mortes (les tours de 2+ min sans trafic
    peuvent tuer le keepalive)."""
    for nom, ws in list(sockets.items()):
        if _est_fermee(ws):
            print(f"  [ws] reconnexion de {nom} (connexion perdue)")
            try:
                sockets[nom] = await connecter(pid, nom)
            except Exception as e:
                print(f"  [ws] reconnexion {nom} échouée : {e}")


async def tour_dm(sender, autres, joueur: str, texte: str, bilan: Bilan,
                  timeout: int = TOUR_TIMEOUT):
    print(f"\n>>> {joueur}: {texte[:110]}"
          + ("…" if len(texte) > 110 else ""))
    t0 = time.time()
    stream = ""
    await sender.send(json.dumps(
        {"type": "say", "player": joueur, "text": texte}))
    while True:
        restant = timeout - (time.time() - t0)
        if restant <= 0:
            bilan.check(f"[tour {joueur}] narration reçue", False,
                        f"timeout {timeout}s")
            return None
        try:
            raw = await asyncio.wait_for(sender.recv(), timeout=restant)
        except asyncio.TimeoutError:
            bilan.check(f"[tour {joueur}] narration reçue", False,
                        f"timeout {timeout}s (2)")
            return None
        except websockets.ConnectionClosed:
            bilan.check(f"[tour {joueur}] connexion WS stable", False,
                        "connexion fermée")
            return None
        msg = json.loads(raw)
        t = msg.get("type", "")
        if t == "delta":
            stream += msg.get("text", "")
        elif t == "sys" and msg.get("event") in ("turn_blocked", "error"):
            print(f"    [sys] {msg.get('event')}: "
                  f"{str(msg.get('detail'))[:100]}")
            if msg.get("event") == "turn_blocked":
                # Le message a été REFUSÉ : aucune narration ne viendra.
                # Petite grâce pour d'éventuels messages résiduels.
                await asyncio.sleep(3)
                try:
                    await asyncio.wait_for(sender.recv(), timeout=3)
                except asyncio.TimeoutError:
                    pass
                bilan.check(f"[tour {joueur}] accepté par le serveur",
                            False, str(msg.get("detail"))[:120])
                return None
        elif t == "dm":
            texte_dm = msg.get("text", "") or stream
            trace = msg.get("tool_calls_trace") or []
            outils = [tc.get("name", "?") for tc in trace]
            dt = time.time() - t0
            fuite = [m for m in THINK_MARKERS if m in texte_dm]
            bilan.check(
                f"[tour {joueur}] narration {len(texte_dm)} car. en "
                f"{dt:.0f}s, {len(outils)} tools",
                len(texte_dm) > 40 and not texte_dm.startswith("⚠️")
                and not fuite, f"outils={outils}")
            print(f"    outils: {outils}")
            for w in autres:
                await vider(w)
            return {"text": texte_dm, "trace": trace}
    return None


def _monstre_a_frapper(snap: dict) -> str | None:
    vivants = [(m, st) for m, st in snap["monstres"].items()
               if "Détruit" not in st["conds"]]
    if not vivants:
        return None
    return min(vivants, key=lambda x: x[1]["pv"] if x[1]["pv"] is not None
               else 9999)[0]


def _pj_a_terre(snap: dict, nom: str) -> bool:
    st = snap["pj"].get(nom)
    return bool(st) and (st["pv"] is None or st["pv"] <= 0
                         or "Mort" in st["conds"]
                         or "Mourant" in st["conds"])


def _blesse_a_soigner(snap: dict, exclure: set[str]) -> str | None:
    """PJ le plus blessé à soigner : inclut les PJ à terre (0 PV Invalide,
    PV négatifs Mourant — ce sont eux qui ont le plus besoin de soins),
    exclut seulement les morts (≤ -10 / « Mort »)."""
    pire, ratio_pire = None, 99.0
    for nom, st in snap["pj"].items():
        if nom in exclure or st["pv"] is None:
            continue
        if st["pv"] <= -10 or "Mort" in st["conds"]:
            continue
        ratio = st["pv"] / max(1, st["pv_max"])
        if ratio < 0.7 and ratio < ratio_pire:
            pire, ratio_pire = nom, ratio
    return pire


async def boucle_combat(pid, ws_map, bilan, titre, max_tours,
                        pas_soigner=frozenset(), focus_monstre=None):
    """Pilote le combat jusqu'à sa clôture. Renvoie l'ensemble des ennemis
    détruits (vide si le MJ a clos le combat sans tuer les monstres)."""
    print(f"\n=== ⚔️  {titre} ===")
    combat_vu = False
    detruits: set[str] = set()
    echecs_consécutifs = 0
    for i in range(max_tours):
        await sante_sockets(pid, ws_map)
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            if combat_vu:
                print(f"  [combat] terminé (phase={snap['phase']}) après "
                      f"{i} tours de boucle.")
                bilan.event(f"{titre} : ennemis détruits = "
                            f"{sorted(detruits)}")
                bilan.check(f"[{titre}] combat réellement engagé",
                            True)
                return detruits
            print(f"  [combat] PAS démarré (phase={snap['phase']}) — "
                  "il faut engager le combat.")
            bilan.check(f"[{titre}] combat réellement engagé", False,
                        f"phase={snap['phase']}")
            return detruits
        combat_vu = True
        detruits |= {m for m, st in snap["monstres"].items()
                     if "Détruit" in st["conds"]}
        courant = str(snap["courant"] or "")
        cible = _monstre_a_frapper(snap)
        if cible is None:
            sender = ws_map["Groth"]
            await tour_dm(sender, [w for n, w in ws_map.items()
                                   if w is not sender], "Groth",
                          "Tous les ennemis sont à terre — termine le "
                          "combat (finir_combat).", bilan)
            continue
        if courant in ws_map and not _pj_a_terre(snap, courant):
            blessé = _blesse_a_soigner(snap, pas_soigner)
            if courant == "Mélodie" and blessé:
                action = (f"Mélodie lance soins légers sur {blessé}.")
                ferme = (f"(MJ : résous le sort — lancer_des 1d8+1 puis "
                         f"fiche_perso_soigner nom=\"{blessé}\") "
                         f"Mélodie soigne {blessé} !")
            elif courant == "Groth":
                action = f"Groth frappe {cible} avec sa grande hache."
                ferme = (f"(MJ : résous MAINTENANT — lancer_attaque puis "
                         f"lancer_degats puis fiche_perso_infliger_degats "
                         f"sur {cible}) Groth attaque {cible} !")
            elif courant == "Elara":
                action = (f"Elara lance projectile magique sur {cible} "
                          "(touche automatique).")
                ferme = (f"(MJ : résous MAINTENANT — lancer_degats 2d4+2 "
                         f"puis fiche_perso_infliger_degats sur {cible}) "
                         f"Les projectiles magiques frappent {cible} !")
            elif courant == "Zarkon":
                action = (f"Zarkon lance mains brûlantes sur {cible} "
                          "(sauvegarde Réflexes).")
                ferme = (f"(MJ : résous MAINTENANT — lancer_degats 1d4 puis "
                         f"lancer_sauvegarde Réflexes pour {cible} puis "
                         f"fiche_perso_infliger_degats) Les flammes "
                         f"engloutissent {cible} !")
            else:
                action = f"{courant} attaque {cible} avec son arme."
                ferme = f"{courant} attaque {cible} ! (résous avec les tools)"
            joueur = courant
        elif courant in ws_map:
            action = f"{courant} est à terre — passe son tour."
            ferme = f"(MJ : tour_suivant_combat) {courant} est inconscient."
            joueur = courant
        else:
            if focus_monstre and not _pj_a_terre(snap, focus_monstre):
                victime = focus_monstre
            else:
                victime = min(
                    (n for n in snap["pj"]
                     if snap["pj"][n]["pv"] and snap["pj"][n]["pv"] > 0),
                    key=lambda n: snap["pj"][n]["pv"], default=None)
            action = (f"{courant} attaque "
                      f"{victime or 'le plus proche aventurier'} !")
            ferme = (f"(MJ : résous l'attaque de {courant} contre "
                     f"{victime} avec lancer_attaque, lancer_degats, "
                     "fiche_perso_infliger_degats puis tour_suivant_combat)")
            joueur = "Groth"
        sender = ws_map[joueur]
        autres = [w for n, w in ws_map.items() if n != joueur]
        avant = snapshot(pid)
        rep = await tour_dm(sender, autres, joueur, action, bilan)
        if (rep is not None and not rep["trace"]
                and snapshot(pid)["courant"] == courant):
            # Tour narré sans AUCUN tool et le tour n'a pas avancé →
            # une relance ferme, nommant les tools, sauve la plupart des
            # tours avec le petit modèle.
            print("    ↻ tour sans tool → relance ferme")
            rep = await tour_dm(sender, autres, joueur, ferme, bilan)
        if rep is None:
            echecs_consécutifs += 1
            if echecs_consécutifs >= 3:
                return detruits
            continue  # l'état a pu progresser malgré l'échec
        echecs_consécutifs = 0
        verifier_tour(avant, snapshot(pid), rep["trace"], bilan)
    bilan.check(
        f"[{titre}] soldé en ≤ {max_tours} tours de boucle",
        snapshot(pid)["phase"] != "combat",
        f"phase={snapshot(pid)['phase']}")
    return detruits


async def ouvrir_sockets(pid: str) -> dict:
    return {j: await connecter(pid, j) for j in JOUEURS}


async def assurer_engagement(pid, sockets, bilan, joueur, intro,
                             ennemis: str, retries: int = 2) -> bool:
    """Envoie le message d'engagement ; si le MJ n'a PAS appelé
    engager_combat (phase ≠ combat), réessaie en nommant l'outil exact."""
    messages = [
        intro,
        f"(MJ : appelle MAINTENANT le tool engager_combat avec "
        f"monstres=\"{ennemis}\") Les {ennemis.split(',')[0].strip()} "
        "attaquent ! Résous l'initiative officielle !",
        f"(OBLIGATOIRE : tool engager_combat, monstres=\"{ennemis}\" — "
        "aucune narration sans ce tool) AU SECOURS, les monstres nous "
        "attaquent pour de vrai !",
    ]
    for essai, msg in enumerate(messages[:retries + 1]):
        snap = snapshot(pid)
        if snap["phase"] == "combat" and snap["monstres"]:
            return True
        await tour_dm(sockets[joueur], [w for n, w in sockets.items()
                                        if n != joueur], joueur, msg, bilan)
    snap = snapshot(pid)
    return bilan.check("[engagement] combat démarré",
                       snap["phase"] == "combat" and bool(snap["monstres"]),
                       f"phase={snap['phase']} après {retries + 1} essais")


def nettoyer(pid: str):
    try:
        for p in (os.path.join(DATA, f"partie_{pid}.json"),
                  os.path.join(DATA, f"chat_{pid}.json")):
            if os.path.isfile(p):
                os.unlink(p)
        for j in JOUEURS:
            nf = unicodedata.normalize("NFKD", j)
            slug = re.sub(r"[^A-Za-z0-9_-]+", "_",
                          "".join(c for c in nf
                                  if not unicodedata.combining(c))
                          ).strip("_").lower()
            fp = os.path.join(DATA, "fiches", f"fiche_{slug}.json")
            if os.path.isfile(fp):
                os.unlink(fp)
        import glob
        for pp in glob.glob(os.path.join(DATA, "portraits_cache",
                                         f"{pid}_*.png")):
            os.unlink(pp)
        print(f"  [nettoyage] fichiers de {pid} supprimés")
    except OSError as e:
        print(f"  [nettoyage] partiel : {e}")


# --------------------------------------------------------------------------- #
#  Phases
# --------------------------------------------------------------------------- #
async def phase_setup():
    os.makedirs(TMP, exist_ok=True)
    bilan = Bilan.neuf()
    pid = _post("/api/parties", {"titre": "E2E Combats réels (4 PJ)"})[
        "partie_id"]
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie créée : {pid} ===")
    sockets = await ouvrir_sockets(pid)
    demandes = {
        "Groth": ("Bonjour MJ ! Crée ma fiche : Groth, humain, barbare "
                  "niveau 1, une grande hache en main. Nous partons "
                  "explorer une crypte maudite !"),
        "Mélodie": ("Salut ! Crée ma fiche : Mélodie, halfeling, barde "
                    "niveau 1, avec son luth, une épée courte et le sort "
                    "de soins légers."),
        "Elara": ("Crée ma fiche aussi : Elara, elfe, magicienne niveau 1, "
                  "son grimoire contient projectile magique."),
        "Zarkon": ("Et la mienne : Zarkon, humain, sorcier niveau 1, il "
                   "connaît projectile magique et mains brûlantes. En "
                   "route pour la crypte !"),
    }
    for j, texte in demandes.items():
        if j in snapshot(pid)["pj"]:
            print(f"  [setup] {j} déjà créé — ignoré")
            continue
        rep = await tour_dm(sockets[j], [w for n, w in sockets.items()
                                         if n != j], j, texte, bilan)
        if rep is None:
            break
    snap = snapshot(pid)
    for j in JOUEURS:
        bilan.check(f"[setup] fiche de {j} créée", j in snap["pj"],
                    f"pj={list(snap['pj'])}")
    bilan.sauver()
    for w in sockets.values():
        await w.close()


async def phase_c1():
    pid = lire_pid()
    bilan = Bilan.charger()
    detruits: set[str] = set()
    for tentative in range(3):
        if not (snapshot(pid)["phase"] == "combat"
                and snapshot(pid)["monstres"]):
            sockets = await ouvrir_sockets(pid)
            await assurer_engagement(
                pid, sockets, bilan, "Groth",
                "Nous Sommes attaqués par DES GOBELINS ! Groth engage le "
                "combat et frappe le gobelin le plus proche avec sa "
                "grande hache !", "Gobelin, Gobelin")
            for w in sockets.values():
                await w.close()
        sockets = await ouvrir_sockets(pid)
        detruits |= await boucle_combat(
            pid, sockets, bilan,
            f"Combat 1 : Gobelins (attaques directes) — vague {tentative + 1}",
            12)
        for w in sockets.values():
            await w.close()
        if len(detruits) >= 2:
            break
    bilan.check("[combat 1] les 2 gobelins détruits", len(detruits) >= 2,
                f"détruits={sorted(detruits)}")
    s = snapshot(pid)
    bilan.event(f"Combat 1 soldé : phase={s['phase']}, monstres="
                f"{ {k: v['pv'] for k, v in s['monstres'].items()} }")
    bilan.sauver()


async def phase_c2():
    pid = lire_pid()
    bilan = Bilan.charger()
    detruits: set[str] = set()
    for tentative in range(3):
        if not (snapshot(pid)["phase"] == "combat"
                and snapshot(pid)["monstres"]):
            sockets = await ouvrir_sockets(pid)
            await assurer_engagement(
                pid, sockets, bilan, "Elara",
                "Un SQUELETTE et un ZOMBIE sortent de leurs cercueils et "
                "attaquent ! Elara engage le combat", "Squelette, Zombie")
            for w in sockets.values():
                await w.close()
        sockets = await ouvrir_sockets(pid)
        detruits |= await boucle_combat(
            pid, sockets, bilan,
            f"Combat 2 : Squelette + Zombie (magie + soins) — "
            f"vague {tentative + 1}", 12)
        for w in sockets.values():
            await w.close()
        if len(detruits) >= 2:
            break
    bilan.check("[combat 2] squelette et zombie détruits",
                len(detruits) >= 2, f"détruits={sorted(detruits)}")
    s = snapshot(pid)
    bilan.event(f"Combat 2 soldé : phase={s['phase']}, pv="
                f"{ {k: v['pv'] for k, v in s['pj'].items()} }")
    bilan.sauver()


async def phase_c3():
    pid = lire_pid()
    bilan = Bilan.charger()
    if not (snapshot(pid)["phase"] == "combat" and snapshot(pid)["monstres"]):
        sockets = await ouvrir_sockets(pid)
        await assurer_engagement(
            pid, sockets, bilan, "Zarkon",
            "Un ÉNORME OGRE bloque le couloir ! L'ogre rugit et frappe "
            "Zarkon avec sa grande massue — engage le combat !", "Ogre")
        for w in sockets.values():
            await w.close()
    sockets = await ouvrir_sockets(pid)
    await boucle_combat(pid, sockets, bilan,
                        "Combat 3 : Ogre (mort d'un PJ)", 12,
                        pas_soigner={"Zarkon"}, focus_monstre="Zarkon")
    s = snapshot(pid)
    bilan.event(f"Combat 3 soldé : phase={s['phase']}, pj="
                f"{ {k: (v['pv'], v['conds']) for k, v in s['pj'].items()} }")
    bilan.sauver()
    for w in sockets.values():
        await w.close()


async def phase_c4():
    pid = lire_pid()
    bilan = Bilan.charger()
    if not (snapshot(pid)["phase"] == "combat" and snapshot(pid)["monstres"]):
        sockets = await ouvrir_sockets(pid)
        await assurer_engagement(
            pid, sockets, bilan, "Groth",
            "Un RAT GÉANT surgit d'un trou dans le mur ! Groth l'attaque "
            "à la grande hache — engage le combat !", "Rat géant")
        for w in sockets.values():
            await w.close()
        s0 = snapshot(pid)
    else:
        s0 = snapshot(pid)
    if s0["phase"] == "combat":
        etat = etat_partie(pid)
        noms_init = [e.get("nom") for e in etat.get("initiative") or []]
        zak = s0["pj"].get("Zarkon") or {}
        if "Mort" in zak.get("conds", []) or (zak.get("pv") or 0) <= -10:
            bilan.check("[combat 4] Zarkon (mort) exclu de l'initiative",
                        "Zarkon" not in noms_init,
                        f"initiative={noms_init}")
        else:
            bilan.event(f"Zarkon n'est pas mort au combat 3 "
                        f"(pv={zak.get('pv')}, conds={zak.get('conds')}) "
                        "— test d'exclusion adapté (non bloquant).")
        sockets = await ouvrir_sockets(pid)
        await boucle_combat(pid, sockets, bilan, "Combat 4 : Rat géant", 8)
        for w in sockets.values():
            await w.close()

    # ── Jalons finaux ───────────────────────────────────────────────────
    sf = snapshot(pid)
    print("\n=== 🧾 JALONS DU SCÉNARIO ===")
    bilan.check("Phase finale = exploration (tous combats clôturés)",
                sf["phase"] == "exploration", str(sf["phase"]))
    soins = [e for e in bilan.oks if e.startswith("✅ [soigner]")]
    bilan.check("Au moins un soin appliqué et indiqué pendant les combats",
                bool(soins))
    bilan.event(f"PV finaux : { {k: (v['pv'], v['conds']) for k, v in sf['pj'].items()} }")
    bilan.sauver()
    nettoyer(pid)
    print("\n" + "=" * 66)
    print(f"BILAN CUMULÉ — {len(bilan.oks)} vérifs OK, "
          f"{len(bilan.fails)} échecs")
    print("=" * 66)
    for f in bilan.fails:
        print("  " + f)
    print("Événements :")
    for e in bilan.evenements:
        print("  ⚑ " + e)
    print("\n" + ("✅ E2E COMBATS RÉELS RÉUSSI" if not bilan.fails
                  else f"❌ {len(bilan.fails)} ÉCHEC(S)"))
    return 1 if bilan.fails else 0


def cmd_statut():
    pid = lire_pid()
    s = snapshot(pid)
    print(f"Partie {pid}")
    print(f"  phase={s['phase']}  tour={s['tour']}  "
          f"courant={s['courant']}")
    for m, st in s["monstres"].items():
        print(f"  👹 {m}: {st['pv']}/{st['pv_max']} {st['conds']}")
    for p, st in s["pj"].items():
        print(f"  🛡️ {p}: {st['pv']}/{st['pv_max']} {st['conds']}")


def cmd_rapport():
    bilan = Bilan.charger()
    print(f"{len(bilan.oks)} vérifs OK, {len(bilan.fails)} échecs")
    for f in bilan.fails:
        print("  " + f)
    print("Événements :")
    for e in bilan.evenements:
        print("  ⚑ " + e)
    sys.exit(1 if bilan.fails else 0)


PHASES = {"setup": phase_setup, "c1": phase_c1, "c2": phase_c2,
          "c3": phase_c3, "c4": phase_c4, "statut": cmd_statut,
          "rapport": cmd_rapport}

if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    coro = fn()
    if asyncio.iscoroutine(coro):
        sys.exit(asyncio.run(coro) or 0)
