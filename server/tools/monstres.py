"""Outil Monstres — adapté de `Outil_ImageMonstre.py`.

Renvoie la fiche complète + génère/c sert l'image d'un monstre D&D 3.5.

Spécificité de l'app standalone (vs OpenWebUI) :
- Pas de backend de génération d'image AI (pas d'OpenWebUI AutoImage ici).
  À la place : un **placeholder SVG stylé** est généré et mis en cache sous
  `data/bestiaire_cache/<slug>.svg`. Si une vraie image PNG existe déjà au
  même chemin, elle est servie en priorité. L'utilisateur peut déposer ses
  propres PNGs dans ce dossier pour remplacer les placeholders.
- L'image est exposée au front via l'URL `/data/bestiaire_cache/<slug>.svg`
  (route servie par StaticFiles dans `main.py`).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool
from ..image.helpers import generer_averti, monstre_prompt


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
_BESTIAIRE_CACHE: Optional[dict[str, Any]] = None
_BESTIAIRE_MTIME: float = 0.0


def _slug(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "monstre"


def _bestiaire_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "bestiaire.json")


def _load_bestiaire(ctx: ToolContext) -> dict[str, Any]:
    """Charge le bestiaire avec cache memo recharge si mtime change."""
    global _BESTIAIRE_CACHE, _BESTIAIRE_MTIME
    path = _bestiaire_path(ctx)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    if _BESTIAIRE_CACHE is None or mt != _BESTIAIRE_MTIME:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Le bestiaire source a les monstres en clés top-level (hors _meta).
            monstres: dict[str, Any] = {}
            for k, v in raw.items():
                if k == "_meta":
                    continue
                if isinstance(v, dict) and "nom" in v:
                    monstres[v.get("cle", k)] = v
            raw["monstres"] = monstres
            _BESTIAIRE_CACHE = raw
            _BESTIAIRE_MTIME = mt
        except (json.JSONDecodeError, OSError):
            return {"_meta": {"nb_monstres": 0}, "monstres": {}}
    return _BESTIAIRE_CACHE  # type: ignore[return-value]


def _cache_dir(ctx: ToolContext) -> str:
    path = os.path.join(ctx.data_dir, "bestiaire_cache")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _normalise_nom(nom: str) -> str:
    """Normalise un nom pour recherche insensible à la casse/accents."""
    nf = unicodedata.normalize("NFKD", nom.lower())
    return re.sub(r"[^a-z0-9]+", "_", "".join(c for c in nf if not unicodedata.combining(c)))


def _find_monstre(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Cherche un monstre par nom (insensible à la casse/accents)."""
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    n = _normalise_nom(nom)
    # 1. clé exacte (slugifiée)
    for k, m in monstres.items():
        if _normalise_nom(k) == n or _normalise_nom(m.get("nom", "")) == n:
            return m
    # 2. inclusion (ex. "dragon rouge jeune" → "dragon_rouge_jeune")
    for k, m in monstres.items():
        if n in _normalise_nom(k) or n in _normalise_nom(m.get("nom", "")):
            return m
    return None


def _find_image(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le chemin d'une vraie image (PNG/JPG/WebP) en cache.

    On exclut le `.svg` car c'est par convention un placeholder affiché en
    dépannage — pas une « vraie » image. On préfère générer un vrai PNG
    via ComfyUI plutôt que resservir un vieux SVG.
    """
    slug = _slug(nom)
    cache_dir = _cache_dir(ctx)
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = os.path.join(cache_dir, f"{slug}.{ext}")
        if os.path.isfile(p):
            return p
    return None


def _find_svg(ctx: ToolContext, nom: str) -> Optional[str]:
    """Renvoie le SVG placeholder existant (le cas échéant)."""
    slug = _slug(nom)
    p = os.path.join(_cache_dir(ctx), f"{slug}.svg")
    return p if os.path.isfile(p) else None


def _placeholder_svg(nom: str) -> str:
    """Génère un SVG placeholder stylé D&D pour un monstre."""
    initiales = "".join(w[0].upper() for w in re.findall(r"[A-Za-zÀ-ÿ]+", nom)[:2]) or "?"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        f'width="256" height="256" role="img" aria-label="{nom}">'
        f'<rect width="256" height="256" fill="#1a1a23" />'
        f'<rect x="4" y="4" width="248" height="248" fill="none" '
        f'stroke="#8a6d3b" stroke-width="2" rx="10" ry="10" />'
        f'<text x="128" y="135" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="100" font-weight="bold" '
        f'fill="#c4a96a">{initiales}</text>'
        f'<text x="128" y="200" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="20" fill="#b0b0b5">'
        f'<tspan>{nom}</tspan></text>'
        f'<text x="128" y="230" text-anchor="middle" '
        f'font-family="Georgia, serif" font-size="11" '
        f'font-style="italic" fill="#777">D&D 3.5 — placeholder</text>'
        f'</svg>'
    )


def _write_placeholder(ctx: ToolContext, nom: str) -> str:
    """Écrit le placeholder SVG et renvoie son chemin."""
    path = os.path.join(_cache_dir(ctx), f"{_slug(nom)}.svg")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_placeholder_svg(nom))
    except OSError:
        pass
    return path


def _url_for(path: str, data_dir: str) -> str:
    """Retourne l'URL publique pour servir l'image (StaticFiles sur `/data`).

    `path` est un chemin absolu sous `data_dir` ; on le rend relatif à `data_dir`
    et on préfixe par `/data/` (mount StaticFiles ajouté dans `main.py`).
    """
    from pathlib import Path
    try:
        rel = Path(path).relative_to(Path(data_dir))
        return "/data/" + rel.as_posix().lstrip("/")
    except ValueError:
        # En cas de chemin hors data_dir, on retombe sur le basename seul.
        return "/data/" + Path(path).name


def _format_fiche(m: dict[str, Any]) -> str:
    """Formate la fiche d'un monstre en Markdown lisible."""
    lignes = [
        f"🐉 **{m.get('nom','?')}** — FP {m.get('fp','?')}",
        f"- Type : {m.get('type','?')} ( taille {m.get('taille','?')} )",
        f"- DV : {m.get('dv','?')} — PV : {m.get('pv','?')} — CA : {m.get('ca','?')}",
        f"- Vitesse : {m.get('vitesse','?')} — Initiative : {m.get('init','?')}",
        f"- Attaques : {m.get('attaques','?')}",
        f"- Dégâts : {m.get('degs','?')}",
        f"- Sauvegardes : {m.get('sauvegardes','?')}",
        f"- Carac : {m.get('carac','?')}",
        f"- Compétences : {m.get('comp','?')}",
        f"- Dons : {m.get('dons','?')}",
        f"- Capacités : {m.get('capacites','—')}",
        f"- Faiblesses : {m.get('faiblesses','—')}",
        f"- Alignement : {m.get('alignement','?')}",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def monstre_consulter(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Renvoie la fiche complète (statistiques D&D 3.5) + l'URL d'une image d'un
    monstre. Cherche d'abord dans le bestiaire local (`data/bestiaire.json`).
    Si le monstre n'est pas trouvé localement, retourne un message invitant à
    interroger la base de connaissances RAG « D&D 3.5 — Manuels » et
    génère quand même un placeholder SVG.

    :param nom (str): nom du monstre (ex. "Gobelin", "Dragon rouge jeune").
    """
    m = _find_monstre(ctx, nom)
    # Image : priorité PNG local. Sinon tentative ComfyUI (vraie image).
    # En dernier recours SVG placeholder.
    img_path = _find_image(ctx, nom)
    src = "locale"
    if img_path is None:
        gen_ok = False
        try:
            slug = _slug(nom)
            cache_dir = os.path.join(ctx.data_dir, "bestiaire_cache")
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, f"{slug}.png")
            prompt_text = monstre_prompt(nom)
            r = await generer_averti(ctx, "monstre", prompt_text, dest)
            if r is not None and os.path.isfile(r):
                img_path = r
                src = "comfyui"
                gen_ok = True
        except Exception as e:
            # On ne casse pas le tour si ComfyUI échoue — fallback SVG.
            src = f"comfyui_echec({type(e).__name__})"
        if not gen_ok:
            img_path = _write_placeholder(ctx, nom)
            src = "placeholder" if "comfyui" not in src else src
    url = _url_for(img_path, ctx.data_dir)
    if m is None:
        return ToolResult(
            text=(
                f"❓ Monstre **{nom}** absent du bestiaire local. "
                f"Pour les stats, interrogez la KB « D&D 3.5 — Manuels » "
                f"(RAG activé). "
                f"Image ({src}) : {url}"
            ),
            state_patch={"image_monstre": url},
        )
    fiche = _format_fiche(m)
    return ToolResult(
        text=(
            f"{fiche}\n\n🖼️ Image ({src}) : {url}\n"
            f"\n[JSON complet]\n" + json.dumps(m, ensure_ascii=False, indent=2)
        ),
        state_patch={"image_monstre": url},
    )


@tool
async def monstre_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste tous les monstres pré-remplis du bestiaire local, avec leur FP.
    Aucun argument. Utile pour que le MJ choisisse enemis crédibles sans
    improviser les stats.
    """
    best = _load_bestiaire(ctx)
    monstres: dict[str, Any] = best.get("monstres", {})
    if not monstres:
        return ToolResult(text="ℹ️ Bestiaire local vide.")
    lignes = []
    # Tri par FP (parsing approximatif : 1/3 < 1 < 2 ...)
    def fp_key(m: dict[str, Any]) -> float:
        fp = str(m.get("fp", "0"))
        try:
            if "/" in fp:
                a, b = fp.split("/")
                return float(a) / float(b)
            return float(fp)
        except (ValueError, ZeroDivisionError):
            return 99.0
    for m in sorted(monstres.values(), key=fp_key):
        lignes.append(f"- **{m.get('nom','?')}** — FP {m.get('fp','?')}")
    return ToolResult(
        text=(
            f"🐉 Bestiaire local ({len(monstres)} monstres, triés par FP) :\n"
            + "\n".join(lignes)
        )
    )


@tool
async def monstre_ajouter_bestiaire(
    ctx: ToolContext,
    nom: str,
    type_monstre: str,
    taille: str,
    dv: str,
    pv: int,
    ca: int,
    vitesse: str,
    bab: str,
    init: str,
    attaques: str,
    degs: str,
    sauvegardes: str,
    carac: str,
    comp: str,
    dons: str,
    capacites: str,
    faiblesses: str,
    fp: str,
    alignement: str,
) -> ToolResult:
    """
    Enrichit le bestiaire local en y ajoutant un monstre custom (pour les
    créatures non couvertes par le Manuel des Monstres 3.5 d'origine). La
    fiche est persistée dans `data/bestiaire.json` et réutilisable à
    l'avenir.

    :param nom (str): nom usuel (ex. "Rois des glaces").
    :param type_monstre (str): ex. "EI (froid)".
    :param taille (str): T/P/M/G/C (cf. MJ 3.5).
    :param dv (str): ex. "4d8+8".
    :param pv (int): points de vie moyens.
    :param ca (int): classe d'armure.
    :param vitesse (str): ex. "9 m (6 cases)".
    :param bab (str): ex. "+4".
    :param init (str): ex. "+1".
    :param attaques (str): description des armes/modes d'attaque.
    :param degs (str): dégâts par attaque.
    :param sauvegardes (str): "Réfl +X, Vig +Y, Vol +Z".
    :param carac (str): "For X, Dex Y, Con Z, Int A, Sag B, Cha C".
    :param comp (str): compétences.
    :param dons (str): dons.
    :param capacites (str): capacités spéciales.
    :param faiblesses (str): faiblesses (— si aucune).
    :param fp (str): facteur de puissance (ex. "1/2", "3").
    :param alignement (str): ex. "Neutre mauvais".
    """
    best_path = _bestiaire_path(ctx)
    try:
        with open(best_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ToolResult(text=f"❌ Bestiaire illisible : {best_path}")

    cle = _slug(nom)
    fiche = {
        "nom": nom,
        "type": type_monstre,
        "taille": taille,
        "dv": dv,
        "pv": int(pv),
        "ca": int(ca),
        "vitesse": vitesse,
        "bab": bab,
        "init": init,
        "attaques": attaques,
        "degs": degs,
        "sauvegardes": sauvegardes,
        "carac": carac,
        "comp": comp,
        "dons": dons,
        "capacites": capacites,
        "faiblesses": faiblesses,
        "fp": fp,
        "alignement": alignement,
        "cle": cle,
        "prompt_image": f"fantasy {nom.lower()} creature, D&D 3.5 illustration, ink style, dramatic lighting",
    }
    raw[cle] = fiche
    # Met à jour le meta nb_monstres
    if "_meta" in raw and isinstance(raw["_meta"], dict):
        raw["_meta"]["nb_monstres"] = sum(1 for k in raw if k != "_meta" and isinstance(raw[k], dict) and "nom" in raw[k])

    try:
        with open(best_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur écriture bestiaire : {e}")
    # Invalide le cache
    global _BESTIAIRE_CACHE
    _BESTIAIRE_CACHE = None
    return ToolResult(
        text=f"✅ Monstre **{nom}** ajouté au bestiaire (clé `{cle}`, FP {fp}).",
    )
