# -*- coding: utf-8 -*-
"""🍺 Rumeurs de taverne (D&D 3.5 — outil de table).

Génère 2d4 rumeurs MÉLANGÉES : de vrais indices tirés de la mémoire de
campagne (mission, intrigue, lieux, créatures déjà croisées) et de fausses
informations (distorsions DÉTERMINISTES par graine : jour de jeu + id de
partie). Le même jour raconte les mêmes rumeurs ; le lendemain, d'autres.

Le MJ les narre à l'oral (la liste elle-même est un canal MJ : le filtre
anti-fuite la neutralise si le modèle la recopie telle quelle).
"""
from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


def _norm_t(s: Any) -> str:
    n = str(s or "").strip().lower()
    return " ".join(n.split())


# Distorsions déterministes des fausses rumeurs : la direction s'inverse,
# un chiffre double, ou un accent nocturne s'ajoute.
_INVERSIONS = (
    ("au nord", "au sud"), ("au sud", "au nord"),
    ("à l'est", "à l'ouest"), ("à l'ouest", "à l'est"),
    ("est", "ouest"), ("ouest", "est"),
)

_FAUSSES_FAÇONS = (
    "des marchands jurent que {fait}, mais ils ont bu plus que de raison",
    "une vieille lavandière murmure que {fait} — elle prend le vent pour "
    "des bottes",
    "un mercenaire ivre raconte que {fait} ; personne n'y croit vraiment",
    "un gamin des rues répète à qui veut l'entendre que {fait}",
)

_VRAIES_FAÇONS = (
    "un voyageur aux yeux graves affirme que {fait}",
    "la rumeur court dans toute la ville : {fait}",
    "un vieux routier, sobre pour une fois, chuchote que {fait}",
    "on en parle bas dans les étables et les chapelles : {fait}",
)


def _faits_de_intrigue(etat: dict[str, Any]) -> list[str]:
    """Extraits courts et utilisables de la mémoire de campagne."""
    faits: list[str] = []
    mem = etat.get("memoire") or {}

    def _ajout(txt: str) -> None:
        t = " ".join(str(txt or "").replace("\n", " ").split())
        if len(t) < 15:
            return
        # première phrase utile, bornée
        for sep in (". ", " ; ", " — "):
            if sep in t:
                t = t.split(sep)[0]
                break
        t = t.strip(" .;:-")
        if len(t) >= 15:
            faits.append(t[0].lower() + t[1:] if len(t) > 1 else t)

    obj = _norm_t(mem.get("objectif_courant") or "")
    if obj:
        _ajout(str(mem.get("objectif_courant")))
    for m in (mem.get("missions") or []):
        _ajout(str((m or {}).get("notes") or ""))
    for e in (mem.get("evenements_rencents") or [])[-4:]:
        _ajout(str(e))
    for l in (mem.get("lieux_visites") or []):
        _ajout(str((l or {}).get("notes") or ""))
    # dédup en normalisé
    vus: set[str] = set()
    uniques: list[str] = []
    for f in faits:
        cle = _norm_t(f)[:60]
        if cle and cle not in vus:
            vus.add(cle)
            uniques.append(f)
    return uniques[:8]


def _distordre(fait: str, rnd: random.Random) -> str:
    """Faussit une info vraie : direction inversée, chiffre doublé, ou
    accent nocturne — déterministe via la graine du jour."""
    t = fait
    for a, b in _INVERSIONS:
        if a in t:
            t = t.replace(a, b, 1)
            return t
    chiffre = None
    import re as _re
    m = _re.search(r"\b(\d{1,4})\b", t)
    if m:
        chiffre = m.group(1)
        t = t.replace(chiffre, str(int(chiffre) * 2), 1)
        return t
    return t + ", en pleine lune"


@tool
async def rumeurs_taverne(
    ctx: ToolContext, lieu: str = "",
) -> ToolResult:
    """
    Tire 2d4 RUMEURS DE TAVERNE mélangées : de vrais indices tirés de
    l'intrigue en cours (mémoire de campagne) et de fausses informations
    (distorsions déterministes — les mêmes rumeurs tout le jour, d'autres
    le lendemain). À utiliser quand le groupe cherche des informations
    (taverne, marché, temple, rue).

    :param lieu (str): OPTIONNEL — le nom du lieu où l'on écoute (ex.
        "La Chope Refendue", "marché de Phlan") pour l'ambiance.

    NARRATION : raconte chaque rumeur dans la bouche d'un client différent
    (voix, maniérismes) — ne recopie JAMAIS la liste telle quelle ni les
    marqueurs [VRAIE]/[FAUSSE] : le joueur doit deviner.
    """
    from ..game.state import PartyState  # noqa: PLC0415

    etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
    if "_erreur" in etat:
        return ToolResult(text="❌ Partie illisible.")

    # ── La graine du JOUR : les mêmes rumeurs toute la journée. ──
    graine_txt = (str(ctx.partie_id) + "|" + date.today().isoformat()
                  + "|" + _norm_t(lieu))
    h = hashlib.md5(graine_txt.encode("utf-8")).hexdigest()
    rnd = random.Random(int(h[:12], 16))

    nb = 2 + rnd.randint(0, 3) + rnd.randint(0, 3)   # ≈ 2d4 (2 à 8)
    faits = _faits_de_intrigue(etat)

    # Créatures du bestiaire pour pimenter les fausses (noms plausibles)
    creatures: list[str] = []
    try:
        from .monstres import ANIMAUX_CONNUS  # noqa: PLC0415  (peut absenter)
    except Exception:                                        # noqa: BLE001
        ANIMAUX_CONNUS = None
    for m in (etat.get("memoire") or {}).get("monstres_combattus") or []:
        n = str((m or {}).get("nom") or "").strip()
        if n:
            creatures.append(n)

    lignes: list[str] = [
        "🎲 **Rumeurs de taverne"
        + (f" — {lieu}" if _norm_t(lieu) else "") + "**"
    ]
    if not faits:
        lignes.append(
            "ℹ️ Aucune intrigue en mémoire : narre des rumeurs génériques "
            "(temps, prix, commerçants) ou lance d'abord la quête "
            "(`memoire_mission`).")
        return ToolResult(text="\n".join(lignes))

    ordre = list(range(nb))
    rnd.shuffle(ordre)
    nb_vraies = max(1, min(len(faits), nb // 2 + 1))
    for i in ordre:
        if i < nb_vraies and faits:
            fait = faits[i % len(faits)]
            façon = _VRAIES_FAÇONS[rnd.randrange(len(_VRAIES_FAÇONS))]
            marque = "[VRAIE]"
        else:
            base = faits[rnd.randrange(len(faits))] if faits else ""
            fait = _distordre(base, rnd)
            façon = _FAUSSES_FAÇONS[rnd.randrange(len(_FAUSSES_FAÇONS))]
            marque = "[FAUSSE]"
        lignes.append(f"- {façon.format(fait=fait)} {marque}")

    lignes.append(
        "\n🗣️ NARRE-LES À L'ORAL (une voix par client), JAMAIS la liste ni "
        "les marqueurs : le joueur doit peser ce qu'il croit. Les "
        "[VRAIE]/[FAUSSE] sont pour toi seul.")
    return ToolResult(text="\n".join(lignes))
