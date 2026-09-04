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


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def incanter_sort(
    ctx: ToolContext,
    nom_personnage: str,
    nom_sort: str,
    cible: str = "",
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
    """
    from .. import sorts as cat
    from .fiches import _save_fiche, _sync_pj, _patch_pj

    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom_personnage}'.")

    classe = str(fiche.get("classe") or "")
    niveau = max(1, int(fiche.get("niveau") or 1))
    sort = cat.sort_par_nom(nom_sort)
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
    spontane = cat.type_lancement(classe) == "spontané"
    if spontane:
        if not any(_norm(s) == _norm(sort["nom"]) for s in etat_sorts["connus"]):
            return ToolResult(
                text=(f"⛔ **{sort['nom']}** n'est pas dans les sorts connus de "
                      f"{nom_personnage} (spontané : liste fixe choisie à la création).")
            )
    else:
        restants_prep = int(etat_sorts["prepares"].get(sort["nom"], 0))
        if restants_prep <= 0:
            return ToolResult(
                text=(f"⛔ **{sort['nom']}** n'a pas été préparé aujourd'hui par "
                      f"{nom_personnage} — utilise preparer_sorts (repos/méditation).")
            )

    # 4) Emplacement disponible ----------------------------------------------
    lvl = sort["niveau"]
    depense = int(etat_sorts["depenses"].get(str(lvl), 0))
    total = slots.get(lvl, 0)
    if depense >= total:
        return ToolResult(
            text=(f"⛔ Plus aucun emplacement de niveau {lvl} pour "
                  f"{nom_personnage} ({depense}/{total} utilisés). "
                  f"Un repos long les restaure.")
        )

    # 5) Consommation ----------------------------------------------------------
    etat_sorts["depenses"][str(lvl)] = depense + 1
    if not spontane:
        etat_sorts["prepares"][sort["nom"]] = restants_prep - 1
    fiche["sorts"] = etat_sorts
    _save_fiche(ctx, nom_personnage, fiche)

    nls_lanceur = niveau  # pour les effets scalés
    lignes = [
        f"✨ **{sort['nom']}** (niv. {lvl}) lancé par {nom_personnage}"
        + (f" → **{cible}**" if cible else ""),
        f"- Emplacements niv.{lvl} : {depense + 1}/{total}",
        f"- Temps d'incantation : {sort['incantation']} — Portée : "
        f"{sort['portee']} — Composantes : {sort['composantes']} — "
        f"Durée : {sort['duree']}",
    ]

    # 6) Effet mécanique -------------------------------------------------------
    effet = sort.get("effet") or {}
    type_effet = effet.get("type")
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
            tr_deg = await fiche_perso_infliger_degats(ctx, cible, total_deg)
            lignes.append(f"- {tr_deg.text}")
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
            tr_soin = await fiche_perso_soigner(ctx, cible, total_soin)
            lignes.append(f"- {tr_soin.text}")
            if tr_soin.state_patch:
                patches_cibles.append(tr_soin.state_patch)
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
        (ex. '{"Projectiles magiques": 2, "Armure du mage": 1}').
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
    try:
        preps = json.loads(preparations_json or "{}")
        if not isinstance(preps, dict):
            raise ValueError
    except (ValueError, TypeError):
        return ToolResult(text="❌ preparations_json invalide : JSON dict attendu.")

    niveau = max(1, int(fiche.get("niveau") or 1))
    carac_cle = cat.carac_incantation(classe)
    val = int((fiche.get("carac") or {}).get(carac_cle, 10) or 10)
    mod = (val - 10) // 2
    slots = cat.emplacements(classe, niveau, mod)

    par_niveau: dict[int, int] = {}
    for nom, nb in preps.items():
        s = cat.sort_par_nom(str(nom))
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
    etat_sorts["prepares"] = {str(n): max(1, int(v or 1)) for n, v in preps.items()}
    etat_sorts["depenses"] = {}  # nouvelle journée d'incantation
    fiche["sorts"] = etat_sorts
    _save_fiche(ctx, nom_personnage, fiche)
    idx = _sync_pj(ctx, nom_personnage, {})
    return ToolResult(
        text=(
            f"📖 Sorts préparés par **{nom_personnage}** : " + ", ".join(
                f"{n} x{v}" for n, v in sorted(etat_sorts['prepares'].items()))
            + f". Emplacements restaurés ({', '.join(f'n.{k}: {v}' for k, v in slots.items())})."
        ),
        state_patch=_patch_pj(nom_personnage, idx, {}),
    )


@tool
async def repos_long(ctx: ToolContext, nom_personnage: str = "") -> ToolResult:
    """
    Applique un repos long de 8 heures : restaure TOUS les emplacements de
    sorts (dépenses du jour remises à zéro — les préparateurs doivent
    re-mémoriser via preparer_sorts) et récupération naturelle de 1 PV par
    niveau. Sans argument, applique à TOUS les PJ de la partie.

    :param nom_personnage (str): nom du PJ (vide = toute l'équipe).
    """
    from .. import sorts as cat
    from .fiches import _load_fiche, _save_fiche, _sync_pj, _patch_pj
    from ..game.state import PartyState

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
        # 1) Emplacements de sorts restaurés.
        if cat.est_lanceur(classe):
            s = cat.sorts_de_fiche(f)
            if s["depenses"]:
                s["depenses"] = {}
                f["sorts"] = s
        # 2) Récupération naturelle : +1 PV/niveau (plafonné à pv_max).
        try:
            pv_max = int(f.get("pv_max") or 0)
            pv = int(f.get("pv") or 0)
        except (TypeError, ValueError):
            pv, pv_max = 0, 0
        if 0 <= pv < pv_max:
            f["pv"] = min(pv_max, pv + niveau)
        _save_fiche(ctx, nom, f)
        detail = f"**{nom}** — PV {f['pv']}/{pv_max}"
        if cat.est_lanceur(classe):
            detail += " — emplacements de sorts restaurés" + (
                " (re-mémorise via preparer_sorts)"
                if cat.type_lancement(classe) == "préparé" else "")
        lignes.append(f"- {detail}")
        idx = _sync_pj(ctx, nom, {"pv": f["pv"]})
        if idx is not None:
            patches[f"pj.{idx}.pv"] = f["pv"]
    patches["pj_updated"] = ", ".join(cibles)
    return ToolResult(text="\n".join(lignes), state_patch=patches)
