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
        },
    )


@tool
async def reset_partie(ctx: ToolContext) -> ToolResult:
    """
    ⚠️ Réinitialise entièrement l'état de la partie (efface le fichier JSON).
    À utiliser seulement lors d'un nouveau départ confirmé par les joueurs.
    """
    return ToolResult(text=_party(ctx).reset(), state_patch={"__reset__": True})
