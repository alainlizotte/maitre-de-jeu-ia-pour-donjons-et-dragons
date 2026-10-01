"""E2E RÉEL — les dons passifs fraîchement implémentés sont EFFECTIFS en jeu.

Joue une VRAIE partie via WebSocket contre le serveur :8123 (LLM Gemma vrai)
avec un guerrier portant les dons nouvellement mécanisés, et vérifie dans la
TRACE des tool calls du tour MJ :

  setup : création de la fiche PAR LE CHAT (dons enregistrés tels quels) ;
  c1    : attaque à l'épée longue → bonus total = BBA + FOR + 1
          (« Arme de prédilection ») ET « Zone de critique 17-20 »
          (Science de la critique, base épée longue 19-20 doublée) ;
  c2    : tir à l'arc à 5 m → « Tir de près » (+1 attaque avec
          `distance_m` fourni, +1 dégâts dans lancer_degats) ;
  ca    : chemin FORMULAIRE (/api/persos authentifié) → « Esquive »
          ajoute +1 à la CA calculée serveur (miroir : sans le don) ;
  rapport : bilan cumulé.

Chaque tour est aussi passé au `verifier_tour` du harnais (application
EXACTE des dégâts, morts conformes) → surveillance de non-régression des
mécaniques existantes en conditions réelles.

  py tests/e2e_dons_passifs.py setup
  py tests/e2e_dons_passifs.py c1
  py tests/e2e_dons_passifs.py c2
  py tests/e2e_dons_passifs.py fin
  py tests/e2e_dons_passifs.py ca
  py tests/e2e_dons_passifs.py nettoyage
  py tests/e2e_dons_passifs.py rapport
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import urllib.error
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e2e_combats_reels as _c  # noqa: E402

# Isolation de CETTE campagne : pid/bilan/fiches dédiés. ⚠️ JOUEURS ne cite
# AUCUN perso réel : `nettoyer` efface les fiches de ces noms-là uniquement.
TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_dons_passifs")
os.makedirs(TMP, exist_ok=True)
_c.PID_FILE = os.path.join(TMP, "pid.txt")
_c.BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.TMP = TMP
_c.JOUEURS = ["DoranE2E", "TempCA-E2E", "TempCA2-E2E"]

from e2e_combats_reels import (  # noqa: E402
    Bilan, connecter, etat_partie, fiche_pj, snapshot, tour_dm,
    verifier_tour, vider,
)

BASE = _c.BASE
PJ = "DoranE2E"
TOKEN = ""   # posé par phase_setup (compte e2e_dons_testeur)
DEMANDE_FICHE = (
    "Bonjour MJ ! Crée ma fiche : DoranE2E, humain, guerrier niveau 1. "
    "FOR 16, DEX 14, CON 14, INT 10, SAG 12, CHA 10. Équipement : une "
    "épée longue, un arc court et 20 flèches. Ses dons sont exactement : "
    "Arme de prédilection (épée longue), Science de la critique (épée "
    "longue), Tir de près, Esquive. Nous partons à l'aventure !"
)
REPRISE_FICHE = (
    "(MJ : appelle MAINTENANT fiche_perso_creer pour DoranE2E — humain "
    "guerrier niveau 1, FOR 16 DEX 14 CON 14 INT 10 SAG 12 CHA 10, "
    "équipement : épée longue + arc court + 20 flèches, dons_json=[\"Arme "
    "de prédilection (épée longue)\", \"Science de la critique (épée "
    "longue)\", \"Tir de près\", \"Esquive\"]) Crée ma fiche !"
)

ATTENDU_BONUS_EPEE = "+5"   # BBA 1 + FOR 16 (+3) + Arme de prédilection (+1)


def _post(path: str, body: dict, token: str = "") -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _get(path: str, token: str = "") -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}", headers=({"Authorization": f"Bearer {token}"}
                                  if token else {}))
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _delete(path: str, token: str = "") -> int:
    req = urllib.request.Request(
        f"{BASE}{path}", method="DELETE",
        headers=({"Authorization": f"Bearer {token}"} if token else {}))
    try:
        return urllib.request.urlopen(req, timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code


def lire_pid() -> str:
    with open(_c.PID_FILE, encoding="utf-8") as f:
        return f.read().strip()


def outils_de(trace: list, nom: str, ok: bool = True) -> list[dict]:
    """Tool calls `nom` réussis d'une trace, avec leurs args/texte."""
    return [tc for tc in trace
            if tc.get("name") == nom and tc.get("ok") == ok]


async def tour_pj(pid: str, ws, bilan: Bilan, action: str, ferme: str):
    """Un tour du PJ (avec relance ferme si narration sans tool), puis
    vérification de non-régression (application PV / morts conformes)."""
    avant = snapshot(pid)
    rep = await tour_dm(ws, [], PJ, action, bilan)
    if rep is not None and not rep["trace"] \
            and snapshot(pid)["courant"] == PJ:
        print("    ↻ tour sans tool → relance ferme")
        rep = await tour_dm(ws, [], PJ, ferme, bilan)
    if rep is not None:
        verifier_tour(avant, snapshot(pid), rep["trace"], bilan)
    return rep


async def jouer_jusqua(pid: str, ws, bilan: Bilan, objectif,
                       action: str, ferme: str, max_tours: int = 8) -> list:
    """Boucle de combat mono-PJ : joue `action` tant que `objectif(traces)`
    n'est pas atteint ; quand c'est au monstre, le fait jouer (non-régression
    des attaques ennemies). Renvoie les traces accumulées."""
    traces: list[list[dict]] = []
    for _ in range(max_tours):
        await _c.sante_sockets(pid, {PJ: ws})
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            return traces
        courant = str(snap["courant"] or "")
        if courant != PJ:
            monstre = courant or "l'ennemi"
            await tour_pj(
                pid, ws, bilan,
                f"{monstre} attaque ! Je pars à couvert.",
                f"(MJ : résous l'attaque de {monstre} contre {PJ} avec "
                "lancer_attaque, lancer_degats, fiche_perso_infliger_degats "
                "puis tour_suivant_combat)")
            continue
        rep = await tour_pj(pid, ws, bilan, action, ferme)
        if rep is None:
            continue
        traces.append(rep["trace"])
        for tc in rep["trace"]:
            if tc.get("name") in ("lancer_attaque", "lancer_degats"):
                print(f"    📄 [{tc.get('name')}] ok={tc.get('ok')} args="
                      f"{json.dumps(tc.get('args'), ensure_ascii=False)}")
                print("\n".join(
                    "       " + l
                    for l in str(tc.get("text", ""))[:400].splitlines()))
        if objectif(traces):
            return traces
    return traces


# --------------------------------------------------------------------------- #
#  Phases
# --------------------------------------------------------------------------- #
async def phase_setup():
    bilan = Bilan.neuf()
    # Compte de test : la création de partie est protégée (Bearer).
    compte, mdp = "e2e_dons_testeur", "e2e-dons-2026!"
    global TOKEN
    try:
        TOKEN = _post("/api/auth/inscription",
                      {"nom": compte, "mot_de_passe": mdp})["token"]
    except urllib.error.HTTPError:
        TOKEN = _post("/api/auth/connexion",
                      {"nom": compte, "mot_de_passe": mdp})["token"]
    pid = _post("/api/parties", {"titre": "E2E Dons passifs (LLM)"},
                token=TOKEN)["partie_id"]
    with open(_c.PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie créée : {pid} ===")
    ws = await connecter(pid, PJ)
    rep = await tour_dm(ws, [], PJ, DEMANDE_FICHE, bilan)
    if rep is None or PJ not in snapshot(pid)["pj"]:
        rep = await tour_dm(ws, [], PJ, REPRISE_FICHE, bilan)
    # Le petit LLM oublie systématiquement l'équipement à la création
    # (constaté 3 runs de suite ; l'ajout d'inventaire n'est PAS l'objet de
    # ce test — couvert par tests/test_inventaire.py). On pose donc les
    # armes directement dans la fiche : le combat en dépend (munition B45,
    # substitution d'arme B27), les DONS, eux, sont bien créés par le chat.
    fiche_tmp = fiche_pj(PJ) or {}
    equ_tmp = json.dumps(fiche_tmp.get("equipement") or []) \
        + json.dumps(fiche_tmp.get("inventaire") or [])
    if "pée longue" not in equ_tmp:
        import re as _re
        import unicodedata as _ud
        from pathlib import Path as _Path
        slug = _re.sub(r"[^A-Za-z0-9_-]+", "_", "".join(
            c for c in _ud.normalize("NFKD", PJ)
            if not _ud.combining(c)).strip()).strip("_").lower()
        fp = _Path(_c.DATA) / "fiches" / f"fiche_{slug}.json"
        # ⚠️ la vérité runtime est `inventaire` (_consommer_fragment
        # n'y touche qu'à ça) — `equipement` est un miroir synchronisé.
        sac = [{"nom": "Épée longue", "qte": 1},
               {"nom": "Arc court", "qte": 1},
               {"nom": "flèche", "qte": 20}]
        fiche_tmp["inventaire"] = sac
        fiche_tmp["equipement"] = [dict(e) for e in sac]
        fp.write_text(json.dumps(fiche_tmp, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print("    [setup] équipement semé directement dans la fiche")
    await ws.close()

    snap = snapshot(pid)
    bilan.check("[setup] fiche de DoranE2E créée", PJ in snap["pj"],
                f"pj={list(snap['pj'])}")
    fiche = fiche_pj(PJ) or {}
    dons = [str(d) for d in (fiche.get("dons") or [])]
    dons_norm = {d.lower() for d in dons}
    for don in ("Arme de prédilection (épée longue)",
                "Science de la critique (épée longue)",
                "Tir de près", "Esquive"):
        bilan.check(f"[setup] don enregistré : {don}",
                    don.lower() in dons_norm, f"dons={dons}")
    equ = {str(e.get("nom", "")) for e in (fiche.get("equipement") or [])}
    bilan.check("[setup] épée longue portée", any("pée longue" in e for e in equ),
                f"équipement={sorted(equ)}")
    bilan.check("[setup] arc court porté", any("arc court" in e.lower()
                                               for e in equ),
                f"équipement={sorted(equ)}")
    bilan.sauver()


async def phase_c1():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, PJ)
    sockets = {PJ: ws}
    await _c.assurer_engagement(
        pid, sockets, bilan, PJ,
        "Trois gobelins jaillissent des tombes ! Je dégaine mon épée longue "
        "et frappe le plus proche en mêlée !", "Gobelin, Gobelin, Gobelin")

    def objectif(traces: list) -> bool:
        for trace in traces:
            for tc in outils_de(trace, "lancer_attaque"):
                if "pée longue" in str((tc.get("args") or {}).get("arme", "")):
                    return True
        return False

    traces = await jouer_jusqua(
        pid, ws, bilan, objectif,
        "J'attaque le gobelin le plus proche avec mon épée longue !",
        f"(MJ : résous MAINTENANT — lancer_attaque (arme=épée longue) puis "
        f"lancer_degats puis fiche_perso_infliger_degats) J'attaque avec "
        f"mon épée longue !")

    attaques = [tc for trace in traces
                for tc in outils_de(trace, "lancer_attaque")
                if "pée longue" in str((tc.get("args") or {}).get("arme", ""))]
    bilan.check("[c1] au moins une attaque à l'épée longue résolue par le "
                "serveur", bool(attaques),
                f"traces={[[tc.get('name') for tc in t] for t in traces]}")
    texts = [str(tc.get("text") or "") for tc in attaques]
    # Prédilection : soit le LLM a passé BBA+FOR+1 (total +5), soit le
    # serveur a corrigé avec la note explicite « +1 Arme de prédilection ».
    bilan.check(
        "[c1] Arme de prédilection appliquée (+1 → bonus total +5)",
        any(ATTENDU_BONUS_EPEE in t or "Arme de prédilection" in t
            for t in texts),
        "\n".join(t[:300] for t in texts[:2]))
    # Science de la critique : note UNCONDITIONNELLE pour l'épée longue.
    bilan.check(
        "[c1] Zone de critique 17-20 (Science de la critique)",
        any("Zone de critique 17-20" in t and "Science de la critique" in t
            for t in texts),
        "\n".join(t[:300] for t in texts[:2]))
    bilan.sauver()
    await ws.close()


async def phase_c2():
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, PJ)
    snap = snapshot(pid)
    if snap["phase"] != "combat":
        await _c.assurer_engagement(
            pid, {PJ: ws}, bilan, PJ,
            "Un RAT GÉANT surgit des sarcophages ! Je décoche une flèche "
            "avec mon arc court : il est à 5 mètres de moi !", "Rat géant")

    def objectif(traces: list) -> bool:
        for trace in traces:
            for tc in outils_de(trace, "lancer_attaque"):
                args = tc.get("args") or {}
                try:
                    d = float(str(args.get("distance_m")).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                if 0 < d <= 9 and "arc" in str(args.get("arme", "")).lower():
                    return True
        return False

    traces = await jouer_jusqua(
        pid, ws, bilan, objectif,
        "Mon épée ne sert pas : je décoche une FLÈCHE avec mon ARC COURT "
        "sur le rat géant, qui est à 5 mètres de moi.",
        "(MJ : résous MAINTENANT avec lancer_attaque arme=\"arc court\" "
        "distance_m=5 (PAS l'épée longue, c'est un TIR) puis lancer_degats "
        "(arme_ou_sort=arc court, distance_m=5) puis "
        "fiche_perso_infliger_degats) Je tire une flèche à l'arc à 5 m !")

    tirs = [tc for trace in traces
            for tc in outils_de(trace, "lancer_attaque")
            if "arc" in str((tc.get("args") or {}).get("arme", "")).lower()]
    tir_proche = [tc for tc in tirs
                  if _distance_tc(tc) is not None and 0 < _distance_tc(tc) <= 9]
    bilan.check("[c2] tir à l'arc résolu avec distance_m fourni",
                bool(tir_proche),
                f"args={[tc.get('args') for tc in tirs][:2]}")
    bilan.check(
        "[c2] Tir de près appliqué à l'attaque (+1)",
        any("Tir de près" in str(tc.get("text") or "") for tc in tir_proche),
        "\n".join(str(tc.get("text"))[:300] for tc in tir_proche[:2]))
    deg = [tc for trace in traces for tc in outils_de(trace, "lancer_degats")
           if "arc" in str((tc.get("args") or {}).get("arme_ou_sort", "")).lower()]
    # Voies valides (comportement ACTUEL) :
    # - la note du lancer_attaque affiche le bonus de dégâts RÉEL avec sa
    #   provenance (« Bonus dégâts officiel : +1 (… +1 Tir de près) ») ;
    # - ou le lancer_degats porte la mention (le MJ a passé distance_m) ;
    # - ou la guidance explicite quand le MJ a omis la distance.
    bilan.check(
        "[c2] Tir de près → dégâts : +1 appliqué (provenance affichée)",
        any("Tir de près" in str(tc.get("text") or "") for tc in deg)
        or any("Bonus dégâts officiel : +1" in str(tc.get("text") or "")
               and "Tir de près" in str(tc.get("text") or "")
               for tc in tirs)
        or any("+1 avec `lancer_degats`" in str(tc.get("text") or "")
               for tc in tirs),
        "deg=" + "\n".join(str(tc.get("text"))[:300] for tc in deg[:2])
        if deg else "aucun lancer_degats tracé")
    bilan.sauver()
    await ws.close()


def _distance_tc(tc: dict):
    try:
        return float(str((tc.get("args") or {}).get("distance_m"))
                     .replace(",", "."))
    except (TypeError, ValueError):
        return None


async def phase_fin():
    """Clôture du combat (non-régression : finir_combat, phase, XP)."""
    pid = lire_pid()
    bilan = Bilan.charger()
    ws = await connecter(pid, PJ)
    for _ in range(8):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            break
        vivant = _c._monstre_a_frapper(snap)
        if vivant is None:
            await tour_dm(ws, [], PJ,
                          "Tous les ennemis sont à terre — termine le combat "
                          "(finir_combat).", bilan)
            break
        courant = str(snap["courant"] or "")
        if courant != PJ:
            await tour_pj(
                pid, ws, bilan, f"{courant} attaque !",
                f"(MJ : résous l'attaque de {courant} contre {PJ} puis "
                "tour_suivant_combat)")
            continue
        await tour_pj(
            pid, ws, bilan, f"J'achève {vivant} à l'épée longue !",
            f"(MJ : lancer_attaque (arme=épée longue) puis lancer_degats "
            f"puis fiche_perso_infliger_degats sur {vivant} puis "
            "tour_suivant_combat)")
    snap = snapshot(pid)
    bilan.check("[fin] combat soldé (phase ≠ combat)", snap["phase"] != "combat",
                f"phase={snap['phase']}")
    fiche = fiche_pj(PJ) or {}
    bilan.event(f"[fin] état : phase={snap['phase']}, xp={fiche.get('xp')}, "
                f"pv={fiche.get('pv')}/{fiche.get('pv_max')}")
    bilan.sauver()
    await ws.close()


def phase_ca():
    """Chemin FORMULAIRE : « Esquive » ajoute +1 à la CA calculée serveur
    (contrôle apparié sans le don), via /api/persos authentifié."""
    bilan = Bilan.charger()
    tok = TOKEN
    if not tok:
        compte, mdp = "e2e_dons_testeur", "e2e-dons-2026!"
        try:
            tok = _post("/api/auth/inscription",
                        {"nom": compte, "mot_de_passe": mdp})["token"]
        except urllib.error.HTTPError:
            tok = _post("/api/auth/connexion",
                        {"nom": compte, "mot_de_passe": mdp})["token"]

    def _creer(nom: str, dons: list[str]) -> int:
        _delete(f"/api/persos/{_slug(nom)}", tok)   # idempotent
        return _post("/api/persos", {
            "nom": nom, "race": "Humain", "classe": "Guerrier", "niveau": 1,
            "carac": {"FOR": 14, "DEX": 14, "CON": 12, "INT": 10, "SAG": 10,
                      "CHA": 10},
            "equipement": [{"nom": "Armure de cuir", "qte": 1}],
            "dons": dons, "competences": {}, "or": 100,
            "alignement": "Neutre", "apparence": {},
        }, tok).get("status", 200)

    def _slug(nom: str) -> str:
        import re as _re
        import unicodedata as _ud
        return _re.sub(r"[^A-Za-z0-9_-]+", "_", "".join(
            c for c in _ud.normalize("NFKD", nom)
            if not _ud.combining(c)).strip()).strip("_").lower() or "perso"

    try:
        _creer("TempCA-E2E", ["Esquive"])
        _creer("TempCA2-E2E", ["Persuasion"])   # contrôle : pas d'effet CA
        ca_avec = (_get(f"/api/persos/{_slug('TempCA-E2E')}", tok) or {}) \
            .get("ca")
        ca_sans = (_get(f"/api/persos/{_slug('TempCA2-E2E')}", tok) or {}) \
            .get("ca")
        # 10 + cuir 2 + DEX 14 (+2, sous dex_max 6) + Esquive 1 = 15.
        bilan.check("[ca] Esquive → CA 15 (10+2 armure+2 DEX+1 don)",
                    ca_avec == 15, f"ca={ca_avec}")
        bilan.check("[ca] contrôle sans Esquive → CA 14", ca_sans == 14,
                    f"ca={ca_sans}")
    finally:
        _delete(f"/api/persos/{_slug('TempCA-E2E')}", tok)
        _delete(f"/api/persos/{_slug('TempCA2-E2E')}", tok)
        _delete("/api/auth/compte", tok)
    bilan.sauver()


def phase_nettoyage():
    pid = lire_pid()
    _c.nettoyer(pid)
    print("  [nettoyage] done")


def cmd_statut():
    try:
        pid = lire_pid()
        snap = snapshot(pid)
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    except Exception as e:                                 # noqa: BLE001
        print(f"statut indisponible : {e}")


def cmd_rapport():
    bilan = Bilan.charger()
    print(f"✅ OK : {len(bilan.oks)}   ❌ ÉCHECS : {len(bilan.fails)}")
    for f in bilan.fails:
        print("  " + f)
    for e in bilan.evenements:
        print("  ⚑ " + e)
    sys.exit(1 if bilan.fails else 0)


PHASES = {
    "setup": phase_setup, "c1": phase_c1, "c2": phase_c2, "fin": phase_fin,
    "ca": phase_ca, "nettoyage": phase_nettoyage, "statut": cmd_statut,
    "rapport": cmd_rapport,
}

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "rapport"
    fn = PHASES.get(phase)
    if fn is None:
        print(f"phase inconnue : {phase} — options : {', '.join(PHASES)}")
        sys.exit(2)
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()
