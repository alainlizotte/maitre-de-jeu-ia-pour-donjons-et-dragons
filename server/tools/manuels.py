"""Outil Distribution manuels — adapté de `Outil_FichiersDepart.py`.

Fournit `distribuer_manuels_carte()` à appeler en début de partie. Émet dans
le chat des liens Markdown vers les manuels D&D 3.5 et la carte du monde.

Spécificité de l'app standalone :
- **Service local d'abord** : si le fichier existe sous `data/manuels/`
  (noms URL-safe : manuel_joueur_3.5.pdf, cote_epees_lowres.jpg, …), le lien
  pointe vers le serveur du projet (`/data/manuels/…`). Sinon, repli sur
  l'hébergement web externe historique (MANUELS_WEB_BASE_URL).
- On persiste le fait que la distribution a été faite (`etat.distribution`)
  pour éviter la redistribuer à chaque message.
"""

from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

from .base import ToolContext, ToolResult, tool
from ..game.state import PartyState


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
MANUELS_WEB_BASE_URL = os.environ.get(
    "DND35_MANUELS_URL",
    "https://ateliersynthetique.ca/d&d/manuels",
)
# Noms de fichiers locaux (data/manuels/) pour la carte de la Côte des Épées.
FICHIER_CARTE_LOWRES = "cote_epees_lowres.jpg"
FICHIER_CARTE_HIRES = "cote_epees_hires.jpg"
WORLD_MAP_LOWRES_URL = os.environ.get(
    "DND35_MAP_LOW_URL",
    "https://ateliersynthetique.ca/d&d/manuels/cote_epees_lowres.jpg",
)
WORLD_MAP_HIGHRES_URL = os.environ.get(
    "DND35_MAP_HIGH_URL",
    "https://media.wizards.com/2015/images/dnd/"
    "resources/Sword-Coast-Map_HighRes.jpg",
)

# Catalogue des fichiers distribués.
FICHIERS_DEFAUT = [
    {
        "public_name": "manuel_joueur_3.5.pdf",
        "titre": "Manuel du Joueur 3.5",
        "description": "Règles de base, races, classes, sorts, équipement.",
    },
    {
        "public_name": "guide_maitre_3.5.pdf",
        "titre": "Guide du Maître 3.5",
        "description": "Règles avancées, PNJ, trésors, gestion de la partie.",
    },
    {
        "public_name": "manuel_monstres_3.5.pdf",
        "titre": "Manuel des Monstres 3.5",
        "description": "Bestiaire officiel pour les rencontres.",
    },
    {
        "public_name": "errata_3.5.pdf",
        "titre": "Errata 3.5",
        "description": "Corrections officielles des manuels 3.5.",
    },
    {
        "public_name": "faq_3.5.pdf",
        "titre": "FAQ 3.5",
        "description": "Éclaircissements officiels.",
    },
    {
        "public_name": "aide_choix_personnage.pdf",
        "titre": "Aide — Choix d'un personnage",
        "description": "Aide-mémoire pour la création de personnage.",
    },
]


def _safe_url(base: str, name: str) -> str:
    """Construit une URL externe en encodant tout composant (le `&` des `d&d`)."""
    base_enc = quote(base, safe="/:#")
    name_enc = quote(name, safe="")
    return f"{base_enc.rstrip('/')}/{name_enc}"


def _url_locale(ctx: ToolContext, nom_fichier: str) -> Optional[str]:
    """URL servie par le serveur du projet si le fichier existe sous
    data/manuels/, sinon None (→ repli externe)."""
    chemin = os.path.join(ctx.data_dir, "manuels", nom_fichier)
    if os.path.isfile(chemin):
        return "/data/manuels/" + quote(nom_fichier, safe="")
    return None


def url_manuel(ctx: ToolContext, public_name: str) -> str:
    """URL d'un manuel : locale (/data/manuels/…) si dispo, sinon externe."""
    return _url_locale(ctx, public_name) or _safe_url(MANUELS_WEB_BASE_URL, public_name)


def url_carte_lowres(ctx: ToolContext) -> str:
    return _url_locale(ctx, FICHIER_CARTE_LOWRES) or WORLD_MAP_LOWRES_URL


def url_carte_hires(ctx: ToolContext) -> str:
    return _url_locale(ctx, FICHIER_CARTE_HIRES) or WORLD_MAP_HIGHRES_URL


def _party_state(ctx: ToolContext) -> PartyState:
    return PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)


def _charger_etat(ctx: ToolContext) -> dict[str, Any]:
    """Charge l'état via PartyState (écritures atomiques, chemin canonique)."""
    return _party_state(ctx).load()


def _sauver_etat(ctx: ToolContext, etat: dict[str, Any]) -> Optional[str]:
    """Sauvegarde l'état via PartyState (écriture atomique tempfile+replace)."""
    return _party_state(ctx).save(etat)


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def manuels_distribuer(ctx: ToolContext) -> ToolResult:
    """
    Distribue aux joueurs (en début de partie) les liens de téléchargement
    Markdown vers les manuels D&D 3.5 (Manuel du Joueur, Guide du Maître,
    Manuel des Monstres, Errata, FAQ, Aide de création) + la carte du monde
    (vignette LowRes affichée comme image, lien HighRes en téléchargement).
    À appeler **une seule fois** au démarrage — l'état `distribution` est
    persisté pour éviter une redistribution.
    Aucun argument.
    """
    etat = _charger_etat(ctx)
    deja = etat.get("distribution", {}).get("faite", False)
    if deja:
        return ToolResult(
            text="ℹ️ Manuels déjà distribués. *(distribution marquée faite "
                 "dans l'état — skip. Pour redistribuer, réinitialiser "
                 "etat.distribution.)*"
        )

    lignes = [
        "📚 **Manuels D&D 3.5 — téléchargeables** :\n",
    ]
    for f in FICHIERS_DEFAUT:
        url = url_manuel(ctx, f["public_name"])
        lignes.append(
            f"- **{f['titre']}** — {f['description']} → [{f['public_name']}]({url})"
        )
    lignes.append("")
    lignes.append(
        f"🗺️ **Carte de la Côte des Épées (LowRes)** : "
        f"![carte]({url_carte_lowres(ctx)})"
    )
    lignes.append(
        f"🗺️ **Carte HighRes** : "
        f"[Cote des Epees HighRes]({url_carte_hires(ctx)})"
    )

    # Marque la distribution comme faite
    etat.setdefault("distribution", {})["faite"] = True
    err = _sauver_etat(ctx, etat)
    if err:
        # On renvoie quand même le contenu distribué + le warning
        return ToolResult(
            text="\n".join(lignes) + f"\n\n⚠️ Échec du marquage état : {err}",
        )
    return ToolResult(
        text="\n".join(lignes),
        state_patch={"distribution_faite": True},
    )


@tool
async def manuels_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste les manuels disponibles (avec leurs URLs — servies par le serveur
    du projet si les fichiers sont présents sous data/manuels/) sans les
    distribuer formellement dans le chat. Utile si le MJ veut rappeler un
    lien précis au cours de la partie. Aucun argument.
    """
    lignes = ["📚 Manuels disponibles :"]
    for f in FICHIERS_DEFAUT:
        url = url_manuel(ctx, f["public_name"])
        lignes.append(f"- **{f['titre']}** → {url}")
    return ToolResult(text="\n".join(lignes))
