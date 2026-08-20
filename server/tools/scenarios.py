"""Outil Scénarios — adapté de `Outil_DonneesDistante.py`.

Donne au MJ (LLM) un catalogue de scénarios pré-rédigés :
- le catalogue historique « univers Laelith » (`data/scenarios_laelith.json`
  s'il existe, sinon un catalogue de secours embarqué de 7 scénarios) ;
- **plus** les scénarios locaux PDF (`data/scenarios.json`, PDF servis sous
  `data/scenarios/` — cf. dossier documentation/scénarios du projet source).
  Le texte d'un PDF est extrait à la demande via PyMuPDF (cache mémoire,
  plafonné en caractères) pour que le MJ puisse réellement le mener.

Les fichiers PDF restent consultables par les joueurs via `/data/scenarios/…`.
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


def _charger_catalogue_laelith(ctx: ToolContext) -> list[dict[str, Any]]:
    """Charge le catalogue Laelith local si présent, sinon renvoie le fallback."""
    path = _catalogue_path(ctx)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return list(data)
        if isinstance(data, dict) and "scenarios" in data:
            return list(data["scenarios"])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return list(CATALOGUE_FALLBACK)


def _charger_scenarios_locaux(ctx: ToolContext) -> list[dict[str, Any]]:
    """Charge le catalogue des scénarios PDF locaux (data/scenarios.json)."""
    path = os.path.join(ctx.data_dir, "scenarios.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("scenarios"), list):
            return [
                s for s in data["scenarios"]
                if isinstance(s, dict) and s.get("fichier")
            ]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _charger_catalogue(ctx: ToolContext) -> list[dict[str, Any]]:
    """Catalogue unifié : Laelith + scénarios locaux PDF (avec doublons filtrés)."""
    global _CATALOGUE_CACHE
    if _CATALOGUE_CACHE is not None:
        return _CATALOGUE_CACHE
    vus: set[str] = set()
    cata: list[dict[str, Any]] = []
    for s in _charger_catalogue_laelith(ctx) + _charger_scenarios_locaux(ctx):
        sid = str(s.get("id", ""))
        if sid and sid in vus:
            continue
        if sid:
            vus.add(sid)
        cata.append(s)
    _CATALOGUE_CACHE = cata
    return _CATALOGUE_CACHE


# --------------------------------------------------------------------------- #
#  Extraction PDF (PyMuPDF) — à la demande, cache mémoire, plafonnée.
# --------------------------------------------------------------------------- #
_PDF_TEXT_CACHE: dict[str, str] = {}
_PDF_MAX_CHARS = 24000  # borne : assez pour un module complet sans exploser le contexte


def _extraire_pdf(ctx: ToolContext, chemin_relatif: str) -> str:
    """Extrait le texte d'un PDF sous data_dir (cache + plafond de caractères)."""
    cle = chemin_relatif
    if cle in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[cle]
    path = os.path.join(ctx.data_dir, chemin_relatif)
    texte = ""
    try:
        import pymupdf  # pylint: disable=import-outside-toplevel
        with pymupdf.open(path) as doc:
            for page in doc:
                texte += page.get_text() + "\n\n---\n\n"
                if len(texte) >= _PDF_MAX_CHARS:
                    texte += "\n⚠️ (extrait tronqué — PDF complet consultable via l'URL)"
                    break
    except ImportError:
        texte = "(extraction PDF indisponible : pymupdf non installé)"
    except Exception as e:                                   # noqa: BLE001
        texte = f"(extraction impossible : {e})"
    _PDF_TEXT_CACHE[cle] = texte[:_PDF_MAX_CHARS + 120]
    return _PDF_TEXT_CACHE[cle]


def _url_data(chemin_relatif: str) -> str:
    """URL publique d'un fichier sous data_dir (montage StaticFiles /data)."""
    from urllib.parse import quote
    return "/data/" + quote(chemin_relatif.lstrip("/"))


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def scenarios_laelith_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste les scénarios disponibles : univers Laelith (catalogue classique)
    + scénarios PDF locaux complets (dont le texte intégral est extrait au
    chargement). Renvoie titre + niveaux + thème + court pitch pour chacun.
    Utiliser ensuite `scenarios_laelith_charger` pour récupérer un scénario.
    Aucun argument.
    """
    cat = _charger_catalogue(ctx)
    if not cat:
        return ToolResult(text="⚠️ Aucun scénario disponible.")
    lignes = ["📚 **Catalogue de scénarios (Laelith + PDF locaux)**"]
    for s in cat:
        local = " 📄 PDF local" if s.get("fichier") else ""
        lignes.append(
            f"\n**[{s.get('id','?')}] {s.get('titre','?')}** "
            f"— Niveaux {s.get('niveau','?')}{local}"
        )
        if s.get("theme"):
            lignes.append(f"   _Thème_ : {s['theme']}")
        if s.get("pitch"):
            lignes.append(f"   _Pitch_ : {s['pitch']}")
    lignes.append(
        "\n— Choisissez un scénario par son identifiant `[id]`. "
        "Je peux adapter le niveau et le cadre si besoin. Les scénarios PDF "
        "locaux sont chargés avec leur texte intégral."
    )
    return ToolResult(text="\n".join(lignes))


@tool
async def scenarios_laelith_charger(
    ctx: ToolContext, scenario_id: str
) -> ToolResult:
    """
    Charge le détail d'un scénario par son identifiant. Pour un scénario PDF
    local, le texte intégral est extrait du fichier (plafonné) pour que le MJ
    puisse mener l'aventure fidèlement ; l'URL publique du PDF est incluse
    pour que les joueurs le consultent. Lancement : le MJ adapte ensuite le
    pitch à sa table via `etat_partie_patch` (quete.titre / quete.pitch /
    quete.source).

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
    for k in ("source", "systeme", "etapes", "pnj", "lieux", "monstres"):
        if s.get(k):
            v = s[k]
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            champs.append(f"- {k.capitalize()} : {v}")
    if s.get("fichier"):
        # Scénario PDF local : URL joueur + texte extrait pour le MJ.
        url = _url_data(s["fichier"])
        champs.append(f"- 📄 PDF consultable par les joueurs : {url}")
        for annexe in s.get("fichiers_annexes", []) or []:
            champs.append(f"- 🗺️ Annexe : {_url_data(annexe)}")
        texte = _extraire_pdf(ctx, s["fichier"])
        champs.append(f"\n=== TEXTE DU SCÉNARIO (extrait) ===\n{texte}")
    elif s.get("full"):
        champs.append(f"\n[JSON complet]\n{json.dumps(s, ensure_ascii=False, indent=2)}")
    return ToolResult(text="\n".join(champs))
