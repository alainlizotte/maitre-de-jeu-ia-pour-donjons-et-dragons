"""Outil Scénarios Laelith — adapté de `Outil_DonneesDistante.py`.

Donne au MJ (LLM) un catalogue de scénarios pré-rédigés, historiquement
récupéré via scraping du site Laelith (univers Donjon du Dragon).

Spécificité app standalone :
- Plus de scraping réseau (fragile). À la place, charge un fichier local
  `data/scenarios_laelith.json` si présent. Sinon, retombe sur un catalogue
  de secours embarqué (7 scénarios génériques) — équivalent au
  `CATALOGUE_FALLBACK` de l'original.
- L'utilisateur peut importer le catalogue complet en déposant un
  `scenarios_laelith.json` (issue du scraping original ou constitué sur
  mesure) — l'occasion d'un import one-shot plutôt qu'une dépendance live.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


# --------------------------------------------------------------------------- #
#  Catalogue de secours (7 scénarios génériques) — idem original.
# --------------------------------------------------------------------------- #
CATALOGUE_FALLBACK = [
    {
        "id": "1",
        "titre": "La Voix sous les Pavés",
        "niveau": "1–3",
        "theme": "Enquête urbaine / politique",
        "pitch": "Disparitions dans les bas-quartiers de Laelith ; une voix attire les âmes égarées dans les égouts.",
    },
    {
        "id": "2",
        "titre": "Les Échos de la Tour Brisée",
        "niveau": "3–5",
        "theme": "Donjon / magie ancienne",
        "pitch": "Un arcane déchu a laissé une tour en ruines, lueurs nocturnes, bestioles mange-bétail.",
    },
    {
        "id": "3",
        "titre": "Le Sel Noir de la Côte",
        "niveau": "2–4",
        "theme": "Maritime / contrebande",
        "pitch": "Brigands attaquant les convois de sel ; derrière eux une organisation plus large.",
    },
    {
        "id": "4",
        "titre": "La Complainte des Bois-Ombres",
        "niveau": "4–6",
        "theme": "Forêt maudite / fey",
        "pitch": "Une forêt voisine se meurt ; les druides implorent les aventuriers.",
    },
    {
        "id": "5",
        "titre": "Le Trône de Cendre",
        "niveau": "6–9",
        "theme": "Politique de cour / trahison",
        "pitch": "Mort suspecte d'un député de Laelith ; l'assassin doit être découvert avant qu'il ne frappe à nouveau.",
    },
    {
        "id": "6",
        "titre": "Le Tombeau de Selvar le Sombre",
        "niveau": "5–7",
        "theme": "Donjon funéraire / mort-vivant",
        "pitch": "Le tombeau d'un ancien sorcier s'ouvre ; les pillards ne reviennent jamais.",
    },
    {
        "id": "7",
        "titre": "Le Marchand de Sable",
        "niveau": "2–4",
        "theme": "Voyage / commerce mystérieux",
        "pitch": "Un marchand ambulant propose des objets magiques à prix ridicule. Que cache sa caravane ?",
    },
]

_CATALOGUE_CACHE: Optional[list[dict[str, Any]]] = None


def _catalogue_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "scenarios_laelith.json")


def _charger_catalogue(ctx: ToolContext) -> list[dict[str, Any]]:
    """Charge le catalogue local si présent, sinon renvoie le fallback."""
    global _CATALOGUE_CACHE
    if _CATALOGUE_CACHE is not None:
        return _CATALOGUE_CACHE
    path = _catalogue_path(ctx)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            _CATALOGUE_CACHE = data
            return _CATALOGUE_CACHE
        if isinstance(data, dict) and "scenarios" in data:
            _CATALOGUE_CACHE = list(data["scenarios"])
            return _CATALOGUE_CACHE
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _CATALOGUE_CACHE = list(CATALOGUE_FALLBACK)
    return _CATALOGUE_CACHE


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def scenarios_laelith_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste les scénarios disponibles dans le catalogue (univers Laelith).
    Renvoie titre + niveaux + thème + court pitch pour chacun. Utiliser
    ensuite `scenarios_laelith_charger` pour récupérer un scénario précis.
    Aucun argument.
    """
    cat = _charger_catalogue(ctx)
    if not cat:
        return ToolResult(text="⚠️ Aucun scénario disponible.")
    lignes = ["📚 **Catalogue de scénarios — univers Laelith**"]
    for s in cat:
        lignes.append(
            f"\n**[{s.get('id','?')}] {s.get('titre','?')}** "
            f"— Niveaux {s.get('niveau','?')}"
        )
        if s.get("theme"):
            lignes.append(f"   _Thème_ : {s['theme']}")
        if s.get("pitch"):
            lignes.append(f"   _Pitch_ : {s['pitch']}")
    lignes.append(
        "\n— Choisissez un scénario par son identifiant `[id]`. "
        "Je peux adapter le niveau et le cadre si besoin."
    )
    return ToolResult(text="\n".join(lignes))


@tool
async def scenarios_laelith_charger(
    ctx: ToolContext, scenario_id: str
) -> ToolResult:
    """
    Charge le détail d'un scénario Laelith par son identifiant. Renvoie un
    résumé exploitable par le MJ (niveau, thème, pitch, étapes s'il les a,
    PNJ clés, monstres usuels). Lancement : le MJ adapte ensuite le pitch à
    sa table via `etat_partie_patch` (quete.titre / quete.pitch).

    :param scenario_id (str): identifiant du scénario tel que listé.
    """
    cat = _charger_catalogue(ctx)
    # Recherche par id (insensible à la casse/type)
    sid = str(scenario_id).strip()
    s = next((x for x in cat if str(x.get("id","")) == sid), None)
    if s is None:
        return ToolResult(
            text=(
                f"❌ Scénario '{scenario_id}' introuvable. "
                f"Utilisez `scenarios_laelith_lister` pour voir les IDs."
            )
        )
    champs = [
        f"📜 **Scénario {s.get('id','?')} : {s.get('titre','?')}**",
        f"- Niveaux : {s.get('niveau','?')}",
        f"- Thème : {s.get('theme','?')}",
        f"- Pitch : {s.get('pitch','?')}",
    ]
    for k in ("etapes", "pnj", "lieux", "monstres"):
        if s.get(k):
            v = s[k]
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            champs.append(f"- {k.capitalize()} : {v}")
    if s.get("full"):
        champs.append(f"\n[JSON complet]\n{json.dumps(s, ensure_ascii=False, indent=2)}")
    return ToolResult(text="\n".join(champs))
