"""Mémoire de campagne — cohérence longue d'une partie D&D.

Chaque partie porte sa mémoire dans `partie_<id>.json` sous `memoire` :
missions (actives/terminées/échouées), lieux visités, personnages
rencontrés, monstres combattus (rempli AUTOMATIQUEMENT par le moteur de
combat à chaque victoire) et position actuelle du groupe.

La mémoire est réinjectée dans le prompt du MJ (voir `memoire_resume`) :
le modèle n'a BESOIN d'aucun tool pour s'en souvenir, seulement de ces
tools pour la tenir à jour.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool

_MAX_LISTE = 100  # bornes mémoire (les plus récents en dernier)


def _memoire(etat: dict[str, Any]) -> dict[str, Any]:
    m = etat.setdefault("memoire", {})
    m.setdefault("missions", [])
    m.setdefault("lieux_visites", [])
    m.setdefault("personnages_rencontres", [])
    m.setdefault("monstres_combattus", [])
    m.setdefault("position", {"lieu": "", "zone": "", "detail": ""})
    return m


def _charger(ctx: ToolContext):
    from ..game.state import PartyState
    st = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = st.load()
    if "_erreur" in etat:
        return None, None
    return st, etat


@tool
async def memoire_mission(
    ctx: ToolContext, titre: str, statut: str = "active", notes: str = ""
) -> ToolResult:
    """
    Crée ou met à jour une mission dans la mémoire de campagne. À faire
    DÈS qu'une quête est acceptée, accomplie ou échouée — c'est ce qui
    garantit la cohérence de l'intrigue sur une longue campagne.

    :param titre (str): titre court de la mission (clé unique).
    :param statut (str): "active", "terminee" ou "echouee".
    :param notes (str): résumé/objectif/état actuel (optionnel).
    """
    st, etat = _charger(ctx)
    if st is None:
        return ToolResult(text="❌ État de partie illisible.")
    m = _memoire(etat)
    titre = str(titre or "").strip()
    if not titre:
        return ToolResult(text="❌ Donne un titre de mission.")
    statut = str(statut or "active").strip().lower()
    if statut not in ("active", "terminee", "echouee"):
        statut = "active"
    for miss in m["missions"]:
        if str(miss.get("titre", "")).lower() == titre.lower():
            miss["statut"] = statut
            if notes:
                miss["notes"] = str(notes)
            miss["ts"] = datetime.now().isoformat()
            st.save(etat)
            return ToolResult(
                text=f"✅ Mission mise à jour : « {titre} » → {statut}.",
                state_patch={"memoire": m},
            )
    m["missions"].append({
        "titre": titre, "statut": statut, "notes": str(notes or ""),
        "ts": datetime.now().isoformat(),
    })
    st.save(etat)
    return ToolResult(
        text=f"✅ Mission mémorisée : « {titre} » ({statut}).",
        state_patch={"memoire": m},
    )


@tool
async def memoire_lieu(
    ctx: ToolContext, nom: str, notes: str = ""
) -> ToolResult:
    """
    Enregistre un lieu visité dans la mémoire de campagne (ville, donjon,
    auberge, région…). Met à jour les notes si le lieu est déjà connu.

    :param nom (str): nom du lieu.
    :param notes (str): ce que le groupe y a fait/trouvé (optionnel).
    """
    st, etat = _charger(ctx)
    if st is None:
        return ToolResult(text="❌ État de partie illisible.")
    m = _memoire(etat)
    nom = str(nom or "").strip()
    if not nom:
        return ToolResult(text="❌ Donne un nom de lieu.")
    for l in m["lieux_visites"]:
        if str(l.get("nom", "")).lower() == nom.lower():
            if notes:
                l["notes"] = str(notes)
            l["ts"] = datetime.now().isoformat()
            st.save(etat)
            return ToolResult(
                text=f"✅ Lieu mis à jour : « {nom} ».",
                state_patch={"memoire": m},
            )
    m["lieux_visites"].append({
        "nom": nom, "notes": str(notes or ""),
        "ts": datetime.now().isoformat(),
    })
    if len(m["lieux_visites"]) > _MAX_LISTE:
        m["lieux_visites"] = m["lieux_visites"][-_MAX_LISTE:]
    st.save(etat)
    return ToolResult(
        text=f"✅ Lieu visité mémorisé : « {nom} ».",
        state_patch={"memoire": m},
    )


@tool
async def memoire_personnage(
    ctx: ToolContext, nom: str, notes: str = ""
) -> ToolResult:
    """
    Enregistre un personnage rencontré (PNJ, allié, antagoniste, mentor…)
    dans la mémoire de campagne. Met à jour les notes s'il est déjà connu.

    :param nom (str): nom du personnage rencontré.
    :param notes (str): qui il est / son rôle / sa réaction (optionnel).
    """
    st, etat = _charger(ctx)
    if st is None:
        return ToolResult(text="❌ État de partie illisible.")
    m = _memoire(etat)
    nom = str(nom or "").strip()
    if not nom:
        return ToolResult(text="❌ Donne un nom de personnage.")
    for p in m["personnages_rencontres"]:
        if str(p.get("nom", "")).lower() == nom.lower():
            if notes:
                p["notes"] = str(notes)
            p["ts"] = datetime.now().isoformat()
            st.save(etat)
            return ToolResult(
                text=f"✅ Personnage mis à jour : « {nom} ».",
                state_patch={"memoire": m},
            )
    m["personnages_rencontres"].append({
        "nom": nom, "notes": str(notes or ""),
        "ts": datetime.now().isoformat(),
    })
    if len(m["personnages_rencontres"]) > _MAX_LISTE:
        m["personnages_rencontres"] = m["personnages_rencontres"][-_MAX_LISTE:]
    st.save(etat)
    return ToolResult(
        text=f"✅ Personnage mémorisé : « {nom} ».",
        state_patch={"memoire": m},
    )


@tool
async def memoire_position(
    ctx: ToolContext, lieu: str, zone: str = "", detail: str = ""
) -> ToolResult:
    """
    Définit la position actuelle du groupe dans la mémoire de campagne
    (lieu + zone + détail : « Donjon de xe, salle du trône, près du puits »).
    Sert de référence spatiale permanente pour la cohérence.

    :param lieu (str): lieu principal (ex. "Donjon des Ombres").
    :param zone (str): zone/salle/région (optionnel).
    :param detail (str): précision (optionnel).
    """
    st, etat = _charger(ctx)
    if st is None:
        return ToolResult(text="❌ État de partie illisible.")
    m = _memoire(etat)
    lieu = str(lieu or "").strip()
    if not lieu:
        return ToolResult(text="❌ Donne un nom de lieu.")
    m["position"] = {"lieu": lieu, "zone": str(zone or ""), "detail": str(detail or "")}
    # Synchronise aussi le lieu « officiel » de l'état (affiché par le front).
    if not etat.get("lieu") or str(etat["lieu"].get("nom") or "") in ("", "(non déterminé)"):
        etat["lieu"] = {"nom": lieu, "type": "donjon", "description": str(detail or ""), "position_x": 0, "position_y": 0}
    st.save(etat)
    pos = m["position"]
    texte_pos = pos["lieu"] + (f" — {pos['zone']}" if pos["zone"] else "") \
        + (f" ({pos['detail']})" if pos["detail"] else "")
    return ToolResult(
        text=f"✅ Position du groupe : {texte_pos}.",
        state_patch={"memoire": m, "lieu": etat.get("lieu")},
    )


# --------------------------------------------------------------------------- #
#  Résumé injecté dans le prompt du MJ (aucun tool requis pour LIRE)
# --------------------------------------------------------------------------- #
def memoire_resume(etat: dict[str, Any], max_chars: int = 1200) -> str:
    """Bloc condensé de la mémoire de campagne pour le system prompt."""
    m = etat.get("memoire") or {}
    lignes: list[str] = []
    pos = m.get("position") or {}
    if pos.get("lieu"):
        lignes.append(
            "Position actuelle : " + pos.get("lieu", "")
            + (f" — {pos['zone']}" if pos.get("zone") else "")
            + (f" ({pos['detail']})" if pos.get("detail") else "")
        )
    actives = [
        miss for miss in (m.get("missions") or [])
        if miss.get("statut") == "active"
    ]
    if actives:
        lignes.append("Missions actives :")
        lignes.extend(
            f"- « {miss.get('titre', '?')} »"
            + (f" : {miss['notes']}" if miss.get("notes") else "")
            for miss in actives[-5:]
        )
    termnees = [
        miss for miss in (m.get("missions") or [])
        if miss.get("statut") != "active"
    ]
    if termnees:
        lignes.append(
            "Missions accomplies/échouées : "
            + ", ".join(
                f"« {miss.get('titre', '?')} » ({miss.get('statut')})"
                for miss in termnees[-5:]
            )
        )
    lieux = m.get("lieux_visites") or []
    if lieux:
        lignes.append(
            "Lieux visités : "
            + ", ".join(str(l.get("nom", "?")) for l in lieux[-8:])
        )
    pnjs = m.get("personnages_rencontres") or []
    if pnjs:
        lignes.append(
            "Personnages rencontrés : "
            + ", ".join(str(p.get("nom", "?")) for p in pnjs[-8:])
        )
    combats = m.get("monstres_combattus") or []
    if combats:
        lignes.append("Combats :")
        lignes.extend(
            f"- {'victoire' if c.get('issue') == 'victoire' else c.get('issue', '?')} "
            + "contre " + ", ".join(c.get("noms") or [])
            for c in combats[-5:]
        )
    if not lignes:
        return ""
    resume = "\n".join(lignes)
    if len(resume) > max_chars:
        resume = resume[:max_chars] + "\n…(mémoire tronquée)"
    return resume
