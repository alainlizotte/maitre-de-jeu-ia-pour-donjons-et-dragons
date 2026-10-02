# -*- coding: utf-8 -*-
"""Vérification de COUVERTURE du correctif « intro 5819af94 » sur TOUS les
scénarios du catalogue.

Le correctif (prompt_builder) injecte le `detail` des objectifs structurés
(manifeste `.donjon.json`, champ `etapes`) dans le bloc 🎯 OBJECTIFS DE
QUÊTE — y compris au tour d'INTRO (avant `carte_donjon_entrer`). Le code
étant générique, ce script vérifie empiriquement, pour CHAQUE scénario du
catalogue (4 univers) :

  1. quête chargée (état d'intro : 1 PJ, phase opening_complete, SANS donjon
     — le contexte exact où l'injection du détail compte) ;
  2. pour chaque objectif structuré portant un `detail`, le détail est-il
     injecté au récap MJ ?

Scénarios sans objectifs structurés (trame simple, sans `detail`) : signalés
comme « hors périmètre du correctif » — avant/après identiques, aucune
régression possible (le bloc 🎯 n'existe simplement pas pour eux).

Usage : py scripts/verif_correctif_tous_scenarios.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server.config import load_config                        # noqa: E402
from server.llm.prompt_builder import PromptBuilder          # noqa: E402

DATA_DIR = os.path.join(_REPO, "server", "data")


def main() -> int:
    with open(os.path.join(DATA_DIR, "scenarios_catalogue.json"),
              encoding="utf-8") as f:
        cat = json.load(f)

    cfg = load_config()
    pb = PromptBuilder(cfg)

    n_scenarios = 0
    n_structures = 0          # scénarios avec objectifs structurés (🎯)
    n_detail_ok = 0           # détails injectés avec succès
    n_detail_ko = 0
    hors_perimetre: list[str] = []
    echecs: list[str] = []

    for uni in cat.get("universes") or []:
        for scen in uni.get("scenarios") or []:
            sid = str(scen.get("id") or "").strip()
            pdf = str(scen.get("pdf") or "").strip()
            titre = str(scen.get("titre") or sid)
            if not sid:
                continue
            n_scenarios += 1

            # État d'INTRO : quête chargée, 1 PJ, SANS donjon (le contexte
            # où le bloc CARTE DU DONJON — porteuse historique du détail —
            # est absent).
            etat = {
                "phase": "opening_complete",
                "pj": [{"nom": "Test", "joueur": "alain"}],
                "quete": {
                    "titre": str(scen.get("titre") or ""),
                    "source": f"[{sid}] {pdf}",
                    # Le picker réel persiste TOUJOURS la bible pour un
                    # scénario au PDF lisible (set_quest) — le bloc 🎯 vit
                    # dans _scenario_bible_bloc, qui exige une bible non
                    # vide. Structure minimale : le contenu du bloc 🎯
                    # (objectifs + détails) vient du MANIFESTE, pas d'ici.
                    "bible": {"resume": "(bible de test — structure minimale)"},
                },
            }
            recap = pb.build_recap(etat, partie_id="verif_tous_scenarios")

            # Objectifs structurés de CE scénario (même découverte que le
            # serveur : manifeste par id de scénario, condition cle/requis/
            # salles — cf. game/objectifs.py).
            from server.game.objectifs import _etapes_manifeste
            etapes = _etapes_manifeste(etat, DATA_DIR)
            if not etapes:
                hors_perimetre.append(f"{sid} ({titre})")
                continue
            n_structures += 1

            details = [str(e.get("detail") or "").strip()
                       for e in etapes if isinstance(e, dict)]
            details = [d for d in details if d]
            if not details:
                hors_perimetre.append(f"{sid} ({titre}) — etapes sans detail")
                continue
            check_bloc = "🎯 OBJECTIFS DE QUÊTE" in recap
            manquants = []
            for d in details:
                # Sondes : début du détail (hors préfixe « PRÉREQUIS… »
                # tronqué à 400 chars — comparer sur ~100 chars).
                sonde = d[:100]
                if check_bloc and sonde in recap:
                    n_detail_ok += 1
                else:
                    n_detail_ko += 1
                    manquants.append(sonde)
            if manquants:
                echecs.append(
                    f"{sid} ({titre}) — {len(manquants)}/{len(details)} "
                    "details non injectes : " + " | ".join(manquants[:2])
                )

    print("═══ COUVERTURE DU CORRECTIF — tous scénarios ═══\n")
    print(f"Scénarios du catalogue ................ {n_scenarios}")
    print(f"Avec objectifs structurés (bloc 🎯) ... {n_structures}")
    print(f"Détails injectés (tous scénarios) ..... {n_detail_ok} OK / "
          f"{n_detail_ko} ÉCHEC")
    print(f"\nHors périmètre (pas d'objectifs structurés ou sans detail — "
          f"avant/après identiques) : {len(hors_perimetre)}")
    for s in hors_perimetre:
        print(f"  · {s}")
    if echecs:
        print(f"\n❌ ÉCHECS ({len(echecs)}) :")
        for s in echecs:
            print(f"  · {s}")
        return 1
    print("\n✅ Aucun échec : le correctif est valide pour tous les scénarios "
          "à objectifs structurés du catalogue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
