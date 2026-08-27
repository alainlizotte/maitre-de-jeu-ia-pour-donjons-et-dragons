"""Outil Fiches personnages — adapté de `Outil_FichesPerso.py`.

Gère les fiches personnages persistantes D&D 3.5, conformes au Manuel du
Joueur 3.5. Les fiches sont stockées comme fichiers JSON individuels dans
`data/fiches/fiche_<slug_nom>.json`, à côté de la partie.

Différences vs la version OpenWebUI :
- Plus de classe `Tools` ni de `pydantic.Valves`. Le `data_dir` vient du
  `ToolContext`.
- Méthodes `def (...) -> str` devenues `@tool` async renvoyant `ToolResult`.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool
from ..image.helpers import generer_averti, portrait_prompt


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _slug(texte: str) -> str:
    """Transforme un nom de personnage en identifiant sûr pour fichier."""
    nf = unicodedata.normalize("NFKD", texte)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "perso"


# --------------------------------------------------------------------------- #
#  Tables de règles D&D 3.5 (Manuel du Joueur)
# --------------------------------------------------------------------------- #

def _norm_key(s: str) -> str:
    """Normalise une chaîne pour lookup : minuscule, sans accent, tirets → espaces."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("-", " ").replace("'", " ").strip()


# Alias de races (FR/EN) → race canonique PHB 3.5
_RACE_ALIAS = {
    "humain": "Humain", "human": "Humain",
    "elfe": "Elfe", "elf": "Elfe", "haut elfe": "Elfe", "haut elf": "Elfe",
    "nain": "Nain", "dwarf": "Nain",
    "halfeling": "Halfeling", "halfling": "Halfeling", "semi homme": "Halfeling",
    "hobbit": "Halfeling", "kender": "Halfeling",
    "gnome": "Gnome",
    "demi elfe": "Demi-elfe", "half elf": "Demi-elfe", "halfelf": "Demi-elfe",
    "demi orc": "Demi-orc", "half orc": "Demi-orc", "halforc": "Demi-orc", "orc": "Demi-orc",
}

# Ajustements raciaux officiels 3.5 (PHB p.12-19) — appliqués au tirage auto.
_RACE_MODS = {
    "Elfe": {"DEX": 2, "CON": -2},
    "Nain": {"CON": 2, "CHA": -2},
    "Halfeling": {"DEX": 2, "FOR": -2},
    "Gnome": {"CON": 2, "FOR": -2},
    "Demi-orc": {"FOR": 2, "INT": -2, "CHA": -2},
}

# Alias de classes (FR/EN, masc./fém.) → classe canonique
_CLASSE_ALIAS = {
    "guerrier": "guerrier", "guerriere": "guerrier", "fighter": "guerrier", "warrior": "guerrier",
    "barbare": "barbare", "barbarian": "barbare",
    "paladin": "paladin",
    "ranger": "ranger", "rodeur": "ranger",
    "voleur": "voleur", "voleuse": "voleur", "roublard": "voleur", "roublarde": "voleur",
    "thief": "voleur", "rogue": "voleur",
    "barde": "barde", "bard": "barde",
    "moine": "moine", "monk": "moine",
    "clerc": "clerc", "pretre": "clerc", "priest": "clerc", "cleric": "clerc",
    "druide": "druide", "druid": "druide",
    "magicien": "magicien", "magicienne": "magicien", "mage": "magicien",
    "wizard": "magicien", "necromancien": "magicien",
    "sorcier": "sorcier", "sorciiere": "sorcier", "ensorcelleur": "sorcier",
    "sorcerer": "sorcier",
    "warlock": "warlock", "demoniste": "warlock",
    "assassin": "assassin",
    "artificier": "artificier",
    "alchimiste": "alchimiste",
}

# Classe canonique → (dé de vie, BBA niv.1, saves de base niv.1 (vig, ref, vol))
# Saves "bons" = +2 au niveau 1, "mauvais" = +0 (PHB 3.5).
_CLASSES_35 = {
    "guerrier": (10, 1, (2, 0, 0)),
    "barbare": (12, 1, (2, 0, 0)),
    "paladin": (10, 1, (2, 0, 0)),
    "ranger": (8, 1, (2, 2, 0)),
    "voleur": (6, 0, (0, 2, 0)),
    "barde": (6, 0, (0, 2, 2)),
    "moine": (8, 0, (2, 2, 2)),
    "clerc": (8, 0, (0, 0, 2)),
    "druide": (8, 0, (2, 0, 2)),
    "magicien": (4, 0, (0, 0, 2)),
    "sorcier": (4, 0, (0, 0, 2)),
    "warlock": (6, 0, (0, 0, 2)),
    "assassin": (6, 0, (0, 2, 0)),
    "artificier": (6, 0, (0, 2, 2)),
    "alchimiste": (8, 0, (0, 0, 2)),
}


def _fiches_dir(ctx: ToolContext) -> str:
    """Renvoie le dossier des fiches pour le contexte courant.
    On garde le même sous-dossier que l'original (`data/fiches`) pour pouvoir
    servir des fiches créées dans l'ancien projet sans migration.
    """
    path = os.path.join(ctx.data_dir, "fiches")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _resoudre_nom(ctx: ToolContext, nom: str) -> str:
    """Nom canonique du personnage à partir d'un nom OU d'un pseudo joueur.

    Les messages arrivent signés du pseudo du joueur humain (« Alice ») mais
    les fiches portent le nom du personnage (« Brunhild »). Un petit LLM
    confond souvent les deux : on résout via l'état de partie (pj[].nom /
    pj[].joueur), insensible à la casse et aux accents.
    """
    def _norm(s: Any) -> str:
        s = unicodedata.normalize("NFKD", str(s or "").strip().lower())
        return "".join(c for c in s if not unicodedata.combining(c))

    cible = _norm(nom)
    if not cible:
        return nom
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        etat = PartyState(
            data_dir=ctx.data_dir, partie_id=ctx.partie_id
        ).load()
    except Exception:                                        # noqa: BLE001
        return nom
    # 1. nom de personnage exact (normalisé)
    for p in etat.get("pj", []):
        if _norm(p.get("nom")) == cible:
            return p.get("nom") or nom
    # 2. pseudo du joueur → son personnage
    for p in etat.get("pj", []):
        if _norm(p.get("joueur")) == cible and p.get("nom"):
            return p["nom"]
    # 3. préfixe (« brun » → « Brunhild »)
    for p in etat.get("pj", []):
        pn = _norm(p.get("nom"))
        if len(cible) >= 4 and pn.startswith(cible):
            return p.get("nom") or nom
    for p in etat.get("pj", []):
        jn = _norm(p.get("joueur"))
        if p.get("nom") and len(cible) >= 4 and jn.startswith(cible):
            return p["nom"]
    return nom


def _chemin(ctx: ToolContext, nom: str) -> str:
    nom = _resoudre_nom(ctx, nom)
    return os.path.join(_fiches_dir(ctx), f"fiche_{_slug(nom)}.json")


def _parse_json(valeur: Any, label: str) -> Any:
    """Coerce une valeur (str JSON / dict / list) en structure."""
    if valeur is None or valeur == "":
        return {}
    if isinstance(valeur, (dict, list)):
        return valeur
    try:
        return json.loads(valeur)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalide pour {label} : {e}")


def _load_fiche(ctx: ToolContext, nom: str) -> Optional[dict[str, Any]]:
    """Charge une fiche ou renvoie None si absente/illisible."""
    path = _chemin(ctx, nom)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None


# Cache module-level du schéma JSON et de son validateur (lazy, chargé une fois).
_SCHEMA_FICHE_CACHE: Optional[dict[str, Any]] = None
_SCHEMA_VALIDATOR_CACHE: Any = None


def _load_schema_fiche(ctx: ToolContext) -> Optional[Any]:
    """Charge (et cache) le validateur JSON Schema draft-07 des fiches.

    Le schéma vit à `data/fiches/schema_fiche.json`. Renvoie None s'il est
    absent ou si `jsonschema` n'est pas installé — la validation est alors
    silencieusement ignorée (comportement pré-validation).
    """
    global _SCHEMA_FICHE_CACHE, _SCHEMA_VALIDATOR_CACHE
    if _SCHEMA_VALIDATOR_CACHE is not None:
        return _SCHEMA_VALIDATOR_CACHE
    schema_path = os.path.join(_fiches_dir(ctx), "schema_fiche.json")
    if not os.path.isfile(schema_path):
        return None
    try:
        if _SCHEMA_FICHE_CACHE is None:
            with open(schema_path, "r", encoding="utf-8") as f:
                _SCHEMA_FICHE_CACHE = json.load(f)
        try:
            import jsonschema                        # pylint: disable=import-outside-toplevel
            from jsonschema import Draft7Validator   # pylint: disable=import-outside-toplevel
        except ImportError:
            return None                              # jsonschema non installé → skip
        _SCHEMA_VALIDATOR_CACHE = Draft7Validator(_SCHEMA_FICHE_CACHE)
        return _SCHEMA_VALIDATOR_CACHE
    except (json.JSONDecodeError, OSError):
        return None


def _save_fiche(ctx: ToolContext, nom: str, fiche: dict[str, Any]) -> str:
    """Écrit une fiche après validation JSON Schema (draft-07).

    Lève `ValueError` avec les erreurs formatées en français si la fiche ne
    respecte pas le schéma `data/fiches/schema_fiche.json`. L'appelant est
    attendu convertir cette exception en `ToolResult(error=...)`.
    """
    validator = _load_schema_fiche(ctx)
    if validator is not None:
        errors = sorted(validator.iter_errors(fiche), key=lambda e: e.path)
        if errors:
            msgs: list[str] = []
            for e in errors:
                # Chemin lisible : "carac.FOR" ou "niveau".
                path = ".".join(str(p) for p in e.absolute_path) or "(racine)"
                msgs.append(f"  • {path} : {e.message}")
            raise ValueError(
                "Fiche invalide (schéma D&D 3.5 non respecté) :\n"
                + "\n".join(msgs)
            )
    path = _chemin(ctx, nom)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=2)
    return path


def _sync_pj(ctx: ToolContext, nom: str, champs: dict[str, Any]) -> Optional[int]:
    """Répercute des champs d'une fiche sur l'entrée PJ de l'état de partie.

    Sans cette synchro, la fiche JSON évolue (PV après dégâts/soins,
    conditions…) mais la liste `pj` de `partie_<id>.json` — celle que le
    front affiche (barres de PV, cartes joueurs) — reste figée. Fail-safe :
    toute erreur (partie absente du disque…) est ignorée.

    Renvoie l'index du PJ mis à jour dans `etat["pj"]` (pour construire des
    path patches `pj.<i>.<champ>` diffusés en temps réel au front), ou None.
    """
    try:
        from ..game.state import PartyState   # import tardif (évite cycles)

        state = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = state.load()
        if "_erreur" in etat:
            return None
        cible = None
        idx: Optional[int] = None
        for i, p in enumerate(etat.get("pj") or []):
            if str(p.get("nom", "")).lower() == str(nom).strip().lower():
                cible = p
                idx = i
                break
        if cible is None:
            return None
        cible.update(champs)
        etat["pj"] = etat.get("pj") or []
        state.save(etat)
        return idx
    except Exception:                                        # noqa: BLE001
        return None


def _patch_pj(nom: str, idx: Optional[int], champs: dict[str, Any]) -> dict[str, Any]:
    """Construit le state_patch d'un tool touchant une fiche PJ.

    `pj_updated` est un signal (déclenche le re-fetch REST côté front) ;
    les entrées `pj.<i>.<champ>` sont des path patches appliqués en direct
    au store Zustand — les barres de PV bougent sans attendre le polling.
    """
    patch: dict[str, Any] = {"pj_updated": nom}
    if idx is not None:
        for k, v in champs.items():
            patch[f"pj.{idx}.{k}"] = v
    return patch


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def fiche_perso_creer(
    ctx: ToolContext,
    nom: str,
    race: str,
    classe: str,
    niveau: int,
    carac_json: str,
    pv: int,
    pv_max: int,
    ca: int,
    sauvegardes_json: str,
    bab: int,
    competences_json: str,
    dons_json: str,
    equipement_json: str,
    or_total: int,
    alignement: str,
    joueur: str,
    histoire: Optional[str] = "",
) -> ToolResult:
    """
    Crée une fiche personnage D&D 3.5 persistante. À appeler à l'issue de la
    création (chaque champ doit être conforme au Manuel du Joueur 3.5).

    :param nom (str): nom complet du personnage.
    :param race (str): ex. "Nain (de bouclier)".
    :param classe (str): ex. "Guerrier".
    :param niveau (int): niveau de classe (commence à 1).
    :param carac_json (str): JSON dict FOR/DEX/CON/INT/SAG/CHA (ex.
        '{"FOR":17,"DEX":13,"CON":16,"INT":10,"SAG":9,"CHA":12}').
    :param pv (int): points de vie actuels.
    :param pv_max (int): points de vie maximum.
    :param ca (int): classe d'armure.
    :param sauvegardes_json (str): JSON dict Vigueur/Reflexes/Volonte.
    :param bab (int): Bonus de Base à l'Attaque.
    :param competences_json (str): JSON dict competence → rangs.
    :param dons_json (str): JSON liste de noms de dons.
    :param equipement_json (str): JSON liste d'objets.
    :param or_total (int): quantite d'or de depart (en pc).
    :param alignement (str): ex. "Loyal Bon".
    :param joueur (str): nom du joueur humain.
    :param histoire (str): court historique/background du perso. Optionnel.
    """
    try:
        carac = _parse_json(carac_json, "carac_json")
        sauv = _parse_json(sauvegardes_json, "sauvegardes_json")
        comp = _parse_json(competences_json, "competences_json")
        dons = _parse_json(dons_json, "dons_json")
        equip = _parse_json(equipement_json, "equipement_json")
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")

    fiche = {
        "nom": nom,
        "joueur": joueur,
        "race": race,
        "classe": classe,
        "niveau": int(niveau),
        "carac": carac,
        "pv": int(pv),
        "pv_max": int(pv_max),
        "ca": int(ca),
        "sauvegardes": sauv,
        "bab": int(bab),
        "competences": comp,
        "dons": dons,
        "equipement": equip,
        "or": int(or_total),
        "alignement": alignement,
        "histoire": histoire or "",
        "conditions": [],
    }
    try:
        path = _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    return ToolResult(
        text=(
            f"✅ Fiche créée pour **{nom}** ({race} {classe} niv.{niveau}) — "
            f"PV {pv}/{pv_max}, CA {ca}. Fichier : {path}"
        ),
        state_patch={"pj_updated": nom},
    )


@tool
async def fiche_perso_creer_rapide(
    ctx: ToolContext,
    nom: str,
    race: str = "",
    classe: str = "",
    niveau: int = 1,
    pv: int = 0,
    pv_max: int = 0,
    ca: int = 0,
    bab: int = 0,
    carac_texte: str = "",
    sauvegardes_texte: str = "",
    equipement_texte: str = "",
    alignement: str = "",
    joueur: str = "",
    apparence: str = "",
    sexe: str = "",
    age: str = "",
    taille_physique: str = "",
    traitsdistinctifs: str = "",
) -> ToolResult:
    """
    Crée rapidement la fiche d'un personnage. **UN SEUL appel suffit** :
    si `carac_texte` est vide, les 6 caractéristiques sont tirées
    automatiquement (4d6 garder les 3 meilleurs). PV, CA, BBA et
    sauvegardes sont aussi calculés automatiquement selon la classe.

    :param nom (str): nom du personnage (OBLIGATOIRE).
    :param race (str): ex. "Nain", "Elfe", "Humain".
    :param classe (str): ex. "Guerrier", "Voleur", "Magicien", "Clerc".
    :param niveau (int): niveau de classe (défaut: 1).
    :param carac_texte (str): "For 14, Dex 12, ..." — si vide, tirage auto.
    :param joueur (str): nom du joueur humain.
    :param alignement (str): ex. "Loyal Bon".
    :param equipement_texte (str): "Hache de guerre, armure de cuir, 50 pc".
    :param apparence (str): description physique libre du personnage.
    :param sexe (str): "M", "F", "Autre", ou vide.
    :param age (str): ex. "32 ans", "Jeune adulte".
    :param taille_physique (str): ex. "1,65 m, mince", "2,10 m, massif".
    :param traitsdistinctifs (str): ex. "Cicatrice sur l'œil gauche, yeux verts".
    """
    import random as _rnd

    if not joueur:
        joueur = getattr(ctx, "joueur", "") or ""

    # ---- Auto-génération des caractéristiques si non fournies ----
    def _mod(c: int) -> int:
        return (c - 10) // 2

    noms_carac = ["FOR", "DEX", "CON", "INT", "SAG", "CHA"]
    carac_vals: dict[str, int] = {}

    if carac_texte and any(k in carac_texte.upper() for k in noms_carac):
        # Parser le texte libre "For 14, Dex 12, ..."
        for token in carac_texte.replace(",", " ").split():
            if token.upper() in noms_carac:
                current_key = token.upper()
            elif current_key and token.isdigit():
                carac_vals[current_key] = int(token)
                current_key = ""
        caracs_explicites = True
    else:
        # Tirage 4d6 garder les 3 meilleurs × 6
        for nom_c in noms_carac:
            quatre = [_rnd.randint(1, 6) for _ in range(4)]
            trois = sorted(quatre, reverse=True)[:3]
            carac_vals[nom_c] = sum(trois)
        caracs_explicites = False

    # ---- Résolution race (alias FR/EN) + ajustements raciaux 3.5 ----
    race = race or ""
    race_key = _norm_key(race)
    race_norm = _RACE_ALIAS.get(race_key)
    if not race_norm:
        for alias, canon_r in _RACE_ALIAS.items():
            if alias in race_key or race_key in alias:
                race_norm = canon_r
                break
    if race_norm:
        race = race_norm

    mods_raciaux_txt = ""
    if not caracs_explicites:
        mods = _RACE_MODS.get(race_norm or "", {})
        if mods:
            for k, v in mods.items():
                carac_vals[k] = max(1, carac_vals.get(k, 10) + v)
            mods_raciaux_txt = " (ajustements raciaux inclus : " + \
                ", ".join(f"{k} {v:+d}" for k, v in mods.items()) + ")"

    carac_texte_final = ", ".join(
        f"{k} {v} (mod {_mod(v):+d})" for k, v in carac_vals.items()
    )

    bonus_con = _mod(carac_vals.get("CON", 10))
    bonus_for = _mod(carac_vals.get("FOR", 10))
    bonus_dex = _mod(carac_vals.get("DEX", 10))

    # ---- Résolution classe (alias FR/EN) + dé de vie / BBA / saves ----
    classe = classe or ""
    classe_key = _norm_key(classe)
    canon = _CLASSE_ALIAS.get(classe_key)
    if not canon:
        for alias, c in _CLASSE_ALIAS.items():
            if alias in classe_key or classe_key in alias:
                canon = c
                break
    dv, bab_val, sauvegardes_base = _CLASSES_35.get(canon or "", (10, 0, (0, 0, 0)))

    # PV — PHB 3.5 p.22 : au niveau 1, PV = maximum du dé de vie + mod CON
    if int(niveau) == 1:
        pv_roll = dv
    else:
        pv_roll = _rnd.randint(1, dv)
    pv_val = max(pv_roll + bonus_con, 1)
    if not pv_max or pv_max < 1:
        pv_max = pv_val

    # CA = 10 + mod DEX (sans armure ; l'équipement n'est pas structuré)
    ca_val = ca if ca > 10 else 10 + bonus_dex

    vig = sauvegardes_base[0] + bonus_con
    ref = sauvegardes_base[1] + bonus_dex
    vol = sauvegardes_base[2] + _mod(carac_vals.get("SAG", 10))

    # ---- Charger fiche existante ----
    etat_pj = {}
    try:
        import json as _json
        from ..game.state import PartyState as _PS
        _ps = _PS(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        ep = _ps.load()
        pj_list = ep.get("pj") or []
        for p in pj_list:
            if str(p.get("nom", "")).lower() == nom.lower():
                etat_pj = p
                break
    except Exception:
        pass

    # Valeurs résolues : priorité args explicites > etat_partie > auto
    def _pick(key: str, raw: Any, default: Any) -> Any:
        if raw:
            return raw
        if etat_pj.get(key):
            return etat_pj[key]
        return default

    race = _pick("race", race, "(non précisée)")
    classe = _pick("classe", classe, "(non précisée)")
    pv = _pick("pv", pv, pv_val)
    pv_max = _pick("pv_max", pv_max, pv_val)
    ca = _pick("ca", ca, ca_val)
    bab = _pick("bab", bab, bab_val)
    alignement = _pick("alignement", alignement, etat_pj.get("alignement", ""))
    equip = equipement_texte or etat_pj.get("equipement", "")

    fiche = {
        "nom": nom,
        "joueur": joueur,
        "race": race,
        "classe": classe,
        "niveau": int(niveau),
        "carac": carac_vals,
        "pv": int(pv),
        "pv_max": int(pv_max),
        "ca": int(ca),
        "sauvegardes": {"Vigueur": vig, "Reflexes": ref, "Volonte": vol},
        "bab": int(bab),
        "competences": {},
        "dons": [],
        "equipement": [],
        "or": 0,
        "alignement": alignement,
        "histoire": "",
        "conditions": [],
        "apparence": {
            "description": apparence or "",
            "sexe": sexe or "",
            "age": age or "",
            "taille_physique": taille_physique or "",
            "traits_distinctifs": traitsdistinctifs or "",
        },
    }
    try:
        path = _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")

    # Mettre à jour le tableau pj dans l'état partie
    try:
        from ..game.state import PartyState as _PS
        _ps = _PS(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        ep = _ps.load()
        pj_list = ep.get("pj") or []
        pj_entry = {
            "nom": nom,
            "race": race,
            "classe": classe,
            "niveau": int(niveau),
            "pv": int(pv),
            "pv_max": int(pv_max),
            "ca": int(ca),
            # Dict FOR/DEX/… (et non le texte formaté) : engager_combat lit
            # la DEX de ce dict pour l'initiative — une chaîne fait planter
            # le calcul ((str).get n'existe pas).
            "carac": dict(carac_vals),
            "joueur": joueur,
            "alignement": alignement,
        }
        replaced = False
        for i, p in enumerate(pj_list):
            if str(p.get("nom", "")).lower() == nom.lower():
                pj_list[i] = pj_entry
                replaced = True
                break
        if not replaced:
            pj_list.append(pj_entry)
        ep["pj"] = pj_list
        ep["meta"]["date_maj"] = datetime.now().isoformat()
        # Auto-transition : dès qu'un PJ existe, passer en opening_complete
        if ep.get("phase") == "opening":
            ep["phase"] = "opening_complete"
        _ps.save(ep)
    except Exception:
        pass  # la fiche est déjà persistée, ce n'est pas critique

    # Portrait PJ en arrière-plan (ComfyUI). Fire-and-forget : la fiche est
    # déjà persitée, le portrait arrivera en cache sous
    # `portraits_cache/<slug>.png`. On ne bloque pas le tour.
    import asyncio
    async def _gen_portrait():
        try:
            slug = _slug(nom)
            # Inclure party_id pour unicité (même nom dans parties différentes).
            portrait_name = f"{ctx.partie_id}_{slug}" if ctx.partie_id else slug
            cache_dir = os.path.join(ctx.data_dir, "portraits_cache")
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, f"{portrait_name}.png")
            if not os.path.isfile(dest):
                # Prompt complet : race (+ traits visuels), classe (+ catégorie
                # visuelle) et apparence de la fiche. Import tardif : persos
                # importe déjà fiches au niveau module (cycle sinon).
                try:
                    from ..persos import construire_prompt_portrait
                    prompt = construire_prompt_portrait(fiche)
                except Exception:
                    prompt = portrait_prompt(nom, race, classe)
                await generer_averti(ctx, "portrait", prompt, dest)
        except Exception:
            pass
    asyncio.create_task(_gen_portrait())

    carac_summary = ", ".join(f"{k} {v}" for k, v in carac_vals.items())
    return ToolResult(
        text=(
            f"✅ Fiche créée pour **{nom}** ({race} {classe} "
            f"niv.{niveau}) — Carac : {carac_summary}{mods_raciaux_txt} — "
            f"PV {pv}/{pv_max}, CA {ca}, BBA {bab:+d}, "
            f"Sauvegardes : Vigueur {vig:+d}, Réflexes {ref:+d}, Volonté {vol:+d}."
        ),
        state_patch={"pj_updated": nom},
    )


@tool
async def fiche_perso_recuperer(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Récupère la fiche personnage persistante d'un personnage (PJ ou PNJ
    mémorisé), pour consultation pendant la partie.

    :param nom (str): nom du personnage (insensible à la casse/accents).
    """
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    return ToolResult(
        text=(
            f"📜 **Fiche de {fiche['nom']}** "
            f"({fiche.get('race','?')} {fiche.get('classe','?')} "
            f"niv.{fiche.get('niveau','?')})\n"
            f"- Joueur : {fiche.get('joueur','?')}\n"
            f"- Alignement : {fiche.get('alignement','?')}\n"
            f"- Caractéristiques : {fiche.get('carac','?')}\n"
            f"- PV : {fiche.get('pv','?')}/{fiche.get('pv_max','?')} — "
            f"CA : {fiche.get('ca','?')}\n"
            f"- Sauvegardes : {fiche.get('sauvegardes','?')}\n"
            f"- BBA : {fiche.get('bab','?')}\n"
            f"- Compétences : {fiche.get('competences','?')}\n"
            f"- Dons : {fiche.get('dons','?')}\n"
            f"- Équipement : {fiche.get('equipement','?')} — "
            f"Or : {fiche.get('or','?')} pc\n"
            f"- Conditions actives : {fiche.get('conditions','[]')}\n"
            f"\n[JSON complet]\n" + json.dumps(fiche, ensure_ascii=False, indent=2)
        )
    )


@tool
async def fiche_perso_mettre_a_jour(
    ctx: ToolContext,
    nom: str,
    champ: str,
    valeur: str,
) -> ToolResult:
    """
    Met à jour un champ d'une fiche personnage persistante. Pour les champs
    imbriqués (ex. carac.FOR), on commence à creuser dans carac.

    :param nom (str): nom du personnage.
    :param champ (str): nom du champ (top-level) ou notation point pour
        imbriqué (ex. "carac.FOR", "competences.Escalade", "pv").
    :param valeur (str): nouvelle valeur (interprétée JSON si possible).
    """
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")

    try:
        v: Any = json.loads(valeur)
    except json.JSONDecodeError:
        v = valeur

    keys = champ.split(".")
    cur: Any = fiche
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = v

    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    # Si le champ touche l'entrée PJ affichée (pv, pv_max, ca…), on synchronise
    # l'état de partie pour que le front voie la fiche évoluer en direct.
    if keys[0] in ("pv", "pv_max", "ca", "conditions") and len(keys) == 1:
        idx = _sync_pj(ctx, nom, {keys[0]: v})
        return ToolResult(
            text=(
                f"✅ Fiche de {nom} mise à jour : {champ} = "
                f"{json.dumps(v, ensure_ascii=False)}"
            ),
            state_patch=_patch_pj(nom, idx, {keys[0]: v}),
        )
    return ToolResult(
        text=(
            f"✅ Fiche de {nom} mise à jour : {champ} = "
            f"{json.dumps(v, ensure_ascii=False)}"
        ),
        state_patch={"pj_updated": nom},
    )


@tool
async def fiche_perso_lister(ctx: ToolContext) -> ToolResult:
    """
    Liste tous les personnages dont une fiche persistante existe (PJ comme PNJ
    mémorisés). Aucun argument.
    """
    fiches_dir = _fiches_dir(ctx)
    try:
        fichiers = [
            fn for fn in os.listdir(fiches_dir)
            if fn.startswith("fiche_") and fn.endswith(".json")
        ]
    except OSError:
        return ToolResult(text="ℹ️ Aucune fiche enregistrée (dossier absent).")
    if not fichiers:
        return ToolResult(text="ℹ️ Aucune fiche enregistrée.")
    noms: list[str] = []
    for fn in fichiers:
        path = os.path.join(fiches_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            noms.append(
                f"- **{d.get('nom','?')}** ({d.get('race','?')} "
                f"{d.get('classe','?')} niv.{d.get('niveau','?')}) — "
                f"joueur : {d.get('joueur','?')}"
            )
        except (json.JSONDecodeError, OSError):
            noms.append(f"- (fichier illisible : {fn})")
    return ToolResult(
        text="📜 Fiches personnages enregistrées :\n" + "\n".join(noms)
    )


def _norm_nom_simple(s: Any) -> str:
    """Comparaison de noms insensible casse/accents (PJ ↔ monstres du combat)."""
    s = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _infliger_degats_monstre(
    ctx: ToolContext, nom: str, d: int
) -> Optional[ToolResult]:
    """Applique des dégâts à un MONSTRE suivi par `engager_combat`
    (etat.monstres_combat). Renvoie None si `nom` ne correspond à aucun
    monstre suivi — l'appelant retombe alors sur le message d'erreur PJ."""
    try:
        from ..game.state import PartyState  # lazy : évite les cycles
        st = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = st.load()
    except Exception:                                        # noqa: BLE001
        return None
    mons = etat.get("monstres_combat") or []
    cn = _norm_nom_simple(nom)
    cible = None
    for m in mons:
        mn = _norm_nom_simple(m.get("nom"))
        if (
            mn == cn
            or (len(cn) >= 4 and mn.startswith(cn))
            or (len(mn) >= 4 and cn.startswith(mn))
        ):
            cible = m
            break
    if cible is None:
        return None
    if cible.get("inconnu"):
        return ToolResult(
            text=(
                f"❓ PV de **{cible['nom']}** inconnus (absent du bestiaire). "
                f"Consulte la KB « D&D 3.5 — Manuels » puis fixe ses PV via "
                f"etat_partie_patch(monstres_combat, …) avant d'infliger des "
                f"dégâts suivis."
            )
        )
    nv = int(cible.get("pv", 0)) - d
    cible["pv"] = nv
    conds: list[str] = cible.setdefault("conditions", [])
    note = ""
    if nv <= 0 and "Détruit" not in conds:
        conds.append("Détruit")
        note = " — ☠️ **DÉTRUIT** (0 PV ou moins)."
    elif "Détruit" in conds:
        note = " — ☠️ déjà détruit."
    err = st.save(etat)
    if err:
        return ToolResult(text=f"❌ {err}")
    restants = ", ".join(
        f"{m['nom']} {m['pv']}/{m['pv_max']}"
        + (" ☠️" if "Détruit" in (m.get("conditions") or []) else "")
        for m in mons
    )
    msg = (
        f"💥 {cible['nom']} (monstre) subit {d} dégâts → "
        f"PV {nv}/{cible.get('pv_max', '?')}{note}\n"
        f"Ennemis : {restants}"
    )
    return ToolResult(text=msg, state_patch={"monstres_combat": mons})


@tool
async def fiche_perso_infliger_degats(
    ctx: ToolContext, nom: str, degats: int
) -> ToolResult:
    """
    Applique des dégâts à un personnage (réduit ses PV). Renvoie l'état après
    coup, avec mention éventuelle de KO ou mort. Fonctionne AUSSI pour les
    monstres engagés via engager_combat (PV suivis mécaniquement).

    :param nom (str): nom du personnage ou du monstre.
    :param degats (int): nombre de points de dégâts (≥0).
    """
    fiche = _load_fiche(ctx, nom)
    d = max(0, int(degats))
    if fiche is None:
        r = _infliger_degats_monstre(ctx, nom, d)
        if r is not None:
            return r
        return ToolResult(
            text=(
                f"❌ Aucune fiche trouvée pour '{nom}' (ni monstre suivi par "
                f"le combat en cours — engager_combat initialise les PV)."
            )
        )
    # D&D 3.5 (Injury and Death) : les PV peuvent descendre sous 0 ;
    # mort à -10 (ou gros dégâts d'un coup : mort si pv - d ≤ -10).
    nv = int(fiche.get("pv", 0)) - d
    if nv < -10:
        nv = -10
    fiche["pv"] = nv
    # Conditions d'état selon la barre de PV (règles officielles).
    conds: list[str] = fiche.setdefault("conditions", [])
    for c in ("Invalide", "Mourant", "Mort"):
        if c in conds:
            conds.remove(c)
    etat_msg = ""
    if nv <= -10:
        conds.append("Mort")
        etat_msg = " — ☠️ **MORT** (-10 PV ou moins)."
    elif nv == 0:
        conds.append("Invalide")
        etat_msg = (
            " — ⚠️ **Invalide** (0 PV) : actions limitées (mouvement ou "
            "action standard), toute action vigoureuse → 1 PV de dégâts."
        )
    elif nv < 0:
        conds.append("Mourant")
        etat_msg = (
            f" — ⚠️ **Mourant** ({nv} PV) : inconscient, jet de "
            "stabilisation 1d20 ≥ 10 par round (1 naturel = -1 PV)."
        )
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    # Synchro de l'entrée PJ de l'état de partie (barres PV du front).
    idx = _sync_pj(ctx, nom, {"pv": nv, "conditions": conds})
    msg = f"💥 {nom} subit {d} dégâts → PV {nv}/{fiche.get('pv_max','?')}" + etat_msg
    return ToolResult(
        text=msg,
        state_patch=_patch_pj(nom, idx, {"pv": nv, "conditions": conds}),
    )


@tool
async def fiche_perso_soigner(
    ctx: ToolContext, nom: str, soin: int
) -> ToolResult:
    """
    Soigne un personnage (restaure des PV, plafonnés à pv_max).

    :param nom (str): nom du personnage.
    :param soin (int): points de vie restaurés (≥0).
    """
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    s = max(0, int(soin))
    max_pv = int(fiche.get("pv_max", 0))
    nv = min(max_pv, int(fiche.get("pv", 0)) + s)
    fiche["pv"] = nv
    # PV positifs → on lève les états liés aux blessures (règles 3.5).
    conds: list[str] = fiche.setdefault("conditions", [])
    nettoye = False
    for c in ("Mourant", "Invalide"):
        if c in conds and nv > 0:
            conds.remove(c)
            nettoye = True
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    # Synchro de l'entrée PJ de l'état de partie (barres PV du front).
    idx = _sync_pj(ctx, nom, {"pv": nv, "conditions": conds})
    return ToolResult(
        text=(
            f"✨ {nom} récupère {s} PV → PV {nv}/{max_pv}"
            + (" (maximum atteint)" if nv == max_pv else "")
            + (" — conditions de blessure levées." if nettoye else "")
        ),
        state_patch=_patch_pj(nom, idx, {"pv": nv, "conditions": conds}),
    )


@tool
async def fiche_perso_condition(
    ctx: ToolContext, nom: str, condition: str, appliquer: bool = True
) -> ToolResult:
    """
    Applique ou retire une condition D&D 3.5 à un personnage (Étourdi,
    Saignant, Paniqué, Aveugle, Pris en tenaille, Affecté…). La liste des
    conditions actives est stockée dans `fiche['conditions']`.

    :param nom (str): nom du personnage.
    :param condition (str): nom de la condition (ex. "Étourdi").
    :param appliquer (bool): True pour appliquer, False pour retirer.
    """
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    conds: list[str] = fiche.setdefault("conditions", [])
    cond = condition.strip()
    if appliquer and cond not in conds:
        conds.append(cond)
    if not appliquer and cond in conds:
        conds.remove(cond)
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    # Synchro de l'entrée PJ de l'état de partie (conditions affichées).
    idx = _sync_pj(ctx, nom, {"conditions": conds})
    action = "affecté par" if appliquer else "n'est plus affecté par"
    return ToolResult(
        text=f"{nom} est {action} **{cond}**. Conditions actives : {conds}",
        state_patch=_patch_pj(nom, idx, {"conditions": conds}),
    )


@tool
async def fiche_perso_supprimer(ctx: ToolContext, nom: str) -> ToolResult:
    """
    ⚠️ Supprime la fiche personnage d'un personnage. À confirmer d'abord.

    :param nom (str): nom du personnage.
    """
    path = _chemin(ctx, nom)
    try:
        os.remove(path)
        return ToolResult(text=f"🗑️ Fiche de {nom} supprimée.")
    except FileNotFoundError:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    except OSError as e:
        return ToolResult(text=f"❌ Erreur : {e}")
