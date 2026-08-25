"""Outil Mémoire de partie — adapté de `Outil_EtatPartie_MEMOIRE.py`.

Délègue toute la persistance à `game.state.PartyState`, qui garantit des
écritures atomiques et préserve la date de création. Les fonctions `demarrer_combat`,
`tour_suivant_combat`, `finir_combat` appliquent les transitions de combat
directement dans l'état.
"""

from __future__ import annotations

import json
from typing import Optional

from ..game.state import PartyState
from .base import ToolContext, ToolResult, tool


def _party(ctx: ToolContext) -> PartyState:
    return PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)


@tool
async def etat_partie_get(ctx: ToolContext) -> ToolResult:
    """
    Charge et renvoie l'état complet actuel de la partie (mémoire persistante).
    À appeler en début de réponse MJ pour rester cohérent sur une partie longue.

    NB : dans cette app, le récapitulatif d'état est déjà injecté
    automatiquement en tête de chaque appel au LLM par le `prompt_builder`.
    Ce tool est utile seulement pour consulter un champ précis à la demande.
    """
    etat = _party(ctx).load()
    return ToolResult(
        text="=== État actuel de la partie ===\n"
        + json.dumps(etat, ensure_ascii=False, indent=2),
        # Le LLM peut appeler ce tool : on expose l'état pour synchro UI.
        state_patch=None,
    )


@tool
async def etat_partie_save(ctx: ToolContext, nouveau_etat: str) -> ToolResult:
    """
    Remplace intégralement l'état de la partie par `nouveau_etat` (JSON string).
    À utiliser pour une mise à jour complète (ex. fin de scène, sauvegarde de
    contrôle). Préserve la date de création.

    :param nouveau_etat (str): chaîne JSON complète conforme au schéma.
    """
    ok, msg = _party(ctx).replace_all(nouveau_etat)
    return ToolResult(text=msg)


@tool
async def etat_partie_patch(ctx: ToolContext, chemin: str, valeur: str) -> ToolResult:
    """
    Applique une mise à jour ciblée de l'état de la partie via notation par
    points (ex. "tour=4", "phase=combat", "courant_tour_pour=Groth",
    "lieu.nom=Auberge du Drakkar", "pj.0.pv=12"). La `valeur` est interprétée
    comme JSON si possible (entiers, booléens, listes, dicts), sinon texte.

    :param chemin (str): chemin de la propriété (notation point).
    :param valeur (str): nouvelle valeur (JSON ou chaîne).
    """
    ok, msg = _party(ctx).patch(chemin, valeur)
    state_patch = None
    if ok:
        # Si on a patché un chemin top-level connu du front, on synchronise UI.
        top = chemin.split(".")[0]
        if top in ("phase", "tour", "courant_tour_pour", "initiative", "lieu", "quete"):
            try:
                import json as _json
                # Récupère la valeur exacte patchée pour l'exposer au front.
                v = _json.loads(valeur) if False else valeur
                # Pour les int/bool on les parse proprement :
                try:
                    parsed = _json.loads(valeur)
                except json.JSONDecodeError:
                    parsed = valeur
                state_patch = {top: parsed}
            except Exception:
                state_patch = None
    return ToolResult(text=msg, state_patch=state_patch)


@tool
async def ajouter_evenement_histoire(
    ctx: ToolContext, evenement: str, tour: Optional[str] = None
) -> ToolResult:
    """
    Ajoute un événement au journal d'histoire de la partie (mémoire longue).
    Utile pour conserver une trace narrative condensée des moments clés.

    :param evenement (str): description courte de l'événement.
    :param tour (str): référence au tour/à la scène (ex. "Tour 3"). Optionnel.
    """
    return ToolResult(text=_party(ctx).add_event(evenement, tour or ""))


@tool
async def set_derniere_narration(ctx: ToolContext, narration: str) -> ToolResult:
    """
    Mémorise le dernier paragraphe de narration marquante (≤1500 carac). Sert
    de référence résumée pour la cohérence de la partie longue.

    :param narration (str): texte court résumant la dernière scène.
    """
    return ToolResult(text=_party(ctx).set_derniere_narration(narration))


def _noms_uniques(noms: list[str]) -> list[str]:
    """Désambiguïse les homonymes : « Naga mineur, Naga mineur » devient
    « Naga mineur », « Naga mineur (2) ». Le suivi de tour ET des PV repose
    sur le nom (courant_tour_pour, initiative, monstres_combat) : sans cela,
    la deuxième créature homonyme ne reçoit jamais de vrai tour (l'index
    retombe toujours sur la première occurrence)."""
    vus: dict[str, int] = {}
    out: list[str] = []
    for n in noms:
        cle = str(n).strip().lower()
        c = vus.get(cle, 0) + 1
        vus[cle] = c
        out.append(str(n) if c == 1 else f"{n} ({c})")
    return out


@tool
async def engager_combat(ctx: ToolContext, monstres: str) -> ToolResult:
    """
    Engage un combat contre un ou plusieurs monstres EN UN SEUL APPEL :
    résout chaque monstre du bestiaire, lance l'initiative officielle
    (1d20 + mod DEX pour les PJ, champ `init` du bestiaire pour les
    monstres), passe la partie en phase "combat" (tour 1) et désigne le
    premier actif. À utiliser dès qu'un monstre surgit ou attaque.

    :param monstres (str): noms de monstres séparés par des virgules
        (ex. "Squelette" ou "Gobelin, Gobelin").
    """
    import random

    from .monstres import _find_monstre

    noms = [n.strip() for n in monstres.split(",") if n.strip()]
    if not noms:
        return ToolResult(text="❌ Donne au moins un nom de monstre.")

    state = _party(ctx)
    etat = state.load()
    participants: list[dict] = []
    monstres_combat: list[dict] = []
    lignes: list[str] = ["🎲 **Initiative du combat**"]

    # Monstres (init officielle du bestiaire, mod 0 si inconnu).
    # Les homonymes sont d'abord désambiguïsés : le suivi de tour/PV est
    # basé sur le nom, deux créatures identiques doivent rester distinctes.
    labels_resolus = []
    for nom in noms:
        m0 = _find_monstre(ctx, nom)
        labels_resolus.append(str((m0 or {}).get("nom") or nom))
    labels_uniques = _noms_uniques(labels_resolus)

    for i, nom in enumerate(noms):
        m = _find_monstre(ctx, nom)
        label = labels_uniques[i]
        if m:
            try:
                mod = int(str(m.get("init", "0")).replace("+", "").strip())
            except ValueError:
                mod = 0
            try:
                pv_m = int(str(m.get("pv", "0")).strip().split("(")[0])
            except ValueError:
                pv_m = 0
            try:
                ca_m = int(str(m.get("ca", "0")).strip())
            except ValueError:
                ca_m = 0
            # Suivi mécanique des PV du monstre pendant le combat.
            monstres_combat.append({
                "nom": label, "pv": pv_m, "pv_max": pv_m, "ca": ca_m,
                "fp": str(m.get("fp", "?")), "conditions": [],
            })
        else:
            mod = 0
            # Stats inconnues : suivi conservatif (pas de clôture auto tant
            # que ce monstre n'a pas été consulté/détruit).
            monstres_combat.append({
                "nom": label, "pv": -1, "pv_max": -1, "ca": None,
                "fp": "?", "conditions": [], "inconnu": True,
            })
        jet = random.randint(1, 20)
        participants.append({"nom": label, "init": jet + mod,
                             "jet_brut": jet, "mod": mod})
        src = "" if m else " (stats inconnues : mod +0)"
        lignes.append(
            f"- **{label}** — initiative {jet + mod} "
            f"(d20={jet}, mod={mod:+d}){src}"
        )

    # PJ vivants (1d20 + mod DEX depuis la fiche)
    for p in etat.get("pj", []):
        conds = [str(c).lower() for c in (p.get("conditions") or [])]
        if p.get("pv", 0) <= -10 or "mort" in conds:
            continue
        # carac est normalement un dict FOR/DEX/… ; par prudence, accepte
        # aussi une chaîne JSON ou un texte « For 16, Dex 12 » (fiches
        # créées par d'anciennes versions de fiche_perso_creer_rapide).
        caracs = p.get("carac") or {}
        if isinstance(caracs, str):
            try:
                parse = json.loads(caracs)
                if isinstance(parse, dict):
                    caracs = parse
                else:
                    raise ValueError
            except ValueError:
                import re as _re
                m = _re.search(r"(?:DEX|Dex)\D{0,3}(\d{1,2})", caracs)
                dex = int(m.group(1)) if m else 10
                mod = (dex - 10) // 2
                jet = random.randint(1, 20)
                participants.append({"nom": p["nom"], "init": jet + mod,
                                     "jet_brut": jet, "mod": mod})
                lignes.append(
                    f"- **{p['nom']}** — initiative {jet + mod} "
                    f"(d20={jet}, mod DEX {mod:+d})"
                )
                continue
        dex = ((caracs.get("DEX") if isinstance(caracs, dict) else None) or 10)
        mod = (int(dex) - 10) // 2
        jet = random.randint(1, 20)
        participants.append({"nom": p["nom"], "init": jet + mod,
                             "jet_brut": jet, "mod": mod})
        lignes.append(
            f"- **{p['nom']}** — initiative {jet + mod} "
            f"(d20={jet}, mod DEX {mod:+d})"
        )

    participants.sort(key=lambda x: x["init"], reverse=True)

    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["initiative"] = participants
    etat["courant_tour_pour"] = participants[0]["nom"]
    etat["monstres_combat"] = monstres_combat
    err = state.save(etat)
    if err:
        return ToolResult(text=err)

    premier = participants[0]
    pj_map = {p["nom"]: p.get("joueur", "?") for p in etat.get("pj", [])}
    qui = f"{premier['nom']} (joueur : {pj_map.get(premier['nom'], 'PNJ/monstre')})"
    lignes += [
        "",
        f"⚔️ **Combat engagé ! Tour 1 — c'est au tour de {qui}.**",
        "Ordre : " + " → ".join(p["nom"] for p in participants),
        "_Au tour de chaque actif : lancer_attaque / lancer_degats / "
        "lancer_sauvegarde selon ses actions, puis tour_suivant_combat._",
    ]
    return ToolResult(
        text="\n".join(lignes),
        state_patch={
            "phase": "combat",
            "tour": 1,
            "initiative": participants,
            "courant_tour_pour": etat["courant_tour_pour"],
        },
    )


@tool
async def demarrer_combat(ctx: ToolContext, initiative_liste: str) -> ToolResult:
    """
    Démarre un combat dans l'état de la partie : passe phase→"combat", met
    tour=1, et enregistre l'ordre d'initiative (premier = courant).

    :param initiative_liste (str): chaîne JSON liste de dicts
        [{"nom":"Groth","init":14},…].
    """
    state = _party(ctx)
    try:
        data = json.loads(initiative_liste)
    except json.JSONDecodeError as e:
        return ToolResult(text=f"❌ JSON invalide : {e}")
    if not isinstance(data, list) or not data:
        return ToolResult(text="❌ Attendu une liste non vide.")

    # Homonymes désambiguïsés (même raison que dans engager_combat).
    noms_l = _noms_uniques([str(e.get("nom", "?")) for e in data])
    for e, n in zip(data, noms_l):
        e["nom"] = n

    etat = state.load()
    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["initiative"] = sorted(data, key=lambda x: x.get("init", 0), reverse=True)
    etat["courant_tour_pour"] = etat["initiative"][0].get("nom", "")
    err = state.save(etat)
    if err:
        return ToolResult(text=err)
    return ToolResult(
        text=(
            "⚔️ Combat démarré. Tour 1 — "
            f"**{etat['courant_tour_pour']}** agit en premier.\n"
            "Ordre : " + " → ".join(e.get("nom", "?") for e in etat["initiative"])
        ),
        state_patch={
            "phase": "combat",
            "tour": 1,
            "initiative": etat["initiative"],
            "courant_tour_pour": etat["courant_tour_pour"],
        },
    )


@tool
async def tour_suivant_combat(ctx: ToolContext) -> ToolResult:
    """
    Passe au tour suivant en combat : fait avancer `courant_tour_pour` dans
    l'ordre d'initiative, et incrémente `tour` si on boucle au début de
    l'ordre. Renvoie le nom du personnage dont c'est maintenant le tour.
    """
    state = _party(ctx)
    etat = state.load()
    if etat.get("phase") != "combat" or not etat.get("initiative"):
        return ToolResult(
            text="❌ Aucun combat en cours (utilisez demarrer_combat d'abord)."
        )
    ordre = etat["initiative"]
    courant = etat.get("courant_tour_pour")
    idx = next(
        (i for i, e in enumerate(ordre) if e.get("nom") == courant), -1
    )
    if idx == -1:
        idx = 0
        etat["tour"] = (etat.get("tour", 0) or 0) + 1
    else:
        idx += 1
        if idx >= len(ordre):
            idx = 0
            etat["tour"] = (etat.get("tour", 0) or 0) + 1
    etat["courant_tour_pour"] = ordre[idx].get("nom", "")
    err = state.save(etat)
    if err:
        return ToolResult(text=err)
    return ToolResult(
        text=(
            f"➡️ Tour {etat['tour']} — Initiative "
            f"{ordre[idx].get('init', '?')} — "
            f"C'est au tour de **{etat['courant_tour_pour']}**."
        ),
        state_patch={
            "tour": etat["tour"],
            "courant_tour_pour": etat["courant_tour_pour"],
        },
    )


@tool
async def finir_combat(ctx: ToolContext) -> ToolResult:
    """
    Termine le combat en cours : phase→"exploration", initiative vidée,
    courant_tour_pour=None, tour=0.
    """
    state = _party(ctx)
    etat = state.load()
    etat["phase"] = "exploration"
    etat["initiative"] = []
    etat["courant_tour_pour"] = None
    etat["tour"] = 0
    etat["monstres_combat"] = []
    err = state.save(etat)
    if err:
        return ToolResult(text=err)
    return ToolResult(
        text="🕊️ Combat terminé. Retour à l'exploration.",
        state_patch={
            "phase": "exploration",
            "tour": 0,
            "courant_tour_pour": None,
            "initiative": [],
            "monstres_combat": [],
        },
    )


@tool
async def reset_partie(ctx: ToolContext) -> ToolResult:
    """
    ⚠️ Réinitialise entièrement l'état de la partie (efface le fichier JSON).
    À utiliser seulement lors d'un nouveau départ confirmé par les joueurs.
    """
    return ToolResult(text=_party(ctx).reset(), state_patch={"__reset__": True})
