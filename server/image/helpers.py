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
    "portrait": "⏳ Génération du portrait du personnage en cours (jusqu'à 60s)...",
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
def monstre_prompt(nom: str) -> str:
    return (
        f"fantasy creature art of a {nom}, "
        "D&D 3.5 style illustration, dramatic lighting, detailed "
        "digital painting, no text, full body, centered, dark background"
    )


def lieu_prompt(salle_type: str, donjon_id: str = "dungeon") -> str:
    """Prompt pour une salle de donjon. On reste générique côté contenu
    pour éviter un sur-spécification que Qwen-Image digère mal."""
    return (
        f"interior illustration of a {salle_type} in a {donjon_id}, "
        "dungeon fantasy concept art, atmospheric lighting, "
        "detailed digital painting, no text, no characters, "
        "video game environment"
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
        "D&D fantasy character art, head and shoulders, "
        "dramatic studio lighting, detailed digital painting, "
        "warm colors, high resolution, no text"
    )
