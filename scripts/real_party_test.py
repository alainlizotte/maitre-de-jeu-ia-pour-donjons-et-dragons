# -*- coding: utf-8 -*-
"""Partie RÉELLE de bout en bout — validation des 3 corrections.

Crée une vraie partie (API live → server/data), charge The Crown of Mystra,
puis rejoue la SÉQUENCE qui avait déraillé en 87b8f286 et vérifie :
  1. Le donjon construit depuis le manifeste porte la trame (`etapes`) et le
     récap MJ affiche le bloc 📜 TRAME DU SCÉNARIO (étape 1 ⬜ salle 4,0).
  2. `voyage_demarrer` vers « Ruines de Sarr » est REFUSÉ tant que la
     Couronne (4,0) n'est pas récupérée (garde de trame).
  3. exploration jusqu'à (4,0) → `scenario_etape(terminée=true)` → trame ✅ +
     suivi de scénario peuplé.
  4. le même voyage est désormais ACCEPTÉ (séquence respectée).
  5. TPK réel via le moteur serveur (`engager_combat` + `boucle_auto`) →
     `game_over=true` + bloc 💀 GAME OVER dans le récap + visible GET API.

La partie est supprimée à la fin (nettoyage). Sortie non nulle si échec.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
import uuid
from dataclasses import replace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from server.config import PathsConfig, load_config            # noqa: E402
from server.game import combat as _combat                     # noqa: E402
from server.game.state import PartyState                      # noqa: E402
from server.llm.prompt_builder import PromptBuilder           # noqa: E402
from server.tools.base import ToolContext, invoke_tool        # noqa: E402
from server.tools.registry import discover_tools              # noqa: E402

API = "http://localhost:8123"
DATA_DIR = os.path.join(_REPO, "server", "data")
TOOLS = discover_tools("server.tools")

PASS = 0
FAIL = 0


def check(nom: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {nom}")
    else:
        FAIL += 1
        print(f"  ❌ {nom} {('- ' + detail) if detail else ''}")


def http(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


async def tool(ctx: ToolContext, nom_outil: str, **kw):
    return await invoke_tool(TOOLS[nom_outil], ctx, kw)


def recap(etat: dict) -> str:
    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=DATA_DIR,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    return PromptBuilder(cfg).build_recap(etat)


async def main() -> int:
    pid = "rl_" + uuid.uuid4().hex[:8]
    print(f"\n⚙️  Nouvelle partie réelle : {pid}\n")

    # 1. Création via l'API live (vraie session serveur) + sélection du
    #    scénario (chemin réel du picker, bible PDF incluse).
    print("── Mise en place de la partie ──")
    http("POST", "/api/parties", {
        "titre": "Test réel : trame + game over",
        "partie_id": pid,
    })
    rq = http("POST", f"/api/parties/{pid}/quest", {
        "titre": "The Crown Of Mystra",
        "pitch": "La Couronne de Mystra, artefact de pouvoir magique immense, "
                 "a disparu. Les PJ parcourront Faerun pour la retrouver avant "
                 "qu'elle ne tombe entre de mauvaises mains.",
        "source": "[ro_the_crown_of_mystra] /data/scenarios/Les Royaumes "
                  "Oubliés/The Crown of Mystra.pdf",
    })
    check("quête chargée via l'API live", bool(rq.get("ok")) and
          "crown_of_mystra" in str(rq.get("quete", {}).get("source", "")))

    ctx = ToolContext(partie_id=pid, joueur="alain", data_dir=DATA_DIR)

    # Création des PJ (voie réelle : fiche_perso_creer_rapide) + manuels.
    pjs = [
        ("Bargoum", "Nain", "Guerrier", "bob"),
        ("Thalia", "Elfe", "Magicienne", "lea"),
        ("Arwyn", "Elfe", "Rôdeuse", "sam"),
    ]
    for nom, race, classe, joueur in pjs:
        await tool(ctx, "fiche_perso_creer_rapide",
                   nom=nom, race=race, classe=classe, joueur=joueur,
                   niveau=2)
    await tool(ctx, "manuels_distribuer")
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("3 PJ créés", len(etat.get("pj") or []) == 3,
          f"pj={len(etat.get('pj') or [])}")

    # 2. Entrée dans le donjon → le manifeste porte la trame (correction).
    r = await tool(ctx, "carte_donjon_entrer", donjon_id="La Couronne de Mystra")
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    etapes = (etat.get("donjon") or {}).get("etapes") or []
    check("donjon construit depuis le manifeste + trame (3 étapes)",
          len(etapes) == 3 and "Couronne" in str(etapes[0].get("titre", "")),
          f"etapes={len(etapes)}")
    check("entrée à (0,0)",
          list((etat.get("donjon") or {}).get("courant", [])) == [0, 0])

    # 3. Récap MJ AVANT : bloc TRAME, étape 1 ⬜ (salle 4,0), pas de game over.
    rc = recap(etat)
    check("récap MJ : bloc 📜 TRAME DU SCÉNARIO", "TRAME DU SCÉNARIO" in rc)
    check("récap MJ : étape 1 ⬜ À FAIRE (salle 4,0)",
          "⬜ À FAIRE (salle 4,0)" in rc and "Couronne" in rc)
    check("récap MJ : aucun game over avant le TPK", "GAME OVER" not in rc)

    # 4. GARDE DE TRAME (la dérive réelle de 87b8f286) : voyage refusé tant
    #    que la Couronne n'est pas récupérée.
    print("\n── Garde de séquence du voyage ──")
    r = await tool(ctx, "voyage_demarrer",
                   destination="Ruines de Sarr", distance_km=45.0,
                   terrain="plaine")
    check("voyage Ruines de Sarr REFUSÉ (trame — Couronne d'abord)",
          "TRAME DU SCÉNARIO" in r.text and "refusé" in r.text, r.text[:120])
    check("signalétique `forcer=true` présente", "forcer=true" in r.text)
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("aucun voyage persisté", not (etat.get("voyage") or {}).get("destination"))

    # 5. Exploration jusqu'à (4,0) — le groove de Zendar Nulentok / la Couronne.
    print("\n── Exploration vers la Couronne (4,0) ──")
    chemin = []
    for _ in range(4):
        r = await tool(ctx, "carte_donjon_explorer", direction="est")
        if r.text.startswith("❌") or r.text.startswith("🚫"):
            break
        etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
        chemin.append(list((etat.get("donjon") or {}).get("courant", [])))
    check("chemin (0,0)→(1,0)→(2,0)→(3,0)→(4,0)",
          chemin == [[1, 0], [2, 0], [3, 0], [4, 0]], f"chemin={chemin}")

    # 6. Suivi de scénario : étape accomplie + étape courante.
    print("\n── Suivi du scénario (scenario_etape) ──")
    r1 = await tool(ctx, "scenario_etape",
                    etape="Récupérer la Couronne de Mystra chez Zendar Nulentok",
                    terminée=True)
    r2 = await tool(ctx, "scenario_etape",
                    etape="Réunir les huit gemmes dispersées sur Faerûn",
                    objectif="Atteindre les Ruines de Sarr pour la Beljuril",
                    terminée=False)
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    faites = (etat.get("quete", {}).get("bible", {}) or {}).get(
        "etapes_terminees") or []
    check("étape 1 (Couronne) marquée accomplie", any(
        "Couronne" in x for x in faites), f"faites={faites}")
    check("étape 2 (gemmes) = étape courante",
          "Réunir les huit gemmes" in
          str((etat.get("quete", {}).get("bible", {}) or {}).get("etape_courante", "")))
    rc = recap(etat)
    check("récap MJ : étape 1 ✅ ACCOMPLIE", "✅ ACCOMPLIE" in rc and "Couronne" in rc)
    check("récap MJ : étape 2 affichée", "Réunir les huit gemmes" in rc)

    # 7. Le MÊME voyage, désormais autorisé (séquence respectée).
    r = await tool(ctx, "voyage_demarrer",
                   destination="Ruines de Sarr", distance_km=45.0,
                   terrain="plaine")
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("voyage ACCEPTÉ après la Couronne (garde levée)",
          "Voyage vers Ruines de Sarr" in r.text and "⛔" not in r.text,
          r.text[:140])
    check("voyage persisté dans l'état", (etat.get("voyage") or {}).get("destination")
          == "Ruines de Sarr")

    # 8. TPK RÉEL via le moteur serveur → game_over + bloc 💀.
    print("\n── Game over (le moteur de combat réel) ──")
    # PJ à 3 PV (un coup du Magmatique les achève — comme la mort de Throk'mar).
    for nom, *_ in pjs:
        fiche = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
        pj = next((p for p in (fiche.get("pj") or []) if p.get("nom") == nom), None)
        if pj:
            await tool(ctx, "fiche_perso_infliger_degats",
                       nom=nom, degats=max(1, int(pj.get("pv", 3)) - 3))
    r = await tool(ctx, "engager_combat", monstres="Magmatique, Magmatique")
    print("   engage:", r.text.splitlines()[0][:90])
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("combat engagé (phase combat)", etat.get("phase") == "combat",
          f"phase={etat.get('phase')}")

    # Fait tourner le moteur (tours monstres automatiques) jusqu'à la fin.
    res_final = None
    for _ in range(40):
        res = await _combat.boucle_auto(ctx, force_avance=True)
        res_final = res
        if res.phase != "combat":
            break
    etat = PartyState(data_dir=DATA_DIR, partie_id=pid).load()
    check("combat terminé par le moteur", etat.get("phase") == "exploration",
          f"phase={etat.get('phase')}, raison="
          f"{res_final.combat_termine if res_final else '?'}")
    check("game_over posé par la clôture de défaite", etat.get("game_over") is True)
    check("évènement 💀 GAME OVER dans la boucle",
          any("GAME OVER" in e for e in (res_final.events if res_final else [])))

    rc = recap(etat)
    check("récap MJ : bloc 💀 GAME OVER", "GAME OVER" in rc)

    # 9. L'API live expose le même état (ce que voit l'UI / la bannière).
    live = http("GET", f"/api/parties/{pid}")["etat"]
    check("GET /api/parties/{id} : game_over=true vu par l'UI",
          live.get("game_over") is True)
    check("GET API : quête + trame toujours présentes",
          bool(live.get("quete", {}).get("titre"))
          and len((live.get("donjon") or {}).get("etapes") or []) == 3)
    check("GET API : voyage et suivi de scénario conservés",
          (live.get("voyage") or {}).get("destination") == "Ruines de Sarr"
          and bool((live.get("quete", {}).get("bible", {}) or {}).get("etapes_terminees")))

    # 10. Nettoyage : suppression via l'API live.
    try:
        http("DELETE", f"/api/parties/{pid}")
        print("\n🧹 Partie de test supprimée.")
    except Exception as e:                                       # noqa: BLE001
        print(f"\n⚠️  Nettoyage impossible : {e}")

    print(f"\n═════════════ RÉSULTAT : {PASS} OK / {FAIL} ÉCHEC ═════════════")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))