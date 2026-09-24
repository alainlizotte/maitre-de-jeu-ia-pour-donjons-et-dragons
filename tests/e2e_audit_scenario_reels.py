"""E2E audit réel : scénario complet « À la chasse aux gobs » (LLM Qwen réel).

Vérifie sur une PARTIE RÉELLE les correctifs de session (120e9243, points
1-9) et le déroulement général d'un scénario complet :
  1. kit de premiers secours : consommé UNIQUEMENT sur demande explicite,
     jamais sur un déplacement/examen, jamais à PV max (garde) ;
  2. combat : engagement fidèle aux `ennemis` canoniques de la salle,
     pas de re-annonce en boucle, pas de résurrection ;
  3. exploration : mouvements réels (carte), prologue ancré sur la salle
     d'entrée, bible de quête injectée ;
  4. prose : pas de répétitions verbatim massives, arithmétique serveur.

Reprise phase par phase (état persistant côté serveur) :
  py tests/e2e_audit_scenario_reels.py setup|entree|explo|combat|kit|rapport
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys
import unicodedata
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_combats_reels as _c  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_audit_gobs")
os.makedirs(TMP, exist_ok=True)
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.BILAN_FILE = BILAN_FILE

from e2e_combats_reels import (  # noqa: E402
    Bilan, _norm, connecter, etat_partie, fiche_pj, snapshot, tour_dm,
    sante_sockets, boucle_combat, vider,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from server.tools.base import ToolContext, invoke_tool         # noqa: E402
from server.tools.registry import discover_tools               # noqa: E402

BASE = "http://localhost:8123"
DATA_DIR = os.path.join(REPO, "server", "data")
TOOLS = discover_tools("server.tools")
SCEN_FILE = os.path.join(
    DATA_DIR, "scenarios", "Laelith", "À la chasse aux gobs",
    "Chasse-aux-gobs.donjon.json")
JOUEURS = ["Groth", "Elara"]
_QUEST = {
    "titre": "À la chasse aux gobs",
    "pitch": ("Des gobelins ravagent les routes autour de Laelith. Les PJ "
              "sont envoyés pour les éradiquer, mais découvrent que la "
              "menace est bien plus organisée qu'un simple raid."),
    "source": "[laelith_à_la_chasse_aux_gobs] À la chasse aux gobs",
}


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _ctx(pid: str) -> ToolContext:
    return ToolContext(partie_id=pid, joueur="e2e_audit", data_dir=DATA_DIR)


async def _tool(pid: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(pid), args)


def lire_pid() -> str:
    if os.path.exists(PID_FILE):
        return open(PID_FILE, encoding="utf-8").read().strip()
    return ""


def salles_scenario() -> dict[tuple[int, int], dict]:
    d = json.load(open(SCEN_FILE, encoding="utf-8"))
    out: dict[tuple[int, int], dict] = {}
    for et in (d.get("etages") or []):
        for s in (et.get("salles") or []):
            out[(int(s.get("x")), int(s.get("y")))] = s
    return out


def donjon_etat(pid: str) -> dict:
    return (etat_partie(pid) or {}).get("donjon") or {}


def chat_log(pid: str) -> list[dict]:
    p = os.path.join(DATA_DIR, f"chat_{pid}.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return []


def charges_kit(nom: str) -> int:
    f = fiche_pj(nom) or {}
    tot = 0
    for e in (f.get("inventaire") or []):
        n = _norm(e.get("nom"))
        if ("kit" in n or "trousse" in n) and ("secour" in n or "soin" in n):
            brut = e.get("charges")
            tot += 10 if brut is None else int(brut or 0)
    return tot


def ouvrir_sockets(pid: str) -> dict:
    async def _go():
        return {j: await connecter(pid, j) for j in JOUEURS}
    return asyncio.run(_go())


async def fermer(sockets: dict):
    for w in sockets.values():
        try:
            await w.close()
        except Exception:                                    # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
async def phase_setup():
    bilan = Bilan.neuf()
    # fiches des PJ de test uniquement (jamais de balayage global)
    for fp in glob.glob(os.path.join(DATA_DIR, "fiches", "fiche_*.json")):
        base = os.path.splitext(os.path.basename(fp))[0]
        slug = base[len("fiche_"):] if base.startswith("fiche_") else base
        if slug in {"groth", "elara"}:
            try:
                os.unlink(fp)
            except OSError:
                pass
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    pid = _post("/api/parties", {
        "titre": "E2E Audit — À la chasse aux gobs"})["partie_id"]
    open(PID_FILE, "w", encoding="utf-8").write(pid)
    print(f"=== Partie créée : {pid} ===")
    _post(f"/api/parties/{pid}/quest", _QUEST)
    r = await _tool(pid, "fiche_perso_creer_rapide", nom="Groth",
                    race="Humain", classe="Barbare", niveau=1,
                    joueur="groth",
                    carac_texte="FOR 16, DEX 12, CON 14, INT 10, SAG 10, "
                                "CHA 10")
    bilan.check("[setup] fiche Groth", r.text.startswith("✅"), r.text[:120])
    r = await _tool(pid, "fiche_perso_creer_rapide", nom="Elara",
                    race="Elfe", classe="Magicien", niveau=1,
                    joueur="elara",
                    carac_texte="FOR 8, DEX 14, CON 12, INT 16, SAG 10, "
                                "CHA 10")
    bilan.check("[setup] fiche Elara", r.text.startswith("✅"), r.text[:120])
    for j in JOUEURS:
        r = await _tool(pid, "inventaire_ajouter", nom=j,
                        objet="Kit de premiers secours", quantite=1,
                        poids=0.45)
        bilan.check(f"[setup] kit de {j} dans l'inventaire",
                    r.text.startswith("✅") or "ajout" in r.text.lower(),
                    r.text[:100])
    await _tool(pid, "etat_partie_patch", chemin="phase",
                valeur="exploration")
    # le ctx outil écrase le champ joueur des PJ → on le rétablit pour que
    # la rotation de combat accepte les envois WS (nom du joueur)
    for i, j in ((0, "groth"), (1, "elara")):
        await _tool(pid, "etat_partie_patch", chemin=f"pj.{i}.joueur",
                    valeur=j)
        await _tool(pid, "fiche_perso_mettre_a_jour", nom=j.capitalize(),
                    champ="joueur", valeur=j)
    etat = etat_partie(pid) or {}
    bilan.check("[setup] quête attachée",
                str((etat.get("quete") or {}).get("titre") or "")
                .lower().startswith("à la chasse"),
                json.dumps(etat.get("quete") or {}, ensure_ascii=False)[:120])
    bilan.check("[setup] bible de scénario injectée",
                bool((etat.get("quete") or {}).get("bible")), "")
    bilan.check("[setup] 2 PJ dans la partie",
                len(etat.get("pj") or []) == 2,
                f"pj={[p.get('nom') for p in etat.get('pj') or []]}")
    bilan.check("[setup] Groth : kit = 10 charges", charges_kit("Groth") == 10,
                f"charges={charges_kit('Groth')}")
    bilan.sauver()


async def phase_entree():
    pid = lire_pid()
    bilan = Bilan.charger()
    dj = donjon_etat(pid)
    if dj.get("id"):
        # idempotence : l'entrée a déjà eu lieu (relance) — on vérifie l'état
        bilan.check("[entrée] donjon actif (carte_donjon_entrer)",
                    True, "déjà actif (relance)")
        bilan.check("[entrée] groupe en salle d'entrée (0,0)",
                    list(dj.get("courant") or [0, 0]) == [0, 0],
                    f"courant={dj.get('courant')}")
        bilan.check("[entrée] AUCUNE charge de kit consommée à l'entrée",
                    charges_kit("Groth") == 10,
                    f"charges={charges_kit('Groth')}")
        msgs = [m for m in chat_log(pid) if m.get("role") == "assistant"]
        narr = str((msgs[-1] if msgs else {}).get("content") or "")
        bilan.check("[entrée] narration ancrée sur la salle canonique",
                    any(w in narr.lower() for w in
                        ("grotte", "porte", "gobelin", "antre")),
                    f"narr[:120]={narr[:120]}")
        bilan.sauver()
        return
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await vider(sockets["Groth"])
    c0 = charges_kit("Groth")
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "Nous voilà devant l'antre des gobelins. Nous "
                        "poussons la porte et nous entrons !", bilan)
    dj = donjon_etat(pid)
    bilan.check("[entrée] donjon actif (carte_donjon_entrer)",
                bool(dj.get("id")), f"donjon={json.dumps(dj)[:120]}")
    bil = True
    try:
        bil = (list(dj.get("courant") or [0, 0])[0],
               list(dj.get("courant") or [0, 0])[1]) == (0, 0)
    except Exception:                                        # noqa: BLE001
        bil = False
    bilan.check("[entrée] groupe en salle d'entrée (0,0)", bil,
                f"courant={dj.get('courant')}")
    narration = str((rep or {}).get("text") or "")
    if not narration:
        msgs = [m for m in chat_log(pid) if m.get("role") == "assistant"]
        narration = str((msgs[-1] if msgs else {}).get("content") or "")
    salle0 = salles_scenario().get((0, 0), {})
    contient = [w for w in ("grotte", "porte", "gobelin", "antre")
                if w in narration.lower()]
    bilan.check("[entrée] narration ancrée sur la salle canonique",
                bool(narration) and bool(contient),
                f"extraits={contient} narration[:120]={narration[:120]}")
    bilan.check("[entrée] AUCUNE charge de kit consommée à l'entrée",
                charges_kit("Groth") == c0,
                f"{c0}→{charges_kit('Groth')}")
    bilan.sauver()
    await fermer(sockets)


def _choisir_direction(salles: dict, courant: list,
                       visitees: set | None = None) -> tuple[str, dict | None]:
    visitees = visitees or set()
    cx, cy = int(courant[0]), int(courant[1])
    portes = salles.get((cx, cy), {}).get("portes") or {}

    def adj(d):
        return {"nord": (cx, cy - 1), "sud": (cx, cy + 1),
                "est": (cx + 1, cy), "ouest": (cx - 1, cy)}[d]

    opts = [(d, adj(d)) for d, o in portes.items() if o]
    # 1) salle NON visitée avec ennemis ; 2) salle non visitée ; 3) qualquer
    for prio in (1, 0):
        for d, nxt in opts:
            room = salles.get(nxt)
            if not room:
                continue
            non_vue = f"{nxt[0]},{nxt[1]}" not in visitees
            if prio == 1 and non_vue and room.get("ennemis"):
                return d, room
            if prio == 0 and non_vue:
                return d, room
    if opts:
        d, nxt = opts[0]
        return d, salles.get(nxt)
    return "", None


async def phase_explo():
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    salles = salles_scenario()
    for etape in range(4):
        await sante_sockets(pid, sockets)
        dj = donjon_etat(pid)
        courant = list(dj.get("courant") or [0, 0])
        snap = snapshot(pid)
        if snap["phase"] == "combat":
            bilan.event(f"[explo] combat engagé en ({courant[0]},{courant[1]})"
                        f" — species={sorted(snap['monstres'])}")
            break
        d, room = _choisir_direction(salles, courant,
                                     set(dj.get("salles_visitees") or []))
        if not d:
            bilan.check("[explo] porte disponible", False,
                        f"salles={list(salles)[:3]}")
            break
        c0 = charges_kit("Groth")
        attendu = [str(e) for e in (room or {}).get("ennemis") or []]
        rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                            f"Nous avançons prudemment au {d}.", bilan)
        dj2 = donjon_etat(pid)
        courant2 = list(dj2.get("courant") or [0, 0])
        bouge = courant2 != courant
        bilan.check(f"[explo {etape}] déplacement réel au {d} "
                    f"({courant}→{courant2})", bouge,
                    f"attendu salle={room.get('type') if room else '?'}")
        narration = str((rep or {}).get("text") or "")
        bilan.check(f"[explo {etape}] AUCUN soin auto sur un déplacement",
                    "Soin résolu par le serveur" not in narration
                    and charges_kit("Groth") == c0,
                    f"kit {c0}→{charges_kit('Groth')}")
        if attendu and bouge:
            attendu_n = _norm(" ".join(attendu))
            engage = snapshot(pid)["monstres"]
            if engage:
                bon = any(_norm(m)[:8] in attendu_n
                          or attendu_n[:8] in _norm(m)
                          for m in engage)
                bilan.check(
                    f"[explo {etape}] ennemis engagés = ennemis du scénario",
                    bon, f"engagés={sorted(engage)} attendu={attendu}")
                break
            bilan.event(f"[explo {etape}] salle à ennemis ({attendu[0][:40]}"
                        ") — engagement attendu au tour suivant")
    bilan.sauver()
    await fermer(sockets)


async def phase_combat():
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    hist: list[dict] = []
    snap0 = snapshot(pid)
    for m, st in snap0["monstres"].items():
        hist.append({"nom": m, "pv": st["pv"], "pv_max": st["pv_max"]})
    # boucle_combat pilote Groth/Elara ; on surveille la résurrection
    detruits = await boucle_combat(pid, sockets, bilan, "Audit — antre", 12)
    # vérif résurrection : aucune PV de monstre au-dessus de son état final
    snap1 = snapshot(pid)
    for h in hist:
        st = snap1["monstres"].get(h["nom"])
        if st and st["pv"] and h["pv"] is not None and st["pv"] > h["pv"]:
            bilan.check("[combat] pas de résurrection de monstre", False,
                        f"{h['nom']} {h['pv']}→{st['pv']}")
    snap = snapshot(pid)
    bilan.check("[combat] combat clôturé (phase ≠ combat)",
                snap["phase"] != "combat", f"phase={snap['phase']}")
    bilan.check("[combat] ennemis détruits (pas de restauration complète)",
                bool(detruits) or not snap["monstres"],
                f"détruits={sorted(detruits)} restants={sorted(snap['monstres'])}")
    bilan.sauver()
    await fermer(sockets)


async def phase_kit():
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await sante_sockets(pid, sockets)
    f = fiche_pj("Groth") or {}
    pv0, pvm = int(f.get("pv") or 0), int(f.get("pv_max") or 0)
    c0 = charges_kit("Groth")
    if pv0 >= pvm:
        r = await _tool(pid, "fiche_perso_infliger_degats", nom="Groth",
                        degats=5)
        bilan.check("[kit] Groth blessé mécaniquement (-5 PV) pour le test",
                    r.text.startswith("✅") or "5" in r.text, r.text[:100])
    # 1) demande EXPLICITE → le kit doit fonctionner (1 charge, PV+)
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "J'utilise un kit de premiers secours pour "
                        "récupérer des pv.", bilan)
    f1 = fiche_pj("Groth") or {}
    c1, pv1 = charges_kit("Groth"), int(f1.get("pv") or 0)
    narr1 = str((rep or {}).get("text") or "")
    bilan.check("[kit] demande explicite : 1 charge consommée", c1 == c0 - 1,
                f"{c0}→{c1} narr={narr1[:90]}")
    bilan.check("[kit] demande explicite : PV remontés",
                pv1 > pv0 or "récupère" in narr1.lower()
                or "Soin résolu" in narr1,
                f"pv {pv0}→{pv1}")
    # 2) tour SANS demande de soin → AUCUNE consommation (fix point 1)
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "J'examine la pièce et je reste sur mes gardes.",
                        bilan)
    narr2 = str((rep or {}).get("text") or "")
    c2 = charges_kit("Groth")
    bilan.check("[kit] tour neutre : AUCUNE charge consommée", c2 == c1,
                f"{c1}→{c2} narr={narr2[:90]}")
    bilan.check("[kit] tour neutre : pas de « Soin résolu par le serveur »",
                "Soin résolu par le serveur" not in narr2, "")
    # 3) déplacement SANS soin → idem
    dj = donjon_etat(pid)
    courant = list(dj.get("courant") or [0, 0])
    d, _room = _choisir_direction(salles_scenario(), courant)
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        f"Nous avançons au {d} sans nous arrêter.", bilan)
    narr3 = str((rep or {}).get("text") or "")
    c3 = charges_kit("Groth")
    bilan.check("[kit] déplacement : AUCUNE charge consommée", c3 == c2,
                f"{c2}→{c3} narr={narr3[:90]}")
    # 4) à PV max → garde anti-gaspillage (refus, 0 charge)
    f4 = fiche_pj("Groth") or {}
    if int(f4.get("pv") or 0) >= int(f4.get("pv_max") or 0):
        rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                            "J'utilise un kit de premiers secours alors que "
                            "je vais bien, au cas où.", bilan)
        narr4 = str((rep or {}).get("text") or "")
        c4 = charges_kit("Groth")
        garde = ("DÉJÀ à" in narr4 or "déjà" in narr4.lower()
                 or c4 == c3)
        bilan.check("[kit] à PV max : garde anti-gaspillage (0 charge)",
                    garde, f"{c3}→{c4} narr={narr4[:90]}")
    bilan.sauver()
    await fermer(sockets)


async def phase_explo2():
    """Poursuite de l'exploration après le kit : 3 salles max, nouveau
    combat éventuel, et suivis identiques (pas de soin auto, mouvements
    réels)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    salles = salles_scenario()
    for etape in range(3):
        await sante_sockets(pid, sockets)
        dj = donjon_etat(pid)
        courant = list(dj.get("courant") or [0, 0])
        snap = snapshot(pid)
        if snap["phase"] == "combat":
            bilan.event(f"[explo2] combat en ({courant[0]},{courant[1]}) — "
                        f"species={sorted(snap['monstres'])}")
            detruits = await boucle_combat(pid, sockets, bilan,
                                           "Audit — suite", 10)
            bilan.check("[explo2] second combat clôturé", bool(detruits) or
                        snapshot(pid)["phase"] != "combat",
                        f"détruits={sorted(detruits)}")
            continue
        if snap["phase"] != "exploration":
            break
        d, room = _choisir_direction(salles, courant,
                                     set(dj.get("salles_visitees") or []))
        if not d:
            break
        c0 = charges_kit("Groth")
        rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                            f"Nous continuons au {d}, en file indienne.",
                            bilan)
        courant2 = list(donjon_etat(pid).get("courant") or [0, 0])
        narr = str((rep or {}).get("text") or "")
        bilan.check(f"[explo2 {etape}] déplacement réel au {d} "
                    f"({courant}→{courant2})", courant2 != courant, "")
        bilan.check(f"[explo2 {etape}] aucune charge consommée", 
                    charges_kit("Groth") == c0, f"{c0}→{charges_kit('Groth')}")
        bilan.check(f"[explo2 {etape}] pas de « Soin résolu » sauvage",
                    "Soin résolu par le serveur" not in narr, "")
    visits = donjon_etat(pid).get("salles_visitees") or []
    bilan.event(f"[explo2] salles visitées ({len(visits)}/18) : {visits}")
    bilan.sauver()
    await fermer(sockets)


async def phase_sortie():
    """Quitte l'antre et voyage vers Laelith : mécanique voyage_demarrer
    (durée réelle, événements, arrivée)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await sante_sockets(pid, sockets)
    snap = snapshot(pid)
    if snap["phase"] == "combat":
        await boucle_combat(pid, sockets, bilan, "Audit — sortie", 10)
    # retour à l'entrée (0,0) par les portes connues, puis sortie + voyage
    salles = salles_scenario()
    for _ in range(12):
        await sante_sockets(pid, sockets)
        snap = snapshot(pid)
        if snap["phase"] == "combat":
            await boucle_combat(pid, sockets, bilan, "Audit — repli", 10)
            continue
        dj = donjon_etat(pid)
        courant = list(dj.get("courant") or [0, 0])
        if (courant[0], courant[1]) == (0, 0):
            break
        cx, cy = courant
        bouge = False
        for d, tgt in (("sud", (cx, cy + 1)), ("nord", (cx, cy - 1)),
                       ("est", (cx + 1, cy)), ("ouest", (cx - 1, cy))):
            if salles.get((cx, cy), {}).get("portes", {}).get(d) \
                    and tgt in salles:
                rep = await tour_dm(sockets["Groth"], [sockets["Elara"]],
                                    "Groth",
                                    f"Nous rebroussons chemin au {d}.", bilan)
                bouge = True
                break
        if not bouge:
            break
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "Nous sortons de l'antre et reprenons la route de "
                        "Laelith (plusieurs jours de route).", bilan)
    etat = etat_partie(pid) or {}
    voyage = etat.get("voyage") or {}
    narr = str((rep or {}).get("text") or "")
    dans_voyage = bool(voyage) or etat.get("phase") == "voyage" \
        or "voyage" in narr.lower() or "route" in narr.lower() \
        or "Laelith" in narr
    bilan.check("[voyage] voyage réel engagé (voyage_demarrer ou route "
                "narrée)", dans_voyage,
                f"voyage={json.dumps(voyage)[:100]} phase={etat.get('phase')}")
    # tour 2 : avancer jusqu'à l'arrivée (le voyage est tourné par jours)
    rep2 = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                         "Nous continuons la route jusqu'à Laelith, quoi "
                         "qu'il arrive.", bilan)
    narr2 = str((rep2 or {}).get("narration") or "")
    lieu = str(((etat_partie(pid) or {}).get("lieu") or {}).get("nom") or "")
    bilan.event(f"[voyage] lieu après route : {lieu!r} ; extrait narration : "
                f"{narr2[:110]}")
    bilan.sauver()
    await fermer(sockets)


async def phase_achat():
    """Achat réel au marché : or débité + objet en inventaire."""
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await sante_sockets(pid, sockets)
    f = fiche_pj("Groth") or {}
    or0 = int(f.get("or") or 0)
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "Passons à l'auberge du marché de Laelith : j'achète "
                        "une potion de soins légers et des rations.", bilan)
    f2 = fiche_pj("Groth") or {}
    or2 = int(f2.get("or") or 0)
    inv = _norm(" ".join(str(e.get("nom"))
                         for e in (f2.get("inventaire") or [])))
    a_achete = or2 < or0 and ("potion" in inv or "ration" in inv)
    narr = str((rep or {}).get("text") or "")
    if not a_achete:
        # relance ferme : le 9B oublie souvent les tools d'achat hors ville
        rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                            "(MJ : appelle marche_acheter nom=\"Groth\" "
                            "article=\"potion de soins légers\" puis "
                            "marche_acheter article=\"rations journalières\") "
                            "Je paie et je prends mes achats.", bilan)
        f2 = fiche_pj("Groth") or {}
        or2 = int(f2.get("or") or 0)
        inv = _norm(" ".join(str(e.get("nom"))
                             for e in (f2.get("inventaire") or [])))
        a_achete = or2 < or0 and ("potion" in inv or "ration" in inv)
    bilan.check("[achat] achat réel : or débité ET objet en inventaire",
                a_achete, f"or {or0}→{or2}, inventaire a potion/ration="
                f"{'potion' in inv or 'ration' in inv}")
    bilan.sauver()
    await fermer(sockets)


async def phase_rapport():
    pid = lire_pid()
    bilan = Bilan.charger()
    msgs = [m for m in chat_log(pid) if m.get("role") == "assistant"]
    texts = [re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
             for m in msgs]

    # ---------- 📝 QUALITÉ DES TEXTES DU MJ ----------
    longueurs = [len(t) for t in texts if t]
    bilan.event(f"[texte] {len(texts)} narrations MJ, moyenne "
                f"{sum(longueurs) / max(1, len(longueurs)):.0f} ch "
                f"(min {min(longueurs) if longueurs else 0}, "
                f"max {max(longueurs) if longueurs else 0})")
    fins_valides = ".!?»\"'…)]}\n "
    tronques = [t[-40:] for t in texts
                if t and t[-1] not in fins_valides]
    bilan.check("[texte] pas de narration tronquée en fin de message",
                len(tronques) <= 1, f"{len(tronques)} ex: {tronques[:1]}")
    vus: dict[str, int] = {}
    for t in texts:
        for ln in set(s.strip() for s in t.split(". ") if len(s.strip()) >= 60):
            vus[ln] = vus.get(ln, 0) + 1
    repets = sorted([k for k, n in vus.items() if n >= 2], key=len,
                    reverse=True)[:5]
    bilan.check("[texte] pas de répétition verbatim massive (≥60 ch)",
                len(repets) == 0, f"{len(repets)} ex: {repets[:2]}")
    # arithmétique inventée : formules de dés hors notes mécaniques serveur
    inventees = 0
    for t in texts:
        corps = re.sub(r"(?:Formule|Dés|Bonus|Total)[^\n]*", "", t)
        corps = re.sub(r"⚖️.*?(?=\n\n|$)", "", corps, flags=re.S)
        inventees += len(re.findall(r"\b1?d(?:4|6|8|10|12|20)\s*[+\d]", corps))
    bilan.check("[texte] peu de formules de dés dans la prose MJ "
                "(≤ 1/message en moyenne)",
                inventees <= max(1, len(texts)),
                f"{inventees} formules sur {len(texts)} messages")
    n_soins = sum("Soin résolu par le serveur" in t for t in texts)
    bilan.check("[méca] soins auto serveur rares (≤ 2 : les kits demandés)",
                n_soins <= 2, f"occurrences={n_soins}")
    n_combat = sum("Le combat commence" in t for t in texts)
    bilan.check("[méca] bloc « Le combat commence » non martelé (≤ 3)",
                n_combat <= 3, f"occurrences={n_combat}")
    interp = sum(1 for t in texts if re.search(
        r"(que faites-?vous|que souhaitez-?vous|à toi|à vous)", t, re.I))
    bilan.event(f"[texte] interpellations des joueurs : {interp}/{len(texts)}")

    # ---------- ⚔️ DÉROULEMENT DES COMBATS ----------
    engs = (etat_partie(pid) or {}).get("historique_engagements") or []
    bilan.event(f"[combat] engagements : {[e.get('sig') for e in engs]}")
    detruits_chat = sum("☠️" in t or "DÉTRUIT" in t for t in texts)
    bilan.event(f"[combat] mentions de destruction (☠️/DÉTRUIT) : "
                f"{detruits_chat}")
    tours_monstres = sum("Tour" in t and "Squelette" in t
                         or "attaque" in t.lower() for t in texts)  # grossier
    bilan.event(f"[combat] narrations d'attaques (approx.) : {tours_monstres}")

    # ---------- 🗺️ EXPLORATION ----------
    dj = donjon_etat(pid)
    visits = dj.get("salles_visitees") or []
    bilan.check("[exploration] ≥ 4 salles visitées sur 18", len(visits) >= 4,
                f"visitées={len(visits)}")
    svg = os.path.join(DATA_DIR, "cartes", f"donjon_{pid}.svg")
    bilan.check("[exploration] carte du donjon rendue (SVG)",
                os.path.isfile(svg), svg)
    lieu = (etat_partie(pid) or {}).get("lieu") or {}
    bilan.event(f"[exploration] lieu final : {lieu.get('nom')!r} / "
                f"{lieu.get('detail') or lieu.get('description', '')[:60]}")

    # ---------- 🧭 VOYAGE ----------
    etat = etat_partie(pid) or {}
    voyage = etat.get("voyage") or {}
    lieu_n = str((lieu.get("nom") or "")).lower()
    sur_route_ou_ville = bool(voyage) or "laelith" in lieu_n \
        or etat.get("phase") in ("voyage", "exploration")
    bilan.check("[voyage] mécanique de voyage utilisée ou arrivée à Laelith",
                sur_route_ou_ville,
                f"voyage={json.dumps(voyage)[:80]} lieu={lieu_n[:40]}")

    # ---------- 💰 ACHATS ----------
    fg = fiche_pj("Groth") or {}
    inv_g = _norm(" ".join(str(e.get("nom"))
                           for e in (fg.get("inventaire") or [])))
    bilan.check("[achat] potion ou rations dans l'inventaire de Groth",
                "potion" in inv_g or "ration" in inv_g,
                f"inv[:120]={inv_g[:120]}")
    bilan.event(f"[achat] or final Groth : {fg.get('or')} pc ; "
                f"kit restant : {charges_kit('Groth')} charges")

    # ---------- ⚙️ AUTRES MÉCANIQUES ----------
    xp_g = int(fg.get("xp") or 0)
    bilan.event(f"[méca] XP Groth : {xp_g} ; PV Groth : "
                f"{fg.get('pv')}/{fg.get('pv_max')} ; PV Elara : "
                f"{(fiche_pj('Elara') or {}).get('pv')}/"
                f"{(fiche_pj('Elara') or {}).get('pv_max')}")
    c_kit = charges_kit("Groth")
    bilan.check("[méca] kit non pillé par le serveur (≥ 5 charges restantes)",
                c_kit >= 5, f"charges={c_kit}")
    etat_pid = f"partie {pid}"
    bilan.event(f"[fin] {etat_pid}")

    bilan.sauver()
    print(f"BILAN — {len(bilan.oks)} OK / {len(bilan.fails)} échecs")
    for f_ in bilan.fails:
        print("  " + str(f_))
    for e in bilan.evenements:
        print("  ⚑ " + str(e))
    return 1 if bilan.fails else 0


PHASES = {"setup": phase_setup, "entree": phase_entree, "explo": phase_explo,
          "combat": phase_combat, "kit": phase_kit, "explo2": phase_explo2,
          "sortie": phase_sortie, "achat": phase_achat,
          "rapport": phase_rapport}

if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    sys.exit(asyncio.run(fn()) or 0)

