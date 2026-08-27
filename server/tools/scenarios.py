"""Outil Scénarios — catalogue structuré par univers.

Charge `data/scenarios_catalogue.json` : chaque univers contient des
scénarios avec PDF, cartes, artwork, objets, enigmes, annexes.
Le texte d'un PDF est extrait à la demande via PyMuPDF (cache mémoire,
plafonné en caractères) pour que le MJ puisse mener l'aventure.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


_CATALOGUE_CACHE: Optional[dict[str, Any]] = None
_FLAT_CACHE: Optional[list[dict[str, Any]]] = None


def _catalogue_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "scenarios_catalogue.json")


def charger_catalogue(ctx: ToolContext) -> dict[str, Any]:
    """Charge le catalogue structuré (cache mémoire)."""
    global _CATALOGUE_CACHE
    if _CATALOGUE_CACHE is not None:
        return _CATALOGUE_CACHE
    path = _catalogue_path(ctx)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "universes" in data:
            _CATALOGUE_CACHE = data
            return _CATALOGUE_CACHE
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _CATALOGUE_CACHE = {"universes": []}
    return _CATALOGUE_CACHE


def _charger_catalogue_plat(ctx: ToolContext) -> list[dict[str, Any]]:
    """Catalogue plat (tous scénarios de tous univers) pour les tools LLM."""
    global _FLAT_CACHE
    if _FLAT_CACHE is not None:
        return _FLAT_CACHE
    cata = charger_catalogue(ctx)
    flat: list[dict[str, Any]] = []
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            s2 = dict(s)
            s2["_univers"] = u.get("nom", "")
            s2["_univers_id"] = u.get("id", "")
            flat.append(s2)
    _FLAT_CACHE = flat
    return _FLAT_CACHE


# --------------------------------------------------------------------------- #
#  Extraction PDF (PyMuPDF) — à la demande, cache mémoire, plafonnée.
# --------------------------------------------------------------------------- #
_PDF_TEXT_CACHE: dict[str, str] = {}
_PDF_MAX_CHARS = 24000


def _url_to_path(ctx: ToolContext, url: str) -> str:
    """Convertit une URL /data/scenarios/... en chemin disque data_dir."""
    if url.startswith("/data/scenarios/"):
        rel = url[len("/data/scenarios/"):]
    elif url.startswith("/data/"):
        rel = url[len("/data/"):]
    else:
        rel = url.lstrip("/")
    return os.path.join(ctx.data_dir, "scenarios", rel)


def extraire_pdf(ctx: ToolContext, pdf_url: str) -> str:
    """Extrait le texte d'un PDF à partir de son URL /data/ (cache + plafond)."""
    cle = pdf_url
    if cle in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[cle]
    path = _url_to_path(ctx, pdf_url)
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


# --------------------------------------------------------------------------- #
#  Tools LLM
# --------------------------------------------------------------------------- #
@tool
async def scenarios_laelith_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste les scénarios disponibles, groupés par univers.
    Utiliser ensuite `scenarios_laelith_charger` pour récupérer un scénario.
    Aucun argument.
    """
    cata = charger_catalogue(ctx)
    universes = cata.get("universes", [])
    if not universes:
        return ToolResult(text="⚠️ Aucun scénario disponible.")
    lignes = ["📚 **Catalogue de scénarios par univers**"]
    for u in universes:
        noms_cartes = ""
        if u.get("cartes"):
            noms_cartes = f" ({len(u['cartes'])} cartes)"
        lignes.append(f"\n## {u.get('nom', '?')}{noms_cartes}")
        if u.get("description"):
            lignes.append(f"_{u['description']}_")
        for s in u.get("scenarios", []):
            pdf = " 📄" if s.get("pdf") else ""
            assets = []
            if s.get("cartes"): assets.append(f"{len(s['cartes'])} cartes")
            if s.get("artwork"): assets.append("artwork")
            if s.get("objets"): assets.append(f"{len(s['objets'])} objets")
            if s.get("enigmes"): assets.append(f"{len(s['enigmes'])} énigmes")
            if s.get("annexes"): assets.append(f"{len(s['annexes'])} annexes")
            extra = f" [{', '.join(assets)}]" if assets else ""
            lignes.append(f"- **[{s.get('id','?')}] {s.get('titre','?')}**{pdf}{extra}")
            if s.get("pitch"):
                lignes.append(f"  _{s['pitch']}_")
    lignes.append(
        "\n— Identifiant du scénario : `[id]`. "
        "Le texte PDF est extrait au chargement. Les images/artwork/objets "
        "sont consultables via les URLs du catalogue."
    )
    return ToolResult(text="\n".join(lignes))


@tool
async def scenarios_laelith_charger(
    ctx: ToolContext, scenario_id: str
) -> ToolResult:
    """
    Charge le détail d'un scénario par son identifiant. Pour un scénario PDF,
    le texte intégral est extrait du fichier (plafonné). L'URL publique du
    PDF est incluse pour que les joueurs le consultent.
    Le MJ adapte ensuite le pitch via etat_partie_patch (quete.titre / pitch).

    :param scenario_id (str): identifiant du scénario tel que listé.
    """
    flat = _charger_catalogue_plat(ctx)
    sid = str(scenario_id).strip()
    s = next((x for x in flat if str(x.get("id", "")) == sid), None)
    if s is None:
        return ToolResult(
            text=(
                f"❌ Scénario '{scenario_id}' introuvable. "
                f"Utilisez `scenarios_laelith_lister` pour voir les IDs."
            )
        )
    champs = [
        f"📜 **Scénario {s.get('id','?')} : {s.get('titre','?')}**",
        f"- Univers : {s.get('_univers', '?')}",
    ]
    if s.get("pitch"):
        champs.append(f"- Pitch : {s['pitch']}")
    if s.get("pdf"):
        champs.append(f"- 📄 PDF consultable par les joueurs : {s['pdf']}")
        # Extraire texte pour le MJ
        texte = extraire_pdf(ctx, s["pdf"])
        champs.append(f"\n=== TEXTE DU SCÉNARIO (extrait) ===\n{texte}")
    # Assets
    for label, cle in [("Cartes", "cartes"), ("Objets", "objets"),
                       ("Énigmes", "enigmes")]:
        items = s.get(cle)
        if items:
            champs.append(f"\n### {label} ({len(items)})")
            for item in items:
                champs.append(f"- {item.get('nom', '?')} : {item.get('fichier', '?')}")
    if s.get("artwork"):
        art = s["artwork"]
        for cat_label, cat_key in [("Lieux", "lieux"), ("Monstres", "monstres"), ("PNJ", "pnj")]:
            imgs = art.get(cat_key)
            if imgs:
                champs.append(f"\n### Artwork — {cat_label} ({len(imgs)})")
                for img in imgs:
                    champs.append(f"- {img.get('nom', '?')} : {img.get('fichier', '?')}")
    if s.get("annexes"):
        champs.append(f"\n### Annexes ({len(s['annexes'])})")
        for a in s["annexes"]:
            champs.append(f"- {a.get('nom', '?')} : {a.get('fichier', '?')}")
    # Auto-patch quête
    quete = {
        "titre": str(s.get("titre", "")),
        "pitch": str(s.get("pitch", "")),
        "source": f"[{s.get('id','')}] " + str(s.get("pdf") or s.get("_univers", "")),
    }
    try:
        if ctx.partie_id:
            from ..game.state import PartyState
            PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id).patch(
                "quete", json.dumps(quete, ensure_ascii=False)
            )
    except Exception:                                           # noqa: BLE001
        pass
    return ToolResult(text="\n".join(champs), state_patch={"quete": quete})
