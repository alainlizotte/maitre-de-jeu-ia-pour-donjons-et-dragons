"""Outil Jets de dés — adapté de `Outil_JetsDes.py`.

Différences vs la version OpenWebUI :
- Plus de classe `Tools` ni de `pydantic.Valves`. La graine (seed) vient du
  contexte de la partie si besoin (extension future).
- Méthodes `def (...) -> str` devenues `@tool` async renvoyant ToolResult.
- Pas d'`__event_emitter__` : les events sont portés par ToolResult.events.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from typing import Optional

from .base import ToolContext, ToolResult, tool


def _norm_arme(s: str) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", n).split())


# Mots-clés de SORTS/effets : un « nom d'arme » qui en contient un n'est PAS
# une arme du catalogue — la validation de dé doit alors être désactivée
# (un sort inflige ses propres dés, ex. Boule de feu 5d6).
_MOTS_SORT = (
    "sort", "sphere", "boule", "trait", "rayon", "projectile", "souffle",
    "eclair", "cone", "nuage", "flamme", "explosion", "tempete",
    "invocation", "doigt", "orbe", "malediction", "mot", "cri",
)


def _est_nom_de_sort(nom: str) -> bool:
    """Vrai si `nom` contient un mot-clé de SORT/effet : son jet de dégâts
    n'est soumis ni au catalogue ni à la fiche d'une arme (Boule de feu 5d6
    n'a pas les dés d'arme de son porteur)."""
    c = _norm_arme(nom)
    return bool(c) and any(
        re.search(r"(?:^|\s)" + re.escape(m) + r"(?:\s|$)", c)
        for m in _MOTS_SORT
    )


_ARMES_CATALOGUE: Optional[dict] = None


def _armes_catalogue() -> dict:
    """Cache local du catalogue d'armes (nom normalisé → entrée)."""
    global _ARMES_CATALOGUE
    if _ARMES_CATALOGUE is None:
        try:
            from ..catalogue import ARMES  # pylint: disable=import-outside-toplevel
            _ARMES_CATALOGUE = {_norm_arme(a.get("nom")): a for a in ARMES}
        except Exception:                                    # noqa: BLE001
            _ARMES_CATALOGUE = {}
    return _ARMES_CATALOGUE


def _armes_portees(fiche: dict) -> list[dict]:
    """Armes du catalogue présentes dans l'équipement ou l'inventaire d'une
    fiche (B27) : liste de {nom, normalisé, entrée catalogue}."""
    from .inventaire import _inventaire as _inv  # pylint: disable=C0415
    try:
        from ..catalogue import ARMES as _armes_cat  # pylint: disable=C0415
        cat = {_norm_arme(a.get("nom")): a for a in _armes_cat}
    except Exception:                                        # noqa: BLE001
        cat = {}
    vues: set[str] = set()
    out: list[dict] = []
    for e in (fiche.get("equipement") or []) + (_inv(fiche) or []):
        if not isinstance(e, dict):
            continue
        nom = str(e.get("nom") or "").strip()
        if not nom:
            continue
        n = _norm_arme(nom)
        if n in vues or not n:
            continue
        vues.add(n)
        entree = cat.get(n)
        if entree:
            out.append({"nom": nom, "norm": n, "cat": entree})
    return out


def _arme_correspondante(demandee: str, portees: list[dict]) -> Optional[dict]:
    """Arme portée qui correspond le mieux au nom demandé (B27).

    Retourne None si aucune arme portée ne correspond — le nom fourni par le
    modèle désignait une arme que le personnage ne possède pas."""
    cible = _norm_arme(demandee)
    if not cible:
        return None
    for a in portees:
        if a["norm"] == cible:
            return a
    # Correspondance partielle : « épée longue +1 » → « Épée longue ».
    for a in portees:
        if a["norm"] and (a["norm"] in cible or cible in a["norm"]):
            return a
    # Un mot significatif commun (≥ 4 lettres) : « dague » vs « dague de fer ».
    for a in portees:
        for mot in cible.split():
            if len(mot) >= 4 and mot in a["norm"].split():
                return a
    return None


def _de_arme_catalogue(nom: str) -> Optional[tuple[int, int, str]]:
    """(nb_des, faces, nom_canonique) des dégâts de base d'une arme du
    catalogue, ou None si `nom` ne désigne pas une arme (sort, objet…).

    Tolère les seuls qualificatifs de fabrication/matière (« de maître »,
    « +1 », « en argent ») : tout autre mot restant après retrait du nom
    d'arme (« Dague de glace » → « glace ») désactive la validation, car il
    s'agit alors d'un sort ou d'une créature, pas de l'arme du catalogue."""
    cible = _norm_arme(nom)
    if not cible:
        return None
    if _est_nom_de_sort(nom):
        return None
    cible = re.sub(r"\b(?:de maitre|masterwork|magique|enchant\w*)\b", " ", cible)
    cible = re.sub(r"\b\d+\b", " ", cible)      # bonus magique (+1 → « 1 »)
    cible = " ".join(cible.split())
    _stop = {"de", "du", "la", "le", "les", "d", "a", "au", "aux", "en",
             "acier", "fer", "argent", "adamantium", "mithral", "bois", "chene"}
    for an, arme in _armes_catalogue().items():
        if not an:
            continue
        if not re.search(r"(?:^|\s)" + re.escape(an) + r"(?:\s|$)", cible):
            continue
        reste = [w for w in cible.replace(an, " ", 1).split()
                 if w and w not in _stop]
        if reste:
            continue                    # mots étrangers → pas cette arme
        m = re.match(r"(\d+)\s*d\s*(\d+)", str(arme.get("degats") or ""))
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), str(arme.get("nom"))
    return None


def _arme_fiche_monstre(
    ctx: ToolContext, nom: str,
) -> Optional[tuple[str, int, int]]:
    """(nom d'arme, nb_des, faces) de l'arme du MONSTRE `nom` lus sur sa
    fiche bestiaire, ou None si `nom` n'est pas un monstre connu (ou si ses
    attaques sont illisibles).

    La fiche du monstre prime sur le catalogue du joueur : les créatures de
    taille G/TG manient des armes aux dés propres (Géant (froid) de taille
    G — « grande hache » 3d6+13, alors que le catalogue n'a la Grande hache
    qu'en 1d12, taille M). Valider ces jets contre le catalogue rejetait
    l'attaque automatique du bestiaire et les dégâts n'étaient JAMAIS
    appliqués. Recherche LECTURE SEULE : aucun monstre générique n'est créé
    ici (contrairement au fallback de combat._attaque_auto)."""
    if not str(nom or "").strip():
        return None
    try:
        from .monstres import _find_monstre                # noqa: PLC0415
        from ..game.combat import _arme_du_bestiaire       # noqa: PLC0415
        m = _find_monstre(ctx, str(nom))
        arme = _arme_du_bestiaire(m) if m else None
    except Exception:                                      # noqa: BLE001
        return None
    if not arme:
        return None
    return str(arme[0]), int(arme[2]), int(arme[3])


def _cite_arme(texte: str, nom_arme: str) -> bool:
    """Vrai si `texte` désigne l'arme `nom_arme` (mots entiers, accents et
    casse ignorés) : « grande hache du géant » cite « grande hache »."""
    t = _norm_arme(texte)
    a = _norm_arme(nom_arme)
    if not t or not a:
        return False
    return t == a or bool(
        re.search(r"(?:^|\s)" + re.escape(a) + r"(?:\s|$)", t)
    )



def _mod(c: int) -> int:
    """Modificateur D&D 3.5 d'une caractéristique (formule officielle (c-10)//2)."""
    return (c - 10) // 2


def _fiche_pj(ctx: ToolContext, nom: str) -> Optional[dict]:
    """Charge la fiche d'un PJ si elle existe (sinon None)."""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        import os as _os                             # noqa: I001
        path = _chemin(ctx, nom)
        if _os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:                                        # noqa: BLE001
        pass
    return None


def _ca_officielle(ctx: ToolContext, nom_cible: str) -> tuple[Optional[int], str]:
    """CA canonique d'une cible : fiche du PJ si joueur, sinon entrée de
    combat en cours (éventuellement ajustée temporairement), sinon bestiaire.

    Renvoie `(ca, source)` — ca=None si la cible est inconnue des sources.
    Évite que le LLM invente une CA trop basse pour toucher facilement.
    """
    fiche = _fiche_pj(ctx, nom_cible)
    if fiche is not None and fiche.get("ca") is not None:
        try:
            # ✨ Objets magiques de protection (anneau de protection,
            # amulette naturelle, brassards d'armure, casque…) : +N CA au
            # moment du jet — l'équipement peut être ajouté EN JEU, la CA
            # stockée à la création serait sinon obsolète.
            from .fiches import bonus_ca_magie  # lazy : évite les cycles
            mag = bonus_ca_magie(fiche)
            return int(fiche["ca"]) + mag, (
                f"fiche de {nom_cible}"
                + (f" (+{mag} CA magique)" if mag else "")
            )
        except (TypeError, ValueError):
            pass
    # Combat en cours : l'entrée suivie prime sur le bestiaire (elle porte
    # l'ajustement temporaire d'équilibrage — cf. engager_combat).
    try:
        import unicodedata as _ud
        nn = "".join(
            c for c in _ud.normalize("NFKD", str(nom_cible or "").lower())
            if not _ud.combining(c)
        )
        from ..game.state import PartyState  # lazy : évite les cycles
        etat_c = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
        for mo in etat_c.get("monstres_combat") or []:
            nm = "".join(
                c for c in _ud.normalize(
                    "NFKD", str(mo.get("nom") or "").lower()
                ) if not _ud.combining(c)
            )
            if nm == nn and mo.get("ca") is not None:
                tag = "ajusté, " if mo.get("_ajuste") else ""
                return int(mo["ca"]), (
                    f"combat en cours ({tag}{mo.get('nom', nom_cible)})"
                )
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from .monstres import _find_monstre   # lazy : évite les imports circulaires
        m = _find_monstre(ctx, nom_cible)
        if m is not None and m.get("ca") is not None:
            return int(m["ca"]), (
                f"bestiaire ({m.get('nom', nom_cible)}, FP {m.get('fp', '?')})"
            )
    except Exception:                                    # noqa: BLE001
        pass
    return None, ""


def _as_int(v: Any, defaut: int = 0) -> int:
    """Coerce un argument numérique du LLM (peut manquer ou arriver en str)."""
    try:
        return int(str(v).strip() or defaut)
    except (TypeError, ValueError):
        return defaut


# --------------------------------------------------------------------------- #
#  🎯 Mémo des sources de dégâts (chantier DRS) — lancer_degats enregistre la
#  source (arme ou sort) du dernier jet par cible ; `_infliger_degats_monstre`
#  (fiches.py) la relit pour appliquer RD / résistances énergie / immunités.
# --------------------------------------------------------------------------- #
_DERNIERS_DEGATS: dict[tuple, dict] = {}


def enregistrer_source_degats(
    partie_id: str, tour_id: str, cible: str, source: str,
    non_letal: bool = False,
) -> None:
    _DERNIERS_DEGATS[(str(partie_id or ""), str(tour_id or ""),
                      _norm_arme(cible))] = {
        "source": str(source or ""), "non_letal": bool(non_letal)}


def dernier_source_degats(partie_id: str, tour_id: str, cible: str) -> dict:
    return dict(_DERNIERS_DEGATS.get(
        (str(partie_id or ""), str(tour_id or ""), _norm_arme(cible)) or {},
        {"source": "", "non_letal": False}))


# --------------------------------------------------------------------------- #
#  ⚡ Effets mécaniques des CONDITIONS (chantier DRS P1-#4) — DMG 3.5
#  « States préjudiciables ». Renvoient des modificateurs réellement
#  appliqués aux jets d'attaque / CA / chances de rater, plus des notes.
# --------------------------------------------------------------------------- #
def _conditions_de(ctx: ToolContext, nom: str) -> list[str]:
    """Conditions d'un combattant : fiche PJ si elle existe, sinon l'entrée
    `monstres_combat` du combat en cours."""
    fiche = _fiche_pj(ctx, nom)
    if fiche is not None:
        return [str(c) for c in (fiche.get("conditions") or [])]
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        etat = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
        nn = _norm_arme(nom)
        for mo in etat.get("monstres_combat") or []:
            if _norm_arme(mo.get("nom")) == nn:
                return [str(c) for c in (mo.get("conditions") or [])]
    except Exception:                                        # noqa: BLE001
        pass
    return []


def _ncond(conds: list[str]) -> set[str]:
    return {_norm_arme(c) for c in conds}


def modificateurs_conditions_attaque(
    conds_attaquant: list[str], conds_cible: list[str],
    a_distance: bool = False,
) -> dict:
    """Modificateurs 3.5 des conditions sur un jet d'attaque.

    Retour : {att: int, ca: int, miss_pct: int, notes: list[str]}
    — `att` : modificateur du jet d'attaque de l'attaquant ;
    — `ca` : ajustement de la CA de la cible (Dex perdue, etc.) ;
    — `miss_pct` : % de chances de rater (camouflage, invisibilité, aveuglé).
    """
    out = {"att": 0, "ca": 0, "miss_pct": 0, "notes": []}
    a = _ncond(conds_attaquant)
    c = _ncond(conds_cible)

    def note(txt: str) -> None:
        out["notes"].append(txt)

    # --- Attaquant -----------------------------------------------------------
    if "epuise" in a:
        out["att"] -= 6
        note("attaquant Épuisé (−6)")
    elif "fatigue" in a:
        out["att"] -= 2
        note("attaquant Fatigué (−2)")
    if "empetre" in a:
        out["att"] -= 2
        note("attaquant Empêtré (−2)")
    if "agrippe" in a or "saisi" in a:
        out["att"] -= 2
        note("attaquant Agrippé/Saisi (−2)")
    if "renverse" in a:
        out["att"] -= 4
        note("attaquant Renversé (−4)")
    if "secoue" in a or "effraye" in a:
        out["att"] -= 2
        note("attaquant Secoué/Effrayé (−2)")
    if "fragilise" in a:
        out["att"] -= 2
        note("attaquant Fragilisé (−2)")
    if "titubant" in a:
        out["att"] -= 2
        note("attaquant Titubant (−2, pas d'attaque totale)")
    if "aveugle" in a:
        out["miss_pct"] = max(out["miss_pct"], 50)
        note("attaquant Aveuglé — cible à camouflage TOTAL (50 % de rater)")
    # --- Cible ---------------------------------------------------------------
    if "renverse" in c:
        if not a_distance:
            out["ca"] -= 4
            note("cible Renversée (−4 CA au corps à corps, Dex perdue)")
        else:
            out["ca"] += 4
            note("cible Renversée (+4 CA à distance, Dex perdue)")
    if "aveugle" in c or "inconscient" in c or "endormi" in c \
            or "paralyse" in c or "petrifie" in c or "titubant" in c:
        out["ca"] -= 2
        note("cible sans bonus de Dextérité (−2 CA)")
    if "empetre" in c:
        out["ca"] -= 2
        note("cible Empêtrée (Dex −4 → −2 CA)")
    if "agrippe" in c or "saisi" in c:
        out["ca"] -= 2
        note("cible Agrippée/Saisie (Dex perdue, −2 CA)")
    if "pris en tenaille" in c:
        out["att"] += 2
        note("cible Prise en tenaille (+2 attaque)")
    if "invisible" in c:
        out["miss_pct"] = max(out["miss_pct"], 50)
        note("cible Invisible — camouflage TOTAL (50 % de rater)")
    return out


def _confirm_critique(bonus_final: int, ca_cible: int) -> tuple[bool, int]:
    """Second jet de confirmation d'un critique (3.5 : un jet d'attaque qui
    touche à nouveau la CA confirme ; 20 = confirmation automatique)."""
    jet = random.randint(1, 20)
    return (jet == 20 or (jet + bonus_final >= ca_cible and jet != 1)), jet


# --------------------------------------------------------------------------- #
#  💥 AUTO-DÉGÂTS (correctif structural, run hurl 090ec0af) : sur un TOUCHÉ,
#  `lancer_attaque` roule et applique LUI-MÊME les dégâts de l'arme. Le
#  modèle oubliait `lancer_degats` après un hit — les créatures semblaient
#  invulnérables et trois groupes niveau 1 ont péri (TPC). Les dégâts
#  AUTONOMES (sorts, poisons, chutes) passent toujours par `lancer_degats`.
# --------------------------------------------------------------------------- #
_AUTO_DEGATS_TOUR: dict[tuple[str, str, str], dict[str, object]] = {}


def _bonus_degats_attaquant(ctx: ToolContext, nom_attaquant: str,
                            arme: str) -> int:
    """Bonus de dégâts officiel de l'ATTAQUANT PJ (mod. FOR, ×1,5 à deux
    mains, 0 à distance) lu sur sa fiche — 0 si fiche absente ou cas non
    couvert. Pour un MONSTRE, le bonus du bestiaire est parsé séparément."""
    try:
        from .fiches import _load_fiche                # noqa: PLC0415
        fiche = _load_fiche(ctx, nom_attaquant)
        if fiche is None:
            return 0
        caracs = fiche.get("carac") or {}
        if isinstance(carac, str):
            import json as _json
            try:
                caracs = _json.loads(carac)
            except ValueError:
                caracs = {}
        a_l = (arme or "").lower()
        if any(m in a_l for m in ("arc", "arbalète", "arbalet", "fronde",
                                  "javelot", "dard", "sarbacane")):
            return 0
        val = int((caracs or {}).get("FOR", 10) or 10)
        mod = (val - 10) // 2
        if any(m in a_l for m in ("deux mains", "2 mains")):
            return mod * 3 // 2
        return mod
    except Exception:                                    # noqa: BLE001
        return 0


def _cible_degats_auto(ctx: ToolContext, nom_cible: str) -> str:
    """Corrige la cible d'un auto-dégât : inconnue du plateau + UN SEUL
    ennemi vivant suivi → c'est LUI (la fiction ne peut pas diverger sur un
    combat à un ennemi — même règle que main._resoudre_cible_degats)."""
    try:
        from ..game.state import PartyState              # noqa: PLC0415
        etat = PartyState(data_dir=ctx.data_dir,
                          partie_id=ctx.partie_id).load()
        n = _norm_arme(nom_cible)
        if not n:
            return nom_cible
        for p in (etat.get("pj") or []):
            if _norm_arme(p.get("nom")) == n:
                return nom_cible
        mons = etat.get("monstres_combat") or []
        for mo in mons:
            if _norm_arme(mo.get("nom")) == n:
                return nom_cible
        vivants = [
            mo for mo in mons
            if not mo.get("allie")
            and int(mo.get("pv", 0) or 0) > 0
            and "Détruit" not in (mo.get("conditions") or [])
            and "Detruit" not in (mo.get("conditions") or [])
        ]
        if len(vivants) == 1:
            corrige = str(vivants[0].get("nom") or "")
            if corrige:
                return corrige
    except Exception:                                    # noqa: BLE001
        pass
    return nom_cible


def _auto_degats_de_arme(ctx: ToolContext, nom_attaquant: str,
                         arme: str,
                         crit_confirme: bool) -> Optional[tuple[int, str]]:
    """Dégâts de l'arme de l'attaquant (roulés serveur) : bestiaire pour un
    MONSTRE (dés + bonus du champ `degs`), catalogue PHB + FOR pour un PJ.
    Renvoie (dégâts, détail) ou None si l'arme est indéterminable."""
    _nb, _faces, _bonus_d = None, None, 0
    _detail_src = ""
    _fiche_m = (_arme_fiche_monstre(ctx, nom_attaquant)
                if str(nom_attaquant or "").strip() else None)
    if _fiche_m is not None and (not arme or _arme_citee(arme, _fiche_m[0])):
        _, _nb, _faces = _fiche_m
        _detail_src = f"bestiaire ({_fiche_m[0]})"
        try:
            from .monstres import _find_monstre                # noqa: PLC0415
            _m_b = _find_monstre(ctx, nom_attaquant) or {}
            _degs_b = str(_m_b.get("degs") or "")
            _seg = next(
                (sg for sg in _degs_b.split(";")
                 if _norm_arme(_fiche_m[0]) in _norm_arme(sg)),
                _degs_b.split(";")[0] if _degs_b else "")
            import re as _re_b
            _md = _re_b.search(r"([+-]\s*\d+)\s*(?:\.|$)", _seg.strip())
            if _md:
                _bonus_d = int(_md.group(1).replace(" ", ""))
        except Exception:                                # noqa: BLE001
            _bonus_d = 0
    else:
        _cat = _de_arme_catalogue(arme)
        if _cat is None:
            return None
        _nb, _faces, _nom_canon = _cat
        _detail_src = f"catalogue ({_nom_canon})"
        _bonus_d = _bonus_degats_attaquant(ctx, nom_attaquant, arme)
    _mult = 2 if crit_confirme else 1
    _deg = sum(random.randint(1, _faces)
               for _ in range(_nb * _mult)) + _bonus_d * _mult
    _deg = max(1, _deg)
    detail = (f"{_nb * _mult}d{_faces}"
              + (f"{_bonus_d * _mult:+d}" if _bonus_d else "")
              + f" ({_detail_src}"
              + (", critique ×2" if _mult == 2 else "")
              + ")")
    return _deg, detail


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def lancer_d20(
    ctx: ToolContext,
    modificateur: Any = 0,
    raison: str = "",
    difficulte: Optional[int] = None,
    nom_personnage: str = "",
    competence: str = "",
) -> ToolResult:
    """
    Lance un d20 et ajoute un modificateur. Renvoie formule, jet brut, total et
    réussite/échec (si `difficulte` fournie). À utiliser pour tout jet de
    résolution D&D 3.5 (compétence, carac, sauvegarde spéciale).

    :param modificateur (int): modificateur applicable (carac + rangs + divers).
    :param raison (str): brève description du jet (ex. "Escalade d'un mur de 6 m").
    :param difficulte (int): DD à atteindre pour réussir. Optionnel.
    :param nom_personnage (str): nom du PJ qui tente le jet. Avec `competence`,
        le modificateur est recalculé depuis sa fiche (rangs + mod. carac) —
        à fournir systématiquement pour un jet de compétence.
    :param competence (str): compétence concernée (ex. "Discrétion", "Fouille").
    """
    # --- Recoupement fiche (conformité 3.5) --------------------------------
    # Un petit LLM fournit souvent modificateur=0 en ignorant les rangs et le
    # mod. de caractéristique. Si nom_personnage + competence sont donnés et
    # que la fiche contient des rangs, on recalcule rangs + mod. carac.
    modificateur = _as_int(modificateur)
    if difficulte is not None:
        difficulte = _as_int(difficulte, 10)
    mod_final = modificateur
    note_mod = ""
    if nom_personnage and competence:
        try:
            from .fiches import _chemin, bonus_dons_effet  # pylint: disable=import-outside-toplevel
            import os as _os                             # noqa: I001
            import unicodedata as _uni

            def _norm(s: str) -> str:
                nf = _uni.normalize("NFKD", (s or "").lower())
                return "".join(c for c in nf if not _uni.combining(c)).strip()

            path = _chemin(ctx, nom_personnage)
            if _os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    fiche = json.load(f)
                comps = fiche.get("competences") or {}
                rangs = 0
                for nom_c, r in comps.items():
                    if _norm(nom_c) == _norm(competence):
                        rangs = int(r or 0)
                        break
                # Facteur hors-classe (PHB 3.5) : les rangs d'une compétence
                # HORS classe de la fiche comptent pour moitié (x0,5).
                rangs_bruts = rangs
                hors_classe = False
                try:
                    from ..catalogue import COMPETENCES_CLASSE  # noqa: PLC0415
                    from ..persos import resoudre_classe        # noqa: PLC0415
                    _classe_c = resoudre_classe(
                        str(fiche.get("classe") or ""))
                    _comp_classe = COMPETENCES_CLASSE.get(_classe_c, [])
                    if _comp_classe and not any(
                        _norm(c) == _norm(competence)
                        for c in _comp_classe
                    ):
                        rangs = rangs // 2
                        hors_classe = True
                except Exception:                            # noqa: BLE001
                    pass
                # Dons passifs sur cette compétence (ex. Alerte → +2
                # Détection / Perception auditive) : s'applique même à
                # rang nul (guerrier avec Alerte et 0 rang en Détection).
                bonus_don = bonus_dons_effet(
                    fiche.get("dons"), "comp_" + _norm(competence)
                )
                # Capacités raciales + faculté de familier (Sens aiguisés de
                # l'elfe → +2 Détection ; le chat → +3 Déplacement
                # silencieux au maître) : même règle, rangs nuls compris.
                try:
                    from .fiches import bonus_race_familier  # noqa: PLC0415
                    bonus_rf = bonus_race_familier(
                        fiche, "comp_" + _norm(competence))
                except Exception:                            # noqa: BLE001
                    bonus_rf = 0
                if rangs > 0 or bonus_don or bonus_rf:
                    from ..catalogue import COMPETENCES  # pylint: disable=import-outside-toplevel
                    cara_cle = next(
                        (c["cara"] for c in COMPETENCES
                         if _norm(c["nom"]) == _norm(competence)), "DEX",
                    )
                    val = int((fiche.get("carac") or {}).get(cara_cle, 10) or 10)
                    calc = rangs + (val - 10) // 2 + bonus_don + bonus_rf
                    # ⚖️ Charge (chantier DRS P2-#12) : une charge Moyenne
                    # ou Lourde applique un malus type « malus d'armure »
                    # (−3 / −6) aux tests basés sur FOR ou DEX (PHB 3.5,
                    # Table : effets de la charge).
                    _pen_charge = 0
                    try:
                        from .inventaire import (  # noqa: PLC0415
                            _recalculer_charge,
                        )
                        _cat_c = str(
                            fiche.get("etat_encumbrance") or "")
                        if not _cat_c:
                            _cat_c = _recalculer_charge(fiche)[1]
                        if cara_cle in ("FOR", "DEX"):
                            if _cat_c == "Moyenne":
                                _pen_charge = -3
                            elif _cat_c in ("Lourde", "Depassee"):
                                _pen_charge = -6
                        calc += _pen_charge
                    except Exception:                            # noqa: BLE001
                        _pen_charge = 0
                    if calc != modificateur:
                        mod_final = calc
                        detail = (
                            f"{competence} "
                            + (f"{rangs} rangs" if not hors_classe else
                               f"{rangs} rangs (½ de {rangs_bruts}, hors "
                               "classe)")
                            + f" + {cara_cle} {val} ({(val - 10) // 2:+d})"
                        )
                        if bonus_don:
                            detail += f" + {bonus_don} (dons)"
                        if bonus_rf:
                            detail += f" + {bonus_rf} (race/familier)"
                        if _pen_charge:
                            detail += f" {_pen_charge} (charge)"
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{calc:+d} (fiche de {nom_personnage} : {detail})."
                        )
        except Exception:                                       # noqa: BLE001
            pass  # fiche/compétence indisponible → modificateur fourni

    jet = random.randint(1, 20)
    total = jet + mod_final
    critique = jet == 20
    fumble = jet == 1
    lignes = [
        f"🎯 **Jet de d20** — {raison}",
        f"- Jet brut : {jet}",
        f"- Modificateur : {mod_final:+d}" + note_mod,
        f"- **Total : {total}**",
    ]
    if critique:
        lignes.append("- ⭐ **20 naturel** → réussite automatique (critique éventuel).")
    if fumble:
        lignes.append("- ❌ **1 naturel** → échec automatique (maladresse).")
    if difficulte is not None and not critique and not fumble:
        ok = total >= difficulte
        lignes.append(
            f"- DD {difficulte} → " + ("✅ **Réussite**." if ok else "❌ **Échec**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def calculer_initiative(ctx: ToolContext, participants: str) -> ToolResult:
    """
    Calcule l'ordre d'initiative D&D 3.5 pour un combat. Chaque participant
    obtient 1d20 + modificateur de Dextérité. Renvoie la liste triée par
    initiative décroissante, prête à afficher en début de combat.

    :param participants (str): format "Nom1:+ModDex1, Nom2:+ModDex2, …"
        (ex. "Groth:+1, Jannedarc:+0, Gobelin:+2"). Le modificateur peut être
        négatif (ex. "-1"). L'IA fournit les valeurs depuis les fiches/manuel.
    """
    try:
        items: list[tuple[str, int]] = []
        for chunk in participants.split(","):
            name, _, mod = chunk.partition(":")
            name = name.strip()
            mod = mod.strip().replace(" ", "")
            if name and mod:
                items.append((name, int(mod)))
    except ValueError as e:
        return ToolResult(
            text=f"⚠️ Format invalide : {e}\nAttendu : 'Nom1:+Mod, Nom2:-Mod, …'"
        )

    # --- Recoupement fiches PJ ---------------------------------------------
    # Pour un PJ (fiche présente), l'initiative officielle = mod. DEX + dons
    # (Initiative améliorée = +4). La valeur du LLM est ignorée si elle
    # diffère ; les monstres (sans fiche) gardent le modificateur fourni.
    notes: list[str] = []
    try:
        from .fiches import bonus_dons_effet  # pylint: disable=import-outside-toplevel
        ajustes: list[tuple[str, int]] = []
        for name, mod in items:
            fiche = _fiche_pj(ctx, name)
            if fiche is None:
                ajustes.append((name, mod))
                continue
            caracs = fiche.get("carac") or {}
            try:
                dex = int(caracs.get("DEX", 10) or 10)
            except (TypeError, ValueError):
                dex = 10
            bonus_don = bonus_dons_effet(fiche.get("dons"), "initiative")
            mod_off = (dex - 10) // 2 + bonus_don
            if mod_off != mod:
                detail = f"DEX {dex} ({(dex - 10) // 2:+d})"
                if bonus_don:
                    detail += f" + {bonus_don} (dons)"
                notes.append(
                    f"- ⚠️ {name} : initiative recalculée {mod:+d} → "
                    f"{mod_off:+d} (fiche : {detail})."
                )
                mod = mod_off
            ajustes.append((name, mod))
        items = ajustes
    except Exception:                                            # noqa: BLE001
        pass  # fiches indisponibles → modificateurs fournis

    jets = []
    for name, mod in items:
        jet = random.randint(1, 20)
        total = jet + mod
        jets.append({"nom": name, "init": total, "jet_brut": jet, "mod": mod})
    jets.sort(key=lambda x: x["init"], reverse=True)

    lignes = ["🎲 **Initiative (combat)**"]
    for i, e in enumerate(jets, 1):
        lignes.append(
            f"{i}. **{e['nom']}** — Initiative "
            f"{e['init']} (d20={e['jet_brut']}, mod={e['mod']:+d})"
        )
    lignes.extend(notes)
    lignes.append("\nOrdre : " + " → ".join(e["nom"] for e in jets))
    lignes.append("\n_C'est à la plus haute initiative d'agir la première._")
    return ToolResult(
        text="\n".join(lignes),
        # Synchro UI : on expose l'ordre pour le panneau initiative du front.
        state_patch={"initiative": jets, "courant_tour_pour": jets[0]["nom"] if jets else None},
    )


def _munition_pour_arme(arme_txt: str) -> str:
    """Munition consommée par une arme à distance (règles 3.5 : chaque tir
    dépense 1 projectile). Vide = arme de mêlée ou jet sans arme."""
    a = (arme_txt or "").lower()
    import unicodedata as _ud
    a = "".join(
        c for c in _ud.normalize("NFD", a)
        if _ud.category(c) != "Mn"
    )
    if not a:
        return ""
    if "arbalete" in a or "arbalet" in a:
        return "carreau d'arbalète"
    if "sarbacane" in a:
        return "dard"
    if "fronde" in a:
        return "balle de fronde"
    if "javelot" in a:
        return "javelot"
    if "arc" in a:
        return "flèche"
    return ""


def _distance_proche(distance_m: Any) -> bool:
    """Vrai si `distance_m` est fournie et ≤ 9 m (portée du Tir de près,
    PHB 3.5 : 30 pieds). Absente ou illisible → False (pas de bonus)."""
    try:
        d = float(str(distance_m).strip().replace(",", ".").rstrip("m "))
    except (TypeError, ValueError):
        return False
    return 0 < d <= 9


@tool
async def lancer_attaque(
    ctx: ToolContext,
    bonus_attaque: Any = 0,
    ca_cible: Any = 10,
    nom_attaquant: str = "",
    arme: str = "",
    nom_cible: str = "",
    distance_m: Any = None,
) -> ToolResult:
    """
    Effectue un jet d'attaque D&D 3.5 contre une Classe d'Armure (CA) cible.
    Renvoie jet brut, total, et résultat (toucher / critique / maladresse / raté).
    Gère le 20 naturel (critique à confirmer) et le 1 naturel (maladresse).

    :param bonus_attaque (int): bonus total = BBA + mod. FOR (mêlée) ou
        mod. DEX (distance), lus sur la fiche du personnage — ne jamais
        inventer de bonus. Un recoupement automatique avec la fiche borne
        les valeurs manifestement erronées et APPLIQUE les dons passifs
        (Arme de prédilection +1, Tir de près +1 à ≤ 9 m, Attaque en
        finesse → DEX au lieu de FOR).
    :param ca_cible (int): Classe d'Armure de la cible. Recoupée
        automatiquement avec la fiche du PJ ou le bestiaire local — la valeur
        officielle prime toujours sur celle fournie.
    :param nom_attaquant (str): nom du personnage qui attaque.
    :param arme (str): nom de l'arme utilisée.
    :param nom_cible (str): nom de la cible.
    :param distance_m (float): distance estimée en mètres jusqu'à la cible
        (optionnel). À ≤ 9 m, le don « Tir de près » donne +1 à l'attaque
        (et +1 aux dégâts dans `lancer_degats`).
    """
    # --- CA officielle de la cible ------------------------------------------
    # Un petit LLM « arrange » parfois la CA pour faire toucher. On impose la
    # valeur des données officielles quand la cible est connue.
    # Arguments numériques blindés (le LLM peut omettre ou envoyer "12").
    bonus_attaque = _as_int(bonus_attaque)
    ca_cible = _as_int(ca_cible, 10)

    # --- Refus des appels sans attaquant/cible -------------------------------
    # Partie dfccc120 : `lancer_attaque{}` (arguments vides) résolvait
    # « Attaque : [] vs [] (CA 10) → Touché », enregistrée comme SUCCÈS dans
    # la trace — le tour semblait résolu alors qu'aucun coup n'avait été
    # joué. Un jet d'attaque sans attaquant ou sans cible est invalide : on
    # renvoie une erreur explicite pour que le modèle ré-appelle avec les
    # vrais noms (le recoupement CA/bonus n'est possible qu'avec eux).
    if not str(nom_attaquant or "").strip() or not str(nom_cible or "").strip():
        return ToolResult(
            text=(
                "❌ Jet d'attaque invalide : nom_attaquant et nom_cible sont "
                "OBLIGATOIRES (reçus : "
                f"attaquant={nom_attaquant!r}, cible={nom_cible!r}). "
                "Rappelle lancer_attaque avec le nom du personnage, la cible, "
                "l'arme, bonus_attaque (BBA + mod. FOR/DEX de la fiche) et "
                "ca_cible."
            )
        )

    # --- Cible déjà DÉTRUITE → redirection auto (partie 263f82dc) ----------
    # Le LLM attaquait des cadavres (« le squelette 3 » détruit au round
    # précédent) : le jet se résolvait contre un ennemi hors jeu, la
    # narration contredisait l'état et les tours se perdaient. Quand la
    # cible demandée ne correspond qu'à des créatures DÉTRUITES du combat
    # en cours (exact/préfixe), l'attaque est redirigée vers la première
    # créature VIVANTE correspondante — avec note explicite.
    cible_renote = ""
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        _etat_c = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
        _mc = [m for m in (_etat_c.get("monstres_combat") or [])
               if isinstance(m, dict)]
        if _mc:
            def _nn2(s: str) -> str:
                return "".join(
                    c for c in unicodedata.normalize(
                        "NFKD", str(s or "").strip().lower()
                    ) if not unicodedata.combining(c)
                )

            def _vivante(m: dict) -> bool:
                conds = m.get("conditions") or []
                return (
                    "Détruit" not in conds and "Detruit" not in conds
                    and int(m.get("pv", 0) or 0) > 0
                )

            _nc = _nn2(nom_cible)
            _matches = [m for m in _mc if _nn2(m.get("nom")) == _nc]
            if not _matches and _nc:
                _matches = [
                    m for m in _mc
                    if (len(_nc) >= 4
                        and _nn2(m.get("nom")).startswith(_nc))
                    or (len(_nn2(m.get("nom"))) >= 4
                        and _nc.startswith(_nn2(m.get("nom"))))
                ]
            if _matches and not any(_vivante(m) for m in _matches):
                _vivantes = [m for m in _mc if _vivante(m)]
                if _vivantes:
                    _nouvelle = _vivantes[0]
                    cible_renote = (
                        f"↪️ **Cible déjà DÉTRUITE** ({nom_cible}) — attaque "
                        f"redirigée vers **{_nouvelle.get('nom')}** "
                        "(seule cible valide encore debout)."
                    )
                    nom_cible = str(_nouvelle.get("nom") or nom_cible)
            # 🛡️ B16 (audit parties complètes) : si un combat est en cours et
            # que la cible ne correspond à AUCUNE créature engagée, le jet se
            # résolvait quand même contre un ennemi imaginaire — et le serveur
            # l'enrichissait depuis le bestiaire. On refuse et on liste les
            # cibles réelles.
            # 🛡️ (complément B16 — bug réel) : ce refus visait les cibles
            # IMAGINAIRES d'une attaque de PJ ; il s'appliquait aussi aux
            # attaques du MONSTRE contre un PJ (flux légitime, moteur
            # d'auto-attaque et tours LLM des monstres) — l'attaque
            # automatique des monstres ne se résolvait JAMAIS (0 dégâts).
            # Une cible qui est un PJ ENGAGÉ est valide : on ne refuse pas.
            if _mc and not _matches and not cible_renote:
                _pjs = [
                    _nn2(str(p.get("nom") or ""))
                    for p in (_etat_c.get("pj") or [])
                    if isinstance(p, dict)
                ]
                if _nc not in _pjs:
                    _valides = [str(m.get("nom") or "?") for m in _mc
                                if _vivante(m)]
                    return ToolResult(text=(
                        f"❌ **Cible absente du combat** : « {nom_cible} » "
                        "n'est pas une créature engagée. Cibles réellement "
                        "en jeu : "
                        + (", ".join(_valides) if _valides else "aucune")
                        + ". Rappelle `lancer_attaque` avec l'un de ces noms "
                        "(ou `engager_combat` d'abord si la rencontre n'a pas "
                        "été déclenchée)."
                    ))
    except Exception:                                           # noqa: BLE001
        pass  # hors combat / état indisponible → cible fournie

    # --- CA officielle de la cible ------------------------------------------
    # Un petit LLM « arrange » parfois la CA pour faire toucher. On impose la
    # valeur des données officielles quand la cible est connue.
    ca_off, src_ca = _ca_officielle(ctx, nom_cible)
    note_ca = ""
    if ca_off is not None:
        if ca_off != ca_cible:
            note_ca = (
                f"\n- ⚠️ CA imposée par les règles : {ca_cible} → {ca_off} "
                f"(source : {src_ca})."
            )
            ca_cible = ca_off

    # --- Recoupement fiche (conformité 3.5) --------------------------------
    # bonus_attaque = BBA + mod FOR (mêlée) / mod DEX (distance). Quand la
    # fiche de l'attaquant existe, la valeur OFFICIELLE PRIME : le LLM ne
    # choisit que QUI frappe QUI avec QUOI — le modificateur est recalculé
    # serveur (partie réelle : le LLM passait +5/+4 pour un guerrier niv. 1
    # à +2, et l'ancienne « marge +3 » laissait passer). Les monstres (sans
    # fiche PJ) conservent le bonus du bestiaire passé par le moteur.
    bonus_final = bonus_attaque
    note_bonus = ""
    note_ammo = ""
    note_degats = ""
    note_arme = ""
    # 🎯 Seuil de menace de critique : zone de base de l'ARME (cimeterre
    # 18-20, épée longue 19-20…) doublée par « Science de la critique ».
    # Monstres (sans fiche PJ) : 20 — leurs statblocks intègrent déjà dons
    # et zones de critique du bestiaire.
    seuil_crit = 20
    detail_crit = ""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        import os as _os                             # noqa: I001
        path = _chemin(ctx, nom_attaquant)
        if _os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                fiche = json.load(f)
            caracs = fiche.get("carac") or {}
            bab = int(fiche.get("bab") or 0)
            arme_l = (arme or "").lower()
            # 🛡️ B27 (audit parties complètes) : le nom d'arme fourni par le
            # modèle était recopié tel quel dans le bloc « officiel » — Aldric
            # (clerc, matraque) attaquait « [épée longue] », Brann
            # « [matraque] ». Le bloc censé faire foi mentait sur
            # l'équipement. On substitue l'arme réellement portée.
            _portees = _armes_portees(fiche)
            _choisie = _arme_correspondante(arme or "", _portees)
            if _portees and _choisie is None:
                _sub = _portees[0]
                note_arme = (
                    f"\n- ⚠️ Arme corrigée : « {arme} » n'est pas dans "
                    f"l'équipement de {nom_attaquant} → "
                    f"**{_sub['nom']}** (la fiche fait foi ; armes portées : "
                    + ", ".join(a["nom"] for a in _portees) + ")."
                )
                arme = _sub["nom"]
                arme_l = arme.lower()
            elif _portees and _choisie is not None \
                    and _norm_arme(arme or "") != _choisie["norm"]:
                note_arme = (
                    f"\n- ℹ️ Arme « {arme} » interprétée comme "
                    f"**{_choisie['nom']}**."
                )
                arme = _choisie["nom"]
                arme_l = arme.lower()
            else:
                note_arme = ""
            a_distance = any(
                m in arme_l for m in
                ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard", "sarbacane")
            )
            cle_car = "DEX" if a_distance else "FOR"
            # ✨ Objets magiques portés (PHB 3.5) — effets AUTOMATIQUES :
            # - arme enchantée « +N » → +N à l'attaque (et +N aux dégâts) ;
            # - gantelets d'ogre de force / bottes de dextérité → FOR/DEX
            #   EFFECTIVES (attaque, et dégâts de mêlée pour la FOR).
            bonus_mag = 0
            detail_mag = ""
            caracs_eff = dict(caracs)
            try:
                from .fiches import (                     # pylint: disable=import-outside-toplevel
                    bonus_arme_magique, bonus_carac_equipement,
                )
                bonus_mag = bonus_arme_magique(arme)
                caracs_eff["FOR"] = (
                    int(caracs.get("FOR", 10) or 10)
                    + bonus_carac_equipement(fiche, "FOR"))
                caracs_eff["DEX"] = (
                    int(caracs.get("DEX", 10) or 10)
                    + bonus_carac_equipement(fiche, "DEX"))
                if caracs_eff["FOR"] != int(caracs.get("FOR", 10) or 10):
                    detail_mag += f", FOR effective {caracs_eff['FOR']}"
                if caracs_eff["DEX"] != int(caracs.get("DEX", 10) or 10):
                    detail_mag += f", DEX effective {caracs_eff['DEX']}"
            except Exception:                                 # noqa: BLE001
                pass  # fiches.py indisponible → scores de fiche bruts
            val_car = int(caracs_eff.get(cle_car, 10) or 10)
            mod_car = (val_car - 10) // 2
            # 🗡️ Dons passifs d'attaque (PHB 3.5) — appliqués au calcul
            # officiel, jamais au-dessus de la valeur du LLM :
            # - Arme de prédilection : +1 (supérieure +2) avec l'arme visée ;
            # - Tir de près : +1 à l'attaque à ≤ 9 m (arme à distance) ;
            # - Attaque en finesse : DEX remplace FOR pour l'attaque (armes
            #   légères, rapière, fouet, chaîne cloutée) — DÉGÂTS exclus.
            dons_pj = fiche.get("dons")
            detail_attaque = ""
            bonus_focus = 0
            bonus_tdp = 0
            try:
                from .fiches import (                     # pylint: disable=import-outside-toplevel
                    a_don_tir_de_pres, attaque_en_finesse_possible,
                    bonus_attaque_dons,
                )
                if not a_distance and attaque_en_finesse_possible(dons_pj, arme):
                    _mod_dex_eff = (int(caracs_eff.get("DEX", 10) or 10) - 10) // 2
                    if _mod_dex_eff > mod_car:
                        cle_car = "DEX"
                        val_car = int(caracs_eff.get("DEX", 10) or 10)
                        mod_car = _mod_dex_eff
                        detail_attaque = (
                            " — Attaque en finesse (DEX au lieu de FOR)"
                        )
                bonus_focus = bonus_attaque_dons(dons_pj, arme)
                if a_distance and a_don_tir_de_pres(dons_pj) \
                        and _distance_proche(distance_m):
                    bonus_tdp = 1
            except Exception:                                 # noqa: BLE001
                pass  # fiches.py indisponible → attaque sans dons
            officiel = bab + mod_car + bonus_focus + bonus_tdp + bonus_mag
            if bonus_attaque != officiel:
                bonus_final = officiel
                # 🛡️ B20 (audit parties complètes) : le bloc « officiel »
                # affichait la valeur FAUSSE proposée par le modèle avant la
                # bonne (« +5 → +2 »), 19 fois sur 19 en partie réelle : le
                # joueur voyait l'erreur du modèle plutôt que la règle. On
                # n'affiche plus que la valeur officielle et son calcul.
                note_bonus = (
                    f"\n- ℹ️ Bonus officiel : {bonus_final:+d} (fiche de "
                    f"{nom_attaquant} : BBA {bab:+d}, {cle_car} {val_car} "
                    f"({mod_car:+d}){detail_attaque}"
                    + (f", {bonus_focus:+d} Arme de prédilection"
                       if bonus_focus else "")
                    + (f", {bonus_tdp:+d} Tir de près (≤ 9 m)"
                       if bonus_tdp else "")
                    + (f", {bonus_mag:+d} arme enchantée"
                       if bonus_mag else "")
                    + (detail_mag if detail_mag else "")
                    + ")."
                )
            # 🎯 Zone de critique : base de l'arme + « Science de la
            # critique » (doublée quand l'arme visée correspond).
            try:
                from .fiches import (                     # pylint: disable=import-outside-toplevel
                    seuil_crit_arme, seuil_crit_dons,
                )
                seuil_base = seuil_crit_arme(arme)
                seuil_crit = seuil_crit_dons(dons_pj, arme)
                if seuil_crit < 20:
                    fragments = []
                    if seuil_base < 20:
                        fragments.append(f"{arme} {seuil_base}-20")
                    if seuil_crit < seuil_base:
                        fragments.append(
                            "Science de la critique : zone doublée")
                    detail_crit = (
                        f"\n- 🎯 Zone de critique {seuil_crit}-20"
                        + (" (" + " ; ".join(fragments) + ")" if fragments
                           else "")
                        + "."
                    )
            except Exception:                                 # noqa: BLE001
                pass  # zones indisponibles → menace sur 20 uniquement
            # 💪 Bonus de dégâts OFFICIEL (partie 263f82dc : le LLM passait
            # +4 puis +6 pour le MÊME attaquant — bonus improvisé au lieu du
            # calcul 3.5). Mêlée = mod. FOR (effectif : gantelets de force),
            # ×1,5 (arrondi vers le bas) pour une arme à deux mains ;
            # distance = +0 (mod. DEX ne s'applique pas aux dégâts) SAUF
            # l'arme enchantée. Le LLM recopie CE bonus dans lancer_degats.
            # NB : l'Attaque en finesse change l'ATTAQUE, jamais les dégâts
            # → le mod. FOR est repris ici même si l'attaque a basculé en DEX.
            _val_for_att = int(caracs_eff.get("FOR", 10) or 10)
            _mod_for_att = (_val_for_att - 10) // 2
            if not a_distance:
                deux_mains = any(
                    m in arme_l for m in ("deux mains", "2 mains")
                )
                bonus_deg_off = (_mod_for_att * 3 // 2 if deux_mains
                                 else _mod_for_att) + bonus_mag
                detail_deg = (
                    f"FOR {_val_for_att} ({_mod_for_att:+d})"
                    + (" ×1,5 arme à deux mains" if deux_mains else "")
                    + (f" + {bonus_mag} arme enchantée" if bonus_mag else "")
                    + (" — l'Attaque en finesse ne s'applique pas aux dégâts"
                       if detail_attaque else "")
                )
                note_degats = (
                    f"\n- 💪 **Bonus dégâts officiel : {bonus_deg_off:+d}** "
                    f"({detail_deg}) — recopie CE bonus dans `lancer_degats` "
                    "(jamais un bonus improvisé)."
                )
            else:
                note_degats = (
                    f"\n- 💪 **Bonus dégâts officiel : {bonus_mag + bonus_tdp:+d}** "
                    "(arme à distance : le mod. DEX ne s'applique pas aux "
                    "dégâts"
                    + (f", +{bonus_mag} arme enchantée" if bonus_mag else "")
                    + (f", +{bonus_tdp} Tir de près" if bonus_tdp else "")
                    + ") — recopie CE bonus dans `lancer_degats`."
                )
            # 🏹 Munition auto (a6d11005, règles d'usage) : chaque tir à
            # distance déduit 1 projectile de l'inventaire du PJ — sans
            # dépendre de l'appel (oublié) à inventaire_consommer_munition.
            munition = _munition_pour_arme(arme)
            if munition:
                from .inventaire import _consommer_fragment
                consomme = _consommer_fragment(fiche, munition, 1)
                if consomme:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(fiche, f, ensure_ascii=False, indent=2)
                    reste = next(
                        (int(e.get("qte", 1) or 1)
                         for e in fiche.get("inventaire") or []
                         if str(e.get("nom", "")).lower() == consomme.lower()),
                        0,
                    )
                    note_ammo = (
                        f"\n- 🏹 **1 × {consomme}** consommée — "
                        + (f"restantes : {reste}" if reste
                           else "plus de munitions !")
                    )
                else:
                    # 🛡️ B45 : un tir sans munition n'est pas résolu. On
                    # l'annonçait « résolu cette fois » puis on jouait le jet :
                    # le joueur apprenait que les munitions ne servent à rien
                    # (l'Assassin a été tué par un tir sans flèche).
                    return ToolResult(text=(
                        f"❌ {nom_attaquant} n'a plus de {munition} — le tir "
                        f"n'est PAS résolu. Rechargement nécessaire "
                        "(inventaire_ajouter, achat au marché, ou arme de "
                        "mêlée)."
                    ))
    except Exception:                                           # noqa: BLE001
        pass  # fiche absente (monstre ?) → on trust le bonus fourni

    # --- ⚡ Conditions préjudiciables (DMG 3.5) — effets mécaniques réels ---
    # Fatigué/Épuisé/Renversé/Empêtré/Agrippé/Secoué/Fragilisé/Titubant →
    # malus d'attaque ; cible Renversée/Aveuglée/Empêtrée/Agrippée/Paralysée
    # → ajustement de CA (Dex perdue) ; attaquant Aveuglé / cible Invisible
    # → % de rater (camouflage total) ; cible « surprise » au round 1 → Dex
    # perdue (−2 CA).
    note_conditions = ""
    try:
        _conds_att = _conditions_de(ctx, nom_attaquant)
        _conds_cib = _conditions_de(ctx, nom_cible)
        _a_distance_l = any(
            m in (arme or "").lower() for m in
            ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard",
             "sarbacane"))
        mc = modificateurs_conditions_attaque(
            _conds_att, _conds_cib, a_distance=_a_distance_l)
        try:
            from ..game.state import PartyState  # lazy : évite les cycles
            _et_s = PartyState(data_dir=ctx.data_dir,
                               partie_id=ctx.partie_id).load()
            if int(_et_s.get("tour") or 0) <= 1 and str(
                    nom_cible or "").strip() in [
                        str(x) for x in (_et_s.get("surpris") or [])]:
                mc["ca"] -= 2
                mc["notes"].append(
                    f"{nom_cible} PRIS AU DÉPOURVU (round de surprise — Dex "
                    "perdue, −2 CA)")
            # 🤝 Aider autrui / feinte (attaque_speciale) : bonus à
            # CONSOMMER sur la prochaine attaque du bénéficiaire.
            _effets = dict(_et_s.get("combat_effets") or {})
            _aides = [a for a in (_effets.get("aides") or [])
                      if _norm_arme(a.get("beneficiaire") or "")
                      == _norm_arme(nom_attaquant)]
            if _aides:
                mc["att"] += 2
                mc["notes"].append("+2 Aider autrui")
                _effets["aides"] = [a for a in (_effets.get("aides") or [])
                                    if a not in _aides]
            _feinte = next(
                (f for f in (_effets.get("feintes") or [])
                 if _norm_arme(f.get("attaquant") or "")
                 == _norm_arme(nom_attaquant)), None)
            if _feinte:
                mc["att"] += 2
                mc["ca"] -= 2
                mc["notes"].append(
                    "cible prise de vitesse (feinte réussie — Dex perdue)")
                _effets["feintes"] = [
                    f for f in (_effets.get("feintes") or [])
                    if f is not _feinte]
            if _aides or _feinte:
                _et_s["combat_effets"] = _effets
                PartyState(data_dir=ctx.data_dir,
                           partie_id=ctx.partie_id).save(_et_s)
        except Exception:                                    # noqa: BLE001
            pass
        if mc["att"]:
            bonus_final += mc["att"]
        if mc["ca"]:
            ca_cible = max(1, ca_cible + mc["ca"])
        if mc["notes"]:
            note_conditions = (
                f"\n- ⚡ Conditions : attaque {mc['att']:+d}, CA cible "
                f"{mc['ca']:+d} → {ca_cible} ("
                + " ; ".join(mc["notes"]) + ").")
    except Exception:                                        # noqa: BLE001
        pass  # état indisponible → attaque sans conditions

    jet = random.randint(1, 20)
    total = jet + bonus_final
    lignes = [
        f"⚔️ **Attaque** : {nom_attaquant} [{arme}] vs {nom_cible} (CA {ca_cible})",
        f"- Jet brut d'attaque : {jet}",
        f"- Bonus total : {bonus_final:+d}"
        + note_arme + note_bonus + note_ca + note_ammo + detail_crit
        + note_conditions,
        f"- **Total attaque : {total}**",
    ]
    if cible_renote:
        lignes.append(cible_renote)
    if note_degats:
        lignes.append(note_degats.strip().lstrip("\n"))
    if jet >= seuil_crit:
        touche = (total >= ca_cible) or jet == 20
        # 20 naturel touche toujours ; sinon la menace doit dépasser la CA.
        if not touche:
            touche = True  # jet == 20
        confirme, jet_conf = _confirm_critique(bonus_final, ca_cible)
        if confirme:
            lignes.append(
                f"- ⭐ **{jet} naturel** → **Touché** + menace de critique "
                f"— 🌟 **CRITIQUE CONFIRMÉ** (confirmation : {jet_conf}"
                f"{bonus_final:+d} ≥ CA {ca_cible}) — relance `lancer_degats`"
                " avec **multiplicateur=2** (les dés de dégâts sont doublés, "
                "pas le bonus)."
            )
        else:
            lignes.append(
                f"- ⭐ **{jet} naturel** → **Touché** + menace de critique "
                f"NON confirmée (confirmation : {jet_conf}{bonus_final:+d} = "
                f"{jet_conf + bonus_final} < CA {ca_cible}) — coup normal, "
                "dégâts simples."
            )
    elif jet == 1:
        lignes.append(
            "- ❌ **1 naturel** → maladresse : attaque ratée automatiquement "
            "(conséquences possibles : arme lâchée, etc.)."
        )
    else:
        # ⚡ % de rater (attaquant aveuglé / cible invisible) : camouflage.
        _miss = 0
        try:
            _a_distance_l2 = any(
                m in (arme or "").lower() for m in
                ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard",
                 "sarbacane"))
            _mc2 = modificateurs_conditions_attaque(
                _conds_att, _conds_cib, a_distance=_a_distance_l2)
            _miss = int(_mc2.get("miss_pct") or 0)
        except Exception:                                    # noqa: BLE001
            _miss = 0
        ok = total >= ca_cible
        if ok and _miss and random.randint(1, 100) <= _miss:
            lignes.append(
                f"- ❌ **Manqué par camouflage** ( {_miss} % de rater : "
                "attaque aveugle/invisible) — le coup passe à côté."
            )
        else:
            lignes.append(
                f"- CA {ca_cible} → "
                + ("✅ **Touché**." if ok else "❌ **Manqué**.")
            )

    # 💥 AUTO-DÉGÂTS : sur un TOUCHÉ, le serveur ROULE les dégâts de
    # l'arme (bestiaire/catalogue + FOR) et les MÉMORISE pour ce tour. Le
    # filet 5bis-c les applique EN FIN DE TOUR seulement si le modèle n'a
    # pas résolu lui-même (`lancer_degats` purge la mémo) — aucun double.
    _touche_final = any("✅ **Touché**" in l for l in lignes)
    if _touche_final:
        try:
            _res_ad = _auto_degats_de_arme(
                ctx, nom_attaquant, arme,
                crit_confirme=any("CRITIQUE CONFIRMÉ" in l for l in lignes))
            if _res_ad is not None:
                _deg_ad, _detail_ad = _res_ad
                _cible_ad = _cible_degats_auto(ctx, nom_cible)
                _cle_ad = (str(ctx.partie_id),
                           str(getattr(ctx, "tour_id", "") or ""),
                           _norm_arme(_cible_ad))
                _memo_ad = _AUTO_DEGATS_TOUR.setdefault(
                    _cle_ad, {"montants": [], "detail": _detail_ad})
                _memo_ad["montants"].append(_deg_ad)
                lignes.append(
                    f"- 💥 Dégâts de l'arme (jet serveur, à appliquer) : "
                    f"**{_deg_ad}** — {_detail_ad}.")
            else:
                lignes.append(
                    "- ℹ️ Dégâts à résoudre via `lancer_degats` (arme "
                    "indéterminable).")
        except Exception as _e_ad:                       # noqa: BLE001
            lignes.append(
                "- ℹ️ Dégâts à résoudre via `lancer_degats` ("
                + str(_e_ad)[:60] + ").")
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_degats(
    ctx: ToolContext,
    nb_des: int,
    faces: int,
    bonus: int,
    arme_ou_sort: str,
    cible: str,
    attaquant: str = "",
    distance_m: Any = None,
    multiplicateur: int = 1,
    non_letal: bool = False,
    sournoise_des: int = 0,
) -> ToolResult:
    # 💥 Auto-dégâts (090ec0af) : le modèle a résolu lui-même → purge la
    # mémo serveur pour cette cible (son jet fait foi, pas de double).
    try:
        _cle_ad = (str(ctx.partie_id), str(getattr(ctx, "tour_id", "") or ""),
                   _norm_arme(cible))
        _AUTO_DEGATS_TOUR.pop(_cle_ad, None)
    except Exception:                                    # noqa: BLE001
        pass
    except Exception:                                    # noqa: BLE001
        pass
    """
    Effectue le jet de dégâts D&D 3.5 selon la formule NdF + bonus. Renvoie les
    jets bruts, le total, et un résumé lisible.

    :param nb_des (int): nombre de dés (ex. 1, 2, 3…).
    :param faces (int): type de dé (4, 6, 8, 10, 12, 20).
    :param bonus (int): bonus de dégâts (mod. FOR, magie, etc.). Peut être négatif.
    :param arme_ou_sort (str): nom de l'arme ou du sort.
    :param cible (str): nom de la cible.
    :param attaquant (str): nom du personnage ou du monstre ATTAQUANT
        (optionnel). Si c'est un monstre du bestiaire, les dés de SON arme
        sont validés contre sa fiche (tailles spéciales) ; sinon contre le
        catalogue d'armes.
    :param distance_m (float): distance estimée en mètres jusqu'à la cible
        (optionnel). À ≤ 9 m, le don « Tir de près » d'un PJ ajoute +1
        dégâts (arme à distance uniquement).
    :param multiplicateur (int): 1 = coup normal ; 2/3/4 = CRITIQUE confirmé
        (règle 3.5 : on multiplie les DÉS, pas le bonus — un ×2 lance deux
        fois les dés). N'appeler avec 2+ qu'après un « CRITIQUE CONFIRMÉ ».
    :param non_letal (bool): dégâts NON LÉTAUX (mains nues, arme à -4, fléau)
        — cumulés dans la réserve de PV non-létaux de la cible ; à hauteur
        des PV restants, la cible s'effondre Inconsciente (sans -1 PV).
    """
    nb_des = max(1, _as_int(nb_des, 1))
    faces = _as_int(faces, 6)
    bonus = _as_int(bonus)
    multiplicateur = max(1, _as_int(multiplicateur, 1))
    if faces not in (2, 3, 4, 6, 8, 10, 12, 20, 100):
        return ToolResult(text=f"⚠️ Type de dé {faces} non standard en D&D 3.5.")

    # --- Bonus de dégâts officiel pour un PJ attaquant -----------------------
    # Le LLM n'a pas à fournir le bonus : pour un attaquant AVEC fiche et une
    # ARME (pas un sort), le serveur recalcule mod. FOR (×1,5 à deux mains) /
    # +0 à distance, et corrige toute valeur différente. Les monstres (sans
    # fiche PJ) et les sorts conservent la valeur passée.
    note_bonus_dm = ""
    if not _est_nom_de_sort(arme_ou_sort):
        try:
            # 🛡️ (correctif dons passifs) : la variable locale se nommait
            # `_fiche_pj` COMME la fonction de module — Python la classait
            # locale, le membre droit levait UnboundLocalError, avalé par le
            # `except` → le recoupement PJ des dégâts était du CODE MORT (le
            # bonus du LLM était cru tel quel, « Tir de près » impossible).
            _fiche_attaquant = _fiche_pj(ctx, attaquant or "")
        except Exception:                                    # noqa: BLE001
            _fiche_attaquant = None
        if _fiche_attaquant is not None:
            _caracs = _fiche_attaquant.get("carac") or {}
            _arme_l = (arme_ou_sort or "").lower()
            _a_distance = any(
                m in _arme_l for m in
                ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard",
                 "sarbacane", "shuriken")
            )
            # ✨ Objets magiques : arme enchantée « +N » → +N dégâts,
            # gantelets de force → FOR effective (mêlée).
            _mag = 0
            try:
                from .fiches import (               # pylint: disable=C0415
                    bonus_arme_magique, bonus_carac_equipement,
                )
                _mag = bonus_arme_magique(arme_ou_sort)
                _for_eff = (int(_caracs.get("FOR", 10) or 10)
                            + bonus_carac_equipement(_fiche_attaquant, "FOR"))
            except Exception:                        # noqa: BLE001
                _for_eff = int(_caracs.get("FOR", 10) or 10)
            if _a_distance:
                bonus_off = 0 + _mag
                _detail = "arme à distance : le mod. DEX ne s'applique pas"
                # 🎯 Tir de près (PHB 3.5) : +1 dégâts à ≤ 9 m avec une
                # arme à distance, pour un PJ portant le don.
                try:
                    from .fiches import (               # pylint: disable=C0415
                        a_don_tir_de_pres,
                    )
                    if a_don_tir_de_pres(_fiche_attaquant.get("dons")) \
                            and _distance_proche(distance_m):
                        bonus_off += 1
                        _detail += " + 1 Tir de près (≤ 9 m)"
                except Exception:                        # noqa: BLE001
                    pass  # fiches.py indisponible → sans le don
                if _mag:
                    _detail += f" + {_mag} arme enchantée"
            else:
                _mod_for = (_for_eff - 10) // 2
                _deux_mains = any(
                    m in _arme_l for m in ("deux mains", "2 mains")
                )
                bonus_off = (_mod_for * 3 // 2 if _deux_mains
                             else _mod_for) + _mag
                _detail = (
                    f"FOR {_for_eff} ({_mod_for:+d})"
                    + (" ×1,5 arme à deux mains" if _deux_mains else "")
                    + (f" + {_mag} arme enchantée" if _mag else "")
                )
            if bonus != bonus_off or _mag:
                note_bonus_dm = (
                    f"\n- ℹ️ Bonus dégâts officiel : {bonus_off:+d} "
                    f"({_detail})."
                )
                bonus = bonus_off

    # Conformité des dés — deux sources de vérité, dans l'ordre :
    # 1. la FICHE du monstre `attaquant`, quand `arme_ou_sort` nomme SON
    #    arme (tailles spéciales : le Géant (froid) de taille G frappe en
    #    « grande hache » 3d6 — pas 1d12 comme au catalogue, taille M) ;
    # 2. à défaut, le CATALOGUE d'armes (partie 4b529064 : la « Hache à
    #    deux mains » — 1d12 au catalogue — fut résolue en 1d20+4).
    # Un nom de sort désactive les deux (Boule de feu 5d6 n'est pas une arme).
    if not _est_nom_de_sort(arme_ou_sort):
        _fiche = _arme_fiche_monstre(ctx, attaquant)
        if _fiche is not None and _cite_arme(arme_ou_sort, _fiche[0]):
            _nom_f, _nb_f, _fa_f = _fiche
            if (nb_des, faces) != (_nb_f, _fa_f):
                return ToolResult(text=(
                    f"⛔ **Formule de dégâts non conforme à la fiche du "
                    f"monstre** : « {attaquant} » inflige {_nb_f}d{_fa_f} "
                    f"avec « {_nom_f} » (fiche bestiaire), pas "
                    f"{nb_des}d{faces}.\n"
                    f"Relance `lancer_degats` avec nb_des={_nb_f}, "
                    f"faces={_fa_f} (le bonus reste inchangé)."
                ))
        else:
            _arme = _de_arme_catalogue(arme_ou_sort)
            if _arme is not None:
                _nb, _fa, _nom_canon = _arme
                if (nb_des, faces) != (_nb, _fa):
                    return ToolResult(text=(
                        f"⛔ **Formule de dégâts non conforme au catalogue** : "
                        f"« {arme_ou_sort} » inflige {_nb}d{_fa} (D&D 3.5, "
                        f"« {_nom_canon} »), pas {nb_des}d{faces}.\n"
                        f"Relance `lancer_degats` avec nb_des={_nb}, "
                        f"faces={_fa} (le bonus de Force reste inchangé)."
                    ))
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    # 🌟 CRITIQUE confirmé (règle 3.5) : on multiplie les DÉS — un ×2 lance
    # nb_des dés SUPPLÉMENTAIRES (pas « total × 2 »).
    jets_crit: list[int] = []
    if multiplicateur > 1:
        jets_crit = [random.randint(1, faces)
                     for _ in range(nb_des * (multiplicateur - 1))]
    # ⚡ Attaque sournoise (règle 3.5) : d6 supplémentaires — JAMAIS
    # multipliés par un critique (« extra damage dice are not multiplied »).
    jets_sour: list[int] = []
    sournoise_des = max(0, _as_int(sournoise_des, 0))
    if sournoise_des:
        jets_sour = [random.randint(1, 6) for _ in range(sournoise_des)]
    # 🛡️ B46 (audit parties complètes) : un coup qui touche inflige au minimum
    # 1 point de dégâts en D&D 3.5. Un `lancer_degats` à 0 (le modèle confond
    # bonus et total) produisait « ✅ Touché … subit 0 dégâts », et le MJ
    # inventait une règle pour le justifier.
    brut = sum(jets) + sum(jets_crit) + sum(jets_sour) + bonus
    if brut <= 0 and not _est_nom_de_sort(arme_ou_sort):
        note_min = (
            "\n- ℹ️ Total nul ou négatif porté au minimum de 1 "
            "(un coup qui touche blesse toujours, règle 3.5)."
        )
        total = 1
    else:
        note_min = ""
        total = max(0, brut)   # jamais de dégâts négatifs (min 0)
    lignes = [
        f"💥 **Dégâts** : {arme_ou_sort} → {cible}",
        f"- Formule : {nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}"
        + (f" ×{multiplicateur} (critique : +{nb_des * (multiplicateur - 1)}"
           " dés)" if multiplicateur > 1 else ""),
        f"- Jets bruts : {jets}"
        + (f" + critique {jets_crit}" if jets_crit else ""),
        f"- Total jets : {sum(jets) + sum(jets_crit)}",
        *([f"- ⚡ **Attaque sournoise** : +{sournoise_des}d6 → "
           f"{sum(jets_sour)}"] if jets_sour else []),
        f"- Bonus dégâts : {bonus:+d}" + note_bonus_dm,
        f"- **Dégâts infligés : {total}**"
        + (" (NON LÉTAUX)" if non_letal else ""),
    ]
    if note_min:
        lignes.append(note_min.strip("\n"))
    elif brut < 0:
        lignes.append(
            "- ℹ️ Total négatif ramené à 0 (les dégâts ne soignent pas la cible)."
        )
    # ⚡ Mémo source (arme/sort) : `_infliger_degats_monstre` relit cette
    # source pour appliquer RD / résistances énergie / immunités du monstre.
    try:
        enregistrer_source_degats(
            ctx.partie_id, str(ctx.tour_id or ""), cible,
            arme_ou_sort, non_letal)
    except Exception:                                        # noqa: BLE001
        pass
    # 🛡️ Défenses du monstre cible : si la cible est un monstre SUIVI doté
    # de RD / résistances, on affiche le montant réellement à infliger —
    # `fiche_perso_infliger_degats` ré-appliquera le même calcul côté serveur
    # (impossible d'oublier ou de tricher).
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        from ..game.regles35 import (reduire_degats_monstre,
                                     defenses_monstre)
        _et_d = PartyState(data_dir=ctx.data_dir,
                           partie_id=ctx.partie_id).load()
        _cn = _norm_arme(cible)
        _mo = next(
            (m for m in (_et_d.get("monstres_combat") or [])
             if _norm_arme(m.get("nom")) == _cn
             and int(m.get("pv", 0) or 0) > 0
             and "Détruit" not in (m.get("conditions") or [])
             and "Detruit" not in (m.get("conditions") or [])), None)
        if _mo is None:
            from .monstres import _find_monstre   # lazy
            _mo = _find_monstre(ctx, cible)
        if _mo is not None and defenses_monstre(_mo):
            _mag = 0
            try:
                from .fiches import bonus_arme_magique
                _mag = bonus_arme_magique(arme_ou_sort)
            except Exception:                                # noqa: BLE001
                pass
            _reduit, _note_r = reduire_degats_monstre(
                _mo, total, arme_ou_sort, _mag)
            if _note_r:
                lignes.append(_note_r)
                lignes.append(
                    f"- ➡️ **Dégâts à infliger via "
                    f"`fiche_perso_infliger_degats` : {_reduit}** "
                    "(le serveur re-vérifie la RD à l'application)."
                )
    except Exception:                                        # noqa: BLE001
        pass
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_sauvegarde(
    ctx: ToolContext,
    type_sauvegarde: str,
    modificateur: int,
    difficulte: int,
    nom_personnage: str,
    source: str,
) -> ToolResult:
    """
    Effectue un jet de sauvegarde D&D 3.5 contre une difficulté DD.

    :param type_sauvegarde (str): "Vigueur", "Réflexes" ou "Volonté".
    :param modificateur (int): total du jet de sauvegarde de base + carac.
        Recoupé avec la fiche du PJ si elle existe (la valeur officielle prime).
    :param difficulte (int): DD à atteindre (souvent 10 + ½ niveau + mod carac).
    :param nom_personnage (str): nom du personnage qui sauvegarde.
    :param source (str): source du danger (sort, piège, poison…).
    """
    t = str(type_sauvegarde or "").lower().strip()
    # Normalisation accents : « Volonté » → « volonte », « Réflexes » →
    # « reflexes » — sans cela, la forme accentuée (documentée ci-dessus !)
    # ne matchait JAMAIS les clés de fiche « Volonte »/« Reflexes » et le
    # recoupement fiche échouait en silence.
    t = "".join(
        c for c in unicodedata.normalize("NFKD", t)
        if not unicodedata.combining(c)
    )
    if t not in ("vigueur", "reflexes", "volonte"):
        # 🛡️ (audit) : le modèle envoie parfois « Reflex », « reflex », ou la
        # forme anglaise « Fortitude / Reflex / Will ». On normalise au lieu
        # de refuser — le jet était sinon perdu et le tour ne se résolvait pas.
        _alias_sv = {
            "reflex": "reflexes", "reflexe": "reflexes",
            "fortitude": "vigueur", "fort": "vigueur", "vig": "vigueur",
            "will": "volonte", "willpower": "volonte", "vol": "volonte",
        }
        t = _alias_sv.get(t, t)
    if t not in ("vigueur", "reflexes", "volonte"):
        return ToolResult(
            text=(
                f"⚠️ type_sauvegarde invalide : '{type_sauvegarde}'. "
                "Attendu : Vigueur, Réflexes ou Volonté."
            )
        )
    aliases = {"reflexes": "Reflexes", "volonte": "Volonte"}
    cle = aliases.get(t, t.capitalize())
    label = {"Vigueur": "Vigueur", "Reflexes": "Réflexes", "Volonte": "Volonté"}.get(cle, cle)
    modificateur = _as_int(modificateur)
    difficulte = _as_int(difficulte, 10)

    # --- Recoupement fiche PJ ----------------------------------------------
    # La fiche stocke les totaux officiels (base de classe + mod. carac) :
    # on les utilise plutôt que le chiffre approximatif du LLM, puis on
    # ajoute les dons passifs de sauvegarde (Volonté de fer +2 Volonté,
    # Grande Fortitude +2 Vigueur, Réflexes surprenants +2 Réflexes).
    mod_final = int(modificateur)
    note_mod = ""
    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is not None:
        sauv = fiche.get("sauvegardes") or {}
        for k, v in sauv.items():
            if str(k).strip().lower().rstrip("s") == cle.strip().lower().rstrip("s"):
                try:
                    off = int(v)
                    try:
                        from .fiches import bonus_dons_effet  # pylint: disable=import-outside-toplevel
                        bonus_don = bonus_dons_effet(
                            fiche.get("dons"), "sauvegarde_" + cle.lower()
                        )
                    except Exception:                        # noqa: BLE001
                        bonus_don = 0
                    # ✨ Objets « Résistance +N » (chantier DRS P3-#19) :
                    # bonus de sauvegarde magique = le MAX des +N portés.
                    try:
                        from .fiches import (  # pylint: disable=import-outside-toplevel
                            bonus_sauvegarde_magie,
                        )
                        _mag_sv = bonus_sauvegarde_magie(fiche)
                    except Exception:                        # noqa: BLE001
                        _mag_sv = 0
                    off_total = off + bonus_don + _mag_sv
                    if off_total != mod_final:
                        detail = f"{label} {off:+d}"
                        if bonus_don:
                            detail += f" + {bonus_don} (dons)"
                        if _mag_sv:
                            detail += f" + {_mag_sv} (Résistance magique)"
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{off_total:+d} (fiche de {nom_personnage} : "
                            f"{detail})."
                        )
                        mod_final = off_total
                    break
                except (TypeError, ValueError):
                    break

    jet = random.randint(1, 20)
    total = jet + mod_final
    ok = (total >= difficulte or jet == 20) and jet != 1
    lignes = [
        f"🛡️ **Sauvegarde** ({label}) — {nom_personnage} vs {source} (DD {difficulte})",
        f"- Jet brut : {jet}",
        f"- Modificateur : {mod_final:+d}" + note_mod,
        f"- **Total : {total}**",
    ]
    if jet == 20:
        lignes.append("- ⭐ **20 naturel** → réussite automatique.")
    elif jet == 1:
        lignes.append(
            "- ❌ **1 naturel** → échec automatique (conséquences aggravées possibles)."
        )
    else:
        lignes.append(
            "- DD " + str(difficulte) + " → "
            + ("✅ **Réussite**." if ok else "❌ **Échec**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_caracteristiques(ctx: ToolContext, methode: str = "4d6_garder_3") -> ToolResult:
    """
    Tire les 6 caractéristiques D&D 3.5 (FOR, DEX, CON, INT, SAG, CHA) selon la
    méthode demandée. Utile à la création de personnage.

    :param methode (str): une parmi —
      "4d6_garder_3" (4d6, garder les 3 meilleurs, ×6) [défaut recommandé],
      "3d6" (3d6, ×6, dans l'ordre),
      "repartition_elite" (15,14,13,12,10,8 à répartir librement — DMG n°3),
      "achat_points" (grille de coûts DMG n°2, budget 25 points).
    """
    methodes = {
        "4d6_garder_3": "4d6, garder les 3 meilleurs, six fois",
        "3d6": "3d6, six fois, dans l'ordre",
        "repartition_elite": (
            "Répartition d'élite (DMG n°3) — 6 valeurs fixes "
            "[15,14,13,12,10,8] à répartir librement entre les 6 carac."
        ),
        "achat_points": (
            "Achat de points (DMG n°2) — budget 25 pts à répartir "
            "selon la grille de coûts 8→18."
        ),
    }
    if methode not in methodes:
        return ToolResult(text="⚠️ Méthode inconnue. Options : " + ", ".join(methodes.keys()))

    noms = ["FOR", "DEX", "CON", "INT", "SAG", "CHA"]
    lignes = [f"🎲 **Caractéristiques** — méthode : {methodes[methode]}"]

    if methode == "4d6_garder_3":
        for nom in noms:
            quatre = [random.randint(1, 6) for _ in range(4)]
            trois = sorted(quatre, reverse=True)[:3]
            somme = sum(trois)
            lignes.append(
                f"  - **{nom}** : {somme} (mod {_mod(somme):+d}) "
                f"[4d6 = {quatre} → garder {trois}]"
            )
    elif methode == "3d6":
        for nom in noms:
            trois = [random.randint(1, 6) for _ in range(3)]
            somme = sum(trois)
            lignes.append(f"  - **{nom}** : {somme} (mod {_mod(somme):+d}) [3d6 = {trois}]")
    elif methode == "repartition_elite":
        valeurs = [15, 14, 13, 12, 10, 8]
        lignes.append(
            "Répartir librement ces 6 valeurs entre FOR, DEX, CON, INT, SAG, CHA "
            "(méthode DMG n°3 « Répartition d'élite »)."
        )
        for nom, v in zip(noms, valeurs):
            lignes.append(f"  - valeur {v} disponible (mod {_mod(v):+d})")
    elif methode == "achat_points":
        couts = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 6, 15: 8, 16: 10, 17: 13, 18: 16}
        budget = 25
        lignes.append(f"**Achat de points (DMG n°2)** — budget : **{budget} points**.")
        lignes.append("Grille de coûts (valeur → coût en points) :")
        for v, c in couts.items():
            lignes.append(f"  - {v:2d} → {c:2d} pts")
        lignes.append(
            f"Répartir les {budget} points entre FOR, DEX, CON, INT, SAG, CHA en "
            "respectant cette grille. La table officielle impose une valeur "
            "minimale de 8 au départ."
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_des(
    ctx: ToolContext,
    nb_des: int,
    faces: int,
    bonus: int = 0,
    raison: str = "lancer générique",
) -> ToolResult:
    """
    Utilitaire générique : lance nb_des dés de `faces` faces, ajoute un bonus,
    renvoie le détail. À utiliser quand aucune méthode spécialisée ne s'applique
    (pourcentage, table aléatoire, percentile d20 d10…).

    :param nb_des (int): nombre de dés (≥1).
    :param faces (int): nombre de faces (ex. 4, 6, 8, 10, 12, 20, 100).
    :param bonus (int): bonus à ajouter (peut être négatif).
    :param raison (str): court descriptif du jet.
    """
    if nb_des < 1:
        return ToolResult(text="⚠️ nb_des doit être ≥ 1.")
    if faces < 2:
        return ToolResult(text="⚠️ faces doit être ≥ 2.")
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    total = sum(jets) + bonus
    lignes = [
        f"🎲 **{nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}** — {raison}",
        f"- Jets bruts : {jets}",
        f"- Total jets : {sum(jets)}",
        f"- Bonus : {bonus:+d}",
        f"- **Total : {total}**",
    ]
    return ToolResult(text="\n".join(lignes))


# --------------------------------------------------------------------------- #
#  🗡️ Attaques d'opportunité & attaques spéciales (chantier DRS P3-#16/20)
# --------------------------------------------------------------------------- #
@tool
async def attaque_opportunite(
    ctx: ToolContext,
    nom_attaquant: str,
    nom_cible: str,
    bonus_attaque: Any = 0,
    ca_cible: Any = 10,
    arme: str = "",
) -> ToolResult:
    """
    Attaque d'opportunité D&D 3.5 : la cible s'est déplacée hors de votre
    menace, a lancé un sort, tiré à distance ou s'est relevée À VOTRE PORTÉE.
    Une SEULE attaque d'opportunité par round et par attaquant (dons de
    supplémentaires : le MJ les résout avec un second appel).

    :param nom_attaquant (str): nom de l'attaquant.
    :param nom_cible (str): nom de la cible qui a PROVOQUÉ l'attaque.
    :param bonus_attaque (int): bonus d'attaque officiel (fiche pour un PJ,
        bestiaire pour un monstre — recoupé automatiquement pour un PJ).
    :param ca_cible (int): CA de la cible (recoupée automatiquement).
    :param arme (str): arme utilisée.
    """
    from ..game.state import PartyState  # lazy
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    round_actuel = int(etat.get("tour") or 1)
    deja = etat.setdefault("aoo_tour", {})
    cle_rond = str(round_actuel)
    joues = deja.get(cle_rond) or []
    if _norm_arme(nom_attaquant) in {_norm_arme(x) for x in joues}:
        return ToolResult(text=(
            f"⛔ {nom_attaquant} a DÉJÀ utilisé son attaque d'opportunité "
            f"au round {round_actuel} (règle 3.5 : 1/round sans Réflexes de "
            "combat). Attendre le round suivant."))
    joues.append(nom_attaquant)
    deja[cle_rond] = joues
    ps.save(etat)
    tr = await lancer_attaque(
        ctx, bonus_attaque=bonus_attaque, ca_cible=ca_cible,
        nom_attaquant=nom_attaquant, arme=arme or "arme", nom_cible=nom_cible)
    prefix = f"⚡ **Attaque d'opportunité** ({nom_attaquant} profite d'une " \
             f"faute de {nom_cible}) — 1/round consommée.\n"
    return ToolResult(text=prefix + tr.text)


@tool
async def attaque_speciale(
    ctx: ToolContext,
    type_attaque: str,
    nom_attaquant: str,
    nom_cible: str,
    bonus_attaquant: Any = 0,
    bonus_cible: Any = 0,
) -> ToolResult:
    """
    Attaque spéciale D&D 3.5 (jets opposés résolus serveur).

    :param type_attaque (str): l'une de —
        "renversement" (trip : attaque toucher vs attaque toucher ; échec de
        10+ = attaquant Renversé ; succès = cible Renversée) ;
        "desarmement" (opposed attacks ; succès = arme de la cible au sol ;
        échec de 10+ = VOTRE arme au sol) ;
        "bousculade" (opposed Force ; succès de 5+ = cible repoussée) ;
        "aider" (test d'attaque DD 10 contre CA 10 — allié désigné par
        `nom_cible` gagne +2 à sa prochaine attaque, appliqué serveur) ;
        "feinte" (Bluff vs Perception auditive/Sense Motive opposé — succès :
        la cible perd sa DEX contre VOTRE prochaine attaque, appliqué serveur) ;
        "immobilisation" (toucher puis opposed Force ; succès : les DEUX sont
        Agrippés ; échec : attaquant Agrippé).
    :param nom_attaquant (str): nom de l'attaquant (PJ ou monstre).
    :param nom_cible (str): nom de la cible — ou de l'ALLIÉ aidé pour
        type_attaque="aider".
    :param bonus_attaquant (int): bonus de jet d'attaque (ou mod. Force pour
        bousculade/immobilisation).
    :param bonus_cible (int): bonus équivalent de la cible (fiche/bestiaire).
    """
    t = _norm_arme(type_attaque).replace(" ", "_")
    b_atq = _as_int(bonus_attaquant)
    b_cib = _as_int(bonus_cible)
    from ..game.state import PartyState  # lazy
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    effets = dict(etat.get("combat_effets") or {})

    def _poser_condition_pj(nom: str, condition: str) -> str:
        """Pose une condition via le tool officiel (PJ) ou l'entrée suivie
        (monstre). Renvoie une note."""
        try:
            from .fiches import _load_fiche, _save_fiche, _sync_pj
            fiche = _load_fiche(ctx, nom)
            if fiche is not None:
                conds = fiche.setdefault("conditions", [])
                if condition not in conds:
                    conds.append(condition)
                _save_fiche(ctx, nom, fiche)
                _sync_pj(ctx, nom, {"conditions": conds})
                return f"{nom} → condition **{condition}** appliquée."
        except Exception:                                # noqa: BLE001
            pass
        for mo in etat.get("monstres_combat") or []:
            if _norm_arme(mo.get("nom")) == _norm_arme(nom):
                conds = mo.setdefault("conditions", [])
                if condition not in conds:
                    conds.append(condition)
                return (f"{nom} (monstre) → condition **{condition}** "
                        "appliquée au suivi de combat.")
        return f"{nom} : condition **{condition}** à narrer (cible inconnue)."

    if t in ("renversement", "renverse"):
        j_a, j_c = random.randint(1, 20), random.randint(1, 20)
        t_a, t_c = j_a + b_atq, j_c + b_cib
        lignes = [
            f"🦵 **Renversement** : {nom_attaquant} vs {nom_cible}",
            f"- {nom_attaquant} : {j_a}+{b_atq} = {t_a} — "
            f"{nom_cible} : {j_c}+{b_cib} = {t_c}",
        ]
        if j_a == 20 or (t_a > t_c and j_a != 1):
            lignes.append(f"- ✅ **Renversé !** " + _poser_condition_pj(
                nom_cible, "Renversé"))
            note = f"- 🗒️ Renversé : −4 attaque, CA sans Dex (−4 mêlée)."
        elif t_a <= t_c - 10 and j_a != 20:
            lignes.append(f"- ❌ Échec de 10+ → {nom_attaquant} chute : "
                          + _poser_condition_pj(nom_attaquant, "Renversé"))
            note = ""
        else:
            lignes.append("- ❌ Échec — la cible tient debout.")
            note = ""
        lignes.append(note)
        ps.save(etat)
        return ToolResult(text="\n".join(lignes))

    if t in ("desarmement", "désarmement"):
        j_a, j_c = random.randint(1, 20), random.randint(1, 20)
        t_a, t_c = j_a + b_atq, j_c + b_cib
        lignes = [
            f"🤺 **Désarmement** : {nom_attaquant} vs {nom_cible}",
            f"- {nom_attaquant} : {j_a}+{b_atq} = {t_a} — "
            f"{nom_cible} : {j_c}+{b_cib} = {t_c}",
        ]
        if t_a > t_c or j_a == 20:
            lignes.append(
                f"- ✅ **Arme désarmée !** L'arme de {nom_cible} tombe au "
                "sol (à récupérer : action de mouvement, AOO possible).")
        elif t_c - t_a >= 10:
            lignes.append(
                f"- ❌ Échec de 10+ → **l'arme de {nom_attaquant} tombe au "
                "sol** (la cible riposte au désarmement).")
        else:
            lignes.append("- ❌ Échec — la cible garde son arme.")
        return ToolResult(text="\n".join(lignes))

    if t == "bousculade":
        j_a, j_c = random.randint(1, 20), random.randint(1, 20)
        t_a, t_c = j_a + b_atq, j_c + b_cib
        lignes = [
            f"🐗 **Bousculade** : {nom_attaquant} vs {nom_cible}",
            f"- Force : {j_a}+{b_atq} = {t_a} vs {j_c}+{b_cib} = {t_c}",
        ]
        if t_a >= t_c + 5:
            lignes.append(
                f"- ✅ **Repoussé !** {nom_cible} recule de 1,50 m ( "
                f"{nom_attaquant} peut le suivre en diagonale).")
        elif t_a > t_c:
            lignes.append(
                f"- ⚖️ Succès court : {nom_attaquant} avance de 1,50 m, "
                f"{nom_cible} ne recule pas.")
        else:
            lignes.append(
                f"- ❌ Échec : {nom_attaquant} recule de 1,50 m.")
        return ToolResult(text="\n".join(lignes))

    if t in ("aider", "aide"):
        jet = random.randint(1, 20)
        total = jet + b_atq
        lignes = [
            f"🤝 **Aider autrui** — {nom_attaquant} prête main-forte à "
            f"{nom_cible}",
            f"- Jet d'attaque DD 10 : {jet}+{b_atq} = {total}",
        ]
        if total >= 10 or jet == 20:
            aides = effets.setdefault("aides", [])
            aides.append({"beneficiaire": nom_cible, "bonus": 2})
            etat["combat_effets"] = effets
            ps.save(etat)
            lignes.append(
                f"- ✅ Réussite — **+2 à la prochaine attaque de "
                f"{nom_cible}** (appliqué automatiquement par le serveur).")
        else:
            lignes.append("- ❌ Échec — pas d'aide, et l'attaquant a "
                          "potentiellement provoqué une AOO.")
        return ToolResult(text="\n".join(lignes))

    if t == "feinte":
        j_a, j_c = random.randint(1, 20), random.randint(1, 20)
        t_a, t_c = j_a + b_atq, j_c + b_cib
        lignes = [
            f"🎭 **Feinte** : {nom_attaquant} (Bluff) vs {nom_cible} "
            "(Perception)",
            f"- Bluff : {j_a}+{b_atq} = {t_a} — Perception : "
            f"{j_c}+{b_cib} = {t_c}",
        ]
        if t_a > t_c or j_a == 20:
            feintes = effets.setdefault("feintes", [])
            feintes.append({"attaquant": nom_attaquant, "cible": nom_cible})
            etat["combat_effets"] = effets
            ps.save(etat)
            lignes.append(
                f"- ✅ **Feinte réussie** — {nom_cible} perd sa DEX contre "
                f"la prochaine attaque de {nom_attaquant} (+2 attaque, −2 "
                "CA cible, appliqués automatiquement).")
        else:
            lignes.append("- ❌ Feinte repérée — aucune ouverture.")
        return ToolResult(text="\n".join(lignes))

    if t in ("immobilisation", "empoignade", "grapple"):
        jet_touch = random.randint(1, 20)
        lignes = [
            f"🤼 **Immobilisation** : {nom_attaquant} tente d'agripper "
            f"{nom_cible}",
            f"- Touche-à-touche : {jet_touch}+{b_atq} = "
            f"{jet_touch + b_atq} vs CA de contact de {nom_cible} "
            "(le MJ fournit sa CA − armure/bouclier).",
        ]
        if jet_touch == 1:
            lignes.append("- ❌ Toucher raté — tentative échouée.")
            return ToolResult(text="\n".join(lignes))
        j_a, j_c = random.randint(1, 20), random.randint(1, 20)
        t_a, t_c = j_a + b_atq, j_c + b_cib
        lignes.append(
            f"- Force opposée : {j_a}+{b_atq} = {t_a} vs {j_c}+{b_cib} = "
            f"{t_c}")
        if t_a > t_c:
            lignes.append("- ✅ **Agrippés tous les deux !** "
                          + _poser_condition_pj(nom_cible, "Agrippé"))
            lignes.append(_poser_condition_pj(nom_attaquant, "Agrippé"))
            lignes.append(
                "- 🗒️ Agrippé : −2 attaques (sauf contre son agrippeur), "
                "Dex perdue à la CA, pas de mouvement.")
        else:
            lignes.append("- ❌ La cible résiste et VOUS agrippe : "
                          + _poser_condition_pj(nom_attaquant, "Agrippé"))
        ps.save(etat)
        return ToolResult(text="\n".join(lignes))

    return ToolResult(text=(
        f"⚠️ type_attaque inconnu : '{type_attaque}'. Attendu : "
        "renversement, desarmement, bousculade, aider, feinte, "
        "immobilisation."))
