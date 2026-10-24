# -*- coding: utf-8 -*-
"""Partie COMPLÈTE réelle : « Dues for the Dead » sur le serveur :8123, 3 PJ.

Joue le scénario au complet via le LLM (Qwen3.5-9B), avec des décisions de
joueurs RÉALISTES et fidèles au module (manifeste `Dues for the Dead`) :

  Brandt Pierrecœur — Nain Guerrier 1   (joueur « bruno »)  — tank
  Aurora Vaillance  — Humaine Prêtresse 1 de Kelemvor (« alys ») — soins/réprouver
  Flick Pasdenom    — Halfelin Roublard 1   (joueur « pip »)   — pièges/discrétion

Route canonique : porche → galleries hautes (fosse, banquets, premières
familles, escaliers branlants) → Bas Tombeaux (obélisque, Talking Dead,
plafond de squelettes, bas tombeaux aux goules, Sedrair II, sentinelle) →
Rorreth Monforoth (fuite scriptée par cercle de téléportation) → rapport au
Doomguide.

Phases REPRISABLES (curseur persistant) :
  py scripts/dues_full_run.py setup
  py scripts/dues_full_run.py p1     # porche → galleries hautes (2 combats)
  py scripts/dues_full_run.py p2     # Bas Tombeaux (4 combats)
  py scripts/dues_full_run.py p3     # finale + rapport au Doomguide
  py scripts/dues_full_run.py statut # état courant
  py scripts/dues_full_run.py rapport# bilan + problèmes

Transcript : TEMP/dues_full/transcript.json — Problèmes : problems.json.
Chaque action joueur part SANS nudge tool ; si le MJ ne résout rien
(aucun tool, état inchangé), UN nudge de récupération est envoyé et le
problème est JOURNALISÉ (métrique de qualité du MJ).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time

if sys.platform == "win32" and not getattr(sys.stdout, "_dnd35_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace")
    try:
        sys.stdout._dnd35_utf8 = True  # type: ignore[attr-defined]
        sys.stderr._dnd35_utf8 = True  # type: ignore[attr-defined]
    except Exception:                                        # pragma: no cover
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tests"))

import e2e_combats_reels as _c  # noqa: E402
_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dues_full")
os.makedirs(_TMP, exist_ok=True)
PID_FILE = os.path.join(_TMP, "pid.txt")
CURSOR_FILE = os.path.join(_TMP, "cursor.json")
TRANSCRIPT_FILE = os.path.join(_TMP, "transcript.json")
PROBLEMS_FILE = os.path.join(_TMP, "problems.json")
_c.BILAN_FILE = os.path.join(_TMP, "bilan.json")

from e2e_combats_reels import (  # noqa: E402
    Bilan, _pj_a_terre, connecter, etat_partie, fiche_pj, sante_sockets,
    tour_dm, vider,
)
import websockets  # noqa: E402

BASE = "http://localhost:8123"

# --------------------------------------------------------------------------- #
#  La table : 3 PJ / 3 joueurs
# --------------------------------------------------------------------------- #
PJS = {
    "Brandt": {"joueur": "bruno", "race": "Nain", "classe": "Guerrier",
               "carac": "FOR 16, DEX 12, CON 16, INT 10, SAG 12, CHA 8"},
    "Aurora": {"joueur": "alys", "race": "Humain", "classe": "Clerc",
               "carac": "FOR 10, DEX 12, CON 14, INT 10, SAG 16, CHA 14"},
    "Flick":  {"joueur": "pip", "race": "Halfelin", "classe": "Roublard",
               "carac": "FOR 8, DEX 17, CON 12, INT 14, SAG 10, CHA 12"},
}
JOUEURS = list(dict.fromkeys(p["joueur"] for p in PJS.values()))
NON_MORTS = ("squelette", "zombie", "goule", "mort-vivant")

QUETE = {
    "titre": "Dues For The Dead",
    "pitch": "Un jeune clerc sollicite l'aide des PJ pour purifier un "
             "ancien sanctuaire profane par des morts-vivants. Aventure "
             "d'initiation courte et efficace.",
    "source": ("[divers_dues_for_the_dead_ch01] /data/scenarios/Divers/"
               "Dues For The Dead/dues for the dead-01-partie.pdf"),
}


# --------------------------------------------------------------------------- #
#  I/O disque
# --------------------------------------------------------------------------- #
def _post(path: str, body: dict, token: str = "") -> dict:
    import urllib.request
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _compte() -> str:
    import urllib.error
    compte, mdp = "e2e_dues_full", "dues-full-2026!"
    try:
        return _post("/api/auth/inscription",
                     {"nom": compte, "mot_de_passe": mdp})["token"]
    except urllib.error.HTTPError:
        return _post("/api/auth/connexion",
                     {"nom": compte, "mot_de_passe": mdp})["token"]


def lire_pid() -> str:
    with open(PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def append_transcript(entry: dict) -> None:
    tr: list = []
    if os.path.isfile(TRANSCRIPT_FILE):
        with open(TRANSCRIPT_FILE, encoding="utf-8") as f:
            tr = json.load(f)
    tr.append(entry)
    with open(TRANSCRIPT_FILE, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1)


def noter_probleme(type_: str, detail: str) -> None:
    pr: list = []
    if os.path.isfile(PROBLEMS_FILE):
        with open(PROBLEMS_FILE, encoding="utf-8") as f:
            pr = json.load(f)
    pr.append({"ts": time.strftime("%H:%M:%S"), "type": type_,
               "detail": detail[:400]})
    with open(PROBLEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(pr, f, ensure_ascii=False, indent=1)
    print(f"    ⚠️ PROBLÈME [{type_}] {detail[:160]}")


def lire_cursor() -> dict:
    if os.path.isfile(CURSOR_FILE):
        with open(CURSOR_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"phase": "", "idx": 0}


def ecrire_cursor(phase: str, idx: int) -> None:
    with open(CURSOR_FILE, "w", encoding="utf-8") as f:
        json.dump({"phase": phase, "idx": idx}, f)


def snapshot(pid: str) -> dict:
    st = etat_partie(pid) or {}
    pjs = {}
    for p in (st.get("pj") or []):
        pjs[str(p.get("nom"))] = {
            "pv": p.get("pv"), "pv_max": p.get("pv_max"),
            "conds": [str(c) for c in (p.get("conditions") or [])],
        }
    monstres = {}
    for m in (st.get("monstres_combat") or []):
        monstres[str(m.get("nom"))] = {
            "pv": m.get("pv"), "pv_max": m.get("pv_max"),
            "conds": [str(c) for c in (m.get("conditions") or [])],
        }
    return {"phase": st.get("phase"), "pj": pjs, "monstres": monstres,
            "courant": str(st.get("courant_tour_pour") or ""),
            "tour": st.get("tour"), "donjon": st.get("donjon") or {},
            "histoire": [str(e.get("evenement") or "")
                         for e in (st.get("histoire") or [])]}


# --------------------------------------------------------------------------- #
#  Tours joueurs
# --------------------------------------------------------------------------- #
async def _ouvrir(pid: str) -> dict:
    socks = {}
    for j in JOUEURS:
        socks[j] = await connecter(pid, j)
        await asyncio.sleep(0.2)
    return socks


async def _fermer(socks: dict) -> None:
    for w in socks.values():
        try:
            await w.close()
        except Exception:
            pass


class PartieTerminee(Exception):
    """🛡️ (run shadows #3) : levée dès que la partie passe en game_over —
    la run s'arrête PROPREMENT (bilan sauvé) au lieu de balancer 8 étapes
    zombies contre un garde qui répond 212 caractères en 0 s."""


async def _tour(socks: dict, joueur: str, texte: str, bilan: Bilan,
                label: str = "") -> dict | None:
    autres = [w for n, w in socks.items() if n != joueur]
    rep = None
    try:
        rep = await tour_dm(socks[joueur], autres, joueur, texte, bilan)
    except Exception as e_ws:
        # 🛡️ (runs longues) : les tours LLM de 2+ min tuent parfois la
        # connexion (keepalive 1011) — le crash remontait jusqu'au pilote
        # et avortait la run entière. Reconnexion + UN retry ; si le retry
        # échoue, le tour est journalisé « tour_perdu » (comme un timeout).
        print(f"  [ws] {joueur} : connexion morte pendant le tour "
              f"({type(e_ws).__name__}) — reconnexion + retry")
        import tests.e2e_combats_reels as _e2e
        socks[joueur] = await _e2e.connecter(lire_pid(), joueur)
        autres = [w for n, w in socks.items() if n != joueur]
        try:
            rep = await tour_dm(socks[joueur], autres, joueur, texte, bilan)
        except Exception as e_ws2:
            print(f"  [ws] retry échoué : {e_ws2}")
            rep = None
    snap = snapshot(lire_pid()) if os.path.isfile(PID_FILE) else {}
    # 🛡️ (run shadows #3) : game_over → on ne poursuit PAS la run.
    if snap.get("phase") == "game_over" or snap.get("game_over"):
        raise PartieTerminee(
            f"partie en game_over (détection sur « {label} »)")
    outils = [tc.get("name", "?") for tc in (rep or {}).get("trace", [])]
    append_transcript({
        "phase": label, "joueur": joueur, "input": texte,
        "dm_text": (rep or {}).get("text", ""), "outils": outils,
        "snap": snap,
    })
    return rep


async def action_pj(pid: str, socks: dict, bilan: Bilan, pj: str,
                    texte: str, label: str, nudge: str = "") -> dict | None:
    """Action RÉALISTE du joueur ; si le MJ ne résout rien (aucun tool,
    état inchangé), UN nudge de récupération, journalisé en problème."""
    avant = snapshot(pid)
    rep = await _tour(socks, PJS[pj]["joueur"], texte, bilan, label)
    if rep is None:
        noter_probleme("tour_perdu", f"{pj} : aucune réponse MJ ({label})")
        return None
    outils = [tc.get("name", "?") for tc in rep.get("trace", [])]
    apres = snapshot(pid)
    mecanique = bool(outils) and not all(
        o in ("etat_partie_get", "ajouter_evenement_histoire",
              "set_derniere_narration", "memoire_evenement")
        for o in outils)
    if mecanique or json.dumps(apres, sort_keys=True) != json.dumps(
            avant, sort_keys=True):
        return rep
    if not nudge:
        return rep
    noter_probleme("nudge_requis",
                   f"{pj} : « {texte[:90]}… » resté sans effet mécanique "
                   f"(outils={outils})")
    return await _tour(socks, PJS[pj]["joueur"], f"{texte}\n({nudge})",
                       bilan, label + ":nudge")


# --------------------------------------------------------------------------- #
#  Boucle de combat (rotation réaliste par PJ)
# --------------------------------------------------------------------------- #
def _action_combat(pj: str, snap: dict, cible: str | None,
                   tourne: dict) -> tuple[str, str]:
    """(texte, nudge) réaliste selon la situation."""
    if cible is None:
        return ("Tous les ennemis sont détruits — nous nous rassurons.",
                "(MJ : clôture le combat avec finir_combat si nécessaire.)")
    blesses = [(n, st) for n, st in snap["pj"].items()
               if st["pv"] is not None and 0 < st["pv"]
               < 0.4 * max(1, st["pv_max"] or 1)
               and not _pj_a_terre(snap, n)]
    if pj == "Brandt":
        return (f"Brandt frappe {cible} de sa hache d'arme !",
                "(MJ : lancer_attaque, lancer_degats, "
                "fiche_perso_infliger_degats puis tour_suivant_combat)")
    if pj == "Flick":
        # 🔧 run 0aef5a12 : l'arc est tombé à court de flèches en plein
        # combat — corps à corps par défaut, l'arc en complément.
        return (f"Flick frappe {cible} de son épée courte.",
                "(MJ : lancer_attaque, lancer_degats, "
                "fiche_perso_infliger_degats puis tour_suivant_combat)")
    # Aurora — prêtresse de Kelemvor
    if blesses:
        n, _ = blesses[0]
        return (f"Aurora invoque un soin léger sur {n} !",
                f"(MJ : lancer_des 1d8+1 puis fiche_perso_soigner nom={n}, "
                f"tour_suivant_combat)")
    undead = any(m in cible.lower() for m in NON_MORTS)
    if undead and not tourne.get("ok"):
        tourne["ok"] = True
        return ("Aurora lève haut le symbole de Kelemvor : « RETOURNEZ "
                "dans vos tombes, serviteurs de la mort ! » (réprouver "
                "les morts-vivants)",
                "(MJ : résous le réprouver des morts-vivants (jet de "
                "réprouver CHA) puis tour_suivant_combat)")
    return (f"Aurora frappe {cible} avec sa masse.",
            "(MJ : lancer_attaque, lancer_degats, "
            "fiche_perso_infliger_degats puis tour_suivant_combat)")


async def jour_combat(pid: str, socks: dict, bilan: Bilan, titre: str,
                      max_rounds: int = 16) -> set:
    print(f"\n=== ⚔️  {titre} ===")
    detruits: set = set()
    vu = False
    tourne = {}
    # 🏃 Retraite tactique (runs gobs/sea/hurl) : 3 rounds consecutifs sans
    # AUCUN degat inflige aux ennemis = enlisement -> le groupe bat en
    # retraite (retraite_combat serveur) au lieu de mourir sur place.
    _pv_ennemis_avant = None
    _rounds_sans_degats = 0
    for _ in range(max_rounds):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            if vu:
                print(f"  [combat] clôturé (phase={snap['phase']})")
                bilan.event(f"{titre} : détruits={sorted(detruits)}")
            return detruits
        vu = True
        detruits |= {m for m, st in snap["monstres"].items()
                     if "Détruit" in st["conds"]}
        # 🏃 Suivi de progression : PV ennemis totaux vs round précédent.
        _pv_tot = sum(
            (st["pv"] or 0) for m, st in snap["monstres"].items()
            if "Détruit" not in st["conds"])
        if _pv_tot == _pv_ennemis_avant:
            _rounds_sans_degats += 1
        else:
            _rounds_sans_degats = 0
        _pv_ennemis_avant = _pv_tot
        if _rounds_sans_degats >= 3:
            print("  [retraite] aucun dégât infligé depuis 3 rounds — "
                  "le groupe bat en retraite.")
            noter_probleme(
                "enlisement_retraite",
                f"{titre} : 3 rounds sans dégât sur les ennemis — "
                "retraite tactique du groupe.")
            try:
                from server.tools.base import ToolContext as _TC, invoke_tool
                from server.tools.registry import discover_tools
                _tools = discover_tools()
                _ctx_r = _TC(partie_id=pid, joueur="e2e", data_dir=_c.DATA)
                _r_ret = await invoke_tool(
                    _tools["retraite_combat"], _ctx_r, {})
                print(f"    [retraite] {_r_ret.text[:80]}")
            except Exception as _e_ret:
                print(f"    [retraite] échec : {_e_ret}")
            bilan.event(f"{titre} : RETRAITE (enlisement)")
            return detruits
        vivants = [m for m, st in snap["monstres"].items()
                   if "Détruit" not in st["conds"]]
        courant = snap["courant"]
        if not vivants:
            await action_pj(pid, socks, bilan, "Brandt",
                            "Le silence retombe — vérifions nos blessures.",
                            titre, "(MJ : clôture le combat.)")
            continue
        pj_courant = next((p for p in PJS if p.lower()
                           in courant.lower() or courant.lower()
                           in p.lower()), None)
        if pj_courant is None:
            # Tour de MONSTRE : le serveur boucle peut-être seul — on
            # attend, puis on relance UNE fois avant de noter un problème.
            await asyncio.sleep(10)
            if snapshot(pid)["courant"] == courant:
                await asyncio.sleep(10)
            if snapshot(pid)["courant"] == courant:
                noter_probleme(
                    "tour_monstre_bloque",
                    f"{titre} : au tour de « {courant} », aucune rotation "
                    f"automatique après 20 s")
                try:
                    await socks[JOUEURS[0]].send(json.dumps({
                        "type": "say", "player": JOUEURS[0],
                        "text": f"({courant} agit — MJ : résous son "
                                f"attaque puis tour_suivant_combat.)"}))
                    await asyncio.sleep(60)
                except Exception:
                    pass
            continue
        if _pj_a_terre(snap, pj_courant):
            await action_pj(pid, socks, bilan, pj_courant,
                            f"{pj_courant} est à terre, inconscient.",
                            titre, "(MJ : tour_suivant_combat.)")
            continue
        cible = min(
            (m for m, st in snap["monstres"].items()
             if "Détruit" not in st["conds"]),
            key=lambda m: snap["monstres"][m]["pv"] or 0, default=None)
        texte, nudge = _action_combat(pj_courant, snap, cible, tourne)
        await action_pj(pid, socks, bilan, pj_courant, texte, titre, nudge)
        await sante_sockets(pid, socks)
    return detruits


# --------------------------------------------------------------------------- #
#  Moteur de phases (curseur persistant)
# --------------------------------------------------------------------------- #
async def jouer_phase(pid: str, socks: dict, bilan: Bilan, phase: str,
                      steps: list[dict]) -> None:
    cur = lire_cursor()
    idx = cur["idx"] if cur["phase"] == phase else 0
    if idx == 0:
        ecrire_cursor(phase, 0)
    while idx < len(steps):
        ecrire_cursor(phase, idx)
        st = steps[idx]
        snap = snapshot(pid)
        if snap["phase"] == "combat":
            await jour_combat(pid, socks, bilan, f"{phase}·salle")
            continue                      # re-vérifie l'étape courante
        print(f"\n--- [{phase} {idx + 1}/{len(steps)}] {st['label']} ---")
        if "vers" in st:
            # Navigation DYNAMIQUE : calcule la direction depuis la salle
            # réelle à chaque tour (1 déplacement/tour, garde serveur).
            await _naviguer(pid, socks, bilan, phase, st["vers"])
        elif "etage" in st:
            await action_pj(
                pid, socks, bilan, st.get("pj", "Brandt"),
                st.get("texte", "Nous prenons l'escalier, prudence."),
                f"{phase}.{idx + 1}",
                f"(MJ : carte_donjon_etage avec direction=\"{st['etage']}"
                f"\".)")
        else:
            await action_pj(pid, socks, bilan, st["pj"], st["texte"],
                            f"{phase}.{idx + 1}", st.get("nudge", ""))
        idx += 1
    # Dernier combat éventuel de la phase
    if snapshot(pid)["phase"] == "combat":
        await jour_combat(pid, socks, bilan, f"{phase}·fin")
    ecrire_cursor(phase, len(steps))


_CARDINALE = {"nord": (0, -1), "sud": (0, 1), "est": (1, 0),
              "ouest": (-1, 0)}


async def _naviguer(pid: str, socks: dict, bilan: Bilan, phase: str,
                    vers: list) -> None:
    """Déplace le groupe 1 salle/tour jusqu'au waypoint (directions
    recalculées depuis la position RÉELLE — plus de texte absolu figé)."""
    cible = (int(vers[0]), int(vers[1]))
    for _ in range(6):
        snap = snapshot(pid)
        cur = list(snap["donjon"].get("courant") or [0, 0])
        cur = (int(cur[0]), int(cur[1]))
        if cur == cible:
            print(f"  [nav] waypoint {cible} atteint")
            return
        dx, dy = cible[0] - cur[0], cible[1] - cur[1]
        if abs(dx) >= abs(dy) and dx != 0:
            d = "est" if dx > 0 else "ouest"
        else:
            d = "nord" if dy < 0 else "sud"
        await action_pj(pid, socks, bilan, "Brandt",
                        f"Avançons vers le {d}.", f"{phase}.nav",
                        f"(MJ : carte_donjon_explorer, direction {d}.)")


# --------------------------------------------------------------------------- #
#  Phases
# --------------------------------------------------------------------------- #
async def phase_setup():
    for f in (PID_FILE, CURSOR_FILE, TRANSCRIPT_FILE, PROBLEMS_FILE,
              _c.BILAN_FILE):
        if os.path.isfile(f):
            os.remove(f)
    import re as _re
    import unicodedata as _ud
    for pj in PJS:
        nf = _ud.normalize("NFKD", pj)
        slug = _re.sub(r"[^a-z0-9_-]+", "_",
                       "".join(c for c in nf
                               if not _ud.combining(c)).lower()).strip("_")
        fp = os.path.join(_c.DATA, "fiches", f"fiche_{slug}.json")
        if os.path.isfile(fp):
            os.unlink(fp)
    bilan = Bilan()
    token = _compte()
    pid = _post("/api/parties",
                {"titre": "Dues for the Dead — partie complète 3 PJ"},
                token=token)["partie_id"]
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie : {pid} ===")
    _post(f"/api/parties/{pid}/quest", QUETE, token=token)
    # Fiches côté hôte (même data_dir que le conteneur).
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    tools = discover_tools()
    ctx = ToolContext(partie_id=pid, joueur="e2e", data_dir=_c.DATA)
    for i, (nom, cfg) in enumerate(PJS.items()):
        r = await invoke_tool(tools["fiche_perso_creer_rapide"], ctx, {
            "nom": nom, "race": cfg["race"], "classe": cfg["classe"],
            "niveau": 1, "joueur": cfg["joueur"],
            "carac_texte": cfg["carac"]})
        bilan.check(f"[setup] fiche {nom}", r.text.startswith("✅"),
                    r.text[:140])
        await invoke_tool(tools["etat_partie_patch"], ctx,
                          {"chemin": f"pj.{i}.joueur",
                           "valeur": cfg["joueur"]})
    await invoke_tool(tools["etat_partie_patch"], ctx,
                      {"chemin": "phase", "valeur": "exploration"})
    # 📜 Aurora prépare ses sorts de clerc du jour (le run 0aef5a12 a
    # montré qu'une prêtresse SANS préparation est impuissante : le serveur
    # refuse incanter_sort — « Soins légers non accessible »).
    r = await invoke_tool(tools["preparer_sorts"], ctx, {
        "nom_personnage": "Aurora",
        "preparations_json": json.dumps({
            "Lumière": 1, "Résistance": 1, "Soin mineur": 1,
            "Soins légers": 2, "Bénédiction": 1})})
    bilan.check("[setup] sorts d'Aurora préparés", "✅" in r.text,
                r.text[:140])
    # 🏹 Flick : carquois plein (le run 0aef5a12 est resté à court de
    # flèches en plein combat — suivi de munitions serveur).
    r = await invoke_tool(tools["inventaire_ajouter"], ctx, {
        "nom": "Flick", "objet": "flèche", "quantite": 20})
    bilan.check("[setup] flèches de Flick", "✅" in r.text, r.text[:140])
    snap = snapshot(pid)
    bilan.check("[setup] 3 PJ dans la partie",
                set(snap["pj"]) == set(PJS), f"pj={list(snap['pj'])}")
    bilan.check("[setup] groupe placé (depart manifeste)",
                bool((snapshot(pid)["donjon"].get("id") is None)),
                "")
    bilan.sauver()
    print(f"=== PJ : {list(snap['pj'])} — quête DFTD attachée ===")


STEPS_P1 = [
    {"pj": "Brandt", "label": "acceptation de la mission",
     "texte": "Doomguide, nous acceptons la mission. Dites-nous tout : "
              "quand les morts ont-ils commencé à rôder, et que savez-vous "
              "de la profanation ?"},
    {"pj": "Flick", "label": "descente dans les catacombes",
     "texte": "Cassyt, guidez-nous. Nous descendons dans les catacombes "
              "pour voir cela de nos propres yeux."},
    {"pj": "Aurora", "label": "enquête avec Cassyt (0,0)",
     "texte": "Cassyt, racontez-nous : où les gardes ont-ils vu les "
              "morts-vivants ? Qui fréquente le cimetière ces derniers "
              "temps ?"},
    {"pj": "Brandt", "label": "vers la fosse aux os (ouest)",
     "texte": "Allons voir cette fosse à l'ouest dont parle Cassyt."},
    {"pj": "Flick", "label": "fouille de la fosse (-1,0)",
     "texte": "Flick longe le passage étroit et examine les ossements "
              "frais, prudent, à quatre pattes (Détection)."},
    {"pj": "Brandt", "label": "vers les banquets funèbres (nord)",
     "texte": "Rien de vivant ici. Revenons sur nos pas, puis prenons au "
              "nord vers les banquets funèbres."},
    {"pj": "Flick", "label": "crochetage de la réserve (0,-1)",
     "texte": "Flick examine la grille de fer de la réserve et tente de "
              "la crocheter en silence (Crochetage DD 15)."},
    {"pj": "Brandt", "label": "vers les premières familles (nord)",
     "texte": "Continuons au nord, vers les cryptes des premières "
              "familles."},
    {"pj": "Aurora", "label": "après le squelette : fouille + nord",
     "texte": "Nous fouillons rapidement la salle, puis prenons le "
              "couloir au nord."},
    {"pj": "Aurora", "label": "descente des escaliers branlants (0,-3)",
     "texte": "Ces escaliers de bois semblent vermoulus — un à la fois, "
              "prudence. Nous descendons vers les Bas Tombeaux."},
]

STEPS_P2 = [
    # Reprise canonique : le groupe est à la fosse (-1,0), étage 0 — les
    # étapes « nord » de p1 n'ont jamais été mécanisées (divergence
    # prose/carte) ; on rejoue le trajet, avec nudges de récupération.
    {"pj": "Brandt", "label": "retour à la galerie d'entrée (est)",
     "texte": "Cette fosse ne nous dit rien de plus. Revenons à la "
              "galerie d'entrée, à l'est.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction est.)"},
    {"pj": "Brandt", "label": "arche du nord vers les banquets (0,-1)",
     "texte": "Prenons maintenant l'arche du nord, vers la salle des "
              "banquets funèbres.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction nord.)"},
    {"pj": "Flick", "label": "réserve des banquets : crochetage (0,-1)",
     "texte": "La réserve du coin sud, derrière sa grille de fer — Flick "
              "la crochète, et cette fois on veut le jet officiel.",
     "nudge": "(MJ : lance le jet officiel avec lancer_d20 "
              "(compétence=Crochetage, difficulté=15) puis narre le "
              "résultat.)"},
    {"pj": "Brandt", "label": "cryptes des premières familles (0,-2)",
     "texte": "Continuons au nord, vers les cryptes des premières "
              "familles de Phlan.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction nord.)"},
    {"pj": "Aurora", "label": "après le squelette : bénédiction + nord",
     "texte": "Aurora bénit les dépouilles profanées d'un signe de "
              "Kelemvor. Nous fouillons la salle puis prenons le couloir "
              "au nord.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction nord.)"},
    {"pj": "Aurora", "label": "descente des escaliers branlants (0,-3)",
     "texte": "Ces escaliers de bois sont vermoulus — un à la fois, "
              "prudence. Nous descendons vers les Bas Tombeaux.",
     "nudge": "(MJ : fais passer le groupe à l'étage -1 avec "
              "carte_donjon_etage.)"},
    {"pj": "Brandt", "label": "pied des escaliers → obélisque (nord)",
     "texte": "Nous débouchons au pied des escaliers. Un passage au nord "
              "— avançons prudemment.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction nord.)"},
    {"pj": "Aurora", "label": "étude de l'obélisque (0,-1)",
     "texte": "Aurora étudie les runes de l'obélisque — elle tente de "
              "lire la langue céleste (Connaissances mystiques)."},
    {"pj": "Flick", "label": "Talking Dead à l'est (1,-1)",
     "texte": "Prenons à l'est — ces niches remplies d'os intriguent "
              "Flick. Il s'incline respectueusement et demande : « Qui "
              "vous a réveillé ? Qui profane vos tombes ? »",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction est, puis laisse Flick questionner les défunts.)"},
    {"pj": "Brandt", "label": "retour obélisque puis sud",
     "texte": "Merci, défunts. Retournons à la salle de l'obélisque et "
              "prenons le passage du sud.",
     "nudge": "(MJ : un déplacement par tour — d'abord ouest vers "
              "l'obélisque avec carte_donjon_explorer.)"},
    {"pj": "Aurora", "label": "plafond de squelettes (0,-2)",
     "texte": "Prenons maintenant le passage du sud, garde à vous.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction sud.)"},
    {"pj": "Aurora", "label": "après l'embuscade : ouest vers l'odeur",
     "texte": "Fouillons vite les cercueils ouverts, puis prenons à "
              "l'ouest — cette odeur de charogne monte du couloir."},
    {"pj": "Flick", "label": "vers le tombeau de Sedrair (sud)",
     "texte": "Revenons à l'est puis prenons au sud. Flick repère les "
              "dalles instables et guette les dangers avant d'entrer.",
     "nudge": "(MJ : un déplacement par tour avec "
              "carte_donjon_explorer.)"},
    {"pj": "Brandt", "label": "couronne de Sedrair + poursuite au sud",
     "texte": "Flick, désamorce ce glyphe avant qu'on touche à la "
              "couronne. Ensuite nous prenons le passage du sud.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction sud.)"},
    {"pj": "Flick", "label": "infiltration de la sentinelle (0,-4)",
     "texte": "Chut — un kobold dort là. Flick se faufile dans l'ombre "
              "(Furtivité) pour lui briser le cou sans bruit, avant qu'il "
              "ne donne l'alarme."},
    {"pj": "Brandt", "label": "l'ouverture au fond du poste",
     "texte": "La voie est libre ? L'ouverture au fond du mur — c'est là "
              "que mènent les corps. Allons-y, prudents.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction sud.)"},
]

STEPS_P2B = [
    # Route canonique pilotée par WAYPOINTS (position réelle relue à
    # chaque tour). Départ : (0,-2) étage 0 — après le 2ᵉ squelette.
    {"vers": [0, -3], "label": "escaliers branlants (0,-3)"},
    {"etage": "descendre", "pj": "Aurora",
     "label": "descente vers les Bas Tombeaux",
     "texte": "Ces escaliers de bois sont vermoulus — un à la fois, "
              "prudence. Nous descendons vers les Bas Tombeaux."},
    {"vers": [0, -1], "label": "obélisque des Bas Tombeaux (0,-1)"},
    {"pj": "Aurora", "label": "étude de l'obélisque",
     "texte": "Aurora étudie les runes de l'obélisque — elle tente de "
              "lire la langue céleste (Connaissances mystiques)."},
    {"vers": [1, -1], "label": "Talking Dead (1,-1)"},
    {"pj": "Flick", "label": "questionner les défunts",
     "texte": "Flick s'incline respectueusement devant les défunts et "
              "demande : « Qui vous a réveillé ? Qui profane vos "
              "tombes ? »"},
    {"vers": [0, -1], "label": "retour à l'obélisque"},
    {"vers": [0, -2], "label": "plafond de squelettes (0,-2)"},
    {"pj": "Aurora", "label": "après l'embuscade : ouest vers l'odeur",
     "texte": "Fouillons vite les sarcophages violés, puis prenons à "
              "l'ouest — cette odeur de charogne monte du couloir."},
    {"vers": [-1, -2], "label": "bas tombeaux aux goules (-1,-2)"},
    {"vers": [0, -2], "label": "retour à l'est (0,-2)"},
    {"vers": [0, -3], "label": "tombeau de Sedrair II (0,-3)"},
    {"pj": "Brandt", "label": "couronne de Sedrair + poursuite au sud",
     "texte": "Flick, désamorce ce glyphe avant qu'on touche à la "
              "couronne. Ensuite nous prenons le passage du sud."},
    {"vers": [0, -4], "label": "poste de la sentinelle (0,-4)"},
    {"pj": "Flick", "label": "infiltration de la sentinelle",
     "texte": "Chut — un kobold dort là. Flick se faufile dans l'ombre "
              "(Furtivité) pour lui briser le cou sans bruit, avant qu'il "
              "ne donne l'alarme."},
    {"vers": [0, -5], "label": "repaire de Rorreth (0,-5)"},
]

STEPS_P1B = [
    # Étage 0 piloté par waypoints (le MJ ne déplace la carte que sur
    # direction explicite — divergence prose/carte observée en p1).
    {"vers": [-1, 0], "label": "fosse aux os (-1,0)"},
    {"pj": "Flick", "label": "fouille de la fosse",
     "texte": "Flick examine les ossements frais au bord de la fosse, "
              "prudent (Détection)."},
    {"vers": [0, 0], "label": "retour à l'entrée"},
    {"vers": [0, -1], "label": "banquets funèbres (0,-1)"},
    {"pj": "Flick", "label": "réserve : crochetage",
     "texte": "Flick crochète la grille de la réserve au coin sud de la "
              "salle des banquets (Crochetage DD 15).",
     "nudge": "(MJ : jet officiel avec lancer_d20 "
              "(compétence=Crochetage, difficulté=15).)"},
    {"vers": [0, -2], "label": "premières familles (0,-2)"},
    {"pj": "Aurora", "label": "bénédiction + nord",
     "texte": "Aurora bénit les dépouilles profanées d'un signe de "
              "Kelemvor. Nous fouillons la salle puis prenons le couloir "
              "au nord."},
    {"vers": [0, -3], "label": "escaliers branlants (0,-3)"},
    {"etage": "descendre", "pj": "Aurora",
     "label": "descente vers les Bas Tombeaux",
     "texte": "Un à la fois, prudence — nous descendons vers les Bas "
              "Tombeaux."},
]

STEPS_P2C = [
    # Route canonique (run 2,équipe préparée). Départ attendu : (0,-3)
    # étage 0 après la p1 — on reprend par le changement d'étage.
    {"etage": "descendre", "pj": "Aurora",
     "label": "descente vers les Bas Tombeaux",
     "texte": "Ces escaliers de bois sont vermoulus — un à la fois, "
              "prudence. Nous descendons vers les Bas Tombeaux."},
    {"vers": [0, -1], "label": "obélisque des Bas Tombeaux (0,-1)"},
    {"pj": "Aurora", "label": "étude de l'obélisque",
     "texte": "Aurora étudie les runes de l'obélisque — elle tente de "
              "lire la langue céleste (Connaissances mystiques)."},
    {"vers": [1, -1], "label": "Talking Dead (1,-1)"},
    {"pj": "Flick", "label": "questionner les défunts",
     "texte": "Flick s'incline respectueusement devant les défunts et "
              "demande : « Qui vous a réveillé ? Qui profane vos "
              "tombes ? »"},
    {"vers": [0, -1], "label": "retour à l'obélisque"},
    {"vers": [0, -2], "label": "plafond de squelettes (0,-2)"},
    {"pj": "Aurora", "label": "après l'embuscade : sud vers Sedrair",
     "texte": "Reposons les morts en paix, fouillons vite, puis prenons "
              "le passage du sud."},
    {"vers": [0, -3], "label": "tombeau de Sedrair II (0,-3)"},
    {"pj": "Brandt", "label": "couronne de Sedrair + poursuite au sud",
     "texte": "Flick, désamorce ce glyphe avant qu'on touche à la "
              "couronne. Ensuite nous prenons le passage du sud."},
    {"vers": [0, -4], "label": "poste de la sentinelle (0,-4)"},
    {"pj": "Flick", "label": "infiltration de la sentinelle",
     "texte": "Chut — un kobold dort là. Flick se faufile dans l'ombre "
              "(Furtivité) pour l'abattre sans bruit, avant qu'il ne "
              "donne l'alarme."},
    {"vers": [0, -5], "label": "repaire de Rorreth (0,-5)"},
]

STEPS_P2D = [
    # Repli après le loup-garou inventé : repos barricadé aux banquets
    # (salle nettoyée, une seule grille — défendable), puis retour.
    {"vers": [0, -2], "label": "repli vers (0,-2)"},
    {"vers": [0, -1], "label": "repli vers (0,-1)"},
    {"vers": [0, 0], "label": "repli au pied des escaliers"},
    {"etage": "monter", "pj": "Brandt",
     "label": "remontée vers les galeries hautes",
     "texte": "Remontons — nous ne pouvons pas continuer ainsi."},
    {"vers": [0, -2], "label": "retour vers les banquets"},
    {"vers": [0, -1], "label": "salle des banquets (0,-1)"},
    {"pj": "Brandt", "label": "repos long barricadé",
     "texte": "Nous barricadons la salle des banquets avec les tréteaux, "
              "veilles par deux. Repos long pour tous — demain, nous "
              "finissons le travail.",
     "nudge": "(MJ : appelle repos_long pour Brandt, Aurora et Flick.)"},
    {"etage": "descendre", "pj": "Brandt",
     "label": "redescente vers les Bas Tombeaux",
     "texte": "Au petit matin — nous redescendons, prudents."},
    {"vers": [0, -1], "label": "obélisque (0,-1)"},
    {"vers": [0, -2], "label": "salle du plafond (0,-2)"},
    {"vers": [0, -3], "label": "tombeau de Sedrair (0,-3)"},
    {"vers": [0, -4], "label": "poste de la sentinelle (0,-4)"},
    {"vers": [0, -5], "label": "repaire de Rorreth (0,-5)"},
]

STEPS_RUN3 = [
    # ── Ouverture (porche) ──────────────────────────────────────────────
    {"pj": "Brandt", "label": "acceptation de la mission",
     "texte": "Doomguide, nous acceptons la mission. Dites-nous tout : "
              "quand les morts ont-ils commencé à rôder, et que savez-vous "
              "de la profanation ?"},
    {"pj": "Flick", "label": "descente dans les catacombes",
     "texte": "Cassyt, guidez-nous. Nous descendons dans les catacombes "
              "pour voir cela de nos propres yeux.",
     "nudge": "(MJ : ouvre le donjon du scénario avec "
              "carte_donjon_entrer.)"},
    {"pj": "Aurora", "label": "enquête avec Cassyt (0,0)",
     "texte": "Cassyt, racontez-nous : où les gardes ont-ils vu les "
              "morts-vivants ? Qui fréquente le cimetière ces derniers "
              "temps ?"},
    # ── Galeries hautes (étage 0) ───────────────────────────────────────
    {"vers": [-1, 0], "label": "fosse aux os (-1,0)"},
    {"pj": "Flick", "label": "fouille de la fosse",
     "texte": "Flick examine les ossements frais au bord de la fosse, "
              "prudent (Détection).",
     "nudge": "(MJ : jet officiel avec lancer_d20 "
              "(compétence=Détection).)"},
    {"vers": [0, 0], "label": "retour à l'entrée"},
    {"vers": [0, -1], "label": "banquets funèbres (0,-1)"},
    {"pj": "Flick", "label": "réserve : crochetage (0,-1)",
     "texte": "Flick crochète la grille de la réserve au coin sud de la "
              "salle des banquets (Crochetage DD 15).",
     "nudge": "(MJ : jet officiel avec lancer_d20 "
              "(compétence=Crochetage, difficulté=15).)"},
    {"vers": [0, -2], "label": "premières familles (0,-2)"},
    {"pj": "Aurora", "label": "bénédiction + fouille + nord",
     "texte": "Aurora bénit les dépouilles profanées d'un signe de "
              "Kelemvor. Nous fouillons la salle puis prenons le couloir "
              "au nord.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction nord.)"},
    {"vers": [0, -3], "label": "escaliers branlants (0,-3)"},
    {"etage": "descendre", "pj": "Aurora",
     "label": "descente vers les Bas Tombeaux",
     "texte": "Ces escaliers de bois sont vermoulus — un à la fois, "
              "prudence. Nous descendons vers les Bas Tombeaux.",
     "nudge": "(MJ : carte_donjon_etage avec direction=\"descendre\".)"},
    # ── Bas Tombeaux (étage -1) ─────────────────────────────────────────
    {"vers": [0, -1], "label": "obélisque (0,-1)"},
    {"pj": "Aurora", "label": "étude de l'obélisque",
     "texte": "Aurora étudie les runes de l'obélisque — elle tente de "
              "lire la langue céleste (Connaissances mystiques).",
     "nudge": "(MJ : jet officiel avec lancer_d20 "
              "(compétence=Connaissances mystiques).)"},
    {"vers": [1, -1], "label": "Talking Dead (1,-1)"},
    {"pj": "Flick", "label": "questionner les défunts",
     "texte": "Flick s'incline respectueusement devant les défunts et "
              "demande : « Qui vous a réveillé ? Qui profane vos "
              "tombes ? »"},
    {"vers": [0, -1], "label": "retour à l'obélisque"},
    {"vers": [0, -2], "label": "plafond de squelettes (0,-2)"},
    {"pj": "Aurora", "label": "après l'embuscade : sud vers Sedrair",
     "texte": "Reposons les morts en paix, fouillons vite, puis prenons "
              "le passage du sud.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction sud.)"},
    {"vers": [0, -3], "label": "tombeau de Sedrair II (0,-3)"},
    {"pj": "Brandt", "label": "couronne de Sedrair + poursuite au sud",
     "texte": "Flick, désamorce ce glyphe avant qu'on touche à la "
              "couronne. Ensuite nous prenons le passage du sud.",
     "nudge": "(MJ : déplace le groupe avec carte_donjon_explorer, "
              "direction sud.)"},
    {"vers": [0, -4], "label": "poste de la sentinelle (0,-4)"},
    {"pj": "Flick", "label": "infiltration de la sentinelle",
     "texte": "Chut — un kobold dort là. Flick se faufile dans l'ombre "
              "(Furtivité) pour l'abattre sans bruit, avant qu'il ne "
              "donne l'alarme.",
     "nudge": "(MJ : jet officiel avec lancer_d20 "
              "(compétence=Déplacement silencieux).)"},
    {"vers": [0, -5], "label": "repaire de Rorreth (0,-5)"},
    {"pj": "Brandt", "label": "confrontation de Rorreth",
     "texte": "Rorreth Monforoth ! Vos morts retourneront au sommeil. "
              "Rendez-vous — ou nous vous arrêterons.",
     "nudge": "(MJ : engage le combat canonique de la salle avec "
              "engager_combat si le mage résiste.)"},
    # ── Épilogue ────────────────────────────────────────────────────────
    {"pj": "Brandt", "label": "fouille du repaire",
     "texte": "Le repaire est à nous : emparons-nous du journal de "
              "Rorreth, des ordres signés et de tout indice.",
     "nudge": "(MJ : les objets de quête passent par "
              "inventaire_ajouter / inventaire_ramasser.)"},
    {"pj": "Aurora", "label": "remontée vers la surface",
     "texte": "Nous en avons fini ici. Remontons rendre compte au "
              "Doomguide.",
     "nudge": "(MJ : plusieurs salles à remonter — un déplacement par "
              "tour avec carte_donjon_explorer.)"},
    {"vers": [0, -4], "label": "remontée (0,-4)"},
    {"vers": [0, -3], "label": "remontée (0,-3)"},
    {"vers": [0, -2], "label": "remontée (0,-2)"},
    {"vers": [0, -1], "label": "remontée (0,-1)"},
    {"vers": [0, 0], "label": "pied des escaliers (0,0)"},
    {"etage": "monter", "pj": "Brandt",
     "label": "remontée vers les galeries hautes",
     "texte": "Nous remontons vers la lumière.",
     "nudge": "(MJ : carte_donjon_etage avec direction=\"monter\".)"},
    {"vers": [0, -2], "label": "galleries hautes : vers la sortie"},
    {"vers": [0, -1], "label": "vers la sortie (0,-1)"},
    {"vers": [0, 0], "label": "entrée des catacombes (0,0)"},
    {"pj": "Brandt", "label": "sortie du donjon",
     "texte": "Nous ressortons au grand air, vers le porche du "
              "cimetière.",
     "nudge": "(MJ : carte_donjon_sortir.)"},
    {"pj": "Brandt", "label": "rapport au Doomguide (fin)",
     "texte": "Doomguide Yovir, Cassyt — le repaire est détruit et le "
              "cimetière purifié. Voici le journal de Rorreth Monforoth "
              "et les ordres signés : un Mage Rouge de Thay au service "
              "du Culte du Dragon levait vos morts pour payer une "
              "dette. Nos blessés ont besoin de soins.",
     "nudge": "(MJ : soigne les PJ avec fiche_perso_soigner, accorde "
              "l'XP d'histoire via fiche_perso_gagner_xp, puis clôture "
              "l'aventure.)"},
]

STEPS_P3 = [
    {"pj": "Brandt", "label": "fouille du repaire de Rorreth",
     "texte": "Le mage s'est échappé… Fouillons son repaire : emparons-"
              "nous du journal, des parchemins et des ordres signés."},
    {"pj": "Aurora", "label": "remontée vers la surface",
     "texte": "Nous en savons assez. Remontons rendre compte au "
              "Doomguide — avec Cassyt si elle est là."},
    {"pj": "Brandt", "label": "rapport au Doomguide (fin)",
     "texte": "Doomguide Yovir, le repaire est détruit et le cimetière "
              "est purifié. Voici le journal de Rorreth Monforoth et les "
              "ordres du Culte du Dragon — un Mage Rouge de Thay levait "
              "vos morts pour payer une dette."},
]


async def phase_named(name: str, steps: list[dict]) -> None:
    pid = lire_pid()
    bilan = Bilan.charger()
    socks = await _ouvrir(pid)
    await jouer_phase(pid, socks, bilan, name, steps)
    await _fermer(socks)
    bilan.sauver()


async def phase_statut():
    pid = lire_pid()
    st = etat_partie(pid) or {}
    snap = snapshot(pid)
    print(f"Phase={st.get('phase')} | tour={st.get('tour')} | "
          f"courant={snap['courant']} | donjon.id={snap['donjon'].get('id')}")
    for n, s in snap["pj"].items():
        print(f"  🛡️ {n}: pv={s['pv']}/{s['pv_max']} {s['conds']}")
    for m, s in snap["monstres"].items():
        print(f"  👹 {m}: {s['pv']}/{s['pv_max']} {s['conds']}")
    ev = [e for e in snap["histoire"]][-6:]
    print("Histoire (6 derniers) :")
    for e in ev:
        print("  • " + e[:110])
    if os.path.isfile(PROBLEMS_FILE):
        with open(PROBLEMS_FILE, encoding="utf-8") as f:
            pr = json.load(f)
        print(f"Problèmes notés : {len(pr)}")
        for p in pr[-8:]:
            print(f"  ⚠️ [{p['type']}] {p['detail'][:120]}")


async def phase_rapport():
    bilan = Bilan.charger()
    pid = lire_pid()
    st = etat_partie(pid) or {}
    print(f"=== RAPPORT — phase={st.get('phase')} tour={st.get('tour')} ===")
    for nom in PJS:
        f = fiche_pj(nom) or {}
        print(f"  🛡️ {nom}: pv={f.get('pv')}/{f.get('pv_max')} "
              f"xp={f.get('xp')}")
    print(f"Vérifs : {len(bilan.oks)} OK, {len(bilan.fails)} échecs")
    for f_ in bilan.fails:
        print("  ✗ " + f_[:160])
    if os.path.isfile(PROBLEMS_FILE):
        with open(PROBLEMS_FILE, encoding="utf-8") as f:
            pr = json.load(f)
        print(f"PROBLÈMES notés ({len(pr)}) :")
        for p in pr:
            print(f"  ⚠️ [{p['type']}] {p['detail'][:180]}")


async def phase_go():
    """Action ad-hoc : py scripts/dues_full_run.py go Brandt « texte ».
    Tourne la boucle de combat si la phase est combat après l'action."""
    pid = lire_pid()
    bilan = Bilan.charger()
    pj = sys.argv[2] if len(sys.argv) > 2 else "Brandt"
    texte = sys.argv[3] if len(sys.argv) > 3 else "Nous continuons."
    socks = await _ouvrir(pid)
    snap = snapshot(pid)
    label = "go"
    if snap["phase"] == "combat":
        cible = min(
            (m for m, st in snap["monstres"].items()
             if "Détruit" not in st["conds"]),
            key=lambda m: snap["monstres"][m]["pv"] or 0, default=None)
        texte = texte.replace("{cible}", cible or "l'ennemi")
    await action_pj(pid, socks, bilan, pj, texte, label,
                    "(MJ : résous avec les tools officiels puis "
                    "tour_suivant_combat si en combat.)")
    for _ in range(20):
        if snapshot(pid)["phase"] != "combat":
            break
        snap = snapshot(pid)
        courant = snap["courant"]
        pj_c = next((p for p in PJS if p.lower() in courant.lower()
                     or courant.lower() in p.lower()), None)
        if pj_c is None:
            await asyncio.sleep(12)
            if snapshot(pid)["courant"] == courant:
                noter_probleme("tour_monstre_bloque",
                               f"go : tour de « {courant} » sans rotation")
                break
            continue
        tourne = {}
        texte2, nudge2 = _action_combat(pj_c, snapshot(pid), min(
            (m for m, st in snap["monstres"].items()
             if "Détruit" not in st["conds"]),
            key=lambda m: snap["monstres"][m]["pv"] or 0, default=None),
            tourne)
        await action_pj(pid, socks, bilan, pj_c, texte2, label, nudge2)
        await sante_sockets(pid, socks)
    await _fermer(socks)
    bilan.sauver()


PHASES = {
    "setup": phase_setup,
    "p1": lambda: phase_named("p1", STEPS_P1),
    "p2": lambda: phase_named("p2", STEPS_P2),
    "p2b": lambda: phase_named("p2b", STEPS_P2B),
    "p2c": lambda: phase_named("p2c", STEPS_P2C),
    "p2d": lambda: phase_named("p2d", STEPS_P2D),
    "run3": lambda: phase_named("run3", STEPS_RUN3),
    "p1b": lambda: phase_named("p1b", STEPS_P1B),
    "p3": lambda: phase_named("p3", STEPS_P3),
    "go": phase_go,
    "statut": phase_statut,
    "rapport": phase_rapport,
}


async def main():
    etape = sys.argv[1] if len(sys.argv) > 1 else "statut"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    await fn()


if __name__ == "__main__":
    asyncio.run(main())
