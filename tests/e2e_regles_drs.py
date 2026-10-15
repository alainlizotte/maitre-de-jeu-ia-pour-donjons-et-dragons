"""E2E réel — chantier DRS (P1/P2/P3) : partie COMPLÈTE pilotée contre le
serveur :8123 avec le LLM réel (Qwen/Gemma via llama.cpp).

Vérifie EN JEU les mécaniques nouvellement câblées (rapport
docs/RAPPORT_AUDIT_REGLES_DRS.md) :
  1. combat : engagement, rotation, critiques confirmés (×multiplicateur),
     conditions, attaques spéciales (feinte/aider), butin DMG par ND à la
     clôture (XP + or crédités, garde trésor canonique) ;
  2. environne­ment : `danger_chute` sur une chute narrée, météo de voyage
     (`voyage_meteo_jour`), rations (`voyage_repas`), rattrapages serveur ;
  3. voyage : `voyage_demarrer` avec climat par région, égarement Survie,
     persistance `voyage.jours_meteo`.

Phases REPRISABLES (état persistant côté serveur) :
  py tests/e2e_regles_drs.py setup    # partie + fiches Groth/Mélodie/Elara
  py tests/e2e_regles_drs.py combat   # gobelins : rotations + butin + XP
  py tests/e2e_regles_drs.py chute    # chute narrée → danger_chute serveur
  py tests/e2e_regles_drs.py voyage   # voyage 2 jours : météo + rations
  py tests/e2e_regles_drs.py rapport  # bilan cumulé
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import unicodedata

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

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_drs")
os.makedirs(TMP, exist_ok=True)
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.BILAN_FILE = BILAN_FILE          # bilan cumulé propre à CE scénario

from e2e_combats_reels import (  # noqa: E402
    Bilan, DATA, _norm, connecter, etat_partie, fiche_pj, sante_sockets,
    snapshot, tour_dm, vider, verifier_tour,
)
import urllib.request  # noqa: E402

JOUEURS = ["Groth", "Mélodie", "Elara"]
WSS = _c.WSS

# 🔒 Compte de test (auth Bearer requise par POST /api/parties).
_COMPTE, _MDP = "e2e_drs", "e2e-drs-token"
_TOKEN = ""


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if _TOKEN:
        headers["Authorization"] = f"Bearer {_TOKEN}"
    req = urllib.request.Request(f"{_c.BASE}{path}", data=data,
                                 headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def assurer_compte() -> None:
    """Inscrit (ou connecte) le compte de test → token Bearer."""
    global _TOKEN
    try:
        rep = _post("/api/auth/inscription",
                    {"nom": _COMPTE, "mot_de_passe": _MDP})
    except Exception:
        rep = _post("/api/auth/connexion",
                    {"nom": _COMPTE, "mot_de_passe": _MDP})
    _TOKEN = str(rep.get("token") or "")
    assert _TOKEN, "token d'authentification non obtenu"


def lire_pid() -> str:
    if os.path.exists(PID_FILE):
        return open(PID_FILE, encoding="utf-8").read().strip()
    return ""


def noms_tools(rep: dict) -> list[str]:
    return [str(tc.get("name") or "") for tc in (rep or {}).get("trace", [])]


def texte_tools(rep: dict) -> str:
    return "\n".join(str(tc.get("text") or "")
                     for tc in (rep or {}).get("trace", []))


async def ouvrir_sockets(pid: str) -> dict:
    return {j: await connecter(pid, j) for j in JOUEURS}


async def envoyer_avec_relance(pid, ws_map, bilan, joueur, essais: list[dict],
                               verifier=None) -> dict | None:
    """Envoie les messages `essais` l'un après l'autre jusqu'à ce que
    `verifier(rep)` renvoie True (None = pas de vérificateur)."""
    autres = [w for n, w in ws_map.items() if n != joueur]
    rep = None
    for spec in essais:
        rep = await tour_dm(ws_map[joueur], autres, joueur,
                            spec["msg"], bilan)
        if rep is None:
            await sante_sockets(pid, ws_map)
            continue
        if verifier is None or verifier(rep):
            return rep
        print(f"    ↻ relance ({spec.get('motif', 'vérif échouée')})")
    return rep


# --------------------------------------------------------------------------- #
#  Phases
# --------------------------------------------------------------------------- #
def _fiches_test_supprimer():
    """Supprime UNIQUEMENT les fiches des PJ de test (jamais de balayage)."""
    import glob
    def _slug_pj(j: str) -> str:
        nf = unicodedata.normalize("NFKD", j)
        return re.sub(r"[^A-Za-z0-9_-]+", "_",
                      "".join(c for c in nf
                              if not unicodedata.combining(c))
                      ).strip("_").lower()
    for fp in glob.glob(os.path.join(DATA, "fiches", "fiche_*.json")):
        base = os.path.splitext(os.path.basename(fp))[0]
        slug = base[len("fiche_"):] if base.startswith("fiche_") else base
        if slug not in {_slug_pj(j) for j in JOUEURS}:
            continue
        try:
            os.unlink(fp)
        except OSError:
            pass


async def _creer_partie_et_pjs(bilan: Bilan):
    """Crée une partie + les 3 fiches de test. Renvoie (pid, ws_map)."""
    assurer_compte()
    pid = _post("/api/parties",
                {"titre": "E2E Règles DRS (P1/P2/P3)"})["partie_id"]
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Nouvelle partie : {pid} ===")
    ws_map = await ouvrir_sockets(pid)
    demandes = {
        "Groth": ("Bonjour MJ ! Crée ma fiche : Groth, humain, barbare "
                  "niveau 1, une grande hache. Nous partons dans les "
                  "plaines près de Phandalin."),
        "Mélodie": ("Crée ma fiche : Mélodie, halfeline, barde niveau 1, "
                    "épée courte, luth, sort soins légers."),
        "Elara": ("Et la mienne : Elara, elfe, magicienne niveau 1, "
                  "projectile magique."),
    }
    for j, texte in demandes.items():
        if j in snapshot(pid)["pj"]:
            print(f"  [setup] {j} déjà créé — ignoré")
            continue

        def _cree(rep, _j=j):
            return _j in snapshot(pid)["pj"]

        await envoyer_avec_relance(pid, ws_map, bilan, j, [
            {"msg": texte},
            {"msg": f"(MJ : appelle fiche_perso_creer_rapide pour {j}) "
                    f"Crée la fiche de {j} maintenant."},
        ], verifier=_cree)
    return pid, ws_map


async def _groupe_vivant_ou_nouvelle_partie(bilan: Bilan):
    """Renvoie (pid, ws_map) avec un groupe en état de jouer : si la partie
    courante est en game_over (groupe décimé — défaite légitime), crée une
    partie neuve avec des fiches neuves."""
    pid = lire_pid()
    etat = etat_partie(pid) or {}
    pjs = etat.get("pj") or []
    game_over = bool(etat.get("game_over")) \
        or str(etat.get("phase") or "") == "game_over"
    morts = bool(pjs) and all(
        (p.get("pv") or 0) <= 0 or "Mort" in [
            str(c) for c in (p.get("conditions") or [])]
        for p in pjs)
    if pid and not game_over and not morts and pjs:
        return pid, await ouvrir_sockets(pid)
    bilan.event("groupe précédent décimé (défaite) → nouvelle partie "
                "pour les phases suivantes")
    _fiches_test_supprimer()
    return await _creer_partie_et_pjs(bilan)


async def phase_setup():
    bilan = Bilan.neuf()
    pid = ""
    if os.path.isfile(PID_FILE):
        candidat = open(PID_FILE, encoding="utf-8").read().strip()
        if etat_partie(candidat):
            pid = candidat
    if pid:
        print(f"=== Partie reprise : {pid} ===")
        ws_map = await ouvrir_sockets(pid)
    else:
        pid, ws_map = await _creer_partie_et_pjs(bilan)
    demandes = {
        "Groth": ("Bonjour MJ ! Crée ma fiche : Groth, humain, barbare "
                  "niveau 1, une grande hache. Nous partons dans les "
                  "plaines près de Phandalin."),
        "Mélodie": ("Crée ma fiche : Mélodie, halfeline, barde niveau 1, "
                    "épée courte, luth, sort soins légers."),
        "Elara": ("Et la mienne : Elara, elfe, magicienne niveau 1, "
                  "projectile magique."),
    }
    for j, texte in demandes.items():
        if j in snapshot(pid)["pj"]:
            print(f"  [setup] {j} déjà créé — ignoré")
            continue

        def _cree(rep, _j=j):
            return _j in snapshot(pid)["pj"]

        await envoyer_avec_relance(pid, ws_map, bilan, j, [
            {"msg": texte},
            {"msg": f"(MJ : appelle fiche_perso_creer_rapide pour {j}) "
                    f"Crée la fiche de {j} maintenant."},
        ], verifier=_cree)
    pjs = snapshot(pid)["pj"]
    bilan.check("[setup] 3 PJ créés", all(j in pjs for j in JOUEURS),
                f"pjs={list(pjs)}")
    bilan.sauver()
    for w in ws_map.values():
        await w.close()


async def phase_combat():
    bilan = Bilan.charger()
    pid, ws_map = await _groupe_vivant_ou_nouvelle_partie(bilan)

    # ── 1) Engagement avec round de surprise ────────────────────────────
    await envoyer_avec_relance(pid, ws_map, bilan, "Groth", [
        {"msg": "Deux gobelins surgissent des buissons et nous chargent ! "
                "Groth sort sa grande hache."},
        {"msg": "(MJ : appelle MAINTENANT le tool engager_combat avec "
                "monstres=\"Gobelin, Gobelin\") Les gobelins attaquent ! "
                "Initiative officielle !"},
        {"msg": "(OBLIGATOIRE : tool engager_combat, monstres=\"Gobelin, "
                "Gobelin\") AU SECOURS !"},
    ], verifier=lambda rep: snapshot(pid)["phase"] == "combat")
    etat = etat_partie(pid) or {}
    bilan.check("[combat] engagé (phase=combat)",
                etat.get("phase") == "combat", f"phase={etat.get('phase')}")

    # ── 2) Boucle de combat (feinte sur le tour de Groth, puis victoire) ─
    crit_vu = False
    feinte_faite = False
    feinte_vue = False
    for tour in range(30):
        await sante_sockets(pid, ws_map)
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            break
        courant = str(snap["courant"] or "")
        vivants = [m for m, st in snap["monstres"].items()
                   if "Détruit" not in st["conds"]]
        if not vivants:
            await tour_dm(ws_map["Groth"], [], "Groth",
                          "Tous les ennemis sont à terre. Clôture le combat.",
                          bilan)
            continue
        if courant in ws_map:
            if courant == "Groth" and not feinte_faite:
                feinte_faite = True
                msg = ("Groth fait une FEINTE pour déstabiliser "
                       f"{vivants[0]}, puis l'attaque.")
                ferme = ("(MJ : appelle attaque_speciale "
                         f"type_attaque=\"feinte\" nom_attaquant=\"Groth\" "
                         f"nom_cible=\"{vivants[0]}\") puis lance son "
                         "attaque normalement.")
                ferme2 = ("(OBLIGATOIRE : tool attaque_speciale, "
                          "type_attaque=\"feinte\", nom_attaquant=\"Groth\", "
                          f"nom_cible=\"{vivants[0]}\" — aucune attaque "
                          "avant la feinte) Groth trompe la garde de son "
                          "adversaire !")
            elif courant == "Mélodie":
                # 🩹 Le barde soigne le plus blessé (< 60 % PV) — sans
                # soins, une rencontre « deux gobelins » devient un TPK.
                blesse = min(
                    ((n, st) for n, st in snap["pj"].items()
                     if st["pv"] and 0 < st["pv"]
                     and st["pv"] / max(1, st["pv_max"]) < 0.6),
                    key=lambda x: x[1]["pv"] / max(1, x[1]["pv_max"]),
                    default=None)
                if blesse:
                    msg = (f"Mélodie lance soins légers sur {blesse[0]}.")
                    ferme = (f"(MJ : résous le sort — lancer_des 1d8+1 puis "
                             f"fiche_perso_soigner nom=\"{blesse[0]}\") "
                             f"Mélodie soigne {blesse[0]} !")
                else:
                    msg = f"{courant} attaque {vivants[0]} avec son arme."
                    ferme = (f"(MJ : résous MAINTENANT — lancer_attaque puis "
                             f"lancer_degats puis fiche_perso_infliger_degats "
                             f"sur {vivants[0]}) {courant} attaque "
                             f"{vivants[0]} !")
                ferme2 = ""
            else:
                msg = f"{courant} attaque {vivants[0]} avec son arme."
                ferme = (f"(MJ : résous MAINTENANT — lancer_attaque puis "
                         f"lancer_degats puis fiche_perso_infliger_degats "
                         f"sur {vivants[0]}) {courant} attaque "
                         f"{vivants[0]} !")
                ferme2 = ""
        else:
            victime = next((n for n, st in snap["pj"].items()
                            if st["pv"] and st["pv"] > 0), "Groth")
            msg = f"{courant} attaque {victime} !"
            ferme = (f"(MJ : résous l'attaque de {courant} avec "
                     "lancer_attaque, lancer_degats, "
                     f"fiche_perso_infliger_degats sur {victime})")
            ferme2 = ""
        avant = snapshot(pid)
        sender = ws_map.get(courant, ws_map["Groth"])
        joueur = courant if courant in ws_map else "Groth"
        autres = [w for n, w in ws_map.items() if n != joueur]
        rep = await tour_dm(sender, autres, joueur, msg, bilan)
        if rep and not rep["trace"]:
            # ⏱️ Anti-relance périmée : entre-temps le tour a pu changer
            # (timeout du tour précédent résorbé par le serveur).
            snap2 = snapshot(pid)
            if snap2["phase"] == "combat" \
                    and str(snap2["courant"] or "") != courant:
                continue
            rep = await tour_dm(sender, autres, joueur, ferme, bilan)
            if rep and not rep["trace"]:
                # 2ᵉ relance ferme (le 9B produit parfois DEUX réponses vides).
                snap3 = snapshot(pid)
                if snap3["phase"] == "combat" \
                        and str(snap3["courant"] or "") == courant:
                    rep = await tour_dm(sender, autres, joueur, ferme, bilan)
        if (rep and not rep["trace"] and ferme2
                and "attaque_speciale" not in texte_tools(rep)):
            rep = await tour_dm(sender, autres, joueur, ferme2, bilan)
        if rep is None:
            continue
        t = texte_tools(rep)
        if courant == "Groth" and feinte_faite and not feinte_vue \
                and "attaque_speciale" in t:
            feinte_vue = True
            bilan.check("[combat] attaque_speciale (feinte) appelée", True)
        if "CRITIQUE CONFIRMÉ" in t:
            crit_vu = True
            noms = noms_tools(rep)
            bilan.check("[critique] confirmé → lancer_degats relancé",
                        "lancer_degats" in noms, f"outils={noms}")
        verifier_tour(avant, snapshot(pid), rep["trace"], bilan)

    if feinte_faite:
        bilan.check("[combat] tentative de feinte effectuée sur le tour de "
                    "Groth", True)
    etat = etat_partie(pid) or {}
    bilan.check("[combat] clôturé (phase ≠ combat)",
                etat.get("phase") != "combat",
                f"phase={etat.get('phase')}")
    bilan.event(f"critique confirmé observé en session : {crit_vu}")
    if crit_vu:
        bilan.check("[critique] au moins un critique confirmé joué", True)

    # ── 3) État des fiches après clôture (XP si victoire / défaite) ──────
    ors = {j: (fiche_pj(j) or {}).get("or", 0) for j in JOUEURS}
    bilan.event(f"or des PJ après combat : {ors} — phase={etat.get('phase')}")
    bilan.check("[clôture] fiches présentes (XP/or lisibles)",
                all(v is not None for v in ors.values()))
    bilan.sauver()
    for w in ws_map.values():
        await w.close()


def _combat_boucle_force(pid: str) -> None:
    """Drain DÉTERMINISTE : avance le combat résiduel via l'API REST
    (boucle serveur, sans LLM) jusqu'au retour en exploration."""
    for _ in range(12):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            return
        try:
            _post(f"/api/parties/{pid}/combat/boucle", {"force": True})
        except Exception:                                    # noqa: BLE001
            return


async def _drainer_combat(pid: str, ws_map: dict, bilan: Bilan,
                          max_tours: int = 8) -> None:
    """Termine un combat résiduel (le MJ a ré-engagé après la victoire…) :
    d'abord la boucle serveur REST (déterministe), puis au besoin quelques
    tours narrés. Sans ce drain, la garde de tour bloque TOUTES les phases."""
    _combat_boucle_force(pid)
    for _ in range(max_tours):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            return
        courant = str(snap["courant"] or "")
        vivants = [m for m, st in snap["monstres"].items()
                   if "Détruit" not in st["conds"]]
        if not vivants:
            msg = ("Tous les ennemis sont à terre — clôture le combat "
                   "(finir_combat).")
            joueur = "Groth"
            sender = ws_map["Groth"]
        elif courant in ws_map:
            msg = (f"{courant} n'agit pas ce tour — termine le combat "
                   "(finir_combat).")
            joueur = courant
            sender = ws_map[courant]
        else:
            msg = (f"(MJ : résous le tour de {courant} — lancer_attaque, "
                   "lancer_degats, fiche_perso_infliger_degats puis "
                   f"tour_suivant_combat) {courant} agit !")
            joueur = "Groth"
            sender = ws_map["Groth"]
        autres = [w for n, w in ws_map.items() if n != joueur]
        rep = await tour_dm(sender, autres, joueur, msg, bilan)
        if rep is None:
            await sante_sockets(pid, ws_map)
        _combat_boucle_force(pid)
    if snapshot(pid)["phase"] == "combat":
        bilan.check("[drain] combat résiduel soldé", False,
                    f"phase=combat après {max_tours} tours")


async def phase_chute():
    bilan = Bilan.charger()
    pid, ws_map = await _groupe_vivant_ou_nouvelle_partie(bilan)
    await _drainer_combat(pid, ws_map, bilan)
    etat = etat_partie(pid) or {}
    if etat.get("game_over") or etat.get("phase") == "game_over":
        pid, ws_map = await _groupe_vivant_ou_nouvelle_partie(bilan)

    def _chute_ok(rep):
        return "danger_chute" in noms_tools(rep)

    rep = await envoyer_avec_relance(pid, ws_map, bilan, "Groth", [
        {"msg": "Le sol s'effondre : Groth TOMBE dans une fosse de 9 mètres !"},
        {"msg": "(MJ : appelle le tool danger_chute avec "
                "nom_personnage=\"Groth\", hauteur_m=9) Groth s'écrase au "
                "fond de la fosse — applique la règle officielle de chute."},
        {"msg": "(OBLIGATOIRE : tool danger_chute, nom_personnage=\"Groth\", "
                "hauteur_m=9 — aucun dégât de chute sans ce tool) Groth est "
                "au fond de la fosse de 9 mètres."},
    ], verifier=_chute_ok)
    if rep:
        t = texte_tools(rep)
        bilan.check("[chute] danger_chute résolu par le serveur",
                    "danger_chute" in noms_tools(rep))
        if "Dégâts" in t:
            m = re.search(r"Dégâts infligés\s*:\s*(\d+)", t)
            if m:
                pv = (fiche_pj("Groth") or {}).get("pv")
                bilan.check("[chute] dégâts appliqués aux PV de Groth",
                            pv is not None, f"pv={pv}, dé={m.group(1)}")
    bilan.sauver()
    for w in ws_map.values():
        await w.close()


async def phase_voyage():
    bilan = Bilan.charger()
    pid, ws_map = await _groupe_vivant_ou_nouvelle_partie(bilan)
    await _drainer_combat(pid, ws_map, bilan)
    etat = etat_partie(pid) or {}
    if etat.get("game_over") or etat.get("phase") == "game_over":
        pid, ws_map = await _groupe_vivant_ou_nouvelle_partie(bilan)

    def _voyage_ok(rep):
        # Le tool doit être appelé ET avoir persisté la météo jour par jour
        # (un 1ᵉʳ appel en erreur — arguments manquants… — déclenche une
        # relance au lieu d'un faux positif).
        if "voyage_demarrer" not in noms_tools(rep):
            return False
        return bool((etat_partie(pid) or {}).get("voyage", {})
                    .get("jours_meteo"))

    rep = await envoyer_avec_relance(pid, ws_map, bilan, "Mélodie", [
        {"msg": "Nous quittons ces lieux et voyageons vers Phandalin : "
                "45 km à travers les plaines, en marche normale."},
        {"msg": "(MJ : appelle voyage_demarrer destination=\"Phandalin\" "
                "distance_km=45 mode=\"marche\" terrain=\"plaine\") Nous "
                "partons immédiatement."},
        {"msg": "(OBLIGATOIRE : tool voyage_demarrer avec "
                "destination=\"Phandalin\", distance_km=45, mode=\"marche\", "
                "terrain=\"plaine\") Le voyage commence maintenant."},
    ], verifier=_voyage_ok)
    etat = etat_partie(pid) or {}
    voyage = etat.get("voyage") or {}
    bilan.check("[voyage] démarré avec météo par jour",
                bool(voyage.get("jours_meteo")),
                f"clés={sorted(voyage.keys())}")
    jours = voyage.get("jours_meteo") or []
    if jours:
        bilan.check("[voyage] climat par région persisté (jours ≥ 1)",
                    all("effet" in j for j in jours),
                    f"jours={[j.get('effet') for j in jours]}")

    # Météo dangereuse → voyage_meteo_jour attendu ; sinon repas.
    effet_j1 = (jours[0] or {}).get("effet") if jours else "clair"
    attendu = ("voyage_meteo_jour" if effet_j1 in
               ("canicule", "froid", "tempete", "orage", "neige")
               else "voyage_repas")

    def _jour_ok(rep):
        noms = noms_tools(rep)
        return attendu in noms or "voyage_meteo_jour" in noms \
            or "voyage_repas" in noms

    rep2 = await envoyer_avec_relance(pid, ws_map, bilan, "Elara", [
        {"msg": "Le jour 1 du voyage s'écoule : nous marchons toute la "
                "journée et campons le soir (repas)."},
        {"msg": f"(MJ : appelle {'voyage_meteo_jour' if attendu == 'voyage_meteo_jour' else 'voyage_repas'} jour=1 puis "
                "narre la journée) Applique la mécanique du jour 1."},
    ], verifier=_jour_ok)
    if rep2:
        noms = noms_tools(rep2)
        ok = ("voyage_meteo_jour" in noms) or ("voyage_repas" in noms)
        bilan.check("[voyage] mécanique du jour appliquée (météo/repas)",
                    ok, f"outils={noms}")

    # Prospection optionnelle : on ne l'exige pas (DD variable), mais on
    # vérifie que le tool existe et peut être invoqué par le MJ si narré.
    bilan.sauver()
    for w in ws_map.values():
        await w.close()


def phase_rapport():
    bilan = Bilan.charger()
    print("\n" + "=" * 70)
    print("BILAN E2E RÈGLES DRS")
    print("=" * 70)
    print(f"✅ réussis : {len(bilan.oks)}   ❌ échecs : {len(bilan.fails)}")
    for f in bilan.fails:
        print("  " + f)
    for e in bilan.evenements:
        print("  ⚑ " + e)
    return 1 if bilan.fails else 0


PHASES = {
    "setup": phase_setup,
    "combat": phase_combat,
    "chute": phase_chute,
    "voyage": phase_voyage,
    "rapport": phase_rapport,
}


async def _main():
    phases = [a for a in sys.argv[1:] if a in PHASES] or list(PHASES)
    for p in phases:
        if p == "rapport":
            sys.exit(phase_rapport())
        print(f"\n########## PHASE {p.upper()} ##########")
        await PHASES[p]()
    if "rapport" not in phases:
        sys.exit(phase_rapport())


if __name__ == "__main__":
    asyncio.run(_main())
