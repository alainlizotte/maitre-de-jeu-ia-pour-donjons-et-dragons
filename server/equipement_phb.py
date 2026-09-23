"""Catalogue marchand des règles officielles (PHB 3.5, tables françaises).

Contrairement au catalogue de CRÉATION (`catalogue.py`, valeurs assistants
légèrement simplifiées), ce catalogue sert le commerce : prix officiels
(po/pa/pc), poids officiels (kg), catégories commerciales et disponibilité
selon le type de peuplement (`server/villes.py`).

MONNAIE : la fiche stocke `or` en pc (convention du projet, 1 po = 10 pc).
Tout prix est converti une bonne fois en pc via `_pc` ; le multiplicateur
local du marché est appliqué par `server/tools/marche.py`.

Chaque article a :
  - `nom`         nom canonique (celui que le LLM lit / demande) ;
  - `categorie`   arme | armure | bouclier | materiel | outil | substance |
                  habillement | nourriture | boisson | monture |
                  harnachement | transport | munition | service ;
  - `cout_pc`     prix en pc (sans le coefficient local) ;
  - `prix_str`    libellé officiel lisible (« 2 po », « 5 pa », « 1 pc ») ;
  - `poids_kg`    poids officiel d'UNE unité (0 si sans objet) ;
  - `niveau_marchand` rang minimum du peuplement pour voir l'article chez le
                  marchand qui le vend (défaut 0 = partout où la catégorie
                  est tenue) ;
  - champs facultatifs repris du catalogue : `groupe` (arme),
    `categorie_armure`, `degats`, `ca`…
"""

from __future__ import annotations

from typing import Any, Optional

from . import catalogue as cat
from .tools.inventaire import _norm
from .villes import (marchands as _villes_marchands,
                     multiplicateur as _villes_mult,
                     rang as _ville_rang,
                     sorts_max_niveau as _villes_sorts_max)


# --------------------------------------------------------------------------- #
#  Conversion po/pa/pc → pc
# --------------------------------------------------------------------------- #
def _pc(po: int = 0, pa: int = 0, cu: int = 0) -> int:
    """Convertit un prix officiel en pc (pièces de cuivre), minimum 1 pc.

    1 po = 10 pc, 1 pa = 1 pc, 1 pièce de cuivre = 0,1 pc. Un prix nul
    (objet offert / sans prix) reste à 1 pc : on ne solde jamais un article.
    """
    valeur = float(po) * 10 + float(pa) * 1 + float(cu) * 0.1
    return max(1, int(round(valeur)))


def _prix_str(po: int = 0, pa: int = 0, cu: int = 0) -> str:
    if pa == 0 and cu == 0:
        return f"{po} po"
    if po == 0 and cu == 0:
        return f"{pa} pa"
    if po == 0 and pa == 0:
        return f"{cu} pc" if cu else "—"
    return f"{po} po" if po else (f"{pa} pa" if pa else f"{cu} pc")


def _art(nom: str, categorie: str, po: int = 0, pa: int = 0, cu: int = 0,
         kg: float = 0.0, **extra: Any) -> dict[str, Any]:
    art: dict[str, Any] = {
        "nom": nom,
        "categorie": categorie,
        "cout_pc": _pc(po, pa, cu),
        "prix_str": _prix_str(po, pa, cu),
        "poids_kg": float(kg),
        "niveau_marchand": 0,
    }
    art.update(extra)
    return art


# --------------------------------------------------------------------------- #
#  Marchands — méta + catégories vendues
# --------------------------------------------------------------------------- #
# `categories` : catégories d'articles tenues en boutique. `description` :
# résumé lisible pour le LLM / le front.
MARCHANDS: dict[str, dict[str, Any]] = {
    "auberge": {
        "description": "Repas et hébergement (voir auberge_commander), "
                       "nourriture et boisson en boutique.",
        "categories": ["nourriture", "boisson"],
    },
    "marché paysan": {
        "description": "Denrées, bêtes de trait et bât : nourriture, "
                       "boisson, montures simples, transports légers.",
        "categories": ["nourriture", "boisson", "monture",
                       "harnachement", "transport"],
    },
    "marchand général": {
        "description": "Échoppe d'aventurier : matériel courant, outils, "
                       "vêtements, nourriture, boisson, munitions.",
        "categories": ["materiel", "outil", "munition", "habillement",
                       "nourriture", "boisson", "harnachement", "transport",
                       "substance"],
    },
    "forge": {
        "description": "Forgeron : armes (simples, puis martiales selon "
                       "l'importance de la ville, exotiques seulement en cité) "
                       "et outils de métier.",
        "categories": ["arme", "outil"],
    },
    "armurerie": {
        "description": "Armures et boucliers (légers et boucliers partout, "
                       "moyennes puis lourdes dans les grandes villes).",
        "categories": ["armure", "bouclier"],
    },
    "écurie": {
        "description": "Montures, harnachement, véhicules et pension "
                       "des montures.",
        "categories": ["monture", "harnachement", "transport", "service"],
    },
    "apothicaire": {
        "description": "Herboriste / apothicaire : remèdes, trousses de "
                       "soins, matériel courant.",
        "categories": ["outil", "substance", "nourriture"],
    },
    "alchimiste": {
        "description": "Substances rares et alchimiques (acide, feu "
                       "grégeois, torche éternelle, antidote…).",
        "categories": ["substance", "outil", "materiel"],
    },
    "boutique d'aventuriers": {
        "description": "Équipement d'aventurier complet et services "
                       "pratiques (messager, diligence, traversée).",
        "categories": ["materiel", "outil", "munition", "habillement",
                       "service"],
    },
    "bibliothèque": {
        "description": "Scribes et bibliothèque : grimoires, encre, "
                       "parchemins et recherches.",
        "categories": ["outil", "materiel", "service"],
    },
    "temple": {
        "description": "Clergé : eau bénite, soins et sortilèges divins "
                       "loués à prix d'or.",
        "categories": ["service", "substance"],
    },
    "tour des arcanes": {
        "description": "Enclave de mages : sortilèges profanes loués, "
                       "grimoires et composantes rares.",
        "categories": ["service", "materiel"],
    },
    "chantier naval": {
        "description": "Calen et carriers : bateaux et navires.",
        "categories": ["transport", "service"],
    },
}

# Rangs minimums (index dans villes.RANGS) pour les groupes d'armes et
# catégories d'armures — calés sur la présence des métiers :
#   forge : simple (village) → martiale (ville) → exotique (cité) ;
#   armurerie : légers/boucliers (bourg) → moyens (ville) → lourds (gde ville).
RANG_ARME = {"simple": 1, "martiale": 3, "exotique": 5}
RANG_ARMURE = {"Legere": 2, "Moyenne": 3, "Lourde": 4, "Bouclier": 2}


# --------------------------------------------------------------------------- #
#  Articles (§ tableaux officiels des règles)
# --------------------------------------------------------------------------- #
def _articles_catalogue() -> list[dict[str, Any]]:
    """Armes / armures / boucliers tirés du catalogue de création : prix en
    po → pc, poids en kg, groupe/catégorie d'armure conservés pour le gating."""
    arts: list[dict[str, Any]] = []
    for a in cat.ARMES:
        arts.append(_art(
            a["nom"], "arme", po=int(a.get("cout") or 0), kg=a.get("poids") or 0,
            groupe=a.get("groupe", "simple"),
            degats=a.get("degats", ""),
            distance=bool(a.get("distance")),
            niveau_marchand=RANG_ARME.get(a.get("groupe", "simple"), 1),
        ))
    for x in cat.ARMURES:
        cat_x = "bouclier" if x.get("categorie") == "Bouclier" else "armure"
        arts.append(_art(
            x["nom"], cat_x, po=int(x.get("cout") or 0), kg=x.get("poids") or 0,
            categorie_armure=x.get("categorie", ""),
            ca=x.get("ca", 0),
            niveau_marchand=RANG_ARMURE.get(x.get("categorie", ""), 2),
        ))
    return arts


def _articles_phb() -> list[dict[str, Any]]:
    """Tous les autres articles (tables PHB pages 412-419), prix officiels."""
    a: list[dict[str, Any]] = []
    E = a.append

    # --- Matériel d'aventurier (page 412) -----------------------------------
    E(_art("Aiguille à coudre", "materiel", cu=5, kg=0.0))
    E(_art("Balance de marchand", "outil", po=2, kg=0.5))
    E(_art("Bélier portable", "materiel", po=10, kg=10.0))
    E(_art("Bois de chauffage (1 jour)", "materiel", cu=1, kg=10.0))
    E(_art("Bougie", "materiel", cu=1, kg=0.0))
    E(_art("Bouteille de vin (verre)", "materiel", po=2, kg=0.7))
    E(_art("Cadenas (très simple)", "materiel", po=20, kg=0.5))
    E(_art("Cadenas (moyen)", "materiel", po=40, kg=0.5))
    E(_art("Cadenas (bon)", "materiel", po=80, kg=0.5))
    E(_art("Cadenas (excellent)", "materiel", po=150, kg=0.5))
    E(_art("Chaîne (3 m)", "materiel", po=30, kg=0.9))
    E(_art("Chausse-trappes", "materiel", po=1, kg=1.0))
    E(_art("Chevalière", "materiel", po=5, kg=0.0))
    E(_art("Chope en terre cuite", "materiel", cu=2, kg=0.5))
    E(_art("Cire à cacheter", "materiel", po=1, kg=0.5))
    E(_art("Cloche", "materiel", po=1, kg=0.0))
    E(_art("Coffre (vide)", "materiel", po=2, kg=12.5))
    E(_art("Corde de chanvre (15 m)", "materiel", po=1, kg=5.0))
    E(_art("Corde de soie (15 m)", "materiel", po=10, kg=2.5))
    E(_art("Couverture hivernale", "materiel", pa=5, kg=1.5))
    E(_art("Craie", "materiel", cu=1, kg=0.05))
    E(_art("Cruche", "materiel", cu=2, kg=2.5))
    E(_art("Échelle (3 m)", "materiel", cu=5, kg=10.0))
    E(_art("Encre (fiole)", "outil", po=8, kg=0.0))
    E(_art("Étui à cartes", "materiel", po=1, kg=0.25))
    E(_art("Étui à parchemins", "materiel", po=1, kg=0.25))
    E(_art("Flasque (vide)", "materiel", cu=3, kg=0.23))
    E(_art("Fiole (vide)", "materiel", po=1, kg=0.05))
    E(_art("Grappin", "materiel", po=1, kg=2.0))
    E(_art("Hameçon", "materiel", pa=1, kg=0.0))
    E(_art("Huile (flasque)", "materiel", pa=1, kg=0.5))
    E(_art("Jarre", "materiel", cu=3, kg=4.5))
    E(_art("Lampe", "materiel", pa=1, kg=0.5))
    E(_art("Lanterne à capote", "materiel", po=7, kg=1.0))
    E(_art("Lanterne sourde", "materiel", po=12, kg=1.5))
    E(_art("Longue-vue", "materiel", po=1000, kg=0.5))
    E(_art("Marteau", "outil", pa=5, kg=1.0))
    E(_art("Masse", "outil", po=1, kg=5.0))
    E(_art("Menottes", "materiel", po=15, kg=1.0))
    E(_art("Menottes (qualité supérieure)", "materiel", po=50, kg=1.0))
    E(_art("Outre", "materiel", po=1, kg=2.0))
    E(_art("Paillasse", "materiel", pa=1, kg=2.5))
    E(_art("Palan", "materiel", po=5, kg=2.5))
    E(_art("Panier (vide)", "materiel", pa=4, kg=0.5))
    E(_art("Papier (feuille)", "materiel", pa=4, kg=0.0))
    E(_art("Parchemin (feuille)", "materiel", pa=2, kg=0.0))
    E(_art("Pelle", "materiel", po=2, kg=4.0))
    E(_art("Pic de mineur", "outil", po=3, kg=4.5))
    E(_art("Pierre à aiguiser", "materiel", cu=1, kg=0.5))
    E(_art("Piton", "materiel", cu=5, kg=0.05))
    E(_art("Sac à dos", "materiel", po=2, kg=0.9))
    E(_art("Seau", "materiel", cu=5, kg=0.9))
    E(_art("Silex et amorce", "materiel", po=1, kg=0.0))
    E(_art("Tente", "materiel", po=10, kg=9.0))
    E(_art("Torche", "materiel", cu=1, kg=0.45))

    # --- Matériel de classe / outils (page 414) -----------------------------
    E(_art("Clepsydre", "outil", po=1000, kg=100.0))
    E(_art("Composantes de sorts (sacoche)", "outil", po=5, kg=0.9))
    E(_art("Filet de pêche", "materiel", po=4, kg=2.5))
    E(_art("Grimoire (vide)", "outil", po=15, kg=1.5))
    E(_art("Houx et gui", "outil", cu=0, kg=0.0))
    E(_art("Instrument de musique (courant)", "outil", po=5, kg=1.5))
    E(_art("Instrument de musique (de maître)", "outil", po=100, kg=1.5))
    E(_art("Laboratoire d'alchimiste", "outil", po=500, kg=20.0))
    E(_art("Loupe", "outil", po=100, kg=0.0))
    E(_art("Matériel d'escalade", "outil", po=80, kg=2.5))
    E(_art("Outils d'artisan (courants)", "outil", po=5, kg=2.5))
    E(_art("Outils d'artisan (de maître)", "outil", po=55, kg=2.5))
    E(_art("Outils de cambrioleur", "outil", po=30, kg=0.5))
    E(_art("Outils de cambrioleur (de qualité)", "outil", po=100, kg=1.0))
    E(_art("Sablier", "outil", po=25, kg=0.5))
    E(_art("Sacoche à composantes", "outil", po=5, kg=1.5))
    E(_art("Symbole sacré (bois)", "outil", po=1, kg=0.0))
    E(_art("Symbole sacré (argent)", "outil", po=25, kg=0.5))
    E(_art("Trousse de déguisement", "outil", po=50, kg=4.0))
    E(_art("Trousse de premiers secours", "outil", po=50, kg=0.5))

    # --- Substances (page 413) ----------------------------------------------
    E(_art("Acide (flasque)", "substance", po=10, kg=0.5, niveau_marchand=4))
    E(_art("Allume-feu", "substance", po=1, kg=0.0))
    E(_art("Antidote (fiole)", "substance", po=50, kg=0.0))
    E(_art("Bâton éclairant", "substance", po=2, kg=0.5))
    E(_art("Bâton fumigène", "substance", po=20, kg=0.25))
    E(_art("Eau bénite (flasque)", "substance", po=25, kg=0.5))
    E(_art("Feu grégeois (flasque)", "substance", po=20, kg=0.5,
          niveau_marchand=4))
    E(_art("Pierre à tonnerre", "substance", po=30, kg=0.5, niveau_marchand=4))
    E(_art("Sacoche immobilisante", "substance", po=50, kg=2.0,
          niveau_marchand=4))
    E(_art("Torche éternelle", "substance", po=110, kg=0.5, niveau_marchand=4))

    # --- Habillement (page 415) ---------------------------------------------
    E(_art("Costume d'artiste", "habillement", po=3, kg=2.0))
    E(_art("Habit d'érudit", "habillement", po=5, kg=3.0))
    E(_art("Habit de cour", "habillement", po=30, kg=3.0))
    E(_art("Habit de moine", "habillement", po=5, kg=1.0))
    E(_art("Habit de noble", "habillement", po=75, kg=5.0))
    E(_art("Habit de paysan", "habillement", pa=1, kg=1.0))
    E(_art("Tenue d'artisan", "habillement", po=1, kg=2.0))
    E(_art("Tenue d'explorateur", "habillement", po=10, kg=4.0))
    E(_art("Tenue de voyage", "habillement", po=1, kg=2.5))
    E(_art("Tenue polaire", "habillement", po=8, kg=3.5))
    E(_art("Tenue sacerdotale", "habillement", po=5, kg=3.0))
    E(_art("Toilette royale", "habillement", po=200, kg=7.5))

    # --- Nourriture / boisson (page 416) ------------------------------------
    E(_art("Repas médiocre (1 jour)", "nourriture", pa=1, kg=0.0))
    E(_art("Repas convenable (1 jour)", "nourriture", pa=3, kg=0.0))
    E(_art("Repas de bonne qualité (1 jour)", "nourriture", pa=5, kg=0.0))
    E(_art("Pain (250 g)", "nourriture", cu=2, kg=0.25))
    E(_art("Fromage (250 g)", "nourriture", pa=1, kg=0.25))
    E(_art("Rations de voyage (1 jour)", "nourriture", pa=5, kg=0.45))
    E(_art("Chope de bière", "boisson", cu=4, kg=0.5))
    E(_art("Litre de bière", "boisson", cu=7, kg=1.0))
    E(_art("Vin de table (pichet)", "boisson", pa=2, kg=3.0))
    E(_art("Vin de bonne qualité (bouteille)", "boisson", po=10, kg=0.75))
    E(_art("Banquet (par convive)", "service", po=10, kg=0.0))

    # --- Munitions -----------------------------------------------------------
    E(_art("Flèches (20)", "munition", po=1, kg=1.36))
    E(_art("Carreaux (10)", "munition", po=1, kg=0.45))
    E(_art("Balles de fronde (10)", "munition", pa=1, kg=1.36))

    # --- Montures (page 417) -------------------------------------------------
    E(_art("Âne ou mulet", "monture", po=8, kg=0.0))
    E(_art("Chien de garde", "monture", po=25, kg=0.0))
    E(_art("Chien de selle", "monture", po=150, kg=0.0))
    E(_art("Cheval léger", "monture", po=75, kg=0.0))
    E(_art("Cheval lourd", "monture", po=200, kg=0.0, niveau_marchand=3))
    E(_art("Poney", "monture", po=30, kg=0.0))
    E(_art("Poney de guerre", "monture", po=100, kg=0.0, niveau_marchand=3))
    E(_art("Destrier léger", "monture", po=150, kg=0.0, niveau_marchand=3))
    E(_art("Destrier lourd", "monture", po=400, kg=0.0, niveau_marchand=5))

    # --- Harnachement (page 417) ---------------------------------------------
    E(_art("Barde (cuir, taille M)", "harnachement", po=20, kg=6.8,
          niveau_marchand=3))
    E(_art("Barde (cuir, taille G)", "harnachement", po=40, kg=13.6,
          niveau_marchand=3))
    E(_art("Fontes", "harnachement", po=4, kg=4.0))
    E(_art("Mors et bride", "harnachement", po=2, kg=0.5))
    E(_art("Nourriture pour animaux (1 jour)", "harnachement", cu=5, kg=5.0))
    E(_art("Selle d'équitation", "harnachement", po=10, kg=12.5,
          niveau_marchand=2))
    E(_art("Selle de bât", "harnachement", po=5, kg=7.5, niveau_marchand=2))
    E(_art("Selle de guerre", "harnachement", po=20, kg=15.0,
          niveau_marchand=3))
    E(_art("Selle d'équitation (spéciale)", "harnachement", po=30, kg=15.0,
          niveau_marchand=3))
    E(_art("Selle de bât (spéciale)", "harnachement", po=15, kg=10.0,
          niveau_marchand=3))
    E(_art("Selle de guerre (spéciale)", "harnachement", po=60, kg=20.0,
          niveau_marchand=4))
    E(_art("Écurie (1 jour)", "service", pa=5, kg=0.0, niveau_marchand=3))

    # --- Transport (page 418) -------------------------------------------------
    E(_art("Barque", "transport", po=50, kg=50.0))
    E(_art("Rame", "transport", po=2, kg=5.0))
    E(_art("Charrette", "transport", po=15, kg=100.0))
    E(_art("Chariot", "transport", po=35, kg=200.0, niveau_marchand=2))
    E(_art("Carriole", "transport", po=100, kg=300.0, niveau_marchand=2))
    E(_art("Traîneau", "transport", po=20, kg=150.0, niveau_marchand=2))
    E(_art("Bateau à fond plat", "transport", po=3000, kg=0.0,
          niveau_marchand=4))
    E(_art("Drakkar", "transport", po=10000, kg=0.0, niveau_marchand=5))
    E(_art("Navire de haute mer", "transport", po=10000, kg=0.0,
          niveau_marchand=5))
    E(_art("Trirème", "transport", po=30000, kg=0.0, niveau_marchand=5))
    E(_art("Vaisseau de guerre", "transport", po=25000, kg=0.0,
          niveau_marchand=5))

    # --- Services (page 419) --------------------------------------------------
    E(_art("Droit de passage", "service", cu=1, kg=0.0))
    E(_art("Employé non qualifié (1 jour)", "service", pa=1, kg=0.0))
    E(_art("Employé qualifié (1 jour)", "service", pa=3, kg=0.0))
    E(_art("Messager (par 1,5 km)", "service", cu=2, kg=0.0))
    E(_art("Diligence (par 1,5 km)", "service", cu=3, kg=0.0))
    E(_art("Traversée en bateau (par 1,5 km)", "service", pa=1, kg=0.0))
    return a


# --------------------------------------------------------------------------- #
#  Assemblage + index
# --------------------------------------------------------------------------- #
ARTICLES: list[dict[str, Any]] = _articles_catalogue() + _articles_phb()

_ARTICLES_PAR_CLE: dict[str, dict[str, Any]] = {
    _norm(a["nom"]): a for a in ARTICLES
}
# Ré-écrase par corps de boucle : les clés normalisées en double (ex. deux
# types d'acier) gardent la DERNIÈRE entrée — on préfère les tables PHB.

def _resoudre_marchand(nom: str) -> Optional[str]:
    """Rapproche un libellé libre (« le forgeron », « écurie du guet ») de la
    clé canonique d'un marchand. None si inconnu."""
    n = _norm(nom)
    if not n:
        return None
    for canon in MARCHANDS:
        if n == _norm(canon):
            return canon
    # Sous-chaîne : « fourbe » → « tour des arcanes » seulement si l'un
    # contient l'autre (mot assez long pour éviter le faux positif « auberge »).
    for canon in MARCHANDS:
        cn = _norm(canon)
        if (len(n) >= 4 and n in cn) or (len(cn) >= 4 and cn in n):
            return canon
    return None


def article(nom: str) -> dict[str, Any] | None:
    """Article canonique par nom (normalisé). None si inconnu du catalogue."""
    return _ARTICLES_PAR_CLE.get(_norm(nom))


def articles() -> list[dict[str, Any]]:
    """Tout le catalogue marchand (copie de travail)."""
    return [dict(a) for a in ARTICLES]


def marchands_pour(article_: dict[str, Any], type_ville: str) -> list[str]:
    """Marchands de la localité susceptibles de VENDRE (donc d'acheter aussi)
    l'article : filtre par catégorie tenue + rang requis de l'article."""
    rng = _ville_rang(type_ville)
    out: list[str] = []
    for m in _villes_marchands(type_ville):
        meta = MARCHANDS.get(m)
        if not meta:
            continue
        if article_.get("categorie") not in meta["categories"]:
            continue
        if rng < int(article_.get("niveau_marchand") or 0):
            continue
        out.append(m)
    return out


def prix_article(article_: dict[str, Any], type_ville: str,
                 base: int | None = None) -> int:
    """Prix d'achat effectif (pc) : base × multiplicateur local, arrondi au
    supérieur (un prix ne descend jamais sous 1 pc)."""
    from math import ceil
    base_pc = int(base if base is not None else article_["cout_pc"])
    return max(1, int(ceil(base_pc * _villes_mult(type_ville))))


def prix_revente(article_: dict[str, Any]) -> int:
    """Prix de revente (pc) : moitié du prix de BASE (sans multiplicateur,
    arrondi à l'inférieur), minimum 1 pc."""
    return max(1, int(article_["cout_pc"]) // 2)


def sorts_service(type_ville: str, niveau: int) -> dict[str, Any] | None:
    """Article dynamique « Sort de service (niveau N) » si la localité peut
    le fournir (page 419). None sinon."""
    if niveau < 1 or niveau > _villes_sorts_max(type_ville):
        return None
    return _art(f"Sort de service (niveau {niveau})", "service",
                po=niveau * 10, kg=0.0,
                niveau_marchand=2)


def auberge_prix(qualite: str) -> dict[str, int]:
    """Tarifs officiels d'auberge (page 416) en pc : {logement, repas}."""
    logement = {"mediocre": 2, "convenable": 5, "bonne": 20}
    repas = {"mediocre": 1, "convenable": 3, "bonne": 5}
    return {"logement": logement.get(qualite, 0), "repas": repas.get(qualite, 0)}