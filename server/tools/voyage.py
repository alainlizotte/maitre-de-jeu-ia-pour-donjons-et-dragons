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

# Météo saisonnière (SRD Weather, simplifiée) : d100 → effet.
_METEO: list[tuple[int, int, str]] = [
    (1, 8,   "☀️ **Canicule** — jets de CON contre la chaleur toutes les heures "
             "passées en mouvement (DD 11, +1 par jour consécutif) ; échec = "
             "1d4 dégâts non létaux."),
    (9, 25,  "🌧️ **Pluie** — Perception/Repérage et Ouïe −4 ; sol boueux."),
    (26, 40, "🌫️ **Brouillard** — visibilité réduite, Perception −4, risque "
             "d'égarement doublé."),
    (41, 88, "⛅ **Temps clair** — aucune contrainte."),
    (89, 96, "⛈️ **Orage** — Perception −8, déplacement ×½ hors route."),
    (97, 100, "🌪️ **Tempête violente** — voyage impossible aujourd'hui "
              "(ou Fortitude DC 18/h pour continuer, échec = 1d3 non-létaux)."),
]


def _norm(s: str) -> str:
    import unicodedata
    nf = unicodedata.normalize("NFKD", (s or "").lower().strip())
    return "".join(c for c in nf if not unicodedata.combining(c))


@tool
async def voyage_demarrer(
    ctx: ToolContext,
    destination: str,
    distance_km: float,
    mode: str = "marche",
    terrain: str = "plaine",
    piste: bool = False,
) -> ToolResult:
    """
    Lance un voyage hors donjon et calcule sa durée réelle selon les règles
    D&D 3.5 (Movement/Wilderness) : allure, terrain, rencontres aléatoires
    quotidiennes, risque de s'égarer, météo, marche forcée. OBLIGATOIRE dès
    qu'un groupe quitte un lieu pour un autre — jamais de téléportation.

    :param destination (str): nom du lieu de destination (ex. "Phandalin").
    :param distance_km (float): distance à vol d'oiseau/route en kilomètres.
    :param mode (str): "lent" | "marche" | "rapide" | "cheval" | "cheval_rapide".
        "rapide" à pied = marche forcée (jets de CON, fatigue).
    :param terrain (str): "route" | "plaine" | "collines" | "foret" |
        "desert" | "marais" | "montagne".
    :param piste (bool): True si sentier/route connue (jamais perdu) ;
        False par défaut en terrain inconnu.
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
    if distance_km <= 0:
        return ToolResult(text="⚠️ distance_km doit être > 0.")

    tdata = _TERRAINS[terrain_n]
    allure_label, base_kmj = _ALLURES[mode_n]
    km_jour_effectifs = base_kmj * tdata["f"]

    # Durée : on avance jour par jour (le dernier peut être partiel).
    jours_total = max(1, math.ceil(distance_km / km_jour_effectifs))

    # Marche forcée à pied (« rapide ») : au-delà de 8 h, jets de CON.
    forcee = mode_n == "rapide"
    a_pied = mode_n not in ("cheval", "cheval_rapide")

    lignes = [
        f"🗺️ **Voyage vers {destination}**",
        f"- Terrain : {tdata['label']} ({'piste connue' if piste else 'hors-piste'}) "
        f"— allure : {allure_label} ({base_kmj:.0f} km/jour × facteur "
        f"{tdata['f']:.2f} = **{km_jour_effectifs:.0f} km/jour**)",
        f"- Distance : {distance_km:.0f} km → durée : **{jours_total} jour(s)** "
        f"(8 h de marche par jour)",
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

    # --- Égarement (Survie) --------------------------------------------------
    egarement = ""
    if not piste and tdata["egarer"] > 0:
        for jour in range(1, jours_total + 1):
            if random.randint(1, 100) <= tdata["egarer"]:
                egarement = (
                    f"- 🧭 **Égaré le jour {jour}** ! Jet de Survie (DD "
                    f"{10 + jours_total}) du guide pour retrouver la direction ; "
                    "sinon journée perdue (+1 jour de voyage, nourriture/eau "
                    "en moins)."
                )
                break
        if not egarement:
            egarement = (
                f"- 🧭 Égarement : aucun échec sur {jours_total} jour(s) "
                f"({tdata['egarer']} %/jour hors-piste)."
            )
        lignes.append(egarement)

    # --- Météo du premier jour -------------------------------------------------
    r100 = random.randint(1, 100)
    meteo = next(
        (txt for lo, hi, txt in _METEO if lo <= r100 <= hi), _METEO[3][2]
    )
    lignes.append(f"- Jour 1 — {meteo}")

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
        "marche_forcee": bool(forcee and a_pied),
        "resume": resume,
    }
    # Persistance côté serveur (state_patch seul = miroir front uniquement).
    try:
        from ..game.state import PartyState   # lazy : évite tout cycle d'import
        ps = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id)
        etat = ps.load()
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
