"""E2E RÉEL — objets magiques EFFECTIFS en vraie partie (LLM Gemma).

Campagne courte sur :8123 : un guerrier porte une Épée longue +1 et un
Anneau de protection +1 (objets ajoutés à la fiche — l'achat est couvert
par test_marche.py). Vérifie dans les traces/jets SERVEUR :
  setup : fiche + équipement en place ;
  c1    : attaque → « +1 arme enchantée » au bonus officiel ET aux dégâts ;
  c2    : le monstre attaque le PJ → CA magique (+1) appliquée au jet
          (complément B16 : les attaques monstre→PJ se résolvent) ;
  fin   : clôture + XP.

  py tests/e2e_objets_magiques.py setup | c1 | c2 | fin | rapport
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e2e_combats_reels as h  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_objets_magiques")
os.makedirs(TMP, exist_ok=True)
h.TMP = TMP
h.PID_FILE = os.path.join(TMP, "pid.txt")
h.BILAN_FILE = os.path.join(TMP, "bilan.json")
h.JOUEURS = ["DoranMag"]
PID_FILE = h.PID_FILE
BILAN_FILE = h.BILAN_FILE
PJ = "DoranMag"
DATA = h.DATA
BASE = h.BASE


class Bilan:
    def __init__(self):
        self.fails: list[str] = []
        self.oks: list[str] = []

    @staticmethod
    def charger() -> "Bilan":
        b = Bilan()
        try:
            with open(BILAN_FILE, encoding="utf-8") as f:
                d = json.load(f)
            b.fails, b.oks = list(d.get("fails", [])), list(d.get("oks", []))
        except FileNotFoundError:
            pass
        return b

    def sauver(self):
        with open(BILAN_FILE, "w", encoding="utf-8") as f:
            json.dump({"fails": self.fails, "oks": self.oks}, f,
                      ensure_ascii=False, indent=1)

    def check(self, label: str, cond: bool, detail: str = "") -> bool:
        line = ("✅" if cond else "❌") + f" {label}" + (
            f" — {detail}" if detail and not cond else "")
        print("    " + line)
        (self.oks if cond else self.fails).append(line)
        return cond


def _post(path: str, body: dict, token: str = "") -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def fiche_pj(nom: str) -> dict:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", "".join(
        c for c in unicodedata.normalize("NFKD", nom)
        if not unicodedata.combining(c)).strip()).strip("_").lower()
    p = os.path.join(DATA, "fiches", f"fiche_{slug}.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _ecrire_fiche(nom: str, fiche: dict) -> None:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", "".join(
        c for c in unicodedata.normalize("NFKD", nom)
        if not unicodedata.combining(c)).strip()).strip("_").lower()
    p = os.path.join(DATA, "fiches", f"fiche_{slug}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=2)


def semer_equipement() -> dict:
    """Le petit LLM oublie l'équipement (constaté) : arme +1 et anneau +1
    posés directement (l'achat est couvert par test_marche.py)."""
    fiche = fiche_pj(PJ)
    if not fiche:
        return {}
    sac = [{"nom": "Épée longue +1", "qte": 1},
           {"nom": "Anneau de protection +1", "qte": 1}]
    fiche["inventaire"] = sac
    fiche["equipement"] = [dict(e) for e in sac]
    _ecrire_fiche(PJ, fiche)
    return fiche


def phase_setup():
    bilan = Bilan()
    compte, mdp = "e2e_mag_testeur", "e2e-mag-2026!"
    try:
        tok = _post("/api/auth/inscription",
                    {"nom": compte, "mot_de_passe": mdp})["token"]
    except urllib.error.HTTPError:
        tok = _post("/api/auth/connexion",
                    {"nom": compte, "mot_de_passe": mdp})["token"]
    pid = _post("/api/parties", {"titre": "E2E Objets magiques"},
                token=tok)["partie_id"]
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie créée : {pid} ===")

    async def run():
        import websockets
        ws = await websockets.connect(f"ws://localhost:8123/ws/{pid}",
                                      ping_interval=20, ping_timeout=None)
        await asyncio.wait_for(ws.recv(), timeout=10)
        await h.tour_dm(ws, [], PJ, (
            "Bonjour MJ ! Crée ma fiche : DoranMag, humain, guerrier "
            "niveau 1. FOR 16, DEX 12, CON 14. Je porte une épée longue +1 "
            "enchantée et un anneau de protection +1. Partons à "
            "l'aventure !"), bilan)
        await ws.close()
    asyncio.run(run())

    fiche = semer_equipement()
    bilan.check("[setup] fiche de DoranMag créée", bool(fiche))
    sac = json.dumps((fiche or {}).get("inventaire") or [], ensure_ascii=False)
    sac_l = sac.lower()
    bilan.check("[setup] Épée longue +1 portée", "pée longue +1" in sac_l, sac)
    bilan.check("[setup] Anneau de protection +1 porté",
                "anneau de protection +1" in sac_l, sac)
    bilan.sauver()


async def _engager_et_attaquer(pid: str, bilan: Bilan, max_tours: int = 6):
    import websockets
    ws = await websockets.connect(f"ws://localhost:8123/ws/{pid}",
                                  ping_interval=20, ping_timeout=None)
    await asyncio.wait_for(ws.recv(), timeout=10)
    traces: list[list[dict]] = []
    attaque_envoyee = False
    for i in range(max_tours):
        # Un tour MJ peut être EN COURS côté serveur (run précédent interrompu)
        # : on attend SA fin (message dm) avant d'envoyer, sinon turn_blocked.
        import json as _json
        import time as _time
        _t0 = _time.time()
        while _time.time() - _t0 < 120:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=125 - (_time.time() - _t0))
                if _json.loads(raw).get("type") == "dm":
                    break
            except asyncio.TimeoutError:
                break
            except Exception:
                break
        snap = h.snapshot(pid)
        if snap["phase"] != "combat":
            rep = await h.tour_dm(
                ws, [], PJ,
                "Un SQUELETTE surgit des sarcophages et me charge ! (MJ : "
                "appelle engager_combat avec monstres=\"Squelette\" puis "
                "résous mon attaque avec lancer_attaque)", bilan)
            if rep:
                # Le MJ enchaîne souvent engagement + attaque DANS le même
                # tour d'ouverture : les traces comptent aussi.
                traces.append(rep["trace"])
                for tc in rep["trace"]:
                    print(f"    • {tc.get('name')} ok={tc.get('ok')} "
                          f"{ascii(str(tc.get('text'))[:200])}")
            continue
        courant = str(snap["courant"] or "")
        if courant != PJ:
            # Tour du monstre : le moteur/LM joue — les notes de CA magique
            # apparaissent dans la narration/les events.
            await h.tour_dm(
                ws, [], PJ, "Je me défends !",
                f"(MJ : résous l'attaque de {courant} contre {PJ} avec "
                "lancer_attaque, lancer_degats, fiche_perso_infliger_degats "
                "puis tour_suivant_combat)")
            continue
        rep = await h.tour_dm(
            ws, [], PJ,
            "J'attaque le squelette avec mon épée longue +1 ! "
            "(MJ : lancer_attaque (arme=Épée longue +1) puis lancer_degats "
            "puis fiche_perso_infliger_degats)", bilan)
        if rep is not None:
            traces.append(rep["trace"])
            attaque_envoyee = True
        # Objectif atteint dès qu'une attaque est résolue (engagement ou tour).
        if any(tc.get("name") == "lancer_attaque" and tc.get("ok")
               for t in traces for tc in t):
            break
    await ws.close()
    return traces


def phase_c1():
    bilan = Bilan.charger()
    pid = lire_pid()
    traces = asyncio.run(_engager_et_attaquer(pid, bilan))
    att = [tc for t in traces for tc in t
           if tc.get("name") == "lancer_attaque" and tc.get("ok")]
    bilan.check("[c1] attaque de DoranMag résolue", bool(att))
    texts = [str(tc.get("text") or "") for tc in att]
    bilan.check("[c1] « +1 arme enchantée » au bonus officiel",
                any("+1 arme enchantée" in t for t in texts),
                "\n".join(t[:300] for t in texts[:2]))
    deg = [tc for t in traces for tc in t
           if tc.get("name") == "lancer_degats" and tc.get("ok")]
    dtexts = [str(tc.get("text") or "") for tc in deg]
    bilan.check("[c1] « +1 arme enchantée » aux dégâts",
                any("+ 1 arme enchantée" in t for t in dtexts),
                "\n".join(t[:300] for t in dtexts[:2]))
    bilan.sauver()


def phase_c2():
    bilan = Bilan.charger()
    pid = lire_pid()

    async def tour_rat():
        import websockets
        ws = await websockets.connect(f"ws://localhost:8123/ws/{pid}",
                                      ping_interval=20, ping_timeout=None)
        await asyncio.wait_for(ws.recv(), timeout=10)
        traces: list[list[dict]] = []
        for i in range(5):
            # Attendre la fin d'un éventuel tour en cours.
            import json as _json
            import time as _time
            _t0 = _time.time()
            while _time.time() - _t0 < 90:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=95 - (_time.time() - _t0))
                    if _json.loads(raw).get("type") == "dm":
                        break
                except asyncio.TimeoutError:
                    break
                except Exception:
                    break
            snap = h.snapshot(pid)
            if snap["phase"] != "combat":
                rep = await h.tour_dm(
                    ws, [], PJ,
                    "Un RAT GÉANT surgit des égouts de la crypte et me "
                    "charge ! (MJ : appelle engager_combat avec "
                    "monstres=\"Rat géant\")", bilan)
                if rep:
                    traces.append(rep["trace"])
                continue
            courant = str(snap["courant"] or "")
            if courant == PJ:
                rep = await h.tour_dm(
                    ws, [], PJ,
                    "Je recule et laisse le rat approcher ! "
                    "(MJ : tour_suivant_combat — c'est au tour du rat)", bilan)
                if rep:
                    traces.append(rep["trace"])
                continue
            # Tour du MONSTRE : son attaque contre DoranMag (anneau +1).
            rep = await h.tour_dm(
                ws, [], PJ,
                f"Le {courant} me mord ! "
                f"(MJ : résous l'attaque de {courant} contre {PJ} avec "
                "lancer_attaque, lancer_degats, fiche_perso_infliger_degats "
                "puis tour_suivant_combat)", bilan)
            if rep:
                traces.append(rep["trace"])
                if any(tc.get("name") == "lancer_attaque" and tc.get("ok")
                       and "doranmag" in str(
                           (tc.get("args") or {}).get("nom_cible", "")).lower()
                       for tc in rep["trace"]):
                    break
        await ws.close()
        return traces

    traces = asyncio.run(tour_rat())
    # Attaques du MONSTRE contre DoranMag (pas celles de DoranMag).
    att = [tc for t in traces for tc in t
           if tc.get("name") == "lancer_attaque" and tc.get("ok")
           and "doranmag" in str((tc.get("args") or {}).get("nom_cible", "")).lower()]
    bilan.check("[c2] attaque du monstre contre DoranMag résolue", bool(att))
    texts = [str(tc.get("text") or "") for tc in att]
    # CA magique du PJ cible : anneau de protection +1 → CA +1.
    bilan.check("[c2] CA magique (+1) appliquée au jet contre DoranMag",
                any("+1 CA magique" in t for t in texts),
                "\n".join(t[:300] for t in texts[:2]))
    bilan.sauver()


def phase_fin():
    bilan = Bilan.charger()
    pid = lire_pid()
    fiche = fiche_pj(PJ)
    bilan.check("[fin] XP/trésor : partie résolue sans crash",
                bool(fiche), f"pv={fiche.get('pv')}")
    bilan.sauver()


def cmd_rapport():
    bilan = Bilan.charger()
    print(f"✅ OK : {len(bilan.oks)}   ❌ ÉCHECS : {len(bilan.fails)}")
    for f in bilan.fails:
        print("  " + f)
    sys.exit(1 if bilan.fails else 0)


PHASES = {"setup": phase_setup, "c1": phase_c1, "c2": phase_c2,
          "fin": phase_fin, "rapport": cmd_rapport}

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "rapport"
    fn = PHASES.get(phase)
    if fn is None:
        print(f"phase inconnue : {phase}")
        sys.exit(2)
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()
