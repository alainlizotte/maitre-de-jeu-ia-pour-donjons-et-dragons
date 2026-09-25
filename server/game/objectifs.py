"""Objectifs de quête — objets requis, verrous et suivi (conformité D&D 3.5).

Un manifeste de donjon (`<nom>.donjon.json`, champ `etapes`) peut déclarer les
objectifs du scénario de façon STRUCTURÉE, avec les objets requis pour
débloquer la suite :

    {
      "cle": "couronne",
      "titre": "Récupérer la Couronne de Mystra …",
      "type": "objet" | "lieu" | "pnj" | "enigme" | "combat" | "etape",
      "detail": "…",
      "requis": [ { "nom": "La Couronne de Mystra", "portee": "quete" } ],
      "salles": ["4,0"],      // zone que cet objectif « verrouille » : on ne
                              // peut entrer dans une salle d'un objectif
                              // PLUTÔT que celui-ci n'est pas accompli
                              // (voir « gating » ci-dessous).
      "gate": true,           // FORCE (ou false, force l'absence de) verrou.
                              // Par défaut : actif seulement si `requis`
                              // non vide — les objectifs de SUIVI purs
                              // (lieux, scènes, énigmes) ne verrouillent RIEN.
      "xp": 600               // récompense d'histoire DMG 3.5 (ch. 2, “Story
                              // Awards”) — accordée par le MJ via
                              // `fiche_perso_gagner_xp` quand l'objectif passe
                              // à « accompli ». Jamais auto-attribuée.
    }

Mécanique de déblocage (pure, sans LLM) :
  - un objectif est « accompli » selon son type :
      objet   → TOUS ses `requis` présents dans l'inventaire de quête LA PARTIE
                (portee="quete" + partie=<courante>) d'au moins un PJ ;
      lieu    → sa salle `salle` visitée, ou titre dans `quete.bible.etapes_terminees` ;
      pnj/enigme/combat/etape → titre dans `quete.bible.etapes_terminees`
                (clôture à l'initiative du MJ via `scenario_etape(terminée=true)`).
  - le PREMIER objectif non accompli est l'objectif courant.
  - GATING (blocage des déplacements/voyages) : actif uniquement pour les
    objectifs GATED (`_est_gate` : `requis` non vide, ou `gate:true`). Le
    verrou refuse d'avancer vers la suite tant que les objets requis ne sont
    pas dans l'inventaire de quête. Les objectifs de suivi purs n'ont AUCUN
    effet sur les déplacements (carte et voyage restent libres).
  - les objets requis sont littéralement des « Objets de quête » : pendant que
    la trame du manifeste est active, `inventaire_ajouter(portee="auto")` les
    classe automatiquement portee="quete" + partie=<courante> (même des gemmes
    ou joyaux que l'heuristique rangerait en permanent).

Conformité D&D 3.5 : la mécanique ne crée aucune règle maison — elle verrouille
des accès qu'un module (ex. « la baguette de téléportation de la Couronne ne
s'active qu'au contact de la couronne ») conditionne à la possession d'objets,
enregistre ces objets dans l'inventaire de quête du PJ (portée de l'aventure),
et expose les récompenses d'histoire telles que le DMG 3.5 les conçoit (au MJ
de les attribuer, jamais au serveur).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Any, Optional, Tuple

# Cache court sur la lecture des manifestes (31+ `.donjon.json` au disque) :
# un outil de donjon lit le manifeste PLUSIEURS fois par tour (verrou de
# déplacement, rafraîchissement d'objectifs, bloc du prompt). TTL 2 s :
# une édition manuelle du manifeste reste prise en compte presque en direct.
_ETAPES_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
_ETAPES_CACHE_TTL = 2.0


# --------------------------------------------------------------------------- #
#  Normalisation des noms d'objets (mêmes règles que inventaire._norm pour
#  que les requis du manifeste matchent les entrées de fiche).
# --------------------------------------------------------------------------- #
def _norm(s: Any) -> str:
    nf = unicodedata.normalize("NFKD", str(s or "").lower())
    ascii_ = "".join(c for c in nf if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", " ", ascii_).strip()
    if len(n) > 2 and n.endswith(("aux", "eaux")):
        n = n[:-3] + "au"
    elif len(n) > 2 and n.endswith("s"):
        n = n[:-1]
    return n


# Désignations alternatives d'un même objet de quête (un manifeste écrit
# « La Couronne de Mystra », un MJ ajoute « couronne de mystra »…). Clé =
# `_norm` SANS ARTICLE, pointe le nom canonique de fusion — mêmes synonymes
# qu'inventaire.
_SYNONYMES: dict[str, str] = {
    "couronne": "couronne de mystra",
    "couronne de mystra": "couronne de mystra",
    "couronne d e mystra": "couronne de mystra",
}

# Articles français retirés du début du nom avant comparaison : le MJ écrit
# « Couronne de Mystra » là où le manifeste exige « La Couronne de Mystra »
# (partie e48e75dd : la couronne en équipement legacy n'était PAS reconnue).
_ARTICLE_RE = re.compile(r"^(le|la|les|l|un|une|de|du|des|d)\s+")


def _sans_article(n: str) -> str:
    while True:
        n2 = _ARTICLE_RE.sub("", n, count=1)
        if n2 == n or not n2:
            return n
        n = n2


def _cle_objet(nom: Any) -> str:
    n = _sans_article(_norm(nom))
    return _SYNONYMES.get(n, n)


# --------------------------------------------------------------------------- #
#  Manifeste / étapes
# --------------------------------------------------------------------------- #
_TYPES_OBJECTIFS = {"objet", "lieu", "pnj", "enigme", "combat", "etape"}


def _norm_nom(s: Any) -> str:
    return _norm(str(s or "").replace("'", " ").replace("-", " "))


def _manifest_etapes(etat: dict[str, Any], data_dir: str) -> list[dict[str, Any]]:
    """Étapes structurées du manifeste (`.donjon.json`) du donjon courant,
    chargées froides depuis le disque — sans dépendance de contexte.

    Découverte : (1) manifeste dont `scenario` == id du scénario de la quête ;
    (2) manifeste dont `donjon_id` == id du donjon en cours. Les étapes du
    disque PRIMENT sur celles stockées en mémoire (migration des donjons créés
    avant l'enrichissement). [] → aucun objectif déclaré.
    """
    donjon = etat.get("donjon") or {}
    try:
        sid = str((etat.get("quete") or {}).get("source") or "").split("]", 1)[0].lstrip("[").strip()
    except Exception:                                               # noqa: BLE001
        sid = ""
    did = str(donjon.get("id") or "")
    cache_key = (data_dir, sid, did)
    _now = time.monotonic()
    _hit = _ETAPES_CACHE.get(cache_key)
    if _hit and (_now - _hit[0]) < _ETAPES_CACHE_TTL:
        return _hit[1]
    base = os.path.join(data_dir, "scenarios")

    def _finish(etapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _ETAPES_CACHE[cache_key] = (time.monotonic(), etapes)
        return etapes

    if not os.path.isdir(base):
        return _finish([])
    for racine, _dirs, fichiers in os.walk(base):
        for f in fichiers:
            if not f.endswith(".donjon.json"):
                continue
            try:
                with open(os.path.join(racine, f), encoding="utf-8") as fh:
                    man = json.load(fh)
            except Exception:                                       # noqa: BLE001
                continue
            if not isinstance(man, dict):
                continue
            scen = man.get("scenario")
            ids = scen if isinstance(scen, list) else [scen]
            match = False
            try:
                if sid and sid in {str(x).strip() for x in ids if x}:
                    match = True
                elif did and _norm_nom(str(man.get("donjon_id") or "")) == _norm_nom(did):
                    match = True
            except Exception:                                       # noqa: BLE001
                continue
            if not match:
                continue
            frais = [e for e in (man.get("etapes") or []) if isinstance(e, dict)]
            if frais and any(e.get("cle") or e.get("requis") or e.get("salles") for e in frais):
                return _finish(frais)
    return _finish([])


def _etapes_manifeste(etat: dict[str, Any], data_dir: str) -> list[dict[str, Any]]:
    """Étapes de la trame courante : enrichies au disque si possible, sinon
    celles stockées dans `donjon["etapes"]` (donjons chargés avant
    l'enrichissement). [] → aucun objectif déclaré."""
    frais = _manifest_etapes(etat, data_dir)
    if frais:
        return frais
    stash = (etat.get("donjon") or {}).get("etapes") or []
    return [e for e in stash if isinstance(e, dict)]


def _est_gate(e: dict[str, Any]) -> bool:
    """L'objectif verrouille-t-il la progression (gating) ?

    Par défaut, un objectif ne VERROUILLE RIEN (suivi pur : lieux, scènes,
    énigmes…) SAUF s'il porte un `requis` (possession d'objets = « cadenas »
    du module) ou un booléen `gate` explicite. Les verrous de déplacement et
    de voyage ne s'appliquent qu'aux objectifs gated.
    """
    g = e.get("gate")
    if g is not None:
        try:
            return bool(g)
        except (TypeError, ValueError):                            # noqa: BLE001
            return False
    return bool(_liste_requis(e.get("requis") or []))


# --------------------------------------------------------------------------- #
#  Inventaire de quête de la partie courante
# --------------------------------------------------------------------------- #
def inventaire_quete(etat: dict[str, Any], data_dir: str,
                     partie_id: str = "") -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    """Objets de QUÊTE (portee="quete") portés par les PJ de la partie.

    Renvoie (par_nom, liste) :
      - `par_nom` : {nom normalisé → {"nom": affiché, "porteur": PJ}} — uniquement
        les objets de la PARTIE courante (`partie == partie_id`) ; les entrées
        de quête sans partie (legacy) sont acceptées pour ne pas casser des
        parties démarrées avant le taggage.
      - `liste`   : même contenu, ordonné (alimentation de l'interface).

    Les entrées d'`equipement` LEGACY (avant le système structuré, sans
    portée) qui correspondent à un objet REQUIS de la trame en cours sont
    comptées comme possédées : une Couronne enregistrée avant la mécanique
    doit débloquer la suite sans réécriture forcée.
    """
    try:
        requis = requis_scenario_noms(etat, data_dir)
    except Exception:                                               # noqa: BLE001
        requis = set()
    par_nom: dict[str, dict[str, str]] = {}
    ordre: list[dict[str, str]] = []
    try:
        from ..persos import charger_fiche  # pylint: disable=import-outside-toplevel
    except Exception:                                                # noqa: BLE001
        return par_nom, ordre
    pjs = etat.get("pj") or []
    if not isinstance(pjs, list):
        return par_nom, ordre
    vus: dict[str, str] = {}

    def _adopter(cle: str, nom_disp: str, porteur: str) -> None:
        if cle in vus:
            return
        vus[cle] = nom_disp
        entree = {"nom": nom_disp, "porteur": porteur}
        par_nom[cle] = entree
        ordre.append(entree)

    for p in pjs:
        if not isinstance(p, dict):
            continue
        nom_pj = str(p.get("nom") or "").strip()
        if not nom_pj:
            continue
        try:
            fiche = charger_fiche(data_dir, nom_pj) or {}
        except Exception:                                            # noqa: BLE001
            continue
        inv = fiche.get("inventaire")
        if not isinstance(inv, list):
            # Fiche pré-structurée : on consulte l'équipement legacy pour les
            # objets requis de la trame (sinon rien à scanner).
            inv = []
        for e in inv:
            if not isinstance(e, dict):
                continue
            if str(e.get("portee") or "") != "quete":
                continue
            partie_entry = str(e.get("partie") or "")
            if partie_entry and partie_id and partie_entry != partie_id:
                continue  # objet de quête d'une AUTRE partie
            nom_obj = str(e.get("nom") or "").strip()
            if not nom_obj:
                continue
            _adopter(_cle_objet(nom_obj), nom_obj, nom_pj)
        # Legacy `equipement` : entrées sans portée qui répondent à un REQUIS.
        equip = fiche.get("equipement")
        if isinstance(equip, list):
            for e in equip:
                if not isinstance(e, dict):
                    continue
                if str(e.get("portee") or "") == "quete":
                    continue  # déjà traité par l'inventaire structuré
                nom_obj = str(e.get("nom") or "").strip()
                if not nom_obj:
                    continue
                if _cle_objet(nom_obj) in requis:
                    _adopter(_cle_objet(nom_obj), nom_obj, nom_pj)
    return par_nom, ordre


# --------------------------------------------------------------------------- #
#  Évaluation des objectifs
# --------------------------------------------------------------------------- #
def _salle_visitee(etat: dict[str, Any], salle_txt: str) -> bool:
    """True si la salle "x,y" de n'importe quel étage du donjon courant a été
    visitée (les étages archivés de `donjons_exploreres` exclus : seul le
    donjon actif compte pour la trame)."""
    if not salle_txt:
        return False
    donjon = etat.get("donjon") or {}
    grilles = [donjon.get("grille") or []]
    for fl in (donjon.get("etages") or {}).values():
        if isinstance(fl, dict):
            grilles.append(fl.get("grille") or [])
    try:
        parts = [int(x) for x in salle_txt.split(",") if x.strip()]
        if len(parts) < 2:
            return True
        cible = (parts[-2], parts[-1])
    except (TypeError, ValueError):
        return False
    for grille in grilles:
        for s in grille if isinstance(grille, list) else []:
            if not isinstance(s, dict):
                continue
            try:
                if (int(s.get("x")), int(s.get("y"))) == cible:
                    return bool(s.get("visitee"))
            except (TypeError, ValueError):
                continue
    return False


def _terminee(etat: dict[str, Any], titre: str) -> bool:
    """L'étape `titre` figure-t-elle dans les étapes accomplies (`etapes_terminees`) ?"""
    bible = (etat.get("quete") or {}).get("bible") or {}
    faites = [str(x).strip().lower() for x in (bible.get("etapes_terminees") or [])]
    return bool(titre) and titre.strip().lower() in faites


def objectifs_quete(etat: dict[str, Any], data_dir: str,
                    partie_id: str = "") -> dict[str, Any]:
    """Évalue les objectifs de la trame en cours.

    Renvoie un dictionnaire exploitable côté serveur (verrous) et client
    (onglet « Objectifs de quête ») :
      {
        "objectifs": [ {cle, titre, detail, type, statut, salle, salles, xp,
                        requis: [{nom, present, porteur}]}, … ],
        "objectif_courant": "titre",
        "manquants": ["nom", …],      // objets requis manquants (objectif bloqué)
        "termines": ["titre", …],     // objectifs accomplis
        "objets_quete": [{nom, porteur}, …],
        "progression": "2/3",
      }
    """
    etapes = _etapes_manifeste(etat, data_dir)
    if not etapes:
        return {
            "objectifs": [], "objectif_courant": "", "manquants": [],
            "termines": [], "objets_quete": [], "progression": "",
        }
    inv_possede, inv_liste = inventaire_quete(
        etat, data_dir, partie_id=partie_id)

    objectifs: list[dict[str, Any]] = []
    termines: list[str] = []
    courant = ""
    manquants: list[str] = []

    for e in etapes:
        titre = str(e.get("titre") or "").strip()
        typ = str(e.get("type") or "").strip().lower()
        if typ not in _TYPES_OBJECTIFS:
            typ = "etape"
        requis = e.get("requis") or []
        requis_norm = [r for r in _liste_requis(requis)]
        # Statut de présence de chaque objet requis.
        preq: list[dict[str, str]] = []
        for nom_req in requis_norm:
            cle = _cle_objet(nom_req)
            porte = inv_possede.get(cle)
            preq.append({
                "nom": str(nom_req),
                "present": bool(porte),
                "porteur": (porte or {}).get("porteur", ""),
            })
        # Accompli selon le type.
        accompli = _terminee(etat, titre)
        if not accompli:
            if typ == "objet":
                accompli = bool(requis_norm) and all(r["present"] for r in preq)
            elif typ == "lieu":
                accompli = _salle_visitee(etat, str(e.get("salle") or "").strip())
        statut = "complet" if accompli else "a_venir"
        if accompli:
            termines.append(titre)
        objectifs.append({
            "cle": str(e.get("cle") or f"o{len(objectifs) + 1}"),
            "titre": titre,
            "detail": str(e.get("detail") or "").strip(),
            "type": typ,
            "statut": statut,
            "salle": str(e.get("salle") or "").strip(),
            "salles": [str(s).strip() for s in (e.get("salles") or []) if str(s).strip()],
            "xp": int(e["xp"]) if str(e.get("xp") or "").strip().isdigit() else 0,
            "requis": preq,
        })

    # Premier objectif non accompli → en_cours (ou « bloque » si requis
    # manquants) ; les suivants restent à venir.
    for obj in objectifs:
        if obj["statut"] != "complet":
            if not courant:
                courant = obj["titre"]
                manquants_courant = [
                    r["nom"] for r in (obj.get("requis") or []) if not r["present"]
                ]
                if manquants_courant and obj["type"] == "objet":
                    obj["statut"] = "bloque"
                    manquants = manquants_courant
                else:
                    obj["statut"] = "en_cours"
                    manquants = []
            else:
                obj["statut"] = "a_venir"

    total = len(objectifs)
    faits = len(termines)
    return {
        "objectifs": objectifs,
        "objectif_courant": courant,
        "manquants": manquants,
        "termines": termines,
        "objets_quete": inv_liste,
        "progression": f"{faits}/{total}",
    }


def _liste_requis(requis: Any) -> list[str]:
    """Noms des objets requis (`requis` = liste de dicts {nom} ou str)."""
    if not isinstance(requis, list):
        return []
    noms: list[str] = []
    for r in requis:
        if isinstance(r, dict):
            n = str(r.get("nom") or "").strip()
        else:
            n = str(r or "").strip()
        if n:
            noms.append(n)
    return noms


def requis_scenario_noms(etat: dict[str, Any], data_dir: str) -> set[str]:
    """Noms normalisés de TOUS les objets requis par la trame en cours.
    Sert à forcer `portee="quete"` dans `inventaire_ajouter(portee="auto")`."""
    noms: set[str] = set()
    for e in _etapes_manifeste(etat, data_dir):
        for nom in _liste_requis(e.get("requis") or []):
            noms.add(_cle_objet(nom))
    return noms


def actualiser_objectifs(etat: dict[str, Any], data_dir: str,
                         partie_id: str = "") -> bool:
    """Évalue les objectifs et les écrit dans `quete.bible["objectifs"]` (+
    `objectif_courant`, `manquants`, `termines`, `progression`).

    Mute `etat` EN PLACE (l'appelant sauvegarde). Renvoie True si un donjon
    avec une trame à objectifs est actif.
    """
    if not isinstance(etat, dict):
        return False
    bible = (etat.get("quete") or {}).get("bible")
    if not isinstance(bible, dict):
        return False
    info = objectifs_quete(etat, data_dir, partie_id=partie_id)
    if not info["objectifs"]:
        return False
    bible["objectifs"] = info["objectifs"]
    bible["objectif_courant"] = info["objectif_courant"]
    # Écrase AUSSI l'étiquette legacy `etape_courante` : le pas 4 de
    # `_journaliser_lieu` la positionne dès qu'une salle d'étape est visitée
    # (partie e48e75dd : entrer dans la grotte à (0,0) — qui est AUSSI la
    # salle de restauration de l'acte final — figeait l'étiquette sur le
    # DERNIER objectif). L'objectif réel (premier non accompli) prime.
    bible["etape_courante"] = info["objectif_courant"]
    bible["manquants"] = info["manquants"]
    bible["termines"] = info["termines"]
    bible["progression_objectifs"] = info["progression"]
    bible["objets_quete"] = info["objets_quete"]
    # ── Mémoire de campagne (le LLM n'appelle presque jamais les tools
    # memoire_* : le serveur alimente lui-même le fil) ──────────────────────
    try:
        mem = etat.setdefault("memoire", {})
        if isinstance(mem, dict):
            mem["objectif_courant"] = info["objectif_courant"]
            missions = mem.get("missions")
            titre_quete = str((etat.get("quete") or {}).get("titre") or "").lower()
            total = len(info["objectifs"])
            faits = len(info["termines"])
            if isinstance(missions, list) and titre_quete:
                for m in missions:
                    if not isinstance(m, dict):
                        continue
                    if str(m.get("titre", "")).lower() != titre_quete:
                        continue
                    m["avancement"] = info["progression"]
                    if total and faits >= total and m.get("statut") == "active":
                        m["statut"] = "terminée"
    except Exception:                                            # noqa: BLE001
        pass
    return True


def _trame_structuree(etat: dict[str, Any], data_dir: str) -> bool:
    """True si la trame en cours porte une structure d'OBJECTIFS (une étape
    avec `requis`, `salles` ou une `cle` de type typé) — actif seulement pour
    les manifestes enrichis ; les donjons legacy gardent leurs comportements."""
    for e in _etapes_manifeste(etat, data_dir):
        if not isinstance(e, dict):
            continue
        if e.get("requis") or e.get("salles") or (
            e.get("cle") and e.get("type")
        ):
            return True
    return False


def _zone_etapes(etat: dict[str, Any], data_dir: str,
                 partie_id: str = "") -> tuple[dict[tuple[int, int], int], list[dict[str, Any]]]:
    """(zone: {(x,y): index_étape}, étapes) — chaque salle des `salles` d'une
    étape est affectée à SON index. Seules les étapes GATED participent au
    verrou : un objectif de suivi pur (sans `requis`, sans `gate`) n'ajoute
    aucune salle à la zone. Les zones d'étapes PLUS TARDIVEs que l'objectif
    courant servent de verrou de déplacement."""
    etapes = _etapes_manifeste(etat, data_dir)
    zone: dict[tuple[int, int], int] = {}
    for i, e in enumerate(etapes):
        if not isinstance(e, dict) or not _est_gate(e):
            continue
        for s in (e.get("salles") or []):
            try:
                parts = [int(p) for p in str(s).split(",") if p.strip()]
            except (TypeError, ValueError):
                continue
            if len(parts) < 2:
                continue
            zone[(parts[-2], parts[-1])] = i
    return zone, etapes


def verrou_deplacement(etat: dict[str, Any], data_dir: str, partie_id: str,
                       destination: tuple[int, int]) -> Optional[str]:
    """⛔ Verrou de déplacement DONJON : refuse d'entrer dans la zone d'une
    étape PLUTÔT que l'objectif courant tant que celui-ci n'est pas accompli
    (ex. la « porte de téléportation » de la Couronne SCELLÉE tant que la
    couronne n'est pas dans l'inventaire). Le retour en arrière (salle déjà
    franchie, hors zone future) reste TOUJOURS possible.

    Renvoie le message de refus ou None si le déplacement est autorisé.
    """
    zone, etapes = _zone_etapes(etat, data_dir, partie_id)
    if not zone:
        return None
    obst = zone.get(destination)
    if obst is None:
        return None
    # Objectif courant = premier non accompli.
    if not etapes:
        return None
    inv_possede, _ = inventaire_quete(etat, data_dir, partie_id=partie_id)
    etapes_ok = []
    for e in etapes:
        titre = str(e.get("titre") or "").strip()
        typ = str(e.get("type") or "").strip().lower()
        if typ not in _TYPES_OBJECTIFS:
            typ = "etape"
        req_noms = _liste_requis(e.get("requis") or [])
        accompli = _terminee(etat, titre)
        if not accompli:
            if typ == "objet":
                accompli = bool(req_noms) and all(
                    _cle_objet(n) in inv_possede for n in req_noms)
            elif typ == "lieu":
                accompli = _salle_visitee(etat, str(e.get("salle") or "").strip())
        etapes_ok.append(accompli)
    courant_idx = next((i for i, ok in enumerate(etapes_ok) if not ok), None)
    if courant_idx is None:
        return None  # tout est accompli
    if obst > courant_idx:
        # Destination dans la zone d'une étape FUTURE : l'objectif courant
        # doit être accompli (requis présents) avant d'aller plus loin.
        cur = etapes[courant_idx]
        titre_cur = str(cur.get("titre") or "").strip()
        req_noms_cur = _liste_requis(cur.get("requis") or [])
        manquant = [n for n in req_noms_cur
                    if _cle_objet(n) not in inv_possede]
        detail_cur = str(cur.get("detail") or "").strip()
        lignes = [
            "⛔ **SCÉNARIO — progression SCELLÉE** : la destination "
            f"({destination[0]},{destination[1]}) appartient à la SUITE de la "
            "quête, mais l'objectif courant n'est pas accompli.",
            f"Objectif courant : « {titre_cur} »",
        ]
        if detail_cur:
            lignes.append(detail_cur[:400])
        if manquant:
            lignes.append(
                "❌ Objets REQUIS manquants à l'inventaire de quête "
                "(`portee=\"quete\"`, partie courante) :\n   • "
                + "\n   • ".join(manquant)
                + "\n→ Enregistre-les via `inventaire_ajouter(nom=\"<PJ>\", "
                  "objet=\"<nom exact>\", portee=\"quete\")` quand le groupe "
                  "les obtient."
            )
        else:
            lignes.append(
                "→ L'objectif courant appelle d'abord une SALLE à atteindre "
                "ou un événement à jouer : accomplis-le avant de continuer."
            )
        return "\n".join(lignes)
    return None


def verrou_voyage(etat: dict[str, Any], data_dir: str, partie_id: str,
                  destination: str) -> Optional[str]:
    """⛔ Verrou de VOYAGE : un départ est refusé si un objectif GATED du
    scénario n'est pas accompli (objet requis manquant OU salle à visiter).
    Complète la garde géographique historique (salle non visitée) par la
    garde d'objets requis (possession effective). Les objectifs de SUIVI pur
    (sans `requis`, sans `gate`) ne bloquent jamais le voyage."""
    etapes = _etapes_manifeste(etat, data_dir)
    if not etapes:
        return None
    inv_possede, _ = inventaire_quete(etat, data_dir, partie_id=partie_id)

    def _accompli(e: dict[str, Any]) -> bool:
        titre = str(e.get("titre") or "").strip()
        typ = str(e.get("type") or "").strip().lower()
        if typ not in _TYPES_OBJECTIFS:
            typ = "etape"
        salle_txt = str(e.get("salle") or "").strip()
        req_noms = _liste_requis(e.get("requis") or [])
        if _terminee(etat, titre):
            return True
        if typ == "objet":
            return bool(req_noms) and all(
                _cle_objet(n) in inv_possede for n in req_noms)
        if typ == "lieu":
            return _salle_visitee(etat, salle_txt)
        if salle_txt:
            # Acte purement narratif (etape/pnj/enigme/combat) lié à une
            # salle : une fois la salle atteinte, le voyage N'EST PAS
            # bloqué — la scène (et la clôture `scenario_etape`) se joue
            # ensuite. Bloque seulement tant que la position n'est pas là.
            return _salle_visitee(etat, salle_txt)
        return False

    # Seul l'objectif COURANT (premier non accompli) peut bloquer le départ :
    # une étape FUTURE ne verrouille pas le voyage (ses zones restent
    # scellées côté donjon par `verrou_deplacement`).
    courant_idx = next(
        (i for i, e in enumerate(etapes)
         if isinstance(e, dict) and not _accompli(e)), None)
    if courant_idx is None:
        return None
    e = etapes[courant_idx]
    if not _est_gate(e):
        return None
    titre = str(e.get("titre") or "").strip()
    typ = str(e.get("type") or "").strip().lower()
    if typ not in _TYPES_OBJECTIFS:
        typ = "etape"
    salle_txt = str(e.get("salle") or "").strip()
    req_noms = _liste_requis(e.get("requis") or [])
    # Les objectifs de COLLECTE mondiale (type objet SANS salle précise,
    # ex. les huit gemmes de la Couronne) s'accomplissent EN VOYAGEANT :
    # bloquer le départ rendrait la chasse impossible. Seules les étapes
    # ancrées au donjon (`salle` présente) ou les scènes à jouer
    # interdisent de quitter la région.
    if typ == "objet" and not salle_txt:
        return None
    # Objectif courant NON accompli → le voyage est bloqué.
    manquant = [n for n in req_noms
                if _cle_objet(n) not in inv_possede]
    detail = str(e.get("detail") or "").strip()
    lignes = [
        "⛔ **TRAME DU SCÉNARIO — voyage refusé** : l'objectif courant "
        f"« {titre} » n'est pas accompli."
    ]
    if manquant:
        lignes.append(
            "❌ Objets REQUIS manquants à l'inventaire de quête :\n   • "
            + "\n   • ".join(manquant)
            + f"\nNe lance PAS de voyage vers « {destination} » tant que "
              "ces objets ne sont pas possédés (la suite du module est "
              "verrouillée par le scénario)."
        )
    elif salle_txt:
        if detail:
            lignes.append(detail[:400])
        lignes.append(
            f"L'objectif appelle d'abord la salle {salle_txt} — reprends "
            "l'exploration du donjon (`carte_donjon_explorer`). Si la "
            "table choisit DÉLIBÉRÉMENT d'abandonner la trame, relance "
            "`voyage_demarrer` avec `forcer=true`."
        )
    else:
        lignes.append(
            "L'objectif courant doit être joué (événement, PNJ, énigme ou "
            "réunion d'objets) AVANT de voyager. Si la table choisit "
            "DÉLIBÉRÉMENT de sortir de la trame, relance "
            "`voyage_demarrer` avec `forcer=true`."
        )
    return "\n".join(lignes)