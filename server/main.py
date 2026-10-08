"""Point d'entrée FastAPI de l'application D&D 3.5 — Maître du Jeu.

Endpoints :
- GET  /                  → frontend statique (chat multijoueur)
- GET  /api/health        → sanity check (ping Ollama)
- GET  /api/parties       → liste les parties
- POST /api/parties       → crée une nouvelle partie (+ état initial)
- GET  /api/parties/{id}  → état persistant d'une partie
- WS   /ws/{partie_id}    → canal chat multijoueur temps réel

Au WS, format de messages reçus :
    {"type": "join", "player": "Alain"}
    {"type": "say", "player": "Alain", "text": "j'ouvre la porte"}
Réponse servant de déclencheur MJ : type=say.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from . import catalogue as catalogue_mod
from . import equipement_phb as equipement_phb_mod
from . import familiers as familiers_mod
from . import gpu as _gpu
from . import persos as persos_mod
from . import sorts as sorts_mod
from . import villes as villes_mod
from .config import AppConfig, get_config, set_config
from .game.session import PartySession, registry as sessions
from .game.state import PartyState, SCHEMA_PARTIE
from .llm.client import Message
from .llm.client import OllamaClient
from .llm.orchestrator import EventCallback, Orchestrator
from .llm.orchestrator import _ENNEMIS_MOTS_GENERIQUES
from .llm.orchestrator import _assemble_narrations
from .llm.prompt_builder import PromptBuilder
from .rag.store import RagStore
from .tools.base import ToolContext
from .tools.monstres import image_pour
from .tools.registry import discover_tools
from .game.combat import boucle_auto as _boucle_combat

import re as _re_mod

# Mots TROP génériques de la narration de combat (normalisés, sans accents) :
# « un monstre surgit », « une créature attaque » ne désignent AUCUNE
# créature précise — jamais de rattrapage d'engagement sur ces mots
# (a6d11005 : le rattrapage avait engagé le placeholder « monstre »).
_MOTS_COMBAT_GENERIQUES = frozenset({
    "monstre", "monstres", "creature", "creatures", "ennemi", "ennemis",
    "adversaire", "adversaires", "chose", "truc", "silhouette", "ombre",
    "ombres", "forme", "formes", "masse", "figure",
})

# Détection d'une invoquation / renfort annoncé par un joueur en combat :
# déclenche le rattrapage 5bis-b si le MJ l'a narré sans tool.
_INVOKE_RE = _re_mod.compile(
    r"\b(invoqu\w*|convoqu\w*|invocation\w*|summon\w*|renforts?)\b",
    _re_mod.IGNORECASE,
)

# Détection d'une action de combat déclarée par le joueur mais non résolue
# par le MJ (aucun jet) → rejeu correctif 5bis-a (comme pour les monstres).
_ACTION_COMBAT_RE = _re_mod.compile(
    r"\b(attaqu\w*|frapp\w*|assén\w*|lanc\w+|incant\w*|tir\w*|soign\w*"
    r"|soins|guér\w*|charge\w*|degat\w*|dégâts?)\b",
    _re_mod.IGNORECASE,
)

# Détection d'une INTENTION D'ATTAQUE MELEE/ARMÉE déclarée par le joueur
# (séparé de _ACTION_COMBAT_RE pour ne déclencher la résolution déterministe
# que sur des attaques physiques, pas les sorts / soins / invocation).
_ACTION_ATTAQUE_RE = _re_mod.compile(
    r"\b(attaqu\w*|frapp\w*|assén\w*|hach\w*|épé\w*|sabre\w*"
    r"|poignard\w*|marteau\w*|couteau\w*|dague\w*|lance\b)\b",
    _re_mod.IGNORECASE,
)

# Déclaration d'ATTAQUE par le JOUEUR (son message) : déclencheur élargi du
# rattrapage 5ter. Partie 4d4b4557 : « J'attaque le loup avec ma hache » est
# resté 100 % prose (le MJ n'a narre « bondit sur vous » — variante absente
# des marqueurs — et aucun dégât chiffré), alors que le loup gris EST au
# bestiaire : aucun engager_combat, aucun dé, PV intacts. Volontairement
# SANS « lance » (« je lance un sort » n'est pas une attaque armée).
_ATTAQUE_JOUEUR_RE = _re_mod.compile(
    r"\b(?:attaqu\w*|frapp\w*|ass[ée]n\w*|hach\w*|transpers\w*|tir\w*)\b",
    _re_mod.IGNORECASE,
)

# Hostilité IMMINENTE narrée par le MJ alors que le joueur n'a pas encore
# déclaré d'attaque. Partie 5b4e2bbe : « Le Gobelin s'arrête à quelques
# mètres, arme levée, prêt à attaquer. Que souhaitez-vous faire ? » — aucune
# attaque joueur, aucun marqueur de combat → la scène restait 100 % prose et
# il fallait attaquer à nouveau pour déclencher la phase combat. Une créature
# du bestiaire qui s'apprête à frapper suffit à vouloir la mécanique
# officielle (initiative, rotation, PV suivis).
_HOSTILITE_IMMINENTE_RE = _re_mod.compile(
    r"\b(?:pr[êéèe]t(?:e|es|s)?\s+(?:à|a)\s+(?:attaqu\w*|frapp\w*|charg\w*)|"
    r"s['’]appr[eêè]te?\s+(?:à|a)\s+(?:attaqu\w*|frapp\w*|charg\w*)|"
    r"vous\s+vise|vous\s+menace|fond\s+sur\s+vous|bondit\s+vers\s+vous|"
    r"charg[eêé]\s+vers\s+vous|arme\s+lev[ée]e?|glai?ve\s+lev[ée]e?|"
    r"armes?\s+point[ée]e?s?|vous\s+d[ée]fie|vous\s+guette)\b",
    _re_mod.IGNORECASE,
)

# Détection d'une INTENTION DE SOINS déclarée par le joueur : si le LLM
# narrer « vous avez récupéré 3 PV » SANS appeler fiche_perso_soigner, le
# rattrapage 5bis-c-long applique le montant déclaré à la fiche. Séparé de
# _ACTION_COMBAT_RE pour ne déclencher le rattrapage que sur les actions
# de soins déclarées (pas sur les attaques).
_ACTION_SOIN_RE = _re_mod.compile(
    r"\b(?:soign\w*|soins|gu[ée]r\w*|pans\w*|bandag\w*"
    # 120e9243 : le joueur écrit aussi « kit de PREMIER secours » (singulier)
    r"|kit\s+(?:de\s+)?premiers?\s+secours?"
    r"|r[ée]tabl\w*|r[ée]cup[ée]r\w*(?:\s+d['']?)?\s*(?:PV|points?\s+de\s+vie))\b",
    _re_mod.IGNORECASE,
)

# Objets d'équipement/inventaire qui PERMETTENT un soin (kit de premiers
# secours, trousse, pansement…) : si le joueur déclare un soin SANS que le
# LLM n'annonce de montant ni n'appelle `fiche_perso_soigner`, le serveur
# résout lui-même un 1d4 au lieu de laisser l'action sans effet (partie
# 5f3e31c9 : le joueur pensait avoir pansé, est resté à 3 PV, a attendu le
# timeout de 300 s, puis est mort au tour du loup-garou).
_RE_KIT_SOIN = _re_mod.compile(
    r"kit\s+(?:de\s+)?premiers\s+secours|trousse\s+de\s+soins"
    r"|pansement|bandage",
    _re_mod.IGNORECASE,
)

# ✨ Résurrection narrée (après GAME OVER) : le MJ narre la restauration
# d'un PJ mort (« récupéré 1 point de vie par niveau, maintenant à 16 PV
# sur vos 17 ») SANS tool — et aucun tool existant ne peut lever « Mort »
# (repos_long ignore pv<0, soigner ne retire pas Mort). Partie 5f3e31c9 :
# PJ resté Mort/-10 alors que la narration l'avait relevé à 16/17.
_RE_PV_RECUPERES = _re_mod.compile(
    r"(?:r[éèe]cup[éèe]r\w*|soign\w*|rend|restaure)\w*\s*(?:\*\*)?\d{1,3}"
    r"(?:\*\*)?\s*(?:PV\b|points?\s+de\s+vie|point\s+de\s+vie)"
    r"|(?:maintenant\s+)?à\s*(?:\*\*)?\d{1,3}(?:\*\*)?\s*PV(?:\*\*)?"
    r"\s*(?:sur|/)\s*(?:vos\s+)?\d{1,3}"
    r"|repos\s+(?:long|de\s+nuit)",
    _re_mod.IGNORECASE,
)
_RESURRECTION_RE = _re_mod.compile(
    r"\b(ressuscit\w*|r[ée]surrection|r[ée]anim\w*"
    r"|reven\w*\s+à\s+la\s+vie|raise\s+dead|r[ée]incarn\w*)\b",
    _re_mod.IGNORECASE,
)
# Offre de choix / refus (« Je ne peux pas ressusciter… voici vos options ») :
# la résurrection est PROPOSÉE, pas réalisée → ne pas appliquer.
# 🛡️ B36 (audit parties complètes) : le modèle GAME OVER du serveur dit
# exactement « Proposez à la table : nouvelle partie, résurrection négociée ou
# reprise narrative plus tôt » — aucune des formes d'origine ne capturait
# « Proposez », et l'OFFRE était donc appliquée comme une réalisation,
# déclenchant un Raise Dead non demandé avec −2 CON permanent. L'ensemble est
# étendu à tous les verbes de proposition et à la structure « X ou Y ».
_RE_OFFRE_RESURRECTION = _re_mod.compile(
    r"ne (?:peux|peut|pourr\w*)\s+pas|nous devons|il faudr\w*"
    r"|choix pour la suite|que choisissez|choisissez-vous"
    r"|co[ûu]t narratif|quelle est votre|à vous de (?:choisir|décider)"
    # B36 : verbes de proposition / offre de menu.
    r"|propos\w*|proposez|offr\w*|souhaitez\s*-?\s*vous|voulez\s*-?\s*vous"
    r"|au\s+choix|plusieurs\s+options|vos\s+options|options\s+sont"
    r"|si\s+vous\s+(?:acceptez|le\s+souhaitez|voulez)"
    r"|dites\s*-?\s*(?:moi|nous)|faites\s+votre\s+choix"
    r"|nouvelle\s+partie|reprise\s+narrative|recharger\s+la\s+partie",
    _re_mod.IGNORECASE,
)
# 🛡️ B36 bis : un GAME OVER annoncé n'est JAMAIS une résurrection réalisée,
# quel que soit le vocabulaire employé dans la même phrase.
_RE_GAME_OVER = _re_mod.compile(
    r"game\s*over|tous\s+les\s+h[ée]ros\s+(?:sont|viennent\s+de\s+"
    r"|viennent\s+d'\u00eatre)\s+(?:tomb[ée]s|mort[s]|\u00e0\s+terre)"
    r"|le\s+groupe\s+(?:est|a\s+[ée]t[ée])\s+(?:d\u00e9truit|an[ée]anti"
    r"|tomb\u00e9)|partie\s+termin\u00e9e|d\u00e9faite\s+de\s+groupe"
    r"|tous\s+morts|personne\s+ne\s+survit",
    _re_mod.IGNORECASE,
)
# ✨ Résurrection VRAIE (True Resurrection, Clr 9) : l'UNIQUE variante sans
# perte de niveau ni de CON (DMG 3.5) — Raise Dead (niv 5) et Résurrection
# (niv 7) infligent toujours la pénalité ; seul le 9e niveau l'évite.
_RE_RESURRECTION_VRAIE = _re_mod.compile(
    r"(?:vraie|v[ée]ritable|totale|parfaite|sup[ée]rieure)\s+r[ée]surrection"
    r"|r[ée]surrection\s+(?:vraie|v[ée]ritable|totale|parfaite|sup[ée]rieure)"
    r"|true\s+resurrection",
    _re_mod.IGNORECASE,
)
# ⚔️ Engagement de combat NARRÉ (hors phase combat) : le LLM écrit
# « Engagement du combat / Initiative : X (14) vs Y (12) » et joue même les
# tours de monstres sans appeler `engager_combat` — la phase reste
# exploration et tout le combat est une fiction sans ancre (partie
# 5f3e31c9, msg 30 : initiative, tour de Zendar et dégâts 100 % inventés,
# « PV 28/32 » recyclés du Loup-garou). On exige l'outil — mais seulement
# si le JOUEUR a déclaré une action de combat (un « combat imminent » dans
# une simple offre de choix ne doit pas engager avant sa décision).
_RE_ENGAGEMENT_NARRE = _re_mod.compile(
    r"engagement du combat|combat\s+est\s+engag[ée]|combat\s+engag[ée]e?\b"
    r"|initiative\s*:\s*[A-Za-zÉÀ]|jet\s+d['']initiative"
    r"|ordre\s+d['']initiative",
    _re_mod.IGNORECASE,
)
# 💥 Dégâts annoncés en prose contre un PJ (« vous subissez 12 dégâts »,
# « Utturgut subit 8 dégâts ») SANS tool : en combat, les filets
# `_appliquer_degats_oublies` couvrent le cas ; hors combat AUCUN filet
# n'existait → la narration blessait sans toucher l'état (partie 5f3e31c9,
# msg 30 : « 12 dégâts de foudre » restés sans effet, PV intacts).
_RE_DEGATS_SUBIS_PJ = _re_mod.compile(
    r"(?:subit|subissez|encaiss\w*|re[çc]oit|prenez)[^.!?\n]{0,60}?"
    r"(?:\*\*)?(\d{1,3})(?:\*\*)?\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
    _re_mod.IGNORECASE,
)

# Détection d'une INTENTION de déplacement de donjon (« Je vais au nord »,
# bouton de la carte, « direction ouest »…) → si le MJ narre l'arrivée sans
# appeler `carte_donjon_explorer`, l'état et la carte ne bougent PAS (bug
# réel : le modèle narrait « tu traverses le passage est » sans tool). Un rejeu
# correctif force l'outil, qui arbitre (refus si pas de porte dans ce mur).
_MOVE_INTENT_RE = _re_mod.compile(
    r"^\s*(?:je\s+(?:vais|souhaite\s+aller|passe|avance)\s+(?:au|à l'|a l'|vers\s+le\s+|vers\s+la\s+)?"
    r"|on\s+va\s+(?:au|à l'|a l')?|allons\s+(?:au|à l'|a l')?|direction\s+)?"
    r"\s*(nord|sud|est|ouest)\s*[.!?]*\s*$",
    _re_mod.IGNORECASE,
)
# Intention de RETOUR (« je retourne dans la salle précédente ») : le chemin
# en arrière est la porte par laquelle le groupe est entré
# (`donjon.arrivee_par`), pas une direction inventée.
_INTENT_RETOUR_RE = _re_mod.compile(
    r"\b(retourn\w*|revien\w*|reven\w*|rebrouss\w*|salle\s+pr[ée]c[ée]dente?"
    r"|pi[èe]ce\s+pr[ée]c[ée]dente?|en\s+arri[èe]re|recul\w*|repart\s+d'où)"
    r"\b",
    _re_mod.IGNORECASE,
)
# Intention de VOYAGE hors donjon (« je me dirige a pied vers la grotte ») :
# destination explicite (« vers X », « jusqu'à X »). Utilisé HORS donjon
# uniquement (le bloc donjon actif est traité avant).
_INTENT_VOYAGE_RE = _re_mod.compile(
    r"\bvers\s+(?:la\s|le\s|les\s|l'|une?\s|d')?[a-zà-ÿ]|\bjusqu",
    _re_mod.IGNORECASE,
)

# Détection d'ENTRÉE dans un lieu à cartographier (partie 8a7c1f92 : le MJ
# narre « Vous vous dirigez vers l'entrée des catacombes… Vous entrez dans
# l'obscurité » SANS jamais appeler `carte_donjon_entrer` — donjon.id reste
# null, la carte /carte-donjon.svg répond 404 « Aucun donjon actif »).
# Deux signaux requis : un MOT DE LIEU clos/souterrain ET un signal de
# franchissement de seuil (une simple mention du lieu ne suffit pas).
_DONJON_LIEU_RE = _re_mod.compile(
    r"\b(catacombes?|donjons?|cryptes?|souterrains?|tunnels?|grottes?|"
    r"cavernes?|[ée]gouts?|tombeaux?|labyrinthes?|repaire|antre"
    # 🔧 Bêta (Duel au pinceau) : le « donjon » du scénario est un HÔTEL
    # particulier (mansarde, estaminet, salles de réception…) — sans ces
    # mots, la carte du plan ne s'initialisait jamais pour ce module.
    r"|h[ôo]tels?\s+(?:particulier|de\s|du\s)"
    r"|m[âa]nsardes?|estaminets?)\b",
    _re_mod.IGNORECASE,
)
_DONJON_ENTREE_RE = _re_mod.compile(
    r"vous\s+(?:entrez\b|p[ée]n[ée]trez\b|descendez\b|franchissez\b"
    r"|vous\s+enfoncez\b|vous\s+engagez\b|dirigez\s+vers\s+l['']entr[ée]e)"
    # 🔧 Bêta : 1re personne du pluriel (message joueur) et formes
    # reformulées (« vous êtes les bienvenus dans l'hôtel… ») — le petit
    # modèle reformule l'entrée sans jamais employer le bon verbe (partie
    # réelle Duel au pinceau : « Bienvenue… dans l'hôtel de Valdenath »).
    r"|nous\s+(?:entrons\b|p[ée]n[ée]trons\b|descendons\b|franchissons\b)"
    r"|bienvenu\w*\s+dans\s+l['' ]"
    r"|l['']entr[ée]e\s+(?:des?\b|du\b|de\s+la\b)"
    r"|\bfranchi\w+\s+le\s+seuil\b|\bau\s+seuil\b"
    r"|descend\w*\s+dans\s+(?:la\s|le\s|les\s|l[''])"
    # Franchissement narré EN PROSE par le LLM : « Le combat s'engage dans la
    # pénombre de la grotte », « vous vous engagez dans les entrailles »,
    # « à l'intérieur de la caverne… » — le groupe est DÉJÀ dans le lieu clos
    # alors qu'aucun verbe d'entrée n'est employé (partie 4b529064 : combat
    # engagé « dans la pénombre de la grotte » sans `carte_donjon_entrer`).
    r"|s['']engage\w*(?:\s+dans)?\b"
    r"|à\s+l['']int[ée]rieur\s+de\b"
    r"|p[ée]n[ée]tre\w*(?:\s+[\wéèêâ-]+){0,2}\s+dans\b",
    _re_mod.IGNORECASE,
)


def _entree_donjon_narree(narration: str) -> bool:
    """True si UNE MÊME PHRASE relate l'ENTRÉE du groupe dans un lieu clos
    à cartographier (donjon, catacombes, crypte…).

    🔧 Partie c21d0734 : la portée était le TEXTE ENTIER — le LIEU d'une
    phrase (le souvenir du « repaire » de Nulentok, mentionné par
    Teleshann) + le VERBE d'une autre (« Bienvenue dans l'auberge »,
    « entrer dans son atelier ») re-déclenchaient `carte_donjon_entrer` :
    le groupe reposant à l'auberge se retrouvait TÉLÉPORTÉ dans le
    donjon."""
    if not narration:
        return False
    import re as _re_scope  # main.py importe re sous l'alias _re_mod
    for phrase in _re_scope.split(r"(?<=[.!?…])\s+|\n", narration):
        if not phrase.strip():
            continue
        if (_DONJON_LIEU_RE.search(phrase)
                and _DONJON_ENTREE_RE.search(phrase)):
            return True
    return False


def _suggestion_outil_explo(etat: dict[str, Any], txt: str) -> Optional[str]:
    """L'outil de déplacement à suggérer au MJ selon la scène, ou None quand
    AUCUN outil ne doit être forcé.

    Parties 4d4b4557 + 6746fc6c :
    - 4d4b4557 : le correctif suggérait `carte_donjon_entrer` pour un simple
      VOYAGE Silverymoon→grotte ; le manifeste du scénario primait sur l'id
      demandé et le donjon était réinitialisé en salle (0,0).
    - 6746fc6c : « je me dirige au centre de la salle » (AUCUNE direction)
      déclenchait un rejeu qui faisait appeler `carte_donjon_explorer(est)` —
      le groupe changeait de salle sans l'avoir décidé. Un mouvement DANS la
      salle ne doit forcer AUCUN outil.

    - donjon actif + direction explicite → `carte_donjon_explorer(direction=)`
    - donjon actif + intention de retour → la porte par laquelle on est entré
      (`donjon.arrivee_par`)
    - donjon actif + mouvement interne (centre, examine…) → None
    - seuil d'un lieu clos narré → `carte_donjon_entrer`
    - hors donjon + direction/retour/destination → `voyage_demarrer`
    """
    _id_dj = str(((etat.get("donjon") or {}).get("id")) or "").strip()
    _dir = _direction_intention(txt)
    _retour = bool(_INTENT_RETOUR_RE.search(txt or ""))
    if _id_dj:
        if _dir:
            return (
                f"`carte_donjon_explorer(direction='{_dir}')` — le joueur "
                f"veut aller au {_dir} ; n'appelle PAS "
                "`carte_donjon_entrer` (il réinitialiserait l'exploration)"
            )
        if _retour:
            _arr = str(
                (etat.get("donjon") or {}).get("arrivee_par") or ""
            ).strip().lower()
            if _arr in ("nord", "est", "sud", "ouest"):
                return (
                    f"`carte_donjon_explorer(direction='{_arr}')` — le "
                    "groupe RETOURNE dans la salle d'où il vient : c'est la "
                    f"porte {_arr.upper()} par laquelle il est entré ici"
                )
            return (
                "`carte_donjon_explorer(direction=…)` vers la salle "
                "VISITÉE adjacente reliée par une porte (celle d'où le "
                "groupe vient) — l'outil refusera toute direction sans "
                "passage réel"
            )
        # Mouvement DANS la salle (centre, approche, examen…) : la prose
        # suffit, aucun changement de salle ne doit être forcé.
        return None
    if _entree_donjon_narree(txt):
        return (
            "`carte_donjon_entrer(donjon_id=…)` — le groupe franchit le "
            "seuil d'un lieu clos à cartographier"
        )
    if _dir or _retour or _INTENT_VOYAGE_RE.search(txt or ""):
        return (
            "`voyage_demarrer(destination=…, distance_km=…, "
            "mode='lent'/'marche'/'rapide'/'cheval', terrain=…)` — un "
            "déplacement HORS donjon (route, ville→site, plusieurs jours) "
            "passe TOUJOURS par lui ; JAMAIS `carte_donjon_entrer` hors "
            "franchissement du seuil d'un lieu clos"
        )
    return None


def _direction_intention(txt: str) -> Optional[str]:
    """Direction EXPLICITE demandée par le joueur (« Je vais au est »), ou
    None (« je me dirige au centre de la salle » n'en exprime aucune)."""
    m = _MOVE_INTENT_RE.match((txt or "").strip())
    return m.group(1).lower() if m else None


def _norm_nom_objet(s: str) -> str:
    """Normalise un nom d'objet pour comparaison (minuscules, sans accents,
    sans ponctuation)."""
    import unicodedata as _u, re as _re
    s = _u.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not _u.combining(c))
    return _re.sub(r"[^a-z0-9]+", " ", s).strip()


def _objet_present_inventaire(ctx: Any, etat: Any, nom: str) -> bool:
    """True si `nom` figure déjà dans l'inventaire d'un PJ de la partie.

    Sert à ne PAS rejouer l'enregistrement d'un objet clé déjà persisté (sinon
    la simple re-narration de sa possession re-déclencherait le rattrapage à
    chaque tour)."""
    cible = _norm_nom_objet(nom)
    if not cible or ctx is None or not isinstance(etat, dict):
        return False
    try:
        from .tools.fiches import _load_fiche
    except Exception:                                        # noqa: BLE001
        return False
    for p in etat.get("pj") or []:
        nom_pj = str((p or {}).get("nom") or "").strip()
        if not nom_pj:
            continue
        try:
            fiche = _load_fiche(ctx, nom_pj)
        except Exception:                                    # noqa: BLE001
            fiche = None
        if not isinstance(fiche, dict):
            continue
        inv = fiche.get("inventaire") or fiche.get("equipement") or []
        if not isinstance(inv, list):
            continue
        for e in inv:
            if not isinstance(e, dict):
                continue
            n = _norm_nom_objet(str(e.get("nom") or ""))
            if n and (cible in n or n in cible):
                return True
    return False


# Noms génériques qui ne sont PAS des objets d'inventaire : évite qu'une
# « remise »/« possession » narrée sur une partie du corps ou un lieu
# (« il vous tend la main », « la sortie est entre vos mains ») ne déclenche
# le rattrapage inventaire (normalisés : minuscules sans accents).
_MOTS_NON_OBJETS = {
    "main", "mains", "pied", "pieds", "pas", "regard", "doigt", "doigts",
    "tete", "bras", "jambe", "jambes", "oeil", "yeux", "voix", "souffle",
    "route", "chemin", "passage", "sortie", "entree", "porte", "direction",
    "parole", "paroles", "coup", "coups", "tour", "initiative",
}

# Remise d'un objet NOMMÉ par un PNJ / transfert explicite, sans article
# indéfini (donc absent de `_ACQUISITION_ANCRE_RE`) : « il vous remet la
# couronne », « vous vous emparez du sceptre », « il vous confie la gemme ».
_RE_TRANSFERT_OBJET_RE = _re_mod.compile(
    r"\b(?:vous\s+)?(?:remet|remets|remettent|confie|confient|tend|tendent|"
    r"transmet|transmettent|empoche\w*|empare\w*|saisi\w*)\s+"
    r"(?:de\s+la\s+|de\s+l['’]|du\s+|des\s+|de\s+)?"
    r"(?:la|le|les|l['’])?\s*"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*(?:\s+[A-Za-zÀ-ÿ'’\-]+){0,4})",
    _re_mod.IGNORECASE,
)

# Possession explicite d'un objet NOMMÉ : « la couronne de Mystra est
# maintenant entre vos mains », « le sceptre est désormais en votre possession »,
# « la clé est dans votre inventaire ».
_RE_POSSESSION_MAINS_RE = _re_mod.compile(
    r"(?:la|le|les|l['’])\s+"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*(?:\s+[A-Za-zÀ-ÿ'’\-]+){0,4})"
    r"\s+(?:est|sont|se\s+trouve|se\s+trouvent)\s+"
    r"(?:maintenant\s+|d[ée]sormais\s+)?"
    r"(?:entre\s+(?:vos|tes|ses)\s+mains|en\s+(?:votre|ta|sa)\s+possession"
    r"|dans\s+(?:votre|ton|son)\s+(?:inventaire|sac|besace|sacoche))",
    _re_mod.IGNORECASE,
)


def _objet_revendique_nom(narration: str) -> str:
    """Extrait le nom d'un objet revendiqué dans la narration (footer « Objet
    clé », remise explicite ou possession nommée) — "" si aucun candidat.

    Indépendant du scénario : les trois motifs couvrent aussi bien le footer
    du side panel que la prose libre du MJ (« la couronne est entre vos
    mains »)."""
    for motif in (_OBJET_CLE_RE, _RE_TRANSFERT_OBJET_RE,
                  _RE_POSSESSION_MAINS_RE):
        m = motif.search(narration or "")
        if not m:
            continue
        nom = m.group(1).strip(" *_•·-–—")
        if nom and _norm_nom_objet(nom) not in _MOTS_NON_OBJETS:
            return nom
    return ""


# --------------------------------------------------------------------------- #
#  🪙 Trésor canonique de salle → effet mécanique (chantier 3).
#  Les 197 salles de scénario portent un `tresor` rédigé à la main avec des
#  montants en clair (« gemmes et pièces pour 200 po au total », « une bourse
#  de 15 po », « 75 pc »). Le MJ le NARRE mais n'écrit jamais « +200 po » :
#  le rattrapage générique ne capte que « +N pièces » et l'or restait
#  fantôme. On parse ici le trésor canonique de la salle COURANTE pour
#  créditer LUI-MÊME le montant au PJ qui fouille.
# --------------------------------------------------------------------------- #
_RE_MONTANT_TRESOR = _re_mod.compile(
    r"(?:(?:pour|de|en|total(?:e)?(?:\s*:)?|valeur)\s*)?"
    r"(\d{1,4})\s*(?:po\b|pi[èe]ces?\s+d'or\b|pi[èe]ces?\s+dor\b|gp\b)",
    _re_mod.IGNORECASE,
)
_RE_MONTANT_PC = _re_mod.compile(
    r"(\d{1,5})\s*(?:pc\b|pi[èe]ces?\s+de\s+cuivre\b|cp\b)",
    _re_mod.IGNORECASE,
)
# Mots qui signalent que le groupe CHERCHE / prend le trésor (et non qu'il
# le voit de loin ou qu'on le mentionne en décor).
_RE_FOUILLE_TRESOR = _re_mod.compile(
    r"\b(fouill\w+|fouillez|fouille|ouvr\w+|ouvrez|ouvrez le coffre"
    r"|ouvre le coffre|saisiss\w+|saisissez|prenez|prend\w+|ramass\w+"
    r"|ramassez|videz|vide\w+|empt\w+|butin|pillage|pillez|recueill\w+"
    r"|recueillez|empoch\w+|empopez|pochez)\b",
    _re_mod.IGNORECASE,
)
# Mots qui signalent que le trésor est DÉJÀ pris (ne jamais re-créditer).
_RE_TRESOR_DEJA_PRIS = _re_mod.compile(
    r"\b(d[ée]j[àa]\s+(?:pris|vide|vid[ée]e|ramass\w+|empoch\w+)"
    r"|plus\s+r[ie]en|coffre\s+vide|sarcophage\s+vide|bourse\s+vide)\b",
    _re_mod.IGNORECASE,
)


def tresor_canonique_salle(etat: dict) -> str:
    """Texte du trésor canonique de la salle COURANTE du donjon actif — ""
    si aucun donjon, aucune salle ou aucun trésor."""
    donjon = etat.get("donjon") or {}
    if not donjon.get("id"):
        return ""
    pos = donjon.get("courant") or [0, 0]
    try:
        cx, cy = int(pos[0]), int(pos[1])
    except (TypeError, ValueError, IndexError):
        return ""
    for salle in donjon.get("grille", []) or []:
        if not isinstance(salle, dict):
            continue
        try:
            sx, sy = int(salle.get("x", -1)), int(salle.get("y", -1))
        except (TypeError, ValueError):
            continue
        if (sx, sy) == (cx, cy):
            return str(salle.get("tresor") or "").strip()
    return ""


def _montants_tresor(texte: str) -> tuple[int, int]:
    """Parse un texte de trésor → (or en pc, objets) ; (0, 0) si rien.

    Gère les formulations réelles des 197 salles :
    - « gemmes et pièces pour 200 po au total » → 2000 pc
    - « une bourse de 15 po » → 150 pc
    - « bourse du culte (75 pc) » → 75 pc
    - « 10 po pièce » (prix unitaire) → 0 pc (pas un total) ;
    - « gemmes de sardonyx … 10 po pièce » → 0 pc.
    """
    if not texte:
        return 0, 0
    t = texte
    # Un « N po pièce/unité » est un PRIX UNITAIRE, pas un total : on le
    # neutralise avant de chercher un total.
    t = _re_mod.sub(r"\d{1,4}\s*po\b\s*(?:pi[èe]ce|unit[ée])", "", t,
                    flags=_re_mod.IGNORECASE)
    or_po = _RE_MONTANT_TRESOR.search(t)
    or_pc = _RE_MONTANT_PC.search(t)
    # La plus grande valeur en pc gagne (un « pour 200 po » écrase un « 15 po »).
    candidats = []
    if or_po:
        candidats.append(int(or_po.group(1)) * 10)
    if or_pc:
        candidats.append(int(or_pc.group(1)))
    total_pc = max(candidats) if candidats else 0
    return total_pc, 0


async def _appliquer_tresor_canonique(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Optional[Any],
    result: Any,
    nom_pj: str = "",
) -> str:
    """Crédite le trésor canonique de la salle courante quand le MJ narre la
    fouille mais n'appelle AUCUN outil d'inventaire (chantier 3).

    Bornes anti-fiction : or ≤ 2000 po, une seule fois par salle (marqueur
    `tresor_pris` sur la salle). Renvoie la note mécanique ("" si rien)."""
    if not nom_pj:
        return ""
    narration = result.narration or ""
    if not _RE_FOUILLE_TRESOR.search(narration):
        return ""
    if _RE_TRESOR_DEJA_PRIS.search(narration):
        return ""
    # ⛔ Double-crédit : si le MJ a déjà écrit un gain chiffré (« +200 po »),
    # le rattrapage générique (`_appliquer_gains_inventaire_narres`) l'a
    # appliqué. Ne jamais re-créditer le même montant. Mêmes motifs que le
    # rattrapage générique (le chiffre et l'unité peuvent être séparés par la
    # fin de ligne).
    if _re_mod.search(
        r"inventaire\s+mis\s+à\s+jour[^+]*\+\s*\d+|"
        r"\+\s*\d+\s*(?:pi[èe]ces?|objets?|po\b|or\b|pc\b)",
        narration,
        _re_mod.IGNORECASE,
    ):
        return ""
    try:
        from .tools.fiches import _load_fiche, _save_fiche
        etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
    except Exception:                                            # noqa: BLE001
        return ""
    texte = tresor_canonique_salle(etat)
    if not texte:
        return ""
    # ⛔ Déjà crédité pour cette salle : marqueur persistant sur la salle.
    donjon = etat.get("donjon") or {}
    try:
        cx, cy = int((donjon.get("courant") or [0, 0])[0]), \
            int((donjon.get("courant") or [0, 0])[1])
    except (TypeError, ValueError, IndexError):
        return ""
    deja_pris = False
    for salle in donjon.get("grille", []) or []:
        if not isinstance(salle, dict):
            continue
        try:
            if (int(salle.get("x", -1)), int(salle.get("y", -1))) == (cx, cy):
                deja_pris = bool(salle.get("tresor_pris"))
                break
        except (TypeError, ValueError):
            continue
    if deja_pris:
        return ""
    total_pc, _objets = _montants_tresor(texte)
    if total_pc <= 0:
        return ""
    # Borne anti-fiction (le trésor canonique est écrit par l'auteur, mais un
    # parseur déréglé ne doit jamais créditer une fortune).
    if total_pc > 20_000:
        return ""
    try:
        fiche = _load_fiche(ctx, nom_pj)
        if fiche is None:
            return ""
        fiche["or"] = int(fiche.get("or", 0) or 0) + total_pc
        _save_fiche(ctx, nom_pj, fiche)
    except Exception as e_or:                                    # noqa: BLE001
        print(f"[dnd35] Trésor canonique échoué : {e_or}")
        return ""
    # Marque la salle pour ne jamais re-créditer.
    try:
        for salle in donjon.get("grille", []) or []:
            if not isinstance(salle, dict):
                continue
            try:
                if (int(salle.get("x", -1)), int(salle.get("y", -1))) == (cx, cy):
                    salle["tresor_pris"] = True
                    break
            except (TypeError, ValueError):
                continue
        # 🛡️ Sauvegarde dans le MÊME data_dir que la lecture (ctx.data_dir) :
        # l'ancien helper écrivait dans cfg.paths.data_dir, un autre dossier —
        # le marqueur `tresor_pris` ne survivait pas à l'écriture.
        _err_save = PartyState(data_dir=ctx.data_dir,
                               partie_id=ctx.partie_id).save(etat)
        _etat_save = None if _err_save else donjon
    except Exception:                                            # noqa: BLE001
        _etat_save = None
    idx = next(
        (i for i, p in enumerate(etat.get("pj") or []) if p.get("nom") == nom_pj),
        0,
    )
    result.state_patches.append(
        {"pj.%d.or" % idx: fiche["or"], "pj_updated": True})
    if _etat_save:
        result.state_patches.append({"donjon": donjon})
    return (
        f"🪙 **Trésor du scénario crédité à {nom_pj}** : {total_pc // 10} po "
        f"(or total : {fiche['or'] // 10} po) — « {texte[:80]}»"
    )


async def _appliquer_gains_inventaire_narres(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Optional[Any],
    result: Any,
    nom_pj: str = "",
) -> str:
    """Rattrapage DÉTERMINISTE des gains d'inventaire que le MJ a NARRÉS dans
    un bloc « Inventaire mis à jour » (partie réelle : « Pièces d'or : +3 »
    affiché mais la fiche jamais créditée — le rejeu LLM 5quater-c ne produisait
    pas l'appel). Le serveur applique LUI-MÊME les gains chiffrés :
    - « Or : +N pièces » / « Pièces d'or : +N » → crédite N po (= ×10 pc) ;
    - « X : +N objet(s) » → `inventaire_ajouter` (portee="quete" : loot).

    Bornes anti-fiction : or ≤ 200 po, objets ≤ 20 (au-delà : refus). Renvoie
    la note mécanique ("" si rien à faire)."""
    narration = result.narration or ""
    if "inventaire mis à jour" not in narration.lower():
        return ""
    # Lignes de gain du bloc (et ±100 chars autour) : « Or : +3 pièces »,
    # « Pièces d'or : +3 (Total : 3) », « Pendentif : +1 objet ».
    lignes_gain = _re_mod.findall(
        r"^\s*[-*]?\s*(?:([A-Za-zÀ-ÿ'œœ][^\n:+*]{0,40}?|Or)\s*:\s*)?"
        r"\+\s*(\d{1,3})\s*(pièces? d'or|pièces?|po\b|or\b|objets?)?",
        narration,
        _re_mod.IGNORECASE | _re_mod.MULTILINE,
    )
    notes: list[str] = []
    for nom_brut, montant_s, type_s in lignes_gain:
        montant = int(montant_s)
        if montant <= 0:
            continue
        type_l = (type_s or "").lower()
        nom_l = (nom_brut or "").strip().lower()
        est_or = ("or" in type_l or "po" in type_l
                  or nom_l in ("or", "pièces d'or", "pieces d'or")
                  or "or" in nom_l)
        if est_or:
            # Or : la fiche stocke en pc. Un gain chiffré du MJ est exprimé
            # en pièces d'OR (po) → ×10 pc. Bornes : ≤ 200 po.
            if montant > 200:
                continue
            from .tools.fiches import _load_fiche as _charger_fiche
            etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
            # 🛡️ Chantier 2 : le créditaire est le PJ ACTIF (celui qui a
            # trouvé le trésor), pas le premier de la liste. En 2J, l'ancien
            # code créditait toujours le joueur 1 — le joueur 2 ne recevait
            # JAMAIS l'or qu'il ramassait. Retombe sur le premier PJ si le
            # nom n'est pas résolvable (comportement antérieur conservé).
            cible_or = None
            if nom_pj:
                for pj in etat.get("pj") or []:
                    if str(pj.get("nom") or "") == nom_pj:
                        cible_or = nom_pj
                        break
            if cible_or is None:
                for pj in etat.get("pj") or []:
                    try:
                        fiche = _charger_fiche(ctx, str(pj.get("nom") or ""))
                    except Exception:                        # noqa: BLE001
                        fiche = None
                    if fiche is not None:
                        cible_or = str(fiche.get("nom"))
                        break
            if cible_or is None:
                continue
            # Patch direct de l'or de la fiche (pas de tool dédié : le champ
            # `or` est un simple compteur en pc, fiche-fait-foi).
            try:
                from .tools.fiches import _load_fiche, _save_fiche
                fiche = _charger_fiche(ctx, cible_or)
                if fiche is not None:
                    fiche["or"] = int(fiche.get("or", 0) or 0) + montant * 10
                    _save_fiche(ctx, cible_or, fiche)
                    idx = next((i for i, p in enumerate(etat.get("pj") or [])
                                if p.get("nom") == cible_or), 0)
                    result.state_patches.append(
                        {"pj.%d.or" % idx: fiche["or"], "pj_updated": True})
                    notes.append(
                        f"📦 **Rattrapage serveur** : +{montant} po "
                        f"crédités à {cible_or} (or total : "
                        f"{fiche['or'] // 10} po).")
            except Exception as e_or:                    # noqa: BLE001
                print(f"[dnd35] Rattrapage or échoué : {e_or}")
        else:
            # Objet : « X : +N objet(s) » → inventaire_ajouter (portee quête).
            if not nom_brut or montant > 20:
                continue
            nom_objet = (nom_brut or "").strip().rstrip(":").strip()
            # Nettoie un éventuel préfixe de puce et « Total »
            nom_objet = _re_mod.sub(r"^[-*]\s*", "", nom_objet).strip()
            if not nom_objet or nom_objet.lower().startswith("total"):
                continue
            etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
            # 🛡️ Chantier 2 : même règle pour les objets — le PJ ACTIF.
            cible_obj = None
            if nom_pj:
                for pj in etat.get("pj") or []:
                    if str(pj.get("nom") or "") == nom_pj:
                        cible_obj = nom_pj
                        break
            if cible_obj is None:
                cible_obj = next(
                    (str(p.get("nom") or "") for p in (etat.get("pj") or [])),
                    None,
                )
            if not cible_obj:
                continue
            tr = await orch.execute_tool_direct(
                "inventaire_ajouter",
                {"nom": cible_obj, "objet": nom_objet, "quantite": montant,
                 "portee": "quete"},
                ctx, on_event, result,
            )
            # 🛡️ Chantier 2 : un objet HORS catalogue sans `poids` est REFUSÉ
            # par `inventaire_ajouter` (charge non calculable) — le code
            # antérieur annonçait « ajouté » alors que l'outil avait refusé.
            # Nouvelle tentative avec un poids par défaut (0,1 kg) : le loot
            # atterrit réellement dans l'inventaire.
            if tr is not None and tr.text.startswith("⚠️") \
                    and "inconnu du catalogue" in tr.text:
                tr = await orch.execute_tool_direct(
                    "inventaire_ajouter",
                    {"nom": cible_obj, "objet": nom_objet, "quantite": montant,
                     "poids": 0.1, "portee": "quete"},
                    ctx, on_event, result,
                )
            if tr is not None and not tr.text.startswith(("❌", "⛔", "⚠️")):
                notes.append(
                    f"📦 **Rattrapage serveur** : {nom_objet} ×{montant} "
                    f"ajouté à l'inventaire de {cible_obj}.")
    return "\n\n".join(notes)


def _rejeu_inventaire_necessaire(
    text: str, narration: str,
    ctx: Any = None, etat: Any = None,
) -> bool:
    """True si le rattrapage 5quater-c doit tourner : acquisition déclarée
    par le JOUEUR, acquisition ANCRÉE (verbe + déterminant indéfini + nom) dans
    la narration, ou OBJET NOMMÉ revendiqué (footer « Objet clé », remise,
    possession) mais absent de la fiche — mais JAMAIS sur une simple mention de
    possession (« la fiole glisse dans votre sac »)."""
    if _ITEM_ACQUISITION_RE.search(text or ""):
        return True                     # le joueur déclare lui-même
    if not (narration or "").strip():
        return False
    # 📦 Liste « Inventaire mis à jour » avec un gain chiffré (« Or : +3
    # pièces », « +1 objet ») : le MJ CROIT avoir mis à jour l'inventaire —
    # signal fort (partie réelle : loot narré « Or : +3 pièces (Total : 3) »
    # resté fiction, fiche jamais crédité). Un gain explicite « +N » est
    # exigé pour éviter les fausses pistes (partie 6746fc6c).
    if _re_mod.search(
        r"inventaire\s+mis\s+à\s+jour[^+]*\+\s*\d+|"
        r"\+\s*\d+\s*(?:pièces?|objets?|or\b)",
        narration,
        _re_mod.IGNORECASE,
    ):
        return True
    if _POSSESSION_SAC_RE.search(narration):
        return False                    # possession narrée, pas acquisition
    if _ACQUISITION_ANCRE_RE.search(narration):
        return True
    # Objet NOMMÉ revendiqué (footer « Objet clé : Couronne de Mystra (dans
    # votre inventaire) », « il vous remet la couronne », « la couronne est
    # maintenant entre vos mains ») sans aucun outil d'inventaire : partie
    # 4b529064, la couronne n'a jamais été persistée. On ne déclenche que si
    # l'objet est réellement ABSENT des fiches — sinon le simple rappel de
    # possession rejouerait le rattrapage à chaque tour. Ce test est
    # FICHE-AWARE et donc valable pour TOUS les scénarios.
    nom = _objet_revendique_nom(narration)
    if nom and not _objet_present_inventaire(ctx, etat, nom):
        return True
    return False

# Détection d'un combat narré EN PROSE par le LLM (le petit modèle écrit
# parfois « Le combat commence ! Le zombie charge… » et enchaîne jets/dégâts
# dans la narration SANS appeler `engager_combat`). Le serveur rattrape alors
# la phase officielle pour que l'ordre d'initiative, le suivi des PV et la
# rotation restent conformes (cf. bloc 5ter sous le moteur de combat).
_COMBAT_PROSE_MARKERS = (
    "le combat commence", "le combat éclate", "le combat s'engage",
    "le combat est lancé", "combat engagé", "les hostilités",
    "charge vers vous", "charge sur vous", "charge sur toi",
    "se jette sur vous", "se jette sur toi", "se précipite sur vous",
    "se précipite sur toi", "bondit vers vous", "bondit sur vous",
    "bondit sur toi", "vous attaque", "attaque toi",
    "t'attaque", "vous agresse", "t'agresse", "se rue sur vous",
    "se rue sur toi",
    "prend son tour", "c'est au tour de",
    # 🔧 Bêta (M1) : créatures qui APPROCHENT en menace ou ATTAQUENT EN PROSE
    # SANS dégâts chiffrés (coup esquivé/paré/raté) — ces tours restaient
    # 100 % fiction sans initiative ni PV (partie réelle « Dues for the
    # Dead » : un zombi décrit puis attaquant 3 tours de suite en exploration).
    "avance vers vous", "avancent vers vous",
    "s'approche de vous", "s'approchent de vous",
    "s'avance vers vous", "s'avancent vers vous",
    "tente de vous mordre", "tentent de vous mordre",
    "prêt à vous mordre", "prêts à vous mordre",
    "tente de vous saisir", "tentent de vous saisir",
    "manque son but", "manque sa cible", "manque de justesse",
    "vous frôle", "vous frôlent", "frôle votre armure",
    "frôlent votre armure", "sans l'entamer", "sans vous toucher",
    "esquivez l'attaque", "esquivez de justesse", "parez l'attaque",
    "vous vous repliez", "se déplace : un", "créature se déplace",
    # 🛡️ B25 (audit parties complètes) : surgissements et alertes narrés sans
    # dégâts chiffrés — « Un Gobelin surgit de l'ombre, brandissant une hache
    # rouillée… Le Gobelin lance un cri de guerre rauque et charge ! » ne
    # matchait AUCUN marqueur : le combat restait en prose, sans initiative ni
    # PV, et le joueur affrontait une fiction.
    "surgit de l'ombre", "surgissent des ombres", "surgit des ombres",
    "émerge de l'ombre", "émergent de l'ombre", "se détache dans",
    "se détachent des recoins", "cri de guerre", "cri d'alerte",
    "hurle une alerte", "hurle son alerte", "lance un cri",
    "se dresse devant vous", "se dressent devant vous",
    "se prépare à attaquer", "se préparent à attaquer",
    "s'apprête à attaquer", "s'apprêtent à attaquer",
    "état du combat", "initiative du combat", "action recommandée",
)
# Prose de DÉGÂTS infligés (attaque portée en narration) : un montant de
# dégâts narré hors combat signifie qu'une action hostile a été jouée —
# le combat DOIT être régularisé (initiative + suivi des PV), sinon les
# dégâts narrés restent de la fiction sans effet (observé en partie réelle :
# « **5 points de dégâts** sont infligés à cette créature » sans engager_combat).
_DEGATS_PROSE_RE = _re_mod.compile(
    r"\b\d{1,3}\s*(?:points?\s+de\s+)?d[ée]g[âa]ts\b", _re_mod.IGNORECASE,
)
# Marqueurs de combat déjà CLÔS dans la narration : on n'engage JAMAIS un
# combat rétroactivement si l'issue a déjà été racontée (victoire, défaite,
# fuite, mort…), pour ne pas écraser un combat terminé.
_COMBAT_PROSE_END_MARKERS = (
    "partie est perdue", "partie est gagnée", "combat est terminé",
    "combat terminé", "combat est clos", "tous les héros sont tombés",
    "vous êtes vaincu", "vous avez vaincu", "les monstres sont vaincus",
    "est vaincu", "sont vaincus", "est détruit", "sont détruits",
    "s'effondre à terre", "s'effondrent à terre", "le monstre s'écroule",
    "a été éliminé", "ont été éliminés", "prenez la fuite", "prend la fuite",
    "vous fuyez", "se rendent", "game over", "vous êtes mort",
)

# Pont anglais → français pour les NOMS de monstres en prose : le petit modèle
# écrit parfois « Ghoul » en anglais dans une narration française. C'est la
# SEULE forme de rapprochement tolérée (`_detecter_combat_prose`) — le difflib
# flou produisait des faux positifs qui faisaient REFUSER tout le rattrapage
# (partie ee5684fe : « signes » → « Singe », « hurle » → « Hurleur »,
# « menaçante » → « Ane », « gobelin » → « Hobgobelin » : cumulés, 65 PV vs
# plafond 42 → `engager_combat` refusait TOUT, aucun combat engagé).
_MONSTRES_EN_FR_PROSE: dict[str, str] = {
    "ghoul": "Goule",
    "goblin": "Gobelin", "goblins": "Gobelin",
    "skeleton": "Squelette", "skeletons": "Squelette",
    "zombie": "Zombie", "zombies": "Zombie",
    # 🔧 Bêta (M1) : orthographe française « zombi » — sans cet alias, la
    # prose « un zombi avance vers vous » ne rapprochait AUCUN monstre du
    # bestiaire (le rattrapage 5ter ignorait la rencontre, partie réelle).
    "zombi": "Zombie", "zombis": "Zombie",
    "kobold": "Kobold",
    "troll": "Troll",
    "ogre": "Ogre",
    "gargoyle": "Gargouille",
    "specter": "Spectre",
}

# Marqueurs d'un déplacement / exploration narré EN PROSE par le LLM (le petit
# modèle décrit souvent une progression sans appeler `carte_donjon_*`).
_EXPLO_PROSE_MARKERS = (
    "avancez", "avancent", "avançons", "avance ", "vous traversez",
    "vous pénétrez", "vous entrez", "vous explorez", "vous vous enfoncez",
    "vous empruntez", "vous ouvrez la porte", "poussez la porte",
    "pénètrent", "explorent", "descendez le couloir", "remontez le couloir",
    "nouvelle salle", "la pièce suivante", "au détour du couloir",
    "vous suivez le passage", "vous suivez le couloir", "vous franchissez",
    "vous suivez le tunnel", "vous progressez dans le couloir",
)
# Marqueurs d'une exploration CLÔSE (retour, sortie, arrêt) : pas de rattrapage.
_EXPLO_PROSE_END_MARKERS = (
    "vous revenez", "vous rebroussez", "vous sortez", "vous repartez",
    "vous vous arrêtez", "la pièce s'arrête", "cul-de-sac",
)

# Outils de la phase d'exploration : dès qu'un de ces outils a été appelé,
# l'exploration a été correctement enregistrée côté serveur.
_EXPLORATION_TOOLS = {
    "carte_donjon_entrer", "carte_donjon_explorer", "carte_donjon_etage",
    "monstre_consulter", "carte_donjon_voir", "voyage_demarrer",
}

# Outils qui valident la phase d'ouverture (le chargement du scénario choisi).
_SCENARIO_LOAD_TOOLS = {"scenarios_laelith_charger", "scenarios_laelith_lister"}

# Détection d'une demande explicite de choisir/charger un scénario (tour de
# phase "load"). Sert de garde : on n'auto-charge JAMAIS le scénario pendant
# la création de personnages (aussi en phase "opening"), uniquement quand le
# joueur demande à voir/choisir les missions disponibles.
_SCENARIO_CHOICE_RE = _re_mod.compile(
    r"\b(scénario\w*|scenario\w*|missions?\s+proposees?|missions?\s+disponi\w*"
    r"|scenarios?_laelith|choisir\s+un\s+scénario|charger\s+le\s+scénario"
    r"|dues\s+for\s+the\s+dead|crypts\s+kelemvor|tombe\s+des\s+rois)\b",
    _re_mod.IGNORECASE,
)

# Outils qui persistent un objet dans l'inventaire d'un PJ. Utilisé par
# 5quater-c : si le MJ annonce une acquisition sans appeler l'un d'eux, on le
# ré-invoque pour forcer l'enregistrement.
_INVENTAIRE_TOOLS = {
    "inventaire_ajouter", "inventaire_retirer", "inventaire_consommer_munition",
}

# Détection d'une acquisition/looting d'objet annoncé par le joueur ou le MJ :
# déclenche le rattrapage 5quater-c si l'objet n'a pas été enregistré.
# ⚠️ Formes fléchées (« je prends ») : l'ancien « je prend\b » ne matchait
# JAMAIS « prends » (le \b exige une fin de mot) — partie réelle : « Je prends
# les bijoux et l'or » restait sans rattrapage, le loot narré jamais appliqué.
_ITEM_ACQUISITION_RE = _re_mod.compile(
    r"\b(ramass\w*|récup\w*|récupèr\w*|trouv\w*|obtien?t|obtenir|acquis\w*"
    r"|pill\w*|prise au|je prend\w*|il prend\w*|elle prend\w*|gagne\w* un"
    r"|butin|loot\w*|rep[èe]r\w*"
    r"|donne\w* à|offre\w* à|cède\w* à"
    # 🔧 Bêta : REMISES DE PNJ (« elle tend un parchemin », « Kaelin prend
    # les objets ») — l'inventaire n'était jamais appliqué pour ce cas
    # (partie réelle : amulette + carte de la Magister jamais ajoutés).
    r"|tend\w*\s+un|remet\w*\s+(?:un|une|le|la|les)"
    r"|re[çc]oi\w*\s+(?:un|une|le|la|les)|pren\w*\s+(?:un|une|le|la|les)"
    r"|tend\w*|remet\w*|re[çc]oi\w*)\b",
    _re_mod.IGNORECASE,
)
# Acquisition ANCRÉE : verbe + déterminant INDÉFINI + nom (« vous trouvez
# une clé », « il vous donne une lettre »). Les articles définis sont
# exclus : « vous trouvez le passage / la sortie » ne concerne pas
# l'inventaire.
_ACQUISITION_ANCRE_RE = _re_mod.compile(
    r"\b(?:ramass|trouv|r[ée]cup|obtien|pill|acquis|gagne|donn|offr|c[èe]d"
    r"|tend|remet|re[çc]oi|pr[ée]sent)"
    r"[a-zà-ÿ]*\w\s+(?:une?\s|des\s|plusieurs\s|\d+\s)"
    r"[a-zà-ÿœæ]",
    _re_mod.IGNORECASE,
)
# Mention de POSSESSION déjà enregistrée (« glisse dans votre sac », « déjà
# rangé ») : ce n'est PAS une acquisition. Partie 6746fc6c : le rejeu
# inventaire a tourné sur 6 tours sur 8 (la narration tissait le contenu du
# sac rappelé par le récap), ré-ajoutant une fiole au passage.
_POSSESSION_SAC_RE = _re_mod.compile(
    r"(d[ée]j[à]\s+(?:dans|rang|pr[ée]sent)|dans\s+(?:votre|son|mon|leur)\s+sac"
    r"|dans\s+(?:votre|son|mon)\s+équipement|glisse\s+dans|referm\w*"
    r"|rang[ée]e?e?\s+dans|poids\s+(?:dans\s+)?(?:votre|son|mon)\s+inventaire)",
    _re_mod.IGNORECASE,
)
# Objet clé revendiqué par le MJ dans son footer (« 🎒 **Objet clé :**
# Couronne de Mystra (dans votre inventaire) ») : signal fort que l'objet
# DEVRAIT être dans l'inventaire. S'il est absent des fiches, on rejoue
# l'enregistrement (partie 4b529064 : couronne jamais persistée).
_OBJET_CLE_RE = _re_mod.compile(
    r"objet\s+cl[ée]\s*[:*]*\s*([^\n(*（]+)",
    _re_mod.IGNORECASE,
)

# Détection d'une demande de SOIN / guérison ou de REPOS (hors combat aussi) :
# le petit modèle 9B narre « Je lance les dés et soigne X » ou « vous vous
# reposez et récupérez vos PV » SANS appeler `fiche_perso_soigner` ni
# `repos_long`. On rejoue alors (5quater-d) pour que les PV changent.
_SOIN_RE = _re_mod.compile(
    r"\b(soign\w*|soins|guéri\w*|guéris\w*|guériss\w*|répar\w*|cicatris\w*"
    r"|soins\s+légers|lancer\s+des\s+et\s+soigne|ressusci\w*"
    r"|repos\w*|r[éè]cup[éèe]r\w*|r[ée]tabl\w*|régénér\w*|bandag\w*)\b",
    _re_mod.IGNORECASE,
)


# Prose intermédiaire d'attaque « brouillon supplanté » : blocs narrés AVANT
# les outils qui décrivent le résultat d'une attaque en prose. Quand le rejeu
# correctif 5bis-a réussit et appelle les outils, ces blocs sont supplantés
# par la narration finale (avec jets réels) — sinon la même attaque apparaît
# deux fois dans le message final (bug vécu 5f3e31c9 : double description de
# la hache qui s'abat).
_RE_PROSE_RESOLUTION = _re_mod.compile(
    r"\b(encaiss\w*|touch\w*|dégâts|degats|bless\w*|s'enfonc\w*|s'abat\w*"
    r"|manqu\w*|esquiv\w*|raté\w*|rate\w*|inflige\w*|subit\w*)\b",
    _re_mod.IGNORECASE,
)

# Bandeau « Au tour de … » recopié par le LLM : le petit modèle ré-émet le
# footer déterministe (avec PV inventés + « Que décidez-vous de faire ? »)
# qu'il a vu en contexte — la table voit alors 2× le même appel d'action.
# Le serveur retire ces copies avant d'ajouter SA version officielle.
_RE_AUTOUR_STRIP = _re_mod.compile(
    r"(?:\n\s*)?⚔[\uFE0F\uFE0E]?\s*\*\*Au tour de .*?\(joueur\s+.+?\)\s*"
    r"de décider une action\.[^\n]*(?:\n\s*🎯[^\n]*)*"
    r"(?:\n\s*Que décidez-vous[^\n]*)?",
    _re_mod.IGNORECASE,
)

# Bandeau de charge / encombrement recopié par le LLM : la fiche du PJ affiche
# DÉJÀ une jauge de poids transporté côté interface — le MJ ne doit pas remettre
# cette valeur mécanique dans sa narration (répétée à CHAQUE tour, partie
# 5a9b99c8 : « Votre charge actuelle est de 41,35 kg (26,3% de votre
# capacité). Vous sentez une tension… »). On retire la phrase de charge (souvent
# la 1re d'un paragraphe, suivie d'autres phrases) ou le bandeau d'outil verbatim
# (« ⚖️ **Charge transportée : 41.35 kg / 157 kg (26.3%) — encombrement :
# Légère.** »).
_RE_CHARGE_STRIP = _re_mod.compile(
    r"[ \t]*(?:Votre|Ta|Sa|La|Leur)\s+charge\b[^.\n]*?\bkg\b[^.\n]*?\."
    r"|(?:[ \t]*(?:🧺\s*)?(?:⚖[\uFE0F\uFE0E]?\s*)?\**\s*Charge\s+transport[ée]e"
    r"\b[^\n]*)"
    r"|(?:[ \t]*[^.\n]*?\bencombrement\s*:[^.\n]*?\.)",
    _re_mod.IGNORECASE,
)

# Footer mécanique recopié par le LLM depuis le side panel (« 📍 **Position
# actuelle :** … 🎒 **Objet clé :** … 🏰 **Destination :** … ») : ces repères
# sont DÉJÀ affichés par l'interface, le MJ ne doit pas les remettre dans sa
# narration (partie 4b529064 : le MJ recopiait « Objet clé : Couronne de
# Mystra (dans votre inventaire) »). Retrait segment par segment (le footer
# tient souvent sur une seule ligne, chaque segment s'arrête au repère suivant).
_RE_FOOTER_MECANIQUE_STRIP = _re_mod.compile(
    r"[ \t]*(?:📍|🎒|🏰|🗺️|🧭)\s*\*{0,2}\s*"
    r"(?:Position actuelle|Objet cl[ée]|Destination|Lieu(?: actuel)?|"
    r"Itin[ée]raire)\s*:?\*{0,2}\s*[^📍🎒🏰🗺️🧭\n]*",
    _re_mod.IGNORECASE,
)

# Consignes destinées au LLM qui fuient dans la narration via la
# régularisation 5ter (le texte BRUT des outils est concaténé au message
# joueur, partie 5b4e2bbe) : ligne d'instruction « 🎭 TA NARRATION… », rappel
# de rotation nommant l'outil `terminer_mon_tour`, et incises « recopie CE
# bonus dans `lancer_degats` ». Ces éléments ne doivent JAMAIS être montrés.
_RE_CONSIGNES_LLM_STRIP = _re_mod.compile(
    r"[ \t]*🎭\s*TA NARRATION[^\n]*"
    r"|[ \t]*_⚙️ Rotation gérée par le SERVEUR[^\n]*"
    r"|[ \t]*—\s*recopie CE bonus dans `lancer_degats`[^.\n]*\.?"
    r"|[ \t]*—?\s*Relance `lancer_degats`[^.\n]*\.?"
    r"|[ \t]*\((?:inventaire_ajouter|inventaire_consommer_munition)\)"
    r"\s*:[^.\n]*\.?"
    r"|>[ \t]*———?\s*et engage via `engager_combat`[^\n]*"
    r"|[ \t]*⚔️\s*Ennemis DU SC[ÉE]NARIO[^\n]*"
    r"|[ \t]*📝\s*Note du module[^\n]*",
    _re_mod.IGNORECASE,
)

# En-têtes des blocs mécaniques ajoutés par le SERVEUR en fin de tour
# (initiative, résolutions, dégâts auto, victoire…). Sert à isoler la PROSE
# du MJ de la mécanique avant de la stocker comme contexte : le petit modèle
# recopiait ensuite ces blocs dans sa prose (partie 5b4e2bbe).
_RE_BLOC_MECANIQUE_SERVEUR = _re_mod.compile(
    r"(?:^|\n)[ \t]*(?:"
    r"⚙️\s*_"
    r"|⚔️\s*_(?:Résolution automatique|Tous les ennemis)"
    r"|⚔️\s*\*\*(?:Au tour de|Combat engagé)"
    r"|⚖️\s*_"
    r"|🎲\s*\*\*Initiative du combat"
    r"|🎲\s*_Jets officiels du tour"
    r"|🏆\s*\*\*Victoire"
    r")"
)


def _narration_prose_seule(narration: str) -> str:
    """Retourne la PROSE du MJ seule, sans les blocs mécaniques ajoutés par le
    serveur en fin de tour. Ces blocs (initiative, jets, résolutions, victoire)
    sont destinés à l'affichage mais NE doivent PAS être stockés comme contexte
    du tour suivant : le petit modèle les recopiait ensuite dans sa prose
    (partie 5b4e2bbe). Les blocs sont toujours ajoutés APRÈS la prose ; on
    tronque donc au premier en-tête mécanique rencontré."""
    if not narration:
        return narration
    m = _RE_BLOC_MECANIQUE_SERVEUR.search(narration)
    if m:
        narration = narration[:m.start()]
    return narration.strip()


# ⚙️ Harmonisation de l'état narré sur l'état SERVEUR. Le petit modèle local
# écrit des chiffres plausibles mais FAUX — partie 5b4e2bbe : fiche officielle
# « pv 2 / pv_max 16 » pendant que la narration affichait « PV Barkrur : 13/17 »
# trois fois dans la même scène. La valeur SERVEUR, finale du tour, fait foi.
# On réécrit chaque bloc « PV <nom> : a/b » et « CA <nom> : n » vers les
# valeurs officielles : PJ depuis l'état de partie, monstres depuis
# monstres_combat (détruit → « détruit »), entité inconnue → bloc supprimé.
_RE_PV_BLOC_NARRE = _re_mod.compile(
    r"(?:\*\*\s*)?PV\s+(?P<nom>[A-Za-zÀ-ÿŒœ][^\n:*，,]{0,40}?)\s*"
    r"[:：]\s*\*{0,2}\s*\d{1,3}\s*/\s*\d{1,3}\s*\*{0,2}(?:\s*\([^)]*\))?",
    _re_mod.IGNORECASE,
)
# 🔧 Bêta (résidu 1b) : format NARRATIF « Les points de vie de <nom> chutent
# à 10/11 » — l'harmonisation ne couvrait que « PV <nom> : a/b » et laissait
# des valeurs inventées contredire l'état serveur (partie réelle : « chutent
# à 10/11 » narré alors que le PJ était à -1, Mourant).
_RE_PV_CHUTE_NARRE = _re_mod.compile(
    r"points?\s+de\s+vie\s+de\s+(?P<nom>[A-Za-zÀ-ÿ' -]{2,40}?)\s+"
    r"(?P<verbe>chut\w*|pass\w*|tomb\w*|descend\w*|baiss\w*|effondr\w*|retomb\w*)"
    r"(?:\s*brutalement)?\s+à\s*(?P<pv>\d{1,3})\s*(?:/|sur)\s*(?P<pm>\d{1,3})",
    _re_mod.IGNORECASE,
)
_RE_CA_BLOC_NARRE = _re_mod.compile(
    r"(?:\*\*\s*)?CA\s+(?P<nom>[A-Za-zÀ-ÿŒœ][^\n:*，,]{0,40}?)\s*"
    r"[:：]\s*\*{0,2}\s*\d{1,3}\*{0,2}",
    _re_mod.IGNORECASE,
)


def _to_int_fiable(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None and str(v).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def _harmoniser_statut_serveur(
    narration: str,
    pj: list[dict],
    monstres: list[dict],
) -> str:
    """Réécrit les blocs « PV <nom> : a/b » / « CA <nom> : n » de la narration
    sur les valeurs officielles FINALES du tour (state_patch appliqué)."""
    if not narration:
        return narration
    pj_map: dict[str, dict] = {
        _norm_nom_objet(p.get("nom")): p for p in pj if isinstance(p, dict)
    }
    mon_map: dict[str, dict] = {
        _norm_nom_objet(m.get("nom")): m for m in monstres if isinstance(m, dict)
    }

    def _remplace_pv(m: re.Match) -> str:
        cle = _norm_nom_objet(m.group("nom"))
        if cle in mon_map:
            _mo = mon_map[cle]
            _label = str(_mo.get("nom") or m.group("nom"))
            _pv = _to_int_fiable(_mo.get("pv"))
            _pm = _to_int_fiable(_mo.get("pv_max"))
            if _pv is not None and _pm and _pv <= 0:
                return f"**PV {_label} :** détruit"
            if _pv is not None and _pm:
                return f"**PV {_label} :** {_pv}/{_pm}"
            return ""
        if cle in pj_map:
            _p = pj_map[cle]
            _label = str(_p.get("nom") or m.group("nom"))
            _pv = _to_int_fiable(_p.get("pv"))
            _pm = _to_int_fiable(_p.get("pv_max"))
            if _pv is not None and _pm:
                return f"**PV {_label} :** {_pv}/{_pm}"
            return ""
        return ""

    def _remplace_ca(m: re.Match) -> str:
        cle = _norm_nom_objet(m.group("nom"))
        if cle in mon_map:
            _mo = mon_map[cle]
            _label = str(_mo.get("nom") or m.group("nom"))
            _ca = _to_int_fiable(_mo.get("ac"))
            if _ca is not None:
                return f"**CA {_label} :** {_ca}"
            return ""
        if cle in pj_map:
            _p = pj_map[cle]
            _label = str(_p.get("nom") or m.group("nom"))
            _ca = _to_int_fiable(_p.get("ca"))
            if _ca is not None:
                return f"**CA {_label} :** {_ca}"
            return ""
        return ""

    _nar = _RE_PV_BLOC_NARRE.sub(_remplace_pv, narration)
    _nar = _RE_CA_BLOC_NARRE.sub(_remplace_ca, _nar)

    # 🔧 Bêta (résidu 1b) : chutes narratives (« points de vie de X chutent
    # à 10/11 ») — le PV COURANT narré est réécrit sur la valeur serveur
    # (le PJ mourant à -1 doit rester mourant dans le récit).
    def _remplace_chute(m: re.Match) -> str:
        cle = _norm_nom_objet(m.group("nom"))
        source = None
        if cle in pj_map:
            source = pj_map[cle]
        elif cle in mon_map:
            source = mon_map[cle]
        if source is None:
            return m.group(0)
        _pv = _to_int_fiable(source.get("pv"))
        _pm = _to_int_fiable(source.get("pv_max"))
        if _pv is None or not _pm:
            return m.group(0)
        _label = str(source.get("nom") or m.group("nom"))
        return f"points de vie de {_label} {m.group('verbe')} à {_pv}/{_pm}"

    _nar = _RE_PV_CHUTE_NARRE.sub(_remplace_chute, _nar)
    return _nar.strip()


def _dedupliquer_phrases(narration: str, seuil: int = 25) -> str:
    """Retire les lignes/paragraphes répétés de la narration.

    Le petit modèle local répète parfois verbatim la même phrase ou le même
    paragraphe — JUXTAPOSÉS (partie 4b529064 : phrase du parchemin narrée
    deux fois de suite) mais aussi DISPERDÉS dans le message (partie réelle
    sept. 2026 : « Votre épée longue heurte l'air… » recopiée 4 fois entre
    des blocs mécaniques, bandeau « Phase : Combat » triplé). Un doublon
    strict n'apporte aucune information : on n'en garde que la PREMIÈRE
    occurrence, où qu'elle soit. La comparaison normalise espaces, casse et
    ponctuation finale, et ne porte que sur les lignes assez longues
    (`seuil`) : les lignes courtes (titres, dialogues brefs, listes
    mécaniques) ne sont jamais touchées. Les lignes vides sont conservées
    telles quelles.
    """
    if not narration:
        return narration
    sortie: list[str] = []
    vues: set[str] = set()
    for ligne in narration.split("\n"):
        cle = " ".join(ligne.split()).strip().lower().rstrip(".!?…:;,»\"'")
        if cle and len(cle) >= seuil:
            if cle in vues:
                continue
            vues.add(cle)
        sortie.append(ligne)
    return "\n".join(sortie)


# 🛡️ (correctif bandeau) — les tool texts contiennent des INSTRUCTIONS
# destinées au LLM (function-calling) qui n'ont rien à faire dans le bandeau
# affiché à la table : « — recopie CE bonus dans `lancer_degats` (jamais un
# bonus improvisé) » est une consigne de recopie du modèle, pas une règle
# visible du joueur. Les blocs player-facing (jets officiels du tour,
# résolution automatique, events) sont épurés SANS toucher au texte envoyé
# AU LLM (le MJ garde ses consignes de recopie).
_RE_FRAGMENTS_LLM_TABLE = _re_mod.compile(
    r"\s*—\s*recopie CE bonus dans `?lancer_degats`?[^.\n]*\.?"
    r"|\s*\(jamais un bonus improvisé\)"
)


def _epure_pour_table(texte: str) -> str:
    """Retire les fragments d'instruction LLM d'un texte affiché à la table.

    « 💪 Bonus dégâts officiel : +3 (FOR 17 (+3)) — recopie CE bonus dans
    `lancer_degats` (jamais un bonus improvisé). » →
    « 💪 Bonus dégâts officiel : +3 (FOR 17 (+3)) » — le chiffre et sa
    provenance restent, la consigne au modèle disparaît.
    """
    if not texte:
        return ""
    return _RE_FRAGMENTS_LLM_TABLE.sub("", texte).rstrip()


# --------------------------------------------------------------------------- #
#  🧹 Bêta (M3) — Assainissement de la narration FINALE avant diffusion.
#
#  Partie réelle : le petit modèle livrait à la table ses COULISSES
#  (« Vérifions son état réel. », « le système n'a pas encore mis à jour ses
#  PV… », « Résultat : Touché ! () » avec parenthèses vides, « vous avez
#  réussi votre ! ») et DOUBLAIT des paragraphes entiers en reformulant
#  (deux versions de la même action dans le même message). Ces défauts
#  échappaient à `_dedupliquer_phrases` (verbatim uniquement) — on nettoie
#  donc : (1) templates cassés, (2) échos mécaniques « Résultat : … »,
#  (3) paragraphes méta-discursifs, (4) paraphrases quasi identiques.
# --------------------------------------------------------------------------- #
_RE_VERDICT_PARENS_VIDES = _re_mod.compile(
    r"\b([Tt]ouch[ée]|[Mm]anqu[ée]|[Rr][ée]ussite|[ÉEé]chec|[Rr][ée]ussi|[Rr]at[ée])"
    r"(\s*(?:!|\.))?\s*\(\s*\)"
)
_RE_POSSESSIF_SUSPENDU = _re_mod.compile(r"\b(?:votre|vos|son|sa|ses)\s*!")
_RE_ECHO_RESULTAT = _re_mod.compile(r"^\s*R[ée]sultat\s*:.*$", _re_mod.MULTILINE)
_RE_PARAGRAPHE_META = _re_mod.compile(
    r"(v[ée]rifi\w*\s+(?:son|leur|sa|l')?\s*[ée]tat"
    r"|[ée]tat\s+r[ée]el"
    r"|le\s+syst[èe]me\s+n'a\s+pas"
    r"|le\s+syst[èe]me\s+semble\s+avoir\s+perdu"
    r"|quelque\s+chose\s+ne\s+va\s+pas"
    r"|nous\s+devons\s+d'abord\s+r[ée]tablir\s+le\s+contexte"
    r"|n'a\s+pas\s+encore\s+mis\s+à\s+jour"
    r"|devrait\s+[êe]tre\s+mort"
    r"|^en\s+r[ée]alit[ée]\b"
    r"|^note\s*:"
    r"|r[ée]g[ée]n[ée]ration\s+rapide"
    r"|^en\s+tant\s+que\s+(mj|mod[èe]le|assistant)"
    r"|^commentaire\s*:)",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)
# 🔧 Bêta (résidu 1a) : TIMEOUTS INVENTÉS — le LLM narrait un « délai de
# trente secondes expiré » alors qu'aucun timeout serveur n'a eu lieu (la
# rotation est gérée par le moteur, qui émet son propre événement « passé
# automatiquement »). Un paragraphe qui RAconte une expiration de délai est
# donc de la fiction fausse : purgé (l'événement serveur, lui, est conservé).
_RE_PARAGRAPHE_TIMEOUT_INVENTE = _re_mod.compile(
    r"(expir\w*\s+(?:d[ée]lai|dans\s+la\s+limite)"
    r"|d[ée]lai\s+(?:de\s+)?(?:trente|\d+)\s+secondes"
    r"|limite\s+\w*\s*des\s+trente\s+secondes"
    r"|le\s+tour\s+(?:de\s+\S+\s+)?(?:a\s+)?expir\w*"
    r"|tour\s+expir\w*\s+(?:automatiquement|sans))",
    _re_mod.IGNORECASE,
)
# Seuil de similarité (Jaccard sur les mots > 3 lettres) au-delà duquel deux
# paragraphes sont considérés comme la MÊME scène re-racontée.
_SEUIL_SIMILARITE_PARAGRAPHE = 0.55


def _jaccard_mots(a: str, b: str) -> float:
    """Similarité Jaccard entre deux textes (mots > 3 lettres, sans accents)."""
    import unicodedata as _ud

    def _tokens(t: str) -> set[str]:
        nf = _ud.normalize("NFKD", t.lower())
        sans = "".join(c for c in nf if not _ud.combining(c))
        return {w for w in _re_mod.split(r"[^a-zà-ÿ]+", sans) if len(w) > 3}

    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# 🛡️ B40/B43/B44 (audit parties complètes) : le serveur injecte au modèle des
# blocs de CONSIGNE (contenu canonique de salle, portes existantes, outils à
# appeler) qui étaient diffusés au joueur tels quels, à l'impératif, avec des
# noms d'outils — en contradiction avec la narration du même message. Ils sont
# désormais routés vers un canal séparé : le joueur ne les voit plus.
_RE_BLOC_CONSIGNE = _re_mod.compile(
    r"^\s*(?:"
    r"[\U0001f4dc\U0001f6aatreud\U0001f5bc\U0001f465\u26d4\u26a0\ufe0f"
    r"]\s+.*$"
    r"|(?:Contenu canonique|Portes (?:EXISTANTES|de cette salle)|Entrée :"
    r"|Carte :|PNJ :|Cette salle N'EST|Interdit de|Entrée de la salle)"
    r".*$"
    r"|(?:\u2699\ufe0f\s+_)?Le serveur a fait entrer le groupe"
    r")\s*$",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)

# Préfixes d'écho du message joueur réinjecté dans la prose du MJ.
_RE_ECHO_JOUEUR = _re_mod.compile(
    r"^\s*\*\*\[[^\]\n]{1,40}\]\*\*\s*[:：]\s*", _re_mod.MULTILINE)

# Méta-commentaires du modèle sur sa propre conduite.
_RE_META_PARENTHETIQUE = _re_mod.compile(
    r"^\s*\*\(\s*(?:Note|NB|Remarque|Je lance|J'ai corrigé|Je dois|"
    r"Précision|Correction)[^)\n]{0,400}\)\*\s*$",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)

# Consignes adressées au modèle (deuxième personne impérative, noms d'outils).
_RE_CONSIGNE_MODELE = _re_mod.compile(
    r"^\s*(?:Suis FIDÈLEMENT|n'improvise\b|n'invente\b|n'arrive pas|"
    r"engage via `|décris-les|fais-les réagir|Reprends l'action hors|"
    r"réapprovisionne-toi|Narre les cadavres|pour des renforts d'un combat)"
    r".*$",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)

_MARQUEURS_CONSIGNE = (
    "📜", "🚪", "🖼️", "👥", "⛔", "🎨",
    "Contenu canonique de la salle",
    "Portes EXISTANTES dans la salle",
    "Portes de cette salle (ce qui est CONNU",
    "Entrée : ",
    "Entrée de la salle",
    "Suis FIDÈLEMENT les descriptions canoniques",
    "n'improvise ni salle ni rencontre hors module",
    "Ce sont les SEULES sorties",
    "Cette salle N'EST **PAS** VIDE",
    # 🛡️ B40 (2e passe) : le contenu canonique de salle (« NE RÉINVENTE PAS
    # cette salle : reprends FIDÈLEMENT… », « ⬅️ Le groupe est ARRIVÉ ICI par
    # la porte SUD — ne la confonds PAS avec une sortie ») était encore
    # affiché au joueur, à l'impératif, avec la description du module.
    "NE RÉINVENTE PAS cette salle",
    "NE RÉINVENTE PAS",
    "reprends FIDÈLEMENT",
    "Le groupe est ARRIVÉ ICI",
    "le groupe est ARRIVÉ ICI",
    "ne la confonds PAS",
    "NE la confonds PAS",
    "ARRIVÉ ICI par la porte",
    "⬅️",
    "Type : ",
    "Description enregistrée :",
    "DÉJÀ VISITÉE",
    "Trésor : ",
    "Portes de cette salle",
    "Rappel : `engager_combat`",
    "Interdit de narrer",
    "engage via `engager_combat`",
    "Reprends l'action hors initiative",
    "combat_ajouter_combattant",
    "inventaire_ajouter",
    "carte_donjon_explorer",
    "_CORRECTIF",
    "⚠️ CORRECTION",
    "ℹ️ SYSTÈME",
    "Système :",
    "tool_choice",
    "narration_intermédiaire supplantée",
)


# 🛡️ M2 (audit parties complètes) : le MJ recopiait parfois des morceaux de
# son propre état interne dans la narration — JSON brut, dict Python, clés
# techniques, note d'outil rejoué. Ces fragments sont purgés du texte joueur.
_RE_JSON_ETAT = _re_mod.compile(
    r"\{\s*['\"]?(?:phase|pj|donjon|monstres?\w*|courant\w*|meta|"
    r"inventaire|initiative|tour\w*|quete|bible|memoire|salles?\w*|"
    r"tool_calls?|state_patch\w*|progression\w*)['\"]?\s*:", _re_mod.IGNORECASE)
_RE_PY_DICT = _re_mod.compile(r"\{\s*'[A-Za-z_][\w]*'\s*:")
_RE_CLE_TECHNIQUE = _re_mod.compile(
    r"^\s*(?:state_patch\w*|tool_calls?_trace|tool_events|"
    r"narrations?_intermediaires?|notes_mecaniques?|iterations|corrections|"
    r"simulation_attempted)\s*[:=]", _re_mod.IGNORECASE)


def _est_fragment_etat(ligne: str) -> bool:
    """Vrai pour une ligne qui est un fragment d'état interne, pas du jeu."""
    a = ligne.strip()
    if not a:
        return False
    if _RE_JSON_ETAT.search(a) or _RE_PY_DICT.search(a) or _RE_CLE_TECHNIQUE.match(a):
        return True
    # Un fragment d'état contient presque toujours une accolade ouvrante ET
    # un deux-points, sans phrase de jeu.
    return ("{" in a and ":" in a and " " not in a.split("{")[0][:30]
            and len(a) < 400)


def _est_ligne_consigne(ligne: str) -> bool:
    """Vrai pour une ligne qui est une consigne serveur→modèle, pas du jeu."""
    a = ligne.strip()
    if not a:
        return False
    if any(m in a for m in _MARQUEURS_CONSIGNE):
        return True
    if _est_fragment_etat(a):
        return True
    return bool(_RE_CONSIGNE_MODELE.match(a) or _RE_META_PARENTHETIQUE.match(a))


def _purger_consignes(texte: str) -> str:
    """Retire les blocs de consignes serveur→modèle et les échos du message
    joueur, en préservant les paragraphes de jeu (B40/B43/B44/B14)."""
    if not texte or not texte.strip():
        return texte or ""
    # Un bloc de consignes est souvent un paragraphe multi-lignes cohérent :
    # on supprime tout paragraphe dont une ligne significative est une consigne.
    paragraphes = texte.split("\n\n")
    gardes: list[str] = []
    for para in paragraphes:
        lignes = para.split("\n")
        utiles = [l for l in lignes if l.strip()]
        if not utiles:
            continue
        if sum(1 for l in utiles if _est_ligne_consigne(l)) >= 1 and len(
                utiles) <= 14:
            # Paragraphe de consigne : on garde les éventuelles lignes de jeu
            # qu'il contiendrait (une prose mélangée à une consigne).
            reste = [l for l in utiles if not _est_ligne_consigne(l)
                     and not l.strip().startswith(("•", "-", "*"))]
            if reste:
                gardes.append("\n".join(reste))
            continue
        gardes.append(para)
    sortie = "\n\n".join(gardes)
    sortie = _RE_ECHO_JOUEUR.sub("", sortie)
    return _re_mod.sub(r"\n{3,}", "\n\n", sortie).strip()


def _nettoyer_meta_narration(narration: str) -> str:
    """Nettoie la narration finale : coulisses LLM, consignes serveur→modèle
    (B40/B43/B44), templates cassés, paraphrases doublées."""
    if not narration or not narration.strip():
        return narration or ""

    texte = narration
    # 🛡️ B40/B43/B44 : purge des consignes AVANT le dédup paragraphe, sinon un
    # bloc de consignes dédoublonnait la narration voisine.
    texte = _purger_consignes(texte)
    if not texte.strip():
        return narration or ""

    # (1) Verdicts à parenthèses vides : « Touché ! () » → « Touché ! ».
    texte = _RE_VERDICT_PARENS_VIDES.sub(r"\1\2", texte)
    # (2) Possessif suspendu : « réussi votre ! » → « réussi ! ».
    texte = _RE_POSSESSIF_SUSPENDU.sub("!", texte)
    # (3) Échos mécaniques d'un outil rejoué en prose : « Résultat : Touché ! ».
    texte = _RE_ECHO_RESULTAT.sub("", texte)

    # (4)+(5) Paragraphe par paragraphe : retire les méta-commentaires et les
    # paraphrases quasi identiques (on garde la PREMIÈRE version, plus
    # fidèle à l'action déclarée).
    paragraphes = [p for p in texte.split("\n\n")]
    sortie: list[str] = []
    gardes: list[str] = []
    for p in paragraphes:
        aplati = " ".join(p.split())
        if not aplati:
            continue
        if _RE_PARAGRAPHE_META.search(aplati):
            continue
        # 🔧 Bêta (résidu 1a) : les timeouts INVENTÉS (« le délai de trente
        # secondes expiré consomme son tour ») sont purgés — le vrai timeout
        # est narré par l'événement serveur « passé automatiquement ».
        if _RE_PARAGRAPHE_TIMEOUT_INVENTE.search(aplati) and (
            "passé automatiquement" not in aplati.lower()
        ):
            continue
        if len(aplati) >= 60:
            duplicata = any(
                _jaccard_mots(aplati, g) >= _SEUIL_SIMILARITE_PARAGRAPHE
                for g in gardes
            )
            if duplicata:
                continue
            gardes.append(aplati)
        sortie.append(p)

    return _re_mod.sub(r"\n{3,}", "\n\n", "\n\n".join(sortie)).strip()


# Tools qui CONSOMMENT l'action standard du personnage courant : dès que le
# joueur actif en a appelé un, le moteur serveur avance la rotation (le LLM
# n'a plus à se souvenir de tour_suivant_combat).
_ACTION_CONSOMMEE_TOOLS = {
    "lancer_attaque", "lancer_degats", "lancer_sauvegarde",
    "fiche_perso_infliger_degats", "fiche_perso_soigner",
    "fiche_perso_niveau_negatif", "inventaire_consommer_munition",
    "terminer_mon_tour",
}




# Logging : active le logger de l'orchestrateur (dnd35.orchestrator) qui trace
# chaque appel d'outil — indispensable pour diagnostiquer Gemma.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# --------------------------------------------------------------------------- #
#  Lifespan : initialise clients singleton, dispose proprement.
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    # Auto-discovery des @tool
    tools = discover_tools("server.tools")
    app.state.tools = tools
    print(f"[dnd35] {len(tools)} tools chargés : {', '.join(tools.keys())}")

    # Configure le registre des sessions avec le data_dir pour persistence
    # conversationnelle (history survit aux redémarrages serveur).
    sessions.configure(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        max_history_events=cfg.game.max_history_events,
    )

    # Singleton client Ollama. Le choix de modèle persisté (via /api/model)
    # prime sur config.yaml — il vit dans data_dir (montage writable en Docker,
    # contrairement à config/ qui est monté read-only).
    choice_path = cfg.abs(cfg.paths.data_dir) / "model_choice.json"
    if choice_path.is_file():
        try:
            saved = json.loads(choice_path.read_text(encoding="utf-8"))
            m = (saved.get("model") or "").strip()
            if m:
                cfg.llm.model = m
        except (json.JSONDecodeError, OSError):
            pass
    # Réglages persistés via le GUI (bouton maître de la galerie d'images) —
    # même mécanique que model_choice : prime sur config.yaml au démarrage.
    # Verrous durs : la valeur BRUTE de config.yaml est autoritaire — si une
    # catégorie (monstres/salles/scènes) y est coupée, settings.json ne peut
    # pas la réactiver et l'onglet correspondant disparaît de l'interface.
    settings_path = cfg.abs(cfg.paths.data_dir) / "settings.json"
    for _cle in ("monstres", "salles", "scenes"):
        if not getattr(cfg.image, f"{_cle}_config"):
            setattr(cfg.image, f"{_cle}_enabled", False)
    if settings_path.is_file():
        try:
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            all_on = (saved.get("images") or {}).get("all_enabled")
            if isinstance(all_on, bool):
                cfg.image.set_all(all_on)
        except (json.JSONDecodeError, OSError):
            pass
    client = OllamaClient(cfg.llm)
    available = await client.list_models()
    model_names = [m.get("id", "") for m in available]
    if available and cfg.llm.model not in model_names:
        print(
            f"[dnd35] ⚠️ Modèle '{cfg.llm.model}' absent d'Ollama "
            f"(disponibles : {', '.join(model_names)}). "
            f"Pensez à `ollama pull {cfg.llm.model}`."
        )
    else:
        print(f"[dnd35] Backend LLM OK : {cfg.llm.base_url} / {cfg.llm.model}")

    app.state.client = client
    app.state.prompt_builder = PromptBuilder(cfg)

    # Arbitrage VRAM LLM ↔ ComfyUI : le modèle est déchargé avant chaque
    # génération d'image hors tour et rechargé peu après (voir server/gpu.py).
    # Sans cela ComfyUI n'a que ~950 Mo de VRAM libre et bascule en CPU.
    _gpu.configure_llm(
        unload=client.unload_model,
        load=client.ensure_model_loaded,
        before_comfy=cfg.llm.unload_before_image,
        reload_delay_s=cfg.llm.reload_after_image_seconds,
    )

    # Store RAG ChromaDB — désactivé si `rag.enabled: false` dans la config.
    rag_store: Optional[RagStore] = None
    if cfg.rag.enabled:
        try:
            rag_store = RagStore(cfg)
            dims = await rag_store.embedder.name_dims()
            if dims is None:
                print(
                    f"[dnd35] ⚠️ Embedding '{cfg.rag.embedding_model}' inaccessible "
                    f"sur '{cfg.rag.embedding_base_url or cfg.llm.base_url}' — RAG "
                    f"désactivé. (Vérifiez que le serveur d'embeddings tourne.)"
                )
                await rag_store.embedder.aclose()
                rag_store = None
            else:
                stats = rag_store.stats()
                total = sum(stats.values())
                print(
                    f"[dnd35] RAG OK : embeddings '{dims[0]}' (dim {dims[1]}) — "
                    f"{total} chunk(s) persistés dans {cfg.rag.persist_dir}"
                )
                if total == 0:
                    print("[dnd35] ⚠️ Aucun chunk dans le vector store — lancez "
                          "`py -m server.rag --ingest` pour populiser la base.")
        except Exception as e:                                   # noqa: BLE001
            print(f"[dnd35] ⚠️ Échec d'initialisation du RAG : {e}")
            rag_store = None
    app.state.rag_store = rag_store

    yield

    await client.aclose()
    if rag_store is not None:
        await rag_store.embedder.aclose()
    print("[dnd35] Arrêt propre terminé.")


app = FastAPI(title="D&D 3.5 — Maître du Jeu", lifespan=lifespan)

cfg = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _dossier_donnees() -> str:
    return str(cfg.abs(cfg.paths.data_dir))


async def utilisateur_courant(
    authorization: str = Header(default=""),
) -> str:
    """Dépendance FastAPI : renvoie le nom d'utilisateur authentifié ou 401.

    Doit être défini AVANT les routes qui l'utilisent en valeur par défaut
    (`Depends(utilisateur_courant)` est évalué à la décoration)."""
    nom = auth_mod.utilisateur_depuis_header(_dossier_donnees(), authorization)
    if not nom:
        raise HTTPException(status_code=401, detail="Non authentifié.")
    return nom


def _ctx(partie_id: str, player: str) -> ToolContext:
    return ToolContext(
        partie_id=partie_id,
        joueur=player,
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
    )


def _hash_mot_de_passe(mdp: str) -> str:
    """Hash SHA-256 du mot de passe de partie (jamais stocké en clair)."""
    return hashlib.sha256(mdp.encode("utf-8")).hexdigest()


def _party_password_hash(partie_id: str) -> Optional[str]:
    """Lit le hash du mot de passe dans l'état persistant de la partie."""
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    if "_erreur" in etat:
        return None
    h = etat.get("meta", {}).get("mot_de_passe_sha256")
    return h or None


def _model_choice_path() -> Path:
    return cfg.abs(cfg.paths.data_dir) / "model_choice.json"


def _orchestrator(app: FastAPI) -> Orchestrator:
    return Orchestrator(
        client=app.state.client,
        tools=app.state.tools,
        tool_mode=cfg.llm.tool_mode,
        detect_simulation=cfg.llm.detect_simulation,
        max_iterations=cfg.llm.max_tool_iterations,
        max_tools_exposed=cfg.llm.max_tools_exposed,
        tool_temperature=cfg.llm.tool_temperature,
        decision_phase=getattr(cfg.game, "decision_phase", True),
    )


# --------------------------------------------------------------------------- #
#  Narration des mécaniques de combat résolues par le serveur
# --------------------------------------------------------------------------- #
#  Philosophie demandée à la table : le SERVEUR exécute d'abord toute la
#  mécanique (jets des monstres, stabilisations, clôture/XP), PUIS le LLM
#  narre ces résultats. Fini les blocs bruts « ⚙️ Mécanique du tour » qui
#  venaient CONTREDIRE la prose du LLM après coup.
_MOTS_COMMUNS_EVENTS = {
    "attaque", "attaques", "dégâts", "degats", "dégat", "touché", "touche",
    "toucher", "manqué", "manque", "total", "formule", "jets", "bruts",
    "bonus", "mécanique", "mecanique", "tour", "tours", "round", "rounds",
    "invalide", "mourant", "morte", "mort", "détruit", "detruit", "combat",
    "victoire", "défaite", "defaite", "griffe", "griffes", "morsure",
    "coup", "coups", "grâce", "grace", "stabilisation", "stabilisé",
    "stabilise", "serveur", "résolus", "resolus", "dernier", "message",
    "événements", "evenements", "expérience", "experience", "points",
    "initiative", "passé", "passe", "condition", "conditions", "arme",
}


def _mecanique_deja_narree(events: list[str], narration: str) -> bool:
    """True si la narration du LLM intègre déjà les événements mécaniques
    (assez de combattants/objets cités dans les events se retrouvent dans la
    prose). Évite de DOUBLER une narration correcte avec le bloc brut
    « ⚙️ Mécanique résolue par le serveur » — la duplication donnait
    l'impression que le serveur « corrigeait » le MJ après coup. En cas de
    doute on renvoie False (le bloc brut reste le filet de sécurité)."""
    if not events:
        return True
    bas = (narration or "").lower()
    mots: set[str] = set()
    for ev in events:
        for w in _re_mod.findall(r"[A-Za-zÀ-ÿ'’]{4,}", ev):
            lw = w.lower()
            if lw not in _MOTS_COMMUNS_EVENTS:
                mots.add(lw)
    if not mots:
        return False
    cites = sum(1 for w in mots if w in bas)
    return cites >= 2 and cites >= len(mots) // 3


def _note_mecanique_deja_narree(narration: str, note: str) -> bool:
    """True si la note mécanique (ex. « Loup-garou : 28/32 PV ») est déjà
    reflétée dans la narration (le MJ a déjà cité le même état PV). Sans ce
    test, la ligne « ⚖️ Dégâts appliqués automatiquement : » ajoutait une
    seconde copie du même chiffre (contradiction « 24/32 » vs « 28/32 » vu
    en partie 5f3e31c9)."""
    if not note:
        return False
    # Extraire le motif « N/M PV » (format le plus courant dans les notes).
    m_pv = _re_mod.search(
        r"(?:(\d{1,3})/(\d{1,3})\s*(?:PV)|PV\s*(\d{1,3})/(\d{1,3}))", note,
    )
    if not m_pv:
        return False
    n1 = m_pv.group(1) or m_pv.group(3)
    n2 = m_pv.group(2) or m_pv.group(4)
    chiffre = f"{n1}/{n2}"
    sur = f"{n1} sur {n2}"
    bas = (narration or "").lower()
    return chiffre in bas or sur in bas


def _camps_du_combat(etat: dict) -> tuple[list[str], list[str]]:
    """(noms des héros, noms des ennemis) depuis un état de partie —
    nourrit `_narrer_mecaniques_serveur` pour que la narration respecte
    les camps (un ennemi n'est jamais un compagnon)."""
    heros = [
        str(p.get("nom") or "")
        for p in (etat.get("pj") or []) if str(p.get("nom") or "")
    ]
    ennemis = [
        str(m.get("nom") or "")
        for m in (etat.get("monstres_combat") or [])
        if not m.get("allie") and str(m.get("nom") or "")
    ]
    return heros, ennemis


def _resume_plateau(etat: dict) -> str:
    """Résumé FACTUEL du plateau (PV des héros, état vivant/détruit de
    chaque ennemi) pour la narration des mécaniques serveur.

    Partie 263f82dc : le narrateur inventait des PV (« votre vitalité
    baissant à 4 sur 16 » pour 8/15 réels), faisait « ressusciter » des
    squelettes détruits (« seul le squelette (5) reste debout » pendant
    que (4) vivait) et en ré-engendrait dans la prose. Avec ce bloc, la
    narration recopie la seule vérité du plateau."""
    lignes: list[str] = []
    for p in (etat.get("pj") or []):
        if not isinstance(p, dict):
            continue
        conds = [c for c in (p.get("conditions") or []) if c]
        lignes.append(
            f"- {p.get('nom', '?')} : "
            f"{p.get('pv', '?')}/{p.get('pv_max', '?')} PV"
            + (f" — {', '.join(conds)}" if conds else "")
        )
    for m in (etat.get("monstres_combat") or []):
        if not isinstance(m, dict):
            continue
        detruit = (
            "Détruit" in (m.get("conditions") or [])
            or int(m.get("pv", 1) or 0) <= 0
        )
        lignes.append(
            f"- {m.get('nom', '?')} : "
            f"{m.get('pv', '?')}/{m.get('pv_max', '?')} PV — "
            + ("☠️ DÉTRUIT (ne peut plus agir, n'est plus une menace : "
               "n'en parle plus comme d'un adversaire actif)"
               if detruit else "vivant")
        )
    return "\n".join(lignes)


async def _narrer_mecaniques_serveur(
    app: FastAPI,
    events: list[str],
    contexte: str = "",
    heros: Optional[list[str]] = None,
    ennemis: Optional[list[str]] = None,
    plateau: str = "",
) -> str:
    """Narre les événements mécaniques DÉJÀ résolus par le moteur serveur
    (jets des monstres, coups de grâce, XP…) via un appel LLM SANS tools.

    C'est le chaînon « mécanique d'abord, narration ensuite » : le serveur
    joue, le LLM reformule en prose fidèle — jamais l'inverse. Retourne ""
    en cas d'échec : l'appelant retombe sur le bloc brut historique.

    `heros` / `ennemis` fixent les camps : sans eux, le LLM prenait les
    créatures des événements pour des compagnons des héros (partie
    87b8f286 : « votre compagnon le magmatique »).
    `plateau` = état factuel (PV vivants/détruits) : la narration recopie
    ces totaux au lieu d'inventer (« 4 sur 16 ») ou de ressusciter des
    ennemis détruits (partie 263f82dc)."""
    if not events:
        return ""
    roles = ""
    if heros:
        roles += (
            "HÉROS (personnages des joueurs — adresse-toi à eux à la 2ᵉ "
            f"personne : « vous ») : {', '.join(heros)}.\n"
        )
        if len(heros) == 1:
            roles += (
                "Il n'y a qu'UN SEUL héros : « vous » le désigne "
                "directement, lui — ne parle jamais de lui à la 3ᵉ "
                "personne ni comme d'un « compagnon ».\n"
            )
    if ennemis:
        roles += (
            "ENNEMIS (créatures HOSTILES aux héros — jamais des "
            f"compagnons, alliés ou membres du groupe) : "
            f"{', '.join(ennemis)}.\n"
        )
    if roles:
        roles += (
            "Toute créature absente de ces listes est un allié explicite "
            "ou un PNJ : suis l'étiquette (ennemi/allié) des événements. "
            "Ne qualifie JAMAIS un héros de « compagnon » : les héros "
            "sont les joueurs eux-mêmes.\n"
        )
    consignes = (
        "Tu es le maître du jeu D&D 3.5. Voici la liste EXACTE des jets et "
        "effets mécaniques DÉJÀ résolus par le moteur de jeu serveur "
        "(attaques, dégâts, stabilisations, fin de combat, XP).\n"
        "RÈGLES ABSOLUES :\n"
        "1. Narre ces événements de façon vivante et CONCISE (1 à 3 courts "
        "paragraphes), à la 2ᵉ personne pour les héros.\n"
        "2. Respecte EXACTEMENT les résultats : qui touche, qui rate, quels "
        "dégâts, quels PV restants, qui meurt. Reste sur les CONSÉQUENCES "
        "concrètes (dégâts subis, état final, mort) sans recopier la "
        "mécanique : aucune formule de dés (« 1d6+3 »), aucun « jet 17 vs "
        "CA 15 », aucune mention de serveur, d'outils ou de moteur.\n"
        "3. N'INVENTE AUCUN jet, dégât ou événement absent de la liste ; "
        "n'ajoute aucun monstre, aucun renfort, aucune action bonus.\n"
        "4. N'appelle AUCUN outil : tout est déjà résolu et inscrit.\n"
        "5. Ne pose AUCUNE question et ne demande l'avis de personne : le "
        "serveur affiche lui-même la ligne « au tour de… » quand c'est "
        "l'heure.\n"
        "6. De la prose narrative uniquement : pas de titre, pas de liste à "
        "puces, pas de section « mécanique ».\n"
        "7. Respecte les camps ci-dessus : les ennemis restent des "
        "adversaires (« la créature vous assaille »), jamais des "
        "compagnons.\n"
        "8. L'« ÉTAT DU PLATEAU » ci-dessous est la SEULE vérité : si tu "
        "mentionnes un total de PV, recopie EXACTEMENT celui de la liste "
        "(jamais un chiffre inventé) ; une créature marquée ☠️ DÉTRUITE ne "
        "peut plus agir ni menacer — ne la « ressuscite » pas et n'annonce "
        "JAMAIS d'ennemi supplémentaire absent de la liste."
    )
    contenu = (
        (("Contexte : " + contexte.strip() + "\n\n") if contexte.strip() else "")
        + (roles + "\n" if roles else "")
        + (("ÉTAT DU PLATEAU (source officielle) :\n" + plateau + "\n\n")
           if plateau.strip() else "")
        + "Événements mécaniques à narler :\n\n"
        + "\n\n".join(events)
    )
    messages = [
        Message(role="system", content=consignes),
        Message(role="user", content=contenu),
    ]
    res = await app.state.client.chat(
        messages, temperature=min(0.6, cfg.llm.temperature)
    )
    return (res.content or "").strip()


async def _renarrer_ouverture(
    app: FastAPI,
    lieu_titre: str,
    lieu_desc: str,
    quete_pitch: str = "",
) -> str:
    """Re-narre la SCÈNE D'OUVERTURE fidèlement au lieu de départ canonique.

    Partie c1f4e547 : au premier tour, le petit modèle a improvisé
    l'ouverture dans un donjon inventé (« donjon de Khundrukar » — nom
    repris de l'exemple du schéma d'outils `carte_donjon_entrer`). Le
    serveur a correctement initialisé le donjon du scénario, mais la prose
    hallucinée restait. Ici, mécanique d'abord : l'entrée est enregistrée,
    puis le LLM (SANS outils) re-narre l'ouverture à partir de la
    description canonique de la salle de départ. Renvoie '' en cas d'échec
    (la prose d'origine est conservée)."""
    consignes = (
        "Tu es le maître du jeu D&D 3.5. C'est la SCÈNE D'OUVERTURE de "
        "l'aventure : pose le décor AVANT toute sollicitation du joueur, "
        "en 4 à 6 paragraphes immersifs, à la 2ᵉ personne (« vous ») :\n"
        "1. Le lieu de départ décrit ci-dessous (décor, atmosphère, "
        "sensations : vue, sons, odeurs).\n"
        "2. La situation des héros à cet instant précis.\n"
        "3. Le PNJ principal présent et son attitude.\n"
        "4. La mission, les enjeux, ce qui presse.\n"
        "5. Termine par une invitation OUVERTE à agir — jamais une simple "
        "question fermée (« acceptez-vous ? »).\n"
        "RÈGLES ABSOLUES :\n"
        "1. La scène se déroule EXCLUSIVEMENT au lieu de départ décrit "
        "ci-dessous : n'invente AUCUN autre lieu, AUCUN « donjon de… », "
        "AUCUNE porte absente de cette description.\n"
        "2. N'appelle AUCUN outil : tout est déjà enregistré.\n"
        "3. N'invente NI monstre NI combat NI rencontre : c'est "
        "l'ouverture paisible de l'aventure.\n"
        "4. Prose narrative uniquement : pas de titre, pas de liste, "
        "aucune mention de serveur ou d'outils."
    )
    contenu = (
        (("Pitch de la quête : " + quete_pitch.strip() + "\n\n")
         if quete_pitch.strip() else "")
        + "Lieu de départ officiel — " + (lieu_titre or "point de départ")
        + " :\n« " + (lieu_desc or "").strip() + " »"
    )
    messages = [
        Message(role="system", content=consignes),
        Message(role="user", content=contenu),
    ]
    res = await app.state.client.chat(
        messages, temperature=min(0.7, cfg.llm.temperature)
    )
    return (res.content or "").strip()


# --------------------------------------------------------------------------- #
#  Routes REST
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict[str, Any]:
    backend = cfg.llm.backend  # "ollama" | "llamacpp"
    try:
        models = await app.state.client.list_models()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "backend": backend, "error": str(e)}, status_code=503
        )
    # Section RAG — opaque tant que le store est inactive (embeddings absents
    # ou base vide). Le frontend s'en sert pour afficher le badge RAG dans le bandeau.
    rag_store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    rag_info: Optional[dict[str, Any]] = None
    if rag_store is not None:
        try:
            rag_info = {"enabled": True, "collections": rag_store.stats()}
        except Exception as e:                                   # noqa: BLE001
            rag_info = {"enabled": True, "error": str(e)}
    else:
        rag_info = {"enabled": False, "collections": {}}
    return {
        "ok": True,
        "backend": backend,
        "backend_url": cfg.llm.base_url,
        "model": cfg.llm.model,
        "model_available": any(
            cfg.llm.model in m.get("id", "") or m.get("id", "").endswith(cfg.llm.model)
            for m in models
        ),
        "tools": sorted(app.state.tools.keys()),
        "tool_mode": cfg.llm.tool_mode,
        "rag": rag_info,
    }


@app.get("/api/parties")
async def list_parties() -> dict[str, Any]:
    ids = sessions.all_ids()
    # On inclut aussi les parties persistées sur disque (sans session active).
    data_dir = cfg.abs(cfg.paths.data_dir)
    on_disk = [
        p.stem[len("partie_") :]
        for p in data_dir.glob("partie_*.json")
    ]
    all_ids = list(set(ids + on_disk))
    details: dict[str, dict[str, Any]] = {}
    for pid in all_ids:
        state_obj = PartyState(
            data_dir=str(data_dir), partie_id=pid,
            max_history=cfg.game.max_history_events,
        )
        etat = state_obj.load()
        details[pid] = {
            "titre": etat.get("meta", {}).get("titre", "(sans titre)"),
            "phase": etat.get("phase", "opening"),
            "tour": etat.get("tour", 0),
            "pj": len(etat.get("pj", [])),
            # Partie protégée par mot de passe (sans révéler le hash).
            "protegee": bool(etat.get("meta", {}).get("mot_de_passe_sha256")),
            # 🔒 Propriétaire (suppression réservée au créateur — C1).
            # Parties anciennes : createur absent → suppression tolérée si
            # la partie est vide ou si le demandeur y a un PJ (cf. delete).
            "createur": etat.get("meta", {}).get("createur") or None,
        }
    return {
        "active": ids,
        "persisted": list(set(on_disk) - set(ids)),
        "details": details,
    }


@app.post("/api/parties")
async def create_party(
    payload: dict[str, Any],
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    titre = payload.get("titre") or cfg.game.default_title
    cadre = payload.get("cadre") or cfg.game.default_frame
    partie_id = payload.get("partie_id") or uuid.uuid4().hex[:8]
    mot_de_passe = (payload.get("mot_de_passe") or "").strip()
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    etat.setdefault("meta", {}).update({
        "titre": titre,
        "cadre": cadre,
        "regles": "D&D 3.5",
        # 🔒 Bêta (C1) : le créateur devient propriétaire — seul lui (ou un
        # compte héritant d'un PJ de la partie pour les parties anciennes)
        # pourra la supprimer (cf. `delete_party`).
        "createur": utilisateur,
    })
    if mot_de_passe:
        etat["meta"]["mot_de_passe_sha256"] = _hash_mot_de_passe(mot_de_passe)
    else:
        etat["meta"].pop("mot_de_passe_sha256", None)
    etat["phase"] = "opening"
    state.save(etat)
    sessions.get(partie_id)  # crée la session en mémoire
    return {
        "partie_id": partie_id,
        "titre": titre,
        "etat": etat,
        "protegee": bool(mot_de_passe),
    }


@app.get("/api/parties/{partie_id}")
async def get_party(partie_id: str) -> dict[str, Any]:
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    if "_erreur" in etat:
        raise HTTPException(status_code=404, detail=etat["_erreur"])
    # 🎯 Objectifs de quête : calcul FRAIS (sans LLM) — requis d'objets de
    # l'inventaire de quête de la partie, salles visitées, étapes clôturées —
    # pour que l'onglet client reflète l'état réel à chaque chargement.
    try:
        from .game.objectifs import actualiser_objectifs  # pylint: disable=import-outside-toplevel
        data_dir = str(cfg.abs(cfg.paths.data_dir))
        actualiser_objectifs(etat, data_dir, partie_id=partie_id)
    except Exception:                                       # noqa: BLE001
        pass
    return {"partie_id": partie_id, "etat": etat}


# --------------------------------------------------------------------------- #
#  Calepin du MJ (journal de notes) — persistance dans l'état de la partie.
# --------------------------------------------------------------------------- #
def _party_state(partie_id: str) -> PartyState:
    return PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )


@app.get("/api/parties/{partie_id}/calepin")
async def calepin_lire(partie_id: str) -> dict[str, Any]:
    """Liste les notes du calepin de la partie (id, texte, fait)."""
    return {"partie_id": partie_id, "notes": _party_state(partie_id).calepin_lire()}


@app.post("/api/parties/{partie_id}/calepin")
async def calepin_ajouter(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Ajoute une note au calepin. `texte` requis (str) ; `fait` (bool) optionnel."""
    texte = str(payload.get("texte") or "").strip()
    if not texte:
        raise HTTPException(status_code=400, detail="Champ 'texte' requis.")
    fait = bool(payload.get("fait", False))
    st = _party_state(partie_id)
    err, note_id = st.calepin_ajouter(texte, fait)
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "note_id": note_id, "notes": st.calepin_lire()}


@app.put("/api/parties/{partie_id}/calepin/{note_id}")
async def calepin_maj(
    partie_id: str, note_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Met à jour une note : `texte` (str) et/ou `fait` (bool)."""
    st = _party_state(partie_id)
    texte = payload.get("texte")
    fait = payload.get("fait")
    if texte is not None:
        texte = str(texte).strip()
        if not texte:
            raise HTTPException(status_code=400, detail="Texte vide.")
    if fait is not None:
        fait = bool(fait)
    err = st.calepin_maj(note_id, texte=texte, fait=fait)
    if err:
        raise HTTPException(status_code=404 if err == "Note introuvable" else 500,
                            detail=err)
    return {"ok": True, "notes": st.calepin_lire()}


@app.delete("/api/parties/{partie_id}/calepin/{note_id}")
async def calepin_supprimer(partie_id: str, note_id: str) -> dict[str, Any]:
    st = _party_state(partie_id)
    err = st.calepin_supprimer(note_id)
    if err:
        raise HTTPException(status_code=404 if err == "Note introuvable" else 500,
                            detail=err)
    return {"ok": True, "notes": st.calepin_lire()}


@app.delete("/api/parties/{partie_id}")
async def delete_party(
    partie_id: str,
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Supprime définitivement une partie : état persistant, historiques de
    chat (MJ + équipe), cartes SVG liées (donjon…) et session en mémoire.
    Les WebSockets encore connectés sont fermés — les joueurs voient la
    partie disparaître.

    🔒 Bêta (C1) : la suppression est réservée au PROPRIÉTAIRE de la partie
    (`meta.createur`, posé à la création). Parties anciennes sans créateur :
    suppression tolérée uniquement si la partie est vide (aucun PJ) OU si le
    demandeur possède un personnage dans la partie — plus aucun compte
    étranger ne peut effacer la table d'autrui."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", partie_id):
        raise HTTPException(status_code=400, detail="Identifiant de partie invalide.")

    data_dir = cfg.abs(cfg.paths.data_dir)

    # 🔒 Contrôle de propriété AVANT toute suppression.
    etat_cible = PartyState(
        data_dir=str(data_dir), partie_id=partie_id
    ).load()
    if "_erreur" not in etat_cible:
        createur = str(
            (etat_cible.get("meta") or {}).get("createur") or ""
        ).strip()
        if createur:
            if utilisateur.strip().lower() != createur.lower():
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Seul le propriétaire de la partie (« {createur} ») "
                        "peut la supprimer."
                    ),
                )
        else:
            # Partie ancienne sans créateur enregistré.
            pjs = etat_cible.get("pj") or []
            possede_pj = any(
                persos_mod._meme_compte(
                    str(p.get("joueur") or ""), utilisateur
                )
                for p in pjs
            )
            if pjs and not possede_pj:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Partie ancienne sans propriétaire enregistré : "
                        "suppression réservée aux joueurs qui y ont un "
                        "personnage."
                    ),
                )

    # Session en mémoire : fermeture propre des WebSockets puis retrait.
    sess = sessions.pop(partie_id)
    if sess is not None:
        for ws in list(sess.connections):
            try:
                await ws.close(code=1001, reason="Partie supprimée")
            except Exception:                                    # noqa: BLE001
                pass
        sess.connections.clear()

    # Fichiers sur disque (best-effort, on liste ce qui est réellement effacé).
    cibles = [
        data_dir / f"partie_{partie_id}.json",
        data_dir / f"chat_{partie_id}.json",
        data_dir / f"team_chat_{partie_id}.json",
        *list((data_dir / "cartes").glob(f"*_{partie_id}.svg")),
    ]
    supprimes = [c.name for c in cibles if c.is_file()]
    for c in cibles:
        try:
            c.unlink(missing_ok=True)
        except OSError as e:
            raise HTTPException(
                status_code=500, detail=f"Impossible de supprimer {c.name} : {e}"
            )

    if not supprimes and sess is None:
        raise HTTPException(status_code=404, detail=f"Partie « {partie_id} » introuvable.")
    return {"ok": True, "partie_id": partie_id, "supprimes": supprimes}


@app.get("/api/parties/{partie_id}/carte-donjon.svg")
async def get_carte_donjon_svg(partie_id: str) -> Response:
    """Carte du donjon rendue À LA VOLÉE depuis l'état live de la partie.

    Le fichier statique `/data/cartes/donjon_<partie>.svg` n'est réécrit que
    lorsqu'un outil tourne — il peut donc être périmé. Cette route re-rend
    le SVG depuis `etat["donjon"]` à chaque requête (style à jour garanti)
    et interdit la mise en cache navigateur.
    """
    from .tools.cartes import _rendre_svg_donjon
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    donjon = (etat or {}).get("donjon") or {}
    if not donjon.get("id"):
        raise HTTPException(status_code=404, detail="Aucun donjon actif")
    return Response(
        content=_rendre_svg_donjon(donjon),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/tools")
async def list_tools() -> dict[str, Any]:
    """Introspection des tools (debug / doc frontend)."""
    from .tools.registry import tools_schemas_all
    return {
        "names": sorted(app.state.tools.keys()),
        "schemas": tools_schemas_all(app.state.tools),
    }


# --------------------------------------------------------------------------- #
#  Modèles IA — sélection à chaud du modèle du MJ (menu déroulant frontend).
# --------------------------------------------------------------------------- #
@app.get("/api/models")
async def list_models() -> dict[str, Any]:
    """Liste les modèles disponibles sur le backend LLM + le modèle courant."""
    try:
        models = await app.state.client.list_models()
    except Exception as e:                                   # noqa: BLE001
        return {"models": [], "current": cfg.llm.model, "error": str(e)}
    return {"models": [m.get("id", "") for m in models], "current": cfg.llm.model}


@app.post("/api/model")
async def set_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Bascule le modèle du MJ à chaud et persiste le choix (data/model_choice.json).

    Le choix persisté prime sur config.yaml au démarrage suivant — la config
    reste montée read-only dans Docker alors que data_dir est writable.
    """
    model = (payload.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Champ 'model' requis.")
    models = await app.state.client.list_models()
    # Si le backend expose une liste non vide, on valide le choix pour éviter
    # les fautes de frappe qui feraient échouer silencieusement tous les tours.
    if models and not any(m.get("id") == model for m in models):
        dispo = ", ".join(m.get("id", "?") for m in models)
        raise HTTPException(
            status_code=404,
            detail=f"Modèle « {model} » introuvable. Disponibles : {dispo}",
        )
    cfg.llm.model = model
    try:
        _model_choice_path().write_text(
            json.dumps({"model": model}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # persistance best-effort ; le changement runtime reste actif
    return {"ok": True, "model": model}


# --------------------------------------------------------------------------- #
#  Réglages d'images — toggle runtime persisté (bouton GUI « scènes »)
# --------------------------------------------------------------------------- #
def _settings_path() -> Path:
    return cfg.abs(cfg.paths.data_dir) / "settings.json"


@app.get("/api/settings/images")
async def image_settings() -> dict[str, Any]:
    """État de la génération/affichage des images par catégorie.

    - `*_enabled` (monstres/salles/scènes) : valeur EFFECTIVE = config.yaml
      (toggle individuel par catégorie) ET interrupteur maître du GUI
      (`all_enabled`, persisté dans data/settings.json).
    - `*_config_enabled` : verrou dur lu de config.yaml — à false, la
      catégorie est coupée, son onglet disparaît et le maître ne peut pas
      la réactiver.
    """
    return {
        "enabled": cfg.image.enabled,
        "all_enabled": cfg.image.all_enabled,
        "monstres_enabled": cfg.image.effective("monstres"),
        "salles_enabled": cfg.image.effective("salles"),
        "scenes_enabled": cfg.image.effective("scenes"),
        "monstres_config_enabled": cfg.image.monstres_config,
        "salles_config_enabled": cfg.image.salles_config,
        "scenes_config_enabled": cfg.image.scenes_config,
    }


@app.post("/api/settings/images/master")
async def set_image_master(payload: dict[str, Any]) -> dict[str, Any]:
    """Interrupteur MAÎTRE du GUI : active/désactive l'affichage et la
    génération des trois catégories d'images (monstres, pièces, scènes)
    d'un coup. Les toggles individuels restent configurés dans config.yaml ;
    le choix du maître est persisté dans data/settings.json et prime au
    redémarrage (config/ est monté read-only en Docker, data_dir writable).
    """
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Champ 'enabled' (bool) requis.")
    cfg.image.set_all(enabled)
    try:
        data: dict[str, Any] = {}
        if _settings_path().is_file():
            try:
                data = json.loads(_settings_path().read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        images["all_enabled"] = enabled
        data["images"] = images
        _settings_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # persistance best-effort ; le changement runtime reste actif
    return {
        "ok": True,
        "enabled": cfg.image.enabled,
        "all_enabled": enabled,
        "monstres_enabled": cfg.image.effective("monstres"),
        "salles_enabled": cfg.image.effective("salles"),
        "scenes_enabled": cfg.image.effective("scenes"),
    }


@app.post("/api/settings/images/scenes")
async def set_image_scenes(payload: dict[str, Any]) -> dict[str, Any]:
    """Compat : l'ancien bouton « scènes » agit désormais comme l'interrupteur
    maître (il coupe/active les trois onglets d'un coup). Voir
    `/api/settings/images/master`."""
    return await set_image_master(payload)


# --------------------------------------------------------------------------- #
#  Authentification — comptes locaux + tokens Bearer
# --------------------------------------------------------------------------- #
@app.post("/api/auth/inscription")
async def auth_inscription(payload: dict[str, Any]) -> dict[str, Any]:
    """Crée un compte {nom, mot_de_passe} et renvoie directement un token."""
    nom = (payload.get("nom") or "").strip()
    mdp = payload.get("mot_de_passe") or ""
    ok, message = auth_mod.creer_utilisateur(_dossier_donnees(), nom, mdp)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {
        "token": auth_mod.generer_token(_dossier_donnees(), nom),
        "utilisateur": nom,
    }


@app.post("/api/auth/connexion")
async def auth_connexion(payload: dict[str, Any]) -> dict[str, Any]:
    """Connecte un compte existant → token Bearer."""
    nom = (payload.get("nom") or "").strip()
    mdp = payload.get("mot_de_passe") or ""
    if not auth_mod.verifier_identifiants(_dossier_donnees(), nom, mdp):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    return {
        "token": auth_mod.generer_token(_dossier_donnees(), nom),
        "utilisateur": nom,
    }


@app.get("/api/auth/moi")
async def auth_moi(utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    return {"utilisateur": utilisateur}


@app.delete("/api/auth/compte")
async def auth_supprimer_compte(
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """🔧 Bêta (r4) : supprime le compte CONNECTÉ (auto-suppression).

    Dans l'ordre : les parties CRÉÉES par le compte (état, historiques,
    cartes, WebSockets fermés), les fiches de personnages qui lui appartiennent
    (+ portraits en cache), puis le compte lui-même — les tokens mémorisés
    deviennent immédiatement invalides (le compte n'existe plus). Les parties
    d'AUTRES comptes où le compte avait des personnages sont conservées
    (les fiches y restent, rattachées au compte supprimé, ignorées au join)."""
    data_dir = cfg.abs(cfg.paths.data_dir)

    # 1) Parties créées par le compte.
    parties_supprimees: list[str] = []
    ids = set(sessions.all_ids())
    ids |= {p.stem[len("partie_"):] for p in data_dir.glob("partie_*.json")}
    for pid in sorted(ids):
        etat = PartyState(data_dir=str(data_dir), partie_id=pid).load()
        if "_erreur" in etat:
            continue
        createur = str((etat.get("meta") or {}).get("createur") or "").strip()
        if createur.lower() == utilisateur.strip().lower():
            try:
                await delete_party(pid, utilisateur=utilisateur)
                parties_supprimees.append(pid)
            except HTTPException:
                pass  # (non-propriétaire impossible ici, garde défensive)

    # 2) Fiches de personnages du compte (+ portraits).
    fiches_supprimees: list[str] = []
    from .tools.fiches import _slug as _slug_fn  # pylint: disable=import-outside-toplevel
    dossier_fiches = data_dir / "fiches"
    if dossier_fiches.is_dir():
        for fn in list(dossier_fiches.glob("fiche_*.json")):
            try:
                with open(fn, "r", encoding="utf-8") as f:
                    fiche = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if persos_mod._meme_compte(fiche.get("proprietaire"), utilisateur):
                slug = _slug_fn(str(fiche.get("nom") or fn.stem))
                for cible in (
                    fn,
                    data_dir / "portraits_cache" / f"{slug}.png",
                    data_dir / "bestiaire_cache" / f"{slug}.png",
                ):
                    try:
                        cible.unlink(missing_ok=True)
                    except OSError:
                        pass
                fiches_supprimees.append(str(fiche.get("nom") or slug))

    # 3) Le compte lui-même (tokens invalides de fait).
    supprime = auth_mod.supprimer_compte(_dossier_donnees(), utilisateur)
    if not supprime:
        raise HTTPException(status_code=404, detail="Compte introuvable.")

    return {
        "ok": True,
        "compte": utilisateur,
        "parties_supprimees": parties_supprimees,
        "fiches_supprimees": fiches_supprimees,
    }


# --------------------------------------------------------------------------- #
#  Personnages joueurs — « Mes personnages » du frontend (auth requis).
# --------------------------------------------------------------------------- #
@app.get("/api/persos/modele")
async def persos_modele() -> dict[str, Any]:
    """Catalogues pour le formulaire : races, classes, alignements, dieux."""
    return {
        "races": [
            {"nom": nom, "mods": r["mods"], "taille": r["taille"], "vitesse": r["vitesse"],
             "capacites": [
                 {"nom": c["nom"], "description": c["description"], "niveau": 1}
                 for c in persos_mod.CAPACITES_RACES.get(nom, [])
             ]}
            for nom, r in persos_mod.RACES.items()
        ],
        "classes": [
            {
                "nom": nom,
                "de_vie": c["de_vie"],
                "bab": c["bab"],
                "sauves_bonnes": c["sauves_bonnes"],
                "capacites": persos_mod.CAPACITES_CLASSES.get(nom, []),
            }
            for nom, c in persos_mod.CLASSES.items()
        ],
        "alignements": persos_mod.ALIGNEMENTS,
        "dieux": [
            {
                "nom": d["nom"],
                "titre": d["titre"],
                "alignement": d["alignement"],
                "races": d["races"],
                "classes": d["classes"],
                "mal": d["mal"],
            }
            for d in persos_mod.DIEUX
        ],
        # Catalogues d'équipement + maîtrises (le front grise l'indisponible).
        "proficiences": catalogue_mod.PROFICIENCES,
        "armes": catalogue_mod.ARMES,
        "armures": catalogue_mod.ARMURES,
        "equipement_aventurier": catalogue_mod.EQUIPEMENT,
        "dons": [
            {"nom": d["nom"], "condition": d["condition"], "prereq": d["prereq"]}
            for d in catalogue_mod.DONS
        ],
        "competences": catalogue_mod.COMPETENCES,
        "competences_classe": catalogue_mod.COMPETENCES_CLASSE,
        "points_competence": catalogue_mod.POINTS_COMPETENCE,
        "or_depart": catalogue_mod.OR_DEPART,
        # Magie 3.5 : catalogue des sorts (filtré par classe/niveau côté
        # client), tables d'emplacements par jour + règles de lancement.
        "sorts": [
            {
                "nom": s["nom"], "niveau": s["niveau"], "ecole": s["ecole"],
                "classes": s["classes"], "incantation": s["incantation"],
                "portee": s["portee"], "composantes": s["composantes"],
                "duree": s["duree"], "sauvegarde": s.get("sauvegarde", ""),
                "description": s.get("description", ""),
            }
            for s in sorts_mod.SORTS
        ],
        "sorts_emplacements": sorts_mod._E,
        "sorts_connus_max": sorts_mod.CONNUS,
        "sorts_carac": sorts_mod.CARAC_INCANTATION,
        "sorts_prepare": sorted(sorts_mod.PREPARE),
        # Marché & auberge : types de peuplement, marchands, tarifs.
        # (les articles détaillés vivent côté tools : equipement_catalogue)
        "villes": [
            {
                "nom": t, "rang": r,
                "multiplicateur": villes_mod.multiplicateur(t),
                "sorts_max": villes_mod.sorts_max_niveau(t),
                "auberge_qualites": villes_mod.auberge_qualites(t),
            }
            for r, t in enumerate(villes_mod.RANGS)
        ],
        "marchands": [
            {"nom": k, "description": v.get("description", ""),
             "categories": v.get("categories", [])}
            for k, v in equipement_phb_mod.MARCHANDS.items()
        ],
        "monnaie": {"1_po_en_pc": 10, "1_pa_en_pc": 1, "min_pc": 1},
        # Familier (Magicien/Sorcier) et compagnon animal (Druide/Rodeur) :
        # espèces + stats de base du bestiaire + tables de progression PHB.
        "familiers": familiers_mod.modele_pour_client(str(_dossier_donnees())),
    }


@app.post("/api/persos/stats-aleatoires")
async def persos_stats_aleatoires(
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Tirage 4d6 (on retire le plus faible) ×6 — méthode classique PHB."""
    return {"carac": persos_mod.tirage_4d6(), "methode": "4d6 garder les 3 meilleurs"}


@app.post("/api/persos/or-depart")
async def persos_or_depart(
    payload: dict[str, Any],
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Or de départ PHB 3.5 pour la classe donnée.

    mode="tirage" (défaut) : lance les dés de la classe.
    mode="moyenne"         : valeur moyenne officielle (ex. guerrier 150 po).
    """
    classe = (payload.get("classe") or "").strip()
    classe_c = persos_mod.resoudre_classe(classe)
    if not classe_c or classe_c not in catalogue_mod.OR_DEPART:
        raise HTTPException(status_code=400, detail=f"Classe inconnue : « {classe} ».")
    mode = payload.get("mode") or "tirage"
    return {
        "or": catalogue_mod.tirer_or_depart(classe_c, mode),
        "formule": catalogue_mod.formule_or_depart(classe_c),
    }


@app.post("/api/persos/apparence-aleatoire")
async def persos_apparence_aleatoire(
    payload: dict[str, Any],
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Tirage âge/taille/poids selon les tables officielles 3.5 (DRS).

    L'âge dépend de la race ET du groupe de classe ; taille/poids de la race
    et du sexe. Renvoie valeurs brutes + chaînes formatées pour le formulaire.
    """
    return persos_mod.tirer_apparence(
        race=(payload.get("race") or "").strip(),
        classe=(payload.get("classe") or "").strip(),
        sexe=(payload.get("sexe") or "").strip(),
    )


@app.get("/api/persos")
async def persos_liste(utilisateur: str = Depends(utilisateur_courant)) -> list[dict[str, Any]]:
    """Liste les personnages du compte connecté (+ URL de portrait)."""
    data_dir = _dossier_donnees()
    resultats = []
    for fiche in persos_mod.lister_fiches(data_dir, proprietaire=utilisateur):
        resultats.append({
            **fiche,
            "portrait": persos_mod.url_portrait(data_dir, str(fiche.get("nom", "")), utilisateur),
        })
    return resultats


@app.get("/api/persos/{slug}")
async def persos_detail(slug: str, utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Fiche d'un personnage du compte connecté (par slug de nom)."""
    from .tools.fiches import _slug as _slug_fn
    for fiche in persos_mod.lister_fiches(_dossier_donnees(), proprietaire=utilisateur):
        if _slug_fn(str(fiche.get("nom", ""))) == slug:
            return {
                **fiche,
                "portrait": persos_mod.url_portrait(
                    _dossier_donnees(), str(fiche.get("nom", "")), utilisateur
                ),
            }
    raise HTTPException(status_code=404, detail="Personnage introuvable.")


def _normaliser_equipement(brut: Any) -> list[dict[str, Any]]:
    """Accepte une chaîne multiligne ou une liste (chaînes « Nom x2 » / dicts)."""
    if isinstance(brut, str):
        lignes: list[Any] = brut.splitlines()
    elif isinstance(brut, list):
        lignes = brut
    else:
        return []
    resultat: list[dict[str, Any]] = []
    for item in lignes:
        if isinstance(item, dict) and str(item.get("nom", "")).strip():
            try:
                qte = max(1, int(item.get("qte", 1)))
            except (TypeError, ValueError):
                qte = 1
            resultat.append({"nom": str(item["nom"]).strip(), "qte": qte})
            continue
        s = str(item).strip()
        if not s:
            continue
        parts = s.rsplit(None, 1)
        if len(parts) == 2 and parts[1][:1].lower() == "x" and parts[1][1:].isdigit():
            resultat.append({"nom": parts[0].strip(), "qte": max(1, int(parts[1][1:]))})
        else:
            resultat.append({"nom": s, "qte": 1})
    return resultat


def _poids_catalogue(nom: str) -> Optional[float]:
    """Poids (kg) d'une unité d'objets du catalogue de création (PHB 3.5).

    Cherche d'abord dans armes/armures/équipement du catalogue ; se rabat sur
    le catalogue de poids du moteur d'inventaire pour les objets synonymes.
    Renvoie None si l'objet est inconnu (poids non compté, comme l'outil
    d'inventaire le fait).
    """
    try:
        from .tools import inventaire as inventaire_mod  # pylint: disable=import-outside-toplevel
    except Exception:                                     # noqa: BLE001
        inventaire_mod = None

    def _norm(s: str) -> str:
        import unicodedata as _u, re as _re
        s = _u.normalize("NFKD", str(s or "").lower())
        s = "".join(c for c in s if not _u.combining(c))
        s = _re.sub(r"[^a-z0-9]+", " ", s).strip()
        return s

    cible = _norm(nom)
    for entrepot in (*catalogue_mod.ARMES, *catalogue_mod.ARMURES, *catalogue_mod.EQUIPEMENT):
        if isinstance(entrepot, dict) and _norm(str(entrepot.get("nom") or "")) == cible:
            p = entrepot.get("poids")
            if isinstance(p, (int, float)):
                return float(p)
    if inventaire_mod is not None:
        info = inventaire_mod._POIDS_OFFICIELS.get(cible)  # noqa: SLF001
        if info:
            lot = int(info.get("lot") or 1)
            return round(float(info["poids_kg"]) / lot, 4)
    return None


def _calculer_charge_equipement(equipement: list[dict[str, Any]],
                                charge_max: float,
                                or_pc: int = 0) -> dict[str, Any]:
    """Calcule la charge portée depuis le catalogue (armes/armures/équipement).

    Renvoie `{poids_transporte, etat_encumbrance, charge_max}`. Chaque objet
    récupère son `poids` (kg/unité) s'il est connu du catalogue, auquel cas
    son poids est compté ; sinon `poids` reste absent et l'objet n'est pas
    compté (cohérent avec le moteur d'inventaire).
    """
    total = 0.0
    for e in equipement:
        if not isinstance(e, dict) or not e.get("nom"):
            continue
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_catalogue(str(e["nom"]))
        if pu is None:
            continue
        e["poids"] = pu
        total += pu * qte
    # Monnaie : 50 pièces = 1 lb (PHB 3.5).
    if or_pc:
        total += or_pc / 50.0 * 0.4536
    total = round(total, 2)
    max_kg = max(1, int(charge_max or 0))
    tiers = max_kg / 3.0
    if total <= tiers:
        cat = "Legere"
    elif total <= 2 * tiers:
        cat = "Moyenne"
    elif total <= max_kg:
        cat = "Lourde"
    else:
        cat = "Depassee"
    return {"poids_transporte": total, "etat_encumbrance": cat, "charge_max": max_kg}


@app.post("/api/persos")
async def persos_sauver(payload: dict[str, Any], utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Crée ou met à jour un personnage du compte connecté.

    Les valeurs dérivées (PV, CA, BBA, sauvegardes…) sont TOUJOURS recalculées
    côté serveur d'après race/classe/niveau/caractéristiques. Le portrait est
    régénéré en arrière-plan à chaque enregistrement.
    """
    data_dir = _dossier_donnees()
    nom = (payload.get("nom") or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="Le nom du personnage est requis.")

    # Collision inter-comptes : le fichier est global (fiche_<slug>.json).
    # Comparaison insensible à la casse : les fiches créées via les tools MJ
    # portent le pseudo WS (« alain »), les comptes leur casse d'origine.
    existante = persos_mod.charger_fiche(data_dir, nom)
    if existante and not persos_mod._meme_compte(
        existante.get("proprietaire"), utilisateur
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Un personnage nommé « {nom} » existe déjà (autre compte). "
                   "Choisissez un autre nom.",
        )

    carac_saisi = payload.get("carac") or {}
    carac: dict[str, int] = {}
    # 🛡️ P6 (audit parties complètes) : AUCUNE borne. Une requête artisanale
    # créait FOR 97 / DEX 101 / CON 99 (+35 de modificateur de DEX) et 50 000
    # po — un personnage invincible, indétectable par le MJ. Bornes 3.5 d'un
    # PJ jouable : 3 à 25 (un 25 naturel reste exceptionnel mais atteignable
    # par la magie permanente) ; or plafonné à l'achat de départ raisonnable.
    for c in persos_mod.CARACS:
        try:
            _v = int(carac_saisi.get(c, 10))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"Caractéristique {c} invalide.")
        if not 3 <= _v <= 25:
            raise HTTPException(
                status_code=400,
                detail=f"Caractéristique {c} = {_v} hors bornes (3 à 25). "
                       "Un personnage joueur est créé avec des valeurs "
                       "humaines, pas monstrueuses.",
            )
        carac[c] = _v

    # +1 de caractéristique des niveaux multiples de 4 : fourni explicitement
    # par le formulaire d'avancement (champ `gain_carac`), appliqué à la base.
    gain_carac = str(payload.get("gain_carac") or "").strip().upper()
    if gain_carac:
        if gain_carac not in persos_mod.CARACS:
            raise HTTPException(
                status_code=400,
                detail=f"Gain de caractéristique invalide : « {gain_carac} ».",
            )
        carac[gain_carac] = int(carac.get(gain_carac, 10)) + 1

    race = (payload.get("race") or "").strip()
    classe = (payload.get("classe") or "").strip()

    # 🛡️ P7 (audit parties complètes) : si la fiche est nouvelle et que le
    # client n'a pas fourni d'or, on tire l'or de départ ICI. Le tirage
    # `POST /api/persos/or-depart` n'écrit rien et le client oubliait souvent
    # de repasser la valeur — plusieurs personnages ont démarré avec 0 po,
    # sans moyen de corriger (la fiche se verrouille aussitôt).
    _or_fourni = payload.get("or")
    if not existante and (_or_fourni is None or int(_or_fourni or 0) <= 0):
        try:
            or_srv = catalogue_mod.tirer_or_depart(classe or "", "tirage")
        except Exception:                                        # noqa: BLE001
            or_srv = 0
        if or_srv > 0:
            payload = dict(payload)
            payload["or"] = or_srv

    # Verrou d'avancement : le niveau ne se choisit PAS dans le formulaire.
    # - création : tout personnage débute au niveau 1 ;
    # - édition : le niveau courant vient de l'XP (moteur de combat / tools MJ)
    #   et la fiche n'est modifiable QUE si un passage de niveau est en attente
    #   (niveau > avancement_confirmé). Après confirmation, re-verrouillage.
    if existante:
        niveau = max(1, int(existante.get("niveau", 1) or 1))
        avancement_confirme = int(existante.get("avancement_confirme", 1) or 1)
        # 🛡️ P7 : la fiche d'un personnage de niveau 1 qui n'a encore JAMAIS
        # joué (XP = 0) reste modifiable. Le verrou immédiat rendait toute
        # erreur de création définitive — un personnage né avec 0 po ne
        # pouvait pas être corrigé sans être supprimé, ce que le rattachement
        # à une partie empêche à son tour.
        if avancement_confirme >= niveau and int(
                existante.get("xp", 0) or 0) > 0:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Fiche verrouillée : les choix d'avancement de ce niveau "
                    "sont déjà confirmés. La fiche sera de nouveau modifiable "
                    "au prochain passage de niveau."
                ),
            )
        gains_carac_precedents = list(existante.get("gains_carac") or [])
    else:
        niveau = 1
        avancement_confirme = 1
        gains_carac_precedents = []

    equipement = _normaliser_equipement(payload.get("equipement"))
    # Dons parsés tôt : ils alimentent les valeurs dérivées (comme les PV
    # plus bas via _bonus_dons_pv) — « Esquive » ajoute +1 de CA.
    dons = payload.get("dons") or []
    if isinstance(dons, str):
        dons = [ligne.strip() for ligne in dons.splitlines() if ligne.strip()]
    # Armures/boucliers portés (présents au catalogue) → comptés dans la CA
    # (10 + armure + bouclier + Dex plafonnée par l'armure, règles PHB 3.5).
    noms_armures_catalogue = {a["nom"] for a in catalogue_mod.ARMURES}
    armures_portees = [e["nom"] for e in equipement if e["nom"] in noms_armures_catalogue]
    calculs = persos_mod.calculer_derivees(
        carac, race, classe, niveau, armures=armures_portees, dons=dons,
    )

    # Dieu : s'il correspond à une divinité du panthéon, elle doit accepter le
    # personnage comme serviteur. Un nom libre (ancienne fiche, dieu maison…)
    # est conservé, MAIS il est désormais signalé : 🛡️ P4 (audit parties
    # complètes), un dieu ABSENT du panthéon court-circuitait le contrôle et
    # était enregistré tel quel — « Tyr » ou « Mickey Mouse » passaient
    # exactement pareil. On pose `dieu_verifie: False` et on journalise.
    dieu = (payload.get("dieu") or "").strip()
    dieu_verifie = False
    if dieu:
        connu = any(
            persos_mod._normaliser(d["nom"]) == persos_mod._normaliser(dieu)
            for d in persos_mod.DIEUX
        )
        if connu:
            eligibles = persos_mod.dieux_disponibles(
                race, classe, payload.get("alignement") or ""
            )
            if not any(
                persos_mod._normaliser(d["nom"]) == persos_mod._normaliser(dieu)
                for d in eligibles
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"« {dieu} » n'accepte pas ce personnage comme serviteur "
                           "(race / classe / alignement incompatibles).",
                )
            dieu_verifie = True
        else:
            print(
                "[dnd35] ⚠️ dieu « " + dieu + " » hors panthéon ("
                + str(len(persos_mod.DIEUX)) + " divinités connues) : conservé "
                "mais NON vérifié (dieu_verifie=False)."
            )

    apparence_in = payload.get("apparence") or {}
    # Budget de dons (règles 3.5) : 1 au niveau 1 puis 1 supplémentaire aux
    # niveaux 3, 6, 9… ; les humains gagnent +1 don.
    max_dons = 1 + max(0, niveau // 3) + (
        1 if (persos_mod.resoudre_race(race) or race) == "Humain" else 0
    )
    # Guerrier : dons supplémentaires aux niveaux 1, 2, 4, 6… (PHB 3.5) :
    # count = 1 (niv. 1) + 1 par niveau pair → 1 + niveau // 2.
    if (persos_mod.resoudre_classe(classe) or classe) == "Guerrier":
        max_dons += 1 + max(0, niveau // 2)
    if len(dons) > max_dons:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de dons ({len(dons)}) : maximum {max_dons} au niveau "
                   f"{niveau} (bonus humain inclus le cas échéant).",
        )
    # Bonus de PV des dons (ex. « Dur à cuire » = +3 PV) appliqué ici, comme
    # dans fiche_perso_creer / fiche_perso_creer_rapide. Sans cela, pv/pv_max
    # omettaient l'effet des dons lors de la création via le formulaire.
    from .tools.fiches import _bonus_dons_pv
    bonus_pv = _bonus_dons_pv(dons, niveau)
    pv = int(calculs["pv"]) + bonus_pv
    pv_max = int(calculs["pv_max"]) + bonus_pv

    competences = payload.get("competences") or {}
    # Budget de points de compétence (même formule que le client) :
    # par_niveau = max(1, base_classe + mod_INT) ; total = par_niveau × (niveau+3)
    # (+niveau si humain). Les rangs saisis ne peuvent pas dépasser ce budget.
    if isinstance(competences, dict) and competences:
        base_pts = catalogue_mod.POINTS_COMPETENCE.get(
            persos_mod.resoudre_classe(classe) or classe, 0
        )
        mod_int = (int(calculs["carac_final"]["INT"]) - 10) // 2
        budget_comp = max(1, base_pts + mod_int) * (3 + niveau)
        if (persos_mod.resoudre_race(race) or race) == "Humain":
            budget_comp += 4 + max(0, niveau - 1)
        try:
            rangs_total = sum(max(0, int(v or 0)) for v in competences.values())
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="Rangs de compétence invalides."
            )
        if rangs_total > budget_comp:
            raise HTTPException(
                status_code=400,
                detail=f"Trop de rangs de compétence ({rangs_total}) : maximum "
                       f"{budget_comp} au niveau {niveau}.",
            )

    # --------------------------- Sorts (magie 3.5) ---------------------------
    # Validation stricte : chaque sort doit exister, appartenir à la liste de
    # CLASSE et être castable à ce niveau. Sorcier/Barde : budget de sorts
    # connus (table PHB). Magicien : grimoire de départ (tous les tours de
    # magicien + 3+mod INT sorts de niveau 1). Clerc/Druide/Paladin/Rodeur :
    # liste complète de classe (préparation quotidienne en jeu).
    classe_canon = persos_mod.resoudre_classe(classe) or classe
    sorts_payload = payload.get("sorts") or {}
    sorts_connus = sorts_payload.get("connus") or []
    sorts_prepares = sorts_payload.get("prepares") or {}
    if not isinstance(sorts_connus, list) or not isinstance(sorts_prepares, dict):
        raise HTTPException(status_code=400, detail="Champ sorts invalide.")
    if sorts_connus or sorts_prepares:
        if not sorts_mod.est_lanceur(classe_canon):
            raise HTTPException(
                status_code=400,
                detail=f"La classe {classe_canon} ne lance pas de sorts.",
            )
        nls = sorts_mod.niveau_sort_max(classe_canon, niveau)
        for s in sorts_connus + list(sorts_prepares.keys()):
            sp = sorts_mod.sort_par_nom(str(s), classe_canon)
            if sp is None:
                raise HTTPException(status_code=400, detail=f"Sort inconnu : « {s} ».")
            if classe_canon not in sp["classes"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"« {sp['nom']} » n'appartient pas à la liste de "
                           f"sorts de {classe_canon}.",
                )
            if sp["niveau"] > nls:
                raise HTTPException(
                    status_code=400,
                    detail=f"« {sp['nom']} » (niv. {sp['niveau']}) est trop "
                           f"puissant pour {classe_canon} niv.{niveau} (max : "
                           f"niveau de sort {nls}).",
                )
        if classe_canon == "Magicien":
            # Grimoire PHB 3.5 : départ = 3 + mod INT sorts de niveau 1 ;
            # chaque nouveau niveau de magicien ajoute 2 sorts de niveau ≥ 1
            # (n'importe quel niveau castable). Les tours sont connus d'office.
            mod_int = (int(calculs["carac_final"]["INT"]) - 10) // 2
            budget_grimoire = 3 + mod_int + 2 * max(0, niveau - 1)
            detail_budget = (
                f"départ 3 + INT {mod_int:+d}"
                if niveau == 1
                else f"départ 3 + INT {mod_int:+d} + 2 x {niveau - 1} niveaux gagnés"
            )
            hors_tours = [
                s for s in sorts_connus
                if (sorts_mod.sort_par_nom(str(s), classe_canon) or {}).get("niveau", 0) >= 1
            ]
            if len(hors_tours) > budget_grimoire:
                raise HTTPException(
                    status_code=400,
                    detail=f"Grimoire : maximum {budget_grimoire} sorts de "
                           f"niveau ≥ 1 ({detail_budget}) — "
                           f"{len(hors_tours)} présents.",
                )
        if classe_canon in sorts_mod.SPONTANE:
            exces = sorts_mod.depassement_connus(classe_canon, niveau, sorts_connus)
            if exces:
                det = ", ".join(f"niv.{l}: {n} de trop" for l, n in sorted(exces.items()))
                raise HTTPException(
                    status_code=400,
                    detail=f"Trop de sorts connus ({classe_canon} niv.{niveau}) : {det}.",
                )
    # ---------------- Gains obligatoires du passage de niveau ----------------
    # L'enregistrement n'est possible QUE pendant un avancement en attente
    # (niveau > avancement_confirmé) ; il exige que les gains du niveau soient
    # consommés : dons complets, +1 de caractéristique aux niveaux 4/8/12…,
    # sorts connus au complet pour les spontanés (Sorcier/Barde, table PHB).
    if existante and niveau > avancement_confirme:
        if len(dons) < max_dons:
            raise HTTPException(
                status_code=400,
                detail=f"Avancement incomplet : il reste {max_dons - len(dons)} "
                       f"don(s) à choisir ({len(dons)}/{max_dons}).",
            )
        if niveau % 4 == 0 and not gain_carac:
            raise HTTPException(
                status_code=400,
                detail="Avancement incomplet : un niveau multiple de 4 doit "
                       "recevoir son +1 de caractéristique.",
            )
        if classe_canon in sorts_mod.SPONTANE:
            budget_connus = sorts_mod.sorts_connus_max(classe_canon, niveau)
            comptes: dict[int, int] = {}
            for s in sorts_connus:
                sp = sorts_mod.sort_par_nom(str(s), classe_canon)
                if sp and sp["niveau"] in budget_connus:
                    comptes[sp["niveau"]] = comptes.get(sp["niveau"], 0) + 1
            manquants = {
                lvl: n - comptes.get(lvl, 0)
                for lvl, n in budget_connus.items()
                if comptes.get(lvl, 0) < n
            }
            if manquants:
                det = ", ".join(
                    f"niv.{lvl} : {m} manquant(s)"
                    for lvl, m in sorted(manquants.items())
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Avancement incomplet — sorts connus "
                           f"({classe_canon} niv.{niveau}) : {det}.",
                )

    sorts_fiche = {
        "connus": [str(s) for s in sorts_connus],
        "prepares": {str(k): max(1, int(v or 1)) for k, v in sorts_prepares.items()},
        "depenses": {},
    }

    # ---------------- Familier / compagnon animal (PHB 3.5) ------------------
    # Choix FACULTATIF : familier (Magicien/Sorcier) ou compagnon animal
    # (Druide dès le niv.1, Rodeur dès le niv.4). Le lien est enregistré sur
    # la fiche ; en jeu, le tool `appeler_familier` matérialise l'arrivée du
    # compagnon (rituel 100 po / 24 h pour le familier).
    fiche_familier = None
    if payload.get("familier"):
        try:
            fiche_familier = familiers_mod.valider_choix(
                payload["familier"], classe_canon, niveau
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # L'état « invoqué » (rituel déjà accompli en jeu) survit à l'édition
        # tant que l'espèce et le type restent identiques.
        ancien = (existante or {}).get("familier")
        if (
            isinstance(ancien, dict)
            and ancien.get("espece") == fiche_familier["espece"]
            and ancien.get("type") == fiche_familier["type"]
        ):
            fiche_familier["invoque"] = bool(ancien.get("invoque"))

    # En édition, l'XP gagnée en jeu est conservée : elle n'évolue que par la
    # progression automatique (moteur de combat) et les tools MJ — jamais par
    # le formulaire (qui ne l'affiche pas et ne doit pas la remettre à zéro).
    xp_conservee = int(existante.get("xp", 0) or 0) if existante else 0

    fiche = {
        "nom": nom,
        "joueur": utilisateur,
        "proprietaire": utilisateur,
        "race": persos_mod.resoudre_race(race) or race,
        "classe": persos_mod.resoudre_classe(classe) or classe,
        "niveau": niveau,
        "xp": xp_conservee,
        # Avancement : dernier niveau dont les choix sont confirmés. Tant que
        # niveau > avancement_confirme, la fiche est modifiable (un passage de
        # niveau est en attente) ; la confirmation re-verrouille. Ici, la
        # sauvegarde a passé le verrou → les choix de CE niveau sont confirmés.
        "avancement_confirme": niveau,
        "gains_carac": (
            gains_carac_precedents
            + ([gain_carac] if gain_carac and niveau % 4 == 0 else [])
        ),
        "carac": calculs["carac_final"],
        "pv": pv,
        "pv_max": pv_max,
        "ca": calculs["ca"],
        "sauvegardes": calculs["sauvegardes"],
        "bab": calculs["bab"],
        "initiative": calculs["initiative"],
        "charge_max": calculs["charge_max"],
        "competences": competences,
        "sorts": sorts_fiche,
        "dons": dons,
        "equipement": equipement,
        "or": max(0, min(int(payload.get("or") or 0), 5_000)),
        "alignement": payload.get("alignement") or "",
        "dieu": dieu,
        # 🛡️ P4 : vrai seulement si le dieu appartient au panthéon ET accepte
        # race/classe/alignement du personnage.
        "dieu_verifie": dieu_verifie,
        "histoire": payload.get("histoire") or "",
        "conditions": [],
        "apparence": {
            "sexe": apparence_in.get("sexe") or "",
            "age": apparence_in.get("age") or "",
            "taille_physique": apparence_in.get("taille") or "",
            "poids": apparence_in.get("poids") or "",
            "yeux": apparence_in.get("yeux") or "",
            "cheveux": apparence_in.get("cheveux") or "",
            "peau": apparence_in.get("peau") or "",
            "description": apparence_in.get("description") or "",
        },
    }
    # Charge transportée (kg) et catégorie d'encombrement D&D 3.5, calculées
    # depuis le catalogue de poids PHB 3.5.
    fiche.update(_calculer_charge_equipement(equipement, fiche["charge_max"], fiche["or"]))
    # Familier/compagnon : clé ABSENTE si aucun choix (jamais null — le
    # schéma de fiche attend un objet).
    if fiche_familier:
        fiche["familier"] = fiche_familier

    chemin = persos_mod.chemin_fiche(data_dir, nom)
    try:
        with open(chemin, "w", encoding="utf-8") as f:            json.dump(fiche, f, ensure_ascii=False, indent=2)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Écriture impossible : {e}")

    # Portrait généré d'après la fiche enregistrée + traits de la race.
    persos_mod.lancer_portrait_background(data_dir, fiche)

    return {"ok": True, "fiche": fiche, "calculs": calculs}


@app.post("/api/persos/{slug}/portrait/regenerer")
async def persos_portrait_regenerer(
    slug: str,
    payload: Optional[dict[str, Any]] = None,
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """🎨 Bêta : RÉGÉNÈRE le portrait d'un personnage du compte connecté avec
    une SEED différente (image mal générée → nouvelle variante) d'après la
    fiche ACTUELLE — l'équipement porté a pu évoluer depuis la création
    (achats, butin, remises de PNJ) : le prompt de portrait relit l'inventaire
    à jour. Accepte `{"seed": <int>}` (0/omis = seed aléatoire).

    Synchrone (attends ComfyUI, quelques secondes) : le client rafraîchit
    l'affichage à la réponse (cache-bust fourni en `version`)."""
    data_dir = _dossier_donnees()
    from .tools.fiches import _slug as _slug_fn

    cible = None
    for fiche in persos_mod.lister_fiches(data_dir, proprietaire=utilisateur):
        if _slug_fn(str(fiche.get("nom", ""))) == slug:
            cible = fiche
            break
    if cible is None:
        raise HTTPException(status_code=404, detail="Personnage introuvable.")

    seed = None
    try:
        brut = (payload or {}).get("seed")
        if brut not in (None, "", 0, "0"):
            seed = max(0, int(brut))
    except (TypeError, ValueError):
        seed = None

    # Génération SYNCHRONE avec une seed neuve (ou celle demandée) : la fiche
    # rechargée porte l'équipement courant (arme/armure/bouclier montrés).
    chemin = await persos_mod.generer_portrait_async(
        data_dir, cible, seed=seed
    )
    if not chemin:
        raise HTTPException(
            status_code=503,
            detail=(
                "Génération d'images indisponible (ComfyUI désactivé ou "
                "injoignable)."
            ),
        )
    return {
        "ok": True,
        "portrait": f"/data/portraits_cache/{os.path.basename(chemin)}",
        "version": int(time.time() * 1000),
    }


@app.delete("/api/persos/{slug}")
async def persos_supprimer(slug: str, utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Supprime un personnage du compte connecté (+ portraits en cache)."""
    from .tools.fiches import _slug as _slug_fn
    data_dir = _dossier_donnees()
    cible = None
    for fiche in persos_mod.lister_fiches(data_dir, proprietaire=utilisateur):
        if _slug_fn(str(fiche.get("nom", ""))) == slug:
            cible = fiche
            break
    if cible is None:
        raise HTTPException(status_code=404, detail="Personnage introuvable.")
    try:
        os.remove(persos_mod.chemin_fiche(data_dir, str(cible.get("nom"))))
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Portraits best-effort (perso_<user>_<slug> + slug nu si présent).
    cache = os.path.join(data_dir, "portraits_cache")
    for base in (
        f"perso_{_slug_fn(utilisateur)}_{slug}",
        slug,
    ):
        for ext in (".png", ".svg"):
            p = os.path.join(cache, base + ext)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Fiches personnages — consultation par le frontend (modal fiche PJ).
# --------------------------------------------------------------------------- #
@app.get("/api/fiches/{nom}")
async def get_fiche(nom: str, partie_id: Optional[str] = None) -> dict[str, Any]:
    """Renvoie la fiche persistante d'un personnage (data/fiches/)."""
    from .tools.fiches import _slug
    fiches_dir = cfg.abs(cfg.paths.data_dir) / "fiches"
    path = fiches_dir / f"fiche_{_slug(nom)}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Aucune fiche pour « {nom} ».")
    try:
        fiche = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Fiche illisible : {e}")
    # Résolution UNIQUE (même ordre et mêmes candidats que la page principale
    # « Mes personnages ») : un même personnage affiche toujours LA même image,
    # quel que soit le nom de fichier sous lequel son portrait a été généré.
    data_dir = str(cfg.abs(cfg.paths.data_dir))
    portrait = persos_mod.resoudre_portrait(
        data_dir,
        str(fiche.get("nom", "") or nom),
        proprietaire=str(fiche.get("proprietaire", "")),
        partie_id=partie_id or "",
    )
    return {"fiche": fiche, "portrait": portrait}


# --------------------------------------------------------------------------- #
#  Ressources — liens permanents (manuels, cartes, scénarios) pour le bandeau
#  de ressources affiché en permanence sur l'écran de jeu.
# --------------------------------------------------------------------------- #
@app.get("/api/ressources")
async def ressources(partie_id: Optional[str] = None) -> dict[str, Any]:
    """Liste les ressources consultables : manuels, cartes de référence
    (Faerûn, nord de Faerûn, Outreterre, Toril), scénarios PDF locaux
    (+ carte du donjon de la partie si `partie_id`). Les manuels pointent
    vers le serveur du projet quand les fichiers sont présents sous
    data/manuels/ (repli externe sinon)."""
    from .tools import scenarios as S
    from .tools.manuels import (
        CARTES_REFERENCE,
        FICHIERS_DEFAUT,
        url_manuel,
    )
    from urllib.parse import quote

    data_dir = cfg.abs(cfg.paths.data_dir)
    ctx = ToolContext(
        partie_id=partie_id or "_",
        joueur="",
        data_dir=str(data_dir),
    )

    manuels = [
        {
            "titre": f["titre"],
            "description": f["description"],
            "categorie": f.get("categorie", "Autre"),
            "url": url_manuel(ctx, f["public_name"]),
        }
        for f in FICHIERS_DEFAUT
    ]

    # Cartes de référence — fichiers PNG du dossier projet `cartes/`, copiés
    # au démarrage vers data/cartes/ et servis sous /data/cartes/…
    cartes = [
        {"titre": _titre, "libelle": _libelle, "url": f"/data/cartes/{quote(_f)}"}
        for _f, _titre, _libelle in CARTES_REFERENCE
        if (data_dir / "cartes" / _f).is_file()
    ]
    # Atlas externes — cartes interactives hébergées par AideDD
    # (liens internet, nouvel onglet ; complètent les PNG hors-ligne ci-dessus).
    cartes.append(
        {
            "titre": "Faerûn — Atlas interactif (AideDD, internet)",
            "libelle": "Atlas AideDD",
            "url": "https://www.aidedd.org/atlas/fr/faerun",
        }
    )
    cartes.append(
        {
            "titre": "Laelith — Atlas interactif (AideDD, internet)",
            "libelle": "Atlas Laelith",
            "url": "https://www.aidedd.org/atlas/fr/laelith",
        }
    )

    # Cartes d'univers — scénarios par univers (cartes communes)
    cata = S.charger_catalogue(ctx)
    for u in cata.get("universes", []):
        for c in u.get("cartes", []):
            cartes.append({
                "titre": f"{u.get('nom', '')} — {c.get('nom', 'Carte')}",
                "libelle": f"🗺️ {c.get('nom', 'Carte')}",
                "url": c.get("fichier", ""),
            })

    # Scénarios PDF (ancien format plat pour la RessourcesBar) — UN lien par
    # scénario, pointant vers le DOCUMENT ORIGINAL complet. Les campagnes
    # découpées en chapitres (`chapitre`/`chapitre_suivant`) exposent leur
    # PDF original via l'annexe « …PDF original… » de leur chapitre 1 ; les
    # chapitres découpés restent consultables depuis le sélecteur de quête.
    scenarios = []
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            if s.get("chapitre"):
                if int(s.get("chapitre") or 0) != 1:
                    continue
                url = next(
                    (
                        a.get("fichier")
                        for a in (s.get("annexes") or [])
                        if "original" in str(a.get("nom", "")).lower()
                    ),
                    None,
                )
                if not url:
                    continue
                scenarios.append({
                    "id": _re_mod.sub(r"_ch\d+.*$", "", str(s.get("id", ""))),
                    "titre": str(s.get("campagne") or s.get("titre", "?")),
                    "niveau": s.get("niveau", "?"),
                    "url": url,
                })
            elif s.get("pdf"):
                scenarios.append({
                    "id": s.get("id", ""),
                    "titre": s.get("titre", "?"),
                    "niveau": s.get("niveau", "?"),
                    "url": s["pdf"],
                })

    donjon: Optional[str] = None
    if partie_id:
        svg = data_dir / "cartes" / f"donjon_{partie_id}.svg"
        if svg.is_file():
            donjon = f"/data/cartes/{quote(svg.name)}"

    return {
        "manuels": manuels,
        "cartes": cartes,
        "scenarios": scenarios,
        "donjon": donjon,
    }


# --------------------------------------------------------------------------- #
#  Scénarios — catalogue structuré par univers pour le sélecteur de quête.
# --------------------------------------------------------------------------- #
@app.get("/api/scenarios")
async def list_scenarios(partie_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Retourne la liste des univers, chacun contenant ses scénarios.
    Le frontend affiche d'abord la sélection univers, puis les scénarios
    de l'univers choisi.

    Campagnes découpées en chapitres (`campagne`/`chapitre`/`chapitre_suivant`)
    : UN SEUL cadre de sélection par campagne (le chapitre 1) — le titre
    affiché est celui de la campagne et `chapitre_total` permet d'annoncer
    le nombre de parties. Les chapitres suivants s'enchaînent automatiquement
    en partie via `scenarios_laelith_charger` (consigne `chapitre_suivant`)."""
    from .tools.scenarios import charger_catalogue
    data_dir = cfg.abs(cfg.paths.data_dir)
    ctx = ToolContext(
        partie_id=partie_id or "_",
        joueur="",
        data_dir=str(data_dir),
    )
    cata = charger_catalogue(ctx)
    universes: list[dict[str, Any]] = []
    for u in cata.get("universes", []):
        scen: list[dict[str, Any]] = []
        for s in u.get("scenarios", []):
            if s.get("chapitre") and int(s.get("chapitre") or 0) > 1:
                continue          # chapitre 2..N : s'enchaîne en partie
            sc = dict(s)
            if s.get("chapitre") and s.get("campagne"):
                sc["titre"] = str(s["campagne"])
                # Retire le détail technique « (22k caracteres) » du pitch.
                sc["pitch"] = _re_mod.sub(
                    r"\s*\(\d+\s*k\s*caract[eè]res\)\s*", "", str(s.get("pitch") or "")
                ).strip()
            scen.append(sc)
        u2 = dict(u)
        u2["scenarios"] = scen
        universes.append(u2)
    return universes


@app.post("/api/parties/{partie_id}/quest")
async def set_quest(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Définit la quête courante d'une partie (sélecteur de quête au démarrage)."""
    titre = payload.get("titre", "")
    pitch = payload.get("pitch", "")
    source = payload.get("source", "")
    # Aventure libre (aucun scénario) : marquer la quête comme choisie pour
    # que le sélecteur de scénario ne réapparaisse pas.
    if not str(titre).strip() and not str(source).strip():
        source = "libre"
    state = PartyState(data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id)
    etat = state.load()
    if "_erreur" in etat:
        raise HTTPException(status_code=404, detail="Partie introuvable.")
    etat["quete"] = {"titre": titre, "pitch": pitch, "source": source}
    # Bible du scénario : construite ici (picker) comme le ferait
    # `scenarios_laelith_charger` — trame + ennemis du module réinjectés au
    # MJ à chaque tour (fidélité au scénario, même sans recharge du module).
    # Best-effort : sans PDF lisible, la bible reste absente.
    _sid = (
        source.split("]", 1)[0].lstrip("[").strip()
        if source.startswith("[") else ""
    )
    if _sid:
        try:
            from .tools.base import ToolContext as _TC
            from .tools.scenarios import (
                _charger_catalogue_plat as _ccp,
                _construire_bible as _cb,
                _ennemis_du_texte as _edt,
                appliquer_depart as _apd,
                initialiser_memoire_scenario as _ims,
                extraire_pdf as _epdf,
            )
            _ctx_q = _TC(
                partie_id=partie_id, joueur="",
                data_dir=str(cfg.abs(cfg.paths.data_dir)),
            )
            _s = next(
                (x for x in _ccp(_ctx_q) if str(x.get("id", "")) == _sid),
                None,
            )
            if _s is not None:
                _txt = _epdf(_ctx_q, _s["pdf"]) if _s.get("pdf") else ""
                _bible = _cb(
                    _s, _txt,
                    str(etat.get("meta", {}).get("regles") or "D&D 3.5"),
                )
                _bible["ennemis"] = _edt(_ctx_q, _txt)
                etat["quete"]["bible"] = _bible
                # Mémoire de campagne + point de départ : les mêmes que via
                # `scenarios_laelith_charger` (partie e48e75dd : quête posée
                # par le picker → `memoire` jamais amorcée, position monde
                # inconnue → voyages impossibles).
                _ims(etat, _s, etat["quete"])
                try:
                    _apd(_ctx_q, etat, _sid)
                except Exception as e:                           # noqa: BLE001
                    print(f"[dnd35] Départ scénario non placé : {e}")
        except Exception as e:                                   # noqa: BLE001
            print(f"[dnd35] Bible scénario non construite (picker) : {e}")
    etat["phase"] = "exploration"
    err = state.save(etat)
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "quete": etat["quete"]}


# --------------------------------------------------------------------------- #
#  ⚔️ Combat server-driven — routes REST (tests E2E réels, clients riches).
#  Toute la mécanique (initiative, rotation, monstres, clôture, XP) est
#  résolue par le moteur serveur SANS LLM ; le LLM ne fait que narrer.
# --------------------------------------------------------------------------- #
@app.post("/api/parties/{partie_id}/combat/engager")
async def combat_engager(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Engage un combat (validation stricte du bestiaire + initiative
    officielle) puis exécute immédiatement la boucle serveur (tours de
    monstres, skips) jusqu'à un PJ actif ou la fin du combat."""
    monstres = str(payload.get("monstres") or "").strip()
    if not monstres:
        raise HTTPException(status_code=400, detail="monstres requis.")
    joueur = str(payload.get("joueur") or "")
    ctx = _ctx(partie_id, joueur)
    from .tools.base import _TOOL_REGISTRY, invoke_tool
    spec = _TOOL_REGISTRY.get("engager_combat")
    if spec is None:
        raise HTTPException(status_code=500, detail="tool engager_combat absent.")
    # 🔒 Même verrou par partie que le tour WS : sérialise les mutations
    # d'état de combat (sinon une écriture concurrente peut réinjecter un
    # `phase=combat` périmé après une clôture — partie 4b529064).
    async with sessions.get(partie_id).turn_lock:
        tr = await invoke_tool(spec, ctx, {"monstres": monstres})
        res_boucle = await _boucle_combat(
            ctx, timeout_secondes=cfg.game.combat_turn_timeout_seconds
        )
        etat = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
    return {
        "ok": not tr.text.startswith("⛔") and not tr.text.startswith("❌"),
        "text": tr.text,
        # 🛡️ (correctif bandeau) : consignes LLM épurées des events affichés.
        "events": [_epure_pour_table(ev) for ev in res_boucle.events],
        "patches": res_boucle.patches,
        "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "combat_termine": res_boucle.combat_termine,
    }


@app.post("/api/parties/{partie_id}/combat/boucle")
async def combat_boucle(partie_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Exécute une passe du moteur de combat serveur : joue les tours de
    monstres, passe les incapables, clôture si victoire/défaite (avec XP
    officielle + mémoire). `{"force": true}` termine le tour du PJ courant."""
    payload = payload or {}
    joueur = str(payload.get("joueur") or "")
    ctx = _ctx(partie_id, joueur)
    # 🔒 Même verrou par partie que le tour WS (cf. `combat_engager`).
    async with sessions.get(partie_id).turn_lock:
        etat_avant = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
        if etat_avant.get("phase") != "combat":
            return {"ok": False, "detail": "Aucun combat en cours.", "events": []}
        res = await _boucle_combat(
            ctx,
            force_avance=bool(payload.get("force")),
            timeout_secondes=cfg.game.combat_turn_timeout_seconds,
        )
        etat = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
    return {
        "ok": True,
        # 🛡️ (correctif bandeau) : consignes LLM épurées des events affichés.
        "events": [_epure_pour_table(ev) for ev in res.events],
        "patches": res.patches,
        "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "tour": etat.get("tour"),
        "combat_termine": res.combat_termine,
    }


@app.post("/api/parties/{partie_id}/combat/action")
async def combat_action(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Résout DÉTERMINISTEMENT l'attaque du personnage courant contre un
    monstre ennemi (jet officiel, dégâts de l'arme, application des PV),
    puis avance la rotation via le moteur serveur. Aucun LLM impliqué.

    Body: {"attaquant": "Brunhild", "cible": "Gobelin",
           "arme": "Hache de guerre", "nb_des": 1, "faces": 8, "bonus": 3}
    (arme/dés optionnels : arme improvisée 1d6, bonus = BBA + mod. FOR de la
    fiche si absents)."""
    from .tools.base import _TOOL_REGISTRY, invoke_tool

    # 🔒 Même verrou par partie que le tour WS (cf. `combat_engager`).
    async with sessions.get(partie_id).turn_lock:
        etat_avant = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
        if etat_avant.get("phase") != "combat":
            raise HTTPException(status_code=400, detail="Aucun combat en cours.")
        attaquant = str(
            payload.get("attaquant") or etat_avant.get("courant_tour_pour") or ""
        )
        cible = str(payload.get("cible") or "").strip()
        if not attaquant or not cible:
            raise HTTPException(status_code=400, detail="attaquant et cible requis.")
        joueur = str(payload.get("joueur") or attaquant)
        ctx = _ctx(partie_id, joueur)

        arme = str(payload.get("arme") or "arme improvisée")
        nb_des = int(payload.get("nb_des") or 1)
        faces = int(payload.get("faces") or 6)
        bonus_degats = int(payload.get("bonus") or 0)

        # Bonus d'attaque depuis la fiche (BBA + mod FOR/DEX) si non fourni.
        bonus_attaque = payload.get("bonus_attaque")
        if bonus_attaque is None:
            bonus_attaque = 0
            try:
                from .tools.fiches import _load_fiche
                fiche = _load_fiche(ctx, attaquant)
                if fiche:
                    caracs = fiche.get("carac") or {}
                    arme_l = arme.lower()
                    a_distance = any(
                        m in arme_l for m in
                        ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard")
                    )
                    cle = "DEX" if a_distance else "FOR"
                    mod = (int(caracs.get(cle, 10) or 10) - 10) // 2
                    bonus_attaque = int(fiche.get("bab") or 0) + mod
            except Exception:                                        # noqa: BLE001
                bonus_attaque = 0

        # CA de la cible depuis l'état de combat.
        ca = 10
        for mo in etat_avant.get("monstres_combat") or []:
            if str(mo.get("nom") or "").lower() == cible.lower():
                try:
                    ca = int(mo.get("ca") or 10)
                except (TypeError, ValueError):
                    ca = 10
                break

        events: list[str] = []
        async def _run(name: str, args: dict[str, Any]):
            spec = _TOOL_REGISTRY.get(name)
            if spec is None:
                return None
            tr = await invoke_tool(spec, ctx, args)
            # 🛡️ (correctif bandeau) : les consignes LLM (« recopie CE bonus
            # dans lancer_degats… ») sont épurées du texte AFFICHÉ ; les
            # contrôles ci-dessus lisent toujours le texte complet.
            events.append(_epure_pour_table(tr.text))
            return tr

        tr_atk = await _run("lancer_attaque", {
            "nom_attaquant": attaquant, "arme": arme,
            "bonus_attaque": int(bonus_attaque), "nom_cible": cible,
            "ca_cible": ca,
        })
        touche = tr_atk is not None and (
            "✅ **Touché**" in tr_atk.text or "⭐ **20 naturel**" in tr_atk.text
        )
        if touche:
            tr_dm = await _run("lancer_degats", {
                "nb_des": nb_des, "faces": faces, "bonus": bonus_degats,
                "arme_ou_sort": arme, "cible": cible,
            })
            m_total = _re_mod.search(r"[Dd]égâts infligés\s*:\s*(\d+)", tr_dm.text)
            if m_total:
                await _run("fiche_perso_infliger_degats", {
                    "nom": cible, "degats": int(m_total.group(1)),
                })

        res = await _boucle_combat(ctx, force_avance=True)
        # 🛡️ (correctif bandeau) : consignes LLM épurées des events affichés.
        events.extend(_epure_pour_table(ev) for ev in res.events)
        etat = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
    return {
        "ok": True, "touche": touche, "events": events,
        "patches": res.patches, "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "combat_termine": res.combat_termine,
    }


# --------------------------------------------------------------------------- #
#  Routes RAG (admin) — ingestion et stats du vector store ChromaDB.
# --------------------------------------------------------------------------- #
@app.get("/api/rag/stats")
async def rag_stats() -> dict[str, Any]:
    store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    if store is None:
        return {"enabled": False, "collections": {}}
    return {"enabled": True, "collections": store.stats()}


@app.post("/api/rag/ingest")
async def rag_ingest(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Déclenche l'ingestion incrémentale du corpus D&D 3.5 (admin).

    `{"force": true}` force la ré-embedding complète. Long-running en première
    exécution (~minutes selon le corpus) — prévoir de l'invoquer hors boucle
    utilisateur.
    """
    store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="RAG désactivé. Passez `rag.enabled: true` dans config.yaml "
                   "et vérifiez que le conteneur llamaembed est actif "
                   "(http://localhost:8081).",
        )
    force = bool((payload or {}).get("force"))
    stats = await store.ingest(force=force)
    return {"ingested": stats["ingested"], "skipped": stats["skipped"], "errors": stats["errors"]}


# --------------------------------------------------------------------------- #
#  WebSocket : canal chat multijoueur
# --------------------------------------------------------------------------- #
async def _ws_envoi(ws: WebSocket, payload: dict) -> None:
    """Envoi WS tolérant : si le client se déconnecte pendant un tour MJ
    long, tout `send_json` ultérieur lève (RuntimeError/WebSocketDisconnect)
    et faisait planter le handler ASGI entier. On avale au lieu de crasher —
    le `finally` de `ws_chat` fait déjà le ménage dans les registres.
    L'envoi est aussi BORNÉ (5 s) : un TCP mort sans FIN bloquerait sinon
    l'envoi (et donc la boucle du handler) pendant des minutes."""
    try:
        await asyncio.wait_for(ws.send_json(payload), timeout=5.0)
    except Exception:                                        # noqa: BLE001
        pass


async def _send_joined(ws: WebSocket, session: PartySession, partie_id: str) -> None:
    """Envoie le payload « joined » (historique + participants) à un client.

    Envoyé uniquement aux clients authentifiés - pour une partie protégée par
    mot de passe, l'historique ne doit pas fuiter avant la vérification.
    """
    history_payload = [
        {"role": m.role, "content": m.content}
        for m in session.history
        if m.role in ("user", "assistant") and m.content
    ]
    await _ws_envoi(ws, {
        "type": "sys",
        "event": "joined",
        "partie_id": partie_id,
        "participants": session.participants,
        "history": history_payload,
        "team_history": session.team_history[-100:],
    })
    # ⚔️ Bêta : retour en cours de COMBAT — le joueur qui revient (après une
    # déconnexion, un redémarrage serveur, une nuit…) doit savoir que le
    # combat se poursuit et de qui est le tour. Sans lui, l'écran affiche
    # le dernier échange et le combat semble « coincé ».
    try:
        etat = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
        ).load()
        if etat.get("phase") == "combat":
            courant = str(etat.get("courant_tour_pour") or "?")
            pj = next(
                (p for p in (etat.get("pj") or [])
                 if p.get("nom") == courant), None
            )
            qui = (
                f"{courant} (joué par {pj.get('joueur')})"
                if pj is not None and pj.get("joueur")
                else courant
            )
            await _ws_envoi(ws, {
                "type": "sys",
                "event": "combat_reprise",
                "detail": (
                    f"⚔️ Combat en cours — round {etat.get('tour', 1)}, "
                    f"au tour de {qui}. Envoyez votre action."
                ),
            })
    except Exception:                                            # noqa: BLE001
        pass


@app.websocket("/ws/{partie_id}")
async def ws_chat(ws: WebSocket, partie_id: str) -> None:
    await ws.accept()
    session: PartySession = sessions.get(partie_id)
    pw_hash = _party_password_hash(partie_id)

    if pw_hash is None:
        # Partie ouverte : accès immédiat à l'historique + broadcasts.
        session.connections.add(ws)
        await _send_joined(ws, session, partie_id)
    else:
        # Partie protégée : on exige le mot de passe via un message "join"
        # avant de révéler quoi que ce soit.
        await _ws_envoi(ws, {
            "type": "sys",
            "event": "auth_required",
            "partie_id": partie_id,
            "detail": "Cette partie est protégée par un mot de passe.",
        })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _ws_envoi(ws, {"type": "sys", "event": "error",
                                     "detail": "payload non JSON"})
                continue

            mtype = msg.get("type")
            player = (msg.get("player") or "Joueur").strip()

            if mtype == "ping":
                # Heartbeat client → serveur : garde la connexion vivante
                # (NAT/proxy) et détecte vite les TCP morts ; sans réponse,
                # le client force une reconnexion au lieu d'attendre un
                # envoi ultérieur qui n'arrive jamais (écran gelé).
                await _ws_envoi(ws, {"type": "pong"})
                continue

            if mtype == "state":
                # 🛡️ M3/m5 : le client peut demander l'état courant au lieu de
                # rafraîchir la carte en continu. Réponse légère, sans grille.
                try:
                    _etat_c = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    _dj_c = _etat_c.get("donjon") or {}
                    await _ws_envoi(ws, {
                        "type": "state",
                        "phase": _etat_c.get("phase"),
                        "tour": _etat_c.get("tour"),
                        "courant_tour_pour": _etat_c.get("courant_tour_pour"),
                        "pj": _etat_c.get("pj") or [],
                        "monstres_combat": _etat_c.get("monstres_combat") or [],
                        "donjon": {
                            "id": _dj_c.get("id"),
                            "etage": _dj_c.get("etage"),
                            "courant": _dj_c.get("courant"),
                            "nb_salles": len(_dj_c.get("grille") or []),
                        },
                    })
                except Exception:                                # noqa: BLE001
                    await _ws_envoi(ws, {"type": "pong"})
                continue

            if mtype == "cancel":
                # 🛡️ B31 : annulation explicite du tour en cours. Sans elle,
                # un tour bloqué n'avait AUCUN remède côté client (ni bouton,
                # ni message, ni endpoint) et la partie restait verrouillée
                # jusqu'au redémarrage du serveur.
                _age = session.verrou_age()
                if not session.thinking:
                    await _ws_envoi(ws, {
                        "type": "sys", "event": "cancel_inutile",
                        "detail": "Aucun tour en cours à annuler.",
                    })
                    continue
                if session.thinking_annules and _age < _TOUR_VERROU_MAX_S:
                    # Une seule annulation par tour : évite le spam qui
                    # relancerait la boucle en plein travail.
                    await _ws_envoi(ws, {
                        "type": "sys", "event": "cancel_refuse",
                        "detail": (
                            "Annulation déjà demandée ce tour — attendez la "
                            "réponse du MJ ou le délai d'interdiction."
                        ),
                    })
                    continue
                session.verrou_lever()
                session.thinking_annules += 1
                _log_tour(
                    "tour annulé par le joueur après %.0f s (partie %s)",
                    _age, partie_id,
                )
                await session.broadcast({
                    "type": "sys",
                    "event": "turn_cancelled",
                    "detail": (
                        "🛑 Tour du MJ annulé après "
                        f"{_age:.0f} s. L'état du jeu est celui du dernier "
                        "tour terminé — renvoyez votre action."
                    ),
                })
                continue

            if mtype == "join":
                # Un personnage sélectionné est OBLIGATOIRE pour rejoindre :
                # pas de participant « fantôme » sans fiche rattachée.
                personnage = (msg.get("personnage") or "").strip()
                if not personnage:
                    # 🛡️ m18 : évènement DÉDIÉ, marqué transitoire — le
                    # message était auparavant affiché comme un message de
                    # chat permanent entre deux narrations, et restait à
                    # l'écran après avoir choisi un personnage.
                    await _ws_envoi(ws, {
                        "type": "sys",
                        "event": "join_need_personnage",
                        "detail": (
                            "Sélectionnez un personnage sur la page d'accueil "
                            "avant de rejoindre la partie."
                        ),
                        "transitoire": True,
                    })
                    continue
                # 🔒 Bêta (C2) : authentification optionnelle par token — le
                # compte VÉRIFIÉ prime sur le nom déclaré (anti-usurpation
                # du `player`). Anciens clients sans token : inchangé.
                token_ws = str(msg.get("token") or "")
                user_verifie = auth_mod.verifier_token(
                    _dossier_donnees(), token_ws
                ) if token_ws else None
                if user_verifie:
                    session.ws_user[ws] = user_verifie
                    player = user_verifie
                if pw_hash is not None:
                    mdp = msg.get("password") or ""
                    if _hash_mot_de_passe(mdp) != pw_hash:
                        await _ws_envoi(ws, {
                            "type": "sys",
                            "event": "auth_failed",
                            "detail": "Mot de passe incorrect.",
                        })
                        continue
                    session.authenticated.add(ws)
                    session.connections.add(ws)
                # Rattachement du personnage choisi (menu déroulant côté
                # client) → le PJ rejoint l'état de partie (liste pj).
                # 🛡️ P5 (audit parties complètes) : les PNJ du module et les
                # PJ partagent le même espace de nommage (positions, initiative,
                # fiches par slug). Un PJ homonyme d'un PNJ (« Cassyt »,
                # « Rorreth Monforoth »…) rendait les deux indistinguables pour
                # le MJ. On NE REFUSE PAS (le joueur a le droit d'incarner ce
                # nom), mais on prévient la table.
                try:
                    _etat_p5 = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    _pnj_p5 = [str(p.get("nom") or "").strip()
                               for p in (_etat_p5.get("pnj") or [])
                               if p.get("nom")]
                    _homonyme_p5 = [n for n in _pnj_p5
                                    if n.casefold() == personnage.strip()
                                    .casefold()]
                except Exception:                                # noqa: BLE001
                    _homonyme_p5 = []
                if _homonyme_p5:
                    await session.broadcast({
                        "type": "sys",
                        "event": "nom_homonyme_pnj",
                        "detail": (
                            "⚠️ Le personnage " + personnage
                            + " porte le même nom qu'un PNJ du module ("
                            + ", ".join(_homonyme_p5) + "). Le MJ précisera "
                            "PNJ vs PJ à chaque fois — soyez explicites."
                        ),
                    })
                    print("[dnd35] P5 : homonyme PNJ/PJ — " + personnage)
                # Rattachement du personnage choisi (menu déroulant côté
                # client) → le PJ rejoint l'état de partie (liste pj).
                fiche_enregistree = persos_mod.enregistrer_personnage_partie(
                    _dossier_donnees(), partie_id, personnage, player
                )
                if fiche_enregistree is None:
                    # Fiche inexistante ou n'appartenant pas au joueur : on
                    # refuse AVANT d'inscrire le participant.
                    # 🛡️ m18 : évènement DÉDIÉ, marqué transitoire — ce
                    # message était affiché comme un message de chat permanent
                    # entre deux narrations et restait à l'écran.
                    await _ws_envoi(ws, {
                        "type": "sys",
                        "event": "join_need_personnage",
                        "detail": (
                            f"Personnage « {personnage} » introuvable ou non "
                            "rattaché à votre compte. Choisissez-en un autre "
                            "sur la page d'accueil."
                        ),
                        "transitoire": True,
                    })
                    continue
                # 🔒 Bêta (C2) : mémorise le personnage incarné par CETTE
                # connexion — la garde de tour l'exige (le même compte peut
                # incarner deux PJ dans deux onglets différents).
                session.ws_personnage[ws] = personnage
                session.add_participant(player)
                # Pour une partie protégée, l'historique n'arrive qu'ici.
                if pw_hash is not None:
                    await _send_joined(ws, session, partie_id)
                await session.broadcast({
                    "type": "sys",
                    "event": "participant_joined",
                    "player": player,
                    "participants": session.participants,
                    "personnage": (
                        fiche_enregistree.get("nom") if fiche_enregistree else None
                    ),
                })
                continue

            if mtype == "dice":
                # 🎲 Bêta (r2) : un jet manuel du joueur est INFORMATIF — il
                # est diffusé à la table et mémorisé dans l'historique, mais
                # n'invoque PLUS le MJ (avant : un tour LLM complet par jet,
                # interprété en fiction : attaque sur une « cible invisible »,
                # test de poison improvisé…). Les jets officiels restent
                # lancés par le serveur (tools).
                texte_des = str(msg.get("text") or "").strip()
                if not texte_des:
                    continue
                session.remember_player_message(player, texte_des)
                await session.broadcast({
                    "type": "player",
                    "player": player,
                    "text": texte_des,
                    "client_id": str(msg.get("client_id") or ""),
                })
                continue

            if mtype == "say":
                if pw_hash is not None and ws not in session.authenticated:
                    await _ws_envoi(ws, {
                        "type": "sys",
                        "event": "auth_required",
                        "detail": "Partie protégée : rejoignez avec le mot de passe.",
                    })
                    continue
                # 🔒 Bêta (C2) : identité canonique = compte vérifié (token)
                # sinon nom déclaré ; personnage = celui mémorisé au join.
                await _handle_say(
                    ws,
                    session,
                    partie_id,
                    session.utilisateur_de(ws, player),
                    msg.get("text", ""),
                    personnage=session.personnage_de(ws),
                    client_id=str(msg.get("client_id") or ""),
                )
                continue

            if mtype == "team_say":
                if pw_hash is not None and ws not in session.authenticated:
                    await _ws_envoi(ws, {
                        "type": "sys",
                        "event": "auth_required",
                        "detail": "Partie protégée.",
                    })
                    continue
                team_text = msg.get("text", "").strip()
                if not team_text:
                    continue
                session.remember_team_message(player, team_text)
                # Renvoie à tous les joueurs connectés (y compris l'auteur).
                await session.broadcast({
                    "type": "team_msg",
                    "player": player,
                    "text": team_text,
                })
                continue

            if mtype == "audio_signal":
                # Relay WebRTC : signal ICE/offer/answer vers tous les AUTRES joueurs.
                signal_payload = msg.get("signal", {})
                for conn in session.connections:
                    if conn is not ws:
                        try:
                            await conn.send_json({
                                "type": "audio_signal",
                                "player": player,
                                "signal": signal_payload,
                            })
                        except Exception:
                            pass
                continue

            await _ws_envoi(ws, {"type": "sys", "event": "error",
                                 "detail": f"type inconnu: {mtype}"})
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Client déconnecté pendant un tour MJ long : `receive_text` lève
        # « WebSocket is not connected » (état ≠ CONNECTED) au lieu d'un
        # WebSocketDispatch propre — traité comme une déconnexion normale
        # (crash ASGI observé en e2e réel sans ce filet).
        pass
    finally:
        session.connections.discard(ws)
        session.authenticated.discard(ws)
        # 🔒 Bêta (C2) : purge des identités par connexion.
        session.ws_personnage.pop(ws, None)
        session.ws_user.pop(ws, None)


# Compteur global de tours MJ actifs (toutes parties confondues) : géré par
# server/gpu.py qui ARBITRE aussi le GPU — un tour LLM attend la fin des
# générations ComfyUI en cours (et réciproquement), pour ne jamais charger
# llama.cpp ET ComfyUI en même temps sur la même carte graphique.
# Tâches d'arrière-plan (illustrations de monstres après le dm final) : la
# référence est gardée pour éviter le garbage-collect prématuré.
_bg_tasks: set[asyncio.Task] = set()
# Unload différé (llm.unload_after_turn = false) : tâche en attente, annulée si
# un nouveau tour démarre avant l'expiration du délai.
_pending_unload: Optional[asyncio.Task] = None
# Verrou autour du déchargement : un tour qui démarre pendant l'unload
# (≈1 s) l'attend au lieu de perdre le modèle en cours de route.
# C'est le VERROU PARTAGÉ de server/gpu.py : il sérialise aussi les
# unload/reload autour des générations d'image. Deux verrous distincts
# permettraient à un unload d'image et à un unload de fin de tour de se
# croiser, et le tour suivant pourrait perdre le modèle entre les deux.
_unload_guard: asyncio.Lock = _gpu.model_lock()


def _cancel_pending_unload() -> None:
    """Annule un unload différé en attente (un tour reprend la main)."""
    global _pending_unload
    if _pending_unload is not None and not _pending_unload.done():
        _pending_unload.cancel()
    _pending_unload = None


async def _turn_begin() -> None:
    _cancel_pending_unload()
    # ⚠️ PAS de `async with _unload_guard` ici : c'est le même verrou que
    # `_gpu.model_lock()`, et `_gpu.turn_begin()` l'acquiert LUI-MÊME.
    # asyncio.Lock n'étant pas réentrant, l'imbriquer deadlockait le MJ à
    # chaque tour. La sérialisation avec l'unload différé est assurée par
    # turn_begin, qui prend le verrou avant d'entrer dans le tour.
    await _gpu.turn_begin()


async def _turn_end() -> bool:
    """Décrémente le compteur de tours ; True s'il ne reste aucun tour actif."""
    return await _gpu.turn_end()


async def _delayed_unload_task(app: FastAPI, delay_s: float) -> None:
    """Décharge le modèle après `delay_s` secondes d'inactivité.

    Le garde `_unload_guard` est conservé pendant l'appel réseau d'unload :
    un tour qui démarre pendant l'unload attend sa fin (≈1 s) au lieu de
    perdre le modèle en cours de route. La tâche est annulée par
    `_cancel_pending_unload` si un tour reprend avant l'expiration du délai.
    """
    global _pending_unload
    try:
        await asyncio.sleep(delay_s)
        async with _unload_guard:
            if _gpu.turns_actifs() > 0:
                return  # un tour a repris — il reprogrammera l'unload
            # 🛡️ B11/M13 (audit parties complètes) : le chien ne regardait que
            # le compteur de tours ACTIFS du GPU. Un tour déjà annoncé
            # (« Le MJ réfléchit… ») mais dont la génération démarre après
            # l'unload perdait le modèle en cours de route — et chaque tour
            # suivait alors un cycle unload → reload de 20 à 60 s. On vérifie
            # aussi le verrou de session de chaque partie : s'il est pris, un
            # tour est en cours ou sur le point de démarrer.
            try:
                for _sess in sessions.values():
                    if getattr(_sess, "thinking", False):
                        print("[dnd35] unload reporté : un tour est en cours "
                              f"(partie {_sess.partie_id})")
                        return
            except Exception:                                    # noqa: BLE001
                pass
            _pending_unload = None
            await app.state.client.unload_model()
    except asyncio.CancelledError:
        pass
    except Exception:                                               # noqa: BLE001
        pass


def _extrait_arme_bonus(attaques: str) -> Optional[tuple[str, int]]:
    """Parse la première attaque du bestiaire : « Cimeterre +2 (corps à
    corps) ; arc court +3 ». Renvoie (arme, bonus) ou None si injouable.
    (Conservé pour compatibilité — le moteur de combat serveur
    `game.combat` possède sa propre implémentation généralisée.)"""
    m = _re_mod.match(r"(.+?)\s*([+-]\d+)\s*(?:\(|$)", (attaques or "").strip())
    if not m:
        return None
    arme = m.group(1).strip()
    return (arme, int(m.group(2))) if arme else None


def _exces_degats_monstres(
    trace: list[dict[str, Any]],
    monstres: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Détecte les dégâts APPLIQUÉS EN EXCÈS sur des monstres suivis.

    Le LLM appelle parfois `fiche_perso_infliger_degats` une seconde fois
    avec ses PROPRES chiffres narrés, en doublon du jet serveur déjà
    consommé (observé en partie réelle : un zombie de 5 PV sous 4 puis
    5 dégâts). On compare, par monstre suivi, le total appliqué
    (`fiche_perso_infliger_degats` de la trace du tour) au total
    RÉELLEMENT JETÉ (`lancer_degats`) : tout excédent est restitué par
    l'appelant. Les seuls appels du tour LLM sont lus — les attaques
    automatiques du moteur serveur passent par ailleurs.

    ⚠️ Alignement des CLÉS (partie 263f82dc) : les dégâts sont rattachés au
    monstre RÉELLEMENT touché — nom résolu dans le résultat du tool
    (« 💥 <nom> (monstre) subit … »), sinon libellé LLM résolu par
    exact/préfixe contre les labels suivis (même logique que
    `_infliger_degats_monstre`). L'ancien comptage par libellé brut
    fabriquait des EXCÈS FANTÔMES (infliger "Squelette" frappant réellement
    « Squelette (2) » compté sur « Squelette ») → PV restitués à un cadavre
    qui « ressuscitait » (squelettes ré-engagés, « PV 3/3 — ☠️ DÉTRUIT »).

    Renvoie `{nom_monstre: {"exces": N, "jetes": M}}` — `jetes` = total
    légitimement jeté contre CE monstre ; l'appelant plafonne la
    restauration à `pv_max - jetes` pour ne JAMAIS ressusciter une créature
    détruite par les seuls dégâts jetés.
    """
    import unicodedata as _u2

    def _nn(s: str) -> str:
        n = _u2.normalize("NFKD", str(s or "").strip().lower())
        return "".join(c for c in n if not _u2.combining(c))

    def _vivante(mo: dict[str, Any]) -> bool:
        conds = mo.get("conditions") or []
        return (
            "Détruit" not in conds and "Detruit" not in conds
            and int(mo.get("pv", 0) or 0) > 0
        )

    def _resolve_cle(label: str) -> str:
        """Libellé LLM → nom normalisé du monstre RÉELLEMENT visé.

        La résolution favorise les créatures **VIVANTES** (même politique que
        le tool `lancer_degats` : vivant d'abord, préfixe autorisé), sur
        l'EXACT comme sur le PRÉFIXE. C'est ce qui évite l'excès fantôme de
        la partie 263f82dc : le libellé « Squelette » d'un `lancer_degats`
        qui a RÉELLEMENT frappé « Squelette (2) » (vivant, préfixe) doit
        s'aligner sur CETTE clé — pas sur le cadavre « Squelette » (Détruit)
        qui correspondait par exact-match et épinglait l'excès sur le mauvais
        monstre (« exces 15, jetes 0 » sur « Squelette (2) » → restauration
        d'un monstre que les seuls dégâts jetés n'avaient jamais atteint).
        L'exact d'un cadavre n'est utilisé qu'en dernier recours, quand
        AUCUNE créature vivante ne correspond (monstre déjà détruit suivi)."""
        nl = _nn(label)
        for pool in ([m for m in monstres if _vivante(m)], monstres):
            for mo in pool:                       # a) exact — vivant d'abord
                if _nn(str(mo.get("nom") or "")) == nl:
                    return nl
            for mo in pool:                       # b) préfixe — comme le tool
                mn = _nn(str(mo.get("nom") or ""))
                if nl and len(nl) >= 4 and mn.startswith(nl):
                    return mn
                if len(mn) >= 4 and nl.startswith(mn):
                    return mn
        return nl

    re_resolu = _re_mod.compile(r"💥\s*(.+?)\s*\(monstre\)\s*subit")
    jetes: dict[str, int] = {}
    appliques: dict[str, int] = {}
    for tc in trace or []:
        if not isinstance(tc, dict) or not tc.get("ok"):
            continue
        nom_tc = tc.get("name")
        if nom_tc == "lancer_degats":
            c = _resolve_cle(
                str((tc.get("args") or {}).get("cible") or ""))
            m_total = _re_mod.search(
                r"[Dd]égâts infligés\s*:\s*(\d+)", tc.get("text") or "")
            if c and m_total:
                jetes[c] = jetes.get(c, 0) + int(m_total.group(1))
        elif nom_tc == "fiche_perso_infliger_degats":
            try:
                d = int((tc.get("args") or {}).get("degats") or 0)
            except (TypeError, ValueError):
                continue
            if d <= 0:
                continue
            m_res = re_resolu.search(tc.get("text") or "")
            if m_res:
                c = _resolve_cle(m_res.group(1))
            else:
                c = _resolve_cle(
                    str((tc.get("args") or {}).get("nom") or ""))
            appliques[c] = appliques.get(c, 0) + d
    exces: dict[str, dict[str, int]] = {}
    for mo in monstres or []:
        nom = str(mo.get("nom") or "").strip()
        cle = _nn(nom)
        surplus = appliques.get(cle, 0) - jetes.get(cle, 0)
        if nom and surplus > 0:
            exces[nom] = {
                "exces": surplus,
                "jetes": jetes.get(cle, 0),
            }
    return exces


async def _rouler_degats_attaque(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Optional[Any],
    result: Any,
    attaquant: str,
    arme: str,
    cible: str,
) -> Optional[str]:
    """Rattrapage « touché SANS dégâts » : le serveur lance LUI-MÊME les
    dégâts de l'arme (partie réelle : une attaque réussie restée sans
    `lancer_degats` narrait un coup qui n'existait pas dans l'état).

    Dés de dégâts, dans l'ordre : fiche bestiaire du MONSTRE attaquant (armes
    de taille spéciale), sinon catalogue PHB pour un PJ. Bonus = mod. FOR
    (×1,5 à deux mains) / +0 à distance — le même calcul officiel que
    `lancer_degats`. Renvoie le texte mécanique (None si l'arme est
    indéterminable : on n'invente pas de dés)."""
    from .tools.dice import _arme_fiche_monstre, _de_arme_catalogue

    nb, faces, bonus = None, None, 0
    fiche_m = _arme_fiche_monstre(ctx, attaquant) if attaquant else None
    if fiche_m is not None and (not arme or _arme_citee(arme, fiche_m[0])):
        _, nb, faces = fiche_m
        bonus = 0  # le bonus du bestiaire est inclus dans son type d'attaque
    else:
        de_cat = _de_arme_catalogue(arme or "")
        if de_cat is None:
            return None
        nb, faces, _nom_canon = de_cat
        bonus = await _bonus_degats_pj(ctx, attaquant, arme)
    tr_dm = await orch.execute_tool_direct(
        "lancer_degats",
        {"nb_des": nb, "faces": faces, "bonus": bonus,
         "arme_ou_sort": arme or "arme improvisée", "cible": cible,
         "attaquant": attaquant},
        ctx, on_event, result,
    )
    if tr_dm is None or tr_dm.text.startswith(("❌", "⛔", "⚠️")):
        return None
    m = _re_mod.search(r"[Dd]égâts infligés\s*:\s*(\d+)", tr_dm.text)
    if not m:
        return None
    tr_inf = await orch.execute_tool_direct(
        "fiche_perso_infliger_degats",
        {"nom": cible, "degats": int(m.group(1))},
        ctx, on_event, result,
    )
    entete = (
        "⚙️ _Rattrapage serveur : attaque réussie sans dégâts — le serveur "
        f"a résolu lui-même les dégâts ({arme or 'arme improvisée'})._"
    )
    blocs = [entete, tr_dm.text]
    if tr_inf is not None and not tr_inf.text.startswith("❌"):
        blocs.append(tr_inf.text)
    return "\n\n".join(blocs)


def _arme_citee(texte: str, nom_arme: str) -> bool:
    """Vrai si `texte` cite l'arme `nom_arme` (mots entiers, sans accents)."""
    import unicodedata as _u

    def _n(s: str) -> str:
        nf = _u.normalize("NFKD", (s or "").lower())
        return " ".join(
            "".join(c for c in nf if not _u.combining(c)).split()
        )

    t, a = _n(texte), _n(nom_arme)
    if not t or not a:
        return False
    return t == a or bool(
        _re_mod.search(r"(?:^|\s)" + _re_mod.escape(a) + r"(?:\s|$)", t)
    )


async def _bonus_degats_pj(ctx: ToolContext, attaquant: str, arme: str) -> int:
    """Bonus de dégâts officiel d'un PJ (mod. FOR, ×1,5 à deux mains, +0
    distance) lu sur sa fiche — 0 si fiche absente ou cas non couvert."""
    try:
        from .tools.fiches import _load_fiche as _charger_fiche
        fiche = _charger_fiche(ctx, attaquant)
        if fiche is None:
            return 0
        caracs = fiche.get("carac") or {}
        a_l = (arme or "").lower()
        if any(m in a_l for m in ("arc", "arbalète", "arbalet", "fronde",
                                  "javelot", "dard", "sarbacane")):
            return 0
        val = int(caracs.get("FOR", 10) or 10)
        mod = (val - 10) // 2
        if any(m in a_l for m in ("deux mains", "2 mains")):
            return mod * 3 // 2
        return mod
    except Exception:                                        # noqa: BLE001
        return 0


async def _appliquer_degats_oublies(
    orch: Orchestrator,
    result: Any,
    ctx: ToolContext,
    on_event: Optional[Any],
) -> str:
    """Rattrapage mécanique : tout `lancer_degats` réussi dont les dégâts
    n'ont PAS été appliqués ensuite (`fiche_perso_infliger_degats` absent de
    la trace du tour) est appliqué ici par le serveur. Les dégâts jetés ne
    doivent jamais rester sans effet sur la cible.

    Anti double-application (file par cible, dans l'ordre de la trace) :
    - un `infliger(D)` consomme le plus ancien jet orphelin de même cible et
      de même total (cas nominal, touche par touche) ;
    - sinon, si la SOMME des jets orphelins de la cible vaut D, il les
      consomme tous (le LLM a appliqué les touches en un seul appel) ;
    - les jets restés orphelins en fin de tour sont appliqués par le serveur.

    Renvoie le texte mécanique à ajouter à la narration ("" si rien à faire).
    """
    import unicodedata as _uni

    def _norm_nom(s: str) -> str:
        n = _uni.normalize("NFKD", str(s or "").strip().lower())
        return "".join(c for c in n if not _uni.combining(c))

    trace = result.tool_calls_trace
    # File des jets orphelins : cible normalisée → liste de totaux.
    orphelins: dict[str, list[int]] = {}

    for tc in trace:
        nom_tc = tc.get("name")
        if nom_tc == "lancer_degats" and tc.get("ok"):
            cible = str((tc.get("args") or {}).get("cible") or "").strip()
            m_total = _re_mod.search(
                r"[Dd]égâts infligés\s*:\s*(\d+)", tc.get("text") or "")
            if cible and m_total:
                orphelins.setdefault(_norm_nom(cible), []).append(
                    int(m_total.group(1)))
        elif nom_tc == "fiche_perso_infliger_degats" and tc.get("ok"):
            args_j = tc.get("args") or {}
            try:
                d = int(args_j.get("degats") or 0)
            except (TypeError, ValueError):
                continue
            cle = _norm_nom(args_j.get("nom"))
            file_c = orphelins.get(cle) or []
            # 1) montant exact → consomme le plus ancien jet correspondant.
            if d in file_c:
                file_c.pop(file_c.index(d))
            # 2) somme des jets restants → le LLM a appliqué plusieurs
            #    touches en un seul appel.
            elif file_c and sum(file_c) == d:
                file_c.clear()
            if not file_c and cle in orphelins:
                del orphelins[cle]

    # Jets restés orphelins → le serveur les applique (toujours indiqués).
    lignes: list[str] = []
    for cible_norm, totaux in orphelins.items():
        for total in totaux:
            tr = await orch.execute_tool_direct(
                "fiche_perso_infliger_degats",
                {"nom": cible_norm, "degats": total},
                ctx, on_event, result,
            )
            if tr is not None and not tr.text.startswith("❌"):
                lignes.append(tr.text)

    # 2ᵉ filet (rattrapage « touché sans dégâts ») : une attaque RÉUSSIE
    # (lancer_attaque → Touché / 20 naturel) dont la cible n'a reçu AUCUN
    # lancer_degats NI aucune application (`fiche_perso_infliger_degats`).
    # Le LLM narre alors le montant en prose — on récupère le chiffre annoncé
    # près du nom de la cible (fenêtre courte) et on l'applique réellement.
    # S'il est absent ou ambigu, on n'invente rien : les PV restent cohérents.
    degats_jetes: set[str] = set()
    deja_applique: set[str] = set()
    for tc in trace:
        nom_tc = tc.get("name")
        if nom_tc == "lancer_degats" and tc.get("ok"):
            c = str((tc.get("args") or {}).get("cible") or "").strip()
            if c:
                degats_jetes.add(_norm_nom(c))
        elif nom_tc == "fiche_perso_infliger_degats" and tc.get("ok"):
            c = str((tc.get("args") or {}).get("nom") or "").strip()
            if c:
                deja_applique.add(_norm_nom(c))
    re_montant = _re_mod.compile(
        r"\+?\s*(\d{1,3})\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
        _re_mod.IGNORECASE,
    )
    # Chute de PV narrée (« ses points de vie chutant de 5 à 4 », « passe de
    # 12 à 8 PV ») : forme OBSERVÉE en partie réelle pour une attaque réussie
    # sans lancer_degats — l'ancien fillet (montant « N points de dégâts »)
    # ne la détectait pas et le coup réussi restait sans effet.
    re_chute_pv = _re_mod.compile(
        r"(?:chut(?:ant|er?)?|pass(?:ant|er?)?|descend(?:ant|er?)?|tombe"
        r"|baiss(?:ant|er?)?)\s*(?:brutalement\s*)?(?:de\s+)?(\d{1,3})"
        r"\s*(?:à|→|->)\s*(\d{1,3})",
        _re_mod.IGNORECASE,
    )
    narration = result.narration or ""
    cibles_traitees: set[str] = set()
    for tc in trace:
        if tc.get("name") != "lancer_attaque" or not tc.get("ok"):
            continue
        texte_tc = tc.get("text") or ""
        if ("✅ **Touché**" not in texte_tc) and ("⭐ **20 naturel**" not in texte_tc):
            continue
        cible = str((tc.get("args") or {}).get("nom_cible") or "").strip()
        cle_cible = _norm_nom(cible)
        if (not cible or cle_cible in degats_jetes
                or cle_cible in deja_applique or cle_cible in cibles_traitees):
            continue
        montants: list[int] = []
        for m_nom in _re_mod.finditer(_re_mod.escape(cible), narration,
                                      _re_mod.IGNORECASE):
            fenetre = narration[max(0, m_nom.start() - 100):m_nom.end() + 100]
            montants.extend(int(x) for x in re_montant.findall(fenetre))
            # Chutes de PV « de X à Y » : montant = X − Y si cohérent (> 0).
            for m_ch in re_chute_pv.finditer(fenetre):
                try:
                    chute = int(m_ch.group(1)) - int(m_ch.group(2))
                except ValueError:
                    continue
                if 0 < chute <= 60:
                    montants.append(chute)
        if not montants:
            # Dernier recours : le LLM n'annonce AUCUN montant → le serveur
            # lance LUI-MÊME les dégâts de l'arme utilisée (le coup a Touché :
            # ne rien appliquer laisserait la fiction créer un état faux).
            tr_roll = await _rouler_degats_attaque(
                orch, ctx, on_event, result,
                attaquant=str((tc.get("args") or {}).get("nom_attaquant") or ""),
                arme=str((tc.get("args") or {}).get("arme") or ""),
                cible=cible,
            )
            if tr_roll is not None:
                cibles_traitees.add(cle_cible)
                lignes.append(tr_roll)
            continue
        uniques = sorted(set(montants))
        if len(uniques) != 1:
            continue
        cibles_traitees.add(cle_cible)
        tr = await orch.execute_tool_direct(
            "fiche_perso_infliger_degats",
            {"nom": cible, "degats": uniques[0]},
            ctx, on_event, result,
        )
        if tr is not None and not tr.text.startswith("❌"):
            lignes.append(tr.text)
    return "\n\n".join(lignes)


async def _appliquer_soins_oublies(
    orch: Orchestrator,
    result: Any,
    ctx: ToolContext,
    on_event: Optional[Any],
    actif_avant: str,
    texte_joueur: str = "",
) -> str:
    """Rattrapage mécanique des SOINS (miroir de `_appliquer_degats_oublies`) :
    quand le joueur actif a déclaré un soin (kit de premiers secours, potion,
    sort narré…) mais que `fiche_perso_soigner` n'a JAMAIS été appelé dans le
    tour — le LLM se contente souvent de NARRER « Vous avez récupéré 3 PV »
    (corrigé 3× en tant que simulation par D1bis/5bis-a sans jamais appeler
    le tool) — le serveur applique alors le MONTANT ANNONCÉ à la fiche.
    Sinon le chat affirme une guérison que l'état n'enregistre jamais
    (partie 5f3e31c9 : chat « 3 PV → 6/17 » mais état réel resté 3/17).

    N'applique RIEN si aucun montant chiffré n'est annoncé près du verbe de
    soin (on n'invente pas de gain) et ne touche pas aux soins déjà résolus.

    `texte_joueur` = message du joueur CE tour : il alimente la porte
    « soin déclaré » du chemin kit C6bis (partie 120e9243).

    Renvoie le texte mécanique à ajouter à la narration ("" si rien à faire).
    """
    if not actif_avant:
        return ""
    if any(
        tc.get("name") == "fiche_perso_soigner" and tc.get("ok")
        for tc in result.tool_calls_trace
    ):
        return ""
    narration = result.narration or ""
    # Montant annoncé près d'un verbe de soin/récupération (« récupéré 3 PV »,
    # « soigne 5 points de vie », « rend 4 PV », « restaure 2 PV »).
    m_annonce = _re_mod.search(
        r"(?:r[éèe]cup[éèe]r(?:ant|[éèe]|er)?|soign(?:e|ant)?|rend|restaure)"
        r"\w*\s*(?:\*\*)?\d{1,3}(?:\*\*)?\s*(?:PV\b|points?\s+de\s+vie)",
        narration,
    )
    if not m_annonce:
        # 🩹 C6bis — Soin DÉCLARÉ mais narré SANS montant ni tool : si le PJ
        # possède un kit de premiers secours (ou équivalent), le serveur
        # résout lui-même le soin (1d4) au lieu de laisser le tour sans
        # effet — sinon le joueur croit avoir agi, attend le timeout et
        # meurt au tour du monstre (partie 5f3e31c9, msg 20).
        return await _soin_kit_sans_montant(
            orch, ctx, on_event, result, actif_avant, texte_joueur)
    m_soin = _re_mod.search(r"(\d{1,3})(?:\*\*)?\s*(?:PV\b|points?\s+de\s+vie)",
                            m_annonce.group(0))
    if not m_soin:
        return ""
    try:
        soin_total = int(m_soin.group(1))
    except (TypeError, ValueError):
        return ""
    if soin_total <= 0:
        return ""
    tr = await orch.execute_tool_direct(
        "fiche_perso_soigner",
        {"nom": actif_avant, "soin": soin_total, "interne": True},
        ctx, on_event, result,
    )
    if tr is not None and not tr.text.startswith("❌"):
        return (
            "ℹ️ **Soin appliqué par le serveur** : le montant narré "
            f"(« {m_annonce.group(0).strip()} ») était annoncé sans appel à "
            f"`fiche_perso_soigner` — {tr.text}."
        )
    return ""


async def _soin_kit_sans_montant(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Any,
    result: Any,
    actif_avant: str,
    texte_joueur: str = "",
) -> str:
    """Résout un soin DÉCLARÉ (kit de premiers secours en main) que le LLM
    a narré SANS montant ni appel à `fiche_perso_soigner` : le serveur
    lance lui-même le 1d4 et l'applique à la fiche — sinon le tour reste
    sans effet et sans rotation (le joueur croit avoir agi, attend le
    timeout, puis subit le tour du monstre ; partie 5f3e31c9, msg 20).
    Renvoie la note mécanique, ou '' si le PJ n'a aucun objet de soin."""
    # 🩹 Porte « soin déclaré » (partie 120e9243, points 1 et 7) : ce
    # rattrapage n'existe que pour un soin DÉCLARÉ PAR LE JOUEUR que le
    # LLM a narré sans montant ni tool. Sans déclaration, on ne soigne
    # PAS — avant, chaque tour d'exploration sans tool de soin d'un PJ
    # blessé portant un kit pompait 1 charge (+1d4 PV) : déplacements
    # (« Je sort de la fosse », « Je vais a l'est ») et même le pansement
    # narré sur le CADAVRE de Zendar ont vidé le kit de 10 à 0 charges
    # (dont ~2 légitimes). La prose du MJ seule ne suffit pas : elle peut
    # décrire un soin sur un PNJ/monstre, pas sur le PJ actif.
    if not (texte_joueur and _ACTION_SOIN_RE.search(texte_joueur or "")):
        return ""
    from .tools.fiches import _chemin  # pylint: disable=import-outside-toplevel

    path = _chemin(ctx, actif_avant)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            fiche = json.load(f)
    except Exception:                                            # noqa: BLE001
        return ""
    # 🛡️ Garde anti-gaspillage (partie 120e9243) : PJ DÉJÀ à PV max → un
    # soin par kit ne peut rien récupérer. On NE lance même PAS le 1d4 et on
    # NE touche AUCUNE charge, sinon une charge est brûlée pour un soin
    # plafonné à 0 (charge 10→9→8 en 2 parties alors que PV restaient 18/18).
    try:
        _pv_k, _pm_k = int(fiche.get("pv") or 0), int(fiche.get("pv_max") or 0)
    except (TypeError, ValueError):
        _pv_k = _pm_k = 0
    if _pm_k and _pv_k >= _pm_k:
        return ""
    objets = list(fiche.get("equipement") or []) + list(
        fiche.get("inventaire") or [])
    a_kit = any(
        _RE_KIT_SOIN.search(
            str(o.get("nom") or "") if isinstance(o, dict) else str(o))
        for o in objets
    )
    if not a_kit:
        return ""
    tr_des = await orch.execute_tool_direct(
        "lancer_des",
        {
            "nb_des": 1, "faces": 4, "bonus": 0,
            "raison": f"soin kit premiers secours ({actif_avant})",
        },
        ctx, on_event, result,
    )
    if tr_des is None:
        return ""
    m_des = _re_mod.search(r"Total jets\s*:\s*(\d+)", tr_des.text or "")
    if not m_des:
        return ""
    soin = int(m_des.group(1))
    if soin <= 0:
        return ""
    tr = await orch.execute_tool_direct(
        "fiche_perso_soigner",
        {"nom": actif_avant, "soin": soin},
        ctx, on_event, result,
    )
    if tr is None or tr.text.startswith("❌"):
        return ""
    return (
        "ℹ️ **Soin résolu par le serveur** : kit de premiers secours "
        f"utilisé sans jet — **{soin} PV** récupérés. {tr.text.strip()}"
    )


# 🛡️ B7 (audit parties complètes) : détection d'un objet ramassé/acheté/offert
# dans la narration. Sur ~90 tours et deux campagnes, AUCUN objet n'est jamais
# entré dans un inventaire alors que le texte en annonçait une douzaine
# (« vous ramassez la fiole », « vous prenez la baguette et l'ajoutez à votre
# inventaire », « Korr range la carte et l'or »).
_RE_OBJET_NARRE = _re_mod.compile(
    r"(?:vous|tu|il|elle|le\s+personnage|le\s+groupe)\s+"
    r"(?:ramass\w*|ramasse[rz]?|ramassez|pren\w*|saisis\w*|ramassent|"
    r"récup\w*|récupèrent|ramasse|empoche\w*|glisse\w*|range\w*)"
    r"\s+(?:précieuse\w*\s+|soigneusement\s+|avec\s+précaution\s+)?"
    r"(?:la|le|les|un|une|des|l')\s*"
    # m3 : la casse n'est PAS un signal — la narration écrit le plus souvent
    # « une dague dorée » en minuscule. Le filtrage passe par `_garde`.
    r"([A-Za-zÀ-ÿ][\wà-ÿ'’\- ]{2,40}?)"
    r"\s*(?:[.,;!?]|\set\s+l'ajoute|\s+et\s+le\s+(?:range|met)|$)",
    _re_mod.IGNORECASE,
)

# Formulations explicites d'ajout à l'inventaire (le modèle les écrit souvent
# sans appeler le tool) — plus fiable que la détection générique.
_RE_AJOUT_INVENTAIRE = _re_mod.compile(
    r"ajout\w*\s+(?:à|a|\u00e0)\s+(?:votre|son|leur)\s+inventaire"
    r"|\b(?:ent\u00e9e|inscrite?)\s+dans\s+l'inventaire"
    r"|range\w*\s+dans\s+(?:votre|son)\s+sac",
    _re_mod.IGNORECASE,
)

_MOTS_OBJET_EXCLUS = frozenset({
    "arme", "armes", "flèche", "flèches", "souffle", "regarde", "regard",
    "note", "notes", "décision", "connaissance", "force", "main", "mains",
    "parole", "choix", "distance", "tête", "assurance", "poignet",
    # 🛡️ m3 (audit parties complètes) : un adjectif ou un mot de jeu faisait
    # apparaître un « objet » fantôme dans le rattrapage de butin. NB : cette
    # liste ne porte que sur le PREMIER et le DERNIER mot du candidat (voir
    # `_garde`), jamais sur les mots intermédiaires — sinon « dague dorée »
    # n'était plus détectée et le butin ne rentrait plus en inventaire.
    "précieuse", "précieux", "précieuses", "brillante", "brillant",
    "surnaturelle", "surnaturel", "douce", "doux", "froide", "froid",
    "légère", "léger", "magique", "magiques", "rouillée", "rouillé",
    "sacrée", "sacré", "ancienne", "ancien", "menaçante", "menaçant",
    "dorée", "doré", "dorées", "dorés", "ternie", "terni",
    "silencieuse", "silencieux", "invisible", "hâte", "haste",
    "compass", "boussole", "attention", "garde", "garde-robe",
    "porte", "portes", "salle", "salles", "couloir", "tunnel", "passage",
    "poussière", "odeur", "ombre", "ombres", "visage", "yeux",
    "sang", "santé", "chance", "niveau", "niveaux",
})


def _objets_narres(narration: str) -> list[str]:
    """Objets que la narration annonce ramasser/ajouter (B7), dédupliqués."""
    if not narration:
        return []
    sortis: list[str] = []
    vus: set[str] = set()

    _ARTICLES_FRAG = _re_mod.compile(
        r"^(?:d|l|e|un|une|la|le|les|des|du|de\s+la|de\s+le)\s+(?=[a-zà-ÿ])",
        _re_mod.IGNORECASE,
    )

    def _garde(candidat: str) -> None:
        cand = re.sub(r"\s+", " ", candidat).strip(" .;:!,")
        # m3 : un fragment d'article résiduel (« e fiole de poison ») est
        # retiré, ainsi qu'un doublon d'article en tête.
        cand = _ARTICLES_FRAG.sub("", cand)
        cand = _ARTICLES_FRAG.sub("", cand)
        if not (2 <= len(cand) <= 40):
            return
        base = _re_mod.sub(r"[àâäéèêëîïôöùûüç]", "a", cand.lower())
        mots = [m for m in re.split(r"[\s'’\-]", base) if m]
        if not mots:
            return
        if mots[0] in _MOTS_OBJET_EXCLUS or mots[-1] in _MOTS_OBJET_EXCLUS:
            return
        cle = base
        if cle in vus:
            return
        vus.add(cle)
        sortis.append(cand)

    for m in _RE_AJOUT_INVENTAIRE.finditer(narration):
        fenetre = narration[max(0, m.start() - 260):m.start()]
        for m2 in re.finditer(
                r"(?:la|le|les|un|une|des|l')\s*"
                r"([A-Za-zÀ-ÿ][\wà-ÿ'’\- ]{2,40}?)(?=[,.]|\s+et\b|$)",
                fenetre):
            _garde(m2.group(1))
    for m in _RE_OBJET_NARRE.finditer(narration):
        _garde(m.group(1))
    return sortis[:3]


async def _appliquer_objets_narres(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Any,
    result: Any,
    nom_pj: str,
    narration: str,
) -> str:
    """Ajoute réellement à l'inventaire les objets que la narration annonce
    ramasser (B7). Sans cet outil, l'état restait `inventaire: []` pendant
    toute une campagne : la promesse de butin était un mensonge."""
    if not nom_pj or not narration:
        return ""
    objets = _objets_narres(narration)
    if not objets:
        return ""
    # 🛡️ Anti-doublon : le MJ avait déjà appelé `inventaire_ajouter` pour une
    # partie des objets (la « dague dorée » est arrivée en qte 2 en partie
    # réelle). On ne ramasse que ce qui manque.
    try:
        _st_inv = PartyState(
            data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=ctx.partie_id,
        ).load()
        _deja = {
            str(e.get("nom") or "").strip().casefold()
            for e in ((
                next((p for p in (_st_inv.get("pj") or [])
                      if str(p.get("nom") or "") == nom_pj), {})
            ) or {}).get("equipement") or []
        }
    except Exception:                                            # noqa: BLE001
        _deja = set()
    # 🛡️ m21 (audit) : le même objet peut être enregistré sous deux noms
    # (« gemme de la Couronne de Mystra » + « gemme »). La comparaison par mots
    # significatifs (≥ 4 lettres) remplace l'égalité exacte.
    def _mots_sig(nom: str) -> frozenset:
        return frozenset(
            w for w in _re_mod.split(r"[^a-zà-ÿ]+", nom.casefold())
            if len(w) >= 4
        )

    _deja_sets = [_mots_sig(d) for d in _deja if _mots_sig(d)]

    def _deja_present(o: str) -> bool:
        o_set = _mots_sig(o)
        if not o_set:
            return False
        if o.casefold() in _deja:
            return True
        for d_set in _deja_sets:
            if o_set & d_set:
                return True
        return False

    objets = [o for o in objets if not _deja_present(o)]
    if not objets:
        return ""
    lignes: list[str] = []
    ajoutes: list[str] = []
    for objet in objets:
        tr = await orch.execute_tool_direct(
            "inventaire_ajouter",
            {"nom": nom_pj, "objet": objet, "quantite": 1},
            ctx, on_event, result,
        )
        if tr is not None and not tr.text.startswith(("❌", "⛔")):
            lignes.append(tr.text.split("\n")[0])
            ajoutes.append(objet)
    if not ajoutes:
        return ""
    return (
        "🎒 **Objets ajoutés à l'inventaire de " + nom_pj + "** : "
        + ", ".join(ajoutes) + ".\n" + "\n".join(lignes)
    )


# 🛡️ B18 (audit parties complètes) : le modèle imite la sortie de
# `tour_suivant_combat` et déclare « **Phase : Exploration** » alors que l'état
# est en combat (et l'inverse). La déclaration de phase dans la PROSE est donc
# retirée systématiquement : elle est remplacée par la ligne officielle du
# serveur, calculée sur l'état réel.
_RE_PHASE_PROSE = _re_mod.compile(
    r"^\s*\**\s*Phase\s*:\s*(Combat|Exploration|exploration|combat)"
    r"\**[^.\n]{0,90}\.?\s*$",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)
_RE_BANDEAU_TOUR = _re_mod.compile(
    r"^\s*\**\s*(?:Initiative\s+\d+\**\s*[—\-–]\s*)?"
    r"C'?est au tour de\s+\*\*[^*\n]{1,40}\*\*\s*\.?\s*$",
    _re_mod.IGNORECASE | _re_mod.MULTILINE,
)

# 🛡️ B23/B39 : le modèle annonce la mort d'une créature encore debout (et
# l'inverse), avec des PV différents à quatre endroits du même message. On
# détecte les formulations de mort pour comparer à l'état réel.
_RE_MORT_PROSE = _re_mod.compile(
    r"\b(s'?effondre\w*|tombe\w*\s+sans\s+vie|n'?est\s+plus|"
    r"s'?\u00e9croule\w*|hors\s+de\s+combat|expir\w*|meurt|"
    r"dernier\s+souffle|s'\u00e9croule|d\u00e9truit\w*|termin\u00e9[es]?)\b",
    _re_mod.IGNORECASE,
)
_RE_PV_PROSE = _re_mod.compile(
    r"\b(\d{1,3})\s*(?:/|sur)\s*(\d{1,3})\s*(?:PV|points?\s+de\s+vie)",
    _re_mod.IGNORECASE,
)


# 🛡️ m4 (audit parties complètes) : les tools de carte renvoyaient
# `state_patch={"donjon": <tout le donjon>}` — grille, descriptions, notes et
# images comprises — et ce dictionnaire partait TEL QUEL vers chaque client à
# chaque tour (plusieurs dizaines de ko × le nombre d'onglets). Le client
# re-télécharge de toute façon `/carte-donjon.svg` : on ne lui envoie que les
# champs légers.
_CHAMPS_DONJON_CLIENT = (
    "id", "nom", "etage", "courant", "arrivee_par", "nb_etages",
    "salles_visitees", "portes_bloquees", "carte_donjon",
)


def _alleger_patches_client(patches: Any) -> list:
    if not isinstance(patches, list):
        return []
    sortie: list = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        q: dict = {}
        for cle, val in p.items():
            if cle == "donjon" and isinstance(val, dict):
                dj = {k: val.get(k) for k in _CHAMPS_DONJON_CLIENT if k in val}
                dj["nb_salles"] = len(val.get("grille") or [])
                q[cle] = dj
                continue
            if cle == "grille":
                q["nb_salles"] = len(val or [])
                continue
            q[cle] = val
        sortie.append(q)
    return sortie


def _dedupliquer_narrations_intermediaires(blocs: list) -> list:
    """Supprime les blocs intermédiaires quasi identiques entre eux (B15/m2).

    Le modèle répète volontiers le même paragraphe d'une itération à l'autre ;
    chaque répétition était diffusée au joueur en direct."""
    if not blocs or len(blocs) < 2:
        return blocs
    sortie: list = []
    gardes: list[str] = []
    for b in blocs:
        txt = b if isinstance(b, str) else str(getattr(b, "text", b))
        aplati = " ".join(str(txt).split())
        if len(aplati) >= 60 and any(
                _jaccard_mots(aplati, g) >= _SEUIL_SIMILARITE_PARAGRAPHE
                for g in gardes):
            continue
        if len(aplati) >= 60:
            gardes.append(aplati)
        sortie.append(b)
    return sortie


def _aligner_verite_terrain(narration: str, etat: dict) -> str:
    """Aligne la narration sur l'état mécanique (B18, B23, B39).

    1. retire les déclarations de phase et les bandeaux de tour inventés ;
    2. ajoute la ligne de phase/tour OFFICIELLE ;
    3. signale les créatures que la prose déclarait mortes alors qu'elles
       sont encore debout (et réciproquement)."""
    if not narration:
        return narration
    texte = _RE_BANDEAU_TOUR.sub("", narration)
    texte = _RE_PHASE_PROSE.sub("", texte)

    phase = str(etat.get("phase") or "")
    if phase == "combat":
        courant = str(etat.get("courant_tour_pour") or "")
        tour = etat.get("tour", 1) or 1
        pj = next(
            (p for p in (etat.get("pj") or [])
             if str(p.get("nom") or "") == courant), None)
        qui = courant
        if pj is not None and pj.get("joueur"):
            qui = f"{courant} (joueur {pj['joueur']})"
        vivants = [
            str(m.get("nom") or "")
            for m in (etat.get("monstres_combat") or [])
            if "Détruit" not in (m.get("conditions") or [])
            and int(m.get("pv", 1) or 0) > 0
        ]
        # 🛡️ B28 : le MJ déformait les classes (« le guerrier » pour le
        # clerc, « le barde » pour le guerrier). La liste officielle est
        # réaffichée à chaque tour de combat.
        _fiches_classe = [
            f"{p.get('nom')} = {p.get('race')} {p.get('classe')} niv "
            f"{p.get('niveau')}"
            for p in (etat.get("pj") or []) if p.get("nom")
        ]
        texte = texte.rstrip() + (
            f"\n\n⚔️ **Phase : Combat** — round {tour}"
            + (f" — au tour de **{qui}**." if courant else ".")
        )
        if vivants:
            texte += "\n🎯 Ennemis encore debout : " + ", ".join(vivants) + "."
        if _fiches_classe:
            texte += ("\n👥 Rappel des personnages : "
                      + " ; ".join(_fiches_classe) + ".")

    # 🛡️ B16 hors combat : la prose fait mourir une créature qui n'est PAS
    # engagée (aucun monstre en état). Observé : « J'attaque le Gobelin » →
    # « Vous avez vaincu l'ennemi » alors que l'état ne compte aucun monstre.
    if phase != "combat" and not (etat.get("monstres_combat") or []):
        _faux_morts: list[str] = []
        for _mot in ("Gobelin", "Gobelin ", "Orc", "Kobold", "Squelette",
                     "Zombie", "Goule", "Araignée", "Assassin", "Bandit",
                     "Loup", "Ogre", "Troll", "Gnoll", "Nécromancien"):
            if _mot.strip() and _mot.strip() in texte:
                for _mm in _re_mod.finditer(_re_mod.escape(_mot.strip()),
                                            texte):
                    _fen = texte[max(0, _mm.start() - 80):_mm.end() + 80]
                    if _RE_MORT_PROSE.search(_fen):
                        _faux_morts.append(_mot.strip())
                        break
        if _faux_morts:
            texte = texte.rstrip() + (
                "\n\nℹ️ _Précision de l'état du jeu : aucun combat n'est "
                "engagé et aucune créature n'est inscrite à l'état — les "
                "créatures mentionnées ("
                + ", ".join(dict.fromkeys(_faux_morts))
                + ") ne sont pas réelles. Utilise `engager_combat` si la "
                "rencontre doit avoir lieu._"
            )

    # B23 : morts annoncées à tort.
    morts_prose: list[str] = []
    for m in etat.get("monstres_combat") or []:
        nom = str(m.get("nom") or "").strip()
        if not nom:
            continue
        pv = int(m.get("pv", 1) or 0)
        if pv > 0 and "Détruit" not in (m.get("conditions") or []):
            fenetre = ""
            for mm in _re_mod.finditer(_re_mod.escape(nom), texte,
                                       _re_mod.IGNORECASE):
                fenetre = texte[max(0, mm.start() - 90):mm.end() + 90]
                if _RE_MORT_PROSE.search(fenetre):
                    morts_prose.append(nom)
                    break
    if morts_prose:
        _par_nom = {
            str(m.get("nom") or ""): int(m.get("pv", 0) or 0)
            for m in (etat.get("monstres_combat") or [])
        }
        detail = "; ".join(
            f"{n} est encore debout ({_par_nom.get(n, 0)} PV)"
            for n in dict.fromkeys(morts_prose))
        texte = texte.rstrip() + (
            "\n\nℹ️ _Précision de l'état du jeu : " + detail + "._"
        )
    return _re_mod.sub(r"\n{3,}", "\n\n", texte).strip()


def _noms_armures_catalogue() -> set[str]:
    """Noms d'armures/boucliers du catalogue (pour recalculer la CA)."""
    try:
        from . import catalogue as _cat
        return {a["nom"] for a in _cat.ARMURES}
    except Exception:                                            # noqa: BLE001
        return set()


async def _ressusciter_pj_oublie(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Any,
    result: Any,
) -> str:
    """Résout la RÉSURRECTION d'un PJ mort que le MJ a narrée SANS tool.

    Après un GAME OVER, le MJ propose « résurrection négociée », la table
    accepte, le MJ narre la scène (repos, PV récupérés, « à 16 PV sur 17 »)
    — mais AUCUN tool existant ne peut lever la condition « Mort » :
    `repos_long` ignore les PV négatifs et `fiche_perso_soigner` n'ôte pas
    « Mort ». L'état restait Mort/-10 alors que la narration décrivait un
    personnage debout (partie 5f3e31c9, msgs 21-24).

    Ici : conditions mortelles levées (Mort/Mourant/Stabilisé/Inconscient),
    PV portés au montant narré (défaut 1), pénalité officielle de Raise
    Dead appliquée (niveau > 1 : −1 niveau ; niveau 1 : −2 CON, perte
    irréparable, PV max réduits si le mod. CON baisse) — SAUF pour une
    Résurrection Vraie (Clr 9, l'unique variante sans perte), flag
    `game_over` effacé. La narration du prix négocié reste à la charge du
    MJ. Renvoie la note mécanique, ou '' si rien n'est à faire.
    """
    import unicodedata as _uni

    def _nn(s: Any) -> str:
        n = _uni.normalize("NFKD", str(s or "").strip().lower())
        return "".join(c for c in n if not _uni.combining(c))

    narration = result.narration or ""

    def _est_mort(p: dict) -> bool:
        conds = {_nn(c) for c in (p.get("conditions") or [])}
        if "mort" in conds:
            return True
        try:
            return int(p.get("pv", 0) or 0) <= -10
        except (TypeError, ValueError):
            return False

    try:
        st = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id)
        etat = st.load()
    except Exception:                                            # noqa: BLE001
        return ""
    morts = [p for p in (etat.get("pj") or []) if _est_mort(p)]
    if not morts:
        return ""
    # Gate : la narration doit AFFIRMER une restauration (montant de PV
    # récupérés / repos long), ou user du vocabulaire de résurrection SANS
    # être une simple offre de choix (« je ne peux pas… choisissez »).
    revendique = bool(_RE_PV_RECUPERES.search(narration)) or (
        bool(_RESURRECTION_RE.search(narration))
        and not _RE_OFFRE_RESURRECTION.search(narration)
    )
    # 🛡️ B36 bis : un GAME OVER annoncé (ou un personnage encore Mourant non
    # stabilisé) n'autorise jamais la résurrection automatique.
    if _RE_GAME_OVER.search(narration):
        _log_tour(
            "résurrection automatique refusée : la narration annonce un "
            "GAME OVER (partie %s)", ctx.partie_id,
        )
        return ""
    for _p_mort in morts:
        _conds = {str(c).strip().lower()
                  for c in (_p_mort.get("conditions") or [])}
        if "mourant" in _conds or "inconscient" in _conds:
            _log_tour(
                "résurrection automatique refusée : %s est Mourant/Inconscient "
                "(partie %s)", _p_mort.get("nom"), ctx.partie_id,
            )
            return ""
    if not revendique:
        return ""

    # Montant narré (« maintenant à **16 PV** sur vos 17 ») : honoré pour
    # TOUS les PJ morts (B33 : la valeur n'était appliquée qu'à un seul mort,
    # toute défaite de groupe revenait donc à 1 PV, condamnée à re-mourir).
    # Sans montant narré, un Raise Dead 3.5 rend conscient à 1 PV — c'est la
    # règle, mais on l'annonce explicitement (B35) et `repos_long` redevient
    # disponible pour remonter le groupe.
    cible_pv = 1
    m_c = _re_mod.search(
        r"à\s*(?:\*\*)?(\d{1,3})(?:\*\*)?\s*PV(?:\*\*)?"
        r"\s*(?:sur|/)\s*(?:vos\s+)?\d{1,3}",
        narration, _re_mod.IGNORECASE,
    )
    if m_c:
        cible_pv = max(1, int(m_c.group(1)))

    lignes: list[str] = []
    _fiche_lue: dict[str, dict[str, Any]] = {}
    from .tools.fiches import _chemin  # pylint: disable=import-outside-toplevel

    for p in morts:
        nom = str(p.get("nom") or "")
        if not nom:
            continue
        # 1) Conditions mortelles levées (outil par outil, valeur stockée).
        chemin = _chemin(ctx, nom)
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                fiche = json.load(f)
        except Exception:                                    # noqa: BLE001
            fiche = dict(p)
        conds_fiche = [
            str(c) for c in (fiche.get("conditions") or [])
            if _nn(c) in {"mort", "mourant", "stabilise", "inconscient"}
        ]
        for cond in conds_fiche:
            await orch.execute_tool_direct(
                "fiche_perso_condition",
                {"nom": nom, "condition": cond, "appliquer": False},
                ctx, on_event, result,
            )
        # 2) PV portés au montant narré (plafonné par le tool à pv_max).
        try:
            pv_actuel = int(fiche.get("pv", 0) or 0)
        except (TypeError, ValueError):
            pv_actuel = p.get("pv", 0)
        pv_cible = min(cible_pv, int(fiche.get("pv_max", 0) or cible_pv))
        if pv_cible > pv_actuel:
            tr = await orch.execute_tool_direct(
                "fiche_perso_soigner",
                {"nom": nom, "soin": pv_cible - pv_actuel, "interne": True},
                ctx, on_event, result,
            )
            if tr is not None:
                lignes.append(tr.text)
        lignes.append(
            f"- {nom} : conditions mortelles levées "
            f"({', '.join(conds_fiche) or 'aucune'})."
        )
        _fiche_lue.setdefault(nom, {})

        # 3) Pénalité de résurrection (Raise Dead / Résurrection, DMG 3.5) :
        #    le sujet perd un niveau — ou, s'il est de niveau 1, 2 points de
        #    Constitution à la place (perte IRRÉPARABLE par aucun moyen). Si
        #    le mod. CON baisse, les PV max diminuent d'autant (× niveau).
        #    EXCEPTION : Résurrection VRAIE (Clr 9) — aucune perte.
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                fiche = json.load(f)
        except Exception:                                    # noqa: BLE001
            pass
        if _RE_RESURRECTION_VRAIE.search(narration):
            lignes.append(
                f"- {nom} : Résurrection Vraie (niv 9) — aucune perte de "
                "niveau ni de CON (règles 3.5)."
            )
            continue
        try:
            niveau_r = int(fiche.get("niveau", 1) or 1)
        except (TypeError, ValueError):
            niveau_r = 1
        if niveau_r > 1:
            tr_np = await orch.execute_tool_direct(
                "fiche_perso_perte_niveau",
                {"nom": nom, "nb": 1},
                ctx, on_event, result,
            )
            if tr_np is not None:
                lignes.append(tr_np.text)
        else:
            try:
                con = int((fiche.get("carac") or {}).get("CON", 10) or 10)
            except (TypeError, ValueError):
                con = 10
            # 🛡️ B37 : plancher à 6. CON < 6 tue le personnage en 3.5
            # (coma puis mort) ; appliquer la pénalité sans plancher et sans
            # avertissement produisait une spirale de mort (CON 15 → 13 → 11
            # observés en deux défaites).
            nouvelle_con = max(6, con - 2)
            perte_pvmax = max(0, (con - 10) // 2 - (nouvelle_con - 10) // 2)
            await orch.execute_tool_direct(
                "fiche_perso_mettre_a_jour",
                {"nom": nom, "champ": "carac.CON",
                 "valeur": str(nouvelle_con)},
                ctx, on_event, result,
            )
            if perte_pvmax > 0:
                try:
                    pv_max_new = max(
                        1, int(fiche.get("pv_max", 1) or 1) - perte_pvmax)
                except (TypeError, ValueError):
                    pv_max_new = 1
                await orch.execute_tool_direct(
                    "fiche_perso_mettre_a_jour",
                    {"nom": nom, "champ": "pv_max",
                     "valeur": str(pv_max_new), "interne": True},
                    ctx, on_event, result,
                )
                try:
                    if int(fiche.get("pv", 0) or 0) > pv_max_new:
                        await orch.execute_tool_direct(
                            "fiche_perso_mettre_a_jour",
                            {"nom": nom, "champ": "pv",
                             "valeur": str(pv_max_new)},
                            ctx, on_event, result,
                        )
                except (TypeError, ValueError):
                    pass
            # 🛡️ B33 d.4 : la CON change → TOUTES les valeurs dérivées
            # changent (sauvegardes de Vigueur, initiative, charge max, PV max
            # au niveau suivant). Sans recalcul, la fiche gardait des
            # sauvegardes calculées sur l'ancienne CON.
            try:
                _armures = [
                    str(e.get("nom")) for e in (fiche.get("equipement") or [])
                    if str(e.get("nom")) in _noms_armures_catalogue()
                ]
                _calc = persos_mod.calculer_derivees(
                    fiche.get("carac") or {}, fiche.get("race") or "",
                    fiche.get("classe") or "",
                    int(fiche.get("niveau", 1) or 1), armures=_armures,
                    dons=fiche.get("dons"),
                )
                for _cle, _val in (("sauvegardes", _calc.get("sauvegardes")),
                                   ("ca", _calc.get("ca")),
                                   ("initiative", _calc.get("initiative")),
                                   ("charge_max", _calc.get("charge_max")),
                                   ("bab", _calc.get("bab"))):
                    if _val is not None:
                        fiche[_cle] = _val
                _pv_max_recalc = int(_calc.get("pv_max") or 0)
                if _pv_max_recalc > 0:
                    _avant = int(fiche.get("pv_max", 0) or 0)
                    if _avant and _pv_max_recalc != _avant:
                        fiche["pv_max"] = _pv_max_recalc
                        if int(fiche.get("pv", 0) or 0) > _pv_max_recalc:
                            fiche["pv"] = _pv_max_recalc
                await orch.execute_tool_direct(
                    "fiche_perso_mettre_a_jour",
                    {"nom": nom, "champ": "carac",
                     "valeur": json.dumps(
                         fiche.get("carac") or {}, ensure_ascii=False),
                     "interne": True},
                    ctx, on_event, result,
                )
                await orch.execute_tool_direct(
                    "fiche_perso_mettre_a_jour",
                    {"nom": nom, "champ": "sauvegardes",
                     "valeur": json.dumps(
                         fiche.get("sauvegardes") or {}, ensure_ascii=False),
                     "interne": True},
                    ctx, on_event, result,
                )
                _log_tour(
                    "résurrection : dérivés recalculés pour %s "
                    "(sauvegardes %s)", nom,
                    json.dumps(fiche.get("sauvegardes") or {},
                               ensure_ascii=False),
                )
            except Exception as _e_calc:                         # noqa: BLE001
                _log_tour("résurrection : recalcul dérivés échoué (%s)",
                          _e_calc)
            lignes.append(
                f"- {nom} : pénalité de résurrection (Raise Dead, DMG 3.5, "
                f"niveau 1) — −2 CON ({con}→{nouvelle_con}, "
                "perte de Constitution permanente)"
                + (f", PV max −{perte_pvmax}" if perte_pvmax else "")
                + ". Vos sauvegardes de Vigueur baissent d'autant."
            )

    for _p_sync in morts:
        try:
            with open(_chemin(ctx, str(_p_sync.get("nom") or "")),
                      "r", encoding="utf-8") as _f_sync:
                _fiche_lue[str(_p_sync.get("nom") or "")] = json.load(_f_sync)
        except Exception:                                    # noqa: BLE001
            pass

    # 4) 🛡️ B33 d.4 + B35 : synchronise l'instantané `etat["pj"]` avec la
    # fiche. Jusqu'ici la pénalité de CON n'était écrite que dans
    # `fiche_*.json` : l'état de partie (utilisé par le moteur de combat ET
    # par le récapitulatif envoyé au MJ) conservait l'ancienne CON, si bien
    # que les sauvegardes ne suivaient pas et que le MJ décrivait des
    # personnages qui n'existaient plus.
    try:
        etat2 = st.load()
        _maj = []
        for _pj in etat2.get("pj") or []:
            _nom = str(_pj.get("nom") or "")
            _f2 = _fiche_lue.get(_nom)
            if not _f2:
                continue
            for _champ in ("carac", "pv", "pv_max", "ca", "sauvegardes",
                           "conditions"):
                if _f2.get(_champ) is not None:
                    _pj[_champ] = _f2[_champ]
            _maj.append(_nom)
        if etat2.get("game_over") or etat2.get("phase") == "game_over":
            etat2["game_over"] = False
            etat2["phase"] = "exploration"
        etat2["pj"] = [
            p for p in (etat2.get("pj") or [])
        ]
        st.save(etat2)
        result.state_patches.append({
            "game_over": False, "phase": "exploration",
        })
        if _maj:
            _log_tour(
                "résurrection : instantané pj resynchronisé (%s) — partie %s",
                ", ".join(_maj), ctx.partie_id,
            )
    except Exception as _e_res:                                  # noqa: BLE001
        _log_tour("résurrection : synchro pj échouée (%s)", _e_res)

    return (
        "\n\n✨ **Résurrection appliquée par le serveur**\n\n"
        "La narration décrivait un retour à la vie alors que l'état "
        "indiquait encore "
        + ("un personnage mort" if len(morts) == 1 else
           f"{len(morts)} personnages morts")
        + " (aucun tool de résurrection n'existe). Concrètement, et "
        "**lisez bien ceci** :\n\n"
        + "\n".join(lignes)
        + "\n\n⚠️ Les personnages reviennent **conscients mais à "
        f"{cible_pv} PV**. Utilisez un soin ou un repos long avant de "
        "repartir au combat — vous êtes à un coup de la mort."
    )


async def _appliquer_degats_pj_narres(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Any,
    result: Any,
    actif_avant: str = "",
) -> str:
    """Rattrapage HORS COMBAT : dégâts annoncés en prose contre un PJ
    (« vous subissez 12 dégâts de foudre ») sans aucun
    `fiche_perso_infliger_degats` dans la trace. En combat, les filets de
    `_appliquer_degats_oublies` couvrent déjà le cas ; en exploration, un
    monstre narré blessait le personnage sans toucher l'état (partie
    5f3e31c9, msg 30 : « 12 dégâts de foudre » restés sans effet).
    Renvoie la note mécanique, ou '' si rien n'est à faire."""
    if any(
        tc.get("name") == "fiche_perso_infliger_degats" and tc.get("ok")
        for tc in result.tool_calls_trace
    ):
        return ""
    try:
        etat = PartyState(
            data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
        ).load()
    except Exception:                                            # noqa: BLE001
        return ""
    if etat.get("phase") == "combat":
        # En combat, les filets dédiés (orphelins lancer_degats + « touché
        # sans dégâts ») s'appliquent — ne pas créer de double application.
        return ""
    narration = result.narration or ""
    m = _RE_DEGATS_SUBIS_PJ.search(narration)
    if not m:
        return ""
    try:
        total = int(m.group(1))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    # Victime : le PJ nommé dans la fenêtre de l'annonce, sinon le PJ actif
    # (la prose « vous subissez… » s'adresse à lui) ou l'unique PJ.
    pjs = [str(p.get("nom") or "") for p in (etat.get("pj") or [])
           if p.get("nom")]
    if not pjs:
        return ""
    fenetre = narration[max(0, m.start() - 120):m.end()].lower()
    victime = next((n for n in pjs if n.lower() in fenetre), "")
    if not victime:
        if len(pjs) == 1:
            victime = pjs[0]
        elif actif_avant and actif_avant in pjs:
            victime = actif_avant
        else:
            return ""
    tr = await orch.execute_tool_direct(
        "fiche_perso_infliger_degats",
        {"nom": victime, "degats": total},
        ctx, on_event, result,
    )
    if tr is None or tr.text.startswith("❌"):
        return ""
    return (
        "ℹ️ **Dégâts appliqués par le serveur** : la narration annonçait "
        f"« {m.group(0).strip()[:80]} » sans jet enregistré — {tr.text}"
    )


def _pj_attaquant(
    personnage: str, actif_avant: str, etat: dict[str, Any],
) -> str:
    """🛡️ B6/B19 (audit parties complètes) : nom du PJ qui doit résoudre
    l'attaque déclarée.

    Le rattrapage utilisait `actif_avant` seul : quand la garde de tour avait
    rétabli le bon PJ (ou quand deux PJ du même compte jouaient en deux
    onglets), l'attaque était résolue pour l'AUTRE personnage — avec son arme,
    son bonus et ses PV. Le personnage incarné par la connexion prime donc,
    à condition d'exister dans la partie."""
    pjs = [str(p.get("nom") or "") for p in (etat.get("pj") or [])]
    if personnage and personnage in pjs:
        return personnage
    if actif_avant and actif_avant in pjs:
        return actif_avant
    return personnage or actif_avant or ""


async def _attaque_pj_sans_jet(
    orch: Orchestrator,
    ctx: ToolContext,
    on_event: Any,
    result: Any,
    nom_pj: str,
) -> str:
    """Résout déterministiquement l'attaque d'un PJ déclarée sans aucun jet.

    Le petit modèle (Gemma 9B) narre l'attaque en prose et n'appelle jamais
    `lancer_attaque`/`lancer_degats` — le rejeu correctif 5bis-a échoue aussi,
    et « l'avancement forcé » laisse l'état inchangé (bug vécu 5f3e31c9 : le
    loup-garou restait à 28/32 alors que la narration annonçait un coup).
    On applique alors la même logique que le moteur pour les monstres :
    fiche du PJ (BBA + mod FOR/DEX), arme du catalogue, première cible
    ennemie vivante, jet d'attaque puis dégâts via execute_tool_direct
    (l'auto-application inflige les PV du monstre suivi). Renvoie la note
    mécanique, ou '' si rien n'a pu être joué.
    """
    from .tools.fiches import _chemin  # pylint: disable=import-outside-toplevel

    try:
        etat = PartyState(
            data_dir=str(ctx.data_dir), partie_id=ctx.partie_id,
        ).load()
    except Exception:                                            # noqa: BLE001
        return ""

    # 1) Cible ennemie vivante — première debout suivie.
    cible = None
    ca_cible = 10
    for mo in etat.get("monstres_combat") or []:
        if mo.get("allie"):
            continue
        try:
            pv = int(mo.get("pv", 0) or 0)
        except (TypeError, ValueError):
            pv = 0
        conds = mo.get("conditions") or []
        if pv <= 0 or "Détruit" in conds or "Detruit" in conds:
            continue
        cible = str(mo.get("nom") or "")
        try:
            ca_cible = int(mo.get("ca") or 10)
        except (TypeError, ValueError):
            ca_cible = 10
        break
    if not cible:
        return ""

    # 2) Lecture de la fiche du PJ.
    path = _chemin(ctx, nom_pj)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        fiche = json.load(f)
    caracs = fiche.get("carac") or {}
    bab = int(fiche.get("bab") or 0)

    # 3) Arme portée (1er objet d'équipement présent au catalogue ARMES).
    arme_nom = ""
    arme_cat = None
    for obj in fiche.get("equipement") or []:
        nom_item = (
            obj.get("nom") if isinstance(obj, dict) else str(obj)
        )
        for a in catalogue_mod.ARMES:
            if (
                str(a.get("nom") or "").strip().lower()
                == str(nom_item).strip().lower()
            ):
                arme_nom = nom_item
                arme_cat = a
                break
        if arme_cat is not None:
            break
    if arme_cat is None:
        return ""

    # 4) Bonus d'attaque = BBA + mod FOR (mêlée) / DEX (distance).
    distance = bool(arme_cat.get("distance"))
    cle = "DEX" if distance else "FOR"
    mod_car = (int(caracs.get(cle, 10) or 10) - 10) // 2
    bonus_atk = bab + mod_car

    # 5) Dégâts du catalogue (ex. « 1d12 »).
    md = _re_mod.search(r"(\d+)d(\d+)", str(arme_cat.get("degats") or "1d6"))
    nb_des, faces = (int(md.group(1)), int(md.group(2))) if md else (1, 6)
    bonus_dmg = mod_car if not distance else 0
    if not distance and "deux mains" in str(arme_nom).lower():
        bonus_dmg = int(mod_car * 1.5)

    # 6) Jet d'attaque — la CA officielle du bestiaire prime toujours.
    tr_atk = await orch.execute_tool_direct(
        "lancer_attaque",
        {
            "bonus_attaque": bonus_atk,
            "ca_cible": ca_cible,
            "nom_attaquant": nom_pj,
            "arme": arme_nom,
            "nom_cible": cible,
        },
        ctx, on_event, result,
    )
    if tr_atk is None or tr_atk.text.startswith("❌"):
        return ""
    note = tr_atk.text
    if (
        "✅ **Touché**" in tr_atk.text
        or "⭐ **20 naturel**" in tr_atk.text
    ):
        tr_dm = await orch.execute_tool_direct(
            "lancer_degats",
            {
                "nb_des": nb_des,
                "faces": faces,
                "bonus": bonus_dmg,
                "arme_ou_sort": arme_nom,
                "cible": cible,
            },
            ctx, on_event, result,
        )
        # Les dégâts sont auto-appliqués par _auto_appliquer_degats
        # et affichés dans les notes mécaniques ; on ne les ajoute pas
        # ici pour éviter un double affichage.
        if tr_dm is not None:
            note = note + "\n" + tr_dm.text
    return note


def _detecter_combat_prose(
    data_dir: str,
    text: str,
    etat_avant: dict[str, Any],
    forcer_declencheur: bool = False,
    exclure: Optional[list[str]] = None,
) -> list[str]:
    """Repère les monstres du bestiaire mentionnés dans une narration qui
    relate un combat SANS avoir appelé `engager_combat`.

    Le petit modèle narratif écrit parfois « Le combat commence ! Le zombie
    bondit… », puis enchaîne jets et dégâts dans la prose, oubliant d'appeler
    l'outil. Le serveur engage alors la phase officielle pour que l'ordre
    d'initiative, le suivi des PV et la rotation restent conformes.

    :param forcer_declencheur: ignore la porte marqueurs/dégâts (le JOUEUR a
        lui-même déclaré une attaque armée — cf. `_ATTAQUE_JOUEUR_RE` —, ce
        qui suffit à vouloir la mécanique officielle). Les marqueurs de FIN
        (victoire/fuite narrée) restent bloquants dans tous les cas.

    Renvoie la liste des noms de type de monstres détectés ([] si aucun).
    Précondition : `etat_avant.phase != "combat"` (sinon rien à rattraper).
    """
    if not text:
        return []
    # Déclencheurs : marqueur explicite de combat OU prose de dégâts (une
    # attaque a été narrée). Sans l'un des deux, on ne déclenche JAMAIS un
    # rattrapage : trop de faux positifs (monstre amical dans une taverne,
    # squelette décoratif dont on parle sans s'y battre…).
    bas = text.lower()
    if not (
        any(m in bas for m in _COMBAT_PROSE_MARKERS)
        or bool(_DEGATS_PROSE_RE.search(bas))
        or forcer_declencheur
    ):
        return []
    # Si la narration indique déjà que le combat est TERMINÉ (victoire,
    # défaite, fuite, monstre vaincu/tombé…), ne rien rattraper : l'issue a
    # déjà été racontée, un ré-engagement écraserait un combat clos.
    if any(_f in bas for _f in _COMBAT_PROSE_END_MARKERS):
        return []
    try:
        best = _load_bestiaire_plain(data_dir)
    except Exception:
        return []
    import unicodedata as _ud

    def _sans_accents(w: str) -> str:
        nf = _ud.normalize("NFKD", w)
        return "".join(c for c in nf if not _ud.combining(c))

    def _normaliser_nom(nom: str) -> str:
        return _sans_accents(str(nom).lower()).replace("_", " ")

    # Vocabulaire de la prose : MOTS ENTIERS (minuscules, accents retirés)
    # au singulier ET au pluriel. Plus de sous-chaîne ni de difflib flou :
    # « menaçante » ne doit pas faire apparaître « Ane », « signes » → « Singe »,
    # « hurle » → « Hurleur », « gobelin » → « Hobgobelin » (partie ee5684fe —
    # ces faux positifs faisaient refuser TOUT le rattrapage par engager_combat).
    # 🛡️ m20 : noms de PJ, CLASSES et RACES à exclure de la détection.
    _exclure_mots: set[str] = set()
    for _n in (exclure or []):
        for _w in _re_mod.split(r"[^a-z']+", _sans_accents(str(_n or "").lower())):
            if len(_w) >= 3:
                _exclure_mots.add(_w)
    # 🛡️ (correctif) : `from .. import persos` dépassait le package
    # `server` (persos.py est dans le MÊME package) → ImportError
    # systématique avalée nulle part : la détection de combat prose était
    # MORTE en production (les monstres surgissant dans la prose ne
    # déclenchaient plus le rattrapage, cf. tests m20/ee5684fe).
    from . import persos as _persos_m20  # pylint: disable=C0415
    try:
        for _cl in (list(_persos_m20.CAPACITES_CLASSES)
                    + list(_persos_m20.CAPACITES_RACES)):
            for _w in _re_mod.split(r"[^a-z']+",
                                    _sans_accents(str(_cl or "").lower())):
                if len(_w) >= 3:
                    _exclure_mots.add(_w)
    except Exception:                                            # noqa: BLE001
        pass
    for _race in ("humain", "elfe", "demi elfe", "demi orc", "nain", "gnome",
                  "halfelin", "halfeling"):
        _exclure_mots.add(_race)
    for _cl in ("clerc", "guerrier", "paladin", "magicien", "sorcier",
                "rodeur", "voleur", "barde", "barbare", "druide", "moine"):
        _exclure_mots.add(_cl)

    tokens_bruts: list[str] = []
    for w in _re_mod.split(r"[^a-z']+", bas):
        w = w.strip("'")
        if len(w) < 4:
            continue
        tokens_bruts.append(_sans_accents(w))
    if not tokens_bruts:
        return []
    # 🛡️ m20 : les mots de CLASSE, de RACE et de PJ sont retirés du vocabulaire
    # de la prose — « Korr (Voleur niveau 1) » ne doit pas engager un monstre
    # « Voleur ».
    _avant_m20 = len(tokens_bruts)
    tokens_bruts = [w for w in tokens_bruts if w not in _exclure_mots]
    if not tokens_bruts:
        return []
    if len(tokens_bruts) != _avant_m20:
        print("[dnd35] m20 : classes/races/PJ exclus de la détection "
              f"({_avant_m20} → {len(tokens_bruts)} mots)")
    mots_prose: set[str] = set(tokens_bruts)
    for w in tokens_bruts:
        if w.endswith("s") and len(w) > 4:
            mots_prose.add(w[:-1])

    def _mots_entiers(nl: str) -> bool:
        """True si le nom normalisé `nl` apparaît comme MOT(S) ENTIER(S) dans
        la prose (nom mono-mot dans le vocabulaire, ou suite de mots
        consécutifs tolérante au pluriel pour les noms composés)."""
        parts = nl.split()
        if not parts:
            return False
        if len(parts) == 1:
            return parts[0] in mots_prose
        k = len(parts)
        for i in range(len(tokens_bruts) - k + 1):
            if all(
                parts[j] == tokens_bruts[i + j]
                or (
                    len(tokens_bruts[i + j]) > 4
                    and tokens_bruts[i + j].endswith("s")
                    and parts[j] == tokens_bruts[i + j][:-1]
                )
                for j in range(k)
            ):
                return True
        return False

    # Index des noms du bestiaire (normalisés → nom d'affichage) : sert au
    # rapprochement exact ET au pont anglais→français. Les fiches GÉNÉRIQUES
    # ou SANS NOM (ex. « cle: monstre », nom vide) sont exclues — partie
    # a6d11005 : la prose « un monstre surgit » a engagé un placeholder FP ¼
    # sans identité comme si c'était une créature du module.
    noms_normalises: dict[str, str] = {}
    for cle, m in (best.get("monstres", {}) or {}).items():
        if not isinstance(m, dict):
            continue
        if m.get("generique"):
            continue
        nom = str(m.get("nom") or cle or "").strip()
        if not nom or len(nom) < 3:
            continue
        noms_normalises.setdefault(_normaliser_nom(nom), nom)

    trouves: list[str] = []
    for nl, nom in noms_normalises.items():
        # Garde anti-faux-positifs (partie dfccc120) : les mots génériques
        # (« ombre », « silhouette »…) désignent le décor, jamais une
        # rencontre — jamais de rattrapage sur ces mots. La prose de dégâts /
        # marqueur de combat exigée plus haut suffit comme signal ici.
        if nl in _ENNEMIS_MOTS_GENERIQUES:
            continue
        # Mots TROP génériques de la narration de combat : « un monstre
        # surgit », « une créature attaque » ne désignent AUCUNE créature
        # du bestiaire (a6d11005).
        if nl in _MOTS_COMBAT_GENERIQUES:
            continue
        if _mots_entiers(nl):
            trouves.append(nom)
    # Pont anglais→français : un mot de la prose résout vers un nom officiel
    # du bestiaire (ex. « Ghoul » → « Goule »). Uniquement pour des mots NON
    # résolus en exact ci-dessus, pour ne jamais concourir avec un nom propre.
    for w in tokens_bruts:
        if w in noms_normalises:
            continue  # déjà couvert par le rapprochement exact
        cible = _MONSTRES_EN_FR_PROSE.get(w)
        if not cible:
            continue
        c = _normaliser_nom(cible)
        if c in noms_normalises and c not in _ENNEMIS_MOTS_GENERIQUES:
            trouves.append(noms_normalises[c])
    # Déduplique par nom (plusieurs clés du bestiaire peuvent pointer vers le
    # même affichage) pour un `engager_combat(nom, nom, …)` propre.
    _dedup = {_t: 1 for _t in trouves}
    return list(_dedup.keys())


def _load_bestiaire_plain(data_dir: str) -> dict[str, Any]:
    """Charge le bestiaire JSON directement (sans ToolContext).

    Le bestiaire source stocke les monstres en clés top-level (hors `_meta`) :
    on les enveloppe ici sous `{"monstres": {...}}`, même convention que
    `tools.monstres._load_bestiaire` — sinon la détection de combat en prose
    (5ter) itérait sur un dict vide et ne détectait JAMAIS rien.
    """
    import json as _json
    from pathlib import Path as _Path
    path = _Path(data_dir) / "bestiaire.json"
    try:
        with open(path, "r", encoding="utf-8") as _f:
            raw = _json.load(_f)
    except Exception:                                            # noqa: BLE001
        return {"monstres": {}}
    monstres: dict[str, Any] = {}
    for _k, _v in raw.items():
        if _k == "_meta":
            continue
        if isinstance(_v, dict) and "nom" in _v:
            monstres[_v.get("cle", _k)] = _v
    return {"monstres": monstres}


# --------------------------------------------------------------------------- #
#  🧪 Bêta (M4) — Détection des sorts NARRÉS sans `incanter_sort`.
#
#  Partie réelle : « il lance Bénédiction… puis Lumière » alors que la fiche
#  n'avait AUCUN sort préparé — le LLM contournait le tool, la préparation et
#  les emplacements restaient intacts (fiction sans effet). On repère les
#  noms de sorts du catalogue dans la prose du tour ; le rattrapage 5quater-s
#  ré-invoque `incanter_sort` qui applique (ou REFUSE honnêtement) les règles :
#  préparation, emplacements, effets.
# --------------------------------------------------------------------------- #
_RE_NEGATION_SORT = _re_mod.compile(
    r"(pas\s+(?:de\s+)?(?:sort|pr[ée]par\w*|emplacement)"
    r"|ne\s+peut|ne\s+peux|impossible|refus\w*|aucun\s+emplacement"
    r"|épuis[ée]s?|n'a\s+pas|n'ai\s+pas|sans\s+succès|en\s+ vain)",
    _re_mod.IGNORECASE,
)
_RE_MEMORISATION_SORT = _re_mod.compile(
    r"\b(m[ée]moris\w*|pr[ée]par\w*(?:er|e|es)?\s+(?:ses\s+|mes\s+|ses)?sorts?)\b",
    _re_mod.IGNORECASE,
)
# 🔧 Bêta (M4, durcissement) : un nom de sort dans la prose ne prouve PAS une
# incantation — « bouclier » désignait l'éQUIPEMENT du clerc et déclenchait le
# rattrapage du sort « Bouclier ». Une mention ne compte que précédée d'un
# verbe d'incantation (lance le sort de…, incante…, prononce…).
_RE_VERBE_INCANTATION = _re_mod.compile(
    r"\b(?:"
    r"lanc[ée]z?s?\s+(?:(?:le|un|une|ensuite|puis|alors|son|sa|ses)\s+)*(?:sort\s+(?:de\s+)?)?"
    r"|incant\w*\s+(?:(?:le|un|une|ensuite|puis)\s+)*(?:sort\s+(?:de\s+)?)?"
    r"|jett[ée]z?s?\s+(?:(?:un|le|une|ensuite|puis|son|sa)\s+)*(?:sort\s+(?:de\s+)?)?"
    r"|r[ée]cit\w+\s+"
    r"|invoqu\w*\s+(?:(?:le|un|une|ensuite|puis)\s+)*(?:sort\s+(?:de\s+)?)?"
    r"|prononc\w+\s+(?:les\s+mots\s+de\s+|le\s+|la\s+|l'\s*)?"
    r"|d[ée]clench\w+\s+(?:(?:le|un|une|son|sa)\s+)?"
    r"|m[ée]moris\w+\s+(?:(?:le|un|une|mes|ses|ensuite|puis)\s+)*"
    r")$",
    _re_mod.IGNORECASE,
)
# 🔧 Bêta (M4, élargissement symétrique) : les phrasings naturels placent le
# verbe APRÈS le nom (« un rayon de givre jaillit de son index », « le sort
# s'abat sur la cible ») — un verbe de MANIFESTATION qui suit prouve tout
# autant l'incantation narrée.
_RE_MANIFESTATION_SORT = _re_mod.compile(
    r"\b(?:"
    r"jaill\w*|s'abat|s\Wabat\w*|foudroi\w*|illumin\w*|s'embrase|s\Wembras\w*"
    r"|travers\w*\s+(?:l'air|la\s+pi[èe]ce|le\s+courloir)"
    r"|frapp\w*|vise\w*|blesse\w*|atteint\w*|glac\w+\s+(?:la|le|le\s+mort|sa|son)"
    r"|envelopp\w*|recouvr\w*|touch\w*\s+(?:la|le|sa|son)|surgit\w*\s+(?:de\s+son|du\s+sac)"
    r")",
    _re_mod.IGNORECASE,
)


def _normaliser_texte_sort(t: str) -> str:
    import unicodedata as _ud
    nf = _ud.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in nf if not _ud.combining(c))


def _detecter_sorts_narres(
    prose: str,
    traces_incanter_norm: set[str],
) -> list[dict[str, Any]]:
    """Repère les sorts du catalogue mentionnés comme LANCÉS dans la prose.

    - Faux positifs maîtrisés : un sort cité dans une phrase de REFUS
      (« pas préparé », « ne peut », « épuisé »…) ou de simple MENTION
      (« tu pourrais lancer… ») n'est PAS considéré comme lancé.
    - `traces_incanter_norm` : textes NORMALISÉS des appels `incanter_sort`
      réussis ce tour — un sort déjà résolu par le tool n'est jamais
      re-rattrapé.

    Renvoie [{nom, position}] trié par position dans la prose.
    """
    if not prose or not prose.strip():
        return []
    norm = _normaliser_texte_sort(prose)
    trouves: dict[str, dict[str, Any]] = {}
    for s in sorts_mod.SORTS:
        nom = str(s.get("nom") or "").strip()
        if len(nom) < 4:
            continue
        n = _normaliser_texte_sort(nom)
        if not n:
            continue
        if any(n in t for t in traces_incanter_norm):
            continue  # déjà résolu par le tool ce tour
        m = _re_mod.search(
            r"(?<![a-z0-9])" + _re_mod.escape(n) + r"(?![a-z0-9])", norm
        )
        if not m:
            continue
        # Fenêtre de contexte : une phrase de refus/mention ne compte pas.
        debut = max(0, m.start() - 80)
        fin = min(len(norm), m.end() + 80)
        contexte = norm[debut:fin]
        if _RE_NEGATION_SORT.search(contexte):
            continue
        if _re_mod.search(r"\b(pourrais?|pourriez?|devr\w+|faudr\w+)\b", contexte):
            continue
        # 🔧 Durcissement + élargissement : un verbe d'incantation doit
        # PRÉCÉDER le nom (~40 caractères), OU un verbe de manifestation le
        # SUIVRE (~60) — sinon simple mention (« bouclier » = objet).
        avant = norm[max(0, m.start() - 40) : m.start()]
        apres = norm[m.end() : min(len(norm), m.end() + 60)]
        if not (
            _RE_VERBE_INCANTATION.search(avant)
            or _RE_MANIFESTATION_SORT.search(apres)
        ):
            continue
        if nom not in trouves:
            trouves[nom] = {"nom": nom, "position": m.start()}
    return sorted(trouves.values(), key=lambda x: x["position"])


def _detecter_sorts_cites(prose: str) -> list[str]:
    """Liste les sorts du catalogue SIMPLEMENT CITÉS dans un texte (noms
    exacts, frontières de mots, sans analyse de verbe).

    Réservé aux contextes déjà verrouillés par un verbe au niveau du message
    (mémorisation : « je mémorise Bénédiction et Lumière » — le verbe porte
    sur TOUTE la liste, les connecteurs « et/puis » entre les noms échappent
    à la détection de verbe par nom)."""
    if not prose or not prose.strip():
        return []
    norm = _normaliser_texte_sort(prose)
    trouves: list[str] = []
    for s in sorts_mod.SORTS:
        nom = str(s.get("nom") or "").strip()
        if len(nom) < 4:
            continue
        n = _normaliser_texte_sort(nom)
        if not n:
            continue
        if _re_mod.search(
            r"(?<![a-z0-9])" + _re_mod.escape(n) + r"(?![a-z0-9])", norm
        ):
            trouves.append(nom)
    return trouves


def _derive_scenario_id(data_dir: str, narration: str) -> Optional[str]:
    """Déduit l'identifiant du scénario à charger pour une écriture « opening ».

    Le petit modèle 9B raconte souvent l'ouverture en prose au lieu d'appeler
    `scenarios_laelith_charger`. On retrouve le scénario par correspondance du
    titre/ID mentionné dans la narration (best effort) ; sinon on retombe sur
    la mission active déjà mémorisée (le MJ a pu appeler `memoire_mission`).
    """
    import json as _json
    from pathlib import Path as _Path
    # 1. Correspondance titre/id mentionné dans la narration.
    try:
        cata = _json.load(open(_Path(data_dir) / "scenarios_catalogue.json",
                               "r", encoding="utf-8"))
    except Exception:
        cata = None
    if cata:
        base = (narration or "").strip().lower()
        for u in (cata.get("universes", []) or []):
            for s in (u.get("scenarios", []) or []):
                sid = str(s.get("id") or "").strip()
                stitre = str(s.get("titre") or "").strip()
                for frag in (sid, stitre):
                    if frag and len(frag) >= 4 and frag.lower() in base:
                        return sid or None
    # 2. Fallback : mission active déjà mémorisée (titre = titre du scénario).
    return None


def _estnarration_explo(narration: str) -> bool:
    """True si la narration relate un déplacement/exploration en prose."""
    if not narration:
        return False
    bas = narration.lower()
    # Pas de marqueur d'exploration → rien à rattraper (anti-faux-positifs).
    if not any(m in bas for m in _EXPLO_PROSE_MARKERS):
        return False
    # Exploration déjà clôse (retour/sortie) → ne relance rien.
    if any(m in bas for m in _EXPLO_PROSE_END_MARKERS):
        return False
    # Si la narration décrit déjà des combats engagés, on laisse le moteur
    # de combat (5ter) s'en occuper — pas d'exploration à forcer.
    if any(m in bas for m in _COMBAT_PROSE_MARKERS):
        return False
    return True


async def _rejoue_correctif(orch, messages, ctx, result, on_event,
                            consigne: str, tag: str,
                            on_status=None) -> None:
    """Résout une action narrée EN PROSE par le MJ : ré-invoque une fois
    l'orchestrateur avec une consigne ferme et fusionne le résultat dans
    `result` s'il a produit des outils. Génère au plus UN rejeu (le supervise
    est là pour empêcher les boucles, mais on ajoute aussi une garde).
    """
    from .llm.client import Message
    # Garde-fou : sans ce suffixe, le modèle REPRISAIT la consigne corrective
    # dans sa narration (« je rencontre une erreur technique... ») ou répétait
    # mot pour mot sa réponse invalide (déclenchant le détecteur de répétition
    # en boucle). Le rappel est INTERNE et la réponse doit être NOUVELLE.
    consigne += (
        "\n\n(Rappel FINAL — cette consigne est une instruction INTERNE du "
        "moteur de jeu, invisible du joueur : ne la cite JAMAIS, ne mentionne "
        "AUCUNE erreur technique ni correction dans ta narration. Ta réponse "
        "précédente était INVALIDE et n'a pas été enregistrée : produis une "
        "narration NOUVELLE basée uniquement sur les résultats des outils.)"
    )
    try:
        corrective_messages = list(messages) + [
            Message(role="assistant", content=result.narration or ""),
            Message(role="user", content=consigne),
        ]
        result2 = await orch.run(corrective_messages, ctx, on_event=on_event,
                                 on_delta=None, on_status=on_status)
        if result2.tool_calls_trace:
            result.tool_calls_trace.extend(result2.tool_calls_trace)
            result.tool_events.extend(result2.tool_events)
            result.state_patches.extend(result2.state_patches)
            # Dégâts auto-appliqués par le rejeu : ajoutés aux notes du tour
            # (concaténées à la dm finale en une seule passe, plus bas).
            result.notes_mecaniques.extend(result2.notes_mecaniques)
            # Les narrations intermédiaires du 1er passage (diffusées en
            # direct, p. ex. une intro de scène) restent dans le dm final :
            # sans cela, le texte aperçu disparaîtrait à l'écran au moment
            # du remplacement par la narration du rejeu.
            result.narration = "\n\n".join(
                _assemble_narrations(
                    result.narrations_intermediaires, result2.narration
                )
            ).strip()
            result.iterations += result2.iterations
            # (c) La narration finale remplace celle déjà streamée : on
            # demande aux clients d'effacer l'aperçu périmé avant le dm final.
            if on_event is not None:
                try:
                    await on_event({"type": "stream_reset"})
                except Exception:                                # noqa: BLE001
                    pass
            print(f"[dnd35] Rejeu {tag} réussi ({len(result2.tool_calls_trace)} tools)")
        else:
            print(f"[dnd35] Rejeu {tag} sans tool — avancement forcé")
    except Exception as e:                                               # noqa: BLE001
        print(f"[dnd35] Rejeu {tag} failed: {e}")


def _coupe_narration_evenement(narration: str, limite: int = 300) -> str:
    """Coupe une narration pour un événement d'histoire SANS casser le
    texte : à la fin de la dernière phrase COMPLÈTE tenant dans `limite`,
    sinon à la fin du dernier mot complet (+ « … »).

    L'ancienne coupe brute à 300 caractères tronquait en pleine phrase
    (partie 263f82dc : « …les murmures des marchands et les cris des
    enfants de la ville libre, mais ici, dans la »)."""
    texte = " ".join((narration or "").split())
    if len(texte) <= limite:
        return texte
    fenetre = texte[:limite]
    # Dernière phrase complète (., !, ?, …) suffisamment ample (≥ 100
    # caractères) pour ne pas jeter les 2/3 du récit.
    derniere = -1
    for m in _re_mod.finditer(r"[.!?…](?=\s)", fenetre):
        derniere = m.end()
    if derniere >= 100:
        return fenetre[:derniere].strip()
    coupe = fenetre.rsplit(" ", 1)[0].rstrip(",;: ")
    return coupe + "…"


def _journaliser_ouverture_si_besoin(etat: dict[str, Any], narration: str) -> bool:
    """Premier tour narré de la partie → consigne l'ouverture dans
    `histoire`. Renvoie True si l'événement a été ajouté.

    Sans cela, la directive « ⚠️ DÉBUT DE L'AVENTURE » (injectée par le
    prompt_builder TANT QUE `histoire` est vide) restait active CHAQUE
    tour : le MJ re-narrait l'accroche du scénario et re-remettait le
    matériel à l'infini (partie 5a9b99c8 : le parchemin de Teleshann
    remis trois tours de suite, le MJ « recommençant » l'ouverture)."""
    if etat.get("histoire"):
        return False
    if not (narration or "").strip():
        return False
    from datetime import datetime as _dt
    etat.setdefault("histoire", []).append({
        "ts": _dt.now().isoformat(),
        "tour": "",
        "evenement": "Début de l'aventure : "
        + _coupe_narration_evenement(narration),
    })
    return True


# 📉 Compression des vieilles narrations (partie 2ca691ec) : le 9B copie
# ses propres proses précédentes présentes dans le contexte (écho), et le
# budget de caractères part en vieilles proses au lieu de l'utile. On garde
# INTÉGRALES les N dernières narrations assistant (continuité immédiate) et
# on réduit les plus anciennes à leurs premières lignes + « […] ». Les
# messages user/tool ne sont pas touchés ; session.history reste complète.
_NARR_GARDEES_PLEINES = 2
_NARR_COMPRESSE_CHARS = 200


def _compresser_narrations_anciennes(
    fenetre: list[Any],
    garder: int = _NARR_GARDEES_PLEINES,
    limite: int = _NARR_COMPRESSE_CHARS,
) -> list[Any]:
    """Renvoie la fenêtre d'historique avec les vieilles narrations
    assistant compressées (les `garder` dernières restent intégrales)."""
    from dataclasses import replace as _replace
    idx = [i for i, m in enumerate(fenetre)
           if getattr(m, "role", "") == "assistant"
           and len(getattr(m, "content", "") or "") > limite]
    for i in idx[:-garder] if garder else idx:
        m = fenetre[i]
        c = m.content
        coupe = c[: limite - 4].rsplit(" ", 1)[0].rstrip(",;:")
        fenetre[i] = _replace(m, content=coupe + " […]")
    return fenetre


async def _tour_mourant(
    partie_id: str, nom: str, pv: int, conditions: list[str],
) -> str:
    """Jet de stabilisation officiel pour un PJ mourant (B26 bis).

    Renvoie le bloc narratif à diffuser, ou '' si rien n'a été joué.
    Règle 3.5 : 1d20 ≥ 10 stabilise ; 1 naturel = −1 PV ; à −10 le
    personnage meurt."""
    import random as _random

    del conditions
    etat = PartyState(data_dir=str(cfg.abs(cfg.paths.data_dir)),
                      partie_id=partie_id)
    data = etat.load()
    pj = next((p for p in (data.get("pj") or [])
               if str(p.get("nom") or "") == nom), None)
    if pj is None:
        return ""
    if int(pv) > 0:
        return ""
    jet = _random.randint(1, 20)
    lignes = [
        "💀 **" + nom + "** n'est plus en mesure d'agir (" + str(pv)
        + " PV).",
        "🩹 **Jet de stabilisation** : 1d20=" + str(jet)
        + (" → ✅ **stabilisé(e)** (inconscient(e), plus de perte de PV)."
           if jet >= 10 else
           " → ❌ **échec** (l'hémorragie continue : −1 PV)."),
    ]
    nv_pv = pv if jet >= 10 else pv - 1
    if nv_pv <= -10:
        lignes.append(
            "☠️ **" + nom + " est MORT(E)** (−10 PV ou moins). "
            "La partie est terminée pour ce personnage."
        )
    conds = {str(c).strip().lower() for c in (pj.get("conditions") or [])}
    if jet >= 10:
        conds.discard("mourant")
        conds.add("stabilisé")
        conds.add("inconscient")
    else:
        conds.add("mourant")
    pj["conditions"] = sorted(conds)
    pj["pv"] = nv_pv
    etat.save(data)
    # 🛡️ L'état de partie ET la fiche doivent dire la même chose : sans cet
    # alignement l'interface affichait l'ancien PV et le bloc de combat un
    # autre (même cause que B33 d.4 sur la CON).
    try:
        import os as _os
        _dossier = _os.path.join(str(cfg.abs(cfg.paths.data_dir)), "fiches")
        _slug = "".join(c for c in nom.lower() if c.isalnum())
        _chem = _os.path.join(_dossier, "fiche_" + _slug + ".json")
        if _os.path.isfile(_chem):
            with open(_chem, "r", encoding="utf-8") as _f_lu:
                _fic = json.load(_f_lu)
            _fic["pv"] = nv_pv
            _fic["conditions"] = pj.get("conditions") or []
            with open(_chem, "w", encoding="utf-8") as _f_ec:
                json.dump(_fic, _f_ec, ensure_ascii=False, indent=2)
    except Exception as _e_fic:                                  # noqa: BLE001
        _log_tour("stabilisation : fiche non alignée (%s)", _e_fic)
    return "\n".join(lignes)


# 🛡️ B31 : durée au-delà de laquelle un verrou de tour est considéré bloqué.
# 240 s couvre largement la latence mesurée (p50/p95 = 12–120 s) et la boucle
# de corrections complète, tout en arrêtant les dérives observées à 25 min.
_TOUR_VERROU_MAX_S = 240.0


def _log_tour(msg: str, *args: Any) -> None:
    print("[dnd35] " + (msg % args if args else msg))


async def _handle_say(
    initiator: WebSocket,
    session: PartySession,
    partie_id: str,
    player: str,
    text: str,
    personnage: str = "",
    client_id: str = "",
) -> None:
    """Traite un message de joueur : invoque le MJ (orchestrateur) et broadcast.

    🔒 Bêta (C2) : `personnage` = personnage incarné par la connexion
    expéditrice (mémorisé au join). En combat, la garde de tour exige que ce
    soit LUI le personnage actif — deux PJ du même compte ne peuvent plus
    faire passer l'action de l'un pour celle de l'autre.
    """
    if not text.strip():
        return

    # 0. Bloquer les messages pendant que le MJ traite (pensée/génération).
    # 🛡️ B31 : le refus n'est plus inconditionnel. Un verrou resté True au-delà
    # de `_TOUR_VERROU_MAX_S` (génération bloquée, coroutine annulée par une
    # déconnexion, exception hors `finally`) levait un refus permanent — la
    # partie devenait injouable et le moteur de combat continuait à frapper les
    # PJ sans que quiconque puisse agir. Au-delà du délai le verrou est levé,
    # la table est prévenue et le message passe.
    _age_verrou = session.verrou_age()
    if getattr(session, "thinking", False):
        if _age_verrou < _TOUR_VERROU_MAX_S:
            await session.broadcast({
                "type": "sys",
                "event": "turn_blocked",
                "detail": (
                    "⏳ Le MJ est en train de travailler — patientez avant "
                    "d'envoyer un nouveau message."
                ),
            })
            return
        session.verrou_lever()
        session.thinking_annules += 1
        _log_tour(
            "verrou de tour levé automatiquement après %.0f s "
            "(partie %s) — le tour bloqué est abandonné", _age_verrou,
            partie_id,
        )
        await session.broadcast({
            "type": "sys",
            "event": "turn_released",
            "detail": (
                "⏱️ Le tour du MJ a été interrompu après "
                f"{_age_verrou:.0f} s (blocage technique). L'état du jeu est "
                "celui du dernier tour terminé — renvoyez votre action."
            ),
        })

    # 1ter. 🛡️ B26 bis : un personnage Mourant/Inconscient (PV ≤ 0) ne peut
    # PAS agir. Observé 6 tours d'affilée : Sirin à −1 PV recevait « Sirin,
    # que faites-vous ? », explorait et combattait. On refuse l'action, on
    # lance le jet de stabilisation officiel et on prévient la table.
    _pj_act = None
    if personnage:
        try:
            _etat_mourant = PartyState(
                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                partie_id=partie_id,
            ).load()
            _pj_act = next(
                (p for p in (_etat_mourant.get("pj") or [])
                 if str(p.get("nom") or "") == personnage),
                None,
            )
        except Exception:                                    # noqa: BLE001
            _pj_act = None
    # 🛡️ B8 (audit parties complètes) : un personnage qui a DÉJÀ agi ce
    # round ne peut pas rejouer. Sans ce garde, le même joueur envoyait un
    # second message pendant le même round et l'action était résolue deux fois
    # (le marqueur `deja_agi` du moteur n'était consulté que par le moteur).
    if _pj_act is not None:
        try:
            _etat_b8 = PartyState(
                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                partie_id=partie_id,
            ).load()
            _deja = list(_etat_b8.get("deja_agi") or [])
        except Exception:                                        # noqa: BLE001
            _deja = []
        if (_etat_b8.get("phase") == "combat" and personnage
                and personnage in _deja):
            await session.broadcast({
                "type": "sys",
                "event": "action_refusee",
                "detail": (
                    f"⛔ {personnage} a déjà agi ce round. Attendez le round "
                    "suivant (ou utilisez `terminer_mon_tour`)."
                ),
            })
            await session.broadcast({
                "type": "status", "description": "", "done": True,
            })
            return
    if _pj_act is not None:
        try:
            _pv_act = int(_pj_act.get("pv", 1) or 0)
        except (TypeError, ValueError):
            _pv_act = 1
        _conds_act = {str(c).strip().lower()
                      for c in (_pj_act.get("conditions") or [])}
        if _pv_act <= 0 or "mourant" in _conds_act \
                or "inconscient" in _conds_act:
            _note_mourant = await _tour_mourant(
                partie_id, str(_pj_act.get("nom") or personnage),
                _pv_act, sorted(_conds_act),
            )
            await session.broadcast({
                "type": "sys",
                "event": "action_refusee",
                "detail": (
                    f"💀 {personnage} est "
                    + ("mourant" if _pv_act <= -1 else "inconscient")
                    + " (PV "
                    + str(_pj_act.get("pv"))
                    + ") et ne peut pas agir. Un jet de stabilisation a été "
                    "lancé. Un compagnon doit l'aider (soin, "
                    "fiche_perso_soigner) ou vous devez patienter."
                ),
            })
            if _note_mourant:
                await session.broadcast({
                    "type": "dm", "text": _note_mourant,
                    "tool_events": [], "state_patches": [],
                    "tool_calls_trace": [],
                })
            await session.broadcast({
                "type": "status", "description": "", "done": True,
            })
            return

    # 1. Mémorise le message joueur + broadcast immédiat à tous (echo).
    session.remember_player_message(player, text)
    await session.broadcast({
        "type": "player",
        "player": player,
        "text": text,
        # 🔁 Bêta (M2) : identifiant client → l'auteur reconnaît son propre
        # echo (déjà affiché en optimiste) ; les AUTRES clients, eux, doivent
        # afficher ce broadcast (c'était le bug : messages joueurs invisibles
        # en direct chez les coéquipiers).
        "client_id": str(client_id or ""),
    })

    # 1bis. ⚔️ Garde de tour : DÉPLACÉE après le pre-run du moteur de combat
    # (voir dans le verrou de session) — la rotation doit d'abord être
    # corrigée par le serveur (skip des incapables, tours de monstres)
    # AVANT de décider qui a le droit de parler.

    # 2. Statut "thinking" aux clients connectés.
    session.thinking = True
    session.thinking_depuis = time.monotonic()
    await session.broadcast({
        "type": "status",
        "description": "Le MJ réfléchit...",
        "thinking_blocked": True,
    })

    # 3→6. Tour du MJ — sérialisé par partie (un seul MJ à la fois) et compté
    # globalement (le unload n'a lieu que quand plus aucun tour n'est actif).
    await _turn_begin()
    try:
        async with session.turn_lock:
            # Callbacks définis AVANT le pre-run : le moteur de combat serveur
            # (et les tools qu'il exécute) doit pouvoir pousser ses patches
            # d'état en direct, pas seulement à la fin du tour.
            async def on_event(ev: dict[str, Any]) -> None:
                # (a)/(c) Les events de contrôle ont leur propre canal WS :
                # patches d'état immédiats et reset de l'aperçu streamé.
                etype = ev.get("type")
                if etype == "state_patches":
                    await session.broadcast({
                        "type": "state_patches",
                        "patches": ev.get("patches") or [],
                    })
                elif etype == "stream_reset":
                    await session.broadcast({"type": "stream_reset"})
                else:
                    await session.broadcast({"type": "tool_event", "event": ev})

            # 📊 Statut enrichi : l'étape en cours est poussée au client
            # (défini TÔT — utilisé par le pre-run du moteur de combat).
            async def on_status(desc: str) -> None:
                await session.broadcast({
                    "type": "status",
                    "description": desc,
                })

            # ⚡ Streaming coupé (stream_to_clients: false) → on_delta=None :
            # l'orchestrateur réutilise alors le contenu de l'appel non-streamé
            # (chat.content) au lieu de RÉ-GÉNÉRER la narration finale en
            # streaming — économie d'un appel LLM complet par tour (~la
            # moitié de la latence de l'étape finale). Passer un callback qui
            # jette les tokens (comme avant) obligeait l'orchestrateur à
            # payer cette seconde génération pour rien.
            if cfg.game.stream_to_clients:

                async def on_delta(token: str) -> None:
                    # Stream des tokens de narration vers les clients.
                    await session.broadcast({"type": "delta", "text": token})
            else:
                on_delta = None

            async def reset_stream() -> None:
                """(c) Efface l'aperçu streamé chez les clients : à réserver
                aux cas où la narration finale va REMPLACER le texte déjà
                affiché (rejeu correctif), sinon le joueur voit un bloc
                disparaître puis un autre le remplacer sans transition."""
                if cfg.game.stream_to_clients:
                    await session.broadcast({"type": "stream_reset"})

            # 2.pre ⚙️ MOTEUR DE COMBAT SERVEUR (pre-run) : avant toute
            # décision de tour, le serveur fait avancer la mécanique —
            # saute les combattants incapables (mourants…), joue les tours
            # de monstres (attaque officielle du bestiaire), détecte
            # victoire/défaite (clôture + XP officielle + mémoire). Aucun
            # LLM n'intervient ici : c'est déterministe.
            ctx_pre = _ctx(partie_id, player)
            ctx_pre.on_event = on_event
            events_pre: list[str] = []
            patches_pre: list[dict[str, Any]] = []
            try:
                etat_pre = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_pre.get("phase") == "combat":
                    # 📊 Statut enrichi : le serveur joue la mécanique des
                    # monstres (déterministe, sans LLM).
                    if on_status is not None:
                        try:
                            await on_status(
                                "Le serveur joue les tours des monstres…")
                        except Exception:                    # noqa: BLE001
                            pass
                    res_pre = await _boucle_combat(
                        ctx_pre,
                        timeout_secondes=cfg.game.combat_turn_timeout_seconds,
                    )
                    events_pre = res_pre.events
                    patches_pre = res_pre.patches
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Pre-run moteur de combat échoué (ignoré) : {e}")

            # 🏆 Clôture survenue pendant le PRE-RUN (victoire/défaite + XP) :
            # ces blocs FACTUELS ne passaient JAMAIS dans la narration du
            # chemin normal (partie réelle : +100 PX attribués en silence,
            # aucun « 🏆 Victoire » affiché — le joueur ne savait pas pourquoi
            # sa barre d'XP avait bougé). On les affichera en fin de tour.
            cloture_pre = [
                ev for ev in events_pre
                if ev.lstrip().startswith("🏆") or "Victoire !" in ev
                or "💀" in ev
            ]

            # 1bis. ⚔️ Application MÉCANIQUE du tour de jeu (D&D 3.5) : en
            # phase de combat, seul le joueur dont c'est le tour peut
            # déclencher le MJ. Les messages des autres joueurs sont
            # diffusés mais n'invoquent PAS le LLM.
            etat_avant = PartyState(
                data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
            ).load()
            actif_avant = str(etat_avant.get("courant_tour_pour") or "")
            if etat_avant.get("phase") == "combat":
                actif = actif_avant
                pj_actif = next(
                    (p for p in (etat_avant.get("pj") or [])
                     if p.get("nom") == actif),
                    None,
                )
                if pj_actif is not None:
                    joueur_actif = str(pj_actif.get("joueur") or "").strip().lower()
                    # 🔒 Bêta (C2) : garde DOUBLE — le compte doit être celui
                    # du PJ actif ET le personnage incarné par la connexion
                    # doit ÊTRE le PJ actif. Sans la seconde vérification,
                    # deux PJ du même compte (deux onglets) se volaient leurs
                    # tours : l'action de l'un était rejouée comme celle de
                    # l'autre (observé en bêta : « coup de masse d'Elandra »
                    # déclenché depuis la fiche de Thorin).
                    perso_exp = (personnage or "").strip().casefold()
                    mauvais_perso = bool(
                        perso_exp and perso_exp != actif.strip().casefold()
                    )
                    if (
                        (joueur_actif and player.strip().lower() != joueur_actif)
                        or mauvais_perso
                    ):
                        # Les événements mécaniques du pre-run (monstres
                        # joués, tours passés…) sont montrés à la table même
                        # si le message n'ouvre pas un tour LLM — NARRÉS par
                        # le LLM (mécanique d'abord, prose ensuite) : le bloc
                        # brut n'est que le repli. Timeout court : ce chemin
                        # ne doit pas retarder l'avis « attendez votre tour ».
                        if events_pre:
                            _heros_b, _ennemis_b = _camps_du_combat(etat_avant)
                            _nar_bloc = ""
                            try:
                                _nar_bloc = await asyncio.wait_for(
                                    _narrer_mecaniques_serveur(
                                        app,
                                        events_pre,
                                        contexte=str(
                                            (etat_avant.get("lieu") or {})
                                            .get("nom") or ""
                                        ),
                                        heros=_heros_b,
                                        ennemis=_ennemis_b,
                                    ),
                                    timeout=20.0,
                                )
                            except (asyncio.TimeoutError, Exception) as e_b:  # noqa: BLE001
                                print(
                                    "[dnd35] Narration pre-run (tour bloqué) "
                                    f"échouée/timeout (repli brut) : {e_b}"
                                )
                            await session.broadcast({
                                "type": "dm",
                                "text": _nar_bloc
                                or (
                                    "⚙️ _Mécanique serveur :_\n\n"
                                    + "\n\n".join(events_pre)
                                ),
                                "tool_events": [],
                                "state_patches": patches_pre,
                                "tool_calls_trace": [],
                            })
                        await session.broadcast({
                            "type": "sys",
                            "event": "turn_blocked",
                            "detail": (
                                f"⏳ {player}"
                                + (f" ({personnage})" if mauvais_perso else "")
                                + " doit attendre : en combat, c'est le tour "
                                f"de {actif} (joué par "
                                f"{pj_actif.get('joueur')}) — round "
                                f"{etat_avant.get('tour', 1)}."
                            ),
                        })
                        # 🔧 Bêta : le returncourt sautait le broadcast final
                        # « status done » (étape 7) — le verrou de réflexion
                        # restait collé chez les clients (champ de saisie
                        # désactivé jusqu'à un F5). On clôt le statut ICI.
                        # 🛡️ B3 : le message refusé est retiré de l'historique,
                        # sinon le MJ le rejouait plusieurs tours plus tard
                        # comme si le joueur venait de le dire.
                        if mauvais_perso:
                            session.oublier_dernier_message_joueur()
                        await session.broadcast({
                            "type": "status", "description": "", "done": True
                        })
                        return
                # Si l'actif est un PNJ/monstre restant (rare après pre-run),
                # le message passe : la boucle post-tour le gérera.

            # 3. Construit le message système (system prompt + récap + sections + RAG).
            rag_context = ""
            rag_store: Optional[RagStore] = getattr(app.state, "rag_store", None)
            if rag_store is not None:
                try:
                    rag_context = await rag_store.render_for_prompt(text)
                except Exception as e:                               # noqa: BLE001
                    # Le RAG ne doit jamais bloquer une narration ; on log et on continue.
                    print(f"[dnd35] RAG requête échouée (ignoré) : {e}")
                    rag_context = ""
            system_text, etat = app.state.prompt_builder.build_system_message(
                partie_id, rag_context=rag_context
            )

            # 3bis. ⚔️ Bannière de tour : rappel mécanique du combat en
            # cours, injecté dans le message système à chaque invocation du
            # MJ. Le rôle du LLM est STRICTEMENT narratif + résolution de
            # l'action du joueur actif : la rotation, les monstres, la
            # clôture et l'XP sont SERVEUR.
            if etat.get("phase") == "combat":
                actif = str(etat.get("courant_tour_pour") or "?")
                ordre = ", ".join(
                    f"{e.get('nom')} ({e.get('init', '?')})"
                    for e in (etat.get("initiative") or [])
                ) or "?"
                pj_actif = next(
                    (p for p in (etat.get("pj") or []) if p.get("nom") == actif),
                    None,
                )
                if pj_actif is not None:
                    qui = (
                        f"{actif} — JOUEUR {pj_actif.get('joueur')}. Résous "
                        "UNIQUEMENT les actions que CE joueur déclare pour "
                        f"{actif} (attaque, sort, soin…) avec les tools ; "
                        "s'il ne déclare rien d'actif, contente-toi de "
                        "narrer sa position/garde et rappelle-lui qu'il peut "
                        "dire « je termine mon tour »."
                    )
                else:
                    qui = (
                        f"{actif} (MONSTRE/PNJ) — le serveur joue déjà son "
                        "tour automatiquement. N'invente PAS ses actions : "
                        "reprends simplement les événements mécaniques "
                        "listés ci-dessous dans ta narration."
                    )
                system_text += (
                    f"\n\n⚔️ **TOUR EN COURS** — round {etat.get('tour', 1)}. "
                    f"Ordre d'initiative : {ordre}. C'est AU TOUR DE {qui}\n"
                    "⚙️ **GÉRÉ PAR LE SERVEUR (n'y touche PAS)** : rotation "
                    "des tours, attaques des monstres, stabilisation des "
                    "mourants, fin de combat, expérience. N'appelle NI "
                    "tour_suivant_combat NI finir_combat NI engager_combat "
                    "pendant un combat en cours.\n"
                    "🚫 N'invente JAMAIS un délai ou timeout expiré pour "
                    "expliquer un changement de tour : la rotation est "
                    "SERVEUR, aucun délai n'« expire » dans ta narration.\n"
                    "🧭 Ton rôle : narrer ce qui vient de se passer "
                    "(notamment les événements mécaniques serveur listés "
                    "ci-dessous) puis, si c'est le tour d'un PJ, résoudre "
                    "son action déclarée — attaque : lancer_attaque puis "
                    "lancer_degats puis fiche_perso_infliger_degats ; soin : "
                    "lancer_des puis fiche_perso_soigner ; sort offensif : "
                    "lancer_degats (+ lancer_sauvegarde si la cible a droit "
                    "à un jet). Économie d'actions D&D 3.5 : max 1 action "
                    "standard + 1 mouvement par round.\n"
                    "✨ Invoquation / renfort en cours de mêlée : "
                    "combat_ajouter_combattant(nom, allie) AVANT de narrer "
                    "l'arrivée — jamais engager_combat."
                )
                if events_pre:
                    system_text += (
                        "\n\n⚙️ **ÉVÉNEMENTS MÉCANIQUES RÉSOLUS PAR LE "
                        "SERVEUR DEPUIS LE DERNIER MESSAGE** (déjà affichés "
                        "aux joueurs — intègre-les à ta narration sans les "
                        "répéter mot à mot, et tire-en les conséquences "
                        "dramatiques) :\n"
                        + "\n\n".join(events_pre)
                    )
            # On re-construit la conversation à partir de l'historique (système
            # en tête), avec un budget en caractères : le message système et
            # les schémas de tools consomment déjà ~6 k tokens, un historique
            # non borné saturerait le contexte sur les longues campagnes.
            budget_hist = cfg.game.max_history_chars
            hist = list(session.history)
            total = 0
            debut = len(hist)
            for i in range(len(hist) - 1, -1, -1):
                total += len(getattr(hist[i], "content", "") or "")
                if total > budget_hist:
                    debut = i + 1
                    break
                debut = i
            if debut < len(hist) - 1:  # garde au moins le dernier message
                print(f"[dnd35] Historique tronqué : {len(hist) - debut} messages "
                      f"anciens omis (budget {budget_hist} chars).")
            fenetre = hist[debut:]

            # 📉 Compression des vieilles narrations (partie 2ca691ec) : les
            # longues proses assistant dans le contexte AMORCENT la copie
            # verbatim (écho intra-contexte du 9B) et brûlent du budget. On
            # garde les 2 dernières narrations INTÉGRALES (continuité) ; les
            # plus anciennes sont réduites à ~200 chars. L'état mécanique
            # VRAI vient du bloc système + état de partie, jamais de ces
            # vieilleries — la table, elle, lit l'historique complet à
            # l'écran (session.history n'est pas modifiée).
            fenetre = _compresser_narrations_anciennes(fenetre)

            messages = [__import__("server.llm.client", fromlist=["Message"]).Message(
                role="system", content=system_text
            )] + fenetre

            # 4. Boucle d'orchestration : LLM ↔ tools → narration + events + patches.
            ctx = _ctx(partie_id, player)
            ctx.on_event = on_event
            # Id unique de CE tour : les tools (ex : verrou « 1 déplacement
            # de donjon par tour » dans cartes.py) s'appuient dessus pour
            # borner certaines actions à une seule fois par message joueur.
            # Les rejeux correctifs ci-dessous réutilisent le même ctx et
            # donc le même tour_id — le quota de déplacement reste global.
            ctx.tour_id = uuid.uuid4().hex

            # Des dégâts viennent d'être résolus par le moteur serveur (pre-run)
            # ? Le LLM les reformule alors légitimement dans sa narration — on
            # lui fait confiance sur la prose de dégâts pour ce tour.
            trust_damage_prose = any(
                "dégâts" in str(ev).lower() for ev in events_pre
            )

            orch = _orchestrator(app)

            result = await orch.run(
                messages, ctx, on_event=on_event, on_delta=on_delta,
                on_status=on_status,
                trust_damage_prose=trust_damage_prose,
            )

            # Statut : la narration est écrite à l'écran — ce qui suit est du
            # post-traitement (vérifications anti-simulation, rejeus, images
            # en arrière-plan). Le libellé change pour que la table sache que
            # le MJ n'« écrit » plus.
            await session.broadcast({
                "type": "status",
                "description": "Le MJ finalise la scène…",
            })

            # 5bis. ⚔️ Post-traitement du tour LLM. Les tours de monstres ne
            # sont PLUS rejoués par le LLM : le moteur serveur (ci-dessous,
            # bloc ⚙️) les joue de façon déterministe. Ne restent ici que
            # les rattrapages liés à l'action DÉCLARÉE par le joueur actif.
            try:
                apres = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                # Une attaque ARMÉE déclarée par le joueur n'est résolue que si
                # un outil d'attaque a réellement tourné (lancer_attaque /
                # lancer_degats / fiche_perso_infliger_degats). Un simple
                # `lancer_d20` — ou AUCUN jet — laisse le coup SANS effet : on
                # déclenche alors le rattrapage 5bis-a MÊME si la rotation a
                # déjà avancé (partie 5a9b99c8 : le MJ a narré « jet 24 /
                # dégâts 11 / goule hors de combat » sans AUCUN outil, et
                # `terminer_mon_tour` avait déplacé le tour actif → le
                # garde-fou historique ne se déclenchait pas, la goule restait
                # à 16/16 malgré la prose).
                _attaque_declaree = bool(_ACTION_ATTAQUE_RE.search(text or ""))
                _outil_attaque = any(
                    tc.get("name") in (
                        "lancer_attaque", "lancer_degats",
                        "fiche_perso_infliger_degats",
                    )
                    for tc in result.tool_calls_trace
                )
                _attaque_non_resolue = _attaque_declaree and not _outil_attaque
                if (
                    etat_avant.get("phase") == "combat"
                    and apres.get("phase") == "combat"
                    and actif_avant
                    and (
                        str(apres.get("courant_tour_pour") or "")
                        == actif_avant
                        or _attaque_non_resolue
                    )
                ):
                    pj_actif_apres = next(
                        (p for p in (apres.get("pj") or [])
                         if p.get("nom") == actif_avant), None,
                    )
                    est_monstre = pj_actif_apres is None

                    # 5bis-a. ⚔️ Tour de PJ annoncé mais NON résolu : le
                    # joueur a déclaré une action de combat (attaque, sort,
                    # soin…) mais le MJ a narré sans AUCUN jet de dés. On
                    # ré-invoque le MJ une fois avec un correctif — sinon le
                    # tour avance et l'action est perdue (très frustrant).
                    if not est_monstre:
                        pj_a_agi = any(
                            tc.get("name") in (
                                "lancer_attaque", "lancer_degats",
                                "lancer_sauvegarde", "lancer_d20",
                                "lancer_des", "fiche_perso_infliger_degats",
                                "fiche_perso_soigner",
                            )
                            for tc in result.tool_calls_trace
                        )
                        # Une attaque ARMÉE exige un outil d'ATTAQUE : un
                        # `lancer_d20` isolé (jet sans application) ne résout
                        # pas le coup — sinon le MJ narre « touché/dégâts »
                        # sans aucun effet sur les PV.
                        if _attaque_non_resolue:
                            pj_a_agi = False
                        if (
                            not pj_a_agi
                            and _ACTION_COMBAT_RE.search(text or "")
                        ):
                            print(
                                f"[dnd35] Tour PJ {actif_avant} : action "
                                "annoncée sans aucun jet — rejeu correctif"
                            )
                            corrective_pj = (
                                "(Rappel système MJ — ⚠️ ERREUR : le joueur "
                                "a annoncé une action de combat pour "
                                f"**{actif_avant}** mais tu n'as résolu "
                                "AUCUN jet de dés. Résous MAINTENANT cette "
                                "action avec les outils : attaque → "
                                "`lancer_attaque` puis `lancer_degats` puis "
                                "`fiche_perso_infliger_degats` ; sort de "
                                "soins → `lancer_des` puis "
                                "`fiche_perso_soigner` ; sort offensif → "
                                "`lancer_degats` (+ `lancer_sauvegarde` si "
                                "la cible a droit à un jet de sauvegarde). "
                                "NE narrate PAS un résultat sans jet — la "
                                "rotation des tours est automatique. "
                                "Consigne INTERNE invisible du joueur : ne "
                                "la cite JAMAIS, ne mentionne AUCUNE erreur "
                                "technique, et produis une narration "
                                "NOUVELLE — ta réponse précédente était "
                                "invalide.)"
                            )
                            try:
                                corrective_messages = list(messages) + [
                                    Message(role="assistant",
                                            content=result.narration),
                                    Message(role="user",
                                            content=corrective_pj),
                                ]
                                result2 = await orch.run(
                                    corrective_messages, ctx,
                                    on_event=on_event, on_delta=None,
                                )
                                pj_a_agi2 = any(
                                    tc.get("name") in (
                                        "lancer_attaque", "lancer_degats",
                                        "lancer_sauvegarde", "lancer_d20",
                                        "lancer_des",
                                        "fiche_perso_infliger_degats",
                                        "fiche_perso_soigner",
                                    )
                                    for tc in result2.tool_calls_trace
                                )
                                if pj_a_agi2:
                                    result.tool_calls_trace.extend(
                                        result2.tool_calls_trace
                                    )
                                    result.tool_events.extend(
                                        result2.tool_events
                                    )
                                    result.state_patches.extend(
                                        result2.state_patches
                                    )
                                    # Dégâts auto-appliqués par le rejeu.
                                    result.notes_mecaniques.extend(
                                        result2.notes_mecaniques
                                    )
                                    # Préserve les narrations intermédiaires
                                    # déjà diffusées (cf. _rejoue_correctif),
                                    # mais abandonne celles qui RÉSOLVENT déjà
                                    # l'action en prose (touché/dégâts) : le
                                    # rejeu les re-narre avec les jets réels —
                                    # les garder relirait la MÊME attaque deux
                                    # fois (bug vécu 5f3e31c9).
                                    _parts = _assemble_narrations(
                                        result.narrations_intermediaires,
                                        result2.narration,
                                    )
                                    if _parts:
                                        _parts = [
                                            p for p in _parts[:-1]
                                            if not _RE_PROSE_RESOLUTION.search(p)
                                        ] + _parts[-1:]
                                    result.narration = "\n\n".join(
                                        _parts
                                    ).strip()
                                    result.iterations += result2.iterations
                                    await reset_stream()
                                    print(
                                        f"[dnd35] Rejeu PJ {actif_avant} "
                                        f"réussi ({len(
                                            result2.tool_calls_trace)} "
                                        f"tools appelés)"
                                    )
                                else:
                                    print(
                                        f"[dnd35] Rejeu PJ {actif_avant} "
                                        "toujours sans jet — avancement forcé"
                                    )
                                    # Le LLM n'appellera JAMAIS l'outil
                                    # (partie 5f3e31c9 : Loup-garou resté
                                    # 28/32 malgré une narration de coup).
                                    # Le serveur résout l'attaque lui-même,
                                    # comme le moteur le fait pour les
                                    # monstres — sinon le tour avance sans
                                    # effet et l'action du joueur est perdue.
                                    if _ACTION_ATTAQUE_RE.search(text or ""):
                                        try:
                                            _note_atk = await (
                                                _attaque_pj_sans_jet(
                                                    orch, ctx, on_event,
                                                    result,
                                                    _pj_attaquant(
                                                        personnage,
                                                        actif_avant,
                                                        etat_avant),
                                                )
                                            )
                                            if _note_atk:
                                                result.narration = (
                                                    result.narration
                                                    + "\n\n⚙️ _Attaque résolue "
                                                    "par le serveur :_\n\n"
                                                    + _note_atk
                                                ).strip()
                                                print(
                                                    f"[dnd35] Attaque PJ "
                                                    f"{actif_avant} résolue "
                                                    "déterministiquement "
                                                    "(tools serveur)."
                                                )
                                        except Exception as e:       # noqa: BLE001
                                            print(
                                                "[dnd35] Attaque PJ "
                                                f"déterministe échouée : {e}"
                                            )
                            except Exception as e:                   # noqa: BLE001
                                print(f"[dnd35] Rejeu PJ failed: {e}")
            except Exception as e:
                print(f"[dnd35] 5bis rejeu failed: {e}")

            # 5bis-e. 🗺️ Déplacement de donjon narré SANS outil. Le joueur
            # demande une direction (« Je vais au nord », bouton de la carte…)
            # mais le MJ narre l'arrivée en prose sans appeler
            # `carte_donjon_explorer` : l'état ne bouge PAS, la carte reste
            # figée (bug réel : le modèle narrait « tu traverses le passage
            # est » sans tool). Ré-invoque une fois avec un correctif — le
            # tool serveur arbitre (refus si pas de porte dans ce mur).
            try:
                _etat_move = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                _move_match = _MOVE_INTENT_RE.match((text or "").strip())
                _deja_explorer = any(
                    tc.get("name") == "carte_donjon_explorer"
                    for tc in result.tool_calls_trace
                )
                if (
                    (_etat_move.get("donjon") or {}).get("id")
                    and _move_match
                ):
                    _dir = _move_match.group(1).lower()
                    # 🛡️ B17 (audit parties complètes) : en combat, une
                    # intention de déplacement était IGNORÉE (`phase != combat`
                    # filtrait le bloc) — le joueur ne pouvait jamais fuir.
                    if _etat_move.get("phase") == "combat":
                        _deja_retraite = any(
                            tc.get("name") == "retraite_combat"
                            for tc in result.tool_calls_trace
                        )
                        if not _deja_retraite:
                            tr_ret = await orch.execute_tool_direct(
                                "retraite_combat",
                                {"direction": _dir},
                                ctx, on_event, result,
                            )
                            note = tr_ret.text if tr_ret else ""
                            result.narration = (
                                (result.narration or "").rstrip()
                                + "\n\n🏃 _Retraite résolue par le serveur :_\n\n"
                                + note
                            ).strip()
                            print("[dnd35] Déplacement en combat → "
                                  f"retraite_combat({_dir}) résolue "
                                  "(B17).")
                    elif not _deja_explorer:
                        # 🛡️ B12 (audit parties complètes) : le rattrapage
                        # relançait une GÉNÉRATION LLM complète pour un
                        # déplacement — chaque mouvement validé coûtait un
                        # tour de plus (20 à 60 s). Le serveur appelle
                        # désormais l'outil lui-même et l'incruste dans la
                        # narration : un déplacement = une seule génération.
                        _dir_b = _move_match.group(1).lower()
                        print("[dnd35] Déplacement donjon narré sans tool "
                              f"({text!r}) — résolution directe par le "
                              "serveur (B12)")
                        _avant_mv = list(
                            ((_etat_move.get("donjon") or {}).get("courant")
                             or [None, None]))
                        _tr_mv = await orch.execute_tool_direct(
                            "carte_donjon_explorer",
                            {"direction": _dir_b},
                            ctx, on_event, result,
                        )
                        if _tr_mv is not None and _tr_mv.text:
                            result.narration = (
                                (result.narration or "").rstrip()
                                + "\n\n🗺️ _Déplacement résolu par le "
                                "serveur :_\n\n" + _tr_mv.text
                            ).strip()
                            # 🛡️ B2 : si la position n'a PAS changé (pas de
                            # porte dans ce mur), la prose du modèle qui
                            # décrivait l'arrivée est fausse — on le dit
                            # explicitement, sinon le joueur croit avoir bougé.
                            try:
                                _etat_ap = PartyState(
                                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                    partie_id=partie_id,
                                ).load()
                                _apres_mv = list(
                                    ((_etat_ap.get("donjon") or {})
                                     .get("courant") or [None, None]))
                            except Exception:                # noqa: BLE001
                                _apres_mv = _avant_mv
                            if _apres_mv == _avant_mv:
                                result.narration += (
                                    f"\n\nℹ️ _Précision de l'état du jeu : le "
                                    f"déplacement au **{_dir_b}** n'a PAS eu "
                                    "lieu (aucune porte dans ce mur). "
                                    "Position réelle du groupe : "
                                    + str(_apres_mv[:2]) + "._"
                                )
                            print("[dnd35] Déplacement résolu sans rejeu "
                                  "LLM (B12).")
                        # 🛡️ m15 : la carte du monde ne suivait JAMAIS
                        # l'exploration — `positions_joueurs` restait au point
                        # de départ du scénario pendant toute la partie. On
                        # note au moins l'étage courant comme zone.
                        try:
                            _st_m15 = PartyState(
                                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                partie_id=partie_id,
                            )
                            _e_m15 = _st_m15.load()
                            _dj_m15 = _e_m15.get("donjon") or {}
                            _mem_m15 = _e_m15.setdefault("memoire", {})
                            _pos_m15 = _mem_m15.setdefault("position", {})
                            _pos_m15["zone"] = (
                                "Donjon « " + str(_dj_m15.get("id") or "?")
                                + " » — étage " + str(_dj_m15.get("etage") or 1)
                                + ", salle " + str(_dj_m15.get("courant")
                                                  or [0, 0])
                            )
                            _e_m15["carte_du_monde_maj"] = True
                            _st_m15.save(_e_m15)
                        except Exception as _e_m15e:             # noqa: BLE001
                            print(f"[dnd35] m15 : position non notée "
                                  f"({_e_m15e})")
            except Exception as e:                               # noqa: BLE001
                print(f"[dnd35] 5bis-e rejeu déplacement failed: {e}")

            # 5bis-f. 🗺️ Entrée de donjon narrée SANS `carte_donjon_entrer`.
            # Le MJ raconte le seuil (« Vous vous dirigez vers l'entrée des
            # catacombes… Vous entrez dans l'obscurité ») mais n'initialise
            # AUCUN donjon : `donjon.id` reste null et la carte
            # `/carte-donjon.svg` répond 404 « Aucun donjon actif »
            # (partie 8a7c1f92 — Dues For The Dead). Contrairement au rejeu
            # 5bis-e (qui re-questionne le LLM), l'entrée est résolue
            # DÉTERMINISTEMENT ici : appel serveur de `carte_donjon_entrer`
            # avec l'id canonique du manifeste du scénario (lien
            # quête.source → <scenario>.donjon.json). Sans manifeste, on ne
            # force rien (risque de faux positif en ville/auberge).
            try:
                _etat_ent = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                _deja_entree = any(
                    tc.get("name") == "carte_donjon_entrer"
                    for tc in result.tool_calls_trace
                )
                if (
                    _etat_ent.get("phase") != "combat"
                    and not (_etat_ent.get("donjon") or {}).get("id")
                    and not _deja_entree
                    and (
                        _entree_donjon_narree(
                            (text or "") + " " + (result.narration or ""))
                        # 🔧 Bêta (résidu carte) : scénario À MANIFESTE dont
                        # l'ouverture se déroule DANS le donjon (salle 0,0 du
                        # plan — ex. « Duel au pinceau » : le duel a lieu sur
                        # la place des Sept Royaumes) : la prose ne narre
                        # JAMAIS d'« entrée » (la scène démarre en place) et
                        # le plan restait à jamais non initialisé (carte du
                        # monde affichée pendant que la narration vivait dans
                        # le donjon). À l'ouverture avec un manifeste, on
                        # initialise le plan DÉTERMINISTEMENT.
                        or (
                            str(_etat_ent.get("phase") or "").strip().lower()
                            in ("opening", "opening_complete", "load")
                            and not (_etat_ent.get("histoire"))
                        )
                    )
                ):
                    from .tools.cartes import _manifest_pour
                    _man = _manifest_pour(ctx, "")
                    _did = str((_man or {}).get("donjon_id") or "").strip()
                    if _did:
                        # 🎬 Tour d'OUVERTURE ? (phase opening/load : la
                        # partie vient de commencer) — si le MJ a « narré »
                        # une entrée de donjon dès l'ouverture, sa prose
                        # décrivait un lieu improvisé (c1f4e547 : ouverture
                        # dans le « donjon de Khundrukar », nom repris de
                        # l'exemple du schéma d'outils) : après l'entrée
                        # forcée, la scène d'ouverture sera RE-NARRÉE depuis
                        # la description canonique de la salle de départ.
                        _tour_ouverture = str(
                            _etat_ent.get("phase") or ""
                        ).strip().lower() in ("opening", "opening_complete",
                                              "load")
                        tr_ent = await orch.execute_tool_direct(
                            "carte_donjon_entrer", {"donjon_id": _did},
                            ctx, on_event, result,
                        )
                        if tr_ent is not None and not tr_ent.text.startswith(
                                ("❌", "⛔", "🚫")):
                            result.narration += (
                                "\n\n⚙️ _Le serveur a fait entrer le groupe "
                                "dans **" + _did + "** — carte du donjon "
                                "initialisée._\n\n" + tr_ent.text
                            )
                            print(
                                "[dnd35] Entrée donjon narrée sans tool → "
                                f"carte_donjon_entrer({_did!r}) exécutée "
                                "(serveur)."
                            )
                            if _tour_ouverture:
                                try:
                                    _etat_ouv = PartyState(
                                        data_dir=str(
                                            cfg.abs(cfg.paths.data_dir)),
                                        partie_id=partie_id,
                                    ).load()
                                    _salle0 = next(
                                        (
                                            s
                                            for s in (
                                                (
                                                    _etat_ouv.get("donjon")
                                                    or {}
                                                ).get("grille") or []
                                            )
                                            if s.get("visitee")
                                        ),
                                        None,
                                    )
                                    if _salle0 and _salle0.get(
                                        "description"
                                    ):
                                        _qb = (
                                            _etat_ouv.get("quete") or {}
                                        )
                                        _pitch = str(
                                            (_qb.get("bible") or {}).get(
                                                "pitch"
                                            )
                                            or _qb.get("pitch") or ""
                                        )
                                        _nar_ouv = await asyncio.wait_for(
                                            _renarrer_ouverture(
                                                app,
                                                (
                                                    f"{_did} — "
                                                    f"{str(_salle0.get('type') or 'point de départ').strip()} "
                                                    f"({_salle0.get('x')},"
                                                    f"{_salle0.get('y')})"
                                                ),
                                                str(
                                                    _salle0.get(
                                                        "description"
                                                    ) or ""
                                                ),
                                                _pitch,
                                            ),
                                            timeout=90.0,
                                        )
                                        if _nar_ouv:
                                            if on_event is not None:
                                                try:
                                                    await on_event({
                                                        "type":
                                                            "stream_reset"
                                                    })
                                                except Exception:  # noqa: BLE001
                                                    pass
                                            result.narration = (
                                                _nar_ouv + "\n\n" + tr_ent.text
                                            )
                                            print(
                                                "[dnd35] Ouverture re-narrée "
                                                "depuis le lieu canonique "
                                                "(5bis-f)."
                                            )
                                except (
                                    asyncio.TimeoutError, Exception
                                ) as e_ouv:                  # noqa: BLE001
                                    print(
                                        "[dnd35] Re-narration ouverture "
                                        f"échouée (prose conservée) : {e_ouv}"
                                    )
                        else:
                            print(
                                "[dnd35] Entrée donjon forcée refusée par "
                                "l'outil : "
                                + (tr_ent.text[:120]
                                   if tr_ent is not None else "None")
                            )
            except Exception as e:                               # noqa: BLE001
                print(f"[dnd35] 5bis-f entrée donjon failed: {e}")

            # 5bis-b. ⚔️ Rattrapage des invoquations non enregistrées.
            # Le joueur annonce une invoquation/renfort en combat mais le MJ
            # l'a narrée en prose sans appeler combat_ajouter_combattant (le
            # petit modèle 4B le fait souvent). On ré-invoque le MJ avec un
            # correctif ciblé — best effort, comme le rejeu monstre ci-dessus.
            try:
                _invoque_match = _INVOKE_RE.search(text or "")
                _deja_ajoute = any(
                    tc.get("name") == "combat_ajouter_combattant"
                    for tc in result.tool_calls_trace
                )
                if (
                    etat_avant.get("phase") == "combat"
                    and _invoque_match
                    and not _deja_ajoute
                ):
                    print("[dnd35] Invoquation narrée sans "
                          "combat_ajouter_combattant — rejeu avec correctif")
                    corrective_inv = (
                        "(Rappel système MJ — ⚠️ ERREUR : le joueur vient "
                        "d'annoncer une invoquation / un renfort, mais tu "
                        "as narré l'arrivée de la créature SANS "
                        "l'enregistrer mécaniquement. Appelle "
                        "IMMÉDIATEMENT "
                        "`combat_ajouter_combattant(nom=<créature invoquée>, "
                        "allie=true si elle combat pour les joueurs)` : "
                        "l'outil l'insère dans l'ordre d'initiative et suit "
                        "ses PV. N'appelle PAS engager_combat (il "
                        "réinitialiserait le combat en cours). Reprends "
                        "ensuite ta narration en t'appuyant sur le résultat "
                        "de l'outil.)"
                    )
                    corrective_messages = list(messages) + [
                        Message(role="assistant", content=result.narration),
                        Message(role="user", content=corrective_inv),
                    ]
                    result2 = await orch.run(
                        corrective_messages, ctx,
                        on_event=on_event, on_delta=None,
                    )
                    ok2 = any(
                        tc.get("name") == "combat_ajouter_combattant"
                        for tc in result2.tool_calls_trace
                    )
                    if ok2:
                        result.tool_calls_trace.extend(
                            result2.tool_calls_trace
                        )
                        result.tool_events.extend(result2.tool_events)
                        result.state_patches.extend(result2.state_patches)
                        # Dégâts auto-appliqués par le rejeu.
                        result.notes_mecaniques.extend(
                            result2.notes_mecaniques
                        )
                        # Préserve les narrations intermédiaires déjà
                        # diffusées (cf. _rejoue_correctif).
                        result.narration = "\n\n".join(
                            _assemble_narrations(
                                result.narrations_intermediaires,
                                result2.narration,
                            )
                        ).strip()
                        result.iterations += result2.iterations
                        await reset_stream()
                        print("[dnd35] Rejeu invoquation réussi")
                    else:
                        print("[dnd35] Rejeu invoquation toujours sans "
                              "outil — best effort accepté")
            except Exception as e:
                print(f"[dnd35] 5bis-b rejeu invoquation failed: {e}")

            # 5bis-c. 💥 Rattrapage dégâts non appliqués : tout lancer_degats
            # réussi du tour DOIT se traduire par une application effective
            # sur la cible (fiche PJ ou monstre suivi). Si le modèle a oublié
            # fiche_perso_infliger_degats, le serveur applique les dégâts
            # manquants exactement une fois (appariement anti double-application).
            # NB : depuis l'auto-application dans `_run_one_tool`, un
            # `lancer_degats` sur un ennemi suivi est déjà appliqué à chaud —
            # ce rattrapage ne couvre plus que les cas résiduels (cible hors
            # état au moment du jet, rejeus partiels…).
            try:
                txt_rattrapage = await _appliquer_degats_oublies(
                    orch, result, ctx, on_event)
                if txt_rattrapage:
                    result.narration += "\n\n" + txt_rattrapage
                    print("[dnd35] Dégâts non appliqués rattrapés "
                          "automatiquement (tools serveur).")
            except Exception as e:
                print(f"[dnd35] Rattrapage dégâts échoué (ignoré) : {e}")

            # 5bis-c-long. 🩹 Rattrapage soin déclaré SANS tool : le joueur
            # (ou le MJ) affirme « vous récupérez N PV » mais
            # fiche_perso_soigner n'a jamais été appelé. Le serveur applique
            # le montant déclaré (plafonné à pv_max par le tool) — sinon
            # l'état reste figé alors que la narration affirme une guérison
            # (partie 5f3e31c9 : chat « 3 PV → 6/17 » mais état 3/17).
            try:
                if (
                    actif_avant
                    and etat_avant.get("phase") == "combat"
                    and _ACTION_SOIN_RE.search(text or "")
                ):
                    txt_soins = await _appliquer_soins_oublies(
                        orch, result, ctx, on_event, actif_avant, text)
                    if txt_soins:
                        result.narration += "\n\n" + txt_soins
                        print("[dnd35] Soins narrés appliqués "
                              "automatiquement (tools serveur).")
            except Exception as e:
                print(f"[dnd35] Rattrapage soins échoué (ignoré) : {e}")

            # 5bis-c-bis. ⚔️ Lignes mécaniques des dégâts auto-appliqués
            # (cf. orchestrator._auto_appliquer_degats) : ajoutées à la dm
            # finale ICI (et non dans run()) pour survivre aux rejeux
            # correctifs qui remplacent `result.narration`.
            # C5 : ne réafficher QUE les notes dont l'état PV n'est PAS
            # déjà cité dans la narration du MJ (le chiffre PV serait en
            # double et lirait comme une « correction » contradictoire).
            _bloc_degats_auto = ""
            if getattr(result, "notes_mecaniques", None):
                notes_visibles = [
                    n for n in result.notes_mecaniques
                    if not _note_mecanique_deja_narree(result.narration, n)
                ]
                if notes_visibles:
                    _bloc_degats_auto = (
                        "\n\n⚖️ _Dégâts appliqués automatiquement :_\n\n"
                        + "\n".join(notes_visibles)
                    )
                    result.narration = (
                        result.narration + _bloc_degats_auto
                    ).strip()

            # 5bis-c2. 💥 Dé-duplication des dégâts de monstres : tout excès
            # appliqué par le LLM au-delà de ce qu'il a réellement jeté est
            # restitué aux PV du monstre (sinon un zombie de 5 PV mourait
            # sous 4 + 5 dégâts narrés à la main).
            try:
                _st_dd = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                )
                _etat_dd = _st_dd.load()
                _mc_dd = _etat_dd.get("monstres_combat") or []
                if _mc_dd and etat_avant.get("phase") == "combat":
                    _exces = _exces_degats_monstres(
                        result.tool_calls_trace, _mc_dd)
                    if _exces:
                        _corrections: list[str] = []
                        for _mo in _mc_dd:
                            _info = _exces.get(str(_mo.get("nom") or ""))
                            if not _info:
                                continue
                            try:
                                _e = int(_info.get("exces", 0))
                            except (TypeError, ValueError):
                                _e = 0
                            try:
                                _jetes_c = int(_info.get("jetes", 0))
                            except (TypeError, ValueError):
                                _jetes_c = 0
                            try:
                                _pv_max = int(_mo.get("pv_max") or 0)
                            except (TypeError, ValueError):
                                _pv_max = 0
                            try:
                                _pv = int(_mo.get("pv", 0) or 0)
                            except (TypeError, ValueError):
                                _pv = 0
                            # Plafond de restauration : l'état LÉGITIME est
                            # pv_max − dégâts RÉELLEMENT jetés. On rend les
                            # dégâts comptés deux fois, mais JAMAIS au point
                            # de ressusciter une créature détruite par les
                            # seuls jets (partie 263f82dc : « PV 3/3 — ☠️
                            # DÉTRUIT », squelettes ré-engagés).
                            # 🛡️ 120e9243 (point 3) : sans AUCUN jet réel
                            # compté ce tour (_jetes_c == 0), restaurer
                            # jusqu'à pv_max est illusoire et a RESSUSCITÉ
                            # Zendar (13 dg narrés en doublon → bannière
                            # « 24/24 PV » au tour suivant). Sans jet, on
                            # ne restitue RIEN : l'état du moteur fait foi
                            # (pas de ⚖️ « réajustement » bidon non plus).
                            if not (_pv_max > 0 and _jetes_c > 0):
                                continue
                            _plafond = _pv_max - _jetes_c
                            # 🛡️ B38 (audit parties complètes) : la
                            # dé-duplication ne ressuscite JAMAIS une créature
                            # déjà à terre. Observé deux fois : Goule à 3 PV
                            # frappée pour 3 (mort légitime) remontée à 3/16,
                            # Assassin à 6/16 remonté à 7/16. Si l'état est
                            # déjà ≤ 0, les jets légitimes ont suffi à la
                            # détruire : il n'y a rien à restituer.
                            if _pv <= 0 or _plafond <= 0:
                                continue
                            _avant_aj = _pv
                            _pv = min(_pv + _e, _plafond)
                            if _pv == _avant_aj:
                                continue
                            _mo["pv"] = _pv
                            if _pv > 0:
                                _mo["conditions"] = [
                                    c for c in (_mo.get("conditions") or [])
                                    if c not in ("Détruit", "Detruit")
                                ]
                            _corrections.append(
                                f"⚖️ {str(_mo.get('nom'))} : {_e} dégâts "
                                f"comptés deux fois — PV réajustés "
                                f"({_pv}/{str(_mo.get('pv_max') or '?')})."
                            )
                        if _corrections:
                            _st_dd.save(_etat_dd)
                            result.narration += (
                                "\n\n⚖️ _Réajustement (dégâts comptés "
                                "deux fois) :_\n\n" + "\n".join(_corrections)
                            )
                            result.state_patches.append(
                                {"monstres_combat": _mc_dd}
                            )
                            print("[dnd35] Dégâts doublés corrigés : "
                                  + "; ".join(_corrections))
            except Exception as e:                               # noqa: BLE001
                print(f"[dnd35] Dé-duplication dégâts échouée (ignoré) : {e}")

            # ⚙️ MOTEUR DE COMBAT SERVEUR (post-tour).
            # 1) Les événements mécaniques résolus AVANT le tour LLM
            #    (pre-run : tours de monstres, skips…) ne sont ré-ajoutés en
            #    bloc brut QUE si le LLM ne les a PAS intégrés à sa
            #    narration — la duplication systématique donnait
            #    l'impression d'une « correction » serveur après coup.
            # 2) Si le PJ courant a consommé son action standard pendant ce
            #    tour (attaque/soin/jet…), la rotation avance automatiquement ;
            #    le moteur joue les tours suivants (monstres, incapables)
            #    jusqu'au prochain PJ actif et clôture le combat
            #    (victoire/défaite) avec XP officielle + mémoire. Le LLM
            #    n'a PLUS à gérer la rotation : c'est garanti ici.
            # 3) Ces événements post-tour sont NARRÉS par le LLM (appel sans
            #    tools, résultats imposés) — mécanique d'abord, prose ensuite ;
            #    le bloc brut n'est plus que le repli si l'appel échoue.
            # 4) Si le combat continue, une ligne DÉTERMINISTE « au tour de
            #    X (joueur Y) de décider une action » est ajoutée : le LLM
            #    confondait les tours et demandait « que fait le monstre ? ».
            try:
                if events_pre and not _mecanique_deja_narree(
                    events_pre, result.narration
                ):
                    result.narration += (
                        "\n\n⚔️ _Résolution automatique du round :_\n\n"
                        + "\n\n".join(events_pre)
                    )
                if events_pre:
                    result.state_patches.extend(patches_pre)

                apres = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if apres.get("phase") == "combat":
                    action_consommee = any(
                        tc.get("name") in _ACTION_CONSOMMEE_TOOLS
                        for tc in result.tool_calls_trace
                    )
                    courant_est_pj = any(
                        str(p.get("nom") or "")
                        == str(apres.get("courant_tour_pour") or "")
                        for p in (apres.get("pj") or [])
                    )
                    # 🔧 Bêta (séquencement) : combat DÉBUTÉ pendant ce tour
                    # (`actif_avant` vide) → l'action du déclencheur est déjà
                    # consommée : on le MARQUE (déjà_agi) et on laisse la
                    # boucle jouer l'initiative DEPUIS LE HAUT — l'ancien
                    # `force=True` avançait le curseur au-delà du PREMIER
                    # combattant de l'initiative, qui perdait silencieusement
                    # son tour de round 1 (partie réelle : Kaelin, init 23).
                    combat_demarre_ce_tour = not bool(actif_avant)
                    if combat_demarre_ce_tour:
                        _acteur = (personnage or "").strip()
                        if _acteur:
                            _deja = [
                                str(x) for x in (apres.get("deja_agi") or [])
                            ]
                            if _acteur not in _deja and any(
                                str(e.get("nom") or "") == _acteur
                                for e in (apres.get("initiative") or [])
                            ):
                                _deja.append(_acteur)
                                apres["deja_agi"] = _deja
                                PartyState(
                                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                    partie_id=partie_id,
                                ).save(apres)
                    # `actif_avant` est vide quand le combat a DÉBUTÉ pendant
                    # ce tour (le joueur attaquait hors combat) — ce cas est
                    # traité ci-dessus via `deja_agi`, SANS forcer l'avance.
                    force = bool(
                        action_consommee
                        and courant_est_pj
                        and actif_avant
                        and str(apres.get("courant_tour_pour") or "")
                        == actif_avant
                    )
                    res_post = await _boucle_combat(
                        ctx,
                        force_avance=force,
                        timeout_secondes=cfg.game.combat_turn_timeout_seconds,
                    )
                    if res_post.events:
                        if res_post.combat_termine:                            # 🧾 Clôture de combat (victoire/défaite/mort) :
                            # bloc brut FACTUEL obligatoire. La narration LLM
                            # de ces moments inventait des issues
                            # contradictoires — « victoire écrasante » sur
                            # une DÉFAITE, PV max lus comme PV courants,
                            # PJ qualifié de « monstre géant » (partie
                            # 63f0838a : mort de Balrog mal racontée).
                            nar_post = ""
                        else:
                            # ⏱️ Borne dur : sur un llama.cpp local partagé avec
                            # d'autres applis (open-webui…), cet appel narratif
                            # peut attendre le slot pendant des minutes — sans
                            # cette limite le tour restait figé « en réflexion »
                            # après un combat. Au-delà de 90 s : repli bloc brut.
                            try:
                                # État POST-moteur : la narration doit
                                # refléter le plateau APRÈS la boucle
                                # (PV courants, ennemis détruits) —
                                # partie 263f82dc : narré d'après un
                                # snapshot périmé → ennemis « ressuscités ».
                                _etat_nar = PartyState(
                                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                    partie_id=partie_id,
                                ).load()
                                _heros_p, _ennemis_p = _camps_du_combat(
                                    _etat_nar)
                                nar_post = await asyncio.wait_for(
                                    _narrer_mecaniques_serveur(
                                        app,
                                        res_post.events,
                                        contexte=str(
                                            (_etat_nar.get("lieu") or {}).get("nom") or ""
                                        ),
                                        heros=_heros_p,
                                        ennemis=_ennemis_p,
                                        plateau=_resume_plateau(_etat_nar),
                                    ),
                                    timeout=90.0,
                                )
                            except (asyncio.TimeoutError, Exception) as e_nar:  # noqa: BLE001
                                print(
                                    "[dnd35] Narration mécaniques échouée/timeout "
                                    f"(repli bloc brut) : {e_nar}"
                                )
                                nar_post = ""
                        if nar_post:
                            result.narration += "\n\n" + nar_post
                        else:
                            result.narration += (
                                "\n\n⚔️ _Résolution automatique du tour :_\n\n"
                                + "\n\n".join(res_post.events)
                            )
                    # 🧹 À la CLÔTURE, le bloc « Dégâts appliqués
                    # automatiquement » ferait doublon avec la clôture
                    # (victoire/XP + « tous les ennemis à terre ») et
                    # répéterait « ☠️ DÉTRUIT » (partie 5b4e2bbe : tour de
                    # mort trop long). On le retire.
                    if res_post.combat_termine and _bloc_degats_auto:
                        if _bloc_degats_auto in result.narration:
                            result.narration = result.narration.replace(
                                _bloc_degats_auto, "").strip()
                    if res_post.patches:
                        result.state_patches.extend(res_post.patches)
                    if res_post.combat_termine:
                        print(
                            "[dnd35] Combat clôturé par le moteur serveur "
                            f"({res_post.combat_termine})."
                        )
                    apres = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    # Retire INCONDITIONNELLEMENT les copies LLM du bandeau
                    # « Au tour de » (PV inventés + « Que décidez-vous de
                    # faire ? ») : même quand le combat se CLÔTURE dans ce
                    # tour (mort du PJ → phase exploration), la copie LLM
                    # ne doit pas fuir les PV du monstre (partie 5f3e31c9,
                    # msg 20 : bandeau « 28/32 PV » resté après GAME OVER).
                    result.narration = _re_mod.sub(
                        r"\n{3,}", "\n\n",
                        _RE_AUTOUR_STRIP.sub(
                            "", result.narration
                        ).strip(),
                    )
                    # ⚔️ Ligne de relance DÉTERMINISTE : quand la mécanique a
                    # fait avancer la rotation jusqu'à un PJ, la table doit
                    # savoir QUI décide maintenant — sans dépendre du LLM
                    # (qui « demandait » au joueur ce que faisait un monstre).
                    if apres.get("phase") == "combat":
                        actif_suivant = str(
                            apres.get("courant_tour_pour") or ""
                        ).strip()
                        pj_suivant = next(
                            (p for p in (apres.get("pj") or [])
                             if str(p.get("nom") or "") == actif_suivant),
                            None,
                        )
                        if pj_suivant is not None and actif_suivant:
                            # Liste DÉTERMINISTE des ennemis vivants : sans
                            # elle, le MJ inventait des adversaires (« squelette
                            # géant ») ou attaquait des cadavres au tour suivant.
                            # Noms seuls : les PV exacts des monstres sont de
                            # l'information de MJ, pas du joueur.
                            vivants = [
                                str(m.get("nom") or "")
                                for m in (apres.get("monstres_combat") or [])
                                if "Détruit" not in (m.get("conditions") or [])
                                and int(m.get("pv", 1) or 0) > 0
                            ]
                            result.narration += (
                                f"\n\n⚔️ **Au tour de {actif_suivant}** "
                                f"(joueur {pj_suivant.get('joueur')}) de "
                                "décider une action."
                                + (
                                    f"\n🎯 Ennemis vivants : "
                                    + ", ".join(vivants) + "."
                                    if vivants
                                    else "\n🎯 Aucun ennemi vivant restant."
                                )
                            )
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Moteur de combat post-tour échoué (ignoré) : {e}")

            # 5quater. 🖼️ Illustrations des monstres en jeu — DÉPORTÉES EN
            # ARRIÈRE-PLAN. La génération ComfyUI (jusqu'à ~100 s par lot)
            # ne doit ni prolonger le statut du tour ni retarder le dm final :
            # la tâche démarre ici, persiste les URLs (image_url + journal des
            # rencontres) et pousse les patches au fil de l'eau ; la narration
            # et les mises à jour de PV partent donc immédiatement.
            async def _illustrer_monstres_arriere_plan() -> None:
                try:
                    # Toggle images de MONSTRES (config.yaml × maître GUI) :
                    # coupé → aucune génération ni push de portrait.
                    if not cfg.image.effective("monstres"):
                        return
                    _t0 = time.time()
                    _vus: set[str] = set()
                    _nouvelles_rencontres: list[tuple[str, str]] = []
                    urls_par_type: dict[str, str] = {}
                    from .tools.monstres import _type_nom
                    etat_img = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    for mo in etat_img.get("monstres_combat") or []:
                        nom_mo = str((mo or {}).get("nom") or "").strip()
                        # Clé de dédup = NOM DE TYPE (« Gobelin (2) » == « Gobelin ») :
                        # un groupe de monstres identiques partage UNE seule illustration.
                        cle_mo = _type_nom(nom_mo).lower()
                        if not nom_mo or cle_mo in _vus:
                            continue
                        _vus.add(cle_mo)
                        if time.time() - _t0 > 180:
                            # Budget élargi (180 s) : l'arbitrage GPU peut
                            # faire ATTENDRE une image avant soumission (tour
                            # LLM en cours) — ce temps d'attente consomme le
                            # budget mais ne charge pas le PC.
                            print("[dnd35] Budget images monstres atteint — "
                                  "le reste sera généré au tour suivant.")
                            break
                        try:
                            url_img = await image_pour(ctx, nom_mo)
                        except Exception as e:                   # noqa: BLE001
                            print(f"[dnd35] Image {nom_mo} échouée (ignoré) : {e}")
                            continue
                        if url_img:
                            _nouvelles_rencontres.append((_type_nom(nom_mo), url_img))
                            urls_par_type[cle_mo] = url_img
                            # Galerie en direct, sans attendre la persistance.
                            await on_event({
                                "type": "state_patches",
                                "patches": [{"image_monstre": url_img}],
                            })
                    if not urls_par_type:
                        return
                    # Persistance : re-load → patch ciblé → save (la fenêtre de
                    # course avec un tour concurrent est réduite au save).
                    st_img = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    )
                    etat_img = st_img.load()
                    touche = False
                    for mo2 in etat_img.get("monstres_combat") or []:
                        cle2 = _type_nom(str((mo2 or {}).get("nom") or "")).lower()
                        url2 = urls_par_type.get(cle2)
                        # On applique la MÊME image à tous les monstres du même
                        # type (Gobelin, Gobelin (2), Gobelin (3)…).
                        if url2 and (mo2 or {}).get("image_url") != url2:
                            mo2["image_url"] = url2
                            touche = True
                    if _nouvelles_rencontres:
                        from .tools.monstres import _fusionner_rencontres
                        if _fusionner_rencontres(etat_img, _nouvelles_rencontres):
                            touche = True
                    if touche:
                        st_img.save(etat_img)
                except Exception as e:                           # noqa: BLE001
                    print(f"[dnd35] Illustrations arrière-plan échouées "
                          f"(ignoré) : {e}")

            _t_img = asyncio.create_task(_illustrer_monstres_arriere_plan())
            _bg_tasks.add(_t_img)
            _t_img.add_done_callback(_bg_tasks.discard)

            # 5quater-bis. 🖼️ Scènes cousues d'avance par univers pilote
            # (Laelith) : la galerie « Scènes » s'alimente automatiquement
            # quand le groupe change de lieu/un moment marquant est narré.
            # On ne sert QUE des prégénérées (cache, aucun appel ComfyUI) —
            # zéro latence, zéro risque. `memoire.scene_hook_dernier` mémorise
            # le dernier slug servi pour ne pas réafficher la même image à
            # chaque tour.
            try:
                from .tools.cartes import serve_scene_si_pregen
                etat_sc = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_sc.get("phase") != "combat" and cfg.image.effective(
                    "scenes"
                ):
                    mem_sc = etat_sc.setdefault("memoire", {})
                    pos_sc = mem_sc.get("position") or {}
                    lieu_sc = str(pos_sc.get("lieu") or "").strip()
                    # Champ de recherche : le lieu courant, sinon l'objectif, sinon le pitch.
                    src_sc = str((etat_sc.get("quete") or {}).get("source") or "")
                    sid_sc = src_sc.split("]", 1)[0].lstrip("[").strip() or ""
                    cible_sc = lieu_sc or str(
                        (etat_sc.get("quete") or {}).get("pitch") or ""
                    ).strip()
                    if lieu_sc and cible_sc:
                        url_sc = serve_scene_si_pregen(
                            ctx, lieu_sc, cible_sc, sid=sid_sc
                        )
                        if url_sc and mem_sc.get("scene_hook_dernier") != url_sc:
                            mem_sc["scene_hook_dernier"] = url_sc
                            PartyState(
                                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                partie_id=partie_id,
                            ).save(etat_sc)
                            result.state_patches.append({"image_scene": url_sc})
                            cb_sc = getattr(ctx, "on_event", None)
                            if cb_sc is not None:
                                try:
                                    await cb_sc({
                                        "type": "image",
                                        "usage": "lieu",
                                        "image": url_sc,
                                        "msg": f"🖼️ Scène (cache) : {lieu_sc}",
                                    })
                                except Exception:                     # noqa: BLE001
                                    pass
                            print(f"[dnd35] Scène prégénérée servie : {lieu_sc} → {url_sc}")
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Hook scène prégénérée échoué (ignoré) : {e}")

            # 5ter. ⚔️ Rattrapage combat narré EN PROSE mais non engagé.
            # Le petit modèle écrit parfois « Le combat commence ! Le zombie
            # bondit et t'attaque… » avec les jets/dégâts dans la narration,
            # SANS appeler `engager_combat` → l'ordre d'initiative et le suivi
            # des PV restaient absents (phase exploration, side panel vide).
            # On détecte la prose de combat ET on engage la mécanique
            # officielle, pour que l'ordre de combat s'affiche et que la
            # rotation suive les règles 3.5.
            # Le rattrapage reste armé même si d'autres tools ont tourné dans
            # le tour (ex. `lancer_degats` hors combat : les dégâts d'un
            # monstre non suivi partaient dans le vide) — seuls
            # `engager_combat`/`combat_ajouter_combattant` prouvent que le
            # combat EST officiel.
            # Seul un engagement RÉUSSI prouve que le combat est officiel :
            # un appel EN ERREUR (partie 5b4e2bbe : `engager_combat` a planté
            # — « 'list' object has no attribute 'split' » — sans engager de
            # combat) ne doit PAS désarmer ce rattrapage, sinon la rencontre
            # reste 100 % prose (phase exploration, aucun PV suivi).
            if not any(
                tc.get("name") in ("engager_combat", "combat_ajouter_combattant")
                and tc.get("ok")
                for tc in result.tool_calls_trace
            ):
                etat_detect = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_detect.get("phase") != "combat":
                    try:
                        # Déclencheurs élargis : prose du MJ (marqueurs /
                        # dégâts chiffrés) OU attaque armée DÉCLARÉE par le
                        # joueur (partie 4d4b4557 : « J'attaque le loup avec
                        # ma hache » est resté 100 % prose — « bondit sur
                        # vous » absent des marqueurs, aucun dégât chiffré —
                        # alors que le loup gris est au bestiaire : aucun
                        # engager_combat, aucun dé, loup intouchable).
                        _attaque_joueur_5t = bool(
                            _ATTAQUE_JOUEUR_RE.search(text or "")
                        )
                        # Fix engagement auto : hostilité imminente narrée par
                        # le MJ (« prêt à attaquer », « vous vise »… cf.
                        # _HOSTILITE_IMMINENTE_RE) — le joueur n'a pas encore
                        # frappé mais la créature s'apprête à agresser : on
                        # engage la mécanique officielle au lieu d'attendre
                        # une deuxième attaque (partie 5b4e2bbe).
                        _menace_imminente_5t = bool(
                            _HOSTILITE_IMMINENTE_RE.search(
                                result.narration or "")
                        )
                        # 🛡️ m20 (audit) : les noms de CLASSES et de RACES
                        # des PJ (« Voleur », « Clerc », « Halfelin »…) sont
                        # exclus — la prose « Korr (Voleur niveau 1) » faisait
                        # engager un monstre « Voleur » : le groupe se
                        # battait contre son propre compagnon.
                        _noms_pj_m20 = [
                            str(p.get("nom") or "")
                            for p in (etat_detect.get("pj") or [])
                        ]
                        _types = _detecter_combat_prose(
                            str(cfg.abs(cfg.paths.data_dir)),
                            result.narration or "",
                            etat_detect,
                            forcer_declencheur=(
                                _attaque_joueur_5t or _menace_imminente_5t
                            ),
                            exclure=_noms_pj_m20,
                        )
                        if _types:
                            from .tools.base import (
                                _TOOL_REGISTRY, invoke_tool,
                            )
                            # Défense en profondeur : on n'engage QUE les
                            # monstres qui résolvent réellement dans le
                            # bestiaire (un nom détecté peut devenir obsolète
                            # si le bestiaire a changé entre détection et
                            # engagement). Sans cette passe, `engager_combat`
                            # REFUSAIT TOUT le rattrapage dès qu'un nom était
                            # inconnu — le « combat » narré restait en prose
                            # sans mécanique (partie ee5684fe).
                            from .tools.monstres import _find_monstre_strict
                            resolus = [
                                nom for nom in _types
                                if _find_monstre_strict(ctx, nom) is not None
                            ]
                            spec = _TOOL_REGISTRY.get("engager_combat")
                            if spec is not None and resolus:
                                tr = await invoke_tool(
                                    spec, ctx,
                                    {"monstres": ", ".join(resolus)},
                                )
                                if tr is not None and not (
                                    tr.text.startswith("⛔")
                                    or tr.text.startswith("❌")
                                ):
                                    if tr.state_patch:
                                        result.state_patches.append(tr.state_patch)
                                    # Ajoute l'initiative officielle à la
                                    # narration pour que la table la voie.
                                    result.narration += (
                                        "\n\n⚙️ _Le serveur a régularisé ce "
                                        "combat porté en prose — initiative "
                                        "officielle engagée pour des "
                                        "monstres du bestiaire._\n\n"
                                        + "".join(
                                            line + "\n"
                                            for line in tr.text.splitlines()
                                        )
                                    )
                                    print(
                                        f"[dnd35] Combat prose rattrapé : "
                                        f"engager_combat({', '.join(resolus)})"
                                    )
                                    # Le joueur avait DÉCLARÉ une attaque
                                    # armée : résous-la déterministement si
                                    # c'est déjà son tour d'initiative
                                    # (sinon le bandeau « au tour de » du
                                    # prochain tour re-prendra la déclaration
                                    # via 5bis-a). Sinon, l'attaque déclarée
                                    # resterait en prose sans effet (4d4b4557 :
                                    # deux attaques au loup, PV intacts).
                                    if _attaque_joueur_5t:
                                        _etat_att = PartyState(
                                            data_dir=str(
                                                cfg.abs(cfg.paths.data_dir)),
                                            partie_id=partie_id,
                                        ).load()
                                        _pjs_att = _etat_att.get("pj") or []
                                        _nom_pj_att = next(
                                            (
                                                str(_p.get("nom") or "")
                                                for _p in _pjs_att
                                                if str(
                                                    _p.get("joueur") or ""
                                                ).strip().lower()
                                                == str(
                                                    ctx.joueur or ""
                                                ).strip().lower()
                                            ),
                                            "",
                                        )
                                        if not _nom_pj_att and len(
                                            _pjs_att
                                        ) == 1:
                                            _nom_pj_att = str(
                                                (_pjs_att[0] or {}).get("nom")
                                                or ""
                                            )
                                        # 🛡️ B6/B19 : le personnage incarné
                                        # par CETTE connexion prime sur
                                        # « le premier PJ du compte » — avec
                                        # deux PJ sur un même compte, le
                                        # rattrapage résolvait l'attaque de
                                        # l'autre personnage.
                                        if personnage and personnage in [
                                            str(_p.get("nom") or "")
                                            for _p in _pjs_att
                                        ]:
                                            _nom_pj_att = personnage
                                        if (
                                            _nom_pj_att
                                            and str(
                                                _etat_att.get(
                                                    "courant_tour_pour"
                                                ) or ""
                                            ) == _nom_pj_att
                                        ):
                                            _note_att = await _attaque_pj_sans_jet(
                                                orch, ctx, on_event, result,
                                                _nom_pj_att,
                                            )
                                            if _note_att:
                                                result.narration += (
                                                    "\n\n⚙️ _Attaque déclarée "
                                                    "résolue par le "
                                                    "serveur :_\n\n"
                                                    + _note_att
                                                )
                                                print(
                                                    "[dnd35] Attaque PJ "
                                                    f"{_nom_pj_att} résolue "
                                                    "(régularisation 5ter)."
                                                )
                                                # L'action du PJ est
                                                # consommée : avance la
                                                # rotation et joue les tours
                                                # (monstres, incapables)
                                                # jusqu'au prochain PJ — le
                                                # moteur post-tour est déjà
                                                # passé (phase exploration à
                                                # ce moment-là).
                                                try:
                                                    _res_att = (
                                                        await _boucle_combat(
                                                            ctx,
                                                            force_avance=(
                                                                True
                                                            ),
                                                            timeout_secondes=(
                                                                cfg.game.combat_turn_timeout_seconds
                                                            ),
                                                        )
                                                    )
                                                    if _res_att.events:
                                                        result.narration += (
                                                            "\n\n⚔️ _Résolution "
                                                            "automatique du "
                                                            "tour :_\n\n"
                                                            + "\n\n".join(
                                                                _res_att.events
                                                            )
                                                        )
                                                    if _res_att.patches:
                                                        result.state_patches.extend(
                                                            _res_att.patches
                                                        )
                                                    _etat_fin_att = PartyState(
                                                        data_dir=str(
                                                            cfg.abs(
                                                                cfg.paths.data_dir
                                                            )
                                                        ),
                                                        partie_id=partie_id,
                                                    ).load()
                                                    if _etat_fin_att.get(
                                                        "phase"
                                                    ) == "combat":
                                                        _suiv_att = str(
                                                            _etat_fin_att.get(
                                                                "courant_tour_pour"
                                                            ) or ""
                                                        )
                                                        _pj_suiv = next(
                                                            (
                                                                _p
                                                                for _p in (
                                                                    _etat_fin_att.get(
                                                                        "pj"
                                                                    ) or []
                                                                )
                                                                if str(
                                                                    _p.get(
                                                                        "nom"
                                                                    ) or ""
                                                                ) == _suiv_att
                                                            ),
                                                            None,
                                                        )
                                                        if (
                                                            _pj_suiv
                                                            is not None
                                                            and _suiv_att
                                                        ):
                                                            result.narration += (
                                                                f"\n\n⚔️ **Au tour "
                                                                f"de {_suiv_att}** "
                                                                f"(joueur "
                                                                f"{_pj_suiv.get('joueur')}) "
                                                                "de décider une "
                                                                "action."
                                                            )
                                                except Exception as e_att:
                                                    print(
                                                        "[dnd35] Rotation "
                                                        "post-attaque 5ter "
                                                        f"échouée (ignoré) : "
                                                        f"{e_att}"
                                                    )
                                elif tr is not None:
                                    # Refus (ex. rencontre écrasante, monstre
                                    # refusé) : NE PAS avaler l'échec en
                                    # silence — la table doit savoir que le
                                    # combat narré n'est PAS officiel (sinon
                                    # le joueur continue à « jouer » une
                                    # scène sans panneau ni initiative).
                                    result.narration += (
                                        "\n\n⚙️ _Le serveur n'a pas pu engager "
                                        "ce combat narré en prose._\n\n"
                                        + tr.text
                                        + "\n\n_Reprends l'action hors "
                                        "initiative : ce combat narré n'existe "
                                        "pas mécaniquement._"
                                    )
                                    print(
                                        f"[dnd35] Combat prose REFUSÉ par "
                                        f"engager_combat({', '.join(resolus)}) : "
                                        f"{tr.text[:120]}"
                                    )
                            elif _types and spec is not None:
                                print(
                                    f"[dnd35] Combat prose ignoré : aucun "
                                    f"monstre détecté ne résout au bestiaire "
                                    f"({', '.join(_types)})"
                                )
                            else:
                                print(
                                    f"[dnd35] Combat prose ignoré : registre "
                                    f"engager_combat absent"
                                )
                            # Recharge l'état pour que l'image soit générée
                            # pour les monstres désormais suivis.
                            apres = PartyState(
                                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                partie_id=partie_id,
                            ).load()
                    except Exception as e:                             # noqa: BLE001
                        print(f"[dnd35] Rattrapage combat prose échoué "
                              f"(ignoré) : {e}")

            # 5quater. 🛠️ Correction des non-conformités signalées au test —
            # le petit modèle 9B narre en prose sans appeler les outils
            # spécifiques de la phase (chargement de scénario à l'ouverture,
            # exploration de donjon, inventaire). On ré-invoque une fois le MJ
            # avec une consigne très ferme ; le bloc rejoue uniquement s'il
            # est SÛR que l'action n'a PAS été résolue (outils de phase absents
            # alors que l'état l'exigeait encore).
            try:
                _etat_rejouer = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                _outils_appeles = {
                    str(tc.get("name") or "") and str(tc.get("name"))
                    for tc in result.tool_calls_trace
                }
                _outils_appeles.discard("")

                # --- 5quater-a. Ouverture SANS scénario chargé.
                # Phase "opening" + quête non choisie : le MJ doit avoir
                # appelé `scenarios_laelith_charger`. S'il n'a fait que de la
                # narration (memoire_mission / intrigue à la place), on le
                # force à charger le scénario correspondant à l'id/titre
                # mentionné.
                phase_ouverture = (
                    str(_etat_rejouer.get("phase") or "").strip().lower()
                    in ("opening", "opening_complete")
                )
                quete_pas_chargee = not str(
                    (_etat_rejouer.get("quete") or {}).get("titre") or ""
                ).strip()
                scena_pas_appele = not (
                    _outils_appeles & _SCENARIO_LOAD_TOOLS
                )
                # ⚠️ On ne force le chargement QUE si le joueur DEMANDE
                # explicitement de choisir/charger un scénario (le tour de
                # phase "load"). Pendant la pure création de personnages (aussi
                # en phase "opening"), on ne déclenche RIEN : il est normal que
                # la quête ne soit pas encore posée.
                _demande_scenario = bool(
                    _SCENARIO_CHOICE_RE.search(text or "")
                )
                if (phase_ouverture and quete_pas_chargee and scena_pas_appele
                        and _demande_scenario):
                    _scena_match = (
                        _derive_scenario_id(
                            str(cfg.abs(cfg.paths.data_dir)),
                            (text or "") + " " + (result.narration or ""),
                        )
                        or _derive_scenario_id(
                            str(cfg.abs(cfg.paths.data_dir)),
                            result.narration or "",
                        )
                    )
                    # 1er essai : on ré-invoque le MJ pour qu'il charge le
                    # scénario par lui-même (trace propre côté table).
                    _obj_open = (
                        "⚠️ ERREUR système : le MJ devait CHARGER le "
                        "scénario choisi avant de narrer l'ouverture. "
                        "Appelle MAINTENANT `scenarios_laelith_charger` "
                        f"avec scenario_id='{_scena_match or '_INFER_'}'. "
                        "Appelle impérativement l'outil (jamais à la place "
                        "`memoire_mission`/`memoire_intrigue`), puis narre "
                        "la scène d'ouverture en 2-4 paragraphes."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_open,
                                            "ouverture scénario",
on_status=on_status)
                    # 2e essai : DÉTERMINISTE. Le petit modèle 9B refuse
                    # parfois catégoriquement d'appeler `scenarios_laelith_`
                    # (il confond avec `memoire_mission`/`etat_partie_patch`).
                    # On charge alors le scénario DIRECTEMENT côté serveur via
                    # le registre d'outils, comme le bloc 5ter fait pour
                    # `engager_combat` — la quête est ainsi TOUJOURS posée.
                    _etat_apres_open = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    _quere_apres = str(
                        (_etat_apres_open.get("quete") or {}).get("titre")
                        or ""
                    ).strip()
                    if (not _quere_apres and _scena_match):
                        from .tools.base import (
                            _TOOL_REGISTRY, invoke_tool,
                        )
                        _spec_c = _TOOL_REGISTRY.get("scenarios_laelith_charger")
                        if _spec_c is not None:
                            _tr_c = await invoke_tool(
                                _spec_c, ctx, {"scenario_id": _scena_match},
                            )
                            if _tr_c is not None:
                                if _tr_c.state_patch:
                                    result.state_patches.append(_tr_c.state_patch)
                                # Le tool renvoie `quete` dans son state_patch,
                                # que le CLIENT applique normalement (persisté
                                # ici par le frontend). Comme le serveur a
                                # injecté le chargement, on persiste AUSSI la
                                # quête directement dans le fichier partie pour
                                # que le side panel et les phases suivantes la
                                # voient.
                                _patch_quete = (
                                    (_tr_c.state_patch or {}).get("quete")
                                    or {}
                                )
                                if _patch_quete.get("titre"):
                                    try:
                                        _st_q = PartyState(
                                            data_dir=str(
                                                cfg.abs(cfg.paths.data_dir)),
                                            partie_id=partie_id,
                                        )
                                        _etat_q = _st_q.load()
                                        _etat_q["quete"] = dict(_patch_quete)
                                        _st_q.save(_etat_q)
                                        print(
                                            f"[dnd35] Quête persistée : "
                                            f"{_patch_quete.get('titre')}"
                                        )
                                    except Exception as _e:            # noqa: BLE001
                                        print(
                                            f"[dnd35] Persistance quête "
                                            f"échouée : {_e}"
                                        )
                                if _tr_c.text and not (
                                    _tr_c.text.startswith("⛔")
                                    or _tr_c.text.startswith("❌")
                                ):
                                    result.narration += (
                                        "\n\n⚙️ _Le serveur a chargé le "
                                        "scénario « "
                                        + str(_scena_match)
                                        + " » — quête posée officiellement._"
                                    )
                                    print(
                                        f"[dnd35] Scénario chargé par le "
                                        f"serveur : "
                                        f"scenarios_laelith_charger("
                                        f"{_scena_match})"
                                    )
                    # Recharge l'état pour les phases suivantes.
                    _etat_rejouer = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()

                # --- 5quater-b. Exploration narrée EN PROSE sans outil.
                # Phase d'exploration + déplacement annoncé mais aucun outil
                # `carte_donjon_*` appelé → on rejoue une fois.
                phase_explo = str(
                    _etat_rejouer.get("phase") or ""
                ).strip().lower() in ("exploration", "voyage", "roleplay")
                explo_pas_appele = not (_outils_appeles & _EXPLORATION_TOOLS)
                if phase_explo and explo_pas_appele and _estnarration_explo(
                        (text or "") + " " + (result.narration or "")):
                    _outil_qb = _suggestion_outil_explo(
                        _etat_rejouer,
                        (text or "") + " " + (result.narration or ""),
                    )
                    # None = mouvement DANS la salle / prose sans intention
                    # spatiale : la narration du MJ est correcte, ne force
                    # RIEN (partie 6746fc6c : « centre de la salle »
                    # provoquait un changement de salle vers l'est).
                    if _outil_qb:
                        _obj_explo = (
                            "⚠️ ERREUR système : le groupe se déplace / "
                            "explore mais aucun outil de déplacement n'a été "
                            f"appelé. Utilise MAINTENANT {_outil_qb}. NE te "
                            "contente PAS de narrer : appelle l'outil puis "
                            "narre le résultat d'après sa sortie officielle."
                        )
                        await _rejoue_correctif(orch, messages, ctx, result,
                                                on_event, _obj_explo,
                                                "exploration donjon",
on_status=on_status)

                # --- 5quater-c. Acquisition d'objet non enregistrée.
                # Le MJ (ou le joueur) annonce la récupération/le don d'un
                # objet mais n'appelle aucun outil d'inventaire → l'objet reste
                # introuvable côté serveur/side panel. On ré-invoque une fois.
                # ⚠️ PAS sur une simple mention de possession : la narration
                # tisse le contenu du sac rappelé par le récap (« la fiole
                # glisse dans votre sac ») et le rejeu tournait 6 tours sur 8,
                # ré-ajoutant des objets (partie 6746fc6c : fiole ×6).
                inv_pas_appele = not (_outils_appeles & _INVENTAIRE_TOOLS)
                if inv_pas_appele and _rejeu_inventaire_necessaire(
                        text or "", result.narration or "",
                        ctx, _etat_rejouer):
                    # 📦 Gain chiffré dans un bloc « Inventaire mis à jour » →
                    # application DÉTERMINISTE (le rejeu LLM ne produisait pas
                    # l'appel — partie réelle : loot narré jamais crédité).
                    # 🛡️ Chantier 2 : le PJ actif est crédité, pas le premier
                    # de la liste.
                    _note_inv = await _appliquer_gains_inventaire_narres(
                        orch, ctx, on_event, result, nom_pj=actif_avant)
                    if _note_inv:
                        result.narration = (
                            (result.narration or "").rstrip()
                            + "\n\n" + _note_inv
                        ).strip()
                        print("[dnd35] Rattrapage inventaire déterministe "
                              "(gains narrés appliqués).")
                    else:
                        _obj_inv = (
                            "⚠️ ERREUR système : l'objet gagné/récupéré/donné "
                            "n'a pas été enregistré. Appelle MAINTENANT "
                            "`inventaire_ajouter` (nom d'un PJ, nom, quantité, "
                            "poids, portee) pour persister l'objet, puis narre "
                            "la suite. PORTÉE : don de PNJ ou objet de "
                            "l'aventure (potion, fiole, or donné, carte, clé, "
                            "parchemin, lettre) → portee=\"quete\" ; achat en "
                            "marchand, arme, armure, trésor → "
                            "portee=\"permanent\". NE narrate PAS "
                            "l'acquisition sans appeler l'outil d'inventaire."
                        )
                        await _rejoue_correctif(orch, messages, ctx, result,
                                                on_event, _obj_inv,
                                                "inventaire objet",
on_status=on_status)

                # --- 5quater-f. 🪙 Trésor canonique de salle → effet mécanique
                # (chantier 3). Les 197 salles de scénario portent un `tresor`
                # rédigé à la main avec des montants en clair, mais le MJ le
                # narre sans jamais écrire « +200 po » : le rattrapage
                # générique ne capte que « +N pièces ». Le serveur crédite
                # LUI-MÊME le montant canonique au PJ actif qui fouille —
                # une seule fois par salle (marqueur `tresor_pris`).
                if inv_pas_appele:
                    try:
                        _note_tres = await _appliquer_tresor_canonique(
                            orch, ctx, on_event, result, nom_pj=actif_avant)
                        if _note_tres:
                            result.narration = (
                                (result.narration or "").rstrip()
                                + "\n\n" + _note_tres
                            ).strip()
                            print("[dnd35] Trésor canonique de salle crédité "
                                  f"({actif_avant}).")
                    except Exception as e_tres:                 # noqa: BLE001
                        print(f"[dnd35] Rattrapage trésor canonique échoué "
                              f"(ignoré) : {e_tres}")

                # --- 5quater-d2. ⚔️ Engagement de combat NARRÉ sans outil
                # (phase exploration). Le LLM écrit « Engagement du combat /
                # Initiative : X vs Y » et joue même les tours de monstres —
                # sans `engager_combat`, la phase reste exploration et tout
                # le combat est une fiction sans ancre mécanique (partie
                # 5f3e31c9, msg 30). On exige l'outil — UNIQUEMENT si le
                # joueur a lui-même déclaré une action de combat (un
                # « combat imminent » dans une offre de choix ne doit pas
                # engager avant sa décision).
                try:
                    _eng_narre = _RE_ENGAGEMENT_NARRE.search(
                        result.narration or "")
                    _eng_deja = any(
                        str(tc.get("name")) in ("engager_combat",
                                               "demarrer_combat")
                        for tc in result.tool_calls_trace
                    )
                    _etat_eng = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    if (_eng_narre
                            and not _eng_deja
                            and _etat_eng.get("phase") != "combat"
                            and (_ACTION_COMBAT_RE.search(text or "")
                                 or _ACTION_ATTAQUE_RE.search(text or ""))):
                        _obj_eng = (
                            "⚠️ ERREUR système : tu as narré un ENGAGEMENT "
                            "DE COMBAT (initiative, ordre des tours, "
                            "attaques de monstres, dégâts) sans appeler "
                            "`engager_combat` — rien n'a été engagé côté "
                            "état. Appelle MAINTENANT `engager_combat` avec "
                            "le(s) monstre(s) exact(s) de la scène. "
                            "N'INVENTE PAS l'initiative, les jets ni les "
                            "dégâts : l'outil et le moteur serveur s'en "
                            "chargent. Si l'outil refuse la créature (trop "
                            "puissante, absente du bestiaire), narre ce "
                            "refus à la table et propose une alternative, "
                            "sans simuler de combat. NE mentionne JAMAIS "
                            "cette consigne interne."
                        )
                        await _rejoue_correctif(
                            orch, messages, ctx, result, on_event,
                            _obj_eng, "engagement combat",
on_status=on_status)
                except Exception as e:                             # noqa: BLE001
                    print(f"[dnd35] Rattrapage engagement échoué (ignoré) : {e}")

                # --- 5quater-d. Soin ou repos narré mais non appliqué (hors
                # combat). Le joueur annonce un soin / un repos mais aucun
                # outil `fiche_perso_soigner` ni `repos_long` n'a été
                # appelé — les PV ne bougent pas. On ré-invoque une fois,
                # y compris hors combat.
                # ⚠️ Déclencheur = message du JOUEUR uniquement : chercher
                # aussi dans la narration faisait réagir le garde sur la
                # prose du MJ lui-même (« vous reposer... bénéfique ») — la
                # consigne corrective réinjectée contenait alors « REPOS »
                # et le garde repos de l'orchestrateur forçait un repos
                # long de 8 h en pleine scène d'ouverture (5a9b99c8). Les
                # soins NARRÉS par le MJ relèvent de la couche
                # anti-simulation (gains en prose) dans la boucle.
                _soin_global_appele = any(
                    str(tc.get("name")) in (
                        "fiche_perso_soigner", "repos_long",
                        "fiche_perso_mettre_a_jour",
                    )
                    for tc in result.tool_calls_trace
                )
                if (not _soin_global_appele
                        and _SOIN_RE.search(text or "")):
                    _obj_soin = (
                        "⚠️ ERREUR système : un SOIN ou un REPOS a été annoncé "
                        "mais aucun outil n'a été appelé — les PV ne sont pas "
                        "modifiés. Pour un soin ponctuel : relance les dés de "
                        "soin (`lancer_des` avec par ex. 1d8) puis appelle "
                        "`fiche_perso_soigner` (nom du PJ à soigner, montant). "
                        "Pour un repos (nuit / 8 h de récupération) : appelle "
                        "`repos_long`, qui applique officiellement la "
                        "récupération des PV et des sorts. NE narrate PAS la "
                        "guérison sans appeler l'outil."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_soin, "soins",
on_status=on_status)

                # --- 5quater-d-bis. 💚 Soin NARRÉ PAR LE MJ appliqué au
                # serveur (hors combat, hors résurrection). La narration
                # décrit une guérison (« vos blessures se referment, vous
                # récupérez 5 points de vie ») mais AUCUN outil de soin n'a
                # été appelé — l'anti-simulation D1bis rejoue la scène à
                # budget limité SANS jamais persister les PV (partie
                # 5b4e2bbe : talisman +5 PV narré, fiche restée 2/16). On
                # applique le MONTANT ANNONCÉ via le registre d'outils.
                try:
                    if (not _soin_global_appele
                            and str(etat_avant.get("phase")) != "combat"
                            and not (etat_avant.get("game_over") or False)
                            and "Soin appliqué par le serveur" not in
                            (result.narration or "")
                            and "Soin résolu par le serveur" not in
                            (result.narration or "")):
                        _nar_s = result.narration or ""
                        _pj_s = [
                            p for p in (etat_avant.get("pj") or [])
                            if isinstance(p, dict)
                        ]
                        _nom_cite = next(
                            (str(p.get("nom")) for p in _pj_s
                             if p.get("nom")
                             and _norm_nom_objet(str(p.get("nom")))
                             in _norm_nom_objet(_nar_s)),
                            "",
                        )
                        if not _nom_cite and len(_pj_s) == 1:
                            _nom_cite = str(_pj_s[0].get("nom") or "")
                        if not _nom_cite:
                            _nom_cite = str(actif_avant or "")
                        if _nom_cite:
                            _txt_s = await _appliquer_soins_oublies(
                                orch, result, ctx, on_event, _nom_cite, text)
                            if _txt_s:
                                result.narration += "\n\n" + _txt_s
                                print(
                                    "[dnd35] Soins narrés (MJ) appliqués "
                                    "automatiquement (tools serveur).")
                except Exception as e_s:                            # noqa: BLE001
                    print(
                        "[dnd35] Rattrapage soins narrés échoué "
                        f"(ignoré) : {e_s}")

                # --- 5quater-e. ✨ Résurrection narrée SANS tool. Après un
                # GAME OVER, le MJ narre le retour à la vie (« repos long,
                # 16 PV sur 17 ») mais AUCUN tool ne peut lever « Mort »
                # (repos_long ignore pv<0, soigner ne retire pas Mort) :
                # l'état restait mort alors que la narration décrivait un
                # personnage debout (partie 5f3e31c9, msgs 21-24).
                try:
                    # 🛡️ B7 : le butin narré entre réellement en inventaire.
                    _txt_butin = await _appliquer_objets_narres(
                        orch, ctx, on_event, result, actif_avant,
                        result.narration or "",
                    )
                    if _txt_butin:
                        result.narration += "\n\n" + _txt_butin
                        print("[dnd35] Objets narrés ajoutés à l'inventaire "
                              f"({actif_avant}).")
                except Exception as e_butin:                     # noqa: BLE001
                    print(f"[dnd35] Rattrapage objets narrés échoué "
                          f"(ignoré) : {e_butin}")

                try:
                    _txt_res = await _ressusciter_pj_oublie(
                        orch, ctx, on_event, result)
                    if _txt_res:
                        result.narration += "\n\n" + _txt_res
                        print(
                            "[dnd35] Résurrection narrée appliquée "
                            "(tools serveur)."
                        )
                except Exception as e:                             # noqa: BLE001
                    print(
                        "[dnd35] Rattrapage résurrection échoué "
                        f"(ignoré) : {e}"
                    )

                # --- 5quater-f. 💥 Dégâts annoncés en prose contre un PJ
                # sans tool (toute phase hors combat) : le monstre narré
                # blessait sans toucher l'état (partie 5f3e31c9, msg 30 :
                # « vous subissez 12 dégâts » restés sans effet, PV intacts).
                try:
                    _txt_dmg = await _appliquer_degats_pj_narres(
                        orch, ctx, on_event, result, actif_avant)
                    if _txt_dmg:
                        result.narration += "\n\n" + _txt_dmg
                        print(
                            "[dnd35] Dégâts PJ narrés appliqués "
                            "(tools serveur)."
                        )
                except Exception as e:                             # noqa: BLE001
                    print(
                        "[dnd35] Rattrapage dégâts narrés échoué "
                        f"(ignoré) : {e}"
                    )

                # --- 5quater-s. ✨ Sorts NARRÉS sans `incanter_sort` (bêta
                # M4) : le LLM racontait l'incantation en prose — préparation
                # et emplacements restaient intacts, la fiche contredisait le
                # récit (« lance Bénédiction » sans AUCUN sort préparé). On
                # ré-invoque le tool officiel : il applique l'effet si les
                # règles le permettent, et REFUSE honnêtement sinon (le refus
                # est affiché à la table — la fiction ne prime plus).
                try:
                    _trace_sorts = {
                        _normaliser_texte_sort(tc.get("text") or "")
                        for tc in (result.tool_calls_trace or [])
                        if tc.get("name") == "incanter_sort" and tc.get("ok")
                    }
                    _prose_sort = _narration_prose_seule(
                        result.narration or ""
                    ) or (result.narration or "")
                    _sorts_narres = _detecter_sorts_narres(
                        _prose_sort,
                        _trace_sorts,
                    )
                    # 🔧 Bêta (M4-bis) : l'action DÉCLARÉE par le joueur
                    # (« je lance un rayon de givre ») que le MJ a ignorée
                    # (narration sans le nom du sort, confusion…) est
                    # rattrapée elle aussi — miroir du rattrapage soins.
                    _nom_perso_joueur = str(
                        personnage or actif_avant or ""
                    ).strip()
                    if _nom_perso_joueur:
                        for _sd in _detecter_sorts_narres(
                            text or "", _trace_sorts
                        ):
                            _sn = str(_sd["nom"])
                            if not any(s["nom"] == _sn for s in _sorts_narres):
                                _sorts_narres.append(
                                    {"nom": _sn, "position": 10_000}
                                )
                    # Mémorisation demandée PAR LE JOUEUR (« je mémorise
                    # Bénédiction et Lumière ») → preparer_sorts avec fusion
                    # des préparations existantes.
                    if _RE_MEMORISATION_SORT.search(text or ""):
                        _preps_fusion: dict[str, int] = {}
                        try:
                            _pjs_mem = etat_avant.get("pj") or []
                            _nom_mem = next(
                                (str(p.get("nom")) for p in _pjs_mem
                                 if p.get("nom")
                                 and _norm_nom_objet(str(p.get("nom")))
                                 in _norm_nom_objet(text)),
                                str(actif_avant
                                    or (_pjs_mem[0].get("nom")
                                        if _pjs_mem else "") or ""),
                            )
                            if _nom_mem:
                                from .tools.sorts import preparer_sorts as _prep_tool
                                _fiche_mem = persos_mod.charger_fiche(
                                    cfg.abs(cfg.paths.data_dir), _nom_mem
                                ) or {}
                                _preps_actu = (
                                    (_fiche_mem.get("sorts") or {})
                                    .get("prepares") or {}
                                )
                                _preps_fusion = {
                                    str(k): int(v or 1)
                                    for k, v in _preps_actu.items()
                                }
                                for _sn in _detecter_sorts_cites(text or ""):
                                    _preps_fusion[_sn] = (
                                        _preps_fusion.get(_sn, 0) + 1
                                    )
                                if _preps_fusion:
                                    _res_prep = await _prep_tool(
                                        ctx,
                                        nom_personnage=_nom_mem,
                                        preparations_json=json.dumps(
                                            _preps_fusion, ensure_ascii=False
                                        ),
                                    )
                                    if _res_prep.text:
                                        result.narration += (
                                            "\n\n⚙️ _Rattrapage serveur "
                                            "(mémorisation)_\n\n"
                                            + _res_prep.text
                                        )
                                        print(
                                            "[dnd35] Mémorisation narrée "
                                            "appliquée (preparer_sorts)."
                                        )
                        except Exception as e_pm:              # noqa: BLE001
                            print(
                                "[dnd35] Rattrapage mémorisation échoué "
                                f"(ignoré) : {e_pm}"
                            )

                    if _sorts_narres:
                        from .tools.sorts import incanter_sort as _cast_tool
                        _pjs_sort = [
                            p for p in (etat_avant.get("pj") or [])
                            if isinstance(p, dict) and p.get("nom")
                        ]
                        _norm_prose = _norm_nom_objet(_prose_sort)
                        for _sd in _sorts_narres:
                            # Lanceur : PJ NOMMÉ le plus proche (avant le sort
                            # dans la prose) ; sort DÉCLARÉ par le joueur →
                            # son personnage incarné ; sinon PJ actif en
                            # combat ; sinon on renonce (jamais imputer un
                            # sort au hasard — faux positif « Bouclier »
                            # facturé à Elandra).
                            _pos = int(_sd.get("position") or 0)
                            _nom_lanceur = ""
                            if _pos >= 10_000:
                                _nom_lanceur = _nom_perso_joueur
                            _meilleure = -1
                            for _p in _pjs_sort:
                                _idx = _norm_prose.find(
                                    _norm_nom_objet(str(_p.get("nom")))
                                )
                                if 0 <= _idx <= _pos and _idx > _meilleure:
                                    _meilleure = _idx
                                    _nom_lanceur = str(_p.get("nom"))
                            if not _nom_lanceur and str(
                                    etat_avant.get("phase")) == "combat":
                                _nom_lanceur = str(actif_avant or "")
                            if not _nom_lanceur:
                                print(
                                    "[dnd35] Sort narré sans lanceur identifié, "
                                    f"rattrapage ignoré : {_sd['nom']}."
                                )
                                continue
                            _res_cast = await _cast_tool(
                                ctx,
                                nom_personnage=_nom_lanceur,
                                nom_sort=str(_sd["nom"]),
                                cible="",
                            )
                            _etiquette = (
                                "accepté" if "✨" in (_res_cast.text or "")
                                else "refusé"
                            )
                            result.narration += (
                                "\n\n⚙️ _Rattrapage serveur (sort narré sans "
                                f"outil — {_etiquette})_\n\n" + _res_cast.text
                            )
                            print(
                                "[dnd35] Sort narré rattrapé : "
                                f"{_sd['nom']} par {_nom_lanceur} → "
                                f"{_etiquette}."
                            )
                except Exception as e_sp:                          # noqa: BLE001
                    print(f"[dnd35] Rattrapage sorts narrés échoué (ignoré) : {e_sp}")

                # --- 5quater-g. 🧹 Bandeau « Au tour de » hors combat : le
                # strip déterministe ne tourne qu'après le moteur (phase
                # combat). En exploration, une copie LLM du bandeau avec PV
                # inventés restait (msg 30 : « Zendar Nulentok (PV 28/32) »
                # recyclé du Loup-garou). On nettoie si la phase n'est PAS
                # le combat (en combat, la ligne officielle du moteur est
                # légitime — ne pas la retirer).
                try:
                    _phase_fin = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load().get("phase")
                    if _phase_fin != "combat":
                        result.narration = _re_mod.sub(
                            r"\n{3,}", "\n\n",
                            _RE_AUTOUR_STRIP.sub(
                                "", result.narration).strip(),
                        )
                except Exception:                                  # noqa: BLE001
                    pass

                # --- 5quater-g2. 🧹 Charge / encombrement recopiés : la fiche
                # du PJ affiche DÉJÀ une jauge de poids transporté — ce texte
                # mécanique ne doit pas apparaître dans la narration (répété à
                # chaque tour, partie 5a9b99c8 : « Votre charge actuelle est de
                # 41,35 kg (26,3% de votre capacité). »). Retrait déterministe,
                # toutes phases confondues.
                try:
                    result.narration = _re_mod.sub(
                        r"\n{3,}", "\n\n",
                        _RE_CHARGE_STRIP.sub(
                            "", result.narration or "").strip(),
                    )
                except Exception:                                  # noqa: BLE001
                    pass

                # --- 5quater-g2-bis. 🧹 Footer mécanique recopié (Position /
                # Objet clé / Destination) : déjà affiché par l'interface.
                try:
                    result.narration = _re_mod.sub(
                        r"\n{3,}", "\n\n",
                        _RE_FOOTER_MECANIQUE_STRIP.sub(
                            "", result.narration or "").strip(),
                    )
                except Exception:                                  # noqa: BLE001
                    pass

                # --- 5quater-g2-ter. 🧹 Consignes destinées au LLM recopiées
                # dans la narration (🎭 TA NARRATION…, rotation nommant
                # `terminer_mon_tour`, « recopie CE bonus dans
                # `lancer_degats` »). Elles fuient via la régularisation 5ter
                # qui concatène le texte BRUT des outils (partie 5b4e2bbe).
                try:
                    result.narration = _re_mod.sub(
                        r"\n{3,}", "\n\n",
                        _RE_CONSIGNES_LLM_STRIP.sub(
                            "", result.narration or "").strip(),
                    )
                except Exception:                                  # noqa: BLE001
                    pass

                # --- 5quater-g3. 🔁 Phrases/paragraphes recopiés à l'identique
                # d'affilée par le petit modèle (partie 4b529064 : la phrase du
                # parchemin narrée DEUX fois de suite). Retrait déterministe,
                # toutes phases confondues — un doublon strict n'apporte rien.
                try:
                    result.narration = _re_mod.sub(
                        r"\n{3,}", "\n\n",
                        _dedupliquer_phrases(result.narration or "").strip(),
                    )
                except Exception:                                  # noqa: BLE001
                    pass
            except Exception as e:                                     # noqa: BLE001
                print(f"[dnd35] 5quater rattrapage échoué (ignoré) : {e}")

            # 5quater-h. ⚙️ Harmonisation FINALE de l'état narré sur l'état
            # SERVEUR. Exécutée EN DERNIER (après 5bis-c-bis, 5quater… et les
            # rejeux correctifs) : tout bloc « PV <nom> : a/b » / « CA <nom> : n »
            # restant est réécrit aux valeurs officielles finales du tour. Un
            # tour rejoué via _rejoue_correctif a pu être vérifié ici aussi, car
            # l'harmonisation relit l'état de partie persisté à l'instant T.
            try:
                _st_h = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                )
                _etat_h = _st_h.load()
                _nar_h = _harmoniser_statut_serveur(
                    result.narration or "",
                    (_etat_h.get("pj") if isinstance(_etat_h, dict) else []) or [],
                    (_etat_h.get("monstres_combat")
                     if isinstance(_etat_h, dict) else []) or [],
                )
                if _nar_h != result.narration:
                    result.narration = _nar_h
                    print("[dnd35] 5quater-h : état narré harmonisé sur "
                          "l'état serveur (PV/CA officiels).")
            except Exception as e_h:                                   # noqa: BLE001
                print(f"[dnd35] 5quater-h harmonisation échouée (ignoré) : {e_h}")

            # 5. On ajoute la narration finale à l'historique de la session.
            # 🧹 PROSE SEULE : les blocs mécaniques serveur (initiative, jets,
            # résolutions, victoire) et les consignes LLM sont destinés à
            # l'affichage, PAS au contexte du tour suivant — sinon le petit
            # modèle les recopie dans sa prose (partie 5b4e2bbe).
            _narration_prose = _narration_prose_seule(result.narration or "")
            if _narration_prose:
                session.remember_assistant(_narration_prose)

            # 5ter-h. 📓 Auto-mémorisation serveur de la narration du tour.
            # Le LLM n'appelait jamais `set_derniere_narration` (abd81275 :
            # champ resté vide après 15 min de jeu) — or ce résumé court sert
            # de repère anti-répétition et de continuité aux tours suivants.
            # On l'écrit ICI, déterministe, à partir de la narration finale
            # (déjà rétrécie aux ~600 premiers caractères utiles).
            try:
                if (result.narration or "").strip():
                    _st_nar = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    )
                    _et_nar = _st_nar.load()
                    if "_erreur" not in _et_nar:
                        _changed = False
                        # Contexte = PROSE SEULE (sans blocs mécaniques) :
                        # recopier « ⚖️ Dégâts appliqués… »/« ⚔️ Résolution
                        # automatique… » apprenait au LLM à émettre de la
                        # mécanique (partie 5b4e2bbe).
                        _nar_courte = " ".join(
                            (_narration_prose or result.narration or "").split()
                        )[:1200]
                        if _et_nar.get("derniere_narration") != _nar_courte:
                            _et_nar["derniere_narration"] = _nar_courte
                            _changed = True
                        # Premier tour narré → journalise l'ouverture :
                        # désactive la directive « DÉBUT DE L'AVENTURE »
                        # (sinon le MJ re-narrait l'accroche à chaque tour).
                        if _journaliser_ouverture_si_besoin(
                                _et_nar, result.narration):
                            _changed = True
                        if _changed:
                            _st_nar.save(_et_nar)
            except Exception as e_nar:                             # noqa: BLE001
                print(f"[dnd35] Auto-mémorisation narration échouée : {e_nar}")

            # 6ter. 🏆 Clôture du pre-run (victoire/XP) jamais affichée :
            # ajoutée ici, sauf si déjà présente dans la narration.
            try:
                if cloture_pre:
                    _deja = [
                        ev for ev in cloture_pre
                        if (ev.strip()[:80]) not in (result.narration or "")
                    ]
                    if _deja:
                        result.narration = (
                            (result.narration or "").rstrip()
                            + "\n\n⚔️ _Résolution automatique du tour :_\n\n"
                            + "\n\n".join(
                                # 🛡️ (correctif bandeau) : consignes LLM
                                # épurées du texte affiché à la table.
                                _epure_pour_table(ev)
                                for ev in _deja
                            )
                        ).strip()
            except Exception as e_cp:                          # noqa: BLE001
                print(f"[dnd35] Affichage clôture pre-run échoué (ignoré) : {e_cp}")

            # 6bis. 🎲 JETS OFFICIELS DU TOUR : les résultats réels des dés
            # (attaque, dégâts, sauvegarde) sont toujours affichés à la table,
            # même quand la narration ne cite aucun chiffre (partie réelle :
            # des tours entiers de combat sans UN seul nombre affiché). Bloc
            # mécanique serveur : affiché, mais exclu du contexte LLM par
            # _narration_prose_seule.
            try:
                _JETS_AFFICHES = (
                    "lancer_attaque", "lancer_degats", "lancer_sauvegarde",
                )
                _jets_txt = [
                    # 🛡️ (correctif bandeau) : les consignes LLM (« recopie
                    # CE bonus dans lancer_degats… ») sont épurées du texte
                    # affiché à la table.
                    _epure_pour_table((tc.get("text") or "").strip())
                    for tc in result.tool_calls_trace
                    if tc.get("name") in _JETS_AFFICHES and tc.get("ok")
                    and (tc.get("text") or "").strip()
                ]
                if _jets_txt:
                    _bloc_jets = (
                        "🎲 _Jets officiels du tour :_\n\n"
                        + "\n\n".join(_jets_txt)
                    )
                    result.narration = (
                        (result.narration or "").rstrip() + "\n\n" + _bloc_jets
                    ).strip()
            except Exception as e_j:                           # noqa: BLE001
                print(f"[dnd35] Bloc jets officiels échoué (ignoré) : {e_j}")

            # 6. Broadcast final (= complet, même en streaming : permet le rendu MD).
            # 🧹 Dédup de sécurité appliquée EN DERNIER : les rejeux/fallbacks
            # peuvent concaténer des blocs répétés (partie réelle : même phrase
            # ×4, bandeau « Phase : Combat » ×3) qui échappaient au passage
            # 5quater-g3 quand une branche intermédiaire échouait.
            try:
                # 🧹 Bêta (M3) : coulisses LLM (« Vérifions son état réel »),
                # templates cassés (« Touché ! () ») et paraphrases doublées
                # sont purgés AVANT le dédup verbatim.
                result.narration = _nettoyer_meta_narration(
                    result.narration or ""
                ).strip()
                # 🛡️ B15/m2 : le dédup verbatim ne tournait qu'en FIN de tour ;
                # les narrations intermédiaires diffusaient déjà des blocs
                # répétés (« Phase : Combat » ×3) que le joueur voyait.
                try:
                    result.narrations_intermediaires = (
                        _dedupliquer_narrations_intermediaires(
                            result.narrations_intermediaires)
                    )
                except Exception:                            # noqa: BLE001
                    pass
            except Exception as e_m3:                         # noqa: BLE001
                print(f"[dnd35] Nettoyage narration (M3) échoué (ignoré) : {e_m3}")
            try:
                result.narration = _re_mod.sub(
                    r"\n{3,}", "\n\n",
                    _dedupliquer_phrases(result.narration or "").strip(),
                )
            except Exception:                                  # noqa: BLE001
                pass
            try:
                # 🛡️ B18/B23/B39 : la narration est alignée sur l'état
                # mécanique réel AVANT diffusion (phase, tour courant,
                # créatures réellement debout).
                _etat_final = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                result.narration = _aligner_verite_terrain(
                    result.narration or "", _etat_final)
            except Exception as _e_align:                    # noqa: BLE001
                print(f"[dnd35] Alignement narration/état échoué (ignoré) : "
                      f"{_e_align}")
            await session.broadcast({
                "type": "dm",
                "text": result.narration,
                "iterations": result.iterations,
                "corrections": result.corrections,
                "simulation_attempted": result.simulation_attempted,
                "tool_events": result.tool_events,
                # 🛡️ m4 : la grille du donjon n'est plus diffusée.
                "state_patches": _alleger_patches_client(result.state_patches),
                "tool_calls_trace": result.tool_calls_trace,
            })
    except Exception as e:                                           # noqa: BLE001
        # Le tour a crashé (LLM injoignable, timeout…) : on prévient la table
        # plutôt que de laisser les clients attendre indéfiniment.
        print(f"[dnd35] Tour MJ échoué ({partie_id}/{player}) : {e}")
        await session.broadcast({
            "type": "dm",
            "text": "⚠️ Le MJ a rencontré un problème technique. "
                    "Réessayez dans un instant.",
        })
    finally:
        # Toujours lever le verrou "thinking", y compris si le tour est
        # annulé (WebSocket coupé au milieu du streaming) ou en erreur.
        # Sinon `session.thinking` reste True et BLOQUE tous les messages
        # suivants ("Le MJ est en train de travailler") définitivement.
        session.verrou_lever()
        last_turn = await _turn_end()

    # 7. Patches d'état → re-synchronise l'UI avec l'état persistant final.
    await session.broadcast({"type": "status", "description": "", "done": True})

    # 8. Décharge le modèle LLM de la VRAM pour libérer la place à ComfyUI —
    #    uniquement si plus AUCUN tour n'est actif (sinon un tour concurrent
    #    perdrait le modèle en cours de route). Le prochain message joueur
    #    rechargera le modèle automatiquement.
    #    - llm.unload_after_turn = true  → immédiat (historique, partage GPU).
    #    - llm.unload_after_turn = false → après llm.unload_delay_minutes
    #      d'inactivité (annulé si un tour reprend ; pratique avec plus de
    #      RAM/VRAM : les tours consécutifs n'ont plus à recharger le modèle).
    if last_turn:
        global _pending_unload
        if cfg.llm.unload_after_turn:
            try:
                await app.state.client.unload_model()
            except Exception:
                pass
        else:
            _cancel_pending_unload()
            _pending_unload = asyncio.create_task(
                _delayed_unload_task(app, cfg.llm.unload_delay_minutes * 60)
            )


# --------------------------------------------------------------------------- #
#  Frontend statique (servi à /) + data mount pour images générées (monstres,
#  fiches portraits, cartes SVG donjon).
# --------------------------------------------------------------------------- #
_static = Path(__file__).resolve().parent / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

_data_dir = cfg.abs(cfg.paths.data_dir)
if _data_dir.is_dir():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")

# Cartes de référence du projet (Faerûn, nord de Faerûn, Outreterre, Toril) :
# copiées au démarrage de `cartes/` (dépôt) vers `data/cartes/` (servi sous
# /data/cartes/…). Idempotent — les fichiers existants ne sont pas écrasés.
_cartes_src = cfg.project_root / "cartes"
if _cartes_src.is_dir() and _data_dir.is_dir():
    _cartes_dst = _data_dir / "cartes"
    _cartes_dst.mkdir(parents=True, exist_ok=True)
    for _f in sorted(_cartes_src.glob("*.*")):
        _dest = _cartes_dst / _f.name
        if not _dest.is_file():
            import shutil as _shutil
            _shutil.copy2(_f, _dest)


@app.get("/")
async def index() -> FileResponse:
    path = _static / "index.html"
    if not path.is_file():
        return JSONResponse(
            {"detail": "static/index.html manquant — voir static/."},
            status_code=404,
        )
    return FileResponse(str(path))


# Catch-all SPA : toute route non-API/WS/static retourne index.html pour que
# le routeur côté client (e.g. `/partie/abc`) fonctionne en rechargement direct.
# On exclut explicitement les préfixes réservés pour ne pas masquer une vraie
# route FastAPI manquante.
@app.get("/{full_path:path}", response_model=None)
async def spa_fallback(full_path: str) -> FileResponse | JSONResponse:
    # Routes réservées : on ne capte pas (laisse FastAPI 404/405 proprement).
    reserved = ("api", "ws", "data", "static", "docs", "redoc", "openapi.json")
    if full_path.startswith(reserved) or not full_path:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Tente d'abord un fichier statique buildé (assets/index-*.js, favicon).
    candidate = _static / full_path
    if candidate.is_file() and ".." not in full_path:
        return FileResponse(str(candidate))
    # Sinon : index.html pour le client-side routing.
    idx = _static / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return JSONResponse(
        {"detail": "static/index.html manquant — voir static/."},
        status_code=404,
    )
