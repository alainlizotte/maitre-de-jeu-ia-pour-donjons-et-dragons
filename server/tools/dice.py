"""Outil Jets de dés — adapté de `Outil_JetsDes.py`.

Différences vs la version OpenWebUI :
- Plus de classe `Tools` ni de `pydantic.Valves`. La graine (seed) vient du
  contexte de la partie si besoin (extension future).
- Méthodes `def (...) -> str` devenues `@tool` async renvoyant ToolResult.
- Pas d'`__event_emitter__` : les events sont portés par ToolResult.events.
"""

from __future__ import annotations

import random
from typing import Optional

from .base import ToolContext, ToolResult, tool


def _mod(c: int) -> int:
    """Modificateur D&D 3.5 d'une caractéristique (formule officielle (c-10)//2)."""
    return (c - 10) // 2


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
@tool
async def lancer_d20(
    ctx: ToolContext,
    modificateur: int,
    raison: str,
    difficulte: Optional[int] = None,
) -> ToolResult:
    """
    Lance un d20 et ajoute un modificateur. Renvoie formule, jet brut, total et
    réussite/échec (si `difficulte` fournie). À utiliser pour tout jet de
    résolution D&D 3.5 (compétence, carac, sauvegarde spéciale).

    :param modificateur (int): modificateur applicable (carac + rangs + divers).
    :param raison (str): brève description du jet (ex. "Escalade d'un mur de 6 m").
    :param difficulte (int): DD à atteindre pour réussir. Optionnel.
    """
    jet = random.randint(1, 20)
    total = jet + modificateur
    critique = jet == 20
    fumble = jet == 1
    lignes = [
        f"🎯 **Jet de d20** — {raison}",
        f"- Jet brut : {jet}",
        f"- Modificateur : {modificateur:+d}",
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
    bonus_attaque: int,
    ca_cible: int,
    nom_attaquant: str,
    arme: str,
    nom_cible: str,
) -> ToolResult:
    """
    Effectue un jet d'attaque D&D 3.5 contre une Classe d'Armure (CA) cible.
    Renvoie jet brut, total, et résultat (toucher / critique / maladresse / raté).
    Gère le 20 naturel (critique à confirmer) et le 1 naturel (maladresse).

    :param bonus_attaque (int): bonus total (BBA + carac + magie + divers).
    :param ca_cible (int): Classe d'Armure de la cible.
    :param nom_attaquant (str): nom du personnage qui attaque.
    :param arme (str): nom de l'arme utilisée.
    :param nom_cible (str): nom de la cible.
    """
    jet = random.randint(1, 20)
    total = jet + bonus_attaque
    lignes = [
        f"⚔️ **Attaque** : {nom_attaquant} [{arme}] vs {nom_cible} (CA {ca_cible})",
        f"- Jet brut d'attaque : {jet}",
        f"- Bonus total : {bonus_attaque:+d}",
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
    if nb_des < 1:
        return ToolResult(text="⚠️ nb_des doit être ≥ 1.")
    if faces not in (2, 3, 4, 6, 8, 10, 12, 20, 100):
        return ToolResult(text=f"⚠️ Type de dé {faces} non standard en D&D 3.5.")
    jets = [random.randint(1, faces) for _ in range(nb_des)]
    total = sum(jets) + bonus
    lignes = [
        f"💥 **Dégâts** : {arme_ou_sort} → {cible}",
        f"- Formule : {nb_des}d{faces}{'+' if bonus >= 0 else ''}{bonus}",
        f"- Jets bruts : {jets}",
        f"- Total jets : {sum(jets)}",
        f"- Bonus dégâts : {bonus:+d}",
        f"- **Dégâts infligés : {total}**",
    ]
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
    :param difficulte (int): DD à atteindre (souvent 10 + ½ niveau + mod carac).
    :param nom_personnage (str): nom du personnage qui sauvegarde.
    :param source (str): source du danger (sort, piège, poison…).
    """
    t = type_sauvegarde.lower().strip()
    if t not in ("vigueur", "réflexes", "reflexes", "volonté", "volonte"):
        return ToolResult(
            text=(
                f"⚠️ type_sauvegarde invalide : '{type_sauvegarde}'. "
                "Attendu : Vigueur, Réflexes ou Volonté."
            )
        )
    aliases = {"reflexes": "Réflexes", "volonte": "Volonté"}
    label = aliases.get(t, t.capitalize())
    jet = random.randint(1, 20)
    total = jet + modificateur
    ok = (total >= difficulte or jet == 20) and jet != 1
    lignes = [
        f"🛡️ **Sauvegarde** ({label}) — {nom_personnage} vs {source} (DD {difficulte})",
        f"- Jet brut : {jet}",
        f"- Modificateur : {modificateur:+d}",
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
