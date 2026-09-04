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


def _nom_normalise(nom: str) -> str:
    """Nom normalisé pour comparaison (accent/underscore-insensible)."""
    import unicodedata

    return "".join(
        ch for ch in unicodedata.normalize("NFD", str(nom).lower())
        if unicodedata.category(ch) != "Mn"
    ).replace("_", " ").replace("-", " ").strip()


def _combattant_mort(etat: dict, nom: str) -> bool:
    """Un combattant est-il mort (à exclure de l'initiative) ?
    PJ : condition « Mort » ; monstre suivi : condition « Détruit ». Un
    combattant inconnu est réputé mort pour ne jamais bloquer l'avancement."""
    nn = _nom_normalise(nom)
    for p in (etat.get("pj") or []):
        if _nom_normalise(p.get("nom")) == nn:
            return "Mort" in (p.get("conditions") or [])
    for mo in (etat.get("monstres_combat") or []):
        if _nom_normalise(mo.get("nom")) == nn:
            return "Détruit" in (mo.get("conditions") or [])
    return True


def _prochain_vivant(etat: dict, ordre: list[dict], idx: int) -> int:
    """Renvoie l'indice du prochain combattant vivant dans `ordre`, en
    partant de `idx` (le combattant qui doit normalement agir), en bouclant
    circulairement. Renvoie -1 si aucun vivant."""
    if not ordre:
        return -1
    n = len(ordre)
    for step in range(n):
        j = (idx + step) % n
        if not _combattant_mort(etat, ordre[j].get("nom", "")):
            return j
    return -1


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

    from .monstres import _find_monstre, _load_bestiaire, _suggestions

    noms = [n.strip() for n in monstres.split(",") if n.strip()]
    if not noms:
        return ToolResult(text="❌ Donne au moins un nom de monstre.")

    # ── VALIDITÉ STRICTE DES MONSTRES ───────────────────────────────────
    # Le MJ ne doit JAMAIS inventer de monstre. On n'accepte que des créatures
    # du bestiaire officiel (avec description physique ET statistiques).
    # Tout nom qui ne résout à AUCUN monstre du bestiaire bloque le combat et
    # affiche les suggestions officielles les plus proches — il est interdit
    # d'engager un combat avec une créature inventée.
    def _bdc(nom: str) -> list[str]:
        best = _load_bestiaire(ctx)
        return _suggestions(best.get("monstres", {}) or {}, [str(nom or "")])

    monstres_ok: list[dict[str, Any]] = []
    refus: list[str] = []
    for nom in noms:
        m = _find_monstre(ctx, nom)
        if m is None:
            refus.append(nom)
            continue
        monstres_ok.append(m)
    if refus:
        lignes_refus = ["⛔ **Monstres refusés (hors bestiaire officiel 3.5) :**"]
        for nom in refus:
            sugg = _bdc(nom)
            lignes_refus.append(f"- **{nom}** — introuvable dans le bestiaire.")
            if sugg:
                lignes_refus.append(
                    "  _Monstres officiels les plus proches : "
                    + ", ".join(sugg) + "._"
                )
        lignes_refus.append(
            "_\nIl est INTERDIT d'invoquer une créature inventée (sans fiche "
            "ni stats officielles). Rejoue avec des noms du bestiaire (voir "
            "liste ci-dessus / monstre_lister)._"
        )
        return ToolResult(text="\n".join(lignes_refus))

    state = _party(ctx)
    etat = state.load()
    participants: list[dict] = []
    monstres_combat: list[dict] = []
    lignes: list[str] = ["🎲 **Initiative du combat**"]

    def _mod_initiative_pj(nom_pj: str, mod_dex: int) -> int:
        """Mod. DEX + dons d'initiative lus sur la fiche (Initiative
        améliorée = +4). L'entrée `pj` de l'état ne transporte pas les
        dons ; fiche absente → mod. DEX seul. Fail-safe."""
        try:
            from .fiches import _load_fiche, bonus_dons_effet
            fiche = _load_fiche(ctx, nom_pj)
            if fiche:
                return mod_dex + bonus_dons_effet(fiche.get("dons"), "initiative")
        except Exception:                                    # noqa: BLE001
            pass
        return mod_dex

    # Monstres validés (init officielle du bestiaire). Les homonymes sont
    # d'abord désambiguïsés : le suivi de tour/PV est basé sur le nom, deux
    # créatures identiques doivent rester distinctes.
    labels_resolus = [str((m or {}).get("nom") or noms[i])
                      for i, m in enumerate(monstres_ok)]
    labels_uniques = _noms_uniques(labels_resolus)

    for i, (nom, m) in enumerate(zip(noms, monstres_ok)):
        label = labels_uniques[i]
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
        jet = random.randint(1, 20)
        participants.append({"nom": label, "init": jet + mod,
                             "jet_brut": jet, "mod": mod})
        lignes.append(
            f"- **{label}** — initiative {jet + mod} "
            f"(d20={jet}, mod={mod:+d})"
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
                mod = _mod_initiative_pj(str(p.get("nom") or ""), (dex - 10) // 2)
                jet = random.randint(1, 20)
                participants.append({"nom": p["nom"], "init": jet + mod,
                                     "jet_brut": jet, "mod": mod})
                lignes.append(
                    f"- **{p['nom']}** — initiative {jet + mod} "
                    f"(d20={jet}, mod DEX {mod:+d})"
                )
                continue
        dex = ((caracs.get("DEX") if isinstance(caracs, dict) else None) or 10)
        mod = _mod_initiative_pj(str(p.get("nom") or ""), (int(dex) - 10) // 2)
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
    from datetime import datetime as _dt
    etat["tour_depuis"] = _dt.now().isoformat()
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
        "_⚙️ Rotation gérée par le SERVEUR : les monstres attaquent, les "
        "mourants sont passés et l'XP distribuée automatiquement. Le joueur "
        "actif déclare son action (attaque, sort…) ; terminer_mon_tour "
        "passe la main._",
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
    # Sauter les combattants morts / détruits (ne jamais leur donner de tour).
    prochain = _prochain_vivant(etat, ordre, idx)
    if prochain == -1:
        # Plus aucun vivant : on laisse tomber (la clôture serveur gérera).
        prochain = idx
    idx = prochain
    etat["courant_tour_pour"] = ordre[idx].get("nom", "")
    from datetime import datetime as _dt2
    etat["tour_depuis"] = _dt2.now().isoformat()
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


def _clore_combat_etat(state: PartyState, raison: str) -> Optional[str]:
    """Clôture un état en combat : phase→exploration, initiative/PV vidés,
    mémoire du dénouement. Renvoie un message d'erreur ou None si ok."""
    etat = state.load()
    if "_erreur" in etat:
        return f"❌ État illisible : {etat}"
    if etat.get("phase") != "combat" or not etat.get("initiative"):
        return "❌ Aucun combat en cours."
    etat["phase"] = "exploration"
    etat["initiative"] = []
    etat["courant_tour_pour"] = None
    etat["tour"] = 0
    etat["monstres_combat"] = []
    from datetime import datetime as _dt
    etat.setdefault("histoire", []).append({
        "ts": _dt.now().isoformat(),
        "tour": "",
        "evenement": f"Combat terminé : {raison}.",
    })
    etat.setdefault("memoire", {}).setdefault("monstres_combattus", []).append({
        "issue": raison, "tour": 0, "ts": _dt.now().isoformat(),
    })
    return state.save(etat)


@tool
async def finir_combat(ctx: ToolContext) -> ToolResult:
    """
    Termine le combat en cours : phase→"exploration", initiative vidée,
    courant_tour_pour=None, tour=0.
    """
    state = _party(ctx)
    err = _clore_combat_etat(state, "résolu")
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
async def retraite_combat(ctx: ToolContext) -> ToolResult:
    """
    Clôture le combat suite à une FUITE / RETRAITE ou à la capitulation des
    ennemis : passe phase→"exploration" (initiative vidée, PV reset) SANS
    distribuer d'XP. À n'appeler QUE quand les hostilités cessent réellement
    (les ennemis fuient, se rendent, ou le groupe bat en retraite après
    avoir décroché). Le désenclavement (retraite sous la menace, attaques
    d'opportunité) reste narré par le MJ avant l'appel.
    """
    state = _party(ctx)
    err = _clore_combat_etat(state, "retraite")
    if err:
        return ToolResult(text=err)
    return ToolResult(
        text="🏃 **Retraite effectuée — le combat se termine.** Retour à "
             "l'exploration (aucune XP de victoire).",
        state_patch={
            "phase": "exploration",
            "tour": 0,
            "courant_tour_pour": None,
            "initiative": [],
            "monstres_combat": [],
        },
    )


@tool
async def combat_ajouter_combattant(
    ctx: ToolContext,
    nom: str,
    initiative: Optional[int] = None,
    allie: bool = False,
) -> ToolResult:
    """
    Ajoute un combattant AU combat DÉJÀ EN COURS sans le réinitialiser :
    invoquation de monstre (Invocation de monstre I-IX, squelettes de clerc…),
    renfort qui surgit, familier qui rejoint la mêlée. Le combattant est
    inséré dans l'ordre d'initiative existant (jet 1d20 + mod. INIT du
    bestiaire si `initiative` n'est pas fourni) et ses PV sont suivis
    mécaniquement dans monstres_combat. NE PAS utiliser engager_combat pour
    cela (il réinitialiserait tout le combat).

    :param nom (str): nom du monstre invoqué (ex. "Loup", "Squelette").
    :param initiative (int): résultat d'initiative déjà jeté (optionnel ;
        sinon 1d20 + mod bestiaire).
    :param allie (bool): True si le combattant se bat POUR les PJ (invoqué
        par un lanceur de sorts joueur). Défaut: False (ennemi).
    """
    import random

    from .monstres import _find_monstre_with_fallback

    state = _party(ctx)
    etat = state.load()
    if etat.get("phase") != "combat" or not etat.get("initiative"):
        return ToolResult(
            text=(
                "❌ Aucun combat en cours — utilise `engager_combat` pour "
                "déclencher une rencontre, puis `combat_ajouter_combattant` "
                "pour les renforts/invoquations en cours de mêlée."
            )
        )

    # Stats du bestiaire (PV/CA/INIT) si le monstre y figure — les créatures
    # INVOQUÉES par magie (Invocation de monstre I-IX, squelette animé, …)
    # sont suivies avec leurs propres PV et n'ont pas besoin de figurer dans
    # le bestiaire ni de description physique.
    m = _find_monstre_with_fallback(ctx, nom)
    label_base = str((m or {}).get("nom") or nom).strip() or nom

    # Désambiguïsation vs les combattants DÉJÀ sur le plateau (homonymes).
    existants = [str(e.get("nom", "")) for e in etat["initiative"]]
    vus: dict[str, int] = {}
    for n in existants:
        cle = n.strip().lower()
        vus[cle] = vus.get(cle, 0) + 1
    cle_base = label_base.lower()
    label = label_base if vus.get(cle_base, 0) == 0 else (
        f"{label_base} ({vus[cle_base] + 1})"
    )

    # Initiative : valeur fournie, sinon 1d20 + mod bestiaire.
    mod = 0
    if m:
        try:
            mod = int(str(m.get("init", "0")).replace("+", "").strip())
        except ValueError:
            mod = 0
    if initiative is not None:
        total = int(initiative)
        jet = total - mod
    else:
        jet = random.randint(1, 20)
        total = jet + mod

    # Insertion dans l'ordre existant (tri décroissant, stable).
    entree = {"nom": label, "init": total, "jet_brut": jet, "mod": mod}
    ordre = etat["initiative"]
    ordre.append(entree)
    ordre.sort(key=lambda x: x.get("init", 0), reverse=True)

    # Suivi mécanique des PV (ennemis ET alliés invoqués).
    if m:
        try:
            pv_m = int(str(m.get("pv", "0")).strip().split("(")[0])
        except ValueError:
            pv_m = 0
        try:
            ca_m = int(str(m.get("ca", "0")).strip())
        except ValueError:
            ca_m = 0
        monstre_entry = {
            "nom": label, "pv": pv_m, "pv_max": pv_m, "ca": ca_m,
            "fp": str(m.get("fp", "?")), "conditions": [],
        }
    else:
        monstre_entry = {
            "nom": label, "pv": -1, "pv_max": -1, "ca": None,
            "fp": "?", "conditions": [], "inconnu": True,
        }
    if allie:
        monstre_entry["allie"] = True
    etat.setdefault("monstres_combat", []).append(monstre_entry)

    err = state.save(etat)
    if err:
        return ToolResult(text=err)

    camp = "allié invoqué" if allie else "ennemi"
    src = "" if m else " (stats inconnues : mod +0 — consulte le bestiaire)"
    position = ordre.index(entree) + 1
    return ToolResult(
        text=(
            f"✨ **{label}** rejoint le combat ({camp}) — initiative "
            f"{total} (d20={jet}, mod={mod:+d}){src}, agira en "
            f"{position}e position dans l'ordre.\n"
            "Ordre : " + " → ".join(e.get("nom", "?") for e in ordre) +
            "\n_Ses PV sont suivis mécaniquement : utilise "
            "fiche_perso_infliger_degats quand il subit des dégâts._"
        ),
        state_patch={
            "initiative": ordre,
            "monstres_combat": etat.get("monstres_combat", []),
        },
    )


@tool
async def reset_partie(ctx: ToolContext) -> ToolResult:
    """
    ⚠️ Réinitialise entièrement l'état de la partie (efface le fichier JSON).
    À utiliser seulement lors d'un nouveau départ confirmé par les joueurs.
    """
    return ToolResult(text=_party(ctx).reset(), state_patch={"__reset__": True})


@tool
async def terminer_mon_tour(ctx: ToolContext) -> ToolResult:
    """
    Termine VOLONTAIREMENT le tour du personnage courant (il renonce à ses
    actions restantes et passe la main). C'est la seule façon pour un joueur
    de céder son tour sans agir — la rotation vers le combattant suivant,
    les tours de monstres et la fin de combat sont ensuite gérés
    automatiquement par le serveur.

    :param (aucun)
    """
    from datetime import datetime as _dt

    state = _party(ctx)
    etat = state.load()
    if etat.get("phase") != "combat" or not etat.get("initiative"):
        return ToolResult(text="❌ Aucun combat en cours.")
    courant = str(etat.get("courant_tour_pour") or "")
    # Sécurité : seul le joueur du personnage courant peut terminer son tour
    # (best effort — un monstre n'a pas de joueur).
    pj = next(
        (p for p in etat.get("pj") or []
         if str(p.get("nom", "")).lower() == courant.lower()),
        None,
    )
    if pj is not None and str(pj.get("joueur") or "").strip():
        emit = str(ctx.joueur or "").strip().lower()
        legitime = str(pj.get("joueur")).strip().lower()
        if emit and emit != legitime:
            return ToolResult(
                text=(
                    f"❌ Ce n'est pas ton tour : c'est à {courant} "
                    f"(joué par {pj.get('joueur')})."
                )
            )
    ordre = etat["initiative"]
    idx = next((i for i, e in enumerate(ordre) if e.get("nom") == courant), -1)
    if idx == -1:
        return ToolResult(text="❌ Combattant courant introuvable.")
    idx += 1
    if idx >= len(ordre):
        idx = 0
        etat["tour"] = (etat.get("tour", 0) or 0) + 1
    vivant = _prochain_vivant(etat, ordre, idx)
    if vivant == -1:
        vivant = idx
    etat["courant_tour_pour"] = ordre[vivant].get("nom", "")
    etat["tour_depuis"] = _dt.now().isoformat()
    err = state.save(etat)
    if err:
        return ToolResult(text=err)
    return ToolResult(
        text=(
            f"➡️ Tour de {courant} terminé — au tour de "
            f"**{etat['courant_tour_pour']}** (round {etat['tour']}). "
            "_Le serveur joue maintenant les monstres et les tours "
            "incapables automatiquement._"
        ),
        state_patch={
            "tour": etat["tour"],
            "courant_tour_pour": etat["courant_tour_pour"],
        },
    )
