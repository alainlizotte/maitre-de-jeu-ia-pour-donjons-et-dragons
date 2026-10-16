"""E2E rÃ©el : correctifs 48b239fa sur Dues For The Dead (LLM 9B rÃ©el).

VÃ©rifie sur une PARTIE RÃ‰ELLE (serveur :8123, LLM llama.cpp + ComfyUI) :

1. **Ouverture HORS DU DONJON** : Â« DÃ©bute la partie Â» â†’ la scÃ¨ne se joue
   Ã€ PHLAN (porche du cimetiÃ¨re) â€” `donjon.id` RESTE VIDE (pas d'entrÃ©e
   forcÃ©e au tour 1, ancre `ouverture_hors_donjon`), aucun Ã©vÃ©nement
   Â« EntrÃ©e dans le donjon Â» dans `histoire`.
2. **EntrÃ©e explicite** : le joueur dÃ©cide de descendre â†’ `donjon.id`
   actif, groupe en salle (0,0).
3. **Fouille SUR PLACE** : Â« Je vais Ã  l'ouest Â» â†’ (-1,0) (fosse) ;
   Â« J'explore la fosse minutieusement Â» (SANS direction) â†’ la carte NE
   BOUGE PAS (courant reste (-1,0), aucun nouvel Ã©vÃ©nement
   Â« DÃ©placement Â») â€” le bug 48b239fa (deux tÃ©lÃ©portations induites).

Phases REPRISABLES (Ã©tat persistant cÃ´tÃ© serveur) :
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
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "data")
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
    """Compte de test : la crÃ©ation de partie est protÃ©gÃ©e (Bearer)."""
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
        "titre": "E2E 48b239fa â€” Dues For The Dead"},
        token=_compte_test())["partie_id"]
    open(PID_FILE, "w", encoding="utf-8").write(pid)
    print(f"=== Partie crÃ©Ã©e : {pid} ===")
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
    bilan.check("[setup] fiche Morgoth", r.text.startswith("âœ…"), r.text[:120])
    await _tool("etat_partie_patch", chemin="phase", valeur="exploration")
    await _tool("etat_partie_patch", chemin="pj.0.joueur", valeur=JOUEUR_ID)
    await _tool("fiche_perso_mettre_a_jour", nom=JOUEUR, champ="joueur",
                valeur=JOUEUR_ID)
    etat = etat_partie(pid) or {}
    bilan.check("[setup] quÃªte Dues attachÃ©e",
                str((etat.get("quete") or {}).get("titre") or "")
                .lower().startswith("dues"), "")
    # Point de dÃ©part Phlan posÃ© par le manifeste (`depart`).
    pos = (etat.get("positions_joueurs") or {}).get("groupe") or []
    bilan.check("[setup] groupe placÃ© Ã  Phlan (manifeste depart)",
                bool(pos), f"pos={pos}")
    bilan.check("[setup] AUCUN donjon actif avant l'ouverture",
                not ((etat.get("donjon") or {}).get("id")), "")
    # L'Ã©tape courante (trame du manifeste) est le PORCHE (prÃ©-donjon).
    from server.game.objectifs import objectifs_quete  # noqa: E402
    obj = objectifs_quete(etat, DATA_DIR, partie_id=pid)
    bilan.check("[setup] trame du manifeste chargÃ©e (5 Ã©tapes)",
                len(obj.get("objectifs") or []) == 5,
                f"n={len(obj.get('objectifs') or [])}")
    bilan.check("[setup] objectif courant = porche du cimetiÃ¨re",
                "porche" in _norm(obj.get("objectif_courant") or ""),
                f"objectif={obj.get('objectif_courant')!r}")
    bilan.sauver()


async def phase_ouverture():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    rep = await tour_dm(ws, [], JOUEUR, "DÃ©bute la partie", bilan)
    await ws.close()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    narr = " ".join(narrations(pid)[-1:])
    # 1. PAS d'entrÃ©e de donjon au tour d'ouverture (filet dÃ©sarmÃ© +
    #    ancre hors-donjon) â€” LE cÅ“ur du correctif 48b239fa-1.
    bilan.check("[ouverture] donjon NON initialisÃ© (pas d'entrÃ©e forcÃ©e)",
                not dj.get("id"), f"donjon.id={dj.get('id')!r}")
    evs = [str(e.get("evenement") or "") for e in histoire(pid)]
    bilan.check("[ouverture] aucun Ã©vÃ©nement Â« EntrÃ©e dans le donjon Â»",
                not any("EntrÃ©e dans le donjon" in e for e in evs),
                f"histoire={evs[:3]}")
    # 2. La scÃ¨ne se joue Ã  Phlan / au porche (module respectÃ©).
    n = _norm(narr)
    indices = [m for m in ("phlan", "porche", "cimetiere", "valhingen",
                           "cassyt", "kelemvor") if m in n]
    bilan.check("[ouverture] scÃ¨ne ancrÃ©e Ã  Phlan/porche (module)",
                len(indices) >= 2, f"indices={indices}")
    # 3. Le groupe est toujours placÃ© Ã€ PHLAN sur la carte du monde.
    pos = (etat.get("positions_joueurs") or {}).get("groupe") or []
    bilan.check("[ouverture] groupe toujours Ã  Phlan sur la carte",
                bool(pos), f"pos={pos}")
    # 4. Chronologie : le briefing de mission est prÃ©sent (pas expÃ©diÃ©).
    bilan.check("[ouverture] briefing de mission prÃ©sent",
                len(narr) > 600, f"len={len(narr)}")
    bilan.sauver()


async def phase_entree():
    pid = lire_pid()
    bilan = Bilan.charger()
    etat = etat_partie(pid) or {}
    if (etat.get("donjon") or {}).get("id"):
        bilan.check("[entrÃ©e] donjon actif (relance)", True, "dÃ©jÃ  actif")
        bilan.sauver()
        return
    ws = await connecter(pid, JOUEUR)
    rep = await tour_dm(ws, [], JOUEUR,
                        "Le briefing est terminÃ©. Nous descendons l'escalier "
                        "de pierre et nous entrons dans les catacombes.",
                        bilan)
    await ws.close()
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    bilan.check("[entrÃ©e] donjon actif aprÃ¨s dÃ©cision du joueur",
                bool(dj.get("id")), f"donjon.id={dj.get('id')!r}")
    c = list(dj.get("courant") or [0, 0])
    bilan.check("[entrÃ©e] groupe en salle d'entrÃ©e (0,0)",
                (int(c[0]), int(c[1])) == (0, 0), f"courant={c}")
    evs = [str(e.get("evenement") or "") for e in histoire(pid)]
    bilan.check("[entrÃ©e] Ã©vÃ©nement Â« EntrÃ©e dans le donjon Â» journalisÃ©",
                any("EntrÃ©e dans le donjon" in e for e in evs),
                f"histoire={evs[-3:]}")
    bilan.sauver()


async def phase_fouille():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    # DÃ©placement LÃ‰GITIME vers la fosse.
    await tour_dm(ws, [], JOUEUR, "Je vais Ã  l'ouest", bilan)
    c1 = courante(pid)
    bilan.check("[fouille] dÃ©placement ouest â†’ fosse (-1,0)",
                c1 == (-1, 0), f"courant={c1}")
    evs_avant = [e for e in histoire(pid)
                 if "DÃ©placement" in str(e.get("evenement") or "")]
    # ðŸ”§ LE test du correctif 48b239fa-2 : explorer SANS direction ne doit
    # PAS changer de salle.
    await tour_dm(ws, [], JOUEUR, "J'explore la fosse minutieusement", bilan)
    c2 = courante(pid)
    bilan.check("[fouille] Â« j'explore la fosse Â» â†’ PAS de dÃ©placement",
                c2 == c1, f"avant={c1} aprÃ¨s={c2}")
    evs_apres = [e for e in histoire(pid)
                 if "DÃ©placement" in str(e.get("evenement") or "")]
    bilan.check("[fouille] aucun Ã©vÃ©nement Â« DÃ©placement Â» ajoutÃ©",
                len(evs_apres) == len(evs_avant),
                f"{len(evs_avant)} â†’ {len(evs_apres)}")
    # Redondance (2e message identique, comme dans la partie rÃ©elle) :
    await tour_dm(ws, [], JOUEUR, "J'explore la fosse minutieusement", bilan)
    c3 = courante(pid)
    bilan.check("[fouille] 2e Â« j'explore Â» â†’ toujours sur place",
                c3 == c1, f"aprÃ¨s={c3}")
    await ws.close()
    bilan.sauver()


async def phase_nettoyage():
    """Termine un combat en cours (rencontre errante du module) par des
    attaques du PJ â€” permet de rejouer `fouille` proprement."""
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    for i in range(8):
        etat = etat_partie(pid) or {}
        if not (etat.get("monstres_combat") or []):
            bilan.check(f"[nettoyage] combat terminÃ© (round {i + 1})", True, "")
            break
        await tour_dm(ws, [], JOUEUR, "J'attaque le squelette", bilan)
    else:
        etat = etat_partie(pid) or {}
        reste = etat.get("monstres_combat") or []
        bilan.check("[nettoyage] combat terminÃ© (8 tours)", not reste,
                    f"monstres={[(m.get('nom'), m.get('pv')) for m in reste]}")
    await ws.close()
    bilan.sauver()


async def phase_retour_haut():
    """Remonte aux galleries hautes (le LLM a enchaÃ®nÃ© entrer+etage au tour
    d'entrÃ©e â€” message E2E ambigu Â« descendons l'escalier Â») pour rejouer
    le scÃ©nario exact de la partie 48b239fa (fosse Ã  l'ouest de l'entrÃ©e)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, JOUEUR)
    await vider(ws)
    await tour_dm(ws, [], JOUEUR,
                  "Je remonte l'escalier pour retourner aux galleries "
                  "hautes, Ã  l'entrÃ©e des catacombes", bilan)
    etat = etat_partie(pid) or {}
    dj = etat.get("donjon") or {}
    c = list(dj.get("courant") or [0, 0])
    bilan.check("[retour_haut] Ã©tage 0 (galleries hautes)",
                int(dj.get("etage") or 0) == 0, f"etage={dj.get('etage')}")
    bilan.check("[retour_haut] salle d'entrÃ©e (0,0)",
                (int(c[0]), int(c[1])) == (0, 0), f"courant={c}")
    await ws.close()
    bilan.sauver()


def phase_rapport():
    bilan = Bilan.charger()
    print(f"BILAN - {len(bilan.oks)} OK / {len(bilan.fails)} Ã©checs")
    for f_ in bilan.fails:
        print("  " + str(f_))
    for e in bilan.evenements:
        print("  ? " + str(e))
    sys.exit(1 if bilan.fails else 0)


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = {
        "setup": phase_setup, "ouverture": phase_ouverture,
        "entree": phase_entree, "retour_haut": phase_retour_haut,
        "fouille": phase_fouille,
        "nettoyage": phase_nettoyage, "rapport": phase_rapport,
    }.get(phase)
    if fn is None:
        print(f"Phase inconnue : {phase}")
        sys.exit(2)
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()


if __name__ == "__main__":
    main()
