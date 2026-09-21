# -*- coding: utf-8 -*-
"""Régressions de la partie 5b4e2bbe (examen 2026-09-21).

Combat contre un Squelette démarré en PROSE (le MJ n'a pas appelé
`engager_combat`) : le serveur a régularisé le combat (bloc 5ter) en
concaténant le texte BRUT de l'outil à la narration du joueur. Trois
problèmes constatés :

1. tours de combat trop longs, surtout à la mort (blocs de clôture
   redondants + « ☠️ DÉTRUIT » en double) ;
2. consignes destinées au LLM et noms d'outils fuités au joueur
   (« 🎭 TA NARRATION… », « terminer_mon_tour passe la main »,
   « recopie CE bonus dans `lancer_degats` ») ;
3. prose contredisant la mécanique : le MJ a inventé « Jet d'attaque : 18
   (réussite) / Dégâts infligés : 0 » alors que la résolution officielle
   était un ÉCHEC.

Correctifs : filtre `_RE_CONSIGNES_LLM_STRIP` (5quater-g2-ter), suppression
du bloc « Dégâts appliqués automatiquement » à la clôture, mémorisation de
la PROSE SEULE (`_narration_prose_seule`), et retrait des faux jets/dégâts
en prose dans `strip_narration_artifacts`.

Usage : py -m pytest tests/test_correctifs_5b4e2bbe.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import main  # noqa: E402
from server.llm import orchestrator  # noqa: E402

# Narration mécanique de la partie 5b4e2bbe (message [13], extrait).
FUITE_LLM = (
    "🎲 **Initiative du combat**\n"
    "- **Squelette** — initiative 11 (d20=11, mod=+0)\n\n"
    "⚔️ **Combat engagé ! Tour 1 — c'est au tour de Squelette "
    "(joueur : PNJ/monstre).**\n"
    "🎭 TA NARRATION doit porter sur EXACTEMENT 1 créature(s) engagée(s) "
    "ci-dessus — PAS une de plus : si ton récit en annonçait davantage "
    "(renforts, horde), corrige-la sans anachronisme (elles n'existent pas "
    "sur le plateau).\n"
    "Ordre : Squelette → Barkrur\n"
    "_⚙️ Rotation gérée par le SERVEUR : les monstres attaquent, les mourants "
    "sont passés et l'XP distribuée automatiquement. Le joueur actif déclare "
    "son action (attaque, sort…) ; terminer_mon_tour passe la main._\n\n"
    "⚔️ **Attaque** : Barkrur [Hache à deux mains] vs Squelette (CA 15)\n"
    "- Jet brut d'attaque : 4\n"
    "- Bonus total : +4\n"
    "- **Total attaque : 8**\n"
    "- 💪 **Bonus dégâts officiel : +4** (FOR 16 (+3) ×1,5 arme à deux mains) "
    "— recopie CE bonus dans `lancer_degats` (jamais un bonus improvisé).\n"
    "- CA 15 → ❌ **Manqué**.\n"
)


def test_consignes_llm_retirees():
    clean = main._RE_CONSIGNES_LLM_STRIP.sub("", FUITE_LLM)
    assert "TA NARRATION" not in clean
    assert "terminer_mon_tour" not in clean
    assert "lancer_degats" not in clean
    assert "recopie CE bonus" not in clean
    assert "Rotation gérée par le SERVEUR" not in clean
    # La VALEUR mécanique reste (transparence) : seule la consigne part.
    assert "Bonus dégâts officiel" in clean
    assert "Total attaque : 8" in clean
    assert "Ordre : Squelette" in clean


def test_consignes_llm_munition():
    texte = (
        "⚠️ Aucune flèche dans l'inventaire — tir résolu cette fois, mais "
        "réapprovisionne-toi (inventaire_ajouter) : sans munition, plus de tir."
    )
    clean = main._RE_CONSIGNES_LLM_STRIP.sub("", texte)
    assert "inventaire_ajouter" not in clean
    assert "réapprovisionne-toi" in clean


def test_narration_prose_seule_coupe_la_mecanique():
    full = (
        "La prose du MJ.\n\nDeuxième paragraphe.\n\n"
        "⚖️ _Dégâts appliqués automatiquement :_\n\n"
        "💥 Squelette (monstre) subit 7 dégâts → PV -4/3 — ☠️ **DÉTRUIT**.\n"
        "Ennemis : Squelette -4/3 ☠️\n\n"
        "⚔️ _Résolution automatique du tour :_\n\n"
        "🏆 **Victoire !** Ennemis vaincus : Squelette (FP 1/3)\n"
    )
    assert main._narration_prose_seule(full) == (
        "La prose du MJ.\n\nDeuxième paragraphe."
    )


def test_narration_prose_seule_coupe_le_bandeau_au_tour():
    full = (
        "Prose.\n\n⚔️ **Au tour de Barkrur** (joueur alain) de décider une "
        "action.\n🎯 Ennemis vivants : Squelette."
    )
    assert main._narration_prose_seule(full) == "Prose."


def test_narration_prose_seule_sans_mecanique_inchangee():
    prose = "Il dégaine ⚔️ sa lame et frappe.\n\nLa suite."
    assert main._narration_prose_seule(prose) == prose


def test_faux_jets_et_degats_retires():
    texte = (
        "Vous frappez avec votre hache. Votre attaque a réussi, mais vous "
        "n'avez pas encore infligé de dégâts.\n\n"
        "**Jet d'attaque :** 18 (réussite)\n"
        "**Dégâts infligés :** 0 (le squelette résiste)\n\n"
        "Le squelette se redresse lentement."
    )
    out = orchestrator.strip_narration_artifacts(texte)
    assert "Jet d'attaque" not in out
    assert "Dégâts infligés" not in out
    assert "Le squelette se redresse lentement." in out
    assert "Vous frappez avec votre hache." in out


if __name__ == "__main__":  # pragma: no cover
    test_consignes_llm_retirees()
    test_consignes_llm_munition()
    test_narration_prose_seule_coupe_la_mecanique()
    test_narration_prose_seule_coupe_le_bandeau_au_tour()
    test_narration_prose_seule_sans_mecanique_inchangee()
    test_faux_jets_et_degats_retires()
    print("OK test_correctifs_5b4e2bbe")
