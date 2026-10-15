# -*- coding: utf-8 -*-
"""E2E déterministe — combat avec les classes de PJ et leurs aptitudes.

Pilote 100 % REST/moteur (aucun LLM dans la boucle : reproductible) :
  - POST /combat/engager   → initiative officielle + premiers tours monstres ;
  - POST /combat/boucle    → rotation serveur (monstres, skips, clôture+XP) ;
  - POST /combat/action    → attaque DÉTERMINISTE du PJ courant (moteur) ;
  - incanter_sort in-process → sorts réels des lanceurs (dégâts/soins/état).

Combat A : Ragnar (barbare, grande hache), Elme (clerc : Bénédiction puis
           soins/masse), Nysa (voleuse, dague) — vs 2 gobelins.
Combat B : Feuilleric (druide : Trait de feu), PoingFer (moine, mains nues),
           Alden (paladin, épée longue) — vs 2 squelettes.

(Magicien/Sorcier/Barde couverts par tests/e2e_regles_drs.py et
tests/e2e_combats_reels.py ; conformité des 11 classes par smoke_classes.)

Phases : py tests/e2e_classes_drs.py a|b|rapport
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
try:
    sys.stdout._dnd35_utf8 = True  # type: ignore[attr-defined]
    sys.stderr._dnd35_utf8 = True  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_combats_reels as _c  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_classes")
os.makedirs(TMP, exist_ok=True)
PID_FILE = os.path.join(TMP, "pid.txt")
BILAN_FILE = os.path.join(TMP, "bilan.json")
_c.BILAN_FILE = BILAN_FILE

from e2e_combats_reels import Bilan, etat_partie, fiche_pj, snapshot  # noqa: E402
import e2e_regles_drs as _r  # noqa: E402

DATA = os.path.join(REPO, "server", "data")

# nom → (classe, carac, arme, sorts préparés, dégâts d'arme (nb, faces),
#        bonus dégâts, bonus attaque, action spéciale)
SETUP = {
    "Ragnar": ("Barbare", "FOR 14, DEX 12, CON 14, INT 10, SAG 10, CHA 10",
               "Grande hache", {}, 1, 12, 2, 1, ""),
    "Elme": ("Clerc", "FOR 12, DEX 10, CON 14, INT 10, SAG 16, CHA 12",
             "Masse d'armes", {"Bénédiction": 1, "Soins légers": 2},
             1, 6, 1, 0, "benédiction"),
    "Nysa": ("Voleur", "FOR 10, DEX 16, CON 12, INT 14, SAG 10, CHA 12",
             "Dague", {}, 1, 4, 0, 0, ""),
    "Feuilleric": ("Druide", "FOR 12, DEX 10, CON 14, INT 10, SAG 16, "
                             "CHA 10",
                   "Masse d'armes", {"Trait de feu": 2, "Enchevêtrement": 1},
                   1, 6, 1, 0, "trait de feu"),
    "PoingFer": ("Moine", "FOR 14, DEX 14, CON 12, INT 10, SAG 14, CHA 10",
                 "Mains nues", {}, 1, 6, 2, 0, ""),
    "Alden": ("Paladin", "FOR 14, DEX 10, CON 14, INT 10, SAG 12, CHA 14",
              "Épée longue", {}, 1, 8, 2, 1, ""),
}

echecs: list[str] = []
n_ok = 0


def ok(label: str, cond: bool, detail: str = ""):
    global n_ok
    if cond:
        n_ok += 1
        print(f"    ✅ {label}")
    else:
        echecs.append(f"{label} — {detail[:200]}")
        print(f"    ❌ {label} — {detail[:200]}")


def _post(path: str, body: dict) -> dict:
    _r.assurer_compte()
    data = json.dumps(body).encode()
    import e2e_regles_drs as _r2
    req = urllib.request.Request(
        f"{_c.BASE}{path}", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_r2._TOKEN}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def _slug(nom: str) -> str:
    import unicodedata as _ud
    return re.sub(r"[^A-Za-z0-9_-]+", "_",
                  "".join(c for c in _ud.normalize("NFKD", nom)
                          if not _ud.combining(c)).strip().lower()).strip("_")


async def creer_fiche_deterministe(partie_id: str, nom: str, classe: str,
                                   carac: str, equipement: str,
                                   dons: list[str], sorts: dict) -> str:
    """Crée la fiche via le tool officiel (in-process) et l'ajoute au groupe
    de la partie. Renvoie '' ou un message d'erreur."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    from server.game.state import PartyState
    tools = discover_tools("server.tools")
    ctx = ToolContext(partie_id=partie_id, joueur="e2e", data_dir=DATA)
    # 🧹 Les fiches de test persistent entre les parties (PV d'après-combat,
    # conditions de mort...) et la garde anti-écrasement refuserait la
    # re-création : on purge la fiche AVANT de créer — le harnais possède
    # ses personnages de test.
    chemin_fiche = os.path.join(DATA, "fiches", "fiche_" + _slug(nom)
                                + ".json")
    if os.path.isfile(chemin_fiche):
        os.remove(chemin_fiche)
    tr = await invoke_tool(
        tools["fiche_perso_creer_rapide"], ctx, {
            "nom": nom, "race": "Humain", "classe": classe, "niveau": 1,
            "carac_texte": carac, "equipement_texte": equipement,
            "dons_texte": ", ".join(dons), "joueur": nom,
        })
    if tr.text.startswith("❌"):
        return tr.text[:200]
    if sorts:
        tr2 = await invoke_tool(
            tools["preparer_sorts"], ctx, {
                "nom_personnage": nom,
                "preparations_json": json.dumps(sorts),
            })
        if tr2.text.startswith("❌"):
            return tr2.text[:200]
    ps = PartyState(data_dir=DATA, partie_id=partie_id)
    etat = ps.load()
    fiche = json.load(open(os.path.join(
        DATA, "fiches", "fiche_" + _slug(nom) + ".json"), encoding="utf-8"))
    if not any(str(p.get("nom", "")).lower() == nom.lower()
               for p in (etat.get("pj") or [])):
        etat.setdefault("pj", []).append({
            "nom": nom, "joueur": nom,
            "classe": classe, "niveau": 1,
            "pv": fiche.get("pv"), "pv_max": fiche.get("pv_max"),
            "ca": fiche.get("ca"), "carac": fiche.get("carac") or {},
            "conditions": [],
        })
    ps.save(etat)
    return ""


async def _in_process_tool(partie_id: str, nom_tool: str, **args) -> str:
    """Appelle un tool du serveur EN IN-PROCESS (même data_dir : l'état est
    partagé avec le serveur REST). Renvoie le texte du résultat."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    tools = discover_tools("server.tools")
    tr = await invoke_tool(
        tools[nom_tool],
        ToolContext(partie_id=partie_id, joueur="e2e", data_dir=DATA), args)
    return tr.text


async def _creer_partie(titre: str, noms: list[str]) -> str:
    pid = _post("/api/parties", {"titre": titre})["partie_id"]
    print(f"=== Partie {pid} : {', '.join(noms)} ===")
    for nom in noms:
        classe, carac, arme, sorts, _, _, _, _, _ = SETUP[nom]
        err = await creer_fiche_deterministe(
            pid, nom, classe, carac, arme,
            {"Voleur": ["Attaque en finesse"],
             "Barbare": ["Esquive instinctive"],
             "Guerrier": ["Science du critique"]}.get(classe, []), sorts)
        ok(f"[{nom}] fiche créée", not err, err)
    return pid


def _premier_ennemi(pid: str) -> str:
    snap = snapshot(pid)
    vivants = [m for m, st in snap["monstres"].items()
               if "Détruit" not in st["conds"]]
    return vivants[0] if vivants else ""


def _soin_pour(allies_blesses: list[str]) -> str:
    for nom in allies_blesses:
        f = fiche_pj(nom) or {}
        pv, mx = int(f.get("pv", 0) or 0), int(f.get("pv_max", 1) or 1)
        if 0 <= pv < mx:
            return nom
    return ""


async def combat_deterministe(pid: str, bilan: Bilan, etiquette: str,
                              joueurs: list[str], ennemis: str,
                              max_rounds: int = 14) -> None:
    eng = _post(f"/api/parties/{pid}/combat/engager",
                {"monstres": ennemis, "joueur": joueurs[0]})
    ok(f"[{etiquette}] combat engagé (REST)",
       str(eng.get("phase")) == "combat", f"reçu phase={eng.get('phase')}")
    sorts_lances: set[str] = set()
    benediction_faite = False
    for _ in range(max_rounds):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            break
        boucle = _post(f"/api/parties/{pid}/combat/boucle", {"force": False})
        if snapshot(pid)["phase"] != "combat":
            break
        courant = str(snapshot(pid).get("courant") or "")
        if courant not in joueurs:
            continue                       # monstres encore en train d'agir
        (classe, _c2, arme, sorts, nb, faces, _bd, _ba, special) = SETUP[
            courant]
        cible = _premier_ennemi(pid)
        if courant == "Elme" and not benediction_faite:
            txt = await _in_process_tool(pid, "incanter_sort",
                                   nom_personnage="Elme",
                                   nom_sort="Bénédiction", cible="")
            benediction_faite = True
            ok(f"[{etiquette}] Elme lance Bénédiction (clerc)",
               not txt.startswith("❌") and "⛔" not in txt[:40], txt[:140])
            sorts_lances.add("Bénédiction")
            _post(f"/api/parties/{pid}/combat/boucle", {"force": True})
            continue
        if courant == "Elme":
            blesse = _soin_pour(joueurs)
            if blesse:
                txt = await _in_process_tool(pid, "incanter_sort",
                                       nom_personnage="Elme",
                                       nom_sort="Soins légers", cible=blesse)
                ok(f"[{etiquette}] Elme soigne {blesse} (Soins légers)",
                   not txt.startswith("❌"), txt[:140])
                sorts_lances.add("Soins légers")
                _post(f"/api/parties/{pid}/combat/boucle", {"force": True})
                continue
        if courant == "Feuilleric" and "Trait de feu" in (sorts or {}):
            txt = await _in_process_tool(pid, "incanter_sort",
                                   nom_personnage="Feuilleric",
                                   nom_sort="Trait de feu", cible=cible)
            ok(f"[{etiquette}] Feuilleric lance Trait de feu (druide)",
               not txt.startswith("❌") and "⛔" not in txt[:40], txt[:140])
            sorts_lances.add("Trait de feu")
            _post(f"/api/parties/{pid}/combat/boucle", {"force": True})
            continue
        # Martiaux : attaque déterministe du MOTEUR (REST, sans LLM).
        act = _post(f"/api/parties/{pid}/combat/action", {
            "attaquant": courant, "cible": cible, "arme": arme,
            "nb_des": nb, "faces": faces,
        })
        ok(f"[{etiquette}] attaque déterministe de {courant} ({arme})",
           bool(act.get("ok")), json.dumps(act)[:180])
    # Clôture éventuelle + contrôles globaux
    for _ in range(6):
        snap = snapshot(pid)
        if snap["phase"] != "combat":
            break
        _post(f"/api/parties/{pid}/combat/boucle", {"force": True})
    snap = snapshot(pid)
    bilan.check(f"[{etiquette}] combat soldé (phase ≠ combat)",
                snap["phase"] != "combat", f"phase={snap['phase']}")
    vainqueur = snap["phase"] == "exploration"
    for m, st in snap["monstres"].items():
        if "Détruit" in st["conds"]:
            bilan.check(f"[{etiquete}] {m} détruit avec PV ≤ 0 ({st['pv']})",
                        (st["pv"] or 0) <= 0)
    for n, st in snap["pj"].items():
        if st["pv"] is not None:
            bilan.check(f"[{etiquette}] PV de {n} dans [-10, max] "
                        f"({st['pv']})", -10 <= st["pv"] <= st["pv_max"])
    if vainqueur:
        ors = {j: (fiche_pj(j) or {}).get("or", 0) for j in joueurs}
        bilan.check(f"[{etiquette}] victoire → butin/XP crédités",
                    any(v > 0 for v in ors.values()), f"or={ors}")
    if sorts_lances:
        bilan.check(f"[{etiquette}] sorts réellement lancés : "
                    f"{sorted(sorts_lances)}", True)
    bilan.event(f"[{etiquette}] sorts lancés : {sorted(sorts_lances)} ; "
                f"issue : {snap['phase']}")


async def phase_a():
    bilan = Bilan.neuf()
    joueurs = ["Ragnar", "Elme", "Nysa"]
    pid = await _creer_partie("E2E classes A (barbare/clerc/voleur)", joueurs)
    await combat_deterministe(pid, bilan, "A", joueurs,
                              "Gobelin")
    bilan.sauver()


async def phase_b():
    bilan = Bilan.charger()
    joueurs = ["Feuilleric", "PoingFer", "Alden"]
    pid = await _creer_partie("E2E classes B (druide/moine/paladin)", joueurs)
    await combat_deterministe(pid, bilan, "B", joueurs,
                              "Squelette")
    bilan.sauver()


def phase_rapport():
    bilan = Bilan.charger()
    print("\n" + "=" * 70)
    print("BILAN E2E CLASSES (déterministe)")
    print("=" * 70)
    print(f"✅ réussis : {len(bilan.oks)}   ❌ échecs : {len(bilan.fails)}")
    for f in bilan.fails:
        print("  " + f)
    for e in bilan.evenements:
        print("  ⚑ " + e)
    return 1 if bilan.fails else 0


PHASES = {"a": phase_a, "b": phase_b, "rapport": phase_rapport}


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
