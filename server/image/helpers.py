"""Helpers pour générer des images via ComfyUI depuis les modules outils.

Les outils (`server.tools.monstres`, `cartes`, `fiches`) ne devraient pas
importer directement `comfyui.py` — on passe par ces wrappers qui :

1. récupèrent le backend singleton ;
2. vérifient sa disponibilité (sinon `None`, sans lever) ;
3. construisent le prompt et le chemin de cache appropriés ;
4. lancent la génération en gérant les exceptions (return `None` si échec,
   pour fallback silencieux).

Les usages valides sont définis dans `comfyui.USAGES_VALIDES` : « monstre »,
« lieu », « portrait ».
"""

from __future__ import annotations
import os
import re
from typing import Any, Optional

from .comfyui import ComfyUIBackend, USAGES_VALIDES


# Messages affichés aux joueurs (transmis via on_event). On reste court —
# l'objectif est de signifier le délai sans immersion-breaking.
_PENDING_EVENTS = {
    "monstre": "⏳ Génération de l'image du monstre en cours (jusqu'à 60s)...",
    "lieu": "⏳ Génération de l'illustration de la salle en cours (jusqu'à 60s)...",
    "portrait": "⏳ Portrait du personnage en cours de génération — cela peut prendre 30 à 60 secondes. Vous pouvez continuer à jouer en attendant.",
}


async def _notify_pending(ctx, usage: str) -> None:
    """Émet un event temps-réel « image en cours » au callback du ctx.

    Fail-safe : si ctx.on_event est indispo (ce qui arrive hors orchestrateur,
    ex tests unitaires), on fait simplement silence.
    """
    cb = getattr(ctx, "on_event", None)
    if cb is None or usage not in _PENDING_EVENTS:
        return
    try:
        await cb({"type": "image_pending", "usage": usage,
                  "msg": _PENDING_EVENTS[usage]})
    except Exception:
        pass


# Anti-texte partagé : les modèles de génération (Qwen-Image notamment)
# adorent orner les illustrations d'écritures décoratives ; comme les
# workflows Lightning tournent à cfg=1 (prompt négatif ignoré), ces
# consignes passent obligatoirement par le prompt positif.
_ANTI_TEXT = (
    "textless image, no letters, no words, no inscriptions, no runes, "
    "no watermark, no signature, no frame, no border, no ornamental writing, "
    "no stat block, no stat sheet, no character sheet, no numbers, "
    "no UI elements, no HUD, no text overlay, no title, no caption, "
    "no hp bar, no health bar, no name plate, no label, no annotation"
)


def get_backend() -> Optional[ComfyUIBackend]:
    """Renvoie un backend ComfyUI prêt à l'emploi, ou None si injoignable.

    On tente l'init — si httpx n'arrive pas à contacter ComfyUI au moment
    d'un appel, generer() lèvera ComfyUIError et l'appelant gérera le cas.
    """
    try:
        from ..config import get_config
        base_url = get_config().image.base_url
        return ComfyUIBackend(base_url=base_url)
    except Exception:
        return None


async def generer_si_dispo(
    usage: str,
    prompt: str,
    dest_path: str,
) -> Optional[str]:
    """Génère une image si ComfyUI est dispo et activé, renvoie son chemin ; sinon None.

    En cas d'erreur (timeout, ComfyUI injoignable, etc.) on renvoie None
    silencieusement — l'appelant doit savoir qu'il dispose d'un fallback
    (SVG placeholder, pas d'image, etc.) à provisionner côté sien.
    """
    if usage not in USAGES_VALIDES:
        return None
    try:
        from ..config import get_config
        if not get_config().image.enabled:
            return None
    except Exception:
        return None
    b = get_backend()
    if b is None:
        return None
    try:
        path, _seed = await b.generer(usage, prompt, dest_path)
        if os.path.isfile(path):
            return path
    except Exception:
        return None
    return None


async def generer_averti(ctx, usage: str, prompt: str, dest_path: str) -> Optional[str]:
    """Génère une image ET prévient le joueur via on_event AVANT.

    Si `dest_path` existe déjà, on ne prévient pas : le cache sert immédiat,
    pas de délai ressenti. Sinon on émet un event « ⏳ Génération... » au
    callback temps réel du ctx avant d'appeler ComfyUI.
    """
    if os.path.isfile(dest_path):
        return dest_path  # cache hit — pas de délai, pas d'avertissement
    await _notify_pending(ctx, usage)
    return await generer_si_dispo(usage, prompt, dest_path)


# --- Helpers de prompts -------------------------------------------------- #
# Version du prompt template — quand elle change, toutes les images en cache
# sont régénérées (via le mécanisme desc_hash_v2 dans monstres.py).
PROMPT_VERSION = "v4_clean_prompt"


def _type_en_from_nom(nom: str) -> str:
    """Extrait le type EN d'un monstre depuis son nom (ex. 'Créature magique de taille G' → 'magical beast')."""
    n = nom.lower()
    type_map = [
        ("mort-vivant", "undead creature"),
        ("extérieur", "outsider creature"),
        ("exterieur", "outsider creature"),
        ("élémentaire", "elemental creature"),
        ("elementaire", "elemental creature"),
        ("créature magique", "magical beast"),
        ("creature magique", "magical beast"),
        ("créature monstrueuse", "monstrous beast"),
        ("créature aberrante", "aberration creature"),
        ("créature artificielle", "construct creature"),
        ("créature feérique", "fey creature"),
        ("humanoïde", "humanoid creature"),
        ("humanoide", "humanoid creature"),
        ("aberration", "aberration"),
        ("dragon", "dragon creature"),
        ("géant", "giant creature"),
        ("ver", "vermin creature"),
    ]
    for fr, en in type_map:
        if fr in n:
            return en
    return ""



def _sanitize_description(desc: str) -> str:
    """Nettoie une description de monstre en supprimant tout contenu
    de stat block D&D (Dés de vie, Initiative, CA, attaques, etc.).

    Le Manuel des Monstres 3.5 mélange souvent le portrait physique et les
    stats dans le même bloc de texte. On détecte le premier fragment de stat
    block et on coupe tout ce qui suit.
    """
    if not desc:
        return desc

    # Markers qui signalent le début du stat block (chaque fragment testé)
    stat_markers = [
        r"(?i)\bD[ée]s de vie\b",
        r"(?i)\bHit Dice\b",
        r"(?i)\bInitiative\b\s*[+:]",
        r"(?i)\bVitesse\b\s*:",
        r"(?i)\bSpeed\b\s*:",
        r"(?i)\bClasse d.armure\b\s*:",
        r"(?i)\bArmor Class\b\s*:",
        r"(?i)\bContact\b\s+\d",
        r"(?i)\bPris au d[eé]pourvu\b\s*:",
        r"(?i)\bFlat-footed\b\s*:",
        r"(?i)\bAttaque de base\b\s*:",
        r"(?i)\bBase Attack\b\s*:",
        r"(?i)\bAttaque \u00e0 outrance\b\s*:",
        r"(?i)\bFull Attack\b\s*:",
        r"(?i)\bEspace occup.e\b\s*:",
        r"(?i)\bSpace/Reach\b\s*:",
        r"(?i)\bAttaques sp.ciales\b\s*:",
        r"(?i)\bSpecial Attacks\b\s*:",
        r"(?i)\bParticularit.s\b\s*:",
        r"(?i)\bSpecial Qualities\b\s*:",
        r"(?i)\bJets de sauvegarde\b\s*:",
        r"(?i)\bSaving Throws\b\s*:",
        r"(?i)\bCaract.sristiques\b\s*:",
        r"(?i)\bAbilities\b\s*:",
        r"(?i)\bComp.tences\b\s*:",
        r"(?i)\bSkills\b\s*:",
        r"(?i)\bDons\b\s*:",
        r"(?i)\bFeats\b\s*:",
    ]

    # Nettoyage initial : newlines → virgules
    text = desc.replace("\n", ", ")
    fragments = [f.strip() for f in text.split(",") if f.strip()]

    kept = []
    for frag in fragments:
        # Si on tombe sur un marker de stat block, on arrête
        if any(re.search(p, frag) for p in stat_markers):
            break
        kept.append(frag)

    result = ", ".join(kept)
    result = re.sub(r",\s*,+", ",", result)
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" ,")


def monstre_prompt(nom: str, description: str = "") -> str:
    """Prompt pour un monstre.

    `description` : apparence physique du monstre (extraite du bestiaire
    local ou de la KB RAG — Manuel des Monstres). Sans elle, le générateur
    ne connaît que le nom et invente une créature quelconque.

    IMPORTANT : le nom du monstre N'EST PAS inclus dans le prompt image
    pour éviter que le modèle ne génère du texte (le nom comme étiquette).
    Seule la description physique est utilisée. Le type de créature (EN)
    est ajouté comme mot-clé pour guider le style.
    """
    # Sanitizer la description pour ne garder que l'apparence physique
    desc = _sanitize_description(description)
    desc = desc.strip().strip(".").replace("\n", ", ")
    # Extraire le type EN depuis la description ou le nom pour le prompt
    type_en = _type_en_from_nom(nom)
    type_prefix = f"{type_en}, " if type_en else ""
    if desc:
        subject = f"{type_prefix}{desc}"
    else:
        subject = f"{type_prefix}dark fantasy creature" if type_en else "dark fantasy creature"
    return (
        f"dark fantasy illustration of {subject}, "
        "tabletop RPG monster art, dramatic lighting, detailed digital "
        "painting, full body, centered, plain dark background, "
        "purely visual artwork, clean illustration, "
        "ABSOLUTELY NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, "
        "NO WRITING, NO INSCRIPTIONS, NO RUNES, NO LABELS, "
        "NO STAT BLOCKS, NO UI, NO HUD, NO WATERMARK, NO BORDER, "
        "NO NAME PLATE, NO CAPTION, NO TITLE, NO FRAME, "
        "purely visual illustration with zero text of any kind, "
        "painting only, no written content whatsoever"
    )


def lieu_prompt(salle_type: str, donjon_id: str = "dungeon") -> str:
    """Prompt pour une salle de donjon. On reste générique côté contenu
    pour éviter un sur-spécification que Qwen-Image digère mal."""
    return (
        f"interior illustration of a {salle_type} in a {donjon_id}, "
        "dungeon fantasy concept art, atmospheric lighting, "
        "detailed digital painting, no characters, "
        "video game environment, "
        + _ANTI_TEXT
    )


def scene_prompt(description: str) -> str:
    """Prompt pour une scène importante d'aventure (outil illustration_scene).

    `description` : ce que montre la scène, en toutes lettres (le MJ résume
    l'action : lieu, protagonistes, ambiance)."""
    desc = description.strip().strip(".").replace("\n", ", ")
    return (
        f"epic fantasy scene illustration of {desc}, "
        "tabletop RPG adventure art, dramatic cinematic lighting, "
        "detailed digital painting, dynamic composition, "
        + _ANTI_TEXT
    )


def portrait_prompt(nom: str, race: str = "", classe: str = "") -> str:
    elements = [nom]
    if race:
        elements.append(race)
    if classe:
        elements.append(f"{classe}")
    sujet = ", ".join(elements)
    return (
        f"heroic portrait of {sujet}, "
        "fantasy character art, head and shoulders, "
        "dramatic studio lighting, detailed digital painting, "
        "warm colors, high resolution, plain background, "
        + _ANTI_TEXT
    )
