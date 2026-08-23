"""Personnages joueurs — catalogue races/classes, calculs D&D 3.5 et portraits.

Ce module alimente le formulaire de création de personnage du frontend :
- catalogues de races (ajustements raciaux + traits visuels pour le portrait)
  et de classes (dé de vie, BBA, sauvegardes) ;
- calcul automatique des valeurs dérivées (mods, PV, CA, BBA, sauvegardes,
  initiative) conformes au Manuel du Joueur 3.5 ;
- tirage aléatoire des caractéristiques (4d6, on garde les 3 meilleurs) ;
- construction du prompt de portrait ComfyUI à partir de la fiche remplie
  (identité + apparence + traits raciaux).

Les fiches restent stockées dans `data/fiches/fiche_<slug>.json` (compatibles
avec les tools MJ existants) avec un champ `proprietaire` = compte utilisateur.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import unicodedata
from typing import Any, Optional

from .tools.fiches import _CLASSES_35, _slug


# --------------------------------------------------------------------------- #
#  Catalogue des races (PHB 3.5)
# --------------------------------------------------------------------------- #
# `traits_visuels` : fragments anglais injectés dans le prompt ComfyUI pour que
# le portrait respecte la physionomie de la race choisie.
RACES: dict[str, dict[str, Any]] = {
    "Humain": {
        "mods": {},
        "taille": "M",
        "vitesse": 9,
        "traits_visuels": "human features",
    },
    "Elfe": {
        "mods": {"DEX": 2, "CON": -2},
        "taille": "M",
        "vitesse": 9,
        "traits_visuels": "long pointed ears, slender graceful build, ageless elegant elven features",
    },
    "Nain": {
        "mods": {"CON": 2, "CHA": -2},
        "taille": "M",
        "vitesse": 6,
        "traits_visuels": "short stout muscular dwarf, long braided beard, broad shoulders",
    },
    "Halfelin": {
        "mods": {"DEX": 2, "FOR": -2},
        "taille": "P",
        "vitesse": 6,
        "traits_visuels": "very small halfling, curly hair, large hairy feet, cheerful round face",
    },
    "Gnome": {
        "mods": {"CON": 2, "FOR": -2},
        "taille": "P",
        "vitesse": 6,
        "traits_visuels": "small gnome, large nose, pointed ears, mischievous smile",
    },
    "Demi-elfe": {
        "mods": {},
        "taille": "M",
        "vitesse": 9,
        "traits_visuels": "half-elf with subtly pointed ears blending human and elven features",
    },
    "Demi-orc": {
        "mods": {"FOR": 2, "INT": -2, "CHA": -2},
        "taille": "M",
        "vitesse": 9,
        "traits_visuels": "tall muscular half-orc, grey-green skin, small tusks, heavy brow",
    },
}

# Alias acceptés à l'écriture (FR/EN) → nom canonique du catalogue.
_RACE_ALIAS = {
    "humain": "Humain", "human": "Humain",
    "elfe": "Elfe", "elf": "Elfe", "haut elfe": "Elfe",
    "nain": "Nain", "dwarf": "Nain",
    "halfelin": "Halfelin", "halfling": "Halfelin", "semi homme": "Halfelin",
    "gnome": "Gnome",
    "demi elfe": "Demi-elfe", "half elf": "Demi-elfe",
    "demi orc": "Demi-orc", "half orc": "Demi-orc",
}


def _normaliser(texte: str) -> str:
    """Minuscules sans accents pour les lookups d'alias."""
    nf = unicodedata.normalize("NFKD", (texte or "").lower())
    return "".join(c for c in nf if not unicodedata.combining(c)).replace("-", " ").strip()


def resoudre_race(race: str) -> str:
    """Normalise une race saisie vers le canon du catalogue ('' si inconnue)."""
    cle = _normaliser(race)
    if cle in _RACE_ALIAS:
        return _RACE_ALIAS[cle]
    for alias, canon in _RACE_ALIAS.items():
        if alias and (alias in cle or cle in alias):
            return canon
    return ""


# --------------------------------------------------------------------------- #
#  Catalogue des classes (PHB 3.5)
# --------------------------------------------------------------------------- #
# Progressions officielles : BBA bon/moyen/mauvais ; sauvegardes bonnes = base
# 2 + niv/2, mauvaises = niv/3 (arrondi inférieur). Dé de vie par niveau.
CLASSES: dict[str, dict[str, Any]] = {
    "Barbare":   {"de_vie": 12, "bab": "bon",    "sauves_bonnes": ["Vigueur"]},
    "Barde":     {"de_vie": 6,  "bab": "moyen",  "sauves_bonnes": ["Reflexes", "Volonte"]},
    "Clerc":     {"de_vie": 8,  "bab": "moyen",  "sauves_bonnes": ["Vigueur", "Volonte"]},
    "Druide":    {"de_vie": 8,  "bab": "moyen",  "sauves_bonnes": ["Vigueur", "Volonte"]},
    "Guerrier":  {"de_vie": 10, "bab": "bon",    "sauves_bonnes": ["Vigueur"]},
    "Magicien":  {"de_vie": 4,  "bab": "mauvais", "sauves_bonnes": ["Volonte"]},
    "Moine":     {"de_vie": 8,  "bab": "moyen",  "sauves_bonnes": ["Vigueur", "Reflexes", "Volonte"]},
    "Paladin":   {"de_vie": 10, "bab": "bon",    "sauves_bonnes": ["Vigueur"]},
    "Rodeur":    {"de_vie": 8,  "bab": "moyen",  "sauves_bonnes": ["Vigueur", "Reflexes"]},
    "Sorcier":   {"de_vie": 4,  "bab": "mauvais", "sauves_bonnes": ["Volonte"]},
    "Voleur":    {"de_vie": 6,  "bab": "moyen",  "sauves_bonnes": ["Reflexes"]},
}

_CLASSE_ALIASES = {k.lower(): k for k in CLASSES}
_CLASSE_ALIASES.update({
    "guerrier": "Guerrier", "fighter": "Guerrier", "warrior": "Guerrier",
    "guerriere": "Guerrier",
    "barbarian": "Barbare",
    "ranger": "Rodeur", "rodeur": "Rodeur", "rodeuse": "Rodeur",
    "rogue": "Voleur", "roublard": "Voleur", "voleur": "Voleur", "thief": "Voleur",
    "voleuse": "Voleur",
    "bard": "Barde", "barde": "Barde",
    "monk": "Moine", "moine": "Moine", "moniale": "Moine",
    "cleric": "Clerc", "clerc": "Clerc", "pretre": "Clerc", "priest": "Clerc",
    "pretresse": "Clerc",
    "druid": "Druide", "druide": "Druide", "druidesse": "Druide",
    "wizard": "Magicien", "mage": "Magicien", "magicien": "Magicien",
    "magicienne": "Magicien", "sorciere": "Magicien",
    "sorcerer": "Sorcier", "sorcier": "Sorcier", "ensorcelleur": "Sorcier",
    "sorciere": "Sorcier",
})


def resoudre_classe(classe: str) -> str:
    """Normalise une classe saisie vers le canon du catalogue ('' si inconnue)."""
    return _CLASSE_ALIASES.get(_normaliser(classe), "")


ALIGNEMENTS = [
    "Loyal Bon", "Neutre Bon", "Chaotique Bon",
    "Loyal Neutre", "Neutre", "Chaotique Neutre",
    "Loyal Mauvais", "Neutre Mauvais", "Chaotique Mauvais",
]

CARACS = ["FOR", "DEX", "CON", "INT", "SAG", "CHA"]


# --------------------------------------------------------------------------- #
#  Dieux principaux (Manuel des Joueurs 3.5 — corpus-draconique.com)
# --------------------------------------------------------------------------- #
# Chaque dieu liste ses serviteurs : races et/ou classes (noms canoniques des
# catalogues ci-dessus). `mal: True` = réservé aux personnages maléfiques
# (« guerriers/roublards maléfiques », « nécromanciens »…).
DIEUX: list[dict[str, Any]] = [
    {
        "nom": "Boccob",
        "titre": "dieu de la magie",
        "alignement": "Neutre",
        "races": [],
        "classes": ["Magicien", "Sorcier"],
        "mal": False,
    },
    {
        "nom": "Corellon Larethian",
        "titre": "dieu des elfes",
        "alignement": "Chaotique Bon",
        "races": ["Elfe", "Demi-elfe"],
        "classes": ["Barde"],
        "mal": False,
    },
    {
        "nom": "Ehlonna",
        "titre": "déesse des forêts",
        "alignement": "Neutre Bon",
        "races": ["Elfe", "Demi-elfe", "Gnome", "Halfelin"],
        "classes": ["Druide", "Rodeur"],
        "mal": False,
    },
    {
        "nom": "Érythnul",
        "titre": "dieu des carnages",
        "alignement": "Chaotique Mauvais",
        "races": [],
        "classes": ["Guerrier", "Barbare", "Voleur"],
        "mal": True,
    },
    {
        "nom": "Fharlanghn",
        "titre": "dieu des routes",
        "alignement": "Neutre",
        "races": [],
        "classes": ["Barde"],
        "mal": False,
    },
    {
        "nom": "Garl Brilledor",
        "titre": "dieu des gnomes",
        "alignement": "Neutre Bon",
        "races": ["Gnome"],
        "classes": [],
        "mal": False,
    },
    {
        "nom": "Gruumsh",
        "titre": "dieu des orques",
        "alignement": "Chaotique Mauvais",
        "races": ["Demi-orc"],
        "classes": [],
        "mal": False,
    },
    {
        "nom": "Héronéus",
        "titre": "dieu de la bravoure",
        "alignement": "Loyal Bon",
        "races": [],
        "classes": ["Paladin", "Guerrier", "Moine"],
        "mal": False,
    },
    {
        "nom": "Hextor",
        "titre": "dieu de la tyrannie",
        "alignement": "Loyal Mauvais",
        "races": [],
        "classes": ["Guerrier", "Moine"],
        "mal": True,
    },
    {
        "nom": "Kord",
        "titre": "dieu de la force",
        "alignement": "Chaotique Bon",
        "races": [],
        "classes": ["Guerrier", "Barbare", "Voleur"],
        "mal": False,
    },
    {
        "nom": "Moradin",
        "titre": "dieu des nains",
        "alignement": "Loyal Bon",
        "races": ["Nain"],
        "classes": [],
        "mal": False,
    },
    {
        "nom": "Nérull",
        "titre": "dieu de la mort",
        "alignement": "Neutre Mauvais",
        "races": [],
        "classes": ["Voleur", "Magicien"],
        "mal": True,
    },
    {
        "nom": "Obad-Haï",
        "titre": "dieu de la nature",
        "alignement": "Neutre",
        "races": [],
        "classes": ["Druide", "Barbare", "Rodeur"],
        "mal": False,
    },
    {
        "nom": "Olidammara",
        "titre": "dieu des voleurs",
        "alignement": "Chaotique Neutre",
        "races": [],
        "classes": ["Voleur", "Barde"],
        "mal": False,
    },
    {
        "nom": "Pélor",
        "titre": "dieu du soleil",
        "alignement": "Neutre Bon",
        "races": [],
        "classes": ["Rodeur", "Barde"],
        "mal": False,
    },
    {
        "nom": "Saint Cuthbert",
        "titre": "dieu de la vengeance",
        "alignement": "Loyal Neutre",
        "races": [],
        "classes": ["Guerrier", "Moine"],
        "mal": False,
    },
    {
        "nom": "Vecna",
        "titre": "dieu des secrets",
        "alignement": "Neutre Mauvais",
        "races": [],
        "classes": ["Magicien", "Sorcier", "Voleur"],
        "mal": True,
    },
    {
        "nom": "Wy-Djaz",
        "titre": "déesse de la mort et de la magie",
        "alignement": "Loyal Neutre",
        "races": [],
        "classes": ["Magicien", "Sorcier"],
        "mal": False,
    },
    {
        "nom": "Yondalla",
        "titre": "déesse des halfelins",
        "alignement": "Loyal Bon",
        "races": ["Halfelin"],
        "classes": [],
        "mal": False,
    },
]


def _est_maléfique(alignement: str) -> bool:
    return "mauvais" in (alignement or "").lower()


def dieux_disponibles(race: str, classe: str, alignement: str) -> list[dict[str, Any]]:
    """Dieux acceptant le personnage comme serviteur.

    Un dieu qualifie le personnage si :
      - sa race figure parmi les races servies OU sa classe parmi les classes
        servies (un dieu peut servir plusieurs types de fidèles) ;
      - les listes vides signifient « ouvert à tous » sur cette dimension ;
      - les dieux `mal` exigent un alignement mauvais (serviteurs « maléfiques »).
    """
    race_c = resoudre_race(race)
    classe_c = resoudre_classe(classe)
    eligibles = []
    for d in DIEUX:
        par_race = bool(d["races"]) and race_c in d["races"]
        par_classe = bool(d["classes"]) and classe_c in d["classes"]
        if not (par_race or par_classe):
            continue
        if d["mal"] and not _est_maléfique(alignement):
            continue
        eligibles.append(d)
    return eligibles


# --------------------------------------------------------------------------- #
#  Calculs D&D 3.5
# --------------------------------------------------------------------------- #
def mod_carac(valeur: int) -> int:
    """Modificateur de caractéristique : (valeur − 10) / 2 arrondi inférieur."""
    return (int(valeur) - 10) // 2


def _bab_par_niveau(progression: str, niveau: int) -> int:
    if progression == "bon":
        return niveau
    if progression == "moyen":
        return int(niveau * 3 / 4)
    return niveau // 2


def _save_base(bonne: bool, niveau: int) -> int:
    return 2 + niveau // 2 if bonne else niveau // 3


def calculer_ca_armure(dex_mod: int, armures: Optional[list[str]] = None) -> int:
    """CA selon les règles 3.5 avec l'équipement porté.

    10 + bonus de la meilleure armure corporelle + meilleur bouclier +
    mod. DEX plafonné par le `dex_max` de l'armure (les boucliers ne
    plafonnent pas la Dex). Sans armure : 10 + mod. DEX.
    """
    from . import catalogue as _catalogue

    par_nom = {a["nom"]: a for a in _catalogue.ARMURES}
    corps: list[tuple[int, int]] = []
    boucliers: list[int] = []
    for nom in armures or []:
        a = par_nom.get(str(nom or "").strip())
        if not a:
            continue
        if a["categorie"] == "Bouclier":
            boucliers.append(int(a["ca"]))
        else:
            corps.append((int(a["ca"]), int(a["dex_max"])))
    bonus_armure, dex_max = max(corps, key=lambda x: x[0]) if corps else (0, 99)
    bonus_bouclier = max(boucliers) if boucliers else 0
    return 10 + bonus_armure + bonus_bouclier + min(dex_mod, dex_max)


def calculer_derivees(
    carac: dict[str, int],
    race: str,
    classe: str,
    niveau: int,
    armures: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Calcule les valeurs dérivées d'un personnage selon les règles 3.5.

    Renvoie un dict : mods, pv_max/pv, ca, bab, sauvegardes, initiative,
    ajustements raciaux appliqués (`carac_final`).
    """
    niveau = max(1, int(niveau or 1))
    race_canon = resoudre_race(race) or race
    classe_canon = resoudre_classe(classe) or classe

    # Ajustements raciaux appliqués aux valeurs saisies (plancher 1).
    mods_race = RACES.get(race_canon, {}).get("mods", {})
    final: dict[str, int] = {}
    for c in CARACS:
        brut = int(carac.get(c, 10))
        final[c] = max(1, brut + int(mods_race.get(c, 0)))

    infos = CLASSES.get(classe_canon, {"de_vie": 10, "bab": "moyen", "sauves_bonnes": []})
    dv = int(infos["de_vie"])

    # PV : niv.1 = maximum du dé + mod CON ; niveaux suivants = moyenne (dv/2+1).
    mod_con = mod_carac(final["CON"])
    pv_autres = max(0, (niveau - 1)) * (dv // 2 + 1 + mod_con)
    pv_max = max(1, dv + mod_con + pv_autres)

    ca = calculer_ca_armure(mod_carac(final["DEX"]), armures)
    bab = _bab_par_niveau(infos["bab"], niveau)
    sauves = {
        s: _save_base(s in infos["sauves_bonnes"], niveau)
        + mod_carac(final[{"Vigueur": "CON", "Reflexes": "DEX", "Volonte": "SAG"}[s]])
        for s in ("Vigueur", "Reflexes", "Volonte")
    }
    return {
        "carac_final": final,
        "mods": {c: mod_carac(final[c]) for c in CARACS},
        "ajustements_raciaux": mods_race,
        "pv": pv_max,
        "pv_max": pv_max,
        "ca": ca,
        "bab": bab,
        "sauvegardes": sauves,
        "initiative": mod_carac(final["DEX"]),
    }


def tirage_4d6() -> dict[str, int]:
    """Tirage classique : 4d6, on retire le plus faible, ×6 caractéristiques."""
    def un_jet() -> int:
        des = sorted(random.randint(1, 6) for _ in range(4))
        return sum(des[1:])
    return {c: un_jet() for c in CARACS}


# --------------------------------------------------------------------------- #
#  Fiches — lecture/écriture avec propriétaire
# --------------------------------------------------------------------------- #
def fiches_dir(data_dir: str) -> str:
    path = os.path.join(data_dir, "fiches")
    os.makedirs(path, exist_ok=True)
    return path


def chemin_fiche(data_dir: str, nom: str) -> str:
    return os.path.join(fiches_dir(data_dir), f"fiche_{_slug(nom)}.json")


def charger_fiche(data_dir: str, nom: str) -> Optional[dict[str, Any]]:
    try:
        with open(chemin_fiche(data_dir, nom), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None


def lister_fiches(data_dir: str, proprietaire: Optional[str] = None) -> list[dict[str, Any]]:
    """Liste toutes les fiches (ou celles d'un propriétaire), triées par nom."""
    dossier = fiches_dir(data_dir)
    resultats: list[dict[str, Any]] = []
    try:
        fichiers = [
            fn for fn in os.listdir(dossier)
            if fn.startswith("fiche_") and fn.endswith(".json")
        ]
    except OSError:
        return []
    for fn in fichiers:
        try:
            with open(os.path.join(dossier, fn), "r", encoding="utf-8") as f:
                fiche = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(fiche, dict) and fiche.get("nom"):
            if proprietaire is None or fiche.get("proprietaire") == proprietaire:
                resultats.append(fiche)
    resultats.sort(key=lambda f: str(f.get("nom", "")).lower())
    return resultats


def url_portrait(data_dir: str, nom: str, proprietaire: str = "") -> Optional[str]:
    """Retrouve l'URL du portrait d'un personnage (png généré ou placeholder svg)."""
    slug = _slug(nom)
    dossier = os.path.join(data_dir, "portraits_cache")
    candidats = []
    if proprietaire:
        candidats.append(f"perso_{_slug(proprietaire)}_{slug}")
    candidats.append(slug)
    for base in candidats:
        for ext in (".png", ".svg"):
            if os.path.isfile(os.path.join(dossier, base + ext)):
                return f"/data/portraits_cache/{base}{ext}"
    return None


# --------------------------------------------------------------------------- #
#  Portrait ComfyUI — prompt construit depuis la fiche remplie
# --------------------------------------------------------------------------- #
_ARTICLE_SEXE = {"F": "a female", "M": "a male", "Autre": "an androgynous"}

# Traductions FR → EN pour que le prompt reste bien compris par ComfyUI.
_RACE_EN = {
    "Humain": "human", "Elfe": "elf", "Nain": "dwarf", "Halfelin": "halfling",
    "Gnome": "gnome", "Demi-elfe": "half-elf", "Demi-orc": "half-orc",
}
_CLASSE_EN = {
    "Barbare": "barbarian", "Barde": "bard", "Clerc": "cleric", "Druide": "druid",
    "Guerrier": "fighter", "Magicien": "wizard", "Moine": "monk",
    "Paladin": "paladin", "Rodeur": "ranger", "Sorcier": "sorcerer",
    "Voleur": "rogue",
}
_COULEURS_EN = {
    "noir": "black", "noire": "black", "noirs": "black",
    "brun": "brown", "brune": "brown", "bruns": "brown",
    "chatain": "brown", "chatin": "brown", "chestnut": "chestnut",
    "blond": "blond", "blonde": "blond", "blonds": "blond",
    "roux": "red", "rousse": "red", "rouge": "red",
    "blanc": "white", "blanche": "white", "blancs": "white",
    "argent": "silver", "argente": "silver", "gris": "grey", "grise": "grey",
    "bleu": "blue", "bleus": "blue", "bleue": "blue",
    "vert": "green", "verts": "green", "verte": "green", "vertes": "green",
    "ambre": "amber", "noisette": "hazel", "violet": "violet",
    "clair": "fair", "claire": "fair", "pale": "pale",
    "halee": "tanned", "mate": "dark", "foncee": "dark", "bronzee": "tanned",
}


def _en_couleur(valeur: str) -> str:
    """Traduit une couleur FR courante vers l'anglais (laisse tel quel sinon)."""
    cle = (valeur or "").strip().lower()
    cle_sans_accent = unicodedata.normalize("NFKD", cle)
    cle_sans_accent = "".join(c for c in cle_sans_accent if not unicodedata.combining(c))
    return _COULEURS_EN.get(cle_sans_accent, valeur.strip())


def construire_prompt_portrait(fiche: dict[str, Any]) -> str:
    """Prompt détaillé pour ComfyUI : identité + apparence + traits raciaux."""
    apparence = fiche.get("apparence") or {}
    sexe = (apparence.get("sexe") or "").strip()
    article = _ARTICLE_SEXE.get(sexe.upper(), "a")

    race_canon = resoudre_race(str(fiche.get("race", ""))) or str(fiche.get("race", ""))
    classe_canon = resoudre_classe(str(fiche.get("classe", ""))) or str(fiche.get("classe", ""))

    sujets = [article]
    sujets.append(_RACE_EN.get(race_canon, race_canon.lower()))
    if classe_canon:
        sujets.append(
            f"{_CLASSE_EN.get(classe_canon, classe_canon.lower())} adventurer"
        )

    details: list[str] = []
    traits_race = RACES.get(race_canon, {}).get("traits_visuels", "")
    if traits_race:
        details.append(traits_race)

    # Âge : on extrait le nombre (« 112 ans » → « 112 years old »).
    age_brut = str(apparence.get("age") or "").strip()
    m_age = re.search(r"\d+", age_brut)
    if m_age:
        details.append(f"{m_age.group(0)} years old")

    yeux = _en_couleur(str(apparence.get("yeux") or ""))
    if yeux:
        details.append(f"{yeux} eyes")
    cheveux = _en_couleur(str(apparence.get("cheveux") or ""))
    if cheveux:
        details.append(f"{cheveux} hair")
    peau = _en_couleur(str(apparence.get("peau") or ""))
    if peau:
        details.append(f"{peau} skin")

    description_libre = str(apparence.get("description") or "").strip()
    if description_libre:
        details.append(description_libre)

    sujet = " ".join(sujets)
    corps = (
        f"heroic portrait of {sujet}, "
        + (", ".join(details) + ", " if details else "")
        + "D&D fantasy character art, head and shoulders, "
        "dramatic lighting, detailed digital painting, warm colors, "
        "high resolution, no text"
    )
    return corps


async def generer_portrait_async(data_dir: str, fiche: dict[str, Any]) -> Optional[str]:
    """Génère le portrait ComfyUI du personnage et le copie aussi sous `<slug>.png`.

    Renvoie le chemin écrit, ou None si ComfyUI est indisponible (fallback
    silencieux — le monogramme côté front reste affiché).
    """
    from .image.helpers import generer_si_dispo

    nom = str(fiche.get("nom", ""))
    proprietaire = str(fiche.get("proprietaire", ""))
    cache_dir = os.path.join(data_dir, "portraits_cache")
    os.makedirs(cache_dir, exist_ok=True)

    dest = os.path.join(cache_dir, f"perso_{_slug(proprietaire)}_{_slug(nom)}.png")
    prompt = construire_prompt_portrait(fiche)
    ecrit = await generer_si_dispo("portrait", prompt, dest)

    # Copie « slug nu » pour la sidebar de partie (portraits_cache/<slug>.png),
    # sans écraser un portrait déjà généré par une autre source.
    if ecrit:
        copie = os.path.join(cache_dir, f"{_slug(nom)}.png")
        try:
            if not os.path.isfile(copie):
                with open(dest, "rb") as src, open(copie, "wb") as dst:
                    dst.write(src.read())
        except OSError:
            pass
    return ecrit


def lancer_portrait_background(data_dir: str, fiche: dict[str, Any]) -> None:
    """Déclenche la génération du portrait sans bloquer la réponse HTTP."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(generer_portrait_async(data_dir, fiche))
    except RuntimeError:
        # Pas de boucle courante (tests sync…) — best effort thread-less.
        asyncio.run(generer_portrait_async(data_dir, fiche))


# --------------------------------------------------------------------------- #
#  Enregistrement du PJ dans l'état de partie (à la connexion WS)
# --------------------------------------------------------------------------- #
def enregistrer_personnage_partie(
    data_dir: str, partie_id: str, nom_personnage: str, joueur: str
) -> Optional[dict[str, Any]]:
    """Ajoute/met à jour le PJ dans l'état persistant de la partie.

    Le personnage doit exister ET appartenir au joueur (champ `proprietaire`).
    Renvoie la fiche si l'enregistrement a réussi, sinon None.
    """
    from .game.state import PartyState

    fiche = charger_fiche(data_dir, nom_personnage)
    if fiche is None:
        return None
    proprio = str(fiche.get("proprietaire", ""))
    joueur_fiche = str(fiche.get("joueur", ""))
    if proprio and proprio.lower() != joueur.strip().lower():
        return None
    if not proprio and joueur_fiche and joueur_fiche.lower() != joueur.strip().lower():
        return None

    state = PartyState(data_dir=data_dir, partie_id=partie_id)
    etat = state.load()
    if "_erreur" in etat:
        return None

    entree = {
        "nom": fiche.get("nom", nom_personnage),
        "joueur": joueur,
        "race": fiche.get("race", ""),
        "classe": fiche.get("classe", ""),
        "niveau": fiche.get("niveau", 1),
        "pv": fiche.get("pv", 0),
        "pv_max": fiche.get("pv_max", 0),
        "ca": fiche.get("ca", 10),
        "carac": fiche.get("carac", {}),
        "sauvegardes": fiche.get("sauvegardes", {}),
        "bab": fiche.get("bab", 0),
        "alignement": fiche.get("alignement", ""),
    }
    pj_list = etat.get("pj") or []
    remplace = False
    for i, p in enumerate(pj_list):
        if str(p.get("nom", "")).lower() == entree["nom"].lower():
            pj_list[i] = entree
            remplace = True
            break
    if not remplace:
        pj_list.append(entree)
    etat["pj"] = pj_list
    if etat.get("phase") == "opening":
        etat["phase"] = "opening_complete"
    state.save(etat)
    return fiche
