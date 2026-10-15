"""Outil Voyage — déplacements hors donjon conformes aux règles D&D 3.5.

Sources : SRD « Movement — Modes of Movement », « Wilderness », « Weather ».

Un déplacement n'est JAMAIS instantané : le tool calcule la durée réelle
(vitesse de marche, terrain, marche forcée), tire les rencontres aléatoires
(par jour, selon le terrain), le risque de s'égarer (Survie) et la météo du
premier jour. Le MJ doit narrer les journées et résoudre chaque rencontre
(monstre_consulter + demarrer_combat).

Auto-enregistré par `registry.discover_tools` (import du package server.tools).
"""

from __future__ import annotations

import math
import random
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool

# --------------------------------------------------------------------------- #
#  Données règles (SRD)
# --------------------------------------------------------------------------- #
# Vitesse de voyage sur 8 h/jour (SRD Movement, arrondi métrique).
#   lent = discrétion maximale (½ vitesse), marche = normale,
#   rapide = 1⅓ × vitesse (marche forcée si à pied).
_ALLURES: dict[str, tuple[str, float]] = {
    "lent": ("Lente (discrète)", 16.0),          # km/jour à pied
    "marche": ("Normale", 24.0),
    "rapide": ("Rapide (marche forcée)", 36.0),
    "cheval": ("Monture (trot)", 48.0),
    "cheval_rapide": ("Monture (galop / route)", 64.0),
}

# Multiplicateur de distance par type de terrain (SRD Wilderness : highway /
# road / trail / trackless, condensé). La distance effective parcourue par
# jour est base × facteur.
_TERRAINS: dict[str, dict[str, Any]] = {
    #                facteur  rencontre%/jour  égarement% (hors-piste)  label
    "route":      {"f": 1.00, "rencontre": 5,  "egarer": 0,  "label": "route"},
    "plaine":     {"f": 0.90, "rencontre": 5,  "egarer": 20, "label": "plaines"},
    "collines":   {"f": 0.75, "rencontre": 10, "egarer": 30, "label": "collines"},
    "foret":      {"f": 0.70, "rencontre": 10, "egarer": 30, "label": "forêt"},
    "desert":     {"f": 0.80, "rencontre": 10, "egarer": 30, "label": "désert"},
    "marais":     {"f": 0.50, "rencontre": 20, "egarer": 40, "label": "marais"},
    "montagne":   {"f": 0.50, "rencontre": 15, "egarer": 40, "label": "montagnes"},
}

# Climat D&D 3.5 (DRS « Climat », table d100 par région — chantier P2-#9).
# Chaque entrée : (min, max, effet, texte). L'effet est exploitable
# mécaniquement par `voyage_meteo_jour` (canicule/froid/tempête…).
_CLIMATS: dict[str, list[tuple[int, int, str, str]]] = {
    "tempere": [
        (1, 60, "clair", "⛅ Normal pour la saison — temps calme."),
        (61, 70, "vent", "🌬️ Venteux (15–50 km/h) — Perception auditive −2."),
        (71, 75, "canicule", "☀️ **Vague de chaleur** (≥ 35 °C) — jets de "
         "Vigueur contre la chaleur (voyage_meteo_jour)."),
        (76, 80, "froid", "🥶 **Coup de froid** (< 5 °C) — jets de Vigueur "
         "contre le froid (voyage_meteo_jour)."),
        (81, 85, "pluie", "🌧️ Pluie — Perception/Repérage −4, sol boueux, "
         "mauvaise visibilité (égarement plus probable)."),
        (86, 90, "neige", "❄️ Neige — déplacement ×½, Perception −4, "
         "mauvaise visibilité."),
        (91, 95, "brouillard", "🌫️ Brouillard — visibilité réduite, "
         "Perception −4, égarement doublé."),
        (96, 99, "orage", "⛈️ **Orage** (vents 50–80 km/h, visibilité −75 %) "
         "— Perception −8, déplacement ×½ hors route."),
        (100, 100, "tempete", "🌪️ **Tempête violente** (≥ 80 km/h) — voyage "
         "impossible aujourd'hui (ou Vigueur DD 18/h, échec = 1d3 "
         "non-létaux)."),
    ],
    "froid": [
        (1, 65, "froid", "🥶 Froid (< 5 °C), temps calme — jets de Vigueur "
         "quotidiens (voyage_meteo_jour)."),
        (66, 70, "vent", "🌬️ Venteux glacial — Perception auditive −2."),
        (71, 75, "canicule", "🌤️ Vague de chaleur inhabituelle (frais)."),
        (76, 80, "froid", "🥶 **Coup de froid** (−15 °C) — Vigueur PAR "
         "TRANCHE DE 10 MIN de marche (voyage_meteo_jour)."),
        (81, 90, "neige", "❄️ **Chutes de neige** — déplacement ×½, "
         "Perception −4, mauvaise visibilité."),
        (91, 99, "tempete", "🌨️ **Tempête de neige** (vents 50–80 km/h) — "
         "voyage impossible (ou Vigueur DD 18/h)."),
        (100, 100, "tempete", "🌪️ **BLIZZARD** (≥ 80 km/h + 30–90 cm de "
         "neige) — voyage impossible aujourd'hui."),
    ],
    "desert": [
        (1, 65, "canicule", "☀️ Chaud (30–45 °C), temps calme — jets de "
         "Vigueur contre la chaleur (voyage_meteo_jour)."),
        (66, 70, "vent", "🌬️ Venteux — sable emporté, Perception −2."),
        (71, 90, "canicule", "🔥 **Chaud venteux** (≥ 35 °C) — chaleur "
         "accablante : besoins en eau ×2 (voyage_meteo_jour)."),
        (91, 99, "tempete", "🏜️ **Tempête de sable** (50–80 km/h, visibilité "
         "−75 %) — voyage impossible (ou Vigueur DD 18/h)."),
        (100, 100, "orage", "🌊 **Trombes d'eau** — visibilité comme "
         "brouillard, risque de crues éclair."),
    ],
}

# Region climatique par terrain (voyage_demarrer la déduit si non fournie).
_REGION_TERRAIN = {
    "route": "tempere", "plaine": "tempere", "collines": "tempere",
    "foret": "tempere", "marais": "tempere", "montagne": "froid",
    "desert": "desert",
}


def _tirer_meteo_jour(region: str, saison: str,
                      rng: Optional[random.Random] = None) -> dict:
    """Tirage d100 du climat quotidien (DRS « Climat ») pour une région.
    `saison` : "hiver" | "printemps" | "ete" | "automne" (tempéré : l'hiver
    transforme pluie→neige, orage→tempête de neige)."""
    rng = rng or random
    r = rng.randint(1, 100)
    table = _CLIMATS.get(region, _CLIMATS["tempere"])
    for lo, hi, effet, txt in table:
        if lo <= r <= hi:
            if region == "tempere" and saison == "hiver":
                if effet == "pluie":
                    effet, txt = "neige", ("❄️ Neige (hiver) — déplacement "
                                           "×½, Perception −4.")
                elif effet == "orage":
                    effet, txt = "tempete", ("🌨️ **Tempête de neige** "
                                             "(hiver) — voyage impossible.")
            return {"effet": effet, "txt": txt, "d100": r}
    return {"effet": "clair", "txt": "⛅ Temps calme.", "d100": r}


def _jour_saison(etat: dict) -> str:
    """Saison courante (état `date` {mois 1-12} sinon printemps par défaut)."""
    try:
        mois = int((etat.get("date") or {}).get("mois") or 4)
    except (TypeError, ValueError):
        mois = 4
    if mois in (12, 1, 2):
        return "hiver"
    if mois in (3, 4, 5):
        return "printemps"
    if mois in (6, 7, 8):
        return "ete"
    return "automne"


def _norm(s: str) -> str:
    import unicodedata
    nf = unicodedata.normalize("NFKD", (s or "").lower().strip())
    return "".join(c for c in nf if not unicodedata.combining(c))


# Échelle de la carte du monde : 1 unité (%) ≈ 45 km. Mesuré sur l'échelle
# IMPRIMÉE de Sword-Coast-Map.jpg (Schley) : 500 miles ≈ 18 % de largeur
# → ~44,7 km par unité, arrondis à 45.
# Sert à CALCULER la distance quand le MJ ne la fournit pas mais que le
# départ et la destination sont connus de la carte (villes + départs de
# scénarios placés dans `positions_joueurs`).
_KM_PAR_UNITE = 45.0


def _distance_auto(etat: dict[str, Any], destination: str) -> Optional[float]:
    """Distance (km, arrondie à 5 km) entre la position du groupe et
    `destination` si celle-ci est une ville connue de la carte, sinon None."""
    try:
        from .cartes import (                            # noqa: PLC0415
            VILLES_REPERES, _normaliser_nom,
        )
    except Exception:                                    # noqa: BLE001
        return None
    cible = _normaliser_nom(destination)
    if not cible:
        return None
    cible_pt: Optional[tuple[float, float]] = None
    for nom, (x, y) in VILLES_REPERES.items():
        nn = _normaliser_nom(nom)
        if cible == nn or cible in nn or nn in cible:
            cible_pt = (float(x), float(y))
            break
    if cible_pt is None:
        return None
    pos = etat.get("positions_joueurs") or {}
    if not isinstance(pos, dict) or not pos:
        return None
    p = pos.get("groupe") or next(iter(pos.values()))
    try:
        px, py = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):          # noqa: BLE001
        return None
    d = math.hypot(px - cible_pt[0], py - cible_pt[1]) * _KM_PAR_UNITE
    return float(max(5, round(d / 5.0) * 5.0))


def _garde_trame_voyage(etat: dict[str, Any], destination: str,
                        data_dir: str = "", partie_id: str = "") -> Optional[str]:
    """⛔ Garde de séquence : un voyage ne doit pas sauter une étape de la
    trame du scénario (partie 87b8f286 : départ vers la gemme de Sarr ALORS
    QUE la Couronne n'avait pas été récupérée (4,0) — prérequis du module).

    Deux étages de garde, selon l'enrichissement du manifeste :
    - manifeste STRUCTURÉ (`requis`/`salles`/`type`) → `verrou_voyage` :
      vérifie en plus les OBJETS REQUIS manquants (inventaire de quête de LA
      PARTIE) et les salles à atteindre ;
    - manifeste legacy → garde historique par `salle` non visitée (la
      première étape à salle non accomplie bloque le voyage)."""
    if data_dir:
        try:
            from ..game.objectifs import (  # pylint: disable=import-outside-toplevel
                _trame_structuree, verrou_voyage,
            )
            if _trame_structuree(etat, data_dir):
                refus = verrou_voyage(etat, data_dir, partie_id, destination)
                if refus:
                    return refus
                return None
        except Exception:                                        # noqa: BLE001
            pass
    donjon = etat.get("donjon") or {}
    etapes = donjon.get("etapes") or []
    if not etapes:
        return None
    grilles = [donjon.get("grille") or []]
    for fl in (donjon.get("etages") or {}).values():
        if isinstance(fl, dict):
            grilles.append(fl.get("grille") or [])

    def _visitee(salle_txt: str) -> bool:
        try:
            parts = [
                int(p) for p in str(salle_txt).split(",") if p.strip()
            ]
            if len(parts) < 2:
                return True
            xy = (parts[-2], parts[-1])
        except (TypeError, ValueError):
            return True
        for grille in grilles:
            for s in grille:
                if not isinstance(s, dict):
                    continue
                try:
                    if (int(s.get("x")), int(s.get("y"))) == xy:
                        return bool(s.get("visitee"))
                except (TypeError, ValueError):
                    continue
        return True

    for e in etapes:
        if not isinstance(e, dict):
            continue
        salle = str(e.get("salle") or "").strip()
        if not salle or _visitee(salle):
            continue  # étape sans salle liée, ou déjà accomplie
        return (
            "⛔ **TRAME DU SCÉNARIO — voyage refusé** : l'étape courante "
            f"« {e.get('titre', '?')} » (salle {salle}) n'est PAS accomplie. "
            + (
                f"{str(e.get('detail')).strip()} "
                if str(e.get("detail") or "").strip() else ""
            )
            + f"Ne lance PAS de voyage vers « {destination} » tant que cette "
            "étape n'est pas faite : la séquence du module serait rompue. "
            "Reprends l'exploration du donjon (`carte_donjon_explorer`) vers "
            f"la salle {salle}. Si la table choisit DÉLIBÉRÉMENT d'abandonner "
            "la trame, relance `voyage_demarrer` avec `forcer=true`."
        )
    return None


@tool
async def voyage_demarrer(
    ctx: ToolContext,
    destination: str,
    distance_km: float = 0,
    mode: str = "marche",
    terrain: str = "plaine",
    piste: bool = False,
    forcer: bool = False,
    region: str = "",
) -> ToolResult:
    """
    Lance un voyage hors donjon et calcule sa durée réelle selon les règles
    D&D 3.5 (Movement/Wilderness) : allure, terrain, rencontres aléatoires
    quotidiennes, risque de s'égader, météo, marche forcée. OBLIGATOIRE dès
    qu'un groupe quitte un lieu pour un autre — jamais de téléportation.

    :param destination (str): nom du lieu de destination (ex. "Phandalin").
    :param distance_km (float): distance en kilomètres. OPTIONNEL si la
        destination est une ville connue de la carte ET que le groupe a une
        position : la distance est alors CALCULÉE automatiquement (~45 km par
        unité de carte). Sinon obligatoire.
    :param mode (str): "lent" | "marche" | "rapide" | "cheval" | "cheval_rapide".
        "rapide" à pied = marche forcée (jets de CON, fatigue).
    :param terrain (str): "route" | "plaine" | "collines" | "foret" |
        "desert" | "marais" | "montagne".
    :param piste (bool): True si sentier/route connue (jamais perdu) ;
        False par défaut en terrain inconnu.
    :param forcer (bool): True pour passer outre la garde de trame (le
        groupe abandonne délibérément la séquence du scénario). Réserver
        aux choix EXPLICITES des joueurs.
    :param region (str): climat régional — "tempere" | "froid" | "desert".
        Déduit du terrain si omis (montagne → froid, désert → desert,
        sinon tempéré). Détermine la table de climat quotidienne.
    """
    mode_n = _norm(mode)
    if mode_n not in _ALLURES:
        return ToolResult(
            text=f"⚠️ Mode '{mode}' inconnu. Options : {', '.join(_ALLURES)}."
        )
    terrain_n = _norm(terrain)
    if terrain_n == "foret":
        terrain_n = "foret"
    if terrain_n not in _TERRAINS:
        return ToolResult(
            text=(f"⚠️ Terrain '{terrain}' inconnu. Options : "
                  f"{', '.join(_TERRAINS)}.")
        )
    # Distance : fournie par le MJ, sinon CALCULÉE carte en main (position du
    # groupe → ville de destination).
    _hors_carte = False
    if float(distance_km or 0) <= 0:
        try:
            from ..game.state import PartyState       # lazy : évite tout cycle
            _etat_d = PartyState(
                data_dir=ctx.data_dir, partie_id=ctx.partie_id
            ).load()
            distance_km = _distance_auto(_etat_d, destination) or 0
        except Exception:                                # noqa: BLE001
            distance_km = 0
        if distance_km <= 0:
            # 🔧 Partie 75f5e74c : une destination HORS CARTE (« le repère de
            # Nulentok », « la cabane du bûcheron »…) refusait le voyage —
            # le petit modèle narrait alors son échec à voix haute au lieu
            # de rejouer avec une distance. On estime désormais un trajet
            # court (1 jour de marche) : le voyage démarre, le MJ précise
            # `distance_km` s'il veut ajuster.
            distance_km = 30.0
            _hors_carte = True
        else:
            _hors_carte = False
        distance_km = float(distance_km)
    if distance_km <= 0:
        return ToolResult(text="⚠️ distance_km doit être > 0.")

    # ⛔ Garde de trame : pas de voyage tant qu'une étape à salle du
    # scénario reste à faire (sauter la séquence du module rompt le fil).
    if not forcer:
        try:
            from ..game.state import PartyState   # lazy : évite tout cycle
            _etat_v = PartyState(
                data_dir=ctx.data_dir, partie_id=ctx.partie_id
            ).load()
            garde = _garde_trame_voyage(
                _etat_v, destination,
                data_dir=ctx.data_dir, partie_id=ctx.partie_id,
            )
        except Exception:                                        # noqa: BLE001
            garde = None
        if garde:
            return ToolResult(text=garde)

    tdata = _TERRAINS[terrain_n]
    allure_label, base_kmj = _ALLURES[mode_n]
    km_jour_effectifs = base_kmj * tdata["f"]

    # 🆕 Région climatique + saison (chantier DRS P2-#9).
    region_n = _norm(region) if region else _REGION_TERRAIN.get(
        terrain_n, "tempere")
    if region_n not in _CLIMATS:
        region_n = "tempere"

    # 🆕 Charge des PJ (chantier DRS P2-#12) : un PJ chargé Moyenne/Lourde
    # ralentit le groupe (VD 9 m → 6 m, PHB « Table : effets de la charge »).
    from ..game.state import PartyState as _PS_vd
    _etat_vd = _PS_vd(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
    _charges: list[tuple[str, str]] = []
    try:
        from .fiches import _load_fiche as _lf_vd
        from .inventaire import _recalculer_charge as _rc_vd
        for _p_vd in (_etat_vd.get("pj") or []):
            _f_vd = _lf_vd(ctx, str(_p_vd.get("nom") or ""))
            if _f_vd is None:
                continue
            _cat_vd = _rc_vd(_f_vd)[1]
            if _cat_vd in ("Moyenne", "Lourde", "Depassee"):
                _charges.append((str(_f_vd.get("nom") or "?"), _cat_vd))
    except Exception:                                        # noqa: BLE001
        _charges = []
    _ralenti = bool(_charges) and mode_n not in ("cheval", "cheval_rapide")
    if _ralenti:
        km_jour_effectifs = km_jour_effectifs * 2.0 / 3.0

    # Durée : on avance jour par jour (le dernier peut être partiel).
    jours_total = max(1, math.ceil(distance_km / km_jour_effectifs))

    # Marche forcée à pied (« rapide ») : au-delà de 8 h, jets de CON.
    forcee = mode_n == "rapide"
    a_pied = mode_n not in ("cheval", "cheval_rapide")

    lignes = [
        f"🗺️ **Voyage vers {destination}**",
        f"- Terrain : {tdata['label']} ({'piste connue' if piste else 'hors-piste'}) "
        f"— allure : {allure_label} ({base_kmj:.0f} km/jour × facteur "
        f"{tdata['f']:.2f} = **{km_jour_effectifs:.0f} km/jour**)"
        + (" — ⚖️ ralenti ×⅔ (charge Moyenne/Lourde : "
           + ", ".join(f"{n} ({c})" for n, c in _charges) + ")"
           if _ralenti else ""),
        f"- Climat : région {region_n}, saison {_jour_saison(_etat_vd)}",
        f"- Distance : {distance_km:.0f} km → durée : **{jours_total} jour(s)** "
        f"(8 h de marche par jour)"
        + (" — ⚠️ destination hors carte : distance ESTIMÉE (1 jour de "
           "marche) ; précisez `distance_km` pour ajuster."
           if _hors_carte else ""),
    ]

    # --- Marche forcée -------------------------------------------------------
    if forcee and a_pied:
        lignes.append(
            "- ⚠️ **Marche forcée** : après 8 h de marche, jet de CON chaque "
            "heure supplémentaire (DD 10, +2 par heure au-delà de la 9ᵉ) ; "
            "échec = 1d6 dégâts non létaux + condition **Fatigué**."
        )

    # --- Rencontres aléatoires (un d100 par jour) ----------------------------
    rencontres: list[int] = []
    for jour in range(1, jours_total + 1):
        if random.randint(1, 100) <= tdata["rencontre"]:
            rencontres.append(jour)
    if rencontres:
        lignes.append(
            f"- 🎲 **Rencontres** : tirage quotidien ({tdata['rencontre']} %/"
            f"jour en {tdata['label']}) → rencontre(s) le(s) jour(s) "
            f"{', '.join(str(j) for j in rencontres)} ! Pour chacune : "
            "`monstre_consulter` puis `demarrer_combat`."
        )
    else:
        lignes.append(
            f"- 🎲 Rencontres : tirage quotidien ({tdata['rencontre']} %/jour) "
            f"→ aucune sur {jours_total} jour(s)."
        )

    # --- Météo quotidienne (climat DRS — chantier P2-#9) ---------------------
    # (tirée AVANT l'égarement : la visibilité du jour module le DD de Survie)
    _saison = _jour_saison(_etat_vd)
    _meteo_jours: list[dict] = []
    for jour in range(1, jours_total + 1):
        _m = _tirer_meteo_jour(region_n, _saison)
        _m["jour"] = jour
        _meteo_jours.append(_m)

    # --- Égarement (Survie, table DRS « Se perdre » — chantier P3-#13) ------
    egarement = ""
    _perdu_jours: list[int] = []
    if not piste and tdata["egarer"] > 0:
        # DD officiel par terrain (DRS « Chances de se perdre ») : la
        # mauvaise visibilité impose un plancher de 12 ; carte = −4.
        _dd_terrain = {
            "plaine": 10, "collines": 10, "foret": 15, "desert": 12,
            "marais": 12, "montagne": 12, "route": 0,
        }.get(terrain_n, 10)
        # Météo du jour 1 (tirée plus bas) peut dégrader la visibilité.
        _visibilite_pire = False
        # Guide = le PJ avec le meilleur modificateur de Survie.
        _guide, _mod_survie_g, _detail_survie = "", 0, ""
        try:
            from .fiches import _load_fiche as _lf_g, bonus_dons_effet as _bd_g
            for _p_g in (_etat_vd.get("pj") or []):
                _f_g = _lf_g(ctx, str(_p_g.get("nom") or ""))
                if _f_g is None:
                    continue
                _rangs_g = int((_f_g.get("competences") or {})
                               .get("Survie", 0) or 0)
                _sag_g = int((_f_g.get("carac") or {}).get("SAG", 10) or 10)
                _mod_g = _rangs_g + (_sag_g - 10) // 2 + _bd_g(
                    _f_g.get("dons"), "comp_survie")
                if _mod_g >= _mod_survie_g:
                    _guide = str(_f_g.get("nom") or "")
                    _mod_survie_g = _mod_g
                    _detail_survie = (f"{_rangs_g} rangs, SAG {_sag_g} "
                                      f"({_mod_g:+d})")
        except Exception:                                    # noqa: BLE001
            pass
        _perdu_jours: list[int] = []
        for jour in range(1, jours_total + 1):
            if random.randint(1, 100) <= tdata["egarer"]:
                _perdu_jours.append(jour)
        if _perdu_jours:
            _details_dd: list[str] = []
            for _j in _perdu_jours:
                _dd_j = _dd_terrain
                try:
                    _eff_j = _meteo_jours[_j - 1]["effet"]
                    if _eff_j in ("pluie", "brouillard", "neige", "orage",
                                  "tempete"):
                        _dd_j = max(_dd_j, 12)   # mauvaise visibilité (DRS)
                except (IndexError, KeyError):
                    pass
                _details_dd.append(f"J{_j} : DD {_dd_j}")
            egarement = (
                f"- 🧭 **Égarement** : tirage hors-piste ({tdata['egarer']} "
                f" %/jour) → le groupe S'ÉGARE le(s) jour(s) "
                f"{', '.join(str(j) for j in _perdu_jours)}. Jet de Survie "
                f"du guide ({_guide or 'aucun PJ qualifié'}, Survie "
                f"{_detail_survie}) : {', '.join(_details_dd)} (table DRS, "
                "mauvaise visibilité = plancher 12). Réussite = direction "
                "tenue ; échec = journée perdue (+1 jour, rations/eau en "
                "moins — `voyage_repas` s'applique) ; reconnaître l'égarement "
                "= Survie DD 20 (−1/heure)."
            )
        else:
            egarement = (
                f"- 🧭 Égarement : aucun échec sur {jours_total} jour(s) "
                f"({tdata['egarer']} %/jour hors-piste)."
            )
        lignes.append(egarement)

    # --- Météo : annonce jour par jour (déjà tirée ci-dessus) ----------------
    _dangereux = [m for m in _meteo_jours
                  if m["effet"] in ("canicule", "froid", "tempete", "orage",
                                    "neige")]
    lignes.append(
        f"- 🌦️ **Météo jour par jour** ({region_n}/{_saison}) : "
        + " | ".join(f"J{m['jour']} {m['txt'].split('—')[0].strip()}"
                     for m in _meteo_jours))
    if _dangereux:
        lignes.append(
            "  ➡️ ⚡ Chaque jour à effet (canicule/froid/tempête/neige) DOIT "
            "être appliqué mécaniquement : appelle **`voyage_meteo_jour("
            "jour=N)`** au fil de la narration (jets de Vigueur serveur, "
            "dégâts non-létaux, conditions Fatigué).")

    # --- Rations (chantier DRS P2-#8) : 1 ration/jour/PJ ---------------------
    _rations_note = ""
    try:
        from .fiches import _load_fiche as _lf_r, _save_fiche as _sf_r
        from .inventaire import _consommer_fragment as _cf_r
        _manquantes: list[str] = []
        for _p_r in (_etat_vd.get("pj") or []):
            _nom_r = str(_p_r.get("nom") or "")
            _f_r = _lf_r(ctx, _nom_r)
            if _f_r is None:
                continue
            _pris_r = 0
            for _ in range(min(jours_total, 10)):
                if _cf_r(_f_r, "ration", 1):
                    _pris_r += 1
                else:
                    break
            _sf_r(ctx, _nom_r, _f_r)
            _reste_r = jours_total - _pris_r
            if _reste_r > 0:
                _manquantes.append(
                    f"{_nom_r} (−{_pris_r} rations, **{_reste_r} jour(s) "
                    "sans**)")
            elif _pris_r:
                pass
        if _manquantes:
            _rations_note = (
                "  ➡️ 🍖 **Rations insuffisantes** : " + ", ".join(_manquantes)
                + ". Appelle **`voyage_repas(jour=N)`** pour chaque jour de "
                "voyage : les tests de CON (faim/soif, DD 10 +1/précédent, "
                "1d6 non-létaux + Fatigué) sont appliqués serveur. Une "
                "**`voyage_prospection`** (Survie) peut compléter.")
    except Exception:                                        # noqa: BLE001
        _rations_note = ""
    if _rations_note:
        lignes.append(_rations_note)
    else:
        lignes.append(
            "- 🍖 Rations : consommées dans l'inventaire de chaque PJ "
            f"({jours_total} jour(s) couverts). Appelle `voyage_repas` par "
            "jour si tu veux tracer les repas à la table.")

    lignes.append(
        "\n➡️ **Le MJ DOIT narrer le voyage jour par jour** (temps qui passe, "
        "repas, veilles), appliquer chaque rencontre/météo ci-dessus via les "
        "outils dédiés, puis patcher l'état (`lieu.nom`) à l'arrivée."
    )

    resume = (
        f"Voyage vers {destination} : {jours_total}j, "
        f"{len(rencontres)} rencontre(s)"
    )
    voyage_etat = {
        "destination": destination,
        "distance_km": distance_km,
        "jours": jours_total,
        "jours_rencontres": rencontres,
        "jours_meteo": _meteo_jours,
        "jours_perdus": _perdu_jours,
        "region_climat": region_n,
        "saison": _saison,
        "marche_forcee": bool(forcee and a_pied),
        "resume": resume,
    }
    # Persistance côté serveur (state_patch seul = miroir front uniquement).
    try:
        from ..game.state import PartyState   # lazy : évite tout cycle d'import
        ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = ps.load()
        # 🧭 F6 (audit eb46aeef) : départ DEPUIS un donjon → on restaure la
        # localité monde d'entrée (`localite_entree`) comme position de
        # départ, sinon `lieu.nom` reste le donjon et le monde n'a plus de
        # position (« Où êtes-vous ? ») — la navigation monde repart d'ici.
        try:
            _lieu_v = etat.get("lieu") or {}
            _dj_v = etat.get("donjon") or {}
            if str(_lieu_v.get("type") or "") == "donjon":
                _loc_v = str(_dj_v.get("localite_entree") or "").strip()
                if _loc_v:
                    def _coord_v(v: Any) -> Any:
                        try:
                            f = float(v)
                            return f if 0 <= f <= 100 else ""
                        except (TypeError, ValueError):
                            return ""
                    etat["lieu"] = {
                        "nom": _loc_v,
                        "type": "localite",
                        "description": "",
                        "position_x": _coord_v(_lieu_v.get("position_x")),
                        "position_y": _coord_v(_lieu_v.get("position_y")),
                    }
        except Exception:                                            # noqa: BLE001
            pass
        etat["voyage"] = voyage_etat
        ps.save(etat)
    except Exception as e:                                       # noqa: BLE001
        return ToolResult(
            text="\n".join(lignes) + f"\n⚠️ État voyage non persisté : {e}",
            state_patch={"voyage": voyage_etat},
        )
    return ToolResult(
        text="\n".join(lignes),
        state_patch={"voyage": voyage_etat},
    )


# --------------------------------------------------------------------------- #
#  ⚡ Météo, rations et prospection — mécanique SERVEUR du voyage
#  (chantier DRS P1-#5 / P2-#8, DRS « Chaleur », « Le froid »,
#  « La faim et la soif »)
# --------------------------------------------------------------------------- #
def _mod_vigueur(fiche: dict) -> tuple[int, str]:
    """Modificateur officiel de Vigueur d'un PJ : total de fiche + dons
    (Grande Fortitude) + objets « Résistance +N »."""
    try:
        total = int((fiche.get("sauvegardes") or {}).get("Vigueur", 0) or 0)
    except (TypeError, ValueError):
        total = 0
    detail = f"Vigueur {total:+d}"
    try:
        from .fiches import bonus_dons_effet, bonus_sauvegarde_magie
        d = bonus_dons_effet(fiche.get("dons"), "sauvegarde_vigueur")
        m = bonus_sauvegarde_magie(fiche)
        total += d + m
        if d:
            detail += f" +{d} (dons)"
        if m:
            detail += f" +{m} (Résistance)"
    except Exception:                                        # noqa: BLE001
        pass
    return total, detail


def _porte_armure(fiche: dict) -> bool:
    """Vrai si le PJ porte une armure du catalogue (malus −4 contre la
    chaleur, PHB 3.5)."""
    try:
        from ..catalogue import ARMURES
        from .inventaire import _inventaire
        noms = {_norm(e.get("nom")) for e in _inventaire(fiche)}
        return any(_norm(a["nom"]) in noms for a in ARMURES)
    except Exception:                                        # noqa: BLE001
        return False


def _survie_pj(fiche: dict) -> tuple[int, str]:
    """Bonus Survie d'un PJ (rangs + SAG + dons) — aide contre chaleur/froid."""
    try:
        rangs = int((fiche.get("competences") or {}).get("Survie", 0) or 0)
        sag = int((fiche.get("carac") or {}).get("SAG", 10) or 10)
        from .fiches import bonus_dons_effet
        d = bonus_dons_effet(fiche.get("dons"), "comp_survie")
        return rangs + (sag - 10) // 2 + d, f"Survie {rangs}+{(sag-10)//2:+d}"
    except Exception:                                        # noqa: BLE001
        return 0, ""


async def _jets_extremes(
    ctx: ToolContext, res_lignes: list[str], type_danger: str,
    nb_jets: int, dd_base: int, deg_par_echec: int,
    etat: dict,
) -> None:
    """Jets de Vigueur contre chaleur/froid pour TOUT le groupe : 1 jet par
    tranche de temps (heure / 10 min), échec = non-létaux + Fatigué (3.5 :
    tout PJ qui subit des non-létaux de chaleur/froid est Fatigué)."""
    from .fiches import (
        fiche_perso_infliger_degats, fiche_perso_condition,
        _load_fiche, _save_fiche,
    )
    from ..game.regles35 import jets_chaleur, jets_froid
    from ..game.state import PartyState
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    for p in etat.get("pj") or []:
        nom = str(p.get("nom") or "")
        fiche = _load_fiche(ctx, nom)
        if fiche is None:
            continue
        if _pj_hors_combat_etat(p):
            continue
        mod, detail = _mod_vigueur(fiche)
        survie, det_survie = _survie_pj(fiche)
        armure = _porte_armure(fiche)
        if type_danger == "chaleur":
            res_j = jets_chaleur(dd_base=dd_base, nb_heures=nb_jets,
                                 armure=armure, survie=survie)
        else:
            res_j = jets_froid(dd_base=dd_base, nb_jets=nb_jets,
                               survie=survie)
        nl = int(res_j.get("non_letal") or 0)
        echecs = sum(1 for j in res_j["jets"] if not j["reussi"])
        res_lignes.append(
            f"🌡️ **{nom}** — {type_danger} : {nb_jets} jet(s) de Vigueur "
            f"(DD {dd_base}+1/précédent ; {detail}"
            + (f", {det_survie}" if det_survie else "")
            + (", armure −4" if armure and type_danger == "chaleur" else "")
            + f") → {echecs} échec(s), **{nl} dégâts non-létaux**."
        )
        if nl > 0:
            tr = await fiche_perso_infliger_degats(
                ctx, nom, nl, source=type_danger, non_letal=True)
            res_lignes.append("- " + tr.text.splitlines()[0])
            # Fatigué (3.5) tant que les non-létaux de chaleur/froid persistent.
            fiche2 = _load_fiche(ctx, nom)
            if fiche2 is not None:
                conds = fiche2.setdefault("conditions", [])
                if "Fatigué" not in conds:
                    tr_c = await fiche_perso_condition(ctx, nom, "Fatigué",
                                                       True)
                    res_lignes.append("- " + tr_c.text.splitlines()[0])


def _pj_hors_combat_etat(p: dict) -> bool:
    conds = [str(c).lower() for c in (p.get("conditions") or [])]
    return p.get("pv", 0) <= -10 or "mort" in conds


@tool
async def voyage_meteo_jour(ctx: ToolContext, jour: int = 1) -> ToolResult:
    """
    Applique MÉCANIQUEMENT la météo d'un jour de voyage (tirée par
    `voyage_demarrer`, stockée dans l'état) : chaleur accablante (jets de
    Vigueur PAR HEURE, DD 15 +1/précédent, échec = 1d4 non-létaux + Fatigué,
    armure −4), froid (1d6 non-létaux), froid vif (jets par 10 min),
    tempête (Vigueur DD 18/h si le groupe force le passage). Le MJ appelle
    ce tool en narrant chaque journée concernée.

    :param jour (int): numéro du jour de voyage concerné (1 = premier jour).
    """
    from ..game.state import PartyState
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    voyage = etat.get("voyage") or {}
    jours = voyage.get("jours_meteo") or []
    j = next((m for m in jours if int(m.get("jour", 0)) == int(jour or 1)),
             None)
    if j is None:
        return ToolResult(text=(
            f"❌ Aucune météo enregistrée pour le jour {jour} (lance "
            "`voyage_demarrer` d'abord — la météo y est tirée jour par "
            "jour)."))
    effet, txt = str(j.get("effet") or "clair"), str(j.get("txt") or "")
    lignes = [f"🌦️ **Météo du jour {jour}** — {txt}"]
    if effet == "canicule":
        # Désert en plein jour = chaleur TORRIDE (≥ 45 °C : 1 jet/10 min).
        torride = str(voyage.get("region_climat") or "") == "desert"
        await _jets_extremes(
            ctx, lignes, "chaleur",
            nb_jets=8 if not torride else 24,   # 8 h vs 8×10 min ≈ 24 jets
            dd_base=15, deg_par_echec=4, etat=etat)
        if torride:
            lignes.append(
                "  🔥 Chaleur TORRIDE (désert) : les non-létaux passés en "
                "inconscience deviennent LÉTAUX (1d4/h) — surveille les PV.")
    elif effet == "froid":
        froid_vif = _norm(str(j.get("txt") or "")).find("-15") >= 0 \
            or "coup de froid" in _norm(str(j.get("txt") or ""))
        await _jets_extremes(
            ctx, lignes, "froid",
            nb_jets=8 if not froid_vif else 24,
            dd_base=15, deg_par_echec=6, etat=etat)
    elif effet == "tempete":
        lignes.append(
            "  🛑 **Voyage impossible aujourd'hui** (vents ≥ 80 km/h) : "
            "+1 jour de voyage. Si la table FORCE le passage : Vigueur "
            "DD 18 par heure de marche (échec = 1d3 non-létaux) — appelle "
            "`lancer_sauvegarde` pour chaque PJ par heure.")
        voyage["jours"] = int(voyage.get("jours") or 0) + 1
        voyage["jours_perdus"] = list(voyage.get("jours_perdus") or []) \
            + [int(jour or 1)]
        etat["voyage"] = voyage
        ps.save(etat)
    elif effet in ("pluie", "neige", "brouillard", "orage", "vent"):
        lignes.append(
            "  🧭 Visibilité/perception réduite : égarement DD 12 minimum "
            "ce jour, Perception pénalisée — pas de jet automatique (pas de "
            "danger physiologique).")
    else:
        lignes.append("  ⛅ Aucun effet mécanique (temps clair).")
    return ToolResult(text="\n".join(lignes))


@tool
async def voyage_repas(ctx: ToolContext, jour: int = 1) -> ToolResult:
    """
    Repas du soir d'un jour de voyage : consomme 1 ration (et de l'eau) par
    PJ depuis son inventaire. Un PJ SANS ration passe un test de CON par
    jour au-delà de 3 jours sans manger (DD 10, +1 par test précédent) —
    échec = 1d6 dégâts non-létaux + Fatigué (règle 3.5 « La faim et la
    soif »). Un jour en désert sans eau compte DOUBLE.

    :param jour (int): numéro du jour de voyage (1 = premier jour).
    """
    from .fiches import (
        _load_fiche, _save_fiche, fiche_perso_infliger_degats,
        fiche_perso_condition,
    )
    from .inventaire import _consommer_fragment
    from ..game.state import PartyState
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    voyage = etat.get("voyage") or {}
    en_desert = str(voyage.get("region_climat") or "") == "desert"
    lignes = [f"🍖 **Repas du jour {jour}**"
              + (" (désert : eau ×2)" if en_desert else "")]
    for p in etat.get("pj") or []:
        nom = str(p.get("nom") or "")
        if _pj_hors_combat_etat(p):
            continue
        fiche = _load_fiche(ctx, nom)
        if fiche is None:
            continue
        consomme = _consommer_fragment(fiche, "ration", 1)
        if consomme:
            _save_fiche(ctx, nom, fiche)
            lignes.append(f"- ✅ {nom} : 1 × {consomme} consommée.")
            continue
        # Pas de ration : compteur de jours de faim sur la fiche.
        jours_faim = int(fiche.get("jours_sans_ration", 0) or 0) + 1
        fiche["jours_sans_ration"] = jours_faim
        if jours_faim <= 3:
            _save_fiche(ctx, nom, fiche)
            lignes.append(
                f"- ⚠️ {nom} : AUCUNE ration (jour {jours_faim}/3 sans "
                "manger) — à 4 jours, les tests de CON commencent.")
            continue
        con_val = int((fiche.get("carac") or {}).get("CON", 10) or 10)
        mod_con = (con_val - 10) // 2
        dd = 10 + (jours_faim - 4) + (1 if en_desert else 0)
        jet = random.randint(1, 20)
        total = jet + mod_con
        if jet == 20 or (total >= dd and jet != 1):
            _save_fiche(ctx, nom, fiche)
            lignes.append(
                f"- 🍽️ {nom} affamé (jour {jours_faim}) : CON {con_val} "
                f"({mod_con:+d}) → {jet}+{mod_con} = {total} vs DD {dd} → "
                "✅ tient le coup (à la PROCHAINE défaite uniquement : "
                "1d6 non-létaux).")
        else:
            d6 = random.randint(1, 6)
            tr = await fiche_perso_infliger_degats(
                ctx, nom, d6, source="faim", non_letal=True)
            tr_c = await fiche_perso_condition(ctx, nom, "Fatigué", True)
            _save_fiche(ctx, nom, fiche)
            lignes.append(
                f"- ❌ {nom} affamé (jour {jours_faim}) : {jet}+{mod_con} = "
                f"{total} vs DD {dd} → échec, **{d6} non-létaux** "
                + tr_c.text.splitlines()[0] + ".")
    return ToolResult(text="\n".join(lignes))


@tool
async def voyage_prospection(ctx: ToolContext, nom_personnage: str = "") -> ToolResult:
    """
    Prospection alimentaire (3.5 « Survie ») : le PJ guide cherche
    nourriture et eau pour le groupe pendant une heure de repos. Succès
    (DD 12, DD 15 en désert/marais) → 1d3 rations ajoutées à SON inventaire
    (règle 3.5 : nourrit une personne par tranche de 2 degrés de maîtrise).
    Échec → rien (et un jour de retard si le MJ l'exige).

    :param nom_personnage (str): nom du PJ qui prospecte (le mieux classé en
        Survie par défaut).
    """
    from .fiches import _load_fiche, _save_fiche
    from ..game.state import PartyState
    ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
    etat = ps.load()
    # Guide par défaut : meilleur Survie du groupe.
    if not str(nom_personnage or "").strip():
        best, best_mod = "", -99
        from .fiches import _load_fiche as _lf
        for p in etat.get("pj") or []:
            fiche = _lf(ctx, str(p.get("nom") or ""))
            if fiche is None:
                continue
            m, _d = _survie_pj(fiche)
            if m > best_mod:
                best, best_mod = str(fiche.get("nom") or ""), m
        nom_personnage = best
    fiche = _load_fiche(ctx, str(nom_personnage or ""))
    if fiche is None:
        return ToolResult(text=f"❌ Aucune fiche pour « {nom_personnage} ».")
    region = str((etat.get("voyage") or {}).get("region_climat") or "tempere")
    dd = 15 if region in ("desert", "marais") else 12
    mod, detail = _survie_pj(fiche)
    jet = random.randint(1, 20)
    total = jet + mod
    lignes = [
        f"🌿 **Prospection** — {nom_personnage} cherche nourriture et eau",
        f"- Jet de Survie : {jet}+{mod} = {total} vs DD {dd} ({detail}"
        + (f" ; région {region} : DD {dd}" if dd == 15 else "") + ")",
    ]
    if jet == 1:
        lignes.append("- ❌ Échec automatique — rien trouvé aujourd'hui.")
        return ToolResult(text="\n".join(lignes))
    if total >= dd or jet == 20:
        import random as _r
        nb = _r.randint(1, 3)
        inv = fiche.setdefault("inventaire", [])
        entree = next(
            (e for e in inv if _norm(e.get("nom", "")) == "ration"
             or "ration" in _norm(e.get("nom", ""))), None)
        if entree is not None:
            entree["qte"] = int(entree.get("qte", 1) or 1) + nb
        else:
            inv.append({"nom": "Rations journalières", "qte": nb})
        _save_fiche(ctx, str(fiche.get("nom") or nom_personnage), fiche)
        lignes.append(
            f"- ✅ Réussite — **{nb} ration(s)** trouvée(s) et ajoutées à "
            "l'inventaire (l'eau de l'outre est renouvelée).")
    else:
        lignes.append(
            "- ❌ Échec — la forêt/le désert ne livre rien : les rations ne "
            "se reconstituent pas aujourd'hui (les compteurs "
            "`jours_sans_ration` s'accumulent via `voyage_repas`).")
    return ToolResult(text="\n".join(lignes))
