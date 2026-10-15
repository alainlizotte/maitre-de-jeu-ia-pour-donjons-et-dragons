"""Tools Dangers d'environnement D&D 3.5 — chantier DRS (rapport §5).

Couvre le chapitre « Environnement » de la DRS :
  - `danger_chute`      : dégâts de chute (1d6/3 m, max 20d6), saut délibéré,
                          surface meuble, chute dans l'eau (DRS « Les chutes »)
  - `danger_noyade`     : souffle 2×CON, tests de CON DD 10+1, séquence
                          0 PV / -1 PV / mort (DRS « La noyade », « L'asphyxie »)
  - `danger_environnement` : chaleur, froid, fumée, acide — jets de Vigueur
                          appliqués au groupe entier par le serveur

Auto-enregistré par `registry.discover_tools` (import du package server.tools).
"""

from __future__ import annotations

import random
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


def _norm(s: Any) -> str:
    import unicodedata
    nf = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in nf if not unicodedata.combining(c))


def _fiche(ctx: ToolContext, nom: str) -> Optional[dict]:
    try:
        from .fiches import _load_fiche
        return _load_fiche(ctx, nom)
    except Exception:                                        # noqa: BLE001
        return None


@tool
async def danger_chute(
    ctx: ToolContext,
    nom_personnage: str,
    hauteur_m: float,
    saut_delibre: bool = False,
    surface_meuble: bool = False,
    dans_eau: bool = False,
) -> ToolResult:
    """
    Chute D&D 3.5 (DRS « Les chutes ») : 1d6 par 3 m (max 20d6), appliqués
    par le serveur. Réductions officielles : saut DÉLIBÉRÉ (les 1d6 premiers
    deviennent non-létaux), test de Saut DD 15 réussi (3 m annulés + 3 m
    suivants non-létaux), réception sur surface meuble/sable/boue (1d6
    non-létal — cumul). Chute dans l'eau (≥ 3 m de fond) : 6 premiers m
    sans dégâts, puis 1d3/3 m non-létaux puis 1d6/3 m létaux ; plongeon
    délibéré (Acrobaties/Natation DD 15) = 0 dégât.

    :param nom_personnage (str): nom du PJ (ou du monstre suivi) qui tombe.
    :param hauteur_m (float): hauteur de chute en mètres.
    :param saut_delibre (bool): True si le personnage SAUTE volontairement.
    :param surface_meuble (bool): True si la réception est sur sable/boue/neige.
    :param dans_eau (bool): True si la chute se termine DANS l'eau (≥ 3 m
        de profondeur).
    """
    from ..game.regles35 import degats_chute
    from .fiches import fiche_perso_infliger_degats

    h = float(hauteur_m or 0)
    if h <= 0:
        return ToolResult(text="❌ hauteur_m doit être > 0.")
    lignes = [f"🪂 **Chute** de {nom_personnage} — {h:g} m"]

    mod_saut = 0
    test_saut_reussi = False
    if saut_delibre and not dans_eau:
        # Test de Saut DD 15 (DRS) : annule les 3 premiers mètres et rend
        # non-létaux les 3 suivants.
        fiche = _fiche(ctx, nom_personnage)
        if fiche is not None:
            rangs = int((fiche.get("competences") or {}).get("Saut", 0) or 0)
            for_val = int((fiche.get("carac") or {}).get("FOR", 10) or 10)
            mod_saut = rangs + (for_val - 10) // 2
        jet_s = random.randint(1, 20)
        lignes.append(
            f"- 🤸 Saut délibéré — test de Saut : {jet_s}+{mod_saut} = "
            f"{jet_s + mod_saut} vs DD 15 → "
            + ("✅ réussi" if jet_s + mod_saut >= 15 or jet_s == 20
               else "❌ raté"))
        test_saut_reussi = jet_s == 20 or (jet_s + mod_saut >= 15
                                           and jet_s != 1)

    # Plongeon délibéré dans l'eau (Acrobaties/Natation DD 15) : 0 dégât.
    if dans_eau:
        fiche = _fiche(ctx, nom_personnage)
        mod_nat = 0
        if fiche is not None:
            rangs = max(
                int((fiche.get("competences") or {}).get("Natation", 0) or 0),
                int((fiche.get("competences") or {}).get("Acrobaties", 0) or 0))
            dex_val = int((fiche.get("carac") or {}).get("DEX", 10) or 10)
            mod_nat = rangs + (dex_val - 10) // 2
        jet_p = random.randint(1, 20)
        plonge = jet_p + mod_nat >= 15 and jet_p != 1
        lignes.append(
            f"- 🌊 Chute dans l'eau — plongeon délibéré (Acrobaties/Natation "
            f"DD 15) : {jet_p}+{mod_nat} = {jet_p + mod_nat} → "
            + ("✅ plongeon maîtrisé : 0 dégât" if plonge
               else "❌ réception brutale"))
        if plonge:
            return ToolResult(text="\n".join(lignes))
        # Table « chute dans l'eau » : 6 m gratuits, puis 1d3 non-létal/3 m
        # (6-12 m), puis 1d6 létaux/3 m au-delà.
        tranche = int(h // 3)
        if tranche <= 2:
            lignes.append(
                "  → chute ≤ 6 m dans l'eau : **aucun dégât** (eau ≥ 3 m "
                "de profondeur).")
            return ToolResult(text="\n".join(lignes))
        nl_tranches = min(2, tranche - 2)
        letales_tranches = max(0, tranche - 4)
        parts = []
        if nl_tranches:
            parts.append(f"{nl_tranches}d3 non-létaux")
        if letales_tranches:
            parts.append(f"{letales_tranches}d6 létaux")
        lignes.append("  → " + (" + ".join(parts) or "aucun dégât"))
        total_nl = sum(random.randint(1, 3) for _ in range(nl_tranches))
        total_l = sum(random.randint(1, 6) for _ in range(letales_tranches))
        lignes.append(f"  → non-létaux : **{total_nl}**, létaux : "
                      f"**{total_l}**")
        if total_l:
            tr_l = await fiche_perso_infliger_degats(
                ctx, nom_personnage, total_l, source="chute dans l'eau")
            lignes.append("- " + tr_l.text.splitlines()[0])
        if total_nl:
            tr_nl = await fiche_perso_infliger_degats(
                ctx, nom_personnage, total_nl, source="chute dans l'eau",
                non_letal=True)
            lignes.append("- " + tr_nl.text.splitlines()[0])
        return ToolResult(text="\n".join(lignes))

    formule, expl = degats_chute(
        h, saut_delibre=saut_delibre, test_saut_reussi=test_saut_reussi,
        surface_meuble=surface_meuble)
    if formule == "0":
        lignes.append(f"- ✅ {expl}")
        return ToolResult(text="\n".join(lignes))
    lignes.append(f"- ⚙️ Règle : {expl}")
    nb = int(formule.split("d")[0])
    deg_brut = sum(random.randint(1, 6) for _ in range(nb))
    # Réductions : les dés « non-létaux » sont reportés séparément.
    nl_des = 0
    if test_saut_reussi:
        nl_des = min(nb, 1)
    elif saut_delibre:
        nl_des = min(nb, 1)
    if surface_meuble:
        nl_des = min(nb, nl_des + 1)
    l_des = max(0, nb - nl_des)
    total_nl = sum(random.randint(1, 6) for _ in range(nl_des))
    total_l = sum(random.randint(1, 6) for _ in range(l_des))
    total_l = max(total_l, 1) if l_des else 0   # min 1 si des létaux
    lignes.append(
        f"- 🎲 Dégâts : {total_l} létaux"
        + (f" + {total_nl} non-létaux" if total_nl else "")
        + f" (formule {formule}).")
    if l_des:
        tr_l = await fiche_perso_infliger_degats(
            ctx, nom_personnage, total_l, source="chute")
        lignes.append("- " + tr_l.text.splitlines()[0])
    if total_nl:
        tr_nl = await fiche_perso_infliger_degats(
            ctx, nom_personnage, total_nl, source="chute", non_letal=True)
        lignes.append("- " + tr_nl.text.splitlines()[0])
    return ToolResult(text="\n".join(lignes))


@tool
async def danger_noyade(
    ctx: ToolContext, nom_personnage: str, sous_eau: bool = True,
) -> ToolResult:
    """
    Noyade / asphyxie D&D 3.5 (DRS « La noyade », « L'asphyxie ») : le
    personnage peut retenir son souffle 2 rounds par point de CON ; ensuite
    un test de CON par round (DD 10, +1 par round). Au PREMIER échec : 0 PV
    + Inconscient ; au deuxième : -1 PV Mourant ; au troisième : Mort.
    Résolu round par round par le serveur jusqu'à l'issue (récupération
    possible : sort de soin ≥ 1 PV pour ranimer).

    :param nom_personnage (str): nom du PJ qui manque d'air.
    :param sous_eau (bool): True = noyade (eau, sables mouvants) ;
        False = asphyxie lente (pièce close, gaz).
    """
    from .fiches import (
        fiche_perso_condition, fiche_perso_infliger_degats,
        _load_fiche, _save_fiche,
    )
    fiche = _fiche(ctx, nom_personnage)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche pour « {nom_personnage} ».")
    con = int((fiche.get("carac") or {}).get("CON", 10) or 10)
    mod_con = (con - 10) // 2
    souffle = max(1, con * 2)
    type_d = "noyade" if sous_eau else "asphyxie"
    lignes = [
        f"🌊 **{type_d.capitalize()}** — {nom_personnage} (CON {con})",
        f"- Souffle retenu : **{souffle} rounds** (2 × CON).",
    ]
    dd = 10
    issue = ""
    while True:
        jet = random.randint(1, 20)
        total = jet + mod_con
        if jet == 20 or (total >= dd and jet != 1):
            lignes.append(
                f"- Round (DD {dd}) : {jet}+{mod_con} = {total} → ✅ tient.")
        else:
            # Premier échec → séquence officielle.
            tr0 = await fiche_perso_condition(ctx, nom_personnage,
                                              "Inconscient", True)
            lignes.append(
                f"- ❌ ÉCHEC (DD {dd}) → **0 PV + Inconscient** :"
                " " + tr0.text.splitlines()[0])
            tr_p = await fiche_perso_infliger_degats(
                ctx, nom_personnage, int(fiche.get("pv", 1) or 1),
                source=type_d)
            lignes.append("  " + tr_p.text.splitlines()[0])
            # Round suivant : -1 PV, Mourant.
            tr_m = await fiche_perso_condition(ctx, nom_personnage,
                                               "Mourant", True)
            tr_d1 = await fiche_perso_infliger_degats(
                ctx, nom_personnage, 1, source=type_d)
            lignes.append(
                f"- Round suivant : **-1 PV, Mourant** — "
                + tr_d1.text.splitlines()[0])
            # Round encore suivant : mort (le PJ est à -1 : le tool Mort
            # exige -10 → on inflige des dégâts suffisants puis Mort).
            fiche2 = _fiche(ctx, nom_personnage)
            pv2 = int((fiche2 or {}).get("pv", -1) or -1)
            if pv2 > -10:
                tr_kill = await fiche_perso_infliger_degats(
                    ctx, nom_personnage, max(1, pv2 + 10),
                    source=type_d)
                lignes.append(
                    "- Round final : **MORT par "
                    f"{type_d}** — " + tr_kill.text.splitlines()[0])
            issue = "mort"
            break
        dd += 1
    lignes.append(
        "  ⚕️ Une intervention avant le round final (sort de soin, sortie "
        "de l'eau) évite la mort — le MJ peut interrompre la séquence à "
        "tout moment : soignez à ≥ 1 PV pour ranimer.")
    return ToolResult(text="\n".join(lignes))


@tool
async def danger_environnement(
    ctx: ToolContext, type_danger: str, heures: int = 8,
) -> ToolResult:
    """
    Danger d'environnement appliqué au GROUPE ENTIER par le serveur
    (DRS « La chaleur », « Le froid », « La fumée », « L'acide ») :
      - "chaleur"    : Vigueur/heure (DD 15, +1/précédent, armure −4,
                       Survie en bonus) — échec = 1d4 non-létaux + Fatigué
      - "froid"      : Vigueur/heure (DD 15, +1/précédent) — échec = 1d6
                       non-létaux + Fatigué
      - "froid_vif"  : froid ≤ -15 °C — 1 jet par 10 MIN (24 jets/8 h)
      - "fumee"      : Vigueur PAR ROUND (DD 15, +1/précédent) — 2 échecs
                       consécutifs = 1d6 non-létaux ; camouflage 20 %
      - "acide"      : 1d6 dégâts PAR ROUND d'exposition (vasques)
    Utiliser `voyage_meteo_jour` pour la météo de VOYAGE (table climat).

    :param type_danger (str): "chaleur" | "froid" | "froid_vif" | "fumee"
        | "acide".
    :param heures (int): durée d'exposition en heures (1-24 ; fumée/acide :
        nombre de ROUNDS).
    """
    from ..game.state import PartyState
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    t = _norm(type_danger).replace("é", "e")
    lignes: list[str] = [f"🌡️ **Danger d'environnement : {type_danger}**"]
    from .fiches import (
        fiche_perso_infliger_degats, fiche_perso_condition, _load_fiche,
        _save_fiche,
    )
    if t in ("fumee", "acide"):
        # Exposition par ROUND — le MJ appelle une fois par round exposé.
        for p in etat.get("pj") or []:
            nom = str(p.get("nom") or "")
            fiche = _load_fiche(ctx, nom)
            if fiche is None:
                continue
            conds = [str(c).lower() for c in (p.get("conditions") or [])]
            if "mort" in conds:
                continue
            if t == "acide":
                d6 = random.randint(1, 6)
                tr = await fiche_perso_infliger_degats(
                    ctx, nom, d6, source="acide")
                lignes.append(f"- 🧪 {nom} : {d6} dégâts d'acide — "
                              + tr.text.splitlines()[0])
                continue
            mod_v, det_v = _mod_vigueur(fiche)
            jet = random.randint(1, 20)
            total = jet + mod_v
            if jet == 20 or (total >= 15 and jet != 1):
                lignes.append(
                    f"- 😤 {nom} : Vigueur {jet}+{mod_v} = {total} vs DD 15 "
                    "→ ✅ respire (toussote).")
            else:
                d6 = random.randint(1, 6)
                tr = await fiche_perso_infliger_degats(
                    ctx, nom, d6, source="fumée", non_letal=True)
                tr_c = await fiche_perso_condition(ctx, nom, "Fatigué", True)
                lignes.append(
                    f"- 😮 {nom} : Vigueur {jet}+{mod_v} = {total} vs DD 15 "
                    f"→ ❌ tousse violemment : **{d6} non-létaux** + "
                    + tr_c.text.splitlines()[0])
        lignes.append(
            "  💨 Fumée : 20 % de chances de RATER toute attaque contre une "
            "cible cachée dedans (camouflage) — applique-le en narratif.")
        return ToolResult(text="\n".join(lignes))
    # chaleur / froid / froid_vif → moteur partagé avec le voyage.
    from .voyage import _jets_extremes  # lazy : évite les cycles
    if t == "chaleur":
        await _jets_extremes(ctx, lignes, "chaleur",
                             nb_jets=max(1, min(24, int(heures or 8))),
                             dd_base=15, deg_par_echec=4, etat=etat)
    else:
        await _jets_extremes(ctx, lignes, "froid",
                             nb_jets=max(1, min(24, int(heures or 8))),
                             dd_base=15, deg_par_echec=6, etat=etat)
    return ToolResult(text="\n".join(lignes))


def _mod_vigueur(fiche: dict) -> tuple[int, str]:
    """Duplicate léger du helper voyage (évite l'import inversé)."""
    try:
        total = int((fiche.get("sauvegardes") or {}).get("Vigueur", 0) or 0)
    except (TypeError, ValueError):
        total = 0
    detail = f"Vigueur {total:+d}"
    try:
        from .fiches import bonus_dons_effet, bonus_sauvegarde_magie
        d = bonus_dons_effet(fiche.get("dons"), "sauvegarde_vigueur")
        m = bonus_sauvegarde_magie(fiche)
        total += d + m
        if d:
            detail += f" +{d} (dons)"
        if m:
            detail += f" +{m} (Résistance)"
    except Exception:                                        # noqa: BLE001
        pass
    return total, detail
