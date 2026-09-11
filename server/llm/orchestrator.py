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
        "incanter_sort",
        "combat_ajouter_combattant",
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


_log = logging.getLogger("dnd35.orchestrator")

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
    # On capture le pattern `NdM+mod = résultat` et `Jet d'X : ... = N`.
    re.compile(
        r"\b(?:jet\s+(?:d['']attaque|de\s+d[ée]g[âa]ts|de\s+sauvegarde)\s*[:\-]\s*)?"
        r"\d+d\d+(?:\s*[+\-]\s*\d+)?\s*[:=]\s*\d{1,3}\b",
        re.IGNORECASE,
    ),
    # "Jet d'attaque : 17" (sans formule NdM, juste le résultat numérique après
    # un label explicite — Survient quand le DM néglige d'appeler lancer_attaque).
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
]


def looks_like_simulation(
    text: str,
    include_damage: bool = True,
    include_checks: bool = True,
    include_creation: bool = True,
) -> Optional[str]:
    """Renvoie le fragment de simulation trouvé, ou None.

    `include_damage=False` désactive les patterns de dégâts en prose — utilisé
    quand un tool de dés a déjà tourné dans le tour : la reformulation du
    résultat ("La créature subit 7 dégâts") est alors légitime.
    `include_checks=False` désactive pareillement les patterns de jets de
    caractéristique/compétence en prose (« jet de Force pour… »).
    `include_creation=False` désactive la détection des « ✅ Fiche créée… »
    narrés — utilisé quand un outil d'écriture de fiche a réellement tourné.
    """
    if not text:
        return None
    pats: list[re.Pattern[str]] = list(_SIMULATION_PATTERNS)
    if include_damage:
        pats += _DAMAGE_PROSE_PATTERNS
    if include_checks:
        pats += _CHECK_PROSE_PATTERNS
    if include_creation:
        pats += _FICHE_CREATION_PATTERNS
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


def trouve_repetition(
    narration: str,
    historique: list["Message"],
    seuil: float = _REPET_SEUIL_CHEVAUCHEMENT,
) -> Optional[str]:
    """Renvoie un extrait de la narration précédente que `narration` répète,
    ou None si la narration est nouvelle.

    Deux critères (le premier atteint suffit) :
    - le préfixe normalisé de la narration apparaît tel quel dans un message
      assistant récent (copie quasi verbatim) ;
    - chevauchement des bigrammes fenêtrés de mots ≥ `seuil` (paraphrase qui
      reprend la scène, même en comprimant/réordonnant ; les narrations
      inédites restent ≪ seuil).
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
    for ancien in reversed(assistant_recents):
        ref = _normalise_pour_compare(ancien)
        if len(ref) < _REPET_MIN_CANDIDAT:
            continue
        if prefixe and prefixe in ref:
            return ref[:120]
        bigrams_ref = _bigrammes_fenetres(ref.split())
        if bigrams_cand and bigrams_ref:
            overlap = len(bigrams_cand & bigrams_ref) / len(bigrams_cand)
            if overlap >= seuil:
                return ref[:120]
    return None


# --------------------------------------------------------------------------- #
#  Dégénérescence INTRA-réponse : le modèle boucle sur ses propres phrases
#  (observé en e2e : « la grande hache de groth s'abat… » recopiée N fois,
#  réponses de plusieurs milliers de tokens qui épuisent le budget du tour).
#  On tronque dès la 3e occurrence d'une même phrase — les relances voient
#  alors un contexte propre au lieu d'un monstre de texte dégénéré.
# --------------------------------------------------------------------------- #
_DEGEN_MIN_MOTS = 6          # phrase significative (pas « Et puis. »)
_DEGEN_OCCURRENCES = 3       # seuil de boucle


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
        if len(cle.split()) < _DEGEN_MIN_MOTS:
            continue
        compteur[cle] = compteur.get(cle, 0) + 1
        if compteur[cle] >= _DEGEN_OCCURRENCES:
            # Garde le texte jusqu'AVANT la 3e occurrence de la phrase.
            coupe = " ".join(x.strip() for x in phrases[:i]).strip()
            _log.warning(
                "dégénérescence intra-réponse détectée (« %s… » ×%d) — "
                "troncature %d → %d chars",
                cle[:60], compteur[cle], len(texte), len(coupe),
            )
            return coupe, cle[:80]
    return texte, ""


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
    for pat in _PROSE_PLACEHOLDER_RES:
        out = pat.sub("", out)
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

# 💰 Budget d'appels par OUTIL et par TOUR de joueur : au-delà, `_run_one_tool`
# refuse l'exécution (le modèle bouclait 17-32× sur `fiche_perso_mettre_a_jour`
# ou `inventaire_consulter`, brûlant des minutes et saturant num_ctx).
# Calibré large pour ne gêner aucun tour légitime (multi-attaques, jets groupés).
_BUDGET_OUTILS_TOUR: dict[str, int] = {
    "fiche_perso_creer_rapide": 1,   # un seul personnage par tour
    "fiche_perso_creer": 1,
    "fiche_perso_mettre_a_jour": 4,
    "fiche_perso_recuperer": 3,
    "inventaire_consulter": 2,
    "inventaire_ajouter": 3,
    "carte_donjon_get": 1,
    "carte_joueurs_get": 2,
    "monstre_consulter": 3,
    "lancer_attaque": 6,
    "lancer_degats": 6,
    "lancer_sauvegarde": 6,
    "lancer_d20": 8,
    "etat_partie_get": 3,
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


class Orchestrator:
    """Pilote un tour complet (request joueur → réponse MJ narrée)."""

    def __init__(
        self,
        client: OllamaClient,
        tools: dict[str, ToolSpec],
        tool_mode: str = "prompt",   # "native" | "prompt" | "auto"
        detect_simulation: bool = True,
        max_iterations: int = 10,
    ):
        self.client = client
        self.tools = tools
        self.tool_mode = tool_mode
        self.detect_simulation = detect_simulation
        self.max_iterations = max_iterations

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
        filtered = {n: s for n, s in all_tools.items() if n in allowed}
        # Garde-fou : si on n'obtient rien (ex. config cassée), on retombe sur
        # l'ensemble complet pour ne jamais brider la discussion.
        return filtered or all_tools

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
        trust_damage_prose: bool = False,
    ) -> OrchestratedResult:
        """Boucle principale : appelle le LLM, exécute les tools, narrate.

        - `messages`   : historique complet (system + tours précédents + nouveau
                         message joueur).
        - `ctx`         : contexte tool (partie_id, joueur, data_dir).
        - `on_event`    : callback async pour events structurés (image, status).
        - `on_delta`    : callback async pour streaming tokens de narration au
                         client (peut être None si on ne stream pas).
        - `trust_damage_prose` : True quand des dégâts viennent d'être résolus
                         côté serveur (pre-run du moteur de combat) et que le
                         LLM les reformule légitimement — désactive la
                         détection « dégâts en prose » pour ce tour.
        """
        result = OrchestratedResult()
        work = list(messages)
        # Filtrage par phase : un modèle 12B gère mal 39 tools fiables. Avant la
        # création de perso, seuls 3 outils de la phase d'ouverture suffisent ;
        # en exploration, seuls les pertinents. En cas de doute, on donne les
        # tools utiles à la phase courante. Le filtrage se détermine par la
        # dernière partie état disponible sur disque via ctx.
        filtered = self._filter_tools_by_phase(self.tools, ctx)
        schemas = tools_schemas_all(filtered)
        tool_section = tools_prompt_section(filtered)

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
            elif self.tool_mode == "auto" and filtered:
                compact = tools_prompt_compact(filtered)
                work[0] = Message(
                    role="system",
                    content=work[0].content + "\n\n" + compact,
                )

        # OpenAI impose : si tools non vides, tool_choice = "auto" sauf si
        # l'on veut forcer un appel. On laisse "auto".
        corrections_vues = -1
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
        try:
            _etat_tour = PartyState(
                data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
            ).load()
            phase_combat = (
                str(_etat_tour.get("phase") or "").strip().lower() == "combat"
            )
        except Exception:                                    # noqa: BLE001
            phase_combat = False
        seuil_repet = 0.75 if phase_combat else _REPET_SEUIL_CHEVAUCHEMENT
        for _ in range(self.max_iterations):
            result.iterations += 1
            use_native = self.tool_mode in ("native", "auto")
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
            chat = await self.client.chat(
                work, tools=tools_arg,
                tool_choice="auto" if use_native else None,
                temperature=temp_relance,
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
                )

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
                sim = looks_like_simulation(
                    chat.content,
                    include_damage=not (damage_rolled or trust_damage_prose),
                    include_checks=not dice_rolled,
                    include_creation=not fiche_ecrite,
                )
                if sim:
                    result.simulation_attempted = True
                    if result.corrections < 2:
                        result.corrections += 1
                        # Correctif ciblé : jet de compétence/caractéristique
                        # ANNONCÉ mais non résolu (« Je lance un jet de
                        # Force… » et le tour s'arrête sans résultat).
                        if any(
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
                sim_final = looks_like_simulation(
                    narration,
                    include_damage=not (damage_rolled or trust_damage_prose),
                    include_checks=not dice_rolled_final,
                    include_creation=not fiche_ecrite_final,
                )
                if sim_final:
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
            if narration.strip() and result.corrections < 3:
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
                if echo:
                    result.corrections += 1
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
                if result.corrections < 3:
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
            result.narration = "\n\n".join(
                [*result.narrations_intermediaires, result.narration]
            ).strip()

        return result

    # ------------------------------------------------------------------ #
    async def _force_final_narration(
        self,
        work: list[Message],
        on_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> str:
        """Dernier appel SANS tools pour forcer une narration clôturante."""
        fallback_msg = Message(
            role="system",
            content=(
                "Tu as épuisé tes tours d'appels d'outils. Synthétise "
                "maintenant une réponse de narration complète au joueur "
                "en t'appuyant sur les résultats des tools ci-dessus. "
                "N'invoque plus aucun tool — raconte la suite au joueur."
                + _CORRECTIF_INTERNE
            ),
        )
        final_work = work + [fallback_msg]
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
        (None si absent ou ambigu — les deux directions détectées)."""
        dernier_user = ""
        for m in reversed(work):
            if m.role == "user":
                dernier_user = m.content or ""
                break
        if not dernier_user:
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
        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
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
            "text": tr.text[:300],
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
        return tr

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
