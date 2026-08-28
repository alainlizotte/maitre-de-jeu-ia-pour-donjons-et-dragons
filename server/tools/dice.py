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
from typing import Optional

from .base import ToolContext, ToolResult, tool


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
    """CA canonique d'une cible : fiche du PJ si joueur, sinon bestiaire local.

    Renvoie `(ca, source)` — ca=None si la cible est inconnue des deux sources.
    Évite que le LLM invente une CA trop basse pour toucher facilement.
    """
    fiche = _fiche_pj(ctx, nom_cible)
    if fiche is not None and fiche.get("ca") is not None:
        try:
            return int(fiche["ca"]), f"fiche de {nom_cible}"
        except (TypeError, ValueError):
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
            from .fiches import _chemin  # pylint: disable=import-outside-toplevel
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
                if rangs > 0:
                    from ..catalogue import COMPETENCES  # pylint: disable=import-outside-toplevel
                    cara_cle = next(
                        (c["cara"] for c in COMPETENCES
                         if _norm(c["nom"]) == _norm(competence)), "DEX",
                    )
                    val = int((fiche.get("carac") or {}).get(cara_cle, 10) or 10)
                    calc = rangs + (val - 10) // 2
                    if calc != modificateur:
                        mod_final = calc
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{calc:+d} (fiche de {nom_personnage} : "
                            f"{competence} {rangs} rangs + "
                            f"{cara_cle} {val} ({(val - 10) // 2:+d}))."
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
    lignes.append("\nOrdre : " + " → ".join(e["nom"] for e in jets))
    lignes.append("\n_C'est à la plus haute initiative d'agir la première._")
    return ToolResult(
        text="\n".join(lignes),
        # Synchro UI : on expose l'ordre pour le panneau initiative du front.
        state_patch={"initiative": jets, "courant_tour_pour": jets[0]["nom"] if jets else None},
    )


@tool
async def lancer_attaque(
    ctx: ToolContext,
    bonus_attaque: Any = 0,
    ca_cible: Any = 10,
    nom_attaquant: str = "",
    arme: str = "",
    nom_cible: str = "",
) -> ToolResult:
    """
    Effectue un jet d'attaque D&D 3.5 contre une Classe d'Armure (CA) cible.
    Renvoie jet brut, total, et résultat (toucher / critique / maladresse / raté).
    Gère le 20 naturel (critique à confirmer) et le 1 naturel (maladresse).

    :param bonus_attaque (int): bonus total = BBA + mod. FOR (mêlée) ou
        mod. DEX (distance), lus sur la fiche du personnage — ne jamais
        inventer de bonus. Un recoupement automatique avec la fiche borne
        les valeurs manifestement erronées.
    :param ca_cible (int): Classe d'Armure de la cible. Recoupée
        automatiquement avec la fiche du PJ ou le bestiaire local — la valeur
        officielle prime toujours sur celle fournie.
    :param nom_attaquant (str): nom du personnage qui attaque.
    :param arme (str): nom de l'arme utilisée.
    :param nom_cible (str): nom de la cible.
    """
    # --- CA officielle de la cible ------------------------------------------
    # Un petit LLM « arrange » parfois la CA pour faire toucher. On impose la
    # valeur des données officielles quand la cible est connue.
    # Arguments numériques blindés (le LLM peut omettre ou envoyer "12").
    bonus_attaque = _as_int(bonus_attaque)
    ca_cible = _as_int(ca_cible, 10)

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
    # bonus_attaque = BBA + mod FOR (mêlée) / mod DEX (distance) + bonus
    # divers (arme magique, focus...). Un petit LLM invente parfois des bonus
    # absurdes (+8 au niveau 1) : on borne au bonus plausible lu sur la fiche,
    # avec une marge de +3 pour les bonus magiques temporaires.
    bonus_final = bonus_attaque
    note_bonus = ""
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
            a_distance = any(
                m in arme_l for m in
                ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard", "sarbacane")
            )
            cle_car = "DEX" if a_distance else "FOR"
            val_car = int(caracs.get(cle_car, 10) or 10)
            mod_car = (val_car - 10) // 2
            plausible = bab + mod_car + 3
            if bonus_attaque > plausible:
                bonus_final = plausible
                note_bonus = (
                    f"\n- ⚠️ Bonus ajusté {bonus_attaque:+d} → {bonus_final:+d} "
                    f"(fiche de {nom_attaquant} : BBA {bab:+d}, {cle_car} "
                    f"{val_car} ({mod_car:+d}) + marge +3 max pour bonus divers)."
                )
    except Exception:                                           # noqa: BLE001
        pass  # fiche absente (monstre ?) → on trust le bonus fourni

    jet = random.randint(1, 20)
    total = jet + bonus_final
    lignes = [
        f"⚔️ **Attaque** : {nom_attaquant} [{arme}] vs {nom_cible} (CA {ca_cible})",
        f"- Jet brut d'attaque : {jet}",
        f"- Bonus total : {bonus_final:+d}" + note_bonus + note_ca,
        f"- **Total attaque : {total}**",
    ]
    if jet == 20:
        lignes.append(
            "- ⭐ **20 naturel** → toucher automatique + menace de critique "
            "(effectuer un second jet d'attaque pour confirmer ; si réussi, "
            "dégâts doublés/triplés selon arme)."
        )
    elif jet == 1:
        lignes.append(
            "- ❌ **1 naturel** → maladresse : attaque ratée automatiquement "
            "(conséquences possibles : arme lâchée, etc.)."
        )
    else:
        ok = total >= ca_cible
        lignes.append(
            f"- CA {ca_cible} → " + ("✅ **Touché**." if ok else "❌ **Manqué**.")
        )
    return ToolResult(text="\n".join(lignes))


@tool
async def lancer_degats(
    ctx: ToolContext,
    nb_des: int,
    faces: int,
    bonus: int,
    arme_ou_sort: str,
    cible: str,
) -> ToolResult:
    """
    Effectue le jet de dégâts D&D 3.5 selon la formule NdF + bonus. Renvoie les
    jets bruts, le total, et un résumé lisible.

    :param nb_des (int): nombre de dés (ex. 1, 2, 3…).
    :param faces (int): type de dé (4, 6, 8, 10, 12, 20).
    :param bonus (int): bonus de dégâts (mod. FOR, magie, etc.). Peut être négatif.
    :param arme_ou_sort (str): nom de l'arme ou du sort.
    :param cible (str): nom de la cible.
    """
    nb_des = max(1, _as_int(nb_des, 1))
    faces = _as_int(faces, 6)
    bonus = _as_int(bonus)
    if faces not in (2, 3, 4, 6, 8, 10, 12, 20, 100):
        return ToolResult(text=f"⚠️ Type de dé {faces} non standard en D&D 3.5.")
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    total = max(0, sum(jets) + bonus)  # jamais de dégâts négatifs (min 0)
    lignes = [
        f"💥 **Dégâts** : {arme_ou_sort} → {cible}",
        f"- Formule : {nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}",
        f"- Jets bruts : {jets}",
        f"- Total jets : {sum(jets)}",
        f"- Bonus dégâts : {bonus:+d}",
        f"- **Dégâts infligés : {total}**",
    ]
    if sum(jets) + bonus < 0:
        lignes.append(
            "- ℹ️ Total négatif ramené à 0 (les dégâts ne soignent pas la cible)."
        )
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
    if t not in ("vigueur", "réflexes", "reflexes", "volonté", "volonte"):
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
    # on les utilise plutôt que le chiffre approximatif du LLM.
    mod_final = int(modificateur)
    note_mod = ""
    fiche = _fiche_pj(ctx, nom_personnage)
    if fiche is not None:
        sauv = fiche.get("sauvegardes") or {}
        for k, v in sauv.items():
            if str(k).strip().lower().rstrip("s") == cle.strip().lower().rstrip("s"):
                try:
                    off = int(v)
                    if off != mod_final:
                        note_mod = (
                            f"\n- ⚠️ Modificateur recalculé {modificateur:+d} → "
                            f"{off:+d} (fiche de {nom_personnage} : {label} {off:+d})."
                        )
                        mod_final = off
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
