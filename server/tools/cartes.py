"""Outil Cartographie — adapté de `Outil_CarteJoueurs.py`.

Gère deux choses :

1. **Position du groupe sur la carte du monde** (nord de Faerûn) —
    coordonnées en pourcentage 0–100 de l'image `cartes/faerun_nord.png`,
    stockées dans l'état persistant (`etat_partie.lieu.position_x/y`) et
    affichées en direct par l'onglet « Monde » du panneau droit.
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

import asyncio
import os
import random
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool
from ..game.state import PartyState


# --------------------------------------------------------------------------- #
#  Constantes
# --------------------------------------------------------------------------- #
TYPES_SALLES = [
    "antichambre", "couloir", "salle vide", "garde", "crypte",
    "tableau", "trésor", "piège", "autel", "cellules", "puits",
    "abattoir", "bibliothèque", "entrée", "salle du trône", "laboratoire",
    "escaliers",
]

DIRECTIONS = {
    "nord": (0, -1), "n": (0, -1),
    "sud":  (0, 1),  "s": (0, 1),
    "est":  (1, 0),  "e": (1, 0),
    "ouest": (-1, 0), "o": (-1, 0),
}
_OPP = {"nord": "sud", "sud": "nord", "est": "ouest", "ouest": "est"}

# Villes repères du nord de Faerûn — coordonnées en POURCENTAGE de la carte
# `cartes/faerun_nord.png` servie au front (/data/cartes/faerun_nord.png) :
# x = 0 % bord ouest (océan) → 100 % bord est, y = 0 % bord nord → 100 % sud.
# Positions approximatives (carte canonique) — ajuster ici si besoin, le
# marqueur joueur et ces repères partagent la même grille.
VILLES_REPERES = {
    "Mirabar":        (13, 9),
    "Luskan":         (24, 13),
    "Neverwinter":    (21, 19),
    "Waterdeep":      (27, 30),
    "Daggerford":     (30, 32),
    "Triboar":        (38, 29),
    "Phandalin":      (37, 34),
    "Everlund":       (49, 26),
    "Silverymoon":    (54, 23),
    "Mithral Hall":   (57, 17),
    "Evereska":       (36, 39),
    "Secomber":       (33, 40),
    "Scornubel":      (33, 46),
    "Elturel":        (37, 50),
    "Baldur's Gate":  (28, 54),
    "Athkatla":       (26, 61),
    "Suzail":         (66, 55),
}
# Nom de la carte affichée dans l'onglet « Monde » du panneau droit.
CARTE_MONDE_FICHIER = "faerun_nord.png"


def _normaliser_nom(nom: str) -> str:
    """Minuscules + sans accents, pour rapprocher « Baldur's gate »/« baldurs gate »."""
    import unicodedata
    nf = unicodedata.normalize("NFKD", (nom or "").lower())
    ascii_ = "".join(c for c in nf if not unicodedata.combining(c))
    return " ".join(ascii_.replace("'", " ").split())


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _party_state(ctx: ToolContext) -> PartyState:
    return PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)


def _charger_etat(ctx: ToolContext) -> dict[str, Any]:
    """Charge l'état via PartyState (écritures atomiques, chemin canonique)."""
    return _party_state(ctx).load()


def _sauver_etat(ctx: ToolContext, etat: dict[str, Any]) -> Optional[str]:
    """Sauvegarde l'état via PartyState (écriture atomique tempfile+replace)."""
    return _party_state(ctx).save(etat)


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
def _lignes_label(texte: str, max_car: int = 12, max_lignes: int = 2) -> list[str]:
    """Découpe un libellé de salle en courtes lignes pour tenir dans la case."""
    mots = str(texte or "?").split()
    lignes: list[str] = []
    cour = ""
    for m in mots:
        essai = f"{cour} {m}".strip()
        if len(essai) > max_car and cour:
            lignes.append(cour)
            cour = m
        else:
            cour = essai
    if cour:
        lignes.append(cour)
    if len(lignes) > max_lignes:
        lignes = lignes[:max_lignes]
        lignes[-1] = (lignes[-1][:max_car - 1]).rstrip() + "…"
    return lignes or ["?"]


def _rendre_svg_donjon(donjon: dict[str, Any], taille_cell: int = 64) -> str:
    """Restitue la carte du donjon en SVG — salles visitées en clair,
    passages/portes connus BIEN VISIBLES (barreaux dorés sur les murs),
    salle courante avec marqueur central.

    Ordre des calques (important pour la lisibilité) :
    1. fond ;
    2. rectangles des salles ;
    3. passages & portes PAR-DESSUS les salles (un barreau doré au milieu
       de chaque mur ouvert ; tirets si le passage mène vers l'inconnu) ;
    4. libellés PAR-DESSUS, avec halo sombre (paint-order=stroke) ;
    5. marqueur de la salle courante AU CENTRE (anneau + point rouge) ;
       le libellé de cette salle est remonté pour ne pas chevaucher.
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
    # Étiquette d'étage dans la bande vide du bas (les salles s'arrêtent à
    # h+pad ; la bande inférieure de `pad` pixels reste libre).
    etage = int(donjon.get("etage", 0) or 0)
    parts.append(
        f'<text x="{pad}" y="{h+2*pad-6}" font-family="Georgia, serif" '
        f'font-size="11" fill="#c4a96a" stroke="#0e0e14" stroke-width="3" '
        f'paint-order="stroke" stroke-linejoin="round">'
        f"Étage : {_nom_etage(etage)}</text>"
    )

    def _coords(x: int, y: int) -> tuple[int, int]:
        return (
            (x - x_min) * taille_cell + pad,
            (y - y_min) * taille_cell + pad,
        )

    visites = [(xy, s) for xy, s in salles.items() if s.get("visitee", True)]
    visites_set = {xy for xy, _ in visites}
    mid = taille_cell / 2

    # --- Calque 1 : rectangles des salles ---------------------------------- #
    for (x, y), s in visites:
        cx, cy = _coords(x, y)
        is_cur = (x, y) == courant
        fill = "#3a2e1a" if is_cur else "#1f1f28"
        border = "#c4a96a" if is_cur else "#6a5a3a"
        parts.append(
            f'<rect x="{cx+1}" y="{cy+1}" width="{taille_cell-2}" '
            f'height="{taille_cell-2}" fill="{fill}" stroke="{border}" '
            f'stroke-width="2" rx="6" ry="6" />'
        )

    # --- Calque 2 : passages & portes connus (PAR-DESSUS les salles) ------- #
    # Un mur partagé ne doit être marqué qu'une fois : clé canonique =
    # paire triée des coordonnées des deux salles voisines.
    murs_deja_dessines: set[frozenset[tuple[int, int]]] = set()
    for (x, y), s in visites:
        cx, cy = _coords(x, y)
        portes = s.get("portes", {})
        for d in ("nord", "sud", "est", "ouest"):
            if not portes.get(d):
                continue
            dx, dy = DIRECTIONS[d]
            voisin = (x + dx, y + dy)
            cle_mur = frozenset({(x, y), voisin})
            if cle_mur in murs_deja_dessines:
                continue
            murs_deja_dessines.add(cle_mur)
            # Point milieu du mur ouvert.
            mx = cx + mid + dx * mid
            my = cy + mid + dy * mid
            horizontal = d in ("nord", "sud")
            # Barreau de porte doré, bien visible sur le mur.
            if horizontal:
                bx, by, bw, bh = mx - 11, my - 3, 22, 6
            else:
                bx, by, bw, bh = mx - 3, my - 11, 6, 22
            parts.append(
                f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" '
                f'fill="#e8c56a" stroke="#0e0e14" stroke-width="1.5" rx="2" />'
            )
            # Passage connu menant hors des zones explorées : trait en
            # tirets qui prolonge la porte vers l'inconnu.
            if voisin not in visites_set:
                sx2 = mx + dx * 15
                sy2 = my + dy * 15
                parts.append(
                    f'<line x1="{mx}" y1="{my}" x2="{sx2}" y2="{sy2}" '
                    f'stroke="#e8c56a" stroke-width="4" '
                    f'stroke-dasharray="4,3" stroke-linecap="round" />'
                )

    # --- Calque 3 : libellés, PAR-DESSUS les dessins ----------------------- #
    for (x, y), s in visites:
        cx, cy = _coords(x, y)
        lignes = _lignes_label(s.get("type", "?"))
        is_cur = (x, y) == courant
        # Salle courante : libellé remonté pour laisser place au marqueur
        # central (anneau + point rouge au centre exact).
        decal = -13 if is_cur else 0
        if len(lignes) == 1:
            tx, ty, fs = cx + mid, cy + mid + 4 + decal, 10
            parts.append(
                f'<text x="{tx}" y="{ty}" text-anchor="middle" '
                f'font-family="Georgia, serif" font-size="{fs}" '
                f'fill="#e8dcc0" stroke="#0e0e14" stroke-width="3" '
                f'paint-order="stroke" stroke-linejoin="round">'
                f"{lignes[0]}</text>"
            )
        else:
            ty1, ty2 = cy + mid - 1 + decal, cy + mid + 10 + decal
            for i, ligne in enumerate(lignes):
                ty = ty1 if i == 0 else ty2
                parts.append(
                    f'<text x="{cx + mid}" y="{ty}" text-anchor="middle" '
                    f'font-family="Georgia, serif" font-size="9" '
                    f'fill="#e8dcc0" stroke="#0e0e14" stroke-width="3" '
                    f'paint-order="stroke" stroke-linejoin="round">'
                    f"{ligne}</text>"
                )

    # --- Calque 4 : marqueur de la salle courante, AU CENTRE --------------- #
    if courant in salles:
        cx, cy = _coords(*courant)
        px, py = cx + mid, cy + mid
        # Halo pulsé (anneau translucide) puis point plein centré.
        parts.append(
            f'<circle cx="{px}" cy="{py}" r="9" fill="none" '
            f'stroke="#ff5252" stroke-opacity="0.45" stroke-width="2.5" />'
        )
        parts.append(
            f'<circle cx="{px}" cy="{py}" r="4.5" fill="#ff5252" '
            f'stroke="#0e0e14" stroke-width="1.5" />'
        )
        parts.append(
            f'<circle cx="{px - 1.5}" cy="{py - 1.5}" r="1.2" '
            f'fill="#ffd9d9" fill-opacity="0.9" />'
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
        f'fill="#7ab08a" font-style="italic">Nord de Faerûn (aperçu)</text>',
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
    du monde (nord de Faerûn). Coordonnées en POURCENTAGE de la carte : x = 0
    (bord ouest/océan) → 100 (est), y = 0 (nord) → 100 (sud). La position est
    persistée dans l'état de la partie sous `lieu.position_x/y` et affichée en
    direct par le marqueur doré de l'onglet « Monde ». Utiliser de préférence
    `carte_joueurs_placer_ville` quand la destination est une ville connue.

    :param nom_perso (str): nom du personnage ou "groupe".
    :param x (float): pourcentage horizontal 0-100 (ouest → est).
    :param y (float): pourcentage vertical 0-100 (nord → sud).
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
async def carte_joueurs_placer_ville(
    ctx: ToolContext, ville: str, nom_perso: str = "groupe"
) -> ToolResult:
    """
    Place un personnage (par défaut le groupe entier) sur une ville connue de
    la carte du monde (nord de Faerûn). À appeler dès que le groupe arrive
    quelque part, voyage ou demande « où sommes-nous ? » — le marqueur doré de
    l'onglet « Monde » se met à jour en direct. Met aussi à jour `lieu.nom`
    avec la ville trouvée.

    :param ville (str): nom de la ville (ex : "Waterdeep", "Phandalin"…).
    :param nom_perso (str): nom du personnage, ou "groupe" (défaut).
    """
    cible = _normaliser_nom(ville)
    if not cible:
        return ToolResult(text="❌ Nom de ville vide.")
    trouve = None
    for nom, (x, y) in VILLES_REPERES.items():
        if _normaliser_nom(nom) == cible or cible in _normaliser_nom(nom):
            trouve = (nom, x, y)
            break
    if trouve is None:
        return ToolResult(
            text=(
                f"❌ Ville « {ville} » inconnue de la carte. Villes repères : "
                + ", ".join(VILLES_REPERES.keys())
            )
        )
    nom_v, x, y = trouve
    etat = _charger_etat(ctx)
    etat.setdefault("lieu", {})
    etat["lieu"]["position_x"] = float(x)
    etat["lieu"]["position_y"] = float(y)
    # Met aussi à jour le nom du lieu : c'est l'étiquette affichée sous le
    # marqueur dans l'onglet « Monde » (le MJ peut la préciser ensuite via
    # etat_partie_patch, ex : « Auberge du Drakkar, Waterdeep »).
    etat["lieu"]["nom"] = nom_v
    etat.setdefault("positions_joueurs", {})
    etat["positions_joueurs"][nom_perso] = [float(x), float(y)]
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    return ToolResult(
        text=f"📍 {nom_perso} placé à {nom_v} ({x}, {y}).",
        state_patch={
            "lieu.nom": nom_v,
            "lieu.position_x": float(x),
            "lieu.position_y": float(y),
        },
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
    Renvoie les positions actuelles de tous les personnages enregistrés.
    Le marqueur correspondant est visible dans l'onglet « Monde » du panneau
    droit des joueurs. Aucun argument.
    """
    etat = _charger_etat(ctx)
    pos_raw = etat.get("positions_joueurs", {})
    positions = {k: tuple(v) for k, v in pos_raw.items()}
    if not positions:
        return ToolResult(
            text=(
                "ℹ️ Aucune position enregistrée. Placez d'abord le groupe via "
                "`carte_joueurs_placer_ville` ou `carte_joueurs_position`. "
                "Villes repères disponibles : "
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
    # Garde-fou : si un donjon est déjà ouvert avec le même id, on refuse
    # poliment de l'écraser — le MJ doit utiliser `carte_donjon_explorer`
    # pour avancer, pas `carte_donjon_entrer` (qui ré-initialiserait la
    # grille et annulerait toute l'exploration déjà faite).
    donjon_existant = etat.get("donjon") or {}
    if donjon_existant and donjon_existant.get("id") == donjon_id:
        pos = donjon_existant.get("courant", [0, 0])
        x, y = (pos[0], pos[1]) if len(pos) >= 2 else (0, 0)
        url = _url_for(_svg_path(ctx, "donjon"), ctx.data_dir)
        return ToolResult(
            text=(
                f"ℹ️ Vous êtes déjà dans **{donjon_id}** (salle actuelle : "
                f"({x},{y})). Pour avancer, utilise `carte_donjon_explorer` "
                f"avec une direction (nord/sud/est/ouest).\n\n🖼️ Carte : {url}"
            ),
        )
    # Vérifier si ce donjon a déjà été exploré dans cette partie.
    archive = (etat.get("donjons_exploreres") or {}).get(donjon_id)
    if archive and archive.get("grille"):
        # Restaurer l'état antérieur du donjon (grille + descriptions/états
        # des salles, étages explorés, position) : le groupe retrouve les
        # salles EXACTEMENT comme il les avait quittées.
        donjon = dict(archive)
        donjon["id"] = donjon_id
        # S'assurer que les champs requis existent.
        donjon.setdefault("grille", [])
        donjon.setdefault("salles_visitees", [])
        donjon.setdefault("portes_bloquees", [])
        donjon.setdefault("courant", [0, 0])
        donjon.setdefault("etage", 0)
        donjon.setdefault("etages", {})
        _sync_etage(donjon)
        courant = donjon["courant"]
        cx, cy = (courant[0], courant[1]) if len(courant) >= 2 else (0, 0)
        msg_restore = (
            f"🔄 Vous retournez dans **{donjon_id}** — "
            f"salle actuelle restaurée ({cx},{cy}), "
            f"{len(donjon.get('salles_visitees', []))} salles déjà explorées."
        )
    else:
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
            "etage": 0,
            "etages": {},
        }
        _sync_etage(donjon)
        msg_restore = (
            f"🚪 Vous entrez dans **{donjon_id}** (rez-de-chaussée). "
            f"Salle d'entrée (0,0). Portes visibles : nord, est, ouest."
        )
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
        text=f"{msg_restore}\n\n🖼️ Carte : {url}",
        state_patch={"donjon": donjon, "phase": "exploration",
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


# --------------------------------------------------------------------------- #
#  Constance des salles visitées
# --------------------------------------------------------------------------- #
# Chaque salle visitée porte désormais :
# - `description` : la description canonique FIGÉE par le MJ au premier
#   passage (via `carte_donjon_decrire_salle`) — reprise telle quelle à
#   chaque retour ;
# - `etat_des_lieux` : l'état dans lequel la salle a été quittée (monstres
#   vaincus, coffres vidés, piège désamorcé…) — idem, repris au retour.
# Si le MJ n'a rien figé, une description de SECOURS déterministe est
# dérivée par hash de (donjon, x, y, type) : identique à chaque visite, donc
# jamais de salle réinventée — même sans coopération du LLM.
_GABARITS_SECOURS = [
    "Une {typ} voûtée de pierre froide. La poussière recouvre les dalles et "
    "l'air y est immobile, chargé d'ancienneté.",
    "Une {typ} aux murs suintants d'humidité ; des torches éteintes "
    "attendent dans des supports de fer forgé.",
    "Une {typ} à demi effondrée, dont les gravats barrent partiellement le "
    "sol craquelé ; l'ombre y garde une fraîcheur de tombe.",
    "Une {typ} en enfilade, éclairée par une faible lueur grise qui filtre "
    "des meurtrières ; le silence y semble pesant.",
    "Une {typ} pavée de dalles usées par les pas, où traîne une odeur "
    "de moisi et de pierre mouillée.",
]


def _description_secours(donjon_id: str, x: int, y: int, typ: str) -> str:
    """Description de secours DÉTERMINISTE pour une salle — le même (donjon,
    x, y, type) produit TOUJOURS le même texte, donc une salle revisitée sans
    description figée reste décrite à l'identique."""
    import hashlib
    seed = f"{donjon_id}|{x}|{y}|{typ}".encode("utf-8")
    idx = int(hashlib.md5(seed).hexdigest(), 16)
    return _GABARITS_SECOURS[idx % len(_GABARITS_SECOURS)].format(
        typ=(typ or "salle").lower()
    )


@tool
async def carte_donjon_decrire_salle(
    ctx: ToolContext, description: str, etat_des_lieux: str = ""
) -> ToolResult:
    """
    FIGE la description canonique de la salle courante (et l'état dans lequel
    le groupe la quitte). À appeler après avoir narré une salle NOUVELLE, et à
    chaque fois que l'état d'une salle change (combat gagné, coffre vidé,
    piège désamorcé, porte enfoncée). Au retour du groupe, `carte_donjon_explorer`
    restituera exactement cette description et cet état — la salle ne sera
    JAMAIS réinventée.

    :param description (str): description physique stable de la salle
        (décor, dimensions, détails marquants).
    :param etat_des_lieux (str): état laissé / changements marquants
        (ex. « 3 squelettes détruits, coffre vidé, porte nord descellée »).
    """
    etat = _charger_etat(ctx)
    donjon = etat.get("donjon") or {}
    if not donjon.get("id"):
        return ToolResult(
            text="❌ Aucun donjon actif — appelez d'abord `carte_donjon_entrer`."
        )
    courant = list(donjon.get("courant", [0, 0]))
    cx, cy = (courant[0], courant[1]) if len(courant) >= 2 else (0, 0)
    salles = _grille_vers_dict(donjon.get("grille", []))
    salle = salles.get((cx, cy))
    if salle is None:
        return ToolResult(
            text=f"❌ Salle courante ({cx},{cy}) introuvable dans la grille."
        )
    description = (description or "").strip()
    if not description:
        return ToolResult(text="❌ Donne une description de la salle.")
    salle["description"] = description[:600]
    if (etat_des_lieux or "").strip():
        salle["etat_des_lieux"] = etat_des_lieux.strip()[:400]
    donjon["grille"] = _dict_vers_grille(salles)
    _sync_etage(donjon)
    etat["donjon"] = donjon
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    return ToolResult(
        text=(
            f"📌 Salle ({cx},{cy}) — description figée"
            + (" + état des lieux à jour." if (etat_des_lieux or "").strip() else ".")
            + " Au retour du groupe, cette salle sera restituée à "
            "l'IDENTIQUE (même décor, même état)."
        ),
        state_patch={"donjon": donjon},
    )


# --------------------------------------------------------------------------- #
#  Étages du donjon
# --------------------------------------------------------------------------- #
# Modèle en mémoire : `donjon["etage"]` (int, 0 = rez-de-chaussée) désigne
# l'étage VISIBLE ; les données de l'étage courant sont TOUJOURS répliquées
# dans les clés de premier niveau `grille` / `salles_visitees` /
# `portes_bloquees` / `courant` (tous les outils existants et le rendu SVG
# continuent de fonctionner sans rien savoir des étages). Chaque étage est
# aussi archivé dans `donjon["etages"] = {"0": {grille, salles_visitees,
# portes_bloquees, courant}, …}` pour être restauré lors d'un changement.
_ETAGE_NOM = ["Rez-de-chaussée", "Sous-sol I", "Sous-sol II", "Sous-sol III",
              "Sous-sol IV", "Sous-sol V", "Sous-sol VI", "Sous-sol VII",
              "Sous-sol VIII", "Sous-sol IX", "Sous-sol X"]
_TYPES_ESCALIER = {"escaliers", "escalier", "escalier du donjon", "escaliers du donjon"}


def _nom_etage(etage: int) -> str:
    if 0 <= etage < len(_ETAGE_NOM):
        return _ETAGE_NOM[etage]
    return f"Étage {etage}"


def _sync_etage(donjon: dict[str, Any]) -> None:
    """Archive l'étage courant (grille/courant…) dans `donjon["etages"]`."""
    etages = donjon.setdefault("etages", {})
    etage = int(donjon.get("etage", 0) or 0)
    etages[str(etage)] = {
        "grille": donjon.get("grille", []),
        "salles_visitees": donjon.get("salles_visitees", []),
        "portes_bloquees": donjon.get("portes_bloquees", []),
        "courant": donjon.get("courant", [0, 0]),
    }


def _charger_etage(donjon: dict[str, Any], etage: int) -> None:
    """Bascule `donjon` sur l'étage demandé.

    Un étage jamais visité démarre avec une unique salle d'escaliers en (0,0)
    (le groupe arrive par l'escalier, les portes restent à découvrir).
    """
    etages = donjon.setdefault("etages", {})
    cle = str(etage)
    if cle not in etages:
        escaliers = {
            "x": 0, "y": 0, "type": "escaliers",
            "description": "Un escalier sombre qui relie les étages du donjon.",
            "visitee": True,
            "portes": {"nord": True, "sud": False, "est": True, "ouest": True},
        }
        etages[cle] = {
            "grille": _dict_vers_grille({(0, 0): escaliers}),
            "salles_visitees": ["0,0"],
            "portes_bloquees": [],
            "courant": [0, 0],
        }
    d = etages[cle]
    donjon["etage"] = int(etage)
    donjon["grille"] = d["grille"]
    donjon["salles_visitees"] = d["salles_visitees"]
    donjon["portes_bloquees"] = d["portes_bloquees"]
    donjon["courant"] = d["courant"]


async def _illustrer_salle(
    ctx: ToolContext,
    donjon: dict[str, Any],
    salles: dict[tuple[int, int], dict[str, Any]],
    nx: int,
    ny: int,
) -> str:
    """Illustre la salle (nx, ny) via ComfyUI (cache `images_salles/`).

    Renvoie `"comfyui"`, `"cache"` ou `"—"` en cas d'échec silencieux.
    Persiste l'URL d'image dans l'état et émet l'événement temps réel comme
    `illustration_scene` (galerie « Scènes » du front).
    """
    if (nx, ny) not in salles:
        return "—"
    from ..image.helpers import generer_averti, lieu_prompt
    salle = salles[(nx, ny)]
    slug = (str(donjon.get("id", "salle")) or "salle").lower().replace(" ", "_")
    cache_dir = os.path.join(ctx.data_dir, "images_salles")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return "—"
    dest = os.path.join(cache_dir, f"{slug}_{nx}_{ny}.png")
    if os.path.isfile(dest):
        img_src = "cache"
    else:
        # Scène prégénérée (scripts/pregen_scenes.py) : sert l'image
        # instantanément sans solliciter ComfyUI — évite le blocage du jeu
        # quand la file de génération est saturée.
        pregen = _scene_pregen_cache(ctx, str(donjon.get("id") or ""), str(salle.get("type") or ""))
        if pregen:
            try:
                salle["image_url"] = _url_for(pregen, ctx.data_dir)
                donjon["grille"] = _dict_vers_grille(salles)
                _sync_etage(donjon)
                etat = _charger_etat(ctx)
                etat["donjon"] = donjon
                _sauver_etat(ctx, etat)
                cb = getattr(ctx, "on_event", None)
                if cb is not None:
                    try:
                        await cb({
                            "type": "image",
                            "usage": "lieu",
                            "image": salle["image_url"],
                            "msg": f"🖼️ Illustration salle ({nx},{ny}) (cache prégénéré).",
                        })
                    except Exception:
                        pass
                return "cache"
            except Exception:
                pass
        # Pas de cache ni de prégénéré → génération ComfyUI EN ARRIÈRE-PLAN
        # (non-bloquant). Le tool renvoie immédiatement ; l'illustration, une
        # fois prête, est persistée dans l'état et poussée à la galerie via
        # l'événement temps réel. Le jeu ne bloque JAMAIS sur ComfyUI.
        _lancer_generation_salle(
            ctx,
            donjon={
                "id": donjon.get("id"),
                "grille": _dict_vers_grille(salles),
                "etage": donjon.get("etage"),
            },
            nx=nx,
            ny=ny,
            dest=dest,
            slug=slug,
        )
        img_src = "—"
    if img_src != "—":
        salle["image_url"] = _url_for(dest, ctx.data_dir)
        # Re-sauve l'état avec l'URL d'image
        donjon["grille"] = _dict_vers_grille(salles)
        _sync_etage(donjon)
        etat = _charger_etat(ctx)
        etat["donjon"] = donjon
        _sauver_etat(ctx, etat)
        # Événement temps réel → la galerie « Scènes » du front affiche
        # l'illustration dès qu'elle est prête (ou sortie du cache).
        cb = getattr(ctx, "on_event", None)
        if cb is not None:
            try:
                await cb({
                    "type": "image",
                    "usage": "lieu",
                    "image": salle["image_url"],
                    "msg": f"🖼️ Illustration : salle ({nx},{ny}).",
                })
            except Exception:
                pass
    return img_src


_BACKGROUND_TASKS: set[Any] = set()


def _lancer_generation_salle(
    ctx: ToolContext,
    *,
    donjon: dict[str, Any],
    nx: int,
    ny: int,
    dest: str,
    slug: str,
) -> None:
    """Lance la génération ComfyUI de l'illustration de salle en arrière-plan.

    Non-bloquant (fire-and-forget) : crée une tâche asyncio qui, une fois
    l'image prête, met à jour l'état du donjon et pousse l'événement temps
    réel. La tâche est conservée dans `_BACKGROUND_TASKS` pour éviter le
    garbage-collection prématuré, et se retire du set à la fin.
    """

    async def _gen() -> None:
        try:
            from ..image.helpers import generer_averti, lieu_prompt
            salle_type = "room"
            try:
                etat = _charger_etat(ctx)
                salle = next(
                    (s for s in (etat.get("donjon") or {}).get("grille", [])
                     if s.get("x") == nx and s.get("y") == ny),
                    None,
                )
                if salle:
                    salle_type = str(salle.get("type") or "room")
            except Exception:
                pass
            prompt = lieu_prompt(salle_type, str(donjon.get("id") or ""))
            r = await generer_averti(ctx, "lieu", prompt, dest)
            if not r or not os.path.isfile(dest):
                return
            # Persist l'URL d'image sur la salle correspondante.
            try:
                etat = _charger_etat(ctx)
                dj = etat.setdefault("donjon", {}) or {}
                salle = next(
                    (s for s in dj.get("grille", [])
                     if s.get("x") == nx and s.get("y") == ny),
                    None,
                )
                if salle is not None:
                    salle["image_url"] = _url_for(dest, ctx.data_dir)
                    _sauver_etat(ctx, etat)
            except Exception:
                pass
            cb = getattr(ctx, "on_event", None)
            if cb is not None:
                try:
                    await cb({
                        "type": "image",
                        "usage": "lieu",
                        "image": _url_for(dest, ctx.data_dir),
                        "msg": f"🖼️ Illustration salle ({nx},{ny}).",
                    })
                except Exception:
                    pass
        except Exception:
            pass

    try:
        task = asyncio.create_task(_gen())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except RuntimeError:
        # Pas d'event loop actif (ex. appel sync hors asyncio) : on renonce
        # silencieusement — l'illustration sera générée à la prochaine visite.
        pass


@tool
async def carte_donjon_explorer(ctx: ToolContext, direction: str) -> ToolResult:
    """
    Déplace le groupe dans la direction indiquée à partir de la salle courante.
    Dévoile la salle adjacente (et la génère si inconnue). Renvoie sa
    description et une nouvelle carte mise à jour.

    Cohérence des retours : si la salle cible a DÉJÀ été visitée, le tool
    restitue sa description enregistrée et l'état dans lequel le groupe
    l'avait quittée — le MJ doit la re-narrer à l'identique, jamais la
    réinventer. Pour une salle NOUVELLE, le MJ fige sa description via
    `carte_donjon_decrire_salle` juste après l'avoir narrée.

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
    deja_visitee = (nx, ny) in salles and salles[(nx, ny)].get("visitee")
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
    _sync_etage(donjon)
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
    porte_ligne = (
        f"Portes visibles : "
        + ", ".join([k for k, v in salle.get("portes", {}).items() if v])
        + "."
    )
    if deja_visitee:
        # ── Salle déjà visitée : restituer la description et l'état figés ──
        # (description MJ, ou secours déterministe si jamais figée).
        desc_stockee = str(salle.get("description") or "").strip()
        if not desc_stockee:
            desc_stockee = _description_secours(
                str(donjon.get("id") or ""), nx, ny, str(salle.get("type") or "")
            )
        etat_stocke = str(salle.get("etat_des_lieux") or "").strip()
        texte = (
            f"↩️ Vous REPARCOURREZ la salle ({nx},{ny}) — type : "
            f"**{salle.get('type','?')}** — DÉJÀ VISITÉE.\n"
            f"Description enregistrée : « {desc_stockee} »\n"
        )
        if etat_stocke:
            texte += f"État tel que laissé : « {etat_stocke} »\n"
        texte += (
            f"{porte_ligne}\n\n"
            "⚠️ NE RÉINVENTE PAS cette salle : reprends FIDÈLEMENT la "
            "description et l'état ci-dessus (ce qui a été fait reste fait : "
            "monstres vaincus, coffres vidés…), puis narre seulement ce que "
            "le groupe y trouve maintenant."
        )
    else:
        texte = (
            f"🚶 Vous avancez au {d} → salle ({nx},{ny}) — type : "
            f"**{salle.get('type','?')}**. {porte_ligne}\n\n"
            "📌 Salle NOUVELLE : narre-la, puis FIGE sa description via "
            "`carte_donjon_decrire_salle(description=…, etat_des_lieux=…)` — "
            "ce fil garantit qu'en revenant ici, la salle sera retrouvée "
            "identique."
        )
    # Illustration de salle : PNG ComfyUI en arrière-plan si dispo
    # (fallback silencieux — on garde le SVG carte principale).
    # Respecte le toggle `image.scenes_enabled` (tableau de bord) comme
    # `illustration_scene` : sans lui, les images de salle repartaient en
    # cache/ComfyUI alors que l'utilisateur les avait désactivées.
    img_src = "—"
    try:
        from ..config import get_config
        if get_config().image.scenes_enabled:
            try:
                img_src = await _illustrer_salle(ctx, donjon, salles, nx, ny)
            except Exception:
                pass
    except Exception:
        pass
    img_line = ""
    if img_src not in ("—",):
        salle_img = salle.get("image_url")
        if salle_img:
            img_line = f"\n\n🖼️ Illustration salle ({img_src}) : {salle_img}"
    return ToolResult(
        text=texte + img_line,
        state_patch={"donjon": donjon, "carte_donjon": url},
    )


@tool
async def carte_donjon_etage(ctx: ToolContext, direction: str) -> ToolResult:
    """
    Monte ou descend d'un étage du donjon. N'est possible QUE depuis une salle
    contenant un escalier (type « escaliers »). Change l'étage actif
    (`donjon.etage`) ; les étages explorés restent mémorisés et reprennent
    exactement où le groupe les a quittés.

    :param direction (str): "monter" (vers le rez-de-chaussée) ou
        "descendre" (vers le sous-sol).
    """
    d = (direction or "").strip().lower()
    descendre = d in ("descendre", "desc", "descends")
    monter = d in ("monter", "monte", "m")
    if not descendre and not monter:
        return ToolResult(
            text=f"❌ Direction invalide '{direction}'. Attendu : « monter » ou « descendre »."
        )
    etat = _charger_etat(ctx)
    donjon = etat.get("donjon") or {}
    if not donjon.get("id"):
        return ToolResult(
            text="❌ Aucun donjon actif — appelez d'abord `carte_donjon_entrer`."
        )
    courant = list(donjon.get("courant", [0, 0]))
    cx, cy = (courant[0], courant[1]) if len(courant) >= 2 else (0, 0)
    salles = _grille_vers_dict(donjon.get("grille", []))
    cour = salles.get((cx, cy)) or {}
    if (cour.get("type") or "").strip().lower() not in _TYPES_ESCALIER:
        return ToolResult(
            text=(
                f"🚫 Pas d'escalier dans la salle actuelle "
                f"({cour.get('type', '?')}) — cherchez une salle "
                f"**escaliers** (`carte_donjon_explorer`)."
            )
        )
    etage_actuel = int(donjon.get("etage", 0) or 0)
    if descendre:
        nouvel_etage = etage_actuel + 1
    else:
        if etage_actuel <= 0:
            return ToolResult(
                text="🚫 Vous êtes déjà au rez-de-chaussée — pas d'étage au-dessus."
            )
        nouvel_etage = etage_actuel - 1
    _sync_etage(donjon)           # archive l'étage qu'on quitte
    _charger_etage(donjon, nouvel_etage)
    etat["donjon"] = donjon
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
    pos = list(donjon.get("courant", [0, 0]))
    return ToolResult(
        text=(
            f"🪜 Vous empruntez l'escalier "
            f"({'descendez vers le sous-sol' if descendre else 'remontez'} → "
            f"**{_nom_etage(nouvel_etage)}**). Salle actuelle ({pos[0]},{pos[1]}). "
            f"Le groupe poursuit son exploration du donjon.\n\n🖼️ Carte : {url}"
        ),
        state_patch={"donjon": donjon, "carte_donjon": url},
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
    # Aperçu des descriptions figées : le MJ vérifie d'un coup d'œil quelles
    # salles ont une description canonique (et lesquelles restent à figer).
    lignes_salles = []
    for xy in sorted(salles):
        s = salles[xy]
        desc = str(s.get("description") or "").strip()
        ligne = f"  - ({xy[0]},{xy[1]}) {s.get('type','?')}"
        if desc:
            ligne += f" — {desc[:100]}" + ("…" if len(desc) > 100 else "")
        else:
            ligne += " — (description à figer via carte_donjon_decrire_salle)"
        lignes_salles.append(ligne)
    return ToolResult(
        text=(
            f"🗺️ **{donjon['id']}** — {len(salles)} salles, "
            f"courante ({courant[0]},{courant[1]}). "
            f"Salles visitées : {len(donjon.get('salles_visitees',[]))}\n"
            + "\n".join(lignes_salles)
            + f"\n\n🖼️ Carte : {url}"
        ),
        state_patch={"donjon": donjon, "carte_donjon": url},
    )


@tool
async def carte_donjon_sortir(ctx: ToolContext) -> ToolResult:
    """
    Quitte le donjon → retour au mode monde. Met `etat_partie.phase` en
    `exploration` (l'exploration reprend où on en était). Le donjon est
    archivé dans `donjons_exploreres` pour conserver le progrès si on y
    retourne plus tard dans la même partie. Aucun argument.
    """
    etat = _charger_etat(ctx)
    donjon = etat.get("donjon") or {}
    donjon_id = donjon.get("id")
    # Archiver le donjon courant avant de le vider — y compris les étages
    # (`etages`/`etage`) et les descriptions/états des salles portés par la
    # grille : le progrès ET la constance des salles survivent à la sortie.
    if donjon_id and donjon.get("grille"):
        etat.setdefault("donjons_exploreres", {})[donjon_id] = {
            "id": donjon_id,
            "grille": donjon.get("grille", []),
            "salles_visitees": donjon.get("salles_visitees", []),
            "portes_bloquees": donjon.get("portes_bloquees", []),
            "courant": donjon.get("courant", [0, 0]),
            "etage": donjon.get("etage", 0),
            "etages": donjon.get("etages", {}),
        }
    etat["donjon"] = {"id": None, "salles_visitees": [], "portes_bloquees": [], "grille": []}
    etat["phase"] = "exploration"
    err = _sauver_etat(ctx, etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    donjon_vide = etat["donjon"]
    return ToolResult(
        text="🚪 Vous quittez le donjon. Retour à la carte du monde.",
        state_patch={"phase": "exploration", "donjon": donjon_vide,
                     "donjons_exploreres": etat.get("donjons_exploreres", {})},
    )


def _slug_image(texte: str) -> str:
    """Slug court pour nommer un fichier image de scène."""
    import re as _re
    import unicodedata as _ud
    nf = _ud.normalize("NFKD", (texte or "").lower())
    ascii_only = "".join(c for c in nf if not _ud.combining(c))
    slug = _re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")[:50]
    return slug or "scene"


def _scene_pregen_cache(ctx: ToolContext, titre: str, description: str) -> Optional[str]:
    """Renvoie le chemin d'une image de scène PRÉ-GÉNÉRÉE en cache, sinon None.

    Les scènes pré-générées (scripts/pregen_scenes.py) sont stockées dans
    `data/images_scenes/pregen/` avec un manifest par scénario
    (`pregen/<scenario_id>.json`) mappant un slug `_slug_image(titre|desc)` vers
    son fichier PNG. Ici on recherche ce manifest (scénario courant puis tous
    les manifests) : si le slug correspond ET que le PNG existe, `illustration_scene`
    servira l'image instantanément sans solliciter ComfyUI.
    """
    import json as _json
    try:
        base = os.path.join(ctx.data_dir, "images_scenes", "pregen")
        if not os.path.isdir(base):
            return None
        slug = _slug_image(titre or description)
        manifests: list[str] = []
        # Manifest du scénario courant d'abord (source quete) — on le charge en
        # premier pour prioriser les scènes du scénario actif.
        try:
            from ..game.state import PartyState
            etat = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id).load()
            source = str((etat.get("quete") or {}).get("source") or "")
            sid = source.split("]", 1)[0].lstrip("[").strip() or ""
            if sid and os.path.isfile(os.path.join(base, f"{sid}.json")):
                manifests.append(os.path.join(base, f"{sid}.json"))
        except Exception:                                           # noqa: BLE001
            pass
        for name in sorted(os.listdir(base)):
            if name.endswith(".json"):
                p = os.path.join(base, name)
                if p not in manifests:
                    manifests.append(p)
        for mp in manifests:
            try:
                with open(mp, encoding="utf-8") as f:
                    manifest = _json.load(f)
            except (OSError, _json.JSONDecodeError):
                continue
            if slug in manifest and manifest[slug].get("file"):
                fpath = os.path.join(base, str(manifest[slug]["file"]))
                if os.path.isfile(fpath):
                    return fpath
    except Exception:                                               # noqa: BLE001
        return None
    return None


def _norm_slug(text: str) -> str:
    """Normalisation accent/casse-insensible pour comparer des libellés
    de lieux/étapes entre le manifest prégénéré et le texte de narration.

    On conserve les espaces (et on convertit `_` des slugs en espace) pour
    permettre une recherche par sous-chaîne de mots, insensible aux accents,
    à la casse et aux apostrophes."""
    import unicodedata as _ud
    nf = _ud.normalize("NFKD", (text or "").lower())
    nf = nf.replace("_", " ").replace("'", " ").replace("’", " ")
    nf = "".join(c for c in nf if not _ud.combining(c))
    return " ".join(nf.split())


def _manifest_scenario(ctx: ToolContext, sid: str) -> dict:
    """Renvoie le manifest prégénéré du scénario `sid` (ou {} absent/corrompu)."""
    import json as _json
    try:
        base = os.path.join(ctx.data_dir, "images_scenes", "pregen")
        p = os.path.join(base, f"{sid}.json")
        if not os.path.isfile(p):
            return {}
        with open(p, encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                                               # noqa: BLE001
        return {}


def serve_scene_si_pregen(
    ctx: ToolContext,
    titre: str,
    description: str = "",
    sid: str = "",
) -> Optional[str]:
    """Sert en cache une scène prégénérée correspondant à `titre`/`description`.

    Variante « hook post-tour » : d'abord un match EXACT par slug
    (`_scene_pregen_cache`), sinon un match FUZZY (normalisé accent/casse,
    sous-chaîne) sur le manifest du scénario `sid`. Sert l'image prégénérée
    et renvoie son URL publique, ou None. N'appelle JAMAIS ComfyUI (cache
    uniquement) — d'où la sécurité d'un hook automatique post-tour.
    """
    if not (titre or description):
        return None
    # 1) Exact (comportement historique : illustration_scene / _scene_pregen_cache)
    pregen = _scene_pregen_cache(ctx, titre, description)
    if pregen:
        return _url_for(pregen, ctx.data_dir)
    # 2) Fuzzy : on cherche dans le manifest du scénario courant une clé dont
    #    le libellé normalisé est inclus dans le lieu narré (ou l'inverse).
    if not sid:
        try:
            from ..game.state import PartyState
            etat = PartyState(data_dir=str(ctx.data_dir), partie_id=ctx.partie_id).load()
            src = str((etat.get("quete") or {}).get("source") or "")
            sid = src.split("]", 1)[0].lstrip("[").strip() or ""
        except Exception:                                           # noqa: BLE001
            pass
    if sid:
        manifest = _manifest_scenario(ctx, sid)
        if manifest:
            tgt = _norm_slug(titre or description)
            for key, val in manifest.items():
                cand = _norm_slug(key)
                if cand and (cand in tgt or tgt in cand):
                    fpath = os.path.join(
                        ctx.data_dir, "images_scenes", "pregen", str(val.get("file") or "")
                    )
                    if os.path.isfile(fpath):
                        return _url_for(fpath, ctx.data_dir)
    return None


@tool
async def illustration_scene(ctx: ToolContext, description: str, titre: str = "") -> ToolResult:
    """
    Illustre une scène importante de l'aventure (combat héroïque, révélation
    dramatique, découverte majestueuse, trahison…) par une image générée.
    L'image apparaît dans la galerie « Scènes » du panneau droit des joueurs.
    À utiliser avec parcimonie, pour les moments marquants uniquement.

    :param description (str): ce que montre la scène — lieu, protagonistes,
        action, ambiance (ex : « le héros affronte un dragon noir dans une
        caverne inondée, éclair par un éclair »).
    :param titre (str): titre court optionnel de la scène (ex :
        « L'autel maudit ») ; sert d'étiquette et de nom de fichier.
    """
    description = (description or "").strip()
    if not description:
        return ToolResult(text="❌ Décris la scène à illustrer.")
    # Toggle runtime (config `image.scenes_enabled` ou bouton du GUI) :
    # les scènes seules sont coupées — monstres, portraits et illustrations
    # de donjon restent générés.
    try:
        from ..config import get_config
        if not get_config().image.scenes_enabled:
            return ToolResult(
                text="🚫 Illustration de scène désactivée (réglage du tableau "
                     "de bord) — poursuis la narration sans image."
            )
    except Exception:                                           # noqa: BLE001
        pass  # config illisible → comportement historique (générer)
    titre = (titre or "").strip()
    cache_dir = os.path.join(ctx.data_dir, "images_scenes")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as e:
        return ToolResult(text=f"❌ Dossier images inaccessible : {e}")
    dest = os.path.join(cache_dir, f"{_slug_image(titre or description)}.png")
    url = _url_for(dest, ctx.data_dir)

    # Scène PRÉ-GÉNÉRÉE ? (scripts/pregen_scenes.py) → cache hit instantané,
    # on sert l'image sans solliciter ComfyUI.
    pregen = _scene_pregen_cache(ctx, titre, description)
    if pregen:
        used_url = _url_for(pregen, ctx.data_dir)
        libelle = titre or (description[:60] + ("…" if len(description) > 60 else ""))
        cb = getattr(ctx, "on_event", None)
        if cb is not None:
            try:
                await cb({
                    "type": "image",
                    "usage": "lieu",
                    "image": used_url,
                    "msg": f"🖼️ Scène illustrée (cache) : {libelle}",
                })
            except Exception:                                       # noqa: BLE001
                pass
        return ToolResult(
            text=f"🖼️ Scène illustrée (cache prégénérité) (« {libelle} ») : {used_url}",
            state_patch={"image_scene": used_url},
        )

    from ..image.helpers import generer_averti, scene_prompt
    r = await generer_averti(ctx, "lieu", scene_prompt(description), dest)
    if not r:
        return ToolResult(
            text="❌ Générateur d'images indisponible — scène non illustrée."
        )
    libelle = titre or (description[:60] + ("…" if len(description) > 60 else ""))
    # Événement temps réel → bascule la galerie du front sur l'onglet Scènes.
    cb = getattr(ctx, "on_event", None)
    if cb is not None:
        try:
            await cb({
                "type": "image",
                "usage": "lieu",
                "image": url,
                "msg": f"🖼️ Scène illustrée : {libelle}",
            })
        except Exception:
            pass
    return ToolResult(
        text=f"🖼️ Scène illustrée (« {libelle} ») : {url}",
        state_patch={"image_scene": url},
    )
