# -*- coding: utf-8 -*-
"""Vérification du correctif « intro 5819af94 » — injection du `detail`
des objectifs dans le bloc 🎯 OBJECTIFS DE QUÊTE (prompt_builder).

Rejoue le contexte du tour d'INTRO de la partie 5819af94 : état SANS donjon
(l'entrée `carte_donjon_entrer` date de 19:40:41, l'intro de 19:37:53) —
le bloc CARTE DU DONJON (qui porte la trame détaillée) était donc ABSENT.
Le récap MJ doit néanmoins contenir, via le bloc 🎯 :
  - le détail « PRÉREQUIS ABSOLU : vaincre Nulentok dans son groove » ;
  - « Au départ, Teleshann remet au groupe le parchemin de la route et la
    fiole de vérité » — l'antidote exact de la boîte à Couronne.

Usage : py scripts/verif_intro_5819af94.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import replace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from server.config import PathsConfig, load_config            # noqa: E402
from server.llm.prompt_builder import PromptBuilder           # noqa: E402

DATA_DIR = os.path.join(_REPO, "server", "data")
PID = "5819af94"

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


def recap_pour_etat(etat: dict) -> str:
    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=DATA_DIR,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    return PromptBuilder(cfg).build_recap(etat)


def main() -> int:
    with open(os.path.join(DATA_DIR, f"partie_{PID}.json"), encoding="utf-8") as fh:
        etat_actuel = json.load(fh)

    # État au tour d'INTRO : pas encore de donjon (entrée à 19:40:41,
    # après l'intro 19:37:53).
    etat_intro = copy.deepcopy(etat_actuel)
    etat_intro.pop("donjon", None)
    etat_intro.pop("donjons_exploreres", None)

    for label, etat in (("état ACTUEL (donjon entré)", etat_actuel),
                        ("état INTRO (sans donjon)", etat_intro)):
        print(f"\n── Récap MJ — {label} ──")
        rc = recap_pour_etat(etat)
        check("bloc 🎯 OBJECTIFS DE QUÊTE présent",
              "🎯 OBJECTIFS DE QUÊTE" in rc)
        check("objectif « Récupérer la Couronne … chez Zendar Nulentok » présent",
              "Récupérer la Couronne de Mystra chez Zendar Nulentok" in rc)
        check("détail « PRÉREQUIS ABSOLU : vaincre Nulentok dans son groove »",
              "vaincre Nulentok dans son groove" in rc)
        check("détail « Teleshann remet … le parchemin de la route et la "
              "fiole de vérité » (antidote de la boîte)",
              "parchemin de la route et la fiole de v" in rc)
        check("détail « Tant que la Couronne n'est pas reprise, la baguette "
              "est inactive »",
              "baguette de Teleshann est inactive" in rc)

    # Contrôle négatif : le détail ne doit PAS être dupliqué (une seule
    # occurrence par bloc 🎯 + une éventuelle dans la trame CARTE du donjon).
    rc = recap_pour_etat(etat_actuel)
    n_detail = rc.count("vaincre Nulentok dans son groove")
    check("détail injecté au plus 2× (bloc 🎯 + trame CARTE du donjon)",
          1 <= n_detail <= 2, f"occurrences={n_detail}")

    print(f"\n═════════════ RÉSULTAT : {PASS} OK / {FAIL} ÉCHEC ═════════════")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
