"""Outils de progression — expérience, niveaux, energy drain (D&D 3.5).

La distribution d'XP à la fin d'un combat est AUTOMATIQUE (moteur serveur
`game.combat`). Ces tools servent aux cas que le moteur ne peut pas
déduire : drains d'énergie (spectres, morts-vivants), Restauration,
quêtes récompensées par le MJ, entraînements…
"""

from __future__ import annotations

from typing import Optional

from .base import ToolContext, ToolResult, tool
from ..game import xp as gxp


def _maj_etat_pj(ctx: ToolContext, nom: str, champs: dict) -> None:
    """Best effort : répercute des champs sur l'entrée PJ de l'état."""
    try:
        from ..game.state import PartyState
        st = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = st.load()
        if "_erreur" in etat:
            return
        for p in etat.get("pj") or []:
            if str(p.get("nom", "")).lower() == nom.strip().lower():
                p.update(champs)
                break
        st.save(etat)
    except Exception:                                        # noqa: BLE001
        pass


@tool
async def fiche_perso_gagner_xp(
    ctx: ToolContext, nom: str, montant: int
) -> ToolResult:
    """
    Ajoute (ou retire, si montant négatif) des points d'expérience à un
    personnage et applique automatiquement les montées de niveau officielles
    (jet de dé de vie + mod. CON par niveau gagné, rappel dons/caractéristiques).
    La fiche affiche alors « XP actuelles / XP requises pour le prochain
    niveau ».

    NB : l'XP des monstres vaincus est déjà distribuée automatiquement par
    le serveur à la fin de chaque combat — n'utilise ce tool QUE pour les
    récompenses de quête, de rôle ou de circonstances spéciales.

    :param nom (str): nom du personnage.
    :param montant (int): XP gagnés (peut être négatif pour une pénalité).
    """
    from .fiches import _load_fiche, _save_fiche
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    try:
        m = int(str(montant).strip())
    except (TypeError, ValueError):
        return ToolResult(text=f"❌ Montant invalide : {montant!r}.")
    logs = gxp.appliquer_gain(fiche, m)
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    _maj_etat_pj(ctx, fiche.get("nom", nom), {
        "niveau": fiche.get("niveau"), "xp": fiche.get("xp", 0),
        "pv": fiche.get("pv"), "pv_max": fiche.get("pv_max"),
    })
    return ToolResult(
        text="\n".join(logs) or f"ℹ️ Aucun changement ({m:+d} XP).",
        state_patch={"pj_updated": fiche.get("nom", nom)},
    )


@tool
async def fiche_perso_niveau_negatif(
    ctx: ToolContext, nom: str, nb: int = 1
) -> ToolResult:
    """
    Inflige des niveaux négatifs (drain d'énergie des morts-vivants,
    spectres…) : -1 aux jets d'attaque/sauvegardes/caractéristiques et -5 PV
    par niveau négatif. Chaque jour, le personnage a droit à une sauvegarde
    de Vigueur (DD 10 + ½ FP du drain) pour les éliminer ; à défaut
    (ou sans Restauration), chaque niveau négatif devient une perte de
    niveau définitive (fiche_perso_perte_niveau). Si les niveaux négatifs
    atteignent le niveau réel du personnage, il meurt.

    :param nom (str): nom du personnage.
    :param nb (int): nombre de niveaux négatifs infligés (défaut 1).
    """
    from .fiches import _load_fiche, _save_fiche
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    try:
        n = max(1, int(str(nb).strip()))
    except (TypeError, ValueError):
        n = 1
    neg = int(fiche.get("niveaux_negatifs", 0) or 0) + n
    fiche["niveaux_negatifs"] = neg
    niveau = int(fiche.get("niveau", 1) or 1)
    texte = (
        f"🧟 {fiche.get('nom', nom)} subit {n} niveau(x) négatif(s) — total "
        f"{neg} (niveau effectif {gxp.niveau_effectif(fiche)})."
    )
    if gxp.niveau_effectif(fiche) <= 0:
        conds = fiche.setdefault("conditions", [])
        if "Mort" not in conds:
            conds.append("Mort")
        texte += (
            "\n💀 **MORT** : les niveaux négatifs égalent son niveau réel "
            "(DMG 3.5, energy drain)."
        )
        _maj_etat_pj(ctx, fiche.get("nom", nom), {"conditions": conds})
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    texte += (
        "\n_Sauvegarde quotidienne de Vigueur (DD 10 + ½ FP du drain) pour "
        "résister, sinon utiliser fiche_perso_perte_niveau pour la perte "
        "définitive, ou fiche_perso_retirer_niveau_negatif après une "
        "Restauration._"
    )
    return ToolResult(
        text=texte, state_patch={"pj_updated": fiche.get("nom", nom)}
    )


@tool
async def fiche_perso_retirer_niveau_negatif(
    ctx: ToolContext, nom: str, nb: int = 1
) -> ToolResult:
    """
    Retire des niveaux négatifs (sauvegarde quotidienne réussie ou sort
    Restauration).

    :param nom (str): nom du personnage.
    :param nb (int): nombre de niveaux négatifs retirés (défaut 1).
    """
    from .fiches import _load_fiche, _save_fiche
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    try:
        n = max(1, int(str(nb).strip()))
    except (TypeError, ValueError):
        n = 1
    neg = max(0, int(fiche.get("niveaux_negatifs", 0) or 0) - n)
    fiche["niveaux_negatifs"] = neg
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    reste = f" — il reste {neg} niveau(x) négatif(s)." if neg else \
        " — plus aucun niveau négatif."
    return ToolResult(
        text=f"✨ {fiche.get('nom', nom)} récupère : {n} niveau(x) négatif(s)"
        f" retiré(s){reste}",
        state_patch={"pj_updated": fiche.get("nom", nom)},
    )


@tool
async def fiche_perso_perte_niveau(
    ctx: ToolContext, nom: str, nb: int = 1
) -> ToolResult:
    """
    Applique une perte de niveau PERMANENTE (drain d'énergie non résisté,
    effet magique) : le personnage redescend d'un niveau, son XP est ramenée
    au point médian du nouveau niveau et il perd les PV du niveau (règle
    officielle DMG 3.5 « Level Loss »).

    :param nom (str): nom du personnage.
    :param nb (int): nombre de niveaux perdus (défaut 1).
    """
    from .fiches import _load_fiche, _save_fiche
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    try:
        n = max(1, int(str(nb).strip()))
    except (TypeError, ValueError):
        n = 1
    logs = gxp.appliquer_perte_niveau(fiche, n)
    # La perte consomme aussi les niveaux négatifs correspondants.
    neg = max(0, int(fiche.get("niveaux_negatifs", 0) or 0) - n)
    fiche["niveaux_negatifs"] = neg
    try:
        _save_fiche(ctx, nom, fiche)
    except ValueError as e:
        return ToolResult(text=f"❌ {e}")
    _maj_etat_pj(ctx, fiche.get("nom", nom), {
        "niveau": fiche.get("niveau"), "xp": fiche.get("xp", 0),
        "pv": fiche.get("pv"), "pv_max": fiche.get("pv_max"),
    })
    return ToolResult(
        text="\n".join(logs),
        state_patch={"pj_updated": fiche.get("nom", nom)},
    )


@tool
async def fiche_perso_consulter_xp(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Affiche l'état d'expérience d'un personnage : XP actuelles, XP requises
    pour le prochain niveau, XP restantes, niveaux négatifs éventuels.

    :param nom (str): nom du personnage.
    """
    from .fiches import _load_fiche
    fiche = _load_fiche(ctx, nom)
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    return ToolResult(
        text=(
            f"📊 {fiche.get('nom', nom)} — niveau "
            f"{fiche.get('niveau', '?')} ({fiche.get('classe', '?')})\n"
            + gxp.ligne_xp_fiche(fiche)
        )
    )
