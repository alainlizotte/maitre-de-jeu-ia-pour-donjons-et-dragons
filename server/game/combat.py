"""Moteur de combat D&D 3.5 100 % serveur (déterministe, sans LLM).

Philosophie : le LLM NARRATE, le serveur DÉCIDE. Toute la mécanique de
rotation — passer les tours des combattants incapables, jouer les monstres,
stabiliser les mourants, détecter la fin du combat, distribuer l'expérience
officielle (DMG 3.5) et tenir la mémoire de campagne — est résolue ici.

`boucle_auto()` est la fonction centrale : appelée avant ET après chaque
tour LLM (et via l'API REST), elle avance l'état de combat jusqu'à ce que
le combattant courant soit un PJ capable d'agir, ou que le combat se
clôture (victoire / défaite). Elle est idempotente : ne rien faire quand
tout est déjà en ordre.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from ..game.state import PartyState

_RE_MOD = re.compile(r"([+-]\s?\d+)")
_RE_DEGATS = re.compile(r"[Dd]égâts infligés\s*:\s*(\d+)")
_RE_ARME_BONUS = re.compile(r"(.+?)\s*([+-]\d+)\s*(?:\(|$)")

# Conditions qui empêchent totalement d'agir (DMG 3.5) — un PJ « A terre »
# (prone) peut agir, un PJ « Invalide » (0 PV, disabled) aussi de façon
# limitée : ils ne sont PAS skippés.
_CONDS_INCAPACITANTES = (
    "mort", "mourant", "stabilise", "stabilisé", "etourdi", "inconscient",
    "endormi", "paralyse", "paralysé", "petrifie", "pétrifié",
)

_MAX_ITER = 60  # garde-fou : un combat ne peut pas boucler indéfiniment


def _norm(s: Any) -> str:
    n = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in n if not unicodedata.combining(c))


@dataclass
class ResultatBoucle:
    """Résultat d'une exécution de `boucle_auto`."""
    events: list[str] = field(default_factory=list)     # lignes mécaniques
    patches: list[dict[str, Any]] = field(default_factory=list)
    phase: str = ""                                      # phase finale
    courant: str = ""                                    # combattant courant
    combat_termine: Optional[str] = None                 # "victoire"/"defaite"


def _party(ctx) -> PartyState:
    return PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)


async def _tool(ctx, name: str, args: dict[str, Any], res: ResultatBoucle):
    """Exécute un tool du registre SANS LLM, collecte son state_patch."""
    from ..tools.base import _TOOL_REGISTRY, invoke_tool
    if name not in _TOOL_REGISTRY:
        # Registre non encore peuplé (tests, appels directs) : on découvre.
        from ..tools.registry import discover_tools
        discover_tools("server.tools")
    spec = _TOOL_REGISTRY.get(name)
    if spec is None:
        return None
    tr = await invoke_tool(spec, ctx, args)
    if tr is None:
        return None
    if tr.state_patch:
        res.patches.append(tr.state_patch)
    return tr


# --------------------------------------------------------------------------- #
#  Capacité d'agir / repérage des combattants
# --------------------------------------------------------------------------- #
def _pj_depuis_etat(etat: dict, nom: str) -> Optional[dict]:
    nn = _norm(nom)
    for p in etat.get("pj") or []:
        if _norm(p.get("nom")) == nn:
            return p
    return None


def _pj_peut_agir(pj: dict) -> tuple[bool, str]:
    """Un PJ peut-il agir ce tour ? (règles DMG 3.5)."""
    conds = [_norm(c) for c in (pj.get("conditions") or [])]
    if "mort" in conds:
        return False, "mort"
    try:
        pv = int(pj.get("pv", 0))
    except (TypeError, ValueError):
        pv = 0
    if pv <= -10:
        return False, "mort"
    if "mourant" in conds or pv < 0:
        return False, "mourant"
    if "stabilise" in conds or "inconscient" in conds or "endormi" in conds:
        return False, "inconscient"
    for c in ("etourdi", "paralyse", "petrifie"):
        if c in conds:
            return False, c
    return True, ""


def _combattant_mort(etat: dict, nom: str) -> bool:
    """Compat avec tools.state._combattant_mort (mort / détruit)."""
    pj = _pj_depuis_etat(etat, nom)
    if pj is not None:
        ok, raison = _pj_peut_agir(pj)
        return raison == "mort"
    nn = _norm(nom)
    for mo in etat.get("monstres_combat") or []:
        if _norm(mo.get("nom")) == nn:
            return "Detruit" in (mo.get("conditions") or []) or \
                "Détruit" in (mo.get("conditions") or [])
    return True


def _prochain_vivant(etat: dict, ordre: list[dict], idx: int) -> int:
    """Indice du prochain combattant vivant en partant de idx (circulaire)."""
    if not ordre:
        return -1
    n = len(ordre)
    for step in range(n):
        j = (idx + step) % n
        if not _combattant_mort(etat, ordre[j].get("nom", "")):
            return j
    return -1


# --------------------------------------------------------------------------- #
#  Rotation du curseur de tour
# --------------------------------------------------------------------------- #
def _avancer_curseur(etat: dict) -> Optional[str]:
    """Fait passer `courant_tour_pour` au prochain combattant vivant.
    Renvoie le nouveau courant (None si aucun ordre). Met à jour `tour`
    (incrément au wrap) et `tour_depuis` (timeout serveur)."""
    ordre = etat.get("initiative") or []
    if not ordre:
        return None
    courant = etat.get("courant_tour_pour")
    idx = next((i for i, e in enumerate(ordre) if e.get("nom") == courant), -1)
    if idx == -1:
        idx = 0
        etat["tour"] = (etat.get("tour", 0) or 0) + 1
    else:
        idx += 1
        if idx >= len(ordre):
            idx = 0
            etat["tour"] = (etat.get("tour", 0) or 0) + 1
    vivant = _prochain_vivant(etat, ordre, idx)
    if vivant == -1:
        vivant = idx  # la clôture gérera ce cas
    etat["courant_tour_pour"] = ordre[vivant].get("nom", "")
    etat["tour_depuis"] = datetime.now().isoformat()
    return etat["courant_tour_pour"]


# --------------------------------------------------------------------------- #
#  Attaque automatique (monstres ennemis ET alliés invoqués)
# --------------------------------------------------------------------------- #
def _arme_du_bestiaire(m: dict) -> Optional[tuple[str, int, int, int, int]]:
    """(arme, bonus_atk, nb_des, faces, bonus_dmg) depuis la fiche bestiaire."""
    attaques = str(m.get("attaques") or "").strip()
    mm = _RE_ARME_BONUS.match(attaques)
    if not mm:
        return None
    arme = mm.group(1).strip()
    if not arme:
        return None
    bonus_atk = int(mm.group(2))
    degs = str(m.get("degs") or "")
    pos = degs.lower().find(arme.lower())
    bloc = degs[pos + len(arme):] if pos >= 0 else degs
    md = re.search(r"(\d+)[dD](\d+)([+-]\s?\d+)?", bloc)
    if not md:
        return None
    bonus_dmg = int((md.group(3) or "0").replace(" ", ""))
    return (arme, bonus_atk, int(md.group(1)), int(md.group(2)), bonus_dmg)


def _premiere_cible_pj(etat: dict) -> Optional[tuple[str, int]]:
    """Premier PJ encore « debout » (capable d'agir ou Invalide 0 PV)."""
    for p in etat.get("pj") or []:
        ok, raison = _pj_peut_agir(p)
        if raison == "mort":
            continue
        if not ok and raison in ("mourant", "inconscient"):
            continue
        if not ok:
            continue
        try:
            ca = int(p.get("ca") or 10)
        except (TypeError, ValueError):
            ca = 10
        return (str(p.get("nom") or ""), ca)
    return None


def _premiere_cible_inconsciente(etat: dict) -> Optional[tuple[str, int]]:
    """Premier PJ à terre (mourant/inconscient) — cible d'un coup de grâce."""
    for p in etat.get("pj") or []:
        ok, raison = _pj_peut_agir(p)
        if raison in ("mourant", "inconscient"):
            try:
                ca = int(p.get("ca") or 10)
            except (TypeError, ValueError):
                ca = 10
            return (str(p.get("nom") or ""), ca)
    return None


def _monstre_ennemi_vivant(etat: dict) -> Optional[dict]:
    for mo in etat.get("monstres_combat") or []:
        conds = mo.get("conditions") or []
        if mo.get("allie"):
            continue
        if "Détruit" in conds or "Detruit" in conds:
            continue
        if int(mo.get("pv", 0) or 0) <= 0 and not mo.get("inconnu"):
            continue
        return mo
    return None


async def _attaque_auto(
    ctx, res: ResultatBoucle, attaquant: str, ennemi: bool
) -> None:
    """Joue l'attaque automatique d'un monstre (ennemi → PJ ; allié invoqué
    → monstre ennemi). Coup de grâce si la seule cible est inconsciente."""
    from ..tools.monstres import _find_monstre_with_fallback

    etat = _party(ctx).load()
    m = _find_monstre_with_fallback(ctx, attaquant)
    arme = _arme_du_bestiaire(m or {})
    if arme is None:
        return
    nom_arme, bonus_atk, nb_des, faces, bonus_dmg = arme

    if ennemi:
        cible_ca = _premiere_cible_pj(etat)
        coup_de_grace = False
        if cible_ca is None:
            # Plus aucun PJ debout : les mourants subissent le coup de grâce
            # officiel (DMG 3.5 — full-round, critique automatique).
            cible_ca = _premiere_cible_inconsciente(etat)
            coup_de_grace = cible_ca is not None
        if cible_ca is None:
            return
        cible, ca = cible_ca
    else:
        mob = _monstre_ennemi_vivant(etat)
        if mob is None:
            return
        cible = str(mob.get("nom") or "")
        try:
            ca = int(mob.get("ca") or 10)
        except (TypeError, ValueError):
            ca = 10
        coup_de_grace = False

    if coup_de_grace:
        # Coup de grâce : touche automatique, dégâts critiques (dés maximaux
        # doublés — multiplicateur standard ×2), pas de jet d'attaque.
        total = max(0, (nb_des * faces + bonus_dmg) * 2)
        res.events.append(
            f"☠️ **Coup de grâce** : {attaquant} achève {cible} à terre — "
            f"dégâts critiques automatiques ({nb_des}d{faces} max ×2"
            f"{bonus_dmg:+d}) = **{total}**."
        )
        tr = await _tool(
            ctx, "fiche_perso_infliger_degats",
            {"nom": cible, "degats": total}, res,
        )
        if tr is not None and not tr.text.startswith("❌"):
            res.events.append(tr.text)
        return

    tr_atk = await _tool(
        ctx, "lancer_attaque",
        {
            "nom_attaquant": attaquant, "arme": nom_arme,
            "bonus_attaque": bonus_atk, "nom_cible": cible, "ca_cible": ca,
        },
        res,
    )
    if tr_atk is None or tr_atk.text.startswith("❌"):
        return
    res.events.append(tr_atk.text)
    if ("✅ **Touché**" not in tr_atk.text) and ("⭐ **20 naturel**" not in tr_atk.text):
        return

    tr_dm = await _tool(
        ctx, "lancer_degats",
        {
            "nb_des": nb_des, "faces": faces, "bonus": bonus_dmg,
            "arme_ou_sort": nom_arme, "cible": cible,
        },
        res,
    )
    if tr_dm is None:
        return
    res.events.append(tr_dm.text)
    md = _RE_DEGATS.search(tr_dm.text)
    if not md:
        return
    tr_inf = await _tool(
        ctx, "fiche_perso_infliger_degats",
        {"nom": cible, "degats": int(md.group(1))}, res,
    )
    if tr_inf is not None and not tr_inf.text.startswith("❌"):
        res.events.append(tr_inf.text)


# --------------------------------------------------------------------------- #
#  Stabilisation des mourants (officiel : 1d20 par round, ≥10 = stabilisé)
# --------------------------------------------------------------------------- #
async def _jet_stabilisation(ctx, res: ResultatBoucle, pj: dict) -> None:
    nom = str(pj.get("nom") or "")
    conds = pj.get("conditions") or []
    if "Stabilisé" in conds or "Stabilise" in conds:
        return
    jet = random.randint(1, 20)
    if jet == 1:
        res.events.append(
            f"🩸 {nom} (mourant) — jet de stabilisation : **1** naturel → "
            "perte 1 PV supplémentaire."
        )
        tr = await _tool(
            ctx, "fiche_perso_infliger_degats",
            {"nom": nom, "degats": 1}, res,
        )
        if tr is not None and not tr.text.startswith("❌"):
            res.events.append(tr.text)
    elif jet >= 10:
        res.events.append(
            f"🩸 {nom} (mourant) — jet de stabilisation : {jet} ≥ 10 → "
            "**stabilisé** (inconscient, plus de perte de PV)."
        )
        # Marque la condition sur la fiche ET l'état (sans LLM).
        try:
            from ..tools.fiches import _load_fiche, _save_fiche, _sync_pj
            fiche = _load_fiche(ctx, nom)
            if fiche is not None:
                fc = fiche.setdefault("conditions", [])
                if "Stabilisé" not in fc:
                    fc.append("Stabilisé")
                _save_fiche(ctx, nom, fiche)
            _sync_pj(ctx, nom, {"conditions": conds + ["Stabilisé"]})
        except Exception:                                    # noqa: BLE001
            pass
    else:
        res.events.append(
            f"🩸 {nom} (mourant) — jet de stabilisation : {jet} < 10 → "
            "continue de saigner."
        )


# --------------------------------------------------------------------------- #
#  Fin de combat : victoire / défaite + XP officielle + mémoire
# --------------------------------------------------------------------------- #
def _verifier_fin(etat: dict) -> Optional[str]:
    """'victoire' si tous les ennemis sont détruits, 'defaite' si tous les
    PJ sont morts, sinon None."""
    ennemis = [m for m in etat.get("monstres_combat") or [] if not m.get("allie")]
    if ennemis and all(
        "Détruit" in (m.get("conditions") or [])
        or "Detruit" in (m.get("conditions") or [])
        for m in ennemis
    ):
        return "victoire"
    pjs = etat.get("pj") or []
    if pjs and all(
        _pj_peut_agir(p)[1] == "mort" for p in pjs
    ):
        return "defaite"
    return None


async def _distribuer_xp(ctx, res: ResultatBoucle, etat: dict) -> None:
    """XP officielle (DMG 3.5) : chaque PJ vivant gagne, pour CHAQUE ennemi
    détruit, la valeur de la table selon SON propre niveau. Applique les
    montées de niveau (jet de dé de vie) et synchronise les fiches."""
    from ..game import xp as gxp
    from ..tools.fiches import _load_fiche, _save_fiche, _sync_pj, _patch_pj

    vaincus = [
        m for m in etat.get("monstres_combat") or []
        if not m.get("allie")
        and ("Détruit" in (m.get("conditions") or [])
             or "Detruit" in (m.get("conditions") or []))
    ]
    if not vaincus:
        return
    resume: list[str] = [
        f"🏆 **Victoire !** Ennemis vaincus : "
        + ", ".join(f"{m.get('nom')} (FP {m.get('fp', '?')})" for m in vaincus)
    ]
    for p in etat.get("pj") or []:
        if _pj_peut_agir(p)[1] == "mort":
            continue  # un PJ mort ne gagne pas d'XP
        nom = str(p.get("nom") or "")
        fiche = _load_fiche(ctx, nom)
        if fiche is None:
            continue
        total = 0
        for m in vaincus:
            total += gxp.xp_pour_cr(m.get("fp"), int(fiche.get("niveau", 1) or 1))
        if total <= 0:
            continue
        logs = gxp.appliquer_gain(fiche, total)
        try:
            _save_fiche(ctx, nom, fiche)
        except ValueError as e:
            res.events.append(f"❌ (XP {nom}) {e}")
            continue
        idx = _sync_pj(ctx, nom, {
            "niveau": fiche.get("niveau"), "xp": fiche.get("xp", 0),
            "pv": fiche.get("pv"), "pv_max": fiche.get("pv_max"),
        })
        res.patches.append(_patch_pj(nom, idx, {
            "niveau": fiche.get("niveau"), "xp": fiche.get("xp", 0),
            "pv": fiche.get("pv"), "pv_max": fiche.get("pv_max"),
        }))
        resume.extend(logs)
    res.events.append("\n".join(resume))


def _memoriser_combat(etat: dict, raison: str) -> None:
    """Journalise le combat dans la mémoire de campagne (monstres battus)."""
    from datetime import datetime as _dt
    ennemis = [
        str(m.get("nom") or "") for m in etat.get("monstres_combat") or []
        if not m.get("allie")
    ]
    memoire = etat.setdefault("memoire", {})
    memoire.setdefault("monstres_combattus", []).append({
        "noms": ennemis,
        "issue": "victoire" if raison == "victoire" else raison,
        "tour": etat.get("tour", 0),
        "ts": _dt.now().isoformat(),
    })


def _fermer_etat(etat: dict, raison: str) -> None:
    etat["phase"] = "exploration"
    etat["initiative"] = []
    etat["courant_tour_pour"] = None
    etat["tour"] = 0
    etat["monstres_combat"] = []
    etat["tour_depuis"] = None


async def cloturer(ctx, res: ResultatBoucle, raison: str) -> None:
    """Clôture officielle du combat : XP, mémoire, journal, reset état."""
    state = _party(ctx)
    etat = state.load()
    if raison == "victoire":
        await _distribuer_xp(ctx, res, etat)
        # Recharge l'état : _distribuer_xp a écrit les XP/niveaux dans la
        # liste `pj` (via _sync_pj → sa propre sauvegarde). Sans ce rechargement,
        # le save final ci-dessous écraserait ces mises à jour avec l'état
        # périmé chargé ci-dessus.
        etat = state.load()
        _memoriser_combat(etat, raison)
        res.events.append(
            "⚔️ _Tous les ennemis sont à terre — le combat est terminé "
            "(clôturé par le serveur)._"
        )
    else:
        _memoriser_combat(etat, raison)
        res.events.append(
            "💀 _Tous les héros sont tombés — la partie est perdue "
            "(clôturée par le serveur)._"
        )
    etat.setdefault("histoire", []).append({
        "ts": datetime.now().isoformat(),
        "tour": "",
        "evenement": f"Combat terminé ({raison}) : "
        + ", ".join(
            str(m.get("nom") or "")
            for m in etat.get("monstres_combat") or [] if not m.get("allie")
        ),
    })
    _fermer_etat(etat, raison)
    state.save(etat)
    res.phase = "exploration"
    res.combat_termine = raison
    res.patches.append({
        "phase": "exploration", "tour": 0, "courant_tour_pour": None,
        "initiative": [], "monstres_combat": [],
    })


# --------------------------------------------------------------------------- #
#  BOUCLE AUTOMATIQUE CENTRALE
# --------------------------------------------------------------------------- #
def _timeout_expire(etat: dict, timeout_s: Optional[int]) -> bool:
    """Le tour courant dure-t-il depuis trop longtemps (joueur fantôme) ?"""
    if not timeout_s or timeout_s <= 0:
        return False
    brut = etat.get("tour_depuis")
    if not brut:
        return False
    try:
        debut = datetime.fromisoformat(str(brut))
    except ValueError:
        return False
    return datetime.now() - debut > timedelta(seconds=timeout_s)


async def boucle_auto(
    ctx,
    force_avance: bool = False,
    timeout_secondes: Optional[int] = None,
) -> ResultatBoucle:
    """Fait tourner le combat jusqu'à un état stable :

    - clôture si victoire/défaite détectée ;
    - skip + stabilisation des PJ incapables (mourants…) ;
    - les monstres ennemis ET les alliés invoqués jouent leur tour
      automatiquement (attaque officielle du bestiaire) ;
    - timeout : le tour d'un PJ silencieux depuis trop longtemps passe ;
    - s'arrête quand le combattant courant est un PJ capable d'agir.

    `force_avance=True` : le tour du PJ courant est considéré terminé
    (action standard consommée) — le curseur avance avant la boucle.
    """
    res = ResultatBoucle()
    state = _party(ctx)
    avance_force_pending = force_avance

    for _ in range(_MAX_ITER):
        etat = state.load()
        res.phase = str(etat.get("phase") or "")
        if etat.get("phase") != "combat" or not etat.get("initiative"):
            res.courant = str(etat.get("courant_tour_pour") or "")
            return res

        # 1) Fin de combat ?
        fin = _verifier_fin(etat)
        if fin:
            await cloturer(ctx, res, fin)
            res.courant = ""
            return res

        actif = str(etat.get("courant_tour_pour") or "")
        res.courant = actif

        # 2) Tour d'un monstre / PNJ → résolution automatique.
        pj = _pj_depuis_etat(etat, actif)
        if pj is None:
            mob = next(
                (mo for mo in etat.get("monstres_combat") or []
                 if _norm(mo.get("nom")) == _norm(actif)),
                None,
            )
            est_allie = bool(mob and mob.get("allie"))
            if mob is None:
                # Combattant inconnu (PNJ improvisé) : on passe son tour.
                res.events.append(f"⏭️ Tour de {actif} (inconnu) passé.")
            elif "Détruit" in (mob.get("conditions") or []) or \
                    "Detruit" in (mob.get("conditions") or []):
                pass  # déjà mort : skip silencieux
            else:
                await _attaque_auto(ctx, res, actif, ennemi=not est_allie)
            etat["tour_depuis"] = datetime.now().isoformat()
            _avancer_curseur(etat)
            state.save(etat)
            continue

        # 3) Tour d'un PJ.
        ok, raison = _pj_peut_agir(pj)
        if ok:
            if avance_force_pending:
                avance_force_pending = False
                _avancer_curseur(etat)
                state.save(etat)
                continue
            if _timeout_expire(etat, timeout_secondes):
                res.events.append(
                    f"⏳ Tour de {actif} passé automatiquement (délai "
                    f"dépassé — {timeout_secondes} s)."
                )
                _avancer_curseur(etat)
                state.save(etat)
                continue
            return res  # état stable : au joueur d'agir

        # PJ incapable : jet de stabilisation officiel puis skip.
        if raison == "mourant":
            await _jet_stabilisation(ctx, res, pj)
            # Un mourant peut mourir de sa blessure pendant la stabilisation.
            fin2 = _verifier_fin(state.load())
            if fin2:
                await cloturer(ctx, res, fin2)
                return res
        res.events.append(
            f"⏭️ Tour de {actif} passé ({raison} — incapable d'agir)."
        )
        _avancer_curseur(etat)
        state.save(etat)

    # Filet ultime : iteration épuisée = aucun PJ capable d'agir → défaite
    # (tous les mourants auront reçu des coups de grâce entre-temps).
    etat = state.load()
    if etat.get("phase") == "combat":
        fin = _verifier_fin(etat)
        if fin:
            await cloturer(ctx, res, fin)
        else:
            res.events.append(
                "⚠️ Boucle de combat interrompue après trop d'itérations "
                "(état incohérent signalé au MJ)."
            )
    return res
