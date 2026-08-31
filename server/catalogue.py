"""Catalogues D&D 3.5 pour la création de personnage.

Sources : Manuel des Joueurs 3.5 (armes/armures/dons/compétences/maîtrises),
or de départ par classe (table PHB). Les noms utilisent les canoniques du
projet (« Rodeur », « Demi-orc »…). Ces catalogues sont indicatifs côté
enregistrement (pas de validation stricte) et contraignants côté affichage :
le frontend grise les choix indisponibles selon race/classe/caractéristiques.
"""

from __future__ import annotations

import random
import re
from typing import Any


# --------------------------------------------------------------------------- #
#  Maîtrises par classe (PHB 3.5)
# --------------------------------------------------------------------------- #
# armures   : catégories maîtrisées ("Legere", "Moyenne", "Lourde")
# boucliers : maîtrise des boucliers
# groupes   : groupes d'armes maîtrisés ("simple", "martiale")
# specifiques : armes précises maîtrisées en plus des groupes
PROFICIENCES: dict[str, dict[str, Any]] = {
    "Barbare":  {"armures": ["Legere", "Moyenne"], "boucliers": True,
                 "groupes": ["simple", "martiale"], "specifiques": []},
    "Barde":    {"armures": ["Legere"], "boucliers": True,
                 "groupes": ["simple"],
                 "specifiques": ["Épée longue", "Rapière", "Épée courte"]},
    "Clerc":    {"armures": ["Legere", "Moyenne", "Lourde"], "boucliers": True,
                 "groupes": ["simple"], "specifiques": []},
    "Druide":   {"armures": ["Legere", "Moyenne"], "boucliers": True,
                 "groupes": [],
                 "specifiques": ["Bâton", "Dague", "Dard", "Javeline",
                                 "Masse d'armes légère", "Faucille", "Fronde",
                                 "Lance courte"]},
    "Guerrier": {"armures": ["Legere", "Moyenne", "Lourde"], "boucliers": True,
                 "groupes": ["simple", "martiale"], "specifiques": []},
    "Magicien": {"armures": [], "boucliers": False,
                 "groupes": [],
                 "specifiques": ["Matraque", "Dague", "Arbalète légère",
                                 "Arbalète lourde", "Bâton"]},
    "Moine":    {"armures": [], "boucliers": False,
                 "groupes": [],
                 "specifiques": ["Matraque", "Dague", "Bâton", "Javeline"]},
    "Paladin":  {"armures": ["Legere", "Moyenne", "Lourde"], "boucliers": True,
                 "groupes": ["simple", "martiale"], "specifiques": []},
    "Rodeur":   {"armures": ["Legere", "Moyenne"], "boucliers": True,
                 "groupes": ["simple", "martiale"], "specifiques": []},
    "Sorcier":  {"armures": [], "boucliers": False,
                 "groupes": [],
                 "specifiques": ["Matraque", "Dague", "Arbalète légère", "Bâton"]},
    "Voleur":   {"armures": ["Legere"], "boucliers": False,
                 "groupes": ["simple"],
                 "specifiques": ["Épée courte", "Rapière", "Arc court"]},
}


# --------------------------------------------------------------------------- #
#  Armes (sélection courante niveau 1)
# --------------------------------------------------------------------------- #
# groupe : "simple" | "martiale" ; distance=True = arme à distance.
ARMES: list[dict[str, Any]] = [
    # Simples, corps à corps
    {"nom": "Bâton",                  "groupe": "simple",   "distance": False, "degats": "1d6",      "cout": 0,   "poids": 1.81},
    {"nom": "Matraque",               "groupe": "simple",   "distance": False, "degats": "1d6",      "cout": 0,   "poids": 1.36},
    {"nom": "Dague",                  "groupe": "simple",   "distance": True,  "degats": "1d4",      "cout": 2,   "poids": 0.45},
    {"nom": "Masse d'armes légère",   "groupe": "simple",   "distance": False, "degats": "1d6",      "cout": 8,   "poids": 1.81},
    {"nom": "Faucille",               "groupe": "simple",   "distance": False, "degats": "1d6",      "cout": 8,   "poids": 0.91},
    {"nom": "Lance courte",           "groupe": "simple",   "distance": True,  "degats": "1d6",      "cout": 1,   "poids": 1.36},
    # Simples, distance
    {"nom": "Javeline",               "groupe": "simple",   "distance": True,  "degats": "1d6",      "cout": 1,   "poids": 0.91},
    {"nom": "Fronde",                 "groupe": "simple",   "distance": True,  "degats": "1d4",      "cout": 0,   "poids": 0.0},
    {"nom": "Arbalète légère",        "groupe": "simple",   "distance": True,  "degats": "1d8",      "cout": 35,  "poids": 1.81},
    {"nom": "Arbalète lourde",        "groupe": "simple",   "distance": True,  "degats": "1d10",     "cout": 50,  "poids": 3.63},
    {"nom": "Lance",                  "groupe": "simple",   "distance": True,  "degats": "1d8",      "cout": 2,   "poids": 2.72},
    # Martiales, corps à corps
    {"nom": "Épée longue",            "groupe": "martiale", "distance": False, "degats": "1d8",      "cout": 15,  "poids": 1.81},
    {"nom": "Épée courte",            "groupe": "martiale", "distance": False, "degats": "1d6",      "cout": 10,  "poids": 0.91},
    {"nom": "Rapière",                "groupe": "martiale", "distance": False, "degats": "1d6",      "cout": 20,  "poids": 0.91},
    {"nom": "Hache d'arme",           "groupe": "martiale", "distance": False, "degats": "1d8",      "cout": 30,  "poids": 2.72},
    {"nom": "Hache à deux mains",     "groupe": "martiale", "distance": False, "degats": "1d12",     "cout": 40,  "poids": 5.44},
    {"nom": "Espadon",                "groupe": "martiale", "distance": False, "degats": "2d6",      "cout": 50,  "poids": 3.63},
    {"nom": "Masse d'armes lourde",   "groupe": "martiale", "distance": False, "degats": "1d8",      "cout": 12,  "poids": 3.63},
    {"nom": "Fléau d'armes",          "groupe": "martiale", "distance": False, "degats": "1d8",      "cout": 15,  "poids": 4.54},
    {"nom": "Marteau de guerre",      "groupe": "martiale", "distance": False, "degats": "1d8",      "cout": 12,  "poids": 2.27},
    {"nom": "Glaive",                 "groupe": "martiale", "distance": False, "degats": "1d10",     "cout": 8,   "poids": 4.54},
    {"nom": "Hallebarde",             "groupe": "martiale", "distance": False, "degats": "1d10",     "cout": 10,  "poids": 5.44},
    # Martiales, distance
    {"nom": "Arc court",              "groupe": "martiale", "distance": True,  "degats": "1d6",      "cout": 30,  "poids": 0.91},
    {"nom": "Arc long",               "groupe": "martiale", "distance": True,  "degats": "1d8",      "cout": 75,  "poids": 1.36},
]

_ARMES_PAR_NOM = {a["nom"]: a for a in ARMES}


# --------------------------------------------------------------------------- #
#  Armures et boucliers
# --------------------------------------------------------------------------- #
# categorie : "Legere" | "Moyenne" | "Lourde" | "Bouclier"
ARMURES: list[dict[str, Any]] = [
    {"nom": "Armure rembourrée",    "categorie": "Legere",  "ca": 1, "dex_max": 8, "malus": 0, "cout": 5,     "poids": 4.54},
    {"nom": "Armure de cuir",       "categorie": "Legere",  "ca": 2, "dex_max": 6, "malus": 0, "cout": 10,    "poids": 6.80},
    {"nom": "Cuir clouté",          "categorie": "Legere",  "ca": 3, "dex_max": 5, "malus": 1, "cout": 25,    "poids": 9.07},
    {"nom": "Chemise de mailles",   "categorie": "Moyenne", "ca": 4, "dex_max": 4, "malus": 2, "cout": 100,   "poids": 11.34},
    {"nom": "Cuir épais",           "categorie": "Moyenne", "ca": 3, "dex_max": 4, "malus": 3, "cout": 15,    "poids": 11.34},
    {"nom": "Armure d'écailles",    "categorie": "Moyenne", "ca": 4, "dex_max": 3, "malus": 4, "cout": 50,    "poids": 13.61},
    {"nom": "Cotte de mailles",     "categorie": "Lourde",  "ca": 5, "dex_max": 2, "malus": 5, "cout": 150,   "poids": 18.14},
    {"nom": "Plastron",             "categorie": "Lourde",  "ca": 5, "dex_max": 3, "malus": 4, "cout": 200,   "poids": 13.61},
    {"nom": "Harnois complet",      "categorie": "Lourde",  "ca": 8, "dex_max": 1, "malus": 6, "cout": 1500,  "poids": 22.68},
    {"nom": "Targe",                "categorie": "Bouclier", "ca": 1, "dex_max": None, "malus": 1, "cout": 15, "poids": 2.27},
    {"nom": "Bouclier bois léger",  "categorie": "Bouclier", "ca": 1, "dex_max": None, "malus": 1, "cout": 3,  "poids": 2.27},
    {"nom": "Bouclier bois lourd",  "categorie": "Bouclier", "ca": 2, "dex_max": None, "malus": 2, "cout": 7,  "poids": 4.54},
]


# --------------------------------------------------------------------------- #
#  Équipement d'aventurier (accessible à toutes les classes)
# --------------------------------------------------------------------------- #
EQUIPEMENT: list[dict[str, Any]] = [
    {"nom": "Sac à dos",              "cout": 2, "poids": 0.91},
    {"nom": "Sac de couchage",        "cout": 0, "poids": 2.27},
    {"nom": "Lit de camp",            "cout": 0, "poids": 2.27},
    {"nom": "Outre à eau",            "cout": 0, "poids": 1.81},
    {"nom": "Corde de chanvre (15 m)", "cout": 1, "poids": 4.54},
    {"nom": "Torche",                 "cout": 0, "poids": 0.45},
    {"nom": "Silex et amorce",        "cout": 1, "poids": 0.0},
    {"nom": "Lanterne à capuchon",    "cout": 7, "poids": 0.91},
    {"nom": "Huile (pinte)",          "cout": 0, "poids": 0.45},
    {"nom": "Rations journalières",   "cout": 1, "poids": 0.45},
    {"nom": "Gibecière",              "cout": 2, "poids": 0.23},
    {"nom": "Kit premiers secours",   "cout": 1, "poids": 0.45},
    {"nom": "Grappin et corde",       "cout": 1, "poids": 1.81},
    {"nom": "Flèches (20)",           "cout": 1, "poids": 1.36},
    {"nom": "Carreaux (10)",          "cout": 1, "poids": 0.45},
    {"nom": "Pierre à aiguiser",      "cout": 0, "poids": 0.45},
    {"nom": "Savon",                  "cout": 0, "poids": 0.23},
    {"nom": "Craie",                  "cout": 0, "poids": 0.05},
]


# --------------------------------------------------------------------------- #
#  Dons courants niveau 1
# --------------------------------------------------------------------------- #
# prereq : conditions chiffrées vérifiables sur les caractéristiques finales
# ou le BBA. Vide = accessible à tous.
DONS: list[dict[str, Any]] = [
    {"nom": "Alerte",             "condition": "",                     "prereq": {}},
    {"nom": "Initiative améliorée", "condition": "",                   "prereq": {}},
    {"nom": "Course",             "condition": "",                     "prereq": {}},
    {"nom": "Dur à cuire (+3 PV)", "condition": "",                    "prereq": {}},
    {"nom": "Esquive",            "condition": "DEX 13+",              "prereq": {"dex": 13}},
    {"nom": "Ambidextrie",        "condition": "DEX 13+",              "prereq": {"dex": 13}},
    {"nom": "Combat à deux armes", "condition": "DEX 15+",             "prereq": {"dex": 15}},
    {"nom": "Attaque en puissance", "condition": "FOR 13+",            "prereq": {"for": 13}},
    {"nom": "Attaque en finesse", "condition": "BBA 1+",               "prereq": {"bab": 1}},
    {"nom": "Expertise combatif", "condition": "INT 13+",              "prereq": {"int": 13}},
    {"nom": "Tir de près",        "condition": "",                     "prereq": {}},
    {"nom": "Tir en mouvement",   "condition": "DEX 13+",              "prereq": {"dex": 13}},
    {"nom": "Volonté de fer",     "condition": "",                     "prereq": {"sag": 13}},
    {"nom": "Arme de prédilection", "condition": "une arme au choix",  "prereq": {}},
]


# --------------------------------------------------------------------------- #
#  Compétences
# --------------------------------------------------------------------------- #
# Liste maîtresse (nom, caractéristique associée).
COMPETENCES: list[dict[str, str]] = [
    {"nom": "Alchimie",               "cara": "INT"},
    {"nom": "Concentration",          "cara": "CON"},
    {"nom": "Connaissance (religion)", "cara": "INT"},
    {"nom": "Connaissance des sorts", "cara": "INT"},
    {"nom": "Décryptage",             "cara": "INT"},
    {"nom": "Déguisement",            "cara": "CHA"},
    {"nom": "Détection",              "cara": "SAG"},
    {"nom": "Déplacement silencieux", "cara": "DEX"},
    {"nom": "Desceller",              "cara": "DEX"},
    {"nom": "Discrétion",             "cara": "DEX"},
    {"nom": "Diplomatie",             "cara": "CHA"},
    {"nom": "Équilibre",              "cara": "DEX"},
    {"nom": "Escalade",               "cara": "FOR"},
    {"nom": "Escamotage",             "cara": "DEX"},
    {"nom": "Estimation",             "cara": "INT"},
    {"nom": "Fouille",                "cara": "INT"},
    {"nom": "Intimidation",           "cara": "CHA"},
    {"nom": "Interprétation",         "cara": "CHA"},
    {"nom": "Maîtrise des animaux",   "cara": "CHA"},
    {"nom": "Natation",               "cara": "FOR"},
    {"nom": "Perception auditive",    "cara": "SAG"},
    {"nom": "Premiers secours",       "cara": "SAG"},
    {"nom": "Psychologie",            "cara": "SAG"},
    {"nom": "Saut",                   "cara": "FOR"},
    {"nom": "Survie",                 "cara": "SAG"},
    {"nom": "Équitation",             "cara": "DEX"},
]

# Compétences de classe par classe (les autres sont « hors classe » → grisées).
COMPETENCES_CLASSE: dict[str, list[str]] = {
    "Barbare":  ["Escalade", "Intimidation", "Saut", "Natation", "Équitation",
                 "Perception auditive", "Survie", "Maîtrise des animaux"],
    "Barde":    ["Concentration", "Décryptage", "Déplacement silencieux",
                 "Diplomatie", "Détection", "Discrétion", "Équilibre",
                 "Escamotage", "Estimation", "Intimidation", "Interprétation",
                 "Perception auditive", "Premiers secours", "Psychologie",
                 "Fouille", "Déguisement", "Connaissance des sorts"],
    "Clerc":    ["Concentration", "Diplomatie", "Psychologie",
                 "Premiers secours", "Connaissance (religion)",
                 "Connaissance des sorts"],
    "Druide":   ["Concentration", "Diplomatie", "Maîtrise des animaux",
                 "Perception auditive", "Premiers secours", "Survie",
                 "Connaissance des sorts"],
    "Guerrier": ["Escalade", "Intimidation", "Saut", "Natation", "Équitation"],
    "Magicien": ["Concentration", "Alchimie", "Estimation", "Fouille",
                 "Connaissance des sorts"],
    "Moine":    ["Équilibre", "Escalade", "Saut", "Concentration",
                 "Déplacement silencieux", "Psychologie",
                 "Perception auditive"],
    "Paladin":  ["Concentration", "Diplomatie", "Équitation",
                 "Premiers secours", "Psychologie"],
    "Rodeur":   ["Déplacement silencieux", "Discrétion", "Escalade",
                 "Natation", "Équitation", "Saut", "Perception auditive",
                 "Survie", "Maîtrise des animaux", "Fouille",
                 "Premiers secours", "Psychologie"],
    "Sorcier":  ["Concentration", "Connaissance des sorts", "Estimation"],
    "Voleur":   ["Équilibre", "Déplacement silencieux", "Desceller",
                 "Discrétion", "Escalade", "Escamotage", "Estimation",
                 "Fouille", "Perception auditive", "Décryptage",
                 "Déguisement", "Intimidation", "Saut"],
}

# Points de compétence par niveau (avant mod. INT ; niveau 1 ×4).
POINTS_COMPETENCE: dict[str, int] = {
    "Barbare": 4, "Barde": 6, "Clerc": 2, "Druide": 4, "Guerrier": 2,
    "Magicien": 2, "Moine": 4, "Paladin": 2, "Rodeur": 6, "Sorcier": 2,
    "Voleur": 8,
}


# --------------------------------------------------------------------------- #
#  Or de départ (table PHB 3.5, par classe)
# --------------------------------------------------------------------------- #
OR_DEPART: dict[str, dict[str, Any]] = {
    "Barbare":  {"des": "4d4", "mult": 10},   # 40–160 po
    "Barde":    {"des": "6d4", "mult": 10},   # 60–240 po
    "Clerc":    {"des": "5d4", "mult": 10},   # 50–200 po
    "Druide":   {"des": "2d4", "mult": 10},   # 20–80 po
    "Guerrier": {"des": "6d4", "mult": 10},   # 60–240 po
    "Magicien": {"des": "3d4", "mult": 10},   # 30–120 po
    "Moine":    {"des": "5d4", "mult": 1},    # 5–20 po (roublard-like)
    "Paladin":  {"des": "6d4", "mult": 10},
    "Rodeur":   {"des": "6d4", "mult": 10},
    "Sorcier":  {"des": "3d4", "mult": 10},
    "Voleur":   {"des": "5d4", "mult": 10},   # 50–200 po
}


def formule_or_depart(classe: str) -> str:
    """Formule lisible, ex. « 6d4 × 10 po ». Chaîne vide si classe inconnue."""
    f = OR_DEPART.get((classe or "").strip())
    if not f:
        return ""
    return f"{f['des']} × {f['mult']} po"


def tirer_or_depart(classe: str, mode: str = "tirage") -> int:
    """Or de départ selon la table PHB.

    mode="tirage"  : lance les dés (ex. 6d4 × 10).
    mode="moyenne" : valeur moyenne officielle arrondie au supérieur
                     (ex. guerrier = 150 po, moine = 13 po).
    """
    f = OR_DEPART.get((classe or "").strip())
    if not f:
        return 0
    m = re.fullmatch(r"(\d+)d(\d+)", f["des"])
    if not m:
        return 0
    nb, faces, mult = int(m.group(1)), int(m.group(2)), f["mult"]
    if mode == "moyenne":
        import math
        return int(math.floor(nb * (faces + 1) / 2 * mult + 0.5))
    return sum(random.randint(1, faces) for _ in range(nb)) * mult


# --------------------------------------------------------------------------- #
#  Disponibilités
# --------------------------------------------------------------------------- #
def armes_disponibles(classe: str) -> set[str]:
    """Noms d'armes que la classe peut utiliser sans pénalité."""
    prof = PROFICIENCES.get((classe or "").strip())
    if prof is None:
        return {a["nom"] for a in ARMES}  # pas de classe choisie → tout montrer
    dispo: set[str] = set(prof.get("specifiques", []))
    for a in ARMES:
        if a["groupe"] in prof.get("groupes", []):
            dispo.add(a["nom"])
    return dispo


def armures_disponibles(classe: str) -> set[str]:
    """Noms d'armures/boucliers que la classe peut porter sans pénalité."""
    prof = PROFICIENCES.get((classe or "").strip())
    if prof is None:
        return {x["nom"] for x in ARMURES}
    cats = set(prof.get("armures", []))
    avec_boucliers = bool(prof.get("boucliers"))
    dispo: set[str] = set()
    for x in ARMURES:
        if x["categorie"] in cats or (avec_boucliers and x["categorie"] == "Bouclier"):
            dispo.add(x["nom"])
    return dispo


def don_disponible(don: dict[str, Any], carac_final: dict[str, int], bab: int) -> bool:
    """Vérifie les prérequis chiffrés d'un don (carac. finales + BBA)."""
    p = don.get("prereq") or {}
    if p.get("for", 0) > carac_final.get("FOR", 10):
        return False
    if p.get("dex", 0) > carac_final.get("DEX", 10):
        return False
    if p.get("int", 0) > carac_final.get("INT", 10):
        return False
    if p.get("sag", 0) > carac_final.get("SAG", 10):
        return False
    if p.get("bab", 0) > bab:
        return False
    return True


def dons_disponibles(carac_final: dict[str, int], bab: int) -> list[dict[str, Any]]:
    """Liste des dons avec leur disponibilité pour ces caractéristiques."""
    return [
        {**d, "disponible": don_disponible(d, carac_final, bab)}
        for d in DONS
    ]


def points_competence(classe: str, mod_int: int, niveau: int, humain: bool) -> int:
    """Budget total de rangs de compétence (info indicative, PHB 3.5)."""
    base = POINTS_COMPETENCE.get((classe or "").strip(), 0)
    if base == 0:
        return 0
    par_niveau = max(1, base + mod_int)
    total = par_niveau * 4 + max(1, base + mod_int) * max(0, niveau - 1)
    if humain:
        total += niveau  # +1 rang/niveau pour les humains
    return total
