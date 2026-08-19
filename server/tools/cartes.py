"""Outil Cartographie — adapté de `Outil_CarteJoueurs.py`.

Gère deux choses :

1. **Position du groupe sur la carte du monde** (Côte des Épées / Faerûn) —
   coordonnées relativistes 0–100, stockées dans l'état persistant
   (`etat_partie.lieu.position_x/y`).
2. **Donjon progressif** : dès que le groupe entre dans un donjon, on
   initialise une grille de salles connectées (procédurale simple). À chaque
   choix de direction, une salle se dévoile. La carte est rendue en SVG et
   exposée au front via `/data/cartes/donjon_<partie>.svg`.

Spécificité de l'app standalone :
- Pas de Files API OpenWebUI → le SVG est écrit directement sous
  `data/cartes/` et servi par la route StaticFiles.
- L'état donjon persisté dans `etat_partie.donjon` (champs `grille` =
  liste de dicts salles, `salles_visitees` = liste de "x,y", `portes_bloquees`
  = liste de "x,y:dir").
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


# --------------------------------------------------------------------------- #
#  Constantes
# --------------------------------------------------------------------------- #
TYPES_SALLES = [
    "antichambre", "couloir", "salle vide", "garde", "crypte",
    "tableau", "trésor", "piège", "autel", "cellules", "puits",
    "abattoir", "bibliothèque", "entrée", "salle du trône", "laboratoire",
]

DIRECTIONS = {
    "nord": (0, -1), "n": (0, -1),
    "sud":  (0, 1),  "s": (0, 1),
    "est":  (1, 0),  "e": (1, 0),
    "ouest": (-1, 0), "o": (-1, 0),
}
_OPP = {"nord": "sud", "sud": "nord", "est": "ouest", "ouest": "est"}

# Villes repères de la Côte des Épées (coordonnées relativistes 0-100).
VILLES_REPERES = {
    "Waterdeep":      (35, 30),
    "Neverwinter":    (28, 22),
    "Port Last":      (20, 18),
    "Luskan":          (15, 14),
    "Baldur's Gate":  (50, 55),
    "Elturel":        (60, 50),
    "Triboar":        (40, 40),
    "Phandalin":      (45, 35),
    "Mirabar":        (10, 10),
}
WORLD_MAP_URL = (
    "https://media.wizards.com/2015/images/dnd/"
    "resources/Sword-Coast-Map_HighRes.jpg"
)


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _etat_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.data_dir, "parties", f"{ctx.partie_id}.json")


def _charger_etat(ctx: ToolContext) -> dict[str, Any]:
    """Charge l'état de partie — gère les deux layouts possibles (sous
    `parties/` ou `data/partie_<id>.json`, comme PartyState)."""
    for path in (
        _etat_path(ctx),
        os.path.join(ctx.data_dir, f"partie_{ctx.partie_id}.json"),
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def _sauver_etat(ctx: ToolContext, etat: dict[str, Any]) -> Optional[str]:
    for path in (
        _etat_path(ctx),
        os.path.join(ctx.data_dir, f"partie_{ctx.partie_id}.json"),
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                # Confirme que le fichier existe (write-back).
                pass
            with open(path, "w", encoding="utf-8") as f:
                json.dump(etat, f, ensure_ascii=False, indent=2)
            return None
        except FileNotFoundError:
            continue
        except OSError as e:
            return str(e)
    return "Partie introuvable (aucun fichier d'état)."


def _normalise_dir(d: str) -> str:
    d = d.lower().strip()
    return {"n": "nord", "s": "sud", "e": "est", "o": "ouest"}.get(d, d)


def _grille_vers_dict(grille: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Convertit la liste `grille` (champ persisté) en dict (x,y) → salle."""
    return {
        (int(s["x"]), int(s["y"])): s
        for s in grille
        if isinstance(s, dict) and "x" in s and "y" in s
    }


def _dict_vers_grille(salles: dict[tuple[int, int], dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for (x, y), s in salles.items():
        s = dict(s)
        s["x"] = x; s["y"] = y
        out.append(s)
    return out


def _cartes_dir(ctx: ToolContext) -> str:
    path = os.path.join(ctx.data_dir, "cartes")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _svg_path(ctx: ToolContext, kind: str = "donjon") -> str:
    return os.path.join(_cartes_dir(ctx), f"{kind}_{ctx.partie_id}.svg")


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


# --------------------------------------------------------------------------- #
#  Rendu SVG
# --------------------------------------------------------------------------- #
def _rendre_svg_donjon(donjon: dict[str, Any], taille_cell: int = 64) -> str:
    """Restitue la carte du donjon en SVG — salles visitées en clair,
    portes ouvertes tracer en traits, salle courante mise en évidence.
    """
    salles = _grille_vers_dict(donjon.get("grille", []))
    courant = tuple(donjon.get("courant", [0, 0]))
    if not salles:
        salles[(0, 0)] = {"x": 0, "y": 0, "type": "entrée", "visitee": True}
    xs = [xy[0] for xy in salles] or [0]
    ys = [xy[1] for xy in salles] or [0]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    w = (x_max - x_min + 1) * taille_cell
    h = (y_max - y_min + 1) * taille_cell
    pad = 12
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w+2*pad} {h+2*pad}" '
        f'width="{w+2*pad}" height="{h+2*pad}" role="img" '
        f'aria-label="Carte du donjon">',
        f'<rect width="{w+2*pad}" height="{h+2*pad}" fill="#0e0e14" />',
    ]
    for (x, y), s in salles.items():
        if not s.get("visitee", True):
            continue
        cx = (x - x_min) * taille_cell + pad
        cy = (y - y_min) * taille_cell + pad
        is_cur = (x, y) == courant
        fill = "#3a2e1a" if is_cur else "#1f1f28"
        border = "#c4a96a" if is_cur else "#6a5a3a"
        parts.append(
            f'<rect x="{cx+1}" y="{cy+1}" width="{taille_cell-2}" '
            f'height="{taille_cell-2}" fill="{fill}" stroke="{border}" '
            f'stroke-width="2" rx="6" ry="6" />'
        )
        # label
        parts.append(
            f'<text x="{cx+taille_cell/2}" y="{cy+taille_cell/2+5}" '
            f'text-anchor="middle" font-family="Georgia, serif" '
            f'font-size="10" fill="#d4c4a4">{s.get("type","?")}</text>'
        )
        # Portes : traits depuis le centre vers l'extérieur.
        mid = taille_cell / 2
        portes = s.get("portes", {})
        for d, vec in DIRECTIONS.items():
            if d not in ("nord", "sud", "est", "ouest"):
                continue
            if portes.get(d):
                dx, dy = vec
                x1 = cx + mid
                y1 = cy + mid
                x2 = x1 + dx * mid
                y2 = y1 + dy * mid
                parts.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                    f'stroke="#c4a96a" stroke-width="3" />'
                )
    # marqueur salle courante
    if courant in salles:
        cx = (courant[0] - x_min) * taille_cell + pad + taille_cell / 2
        cy = (courant[1] - y_min) * taille_cell + pad + taille_cell / 2
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="8" fill="#ff5252" />'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _rendre_svg_monde(positions: dict[str, tuple[float, float]]) -> str:
    """SVG minimaliste avec les villes repères + positions du groupe."""
    w = h = 480
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="Carte du monde">',
        f'<rect width="{w}" height="{h}" fill="#0e1a14" />',
        f'<rect x="4" y="4" width="{w-8}" height="{h-8}" fill="none" '
        f'stroke="#3a5a4a" rx="8" ry="8" />',
        f'<text x="20" y="32" font-family="Georgia, serif" font-size="14" '
        f'fill="#7ab08a" font-style="italic">Côte des Épées (placeholder)</text>',
    ]
    # villes repères
    for nom, (x, y) in VILLES_REPERES.items():
        cx = x * w / 100
        cy = y * h / 100
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="4" fill="#a4c4a4" />'
            f'<text x="{cx+6}" y="{cy+4}" font-family="Georgia, serif" '
            f'font-size="10" fill="#88a088">{nom}</text>'
        )
    for perso, (x, y) in positions.items():
        cx = x * w / 100
        cy = y * h / 100
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="6" fill="#ff5252" />'
            f'<text x="{cx+8}" y="{cy+4}" font-family="Georgia, serif" '
            f'font-size="11" fill="#ff5252">{perso}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  Tools — carte du monde
# --------------------------------------------------------------------------- #
@tool
async def carte_joueurs_position(
    ctx: ToolContext, nom_perso: str, x: float, y: float
) -> ToolResult:
    """
    Place / met à jour la position d'un personnage (ou du groupe) sur la carte
    du monde. Coordonnées relativistes 0-100 (longitude/latitude entre
    Waterdeep et Luskan). La position est persistée dans l'état de la partie
    sous `lieu.position_x/y` (valeur moyenne si plusieurs PJ).

    :param nom_perso (str): nom du personnage ou "groupe".
    :param x (float): longitude 0-100.
    :param y (float): latitude 0-100.
    """
    if not (0 <= float(x) <= 100 and 0 <= float(y) <= 100):
        return ToolResult(text=f"❌ Coordonnées hors bornes (0-100) : x={x}, y={y}")
    etat = _charger_etat(ctx)
    etat.setdefault("lieu", {})
    etat["lieu"]["position_x"] = float(x)
    etat["lieu"]["position_y"] = float(y)
    # Stocke positions individuelles sous positions_joueurs.
    etat.setdefault("positions_joueurs", {})
    etat["positions_joueurs"][nom_perso] = [float(x), float(y)]
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    return ToolResult(
        text=f"📍 {nom_perso} placé en ({x:.1f}, {y:.1f}).",
        state_patch={"lieu.position_x": float(x), "lieu.position_y": float(y)},
    )


@tool
async def carte_joueurs_deplacer(
    ctx: ToolContext, nom_perso: str, dx: float, dy: float
) -> ToolResult:
    """
    Déplace un personnage relativement (delta x, delta) sur la carte du monde.

    :param nom_perso (str): nom du personnage ou "groupe".
    :param dx (float): delta longitude (peut être négatif).
    :param dy (float): delta latitude (peut être négatif).
    """
    etat = _charger_etat(ctx)
    pos = etat.get("positions_joueurs", {})
    if nom_perso not in pos:
        return ToolResult(
            text=f"❌ {nom_perso} n'a pas de position enregistrée. "
             f"Utilisez `carte_joueurs_position` d'abord."
        )
    x, y = pos[nom_perso]
    nx, ny = float(x) + float(dx), float(y) + float(dy)
    nx = max(0.0, min(100.0, nx))
    ny = max(0.0, min(100.0, ny))
    return await carte_joueurs_position(ctx, nom_perso, nx, ny)


@tool
async def carte_joueurs_get(ctx: ToolContext) -> ToolResult:
    """
    Renvoie les positions actuelles de tous les personnages enregistrés et
    émet une carte du monde SVG (placeholder avec villes repères). Aucun
    argument.
    """
    etat = _charger_etat(ctx)
    pos_raw = etat.get("positions_joueurs", {})
    positions = {k: tuple(v) for k, v in pos_raw.items()}
    if not positions:
        return ToolResult(
            text=(
                "ℹ️ Aucune position enregistrée. Définissez d'abord via "
                "`carte_joueurs_position`.Villes repères disponibles : "
                + ", ".join(VILLES_REPERES.keys())
            )
        )
    svg = _rendre_svg_monde(positions)
    path = _svg_path(ctx, "monde")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur écriture carte : {e}")
    url = _url_for(path, ctx.data_dir)
    lignes = [f"- {n} : ({x:.1f}, {y:.1f})" for n, (x, y) in positions.items()]
    return ToolResult(
        text="🗺️ **Positions actuelles**\n" + "\n".join(lignes) + f"\n\n🖼️ Carte : {url}",
        state_patch={"carte_monde": url},
    )


# --------------------------------------------------------------------------- #
#  Tools — donjon progressif
# --------------------------------------------------------------------------- #
@tool
async def carte_donjon_entrer(ctx: ToolContext, donjon_id: str) -> ToolResult:
    """
    Démarre le mode donjon : initialise une grille de salles procédurales avec
    une salle d'entrée (0,0). Appel avant d'explorer. Met à jour
    `etat_partie.donjon` et bascule `etat_partie.phase` en "exploration".

    :param donjon_id (str): identifiant libre ("Donjon de Khundrukar", etc.).
    """
    etat = _charger_etat(ctx)
    entree = {
        "x": 0, "y": 0, "type": "entrée",
        "description": "L'entrée du donjon. Une lourde porte de bois noir se dresse devant vous.",
        "visitee": True,
        "portes": {"nord": True, "sud": False, "est": True, "ouest": True},
    }
    donjon = {
        "id": donjon_id,
        "grille": _dict_vers_grille({(0, 0): entree}),
        "salles_visitees": ["0,0"],
        "portes_bloquees": [],
        "courant": [0, 0],
    }
    etat["donjon"] = donjon
    etat["phase"] = "exploration"
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    path = _svg_path(ctx, "donjon")
    svg = _rendre_svg_donjon(donjon)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur SVG : {e}")
    url = _url_for(path, ctx.data_dir)
    return ToolResult(
        text=(
            f"🚪 Vous entrez dans **{donjon_id}**. Salle d'entrée (0,0). "
            f"Portes visibles : nord, est, ouest. \n\n🖼️ Carte : {url}"
        ),
        state_patch={"donjon_id": donjon_id, "phase": "exploration",
                     "carte_donjon": url},
    )


def _nouvelle_salle(x: int, y: int) -> dict[str, Any]:
    typ = random.choice(TYPES_SALLES)
    portes = {d: random.random() < 0.7 for d in ("nord", "sud", "est", "ouest")}
    if not any(portes.values()):
        portes[random.choice(list(portes.keys()))] = True
    return {
        "x": x, "y": y, "type": typ,
        "description": "",
        "visitee": True,
        "portes": portes,
    }


@tool
async def carte_donjon_explorer(ctx: ToolContext, direction: str) -> ToolResult:
    """
    Déplace le groupe dans la direction indiquée à partir de la salle courante.
    Dévoile la salle adjacente (et la génère si inconnue). Renvoie sa
    description et une nouvelle carte mise à jour.

    :param direction (str): "nord", "sud", "est", "ouest" (ou n/s/e/o).
    """
    d = _normalise_dir(direction)
    if d not in ("nord", "sud", "est", "ouest"):
        return ToolResult(text=f"❌ Direction invalide '{direction}'. Attendu : N/S/E/O.")
    dx, dy = DIRECTIONS[d]
    etat = _charger_etat(ctx)
    donjon = etat.get("donjon") or {}
    if not donjon.get("id"):
        return ToolResult(
            text="❌ Aucun donjon actif — appelez d'abord `carte_donjon_entrer`."
        )
    courant = list(donjon.get("courant", [0, 0]))
    cx, cy = courant
    salles = _grille_vers_dict(donjon.get("grille", []))
    cour = salles.get((cx, cy))
    if cour and not cour.get("portes", {}).get(d):
        return ToolResult(text=f"🚫 Pas de porte au {d} depuis la salle courante.")
    nx, ny = cx + dx, cy + dy
    if (nx, ny) not in salles:
        salles[(nx, ny)] = _nouvelle_salle(nx, ny)
    salles[(nx, ny)]["visitee"] = True
    # Porte de retour (cohérence topologique)
    opp = _OPP[d]
    salles[(nx, ny)].setdefault("portes", {})[opp] = True
    donjon["courant"] = [nx, ny]
    donjon["grille"] = _dict_vers_grille(salles)
    salles_vis = list(set(donjon.get("salles_visitees", []) + [f"{nx},{ny}"]))
    donjon["salles_visitees"] = salles_vis
    etat["donjon"] = donjon
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    # SVG
    path = _svg_path(ctx, "donjon")
    svg = _rendre_svg_donjon(donjon)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur SVG : {e}")
    url = _url_for(path, ctx.data_dir)
    salle = salles[(nx, ny)]
    # Illustration de salle : PNG ComfyUI en arrière-plan si dispo
    # (fallback silencieux — on garde le SVG carte principale).
    img_src = "—"
    try:
        from ..image.helpers import generer_averti, lieu_prompt
        slug = (str(donjon.get("id", "salle")) or "salle").lower().replace(" ", "_")
        cache_dir = os.path.join(ctx.data_dir, "images_salles")
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, f"{slug}_{nx}_{ny}.png")
        if not os.path.isfile(dest):
            salle_type = salle.get("type", "room")
            prompt = lieu_prompt(salle_type, donjon.get("id", ""))
            r = await generer_averti(ctx, "lieu", prompt, dest)
            img_src = "comfyui" if r else "—"
        else:
            img_src = "cache"
        if img_src != "—":
            salle["image_url"] = _url_for(dest, ctx.data_dir)
            # Re-sauve l'état avec l'URL d'image
            donjon["grille"] = _dict_vers_grille(salles)
            etat["donjon"] = donjon
            _sauver_etat(ctx, etat)
    except Exception:
        pass
    img_line = ""
    if img_src not in ("—",):
        salle_img = salle.get("image_url")
        if salle_img:
            img_line = f"\n\n🖼️ Illustration salle ({img_src}) : {salle_img}"
    return ToolResult(
        text=(
            f"🚶 Vous avancez au {d} → salle ({nx},{ny}) — type : "
            f"**{salle.get('type','?')}**. "
            f"Portes visibles : "
            + ", ".join([k for k, v in salle.get("portes", {}).items() if v])
            + f".\n\n🖼️ Carte : {url}{img_line}"
        ),
        state_patch={"carte_donjon": url, "donjon_courant": [nx, ny]},
    )


@tool
async def carte_donjon_get(ctx: ToolContext) -> ToolResult:
    """
    Émet la carte du donjon actuel en SVG (sans déplacement). Aucun argument.
    """
    etat = _charger_etat(ctx)
    donjon = etat.get("donjon") or {}
    if not donjon.get("id"):
        return ToolResult(text="❌ Aucun donjon actif.")
    path = _svg_path(ctx, "donjon")
    svg = _rendre_svg_donjon(donjon)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError as e:
        return ToolResult(text=f"❌ Erreur SVG : {e}")
    url = _url_for(path, ctx.data_dir)
    salles = _grille_vers_dict(donjon.get("grille", []))
    courant = tuple(donjon.get("courant", [0, 0]))
    return ToolResult(
        text=(
            f"🗺️ **{donjon['id']}** — {len(salles)} salles, "
            f"courante ({courant[0]},{courant[1]}). "
            f"Salles visitées : {len(donjon.get('salles_visitees',[]))}\n"
            f"🖼️ Carte : {url}"
        ),
        state_patch={"carte_donjon": url},
    )


@tool
async def carte_donjon_sortir(ctx: ToolContext) -> ToolResult:
    """
    Quitte le donjon → retour au mode monde. Met `etat_partie.phase` en
    `opening_complete` (l'exploration reprend où on en était). Aucun argument.
    """
    etat = _charger_etat(ctx)
    etat["donjon"] = {"id": None, "salles_visitees": [], "portes_bloquees": [], "grille": []}
    etat["phase"] = "opening_complete"
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    return ToolResult(
        text="🚪 Vous quittez le donjon. Retour à la carte du monde.",
        state_patch={"phase": "opening_complete", "quitte_donjon": True},
    )
