"""Constructeur de prompt système — réplique le comportement des 3 filtres
OpenWebUI côté serveur, sans dépendance au framework.

Reproduit la logique de `Filtre_EtatPartie_INJECT.py` :
1. charger SystemPrompt (`modele_md/SystemPrompt_MaitreDuJeu_ALLEGE.md`,
   section entre les balises `## PROMPT SYSTÈME ...`);
2. sélectionner les sections dynamiques selon la `phase` courante (
   `prompts/sections/standard.md` + sections spécifiques) ;
3. construire un `=== RÉCAP DE L'ÉTAT === ... ===` cohérent avec le schéma
   persistant ;
4. (Phase 2) agréger les extraits RAG Knowledge Base en bloc dédié.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from ..config import AppConfig
from ..game.state import PartyState


# --------------------------------------------------------------------------- #
#  Mapping phase -> sections injectées (identique à l'original)
# --------------------------------------------------------------------------- #
_PHASE_SECTIONS: dict[str, list[str]] = {
    "opening":           ["standard", "ouverture"],
    "opening_complete":  ["standard"],
    "combat":            ["standard", "combats", "monstres"],
    "exploration":       ["standard", "exploration"],
    "voyage":            ["standard", "exploration"],
    "roleplay":          ["standard", "exploration"],
    "clôture":           ["standard", "cloture"],
    "cloture":           ["standard", "cloture"],
    "bilan":             ["standard", "cloture"],
}

# Directive injectée dans le récap tant que la quête est choisie mais qu'aucun
# événement d'histoire n'existe : force la scène d'ouverture narrative (décor
# du pitch) et interdit rencontre/image de monstre au premier tour.
_DEBUT_AVENTURE = (
    "⚠️ DÉBUT DE L'AVENTURE — aucun événement d'histoire enregistré. Ce "
    "tour-ci est la SCÈNE D'OUVERTURE : pose le décor AVANT toute "
    "sollicitation, en 4 à 6 paragraphes immersifs — le lieu de départ "
    "(avec le lieu canonique ci-dessus s'il existe : décor, sons, "
    "odeurs), la situation des héros à cet instant, le PNJ principal et "
    "son attitude, la mission avec ses enjeux et son urgence, la remise "
    "d'un objet via l'outil d'inventaire le cas échéant — puis termine "
    "par une invitation OUVERTE à agir (jamais une question fermée "
    "« acceptez-vous ? »). INTERDIT ce tour : combat, monstre, rencontre, "
    "image de monstre, mise en contexte expédiée en trois lignes."
)

_MAX_SALLES_BLOC = 40       # plafond de salles listées dans le bloc donjon
_MAX_DESC_BLOC = 400        # plafond de la description reprise par salle


def _lieu_depart_canonique(etat: dict[str, Any]) -> str:
    """Ligne d'ancrage « lieu de départ canonique » pour la scène d'ouverture.

    Partie c1f4e547 : au premier tour, le petit modèle a improvisé
    l'ouverture dans un donjon inventé (« donjon de Khundrukar », nom repris
    de l'exemple du schéma d'outils) au lieu de suivre le pitch. Quand le
    donjon du scénario est déjà initialisé, on ancre la scène d'ouverture
    sur la salle canonique de départ (visitee=True du plan). Renvoie '' si
    rien n'ancre (donjon absent, ou pas au premier tour)."""
    if etat.get("histoire"):
        return ""
    donjon = etat.get("donjon") or {}
    if not (donjon.get("id") and donjon.get("grille")):
        return ""
    s0 = next(
        (s for s in (donjon.get("grille") or []) if s.get("visitee")),
        None,
    )
    if not (s0 and str(s0.get("description") or "").strip()):
        return ""
    return (
        "LIEU DE DÉPART CANONIQUE — la scène d'ouverture s'y déroule : "
        f"{str(s0.get('type') or '?').strip()} "
        f"({s0.get('x')},{s0.get('y')}) — « "
        f"{str(s0.get('description')).strip()[:_MAX_DESC_BLOC]} » . "
        "N'invente AUCUN autre lieu (pas de « donjon de… » absent d'ici) "
        "et n'y entre PAS : c'est déjà où se trouve le groupe."
    )


def _portes_salle(salle: dict[str, Any]) -> list[str]:
    """Portes OUVERTES d'une salle, dans l'ordre géographique."""
    p = salle.get("portes") or {}
    return [d for d in ("nord", "est", "sud", "ouest") if p.get(d)]


def _salle_visitee(donjon: dict[str, Any], salle: str) -> bool:
    """La salle « x,y » (ou « etage:x,y ») est-elle visitée ? Cherche dans la
    grille courante puis dans tous les étages connus."""
    try:
        txt = str(salle or "").strip().lower().replace("étage:", "")
        parts = [int(p) for p in txt.split(",") if p.strip()]
        if len(parts) < 2:
            return False
        xy = (parts[-2], parts[-1])
    except (TypeError, ValueError):
        return False
    grilles = [donjon.get("grille") or []]
    for fl in (donjon.get("etages") or {}).values():
        if isinstance(fl, dict):
            grilles.append(fl.get("grille") or [])
    for grille in grilles:
        for s in grille:
            if not isinstance(s, dict):
                continue
            try:
                if (int(s.get("x")), int(s.get("y"))) == xy:
                    return bool(s.get("visitee"))
            except (TypeError, ValueError):
                continue
    return False


def _tous_pj_morts(etat: dict[str, Any]) -> bool:
    """Tous les PJ sont-ils morts (≤ -10 PV ou condition « Mort ») ?"""
    pjs = etat.get("pj") or []
    if not pjs:
        return False
    for p in pjs:
        if "Mort" in (p.get("conditions") or []):
            continue
        try:
            if int(p.get("pv", 0) or 0) <= -10:
                continue
        except (TypeError, ValueError):
            pass
        return False
    return True


_BLOC_GAME_OVER = (
    "\n💀 === GAME OVER — TOUS LES HÉROS SONT TOMBÉS ===\n"
    "La partie est TERMINÉE : ne continue PAS la narration « normalement »\n"
    "(pas de donjon, de voyage, de rencontre). Adresse la table et propose\n"
    "UN de ces choix, puis ATTENDS la décision des joueurs :\n"
    "1. 🆕 Nouvelle partie (nouveau groupe et/ou nouveau scénario) ;\n"
    "2. ✨ Résurrection/deus ex machina (un PNJ puissant intervient —\n"
    "   négocie un prix narratif clair) puis reprise de la partie ;\n"
    "3. ⏪ Reprise narrative plus tôt (« en fait, nous avions fui… »).\n"
    "================================================"
)


def _est_game_over(etat: dict[str, Any]) -> bool:
    """Partie terminée ? Flag posé par la clôture de combat (défaite) OU
    constat direct : tous les PJ morts (mort hors combat, piège…)."""
    if etat.get("game_over"):
        return True
    return _tous_pj_morts(etat)


def _donjon_bloc(etat: dict[str, Any]) -> str:
    """Bloc « CARTE DU DONJON » injecté au MJ à chaque tour : salle courante
    (type, description figée, état des lieux), portes réellement ouvertes,
    plan des salles connues. C'est la SOURCE DE VÉRITÉ géographique.

    Sans ce bloc, le petit modèle narratif INVENTAIT portes et salles
    (« une porte au nord » alors que la grille n'en a pas) : la carte se
    désynchronisait de la narration. Ici, la géographie narrée DOIT coller
    au bloc — tout déplacement passe par `carte_donjon_explorer`, qui
    refuse les directions sans porte.
    """
    donjon = etat.get("donjon") or {}
    if not (donjon.get("id") and donjon.get("grille")):
        return ""
    salles: dict[tuple[int, int], dict[str, Any]] = {}
    for s in donjon.get("grille", []):
        if isinstance(s, dict) and "x" in s and "y" in s:
            try:
                salles[(int(s["x"]), int(s["y"]))] = s
            except (TypeError, ValueError):
                continue
    if not salles:
        return ""
    try:
        cr = list(donjon.get("courant") or [0, 0])
        courant = (int(cr[0]), int(cr[1]))
    except (TypeError, ValueError, IndexError):
        courant = (0, 0)
    cur = salles.get(courant) or {}
    try:
        from ..tools.cartes import _nom_etage
        etage_lbl = _nom_etage(int(donjon.get("etage", 0) or 0))
    except Exception:                                        # noqa: BLE001
        etage_lbl = f"étage {donjon.get('etage', 0)}"
    desc_cur = str(cur.get("description") or "").strip()
    if not desc_cur:
        try:
            from ..tools.cartes import _description_secours
            desc_cur = _description_secours(
                str(donjon.get("id") or ""), courant[0], courant[1],
                str(cur.get("type") or "salle"),
            )
        except Exception:                                    # noqa: BLE001
            desc_cur = ""

    lignes = ["\n=== CARTE DU DONJON (source de vérité géographique) ==="]
    lignes.append(
        f"Donjon « {donjon.get('id')} » — étage : {etage_lbl} — "
        f"{len(salles)} salle(s) connue(s)."
    )
    portes_cur = _portes_salle(cur)
    lignes.append(
        f"Salle COURANTE : ({courant[0]},{courant[1]}) — "
        f"type « {cur.get('type', '?')} »."
    )
    if desc_cur:
        lignes.append(
            f"  Description figée (reprends-la à l'identique) : « "
            f"{desc_cur[:_MAX_DESC_BLOC]}"
            + ("…" if len(desc_cur) > _MAX_DESC_BLOC else "") + " »"
        )
    edl = str(cur.get("etat_des_lieux") or "").strip()
    if edl:
        lignes.append(f"  État des lieux : {edl[:_MAX_DESC_BLOC]}")
    # Partie 6746fc6c : le MJ inventait un PNJ masqué et une « première clé »
    # absents du module. La description figée ci-dessus est EXHAUSTIVE.
    lignes.append(
        "  ⚠️ Ce qui précède est la SEULE réalité de la salle : n'invente NI "
        "PNJ NI clé NI objet de quête NI créature absents de ce bloc et du "
        "manifeste du scénario."
    )
    lignes.append(
        "  Portes EXISTANTES : "
        + (", ".join(portes_cur) if portes_cur else "AUCUNE (cul-de-sac)")
    )
    arr = str(donjon.get("arrivee_par") or "").strip().lower()
    if arr in ("nord", "est", "sud", "ouest"):
        lignes.append(
            f"  ⬅️ Le groupe y est ENTRÉ par la porte {arr.upper()} : c'est "
            "d'où il VIENT — ne la décris JAMAIS comme une sortie ni comme "
            "menant à un lieu inconnu."
        )
    try:
        from ..tools.cartes import _portes_detaillees
        lignes.extend(_portes_detaillees(donjon, cur))
    except Exception:                                        # noqa: BLE001
        pass
    lignes.append("Plan des salles connues (x,y — type — portes ouvertes) :")
    for xy in sorted(salles)[:_MAX_SALLES_BLOC]:
        s = salles[xy]
        marque = "  ← VOUS ÊTES ICI" if xy == courant else ""
        lignes.append(
            f"  - ({xy[0]},{xy[1]}) {s.get('type', '?')} — portes : "
            f"{', '.join(_portes_salle(s)) or 'aucune'}{marque}"
        )
    lignes.append(
        "⚠️ RESPECT STRICT DE LA CARTE : seules les portes listées EXISTENT. "
        "N'invente JAMAIS une porte, un passage ou une salle absents de ce "
        "plan ; n'affirme une sortie au nord/sud/est/ouest que si la porte "
        "est listée. Tout déplacement passe par `carte_donjon_explorer`, qui "
        "refuse les directions sans porte. Une salle revisitée se narre "
        "d'après sa description figée — jamais réinventée."
    )
    # 📜 Trame du scénario (manifeste) : l'ordre attendu des étapes. Sans ce
    # rappel, le MJ sautait des prérequis (partie 87b8f286 : voyage vers la
    # gemme de Sarr ALORS QUE la Couronne n'avait pas été récupérée (4,0)).
    etapes_trame = donjon.get("etapes") or []
    if etapes_trame:
        lignes.append("📜 TRAME DU SCÉNARIO (ordre à respecter) :")
        for i, e in enumerate(etapes_trame, 1):
            if not isinstance(e, dict):
                continue
            statut = ""
            salle = str(e.get("salle") or "").strip()
            if salle:
                statut = (
                    " — ✅ ACCOMPLIE" if _salle_visitee(donjon, salle)
                    else " — ⬜ À FAIRE (salle " + salle + ")"
                )
            detail = str(e.get("detail") or "").strip()
            lignes.append(
                f"  {i}. {e.get('titre', '?')}{statut}"
                + (f" — {detail}" if detail else "")
            )
        lignes.append(
            "⚠️ SUIVIS CET ORDRE : ne commence pas une étape ultérieure (ni "
            "un voyage vers une locale future) tant que la première étape "
            "⬜ n'est pas ACCOMPLIE. Consigne la progression au fil du jeu "
            "via `scenario_etape(etape=…, terminée=true)`."
        )
    try:
        from ..tools.cartes import _TYPES_ESCALIER
        if (cur.get("type") or "").strip().lower() in _TYPES_ESCALIER:
            lignes.append(
                "🪜 ESCALIER dans la salle COURANTE : « monter/descendre "
                "l'escalier » = CHANGEMENT D'ÉTAGE via `carte_donjon_etage("
                "direction=\"monter\"|\"descendre\")` — JAMAIS "
                "`carte_donjon_explorer`. « Descendre » n'est PAS « aller "
                "au sud » : ce sont deux actions différentes."
            )
    except Exception:                                        # noqa: BLE001
        pass
    lignes.append("===============================================")
    return "\n".join(lignes)


def _ennemis_donjon(etat: dict[str, Any]) -> dict[str, Any]:
    """Créatures listées dans les salles du donjon courant.

    Le manifeste de scénario (`<nom>.donjon.json`) porte le contenu canonique
    de chaque salle (`ennemis: ["Squelette ×4", …]`) — souvent PLUS fidèle
    que `bible.ennemis`, qui dérive d'un extrait PDF plafonné (abd81275 :
    la bible ne détectait que « Squelette » alors que le module met en scène
    gobelins, goules, rats, ombres et un nécromancien rouge ; le MJ
    improvisait donc des rencontres hors scénario, ex. un loup-garou).

    Renvoie `{"places": {nom_normalisé: (nom_affiché, [salles "x,y"])}}` :
    l'annotation de SALLE injectée dans le prompt empêche le MJ de faire
    surgir un ennemi d'ENDGAME au premier étage (a6d11005 : une Ombre FP 3
    de la salle (0,3) attaquée dans le temple d'entrée contre un barbare
    niv 1)."""
    places: dict[str, tuple[str, list[str]]] = {}
    for s in ((etat.get("donjon") or {}).get("grille") or []):
        if not isinstance(s, dict):
            continue
        try:
            salle = f"{int(s['x'])},{int(s['y'])}"
        except (KeyError, TypeError, ValueError):
            continue
        for e in (s.get("ennemis") or []):
            # « Squelette ×4 » / « Gobelin x2 » → « Squelette » / « Gobelin ».
            nom = re.sub(r"\s*[×xX*]\s*\d+\s*$", "", str(e or "")).strip()
            if not nom:
                continue
            cle = nom.lower()
            if cle not in places:
                places[cle] = (nom, [])
            if salle not in places[cle][1]:
                places[cle][1].append(salle)
    return {"places": places}


def _scenario_bible_bloc(
    quete: dict[str, Any], etat: dict[str, Any] | None = None,
    data_dir: str = "", partie_id: str = "",
) -> str:
    """Bloc « SCÉNARIO (bible) » injecté au MJ à chaque tour : la trame du
    scénario (accroche, PNJ, objectifs, étapes en cours/accomplies) et
    l'avertissement d'édition. Suffisant pour que le MJ reste sur la trame
    même quand l'historique est tronqué.

    Avec `etat`, les ennemis canoniques des salles du donjon (manifeste)
    sont fusionnés à `bible.ennemis` — la liste d'ancrage des rencontres."""
    bible = (quete or {}).get("bible") or {}
    if not bible:
        return ""
    lignes = ["\n=== SCÉNARIO (bible) — reste sur cette trame ==="]
    # Avertissement d'édition / difficulté (cohérence 3.5).
    if bible.get("avertissement"):
        lignes.append(bible["avertissement"])
    if bible.get("niveau_recommande"):
        lignes.append(
            f"Niveaux recommandés : {bible['niveau_recommande']}"
            + (f" — joueurs : {bible['joueurs_recommandes']}"
               if bible.get("joueurs_recommandes") else "")
        )
    if bible.get("resume"):
        lignes.append(f"Résumé du scénario : {bible['resume'][:2000]}")
    ennemis = list(bible.get("ennemis") or [])
    if etat is not None:
        # Fusion (sans doublon) avec les ennemis du donjon — annotés de LEUR
        # SALLE : le MJ doit les rencontrer là où le module les place, pas
        # les semer n'importe où (a6d11005 : Ombre d'endgame dans le temple
        # d'entrée contre un barbare niv 1).
        places = _ennemis_donjon(etat).get("places") or {}
        vus = {str(n).strip().lower() for n in ennemis}
        for _cle, (nom, salles) in places.items():
            if nom.lower() in vus:
                continue
            vus.add(nom.lower())
            if salles:
                lbl = (
                    f"{nom} (salles {', '.join(salles)})"
                    if len(salles) > 1
                    else f"{nom} (salle {salles[0]})"
                )
            else:
                lbl = nom
            ennemis.append(lbl)
        if places:
            lignes.append(
                "📍 PLACEMENT : chaque ennemi du donjon ci-dessous est "
                "annoté de SA(S) salle(s) — ne le fait PAS surgir ailleurs "
                "(une salle sans ennemi listé n'a PAS de rencontre prévue "
                "par le module ; une rencontre de voyage aléatoire reste "
                "possible hors donjon)."
            )
    if not ennemis and bible.get("resume"):
        # Repli : bibles persistées AVANT l'ajout de la détection (liste
        # vide) — on recalcule les ennemis du scénario à chaque tour
        # (bestiaire × résumé FR/EN). Sans cela, aucune liste d'ennemis
        # officiels n'ancrait le MJ, qui improvisait des créatures hors
        # scénario (observé en partie réelle : dragon rouge FP 7 substitué
        # au nécromancien d'un module de niveau 1).
        try:
            from ..tools.scenarios import ennemis_du_resume
            ennemis = ennemis_du_resume(str(bible["resume"]))
            if ennemis:
                bible["ennemis"] = ennemis
        except Exception:                                        # noqa: BLE001
            ennemis = bible.get("ennemis") or []
    if ennemis:
        lignes.append(
            "Ennemis/monstres DU SCÉNARIO (à utiliser EN PRIORITÉ pour toute "
            "rencontre) : " + ", ".join(ennemis)
        )
    # ⚠️ CRÉATURES DÉJÀ AFFRONTÉES — le petit modèle (9B) retombe sur le même
    # monstre générique du bestiaire pour « remplir » les tours de rencontre
    # (partie 5b4e2bbe : 6 Gobelins d'affilée après le Squelette du module,
    # monstres_combattus témoigne). Rappeler les affrontements passés empêche
    # la table d'embrayer sur une énième copie carbone du même combat.
    if etat is not None:
        _mcomb = (etat.get("memoire") or {}).get("monstres_combattus") or []
        if isinstance(_mcomb, list):
            _vus: dict[str, int] = {}
            for _ec in _mcomb:
                if not isinstance(_ec, dict):
                    continue
                _ns = _ec.get("noms") or []
                for _n in _ns if isinstance(_ns, list) else [_ns]:
                    if isinstance(_n, str) and _n.strip():
                        _cle_v = str(_n).strip().lower()
                        _vus[_cle_v] = _vus.get(_cle_v, 0) + 1
            if _vus:
                _deja_txt = ", ".join(
                    f"{_k.capitalize()} (×{_c})"
                    for _k, _c in sorted(_vus.items())
                )
                lignes.append(
                    "⚠️ CRÉATURES DÉJÀ AFFRONTÉES dans CETTE partie : "
                    + _deja_txt + ". Ne RELANCE PAS la table dans une "
                    "énième rencontre identique contre ces combattants pour "
                    "« remplir » un tour : fais AVANCER la trame (PNJ, "
                    "indice, épreuve, découverte, retournement — le module "
                    "en regorge). Un rencontre originale ponctuelle reste "
                    "possible, mais plus jamais le même monstre répété."
                )
    objectif = bible.get("objectif") or bible.get("etape_courante") or ""
    if objectif:
        lignes.append(f"Objectif courant (étape en cours) : {objectif}")
    etapes_faites = bible.get("etapes_terminees") or []
    if etapes_faites:
        lignes.append(
            "Étapes accomplies : "
            + ", ".join(etapes_faites[-6:])
        )
    # Campagne en chapitres : rappel du cadre + résumé des chapitres déjà
    # joués + consigne d'enchaînement (réinjectés à CHAQUE tour — c'est ce
    # qui fait suivre les chapitres automatiquement dans la même partie).
    if bible.get("chapitre"):
        _tot = bible.get("chapitre_total") or "?"
        _camp = bible.get("campagne") or bible.get("titre") or "campagne"
        lignes.append(
            f"📌 CAMPAGNE « {_camp} » — chapitre "
            f"{bible['chapitre']}/{_tot}. Reste sur le CONTENU de ce "
            f"chapitre : n'anticipe pas les chapitres suivants."
        )
        _prec = bible.get("chapitres_precedents") or []
        if _prec:
            _recaps = []
            for _p in _prec[-4:]:
                _t = str(_p.get("titre") or "?")
                _e = [_x for _x in (_p.get("etapes") or []) if _x][-3:]
                _recaps.append(
                    _t + (" (fait : " + ", ".join(map(str, _e)) + ")" if _e else "")
                )
            lignes.append(
                "Continuité — chapitres déjà joués : " + " | ".join(_recaps)
            )
        _suiv = str(bible.get("chapitre_suivant") or "")
        if _suiv:
            lignes.append(
                "→ FIN DE CHAPITRE ATTEINTE ? Dès que l'objectif de ce "
                "chapitre est accompli (et seulement à ce moment), appelle "
                f"`scenarios_laelith_charger(scenario_id=\"{_suiv}\")` pour "
                "charger le chapitre suivant dans la MÊME partie."
            )
        else:
            lignes.append(
                "→ DERNIER chapitre de la campagne : joue le dénouement "
                "jusqu'au bout (aucun chapitre suivant)."
            )
    if not objectif and not etapes_faites:
        # 📌 Aucun suivi : la partie dérivait hors trame sans garde-fou
        # (partie 87b8f286 — zéro étape consignée, séquence du module rompue).
        lignes.append(
            "📌 SUIVI DE SCÉNARIO VIDE : appelle MAINTENANT "
            "`scenario_etape(etape=\"…\")` pour consigner l'étape en cours "
            "de la trame, et `scenario_etape(etape=\"…\", terminée=true)` à "
            "chaque étape accomplie. Ce journal est réinjecté à chaque tour : "
            "c'est lui qui empêche de dévier."
        )
    # 🎯 OBJECTIFS DE QUÊTE (mécanique serveur — game/objectifs) : statuts
    # recalculés À FROID (objets requis de l'inventaire de quête de la partie
    # + salles visitées + étapes clôturées). La mécanique VERROUILLE la suite
    # du module tant que les objets requis ne sont pas dans l'inventaire de
    # quête des PJ ; l'onglet client affiche exactement ce même suivi.
    if etat is not None and data_dir:
        try:
            from ..game.objectifs import objectifs_quete as _objectifs_quete  # pylint: disable=import-outside-toplevel
            _info_o = _objectifs_quete(etat, data_dir, partie_id=partie_id)
            _objs_o = _info_o.get("objectifs") or []
            if _objs_o:
                _lbl_o = {
                    "complet": "✅ ACCOMPLI",
                    "en_cours": "🔵 en cours",
                    "bloque": "⛔ BLOQUÉ (objets requis manquants)",
                    "a_venir": "⚪ à venir",
                }
                lignes.append(
                    "\n🎯 OBJECTIFS DE QUÊTE (mécanique serveur — requis) :"
                )
                lignes.append(
                    f"   Progression : {_info_o.get('progression', '')} — "
                    f"objectif courant : "
                    f"{_info_o.get('objectif_courant') or '(tous accomplis)'}"
                )
                for _o in _objs_o:
                    _req_o = _o.get("requis") or []
                    _req_txt = ""
                    if _req_o:
                        _req_txt = "requis : " + "; ".join(
                            f"« {r.get('nom')} » "
                            f"{'✅' if r.get('present') else '❌'}"
                            + (f" (porteur : {r.get('porteur')})"
                               if r.get('present') and r.get('porteur') else "")
                            for r in _req_o
                        )
                        if _o.get("xp") and _o.get("statut") != "complet":
                            _req_txt += (
                                f" — récompense d'histoire DMG 3.5 : {_o['xp']} "
                                "XP à attribuer via `fiche_perso_gagner_xp` "
                                "quand cet objectif passe à ACCOMPLI."
                            )
                    lignes.append(
                        f"   - {_o.get('titre','?')} — "
                        + _lbl_o.get(_o.get("statut", ""), str(_o.get("statut") or ""))
                        + (f" — {_req_txt}" if _req_txt else "")
                    )
                _manq_o = _info_o.get("manquants") or []
                if _manq_o:
                    lignes.append(
                        "   ⛔ Objets REQUIS manquants à l'inventaire de quête "
                        "(de la partie courante) : " + ", ".join(_manq_o)
                        + " — QUELLE que soit la tentation, ne fais PAS "
                        "franchir la suite du module sans ces objets ; dès que "
                        "le groupe les obtient, persiste-les via "
                        "`inventaire_ajouter(nom=\"<PJ>\", objet=\"<nom exact>\", "
                        "portee=\"quete\")` et la mécanique débloque alors la "
                        "progression."
                    )
        except Exception:                                        # noqa: BLE001
            pass
    lignes.append(
        "→ FIDÉLITÉ AU SCÉNARIO : les PNJ, lieux et organisations du résumé "
        "font foi — reprends leurs noms EXACTS. N'invente NI village, NI PNJ, "
        "NI faction, NI créature hors scénario : les rencontres doivent "
        "correspondre aux ennemis listés ci-dessus (une créature hors liste "
        "n'est acceptable que comme rencontre de voyage aléatoire explicite)."
    )
    lignes.append(
        "→ Chaque tour, lie une action des PJ à cet objectif. Ne dérive pas "
        "hors-sujet : si l'action s'éloigne, ramène-la vers la trame (sans "
        "forcer brutalement : respecte les choix des PJ). Mets à jour "
        "`scenario_etape` quand une étape est franchie."
    )
    return "\n".join(lignes)


def _sexe_libelle(sexe_brut: str) -> str:
    """Normalise un sexe stocké (« M », « F », « Autre », « f »…) en libellé."""
    s = (sexe_brut or "").strip().lower()
    if s.startswith("m"):
        return "Masculin"
    if s.startswith("f"):
        return "Féminin"
    if s.startswith("a"):
        return "Autre"
    return ""


def _genre_pj(data_dir: str, p: dict[str, Any]) -> str:
    """Genre d'un PJ : d'abord dans l'entrée d'état (`apparence.sexe`), sinon
    lu dans la fiche sur disque — le récapitulatif des PJ (`etat["pj"]`) ne
    transporte pas l'apparence. Retourne un libellé ou une chaîne vide."""
    app = p.get("apparence")
    sexe_brut = str(app.get("sexe") or "") if isinstance(app, dict) else ""
    if not sexe_brut:
        nom = str(p.get("nom") or "").strip()
        if nom and data_dir:
            try:
                from ..persos import charger_fiche
                fiche = charger_fiche(data_dir, nom) or {}
                app = fiche.get("apparence") or {}
                sexe_brut = str(app.get("sexe") or "")
            except Exception:                                   # noqa: BLE001
                return ""
    return _sexe_libelle(sexe_brut)


def _dons_competences_pj(data_dir: str, nom: str) -> str:
    """Dons + rangs de compétences d'un PJ, lus dans sa fiche sur disque —
    l'entrée `pj` de l'état ne transporte ni les dons ni les rangs. Sans
    cette ligne, le MJ ignore les choix faits à la création. Renvoie ''
    si fiche absente ou sans dons/rangs (fail-safe)."""
    if not nom or not data_dir:
        return ""
    try:
        from ..persos import charger_fiche, resume_dons_competences
        return resume_dons_competences(charger_fiche(data_dir, nom) or {})
    except Exception:                                        # noqa: BLE001
        return ""


def _inventaire_pj(data_dir: str, nom: str, partie_id: str = "") -> str:
    """Résumé compact du sac d'un PJ, lu dans sa fiche sur disque — l'entrée
    `pj` de l'état ne transporte PAS l'inventaire. Sans cette ligne, le MJ
    ignore le contenu réel du sac et peut nier un objet pourtant porté
    (bug « tu n'as pas de fiole de guérison » alors que le PJ en a 4).
    `partie_id` filtre l'inventaire de quête : seuls les objets de quête de
    CETTE partie sont listés (une fiche réutilisée dans plusieurs parties
    garde l'inventaire de quête de chacune).
    Renvoie '' si fiche absente ou sans inventaire (fail-safe)."""
    if not nom or not data_dir:
        return ""
    try:
        from ..persos import charger_fiche, resume_inventaire
        return resume_inventaire(
            charger_fiche(data_dir, nom) or {}, partie_id=partie_id
        )
    except Exception:                                        # noqa: BLE001
        return ""


def _fiche_pj_lignes(data_dir: str, nom: str, partie_id: str = "") -> list[str]:
    """Détails officiels d'un PJ lus dans sa fiche sur disque : sac,
    dons/compétences, sorts. L'entrée `pj` de l'état n'en transporte aucun.
    Renvoie une liste de lignes (préfixées par l'appelant), vide si fiche
    absente (fail-safe)."""
    if not nom or not data_dir:
        return []
    out: list[str] = []
    sac = _inventaire_pj(data_dir, nom, partie_id)
    if sac:
        out.append(sac)
    dons = _dons_competences_pj(data_dir, nom)
    if dons:
        out.append(dons)
    try:
        from ..persos import charger_fiche
        from ..sorts import resume_sorts
        sorts = resume_sorts(charger_fiche(data_dir, nom) or {})
    except Exception:                                        # noqa: BLE001
        sorts = ""
    if sorts:
        out.append(sorts)
    return out


# --------------------------------------------------------------------------- #
#  Extraction du SystemPrompt
# --------------------------------------------------------------------------- #
def extract_system_prompt(raw_md: str) -> str:
    """Renvoie le contenu du SystemPrompt tel que marqué par la balise.

    Le fichier source contient une section « ## PROMPT SYSTÈME (à coller
    intégralement) » suivie du prompt, puis des notes (« Phase de jeu —
    sections dynamiques »). On garde tout entre la balise d'ouverture et la
    première ligne `## ` qui suit.
    """
    marker = "## PROMPT SYSTÈME"
    idx = raw_md.find(marker)
    if idx == -1:
        # Pas de balise : on retourne tout (fallback).
        return raw_md.strip()
    after = raw_md[idx + len(marker) :]
    # On retire la fin de ligne du marker (ex. "(à coller intégralement)")
    nl = after.find("\n")
    if nl != -1:
        after = after[nl + 1 :]
    # On s'arrête à la prochaine section `## ` (hors titres inline).
    end = after.find("\n## ")
    body = after if end == -1 else after[:end]
    return body.strip()


# --------------------------------------------------------------------------- #
#  Builder
# --------------------------------------------------------------------------- #
class PromptBuilder:
    """Construit le message système injecté à chaque appel au LLM."""

    def __init__(self, cfg: AppConfig, Registry=None):
        self.cfg = cfg
        self.prompts_dir = cfg.abs(cfg.paths.prompts_dir)
        self.sections_dir = cfg.abs(cfg.paths.sections_dir)
        self._system_prompt: Optional[str] = None
        self._system_prompt_opening: Optional[str] = None
        self._system_prompt_exploration: Optional[str] = None

    # ------------------------------------------------------------------ #
    def system_prompt(self) -> str:
        """Charge et cache le SystemPrompt (version allégée recommandée)."""
        if self._system_prompt is None:
            path = self.prompts_dir / "SystemPrompt_MaitreDuJeu_ALLEGE.md"
            if not path.is_file():
                # Fallback : l'ancienne version (346 lignes) — à éviter.
                path = self.prompts_dir / "SystemPrompt_MaitreDuJeu.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                self._system_prompt = extract_system_prompt(raw)
            else:
                # Aucun prompt installé : placeholder minimal.
                self._system_prompt = (
                    "Tu es un Maître du Jeu de Donjons & Dragons 3.5, "
                    "narrateur et arbitre impartial, qui répond en français. "
                    "(SystemPrompt non trouvé — vérifier le déploiement de "
                    "server/prompts/)."
                )
        return self._system_prompt

    # ------------------------------------------------------------------ #
    def system_prompt_opening(self) -> str:
        """Variante courte (~1,5 ko) du system prompt, dédiée à la phase
        d'ouverture sans PJ créé. Maximise le signal tool-calling pour Gemma
        (le prompt complet noie l'instruction dans ~5 k tokens).
        """
        if self._system_prompt_opening is None:
            path = self.prompts_dir / "SystemPrompt_OUVERTURE_COURT.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                # Le fichier commence par un titre H1 de doc, puis un bloc
                # explicatif destiné au lecteur humain — on injecte tout ce
                # qui suit la première ligne `---` séparatrice.
                sep = raw.find("\n---\n")
                body = raw[sep + len("\n---\n") :] if sep != -1 else raw
                self._system_prompt_opening = body.strip()
            else:
                # Fallback inline : on garde une version minimale même si
                # le fichier est manquant.
                self._system_prompt_opening = (
                    "Tu es le Maître du Jeu d'une partie de D&D 3.5, en "
                    "français. Pour tout jet de dés, création de perso ou "
                    "persistance d'état, tu DOIS appeler un tool via le "
                    "mécanisme `tool_calls` natif du payload. Le résultat "
                    "du tool est la seule source de vérité. INTERDIT : "
                    "écrire *(Simulation de l'appel ...)*. Ouverture : "
                    "présente-toi en une phrase, demande prénom+race+classe, "
                    "appelle etat_partie_patch puis lancer_caracteristiques"
                    "(methode=\"4d6_garder_3\"). Sois bref."
                )
        return self._system_prompt_opening

    # ------------------------------------------------------------------ #
    def system_prompt_exploration(self) -> str:
        """Variante courte dédiée aux phases post-ouverture (`opening_complete`
        ou `exploration`) où un PJ existe déjà. Le prompt complet (13,6 ko)
        noie le signal d'appel d'outil dès qu'on dépasse le tout premier tour
        — cette variante garde le régime « action immédiate » en couvrant
        fiches perso, monstres, donjon, combat, scénarios.
        """
        if self._system_prompt_exploration is None:
            path = self.prompts_dir / "SystemPrompt_EXPLORATION_COURT.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                sep = raw.find("\n---\n")
                body = raw[sep + len("\n---\n") :] if sep != -1 else raw
                self._system_prompt_exploration = body.strip()
            else:
                # Fallback inline minimal.
                self._system_prompt_exploration = (
                    "Tu es le Maître du Jeu d'une partie de D&D 3.5, en "
                    "français. Le personnage du joueur est déjà créé : ne te "
                    "re-présente pas et ne rejoue pas l'intro. Pour tout jet "
                    "de dés, rencontre de monstre, exploration de donjon, "
                    "fiche perso ou persistance d'état, tu DOIS appeler un "
                    "tool via `tool_calls`. INTERDIT : simuler un appel ou "
                    "raconter un résultat de dés sans tool. Outils clés "
                    "exploration : monstre_consulter, carte_donjon_entrer / "
                    "_explorer / _get / _sortir, fiche_perso_creer / "
                    "_recuperer, demarrer_combat / tour_suivant_combat / "
                    "finir_combat. La quête est choisie dans l'interface — ne "
                    "liste jamais de scénarios. "
                    "Sois bref : 2-4 paragraphes par tour, finis par une "
                    "invitation à agir."
                )
        return self._system_prompt_exploration

    # ------------------------------------------------------------------ #
    def _load_section(self, name: str) -> str:
        path = self.sections_dir / f"{name}.md"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def select_sections(self, phase: str) -> str:
        """Construit le bloc des sections dynamiques pour une phase donnée."""
        phase_norm = (phase or "").strip().lower()
        names = _PHASE_SECTIONS.get(phase_norm)
        if names is None:
            # défaut prudent : standard + ouverture
            names = ["standard", "ouverture"]
        blocs = [b for b in (self._load_section(n) for n in names) if b]
        return "\n\n---\n\n".join(blocs) if blocs else ""

    # ------------------------------------------------------------------ #
    def build_recap(
        self, etat: dict[str, Any], partie_id: str = ""
    ) -> str:
        """Construit le récapitulatif de l'état.

        En phase d'ouverture sans PJ créé, on produit un récap **minimal**
        orienté action — allège le contexte pour que le LLM déclenche les tools
        plutôt que de noyer l'instruction dans 6 ko de récap. Sinon on
        reproduit le récap complet (PJ, initiative, lieu, donjon, etc.).
        """
        if not etat or "_erreur" in etat:
            return (
                "=== ÉTAT DE LA PARTIE ===\n"
                "(Aucun état. C'est le tout premier message : déroule l'ouverture "
                "UNE seule fois selon la section dynamique « ouverture ». Persiste "
                "chaque choix via `etat_partie_patch` au fur et à mesure.)\n"
                "========================"
            )

        data_dir = str(self.cfg.abs(self.cfg.paths.data_dir))

        # 💀 Game over (flag posé par la clôture de combat OU tous les PJ
        # morts) : le bloc prime sur tout le reste du récap.
        go = _est_game_over(etat)

        # État neuf en phase d'opening : récap minimal (allègement Gemma).
        phase = (etat.get("phase") or "").strip().lower()
        pj = etat.get("pj") or []
        # Si un PJ existe déjà mais qu'on est toujours en phase « opening »
        # (cas fréquent : le MJ a patché pj.0.nom etc. mais a oublié de
        # déplacer `phase` à `opening_complete`), on reste en récap minimal :
        # la party n'a pas d'initiative, pas de quête, etc. — le récap
        # complet serait surtout du bruit.
        if (phase in ("opening", "") or not phase) or (
            pj and phase in ("opening", "opening_complete", "exploration")
        ):
            meta = etat.get("meta", {}) or {}
            titre = meta.get("titre", "")
            distrib = (meta.get("distribution_faite") or False)
            pjs_sum = (
                "0 PJ créé."
                if not pj
                else f"{len(pj)} PJ créé(s) : "
                     + ", ".join(
                         f"{p.get('nom','?')} ({p.get('race','?')} "
                         f"{p.get('classe','?')}, genre "
                         f"{_genre_pj(data_dir, p) or '?'}, "
                         f"PV {p.get('pv','?')}/{p.get('pv_max','?')}, "
                         f"CA {p.get('ca','?')})" for p in pj
                     )
            )
            lignes = [
                "=== ÉTAT DE LA PARTIE ===" if not pj
                else "=== ÉTAT DE LA PARTIE (PJ créé, suite de l'aventure) ===",
                f"Partie : {titre or '(sans-titre)'} — phase : {phase or 'opening'} — {pjs_sum}",
                f"Distribution manuels : {'FAITE (ne PAS redistribuer)' if distrib else 'PAS ENCORE FAITE'}.",
            ]
            # Fiches PJ (valeurs officielles) : sac, dons/rangs, sorts. Sans
            # elles, le MJ ignore ce que porte réellement le PJ et peut nier
            # un objet pourtant présent (bug « pas de fiole de guérison » ×4).
            for p in pj:
                det = _fiche_pj_lignes(
                    data_dir, str(p.get("nom") or ""), partie_id
                )
                if det:
                    lignes.append(
                        f"  · {p.get('nom','?')} — " + " · ".join(det)
                    )
            if pj:
                lignes.append(
                    "  (Sac listé = contenu OFFICIEL de l'inventaire : ne dis "
                    "JAMAIS qu'un objet listé est absent ; pour l'utiliser, "
                    "appelle le tool avec `source=\"<nom exact listé>\"`. "
                    "L'inventaire de quête listé n'existe que dans CETTE "
                    "partie — n'apparaisse jamais dans une autre.)"
                )
            if go:
                lignes.append(_BLOC_GAME_OVER)
            # Quête choisie via l'interface — le MJ doit la connaître même en
            # récap minimal, avec la directive de scène d'ouverture au besoin.
            quete_min = etat.get("quete", {}) or {}
            if (quete_min.get("titre") or "").strip():
                lignes.append(
                    f"Quête en cours : {quete_min.get('titre', '(sans titre)')} "
                    f"— {quete_min.get('pitch', '')}"
                )
                if not (etat.get("histoire") or []):
                    lignes.append(_DEBUT_AVENTURE)
                    _ancre = _lieu_depart_canonique(etat)
                    if _ancre:
                        lignes.append(_ancre)
            bible_min = _scenario_bible_bloc(
                quete_min, etat=etat, data_dir=data_dir, partie_id=partie_id)
            if bible_min:
                lignes.append(bible_min)
            # Carte du donjon (source de vérité géographique) : sans elle,
            # le MJ inventait portes et salles — la carte se désynchronisait.
            donjon_min = _donjon_bloc(etat)
            if donjon_min:
                lignes.append(donjon_min)
            lignes += [
                "",
                "Pour progresser maintenant : appelle les outils adéquats "
                "(lancer_caracteristiques, fiche_perso_creer, etat_partie_patch, "
                "monstre_consulter, carte_donjon_entrer, carte_donjon_explorer…) "
                "— ne raconte pas simplement le résultat et NE TE RE-PRÉSENTE PAS.",
                "================================================",
            ]
            return "\n".join(lignes)

        # État riche (partie en cours) : récap complet.
        lignes = ["=== ÉTAT DE LA PARTIE (mémoire persistante) ==="]
        meta = etat.get("meta", {})
        if meta:
            lignes.append(
                f"Partie : {meta.get('titre', '(sans-titre)')} — "
                f"cadre : {meta.get('cadre', 'Côte des Épées')} — "
                f"règles : {meta.get('regles', 'D&D 3.5')}"
            )

        phase = etat.get("phase", "inconnue")
        lignes.append(f"Phase actuelle : {phase}")
        if go:
            lignes.append(_BLOC_GAME_OVER)

        pjs = etat.get("pj", []) or []

        if phase == "combat":
            tour = etat.get("tour", 1)
            lignes.append(f"Tour de combat : {tour}")
            initiative = etat.get("initiative", []) or []
            if initiative:
                ordre = ", ".join(
                    f"{e.get('nom','?')} ({e.get('init','?')})" for e in initiative
                )
                lignes.append(f"Ordre d'initiative : {ordre}")
            courant = etat.get("courant_tour_pour")
            if courant:
                pj_courant = next(
                    (p for p in pjs if p.get("nom") == courant), None
                ) if pjs else None
                qui = (
                    f"{courant} (joueur : {pj_courant.get('joueur')})"
                    if pj_courant is not None
                    else f"{courant} (PNJ/monstre)"
                )
                lignes.append(f"C'est au tour de : {qui}")
                lignes.append(
                    "Résous UNIQUEMENT les actions déclarées par CE joueur. "
                    "La rotation des tours, les monstres, la clôture et l'XP "
                    "sont gérées automatiquement par le serveur — n'appelle "
                    "ni tour_suivant_combat ni finir_combat."
                )
                lignes.append(
                    "OUTILS DE RÉSOLUTION OBLIGATOIRES : une attaque armée se "
                    "résout par `lancer_attaque` puis, si touché, `lancer_degats` "
                    "(JAMAIS `lancer_d20` pour une attaque). N'annonce JAMAIS un "
                    "jet, un « touché », un montant de dégâts ni une mort "
                    "(« s'effondre », « hors de combat ») sans que l'outil "
                    "correspondant ait produit ce résultat — n'invente aucun dé. "
                    "Le bonus de DÉGÂTS à passer à `lancer_degats` est celui "
                    "affiché « 💪 Bonus dégâts officiel » par `lancer_attaque` : "
                    "recopie-le TEL QUEL (jamais un bonus improvisé), et ne "
                    "change JAMAIS le total de PV d'un personnage en prose — "
                    "recopie les PV des événements serveur à l'identique."
                )
                lignes.append(
                    "UNE SEULE attaque par tour (sauf attaques multiples "
                    "explicitement accordées par la fiche/niveau) : n'appelle "
                    "`lancer_attaque` qu'une fois. `terminer_mon_tour` : AU PLUS "
                    "une fois par tour, et seulement si le joueur renonce "
                    "explicitement à agir — ne le spamme jamais."
                )
            # ⚔️ Ennemis engagés : SANS ce bloc, le MJ ne connaissait pas les
            # combattants réels (PV/conditions) et en INVENTAIT (« squelette
            # géant » inexistant, partie fa4e7366) ou attaquait des cadavres —
            # le combat ne pouvait plus se conclure.
            monstres_c = etat.get("monstres_combat") or []
            if monstres_c:
                lignes.append("\nEnnemis engagés (SOURCE DE VÉRITÉ — n'invente "
                              "AUCUN autre adversaire) :")
                for m in monstres_c:
                    detruit = (
                        "Détruit" in (m.get("conditions") or [])
                        or int(m.get("pv", 1) or 0) <= 0
                    )
                    lignes.append(
                        f"  - {m.get('nom', '?')} : "
                        f"{m.get('pv', '?')}/{m.get('pv_max', '?')} PV — "
                        f"CA {m.get('ca', '?')}"
                        + (" — ☠️ DÉTRUIT (cible INVALIDE)" if detruit else "")
                    )
                vivants = [
                    str(m.get("nom") or "")
                    for m in monstres_c
                    if "Détruit" not in (m.get("conditions") or [])
                    and int(m.get("pv", 1) or 0) > 0
                ]
                if vivants:
                    lignes.append(
                        "  → Cibles VALIDES : " + ", ".join(vivants)
                        + ". Utilise ces noms EXACTS."
                    )
                else:
                    lignes.append(
                        "  → Tous les ennemis sont détruits : narre la fin du "
                        "combat, le serveur clôture (XP)."
                    )
        # Mémoire de campagne (missions, lieux, PNJ, combats, position) :
        # injectée automatiquement — le MJ la CONNAÎT sans tool de lecture.
        try:
            from ..tools.memoire import memoire_resume
            bloc_memoire = memoire_resume(etat)
            if bloc_memoire:
                lignes.append("\n=== MÉMOIRE DE CAMPAGNE ===\n" + bloc_memoire)
        except Exception:                                       # noqa: BLE001
            pass
        voyage = etat.get("voyage") or {}
        if voyage:
            lignes.append(
                f"\nVoyage en cours : {voyage.get('resume', '?')} "
                f"(jours avec rencontre : "
                f"{', '.join(str(j) for j in voyage.get('jours_rencontres', []) or []) or 'aucun'})"
            )

        pjs = etat.get("pj", []) or []
        if pjs:
            lignes.append("\nPersonnages Joueurs (nom — genre — joueur qui le joue) :")
            for p in pjs:
                lignes.append(
                    f"  - {p.get('nom','?')}: {p.get('race','?')} "
                    f"{p.get('classe','?')} niv.{p.get('niveau','?')} — "
                    f"genre {_genre_pj(data_dir, p) or '?'} — "
                    f"PV {p.get('pv','?')}/{p.get('pv_max','?')} — "
                    f"CA {p.get('ca','?')} — joueur: {p.get('joueur','?')} — "
                    f"conditions: {p.get('conditions') or 'aucune'}"
                )
                # Fiche PJ (valeurs officielles) : sac, dons/rangs, sorts —
                # l'entrée `pj` de l'état n'en transporte aucune.
                for detail in _fiche_pj_lignes(
                    data_dir, str(p.get("nom") or ""), partie_id
                ):
                    lignes.append(f"    · {detail}")
            lignes.append(
                "  (Dons et rangs listés = valeurs officielles des fiches : "
                "applique-les systématiquement aux jets de compétence, "
                "d'initiative, de sauvegarde et aux effets des dons.)"
            )
            lignes.append(
                "  (Le « Sac » listé = contenu OFFICIEL de l'inventaire : ne "
                "dis JAMAIS qu'un objet listé est absent. Pour utiliser un "
                "objet, appelle le tool (ex. `fiche_perso_soigner` avec "
                "`source=\"<nom exact de l'objet listé>\"`) ; la quantité se "
                "déduit toute seule. « Sac (permanent) » = équipement durable "
                "du PJ (reste d'une partie à l'autre) ; « Inventaire de "
                "quête » = dons de PNJ et objets de l'aventure, propres à "
                "CETTE partie — ils n'existent pas dans les autres parties.)"
            )
            # Partie 6746fc6c : ce rappel de sac était re-tissé dans CHAQUE
            # narration (« votre kit est bien rangé… », « la fiole glisse
            # dans votre sac ») — inventory-weaving sans rapport avec
            # l'action du joueur.
            lignes.append(
                "  (Ce rappel de sac sert de VÉRIFICATION, pas de sujet : "
                "n'inventorie PAS l'équipement dans ta narration — le "
                "panneau du joueur l'affiche déjà. Ne mentionne un objet du "
                "sac QUE s'il sert l'action en cours.)"
            )

        pnjs = etat.get("pnj", []) or []
        if pnjs:
            lignes.append("\nPNJ notables :")
            for p in pnjs:
                lignes.append(f"  - {p.get('nom','?')}: {p.get('role','?')}")

        lieu = etat.get("lieu", {}) or {}
        if lieu:
            lignes.append(
                f"\nLieu actuel : {lieu.get('nom', '?')} "
                f"(type: {lieu.get('type', '?')})"
            )
            if lieu.get("description"):
                lignes.append(f"  Description : {lieu['description']}")

        donjon = etat.get("donjon", {}) or {}
        if donjon.get("id"):
            donjon_riche = _donjon_bloc(etat)
            if donjon_riche:
                lignes.append(donjon_riche)
            else:
                salles = donjon.get("salles_visitees", []) or []
                lignes.append(
                    f"\nDonjon '{donjon.get('id')}' — "
                    f"{len(salles)} salle(s) visitée(s) : {', '.join(salles)}"
                )

        donjons_archives = etat.get("donjons_exploreres") or {}
        if donjons_archives:
            noms = [
                f"{did} ({len(d.get('salles_visitees', []))} salles)"
                for did, d in donjons_archives.items()
                if did and d.get("grille")
            ]
            if noms:
                lignes.append(
                    "\nDonjons déjà explorés dans cette partie (archivés) : "
                    + ", ".join(noms)
                )

        quete = etat.get("quete", {}) or {}
        if quete:
            lignes.append(
                f"\nQuête en cours : {quete.get('titre','(sans titre)')} "
                f"— {quete.get('pitch','')}"
            )
            # Début d'aventure : quête choisie mais aucun événement d'histoire
            # → on force la mise en contexte narrative AVANT toute action.
            if (quete.get("titre") or "").strip() and not (
                etat.get("histoire") or []
            ):
                lignes.append(_DEBUT_AVENTURE)
                _ancre_riche = _lieu_depart_canonique(etat)
                if _ancre_riche:
                    lignes.append(_ancre_riche)
            # Bible du scénario : la trame, les étapes et la difficulté,
            # réinjectées pour tenir le cap malgré l'improvisation.
            bible_bloc = _scenario_bible_bloc(
                quete, etat=etat, data_dir=data_dir, partie_id=partie_id)
            if bible_bloc:
                lignes.append(bible_bloc)

        derniere = etat.get("derniere_narration", "")
        if derniere:
            lignes.append(
                "\nDernier événement marquant (DÉJÀ NARRÉ au tour précédent "
                "— ne le RE-NARRE PAS, ne redonne PAS ce qui a déjà été "
                "remis ou dit ; poursuis l'histoire À PARTIR de cet état) :"
            )
            lignes.append(derniere[:1500])

        recap = "\n".join(lignes)
        if len(recap) > self.cfg.game.max_recap_chars:
            recap = recap[: self.cfg.game.max_recap_chars - 60] + "\n…[récap tronqué]"
        recap += "\n==============================="
        return recap

    # ------------------------------------------------------------------ #
    def build_system_message(
        self,
        partie_id: str,
        rag_context: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Construit le message système complet d'un tour.

        Renvoie `(system_message, etat)` — l'état est retourné pour que la
        session puisse l'exposer au frontend (UI) en parallèle du LLM.
        """
        state = PartyState(
            data_dir=str(self.cfg.abs(self.cfg.paths.data_dir)),
            partie_id=partie_id,
            max_history=self.cfg.game.max_history_events,
        )
        etat = state.load()
        recap = self.build_recap(etat, partie_id=partie_id)

        # Détection du régime « ouverture court » : phase opening et aucun PJ.
        # Dans ce cas, on remplace le system prompt complet (~5 k tokens) par
        # la variante courte (~1,5 k tokens) et on n'injecte QUE la section
        # `ouverture` (on zappe `standard`, 2,6 ko de règles génériques peu
        # utiles tant que le personnage n'existe pas). Objectif : laisser le
        # signal d'appel d'outil dominer pour Gemma.
        phase_raw = (etat.get("phase") if etat else "") or ""
        phase = phase_raw.strip().lower() or "opening"
        pj = (etat.get("pj") if etat else []) or []
        opening_court = phase in ("opening", "") and not pj
        # Régime « exploration court » : dès qu'un PJ existe (la phase exacte
        # importe peu — Gemma oublie souvent de déplacer `phase` de
        # `opening` à `opening_complete`, mais la présence d'un PJ est
        # un signal fort qu'on a fini la phase d'ouverture). Le prompt
        # complet noie les appels d'outil dès qu'on dépasse le premier
        # tour — on garde donc la version courte tant qu'un PJ existe et
        # qu'on n'est pas en combat (combat a son propre régime détaillé).
        exploration_court = bool(pj) and phase not in ("combat",)

        if opening_court or exploration_court:
            # Régime « court » : on n'injecte PAS la section dynamique —
            # l'énoncé des étapes est déjà inline dans le prompt court. On
            # garde aussi un récap léger (selon build_recap). Objectif :
            # garder le système aussi léger que le test diagnostic qui a fait
            # déclencher Gemma (~1-2 k tokens, schemas JSON portés par le
            # payload natif).
            sections = ""
        else:
            sections = self.select_sections(phase)

        if opening_court:
            sys_prompt = self.system_prompt_opening()
        elif exploration_court:
            sys_prompt = self.system_prompt_exploration()
        else:
            sys_prompt = self.system_prompt()
        parts = [sys_prompt, recap]
        if sections:
            parts.append(sections)
        if rag_context:
            parts.append(
                "=== CONTEXTE RÈGLES (Knowledge Base D&D 3.5) ===\n"
                + rag_context
                + "\n==============================\n"
                + "Utilise ces extraits des manuels pour appliquer fidèlement les "
                "règles quand un point mécanique se présente."
            )
        return "\n\n".join(p for p in parts if p), etat
