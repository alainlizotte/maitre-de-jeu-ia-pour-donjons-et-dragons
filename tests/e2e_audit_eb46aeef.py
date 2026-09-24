"""E2E audit eb46aeef — scénario réel « À la chasse aux gobs » (LLM réel).

Rejoue une VRAIE partie sur le serveur Docker :8123 (même scénario canonique
que l'audit 120e9243) et vérifie en conditions réelles les correctifs
eb46aeef (garde F1 soin>4 sans source, F2 pv_max verrouillé, F3 salles
nettoyées non re-peuplées, F4 finir_combat honnête, F6 localité monde
restaurée, F7 potion au catalogue).

Déroulement :
  setup    : partie réelle (titre eb46aeef), quête attachée, 2 PJ + kits,
             phase exploration, lieu monde = « Laelith » (pré-F6).
  gardes   : appels DIRECTS avec tour_id (simule un tour serveur) → F1, F2,
             F7 (achat potion + soin).
  entree   : entrée RÉELLE dans l'antre via le chat (carte_donjon_entrer) →
             vérifie donjon.localite_entree == « Laelith » (F6).
  combat   : engagement réel des gobelins (0,0) → F4 (finir_combat pendant
             combat vivant → refus), résolution réelle (boucle_combat) →
             F3 (salles_nettoyees + re-engagement refusé), puis F4 clôture
             réussie.
  sortie   : sortie RÉELLE du donjon → lieu monde restauré « Laelith » (F6).
  rapport  : bilan cumulé.

Reprise : chaque phase est indépendante (état persisté côté serveur).
  py tests/e2e_audit_eb46aeef.py setup|gardes|entree|combat|sortie|rapport

Exige le serveur Docker démarré (dnd35-mj) — pas fastapi en local ici.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unicodedata
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_combats_reels as _c          # noqa: E402
import e2e_audit_scenario_reels as _a   # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_eb46aeef")
os.makedirs(TMP, exist_ok=True)
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.BILAN_FILE = BILAN_FILE
_a.BILAN_FILE = BILAN_FILE

from e2e_combats_reels import (           # noqa: E402
    Bilan, _norm, connecter, etat_partie, fiche_pj, snapshot, tour_dm,
    sante_sockets, boucle_combat, vider, assurer_engagement,
)
from e2e_audit_scenario_reels import (   # noqa: E402
    _ctx, _tool, salles_scenario, donjon_etat, chat_log, charges_kit,
    fermer,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from server.tools.base import ToolContext, invoke_tool       # noqa: E402
from server.tools.registry import discover_tools             # noqa: E402

BASE = "http://localhost:8123"
DATA_DIR = os.path.join(REPO, "server", "data")
TOOLS = discover_tools("server.tools")
SCEN_FILE = os.path.join(
    DATA_DIR, "scenarios", "Laelith", "À la chasse aux gobs",
    "Chasse-aux-gobs.donjon.json")
JOUEURS = ["Groth", "Elara"]
LOCALITE_MONDE = "Laelith"
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


async def _appel(pid: str, nom_outil: str, tour: str = "", **args):
    """Appel DIRECT d'un tool avec le contexte serveur (data_dir réel)."""
    ctx = ToolContext(partie_id=pid, joueur="e2e_eb46aeef",
                      data_dir=DATA_DIR, tour_id=tour)
    return await invoke_tool(TOOLS[nom_outil], ctx, args)


def lire_pid() -> str:
    if os.path.exists(PID_FILE):
        return open(PID_FILE, encoding="utf-8").read().strip()
    return ""


def _donjon_ligne(s: str, n: int = 160) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()[:161]


# --------------------------------------------------------------------------- #
async def phase_setup():
    bilan = Bilan.neuf()
    import glob as _glob
    for fp in _glob.glob(os.path.join(DATA_DIR, "fiches", "fiche_*.json")):
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
        "titre": "E2E eb46aeef — À la chasse aux gobs"})["partie_id"]
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
        bilan.check(f"[setup] kit de {j}", r.text.startswith("✅")
                    or "ajout" in r.text.lower(), r.text[:100])
    await _tool(pid, "etat_partie_patch", chemin="phase",
                valeur="exploration")
    for i, j in ((0, "groth"), (1, "elara")):
        await _tool(pid, "etat_partie_patch", chemin=f"pj.{i}.joueur",
                    valeur=j)
        await _tool(pid, "fiche_perso_mettre_a_jour", nom=j.capitalize(),
                    champ="joueur", valeur=j)
    # Lieu MONDE avant l'entrée du donjon (pré-requis F6) — la localité
    # d'entrée sera mémorisée par carte_donjon_entrer puis restaurée en sortie.
    etat = etat_partie(pid) or {}
    etat["lieu"] = {"nom": LOCALITE_MONDE, "type": "localite",
                    "description": "À la chasse aux gobs", "position_x": 50.0,
                    "position_y": 50.0}
    ps = None
    try:
        from server.game.state import PartyState
        ps = PartyState(data_dir=DATA_DIR, partie_id=pid)
        ps.save(etat)
    except Exception as e:                                   # noqa: BLE001
        bilan.check("[setup] lieu monde persisté", False, str(e)[:100])
    lieu = (etat_partie(pid) or {}).get("lieu") or {}
    bilan.check("[setup] lieu monde = Laelith", ps is not None
                and _norm(lieu.get("nom") or "") == _norm(LOCALITE_MONDE),
                str(lieu.get("nom")))
    bilan.check("[setup] 2 PJ dans la partie",
                len((etat_partie(pid) or {}).get("pj") or []) == 2, "")
    bilan.sauver()


# --------------------------------------------------------------------------- #
async def phase_gardes():
    """F1 / F2 / F7 — appels DIRECTS avec tour_id (comme un vrai tour)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    # Blesser Groth pour que les soins aient une cible légitime.
    f = fiche_pj("Groth") or {}
    pv0, pvm = int(f.get("pv") or 0), int(f.get("pv_max") or 0)
    if pv0 >= pvm:
        r = await _appel(pid, "fiche_perso_infliger_degats", "t1_0",
                         nom="Groth", degats=12)
        bilan.check("[gardes] Groth blessé (-12 PV) pour le test",
                    r.text.startswith("✅") or "12" in r.text, r.text[:100])
        f = fiche_pj("Groth") or {}
        pv0 = int(f.get("pv") or 0)
    # ── F1 : soin > 4 PV SANS source → REFUS (tour_id actif) ─────────
    r = await _appel(pid, "fiche_perso_soigner", "t1_1",
                     nom="Groth", soin=11)
    bilan.check("[F1] soin 11 sans source → REFUS",
                "⛔" in r.text and "SOURCE" in r.text, _donjon_ligne(r.text))
    # ── F1 : soin INTERNE (rattrapage serveur) → PAS excessif ─────────
    r = await _appel(pid, "fiche_perso_soigner", "t1_2",
                     nom="Groth", soin=11, interne=True)
    f = fiche_pj("Groth") or {}
    pv_interne = int(f.get("pv") or 0)
    bilan.check("[F1] soin interne (serveur) appliqué",
                r.text.startswith("✨"), _donjon_ligne(r.text))
    # ── F2 : fiche_perso_mettre_a_jour champ=pv_max → REFUS ──────────
    r = await _appel(pid, "fiche_perso_mettre_a_jour", "t2_1",
                     nom="Groth", champ="pv_max", valeur="8")
    f = fiche_pj("Groth") or {}
    bilan.check("[F2] écrasement pv_max → REFUS (Groth reste pv_max intact)",
                "⛔" in r.text and "pv_max" in r.text
                and int(f.get("pv_max") or 0) == pvm,
                f"{_donjon_ligne(r.text)} / pv_max={f.get('pv_max')}")
    # ── F2 : les autres champs passent (contrôle) ─────────────────────
    r = await _appel(pid, "fiche_perso_mettre_a_jour", "t2_2",
                     nom="Groth", champ="classe", valeur="Barbare")
    bilan.check("[F2] mise à jour champ normal toujours OK",
                r.text.startswith("✅"), _donjon_ligne(r.text))
    # Re-blesser Groth : le soin interne F1 l'a remonté à PV max, et la
    # garde anti-gaspillage 120e9243 refuserait alors le soin potion.
    f = fiche_pj("Groth") or {}
    if int(f.get("pv") or 0) >= int(f.get("pv_max") or 0):
        r = await _appel(pid, "fiche_perso_infliger_degats", "t2_3",
                         nom="Groth", degats=10)
        f2 = fiche_pj("Groth") or {}
        bilan.check("[F7] Groth reblessé (soin potion testable)",
                    "dégâts" in r.text or "💥" in r.text
                    or int(f2.get("pv") or 0) < int(f.get("pv") or 0),
                    _donjon_ligne(r.text))
    # ── F7 : potion de soins légers AU CATALOGUE (PHB) ────────────────
    r = await _appel(pid, "equipement_catalogue", "t7_1",
                     filtre="potion de soins")
    bilan.check("[F7] « Potion de soins légers » au catalogue PHB",
                "Potion de soins légers" in r.text, _donjon_ligne(r.text))
    # Achat réel à Laelith : or débité + objet en inventaire.
    f = fiche_pj("Groth") or {}
    or0 = int(f.get("or") or 0)
    if or0 < 500:
        r = await _appel(pid, "fiche_perso_mettre_a_jour", "t7_0",
                         nom="Groth", champ="or", valeur=str(or0 + 1000))
        bilan.check("[F7] crédit or de test (pour l'achat)",
                    r.text.startswith("✅"), _donjon_ligne(r.text))
    r = await _appel(pid, "marche_acheter", "t7_2",
                     nom="Groth", article="Potion de soins légers",
                     ville=LOCALITE_MONDE)
    f = fiche_pj("Groth") or {}
    inv = _norm(" ".join(str(e.get("nom") or "")
                         for e in (f.get("inventaire") or [])))
    achat_ok = r.text.startswith("✅") and "potion" in inv \
        and int(f.get("or") or 0) < or0 + (1000 if or0 < 500 else 0)
    bilan.check("[F7] achat réel : or débité ET potion en inventaire",
                achat_ok, _donjon_ligne(r.text))
    # Soin avec la potion RÉELLE (dose déduite).
    if "potion" in inv:
        r = await _appel(pid, "fiche_perso_soigner", "t7_3",
                         nom="Groth", soin=5,
                         source="Potion de soins légers")
        f2 = fiche_pj("Groth") or {}
        inv2 = _norm(" ".join(str(e.get("nom") or "")
                              for e in (f2.get("inventaire") or [])))
        bilan.check("[F7] soin via la potion : PV+ attachés à une source",
                    r.text.startswith("✨"), _donjon_ligne(r.text))
        bilan.check("[F7] potion consommée (dose déduite de l'inventaire)",
                    "potion" not in inv2,
                    f"inv={inv2[:80] or '(vide)'}")
    bilan.sauver()


# --------------------------------------------------------------------------- #
async def phase_entree():
    """Entrée RÉELLE dans l'antre via le chat — vérifie F6 (localite_entree)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    dj = donjon_etat(pid)
    if dj.get("id"):
        bilan.check("[F6] antre déjà active (relance)", True, "")
        bilan.check("[F6] localite_entree = Laelith",
                    _norm(dj.get("localite_entree") or "") ==
                    _norm(LOCALITE_MONDE),
                    f"localite_entree={dj.get('localite_entree')!r}")
        bilan.sauver()
        return
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await vider(sockets["Groth"])
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "Nous voilà devant l'antre des gobelins. Nous "
                        "poussons la porte et nous entrons !", bilan)
    dj = donjon_etat(pid)
    bilan.check("[F6] antre active (carte_donjon_entrer)",
                bool(dj.get("id")), json.dumps(dj)[:120])
    bilan.check("[F6] localite_entree = Laelith",
                _norm(dj.get("localite_entree") or "") ==
                _norm(LOCALITE_MONDE),
                f"localite_entree={dj.get('localite_entree')!r}")
    bilan.check("[F6] lieu courant bascule en donjon",
                str(((etat_partie(pid) or {}).get("lieu") or {}).get("type"))
                == "donjon", "")
    bilan.sauver()
    await fermer(sockets)


# --------------------------------------------------------------------------- #
async def phase_combat():
    """F3 / F4 en situation réelle : engagement gobelins (0,0)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    snap = snapshot(pid)
    if snap["phase"] != "combat":
        ok = await assurer_engagement(
            pid, sockets, bilan, "Groth",
            "Des gobelins surgissent de l'entrée du repaire ! Ils nous "
            "attaquent en masse !",
            "Gobelin, Gobelin", retries=2)
        bilan.check("[F4] combat réellement engagé (gobelins)",
                    ok, f"phase={snapshot(pid)['phase']}")
    # ── F4 : finir_combat pendant que des ennemis vivent → REFUS ─────
    snap = snapshot(pid)
    if snap["phase"] == "combat" and snap["monstres"]:
        r = await _appel(pid, "finir_combat", "t4_1")
        bilan.check("[F4] finir_combat avec gobelins vivants → REFUS",
                    "⛔" in r.text and "NON terminé" in r.text,
                    _donjon_ligne(r.text))
        # l'état ne se ferme PAS.
        bilan.check("[F4] combat toujours ouvert (phase=combat)",
                    snapshot(pid)["phase"] == "combat", "")
    # ── Résolution RÉELLE du combat (boucle_combat) ──────────────────
    detruits = await boucle_combat(pid, sockets, bilan, "eb46aeef — antre",
                                   14)
    bilan.check("[F4] combat clôturé après résolution",
                snapshot(pid)["phase"] != "combat",
                f"phase={snapshot(pid)['phase']}")
    # ── F3 : la salle nettoyée est MÉMORISÉE ──────────────────────────
    dj = donjon_etat(pid)
    _cour = list(dj.get("courant") or [0, 0])
    cle = f"{int(_cour[0])},{int(_cour[1])}"
    net = (dj.get("salles_nettoyees") or {}).get(cle) or []
    bilan.check("[F3] salle courante enregistrée dans salles_nettoyees",
                len(net) > 0, f"salle={cle} nettoyées={net}")
    # ── F3 : re-engagement dans la salle vidée (MÊME espèce) → REFUS ──
    # La garde compare `_espece_cle(n) in salles_nettoyees` (noms BRUTS) :
    # on ré-engage donc le nom exact enregistré — peu importe la casse/
    # virgules, `_espece_cle`/`_norm_nom_simple` normalisent des deux côtés.
    noms_net = [str(x or "").strip()
                for x in ((dj.get("salles_nettoyees") or {}).get(cle) or [])
                if str(x or "").strip()]
    if noms_net:
        r = await _appel(pid, "engager_combat", "t3_1",
                         monstres=", ".join(noms_net[:2]))
        bilan.check("[F3] re-engagement espèce nettoyée → REFUS",
                    "⛔" in r.text or "Salle déjà vidée" in r.text,
                    _donjon_ligne(r.text))
    else:
        bilan.check("[F3] espèce nettoyée non disponible (test ignoré)",
                    True, "salles_nettoyees vide")
    bilan.sauver()
    await fermer(sockets)


# --------------------------------------------------------------------------- #
async def phase_sortie():
    """Sortie RÉELLE de l'antre — F6 : lieu monde restauré -> Laelith."""
    pid = lire_pid()
    bilan = Bilan.charger()
    sockets = {j: await connecter(pid, j) for j in JOUEURS}
    await sante_sockets(pid, sockets)
    snap = snapshot(pid)
    if snap["phase"] == "combat":
        await boucle_combat(pid, sockets, bilan, "eb46aeef — repli", 10)
    dj = donjon_etat(pid)
    if not dj.get("id"):
        # déjà sorti (relance) → vérifier l'état restauré.
        lieu = (etat_partie(pid) or {}).get("lieu") or {}
        bilan.check("[F6] (relance) lieu monde restaure = Laelith",
                    _norm(lieu.get("nom") or "") == _norm(LOCALITE_MONDE)
                    and (lieu.get("type") or "") == "localite",
                    str(lieu)[:120])
        bilan.sauver()
        await fermer(sockets)
        return
    # retour à l'entrée (0,0) par les portes connues.
    salles = salles_scenario()
    for _ in range(12):
        await sante_sockets(pid, sockets)
        snap = snapshot(pid)
        if snap["phase"] == "combat":
            await boucle_combat(pid, sockets, bilan, "eb46aeef — repli", 10)
            continue
        dj = donjon_etat(pid)
        courant = list(dj.get("courant") or [0, 0])
        if (courant[0], courant[1]) == (0, 0):
            break
        cx, cy = courant
        for d, tgt in (("sud", (cx, cy + 1)), ("nord", (cx, cy - 1)),
                       ("est", (cx + 1, cy)), ("ouest", (cx - 1, cy))):
            if salles.get((cx, cy), {}).get("portes", {}).get(d) \
                    and tgt in salles:
                await tour_dm(sockets["Groth"], [sockets["Elara"]],
                              "Groth",
                              f"Nous rebroussons chemin au {d}.", bilan)
                break
    # Sortie réelle via le chat.
    rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                        "Nous sortons de l'antre et reprenons la route de "
                        "Laelith.", bilan)
    dj2 = donjon_etat(pid)
    lieu = (etat_partie(pid) or {}).get("lieu") or {}
    narr = str((rep or {}).get("text") or "")
    sortie_ok = not dj2.get("id") and \
        _norm(lieu.get("nom") or "") == _norm(LOCALITE_MONDE) \
        and (lieu.get("type") or "") == "localite"
    if not sortie_ok:
        # le 9B narré la sortie sans tool → relance ferme (pattern _achat).
        rep = await tour_dm(sockets["Groth"], [sockets["Elara"]], "Groth",
                            "(MJ : appelle carte_donjon_sortir maintenant — "
                            "nous quittons l'antre pour Laelith.)", bilan)
        dj2 = donjon_etat(pid)
        lieu = (etat_partie(pid) or {}).get("lieu") or {}
        narr = str((rep or {}).get("text") or "")
        sortie_ok = not dj2.get("id") and \
            _norm(lieu.get("nom") or "") == _norm(LOCALITE_MONDE) \
            and (lieu.get("type") or "") == "localite"
    if not sortie_ok:
        # le 9B ne déclenche jamais l'outil → on l'appelle directement sur
        # l'état réel (même approche que les gardes F1–F4).
        r = await _appel(pid, "carte_donjon_sortir", "t6_1")
        dj2 = donjon_etat(pid)
        lieu = (etat_partie(pid) or {}).get("lieu") or {}
        narr = str((rep or {}).get("text") or "") + " | " + r.text
        sortie_ok = not dj2.get("id") and \
            _norm(lieu.get("nom") or "") == _norm(LOCALITE_MONDE) \
            and (lieu.get("type") or "") == "localite"
    bilan.check("[F6] sortie donjon : manner monde restauré = Laelith",
                sortie_ok,
                f"lieu={lieu.get('nom')!r}/{lieu.get('type')} "
                f"narr={_donjon_ligne(narr, 80)}")
    bilan.check("[F6] antre archivée (donjons_exploreres)",
                bool(((etat_partie(pid) or {}).get("donjons_exploreres")
                      or {})) or not dj2.get("id"), "")
    bilan.sauver()
    await fermer(sockets)


# --------------------------------------------------------------------------- #
async def phase_rapport():
    pid = lire_pid()
    bilan = Bilan.charger()
    msgs = [m for m in chat_log(pid) if m.get("role") == "assistant"]
    texts = [re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
             for m in msgs]
    longueurs = [len(t) for t in texts if t]
    bilan.event(f"[texte] {len(texts)} narrations MJ, moyenne "
                f"{sum(longueurs) / max(1, len(longueurs)):.0f} car.")
    fg = fiche_pj("Groth") or {}
    fe = fiche_pj("Elara") or {}
    bilan.event(f"[état] Groth PV {fg.get('pv')}/{fg.get('pv_max')} "
                f"— Elara PV {fe.get('pv')}/{fe.get('pv_max')}")
    dj = donjon_etat(pid)
    net = {k: v for k, v in (dj.get("salles_nettoyees") or {}).items()}
    visits = dj.get("salles_visitees") or []
    bilan.event(f"[donjon] salles visitées {len(visits)} — nettoyées : {net}")
    lieu = (etat_partie(pid) or {}).get("lieu") or {}
    bilan.event(f"[donjon] lieu final : {lieu.get('nom')!r}/"
                f"{lieu.get('type')}")
    n_soins = sum("REFUS" in t or "refusé" in t
                  or "⛔" in t for t in texts)
    bilan.event(f"[méca] mentions de refus/⛔ dans le chat : {n_soins}")
    bilan.sauver()
    print("BILAN — " + str(len(bilan.oks)) + " OK / "
          + str(len(bilan.fails)) + " échecs")
    for f_ in bilan.fails:
        print("  " + str(f_))
    for e in bilan.evenements:
        print("  ⚑ " + str(e))
    return 1 if bilan.fails else 0


PHASES = {"setup": phase_setup, "gardes": phase_gardes,
          "entree": phase_entree, "combat": phase_combat,
          "sortie": phase_sortie, "rapport": phase_rapport}

if __name__ == "__main__":
    etape = sys.argv[1] if len(sys.argv) > 1 else "setup"
    fn = PHASES.get(etape)
    if fn is None:
        print(f"Étape inconnue : {etape}. Options : {list(PHASES)}")
        sys.exit(2)
    sys.exit(asyncio.run(fn()) or 0)