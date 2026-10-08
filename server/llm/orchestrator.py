"""Orchestrateur de function-calling — cœur de la fiabilité du MJ.

Remplace le pipeline tool-calling d'OpenWebUI par une boucle contrôlée par le
backend, qui :
1. tente le function-calling **natif** (schéma JSON envoyé à Ollama) ;
2. si le LLM « simule » l'appel en prose, ou si l'API native est absente,
   retombe sur un mode **prompt-based** : description des tools dans le system
   + parsing des balises `<tool name="..." key="...">` ;
3. exécute le tool, renvoie son résultat en message `role=tool`, et boucle
   jusqu'à ce que le LLM produise une vraie réponse de narration.

La détection des patterns de simulation (`*(Simulation de l'appel ...)*`,
`*(Appel de l'outil ...)*`, `*(Simulation des jets)*`) injecte un correctif
système et relance — max 2 essais — pour éviter la boucle infernale décrite
dans le guide d'installation d'OpenWebUI.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..tools.base import ToolContext, ToolResult, ToolSpec, invoke_tool
from ..game.state import PartyState
from ..tools.registry import tools_prompt_compact, tools_prompt_section, tools_schemas_all
from .client import ChatResult, Message, OllamaClient, _strip_thinking

# Ensembles de tools par phase. Un modèle 12B ne gère fiablement que ~10
# tools ; Gemma se perd au-delà de 30. On filtre dynamiquement selon la phase
# de la partie disponible sur disque. Chaque liste reste <= 12 tools.
_PHASE_TOOLS: dict[str, tuple[str, ...]] = {
    "opening": (
        "etat_partie_get",
        "etat_partie_patch",
        "fiche_perso_creer_rapide",
        "fiche_perso_recuperer",
        "lancer_caracteristiques",
        "lancer_d20",
        "lancer_sauvegarde",
        "lancer_des",
        "manuels_distribuer",
        "manuels_lister",
        "carte_joueurs_get",
        "carte_joueurs_position",
        "carte_joueurs_placer_ville",
        "ajouter_evenement_histoire",
        "set_derniere_narration",
        # NB : plus de tools scénarios ici — la quête est choisie via
        # l'interface (ScenarioPicker) à la création de la partie.
    ),
    "opening_complete": (
        "etat_partie_get",
        "etat_partie_patch",
        "fiche_perso_creer_rapide",
        "fiche_perso_recuperer",
        "fiche_perso_mettre_a_jour",
        "lancer_caracteristiques",
        "lancer_d20",
        "lancer_sauvegarde",
        "lancer_des",
        "manuels_lister",
        "monstre_consulter",
        "carte_joueurs_get",
        "carte_joueurs_placer_ville",
        "carte_donjon_entrer",
        "carte_donjon_etage",
        "carte_donjon_decrire_salle",
        "memoire_mission",
        "memoire_lieu",
        "memoire_personnage",
        "memoire_position",
        "memoire_intrigue",
        "memoire_evenement",
        # Scénario (bible + suivi d'étapes) : relire la trame et garder le
        # groupe sur les objectifs même si l'historique est tronqué.
        "scenarios_laelith_lister",
        "scenarios_laelith_charger",
        "scenario_etape",
        "ajouter_evenement_histoire",
        "set_derniere_narration",
    ),
    "exploration": (
        "etat_partie_get",
        "fiche_perso_recuperer",
        "fiche_perso_mettre_a_jour",
        "carte_donjon_entrer",
        "carte_donjon_explorer",
        "carte_donjon_get",
        "carte_donjon_etage",
        "carte_donjon_sortir",
        # Constance des salles : figer la description/l'état de chaque salle
        # pour qu'un retour sur ses pas retrouve la salle à l'identique.
        "carte_donjon_decrire_salle",
        "carte_joueurs_get",
        "carte_joueurs_deplacer",
        "carte_joueurs_placer_ville",
        "carte_joueurs_position",
        # Voyage hors donjon : durée réelle, rencontres, météo (jamais instantané).
        "voyage_demarrer",
        "monstre_consulter",
        "lancer_d20",
        "lancer_sauvegarde",
        "lancer_des",
        # Magie 3.5 : incantation validée (classe/niveau/préparation/slots),
        # mémorisation quotidienne des préparateurs, repos long.
        "incanter_sort",
        "preparer_sorts",
        "repos_long",
        # Familier (Magicien/Sorcier) / compagnon animal (Druide) : appel du
        # compagnon choisi à la création (initiative si combat en cours) et
        # renvoi (jet de Vigueur DD 15 + perte d'XP).
        "appeler_familier",
        "renvoyer_familier",
        # Marché PHB 3.5 (D&D 3.5 + règles-maison) : consulter le marché
        # local (types d'habitat, coefficients de prix), l'inventaire des
        # marchands, ACHETER (débit de l'or) / VENDRE (crédit), catalogue
        # officiel. L'auberge (repas/logement « bonne »…) est ici aussi.
        "marche_consulter",
        "marche_stock",
        "marche_acheter",
        "marche_vendre",
        "equipement_catalogue",
        "auberge_commander",
        # Transition exploration → combat : engager_combat déclenche la
        # rencontre (initiative officielle) ; TOUTE la suite (rotation,
        # attaques des monstres, clôture, XP) est gérée par le serveur.
        "engager_combat",
        # Mémoire de campagne : missions, lieux, PNJ, position (la lecture
        # est automatique via le récap, l'écriture passe par ces tools).
        "memoire_mission",
        "memoire_lieu",
        "memoire_personnage",
        "memoire_position",
        "memoire_intrigue",
        "memoire_evenement",
        # Mutations d'état HORS combat : pièges, ripostes isolées, potions,
        # XP d'histoire, butin (E2E 09/2026 : ces tools ABSENTS de la phase
        # expliquaient des tours « dégâts/soin/XP/inventaire » 100 %
        # narratifs — le modèle ne peut pas appeler un outil masqué).
        "fiche_perso_infliger_degats",
        "fiche_perso_soigner",
        "fiche_perso_gagner_xp",
        "inventaire_ramasser",
        "inventaire_ajouter",
        "inventaire_retirer",
        # Munitions hors combat (chasse, entraînement, tir de couverture) :
        # sans ce tool le compteur de flèches n'existe qu'en combat.
        "inventaire_consommer_munition",
        "inventaire_consulter",
        # Scénario : relire le livret et suivre les étapes de la trame.
        "scenarios_laelith_lister",
        "scenarios_laelith_charger",
        "scenario_etape",
        "ajouter_evenement_histoire",
        "set_derniere_narration",
    ),
    # ⚔️ En combat, le LLM ne décide QUE l'action du personnage joueur
    # courant. La rotation des tours, les monstres, la stabilisation des
    # mourants, la clôture et l'XP sont SERVEUR (game/combat.py).
    "combat": (
        "etat_partie_get",
        "lancer_attaque",
        "lancer_degats",
        "lancer_sauvegarde",
        "lancer_des",
        # Jet de compétence/caractéristique EN combat (Équilibre pour ne pas
        # tomber, Concentration pour maintenir un sort, Escalade…) : le
        # recoupement fiche (rangs + mod. carac + dons) est le même qu'en
        # exploration — `lancer_attaque` n'est PAS un test de compétence.
        "lancer_d20",
        "incanter_sort",
        "combat_ajouter_combattant",
        # Familier/compagnon animal : rappel en cours de mêlée → rejoint
        # l'initiative comme allié (renvoi possible via renvoyer_familier).
        "appeler_familier",
        "renvoyer_familier",
        "fiche_perso_recuperer",
        "fiche_perso_infliger_degats",
        "fiche_perso_soigner",
        "fiche_perso_condition",
        "fiche_perso_niveau_negatif",
        "inventaire_consommer_munition",
        "terminer_mon_tour",
        "monstre_consulter",
        # Fuite / retraite : le MJ clôt le combat quand le groupe décroche
        # ou que les ennemis se rendent/fuient (aucune XP de victoire).
        "retraite_combat",
        "memoire_intrigue",
        "memoire_evenement",
        "ajouter_evenement_histoire",
        "set_derniere_narration",
    ),
}

# Outils d'ÉCRITURE de la mémoire de campagne. Retirés temporairement quand
# un scénario choisi n'a pas de texte exploitable (voir le garde-mémoire de
# `_filter_tools_by_phase`) : sans trame officielle, leurs écritures
# persisteraient des lieux/PNJ inventés. La lecture (récap) n'est pas concernée.
_MEMOIRE_ECRITURE_TOOLS = frozenset({
    "memoire_mission", "memoire_lieu", "memoire_personnage",
    "memoire_position", "memoire_intrigue", "memoire_evenement",
})


_log = logging.getLogger("dnd35.orchestrator")

# 🛡️ B32 (audit parties complètes) : cinq sites indépendants incrémentaient
# `result.corrections`, dont trois SANS borne. Un tour consommait ainsi jusqu'à
# 12 appels `chat/completions` (boucle d'outils + corrections + rattrapages
# serveur) et pouvait durer 25 minutes. Budget GLOBAL par tour, partagé par
# tous les sites : au-delà, on narre avec ce qu'on a.
_CORRECTIONS_MAX_PAR_TOUR = 2


def _correction_autorisee(result: "OrchestratedResult") -> bool:
    """Vrai tant que le budget global de corrections du tour n'est pas épuisé."""
    return result.corrections < _CORRECTIONS_MAX_PAR_TOUR

# --------------------------------------------------------------------------- #
# 🎯 Phase de décision contrainte (correctif abd81275)
#
# Le 9B « narratif » choisit parfois la prose au lieu de l'appel d'outil
# (repos/inventaire/déplacement narrés sans tool — 15 rejeux en 15 min en
# partie réelle). La couche anti-simulation rattrape, mais au prix de rejeux
# lents et d'une narration décousue. La décision est donc ANTICIPÉE dans un
# appel LLM court contraint par `response_format: json_schema` : llama.cpp
# masque les logits à chaque token — un nom d'outil hors `enum` ou une
# réponse en prose devient STRUCTURELLEMENT impossible (pas juste découragé).
# Les outils décidés sont exécutés serveur, puis la boucle narrative
# normale raconte à partir des résultats officiels.
#
# Périmètre volontairement restreint : les actions du MONDE (exploration,
# voyage, soin, inventaire, magie, dés). Hors périmètre :
# - le COMBAT : le moteur serveur y joue déjà les tours déterministes et la
#   boucle corrective 5bis y est fiable ;
# - les tools de consultation/rédaction (etat_partie_get, memoire_*, monstre_
#   consulter, scenario_etape…) : sans effet mécanique immédiat.
# --------------------------------------------------------------------------- #
_PHASES_DECISION = frozenset({"exploration", "voyage", "roleplay"})

_OUTILS_DECISION = frozenset({
    # Déplacement / monde
    "carte_donjon_entrer", "carte_donjon_explorer", "carte_donjon_etage",
    "carte_donjon_sortir", "voyage_demarrer",
    "carte_joueurs_deplacer", "carte_joueurs_placer_ville",
    # Combat (engagement uniquement — la rotation est serveur)
    "engager_combat",
    # Repos / soin / dégâts
    "repos_long", "fiche_perso_soigner", "fiche_perso_infliger_degats",
    # Inventaire
    "inventaire_ajouter", "inventaire_ramasser", "inventaire_retirer",
    "inventaire_consommer_munition",
    # Magie 3.5
    "incanter_sort", "preparer_sorts",
    # Familier / compagnon animal (appel lié à la fiche, renvoi)
    "appeler_familier", "renvoyer_familier",
    # Marché PHB/maison : achat (débit or), vente (crédit), auberge,
    # catalogue — des décisions mécaniques comme le repos/les dégâts.
    "marche_consulter", "marche_stock", "marche_acheter", "marche_vendre",
    "equipement_catalogue", "auberge_commander",
    # Jets de dés isolés (jet de caractéristique, test de compétence…)
    "lancer_d20", "lancer_sauvegarde", "lancer_des",
})

# Garde-fou : la phase de décision ne peut déclencher que 2 outils par tour
# (une action joueur = une mécanique ; les chaînes plus longues passent par
# la boucle narrative normale).
_MAX_OUTILS_DECISION = 2

# Outils MÉCANIQUES prioritaires (« recommandation outil ») : quand le
# plafond `max_tools_exposed` réduit l'ensemble présenté, ces outils restent
# exposés en priorité (après ceux déjà exécutés ce tour). Ils ne font pas
# partie du choix contraint de `_OUTILS_DECISION` (attaque, dégâts,
# sauvegarde, fin de tour) : sans ce précis, un plafond les filtrerait en
# combat et le modèle ne pourrait physiquement pas résoudre son action.
_COMBAT_PRIORITAIRES = frozenset({
    "lancer_attaque", "lancer_degats", "lancer_sauvegarde", "lancer_des",
    "incanter_sort", "terminer_mon_tour",
    "fiche_perso_infliger_degats", "fiche_perso_soigner",
    "fiche_perso_condition", "fiche_perso_niveau_negatif",
    "inventaire_consommer_munition", "retraite_combat",
    "combat_ajouter_combattant", "appeler_familier",
})

# Plafond BASSE d'exposition des outils : en dessous de cette taille, un jeu
# d'outils de phase est présenté EN ENTIER (réordonné) plutôt que tronqué par
# `max_tools_exposed`. Voir `_sous_ensemble_prioritaire` (B22).
_PLAFOND_EXPOSITION_MIN = 48

# 🧱 Budget TOTAL du contexte d'une requête (en CARACTÈRES) : work + schémas
# d'outils + template. Les schémas natifs (function-calling « auto ») sont
# envoyés HORS de `work` et comptaient donc dans les tokens sans être bornés
# (partie 5a9b99c8 : work 40 k chars + 39 schémas 22 k chars = 21 058 tokens
# pour un ctx de 20 224 → 400 « exceeds the available context size », et quand
# llama.cpp tronque lui-même le prompt il coupe le début → system perdu →
# narration courte et coupée). Le tokenizer français Qwen coûte ~2,9
# chars/token (mesuré : 62 k chars ≈ 21 058 tokens). Contexte llama.cpp porté
# à 32 768 (`-c`, n_predict 2 048 réservé → ~30,7 k tokens de prompt, coût
# VRAM mesuré +206 Mo seulement) : budget 68 000 chars ≈ 23,4 k tokens, large
# marge sous les 30,7 k disponibles. `_borner_work` réserve en plus la place
# des schémas d'outils natifs (envoyés hors de `work`).
_REQ_BUDGET_CHARS = 68_000
# Plancher : même si les schémas sont énormes, on garde au moins ça pour le
# system + les derniers échanges (sinon le modèle raisonne à l'aveugle).
_WORK_BUDGET_MIN_CHARS = 14_000
_TRONC_MARQUEUR = (
    "…[les échanges les plus anciens de CE tour ont été retirés pour tenir "
    "dans le contexte du modèle — les résultats d'outils ESSENTIELS "
    "restent listés ci-dessous]…"
)


def _borner_work(work: list[Message], reserve_chars: int = 0) -> list[Message]:
    """Borne la taille totale de `work` : retire les messages intermédiaires
    les plus anciens (en gardant le system et les plus récents) tant que le
    total dépasse le budget. Non destructif pour l'appelant : renvoie une
    NOUVELLE liste tronquée (le `work` de la boucle reste vivant pour les
    itérations suivantes, on ne le mute pas).

    `reserve_chars` = place réservée pour ce qui est envoyé HORS de `work`
    (schémas d'outils natifs, template) : le budget effectif devient
    `_REQ_BUDGET_CHARS - reserve_chars` (plancher `_WORK_BUDGET_MIN_CHARS`),
    pour que le TOKEN TOTAL de la requête reste sous le ctx du serveur."""
    budget = max(_WORK_BUDGET_MIN_CHARS, _REQ_BUDGET_CHARS - reserve_chars)
    total = sum(len(m.content or "") for m in work)
    if total <= budget:
        return work
    garde_tete = 1 if (work and work[0].role == "system") else 0
    # Conserve depuis la FIN tant que le budget n'est pas atteint.
    gardes: list[Message] = []
    reste = budget - sum(
        len(m.content or "") for m in work[:garde_tete]
    )
    for m in reversed(work[garde_tete:]):
        l = len(m.content or "")
        if reste - l < 0 and gardes:
            break
        reste -= l
        gardes.append(m)
    gardes.reverse()
    sortie = work[:garde_tete]
    if garde_tete:
        sortie = sortie + [Message(role="system", content=_TRONC_MARQUEUR)]
    else:
        sortie = [Message(role="user", content=_TRONC_MARQUEUR)]
    sortie = sortie + gardes
    _log.info(
        "work borné : %d → %d chars (budget %d, réserve %d, %d messages "
        "conservés)",
        total, sum(len(m.content or "") for m in sortie), budget,
        reserve_chars, len(gardes),
    )
    return sortie

# Suffixe anti-écho apposé à TOUT message correctif injecté en fin de tour.
# Les petits modèles (Qwen 9B, Gemma E4B) recopient parfois la consigne
# corrective dans leur narration visible — le joueur voyait alors les
# « ⚠️ Consigne du Maître du Jeu » à l'écran (partie dc4dd5aa). Ce rappel
# fait partie du message lui-même pour contrecarrer l'écho.
_CORRECTIF_INTERNE = (
    "\n\n(Consigne INTERNE du moteur de jeu, INVISIBLE du joueur : ne la "
    "cite JAMAIS, ne la recopie JAMAIS dans ta narration, ne mentionne "
    "AUCUNE erreur technique, AUCUNE correction ni balise thinking. Produis "
    "uniquement de la narration de jeu en prose visible.)"
)


# --------------------------------------------------------------------------- #
#  Patterns de « simulation » (à détecter et corriger)
# --------------------------------------------------------------------------- #
# Tools de résolution — si l'un a déjà tourné dans le tour, la reformulation
# prose de son résultat ne compte plus comme simulation.
_DICE_TOOL_NAMES = {
    "lancer_d20", "lancer_attaque", "lancer_degats", "lancer_sauvegarde",
    "lancer_des", "calculer_initiative",
}

_SIMULATION_PATTERNS = [
    # `[^*]*?` accepte toute parenthèse interne (le text a souvent `())*`).
    re.compile(r"\*\(Simulation\s+de\s+l'appel[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*\(Appel\s+de\s+l'outil[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*\(Simulation\s+des\s+jets[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\*Simulation\s+de\s+l'appel\s+`?\w+`?\*\*", re.IGNORECASE),
    # « (L'application de l'outil X met à jour ...) » — Gemma formule aussi
    # ses simulations de cette façon (observé en partie réelle).
    re.compile(r"\*\(L'application\s+de\s+l'outil[^*]*?\)\*", re.IGNORECASE),
    re.compile(r"\(L'application\s+de\s+l'outil[^)]*\)", re.IGNORECASE),
    # « (L'outil X est appliqué : ...) » / « (Application de l'outil ...) ».
    re.compile(r"\*?\(?(?:L'outil\s+\w+\s+est\s+appliqu|Application\s+de\s+l'outil)[^)]*\)?\*?", re.IGNORECASE),
    # Prose ordinaire : "Je vais simuler l'appel ..."  (sans astérisques).
    re.compile(r"\bsimul(?:er|e|ait|ent|é)\s+?(?:l'appel|l'outil|le\s+tool|les\s+jets)\b", re.IGNORECASE),
    # Variante sans astérisques : "(Simulation de l'appel ...)".
    re.compile(r"\(Simulation\s+de\s+l'appel[^*]*?\)", re.IGNORECASE),
    re.compile(r"\(Simulation\s+des\s+jets[^*]*?\)", re.IGNORECASE),
    # Méta-placeholders observés en partie réelle (Gemma E4B) :
    # « *(Appel au tool lancer_attaque pour la dague)* »,
    # « *(Attente du résultat du jet de dés)* »,
    # « *(Le résultat du jet est appliqué et les dégâts sont calculés.)* ».
    re.compile(r"\(\s*Appel\s+au\s+tool\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\(\s*Appel\s+au\s+sort\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*Attente\s+du\s+r[ée]sultat\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\(\s*Le\s+r[ée]sultat\s+du\s+jet\s+est\s+appliqu[ée][^)]*\)", re.IGNORECASE),
    re.compile(r"\(\s*Les\s+d[ée]g[âa]ts\s+sont\s+calcul[ée]s?[^)]*\)", re.IGNORECASE),
    re.compile(r"\(\s*Le\s+jet\s+d['']attaque\s+est\s+lanc[ée][^)]*\)", re.IGNORECASE),
    # Prose de jets improvisés (Gemma E4B sans balise tool) : le modèle écrit
    # le résultat directement dans la narration au lieu d'appeler lancer_d20
    # / lancer_attaque. Exemples ciblés :
    #   "Jet d'attaque : 1d20+4 = 17"
    #   "Jet de dégâts : 2d6+2 = 9"
    #   "1d20+5 = 18, touché !"
    #   "Jet d'attaque : 1d20 + 5 (BBA) + 3 (FOR) = 18"   ← partie dfccc120 :
    #     modificateurs MULTIPLES avec labels entre parenthèses — l'ancienne
    #     regex (un seul modificateur, pas de parenthèses) laissait passer
    #     ces jets 100 % simulés, dégâts jamais appliqués.
    # Les formules sont dans _DICE_FORMULA_PATTERNS (désactivées quand un
    # tool de dés a déjà tourné : la reformulation du résultat est légitime).
]

# Formules de dés RÉCITÉES : détectées seulement quand AUCUN tool de dés n'a
# encore tourné dans le tour (`include_checks=True`, cf.
# looks_like_simulation) — quand les dés ONT été jetés, reciter la formule
# (« 1d8+3 = 7 ») est une reformulation légitime du résultat officiel.
_DICE_FORMULA_PATTERNS = [
    # "1d20+5 = 18" / "2d6+2 = 9" / "1d20 + 5 (BBA) + 3 (FOR) = 18".
    re.compile(
        r"\b\d+d\d+(?:\s*[+\-]\s*\d+|\s*\([^)]{1,25}\))*\s*[:=]\s*\d{1,3}\b",
        re.IGNORECASE,
    ),
    # "Jet d'attaque : 17" (résultat numérique nu après un label explicite).
    re.compile(
        r"\bjet\s+(?:d['']attaque|de\s+d[ée]g[âa]ts|de\s+sauvegarde)\s*[:\-]\s*\d{1,3}\b",
        re.IGNORECASE,
    ),
]

# « ✅ Fiche créée pour **X** (...) — Carac : ... » / « ✅ Fiche de X mise à
# jour : ... » / « ✅ Fiche renommée : ... » : le modèle RÉCITE la sortie d'un
# outil de fiche (fiche_perso_creer/creer_rapide/mettre_a_jour/renommer) sans
# l'avoir réellement appelé (observé avec Qwen : « fiche de Zarkon créée »
# narrée, la fiche n'existe jamais). La reformulation d'un résultat LÉGITIME
# est exemptée dans run() si l'outil a réellement tourné dans le tour.
_FICHE_CREATION_PATTERNS = [
    re.compile(r"✅\s*Fiche\s+(?:créée|creee)\s+pour\b", re.IGNORECASE),
    re.compile(r"✅\s*Fiche\s+de\s+[^:：]{1,40}\s+mise\s+à\s+jour\s*[:：]", re.IGNORECASE),
    re.compile(r"✅\s*Fiche\s+renommée\s*:", re.IGNORECASE),
]

# Outils qui ÉCRIVENT réellement une fiche : leur présence dans la trace
# légitime la reformulation de leur résultat (exemption de _FICHE_CREATION_PATTERNS).
_FICHE_ECRITURE_TOOLS = {
    "fiche_perso_creer",
    "fiche_perso_creer_rapide",
    "fiche_perso_mettre_a_jour",
    "fiche_perso_renommer",
}

# Outils CANONIQUES des gains d'état (soins, XP, inventaire) : quand l'un
# d'eux a tourné dans le tour, la reformulation en prose du gain est
# légitime — la détection _GAIN_PROSE_PATTERNS est alors désactivée.
_GAIN_TOOLS = {
    "fiche_perso_soigner",
    "fiche_perso_gagner_xp",
    "inventaire_ramasser",
    "inventaire_ajouter",
    "inventaire_retirer",
}

# Dégâts narrés en prose ("inflige 12 points de dégâts", "subit 5 dégâts") sans
# appel de lancer_degats. Séparé de _SIMULATION_PATTERNS car la reformulation
# d'un résultat de tool LÉGITIME utilise la même tournure : on ne l'active que
# si aucun tool de dés n'a encore été appelé dans le tour (cf. run()).
_DAMAGE_PROSE_PATTERNS = [
    # "inflige/infligeant/subit ... 12 (points de) dégâts" — résultat chiffré.
    # Les descriptions d'armes ("inflige 1d8 dégâts") ne matchent pas : le
    # nombre doit être immédiatement suivi de "dégâts".
    re.compile(
        r"\b(?:inflig\w*|subit|subissent|encourt)\s+(?:\*\*)?\d{1,3}(?:\*\*)?"
        r"\s*(?:points\s+de\s+)?d[ée]g[âa]ts",
        re.IGNORECASE,
    ),
    # "Résultat de l'attaque] : 14 au toucher, 12 dégâts" — sans tool
    re.compile(
        r"\[?R[ée]sultat\s+de\s+l['']attaque\]?\s*[:\.]?\s*\d+\s*au\s+toucher"
        r"[,;\s]+\d+\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
        re.IGNORECASE,
    ),
    # "te tue X points de dégâts", "lui inflige X PV de dégâts"
    re.compile(
        r"\b(?:t[''](?:a|e)\s+)?(?:inflig\w*|tue|font)\s+(?:\*\*)?\d{1,3}"
        r"(?:\*\*)?\s*(?:points?\s+(?:de\s+)?(?:vie|dégâts)|PV\s+de\s+dégâts)",
        re.IGNORECASE,
    ),
    # "遭遇" style : "X au toucher, Y dégâts" after a mention of attack
    re.compile(
        r"\b\d+\s+au\s+toucher[,;\s]+\d+\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
        re.IGNORECASE,
    ),
    # « 12 (points de) dégâts sont infligés / ont été infligés » — voix
    # passive (nombre AVANT le verbe) : échappait aux patterns actifs
    # (« inflige 12 dégâts ») et laissait des dégâts 100 % narratifs.
    re.compile(
        r"\b\d{1,3}(?:\*\*)?\s*(?:points?\s+de\s+)?d[ée]g[âa]ts?\s*"
        r"(?:\*\*)?\s*(?:sont|est|ont|a)\s+(?:\w+\s+)?inflig\w*",
        re.IGNORECASE,
    ),
    # « +2 dégâts » (« **+2 dégâts** sont infligés au Gnoll ») — forme très
    # fréquente chez Qwen/Gemma : montant écrit à la main sans lancer_degats.
    re.compile(
        r"\+\s*\d{1,3}\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
        re.IGNORECASE,
    ),
    # « PV restant du Gnoll : 7 » / « PV restants : 7 » — état de PV affirmé
    # en prose sans tool de suivi (le tool dit « PV X/Y », jamais « restant »).
    re.compile(
        r"\bPV\s+restants?\b[^.:!?\n]{0,60}?[:=]\s*\d{1,4}\b",
        re.IGNORECASE,
    ),
]


# Jets de caractéristique/compétence ANNONCÉS en prose sans être résolus :
# « Je lance un jet de Force pour forcer la porte de pierre. » — le tour
# s'arrêtait là, sans aucun dé (observé en partie réelle). Désactivés quand
# un tool de dés a déjà tourné (la reformulation du résultat est légitime).
# Partie dfccc120 : « réussit son jet d'intimidation (18/15) » et « jet de
# Discours (16/15) » — les COMPÉTENCES n'étaient pas couvertes : le modèle
# inventait réussite/échec sans jamais appeler lancer_d20.
_CHECK_PROSE_PATTERNS = [
    re.compile(
        r"\bje\s+(?:vais\s+)?(?:tenter\s+de\s+)?lancer\s+un\s+jet\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bjet\s+(?:de\s+)?(?:force|dext[ée]rit[ée]|constitution"
        r"|intelligence|sagesse|charisme)\b\s*(?:pour|…|\?|$|\.)",
        re.IGNORECASE,
    ),
    # « jet d'intimidation (18/15) », « jet de Discours : 16 / DD 15 » —
    # résultat de compétence récité avec son score et sa difficulté.
    re.compile(
        r"\bjet\s+d(?:e\s+|')[a-zà-öø-ÿ]{3,20}\b[^.!?;\n]{0,50}?"
        r"(?:\(\s*\d{1,2}\s*/\s*\d{1,3}\s*\)"
        r"|:\s*\d{1,2}\s*/\s*(?:DD\s*)?\d{1,3})",
        re.IGNORECASE,
    ),
    # « réussit son jet de Discours / son jet d'intimidation » — issue
    # affirmée sans dé : toujours une simulation quand aucun outil n'a tourné.
    re.compile(
        r"\br[ée]ussit\s+(?:son|un|le)\s+jet\b|\b[ée]choue\s+(?:son|un|le)\s+jet\b",
        re.IGNORECASE,
    ),
]


# Gains d'état AFFIRMÉS EN PROSE sans tool (soins, XP, inventaire) :
# « Throkmar récupère 5 PV », « il gagne 50 points d'expérience »,
# « j'ajoute l'épée à ton inventaire ». Comme pour les dégâts, ce sont des
# mutations d'état qui DOIVENT passer par fiche_perso_soigner /
# fiche_perso_gagner_xp / inventaire_ramasser — sinon la fiche ne bouge pas
# (observé en E2E réel : tours « soin »/« xp »/« inventaire » 100 % narratifs,
# partie 09b56d5b / f51b1be0). Désactivés quand l'outil canonique a déjà
# tourné (reformulation légitime) — cf. looks_like_simulation.
_GAIN_PROSE_PATTERNS = [
    # « récupère/récupérer/restaure/regagne/fait récupérer N PV / points de vie »
    re.compile(
        r"\b(?:r[ée]cup[eèé]re?|restaure?|regagne?|regagnera?"
        r"|fait\s+r[ée]cup[eèé]rer|lui\s+fait\s+r[ée]cup[eèé]rer)\s+(?:\*\*)?"
        r"\d{1,3}(?:\*\*)?\s*(?:points?\s+de\s+vie\b|PV\b)",
        re.IGNORECASE,
    ),
    # « soigne N (points de) dégâts / N PV »
    re.compile(
        r"\bsoigne?\s+(?:\*\*)?\d{1,3}(?:\*\*)?\s*"
        r"(?:points?\s+(?:de\s+)?(?:vie|dégâts)|PV\b)",
        re.IGNORECASE,
    ),
    # « gagne N points d'expérience » / « gagne N XP »
    re.compile(
        r"\bgagne?(?:z)?\s+(?:\*\*)?\d{1,5}(?:\*\*)?\s*"
        r"(?:points?\s+d['']exp[ée]rience\b|XP\b)",
        re.IGNORECASE,
    ),
    # « +50 XP » (montant récité)
    re.compile(r"\+\s*\d{1,5}\s*(?:points?\s+d['']exp[ée]rience|XP)\b",
               re.IGNORECASE),
    # « ajoute/ajouté … à son/votre inventaire », « range … dans son sac »
    re.compile(
        r"\b(?:ajout\w*|range\w*|glisse\w*)\b[^.!?;\n]{0,80}?"
        r"\b(?:[àa]\s+(?:son|sa|votre)|dans\s+(?:son|votre))\s+"
        r"(?:inventaire|sac|sacoche)",
        re.IGNORECASE,
    ),
    # 🎁 Remise d'OBJET de la main à la main (partie e55cc855) : « Thukmuul
    # tend la fiole de vérité vers Margoth », « remet le parchemin de la
    # route », « vous donne une amulette » — l'objet n'atteignait JAMAIS
    # l'inventaire (l'appel `inventaire_ajouter` n'était pas émis) : le
    # joueur ne pouvait pas l'utiliser (« tu ne l'as pas »). Désactivée
    # quand un tool d'inventaire a déjà tourné (include_gains=False) : la
    # narration de la remise APRÈS l'appel est légitime.
    re.compile(
        r"\b(?:remets?|tends?|donnes?|passe?s?)\b[^.!?;\n]{0,80}?"
        r"\b(?:fiole|parchemin|baguette|couronne|gemme|amulette|anneau|"
        r"cl[ée]|artefact)\b",
        re.IGNORECASE,
    ),
]

# Dégâts SUBIS PAR LES PJ narrés en prose SANS résultat mécanique officiel :
# « vous avez été touché pour 8 dégâts », « vous subissez 6 PV de dégâts »,
# « votre vie tombe à 12 ». Le LLM inventait l'attaque d'un monstre et les
# dégâts correspondants (partie 5f3e31c9 : « Vous avez été touché pour
# **8 dégâts** » pour un Loup-garou DÉJÀ joué en échec officiel par le
# serveur). Ces dégâts passent TOUJOURS par l'événement mécanique du moteur
# serveur : la narration ne peut QUE reformuler un événement injecté en
# pre-run — désactivé quand `trust_damage_prose` (des événements serveur avec
# « dégâts » viennent d'être injectés : la reformulation est légitime).
# Nombres ÉCRITS EN LETTRES que le narrateur utilise pour les PV
# (« votre vitalité à douze ») — partie 263f82dc.
_PV_NOMBRES_MOTS = (
    r"un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize"
    r"|quatorze|quinze|seize|dix-sept|dix-huit|dix-neuf|vingt"
)
# Suite interdite après « à N » : une mesure/tempo, pas des PV
# (« tombe à 3 mètres », « chute à 12 km »…).
_PV_PAS_UN_PV = (
    r"(?!\s*(?:m\b|m[èe]tres?\b|km\b|kilom[èe]tres?\b|pi[èe]ces?\b|or\b"
    r"|minutes?\b|secondes?\b|heures?\b|jours?\b|m[ée]tres\s+carr[ée]s?\b))"
)

_PJ_DEGATS_PROSE_PATTERNS = [
    # « (vous avez été) touché/touchée/touchez pour 8 dégâts / 6 PV ».
    re.compile(
        r"\btouch[ée]e?s?\s+(?:par|pour)\s+(?:\*\*)?\d{1,3}(?:\*\*)?\s*"
        r"(?:points?\s+de\s+)?(?:d[ée]g[âa]ts|dégats|PV)\b",
        re.IGNORECASE,
    ),
    # « vous subissez 8 (points de) dégâts / 6 PV » (paradigme « subissez »
    # absent de _DAMAGE_PROSE_PATTERNS qui ne couvre que subit/subissent).
    re.compile(
        r"\b(?:vous\s+)?subissez\s+(?:\*\*)?\d{1,3}(?:\*\*)?\s*"
        r"(?:points?\s+de\s+)?(?:d[ée]g[âa]ts|dégats|PV)\b",
        re.IGNORECASE,
    ),
    # « votre vie chute/tombe/descend/baissé », « vos PV tombent »,
    # « votre vitalité baissant… » (partie 263f82dc : « vitalité » absent
    # de l'ancienne liste de noms, « baissant » du verbe → jetait passer).
    re.compile(
        r"\b(?:votre|ta|vos|tes)\s+(?:sant[ée]|vie|vitalit[ée]"
        r"|points?\s+de\s+vie|PV)\s*"
        r"[,:\s]+(?:descend\w*|chut\w*|tombe\w*|s['']?effondre\w*"
        r"|baiss\w*|r[ée]duis\w*|n'est\s+plus\s+que)\b",
        re.IGNORECASE,
    ),
    # « (il) chute/chutant/baissant/réduisant à 12 (sur 17) », « vous tombez
    # à 12 PV », « …à douze » (nombre en lettres) — l'état d'un PJ affirmé
    # sans événement mécanique (5f3e31c9 : « chutant à 12 sur 17 » pour
    # 3/17 réels ; 263f82dc : « votre vitalité baissant à 4 sur 16 »,
    # « réduisant votre vitalité à douze » pour 8/15 réels).
    re.compile(
        r"\b(?:chut\w*|tombez?|baiss\w*|descend\w*|r[ée]dui\w*"
        r"|n'est\s+plus\s+que)\s+[àa]\s+(?:\*\*)?(?:\d{1,3}|"
        + _PV_NOMBRES_MOTS + r")(?:\*\*)?"
        r"\s*(?:sur\s+(?:\d{1,3}|" + _PV_NOMBRES_MOTS + r"))?\s*" + _PV_PAS_UN_PV,
        re.IGNORECASE,
    ),
    # « votre vitalité à N », « vie à N (sur M) » — nom de PV suivi
    # immédiatement d'un total (« réduisant votre vitalité à douze »).
    re.compile(
        r"\b(?:votre|ta|sa|leur)?\s*(?:vitalit[ée]|vie|sant[ée]"
        r"|points?\s+de\s+vie|PV)\s+[àa]\s+(?:\*\*)?(?:\d{1,3}|"
        + _PV_NOMBRES_MOTS + r")(?:\*\*)?"
        r"\s*(?:sur\s+(?:\d{1,3}|" + _PV_NOMBRES_MOTS + r"))?",
        re.IGNORECASE,
    ),
]


def _norm_nom_outil(s: Any) -> str:
    """Normalisation d'un nom de combattant (LLM vs état) : minuscules,
    sans accents, accents circonflexes/trémas plats. « Loup-garou (humain) »
    et « loup-garou » doivent correspondre."""
    import unicodedata as _uni
    n = _uni.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in n if not _uni.combining(c))


def _pv_officiels_ligne(ctx: Any) -> str:
    """Ligne « PV OFFICIELS » injectée dans les correctifs quand le LLM
    invente des PV de héros (« votre vitalité baissant à 4 sur 16 » pour
    8/15 réels, partie 263f82dc) : la relance recopie alors les valeurs
    serveur au lieu de ré-improviser un total. Renvoie '' sans état."""
    try:
        etat = PartyState(
            data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
        ).load()
        pjs = [p for p in (etat.get("pj") or []) if isinstance(p, dict)]
        if not pjs:
            return ""
        parts: list[str] = []
        for p in pjs:
            lbl = f"{p.get('nom', '?')} {p.get('pv', '?')}/{p.get('pv_max', '?')} PV"
            conds = [c for c in (p.get("conditions") or []) if c]
            if conds:
                lbl += f" ({', '.join(conds)})"
            parts.append(lbl)
        return (
            "PV OFFICIELS (source serveur — les SEULES valeurs valides : "
            "recopie-les EXACTEMENT si tu mentionnes un total de PV, "
            "n'invente JAMAIS un chiffre) : " + " ; ".join(parts) + "."
        )
    except Exception:                                        # noqa: BLE001
        return ""


def looks_like_simulation(
    text: str,
    include_damage: bool = True,
    include_checks: bool = True,
    include_creation: bool = True,
    include_gains: bool = True,
    include_pj_damage: bool = True,
) -> Optional[str]:
    """Renvoie le fragment de simulation trouvé, ou None.

    `include_damage=False` désactive les patterns de dégâts en prose — utilisé
    quand un tool de dés a déjà tourné dans le tour : la reformulation du
    résultat ("La créature subit 7 dégâts") est alors légitime.
    `include_checks=False` désactive pareillement les patterns de jets de
    caractéristique/compétence en prose (« jet de Force pour… »).
    `include_creation=False` désactive la détection des « ✅ Fiche créée… »
    narrés — utilisé quand un outil d'écriture de fiche a réellement tourné.
    `include_gains=False` désactive les gains d'état en prose (soins, XP,
    inventaire) — légitimes quand l'outil canonique a déjà tourné.
    `include_pj_damage=False` désactive les patterns de dégâts SUBIS par les
    PJ (« vous avez été touché pour 8 dégâts ») — utilisé quand le moteur
    serveur vient d'injecter des événements mécaniques avec « dégâts »
    (pre-run) : la reformulation en prose est alors légitime.
    """
    if not text:
        return None
    pats: list[re.Pattern[str]] = list(_SIMULATION_PATTERNS)
    if include_damage:
        pats += _DAMAGE_PROSE_PATTERNS
    if include_pj_damage:
        pats += _PJ_DEGATS_PROSE_PATTERNS
    if include_checks:
        pats += _CHECK_PROSE_PATTERNS
        # Formules de dés récitées (« 1d20 + 5 (BBA) + 3 = 18 ») : jet
        # improvisé si AUCUN tool de dés n'a tourné ; reformulation
        # légitime sinon → désactivées avec les autres patterns de jets.
        pats += _DICE_FORMULA_PATTERNS
    if include_creation:
        pats += _FICHE_CREATION_PATTERNS
    if include_gains:
        pats += _GAIN_PROSE_PATTERNS
    for pat in pats:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# --------------------------------------------------------------------------- #
#  Détection de répétition narrative (écho d'une scène déjà narrée)
# --------------------------------------------------------------------------- #
# Symptôme observé en partie réelle : le joueur choisit une des options
# proposées et le MJ RE-NARRE mot pour mot une scène précédente au lieu de
# répondre (ex. la torche allumée deux fois, une salle re-décrite à
# l'identique). L'action du joueur est perdue et le fil de l'histoire casse.
# On détecte l'écho quasi verbatim contre les narrations récentes, et la boucle
# run() relance alors le tour avec un correctif ciblé.
_REPET_SEUIL_CHEVAUCHEMENT = 0.40  # bigrammes fenêtrés = reprise de la scène

# 📊 Libellés joueur des outils les plus courants (statut « quelle étape ») —
# le défaut est « Applique {tool}… ».
_STATUT_OUTILS = {
    "lancer_attaque": "Résout l'attaque…",
    "lancer_degats": "Calcule les dégâts…",
    "lancer_sauvegarde": "Résout la sauvegarde…",
    "lancer_des": "Lance les dés…",
    "calculer_initiative": "Calcule l'initiative…",
    "engager_combat": "Met en place le combat…",
    "demarrer_combat": "Met en place le combat…",
    "fiche_perso_infliger_degats": "Applique les dégâts…",
    "fiche_perso_soigner": "Applique les soins…",
    "fiche_perso_creer_rapide": "Crée le personnage…",
    "fiche_perso_recuperer": "Consulte la fiche…",
    "fiche_perso_mettre_a_jour": "Met à jour la fiche…",
    "inventaire_ajouter": "Ajoute au sac…",
    "inventaire_consulter": "Fouille l'inventaire…",
    "inventaire_consommer_munition": "Compte les munitions…",
    "carte_donjon_entrer": "Entre dans le donjon…",
    "carte_donjon_explorer": "Explore la salle suivante…",
    "carte_donjon_etage": "Change d'étage…",
    "carte_donjon_get": "Met à jour la carte…",
    "carte_joueurs_placer_ville": "Met à jour la carte du monde…",
    "carte_joueurs_position": "Met à jour la carte du monde…",
    "terminer_mon_tour": "Termine le tour…",
    "tour_suivant_combat": "Passe au tour suivant…",
    "incanter_sort": "Lance le sort…",
    "preparer_sorts": "Prépare les sorts…",
    "scenario_etape": "Note la progression…",
    "memoire_ajouter": "Note dans la mémoire de campagne…",
    "etat_partie_patch": "Enregistre l'état…",
}
_REPET_PREFIXE = 200          # préfixe normalisé dont le containment suffit
_REPET_MIN_CANDIDAT = 80      # narrations trop courtes : pas de verdict
_REPET_FENETRE = 8            # nb de narrations assistant récentes comparées


def _normalise_pour_compare(texte: str) -> str:
    """Normalise un texte pour comparaison : minuscules, sans accents, sans
    ponctuation/markdown, espaces et sauts de ligne collapés."""
    import unicodedata
    t = re.sub(r"[*_`>#\[\]()|…\"']", " ", texte or "")
    nf = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in nf if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _bigrammes_fenetres(mots: list[str], fenetre: int = 4) -> set[tuple[str, str]]:
    """Paires de mots distantes d'au plus `fenetre` positions — tolère la
    compression/réorganisation de phrases (une paire adjacente stricte casse
    dès qu'un mot est inséré ou supprimé)."""
    n = len(mots)
    return {
        (mots[i], mots[j])
        for i in range(n)
        for j in range(i + 1, min(i + fenetre, n))
    }


def _paragraphes_norm(texte: str) -> list[str]:
    """Paragraphes NORMALISÉS d'un texte (découpe sur les sauts de ligne).
    Sert à l'écho au niveau paragraphe : un paragraphe entier recyclé
    verbatim dans une narration par ailleurs nouvelle échappe à la
    comparaison globale (les bigrammes du reste diluent le recouvrement)."""
    return [
        _normalise_pour_compare(p)
        for p in re.split(r"\n+", texte or "")
        if p.strip()
    ]


def trouve_repetition(
    narration: str,
    historique: list["Message"],
    seuil: float = _REPET_SEUIL_CHEVAUCHEMENT,
) -> Optional[str]:
    """Renvoie un extrait de la narration précédente que `narration` répète,
    ou None si la narration est nouvelle.

    Trois critères (le premier atteint suffit) :
    - le préfixe normalisé de la narration apparaît tel quel dans un message
      assistant récent (copie quasi verbatim) ;
    - chevauchement des bigrammes fenêtrés de mots ≥ `seuil` (paraphrase qui
      reprend la scène, même en comprimant/réordonnant ; les narrations
      inédites restent ≪ seuil) ;
    - Écho au niveau PARAGRAPHE : un paragraphe entier de la nouvelle
      narration recopie (verbatim ou ≥ seuil) un paragraphe d'un message
      assistant récent — partie 263f82dc : l'intro de rencontre « un second
      squelette surgit des ombres, suivi d'un troisième… » re-collée à
      l'identique à chaque round alors que le reste du texte changeait, la
      comparaison globale restait sous le seuil.
    Les messages système/tool/user et les narrations très courtes sont ignorés.

    `seuil` : en COMBAT, les rounds rejouent la même action (« j'attaque à la
    hache ») — des narrations voisines sont NORMALES ; on ne relance que les
    vraies copies (l'orchestrateur passe un seuil plus haut, cf. D1ter).
    """
    cand = _normalise_pour_compare(narration)
    if len(cand) < _REPET_MIN_CANDIDAT:
        return None
    prefixe = cand[:_REPET_PREFIXE]
    bigrams_cand = _bigrammes_fenetres(cand.split())
    assistant_recents = [
        m.content for m in historique
        if m.role == "assistant" and (m.content or "").strip()
    ][-_REPET_FENETRE:]
    # Paragraphes normalisés pré-calculés (critère 3).
    paras_cand = [
        p for p in _paragraphes_norm(narration)
        if len(p) >= _REPET_MIN_CANDIDAT
    ]
    refs: list[tuple[str, list[str]]] = []
    for ancien in reversed(assistant_recents):
        ref = _normalise_pour_compare(ancien)
        if len(ref) < _REPET_MIN_CANDIDAT:
            continue
        refs.append((ref, [p for p in _paragraphes_norm(ancien)
                           if len(p) >= _REPET_MIN_CANDIDAT]))
        if prefixe and prefixe in ref:
            return ref[:120]
        bigrams_ref = _bigrammes_fenetres(ref.split())
        if bigrams_cand and bigrams_ref:
            overlap = len(bigrams_cand & bigrams_ref) / len(bigrams_cand)
            if overlap >= seuil:
                return ref[:120]
    # Critère 3 : écho de paragraphe.
    for para_c in paras_cand:
        bigrams_para = _bigrammes_fenetres(para_c.split())
        if not bigrams_para:
            continue
        for ref, paras_ref in refs:
            for para_r in paras_ref:
                if para_c in para_r or para_r in para_c:
                    return para_c[:120]
                b_r = _bigrammes_fenetres(para_r.split())
                if b_r and (
                    len(bigrams_para & b_r) / len(bigrams_para) >= seuil
                ):
                    return para_c[:120]
    return None


# Signaux d'ATTAQUE ENNEMIE dans une narration : créatures qui passent à
# l'offensive contre le groupe (embuscade, surgissement, encerclement).
# Partie b59b4a9a : l'embuscade des perceurs était narrée SANS les nommer
# (« des pierres tombent du plafond, frappant Margoth avec violence ») —
# ces motifs évitent que l'attaque passe inaperçue du garde-fou.
_NARRATION_ATTAQUE_RE = re.compile(
    r"(combat\s+imminent|surgissent|s'élancent|se précipitent|se ruent|"
    r"encercl\w+|fondent\s+sur|vous\s+attaqu\w+|prêts\s+à\s+attaquer|"
    r"passent\s+à\s+l'attaque|"
    r"embuscade|s'abattent\s+sur|tomb\w+\s+du\s+plafond|"
    r"frapp\w+\s+\w+\s+avec\s+violence|pris(?:es?)?\s+sous\s+coups)",
    re.IGNORECASE,
)
# Marqueurs de combat DÉJÀ RÉSOLU (récit) : inhibent le garde.
_NARRATION_PASSE_RE = re.compile(
    r"(vaincu\w*|achèv\w*|à\s+terre\s*,?\s*(?:inerte|sans vie)|coffre\s+vid)",
    re.IGNORECASE,
)
_NOMBRES_FR: dict[str, int] = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "douze": 12,
}

# Quantificateurs acceptés devant une mention de créature (mots ou chiffres).
_RE_QUANTIFIANT_FR = re.compile(
    r"(\d+|" + "|".join(_NOMBRES_FR)
    + r"|plusieurs|dizaine|douzaine|horde|groupe|couple|paire|triplet)\s*$",
    re.IGNORECASE,
)

# Noms du bestiaire qui sont AUSSI des mots courants de la narration :
# « les ombres de l'auberge », « une silhouette dans la nuit »… désignent
# le DÉCOR, pas une rencontre. Sur ces mots, l'engagement forcé ne doit
# JAMAIS se déclencher (partie dfccc120 : le joueur négociait avec des
# voleurs, le garde a engagé le monstre « Ombre » sur le mot « ombres ») —
# le MJ peut toujours appeler `engager_combat` explicitement. Volontairement
# réduit à deux mots quasi exclusivement décoratifs : les autres noms
# plausibles (squelette, zombie…) restent éligibles à l'engagement forcé
# dès qu'ils sont quantifiés (régression 77e2862b : « cinq squelettes »).
_ENNEMIS_MOTS_GENERIQUES = {
    "ombre", "silhouette",
}

# Signaux de DÉPLACEMENT NARRÉ : le modèle raconte l'entrée dans un LIEU
# (grotte, salle, donjon, temple…) sans avoir appelé l'outil de déplacement.
# Partie ae358455 : « pénètre dans la grotte de Nulentok » narré 3 tours de
# suite sans `carte_donjon_explorer` — la carte restait en (0,0) pendant que
# la narration était déjà au fond de la grotte. Volontairement restreint aux
# entrées de lieu closes : les micro-déplacements libres d'une même zone
# (l'auberge, la rue du village) ne doivent PAS déclencher la correction.
_DEPLACEMENT_NARRE_RE = re.compile(
    r"(pénètre\s+dans|franchit\s+(?:la|le|l')\s|débouche\s+(?:dans|sur)|"
    r"arrive\s+(?:devant|enfin\s+devant)\s+(?:la|le|l')|"
    r"entre\s+dans\s+(?:la|le|l'|cette)\s*(?:grotte|caverne|salle|donjon|"
    r"temple|crypte|tour|pièce|ruine)|s'engage\s+sur\s+la\s+route|"
    r"quitte\s+(?:la|le|l')\s*(?:grotte|caverne|salle|donjon|temple|"
    r"crypte|tour|pièce|ruine|couloir))",
    re.IGNORECASE,
)
_OUTILS_DEPLACEMENT = frozenset({
    "carte_donjon_entrer", "carte_donjon_explorer", "carte_donjon_etage",
    "voyage_demarrer",
})


def _deplacement_narre(narration: str) -> Optional[str]:
    """Renvoie un extrait de la narration qui raconte un déplacement de lieu
    (entrée dans une salle/grotte), ou None si aucun."""
    m = _DEPLACEMENT_NARRE_RE.search(narration or "")
    if not m:
        return None
    debut = max(0, m.start() - 40)
    return (narration[debut:m.end() + 60]).replace("\n", " ").strip()


# Verbes de DÉPLACEMENT dans l'action du JOUEUR : la décision « narrer » pour
# une telle action se fie à une narration désynchronisée (partie ae358455 :
# « j'avance prudemment dans la grotte » — la décision répondait « narrer »
# en se basant sur la dernière narration (« déjà dans la grotte ») pendant
# que la carte restait figée en (0,0) 3 tours de suite). La re-consultation
# de la décision avec la consigne explicite force la résolution mécanique.
_ACTION_DEPLACEMENT_RE = re.compile(
    r"\b(j'?avance|j'?entre|je\s+pénètre|je\s+franchis|je\s+traverse|"
    r"je\s+vais\s+(?:vers|à|au|aux|dans)|je\s+me\s+dirige|je\s+monte|"
    r"je\s+descends?|je\s+quitte|"
    r"je\s+continue\s+(?:vers|dans|sur\s+la\s+route)|"
    # Partie b59b4a9a : actions COMPOSÉES (« je demande…, puis j'explore
    # la galerie vers l'est ») et départs (« je pars de… », « j'emprunte
    # la porte est », « je prends la route ») restaient invisibles du D0.
    r"j'explore|on\s+avance|nous\s+avan[çc]ons|je\s+pars|"
    r"j'emprunte|je\s+prends\s+(?:la\s+route|le\s+passage|le\s+couloir|"
    r"la\s+porte|le\s+sentier)|"
    # Partie d9f65ed2 : « je continue ma route vers le puits de Nulentok »
    # — la variante possessive passait au travers du D0.
    r"je\s+(?:continue|reprends)\s+(?:ma\s+route|mon\s+chemin|le\s+chemin|"
    r"la\s+route)|en\s+route\s+vers)",
    re.IGNORECASE,
)


def _action_deplacement_dans_donjon(action: str, etat: dict[str, Any]) -> bool:
    """L'action du joueur réclame-t-elle un déplacement alors que le groupe
    est dans un donjon ACTIF (portes ouvertes dans la salle courante) ?"""
    if not _ACTION_DEPLACEMENT_RE.search(action or ""):
        return False
    donjon = etat.get("donjon") or {}
    if not (donjon.get("id") and donjon.get("grille")):
        return False
    cr = list(donjon.get("courant") or [0, 0])
    cx, cy = (cr[0], cr[1]) if len(cr) >= 2 else (0, 0)
    salle = next(
        (s for s in (donjon.get("grille") or [])
         if s.get("x") == cx and s.get("y") == cy), {},
    )
    return any((salle.get("portes") or {}).values())


# 🎁 Remises d'OBJETS DE QUÊTE narrées (partie e55cc855) : « Thukmuul tend la
# fiole de vérité vers Margoth », « Margoth range la fiole de vérité, le
# parchemin de la route et la baguette de téléportation dans son sac à dos »
# — la remise était narrée sans JAMAIS appeler `inventaire_ajouter` :
# l'objet n'atteignait pas l'inventaire (le joueur ne pouvait pas
# l'utiliser), et les rejeux correctifs n'y changeaient rien (le modèle
# ré-échoit la remise sans appeler l'outil).
_NOMS_QUETE_RE = re.compile(
    r"\b((?:une?|le|la|les|l'|son|sa)\s*"
    r"(?:fiole(?:\s+de\s+v[ée]rit[ée])?|parchemin(?:\s+de\s+la\s+route)?|"
    r"baguette(?:\s+de\s+t[ée]l[ée]portation)?|couronne(?:\s+de\s+mystra)?|"
    r"gemme|cl[ée]|amulette|anneau|potion|carte|lettre|relique|talisman|"
    r"[ée]meraude|saphir|rubis|diamant|jacinthe|beljuril))\b",
    re.IGNORECASE,
)
_REMISE_VERBE_RE = re.compile(
    r"\b(?:remets?|tends?|donnes?|passe?s?|glisse?s?|range|rangea|"
    r"saisi[tz]?|attrape|empoches?|"
    # parties 7177d819/b59b4a9a : « Thukmuul sort de ses vêtements une fiole
    # de vérité… », « Elle dépose les objets dans vos mains » — la remise
    # avec ces verbes passait inaperçue (inventaire resté vide).
    # ⚠️ « accepte », « confie » et « sort(nu) » retirés (82a77cbe) :
    # « Si vous acceptez cette mission, vous devrez récupérer la couronne »
    # et « elle confie la quête de la Couronne » transformaient des MENTIONS
    # de mission en remises — la Couronne elle-même était ajoutée dès l'intro.
    r"d[ée]p[ôo]ses?|d[ée]posa|offr[ei]t?|"
    # « sort » verbe UNIQUEMENT en contexte de tirer un objet (« sort de
    # ses vêtements », « sort une dague », « sortit son épée ») — jamais le
    # NOM « un sort » (sortilège) : on exige un complément d'objet direct ou
    # « de + son/sa/ses » juste après.
    r"sort(?:it|s)?\s+(?:de\s+)?(?:ses|sa|son|une?|le|la|les)\s)\b",
    re.IGNORECASE,
)


def _objets_remettes_narration(narration: str) -> list[str]:
    """Objets de quête REMIS dans la narration (remise main à la main ou
    rangement dans le sac), avec leur déterminant : « la fiole de vérité »,
    « le parchemin de la route ». Dédupliqués (clé normalisée), dans
    l'ordre d'apparition. Une phrase sans verbe de remise/rangement ne
    compte pas (« la Couronne est entre les mains de Nulentok » : mention,
    pas remise).

    Partie b59b4a9a : l'anaphore inter-phrases (« Thukmuul sort de ses
    vêtements une fiole de vérité et un parchemin de la route. Elle dépose
    les objets dans vos mains. ») laissait l'inventaire vide — la 2ᵉ phrase
    porte le verbe de remise mais « les objets » ne nomme rien. Quand une
    phrase à verbe de remise ne nomme AUCUN objet, on reprend donc les noms
    d'objets de quête de la phrase précédente."""
    objets: dict[str, str] = {}
    if not narration:
        return []
    try:
        from ..tools.inventaire import _cle_objet  # lazy : évite un cycle
    except Exception:                                    # noqa: BLE001
        def _cle_objet(x):                               # type: ignore[misc]
            n = re.sub(r"\s+", " ", str(x or "").strip().lower())
            return re.sub(r"^(le|la|les|l'|une?|son|sa)\s+", "", n)
    noms_precedents: dict[str, str] = {}
    _remise_precedente = False
    for phrase in re.split(r"(?<=[.!?…])\s+|\n", narration):
        if not phrase.strip():
            continue
        noms_phrase: dict[str, str] = {}
        for m in _NOMS_QUETE_RE.finditer(phrase):
            nom = m.group(0).strip()
            cle = _cle_objet(nom)
            if cle:
                noms_phrase.setdefault(cle, nom)
        if _REMISE_VERBE_RE.search(phrase):
            # Anaphore : « … une fiole de vérité et un parchemin de la
            # route. Elle dépose les objets dans vos mains. » — on ne tire
            # les noms de la phrase précédente que si ELLE aussi portait un
            # verbe de remise/tirage (sinon une simple mention — « la
            # Couronne est entre les mains de Nulentok » — se retrouverait
            # ajoutée par la phrase suivante sans lien).
            candidats = noms_phrase or (
                noms_precedents if _remise_precedente else {}
            )
            for cle, nom in candidats.items():
                objets.setdefault(cle, nom)
        if noms_phrase:
            noms_precedents = noms_phrase
        _remise_precedente = bool(_REMISE_VERBE_RE.search(phrase))
    return list(objets.values())


def _corriger_pronoms_pj(narration: str, etat: dict[str, Any],
                         data_dir: str) -> str:
    """Corrige les PRONOMS du mauvais genre attribués aux PJ dans la
    narration finale (partie b59b4a9a : « Margoth… Elle se relève » pour un
    PJ sexe M — l'écho du mauvais genre dans l'historique domine toute
    consigne de prompt, même explicite ; le correctif est DÉTERMINISTE).

    Contextes corrigés (conservateurs) :
    - le PJ est SUJET dans la phrase (« Margoth … , elle ») → pronoms fixés ;
    - le PJ était sujet à la phrase PRÉCÉDENTE et la phrase courante ne cite
      aucun PNJ de genre opposé connu (veto : le pronom peut être LEURS).

    Les accords (« blessée », « prête ») ne sont PAS retouchés (trop
    risqué) ; les possessifs non plus (« sa hache » est correct en
    français — accord avec l'objet). Renvoie la narration corrigée."""
    if not narration or not narration.strip():
        return narration
    try:
        from .prompt_builder import _genre_pj, _genres_pnj_donjon
    except Exception:                                        # noqa: BLE001
        return narration
    pj_genres: list[tuple[str, str]] = []
    for _p in (etat.get("pj") or []):
        _nom = str((_p or {}).get("nom") or "").strip()
        if not _nom:
            continue
        _g = _genre_pj(data_dir, _p)
        if _g in ("Masculin", "Féminin"):
            pj_genres.append((_nom, _g))
    if not pj_genres:
        return narration
    # PNJ de genre connu (manifeste) : une phrase qui les cite n'est pas
    # retouchée — le pronom peut se rapporter à EUX.
    pnj_fem: list[str] = []
    pnj_masc: list[str] = []
    try:
        for _l in _genres_pnj_donjon(etat):
            _m = re.match(r"(.+?)\s+—\s+(FÉMININ|MASCULIN)$", _l or "")
            if not _m:
                continue
            (pnj_fem if _m.group(2) == "FÉMININ" else pnj_masc).append(
                _m.group(1))
    except Exception:                                        # noqa: BLE001
        pass

    def _sujet(nom: str, phrase: str) -> bool:
        """Le nom apparaît-il comme SUJET (pas complément « de/à/par N ») ?"""
        m = re.search(
            r"(?<![A-Za-zÀ-ÿ])" + re.escape(nom) + r"(?![A-Za-zÀ-ÿ])",
            phrase)
        if not m:
            return False
        avant = phrase[max(0, m.start() - 4):m.start()].lower()
        return not avant.rstrip().endswith(
            ("de", "du", "à", "par", "avec", "sur", "dans"))

    phrases = [m.group(0) for m in re.finditer(
        r"[^.!?…]+(?:[.!?…]+|$)", narration)]

    # Passe 1 (gauche → droite) : chaque phrase est-elle LIÉE au PJ (sujet
    # nommé, ou chaîne prononciale continuée sans nouveau référent) ?
    lie: list[bool] = []
    veto: list[bool] = []
    for i, phrase in enumerate(phrases):
        bas = phrase.lower()
        _veto_i = False
        _lie_i = False
        for nom, genre in pj_genres:
            bon = "il" if genre == "Masculin" else "elle"
            mauvais = "elle" if genre == "Masculin" else "il"
            if genre == "Masculin" and any(
                    ff.lower() in bas for ff in pnj_fem):
                _veto_i = True
            if genre == "Féminin" and any(
                    m2.lower() in bas for m2 in pnj_masc):
                _veto_i = True
            if _sujet(nom, phrase):
                _lie_i = True
            elif lie and lie[i - 1] and not _veto_i:
                # Chaîne prononciale : la phrase précédente était liée et
                # celle-ci démarre par un pronom / possessif du même référent
                # (« Elle s'appuie… », « Son armure… et elle sent… »).
                if re.match(
                        r"\s*(" + mauvais.capitalize() + r"|" + mauvais
                        + r"|Son |Sa |Ses |et " + mauvais + r"|, "
                        + mauvais + r")", phrase):
                    _lie_i = True
        lie.append(_lie_i)
        veto.append(_veto_i)

    # Passe 2 (droite → gauche) : application des remplacements — les spans
    # des phrases encore à traiter ne bougent pas.
    resultats = list(phrases)
    for i in range(len(phrases) - 1, -1, -1):
        phrase = phrases[i]
        if not lie[i] or veto[i]:
            continue
        for nom, genre in pj_genres:
            if not _sujet(nom, phrase) and not (
                    i and lie[i - 1]):
                continue
            bon_l = "il" if genre == "Masculin" else "elle"
            mauvais = "elle" if genre == "Masculin" else "il"
            bon_fort = "Il" if genre == "Masculin" else "Elle"
            # Élisions d'abord : « d'elle » → « de lui », préposition + elle
            # → + lui (jamais « d'il »).
            if genre == "Masculin":
                phrase = re.sub(r"d[''\u2019]" + mauvais + r"\b",
                                "de lui", phrase, flags=re.IGNORECASE)
                phrase = re.sub(
                    r"\b(à|vers|pour|avec|sans|chez|sur) " + mauvais
                    + r"\b", r"\1 lui", phrase, flags=re.IGNORECASE)
            phrase = re.sub(
                r"(?<![A-Za-zÀ-ÿ])" + mauvais + r"-même\b",
                ("lui-même" if genre == "Masculin" else "elle-même"),
                phrase, flags=re.IGNORECASE)
            phrase = re.sub(
                r"(?<![A-Za-zÀ-ÿ])" + mauvais.capitalize()
                + r"(?![A-Za-zÀ-ÿ'])", bon_fort, phrase)
            phrase = re.sub(
                r"(?<![A-Za-zÀ-ÿ])" + mauvais + r"(?![A-Za-zÀ-ÿ'])",
                bon_l, phrase)
        resultats[i] = phrase
    return "".join(resultats)


_ACHAT_INTENT_RE = re.compile(
    r"\bj['’]ach[eè]te?\b|\bach[eè]ter\b|\bje\s+commande\b|"
    r"\bach[eè]te(?:z)?[-\s]moi\b|je\s+v[eè]ux\s+(?:acheter|un|une)\b|"
    r"je\s+(?:prends|demande)\s+(?:un|une)\s+(?:repas|chambre|logement)|"
    # 🔧 Partie d8f41637 : le message du joueur porte le préfixe de signature
    # (« **[alain]** : repas mediocre ») — l'alternative ANCRÉE (^…$) ne
    # matchait plus et le repas n'était jamais commandé.
    r"\b(?:repas|chambre|logement)\s+(?:m[ée]diocre|convenable|bonne?)\b|"
    # 💰 Partie 2dfa9c75 : les VENTES suivent la même mécanique (or crédité
    # par `marche_vendre` à 50 % du prix de base officiel).
    r"\bje\s+vends?\b|\bje\s+vendrais?\b",
    re.IGNORECASE,
)
_RE_QUALITE_REPAS = re.compile(
    r"\b(m[ée]diocre|convenable|bonne?|poor|common|good)\b", re.IGNORECASE)
_RE_QTE_ACHAT = re.compile(r"\b(\d{1,3})\b")


def _intention_achat(user_msgs: list[str]) -> Optional[str]:
    """Intention d'ACHAT explicite du joueur dans ses 2 derniers messages —
    None si aucune (« je visite l'auberge » ne suffit pas, partie 1808ebab :
    il faut j'achète / je commande / un choix de menu explicite)."""
    for msg in reversed(user_msgs[-2:]):
        if _ACHAT_INTENT_RE.search(msg or ""):
            return msg
    return None


def _extraire_achat(message: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Décompose une intention d'achat : type (auberge/marché), article du
    catalogue, qualité, quantité (lots pour les munitions)."""
    from ..tools.inventaire import _norm  # pylint: disable=import-outside-toplevel

    def _sing_mot(w: str) -> str:
        """Pluriel grossier mot à mot (fleches → fleche, carreaux → carreau)."""
        if w.endswith(("aux", "eaux")):
            return w[:-3] + "au"
        if w.endswith("s") and len(w) > 3:
            return w[:-1]
        return w

    msg = message or ""
    q_qual = _RE_QUALITE_REPAS.search(msg)
    qualite = q_qual.group(1).lower() if q_qual else ""
    if qualite == "poor":
        qualite = "mediocre"
    elif qualite in ("common",):
        qualite = "convenable"
    elif qualite in ("good", "bon", "bonne"):
        qualite = "bonne"
    if qualite:
        qualite = _norm(qualite)          # sans accent (médiocre → mediocre)
        if qualite == "bon":
            qualite = "bonne"

    mots = {_sing_mot(w) for w in _norm(msg).split() if len(w) >= 4}

    # Repas / logement → auberge_commander.
    if re.search(r"repas", msg, re.I):
        return {"type": "auberge", "repas": qualite or "mediocre",
                "logement": "", "nuits": 1,
                "libelle": "repas " + (qualite or "mediocre")}
    if re.search(r"chambre|logement|nuit", msg, re.I):
        return {"type": "auberge", "repas": "",
                "logement": qualite or "mediocre", "nuits": 1,
                "libelle": "logement " + (qualite or "mediocre")}

    # 💰 VENTE (partie 2dfa9c75) : « je vends mes rations » → marche_vendre
    # (revente officielle à 50 %, or crédité sur la fiche).
    if re.search(r"\bvends?\b|\bvendrais?\b", msg, re.I):
        meilleur_v = None
        for art in articles:
            na = _norm(str(art.get("nom") or ""))
            mots_art = {_sing_mot(w) for w in na.split()}
            communs = mots & mots_art
            if communs and (meilleur_v is None
                            or len(communs) > len(meilleur_v[0])):
                meilleur_v = (communs, art)
        if meilleur_v is None:
            return {}
        art = meilleur_v[1]
        qte = 1
        m_q = _RE_QTE_ACHAT.search(msg)
        if m_q:
            qte = max(1, int(m_q.group(1)))
        return {"type": "vente", "article": str(art.get("nom") or ""),
                "quantite": qte, "libelle": "vente " + str(art.get("nom") or "")}

    # Sinon : meilleur article du catalogue par mots communs.
    meilleure: tuple[int, dict[str, Any]] | None = None
    for art in articles:
        na = _norm(str(art.get("nom") or ""))
        mots_art = {_sing_mot(w) for w in na.split()}
        communs = mots & mots_art
        score = len(communs) * 2 + (1 if _norm(na) in _norm(msg) else 0)
        if score and (meilleure is None or score > meilleure[0]):
            meilleure = (score, art)
    if meilleure is None:
        return {}
    art = meilleure[1]
    # Quantité : le premier nombre du message, converti en LOTS si l'article
    # en est un (« Flèches (10) », 5 flèches demandées = 1 lot).
    qte = 1
    m_q = _RE_QTE_ACHAT.search(msg)
    if m_q:
        qte = max(1, int(m_q.group(1)))
    m_lot = re.search(r"\((\d+)\)\s*$", str(art.get("nom") or ""))
    if m_lot:
        lot = max(1, int(m_lot.group(1)))
        qte = max(1, -(-qte // lot))   # ceil
    return {"type": "marche", "article": str(art.get("nom") or ""),
            "quantite": qte, "libelle": str(art.get("nom") or "")}


_VOYAGE_INTENT_RE = re.compile(
    r"\bje\s+me\s+dirige\s+vers\b|\bje\s+pars\s+(?:vers|pour)\b|"
    r"\bje\s+vais\s+vers\b|\ben\s+route\s+(?:vers|pour)\b|"
    r"\bje\s+rejoins\b|\ballons?\s+vers\b|"
    r"\bje\s+prends\s+la\s+route\b|"
    # Partie 1808ebab (suite) : « Je retourne à la ville pour voir
    # Thukmuul Teleshann » — un retour à 30 km est un vrai voyage.
    r"\bje\s+(?:retourne|rentre)\s+(?:à|au|aux|en|vers)\b",
    re.IGNORECASE,
)
_DEST_VOYAGE_RE = re.compile(
    r"(?:vers|pour)\s+(?:le\s+|la\s+|les\s+|l'\s*|au\s+|à la\s+)?"
    r"([A-Za-zÀ-ÿ'’\- ]{4,60})", re.IGNORECASE,
)


def _extraire_voyage(message: str) -> Optional[str]:
    """Destination de voyage exprimée par le joueur (« Je me dirige vers le
    repère de Zendar » → « repère de Zendar »). None si pas de destination."""
    m = None
    for m in _DEST_VOYAGE_RE.finditer(message or ""):
        pass
    if m is None:
        return None
    dest = (m.group(1) or "").strip(" .!,« »'")
    # Articles résiduels (« l'est », « le repère… ») — hors capture quand la
    # classe les recouvre.
    dest = re.sub(r"^(?:le\s+|la\s+|les\s+|au\s+|aux\s+|à la\s+|l')",
                  "", dest, flags=re.IGNORECASE)
    # Coupe les compléments de but (« pour lui demander la Couronne »).
    dest = re.split(r"\s+(?:pour|afin|et|puis)\s+", dest, maxsplit=1)[0]
    return dest.strip() or None


def _decor_ancre_absent(narration: str, ancre_ligne: str) -> bool:
    """Partie 083c7bba : la LONGUEUR ne suffit pas à valider une intro —
    celle-ci faisait 1331 caractères mais SAUTAIT le décor (l'acceptation
    « déjà faite » avant le premier mot). Vrai si la ligne d'ancrage
    « LIEU DE DÉPART CANONIQUE » existe et que la narration n'ANCRE PAS le
    décor : moins d'un tiers des mots caractéristiques (≥ 5 car.) de la
    description canonique y apparaissent."""
    if not ancre_ligne or not (narration or "").strip():
        return False
    m_desc = re.search(r"«\s*(.+?)\s*»", ancre_ligne)
    if not m_desc:
        return False
    mots_decor = [
        m for m in _normalise_pour_compare(m_desc.group(1)[:200]).split()
        if len(m) >= 5
    ][:10]
    if not mots_decor:
        return False
    nar = _normalise_pour_compare(narration)
    presents = sum(1 for md in mots_decor if md in nar)
    return presents < max(2, len(mots_decor) // 3)


_OR_GAIN_RE = re.compile(
    r"\b(?:trouv\w+|découv\w+|gagn\w+|empo?ch\w+|récolt\w+|vous offr\w+|"
    r"sac\s+contenant)\b"
    r"[^.!?]{0,100}?\b(\d{1,4})\s*(?:pièces?\s+d'or|po)\b",
    re.IGNORECASE,
)
_OR_PRIX_RE = re.compile(
    r"\b(co[ûu]t(?:e|era|ant)?|prix|pai(?:e|er|é|ement)|pay(?:e|ez|é)|"
    r"demande|propose|acheter|coûtera)\b", re.IGNORECASE)

# 🎁 Partie 2dfa9c75/audit : le BUTIN NON-QUÊTE narré (« vous trouvez une
# épée longue ») sans tool — l'objet n'atteignait jamais l'inventaire. Le
# rattrapage ne s'applique qu'au trésor CANONIQUE de la salle (champ
# `tresor` du manifeste) : le nom extrait de la prose doit recouper ce
# champ (pas d'objet inventé).
_BUTIN_VERBE_RE = re.compile(
    r"\b(?:trouv\w+|découv\w+|rév[eè]l\w+|s'ouvre\b|dévoil\w+|"
    r"coffres?\s+(?:contient|s'ouvre|rév[eè]le)|conten\w+)\b",
    re.IGNORECASE,
)
_BUTIN_OBJET_RE = re.compile(
    r"\b((?:une?|le|la|les|l')[a-zà-ÿ'’\-]+(?:\s+[a-zà-ÿ'’\-]+){0,3})",
    re.IGNORECASE,
)


def _or_gagne_narre(narration: str) -> Optional[int]:
    """Or GAGNÉ narré (trésor découvert, butin empoché) — pour le
    rattrapage déterministe. None si aucun gain clair : les PRIX
    (« coûte 50 po », « le forgeron demande 10 po ») sont exclus."""
    if not narration:
        return None
    for m in _OR_GAIN_RE.finditer(narration):
        phrase_d = narration.rfind(".", 0, m.start())
        phrase_f = narration.find(".", m.start())
        phrase = narration[
            (phrase_d + 1) if phrase_d != -1 else 0:
            phrase_f if phrase_f != -1 else len(narration)]
        if _OR_PRIX_RE.search(phrase):
            continue
        return int(m.group(1))
    return None


# 🎲 Jets de compétence RÉUSSIS narrés sans dé (audit 33d8f18e/dfbb4846) :
# « il fouille la pièce et réussit sa Perception » — la réussite décide
# d'un verrou, d'un piège ou d'un trésor SANS mécanique (règle 2 : le dé
# doit PRÉCÉDER la prose). Détection : un mot de RÉUSSITE + un nom de
# compétence dans la fenêtre, sans mention de dé ni de tool.
_COMPETENCES_RE = re.compile(
    r"(discr[eé]tion|perception|escamotage|escalade|diplomatie|"
    r"intimidation|d[ée]guisement|fouille|saut|natation|[ée]quilibre|"
    r"concentration|psychologie|premier\s+secours|vol\s+à\s+la\s+tire|"
    r"dressage|[ée]quit\w+|connaissance|savoir|m[ée]decine|survie|"
    r"arcane?s?|religion|nature|auditif|rep[ée]rage|"
    r"ma[îi]trise\s+des\s+animaux|repr[ée]sentation|estimation|"
    r"cryptographie|artisanat|profan\w+|force)",
    re.IGNORECASE,
)
_REUSSITE_RE = re.compile(r"\b(r[ée]ussi\w*|r[ée]ussite)\b", re.IGNORECASE)


def _jet_reusse_narre(narration: str) -> Optional[str]:
    """Fragment « réussite + compétence » narré SANS dé (règle 2 violée :
    la réussite décide d'un verrou/piège/trésor sans mécanique). None si
    le jet est cité avec son dé (légitime) ou absent."""
    if not narration:
        return None
    for m in _REUSSITE_RE.finditer(narration):
        fenetre = narration[max(0, m.start() - 90):m.end() + 90]
        bas = fenetre.lower()
        if "d20" in bas or "lancer" in bas or "avait" in bas:
            continue
        m_comp = _COMPETENCES_RE.search(fenetre)
        if m_comp:
            return fenetre.strip()[:90]
    return None


def _butin_salle_courante(narration: str, etat: dict[str, Any]) -> Optional[str]:
    """Objet de BUTIN narré qui correspond au trésor CANONIQUE de la salle
    courante (champ `tresor` du manifeste) — pour le rattrapage
    `inventaire_ajouter(portee="auto")` quand le modèle narre la découverte
    sans tool (partie 2dfa9c75/audit : « vous trouvez une épée longue »
    narrée, objet jamais ajouté).

    Le nom extrait de la prose doit RECOUPER le champ `tresor` (≥ 1 mot
    fort commun) — pas d'objet inventé hors salle. None si aucun
    rapprochement (ou si aucun inventaire-tool n'a déjà tourné, vérifié par
    l'appelant)."""
    if not narration or _BUTIN_VERBE_RE.search(narration) is None:
        return None
    donjon = etat.get("donjon") or {}
    courant = donjon.get("courant") or [0, 0]
    try:
        cx, cy = int(courant[0]), int(courant[1])
    except (TypeError, ValueError, IndexError):
        return None
    salle = next(
        (s for s in (donjon.get("grille") or [])
         if isinstance(s, dict) and s.get("x") == cx and s.get("y") == cy),
        None,
    )
    tresor = str((salle or {}).get("tresor") or "").strip()
    if not tresor:
        return None
    from ..tools.inventaire import _norm  # pylint: disable=import-outside-toplevel

    mots_tresor = {w for w in _norm(tresor).split() if len(w) >= 4}
    meilleure: tuple[int, str] | None = None
    for m in _BUTIN_OBJET_RE.finditer(narration):
        nom_candidat = (m.group(1) or "").strip()
        if len(nom_candidat) < 4:
            continue
        mots_cand = {w for w in _norm(nom_candidat).split()
                     if len(w) >= 4 and w not in (
                         "sac", "coffre", "petit", "bois", "boite", "boîte")}
        communs = mots_cand & mots_tresor
        if not communs:
            continue
        score = len(communs)
        if meilleure is None or score > meilleure[0]:
            meilleure = (score, nom_candidat)
    return meilleure[1] if meilleure else None


def _deplacement_local(narration: str, etat: dict[str, Any]) -> bool:
    """Vrai si le déplacement narré reste DANS la localité courante
    (micro-déplacement libre — règle 7 : « les rues de la ville »).

    Partie 15aa0b6f : « Je quitte les lieux et me dirige chez le marchand »
    — le groupe est DÉJÀ à Silverymoon : marcher jusqu'à la boutique est
    libre, mais D1 relançait (aucun tool de déplacement) et le modèle,
    sans bonne option (`carte_donjon_explorer(est)` = la route du repère,
    `voyage_demarrer` = l'inter-cités), bouclait sur la copie de [5].
    Dans un DONJON, l'exploration reste toujours mécanique."""
    lieu = etat.get("lieu") or {}
    if str(lieu.get("type") or "").strip().lower() in ("donjon", "donjons"):
        return False
    lieu_nom = str(lieu.get("nom") or "").strip()
    t = narration or ""
    if not t:
        return False
    # Une AUTRE ville connue du répertoire est mentionnée → vrai voyage.
    try:
        from .. import villes as _villes
        for nom_v in _villes.VILLES_TYPES:
            nv = nom_v.lower()
            if lieu_nom and nv == lieu_nom.lower():
                continue
            if nv in t.lower():
                return False
    except Exception:                                        # noqa: BLE001
        pass
    # Lieu intra-muros (marché, boutique, auberge, forgeron…) ou la ville
    # elle-même citée → déplacement local, libre.
    if re.search(
        r"\b(march[ée]\w*|boutique|échoppe|forgeron|armurerie|auberge|"
        r"taverne|temple|guilde|rues?|place du|marchand)\b", t,
        re.IGNORECASE,
    ):
        return True
    if lieu_nom and lieu_nom.lower() in t.lower():
        return True
    return False


def _mention_monstre_quantifiee(t: str, n: str) -> bool:
    """True si au moins une occurrence du nom normalisé `n` dans le texte
    normalisé `t` est précédée d'un QUANTIFICATEUR (nombre, « plusieurs »,
    « horde »…). Les mentions non quantifiées (« les ombres du couloir »)
    sont traitées comme du décor, jamais comme une rencontre forcée."""
    for m in re.finditer(re.escape(n) + r"s?", t):
        avant = t[max(0, m.start() - 20):m.start()]
        if _RE_QUANTIFIANT_FR.search(avant):
            return True
    return False


def _ennemis_annonces(texte: str, ctx: Any) -> Optional[str]:
    """Extrait les ennemis du bestiaire qui ATTAQUENT le groupe dans une
    narration (noms + quantités) — pour forcer `engager_combat` quand le
    modèle narre une embuscade en pur prose sans l'appeler (partie
    77e2862b : « cinq squelettes ... vous attaquent » narré sans aucun
    tool call, la calibration d'équilibre n'a jamais tourné). Renvoie la
    chaîne `monstres` pour `engager_combat`, ou None si rien détecté.

    Deux garde-fous anti-faux-positifs (partie dfccc120) :
    - les noms génériques du français (ombre, silhouette…) ne déclenchent
      JAMAIS l'engagement forcé ;
    - une mention non quantifiée (« les ombres de l'auberge ») est du
      décor : il faut « deux ombres », « cinq squelettes »…
    """
    if not texte or not _NARRATION_ATTAQUE_RE.search(texte):
        return None
    t = _normalise_pour_compare(texte)
    if _NARRATION_PASSE_RE.search(t):
        return None
    try:
        from ..tools.monstres import _load_bestiaire
        mons = _load_bestiaire(ctx)
    except Exception:                                        # noqa: BLE001
        return None
    if not isinstance(mons, dict):
        return None
    vus: list[str] = []
    # 🔤 Partie 0e615b81 : alias de variante — « le lycanthrope attaque »
    # doit détecter le Loup-garou (le mot « lycanthrope » ne figure dans
    # AUCUN nom du bestiaire). Alias = table de départ + champ « alias »
    # éditable dans le bestiaire.
    try:
        from ..tools.monstres import _aliases_variante
        _alias_par_cle: dict[str, list[str]] = {}
        for _a, _cle in _aliases_variante(mons).items():
            _alias_par_cle.setdefault(_cle, []).append(
                _normalise_pour_compare(_a))
    except Exception:                                        # noqa: BLE001
        _alias_par_cle = {}
    for k, v in mons.items():
        if k == "_meta" or not isinstance(v, dict):
            continue
        nom = str(v.get("nom") or "").strip()
        n = _normalise_pour_compare(nom)
        # Noms trop courts : faux positifs garantis (« orc » ⊂ « torche »).
        if len(n) < 5 or n in ("monstre", "monstres"):
            continue
        if n in _ENNEMIS_MOTS_GENERIQUES:
            continue
        aliases_k = _alias_par_cle.get(str(k)) or []
        present = n in t or n + "s" in t or any(a in t for a in aliases_k)
        if not present:
            continue
        # Quantité OBLIGATOIRE : nombre (chiffre ou mot) juste avant la
        # mention — sans elle, la mention est du décor et on passe.
        motifs = [n, n + "s"] + list(aliases_k)
        if not any(_mention_monstre_quantifiee(t, mo) for mo in motifs):
            continue
        # Compte de la première occurrence quantifiée.
        compte = 1
        for mo in motifs:
            for m in re.finditer(re.escape(mo) + r"s?", t):
                avant = t[max(0, m.start() - 20):m.start()]
                mn = _RE_QUANTIFIANT_FR.search(avant)
                if mn:
                    j = mn.group(1)
                    compte = int(j) if j.isdigit() else _NOMBRES_FR.get(j, 1)
                    break
            else:
                continue
            break
        vus.extend([nom] * max(1, min(compte, 6)))
    if not vus:
        return None
    return ", ".join(vus[:6])


def _ennemis_salle_courante(texte: str, ctx: Any) -> Optional[str]:
    """Ennemis CANONIQUES de la salle courante (manifeste de scénario,
    champ `ennemis`) si la narration décrit une ATTAQUE — pour forcer
    `engager_combat` quand le modèle narre l'embuscade du module en prose
    SANS nommer les créatures (partie b59b4a9a : « des pierres tombent du
    plafond, frappant Margoth avec violence » dans la salle « Perceur ×6 » —
    la détection par noms du bestiaire ne voyait rien, le combat canonique
    était esquivé, le modèle inventait même une « attaque magique » qui
    désintégrait les ennemis sans un seul dé).

    Les ennemis DÉJÀ VAINCUS dans la partie (mémoire de campagne) sont
    exclus — pas de re-engagement des salles nettoyées. None si rien à
    engager (pas d'attaque narrée, salle sans `ennemis`, tous vaincus)."""
    if not texte or not _NARRATION_ATTAQUE_RE.search(texte):
        return None
    t = _normalise_pour_compare(texte)
    if _NARRATION_PASSE_RE.search(t):
        return None
    try:
        from ..game.state import PartyState
        etat = PartyState(
            data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
            max_history=0,
        ).load()
    except Exception:                                        # noqa: BLE001
        return None
    donjon = etat.get("donjon") or {}
    courant = donjon.get("courant") or [0, 0]
    try:
        cx, cy = int(courant[0]), int(courant[1])
    except (TypeError, ValueError, IndexError):
        return None
    salle = next(
        (s for s in (donjon.get("grille") or [])
         if isinstance(s, dict) and s.get("x") == cx and s.get("y") == cy),
        None,
    )
    declares = salle.get("ennemis") if isinstance(salle, dict) else None
    if not declares:
        return None
    # Créatures déjà battues dans cette partie (mémoire de campagne).
    battus: set[str] = set()
    for ent in ((etat.get("memoire") or {}).get("monstres_combattus") or []):
        for nom in (ent or {}).get("noms") or []:
            n = _normalise_pour_compare(str(nom or ""))
            if n:
                battus.add(n)
    noms: list[str] = []
    for brut in declares:
        item = re.sub(r"\([^)]*\)", "", str(brut or "")).strip()
        m = re.match(r"^(.*?)(?:\s*[×xX]\s*(\d+))?$", item)
        nom = (m.group(1) if m else "").strip(" .-–—")
        if not nom:
            continue
        if _normalise_pour_compare(nom) in battus:
            continue
        try:
            nb = int((m.group(2) if m else None) or 1)
        except ValueError:
            nb = 1
        noms.extend([nom] * max(1, min(nb, 6)))
    if not noms:
        return None
    return ", ".join(noms[:6])


def _assemble_narrations(intermediaires: list[str], finale: str) -> list[str]:
    """Fusionne les narrations intermédiaires et la narration finale en
    gardant une seule version par scène.

    Contexte : les narrations produites EN MÊME TEMPS que les appels
    d'outils précèdent la narration finale (dm + historique). Sans cela,
    une intro de scène narrée avant `memoire_lieu`/`etat_partie_patch`
    n'atteignait jamais le joueur (partie 43234a00 : « une bonne partie de
    la narration manque »).

    Dédoublonnage « brouillon supplanté » : quand le modèle narre toute la
    scène PUIS appelle un outil qui invalide sa première version (refus
    `engager_combat` « trois Ghouls » → re-narration « deux Ghouls »,
    partie 54de40ed), le joueur lisait TOUTES les versions à la suite. Un
    bloc intermédiaire qui recouvre fortement un bloc ULTÉRIEUR
    (intermédiaire ou final) est un brouillon supplanté : il est abandonné
    au profit du plus récent, qui est la version corrigée. Les blocs
    distincts (intro de scène, puis continuation) restent conservés.
    """
    blocs = [*intermediaires, finale]
    gardes: list[str] = []
    for i in range(len(blocs) - 1, -1, -1):
        cand = _normalise_pour_compare(blocs[i])
        if len(cand) >= _REPET_MIN_CANDIDAT:
            supplante = False
            for garde in gardes:
                ref = _normalise_pour_compare(garde)
                if not ref:
                    continue
                pref = cand[:_REPET_PREFIXE]
                if pref and pref in ref:
                    supplante = True
                    break
                b_c = _bigrammes_fenetres(cand.split())
                b_r = _bigrammes_fenetres(ref.split())
                if b_c and b_r and (
                    len(b_c & b_r) / len(b_c) >= _REPET_SEUIL_CHEVAUCHEMENT
                ):
                    supplante = True
                    break
            if supplante:
                _log.info(
                    "narration intermédiaire supplantée par une "
                    "version ultérieure : %s…", blocs[i][:80],
                )
                continue
        gardes.insert(0, blocs[i])
    return gardes


# --------------------------------------------------------------------------- #
#  Dégénérescence INTRA-réponse : le modèle boucle sur ses propres phrases
#  (observé en e2e : « la grande hache de groth s'abat… » recopiée N fois,
#  réponses de plusieurs milliers de tokens qui épuisent le budget du tour).
#  On tronque dès la 3e occurrence d'une même phrase — les relances voient
#  alors un contexte propre au lieu d'un monstre de texte dégénéré.
# --------------------------------------------------------------------------- #
_DEGEN_MIN_MOTS = 6          # phrase significative (pas « Et puis. »)
_DEGEN_OCCURRENCES = 3       # seuil de boucle


# 🛡️ B29 (audit parties complètes) : la garde ne doit PAS déclencher sur la
# ligne de statut que le serveur lui-même injecte (« **Phase : Combat** —
# Initiative 14 — C'est au tour de Brann. ») : le modèle la répète en tête de
# réponse comme un rituel, et la troncature supprimait alors 6,7 ko de
# narration d'un coup. Les phrases de statut sont donc ignorées, et une
# troncature réelle laisse une marque visible + ne descend jamais sous un
# plancher (une narration de 400 chars coupée net est pire qu'une répétition).
_DEGEN_MOTIFS_EXCLUS = (
    "phase :",
    "initiative",
    "c est au tour de",
    "au tour de",
    "ennemis vivants",
    "jets officiels",
    "que faites-vous",
    "que souhaitez-vous",
)
_DEGEN_PLANCHER_CHARS = 400


def _est_motif_statut(phrase: str) -> bool:
    """Vrai pour une ligne de statut de combat injectée par le serveur."""
    c = _normalise_pour_compare(phrase)
    return any(m in c for m in _DEGEN_MOTIFS_EXCLUS)


def tronquer_degeneration(texte: str) -> tuple[str, str]:
    """Renvoie (texte_tronqué, phrase_en_boucle). Si aucune phrase d'au moins
    `_DEGEN_MIN_MOTS` mots (normalisée) ne revient `_DEGEN_OCCURRENCES` fois,
    renvoie (texte, "") — texte intact."""
    if not texte:
        return texte, ""
    phrases = [p for p in re.split(r"(?<=[.!?…])\s+|\n+", texte) if p.strip()]
    compteur: dict[str, int] = {}
    for i, p in enumerate(phrases):
        cle = _normalise_pour_compare(p)
        if len(cle.split()) < _DEGEN_MIN_MOTS or _est_motif_statut(p):
            continue
        compteur[cle] = compteur.get(cle, 0) + 1
        if compteur[cle] >= _DEGEN_OCCURRENCES:
            # Garde le texte jusqu'AVANT la 3e occurrence de la phrase.
            coupe = " ".join(x.strip() for x in phrases[:i]).strip()
            if len(coupe) < _DEGEN_PLANCHER_CHARS:
                # Narration trop courte pour être coupée : on garde tout et
                # on laisse la répétition (moins dommageable qu'un texte
                # tronqué au milieu d'une phrase).
                _log.info(
                    "dégénérescence intra-réponse détectée (« %s… » ×%d) "
                    "mais narration de %d chars seulement : conservation",
                    cle[:60], compteur[cle], len(coupe),
                )
                return texte, ""
            _log.warning(
                "dégénérescence intra-réponse détectée (« %s… » ×%d) — "
                "troncature %d → %d chars",
                cle[:60], compteur[cle], len(texte), len(coupe),
            )
            return coupe.rstrip(" ,;:.!?…") + "\n\n[… _la répétition a été supprimée par le serveur_]", cle[:80]
    return texte, ""


def _segments_thinking(texte: str) -> list[str]:
    """Contenus des blocs <think>…</think> (ou non fermés) d'une réponse
    brute — pour récupérer les appels d'outils émis DANS le raisonnement
    (Qwen3.5, issue llama.cpp #20837)."""
    if not texte:
        return []
    return [
        m.group(1)
        for m in re.finditer(r"<think\s*>(.*?)(?:</think\s*>|\Z)", texte,
                             re.DOTALL)
        if m.group(1).strip()
    ]


_RESIDUE_XML_ARGS_RE = re.compile(
    r"</?(?:parameter|function|tool_call|tool|item)\b[^>]*>", re.IGNORECASE)


def _nettoyer_args_outils(args: dict[str, Any]) -> dict[str, Any]:
    """Nettoie la contamination XML→JSON du modèle (Qwen3.5-9B Q4, format
    Hermes demandé) : l'ancien format `<parameter=clé>valeur</parameter>`
    fuit PARFOIS DANS LES VALEURS de chaînes des arguments JSON — le JSON
    reste valide mais les valeurs sont corrompues (ex. vérifié :
    nom="Throk'mar</parameter>\\n<parameter=degats>\\n5</parameter>"). On
    retire les résidus de balises + quotes/backslashes parasites en bord
    de valeur (« 1d20" » → « 1d20 ») ; une valeur qui devient vide est
    retirée. Idempotent, sans effet sur des valeurs saines."""
    propres: dict[str, Any] = {}
    for cle, val in (args or {}).items():
        if isinstance(val, str) and _RESIDUE_XML_ARGS_RE.search(val):
            # Garde le préfixe sain AVANT la première balise : dans
            # « Throk'mar</parameter>\n<parameter=degats>\n5</parameter> »,
            # le « 5 » appartient au champ fantôme `degats` (déjà présent
            # en tant que vrai champ JSON), pas au nom.
            v = _RESIDUE_XML_ARGS_RE.split(val)[0]
            v = re.sub(r"\s+", " ", v).strip(" \t\"'\\")
            if v:
                propres[cle] = v
        elif isinstance(val, str):
            v = val.strip().strip("\"'").strip("\\").strip()
            propres[cle] = v if v else val
        else:
            propres[cle] = val
    return propres


# --------------------------------------------------------------------------- #
#  Extraction des appels d'outils en mode prompt-based
# --------------------------------------------------------------------------- #
_TOOL_TAG_RE = re.compile(
    r'<tool\s+name="(?P<name>[A-Za-z_][A-Za-z0-9_]*)"(?P<args>[^>]*)>',
    re.IGNORECASE,
)
_ARG_RE = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<val>(?:\\.|[^"\\])*)"')


# --------------------------------------------------------------------------- #
#  Normalisation des jetons canal-gemma dans les appels d'outils
# --------------------------------------------------------------------------- #
# Gemma 4 (fonction-calling natif sur llama.cpp) écrit parfois les valeurs
# de chaîne avec le jeton spécial `<|"|>` à la place des guillemets réels :
#   <tool_call>engager_combat{monstres:<|"|>Gobelin, Gobelin<|"|>}</tool_call>
# Sans normalisation, ces jetons corrompent le découpage des arguments
# (la virgule interne n'est plus protégée) et l'appel aboutit avec des
# arguments VIDES — résolution sans effet. On les remplace par `"`.
_GEM_QUOTE_TOKENS = ("<|\"|>",)


def _norm_gemma_quote_tokens(text: Optional[str]) -> Optional[str]:
    """Remplace les jetons canal-gemma (`<|"|>`) par de vrais guillemets.

    Renvoie `text` inchangé quand aucun jeton n'est présent (rapide, sans
    allocation supplémentaire sur le chemin nominal).
    """
    if not text:
        return text
    out = text
    for tok in _GEM_QUOTE_TOKENS:
        if tok in out:
            out = out.replace(tok, '"')
    return out


def parse_prompt_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extrait les balises `<tool name=".." key="value" ...>` du texte.

    Renvoie une liste de dicts `{"name": str, "arguments": {...}}` au même
    format que `tool_calls` OpenAI natif. Les valeurs sont dé-échappées
    (anti-escape des quotes/backslash).
    """
    calls: list[dict[str, Any]] = []
    for m in _TOOL_TAG_RE.finditer(text):
        name = m.group("name")
        args_blob = m.group("args")
        args: dict[str, Any] = {}
        for am in _ARG_RE.finditer(args_blob):
            v = am.group("val")
            # Dé-échapper \" \\ \n \t
            v = v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
            args[am.group("key")] = v
        calls.append({"name": name, "arguments": args})
    return calls


def strip_prompt_tool_calls(text: str) -> str:
    """Retire les balises `<tool ...>` du texte (pour le rendu narration)."""
    # Supprime la ligne entière contenant la balise tool
    return re.sub(
        r"^[ \t]*<tool\s+name=\"[^\"]*\"[^>]*>[ \t]*\r?\n?",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )


# --------------------------------------------------------------------------- #
#  Blocs <tool_call>{...}</tool_call> (format texte llama.cpp / Qwen / jinja)
# --------------------------------------------------------------------------- #
_TOOLCALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*(?P<json>\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def extract_toolcall_blocks(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extrait les blocs `<tool_call>{"name": ..., "arguments": {...}}</tool_call>`.

    llama.cpp sans `--jinja` (ou avec un template de chat non-outils) laisse
    parfois l'appel en texte brut dans `content` au lieu de peupler
    `tool_calls`. On normalise ici ces blocs vers le format natif.

    Renvoie `(calls, texte_nettoyé)` — les blocs sont retirés du texte.
    """
    calls: list[dict[str, Any]] = []
    cleaned = text
    for m in list(_TOOLCALL_BLOCK_RE.finditer(text)):
        try:
            data = json.loads(_norm_gemma_quote_tokens(m.group("json")))
        except json.JSONDecodeError:
            continue
        name = data.get("name") or (data.get("function") or {}).get("name") or ""
        raw_args = (
            data.get("arguments")
            if "arguments" in data
            else (data.get("function") or {}).get("arguments")
        )
        if not name:
            continue
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                raw_args = {}
        calls.append({"name": str(name), "arguments": raw_args or {}})
    if calls:
        cleaned = _TOOLCALL_BLOCK_RE.sub("", cleaned)
    return calls, cleaned


# --------------------------------------------------------------------------- #
#  Balises <tool_call name=".." key="value" ... /> (attributs XML)
# --------------------------------------------------------------------------- #
# Variante observée en partie réelle (Qwen/Hermes sur llama.cpp) : le modèle
# écrit l'appel en balise auto-fermée avec les arguments en ATTRIBUTS XML,
# `<tool_call name="lancer_d20" difficulte="30" raison="..." />`, au lieu du
# bloc JSON `<tool_call>{...}</tool_call>`. Ni le parseur `<tool ...>` (mode
# prompt) ni le parseur de blocs JSON ne la couvrent — elle fuyait alors
# telle quelle dans la narration montrée au joueur.
_TOOLCALL_ATTR_SELFCLOSE_RE = re.compile(
    r"<tool_call\s+(?P<attrs>[^<>]*?)/>",
    re.IGNORECASE | re.DOTALL,
)
_TOOLCALL_ATTR_PAIR_RE = re.compile(
    r"<tool_call\s+(?P<attrs>[^<>]*?)>\s*</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
# Fermeture orpheline résiduelle (ex. `<tool_call ... /> </tool_call>`).
_TOOLCALL_ORPHAN_CLOSE_RE = re.compile(r"</tool_call\s*>", re.IGNORECASE)


def extract_toolcall_attr_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extrait les balises `<tool_call name=".." key="value" ... />`.

    Renvoie `(calls, texte_nettoyé)` au même format que les tool_calls natifs
    — les balises sont retirées du texte. La valeur de l'attribut `name` sert
    de nom d'outil, les autres attributs deviennent les arguments.
    """
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for pat in (_TOOLCALL_ATTR_SELFCLOSE_RE, _TOOLCALL_ATTR_PAIR_RE):
        for m in pat.finditer(text or ""):
            args: dict[str, Any] = {}
            for am in _ARG_RE.finditer(m.group("attrs")):
                v = am.group("val")
                v = v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
                v = _norm_gemma_quote_tokens(v).replace('"', "")
                args[am.group("key")] = v
            name = str(args.pop("name", "")).strip()
            if not name:
                continue
            calls.append({"name": name, "arguments": args})
            spans.append((m.start(), m.end()))
    cleaned = text
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    return calls, cleaned


# --------------------------------------------------------------------------- #
#  Blocs <tool_call><function=nom><parameter=clé>…</parameter></function>
# --------------------------------------------------------------------------- #
# Variante observée en partie réelle (54de40ed) : le modèle écrit l'appel
# façon ChatML — wrapper `<tool_call>` optionnel, `<function=nom>`, puis les
# arguments en enfants `<parameter=clé>valeur</parameter>`. Pire : après le
# premier paramètre il OUBLIE le préfixe `parameter=` et invente des balises
# nues (`<item>…</item>`, `<quantity>1</quantity>`). Aucun parseur existant
# ne couvrait ce format → il fuyait tel quel dans la narration montrée au
# joueur et l'action n'était JAMAIS exécutée.
_FUNCTION_BLOCK_RE = re.compile(
    r"[ \t]*(?:<tool_call>\s*)?"
    r"<function=(?P<name>[^<>=\s]+)\s*>"
    r"(?P<body>.*?)"
    r"</function\s*>"
    r"(?:\s*</tool_call\s*>)?[ \t]*",
    re.IGNORECASE | re.DOTALL,
)
_FUNCTION_PARAM_RE = re.compile(
    r"<parameter=(?P<pkey>[^<>=\s]+)\s*>(?P<pval>.*?)</parameter\s*>"
    r"|<(?P<bkey>[a-zA-Z_][\w\-]*)>(?P<bval>.*?)</(?P=bkey)\s*>",
    re.IGNORECASE | re.DOTALL,
)


def extract_function_blocks(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extrait les blocs `<function=nom>…</function>` (wrapper `<tool_call>`
    optionnel) avec arguments `<parameter=clé>` ou balises nues.

    Renvoie `(calls, texte_nettoyé)` au format des tool_calls natifs ; les
    blocs et leurs wrappers résiduels sont retirés du texte.
    """
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for m in _FUNCTION_BLOCK_RE.finditer(text or ""):
        name = (m.group("name") or "").strip().strip("\"'")
        if not name:
            continue
        args: dict[str, Any] = {}
        for pm in _FUNCTION_PARAM_RE.finditer(m.group("body")):
            key = (pm.group("pkey") or pm.group("bkey") or "").strip()
            val = (pm.group("pval") if pm.group("pkey") is not None
                   else pm.group("bval")) or ""
            if not key:
                continue
            args[key] = val.strip()
        calls.append({"name": name, "arguments": args})
        spans.append((m.start(), m.end()))
    if not calls:
        return [], text or ""
    cleaned = text or ""
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    # Wrappers orphelins résiduels (le modèle ferme rarement `<tool_call>`).
    cleaned = re.sub(r"</?tool_call\s*>", "", cleaned, flags=re.IGNORECASE)
    return calls, cleaned


# --------------------------------------------------------------------------- #
#  Résolution floue de noms d'outils (Lancer_d20 → lancer_d20, etc.)
# --------------------------------------------------------------------------- #
def _norm_tool_name(name: str) -> str:
    """Normalise un identifiant pour comparaison : minuscules, sans accents,
    sans underscores/tirets (« Lancer_d20 », « lancer-dés », « Lancer_dés »
    → « lancerd20 » / « lancerdes »)."""
    import unicodedata
    nf = unicodedata.normalize("NFKD", (name or "").lower())
    ascii_ = "".join(c for c in nf if not unicodedata.combining(c))
    return re.sub(r"[_\-\s]+", "", ascii_)


def resolve_tool_name(raw: str, tools: dict[str, Any]) -> Optional[str]:
    """Résout un nom d'outil écrit par le LLM vers un tool réel du registre.

    Ordre : exact → insensible à la casse → normalisé (accents/underscores) →
    containment unique (alias court non ambigu, ex. « infliger_degats » →
    « fiche_perso_infliger_degats »). Renvoie None si rien ne correspond.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw in tools:
        return raw
    lower = raw.lower()
    for n in tools:
        if n.lower() == lower:
            return n
    norm = _norm_tool_name(raw)
    if norm:
        for n in tools:
            if _norm_tool_name(n) == norm:
                return n
        # Alias court non ambigu (garder une longueur minimale pour éviter
        # les collisions du type « lancer » → plusieurs candidats).
        if len(norm) >= 6:
            cands = [
                n for n in tools
                if norm in _norm_tool_name(n) or _norm_tool_name(n) in norm
            ]
            if len(cands) == 1:
                return cands[0]
    return None


def _norm_arg_name(name: str) -> str:
    return _norm_tool_name(name)


# Synonymes d'arguments fréquemment inventés par les LLM (anglicismes,
# variantes) → nom CANONIQUE du paramètre. Consultés quand la résolution
# exacte/normalisée/contenue échoue, et VÉRIFIÉS contre les params réels du
# tool (un alias qui ne correspond à aucun paramètre est simplement ignoré).
# Observé en 54de40ed : `inventaire_ajouter(item=…, quantity=…)` au lieu de
# `objet`/`quantite` → l'objet n'était jamais ajouté.
_ALIASES_ARGS_NORM: dict[str, str] = {
    "item": "objet",
    "object": "objet",
    "itemname": "objet",
    "quantity": "quantite",
    "qty": "quantite",
    "count": "quantite",
    "amount": "quantite",
    "weight": "poids",
    "character": "nom",
    "charactername": "nom",
    "charname": "nom",
}


def sanitize_tool_args(
    spec: Any, args: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Nettoie les arguments d'un appel récupéré du LLM (natif ou prose).

    - rapproche les noms d'arguments mal orthographiés du nom réel
      (ex. `attaquant` → `nom_attaquant`) ;
    - coerce les valeurs numériques reçues en chaîne ;
    - REJETTE les valeurs placeholders non numériques pour les paramètres
      int/float (ex. `bonus_attaque="Calculé sur la fiche"`, `degats=N`) :
      le paramètre retombe alors sur sa valeur par défaut et le tool garde
      son propre recoupement (fiche/bestiaire).

    Renvoie `(args_propres, notes)` — les notes expliquent les ajustements
    et sont renvoyées au LLM dans le résultat du tool.
    """
    import inspect as _inspect
    expected = getattr(spec, "expected_args", {}) or {}
    norm_map = {_norm_arg_name(p): p for p in expected}
    notes: list[str] = []
    out: dict[str, Any] = {}
    for key, val in list((args or {}).items()):
        # 1. Résolution du nom d'argument.
        target = expected.get(key) and key or norm_map.get(_norm_arg_name(key))
        if target is None:
            cands = [
                p for p in expected
                if _norm_arg_name(p) == _norm_arg_name(key)
                or (_norm_arg_name(key) and (
                    _norm_arg_name(key) in _norm_arg_name(p)
                    or _norm_arg_name(p) in _norm_arg_name(key)
                ))
            ]
            if len(cands) == 1:
                target = cands[0]
            elif len(cands) > 1:
                # Clé courte ambiguë (ex. `cible` → `ca_cible` ET `nom_cible`) :
                # on préfère le paramètre « nom_<x> » (la cible/le sujet nommé),
                # plus sémantiquement fidèle que la variante de CA/stat.
                nk = _norm_arg_name(key)
                nom_hits = [p for p in cands
                            if _norm_arg_name(p).startswith("nom") and nk in _norm_arg_name(p)]
                if len(nom_hits) == 1:
                    target = nom_hits[0]
        if target is None:
            alias = _ALIASES_ARGS_NORM.get(_norm_arg_name(key))
            if alias:
                cand = alias if alias in expected else norm_map.get(alias)
                if cand:
                    target = cand
                    notes.append(
                        f"argument « {key} » interprété comme « {cand} »"
                    )
        if target is None:
            continue  # argument inconnu → ignoré (invoke_tool filtre aussi)
        # 2. Placeholders numériques (« N », « X », « Calculé sur la fiche »).
        hint = spec.resolved_hints.get(target)
        origin = getattr(hint, "__origin__", None)
        if origin is not None:
            from typing import Union as _U
            if origin is _U:
                real = [a for a in getattr(hint, "__args__", [])
                        if a is not type(None)]
                hint = real[0] if real else hint
        # Les params annotés `Any` (ex. bonus_attaque: Any = 0) se trahissent
        # par leur valeur par défaut numérique.
        param = expected.get(target)
        if param is not None:
            from typing import Any as _Any
            hint_is_vague = (
                hint is None
                or hint is _inspect.Parameter.empty
                or hint is _Any
                or hint is object
            )
            if hint_is_vague and isinstance(param.default, (int, float)) \
                    and not isinstance(param.default, bool):
                hint = int if isinstance(param.default, int) else float
        if hint in (int, float) and isinstance(val, str):
            v = val.strip()
            if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", v):
                notes.append(
                    f"argument « {key} » ignoré (valeur non numérique "
                    f"« {val[:60] } ») — le tool utilise sa valeur par défaut"
                )
                continue
        out[target] = val
    return out, notes


# --------------------------------------------------------------------------- #
#  Récupération des appels écrits en syntaxe fonctionnelle dans la prose
# --------------------------------------------------------------------------- #
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_éèêëàâäîïôöûüçÉÈÊÀÂÎÔÛÇ]*")
# Placeholders méta que le modèle écrit autour de ses faux appels.
_PROSE_PLACEHOLDER_RES = [
    re.compile(r"\*?\(\s*Appel\s+au\s+tool\b[^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Appel\s+au\s+sort\s*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Appels?\s+d['']outils?[^)]*\)\*?", re.IGNORECASE),
    # « *(Appel de l'outil lancer_attaque pour résoudre le combat)* » —
    # variante détectée par _SIMULATION_PATTERNS mais PAS nettoyée avant
    # (partie dfccc120 : le placeholder est parti jusqu'au joueur).
    re.compile(r"\*?\(\s*Appel\s+de\s+l['']outil[^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Appel\s+des\s+outils[^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Attente\s+du\s+r[ée]sultat\b[^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Le\s+r[ée]sultat\s+du\s+jet\s+est\s+appliqu[ée][^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Les\s+d[ée]g[âa]ts\s+sont\s+calcul[ée]s?[^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Le\s+jet\s+d['']attaque\s+est\s+lanc[ée][^)]*\)\*?", re.IGNORECASE),
    re.compile(r"\*?\(\s*Simulation[^)]*\)\*?", re.IGNORECASE),
]


def _parse_args_blob_colon(blob: str) -> dict[str, Any]:
    """Parse une liste d'arguments en syntaxe pseudo-JSON `clé: valeur`.

    Gemma écrit parfois ses appels blocs avec deux-points et clés non
    quotées : `<tool_call>engager_combat{monstres:"Gobelin, Gobelin"}`.
    On découpe sur les virgules hors chaînes, puis on splitte sur le
    premier `:` (ou `=`) pour retrouver clé/valeur, avec support des
    valeurs quotées et des scalaires typés.
    """
    args: dict[str, Any] = {}
    parts: list[str] = []
    cur: list[str] = []
    in_str: Optional[str] = None
    for ch in blob:
        if in_str:
            cur.append(ch)
            if ch == "\\":
                cur.append("")
                continue
            if ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
            cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    for part in parts:
        sep = None
        in_s: Optional[str] = None
        for i, ch in enumerate(part):
            if in_s:
                if ch == "\\":
                    continue
                if ch == in_s:
                    in_s = None
                continue
            if ch in "\"'":
                in_s = ch
            elif ch in ":=":
                sep = i
                break
        if sep is None:
            continue
        key = part[:sep].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = part[sep + 1:].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            inner = val[1:-1]
            inner = inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
            args[key] = inner
            continue
        if re.fullmatch(r"[+-]?\d+", val):
            args[key] = int(val)
            continue
        if re.fullmatch(r"[+-]?\d+\.\d+", val):
            args[key] = float(val)
            continue
        if val.lower() in ("true", "vrai"):
            args[key] = True
            continue
        if val.lower() in ("false", "faux"):
            args[key] = False
            continue
        args[key] = val
    return args


_BRACE_CALL_RE = re.compile(
    r"(?:<tool_call\s*>)?\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*{\s*(?P<blob>[^{}]*?)\s*}"
    r"(?:\s*</tool_call\s*>)?"
)


def parse_prose_brace_calls(
    text: str, tools: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Récupère les appels écrits en bloc accolade : `nom{clé: valeur, ...}`.

    Format observé chez Gemma/llama.cpp : soit nu (`engager_combat{...}`),
    soit enveloppé dans une balise `<tool_call>...{...}</tool_call>` (parfois
    sans balise fermante, avec le nom de l'outil entre `<tool_call>` et `{`).
    Ni le parseur `<tool>` (mode prompt), ni le parseur de blocs JSON stricts,
    ni le parseur `outil(...)` ne couvrent cette variante — elle fuyait alors
    telle quelle dans la narration (tour sans résolution).

    Renvoie `(calls, texte_nettoyé)` au même format que les tool_calls natifs.
    """
    if not text or "{" not in text:
        return [], text
    text = _norm_gemma_quote_tokens(text)
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for m in _BRACE_CALL_RE.finditer(text):
        raw_name = m.group("name")
        if not raw_name:
            continue
        resolved = resolve_tool_name(raw_name, tools)
        if not resolved:
            continue
        args = _parse_args_blob_colon(m.group("blob"))
        args, _notes = sanitize_tool_args(tools[resolved], args)
        calls.append({"name": resolved, "arguments": args})
        spans.append((m.start(), m.end()))
    if not calls:
        return [], text
    cleaned = text
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    cleaned = _tidy_empty_lines(cleaned)
    cleaned = re.sub(r"[ \t]*</tool_call\s*>", "", cleaned)
    cleaned = re.sub(r"[ \t]*<tool_call[ \t]*>?", "", cleaned)
    return calls, cleaned


def _parse_args_blob(blob: str) -> dict[str, Any]:
    """Parse `key="val", key2=12, key3='x'` en dict (valeurs typées)."""
    args: dict[str, Any] = {}
    depth = 0
    cur = ""
    parts: list[str] = []
    in_str: Optional[str] = None
    i = 0
    n = len(blob)
    while i < n:
        ch = blob[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                # Caractère échappé (ex. \" dans un JSON quoté) : la paire
                # entière appartient à la valeur et ne referme pas la chaîne.
                cur += ch + blob[i + 1]
                i += 2
                continue
            cur += ch
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "\"'":
            in_str = ch
            cur += ch
        elif ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        parts.append(cur)
    # Un fragment SANS `=` ne peut pas être un nouvel argument (syntaxe
    # invalide en Python/JSON) : c'est une valeur non quotée qui contenait
    # une virgule (ex. `participants=Brunhild:+2, Gobelin:+1`) — on le
    # rattache au fragment précédent.
    merged: list[str] = []
    for part in parts:
        if "=" not in part and merged:
            merged[-1] = merged[-1] + "," + part
        else:
            merged.append(part)
    for part in merged:
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            inner = val[1:-1]
            inner = inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
            args[key] = inner
            continue
        if re.fullmatch(r"[+-]?\d+", val):
            args[key] = int(val)
            continue
        if re.fullmatch(r"[+-]?\d+\.\d+", val):
            args[key] = float(val)
            continue
        if val.lower() in ("true", "vrai"):
            args[key] = True
            continue
        if val.lower() in ("false", "faux"):
            args[key] = False
            continue
        # Jeton nu (placeholder « N »/« X » ou valeur sans guillemets).
        args[key] = val
    return args


def _find_call_end(text: str, open_paren: int) -> Optional[int]:
    """Trouve l'index de la `)` fermante en respectant les chaînes quotées."""
    depth = 0
    in_str: Optional[str] = None
    i = open_paren
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2  # caractère échappé : sauter le suivant
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "\"'":
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def parse_prose_tool_calls(
    text: str, tools: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Récupère les appels écrits en syntaxe fonctionnelle dans la narration.

    Gemma (comme la plupart des modèles sans tool-calling natif fiable sur
    llama.cpp) écrit souvent ses appels directement en prose :

        `carte_donjon_entrer(donjon_id="Grotte du Gobelin")`
        **Lancer_d20(nom_personnage="Sylvaris", difficulte="15")**

    On détecte ces motifs (backticks, gras, texte nu), on résout le nom en
    flou vers le registre réel, on parse/sanitise les arguments, et on
    renvoie `(calls, texte_nettoyé)` où le texte nettoyé peut être montré
    au joueur. Seuls les identifiants résolvant vers un tool connu sont
    interceptés — la prose ordinaire n'est pas affectée.
    """
    if not text or "(" not in text:
        return [], text
    text = _norm_gemma_quote_tokens(text)
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for m in _IDENT_RE.finditer(text):
        start_ident = m.start()
        open_paren = m.end()
        # L'identifiant doit être immédiatement suivi de `(`.
        while open_paren < len(text) and text[open_paren] == " ":
            open_paren += 1
        if open_paren >= len(text) or text[open_paren] != "(":
            continue
        # Pas de mot-clé Python/JS courant devant (ex. fonction narrative).
        prefix = text[max(0, start_ident - 1):start_ident]
        if prefix.isalnum():
            continue
        name = m.group(0)
        resolved = resolve_tool_name(name, tools)
        if not resolved:
            continue
        end = _find_call_end(text, open_paren)
        if end is None:
            continue
        blob = text[open_paren + 1:end]
        # Éviter les gigantesques faux positifs (prose avec parenthèses).
        if len(blob) > 2000:
            continue
        args = _parse_args_blob(blob)
        args, _notes = sanitize_tool_args(tools[resolved], args)
        calls.append({"name": resolved, "arguments": args})
        # Étend le span aux décorations markdown (backticks / astérisques).
        s, e = start_ident, end + 1
        while s > 0 and text[s - 1] in "`*_~":
            s -= 1
        while e < len(text) and text[e] in "`*_~":
            e += 1
        spans.append((s, e))
    cleaned = text
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    if spans:
        # Double espace résiduel après retrait d'un appel en milieu de phrase.
        cleaned = re.sub(r"(?<=\S)  +(?=\S)", " ", cleaned)
        # Lignes devenues vides (l'appel était seul sur sa ligne) → repli des
        # sauts de ligne multiples, puis nettoyage des lignes blanches fins.
        cleaned = _tidy_empty_lines(cleaned)
        cleaned = re.sub(r"^[ \t]+\n", "\n", cleaned, flags=re.MULTILINE)
    return calls, cleaned


def _tidy_empty_lines(text: str) -> str:
    """Réduit les runs de >2 sauts de ligne consécutifs après nettoyage."""
    return re.sub(r"\n{3,}", "\n\n", text)


# --------------------------------------------------------------------------- #
#  Nettoyage streaming — filtrage des artefacts AVANT broadcast
# --------------------------------------------------------------------------- #
# Marqueurs partiels pouvant être le début d'une balise en fuite.  On retient
# le fragment terminal du buffer tant qu'il pourrait former le début d'une
# balise problématique ; on ne le broadcast qu'une fois le flush final fait.
_STREAM_LEAK_MARKERS = (
    "<tool_call", "<tool_call/", "<tool_call ",
    "</tool_call", "</tool_call>",
    "<tool", "</tool",
    "<|",  # Gemma channel-quote / thinking
    "*(",  # début de placeholder prose  *(Appel au tool …)*
)


def _safe_stream_split(buf: str) -> tuple[str, str]:
    """Sépare un buffer streaming en (texte_sûr, fragment_retenu).

    Le fragment retenu est un suffixe qui pourrait être le début d'une
    balise d'artefact (`<tool`, `<tool_call>`, `</tool`, `<|`, `*(`…).
    Seul le texte sûr est broadcast ; le fragment est réinjecté au tour
    suivant quand on aura plus de contexte pour décider.
    """
    for m in _STREAM_LEAK_MARKERS:
        if buf.endswith(m):
            return buf[: -len(m)], buf[-len(m):]
    return buf, ""


# Faux jets/dégâts « bricolés » par le LLM dans sa prose : partie 5b4e2bbe, le
# modèle a écrit « **Jet d'attaque :** 18 (réussite) / **Dégâts infligés :** 0 »
# alors que la résolution OFFICIELLE était un ÉCHEC (total 8 vs CA 15). La
# mécanique officielle est ajoutée par le serveur APRÈS ce nettoyage : toute
# ligne de jet/dégâts en prose est une invention → retirée.
_RE_FAUX_JET_PROSE = re.compile(
    r"[ \t]*(?:\*{0,2})\s*Jet d'attaque\s*:?\s*\*{0,2}[^\n]*",
    re.IGNORECASE,
)
_RE_FAUX_DEGATS_PROSE = re.compile(
    r"[ \t]*(?:\*{0,2})\s*D[ée]g[âa]ts inflig[ée]s\s*:?\s*\*{0,2}[^\n]*",
    re.IGNORECASE,
)
# « Jet de dégâts : … = ? » — invention du LLM dont la résolution n'a jamais
# été calculée (partie 5b4e2bbe msg [28]) ; contrairement au vrai jet, elle ne
# doit pas être montrée, l'officielle arrive après.
_RE_FAUX_JET_DEGATS_PROSE = re.compile(
    r"[ \t]*(?:\*{0,2})\s*Jet\s+de\s+d[ée]g[âa]ts\s*:?\s*\*{0,2}[^\n]*",
    re.IGNORECASE,
)
_RE_FORMULE_INCOMPLETE = re.compile(
    r"[ \t]*(?:\*{0,2})[^\n]*?(?:\d+[dD]\d+\s*(?:\+\s*\d+)?|[1-9]\d*\s*[dD]\d+\s*\+\s*[1-9]\d*)\s*=\s*\?\s*[^\n]*",
)


def strip_narration_artifacts(text: str, tools: Optional[dict[str, Any]] = None) -> str:
    """Nettoie la narration finale de toute trace de mécanique d'appel :

    - appels en syntaxe fonctionnelle `tool(...)` (résolvant un vrai tool) ;
    - balises `<tool ...>` et blocs `<tool_call>` résiduels ;
    - placeholders méta « *(Appel au tool …)* », « *(Attente du résultat …)* »…
    """
    if not text:
        return text
    # Retire les jetons canal-gemma résiduels (`<|"|>`) : ce sont des
    # délimiteurs de valeur internes qui ne doivent JAMAIS être montrés.
    out = text
    for tok in _GEM_QUOTE_TOKENS:
        if tok in out:
            out = out.replace(tok, "")
    if tools:
        _calls, out = parse_prose_tool_calls(out, tools)
    out = strip_prompt_tool_calls(out)
    out = _TOOLCALL_BLOCK_RE.sub("", out)
    out = _TOOLCALL_ATTR_SELFCLOSE_RE.sub("", out)
    out = _TOOLCALL_ATTR_PAIR_RE.sub("", out)
    out = _TOOLCALL_ORPHAN_CLOSE_RE.sub("", out)
    # Blocs `<function=..><parameter=..>` (ChatML/Qwen) + wrappers orphelins :
    # si un tel bloc atteint la narration FINALE (outils désactivés, budget
    # épuisé…), il ne doit JAMAIS être montré au joueur.
    out = _FUNCTION_BLOCK_RE.sub("", out)
    out = re.sub(r"</?tool_call\s*>", "", out)
    # Balises <tool>/<\/tool> orphelines ou tronquées (partie dfccc120 :
    # « Throk'mar</tool> » est parti jusqu'au joueur). Le parseur
    # strip_prompt_tool_calls n'attrape que les balises bien formées avec
    # name="…" ; ce balayage final retire tout résidu de balise tool.
    out = re.sub(r"</?tool(?=[\s/>])[^>]*>", "", out)
    for pat in _PROSE_PLACEHOLDER_RES:
        out = pat.sub("", out)
    # Faux jets/dégâts inventés en prose (cf. _RE_FAUX_JET_PROSE) : la
    # mécanique officielle arrive après, on ne garde jamais la copie du LLM.
    out = _RE_FAUX_JET_PROSE.sub("", out)
    out = _RE_FAUX_DEGATS_PROSE.sub("", out)
    out = _RE_FAUX_JET_DEGATS_PROSE.sub("", out)
    out = _RE_FORMULE_INCOMPLETE.sub("", out)
    out = _tidy_empty_lines(out)
    return out.strip()


# --------------------------------------------------------------------------- #
#  Session d'orchestration (une par message joueur)
# --------------------------------------------------------------------------- #
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Hook async pour émettre des events aux clients (image, status, deltas...)."""


# --------------------------------------------------------------------------- #
#  Confusion escalier — « descendre l'escalier » interprété comme un
#  déplacement cardinal. Régression observée avec un modèle RP finetuné qui
#  ignore le rappel prompt : le groupe RESSORTAIT de la salle d'escaliers
#  (`explorer(sud)`) au lieu de changer d'étage. Détection d'intention sur le
#  dernier message joueur + réécriture `explorer` → `carte_donjon_etage`.
# --------------------------------------------------------------------------- #
_INTENT_DESCENDRE_RE = re.compile(
    r"(descend\w*|sous-?sol\b|[eé]tage\s+(?:inf[ée]rieur|en\s+dessous|du\s+dessous)|"
    r"niveau\s+(?:inf[ée]rieur|en\s+dessous)|vers\s+le\s+bas\b|plus\s+bas\b)",
    re.IGNORECASE,
)
_INTENT_MONTER_RE = re.compile(
    r"(\b(?:monte|montes|montons|montez|monter|montant|mont[ée]e|mont[ée]es)\b|"
    r"\bremont\w*|[eé]tage\s+(?:sup[ée]rieur|au-?dessus|du\s+dessus)|"
    r"niveau\s+(?:sup[ée]rieur|au-?dessus)|rez-?de-?chauss[eé]e|"
    r"vers\s+le\s+haut\b|plus\s+haut\b|\bsurface\b)",
    re.IGNORECASE,
)

# Intention « repos » : le joueur demande une récupération (« je me
# repose », « on dort »…). Si le modèle narre sans appeler `repos_long`,
# les PV ne sont JAMAIS restaurés (2× ignoré en partie abf74a77).
_INTENT_REPOS_RE = re.compile(
    r"\b(repos\w*|dormir|dormons|dormez|sommeil)\b"
    r"|\bme\s+reposer\b",
    re.IGNORECASE,
)

# Intention MÉCANIQUE du joueur — relance « outil requis mais non appelé »
# en mode natif (recommandation « température tool-calling » + retry
# forcé) : quand l'action demandée exige un outil (attaque, jet, soin,
# sort, fin de tour, création de fiche) et que le modèle répond en pur
# récit SANS rien appeler, on relance une fois avec tool_choice="required".
# Bords de mots STRICTS pour éviter les faux positifs ( « tirade » ne
# matche pas `tir\w*`, « couple » pas `coup\w*`, « sortir » pas `sort\w*`).
_INTENT_MECANIQUE_RE = re.compile(
    r"(?<!m['’])\battaqu\w*\b"                           # j'attaque / je l'attaquais
    r"|\bfrapp\w*\b"                                     # frapper / frappe
    r"|\btir(?:e|es|ez|er|[ée][es]?)\b"                  # tirer / je tire
    r"|\bcoups?\b"                                       # un coup / des coups
    r"|\btouch(?:e|es|ez|ons|er|ant|[ée][es]?)\b"        # toucher / je touche
    r"|\bripost\w*\b"                                    # riposter
    r"|\blanc(?:e|es|ez|ons)\b[^^\n]{0,40}?\bsorts?\b"   # lance un sort
    r"|\bincant\w*\b"                                    # incanter
    r"|\bpr[ée]par(?:e|es|ez|ons|er)\b[^^\n]{0,40}?\bsorts?\b"
    r"|\bsoign\w*\b|\bsoin(?:s)?\b|\bpotion\w*\b|\bgu[ée]ri\w*\b"
    r"|\bsauvegarde\w*\b"                                # jet de sauvegarde
    r"|\bjet\s+de\b"                                     # jet de (caractéristique)
    r"|\bcr[ée](?:e|es|er|ons|z)\b[^^\n]{0,40}?\b(?:personnage|fiche)\b"
    r"|\bcréation\w*\b[^^\n]{0,40}?\b(?:personnage|fiche)\b"
    r"|\b(?:jette|jettes|jetons|jetez|jeter|fasse|fais)\b[^^\n]{0,30}?"
    r"\b\d{0,3}d(?:4|6|8|10|12|20|100)\b"
    r"|\b(?:termine|terminez|passe|passes|fin\w*)\b[^^\n]{0,20}?\btour\b"
    r"|\bterminer\s+mon\s+tour\b",
    re.IGNORECASE,
)

# 💰 Budget d'appels par OUTIL et par TOUR de joueur : au-delà, `_run_one_tool`
# refuse l'exécution (le modèle bouclait 17-32× sur `fiche_perso_mettre_a_jour`
# ou `inventaire_consulter`, brûlant des minutes et saturant num_ctx).
# Calibré large pour ne gêner aucun tour légitime (multi-attaques, jets groupés).
_BUDGET_OUTILS_TOUR: dict[str, int] = {
    "fiche_perso_creer_rapide": 1,   # un seul personnage par tour
    "fiche_perso_creer": 1,
    "fiche_perso_mettre_a_jour": 2,   # ⛔ 4→2 (audit eb46aeef : le MJ écrasait les fiches — pv_max bloqué, on borne aussi)
    "fiche_perso_recuperer": 3,
    "inventaire_consulter": 2,
    # Partie ae358455 : 3→4 — la remise d'ouverture porte DEUX objets de
    # quête (fiole de vérité + parchemin de la route) ; à 3, le spam du
    # modèle épuisait le quota et le 4ᵉ appel (le parchemin) était REFUSÉ :
    # l'objet narré « ajouté à l'inventaire » n'y était jamais arrivé.
    "inventaire_ajouter": 4,
    "carte_donjon_get": 1,
    "carte_joueurs_get": 2,
    "monstre_consulter": 3,
    "lancer_attaque": 6,
    "lancer_degats": 6,
    "lancer_sauvegarde": 6,
    "lancer_d20": 8,
    # 🧪 Anti-spam sorts (partie 2ca691ec) : 1 action = 1 incantation ; le
    # 9B bouclait 4-6× sur `incanter_sort` (emplacements épuisés) et
    # 5× sur `preparer_sorts` en plein combat — chaque boucle coûtait
    # 2-4 min de génération.
    "incanter_sort": 2,            # ⛔ 3→2 (audit eb46aeef : 6 incantations dans un seul tour)
    "preparer_sorts": 2,
    "appeler_familier": 1,           # un appel de compagnon par tour max
    "renvoyer_familier": 1,
    "fiche_perso_soigner": 3,       # ⛔ 4→3 (audit eb46aeef : le MJ s'auto-soignait +11/source absente — voir F1)
    "etat_partie_get": 3,
    "terminer_mon_tour": 1,          # un SEUL par tour (anti-spam rounds)
    # ⛔ Anti-spam scénario (audit eb46aeef) : le MJ appelait scenario_etape
    # 3-6× par tour et etat_partie_patch 3-4× (contournait d'autres tools) —
    # 1 étape de scénario et 2 patches max par tour :
    "scenario_etape": 1,
    "etat_partie_patch": 2,
}
_BUDGET_DEFAUT = 6

# Taille minimale d'un texte d'accompagnement (itération avec tools) pour
# être considéré comme une vraie narration à préserver : en dessous, c'est
# de la prose de transition autour des appels (« Je consulte l'état… »)
# qu'on ne montre pas au joueur.
_NARR_INTERMEDIAIRE_MIN = 200

# Outils qui exigent que la fiche du personnage EXISTE déjà — quand le LLM
# boucle dessus pour un perso jamais créé, on débloque par création auto.
_TOOLS_FICHE_EXIGEANTE = ("fiche_perso_mettre_a_jour", "fiche_perso_recuperer")
_RATTRAPAGE_FICHE_ABSENTE_MIN = 2  # échecs « fiche absente » pour le MÊME nom


def _nom_fiche_absente(texte: str) -> Optional[str]:
    """Extrait le nom de la fiche visée par un message « Aucune fiche ... »."""
    m = re.search(r"Aucune fiche trouvée pour '([^']+)'", texte)
    return m.group(1) if m else None


@dataclass
class OrchestratedResult:
    narration: str = ""
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    state_patches: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    corrections: int = 0
    # Corrections d'ÉCHO (scène déjà narrée, D1ter) : budget DÉDIÉ, délibérément
    # indépendant de `corrections`. Partie 5b4e2bbe : la simulation (D1bis)
    # consomme `corrections` (3 max) puis se désactive ; l'anti-répétition
    # partageait le même budget → l'écho [46]==[44] n'était jamais purgé et
    # rebouclait à l'identique. L'écho a son propre plafond.
    corrections_echo: int = 0
    simulation_attempted: bool = False
    # Trace lisible des appels d'outils effectifs — diagnostic & logs.
    # Liste de dicts {name, args, ok, text} alimentée par _run_one_tool.
    tool_calls_trace: list[dict[str, Any]] = field(default_factory=list)
    # Compteur de refus par budget (% tour) : au-delà d'un seuil on coupe
    # la boucle d'outils (Q4 re-tente sans cesse un outil refusé, brûlant
    # des minutes : 23 inventaire_consulter dont 21 refusés en un tour).
    refus_budget: int = 0
    # True dès que les outils sont retirés du tour (narration forcée).
    narration_forcee: bool = False
    # Narrations produites par le modèle EN MÊME TEMPS que des appels
    # d'outils (phases B/C de la boucle). Ces textes entraient dans `work`
    # (contexte du LLM) mais n'étaient jamais montrés au joueur — seule la
    # narration FINALE était diffusée. Symptôme réel (partie 43234a00) :
    # le modèle narre la scène d'ouverture PUIS appelle memoire_lieu /
    # memoire_personnage ; le joueur ne voit que la queue (« Thalric
    # attend votre réponse… ») sans l'intro de scène. Elles sont désormais
    # conservées, diffusées en direct, et préfixées à la narration finale
    # (dm + historique — le dm final remplace l'aperçu streamé côté
    # client, donc aucun doublon à l'écran).
    narrations_intermediaires: list[str] = field(default_factory=list)
    # Lignes mécaniques des dégâts appliqués DIRECTEMENT par le serveur au
    # moment du lancer_degats (auto-application, cf. _run_one_tool) : le
    # modèle voit la narration du joueur mais n'inclut pas ces résultats
    # dans son contexte — on les ajoute à la dm finale pour que la table
    # voie les PV officiels (partie dfccc120 : 6 dégâts jetés, jamais
    # appliqués, PV monstre restés à 19/19).
    notes_mecaniques: list[str] = field(default_factory=list)


class Orchestrator:
    """Pilote un tour complet (request joueur → réponse MJ narrée)."""

    def __init__(
        self,
        client: OllamaClient,
        tools: dict[str, ToolSpec],
        tool_mode: str = "prompt",   # "native" | "prompt" | "auto"
        detect_simulation: bool = True,
        max_iterations: int = 10,
        decision_phase: bool = True,
        # Plafond d'outils exposés au LLM dans la boucle narrative (0 =
        # aucun plafond). Appliqué aux schémas natifs + documentation
        # compacte, PAS à la phase de décision (enum complète).
        max_tools_exposed: int = 0,
        # Température dédiée au tool-calling (itérations mécaniques).
        tool_temperature: float = 0.2,
    ):
        self.client = client
        self.tools = tools
        self.tool_mode = tool_mode
        self.detect_simulation = detect_simulation
        self.max_iterations = max_iterations
        self.decision_phase = decision_phase
        self.max_tools_exposed = max_tools_exposed
        self.tool_temperature = tool_temperature

    # ------------------------------------------------------------------ #
    def _filter_tools_by_phase(
        self, all_tools: dict[str, ToolSpec], ctx: ToolContext
    ) -> dict[str, ToolSpec]:
        """Renvoie un sous-ensemble de tools limité à la phase courante.

        Un modèle 12B comme Gemma ne gère qu'~10 tools fiables ; à 39 il se
        perd, hallucine des noms d'outils et répond en prose sans appeler.
        On lit le `state.phase` et la présence de PJ sur disque, puis on
        restreint l'ensemble exposé au LLM. Le filtrage reste transparent :
        tous les tools restent exécutables ; seul le set présenté change.
        """
        try:
            state = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=50,
            )
            etat = state.load()
            phase = (etat.get("phase") or "opening").strip().lower() if etat else "opening"
            pj = etat.get("pj") if etat else []
        except Exception:
            phase, pj = "opening", []
        # Cas particulier : pas encore de PJ → on force "opening".
        if not pj:
            phase = "opening"
        elif phase not in _PHASE_TOOLS:
            phase = "exploration"
        allowed = set(_PHASE_TOOLS[phase])
        # Garde-mémoire (partie ee5684fe) : un scénario CHOISI mais dont la
        # bible est inutilisable (résumé vide/absent — PDF illisible) ne peut
        # pas ancrer le monde : on retire les outils d'ÉCRITURE de mémoire
        # (lieux, PNJ, intrigue, événements, mission, position) pour que le
        # MJ ne persiste pas des faits INVENTÉS (Phandalin, un « mage »
        # compagnon…) à la place du décor réel. La lecture reste automatique
        # via le récap ; et une aventure « libre » (source=libre, sans
        # scénario) garde sa mémoire : c'est le seul fil de la campagne.
        if phase in ("exploration", "voyage", "roleplay"):
            quete = etat.get("quete") or {}
            source = str(quete.get("source") or "").strip()
            resume = str(((quete.get("bible") or {}).get("resume") or "")).strip()
            scena_sans_texte = bool(
                str(quete.get("titre") or "").strip()
                and source and source != "libre"
                and len(resume) < 120
            )
            if scena_sans_texte:
                allowed -= _MEMOIRE_ECRITURE_TOOLS
        filtered = {n: s for n, s in all_tools.items() if n in allowed}
        # Garde-fou : si on n'obtient rien (ex. config cassée), on retombe sur
        # l'ensemble complet pour ne jamais brider la discussion.
        return filtered or all_tools

    # ------------------------------------------------------------------ #
    #  Recommandation outil : plafond d'outils exposés à la narration
    # ------------------------------------------------------------------ #
    def _sous_ensemble_prioritaire(
        self,
        filtered: dict[str, ToolSpec],
        result: OrchestratedResult,
    ) -> dict[str, ToolSpec]:
        """Réduit l'ensemble exposé au plafond `max_tools_exposed` sans
        jamais perdre les outils MÉCANIQUES du tour.

        - `max_tools_exposed <= 0` : aucun plafond → `filtered` renvoyé TEL
          QUEL, sans réordonner (les tests d'exposition s'appuient sur
          l'ordre du registre).
        - Sinon, priorité stable :
            1. outils déjà exécutés ce tour (result.tool_calls_trace) ;
            2. outils mécaniques/combat (_COMBAT_PRIORITAIRES) puis outils
               du choix contraint (_OUTILS_DECISION) — ils doivent rester
               appelables par la narration ;
            3. le reste, dans l'ordre d'origine de `filtered`.
        Le filtrage est transparent : tout outil reste exécutable par les
        parsers de secours ; seul le set présenté change.
        """
        if self.max_tools_exposed <= 0:
            return filtered
        # 🛡️ B22 (audit parties complètes) : un plafond plus petit que le jeu
        # d'outils de la phase masquait DÉFINITIVEMENT les outils non
        # prioritaires — en exploration, les 15 places étaient occupées par
        # _OUTILS_DECISION (28) et en combat par _COMBAT_PRIORITAIRES (14), si
        # bien que inventaire_ramasser, memoire_*, scenario_etape, repos_long
        # et carte_donjon_explorer n'étaient JAMAIS présentés. Un outil masqué
        # n'est pas « moins prioritaire », il est impossible. On ne tronque
        # donc que si la phase expose vraiment trop d'outils ; sinon tout est
        # présenté, simplement RÉORDONNÉ (les outils mécaniques en tête).
        plafond = max(self.max_tools_exposed, _PLAFOND_EXPOSITION_MIN)
        if len(filtered) <= plafond:
            ordre_total: list[str] = []
            deja: list[str] = []
            for tc in result.tool_calls_trace:
                nom = tc.get("name")
                if nom and nom in filtered and nom not in deja:
                    deja.append(nom)
            for n in (deja + sorted(_COMBAT_PRIORITAIRES)
                      + sorted(_OUTILS_DECISION)):
                if n in filtered and n not in ordre_total:
                    ordre_total.append(n)
            for n in filtered:
                if n not in ordre_total:
                    ordre_total.append(n)
            return {n: filtered[n] for n in ordre_total}
        deja_vus: list[str] = []
        for tc in result.tool_calls_trace:
            nom = tc.get("name")
            if nom and nom not in deja_vus:
                deja_vus.append(nom)
        prioritaires: list[str] = []
        for n in (
            deja_vus
            + sorted(_COMBAT_PRIORITAIRES)
            + sorted(_OUTILS_DECISION)
        ):
            if n in filtered and n not in prioritaires:
                prioritaires.append(n)
        exposes: list[str] = []
        for n in prioritaires + list(filtered):
            if len(exposes) >= plafond:
                break
            if n in filtered and n not in exposes:
                exposes.append(n)
        return {n: filtered[n] for n in exposes}

    # ------------------------------------------------------------------ #
    #  🎯 Phase de décision contrainte (json_schema → grammaire llama.cpp)
    # ------------------------------------------------------------------ #
    def _schema_decision(self, noms: list[str]) -> dict[str, Any]:
        """Schéma JSON de décision : `action` + outils optionnels.

        Les noms d'outils sont un `enum` : llama.cpp compile le schéma en
        grammaire et masque les logits — un nom inventé ou une réponse en
        prose devient impossible au niveau des tokens."""
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["outils", "narrer"]},
                "outils": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nom": {"type": "string", "enum": sorted(noms)},
                            "arguments": {"type": "object"},
                        },
                        "required": ["nom"],
                    },
                },
            },
            "required": ["action"],
        }

    def _contexte_decision(
        self, ctx: ToolContext
    ) -> tuple[dict[str, Any], str]:
        """Bloc d'état MINIMAL pour la décision (~15 lignes) : phase, salle
        courante et portes, PV des PJ, dernier événement. Volontairement
        minuscule — la fiabilité de la décision dépend de la petitesse du
        contexte (le gros prompt narratif est ce qui diluait les consignes)."""
        etat: dict[str, Any] = {}
        try:
            etat = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=50,
            ).load()
        except Exception:                                        # noqa: BLE001
            pass
        lignes = [f"Phase : {etat.get('phase') or '?'}"]
        donjon = etat.get("donjon") or {}
        if donjon.get("id"):
            cr = list(donjon.get("courant") or [0, 0])
            cx, cy = (cr[0], cr[1]) if len(cr) >= 2 else (0, 0)
            salle = next(
                (s for s in (donjon.get("grille") or [])
                 if s.get("x") == cx and s.get("y") == cy), {},
            )
            portes = [d for d, v in (salle.get("portes") or {}).items() if v]
            lignes.append(
                f"Donjon « {donjon.get('id')} » — salle ({cx},{cy}) "
                f"type « {salle.get('type', '?')} » — portes ouvertes : "
                + (", ".join(portes) or "AUCUNE (cul-de-sac)")
            )
        pjs = etat.get("pj") or []
        if pjs:
            lignes.append("PJ : " + "; ".join(
                f"{p.get('nom')} ({p.get('classe')} {p.get('niveau')}, "
                f"PV {p.get('pv')}/{p.get('pv_max')}"
                + (f", conditions : {', '.join(p.get('conditions'))}"
                   if p.get("conditions") else "")
                + ")"
                for p in pjs
            ))
        der = str(etat.get("derniere_narration") or "").strip()
        if der:
            lignes.append("Dernier événement : " + der[:300])
        return etat, "\n".join(lignes)

    def _prompt_decision(
        self, noms: list[str], filtered: dict[str, ToolSpec],
        contexte: str,
    ) -> str:
        """System prompt COURT de la phase de décision."""
        specs = []
        for n in noms:
            spec = filtered.get(n)
            if spec is None:
                continue
            args = []
            for pname, p in spec.expected_args.items():
                req = "" if p.default is inspect.Parameter.empty else "?"
                args.append(f"{pname}{req}")
            desc = " ".join(
                str(spec.docstring or "").split()
            )[:110]
            specs.append(f"- {n}({', '.join(args)}) — {desc}")
        return (
            "Tu es le module de DÉCISION MÉCANIQUE d'un MJ D&D 3.5. "
            "Le joueur vient d'agir : détermine quels outils doivent être "
            "exécutés pour résoudre SON action. La narration sera écrite "
            "plus tard par un autre module — ne raconte RIEN ici.\n\n"
            "Règles :\n"
            "- action \"outils\" SEULEMENT si l'action du joueur (ou sa "
            "conséquence immédiate) nécessite de la mécanique : "
            "déplacement, entrée/sortie de donjon, voyage, engagement de "
            "combat, repos de nuit, soin, dégâts subis/infligés, objet "
            "gagné/perdu, sort, jet de dés.\n"
            "- Plusieurs outils nécessaires ? Liste-les dans l'ordre "
            f"(max {_MAX_OUTILS_DECISION}).\n"
            "- Action purement narrative, dialogue ou description → "
            "\"narrer\" (outils absent ou vide).\n"
            "- En cas de doute entre deux outils, choisis le plus "
            "spécifique (soin ponctuel = fiche_perso_soigner, nuit "
            "complète = repos_long).\n\n"
            "=== ÉTAT DU JEU ===\n" + contexte + "\n\n"
            "=== OUTILS DISPONIBLES ===\n" + "\n".join(specs)
        )

    async def _phase_decision(
        self,
        work: list[Message],
        ctx: ToolContext,
        filtered: dict[str, ToolSpec],
    ) -> list[dict[str, Any]]:
        """Décide les outils du tour par un appel contraint json_schema.

        Renvoie une liste de calls `[{"name":…, "arguments":{…}}]` prêts
        pour `_exec_tool_calls_prompt`, ou [] (pas de mécanique à résoudre,
        phase non concernée, backend sans support, ou réponse inexploitable
        — dans tous ces cas la boucle narrative normale prend le relais
        SANS régression)."""
        if not self.decision_phase:
            return []
        # Dernier message joueur = l'action à résoudre.
        dernier_user = ""
        for m in reversed(work):
            if m.role == "user":
                dernier_user = m.content or ""
                break
        if not dernier_user.strip():
            return []
        # Consigne corrective (rejeu serveur) : ce n'est PAS une action du
        # joueur — la décision doit laisser la main à la boucle de rejeu.
        if "instruction INTERNE du moteur de jeu" in dernier_user:
            return []
        etat, contexte = self._contexte_decision(ctx)
        phase = str(etat.get("phase") or "").strip().lower()
        if phase not in _PHASES_DECISION:
            return []
        noms = sorted(set(filtered) & _OUTILS_DECISION)
        if not noms:
            return []
        # 🚶 D0 (partie ae358455) : re-consultation UNIQUE si l'action du
        # joueur réclame un déplacement (avancer/entrer/franchir/continuer
        # vers un autre lieu) alors que le groupe est dans un donjon ACTIF —
        # la décision « narrer » se fiait à une narration désynchronisée
        # (« déjà dans la grotte ») pendant que la carte restait figée en
        # (0,0). Avec la consigne explicite, la décision contrainte
        # (température 0.0, enum) choisit `carte_donjon_explorer`.
        _consigne_depl = ""
        if _action_deplacement_dans_donjon(dernier_user, etat):
            _consigne_depl = (
                "⚠️ L'action du joueur réclame un DÉPLACEMENT (avancer, "
                "entrer, franchir, continuer vers un autre lieu) — action "
                "\"outils\" OBLIGATOIRE avec `carte_donjon_explorer("
                "direction=…)` et une porte OUVERTE de la salle courante "
                "(liste ci-dessus). La carte DOIT avancer : ne réponds PAS "
                "\"narrer\"."
            )
        messages_dec = []
        decision: Optional[dict[str, Any]] = None
        for _tentative in range(2):
            messages_dec = [
                Message(role="system", content=self._prompt_decision(
                    noms, filtered, contexte)),
                Message(role="user", content=(
                    f"[Message du joueur] {dernier_user}\n\n"
                    "Résous la décision mécanique (objet JSON attendu)."
                )),
            ]
            if _consigne_depl:
                messages_dec.append(
                    Message(role="system", content=_consigne_depl))
            try:
                res = await self.client.chat(
                    messages_dec,
                    temperature=0.0,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "decision_outil",
                            "schema": self._schema_decision(noms),
                        },
                    },
                )
            except Exception as e:                               # noqa: BLE001
                # Backend sans support json_schema ou panne : repli
                # transparent.
                _log.warning(
                    "décision contrainte indisponible (repli boucle "
                    "normale) : %s", e,
                )
                return []
            # Parse robuste : le contenu contraint DEVRAIT être l'objet JSON
            # exact ; on tolère un emballage résiduel (fences, prose courte).
            brut = (res.content or "").strip()
            if brut.startswith("```"):
                brut = re.sub(
                    r"^```[a-zA-Z0-9]*\s*|\s*```$", "", brut).strip()
            try:
                decision = json.loads(brut)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", brut, re.DOTALL)
                if not m:
                    _log.warning("décision non parsable : %.200s", brut)
                    return []
                try:
                    decision = json.loads(m.group(0))
                except json.JSONDecodeError:
                    _log.warning(
                        "décision non parsable (2e essai) : %.200s", brut)
                    return []
            if not isinstance(decision, dict):
                return []
            if str(decision.get("action") or "") == "outils":
                break
            if not _consigne_depl:
                break       # pas de motif de re-consultation : une passe
            _log.warning(
                "décision « narrer » pour un déplacement demandé — "
                "re-consultation avec consigne"
            )
        calls: list[dict[str, Any]] = []
        for o in (decision.get("outils") or [])[:_MAX_OUTILS_DECISION]:
            if not isinstance(o, dict):
                continue
            nom = str(o.get("nom") or "").strip()
            # L'enum garantit déjà la validité, mais un backend défaillant
            # pourrait laisser passer : re-vérification stricte.
            if nom in noms:
                args = o.get("arguments")
                calls.append({
                    "name": nom,
                    "arguments": args if isinstance(args, dict) else {},
                })
        if calls:
            _log.info(
                "phase de décision : %s", 
                ", ".join(f"{c['name']}({c['arguments']})" for c in calls),
            )
        return calls

    # ------------------------------------------------------------------ #
    async def _preserve_narration(
        self,
        result: OrchestratedResult,
        texte: str,
        on_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> None:
        """Conserve (et diffuse) une narration produite AVEC des appels d'outils.

        Les itérations B/C de la boucle ajoutent la prose du modèle à `work`
        (contexte du LLM) mais le texte n'atteignait jamais le joueur : seule
        la narration FINALE était diffusée. Quand le modèle narre la scène
        d'ouverture PUIS appelle `memoire_lieu`/`memoire_personnage`, l'intro
        était perdue — le joueur ne voyait que la suite (« Thalric attend
        votre réponse… », partie 43234a00). On garde ici les textes
        suffisamment longs pour être de la vraie narration (pas une simple
        transition autour d'un appel), on les diffuse en direct dans l'aperçu
        streamé, et run() les préfixera à la narration finale : le dm final
        REMPLACE l'aperçu côté client, donc aucun doublon à l'écran.
        """
        propre = strip_narration_artifacts(texte or "", self.tools).strip()
        if len(propre) < _NARR_INTERMEDIAIRE_MIN:
            return
        # Dédoublonnage : le même passage peut revenir dans plusieurs
        # itérations (le modèle répète son intro en ré-enrobant un appel).
        cle = _normalise_pour_compare(propre)
        for deja in result.narrations_intermediaires:
            if _normalise_pour_compare(deja) == cle:
                return
        result.narrations_intermediaires.append(propre)
        if on_delta is not None:
            try:
                await on_delta(propre + "\n\n")
            except Exception:                                    # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    async def run(
        self,
        messages: list[Message],
        ctx: ToolContext,
        on_event: Optional[EventCallback] = None,
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
        trust_damage_prose: bool = False,
    ) -> OrchestratedResult:
        """Boucle principale : appelle le LLM, exécute les tools, narrate.

        - `messages`   : historique complet (system + tours précédents + nouveau
                         message joueur).
        - `ctx`         : contexte tool (partie_id, joueur, data_dir).
        - `on_event`    : callback async pour events structurés (image, status).
        - `on_delta`    : callback async pour streaming tokens de narration au
                         client (peut être None si on ne stream pas).
        - `on_status`   : callback async pour l'étape en cours (« Résout
                         l'action avec les outils… ») — affiché au joueur
                         pendant la réflexion.
        - `trust_damage_prose` : True quand des dégâts viennent d'être résolus
                         côté serveur (pre-run du moteur de combat) et que le
                         LLM les reformule légitimement — désactive la
                         détection « dégâts en prose » pour ce tour.
        """
        result = OrchestratedResult()
        work = list(messages)
        # 📊 Statut enrichi : exposé via ctx (par partie) pour que les tools
        # et la boucle puissent pousser l'étape en cours au client.
        ctx.on_status = on_status
        # Filtrage par phase : un modèle 12B gère mal 39 tools fiables. Avant la
        # création de perso, seuls 3 outils de la phase d'ouverture suffisent ;
        # en exploration, seuls les pertinents. En cas de doute, on donne les
        # tools utiles à la phase courante. Le filtrage se détermine par la
        # dernière partie état disponible sur disque via ctx.
        filtered = self._filter_tools_by_phase(self.tools, ctx)
        # Plafond d'outils exposés (recommandation « réduction du zoo
        # d'outils ») : la phase de décision garde l'énumération COMPLÈTE
        # (filtered) ; seuls les schémas natifs + la documentation compacte
        # de la boucle narrative passent par le sous-ensemble prioritaire.
        exposes = self._sous_ensemble_prioritaire(filtered, result)
        schemas = tools_schemas_all(exposes)
        tool_section = tools_prompt_section(exposes)

        # En mode "prompt", on documente les balises <tool ...> au system
        # message (Gemma/Qwen tool-calling natif fragile sans schémas).
        # En mode "auto", on injecte une version COMPACTE (noms + deux
        # formats acceptés) : le tool-calling natif de llama.cpp/Gemma
        # échoue souvent en prose — la balise textuelle donne au modèle un
        # canal d'appel déterministe que le backend parse de façon fiable.
        if work and work[0].role == "system":
            if self.tool_mode == "prompt" and tool_section:
                work[0] = Message(
                    role="system",
                    content=work[0].content + "\n\n" + tool_section,
                )
            elif self.tool_mode == "auto" and exposes:
                compact = tools_prompt_compact(exposes)
                work[0] = Message(
                    role="system",
                    content=work[0].content + "\n\n" + compact,
                )

        # OpenAI impose : si tools non vides, tool_choice = "auto" sauf si
        # l'on veut forcer un appel. On laisse "auto".
        corrections_vues = -1
        # 🎯 Relance « outil requis mais non appelé » : au plus UNE fois par
        # tour (montée par le hook A3ter avec tool_choice="required").
        retry_requis_envoye = False
        # Narrations INVALIDES ajoutées à `work` au fil des corrections
        # (simulation en prose…) : elles servent de contexte au modèle mais
        # ne doivent PAS compter comme références anti-répétition — sinon la
        # relance, qui légitimement RE-NARRE la même scène avec les vrais
        # chiffres des tools, se fait écho d'elle-même et déclenche une
        # spirale correction 2 → 3 → boucle épuisée (observé en combat,
        # partie fa4e7366).
        ids_corriges: list[int] = []
        # Phase combat : les rounds rejouent la MÊME action (« j'attaque à la
        # hache ») → des narrations voisines sont normales. On ne relance que
        # les vraies copies (préfixe verbatim) ou les recouvrements massifs
        # (partie 44b02cfc : 3 corrections + fallback PERDUS à chaque round
        # sur du texte pourtant adapté à l'action répétée du joueur — les
        # relances n'ont jamais produit de variation utile).
        phase_combat = False
        ouverture_tour = False
        a_deja_narre = any(
            m.role == "assistant" and (m.content or "").strip()
            for m in messages
        )
        try:
            _etat_tour = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,            ).load()
            phase_combat = (
                str(_etat_tour.get("phase") or "").strip().lower() == "combat"
            )
            # 🔧 Bêta : tour d'OUVERTURE (phase opening, aucun événement
            # d'histoire, AUCUNE narration antérieure) — les brouillons
            # d'ouverture re-décrivent naturellement la même scène de départ :
            # l'anti-répétition y épuisait la boucle (2-3 corrections + repli)
            # et livrait une intro bâclée (partie réelle e920255c : intro
            # précipitée). S'il existe DÉJÀ une narration (re-visite, combat…),
            # l'anti-répétition redevient normale.
            ouverture_tour = (
                str(_etat_tour.get("phase") or "").strip().lower()
                in ("opening", "opening_complete")
                and not (_etat_tour.get("histoire"))
                and not a_deja_narre
            )
        except Exception:                                    # noqa: BLE001
            phase_combat = False
            ouverture_tour = False
        seuil_repet = 0.75 if phase_combat else _REPET_SEUIL_CHEVAUCHEMENT
        # 🎲 À l'ouverture : AUCUNE correction echo (le premier brouillon est
        # livré tel quel — il pose le décor, c'est son rôle).
        cap_echo = 0 if ouverture_tour else 3
        cles_filtre_prec: Optional[set] = None

        # 🎯 PHASE DE DÉCISION CONTRAITE — avant toute narration, un appel
        # LLM court avec `response_format: json_schema` choisit les outils
        # mécaniques du tour (enum = sortie invalide impossible). Les outils
        # retenus sont exécutés serveur ICI ; la boucle narrative en dessous
        # raconte ensuite à partir des résultats officiels injectés dans
        # `work`. Repli total sur la boucle normale au moindre pépin
        # (backend sans support, réponse non parsable, phase hors périmètre).
        if not phase_combat:
            try:
                calls_decides = await self._phase_decision(work, ctx, filtered)
            except Exception as e:                               # noqa: BLE001
                _log.warning(
                    "phase de décision échouée (repli boucle normale) : %s", e,
                )
                calls_decides = []
            if calls_decides:
                work.append(Message(
                    role="system",
                    content=(
                        "ℹ️ SYSTÈME : la mécanique du tour a DÉJÀ été "
                        "résolue (décision contrainte) — les résultats "
                        "officiels des outils exécutés suivent. Narre le "
                        "tour en t'appuyant sur CES résultats ; ne rappelle "
                        "PAS ces outils pour la même action."
                    ),
                ))
                await self._exec_tool_calls_prompt(
                    calls_decides, ctx, work, result, on_event,
                )

        # 🚀 Itérations ≥ 2 : élage des règles narratives du system prompt.
        # Les itérations mécaniques (attaque → dégâts → fin de tour) n'ont plus
        # besoin des sections narratives longues : l'état (récap) et les
        # résultats des tools déjà exécutés (dans `work`) suffisent. Gain :
        # prefill réduit sur chaque itération de combat. Le récap d'état
        # (avant le marqueur) est conservé intégralement.
        _SECTIONS_MARKER = "=== RÈGLES DU JEU (sections dynamiques) ==="
        elage_fait = False

        for _ in range(self.max_iterations):
            result.iterations += 1
            if (False and not elage_fait and result.iterations >= 2 and work
                    and work[0].role == "system"
                    and _SECTIONS_MARKER in work[0].content):
                _sys_court, _sep, _reste = work[0].content.partition(
                    _SECTIONS_MARKER)
                work[0] = Message(
                    role="system",
                    content=(
                        _sys_court.rstrip()
                        + "\n\n⚠️ Itération de mécanique : applique les "
                        "règles essentielles (résous avec les outils, "
                        "narre bref d'après leurs résultats)."
                    ),
                )
                elage_fait = True
                _log.info(  # pragma: no cover — branche désactivée (B30)
                    "itération %d : sections narratives élaguées du system "
                    "prompt (-%d chars).",
                    result.iterations, len(_sep) + len(_reste),
                )
            use_native = self.tool_mode in ("native", "auto")
            # ⟳ Re-filtrage par phase À CHAQUE itération : un changement de
            # phase EN COURS de tour (ex. `engager_combat` appelé depuis
            # l'exploration, partie 54de40ed) doit rendre les outils de la
            # nouvelle phase disponibles pour les itérations suivantes. Sans
            # cela, les schémas restaient figés sur l'ensemble initial (sans
            # lancer_attaque/lancer_degats) et le modèle ne pouvait
            # physiquement pas résoudre ses attaques — il les simulait en
            # prose, 3 corrections puis abandon, dégâts jamais appliqués.
            filtered = self._filter_tools_by_phase(self.tools, ctx)
            # Plafond ré-appliqué à CHAQUE itération (rés. prioritaire):
            # les outils exécutés ce tour restent exposés (ceux qu'on vient
            # d'utiliser doivent rester rappelables pour la narration).
            exposes = self._sous_ensemble_prioritaire(filtered, result)
            if cles_filtre_prec is not None and set(filtered) != cles_filtre_prec:
                outils_noms = ", ".join(sorted(exposes))
                # 🛡️ M12 : la liste des outils était journalisée EN ENTIER à
                # chaque changement de phase (jusqu'à 47 noms, à chaque tour),
                # noyant les logs utiles. On ne trace que le nombre.
                _log.info(
                    "phase changée en cours de tour : outils réinjectés "
                    "(%d outils)", len(exposes),
                )
                section_maj = (
                    tools_prompt_section(exposes)
                    if self.tool_mode == "prompt"
                    else tools_prompt_compact(exposes)
                )
                work.append(Message(
                    role="system",
                    content=(
                        "ℹ️ SYSTÈME : la phase de jeu a changé EN COURS DE "
                        "TOUR — les outils disponibles changent aussi. Leur "
                        "documentation à jour suit. Résolvez TOUTE mécanique "
                        "annoncée (attaque, dégâts, sauvegarde) avec CES "
                        "outils : " + outils_noms + "\n\n" + section_maj
                    ),
                ))
            cles_filtre_prec = set(filtered)
            schemas = tools_schemas_all(exposes)
            tools_arg = schemas if use_native else None
            # ⚡ Blocage anti-boucle budget : le modèle (Q4 en particulier) a
            # re-tenté ≥3 fois des outils refusés par le quota. On retire les
            # outils pour ce tour et on force une narration unique — sinon il
            # brûle des minutes à ré-émettre le même appel (tour observé : 23
            # inventaire_consulter dont 21 refusés = 5+ appels LLM inutiles).
            consequences_budget = (
                result.refus_budget >= 3 or result.narration_forcee
            )
            if consequences_budget:
                use_native = False
                tools_arg = None
                if not result.narration_forcee:
                    result.narration_forcee = True
                    # Plus de correction de simulation : tout doit finir en
                    # narration dans cet appel unique.
                    result.corrections = max(result.corrections, 2)
                    work.append(Message(
                        role="system",
                        content=(
                            "⚠️ CORRECTION BUDGET : 3 appels d'outils ont été "
                            "REFUSÉS ce tour par la limite anti-boucle du "
                            "serveur. Arrête IMMÉDIATEMENT de rappeler des "
                            "outils — ils sont désactivés pour ce tour. "
                            "Rédige MAINTENANT la narration finale de "
                            "l'action, en exploitant les résultats d'outils "
                            "déjà obtenus ci-dessus (sans inventer de "
                            "nouveaux jets)." + _CORRECTIF_INTERNE
                        ),
                    ))
            # Après une correction injectée, la relance part à basse
            # température : à 0.75 le sampling réinvente la même boucle
            # (répétitions observées en e2e avec Qwen standard).
            temp_relance = 0.35 if result.corrections > corrections_vues else None
            corrections_vues = result.corrections
            # 🎯 Température dédiée au tool-calling (recommandation) : les
            # itérations MÉCANIQUES (combat, ou outils déjà appelés ce tour)
            # partent À BASSE température — à 0.75 le sampling Qwen 9B narre
            # au lieu de formater l'appel. Les tours de pure narration
            # gardent la température narrative (temp_relance si correction,
            # sinon None → température du client).
            mecanique = phase_combat or bool(result.tool_calls_trace)
            if temp_relance is not None:
                temp_effet = temp_relance
            elif mecanique:
                temp_effet = self.tool_temperature
            else:
                temp_effet = None
            # 🧱 Borne de contexte : les résultats d'outils (≤ 4 000 chars
            # chacun) et les correctifs s'accumulent dans `work` au fil des
            # itérations — en combat, le prompt a atteint 18 709 tokens pour
            # un ctx de 20 000 (n_predict 2 048) et llama.cpp renvoyait 400
            # « exceeds the available context size » → « problème technique »
            # (partie a6d11005). On borne le total AVANT chaque appel, en
            # RÉSERVANT la place des schémas d'outils natifs (envoyés hors
            # de `work`) pour ne jamais dépasser le ctx du serveur.
            reserve = 0
            if use_native and tools_arg:
                try:
                    reserve = len(json.dumps(tools_arg, ensure_ascii=False))
                except (TypeError, ValueError):
                    reserve = 0
            work = _borner_work(work, reserve_chars=reserve)
            # tool_choice effacé après la relance A3ter : un seul appel forcé.
            tool_choice_effectif = (
                "required"
                if (use_native and retry_requis_envoye)
                else ("auto" if use_native else None)
            )
            # 📊 Statut enrichi : l'étape en cours, affichée au joueur.
            if on_status is not None:
                try:
                    if result.iterations == 1:
                        await on_status("Résout l'action avec les outils…")
                    elif result.corrections > 0:
                        # 🛡️ m19 : le compteur « (2/4) » laissait croire à un
                        # pourcentage de progression. On nomme ce qui se passe
                        # réellement (une passe de correction du modèle).
                        await on_status(
                            f"Corrige la résolution "
                            f"({result.corrections} correction(s))…"
                        )
                    else:
                        await on_status(
                            f"Résout l'action ({result.iterations}/"
                            f"{self.max_iterations})…"
                        )
                except Exception:                            # noqa: BLE001
                    pass
            try:
                chat = await self.client.chat(
                    work, tools=tools_arg,
                    tool_choice=tool_choice_effectif,
                    temperature=temp_effet,
                )
            except Exception as exc:                        # noqa: BLE001
                if tool_choice_effectif != "required":
                    raise
                # Certains backends rejettent `tool_choice="required"` :
                # repli sur "auto" (la consigne correctif reste dans `work`).
                _log.warning(
                    "tool_choice='required' rejeté (%s) → repli auto", exc,
                )
                chat = await self.client.chat(
                    work, tools=tools_arg,
                    tool_choice="auto" if use_native else None,
                    temperature=temp_effet,
                )

            # --- Bter2. Troncature de dégénérescence intra-réponse -------
            # Le modèle boucle sur ses propres phrases : on coupe AVANT tout
            # traitement (parsing, narration, réinjection au contexte).
            if chat.content:
                coupe, _motif = tronquer_degeneration(chat.content)
                if _motif:
                    chat = ChatResult(
                        content=coupe,
                        tool_calls=chat.tool_calls,
                        finish_reason=chat.finish_reason,
                        raw=chat.raw,
                        raw_content=chat.raw_content,
                    )

            # --- Bbis0. Appels d'outils cachés DANS le bloc thinking ------
            # Qwen3.5 émet parfois l'appel `<tool_call><function=…>
            # <parameter=…>` À L'INTÉRIEUR du bloc <think> — llama.cpp ne le
            # parse pas (issue #20837) et le strip-thinking du client le
            # DÉTRUIT (le contenu nettoyé ne contient plus rien). On récupère
            # l'appel depuis le contenu BRUT (raw_content), segments thinking
            # uniquement, sans doublon avec les appels natifs déjà présents.
            _brut = getattr(chat, "raw_content", "") or ""
            if _brut and "<think" in _brut and not result.narration_forcee:
                _caches: list[dict[str, Any]] = []
                for _seg in _segments_thinking(_brut):
                    _c1, _ = extract_function_blocks(_seg)
                    _caches.extend(_c1)
                    _c2, _ = extract_toolcall_blocks(_seg)
                    _caches.extend(_c2)
                    _caches.extend(parse_prompt_tool_calls(_seg))
                if _caches:
                    def _cle_appel(c: dict[str, Any]) -> tuple[str, str]:
                        f = c.get("function", c)
                        return (
                            _norm_tool_name(str(f.get("name", ""))),
                            json.dumps(f.get("arguments", "{}"),
                                       sort_keys=True, default=str),
                        )
                    _existantes = {_cle_appel(tc)
                                   for tc in (chat.tool_calls or [])}
                    _nouveaux = [c for c in _caches
                                 if _cle_appel(c) not in _existantes]
                    if _nouveaux:
                        _log.info(
                            "%d appel(s) d'outil récupéré(s) DANS le bloc "
                            "thinking : %s", len(_nouveaux),
                            ", ".join(str(c.get("name")) for c in _nouveaux),
                        )
                        chat = ChatResult(
                            content=chat.content,
                            tool_calls=(chat.tool_calls or []) + _nouveaux,
                            finish_reason=chat.finish_reason,
                            raw=chat.raw,
                            raw_content=_brut,
                        )

            # --- Bbis. Blocs <tool_call> textuels (llama.cpp sans jinja) ----
            # Le backend laisse parfois l'appel dans `content` au lieu de
            # `tool_calls` : on les normalise vers le pipeline natif.
            block_calls, content_clean = extract_toolcall_blocks(chat.content or "")
            if block_calls and not result.narration_forcee:
                _log.info(
                    "%d bloc(s) <tool_call> textuel(s) récupéré(s) dans content",
                    len(block_calls),
                )
                chat = ChatResult(
                    content=content_clean,
                    tool_calls=(chat.tool_calls or []) + block_calls,
                    finish_reason=chat.finish_reason,
                    raw=chat.raw,
                    raw_content=chat.raw_content,
                )

            # --- Bter. Blocs <function=..><parameter=..> (ChatML/Qwen) ------
            # Variante `<tool_call><function=nom><parameter=clé>…` — avec
            # sometimes des balises d'args nues (`<item>…</item>`). Normalisé
            # vers le pipeline natif, sinon il fuit dans la narration.
            func_calls, func_clean = extract_function_blocks(chat.content or "")
            if func_calls and not result.narration_forcee:
                _log.info(
                    "%d bloc(s) <function=..> récupéré(s) dans content : %s",
                    len(func_calls),
                    ", ".join(c["name"] for c in func_calls),
                )
                if use_native:
                    chat = ChatResult(
                        content=func_clean,
                        tool_calls=(chat.tool_calls or []) + func_calls,
                        finish_reason=chat.finish_reason,
                        raw=chat.raw,
                    )
                else:
                    # Mode prompt : pas de pipeline natif → exécution directe
                    # (comme les balises <tool> en section C), puis boucle
                    # pour narrer le résultat officiel.
                    await self._preserve_narration(
                        result, func_clean.strip(), on_delta)
                    work.append(Message(
                        role="assistant", content=func_clean.strip()))
                    work.append(Message(
                        role="system",
                        content=(
                            "ℹ️ SYSTÈME : ton bloc `<function=…>` a été "
                            "INTERCEPTÉ et exécuté réellement. Les résultats "
                            "officiels suivent dans les messages tool — "
                            "n'écris JAMAIS la syntaxe d'appel dans la "
                            "narration."
                        ),
                    ))
                    await self._exec_tool_calls_prompt(
                        func_calls, ctx, work, result, on_event)
                    continue

            # --- B. Mode natif : tool_calls présents -----------------------
            if chat.tool_calls and use_native and not result.narration_forcee:
                # Contenu nettoyé de toute syntaxe d'appel résiduelle pour ne
                # pas encourager le modèle à répéter le format en prose.
                clean_native = strip_narration_artifacts(chat.content or "", self.tools)
                # La prose narrée à côté des appels (intro de scène…) est
                # conservée et diffusée — sinon elle n'atteint jamais le
                # joueur (seule la narration finale est montrée).
                await self._preserve_narration(result, clean_native, on_delta)
                work.append(Message(
                    role="assistant",
                    content=clean_native,
                    tool_calls=chat.tool_calls,
                ))
                await self._exec_tool_calls(chat.tool_calls, ctx, work, result, on_event)
                continue

            # --- Bter. Balises <tool_call name=".." key="value" .. /> -----
            # Variante attributs XML auto-fermée (Qwen/Hermes sur llama.cpp) :
            # ni tool_calls natif ni balise `<tool ...>` du mode prompt —
            # on l'exécute réellement quel que soit le mode, puis on boucle
            # pour que le modèle narrate le VRAI résultat du tool.
            attr_calls, attr_clean = extract_toolcall_attr_calls(chat.content or "")
            if attr_calls and not result.narration_forcee:
                _log.info(
                    "%d balise(s) <tool_call .../> (attributs XML) récupérée(s) : %s",
                    len(attr_calls),
                    ", ".join(c["name"] for c in attr_calls),
                )
                await self._preserve_narration(result, attr_clean, on_delta)
                work.append(Message(role="assistant", content=attr_clean.strip()))
                work.append(Message(
                    role="system",
                    content=(
                        "ℹ️ SYSTÈME : ta balise "
                        "`<tool_call name=\"...\" key=\"value\" />` a été "
                        "INTERCEPTÉE et exécutée réellement. Le résultat "
                        "officiel suit dans le message tool — c'est la seule "
                        "valeur valide. À l'avenir, utilise la balise "
                        "`<tool name=\"...\" key=\"value\">` seule sur sa "
                        "ligne, et n'écris jamais la syntaxe d'appel dans la "
                        "narration."
                    ),
                ))
                await self._exec_tool_calls_prompt(attr_calls, ctx, work, result, on_event)
                continue

            # --- C. Mode prompt : extraire balises <tool> -------------------
            prompt_calls = parse_prompt_tool_calls(chat.content)
            if prompt_calls and not result.narration_forcee:
                # On enregistre la réponse nettoyée des balises comme
                # assistant — le modèle voit sa propre prose SANS la balise,
                # ce qui évite de le pousser à réécrire du pseudo-code.
                clean = strip_prompt_tool_calls(chat.content).strip()
                # Prose narrée à côté des balises <tool> : conservée et
                # diffusée (cf. _preserve_narration). Le dm final inclura ce
                # texte, donc l'aperçu streamé n'est plus « écrasé » — le
                # vieux symptôme « des blocs de conversation disparaissent »
                # venait de ce que la narration finale ne contenait PAS ces
                # blocs ; c'est corrigé par la préfixation dans run().
                await self._preserve_narration(result, clean, on_delta)
                work.append(Message(
                    role="assistant",
                    content=clean,
                ))
                await self._exec_tool_calls_prompt(prompt_calls, ctx, work, result, on_event)
                continue

            # --- C2. Rattrapage : appels écrits en syntaxe fonctionnelle ----
            # `tool_name(key="value")` dans la prose (backticks, gras ou nu).
            # Comportement observé avec Gemma/llama.cpp en mode natif : le
            # modèle « narre » l'appel au lieu de l'émettre — on l'exécute
            # réellement puis on boucle pour qu'il narrate le VRAI résultat.
            brace_calls, brace_clean = parse_prose_brace_calls(
                chat.content or "", self.tools)
            if brace_calls and not result.narration_forcee:
                _log.info(
                    "%d appel(s) en bloc accolade récupéré(s) : %s",
                    len(brace_calls),
                    ", ".join(c["name"] for c in brace_calls),
                )
                clean = brace_clean.strip()
                await self._preserve_narration(result, clean, on_delta)
                work.append(Message(role="assistant", content=clean))
                work.append(Message(
                    role="system",
                    content=(
                        "ℹ️ SYSTÈME : ton appel en bloc `nom{...}` a été "
                        "INTERCEPTÉ et exécuté réellement. Les résultats "
                        "officiels suivent dans les messages tool — ce sont "
                        "les seules valeurs valides. À l'avenir, appelle les "
                        "outils via le tool_calls natif ou la balise "
                        "`<tool name=\"...\" key=\"value\">` seule sur sa "
                        "ligne."
                    ),
                ))
                await self._exec_tool_calls_prompt(brace_calls, ctx, work, result, on_event)
                continue

            prose_calls, prose_clean = parse_prose_tool_calls(chat.content or "", self.tools)
            if prose_calls and not result.narration_forcee:
                _log.info(
                    "%d appel(s) d'outil récupéré(s) de la prose : %s",
                    len(prose_calls),
                    ", ".join(c["name"] for c in prose_calls),
                )
                clean = prose_clean.strip()
                await self._preserve_narration(result, clean, on_delta)
                work.append(Message(role="assistant", content=clean))
                # (la prose substantielle est streamée via _preserve_narration ;
                #  le résidu court de transition reste ignoré — le dm final
                #  inclut de toute façon les blocs conservés.)
                work.append(Message(
                    role="system",
                    content=(
                        "ℹ️ SYSTÈME : tes appels écrits en syntaxe "
                        "`outil(...)` dans la narration ont été INTERCEPTÉS et "
                        "exécutés réellement. Les résultats officiels suivent "
                        "dans les messages tool — ce sont les seules valeurs "
                        "valides (ignore tout chiffre que tu aurais pu "
                        "inventer avant). À l'avenir, appelle les outils via "
                        "le tool_calls natif ou la balise "
                        "`<tool name=\"...\" key=\"value\">` seule sur sa "
                        "ligne, JAMAIS en syntaxe fonctionnelle dans le texte."
                    ),
                ))
                await self._exec_tool_calls_prompt(prose_calls, ctx, work, result, on_event)
                continue

            # --- A0. Garde-fou « escalier sans aucun appel d'outil » -------
            # Le modèle répond en pur récit (« vous êtes dans la salle des
            # escaliers… ») sans appeler AUCUN outil, alors que le joueur a
            # demandé monter/descendre depuis une salle d'escaliers — le
            # garde-fou `_rediriger_escalier` ne couvre que les appels
            # `carte_donjon_explorer`. Observé en partie 54de40ed : le tour
            # se terminait en description de la salle, le groupe ne changeait
            # jamais d'étage. On force ici l'appel manquant
            # `carte_donjon_etage` (une seule fois par tour, cf. trace
            # ci-dessous), puis on boucle pour la narration du résultat.
            if (
                not result.narration_forcee
                and not chat.tool_calls
                and not any(
                    tc.get("name") == "carte_donjon_etage"
                    for tc in result.tool_calls_trace
                )
            ):
                en_combat = False
                try:
                    _etat_a0 = PartyState(
                        data_dir=str(ctx.data_dir),
                        partie_id=ctx.partie_id,
                    ).load()
                    en_combat = (
                        str(_etat_a0.get("phase") or "") == "combat"
                    )
                except Exception:                          # noqa: BLE001
                    pass
                intention = self._intention_escalier(work)
                if intention and await self._groupe_dans_escalier(ctx):
                    _log.warning(
                        "escalier sans appel d'outil : intention joueur "
                        "« %s » mais aucune tool call → appel forcé de "
                        "carte_donjon_etage(direction=%s)",
                        intention, intention,
                    )
                    work.append(Message(
                        role="assistant", content=(chat.content or "").strip(),
                    ))
                    work.append(Message(
                        role="system",
                        content=(
                            "ℹ️ SYSTÈME : le joueur a demandé de "
                            f"{intention} l'escalier et le groupe est dans "
                            "une salle d'escaliers, mais tu n'as appelé "
                            "AUCUN outil. L'appel `carte_donjon_etage` "
                            "vient d'être exécuté pour toi — narre le "
                            "CHANGEMENT D'ÉTAGE d'après le résultat "
                            "officiel ci-dessous (JAMAIS `carte_donjon_"
                            "explorer` pour un escalier)."
                        ),
                    ))
                    await self._exec_tool_calls_prompt(
                        [{"name": "carte_donjon_etage",
                          "arguments": {"direction": intention}}],
                        ctx, work, result, on_event,
                    )
                    continue

                # --- Garde-fou « repos demandé » ---------------------------
                # Le joueur demande un repos (« je me repose ») et le modèle
                # l'IGNORE en narrant autre chose — observé deux fois de
                # suite en partie abf74a77 : jamais de `repos_long`, PV
                # jamais restaurés. On force l'appel (toute l'équipe) une
                # seule fois, puis la boucle narre le résultat officiel.
                dernier_user = ""
                for m in reversed(work):
                    if m.role == "user":
                        dernier_user = m.content or ""
                        break
                # Les consignes correctives du serveur (rejeus) arrivent en
                # message `user` et citent souvent les MOTS de l'intention
                # (« REPOS », « soigner », « descendre »...) : elles ne sont
                # PAS une demande du joueur. Sans ce filtre, le rattrapage
                # « soin/repos narrés » (5quater-d) réinjectait sa propre
                # consigne et le garde forçait un repos_long de 8 h au
                # milieu de la scène d'ouverture (partie 5a9b99c8).
                dernier_user_correctif = (
                    "instruction INTERNE du moteur de jeu" in dernier_user
                )
                repos_deja_appele = any(
                    tc.get("name") == "repos_long"
                    for tc in result.tool_calls_trace
                )
                if (
                    dernier_user
                    and not dernier_user_correctif
                    and _INTENT_REPOS_RE.search(dernier_user)
                    and not repos_deja_appele
                ):
                    if not en_combat:
                        _log.warning(
                            "repos demandé sans appel d'outil → appel "
                            "forcé de repos_long (toute l'équipe)"
                        )
                        work.append(Message(
                            role="assistant",
                            content=(chat.content or "").strip(),
                        ))
                        work.append(Message(
                            role="system",
                            content=(
                                "ℹ️ SYSTÈME : le joueur a demandé à se "
                                "REPOSER, mais tu n'as appelé AUCUN outil. "
                                "L'appel `repos_long` vient d'être exécuté "
                                "pour toi (toute l'équipe) — narre le repos "
                                "et ses effets d'après le résultat officiel "
                                "ci-dessous (PV récupérés = 1/niveau)."
                            ),
                        ))
                        await self._exec_tool_calls_prompt(
                            # forcer=True : le repos est ici piloté PAR LE
                            # JOUEUR (demande explicite) — la garde
                            # anti-repos-spam ne doit pas le bloquer ; elle
                            # vise les repos initiés par le MJ en rafale.
                            [{"name": "repos_long",
                              "arguments": {"forcer": True}}],
                            ctx, work, result, on_event,
                        )
                        continue

                # --- Garde-fou « combat narré sans engager » ----------------
                # Le modèle narre une ATTAQUE de créatures contre le groupe
                # en pur prose SANS appeler `engager_combat` : ni initiative,
                # ni PV officiels, ni calibration d'équilibre — partie
                # 77e2862b : « cinq squelettes ... vous attaquent » narré
                # sans aucun tool call. On extrait les ennemis du bestiaire
                # depuis la narration et on force l'engagement (le plafond
                # d'équilibre interne arbitre ensuite la quantité).
                if not en_combat:
                    monstres_str = _ennemis_annonces(chat.content or "", ctx)
                    if not monstres_str:
                        # Partie b59b4a9a : l'embuscade CANONIQUE de la
                        # salle (ennemis du manifeste) narrée en prose sans
                        # engager — le modèle évitait même le nom de la
                        # créature (« des pierres tombent du plafond »).
                        monstres_str = _ennemis_salle_courante(
                            chat.content or "", ctx)
                    if monstres_str:
                        _log.warning(
                            "combat narré sans engager_combat → appel forcé "
                            "avec : %s", monstres_str,
                        )
                        work.append(Message(
                            role="assistant",
                            content=(chat.content or "").strip(),
                        ))
                        work.append(Message(
                            role="system",
                            content=(
                                "ℹ️ SYSTÈME : tu as narré des créatures qui "
                                "attaquent le groupe, mais tu n'as appelé "
                                "AUCUN outil. `engager_combat` vient d'être "
                                "exécuté pour toi avec les créatures "
                                "détectées — initiative officielle, PV et "
                                "calibration d'équilibre inclus. Narre le "
                                "début du combat d'après le résultat "
                                "officiel ci-dessous (n'invente AUCUN "
                                "nombre)."
                            ),
                        ))
                        # Partie b59b4a9a : la rencontre canonique peut être
                        # AU-DELÀ du plafond d'équilibre (« Crawler charognard
                        # ×6 » = 150 PV contre 17 PV pour le groupe — refus
                        # « Rencontre écrasante » en BOUCLE : le modèle
                        # re-narrait l'attaque, le garde re-forçait, refus
                        # encore). Dégradation EN DEUX TEMPS : (1) la
                        # quantité canonique est CONSERVÉE et la rencontre
                        # est adaptée via `ajustement` (pv −x%, attaque/dégâts
                        # réduits — c'est ce que la note [AJUSTEMENT REQUIS]
                        # du manifeste demande et ce que suggère le refus) ;
                        # (2) en dernier recours, vague réduite. Le résultat
                        # officiel fait foi pour la narration.
                        _essai = monstres_str
                        _ajust = ""
                        for _palier in range(3):
                            await self._exec_tool_calls_prompt(
                                [{"name": "engager_combat",
                                  "arguments": {"monstres": _essai,
                                                "ajustement": _ajust}}],
                                ctx, work, result, on_event,
                            )
                            _tr_txt = str(
                                (result.tool_calls_trace or [{}])[-1]
                                .get("text") or ""
                            )
                            if ("écrasante" not in _tr_txt.lower()
                                    or _palier == 2):
                                break
                            if _palier == 0:
                                # PV canoniques → facteur pour entrer sous le
                                # plafond (le refus affiche « X PV cumulés …
                                # (plafond : Y, 2,5×) »).
                                _m_pv = re.search(
                                    r"(\d+)\s+PV\s+cumulés", _tr_txt)
                                _m_pl = re.search(
                                    r"plafond\s*:\s*(\d+)", _tr_txt)
                                if _m_pv and _m_pl:
                                    _somme = max(1, int(_m_pv.group(1)))
                                    _cible = max(8, int(_m_pl.group(1)) - 10)
                                    _pct = max(10, min(90, int(
                                        100 * _cible / _somme)))
                                    _ajust = (
                                        f"pv {_pct}%, attaque -2, degats -2")
                                    continue
                            _parties = [p.strip()
                                        for p in _essai.split(",") if p.strip()]
                            if len(_parties) <= 1:
                                break
                            _essai = ", ".join(
                                _parties[:max(1, len(_parties) // 3)])
                        continue

            # --- A. Détection de simulation textuelle ----------------------
            # (après B/C/C2 : si un appel réel a été récupéré, ce n'est plus
            # une simulation à corriger — le tour continue avec les résultats.)
            if self.detect_simulation and not result.narration_forcee:
                # Seul un JET DE DÉGÂTS réel (lancer_degats) légitime la
                # reformulation en prose (« il subit 7 dégâts »). Une attaque
                # résolue (lancer_attaque) ne prouve PAS que les dégâts aient
                # été jetés : la détection reste active, sinon « touché +2
                # dégâts » narré sans lancer_degats restait sans effet.
                damage_rolled = any(
                    tc.get("name") == "lancer_degats"
                    for tc in result.tool_calls_trace
                )
                dice_rolled = any(
                    tc.get("name") in _DICE_TOOL_NAMES
                    for tc in result.tool_calls_trace
                )
                fiche_ecrite = any(
                    tc.get("name") in _FICHE_ECRITURE_TOOLS
                    for tc in result.tool_calls_trace
                )
                # Gains d'état (soins, XP, inventaire) : la reformulation en
                # prose n'est légitime que si l'outil canonique a tourné.
                gain_rolled = any(
                    tc.get("name") in _GAIN_TOOLS
                    for tc in result.tool_calls_trace
                )
                sim = looks_like_simulation(
                    chat.content,
                    include_damage=not (damage_rolled or trust_damage_prose),
                    include_checks=not dice_rolled,
                    include_creation=not fiche_ecrite,
                    include_gains=not gain_rolled,
                    include_pj_damage=not trust_damage_prose,
                )
                if sim:
                    result.simulation_attempted = True
                    if _correction_autorisee(result):
                        result.corrections += 1
                        # Correctif ciblé : attaque adverse et dégâts SUBIS
                        # INVENTÉS (« vous avez été touché pour 8 dégâts ») —
                        # le serveur joue les monstres, leur résultat arrive
                        # en événement officiel APRÈS le tour PJ.
                        if any(
                            p.search(sim) for p in _PJ_DEGATS_PROSE_PATTERNS
                        ):
                            consigne_sim = (
                                "⚠️ CORRECTION : tu as narré "
                                f"« {sim} » — une attaque de monstre et des "
                                "dégâts SUBIS par un personnage sans aucun "
                                "événement mécanique. Le serveur joue les "
                                "monstres avec les jets officiels du "
                                "bestiaire : leurs résultats t'arrivent en "
                                "événements à NARRER (jamais à inventer). "
                                "Termine ta narration à l'action du JOUEUR : "
                                "résous SON attaque avec `lancer_attaque` "
                                "puis `lancer_degats` et narre le résultat "
                                "officiel, SANS inventer de riposte adverse."
                                + ("\n" + _pv_officiels_ligne(ctx)
                                   if _pv_officiels_ligne(ctx) else "")
                                + _CORRECTIF_INTERNE
                            )
                        elif any(
                            p.search(sim) for p in _CHECK_PROSE_PATTERNS
                        ):
                            consigne_sim = (
                                "⚠️ CORRECTION : tu as ÉCRIT "
                                f"« {sim} » sans le résoudre — le tour s'est "
                                "arrêté avant tout résultat. Résous le jet "
                                "MAINTENANT avec les outils : jet de "
                                "caractéristique/compétence → `lancer_d20` "
                                "(competence + difficulte/DD) ou `lancer_des` "
                                "; sauvegarde → `lancer_sauvegarde`. Attends "
                                "le résultat du tool, puis narre l'issue "
                                "(réussite/échec et conséquences concrètes)."
                                + _CORRECTIF_INTERNE
                            )
                        elif any(
                            p.search(sim) for p in _FICHE_CREATION_PATTERNS
                        ):
                            consigne_sim = (
                                "⚠️ CORRECTION : tu as écrit "
                                f"« {sim} » — un résultat de création de fiche "
                                "sans avoir appelé l'outil. C'est interdit : "
                                "la fiche n'existe PAS réellement. Crée-la "
                                "MAINTENANT en appelant `fiche_perso_creer_rapide` "
                                "(nom, race, classe, niveau, carac_texte, "
                                "pv, ca…) — pour un personnage de joueur, "
                                "passe aussi `joueur`. Attends le résultat "
                                "officiel du tool, puis narre l'issue de sa "
                                "création." + _CORRECTIF_INTERNE
                            )
                        else:
                            consigne_sim = (
                                "⚠️ CORRECTION : tu as écrit "
                                f"« {sim} » au lieu d'appeler réellement l'outil. "
                                "Les `*(Simulation de l'appel ...)*` sont interdites :"
                                " elles invalident le jet. Rappelle l'outil via la "
                                "balise exacte `<tool name=\"...\" key=\"value\">` "
                                "(mode prompt) ou via le tool_calls natif — sans "
                                "reformuler la narrative jusqu'à obtenir le résultat. "
                                "Recommence ce tour en appelant réellement l'outil."
                                + _CORRECTIF_INTERNE
                            )
                        _msg_invalide = Message(
                            role="assistant",
                            content=chat.content,
                            tool_calls=chat.tool_calls or None,
                        )
                        work.append(_msg_invalide)
                        ids_corriges.append(id(_msg_invalide))
                        work.append(Message(
                            role="system",
                            content=consigne_sim,
                        ))
                        continue
                    # 2 corrections déjà : on continue avec le reste (best effort).

            # --- A3ter. Relance « outil requis mais non appelé » -----------
            # Recommandation « température tool-calling » : le modèle native
            # (Qwen 9B en particulier) narre une action MÉCANIQUE du joueur
            # en pur récit (« vous abattez la hache sur la goule ») sans
            # AUCUN appel d'outil — dégâts/PV jamais appliqués. Quand le
            # message du joueur exige un outil et que la réponse ne contient
            # QUE de la prose (ni appels naturels, ni zone mécanique), on
            # relance le tour UNE seule fois avec `tool_choice="required"`
            # + température de relance (0.35) : le backend est contraint de
            # produire un appel, impossible de re-narrer.
            if (
                use_native
                and tools_arg
                and not result.narration_forcee
                and not retry_requis_envoye
                and result.corrections < 3
                and not chat.tool_calls
                and not result.tool_calls_trace
            ):
                dernier_user = ""
                for m in reversed(work):
                    if m.role == "user":
                        dernier_user = m.content or ""
                        break
                # Une consigne interne (rejeu 5quater-d, correction budget)
                # arrive en message `user` : elle n'est PAS une action du
                # joueur (cf. correctif 5a9b99c8) — on ne relance pas dessus.
                _phase_a3 = ""
                if (
                    dernier_user
                    and "instruction INTERNE du moteur de jeu" not in dernier_user
                    and _INTENT_MECANIQUE_RE.search(dernier_user)
                ):
                    try:
                        _etat_a3 = PartyState(
                            data_dir=str(ctx.data_dir),
                            partie_id=ctx.partie_id,
                        ).load()
                        _phase_a3 = str(
                            _etat_a3.get("phase") or ""
                        ).strip().lower()
                    except Exception:                     # noqa: BLE001
                        _phase_a3 = ""
                # Exploration/voyage/roleplay sont DEJA couverts par la
                # phase de décision contrainte : la relance ne concerne que
                # combat / opening / opening_complete (où l'action demandée
                # est mécanique et aucun choix contraint ne tourne).
                if (_phase_a3 and _phase_a3 not in _PHASES_DECISION
                        and _correction_autorisee(result)):
                    retry_requis_envoye = True
                    result.corrections += 1
                    _log.warning(
                        "outil requis mais non appelé (prose seule) → "
                        "relance avec tool_choice='required' "
                        "(phase %r)", _phase_a3,
                    )
                    work.append(Message(
                        role="assistant",
                        content=(chat.content or "").strip(),
                    ))
                    work.append(Message(
                        role="system",
                        content=(
                            "ℹ️ SYSTÈME : l'action du joueur exige un appel "
                            "d'outil MÉCANIQUE que tu n'as pas émis — ta "
                            "réponse était en pur récit. Appelle MAINTENANT "
                            "l'outil adéquat : attaque → `lancer_attaque` "
                            "puis `lancer_degats` ; sauvegarde → "
                            "`lancer_sauvegarde` ; jet → `lancer_des` / "
                            "`lancer_d20` ; soin → `fiche_perso_soigner` ; "
                            "potion → `inventaire_consommer_munition` ; "
                            "sort → `incanter_sort` ; les dégâts subis par "
                            "les personnages sont joués par le serveur "
                            "(événements à narrer, jamais à inventer) ; fin "
                            "du tour → `terminer_mon_tour`. Ne narre le "
                            "résultat qu'APRÈS le retour officiel du tool."
                            + _CORRECTIF_INTERNE
                        ),
                    ))
                    continue

            # --- D. Réponse finale (narration) ------------------------------
            # Aucun appel d'outil à effectuer ⇒ narration complète du MJ.
            # On refait l'appel en streaming pour envoyer les tokens au fur
            # et à mesure au client.
            if on_delta:
                collected = ""
                pending = ""   # fragment retenu (début de balise potentiel)
                async for token in self.client.stream_chat(
                    work, tools=tools_arg,
                ):
                    collected += token
                    pending += token
                    # On nettoie le contenu SÛR du buffer avant de le pousser au
                    # client, pour éviter la fuite des balises d'appel/thinking à
                    # l'écran. Le fragment suspect (`<tool`, `*(`…) est retenu
                    # jusqu'au flush final où on aura le contexte complet.
                    safe, pending = _safe_stream_split(pending)
                    if safe:
                        # Filtre léger idempotent : applique le strip thinking +
                        # suppression des jetons gemma, SANS les regex multi-token
                        # (qui seraient tronquées en streaming) — elles s'exécutent
                        # en intégralité sur le buffer final ci-dessous.
                        # strip_spaces=False : ne JAMAIS dénuder chaque delta,
                        # sinon les espaces/retours à la ligne entre les mots
                        # disparaissent (texte collé à l'écran pendant le
                        # streaming, corrigé seulement au message final).
                        safe = _strip_thinking(safe, strip_spaces=False)
                        for _tok in _GEM_QUOTE_TOKENS:
                            if _tok in safe:
                                safe = safe.replace(_tok, "")
                        safe = _TOOLCALL_ORPHAN_CLOSE_RE.sub("", safe)
                        if safe:
                            await on_delta(safe)
                # Flush final : le fragment retenu + tout reste, nettoyé complet.
                if pending.strip():
                    final = _strip_thinking(
                        pending
                    )
                    final = _TOOLCALL_ORPHAN_CLOSE_RE.sub("", final)
                    final = _tidy_empty_lines(final).strip()
                    if final:
                        await on_delta(final)
                narration = collected
            else:
                narration = chat.content
            # Filet de sécurité : même en streaming, un bloc thinking peut
            # fuir (tags coupés entre chunks) — on re-nettoie la narration
            # finale avant historique/broadcast.
            narration = _strip_thinking(narration)
            # Nettoyage des artefacts d'appel (syntaxe `outil(...)`, balises
            # résiduelles, placeholders « *(Attente du résultat …)* ») : le
            # joueur ne doit JAMAIS voir la mécanique interne.
            narration = strip_narration_artifacts(narration, self.tools)

            # --- D1bis. Simulation dans la narration FINALE (streamée) ------
            # Le check A porte sur le premier échantillon (chat non streamé) ;
            # la narration vient d'un second appel en streaming qui peut encore
            # contenir des jets simulés. On re-vérifie ici : les deltas déjà
            # poussés au client seront remplacés par le dm final corrigé.
            if self.detect_simulation and narration.strip() and result.corrections < 3:
                damage_rolled = any(
                    tc.get("name") == "lancer_degats"
                    for tc in result.tool_calls_trace
                )
                dice_rolled_final = any(
                    tc.get("name") in _DICE_TOOL_NAMES
                    for tc in result.tool_calls_trace
                )
                fiche_ecrite_final = any(
                    tc.get("name") in _FICHE_ECRITURE_TOOLS
                    for tc in result.tool_calls_trace
                )
                gain_rolled_final = any(
                    tc.get("name") in _GAIN_TOOLS
                    for tc in result.tool_calls_trace
                )
                sim_final = looks_like_simulation(
                    narration,
                    include_damage=not (damage_rolled or trust_damage_prose),
                    include_checks=not dice_rolled_final,
                    include_creation=not fiche_ecrite_final,
                    include_gains=not gain_rolled_final,
                    include_pj_damage=not trust_damage_prose,
                )
                if sim_final and _correction_autorisee(result):
                    result.simulation_attempted = True
                    result.corrections += 1
                    # (c) L'aperçu déjà streamé va être remplacé par la relance :
                    # on demande au client d'effacer le bloc de streaming pour
                    # un remplacement propre (pas de texte périmé affiché).
                    if on_delta is not None and on_event is not None:
                        try:
                            await on_event({"type": "stream_reset"})
                        except Exception:                     # noqa: BLE001
                            pass
                    _log.warning(
                        "simulation dans narration streamée (« %s », correction %d) — relance",
                        sim_final, result.corrections,
                    )
                    # Dégâts SUBIS par les PJ INVENTÉS (attaque adverse
                    # sans événement mécanique) : correctif CIBLÉ.
                    if any(
                        p.search(sim_final) for p in _PJ_DEGATS_PROSE_PATTERNS
                    ):
                        work.append(Message(
                            role="system",
                            content=(
                                "⚠️ CORRECTION : ta narration contient "
                                f"« {sim_final} » — une attaque de monstre et "
                                "des dégâts subis INVENTÉS, sans événement "
                                "mécanique officiel. Le serveur joue les "
                                "monstres (jets du bestiaire) et NARRE leurs "
                                "résultats APRÈS ton tour : ne les anticipe "
                                "JAMAIS. Termine ta narration à l'action du "
                                "joueur, avec SEUL le résultat officiel de "
                                "SON action (lancer_attaque/lancer_degats) "
                                "et les événements déjà fournis."
                                + ("\n" + _pv_officiels_ligne(ctx)
                                   if _pv_officiels_ligne(ctx) else "")
                                + _CORRECTIF_INTERNE
                            ),
                        ))
                        continue
                    if any(
                        p.search(sim_final) for p in _FICHE_CREATION_PATTERNS
                    ):
                        work.append(Message(
                            role="system",
                            content=(
                                "⚠️ CORRECTION : ta narration contient "
                                f"« {sim_final} » — un résultat de fiche narré "
                                "à la main. C'est interdit : la fiche n'existe "
                                "PAS tant que l'outil n'a pas été réellement "
                                "appelé. Recommence ce tour : appelle "
                                "`fiche_perso_creer_rapide` (ou "
                                "`fiche_perso_creer` pour la fiche complète) "
                                "avec nom, race, classe, niveau, carac_texte, "
                                "pv, ca — et `joueur` pour un personnage de "
                                "joueur — attends le résultat officiel du "
                                "tool, puis raconte la scène à partir de ce "
                                "résultat." + _CORRECTIF_INTERNE
                            ),
                        ))
                        continue
                    # Gains d'état en prose (soins / XP / inventaire) :
                    # correctif CIBLÉ (AVANT le correctif générique « jet »,
                    # sinon un gain narré reçoit un message hors-sujet).
                    if any(
                        p.search(sim_final) for p in _GAIN_PROSE_PATTERNS
                    ):
                        work.append(Message(
                            role="system",
                            content=(
                                "⚠️ CORRECTION : ta narration contient "
                                f"« {sim_final} » — un gain d'état affirmé à la "
                                "main. C'est interdit : la fiche n'a PAS changé. "
                                "Recommence ce tour en appelant l'outil canonique "
                                "AVANT de narrer : soins → `fiche_perso_soigner` ; "
                                "expérience → `fiche_perso_gagner_xp` ; objet "
                                "ramassé → `inventaire_ramasser` (ou "
                                "`inventaire_ajouter`). Attends le résultat "
                                "officiel du tool, puis narre la scène à partir "
                                "de ce résultat." + _CORRECTIF_INTERNE
                            ),
                        ))
                        continue
                    work.append(Message(
                        role="system",
                        content=(
                            "⚠️ CORRECTION : ta narration contient "
                            f"« {sim_final} » — un résultat de jet écrit à la main. "
                            "C'est interdit : chaque jet (attaque, dégâts, sauvegarde, "
                            "compétence) DOIT passer par l'appel réel de l'outil "
                            "(lancer_attaque, lancer_degats, lancer_sauvegarde, "
                            "lancer_d20...). Recommence ce tour : appelle l'outil, "
                            "attends son résultat, puis narre l'issue en reprenant "
                            "le chiffre donné par l'outil." + _CORRECTIF_INTERNE
                        ),
                    ))
                    continue

            # --- D1ter. Répétition d'une scène déjà narrée ------------------
            # Le modèle re-narre mot pour mot un tour précédent au lieu de
            # répondre à l'action du joueur (écho quasi verbatim observé en
            # partie réelle) : l'action est perdue et le fil de l'histoire
            # casse. On relance avec un correctif qui re-cite l'action du
            # joueur — les deltas déjà streamés sont remplacés par le dm final.
            if narration.strip() and result.corrections_echo < cap_echo:
                # EXCEPTION : re-visite d'une salle à description FIGÉE. Le
                # tool `carte_donjon_explorer` ordonne alors explicitement de
                # re-narrer À L'IDENTIQUE (« Description enregistrée » /
                # « REPARCOURREZ ») : la répétition y est LÉGITIME. Sans cette
                # exception, l'anti-répétition rejetait la re-description,
                # enchaînait les relances et le modèle inventait n'importe
                # quoi pour « faire nouveau » (soin spontané halluciné,
                # PV > PV max — bug réel).
                dernier_tool = next(
                    (m for m in reversed(work) if m.role == "tool"),
                    None,
                )
                revisite_froide = bool(
                    dernier_tool
                    and dernier_tool.content
                    and (
                        "REPARCOURREZ" in dernier_tool.content
                        or "DÉJÀ VISITÉE" in dernier_tool.content
                        or "Description enregistrée" in dernier_tool.content
                    )
                )
                # Le référentiel exclut les narrations INVALIDES ajoutées par
                # les corrections (cf. ids_corriges) : la re-narration de la
                # même scène — avec les VRAIS chiffres des tools cette fois —
                # est le comportement attendu d'une relance, pas une
                # répétition à corriger.
                reference = [
                    m for m in work
                    if m.role == "assistant" and id(m) not in ids_corriges
                ]
                echo = (
                    None if revisite_froide
                    else trouve_repetition(narration, reference, seuil=seuil_repet)
                )
                if echo and _correction_autorisee(result):
                    result.corrections += 1
                    result.corrections_echo += 1
                    # (c) L'aperçu streamé est une répétition périmée : reset
                    # client avant la relance (même logique que D1bis).
                    if on_delta is not None and on_event is not None:
                        try:
                            await on_event({"type": "stream_reset"})
                        except Exception:                     # noqa: BLE001
                            pass
                    derniere_action = next(
                        (m.content for m in reversed(work)
                         if m.role == "user" and (m.content or "").strip()),
                        "(action illisible)",
                    )
                    _log.warning(
                        "narration répétée d'un tour précédent (« %s… », "
                        "correction %d) — relance orientée sur l'action joueur",
                        echo[:80], result.corrections,
                    )
                    # Escalade : dès la 2e correction echo, la consigne se
                    # durcit (le modèle ré-échoisait jusqu'à 4× d'affilée —
                    # partie 2ca691ec) : borne la longueur, bannit l'amorce
                    # copiée et exige un fait NOUVEAU.
                    durcissement = ""
                    if result.corrections >= 2:
                        amorce = " ".join(echo.split()[:8])
                        durcissement = (
                            " ⛔ DERNIER AVERTISSEMENT : LIMITE ta réponse à "
                            "DEUX PHRASES. Ne commence PAS par « "
                            f"{amorce}… ». Écris UNE conséquence NOUVELLE "
                            "et concrète de l'action (son, odeur, dégât, "
                            "réaction, découverte) puis ARRÊTE."
                        )
                    work.append(Message(
                        role="system",
                        content=(
                            "⚠️ CORRECTION : tu viens de RÉPÉTER mot pour mot "
                            "une narration déjà envoyée "
                            f"(« {echo}… »). C'est interdit : chaque tour "
                            "FAIT AVANCER l'histoire. L'action du joueur à "
                            f"laquelle tu dois répondre est : "
                            f"« {derniere_action} ». Raconte la CONSÉQUENCE "
                            "de cette action (nouveaux événements, PNJ, "
                            "découvertes ou dangers), en t'appuyant sur "
                            "l'état actuel et les résultats d'outils — "
                            "jamais en recopiant un texte précédent."
                            + durcissement
                            + _CORRECTIF_INTERNE
                        ),
                    ))
                    continue

            # --- D1quater. Déplacement narré SANS outil ----------------------
            # Le modèle raconte l'entrée dans une salle ou un lieu (« pénètre
            # dans la grotte », « arrive devant la grotte ») SANS avoir appelé
            # `carte_donjon_explorer`/`voyage_demarrer` : le déplacement n'a
            # PAS eu lieu mécaniquement — la carte et l'état restent figés
            # (courant (0,0) pendant que la narration est déjà au fond de la
            # grotte ; partie ae358455 : 3 tours pour faire entrer le groupe,
            # l'outil jamais appelé). On relance avec l'outil d'abord, puis la
            # narration d'après le résultat officiel.
            if narration.strip() and _correction_autorisee(result):
                _depl_outil = any(
                    tc.get("name") in _OUTILS_DEPLACEMENT
                    for tc in result.tool_calls_trace
                )
                _depl_narre = (
                    None if _depl_outil else _deplacement_narre(narration)
                )
                # 🚶 Partie 15aa0b6f : exemption MICRO-DÉPLACEMENT — sortir
                # de la tour pour « aller chez le marchand » (même ville)
                # est libre (règle 7) ; D1 relançait et le modèle, sans
                # bonne option, bouclait sur la copie de [5].
                if _depl_narre and _deplacement_local(narration, etat):
                    _depl_narre = None
                if _depl_narre:
                    result.corrections += 1
                    if on_delta is not None and on_event is not None:
                        try:
                            await on_event({"type": "stream_reset"})
                        except Exception:                     # noqa: BLE001
                            pass
                    _log.warning(
                        "déplacement narré sans outil (« %s », correction %d)"
                        " — relance",
                        _depl_narre[:80], result.corrections,
                    )
                    work.append(Message(
                        role="system",
                        content=(
                            "⚠️ CORRECTION : ta narration raconte un "
                            f"DÉPLACEMENT (« {_depl_narre} ») sans qu'AUCUN "
                            "outil de déplacement n'ait été appelé ce tour — "
                            "le déplacement n'a PAS eu lieu : la carte et "
                            "l'état restent figés, le groupe est toujours en "
                            "salle COURANTE. Appelle D'ABORD l'outil adéquat :"
                            " dans un donjon, `carte_donjon_explorer("
                            "direction=\"nord\"|\"est\"|\"sud\"|\"ouest\")` "
                            "avec une porte OUVERTE de la salle courante (voir"
                            " le bloc CARTE DU DONJON) ; hors donjon, "
                            "`voyage_demarrer(destination=..., distance_km=...,"
                            " terrain=...)`. Attends le résultat OFFICIEL puis "
                            "narre la salle/le voyage d'après ce résultat — "
                            "JAMAIS le déplacement en prose seule."
                            + _CORRECTIF_INTERNE
                        ),
                    ))
                    continue

                # 🎲 Partie 0e615b81/audit : les JETS DE COMPÉTENCE RÉUSSIS
                # narrés sans dé (« il fouille la pièce et réussit sa
                # Perception ») — la réussite ouvrait un verrou/un piège/un
                # trésor SANS mécanique (règle 2 : le dé précède la prose).
                # Relance roll-to-confirm : `lancer_d20` D'ABORD (la fiche
                # fournit rangs et modificateurs), PUIS la narration du
                # résultat OFFICIEL — réussite OU échec, tel quel.
                if narration.strip() and _correction_autorisee(result):
                    _jet_outil = any(
                        tc.get("name") in ("lancer_d20", "lancer_sauvegarde",
                                           "calculer_initiative",
                                           "lancer_attaque")
                        and tc.get("ok")
                        for tc in result.tool_calls_trace
                    )
                    _jet_narre = (
                        None if _jet_outil else _jet_reusse_narre(narration))
                    if _jet_narre:
                        result.corrections += 1
                        if on_delta is not None and on_event is not None:
                            try:
                                await on_event({"type": "stream_reset"})
                            except Exception:                 # noqa: BLE001
                                pass
                        _log.warning(
                            "jet de compétence réussi narré sans dé (« %s », "
                            "correction %d) — relance roll-to-confirm",
                            _jet_narre[:80], result.corrections,
                        )
                        work.append(Message(
                            role="system",
                            content=(
                                "⚠️ CORRECTION : ta narration raconte la "
                                "RÉUSSITE d'un jet de compétence (« "
                                f"{_jet_narre} ») sans qu'aucun "
                                "`lancer_d20` n'ait été appelé ce tour — "
                                "le jet n'a PAS eu lieu mécaniquement : le "
                                "verrou, le piège ou le trésor ne s'ouvrent "
                                "pas en prose. Appelle D'ABORD "
                                "`lancer_d20(nom_personnage=…, "
                                "competence=…, difficulte=…)` — la fiche "
                                "fournit rangs et modificateurs (gradation "
                                "DMG : facile 5, moyenne 10, difficile 15, "
                                "très difficile 20, héroïque 25) — PUIS "
                                "narre le résultat OFFICIEL, réussite ou "
                                "échec, TEL QUEL."
                                + _CORRECTIF_INTERNE
                            ),
                        ))
                        continue

            # --- D2. Rattrapage : contenu vide après stripping thinking -----
            # Gemma 4 E4B renvoie parfois des réponses entièrement thinking
            # (tout le texte est dans <|channel>thought...<channel|>), résultat
            # visible = "" sans tool_calls. Sans intervention, on sortirait avec
            # une narration vide. On injecte un correctif et on relance.
            if not narration.strip():
                if _correction_autorisee(result):
                    result.corrections += 1
                    # (c) Rien de viable à l'écran : efface l'aperçu streamé
                    # (le cas échéant) avant la relance.
                    if on_delta is not None and on_event is not None:
                        try:
                            await on_event({"type": "stream_reset"})
                        except Exception:                     # noqa: BLE001
                            pass
                    _log.warning(
                        "narration vide après stripping thinking (correction %d/3) — relance",
                        result.corrections,
                    )
                    work.append(Message(
                        role="system",
                        content=(
                            "Ta réponse précédente ne contenait aucun texte "
                            "visible — tout était dans les balises thinking "
                            "(réflexion interne). Le joueur ne voit que le "
                            "texte en dehors de ces balises. RÉPONDS EN PROSE "
                            "VISIBLE, directement, sans balises thinking. "
                            "Raconte au joueur ce qui se passe et propose-lui "
                            "des actions." + _CORRECTIF_INTERNE
                        ),
                    ))
                    continue
                # 3 corrections déjà : on accepte ce qu'on a (best effort).
                _log.warning("narration vide malgré 3 corrections — on accepte")

            work.append(Message(role="assistant", content=narration))
            result.narration = narration
            break

        else:
            # Boucle épuisée sans narration finale. Le LLM reste coincé à
            # appeler des tools sans produire de synthèse (souvent le modèle
            # 12B sature num_ctx avec les messages tool successifs). On tente
            # un dernier appel SANS tools pour forcer une narration clôturante,
            # plutôt que de retourner une chaîne vide au client.
            _log.warning(
                "tool loop épuisé (iterations=%d, corrections=%d) — fallback narration",
                result.iterations, result.corrections,
            )
            narration = await self._force_final_narration(work, on_delta)
            result.narration = narration or (
                work[-1].content if work and work[-1].role == "assistant" else ""
            )

        # Filet ultime : narration vide après break (ex. Gemma en thinking pur
        # malgré 3 corrections) → un dernier appel sans tools, jamais un dm vide.
        if not result.narration.strip():
            _log.warning("narration finale vide — fallback sans tools")
            narration = await self._force_final_narration(work, on_delta)
            result.narration = narration or (
                "(Le Maître du Jeu marque une pause… Reformulez votre action.)"
            )

        # Rassemblement : les narrations produites EN MÊME TEMPS que les
        # appels d'outils précèdent la narration finale (dm + historique).
        # Sans cela, une intro de scène narrée avant `memoire_lieu`/
        # `etat_partie_patch` n'atteignait jamais le joueur (partie 43234a00 :
        # « une bonne partie de la narration manque »). La vérification
        # anti-répétition (D1ter) a déjà tourné sur la narration finale seule,
        # donc pas de faux positif contre les blocs intermédiaires.
        if result.narrations_intermediaires:
            gardes = _assemble_narrations(
                result.narrations_intermediaires, result.narration)
            supprimes = (
                len(result.narrations_intermediaires) + 1 - len(gardes))
            if supprimes > 0:
                _log.info(
                    "%d narration(s) intermédiaire(s) supplantée(s) "
                    "(brouillons recouverts par une version ultérieure)",
                    supprimes,
                )
            result.narration = "\n\n".join(gardes).strip()

        # NB : `notes_mecaniques` (dégâts auto-appliqués) n'est PAS concaténé
        # ici — main.py l'ajoute à la dm finale APRÈS les rejeux correctifs
        # (qui remplacent la narration), pour ne rien perdre.

        # 📏 Partie dfbb4846 : garde « INTRO TROP COURTE » — le tour
        # d'ouverture, fragilisé par une coupure de stream (le modèle
        # déchargé entre les tours recharge en lazy et la connexion meurt :
        # « Server disconnected »), se terminait par une synthèse de 775
        # caractères SANS amorce de scène (le décor sauté, remise d'objets
        # directe, glitch de tokens). Si l'ouverture n'a produit QU'UN
        # événement d'histoire et une narration < ~700 caractères, UNE
        # relance force la scène d'ouverture complète (4-6 paragraphes).
        try:
            from ..game.state import PartyState as _PS_intro
            _etat_intro = _PS_intro(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=0,
            ).load()
            _hist_intro = [
                _ev for _ev in (_etat_intro.get("histoire") or [])
                if isinstance(_ev, dict)
                and str(_ev.get("evenement") or "").startswith(
                    "Début de l'aventure")
            ]
            # 📏 Correctif 33d8f18e : le simple fait que l'histoire soit
            # courte ne fait PAS du tour courant une ouverture — au tour du
            # repas (histoire encore ≤ 2 événements), la garde a relancé la
            # « scène complète » et le modèle a RE-NARRÉ le briefing avec un
            # second parchemin. Le drapeau `ouverture_tour` (phase opening +
            # aucune narration antérieure, calculé au début de run()) est la
            # seule autorisation.
            # 🎨 Partie 083c7bba : la LONGUEUR ne suffit pas — une intro de
            # 1331 caractères sautait le décor (l'acceptation « déjà faite »
            # avant le premier mot, zéro description). Le décor canonique de
            # la salle d'entrée (ancre du manifeste) doit être ANCRÉ.
            from .prompt_builder import (_DEBUT_AVENTURE,
                                         _lieu_depart_canonique)
            _ancre_ligne = _lieu_depart_canonique(
                _etat_intro, str(ctx.data_dir))
            _decor_absent = _decor_ancre_absent(
                result.narration or "", _ancre_ligne)
            if (
                ouverture_tour
                and _hist_intro
                and (len((result.narration or "").strip()) < 700
                     or _decor_absent)
            ):
                _log.warning(
                    "intro trop courte (%d car.) ou décor absent — relance "
                    "scène complète", len(result.narration.strip()))
                _msg_intro = (
                    "⚠️ CORRECTION : ton introduction est TROP COURTE et "
                    "INCOMPLÈTE (un résumé expédié au lieu de la scène). "
                    "Reprends la SCÈNE D'OUVERTURE COMPLÈTE, en 4 à 6 "
                    "paragraphes immersifs, SANS rappeler d'outil : "
                    + _DEBUT_AVENTURE
                )
                if _decor_absent and _ancre_ligne:
                    _m_dec = re.search(r"«\s*(.+?)\s*»", _ancre_ligne)
                    if _m_dec:
                        _msg_intro += (
                            "\n🏛️ OUVRE obligatoirement par le DÉCOR "
                            "CANONIQUE du lieu, ancré mot pour mot dans ta "
                            "première phrase : « "
                            + _m_dec.group(1)[:220] + " » — AVANT toute "
                            "réplique, toute remise d'objet et tout "
                            "résumé de mission."
                        )
                if on_delta is not None and on_event is not None:
                    try:
                        await on_event({"type": "stream_reset"})
                    except Exception:                     # noqa: BLE001
                        pass
                _narr_intro = await self._force_final_narration(
                    work, on_delta, message=_msg_intro)
                if len((_narr_intro or "").strip()) > len(
                        result.narration.strip()):
                    result.narration = _narr_intro
                    result.corrections += 1
        except Exception as _e_intro:                    # noqa: BLE001
            _log.warning("garde intro courte échouée (ignoré) : %s", _e_intro)

        # 🧍 Partie b59b4a9a : correction DÉTERMINISTE des pronoms du mauvais
        # genre attribués aux PJ (« Margoth… Elle se relève », PJ masculin) —
        # l'écho du mauvais genre dans l'historique domine toute consigne de
        # prompt ; le serveur corrige les contextes non ambigus.
        try:
            from ..game.state import PartyState
            _etat_g = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=0,
            ).load()
            _avant_g = result.narration
            result.narration = _corriger_pronoms_pj(
                result.narration, _etat_g or {}, str(ctx.data_dir))
            if result.narration != _avant_g:
                _log.info(
                    "pronoms de PJ corrigés (genre) dans la narration finale")
        except Exception:                                    # noqa: BLE001
            pass

        # 🔁 Partie 15aa0b6f : filet anti-écho EXACT en post-boucle — [7]
        # était la copie OCTET POUR OCTET de [5] (le plafond de corrections
        # épuisé par les corrections de déplacement, la copie est passée
        # telle quelle). Si la narration finale recouvre quasi verbatim
        # (~60 %) une narration antérieure du contexte, une DERNIÈRE
        # re-narration anti-copie est forcée (sans tools).
        try:
            _refs_echo = [
                Message(role="assistant", content=m.content)
                for m in work
                if m.role == "assistant" and len(m.content or "") > 200
            ]
            _echo_final = trouve_repetition(
                result.narration or "", _refs_echo, seuil=0.6)
            if _echo_final and len((result.narration or "").strip()) > 200:
                _log.warning(
                    "narration finale = écho d'un tour précédent (« %s… ») "
                    "— re-narration anti-copie forcée", _echo_final[:80])
                _msg_anti = (
                    "⚠️ CORRECTION : ta réponse RECOPIE presque mot pour "
                    "mot une narration précédente (« "
                    + _echo_final[:140]
                    + "… »). C'est inacceptable : réécris une narration "
                    "NOUVELLE qui répond à l'action du joueur — avance la "
                    "scène, un fait NOUVEAU par paragraphe, aucun texte "
                    "recyclé, aucune re-narration d'un tour passé."
                    + _CORRECTIF_INTERNE
                )
                if on_delta is not None and on_event is not None:
                    try:
                        await on_event({"type": "stream_reset"})
                    except Exception:                            # noqa: BLE001
                        pass
                _narr_anti = await self._force_final_narration(
                    work, on_delta, message=_msg_anti)
                if _narr_anti and len(_narr_anti.strip()) > 200:
                    result.narration = _narr_anti
        except Exception as _e_echo:                         # noqa: BLE001
            _log.warning("filet anti-écho échoué (ignoré) : %s", _e_echo)

        # 🪙 Partie 1808ebab : rattrapage DÉTERMINISTE des ACHATS — le joueur
        # demande explicitement (« j'achète 5 flèches », « Repas médiocre »),
        # le modèle narre la transaction en prose (prix inventés) SANS
        # `marche_acheter` ni `auberge_commander` : or non débité, objet non
        # ajouté. Comme pour les remises de quête, le serveur applique
        # l'achat aux tarifs OFFICIELS (le prix narré, s'il est inventé, est
        # simplement ignoré).
        try:
            _msgs_user = [
                str(_m.content or "") for _m in work
                if getattr(_m, "role", "") == "user"
            ]
            _intention = _intention_achat(_msgs_user)
            if _intention and result.narration.strip() and not any(
                tc.get("name") in ("marche_acheter", "auberge_commander")
                and tc.get("ok")
                # 🔧 Partie 33d8f18e : un appel REFUSÉ (args corrompus du
                # modèle : « repas": true » → « Qualité « true » inconnue »)
                # porte ok=True mais n'a RIEN acheté — il ne doit pas bloquer
                # le rattrapage. Seul un ✅ (transaction réelle) le bloque.
                and "✅" in (tc.get("text") or "")
                for tc in result.tool_calls_trace
            ):
                from ..tools.marche import (       # noqa: E501 pylint: disable=import-outside-toplevel
                    auberge_commander as _aub_cmd,
                    marche_acheter as _marche_ach,
                )
                from .. import equipement_phb as _phb_articles  # noqa: E501 pylint: disable=import-outside-toplevel
                from ..game.state import PartyState  # noqa: E501 pylint: disable=import-outside-toplevel
                _etat_ach = PartyState(
                    data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                    max_history=0,
                ).load()
                _pj_nom_achat = next(
                    (str(_p.get("nom")) for _p in (_etat_ach.get("pj") or [])
                     if _p.get("nom")),
                    None,
                )
                _achat = _extraire_achat(
                    _intention, list(_phb_articles.articles()))
                if _pj_nom_achat and _achat.get("type") == "auberge":
                    _args_ach = {
                        "nom": _pj_nom_achat, "repas": _achat.get("repas", ""),
                        "logement": _achat.get("logement", ""),
                        "nuits": int(_achat.get("nuits") or 1)}
                    _tr_ach = await self.execute_tool_direct(
                        "auberge_commander", _args_ach, ctx, on_event, result,
                    )
                    # 🔧 Partie 1808ebab (suite) : Silverymoon (Cité) ne sert
                    # que la qualité « bonne » — le défaut « mediocre » (et le
                    # « bon » du modèle) était REFUSÉ 3× et le repas
                    # n'arrivait jamais. Sur refus d'indisponibilité, reprends
                    # la qualité PROPOSÉE la plus proche du rang demandé.
                    if _tr_ach is not None and "indisponible" in (
                            _tr_ach.text or ""):
                        _m_prop = re.search(
                            r"proposé\s*:\s*\[([^\]]+)\]",
                            _tr_ach.text or "")
                        if _m_prop:
                            _proposes = [
                                _q.strip(" '\"") for _q in
                                _m_prop.group(1).split(",") if _q.strip()
                            ]
                            _rang = {"mediocre": 0, "convenable": 1,
                                     "bonne": 2}
                            _rq = _rang.get(
                                _normalise_pour_compare(_achat.get("repas")
                                            or _achat.get("logement") or ""),
                                1)
                            _choix = min(
                                _proposes,
                                key=lambda _q: abs(
                                    _rang.get(_normalise_pour_compare(_q), 1) - _rq),
                                default="",
                            )
                            if _choix:
                                _cle_q = ("repas" if _achat.get("repas")
                                          else "logement")
                                _args_ach[_cle_q] = _choix
                                _achat["libelle"] = (
                                    _cle_q + " " + _choix
                                    + " (qualité adaptée à la ville)")
                                _tr_ach = await self.execute_tool_direct(
                                    "auberge_commander", _args_ach,
                                    ctx, on_event, result,
                                )
                elif _achat.get("type") == "marche":
                    _tr_ach = await self.execute_tool_direct(
                        "marche_acheter",
                        {"nom": _pj_nom_achat,
                         "article": _achat.get("article", ""),
                         "quantite": int(_achat.get("quantite") or 1)},
                        ctx, on_event, result,
                    )
                elif _achat.get("type") == "vente":
                    # 💰 Partie 2dfa9c75 : la revente crédite l'or du PJ à
                    # 50 % du prix de base (règle officielle) via le tool.
                    _tr_ach = await self.execute_tool_direct(
                        "marche_vendre",
                        {"nom": _pj_nom_achat,
                         "article": _achat.get("article", ""),
                         "quantite": int(_achat.get("quantite") or 1)},
                        ctx, on_event, result,
                    )
                else:
                    _tr_ach = None
                    if not _achat:
                        _log.info(
                            "rattrapage achat : intention sans article "
                            "reconnu (%.120s)", _intention)
                if _tr_ach is not None:
                    result.notes_mecaniques.append(
                        "🪙 Achat appliqué par le serveur ("
                        + str(_achat.get("libelle") or "achat")
                        + ") : " + str(_tr_ach.text)[:220]
                        + " — les prix narrés éventuellement inventés sont "
                        "ignorés au profit des tarifs officiels."
                    )
        except Exception as _e_achat:                        # noqa: BLE001
            _log.warning("rattrapage achat échoué (ignoré) : %s", _e_achat)

        # 🧭 Partie 1808ebab : rattrapage de VOYAGE — « Je me dirige vers le
        # repère de Zendar » narré comme une ARRIVÉE instantanée sans
        # `voyage_demarrer` (aucune journée, aucune rencontre, `voyage` vide).
        # Intention de voyage explicite + aucun tool de déplacement appelé →
        # le serveur lance le voyage (distance par défaut ~1 journée, la
        # destination extraite du message ; le garde de trame est passé avec
        # `forcer` car le choix du joueur est explicite).
        try:
            _voy_msgs = [
                str(_m.content or "") for _m in work
                if getattr(_m, "role", "") == "user"
            ]
            from ..game.state import PartyState  # noqa: E501 pylint: disable=import-outside-toplevel
            from ..tools.voyage import voyage_demarrer as _voy_dem  # noqa: E501 pylint: disable=import-outside-toplevel
            _etat_v = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                max_history=0,
            ).load()
            _dest_voy = None
            if _voy_msgs and _VOYAGE_INTENT_RE.search(_voy_msgs[-1]):
                _dest_voy = _extraire_voyage(_voy_msgs[-1])
                if _dest_voy and _dest_voy.lower() in (
                        "ville", "cité", "cite", "village", "bourg",
                        "la ville"):
                    # Destination vague (« retourne à la ville ») : la ville
                    # OÙ SE TROUVE le groupe (lieu courant, ex. Silverymoon).
                    _dest_voy = str(
                        (_etat_v.get("lieu") or {}).get("nom") or ""
                    ).strip() or _dest_voy
            _tools_depl = {
                tc.get("name") for tc in result.tool_calls_trace
            }
            if (_dest_voy and result.narration.strip()
                    and not (_tools_depl & {
                        "voyage_demarrer", "carte_donjon_entrer",
                        "carte_donjon_explorer", "carte_donjon_sortir"})):
                if not (_etat_v.get("voyage") or {}).get("en_cours"):
                    _tr_voy = await self.execute_tool_direct(
                        "voyage_demarrer",
                        {"destination": _dest_voy, "distance_km": 30,
                         "mode": "marche", "terrain": "route", "piste": True,
                         "forcer": True},
                        ctx, on_event, result,
                    )
                    result.notes_mecaniques.append(
                        "🧭 Voyage appliqué par le serveur (destination « "
                        + _dest_voy + " », ~30 km ≈ 1 journée — "
                        "rencontres et risque de s'égarer officiels) : "
                        + str(_tr_voy.text)[:220]
                        + " Narre le voyage JOUR PAR JOUR d'après ce "
                        "résultat — l'arrivée instantanée était une "
                        "téléportation interdite."
                    )
        except Exception as _e_voy:                          # noqa: BLE001
            _log.warning("rattrapage voyage échoué (ignoré) : %s", _e_voy)

        # 💰 Partie 2dfa9c75 : rattrapage d'OR NARRÉ (« vous trouvez un sac
        # contenant 50 po ») sans AUCUN tool d'or — le trésor n'atteignait
        # jamais la fiche. Crédit déterministe au montant narré (les PRIX
        # — coûte/demande/paye — sont exclus ; l'achat passe par ses tools).
        try:
            _gain_or = _or_gagne_narre(result.narration or "")
            if _gain_or and _gain_or > 0 and not any(
                tc.get("name") in ("marche_acheter", "marche_vendre",
                                   "auberge_commander")
                and "✅" in (tc.get("text") or "")
                for tc in result.tool_calls_trace
            ):
                from ..game.state import PartyState as _PS_or  # noqa: E501 pylint: disable=import-outside-toplevel
                _etat_or = _PS_or(
                    data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                    max_history=0,
                ).load()
                _pj_or = next(
                    (str(_p.get("nom"))
                     for _p in (_etat_or.get("pj") or []) if _p.get("nom")),
                    None,
                )
                if _pj_or:
                    from ..tools.fiches import _load_fiche as _lf, _save_fiche as _sf  # noqa: E501 pylint: disable=import-outside-toplevel
                    _f_or = _lf(ctx, _pj_or)
                    if _f_or is not None:
                        _avant_or = int(_f_or.get("or", 0) or 0)
                        _f_or["or"] = _avant_or + _gain_or
                        _sf(ctx, _pj_or, _f_or)
                        result.notes_mecaniques.append(
                            "💰 Or narré crédité par le serveur : +"
                            + str(_gain_or) + " po à " + _pj_or
                            + " (total " + str(_f_or["or"]) + " po)."
                        )
                        _log.info(
                            "or narré crédité : +%d po à %s", _gain_or,
                            _pj_or)
        except Exception as _e_or:                           # noqa: BLE001
            _log.warning("rattrapage or narré échoué (ignoré) : %s", _e_or)

        # 🎁 Partie 2dfa9c75 : rattrapage du BUTIN NON-QUÊTE — « vous
        # trouvez une épée longue » dans la salle dont le `tresor` du
        # manifeste mentionne cet objet, sans tool → `inventaire_ajouter(
        # portee="auto")` (les verrous anti-triche s'appliquent aussi).
        try:
            _etat_butin = None
            _objets_inventaire_trace = any(
                tc.get("name") in ("inventaire_ramasser", "inventaire_ajouter")
                and "✅" in (tc.get("text") or "")
                for tc in result.tool_calls_trace
            )
            if (not _objets_inventaire_trace
                    and (result.narration or "").strip()
                    and _BUTIN_VERBE_RE.search(result.narration)):
                from ..game.state import PartyState as _PS_but  # noqa: E501 pylint: disable=import-outside-toplevel
                _etat_butin = _PS_but(
                    data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                    max_history=0,
                ).load()
                _pj_but = next(
                    (str(_p.get("nom"))
                     for _p in (_etat_butin.get("pj") or []) if _p.get("nom")),
                    None,
                )
                _objet_butin = _butin_salle_courante(
                    result.narration, _etat_butin or {})
                if _pj_but and _objet_butin:
                    _tr_but = await self.execute_tool_direct(
                        "inventaire_ajouter",
                        {"nom": _pj_but, "objet": _objet_butin,
                         "portee": "auto"},
                        ctx, on_event, result,
                    )
                    if _tr_but is not None and "✅" in (_tr_but.text or ""):
                        result.notes_mecaniques.append(
                            "🎁 Butin ajouté par le serveur (trésor "
                            "canonique de la salle) : " + _objet_butin + "."
                        )
                        _log.info(
                            "butin de salle ajouté : %s à %s", _objet_butin,
                            _pj_but)
        except Exception as _e_but:                          # noqa: BLE001
            _log.warning("rattrapage butin échoué (ignoré) : %s", _e_but)

        # 🎁 Rattrapage DÉTERMINISTE de la remise d'objets de quête (partie
        # e55cc855) : la remise était narrée (« range la fiole de vérité… dans
        # son sac ») sans JAMAIS appeler `inventaire_ajouter` — l'objet
        # n'atteignait pas l'inventaire (le joueur ne pouvait pas l'utiliser)
        # et les rejeux correctifs n'y changeaient rien (le modèle ré-échoit
        # la remise sans appeler l'outil). Comme pour les sorts narrés sans
        # tool (rattrapage déterministe des règles), le serveur applique
        # l'ajout : les objets de quête nommés dans une phrase de
        # remise/rangement sont ajoutés à l'inventaire de quête du PJ
        # (portée "quete" forcée — inventaire_ajouter gère le poids léger par
        # défaut et la déduplication).
        if result.narration.strip() and not any(
            tc.get("name") in ("inventaire_ajouter", "inventaire_ramasser")
            and tc.get("ok")
            for tc in result.tool_calls_trace
        ):
            _objets_remis = _objets_remettes_narration(result.narration)
            if _objets_remis:
                _pj_nom = None
                try:
                    from ..game.state import PartyState
                    _etat_r = PartyState(
                        data_dir=str(ctx.data_dir),
                        partie_id=ctx.partie_id, max_history=0,
                    ).load()
                    _pj_nom = next(
                        (str(p.get("nom"))
                         for p in (_etat_r.get("pj") or []) if p.get("nom")),
                        None,
                    )
                except Exception:                            # noqa: BLE001
                    _pj_nom = None
                if _pj_nom:
                    _ajoutes: list[str] = []
                    _refus: list[str] = []
                    for _objet in _objets_remis:
                        try:
                            _tr_r = await self.execute_tool_direct(
                                "inventaire_ajouter",
                                {"nom": _pj_nom, "objet": _objet,
                                 "portee": "quete"},
                                ctx, on_event, result,
                            )
                            _txt_r = str(
                                (_tr_r.text if _tr_r else "") or "")
                            # 🔧 Partie 82a77cbe : un REFUS (🚫 anti-triche,
                            # étape à venir, ❌…) ne doit pas être annoncé
                            # comme un ajout — la note affirmait « couronne
                            # ajoutée » dès l'intro. Seul le ✅ compte.
                            if "✅" in _txt_r[:20] and "DÉJÀ" not in \
                                    _txt_r[:80]:
                                _ajoutes.append(_objet)
                            elif _txt_r:
                                _refus.append(_objet + " — "
                                              + _txt_r[:80].strip())
                        except Exception:                    # noqa: BLE001
                            continue
                    for _r in _refus:
                        _log.info(
                            "rattrapage remise : refus serveur pour %s", _r)
                    if _ajoutes:
                        _log.info(
                            "rattrapage remise de quête : %s ajouté(s) à %s",
                            ", ".join(_ajoutes), _pj_nom,
                        )
                        result.notes_mecaniques.append(
                            "🎁 Objets de quête ajoutés par le serveur "
                            "(remise narrée sans appel d'outil) : "
                            + ", ".join(_ajoutes) + "."
                        )

        return result

    # ------------------------------------------------------------------ #
    async def _force_final_narration(
        self,
        work: list[Message],
        on_delta: Optional[Callable[[str], Awaitable[None]]],
        message: str = "",
    ) -> str:
        """Dernier appel SANS tools pour forcer une narration clôturante.
        `message` : consigne de remplacement (garde « intro trop courte »)."""
        fallback_msg = Message(
            role="system",
            content=(
                message
                or (
                "Tu as épuisé tes tours d'appels d'outils. Synthétise "
                "maintenant une réponse de narration complète au joueur "
                "en t'appuyant sur les résultats des tools ci-dessus. "
                "N'invoque plus aucun tool — raconte la suite au joueur."
                # Partie b59b4a9a : la narration de fallback écrivait
                # « Margoth… elle » (PJ masculin) — rappel d'identité +
                # interdit d'inventer des nombres hors résultats officiels.
                + " 🧍 RESPECTE l'identité des PJ du récapitulatif : nom, "
                "race, classe et surtout GENRE → pronoms et accords "
                "(un PJ « MASCULIN » est « il », JAMAIS « elle »). "
                "N'invente AUCUN nombre : reprends ceux des résultats "
                "officiels ci-dessus."
                )
                + _CORRECTIF_INTERNE
            ),
        )
        final_work = _borner_work(work) + [fallback_msg]
        try:
            if on_delta:
                collected = ""
                pending = ""
                async for token in self.client.stream_chat(final_work, tools=None):
                    collected += token
                    pending += token
                    safe, pending = _safe_stream_split(pending)
                    if safe:
                        # strip_spaces=False : cf. commentaire du flux
                        # streaming principal — préserve espaces/retours
                        # à la ligne entre les deltas.
                        safe = _strip_thinking(safe, strip_spaces=False)
                        for _tok in _GEM_QUOTE_TOKENS:
                            if _tok in safe:
                                safe = safe.replace(_tok, "")
                        safe = _TOOLCALL_ORPHAN_CLOSE_RE.sub("", safe)
                        if safe:
                            await on_delta(safe)
                if pending.strip():
                    await on_delta(
                        _tidy_empty_lines(
                            _TOOLCALL_ORPHAN_CLOSE_RE.sub(
                                "", _strip_thinking(pending)
                            )
                        ).strip()
                    )
                narration = collected
            else:
                fb = await self.client.chat(final_work, tools=None)
                narration = fb.content
            return _strip_thinking(
                strip_narration_artifacts(narration, self.tools)
            ).strip()
        except Exception as e:                                   # noqa: BLE001
            _log.warning("narration fallback échoué : %s", e)
            return ""

    # ------------------------------------------------------------------ #
    @staticmethod
    def _intention_escalier(work: list[Message]) -> Optional[str]:
        """Détecte dans le DERNIER message joueur une intention « monter »
        ou « descendre » un escalier. Renvoie "monter" | "descendre" | None
        (None si absent ou ambigu — les deux directions détectées).

        Les consignes correctives du serveur (messages `user` marqués
        « instruction INTERNE du moteur de jeu ») ne sont PAS un message
        joueur : une consigne citant « descendre » ne doit jamais forcer un
        changement d'étage."""
        dernier_user = ""
        for m in reversed(work):
            if m.role == "user":
                dernier_user = m.content or ""
                break
        if not dernier_user:
            return None
        if "instruction INTERNE du moteur de jeu" in dernier_user:
            return None
        descendre = bool(_INTENT_DESCENDRE_RE.search(dernier_user))
        monter = bool(_INTENT_MONTER_RE.search(dernier_user))
        if descendre and not monter:
            return "descendre"
        if monter and not descendre:
            return "monter"
        return None

    # ------------------------------------------------------------------ #
    async def _groupe_dans_escalier(self, ctx: ToolContext) -> bool:
        """True si le groupe se trouve actuellement (donjon.etage actif,
        position `donjon.courant`) dans une salle de type « escaliers »."""
        try:
            from ..tools.cartes import _TYPES_ESCALIER, _grille_vers_dict
            etat = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
            ).load()
            donjon = etat.get("donjon") or {}
            if not donjon.get("id"):
                return False
            courant = list(donjon.get("courant", [0, 0]))
            cx, cy = int(courant[0]), int(courant[1])
            salles = _grille_vers_dict(donjon.get("grille", []))
            cour = salles.get((cx, cy)) or {}
            return (cour.get("type") or "").strip().lower() in _TYPES_ESCALIER
        except Exception:                                    # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    async def _garder_monstres_serveur(
        self,
        resolved: str,
        args: dict[str, Any],
        ctx: ToolContext,
        result: OrchestratedResult,
    ) -> Optional[str]:
        """Garde-fou « le serveur joue les monstres » (indépendant du modèle).

        Dès qu'un combat est engagé (`engager_combat`) ou qu'un tour PJ est
        terminé (`terminer_mon_tour`), `combat.boucle_auto` joue
        SYNCHRONIQUEMENT les tours de monstres avec les attaques officielles
        du bestiaire. Si le modèle rejoue ces attaques lui-même
        (`lancer_attaque` d'un monstre, dégâts à un PJ), elles sont
        appliquées DEUX FOIS :
        - partie 54de40ed : BBB 9→6→4 PV par le serveur, puis -3 par le
          modèle sur la même frappe → 1 PV, lu comme « les dégâts me
          soignent » ;
        - partie ce0c9dd1 : le tour 1 commence par la Goule (init. 19) —
          7→1 PV par le serveur, puis -6 rejoués par le modèle → -5, mourant
          au tour 1.
        On refuse ces appels : le modèle doit narreR la transition SANS
        mécanique et attendre les résultats officiels déjà injectés.
        """
        # C2 : DOUBLE application sur monstre suivi — indépendante du garde
        # « serveur joue les monstres » (le double se produit en plein tour
        # PJ, sans terminer_mon_tour/engager_combat dans la même trace).
        if resolved == "fiche_perso_infliger_degats":
            nom_c2 = str(args.get("nom") or "").strip()
            if nom_c2:
                try:
                    _etat_c2 = PartyState(
                        data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
                    ).load()
                    _suivis_c2 = {
                        _norm_nom_outil(m.get("nom"))
                        for m in (_etat_c2.get("monstres_combat") or [])
                        if isinstance(m, dict)
                        and "Détruit" not in (m.get("conditions") or [])
                        and "Detruit" not in (m.get("conditions") or [])
                        and int(m.get("pv", 0) or 0) > 0
                    }
                except Exception:                             # noqa: BLE001
                    _suivis_c2 = set()
                _nc2 = _norm_nom_outil(nom_c2)
                if (
                    _nc2 in _suivis_c2
                    and any(
                        tc.get("name") == "lancer_degats" and tc.get("ok")
                        and _norm_nom_outil((tc.get("args") or {}).get("cible"))
                        == _nc2
                        for tc in result.tool_calls_trace
                    )
                ):
                    return (
                        "⛔ Les dégâts de cette frappe sont DÉJÀ appliqués "
                        "par le serveur : chaque `lancer_degats` réussi sur "
                        "un ennemi suivi est appliqué automatiquement au "
                        "moment du jet. N'appelez PAS "
                        "`fiche_perso_infliger_degats` sur un ennemi dont "
                        "les dégâts ont déjà été jetés/auto-appliqués ce "
                        "tour — cela les compterait DEUX FOIS. Narrez le "
                        "résultat tel qu'il est affiché dans la sortie "
                        "officielle du `lancer_degats`."
                    )
        if not any(
            tc.get("name") in ("terminer_mon_tour", "engager_combat")
            and tc.get("ok")
            for tc in result.tool_calls_trace
        ):
            return None
        try:
            etat = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
            ).load()
            pjs = {
                str(p.get("nom") or "").strip().lower()
                for p in (etat.get("pj") or []) if isinstance(p, dict)
            }
        except Exception:                                    # noqa: BLE001
            return None
        refus = (
            "⛔ Le serveur joue DÉJÀ les tours de monstres automatiquement "
            "(résultats officiels injectés ci-dessus). N'émettez AUCUNE "
            "attaque de monstre ni dégât à un personnage : ils seraient "
            "appliqués une deuxième fois. Narrez la transition SANS aucun "
            "appel de mécanique, en vous appuyant sur les résultats "
            "officiels."
        )
        if resolved == "lancer_attaque":
            attaquant = str(args.get("nom_attaquant") or "").strip().lower()
            if attaquant and attaquant not in pjs:
                return refus
        elif resolved == "lancer_degats":
            cible = str(args.get("cible") or "").strip().lower()
            if cible and cible in pjs:
                return refus
        elif resolved == "fiche_perso_infliger_degats":
            nom = str(args.get("nom") or "").strip().lower()
            if nom and nom in pjs:
                return refus
        return None

    async def _rediriger_escalier(
        self,
        resolved: str,
        args: dict[str, Any],
        ctx: ToolContext,
        work: list[Message],
    ) -> tuple[str, dict[str, Any], Optional[str]]:
        """Garde-fou « escalier » (indépendant du modèle) : si le groupe est
        dans une salle escaliers, que le joueur demande monter/descendre et
        que le modèle appelle `carte_donjon_explorer` (confusion « descendre
        l'escalier » ↔ « aller au sud »), l'appel est réécrit en
        `carte_donjon_etage(direction=…)` AVANT exécution. Renvoie
        (nom_résolu, args, note_système éventuelle)."""
        if resolved != "carte_donjon_explorer":
            return resolved, args, None
        intention = self._intention_escalier(work)
        if not intention:
            return resolved, args, None
        if not await self._groupe_dans_escalier(ctx):
            return resolved, args, None
        _log.warning(
            "confusion escalier : carte_donjon_explorer(%s) appelé depuis "
            "une salle escaliers avec intention joueur « %s » → réécrit "
            "en carte_donjon_etage(direction=%s)",
            args.get("direction"), intention, intention,
        )
        note = (
            "ℹ️ SYSTÈME : ton appel `carte_donjon_explorer` a été RÉÉCRIT "
            f"en `carte_donjon_etage(direction=\"{intention}\")` — le "
            f"joueur voulait {intention} l'escalier, PAS se déplacer dans "
            "une direction cardinale. Narre le CHANGEMENT D'ÉTAGE d'après "
            "le résultat officiel ci-dessous."
        )
        return "carte_donjon_etage", {"direction": intention}, note

    # ------------------------------------------------------------------ #
    async def _exec_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        ctx: ToolContext,
        work: list[Message],
        result: OrchestratedResult,
        on_event: Optional[EventCallback],
    ) -> None:
        """Exécute un lot de tool_calls OpenAI natifs (peut être parallèle)"""
        for call in tool_calls:
            fn = call.get("function", call)
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            # arguments peut être une string JSON ou un dict
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError as e:
                    args = {}
                    self._reply_tool_error(work, name, call.get("id"), f"❌ JSON d'args invalide : {e}")
                    continue
            else:
                args = raw_args or {}

            # Résolution floue du nom (le LLM écrit parfois « Lancer_d20 »).
            resolved = resolve_tool_name(name, self.tools)
            if not resolved:
                self._reply_tool_error(
                    work, name, call.get("id"),
                    f"❌ Tool '{name}' inconnu. Tools disponibles : "
                    + ", ".join(sorted(self.tools.keys())),
                )
                continue
            resolved, args, note_esc = await self._rediriger_escalier(
                resolved, args, ctx, work,
            )
            refus_monstres = await self._garder_monstres_serveur(
                resolved, args, ctx, result,
            )
            if refus_monstres:
                self._reply_tool_error(
                    work, name, call.get("id"), refus_monstres,
                )
                continue
            spec = self.tools[resolved]
            args, notes = sanitize_tool_args(spec, args)
            tr = await self._run_one_tool(spec, ctx, args, on_event, result)
            extra = f"\nℹ️ {'; '.join(notes)}" if notes else ""
            if note_esc:
                extra = f"\n{note_esc}" + extra
            work.append(Message(
                role="tool",
                name=resolved,
                tool_call_id=call.get("id") or resolved,
                content=self._cap_tool_text(tr.text + extra),
            ))

    async def _exec_tool_calls_prompt(
        self,
        calls: list[dict[str, Any]],
        ctx: ToolContext,
        work: list[Message],
        result: OrchestratedResult,
        on_event: Optional[EventCallback],
    ) -> None:
        """Exécute les tool calls trouvés par parsing prompt-based."""
        # 🚶 Partie 15aa0b6f : UN SEUL DÉPLACEMENT par tour — le modèle avait
        # enchaîné `voyage_demarrer` (2 jours) PUIS `carte_donjon_entrer`
        # dans la même seconde : départ, journées, arrivée et entrée du
        # donjon comprimés en un tour incohérent. Un déplacement réussi ce
        # tour bloque les suivants (note au modèle, tour terminé ensuite).
        _depl_deja = any(
            tc.get("name") in _OUTILS_DEPLACEMENT and tc.get("ok")
            for tc in result.tool_calls_trace
        )
        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            resolved = resolve_tool_name(name, self.tools)
            if resolved in _OUTILS_DEPLACEMENT and _depl_deja:
                work.append(Message(
                    role="tool",
                    name=name,
                    content=(
                        "⚠️ **UN SEUL DÉPLACEMENT par tour** : un "
                        "déplacement a déjà réussi ce tour — il a eu lieu, "
                        "l'état est à jour. Narre-le (journée par journée "
                        "pour un voyage) et ATTENDS le choix du joueur "
                        "avant tout autre déplacement."
                    ),
                ))
                continue
            # Résolution floue du nom (prose : casse/accents/alias courts).
            resolved = resolve_tool_name(name, self.tools)
            if not resolved:
                work.append(Message(
                    role="tool",
                    name=name,
                    content=(
                        f"❌ Tool '{name}' inconnu. Tools disponibles : "
                        + ", ".join(sorted(self.tools.keys()))
                    ),
                ))
                continue
            resolved, args, note_esc = await self._rediriger_escalier(
                resolved, args, ctx, work,
            )
            refus_monstres = await self._garder_monstres_serveur(
                resolved, args, ctx, result,
            )
            if refus_monstres:
                work.append(Message(
                    role="tool",
                    name=resolved,
                    content=refus_monstres,
                ))
                continue
            spec = self.tools[resolved]
            args, notes = sanitize_tool_args(spec, args)
            tr = await self._run_one_tool(spec, ctx, args, on_event, result)
            extra = f"\nℹ️ {'; '.join(notes)}" if notes else ""
            if note_esc:
                extra = f"\n{note_esc}" + extra
            work.append(Message(
                role="tool",
                name=resolved,
                content=self._cap_tool_text(tr.text + extra),
            ))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _cap_tool_text(text: str, limit: int = 4000) -> str:
        """Tronque un résultat de tool volumineux avant réinjection dans le
        contexte LLM. Un texte intégral de PDF (24k chars ≈ 10k tokens) sature
        num_ctx et fait échouer chat/completions (400 llama.cpp). Le LLM n'a
        besoin que de l'essentiel pour agir ; la trace complète reste visible
        dans les logs. 4000 chars ≈ 1200 tokens : plusieurs résultats tiennent
        dans le tour même au plafond (contexte 20224, mesuré 44b02cfc)."""
        if len(text) <= limit:
            return text
        return text[:limit] + "\n…[résultat tronqué pour préserver le contexte]"

    # ------------------------------------------------------------------ #
    def _reply_tool_error(
        self,
        work: list[Message],
        name: str,
        call_id: Optional[str],
        err: str,
    ) -> None:
        work.append(Message(role="tool", name=name, tool_call_id=call_id or name, content=err))

    # ------------------------------------------------------------------ #
    async def _run_one_tool(
        self,
        spec: ToolSpec,
        ctx: ToolContext,
        args: dict[str, Any],
        on_event: Optional[EventCallback],
        result: OrchestratedResult,
        hors_budget: bool = False,
    ) -> ToolResult:
        """Exécute un tool, relaye ses events, agrège le patch d'état."""
        # Nettoyage de la contamination XML→JSON des arguments (Qwen3.5-9B).
        args = _nettoyer_args_outils(args or {})
        # 💰 Budget par tour : borne le spam d'outils observé en e2e
        # (17-32 appels `fiche_perso_mettre_a_jour` dans un même tour = des
        # minutes perdues et un contexte saturé). Au-delà du quota, l'outil
        # n'est PAS exécuté : le modèle reçoit un refus et doit narrer.
        # Les rattrapages SERVEUR (`execute_tool_direct`) passent hors budget.
        if not hors_budget:
            quota = _BUDGET_OUTILS_TOUR.get(spec.name, _BUDGET_DEFAUT)
            # Seuls les SUCCÈS consomment le quota : après un appel en
            # erreur le modèle doit pouvoir REESSAYER (observé en e2e :
            # 1er creer_rapide en erreur → retry bloqué → fiche jamais créée).
            deja = sum(
                1 for tc in result.tool_calls_trace
                if tc.get("name") == spec.name and tc.get("ok")
            )
            if deja >= quota:
                result.tool_calls_trace.append({
                    "name": spec.name,
                    "args": args,
                    "ok": False,
                    "text": "🚫 budget tour atteint",
                })
                result.refus_budget += 1
                _log.warning(
                    "budget tour atteint pour %s (%d/%d) — refus",
                    spec.name, deja, quota,
                )
                return ToolResult(text=(
                    f"🚫 **LIMITE ATTEINTE** : `{spec.name}` a déjà été "
                    f"appelé {deja}× ce tour (quota : {quota}). N'appelle "
                    "PLUS cet outil — exploite les résultats déjà obtenus "
                    "ci-dessus et produis ta narration finale pour le "
                    "joueur."
                ))
        # On attache le callback temps-réel au ctx pour que les tools puissent
        # émettre des events en live (ex : « ⏳ Génération image en cours »).
        ctx.on_event = on_event
        args_log = json.dumps(args, ensure_ascii=False, default=str)[:200]
        _log.info("tool_call name=%s args=%s", spec.name, args_log)
        # 📊 Statut enrichi : l'outil en cours d'application, affiché au
        # joueur pendant la réflexion (« Résout l'attaque… »).
        _on_status = getattr(ctx, "on_status", None)
        if _on_status is not None:
            try:
                await _on_status(
                    _STATUT_OUTILS.get(spec.name,
                                       f"Applique {spec.name}…"))
            except Exception:                                # noqa: BLE001
                pass
        tr = await invoke_tool(spec, ctx, args)
        # ok = succès : ni message d'erreur ❌, ni REFUS de verrou ⛔ (les refus
        # ⛔ du verrou d'incarnation déclenchent une RETENTATIVE du LLM — on ne
        # doit PAS les compter comme succès : le budget `creer_rapide` est 1 et
        # un refus bloquerait la création du perso du joueur courant, observé
        # en e2e : Zarkon ne pouvait jamais se créer car le tour gaspillait son
        # seul appel à recréer Groth (⛔), consommé comme un succès).
        ok = not (tr.text.startswith("❌") or tr.text.startswith("⛔"))

        # --- Rattrapage « fiche absente » -------------------------------
        # Le LLM boucle sur `fiche_perso_mettre_a_jour` / `fiche_perso_recuperer`
        # pour un personnage qui n'a JAMAIS été créé : chaque erreur le relance
        # sur le même outil (observé avec Qwen3.5-9B-Q5 : 8-12 appels identiques,
        # tour figé, fiche jamais créée). Après N échecs pour le MÊME nom, on
        # CRÉE automatiquement la fiche (caractéristiques tirées) pour débloquer
        # le tour), puis on rejoue l'appel d'origine (la mise à jour réussit).
        if (not hors_budget and not ok and spec.name in _TOOLS_FICHE_EXIGEANTE
                and "Aucune fiche trouvée pour '" in tr.text):
            nom_m = _nom_fiche_absente(tr.text)
            # `_compte_fiches_absentes` lit les échecs DÉJÀ tracés (l'appel
            # courant n'est ajouté qu'après) : on déclenche dès `MIN - 1`
            # échecs antérieurs pour le même nom (= MIN échecs au total).
            if nom_m and self._compte_fiches_absentes(
                    result, nom_m) >= _RATTRAPAGE_FICHE_ABSENTE_MIN - 1:
                _log.warning(
                    "rattrapage fiche absente : création auto de '%s' "
                    "(%d échecs)", nom_m,
                    self._compte_fiches_absentes(result, nom_m))
                if await self._rattraper_fiche_absente(ctx, nom_m, on_event):
                    tr2 = await self._run_one_tool(
                        spec, ctx, args, on_event, result, hors_budget=True)
                    tr = ToolResult(
                        text=(
                            f"ℹ️ **Rattrapage serveur** : « {nom_m} » n'avait "
                            "aucune fiche — le serveur vient d'en créer une "
                            "(caractéristiques tirées au hasard). Complète-le "
                            "si besoin, puis poursuis.\n" + tr2.text
                        ),
                        events=tr2.events + tr.events,
                        state_patch=tr2.state_patch or tr.state_patch,
                    )
                    ok = not tr2.text.startswith("❌")

        result.tool_calls_trace.append({
            "name": spec.name,
            "args": args,
            "ok": ok,
            # 🔧 Bêta : budget relevé 300 → 900 chars — les traces d'attaque
            # (en-tête + jets + bonus recalculé + verdict « ✅ Touché » en FIN
            # de texte) étaient TRONQUÉES avant le verdict : le rattrapage
            # « touché sans dégâts » et le bloc « 🎲 Jets officiels » ne
            # voyaient jamais l'issue du jet (dégâts jamais appliqués, partie
            # réelle : attaque touchée restée sans effet sur le monstre).
            "text": tr.text[:900],
        })
        _log.info("tool_call done name=%s ok=%s text=%s",
                  spec.name, ok, tr.text[:150].replace("\n", " "))
        for ev in tr.events:
            result.tool_events.append(ev)
            if on_event:
                try:
                    await on_event(ev)
                except Exception:
                    pass
        if tr.state_patch:
            result.state_patches.append(tr.state_patch)
            # (a) Push immédiat du patch (PV, phase, initiative…) : la barre de
            # vie bouge à l'écran DÈS l'exécution du tool, sans attendre le dm
            # final — le post-traitement (corrections, rejeus, images) peut
            # retarder ce dernier de plusieurs dizaines de secondes.
            if on_event:
                try:
                    await on_event({
                        "type": "state_patches",
                        "patches": [tr.state_patch],
                    })
                except Exception:                             # noqa: BLE001
                    pass
        # (d) ⚔️ Auto-application des dégâts sur un ennemi suivi : un
        # `lancer_degats` réussi dont la cible figure dans `monstres_combat`
        # est appliqué IMMÉDIATEMENT par le serveur. Avant (partie dfccc120),
        # l'application dépendait que le modèle pense à rappeler
        # `fiche_perso_infliger_degats` — il l'oubliait, les 6 dégâts jetés
        # restaient sans effet (Ombre 19/19 PV malgré un touché 23 vs CA 13).
        # La trace enregistre ensuite l'appel `fiche_perso_infliger_degats`
        # effectué ici : la file anti-double-application de
        # `_appliquer_degats_oublies` et le dédoublonnage
        # `_exces_degats_monstres` restent EXACTS (appliqué == jeté).
        if spec.name == "lancer_degats" and ok:
            await self._auto_appliquer_degats(args, tr, ctx, result, on_event)
        return tr

    async def _auto_appliquer_degats(
        self,
        args: dict[str, Any],
        tr: ToolResult,
        ctx: ToolContext,
        result: OrchestratedResult,
        on_event: Optional[EventCallback],
    ) -> None:
        """Applique immédiatement les dégâts d'un `lancer_degats` réussi
        quand la cible est un monstre SUIVI (etat.monstres_combat). Idempotent
        avec les garde-fous existants : l'appel `fiche_perso_infliger_degats`
        généré consomme l'orphelin dans la file du rattrapage et équilibre
        appliqué/jeté pour le dédoublonnage."""
        cible = str((args or {}).get("cible") or "").strip()
        if not cible:
            return
        m_total = re.search(r"[Dd]égâts infligés\s*:\s*(\d+)", tr.text or "")
        if not m_total:
            return
        total = int(m_total.group(1))
        if total <= 0:
            return
        # Uniquement les monstres SUIVIS et vivants : une cible hors combat
        # (PJ narratif, monstre absent de l'état) reste un jet sans effet.
        import unicodedata as _uni

        def _nn(s: Any) -> str:
            n = _uni.normalize("NFKD", str(s or "").strip().lower())
            return "".join(c for c in n if not _uni.combining(c))

        try:
            etat = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
            ).load()
        except Exception:                                    # noqa: BLE001
            return
        cn = _nn(cible)
        suivi = any(
            _nn(mo.get("nom")) == cn
            and int(mo.get("pv", 0) or 0) > 0
            and "Détruit" not in (mo.get("conditions") or [])
            and "Detruit" not in (mo.get("conditions") or [])
            for mo in (etat.get("monstres_combat") or [])
        )
        if not suivi:
            return
        tr_inf = await self.execute_tool_direct(
            "fiche_perso_infliger_degats",
            {"nom": cible, "degats": total},
            ctx, on_event, result,
        )
        if tr_inf is None:
            return
        result.notes_mecaniques.append(tr_inf.text)
        _log.info(
            "auto-application des dégâts : %d → %s (lancer_degats réussi)",
            total, cible,
        )

    # ------------------------------------------------------------------ #
    def _compte_fiches_absentes(self, result: OrchestratedResult, nom: str) -> int:
        """Échecs « Aucune fiche trouvée » déjà tracés pour `nom` dans le tour."""
        return sum(
            1 for tc in result.tool_calls_trace
            if not tc.get("ok")
            and f"Aucune fiche trouvée pour '{nom}'" in (tc.get("text") or "")
        )

    async def _rattraper_fiche_absente(
        self, ctx: ToolContext, nom: str, on_event: Optional[EventCallback],
    ) -> bool:
        """Crée la fiche manquante (caractéristiques tirées) via
        `fiche_perso_creer_rapide`, hors budget et sans trace polluante."""
        try:
            spec_creer = self.tools.get("fiche_perso_creer_rapide")
            if spec_creer is None:
                return False
            tr = await self._run_one_tool(
                spec_creer, ctx,
                {"nom": nom, "carac_texte": "", "joueur": ""},
                on_event, OrchestratedResult(), hors_budget=True,
            )
            return tr.text.startswith("✅")
        except Exception:                                        # noqa: BLE001
            return False

    async def execute_tool_direct(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
        on_event: Optional[EventCallback] = None,
        result: Optional[OrchestratedResult] = None,
    ) -> Optional[ToolResult]:
        """Exécute un tool par nom (résolution floue incluse) avec le
        bookkeeping commun (trace, events, patches). Sert aux rattrapages
        serveur déterministes — ex. l'attaque automatique des monstres quand
        le LLM n'a pas joué leur tour. Renvoie None si le nom est inconnu."""
        resolved = resolve_tool_name(name, self.tools)
        if resolved is None:
            return None
        spec = self.tools[resolved]
        result = result or OrchestratedResult()
        # Rattrapage SERVEUR : hors budget (déterministe, jamais du spam LLM).
        return await self._run_one_tool(
            spec, ctx, args, on_event, result, hors_budget=True,
        )
