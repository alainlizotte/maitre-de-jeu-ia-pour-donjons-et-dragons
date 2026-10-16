"""E2E réel : correctifs 48b239fa sur Dues For The Dead (LLM 9B réel).

Vérifie sur une PARTIE RÉELLE (serveur :8123, LLM llama.cpp + ComfyUI) :

1. **Ouverture HORS DU DONJON** : « Débute la partie » → la scène se joue
   À PHLAN (briefing au porche du cimetière) — `donjon.id` RESTE VIDE (pas
   d'entrée forcée au tour 1, ancre `ouverture_hors_donjon` + verrou
   serveur sur `carte_donjon_entrer`), aucun événement « Entrée dans le
   donjon » dans `histoire`.
2. **Entrée explicite** : le joueur décide de descendre → `donjon.id`
   actif, groupe en salle (0,0).
3. **Fouille SUR PLACE** : « Je vais à l'ouest » → (-1,0) (fosse) ;
   « J'explore la fosse minutieusement » (SANS direction) → la carte NE
   BOUGE PAS (courant reste (-1,0), aucun nouvel événement
   « Déplacement ») — le bug 48b239fa (deux téléportations induites).

Phases REPRISABLES (état persistant côté serveur) :
  py tests/e2e_correctifs_48b239fa.py setup|ouverture|entree|retour_haut|fouille|nettoyage|rapport
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_combats_reels as _c  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_48b239fa")
os.makedirs(TMP, exist_ok=True)
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.BILAN_FILE = BILAN_FILE

from e2e_combats_reels import (  # noqa: E402
    Bilan, connecter, etat_partie, tour_dm, vider,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BASE = "http://localhost:8123"
DATA_DIR = os.path.join(REPO, "server", "data")
JOUEUR = "Morgoth"
JOUEUR_ID = "morgoth"

_QUEST = {
    "titre": "Dues For The Dead",
    "pitch": ("Un jeune clerc sollicite l'aide des PJ pour purifier un "
              "ancien sanctuaire profane par des morts-vivants. Aventure "
              "d'initiation courte et efficace."),
    "source": ("[divers_dues_for_the_dead_ch01] /data/scenarios/Divers/"
               "Dues For The Dead/dues for the dead-01-partie.pdf"),
}


def _post(path: str, body: dict, token: str = "") -> dict:
    import urllib.request
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _compte_test() -> str:
    """Compte de test : la création de partie est protégée (Bearer)."""
    import urllib.error
    compte, mdp = "e2e_48b_testeur", "e2e-48b-2026!"
    try:
        return _post("/api/auth/inscription",
                     {"nom": compte, "mot_de_passe": mdp})["token"]
    except urllib.error.HTTPError:
        return _post("/api/auth/connexion",
                     {"nom": compte, "mot_de_passe": mdp})["token"]


def lire_pid() -> str:
    if os.path.exists(PID_FILE):
        return open(PID_FILE, encoding="utf-8").read().strip()
    return ""


def chat_log(pid: str) -> list[dict]:
    p = os.path.join(DATA_DIR, f"chat_{pid}.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return []


def histoire(pid: str) -> list[dict]:
    return (etat_partie(pid) or {}).get("histoire") or []


def courante(pid: str) -> tuple[int, int]:
    dj = (etat_partie(pid) or {}).get("donjon") or {}
    c = list(dj.get("courant") or [0, 0])
    return (int(c[0]), int(c[1]))


def narrations(pid: str) -> list[str]:
    return [str(m.get("content") or "") for m in chat_log(pid)
            if m.get("role") == "assistant"]


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(ch for ch in n if not unicodedata.combining(ch))


# --------------------------------------------------------------------------- #
async def phase_setup():
    bilan = Bilan.neuf()
    for fp in glob.glob(os.path.join(DATA_DIR, "fiches", "fiche_morgoth.json")):
        try:
            os.unlink(fp)
        except OSError:
            pass
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    pid = _post("/api/parties", {
        "titre": "E2E 48b239fa — Dues For The Dead"},
        token=_compte_test())["partie_id"]
    open(PID_FILE, "w", encoding="utf-8").write(pid)
    print(f"=== Partie créée : {pid} ===")
    _post(f"/api/parties/{pid}/quest", _QUEST)
    from server.tools.base import ToolContext, invoke_tool  # noqa: E402
    from server.tools.registry import discover_tools       # noqa: E402
    tools = discover_tools()
    ctx = ToolContext(partie_id=pid, joueur="e2e_48b", data_dir=DATA_DIR)

    async def _tool(nom_outil, **args):
        return await invoke_tool(tools[nom_outil], ctx, args)

    r = await _tool("fiche_perso_creer_rapide", nom=JOUEUR, race="Demi-orc",
                    classe="Barbare", niveau=1, joueur=JOUEUR_ID,
                    carac_texte="FOR 14, DEX 16, CON 18, INT 12, SAG 13, "
                                "CHA 11")
    bilan.check("[setup] fiche Morgoth", r.text.startswith("✅"), r.text[:120])
    await _tool("etat_partie_patch", chemin="phase", valeur="exploration")
    await _tool("etat_partie_patch", chemin="pj.0.joueur", valeur=JOUEUR_ID)
    await _tool("fiche_perso_mettre_a_jour", nom=JOUEUR, champ="joueur",
                valeur=JOUEUR_ID)
    etat = etat_partie(pid) or {}
    bilan.check("[setup] quête Dues attachée",
                str((etat.get("quete") or {}).get("titre") or "")
                .lower().startswith("dues"), "")
    # Point de départ Phlan posé par le manifeste (`depart`).
    pos = (etat.get("positions_joueurs") or {}).get("groupe") or []
    bilan.check("[setup] groupe placé à Phlan (manifeste depart)",
                bool(pos), f"pos={pos}")
    bilan.check("[setup] AUCUN donjon actif avant l'ouverture",
                not ((etat.get("donjon") or {}).get("id")), "")
    # L'étape courante (trame du manifeste) est le PORCHE (pré-donjon).
    from server.game.objectifs import objectifs_quete  # noqa: E402
    obj = objectifs_quete(etat, DATA_DIR, partie_id=pid)
    bilan.check("[setup] trame du manifeste chargée (5 étapes)",
                len(obj.get("objectifs") or []) == 5,
                f"n={len(obj.get('objectifs') or [])}")
    bilan.check("[setup] objectif courant = porche du cimetière",
                "porche" in _norm(obj.get("objectif_courant") or ""),
                f"objectif={obj.get('objectif_courant')!r}")
    bilan.sauver()


async def phase_ouverture():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    rep = await tour_dm(ws, [], JOUEUR, "Débute la partie", bilan)
    await ws.close()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    narr = " ".join(narrations(pid)[-1:])
    # 1. PAS d'entrée de donjon au tour d'ouverture (filet désarmé +
    #    ancre hors-donjon + verrou serveur) — cœur du correctif 48b239fa-1.
    bilan.check("[ouverture] donjon NON initialisé (pas d'entrée forcée)",
                not dj.get("id"), f"donjon.id={dj.get('id')!r}")
    evs = [str(e.get("evenement") or "") for e in histoire(pid)]
    bilan.check("[ouverture] aucun événement « Entrée dans le donjon »",
                not any("Entrée dans le donjon" in e for e in evs),
                f"histoire={evs[:3]}")
    # 2. La scène se joue au porche / au briefing (module respecté).
    n = _norm(narr)
    indices = [m for m in ("phlan", "porche", "cimetiere", "valhingen",
                           "cassyt", "kelemvor", "yovir", "glandon",
                           "catacombes", "briefing")
               if m in n]
    bilan.check("[ouverture] scène ancrée au briefing hors donjon (module)",
                len(indices) >= 2, f"indices={indices}")
    # 3. Le groupe est toujours placé À PHLAN sur la carte du monde.
    pos = (etat.get("positions_joueurs") or {}).get("groupe") or []
    bilan.check("[ouverture] groupe toujours à Phlan sur la carte",
                bool(pos), f"pos={pos}")
    # 4. Chronologie : le briefing de mission est présent (pas expédié).
    bilan.check("[ouverture] briefing de mission présent",
                len(narr) > 600, f"len={len(narr)}")
    bilan.sauver()


async def phase_entree():
    pid = lire_pid()
    bilan = Bilan.charger()
    etat = etat_partie(pid) or {}
    if (etat.get("donjon") or {}).get("id"):
        bilan.check("[entrée] donjon actif (relance)", True, "déjà actif")
        bilan.sauver()
        return
    ws = await connecter(pid, JOUEUR)
    await tour_dm(ws, [], JOUEUR,
                  "Le briefing est terminé. Nous descendons l'escalier de "
                  "pierre et nous entrons dans les catacombes.",
                  bilan)
    await ws.close()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    bilan.check("[entrée] donjon actif après décision du joueur",
                bool(dj.get("id")), f"donjon.id={dj.get('id')!r}")
    c = list(dj.get("courant") or [0, 0])
    bilan.check("[entrée] groupe en salle d'entrée (0,0)",
                (int(c[0]), int(c[1])) == (0, 0), f"courant={c}")
    evs = [str(e.get("evenement") or "") for e in histoire(pid)]
    bilan.check("[entrée] événement « Entrée dans le donjon » journalisé",
                any("Entrée dans le donjon" in e for e in evs),
                f"histoire={evs[-3:]}")
    bilan.sauver()


async def phase_retour_haut():
    """Remonte aux galleries hautes (le LLM a parfois enchaîné
    entrer+etage au tour d'entrée — message E2E ambigu) pour rejouer le
    scénario exact de la partie 48b239fa (fosse à l'ouest de l'entrée)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    if int(dj.get("etage") or 0) == 0:
        bilan.check("[retour_haut] déjà à l'étage 0", True, "")
        bilan.sauver()
        return
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    await tour_dm(ws, [], JOUEUR,
                  "Je remonte l'escalier pour retourner aux galleries "
                  "hautes, à l'entrée des catacombes", bilan)
    await ws.close()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    c = list(dj.get("courant") or [0, 0])
    bilan.check("[retour_haut] étage 0 (galleries hautes)",
                int(dj.get("etage") or 0) == 0, f"etage={dj.get('etage')}")
    bilan.check("[retour_haut] salle d'entrée (0,0)",
                (int(c[0]), int(c[1])) == (0, 0), f"courant={c}")
    bilan.sauver()


async def phase_fouille():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    etat = etat_partie(pid) or {}
    c0 = courante(pid)
    if c0 == (0, 0):
        # Déplacement LÉGITIME vers la fosse.
        await tour_dm(ws, [], JOUEUR, "Je vais à l'ouest", bilan)
        c1 = courante(pid)
        bilan.check("[fouille] déplacement ouest → fosse (-1,0)",
                    c1 == (-1, 0), f"courant={c1}")
    else:
        c1 = c0
        bilan.check("[fouille] reprise depuis la fosse (relance)",
                    c1 == (-1, 0), f"courant={c1}")
    evs_avant = [e for e in histoire(pid)
                 if "Déplacement" in str(e.get("evenement") or "")]
    # 🔧 LE test du correctif 48b239fa-2 : explorer SANS direction ne doit
    # PAS changer de salle.
    await tour_dm(ws, [], JOUEUR, "J'explore la fosse minutieusement", bilan)
    c2 = courante(pid)
    bilan.check("[fouille] « j'explore la fosse » → PAS de déplacement",
                c2 == c1, f"avant={c1} après={c2}")
    evs_apres = [e for e in histoire(pid)
                 if "Déplacement" in str(e.get("evenement") or "")]
    bilan.check("[fouille] aucun événement « Déplacement » ajouté",
                len(evs_apres) == len(evs_avant),
                f"{len(evs_avant)} → {len(evs_apres)}")
    # Redondance (2e message identique, comme dans la partie réelle) :
    await tour_dm(ws, [], JOUEUR, "J'explore la fosse minutieusement", bilan)
    c3 = courante(pid)
    bilan.check("[fouille] 2e « j'explore » → toujours sur place",
                c3 == c1, f"après={c3}")
    await ws.close()
    bilan.sauver()


async def phase_nettoyage():
    """Termine un combat en cours (rencontre errante du module) par des
    attaques du PJ — permet de rejouer `fouille` proprement."""
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    for i in range(8):
        etat = etat_partie(pid) or {}
        if not (etat.get("monstres_combat") or []):
            bilan.check(f"[nettoyage] combat terminé (round {i + 1})", True,
                        "")
            break
        await tour_dm(ws, [], JOUEUR, "J'attaque le squelette", bilan)
    else:
        etat = etat_partie(pid) or {}
        reste = etat.get("monstres_combat") or []
        bilan.check("[nettoyage] combat terminé (8 tours)", not reste,
                    f"monstres={[(m.get('nom'), m.get('pv')) for m in reste]}")
    await ws.close()
    bilan.sauver()


def phase_rapport():
    bilan = Bilan.charger()
    print(f"BILAN - {len(bilan.oks)} OK / {len(bilan.fails)} échecs")
    for f_ in bilan.fails:
        print("  " + str(f_))
    for e in bilan.evenements:
        print("  ? " + str(e))
    sys.exit(1 if bilan.fails else 0)


PHASES = {
    "setup": phase_setup, "ouverture": phase_ouverture,
    "entree": phase_entree, "retour_haut": phase_retour_haut,
    "fouille": phase_fouille, "nettoyage": phase_nettoyage,
    "rapport": phase_rapport,
}

if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()
