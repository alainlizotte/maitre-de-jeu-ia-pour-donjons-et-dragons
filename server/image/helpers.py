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
    "no watermark, no signature, no frame, no border, no ornamental writing"
)


def get_backend() -> Optional[ComfyUIBackend]:
    """Renvoie un backend ComfyUI prêt à l'emploi, ou None si injoignable.

    On tente l'init — si httpx n'arrive pas à contacter ComfyUI au moment
    d'un appel, generer() lèvera ComfyUIError et l'appelant gérera le cas.
    """
    try:
        return ComfyUIBackend()
    except Exception:
        return None


async def generer_si_dispo(
    usage: str,
    prompt: str,
    dest_path: str,
) -> Optional[str]:
    """Génère une image si ComfyUI est dispo, renvoie son chemin ; sinon None.

    En cas d'erreur (timeout, ComfyUI injoignable, etc.) on renvoie None
    silencieusement — l'appelant doit savoir qu'il dispose d'un fallback
    (SVG placeholder, pas d'image, etc.) à provisionner côté sien.
    """
    if usage not in USAGES_VALIDES:
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
def monstre_prompt(nom: str, description: str = "") -> str:
    """Prompt pour un monstre.

    `description` : apparence physique du monstre (extraite du bestiaire
    local ou de la KB RAG — Manuel des Monstres). Sans elle, le générateur
    ne connaît que le nom et invente une créature quelconque : une « Goule »
    n'aurait rien d'un mort-vivant décharné. On évite la mention « D&D 3.5 »
    qui poussait le modèle vers un style page de manuscrit encadré d'écritures.
    """
    desc = description.strip().strip(".").replace("\n", ", ")
    suffixe = f", {desc}" if desc else ""
    return (
        f"dark fantasy illustration of a single {nom}{suffixe}, "
        "tabletop RPG monster art, dramatic lighting, detailed digital "
        "painting, full body, centered, plain dark background, "
        + _ANTI_TEXT
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
