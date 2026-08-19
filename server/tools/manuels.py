"""Outil Distribution manuels — adapté de `Outil_FichiersDepart.py`.

Fournit `distribuer_manuels_carte()` à appeler en début de partie. Émet dans
le chat des liens Markdown vers des manuels PDF hébergés sur un serveur web
public (URL de base configurable via `MANUELS_WEB_BASE_URL` ci-dessous).

Spécificité de l'app standalone :
- Mode serveur web uniquement (`web_base_url`). Pas de mode upload
  OpenWebUI (l'app n'est pas OpenWebUI).
- `MANUELS_WEB_BASE_URL` et l'éventuelle URL de carte sont récupérés depuis
  `config/config.yaml` si disponible, sinon depuis l'environnement.
- On persiste le fait que la distribution a été faite (`etat.distribution`)
 pour éviter la redistribuer à chaque message.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import quote

from .base import ToolContext, ToolResult, tool


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
MANUELS_WEB_BASE_URL = os.environ.get(
    "DND35_MANUELS_URL",
    "https://ateliersynthetique.ca/d&d/manuels",
)
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
    """Construit une URL en encodant tout composant (le `&` des `d&d`)."""
    # On encode le base_dir entier, puis on encode le nom (séparateur `/`).
    base_enc = quote(base, safe="/:#")
    name_enc = quote(name, safe="")
    return f"{base_enc.rstrip('/')}/{name_enc}"


def _etat_path(ctx: ToolContext) -> str:
    # Privilégie `parties/<id>.json` puis `partie_<id>.json` au racine.
    for path in (
        os.path.join(ctx.data_dir, "parties", f"{ctx.partie_id}.json"),
        os.path.join(ctx.data_dir, f"partie_{ctx.partie_id}.json"),
    ):
        if os.path.isfile(path):
            return path
    return os.path.join(ctx.data_dir, "parties", f"{ctx.partie_id}.json")


def _charger_etat(ctx: ToolContext) -> dict[str, Any]:
    try:
        with open(_etat_path(ctx), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _sauver_etat(ctx: ToolContext, etat: dict[str, Any]) -> Optional[str]:
    path = _etat_path(ctx)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(etat, f, ensure_ascii=False, indent=2)
        return None
    except OSError as e:
        return str(e)


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
        "📚 **Manuels D&D 3.5 — warmly téléchargeables** :\n",
    ]
    for f in FICHIERS_DEFAUT:
        url = _safe_url(MANUELS_WEB_BASE_URL, f["public_name"])
        lignes.append(
            f"- **{f['titre']}** — {f['description']} → [{f['public_name']}]({url})"
        )
    lignes.append("")
    lignes.append(
        f"🗺️ **Carte de la Côte des Épées (LowRes)** : "
        f"![carte]({WORLD_MAP_LOWRES_URL})"
    )
    lignes.append(
        f"🗺️ **Carte HighRes** : "
        f"[Sword-Coast-Map_HighRes.jpg]({WORLD_MAP_HIGHRES_URL})"
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
    Liste les manuels disponibles (avec leurs URLs d'hébergement) sans les
    distribuer formellement dans le chat. Utile si le MJ veut rappeler un
    lien précis au cours de la partie. Aucun argument.
    """
    lignes = ["📚 Manuels disponibles :"]
    for f in FICHIERS_DEFAUT:
        url = _safe_url(MANUELS_WEB_BASE_URL, f["public_name"])
        lignes.append(f"- **{f['titre']}** → {url}")
    return ToolResult(text="\n".join(lignes))
