"""Tools magie D&D 3.5 : incantation avec validation stricte, préparation
quotidienne des sorts, repos long.

La mécanique appliquée par `incanter_sort` (PHB 3.5) :
- le sort doit exister et figurer dans la liste de CLASSE du lanceur ;
- le niveau de sort doit être CASTABLE pour sa classe/son niveau (table
  d'emplacements — un magicien niv.1 ne lance pas de boule de feu) ;
- spontané (Sorcier/Barde) : le sort doit être CONNU ;
  préparateur (Magicien/Clerc/Druide/Paladin/Rodeur) : il doit avoir été
  PRÉPARÉ ce jour (`preparer_sorts`) — chaque préparation est dépensée
  à l'incantation ;
- un EMPLACEMENT du niveau du sort est consommé (décompte quotidien,
  réinitialisé au repos long) ;
- l'EFFET est résolu mécaniquement : dégâts (jet de dés + application via
  le suivi PV PJ/monstre), soins, condition (jet de sauvegarde demandé au
  MJ pour les sorts « Volonté annule »).
"""

from __future__ import annotations

import random
from typing import Any

from .base import ToolContext, ToolResult, tool


def _norm(s: str) -> str:
    import unicodedata
    nf = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nf if not unicodedata.combining(c)).strip()


def _fiche_pj(ctx: ToolContext, nom: str):
    from .fiches import _load_fiche
    return _load_fiche(ctx, nom)


def _rouler_des(formule: str) -> int:
    """Roule une formule « NdF » (ex. « 3d6 ») — renvoie 0 si invalide."""
    try:
        nb, faces = formule.lower().split("d")
        return sum(random.randint(1, int(faces)) for _ in range(max(1, int(nb))))
    except (ValueError, TypeError):
        return 0


# ♻️ Soins magiques DÉJÀ appliqués ce tour (partie, tour, cible) → montant.
# `incanter_sort` applique LUI-MÊME l'effet « soin » (appel interne à
# fiche_perso_soigner) : un second appel manuel du LLM double le soin, et un
# appel après REFUS de l'incantation invente un soin fictif (partie
# 2ca691ec). Inactif sans `tour_id` (REST/tests).
_SOINS_MAGIQUES_TOUR: dict[tuple, int] = {}


def _marquer_soin_magique(ctx: ToolContext, cible: str, montant: int) -> None:
    cle = (ctx.partie_id, ctx.tour_id, str(cible or "").strip().casefold())
    for k in [k for k in _SOINS_MAGIQUES_TOUR if k[:2] != cle[:2]]:
        _SOINS_MAGIQUES_TOUR.pop(k, None)
    _SOINS_MAGIQUES_TOUR[cle] = montant


def _soin_magique_du_tour(ctx: ToolContext, cible: str):
    return _SOINS_MAGIQUES_TOUR.get(
        (ctx.partie_id, ctx.tour_id, str(cible or "").strip().casefold())
    )


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def incanter_sort(
    ctx: ToolContext,
    nom_personnage: str,
    nom_sort: str,
    cible: str = "",
    degats_subis: int = 0,
) -> ToolResult:
    """
    Lance un sort en appliquant les règles D&D 3.5 : classe autorisée,
    niveau de sort castable, sort connu (Sorcier/Barde) ou préparé ce jour
    (Magicien/Clerc/Druide/Paladin/Rodeur), emplacement disponible. L'effet
    mécanique est résolu automatiquement (dégâts, soins, condition).
    Le tool REFUSE et explique si une règle manque (sort non préparé,
    emplacement épuisé…) — ne jamais narrer un sort sans ce tool.

    :param nom_personnage (str): nom du lanceur (insensible casse/accents).
    :param nom_sort (str): nom exact du sort (ex. « Boule de feu »).
    :param cible (str): nom de la cible (PJ ou monstre). Vide = soi-même /
        utilitaire.
    :param degats_subis (int): OPTIONNEL — dégâts ENCAISSÉS CE ROUND par le
        lanceur : force un jet de CONCENTRATION comparé serveur au DD
        officiel 10 + dégâts (max 20) ; échec = le sort est PERDU
        (emplacement dépensé). 0 = lancement tranquille (chantier DRS
        P3-#18).
    """
    from .. import sorts as cat
    from .fiches import _save_fiche, _sync_pj, _patch_pj

    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")

    classe = str(fiche.get("classe") or "")
    niveau = max(1, int(fiche.get("niveau") or 1))
    sort = cat.sort_par_nom(nom_sort, classe)
    if sort is None:
        return ToolResult(
            text=(f"❌ Sort inconnu : « {nom_sort} ». Consulte la liste "
                  f"officielle (catalogue du formulaire) — n'invente pas de sort.")
        )

    # 1) Liste de classe -----------------------------------------------------
    if classe not in sort["classes"]:
        return ToolResult(
            text=(f"⛔ **{classe}** ne peut pas lancer **{sort['nom']}** "
                  f"(classes autorisées : {', '.join(sort['classes'])}).")
        )

    # 2) Niveau de sort castable ---------------------------------------------
    carac_cle = cat.carac_incantation(classe)
    val_carac = int((fiche.get("carac") or {}).get(carac_cle, 10) or 10)
    mod_carac = (val_carac - 10) // 2
    nls = cat.niveau_sort_max(classe, niveau)
    if nls < sort["niveau"]:
        dispo = f"jusqu'au niveau {nls}" if nls >= 0 else "aucun sort"
        return ToolResult(
            text=(f"⛔ {nom_personnage} ({classe} niv.{niveau}) ne peut lancer "
                  f"{dispo} de sort — **{sort['nom']}** (niv. {sort['niveau']}) "
                  f"est trop puissant.")
        )
    slots = cat.emplacements(classe, niveau, mod_carac)

    # 3) Préparé / connu ------------------------------------------------------
    etat_sorts = cat.sorts_de_fiche(fiche)
    # Une fiche avec un champ `sorts` présent (même vide) a été configurée
    # via le formulaire : validation STRICTE. Une fiche SANS champ `sorts`
    # (créée vite / importée) n'a jamais eu de liste choisie : l'incantation
    # d'un sort castable est alors AUTO-ADOPTÉE à la première utilisation —
    # sinon un Sorcier/Magicien sans formulaire ne pourrait JAMAIS lancer
    # (observé en e2e : incanter_sort refusé en boucle, combat figé).
    sorts_configures = isinstance(fiche.get("sorts"), dict)
    spontane = cat.type_lancement(classe) == "spontané"
    if spontane:
        if not any(_norm(s) == _norm(sort["nom"]) for s in etat_sorts["connus"]):
            if sorts_configures:
                return ToolResult(
                    text=(f"⛔ **{sort['nom']}** n'est pas dans les sorts connus de "
                          f"{nom_personnage} (spontané : liste fixe choisie à la création).")
                )
            etat_sorts["connus"].append(sort["nom"])
    else:
        restants_prep = int(etat_sorts["prepares"].get(sort["nom"], 0))
        if restants_prep <= 0:
            if sorts_configures:
                return ToolResult(
                    text=(f"⛔ **{sort['nom']}** n'a pas été préparé aujourd'hui par "
                          f"{nom_personnage} — utilise preparer_sorts (repos/méditation). "
                          "Cite ce refus TEL QUEL dans ta narration : n'invente ni une "
                          "autre raison (le sort existe bel et bien), ni une réussite, "
                          "ni un soin/effet de remplacement.")
                )
            etat_sorts["prepares"][sort["nom"]] = 1

    # 4) Emplacement disponible ----------------------------------------------
    lvl = sort["niveau"]
    depense = int(etat_sorts["depenses"].get(str(lvl), 0))
    total = slots.get(lvl, 0)
    if depense >= total:
        return ToolResult(
            text=(f"⛔ Plus aucun emplacement de niveau {lvl} pour "
                  f"{nom_personnage} ({depense}/{total} utilisés). "
                  f"Un repos long les restaure. Cite ce refus TEL QUEL : "
                  "le sort EXISTE, ce sont les emplacements qui sont "
                  "épuisés — n'annonce ni réussite ni effet de remplacement.")
        )

    # 5) Consommation ----------------------------------------------------------
    etat_sorts["depenses"][str(lvl)] = depense + 1
    if not spontane:
        # ⛔ Clamp ≥ 0 (partie 2ca691ec) : un compteur de préparation est
        # passé à -1 (décréments successifs sur fiche non configurée),
        # rendant tout preparer_sorts ultérieur inopérant côté lectures.
        reste = max(0, restants_prep - 1)
        if reste > 0:
            etat_sorts["prepares"][sort["nom"]] = reste
        else:
            # 🧹 gruumsh4pj (P26b) : l'entrée à 0 est SUPPRIMÉE — un « 0 »
            # résiduel polluait l'état (fiche lisible « Armure du mage: 0 »)
            # et brouillait les lectures suivantes.
            etat_sorts["prepares"].pop(sort["nom"], None)
    fiche["sorts"] = etat_sorts
    _save_fiche(ctx, nom_personnage, fiche)

    nls_lanceur = niveau  # pour les effets scalés
    details = " — ".join(x for x in (
        f"Temps d'incantation : {sort['incantation']}" if sort.get("incantation") else "",
        f"Portée : {sort['portee']}" if sort.get("portee") else "",
        f"Composantes : {sort['composantes']}" if sort.get("composantes") else "",
        f"Durée : {sort['duree']}" if sort.get("duree") else "",
    ) if x)
    lignes = [
        f"✨ **{sort['nom']}** (niv. {lvl}) lancé par {nom_personnage}"
        + (f" → **{cible}**" if cible else ""),
        f"- Emplacements niv.{lvl} : {depense + 1}/{total}",
    ]
    if details:
        lignes.append(f"- {details}")
    # 🎯 Jet de CONCENTRATION effectif quand le sort exige une concentration
    # (durée « concentration… ») : le DD de contexte (blessures, interruption)
    # dépend de la scène — le MJ compare ce total au DD du moment. La
    # compétence Concentration (CON) est recoupée depuis la fiche (rangs +
    # mod. CON), elle n'est plus jamais inapplicable.
    # ⚡ Chantier DRS (P3-#18) : avec `degats_subis` > 0, le DD est
    # OFFICIEL et COMPARÉ SERVEUR (10 + dégâts, max 20) — échec = sort perdu.
    concentration_perdue = False
    if "concentration" in str(sort.get("duree") or "").lower() \
            or int(degats_subis or 0) > 0:
        try:
            _rangs = 0
            for _nom_c, _r in (fiche.get("competences") or {}).items():
                if _norm(_nom_c) == "concentration":
                    _rangs = int(_r or 0)
                    break
            _val_con = int((fiche.get("carac") or {}).get("CON", 10) or 10)
            _mod_con = (_val_con - 10) // 2
            _jet = random.randint(1, 20)
            _total_con = _jet + _rangs + _mod_con
            _dd_ctx = ""
            if int(degats_subis or 0) > 0:
                _dd = 10 + min(20, int(degats_subis))
                _ok = (_jet == 20 or (_total_con >= _dd and _jet != 1))
                concentration_perdue = not _ok
                _dd_ctx = (
                    f" — DD {10 + min(20, int(degats_subis))} (10 + "
                    f"{int(degats_subis)} dégâts subis ce round) → "
                    + ("✅ **concentration tenue**."
                       if _ok else
                       "❌ **ÉCHEC — le sort est PERDU** (emplacement "
                       "dépensé, aucun effet).")
                )
            lignes.append(
                f"- 🎯 Jet de Concentration — Concentration {_rangs} rangs "
                f"+ CON {_val_con} ({_mod_con:+d}) : jet brut {_jet} → "
                f"**{_total_con}** (à comparer au DD de contexte : 10 + "
                f"dégâts subis…){_dd_ctx}"
            )
        except Exception:                                    # noqa: BLE001
            pass  # fiche illisible → pas de jet de concentration

    # 6) Effet mécanique -------------------------------------------------------
    effet = sort.get("effet") or {}
    type_effet = effet.get("type")
    # ⚡ Concentration brisée (degats_subis) → SORT PERDU : aucun effet
    # mécanique ; l'emplacement est dépensé (règle 3.5, chantier P3-#18).
    if concentration_perdue:
        effet = {}
        type_effet = None
        lignes.append(
            "- ❌ **Sort PERDU** (concentration brisée) : aucun effet "
            "mécanique appliqué — l'emplacement est dépensé.")
    # Patches PV/conditions des cibles (pj.<i>.pv…) à propager au front.
    patches_cibles: list[dict] = []
    if type_effet == "degats":
        if effet.get("degats_fixes"):
            # Formule fixe (ex. Tempête de glace « 3d6+2d6 »).
            total_deg = 0
            detail = []
            for part in str(effet["degats_fixes"]).split("+"):
                v = _rouler_des(part.strip())
                total_deg += v
                detail.append(f"{part.strip()}={v}")
            lignes.append(
                f"- Dégâts ({effet['degats_fixes']} {effet.get('element', '')}) : "
                + ", ".join(detail) + f" → **{total_deg}**"
            )
        else:
            des = str(effet.get("des", "1d6"))
            faces = int(des.split("d")[1]) if "d" in des else 6
            if effet.get("par_multiple"):
                # Projectiles magiques : 1 projectile, +1 tous les 2 niveaux.
                nb = min(1 + (nls_lanceur - 1) // 2, 5)
            elif effet.get("par_niveau"):
                nb = effet["par_niveau"] * nls_lanceur
                if effet.get("max_des"):
                    nb = min(nb, int(effet["max_des"]))
            else:
                nb = 1
            nb = max(1, nb)
            jets = [random.randint(1, faces) for _ in range(nb)]
            total_deg = sum(jets)
            lignes.append(
                f"- Dégâts ({nb}d{faces} {effet.get('element', '')}) : "
                f"{jets} → **{total_deg}**"
            )
        if cible:
            from .fiches import fiche_perso_infliger_degats
            # 🛡️ Défenses du monstre cible (chantier DRS P1-#2) : immunités,
            # résistances (feu/froid/électricité/acide/sonique) et RD
            # applicables — l'élément est déduit du NOM du sort.
            _note_def_sp = ""
            try:
                from ..game.state import PartyState as _PS_sp
                from ..game.regles35 import (
                    defenses_monstre as _dm_sp, reduire_degats_monstre as _rd_sp,
                )
                _et_sp = _PS_sp(data_dir=ctx.data_dir,
                                partie_id=ctx.partie_id).load()
                _cn_sp = _norm(cible)
                _mo_sp = next(
                    (mo for mo in _et_sp.get("monstres_combat") or []
                     if _norm(mo.get("nom")) == _cn_sp), None)
                if _mo_sp is None:
                    from .monstres import _find_monstre as _fm_sp
                    _mo_sp = _fm_sp(ctx, cible)
                if _mo_sp is not None and _dm_sp(_mo_sp):
                    _avant_sp = total_deg
                    total_deg, _note_def_sp = _rd_sp(
                        _mo_sp, total_deg, str(sort.get("nom") or ""))
            except Exception:                                # noqa: BLE001
                pass
            tr_deg = await fiche_perso_infliger_degats(
                ctx, cible, total_deg, source=str(sort.get("nom") or ""))
            lignes.append(f"- {tr_deg.text}")
            if _note_def_sp:
                lignes.append(f"- {_note_def_sp}")
            # Le patch PV de la cible (pj.<i>.pv) est propagé au front pour
            # une mise à jour en direct de la barre de vie.
            if tr_deg.state_patch:
                patches_cibles.append(tr_deg.state_patch)
        if sort.get("sauvegarde"):
            dd = 10 + lvl + mod_carac
            lignes.append(f"- 🛡️ Sauvegarde : {sort['sauvegarde']} "
                          f"(DD {dd}) → demi-dégâts si réussie.")
    elif type_effet == "soin":
        if "fixe" in effet:
            total_soin = int(effet.get("fixe") or 1)
            lignes.append(f"- Soins : **{total_soin} PV**")
        else:
            des = str(effet.get("des", "1d8"))
            faces = int(des.split("d")[1]) if "d" in des else 8
            base = int(des.split("d")[0] or 1)
            nb = base
            if effet.get("par_niveau"):
                nb = effet["par_niveau"] * nls_lanceur
                if effet.get("max_des"):
                    nb = min(nb, int(effet["max_des"]))
            nb = max(1, nb)
            jets = [random.randint(1, faces) for _ in range(nb)]
            bonus = nb if effet.get("par_niveau") else 0
            total_soin = sum(jets) + bonus
            lignes.append(f"- Soins ({nb}d{faces}+{bonus}) : {jets} → **{total_soin} PV**")
        if effet.get("masse"):
            lignes.append("- Sort de masse : étends les soins aux alliés dans 9 m (MJ).")
        if cible:
            from .fiches import fiche_perso_soigner
            tr_soin = await fiche_perso_soigner(
                ctx, cible, total_soin, interne=True)
            lignes.append(f"- {tr_soin.text}")
            if tr_soin.state_patch:
                patches_cibles.append(tr_soin.state_patch)
            if ctx.tour_id:
                _marquer_soin_magique(ctx, cible, total_soin)
    elif type_effet == "etat" and cible:
        from .fiches import fiche_perso_condition
        tr_cond = await fiche_perso_condition(ctx, cible, effet["condition"], True)
        lignes.append(f"- {tr_cond.text}")
        if tr_cond.state_patch:
            patches_cibles.append(tr_cond.state_patch)
        if sort.get("sauvegarde"):
            dd = 10 + lvl + mod_carac
            lignes.append(f"- 🛡️ Sauvegarde : {sort['sauvegarde']} "
                          f"(DD {dd}) → si réussie, la condition est annulée : "
                          "retire-la via fiche_perso_condition(appliquer=False).")
    else:
        lignes.append(f"- Effet : {sort.get('description', 'utilitaire')}")

    # 🛡️ gruumsh4pj (P24) : les sorts à effet d'ARMURE étaient purement
    # narratifs — emplacement dépensé, prose « CA 18 », fiche inchangée
    # (CA 14, aucune condition). L'effet mécanique est appliqué ici :
    # champ racine `effets_actifs` (hors schéma `conditions` qui n'accepte
    # que des chaînes) + bonus CA sur la fiche, purge automatique à échéance
    # (voir `_purger_effets_expires` dans fiches.py).
    if _norm(sort["nom"]) == _norm("Armure du mage"):
        try:
            from datetime import datetime as _dt, timedelta as _td
            _bonus_ca = 4
            _duree_min = 60 * max(1, nls_lanceur)   # 1 heure / niveau (3.5)
            _fin = (_dt.now() + _td(minutes=_duree_min)).isoformat()
            _effets = [e for e in (fiche.get("effets_actifs") or [])
                       if isinstance(e, dict)
                       and _norm(str(e.get("nom") or "")) != _norm("Armure du mage")]
            _effets.append({"nom": "Armure du mage",
                            "ca_bonus": _bonus_ca, "fin_ts": _fin})
            fiche["effets_actifs"] = _effets
            fiche["ca"] = int(fiche.get("ca", 10) or 10) + _bonus_ca
            _save_fiche(ctx, nom_personnage, fiche)
            _idx_am = _sync_pj(ctx, nom_personnage, {"ca": fiche["ca"]})
            patches_cibles.append(_patch_pj(nom_personnage, _idx_am,
                                            {"ca": fiche["ca"]}))
            lignes.append(
                f"- 🛡️ Armure magique : +{_bonus_ca} de CA (bonus d'armure) "
                f"pour **{nom_personnage}** → CA **{fiche['ca']}** "
                f"(durée {_duree_min // 60} h, s'efface automatiquement)."
            )
        except Exception as _e_am:                           # noqa: BLE001
            lignes.append(f"- (effet Armure du mage non appliqué : {_e_am})")

    idx = _sync_pj(ctx, nom_personnage, {})
    # Patch final : signal lanceur + les patches PV/conditions des cibles
    # (pj.<i>.pv etc.) collectés ci-dessus → barres de vie en direct.
    patch = _patch_pj(nom_personnage, idx, {})
    for p in patches_cibles:
        for k, v in p.items():
            if k != "pj_updated":
                patch[k] = v
    return ToolResult(
        text="\n".join(lignes),
        state_patch=patch,
    )


@tool
async def preparer_sorts(
    ctx: ToolContext,
    nom_personnage: str,
    preparations_json: str,
) -> ToolResult:
    """
    Mémorise les sorts du jour d'un lanceur PRÉPARÉ (Magicien, Clerc, Druide,
    Paladin, Rodeur) après un repos : remplace la préparation actuelle.
    Chaque sort doit appartenir à la liste de classe et être castable ;
    le nombre de préparations par niveau ne peut pas dépasser les
    emplacements quotidiens. Les Sorciers/Barde (spontanés) n'ont RIEN à
    préparer : leurs sorts connus sont toujours disponibles.

    :param nom_personnage (str): nom du lanceur.
    :param preparations_json (str): JSON dict {nom du sort: nb de fois}
        (ex. '{"Projectiles magiques": 2, "Armure du mage": 1}'). Une LISTE
        de noms (ex. '["Sommeil", "Armure du mage"]') est aussi acceptée
        (chaque sort compte pour 1) — le petit modèle envoyait une liste
        native et la préparation était perdue (audit gruumsh4pj P1/P19).
    """
    from .. import sorts as cat
    from .fiches import _save_fiche, _sync_pj, _patch_pj
    import json

    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")
    classe = str(fiche.get("classe") or "")
    if classe not in cat.PREPARE:
        return ToolResult(
            text=(f"⛔ {nom_personnage} ({classe}) est un lanceur "
                  f"{cat.type_lancement(classe)} : pas de préparation nécessaire.")
        )
    _brut = preparations_json
    if isinstance(_brut, str):
        _txt = (_brut or "").strip()
        try:
            _brut = json.loads(_txt or "{}")
        except (ValueError, TypeError):
            # 🛡️ gruumsh4pj (P19) : le modèle (et certains relais) émet la
            # REPRÉSENTATION python de la liste (guillemets simples) :
            # « ['Sommeil', 'Armure du mage'] ». On tolère via literal_eval.
            try:
                import ast as _ast
                _brut = _ast.literal_eval(_txt)
            except (ValueError, SyntaxError):
                # 🛡️ invoke_tool aplatit une liste native en chaîne « à
                # plat » (« Sommeil, Armure du mage ») — traitée comme une
                # liste de noms (chaque sort compte pour 1).
                _noms_plats = [
                    s.strip().strip("'\"")
                    for s in _txt.replace("[", "").replace("]", "")
                    .replace("\n", ",").split(",")
                    if s.strip().strip("'\"")
                ]
                _brut = _noms_plats if _noms_plats else None
    if _brut is None:
        return ToolResult(text=(
            "❌ preparations_json invalide : JSON attendu. Deux formats "
            "acceptés — dict {\"Sort\": nb} (ex. '{\"Sommeil\": 1, "
            "\"Armure du mage\": 1}') ou liste [\"Sort\", …] (chaque sort "
            "compte pour 1)."
        ))
    if isinstance(_brut, list):
        _brut = {str(n): 1 for n in _brut if str(n or "").strip()}
    if not isinstance(_brut, dict):
        return ToolResult(text=(
            "❌ preparations_json invalide : JSON attendu. Deux formats "
            "acceptés — dict {\"Sort\": nb} (ex. '{\"Sommeil\": 1, "
            "\"Armure du mage\": 1}') ou liste [\"Sort\", …] (chaque sort "
            "compte pour 1)."
        ))
    preps = _brut

    niveau = max(1, int(fiche.get("niveau") or 1))
    carac_cle = cat.carac_incantation(classe)
    val = int((fiche.get("carac") or {}).get(carac_cle, 10) or 10)
    mod = (val - 10) // 2
    slots = cat.emplacements(classe, niveau, mod)

    par_niveau: dict[int, int] = {}
    for nom, nb in preps.items():
        s = cat.sort_par_nom(str(nom), classe)
        if s is None:
            return ToolResult(text=f"❌ Sort inconnu : « {nom} ».")
        if classe not in s["classes"]:
            return ToolResult(text=f"⛔ {classe} ne connaît pas « {nom} ».")
        if s["niveau"] > cat.niveau_sort_max(classe, niveau):
            return ToolResult(
                text=(f"⛔ « {nom} » (niv. {s['niveau']}) est trop puissant pour "
                      f"{classe} niv.{niveau}.")
            )
        nb = max(1, int(nb or 1))
        par_niveau[s["niveau"]] = par_niveau.get(s["niveau"], 0) + nb
    for lvl, nb in sorted(par_niveau.items()):
        if nb > slots.get(lvl, 0):
            return ToolResult(
                text=(f"⛔ Trop de préparations de niveau {lvl} : {nb} pour "
                      f"{slots.get(lvl, 0)} emplacements.")
            )

    etat_sorts = cat.sorts_de_fiche(fiche)
    # 🧹 gruumsh4pj (P26b) : purger les compteurs à 0/negatif — un « 0 »
    # residuel bloquait la lecture et polluait l'etat (l'entree disparait
    # quand il ne reste plus rien a lancer).
    for _s in [k for k, v in (etat_sorts["prepares"] or {}).items()
               if int(v or 0) <= 0]:
        etat_sorts["prepares"].pop(_s, None)
    etat_sorts["prepares"] = {
        str(n): max(1, int(v or 1)) for n, v in preps.items()
        if int(v or 0) > 0
    }
    etat_sorts["depenses"] = {}  # nouvelle journée d'incantation
    fiche["sorts"] = etat_sorts
    _save_fiche(ctx, nom_personnage, fiche)
    idx = _sync_pj(ctx, nom_personnage, {})
    return ToolResult(
        text=(
            f"📖 Sorts préparés par **{nom_personnage}** : " + ", ".join(
                f"{n} x{v}" for n, v in sorted(etat_sorts['prepares'].items()))
            + f". Emplacements du jour ({', '.join(f'n.{k}: {v}' for k, v in slots.items())})."
            + " ⚠️ La préparation est terminée : n'appelle PAS `repos_long`"
              " pour elle — un repos n'est qu'une vraie nuit de sommeil jouée"
              " (lieu sûr, veilles)."
        ),
        state_patch=_patch_pj(nom_personnage, idx, {}),
    )


@tool
async def repos_long(
    ctx: ToolContext, nom_personnage: str = "", forcer: bool = False
) -> ToolResult:
    """
    Applique un repos long de 8 heures : restaure TOUS les emplacements de
    sorts (dépenses du jour remises à zéro — les préparateurs doivent
    re-mémoriser via preparer_sorts) et récupération naturelle de 1 PV par
    niveau. Sans argument, applique à TOUS les PJ de la partie.

    UN SEUL repos long par période de repos : si le groupe vient à peine de
    se reposer, l'outil REFUSE (un perso ne récupère pas deux nuits de suite
    sans que le temps s'écoule) — fais alors avancer le temps (voyage,
    veille narrative) ou utilise fiche_perso_soigner/une potion.

    :param nom_personnage (str): nom du PJ (vide = toute l'équipe).
    :param forcer (bool): True pour passer outre la garde anti-répétition
        (à réserver aux cas où le temps passe réellement : plusieurs jours
        de voyage, week-end de repos à l'auberge…).
    """
    from .. import sorts as cat
    from .fiches import _load_fiche, _save_fiche, _sync_pj, _patch_pj
    from ..game.state import PartyState
    from .marche import consommer_marqueur_auberge  # noqa: PLC0415
    from datetime import datetime as _dt

    # ── Garde anti-repos-spam ────────────────────────────────────────────
    # Observé en partie réelle (abd81275) : 4 repos longs forcés en 4
    # minutes — le MJ utilisait le repos comme bouton de soin après CHAQUE
    # action, brisant le rythme (4×8 h de sommeil devant la grotte). Un
    # second repos à moins de 20 min de réel d'intervalle est refusé : le
    # temps de jeu ne s'écoule pas si vite sans narration de voyage.
    state_repos = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat_repos = state_repos.load()
    # 🛡️ gruumsh4pj (P2) : AUCUN repos long en combat. Observé deux fois
    # dans la même partie : « 8 h de sommeil » narrées face au boss à 10 PV
    # (PV + slots remis à zéro au mépris total du tour par tour). `forcer`
    # ne passe PAS cette garde : un combat n'est jamais l'heure de dormir.
    if str((etat_repos or {}).get("phase") or "") == "combat":
        return ToolResult(text=(
            "⛔ **Repos refusé : le groupe est EN COMBAT** — des ennemis "
            "sont debout et la rotation est active. Le repos long n'est "
            "possible qu'une la menace réglée (clôture du combat) : termine "
            "le combat (attaques, `finir_combat`), soigne via "
            "`fiche_perso_soigner`/potion si besoin."
        ))
    dernier = str((etat_repos.get("repos_dernier") or ""))[:19]
    if dernier and not bool(forcer):
        try:
            delta_min = (
                _dt.now() - _dt.fromisoformat(dernier)
            ).total_seconds() / 60.0
        except ValueError:
            delta_min = 1e9
        if delta_min < 20.0:
            return ToolResult(text=(
                f"⛔ **Repos refusé** : le groupe vient à peine de se reposer "
                f"(il y a {int(delta_min)} min). Un repos long de 8 h ne peut "
                "pas recommencer immédiatement : ce n'est pas un soin rapide. "
                "Soigne plutôt via `fiche_perso_soigner` ou une potion ; pour "
                "un VRAI nouveau repos, fais d'abord avancer le temps (voyage "
                "de plusieurs heures/jours, veille de garde…), puis relance "
                "`repos_long(forcer=true)`."
            ))

    cibles: list[str] = []
    if nom_personnage.strip():
        f = _fiche_pj(ctx, nom_personnage)
        if f is None:
            return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")
        cibles.append(str(f.get("nom") or nom_personnage))
    else:
        try:
            etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
            cibles = [str(p.get("nom") or "") for p in (etat.get("pj") or []) if p.get("nom")]
        except Exception:                                # noqa: BLE001
            return ToolResult(text="❌ Partie introuvable.")

    lignes: list[str] = ["🛏️ **Repos long (8 h)**"]
    patches: dict[str, Any] = {}
    for nom in cibles:
        f = _fiche_pj(ctx, nom)
        if f is None:
            continue
        niveau = max(1, int(f.get("niveau") or 1))
        classe = str(f.get("classe") or "")
        # 0) Auberge (règle-maison du projet) : une nuitée de « bonne
        #    qualité » au lieu du repos → le repos soigne +ceil(niveau/2) PV
        #    et retire « fatigue »/« épuisé ». Le marqueur est CONSOMMÉ ici
        #    (une nuitée n'opère qu'AU PROCHAIN repos long, pas en boucle).
        bonus_auberge, _ville_nuit = consommer_marqueur_auberge(f)
        conds = [c for c in (f.get("conditions") or [])]
        if bonus_auberge:
            conds = [c for c in conds
                     if str(c).lower() not in ("fatigue", "épuisé")]
        # 1) Emplacements de sorts restaurés.
        if cat.est_lanceur(classe):
            s = cat.sorts_de_fiche(f)
            if s["depenses"]:
                s["depenses"] = {}
                f["sorts"] = s
        # 2) Récupération naturelle : +1 PV/niveau (plafonné à pv_max),
        #    majoré du bonus d'auberge « bonne qualité » le cas échéant.
        try:
            pv_max = int(f.get("pv_max") or 0)
            pv = int(f.get("pv") or 0)
        except (TypeError, ValueError):
            pv, pv_max = 0, 0
        soin = niveau + bonus_auberge
        old_pv = int(f.get("pv") or 0)
        if 0 <= pv < pv_max:
            f["pv"] = min(pv_max, pv + soin)
        f["conditions"] = conds
        _save_fiche(ctx, nom, f)
        detail = f"**{nom}** — PV {old_pv} → {f['pv']}/{pv_max}"
        if bonus_auberge:
            detail += (
                f" · +{bonus_auberge} PV grâce à la nuitée d'auberge "
                "« bonne » (fatigue/épuisé retirés)")
        if cat.est_lanceur(classe):
            detail += " — emplacements de sorts restaurés" + (
                " (re-mémorise via preparer_sorts)"
                if cat.type_lancement(classe) == "préparé" else "")
        lignes.append(f"- {detail}")
        idx = _sync_pj(ctx, nom, {"pv": f["pv"], "conditions": conds})
        if idx is not None:
            patches[f"pj.{idx}.pv"] = f["pv"]
            patches[f"pj.{idx}.conditions"] = conds
    patches["pj_updated"] = ", ".join(cibles)
    # Horodate le dernier repos réussi (garde anti-repos-spam).
    try:
        etat_r = state_repos.load()
        etat_r["repos_dernier"] = _dt.now().isoformat()
        state_repos.save(etat_r)
        patches["repos_dernier"] = etat_r["repos_dernier"]
    except Exception:                                        # noqa: BLE001
        pass
    return ToolResult(text="\n".join(lignes), state_patch=patches)
