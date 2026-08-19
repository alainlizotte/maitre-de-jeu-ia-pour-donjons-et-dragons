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


def _chemin(ctx: ToolContext, nom: str) -> str:
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
    ca: int = 10,
    bab: int = 0,
    carac_texte: str = "",
    sauvegardes_texte: str = "",
    equipement_texte: str = "",
    alignement: str = "",
    joueur: str = "",
) -> ToolResult:
    """
    Version simplifiée de `fiche_perso_creer` : POST-ouverture, à appeler
    dès que le personnage du joueur a été créé (via etat_partie_patch sur
    `pj.0.nom/race/classe/...`) pour **persister durablement** sa fiche. Ne
    demande QUE le nom en obligatoire ; tous les autres champs ont des
    défauts simples (classe, race, niveau, etc.). Les carac / sauvegardes
    / équipement sont passés comme **texte libre** (pas du JSON) :
    ex. "For 14, Dex 12, Con 18, Int 10, Sag 12, Cha 9".

    Plus accessible que `fiche_perso_creer` (qui exige 16 JSON obligatoires)
    — à utiliser sans hésiter en fin de création de perso.

    :param nom (str): nom du personnage (OBLIGATOIRE).
    :param race (str): ex. "Nain".
    :param classe (str): ex. "Guerrier".
    :param niveau (int): niveau de classe (commence à 1).
    :param pv (int): points de vie actuels.
    :param pv_max (int): PV max.
    :param ca (int): classe d'armure.
    :param bab (int): Bonus de Base à l'Attaque.
    :param carac_texte (str): "For 14, Dex 12, Con 18, …" (texte libre).
    :param sauvegardes_texte (str): "Vigueur +4, Réflexes +2, Volonté +1".
    :param equipement_texte (str): "Hache de guerre, armure de cuir, 50 pc".
    :param alignement (str): ex. "Loyal Bon".
    :param joueur (str): nom du joueur humain.
    """
    # Si joueur vide, on essaie le champ ctx.joueur
    if not joueur:
        joueur = getattr(ctx, "joueur", "") or ""

    # Charger l'éventuelle fiche existante (si le PJ existe déjà dans l'état
    # partie, on récupère race/classe etc. sans forcer l'utilisateur à
    # répéter).
    etat_pj = {}
    try:
        import json as _json
        etat_path = (
            _fiches_dir(ctx).rsplit("fiches", 1)[0]
            + f"partie_{ctx.partie_id}.json"
        )
        with open(etat_path, "r", encoding="utf-8") as f:
            ep = _json.load(f)
        pj_list = ep.get("pj") or []
        for p in pj_list:
            if str(p.get("nom", "")).lower() == nom.lower():
                etat_pj = p
                break
    except (OSError, _json.JSONDecodeError, Exception):
        pass

    # Valeurs résolues : priorité args explicites > etat_partie > défauts.
    def _pick(key: str, raw: Any, default: Any) -> Any:
        if raw:
            return raw
        if etat_pj.get(key):
            return etat_pj[key]
        return default

    race = _pick("race", race, "(non précisée)")
    classe = _pick("classe", classe, "(non précisée)")
    niveau = _pick("niveau", niveau, 1)
    pv = _pick("pv", pv, int(etat_pj.get("pv", 0)) or 10)
    pv_max = _pick("pv_max", pv_max, int(etat_pj.get("pv_max", pv)) or pv)
    ca = _pick("ca", ca, int(etat_pj.get("ca", 10)) or 10)
    bab = _pick("bab", bab, int(etat_pj.get("bab", 0)) or 0)
    alignement = _pick("alignement", alignement,
                       etat_pj.get("alignement", ""))
    carac = carac_texte or etat_pj.get("carac", "")
    sauv = sauvegardes_texte or etat_pj.get("sauvegardes", "")
    equip = equipement_texte or etat_pj.get("equipement", "")

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
        "competences": {},
        "dons": [],
        "equipement": equip,
        "or": 0,
        "alignement": alignement,
        "histoire": "",
        "conditions": [],
    }
    try:
        path = _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")

    # Portrait PJ en arrière-plan (ComfyUI). Fire-and-forget : la fiche est
    # déjà persitée, le portrait arrivera en cache sous
    # `portraits_cache/<slug>.png`. On ne bloque pas le tour.
    import asyncio
    async def _gen_portrait():
        try:
            slug = _slug(nom)
            cache_dir = os.path.join(ctx.data_dir, "portraits_cache")
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, f"{slug}.png")
            if not os.path.isfile(dest):
                prompt = portrait_prompt(nom, race, classe)
                await generer_averti(ctx, "portrait", prompt, dest)
        except Exception:
            pass  # le portrait est bonus — pas critique
    asyncio.create_task(_gen_portrait())

    return ToolResult(
        text=(
            f"✅ Fiche persistante créée pour **{nom}** ({race} {classe} "
            f"niv.{niveau}) — PV {pv}/{pv_max}, CA {ca}, BBA {bab:+}. "
            f"Fichier : {path}"
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


@tool
async def fiche_perso_infliger_degats(
    ctx: ToolContext, nom: str, degats: int
) -> ToolResult:
    """
    Applique des dégâts à un personnage (réduit ses PV). Renvoie l'état après
    coup, avec mention éventuelle de KO ou mort.

    :param nom (str): nom du personnage.
    :param degats (int): nombre de points de dégâts (≥0).
    """
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    d = max(0, int(degats))
    nv = max(0, int(fiche.get("pv", 0)) - d)
    fiche["pv"] = nv
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    msg = f"💥 {nom} subit {d} dégâts → PV {nv}/{fiche.get('pv_max','?')}"
    if nv == 0:
        msg += (
            " — ⚠️ Le personnage est à 0 PV. Vérifier état (mourant/mort "
            "selon D&D 3.5)."
        )
    return ToolResult(text=msg, state_patch={"pj_updated": nom})


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
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    return ToolResult(
        text=(
            f"✨ {nom} récupère {s} PV → PV {nv}/{max_pv}"
            + (" (maximum atteint)" if nv == max_pv else "")
        ),
        state_patch={"pj_updated": nom},
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
    action = "affecté par" if appliquer else "n'est plus affecté par"
    return ToolResult(
        text=f"{nom} est {action} **{cond}**. Conditions actives : {conds}",
        state_patch={"pj_updated": nom},
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
