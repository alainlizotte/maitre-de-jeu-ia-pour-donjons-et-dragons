"""Familiers (Magicien/Sorcier) et compagnons animaux (Druide/Rodeur) — PHB 3.5.

Sources officielles (regles-donjons-dragons.com) :
- pages 60/64 « Appel de familier » : liste des 10 espèces, facultés
  spéciales transmises au maître, table de progression (Aj. d'armure
  naturelle, Int, pouvoirs), profil (PV = moitié du maître, BBA du maître +
  meilleur mod For/Dex, sauvegardes au meilleur des deux) ;
- page 48 « Compagnon animal » : liste de départ du druide, compagnons
  hors-normes (niveau −ajustement), table de progression (DV sup., Aj.
  d'armure naturelle, Aj. For/Dex, tours supplémentaires, pouvoirs).

Les stats de BASE de chaque espèce viennent du bestiaire
(server/data/bestiaire.json) ; ce module calcule la VERSION LIÉE AU MAÎTRE
(profil ajusté) et valide les choix du formulaire de création.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

# --------------------------------------------------------------------------- #
#  Classes éligibles
# --------------------------------------------------------------------------- #
CLASSES_FAMILIER = {"Magicien", "Sorcier"}
CLASSES_COMPAGNON = {"Druide", "Rodeur"}
# Niveau minimal de classe pour le compagnon animal (druide : dès le niv 1).
NIVEAU_MIN_COMPAGNON: dict[str, int] = {"Druide": 1, "Rodeur": 4}
# Niveau effectif : le rôdeur compte −3 niveaux de classe pour son compagnon.
AJUSTEMENT_NIVEAU_COMPAGNON: dict[str, int] = {"Rodeur": 3}

# --------------------------------------------------------------------------- #
#  Familiers — table PHB 3.5 (pages 60/64)
# --------------------------------------------------------------------------- #
FAMILIERS: list[dict[str, str]] = [
    {"nom": "Belette", "cle": "belette",
     "faculte": "Le maître obtient un bonus de +2 aux jets de Réflexes."},
    {"nom": "Chat", "cle": "chat",
     "faculte": "Le maître obtient un bonus de +3 aux tests de Déplacement silencieux."},
    {"nom": "Chauve-souris", "cle": "chauve_souris",
     "faculte": "Le maître obtient un bonus de +3 aux tests de Perception auditive."},
    {"nom": "Chouette", "cle": "chouette",
     "faculte": "Le maître obtient un bonus de +3 aux tests de Détection dans l'obscurité."},
    {"nom": "Corbeau", "cle": "corbeau",
     "faculte": "Le maître obtient un bonus de +3 aux tests d'Estimation "
                "(le corbeau parle une langue au choix du maître)."},
    {"nom": "Crapaud", "cle": "crapaud",
     "faculte": "Le maître obtient +3 points de vie."},
    {"nom": "Faucon", "cle": "faucon",
     "faculte": "Le maître obtient un bonus de +3 aux tests de Détection sous une lumière vive."},
    {"nom": "Lézard", "cle": "lezard",
     "faculte": "Le maître obtient un bonus de +2 aux tests d'Escalade."},
    {"nom": "Rat", "cle": "rat",
     "faculte": "Le maître obtient un bonus de +2 aux jets de Vigueur."},
    {"nom": "Serpent (très petit venimeux)", "cle": "serpent_venimeux_tres_petit",
     "faculte": "Le maître obtient un bonus de +3 aux tests de Bluff."},
]

# Niveau de classe du maître → (min, max, Aj. armure naturelle, Int, pouvoirs).
# Table PHB 3.5 : 1-2 → +1/6 ; 3-4 → +2/7 ; … 19-20 → +10/15.
_PROGRESSION_FAMILIER: list[tuple[int, int, int, int, list[str]]] = [
    (1, 2, 1, 6, ["Esquive extraordinaire", "Transfert d'effet magique",
                  "Lien télépathique", "Vigilance"]),
    (3, 4, 2, 7, ["Conduit (porte les sorts de contact)"]),
    (5, 6, 3, 8, ["Communication avec le maître"]),
    (7, 8, 4, 9, ["Communication avec les animaux du même type"]),
    (9, 10, 5, 10, []),
    (11, 12, 6, 11, ["Résistance à la magie (niveau du maître + 5)"]),
    (13, 14, 7, 12, ["Scrutation sur le familier (1/jour)"]),
    (15, 16, 8, 13, []),
    (17, 18, 9, 14, []),
    (19, 20, 10, 15, []),
]

# --------------------------------------------------------------------------- #
#  Compagnons animaux — table PHB 3.5 (page 48)
# --------------------------------------------------------------------------- #
COMPAGNONS: list[dict[str, str]] = [
    {"nom": "Aigle", "cle": "aigle"},
    {"nom": "Blaireau", "cle": "blaireau"},
    {"nom": "Chameau", "cle": "chameau"},
    {"nom": "Cheval léger", "cle": "cheval_leger"},
    {"nom": "Cheval lourd", "cle": "cheval_lourd"},
    {"nom": "Chien", "cle": "chien"},
    {"nom": "Chien de selle", "cle": "chien_de_selle"},
    {"nom": "Chouette", "cle": "chouette"},
    {"nom": "Faucon", "cle": "faucon"},
    {"nom": "Loup", "cle": "loup"},
    {"nom": "Poney", "cle": "poney"},
    {"nom": "Rat sanguinaire", "cle": "rat_sanguinaire"},
    {"nom": "Serpent venimeux (petit)", "cle": "serpent_venimeux_petit"},
    {"nom": "Serpent venimeux (moyen)", "cle": "serpent_venimeux_moyen"},
]

# Compagnons hors-normes : niveau de classe minimal requis et réduction du
# niveau effectif du druide (ex. « Niveau 4 et plus (Niveau −3) »).
COMPAGNONS_HORS_NORME: list[dict[str, Any]] = [
    {"nom": "Belette sanguinaire", "cle": "belette_sanguinaire", "niveau_min": 4, "reduction": 3},
    {"nom": "Blaireau sanguinaire", "cle": "blaireau_sanguinaire", "niveau_min": 4, "reduction": 3},
    {"nom": "Chauve-souris sanguinaire", "cle": "chauve_souris_sanguinaire", "niveau_min": 4, "reduction": 3},
    {"nom": "Crocodile", "cle": "crocodile", "niveau_min": 4, "reduction": 3},
    {"nom": "Glouton", "cle": "glouton", "niveau_min": 4, "reduction": 3},
    {"nom": "Gorille", "cle": "gorille", "niveau_min": 4, "reduction": 3},
    {"nom": "Guépard", "cle": "guepard", "niveau_min": 4, "reduction": 3},
    {"nom": "Léopard", "cle": "leopard", "niveau_min": 4, "reduction": 3},
    {"nom": "Ours noir", "cle": "ours_noir", "niveau_min": 4, "reduction": 3},
    {"nom": "Sanglier", "cle": "sanglier", "niveau_min": 4, "reduction": 3},
    {"nom": "Serpent constricteur", "cle": "serpent_constricteur", "niveau_min": 4, "reduction": 3},
    {"nom": "Varan", "cle": "lezard_carnivore_varan", "niveau_min": 4, "reduction": 3},
    {"nom": "Ours brun", "cle": "ours_brun", "niveau_min": 7, "reduction": 6},
    {"nom": "Crocodile géant", "cle": "crocodile_geant", "niveau_min": 7, "reduction": 6},
    {"nom": "Deinonychus", "cle": "deinonychus", "niveau_min": 7, "reduction": 6},
    {"nom": "Lion", "cle": "lion", "niveau_min": 7, "reduction": 6},
    {"nom": "Rhinocéros", "cle": "rhinoceros", "niveau_min": 7, "reduction": 6},
    {"nom": "Tigre", "cle": "tigre", "niveau_min": 7, "reduction": 6},
    {"nom": "Lion sanguinaire", "cle": "lion_sanguinaire", "niveau_min": 10, "reduction": 9},
    {"nom": "Mégaraptor", "cle": "megaraptor", "niveau_min": 10, "reduction": 9},
    {"nom": "Ours polaire", "cle": "ours_polaire", "niveau_min": 10, "reduction": 9},
    {"nom": "Éléphant", "cle": "elephant", "niveau_min": 13, "reduction": 12},
    {"nom": "Ours sanguinaire", "cle": "ours_sanguinaire", "niveau_min": 13, "reduction": 12},
    {"nom": "Pieuvre géante", "cle": "pieuvre_geante", "niveau_min": 13, "reduction": 12},
    {"nom": "Tricératops", "cle": "triceratops", "niveau_min": 16, "reduction": 15},
    {"nom": "Tyrannosaure", "cle": "tyrannosaure", "niveau_min": 16, "reduction": 15},
]

# Niveau de classe → (min, max, DV sup., Aj. armure naturelle, Aj. For/Dex,
# tours sup., pouvoirs). Table PHB 3.5.
_PROGRESSION_COMPAGNON: list[tuple[int, int, int, int, int, int, list[str]]] = [
    (1, 2, 0, 0, 0, 1, ["Lien", "Transfert d'effet magique"]),
    (3, 5, 2, 2, 1, 2, ["Esquive totale"]),
    (6, 8, 4, 4, 2, 3, ["Dévotion"]),
    (9, 11, 6, 6, 3, 4, ["Attaques multiples"]),
    (12, 14, 8, 8, 4, 5, []),
    (15, 17, 10, 10, 5, 6, ["Esquive extraordinaire"]),
    (18, 20, 12, 12, 6, 7, []),
]

# --------------------------------------------------------------------------- #
#  Helpers de lecture du bestiaire
# --------------------------------------------------------------------------- #
_RE_DV = re.compile(r"^(?:(\d+)\s*/\s*(\d+)\s*)?(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?")
_RE_CARAC = re.compile(r"(For|Dex|Con|Int|Sag|Cha)\s+(\d+)")
_RE_SAVE = re.compile(r"(Réf|Vig|Vol)\s*([+-]\d+)")
_MAP_SAVE = {"Réf": "Reflexes", "Vig": "Vigueur", "Vol": "Volonte"}
_MAP_CARAC = {"For": "FOR", "Dex": "DEX", "Con": "CON",
              "Int": "INT", "Sag": "SAG", "Cha": "CHA"}


def mod_carac(valeur: int) -> int:
    return (int(valeur) - 10) // 2


def _parser_dv(dv_str: str) -> tuple[float, int]:
    """« 3d8+6 (19 pv) » → (3.0, 6) ; « 1/4d8 (1 pv) » → (0.25, 0)."""
    m = _RE_DV.search(str(dv_str or "").lower())
    if not m:
        return 1.0, 0
    if m.group(1) and m.group(2):
        nb = int(m.group(1)) / int(m.group(2))
    else:
        nb = float(m.group(3) or 1)
    bonus = 0
    if m.group(5):
        bonus = int(m.group(5).replace(" ", ""))
    return nb, bonus


def _parser_carac(carac_str: str) -> dict[str, int]:
    res: dict[str, int] = {v: 10 for v in _MAP_CARAC.values()}
    for nom, val in _RE_CARAC.findall(str(carac_str or "")):
        cle = _MAP_CARAC.get(nom)
        if cle:
            res[cle] = int(val)
    return res


def _parser_sauves(sauv_str: str) -> dict[str, int]:
    res = {"Vigueur": 0, "Reflexes": 0, "Volonte": 0}
    for nom, val in _RE_SAVE.findall(str(sauv_str or "")):
        cle = _MAP_SAVE.get(nom)
        if cle:
            res[cle] = int(val)
    return res


def charger_bestiaire(data_dir: str) -> dict[str, dict[str, Any]]:
    """Charge data_dir/bestiaire.json → {clé: entrée} (hors _meta)."""
    path = os.path.join(data_dir, "bestiaire.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(v.get("cle") or k): v
        for k, v in raw.items()
        if k != "_meta" and isinstance(v, dict) and "nom" in v
    }


def charger_animal(data_dir: str, cle: str) -> dict[str, Any] | None:
    return charger_bestiaire(data_dir).get(cle)


# --------------------------------------------------------------------------- #
#  Niveaux effectifs et espèces disponibles
# --------------------------------------------------------------------------- #
def niveau_effectif_compagnon(classe: str, niveau: int) -> int:
    """Niveau de classe compté pour le compagnon (rôdeur : niveau − 3)."""
    niv = max(1, int(niveau or 1))
    return max(0, niv - AJUSTEMENT_NIVEAU_COMPAGNON.get(classe, 0))


def especes_disponibles(classe: str, niveau: int) -> list[dict[str, Any]]:
    """Espèces choisissables par la classe au niveau donné (pour le modèle /
    le formulaire). Familier : 10 espèces (Magicien/Sorcier). Compagnon :
    liste de départ + hors-normes dont le niveau de classe suffit."""
    niv = max(1, int(niveau or 1))
    if classe in CLASSES_FAMILIER:
        return [{"type": "familier", **f} for f in FAMILIERS]
    if classe in CLASSES_COMPAGNON and niv >= NIVEAU_MIN_COMPAGNON.get(classe, 1):
        res = [{"type": "compagnon", **c} for c in COMPAGNONS]
        niv_eff = niveau_effectif_compagnon(classe, niv)
        for hn in COMPAGNONS_HORS_NORME:
            if niv_eff - hn["reduction"] >= 1 and niv >= hn["niveau_min"]:
                res.append({
                    "type": "compagnon", "nom": hn["nom"], "cle": hn["cle"],
                    "faculte": f"Hors-norme : niveau effectif −{hn['reduction']}.",
                    "niveau_min": hn["niveau_min"], "reduction": hn["reduction"],
                })
        return res
    return []


def trouver_espece(nom: str) -> dict[str, Any] | None:
    """Cherche une espèce (familier OU compagnon, hors-normes inclus) par son
    nom canonique (insensible casse/accents grossiers)."""
    cible = (nom or "").strip().lower()
    for f in FAMILIERS:
        if f["nom"].lower() == cible:
            return {"type": "familier", **f}
    for c in COMPAGNONS:
        if c["nom"].lower() == cible:
            return {"type": "compagnon", **c}
    for hn in COMPAGNONS_HORS_NORME:
        if hn["nom"].lower() == cible:
            return {"type": "compagnon", "faculte": "", **hn}
    return None


def type_compagnon_classe(classe: str) -> str | None:
    """« familier », « compagnon » ou None selon la classe."""
    if classe in CLASSES_FAMILIER:
        return "familier"
    if classe in CLASSES_COMPAGNON:
        return "compagnon"
    return None


def valider_choix(
    payload: Any, classe: str, niveau: int
) -> dict[str, Any]:
    """Valide le choix de familier/compagnon du formulaire.

    Renvoie le dict à stocker en fiche (`{type, espece, invoque}`) ou lève
    ValueError avec un message utilisateur.
    """
    if not isinstance(payload, dict):
        raise ValueError("Champ familier invalide.")
    type_ = str(payload.get("type") or "").strip().lower()
    espece_saisie = str(payload.get("espece") or "").strip()
    niv = max(1, int(niveau or 1))

    attendu = type_compagnon_classe(classe)
    if attendu is None:
        raise ValueError(
            f"La classe {classe} n'a ni familier ni compagnon animal."
        )
    if not espece_saisie:
        raise ValueError("Espèce du familier/compagnon manquante.")
    espece = trouver_espece(espece_saisie)
    if espece is None:
        raise ValueError(f"Espèce inconnue : « {espece_saisie} ».")
    if espece["type"] != attendu:
        raise ValueError(
            f"« {espece['nom']} » est un {espece['type']}, pas un {attendu} — "
            f"incompatible avec {classe}."
        )
    if attendu == "compagnon":
        niv_min = NIVEAU_MIN_COMPAGNON.get(classe, 1)
        if niv < niv_min:
            raise ValueError(
                f"Le {classe} obtient son compagnon animal au niveau {niv_min} "
                f"(niveau actuel : {niv})."
            )
        if "niveau_min" in espece:
            niv_eff = niveau_effectif_compagnon(classe, niv)
            if niv < int(espece["niveau_min"]) or \
                    niv_eff - int(espece["reduction"]) < 1:
                raise ValueError(
                    f"« {espece['nom']} » (hors-norme) exige un {classe} de "
                    f"niveau {espece['niveau_min']} minimum "
                    f"(niveau effectif −{espece['reduction']})."
                )
    return {"type": attendu, "espece": espece["nom"], "invoque": False}


# --------------------------------------------------------------------------- #
#  Progression — familier
# --------------------------------------------------------------------------- #
def progression_familier(niveau_maitre: int) -> dict[str, Any]:
    """Table PHB : Aj. d'armure naturelle, Int, pouvoirs cumulés."""
    niv = max(1, min(20, int(niveau_maitre or 1)))
    aj, intel, pouvoirs = 1, 6, []
    for min_, max_, a, i, p in _PROGRESSION_FAMILIER:
        if min_ <= niv <= max_:
            aj, intel = a, i
            break
    for min_, max_, a, i, p in _PROGRESSION_FAMILIER:
        if min_ <= niv:
            pouvoirs.extend(p)
    return {"aj_naturelle": aj, "int": intel, "pouvoirs": pouvoirs}


def profil_familier(fiche: dict[str, Any], animal: dict[str, Any]) -> dict[str, Any]:
    """Profil du familier LIÉ au maître (règles pages 60/64) :
    PV = moitié de pv_max du maître ; CA = espèce + Aj. naturelle ;
    BBA = BBA du maître + meilleur mod For/Dex ; DV = niveau du maître
    (ou ceux de la créature si supérieurs) ; sauvegardes au meilleur des
    deux (maître / animal)."""
    niv_maitre = max(1, int(fiche.get("niveau") or 1))
    prog = progression_familier(niv_maitre)
    try:
        pv_maitre = max(1, int(fiche.get("pv_max") or 1))
    except (TypeError, ValueError):
        pv_maitre = 1
    carac = _parser_carac(animal.get("carac", ""))
    dv_eff = max(niv_maitre, math.ceil(_parser_dv(str(animal.get("dv", "1d8")))[0]))

    bab_maitre = int(fiche.get("bab") or 0)
    bonus_att = max(mod_carac(carac["FOR"]), mod_carac(carac["DEX"]))
    sauves_animal = _parser_sauves(animal.get("sauvegardes", ""))
    sauves_maitre = fiche.get("sauvegardes") or {}
    sauves = {
        cle: max(int(sauves_animal.get(cle, 0)),
                 int(sauves_maitre.get(cle, 0) or 0))
        for cle in ("Vigueur", "Reflexes", "Volonte")
    }
    return {
        "espece": str(animal.get("nom", "")),
        "type": "familier",
        "pv": max(1, pv_maitre // 2),
        "ca": int(animal.get("ca") or 10) + prog["aj_naturelle"],
        "bba_attaque": bab_maitre + bonus_att,
        "dv": dv_eff,        "int": prog["int"],
        "sauvegardes": sauves,
        "attaques": animal.get("attaques", "—"),
        "degats": animal.get("degs", "—"),
        "faculte_maitre": next(
            (f["faculte"] for f in FAMILIERS
             if f["nom"] == str(animal.get("nom", ""))),
            ""),
        "pouvoirs": prog["pouvoirs"],
    }


# --------------------------------------------------------------------------- #
#  Progression — compagnon animal
# --------------------------------------------------------------------------- #
def progression_compagnon(niveau_classe: int) -> dict[str, Any]:
    """Table PHB (niveau de classe effectif) : DV sup., Aj. armure naturelle,
    Aj. For/Dex, tours supplémentaires, pouvoirs cumulés."""
    niv = max(1, min(20, int(niveau_classe or 1)))
    dv_sup = aj_nat = aj_for_dex = tours = 0
    for min_, max_, dv, an, fd, tr, _p in _PROGRESSION_COMPAGNON:
        if min_ <= niv <= max_:
            dv_sup, aj_nat, aj_for_dex, tours = dv, an, fd, tr
            break
    pouvoirs: list[str] = []
    for min_, max_, dv, an, fd, tr, p in _PROGRESSION_COMPAGNON:
        if min_ <= niv:
            pouvoirs.extend(p)
    return {"dv_sup": dv_sup, "aj_naturelle": aj_nat, "aj_for_dex": aj_for_dex,
            "tours": tours, "pouvoirs": pouvoirs}


def _bba_moyen(dv: float) -> int:
    return int(math.floor(dv * 3 / 4))


def _base_sauves(dv: float) -> dict[str, int]:
    """BBA moyen (comme druide), Réf/Vig élevées, Vol faible (PHB p.48)."""
    n = int(math.floor(dv))
    return {
        "Vigueur": 2 + n // 2,
        "Reflexes": 2 + n // 2,
        "Volonte": n // 3,
    }


def profil_compagnon(fiche: dict[str, Any], animal: dict[str, Any]) -> dict[str, Any]:
    """Profil du compagnon animal LIÉ au druide/rôdeur (page 48) :
    DV sup. ajoutés (d8, mod Con), CA + Aj. naturelle, For/Dex ajustées,
    BBA recalculé (moyen) sur les DV totaux, sauvegardes recalculées."""
    classe = str(fiche.get("classe") or "Druide")
    niv_eff = niveau_effectif_compagnon(classe, fiche.get("niveau") or 1)
    prog = progression_compagnon(niv_eff)

    dv_base, _bonus = _parser_dv(animal.get("dv", "1d8"))
    dv_total = dv_base + prog["dv_sup"]
    carac = _parser_carac(animal.get("carac", ""))
    carac["FOR"] += prog["aj_for_dex"]
    carac["DEX"] += prog["aj_for_dex"]

    bonus_con = mod_carac(carac["CON"])
    pv_animal = int(animal.get("pv") or 1)
    pv = pv_animal + int(math.floor(4.5 * prog["dv_sup"])) \
        + bonus_con * prog["dv_sup"]

    bases = _base_sauves(dv_total)
    sauves = {
        "Vigueur": bases["Vigueur"] + bonus_con,
        "Reflexes": bases["Reflexes"] + mod_carac(carac["DEX"]),
        "Volonte": bases["Volonte"] + mod_carac(carac["SAG"]),
    }
    return {
        "espece": str(animal.get("nom", "")),
        "type": "compagnon",
        "pv": max(1, pv),
        "ca": int(animal.get("ca") or 10) + prog["aj_naturelle"],
        "bba_attaque": _bba_moyen(dv_total),
        "dv": int(math.ceil(dv_total)),
        "for": carac["FOR"],
        "dex": carac["DEX"],
        "sauvegardes": sauves,
        "attaques": animal.get("attaques", "—"),
        "degats": animal.get("degs", "—"),
        "tours": prog["tours"],
        "niveau_effectif": niv_eff,
        "pouvoirs": prog["pouvoirs"],
    }


def profil(fiche: dict[str, Any], animal: dict[str, Any]) -> dict[str, Any]:
    """Profil ajusté selon le type stocké en fiche (familier OU compagnon)."""
    info = fiche.get("familier") or {}
    type_ = str(info.get("type") or "") or type_compagnon_classe(
        str(fiche.get("classe") or "")) or "familier"
    if type_ == "compagnon":
        return profil_compagnon(fiche, animal)
    return profil_familier(fiche, animal)


# --------------------------------------------------------------------------- #
#  Modèle pour le frontend (espèces + stats de base du bestiaire)
# --------------------------------------------------------------------------- #
def modele_pour_client(data_dir: str) -> dict[str, Any]:
    """Listes espèces avec stats de base (formulaire + calculs côté client)."""
    bestiaire = charger_bestiaire(data_dir)

    def _avec_stats(entree: dict[str, Any]) -> dict[str, Any]:
        animal = bestiaire.get(entree["cle"], {})
        return {
            **entree,
            "pv": animal.get("pv"),
            "ca": animal.get("ca"),
            "degats": animal.get("degs", "—"),
            "dv": animal.get("dv", ""),
        }

    fams = [_avec_stats({**f}) for f in FAMILIERS]
    comps = [_avec_stats({**c}) for c in COMPAGNONS]
    hors = [_avec_stats({**hn}) for hn in COMPAGNONS_HORS_NORME]
    return {
        "classes_familier": sorted(CLASSES_FAMILIER),
        "classes_compagnon": sorted(CLASSES_COMPAGNON),
        "niveau_min_compagnon": NIVEAU_MIN_COMPAGNON,
        "familiers": fams,
        "compagnons_animaux": comps,
        "compagnons_hors_norme": hors,
        "progression_familier": [
            {"niveaux": f"{a}-{b}", "aj_naturelle": an, "int": i, "pouvoirs": p}
            for a, b, an, i, p in _PROGRESSION_FAMILIER
        ],
        "progression_compagnon": [
            {"niveaux": f"{a}-{b}", "dv_sup": dv, "aj_naturelle": an,
             "aj_for_dex": fd, "tours": tr, "pouvoirs": p}
            for a, b, dv, an, fd, tr, p in _PROGRESSION_COMPAGNON
        ],
    }
