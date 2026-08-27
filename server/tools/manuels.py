"""Outil Distribution manuels — adapté de `Outil_FichiersDepart.py`.

Fournit `manuels_distribuer()` à appeler en début de partie. Émet dans
le chat des liens Markdown vers les manuels D&D 3.5 et les cartes de
référence (Faerûn, Outreterre, Toril).

Le catalogue inclut les 17 manuels de l'édition 3.5 (SCAN + OCR disponibles).
Les manuels sont groupés par catégorie pour le menu déroulant frontend.
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

# Cartes de référence (dossier projet `cartes/`, copié au démarrage du serveur
# vers data/cartes/ et servi sous /data/cartes/…). La première est la carte
# de jeu affichée par l'onglet « Monde » du panneau droit.
CARTES_REFERENCE = [
    ("faerun_nord.png", "Faerûn — Nord (Côte des Épées)", "Faerûn Nord"),
    ("faerun.png", "Faerûn — carte complète", "Faerûn"),
    ("laelith.jpg", "Laelith — carte de la ville", "Laelith"),
    ("outreterre.png", "Outreterre (Underdark)", "Outreterre"),
    ("toril.png", "Toril — monde entier", "Toril"),
]

# Catalogue complet des 17 manuels D&D 3.5, groupés par catégorie.
FICHIERS_DEFAUT = [
    # --- Manuels de base ---
    {
        "public_name": "manuel_joueur_3.5.pdf",
        "titre": "Manuel du Joueur 3.5",
        "description": "Règles de base, races, classes, sorts, équipement.",
        "categorie": "Manuels de base",
    },
    {
        "public_name": "guide_maitre_3.5.pdf",
        "titre": "Guide du Maître 3.5",
        "description": "Règles avancées, PNJ, trésors, gestion de la partie.",
        "categorie": "Manuels de base",
    },
    {
        "public_name": "manuel_monstres_3.5.pdf",
        "titre": "Manuel des Monstres 3.5",
        "description": "Bestiaire officiel pour les rencontres.",
        "categorie": "Manuels de base",
    },
    # --- Aide & références ---
    {
        "public_name": "errata_3.5.pdf",
        "titre": "Errata 3.5",
        "description": "Corrections officielles des manuels 3.5.",
        "categorie": "Références",
    },
    {
        "public_name": "faq_3.5.pdf",
        "titre": "FAQ 3.5",
        "description": "Éclaircissements officiels.",
        "categorie": "Références",
    },
    {
        "public_name": "aide_choix_personnage.pdf",
        "titre": "Aide — Choix d'un personnage",
        "description": "Aide-mémoire pour la création de personnage.",
        "categorie": "Références",
    },
    # --- Codex d'extension ---
    {
        "public_name": "codex_aventureux_3.5.pdf",
        "titre": "Codex Aventureux",
        "description": "Archetypes, prestige, styles de jeu aventureux.",
        "categorie": "Codex",
    },
    {
        "public_name": "codex_divin_3.5.pdf",
        "titre": "Codex Divin",
        "description": "Prêtres, druides, dieux, sorts divins.",
        "categorie": "Codex",
    },
    {
        "public_name": "codex_martial_3.5.pdf",
        "titre": "Codex Martial",
        "description": "Guerriers, combattants, armes, arts martiaux.",
        "categorie": "Codex",
    },
    {
        "public_name": "codex_profane_3.5.pdf",
        "titre": "Codex Profane",
        "description": "Magiciens, sorciers, arcanes, sorts profanes.",
        "categorie": "Codex",
    },
    {
        "public_name": "chapitres_sacres_3.5.pdf",
        "titre": "Les Chapitres Sacrés",
        "description": "Divins, prêtres, religions, sorts sacrés.",
        "categorie": "Codex",
    },
    {
        "public_name": "arcanes_exhumes_3.5.pdf",
        "titre": "Les Arcanes Exhumés",
        "description": "Sorts, rituels, artefacts, magie ancienne.",
        "categorie": "Codex",
    },
    {
        "public_name": "grand_manuel_des_psioniques_3.5.pdf",
        "titre": "Grand Manuel des Psioniques",
        "description": "Pouvoirs psioniques, classes, discipline.",
        "categorie": "Codex",
    },
    # --- Bestiaires avancés ---
    {
        "public_name": "manuel_monstres_2_3.5.pdf",
        "titre": "Manuel des Monstres 2",
        "description": "Deuxième tome du bestiaire officiel.",
        "categorie": "Bestiaires",
    },
    {
        "public_name": "manuel_monstres_3_3.5.pdf",
        "titre": "Manuel des Monstres 3",
        "description": "Troisième tome du bestiaire officiel.",
        "categorie": "Bestiaires",
    },
    {
        "public_name": "draconomicon_3.5.pdf",
        "titre": "Draconomicon",
        "description": "Le livre des dragons — types, trésors, nids.",
        "categorie": "Bestiaires",
    },
    {
        "public_name": "libris_mortis_3.5.pdf",
        "titre": "Libris Mortis",
        "description": "Les livres des morts — undead, nécromancie.",
        "categorie": "Bestiaires",
    },
    # --- Mondes & régions ---
    {
        "public_name": "eberron_ombres_derniere_guerre_3.5.pdf",
        "titre": "Eberron — Les Ombres de la Dernière Guerre",
        "description": "Campagne Eberron, intrigues et guerre.",
        "categorie": "Mondes",
    },
    {
        "public_name": "eberron_univers_3.5.pdf",
        "titre": "Eberron — Univers",
        "description": "Le monde d'Eberron en détail.",
        "categorie": "Mondes",
    },
    {
        "public_name": "ravenloft_livre_regles_3.5.pdf",
        "titre": "Ravenloft — Livre de Règles",
        "description": "Le domaines des ombres, horreur gothique.",
        "categorie": "Mondes",
    },
]


def _safe_url(base: str, name: str) -> str:
    """Construit une URL externe en encodant tout composant."""
    base_enc = quote(base, safe="/:#")
    name_enc = quote(name, safe="")
    return f"{base_enc.rstrip('/')}/{name_enc}"


def _url_locale(ctx: ToolContext, nom_fichier: str) -> Optional[str]:
    """URL servie par le serveur du projet si le fichier existe sous data/manuels/."""
    chemin = os.path.join(ctx.data_dir, "manuels", nom_fichier)
    if os.path.isfile(chemin):
        return "/data/manuels/" + quote(nom_fichier, safe="")
    return None


def url_manuel(ctx: ToolContext, public_name: str) -> str:
    """URL d'un manuel : locale (/data/manuels/…) si dispo, sinon externe."""
    return _url_locale(ctx, public_name) or _safe_url(MANUELS_WEB_BASE_URL, public_name)


def url_carte_reference(ctx: ToolContext, nom_fichier: str) -> Optional[str]:
    """URL locale d'une carte de référence si présente sous data/cartes/."""
    chemin = os.path.join(ctx.data_dir, "cartes", nom_fichier)
    if os.path.isfile(chemin):
        return "/data/cartes/" + quote(nom_fichier, safe="")
    return None


def _party_state(ctx: ToolContext) -> PartyState:
    return PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)


def _charger_etat(ctx: ToolContext) -> dict[str, Any]:
    return _party_state(ctx).load()


def _sauver_etat(ctx: ToolContext, etat: dict[str, Any]) -> Optional[str]:
    return _party_state(ctx).save(etat)


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def manuels_distribuer(ctx: ToolContext) -> ToolResult:
    """
    Distribue aux joueurs (en début de partie) les liens de téléchargement
    Markdown vers les manuels D&D 3.5 (17 manuels + carte du monde).
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

    categories: dict[str, list[dict[str, str]]] = {}
    for f in FICHIERS_DEFAUT:
        cat = f.get("categorie", "Autres")
        categories.setdefault(cat, []).append(f)

    lignes = [
        "📚 **Manuels D&D 3.5 — téléchargeables** :\n",
    ]
    for cat, fichiers in categories.items():
        lignes.append(f"**{cat}** :")
        for f in fichiers:
            url = url_manuel(ctx, f["public_name"])
            lignes.append(
                f"- **{f['titre']}** — {f['description']} → [{f['public_name']}]({url})"
            )
        lignes.append("")

    # Cartes de référence — la carte de jeu (nord de Faerûn, celle de
    # l'onglet « Monde ») en image, les autres en liens cliquables.
    cartes_dispo = []
    for _f, _t, _l in CARTES_REFERENCE:
        _u = url_carte_reference(ctx, _f)
        if _u:
            cartes_dispo.append((_f, _t, _l, _u))
    if cartes_dispo:
        lignes.append("🗺️ **Cartes de référence** :")
        premier = cartes_dispo[0]
        lignes.append(f"![{premier[1]}]({premier[3]})")
        for _f, _t, _l, _u in cartes_dispo[1:]:
            lignes.append(f"- [{_t}]({_u})")
        lignes.append("")

    etat.setdefault("distribution", {})["faite"] = True
    err = _sauver_etat(ctx, etat)
    if err:
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
    Liste les manuels disponibles (avec leurs URLs) sans les distribuer
    formellement dans le chat. Utile si le MJ veut rappeler un lien précis.
    Aucun argument.
    """
    lignes = ["📚 Manuels disponibles :"]
    for f in FICHIERS_DEFAUT:
        url = url_manuel(ctx, f["public_name"])
        lignes.append(f"- **{f['titre']}** → {url}")
    return ToolResult(text="\n".join(lignes))
