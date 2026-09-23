"""Types de peuplement (Hameau → Métropole) et classification des villes.

La disponibilité des marchands, le niveau maximum des sorts de service et le
prix des biens dépendent de l'importance de la localité où se trouve le groupe
(règles-maison inspirées du Manuel du Joueur 3.5, tables des pages 416-419 des
règles officielles https://regles-donjons-dragons.com/page409.html).

Monnaie : la fiche stocke `or` en pc (pièces de cuivre). 1 po = 10 pc,
1 pa = 1 pc, 1 pc = 0,1 po. Les prix officiels (po/pa/pc) sont convertis en pc
par `equipement_phb._pc` puis éventuellement majorés du coefficient local.
"""

from __future__ import annotations

import re

# Rang 0 → Hameau, 6 → Métropole. Ordre strictement croissant.
RANGS: list[str] = [
    "Hameau", "Village", "Bourg", "Ville", "Grande ville", "Cité", "Métropole",
]

# Classification des localités du répertoire (server/tools/cartes.py,
# VILLES_REPERES — même noms de villes). NOTE multiplicateur prix : un
# hameau vend CHER (rareté), une métropole casse les prix (volume).
VILLES_TYPES: dict[str, str] = {
    "Waterdeep": "Métropole",
    "Athkatla": "Métropole",
    "Baldur's Gate": "Cité",
    "Silverymoon": "Cité",
    "Neverwinter": "Cité",
    "Evereska": "Cité",
    "Suzail": "Cité",
    "Mirabar": "Grande ville",
    "Luskan": "Grande ville",
    "Elturel": "Grande ville",
    "Scornubel": "Grande ville",
    "Everlund": "Ville",
    "Mithral Hall": "Ville",
    "Secomber": "Bourg",
    "Triboar": "Bourg",
    "Daggerford": "Village",
    "Phandalin": "Village",
}

# Type retenu pour une localité inconnue du répertoire (ou un lieu intérieur
# tel « Auberge du Drakkar ») : commodité raisonnable pour de la marchandise.
TYPE_INCONNU = "Bourg"

# Clé normalisée (minuscules, sans accent ni apostrophe) → type.
def _cle(nom: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", (nom or "").lower())
    return s


VILLES_PAR_CLE: dict[str, str] = {
    _cle(nom): typ for nom, typ in VILLES_TYPES.items()
}

# Multiplicateur de prix d'ACHAT selon le type de peuplement (règles-maison).
PRIX_MULTIPLICATEURS: dict[str, float] = {
    "Hameau": 1.25,
    "Village": 1.15,
    "Bourg": 1.10,
    "Ville": 1.05,
    "Grande ville": 1.0,
    "Cité": 1.0,
    "Métropole": 0.95,
}

# Niveau maximum des sorts de SERVICE disponibles (page 419 des règles) :
# petite ville → 1er niveau, ville importante → 2e, grande ville → 3-4,
# cité → 5-6, métropole → 7-8+. 0 = pas de sorts de service (hameau/village
# n'ont qu'un guérisseur de terrain, soins non-magiques).
SORTS_MAX_NIVEAU: dict[str, int] = {
    "Hameau": 0,
    "Village": 0,
    "Bourg": 1,
    "Ville": 2,
    "Grande ville": 4,
    "Cité": 6,
    "Métropole": 8,
}

# Qualités de repas / hébergement proposées à l'auberge selon la localité.
AUBERGE_QUALITES_PAR_TYPE: dict[str, list[str]] = {
    "Hameau": ["mediocre"],
    "Village": ["mediocre", "convenable"],
    "Bourg": ["mediocre", "convenable"],
    "Ville": ["convenable", "bonne"],
    "Grande ville": ["convenable", "bonne"],
    "Cité": ["bonne"],
    "Métropole": ["bonne"],
}

# Marchands présents selon le type de peuplement (clés = seller de
# `equipement_phb.MARCHANDS`). Chaque type hérite des précédents (cumul).
MARCHANDS_PAR_TYPE: dict[str, list[str]] = {
    "Hameau": ["auberge", "marché paysan"],
    "Village": ["auberge", "marché paysan", "marchand général", "forge"],
    "Bourg": ["auberge", "marché paysan", "marchand général", "forge",
              "armurerie", "temple"],
    "Ville": ["auberge", "marché paysan", "marchand général", "forge",
              "armurerie", "écurie", "apothicaire", "temple",
              "tour des arcanes"],
    "Grande ville": ["auberge", "marché paysan", "marchand général", "forge",
                     "armurerie", "écurie", "apothicaire", "alchimiste",
                     "boutique d'aventuriers", "bibliothèque", "temple",
                     "tour des arcanes"],
    "Cité": ["auberge", "marché paysan", "marchand général", "forge",
             "armurerie", "écurie", "apothicaire", "alchimiste",
             "boutique d'aventuriers", "bibliothèque", "temple",
             "tour des arcanes", "chantier naval"],
    "Métropole": ["auberge", "marché paysan", "marchand général", "forge",
                  "armurerie", "écurie", "apothicaire", "alchimiste",
                  "boutique d'aventuriers", "bibliothèque", "temple",
                  "tour des arcanes", "chantier naval"],
}


def rang(type_: str) -> int:
    """Rang 0→6 d'un type (Hameau→Métropole)."""
    try:
        return RANGS.index(type_ or "")
    except ValueError:
        return RANGS.index(TYPE_INCONNU)


def type_de_ville(nom: str) -> str:
    """Type d'une localité d'après son nom (repli : TYPE_INCONNU)."""
    if not nom:
        return TYPE_INCONNU
    return VILLES_PAR_CLE.get(_cle(nom), TYPE_INCONNU)


def multiplicateur(type_: str) -> float:
    return PRIX_MULTIPLICATEURS.get(
        type_ or "", PRIX_MULTIPLICATEURS[TYPE_INCONNU])


def sorts_max_niveau(type_: str) -> int:
    return SORTS_MAX_NIVEAU.get(
        type_ or "", SORTS_MAX_NIVEAU[TYPE_INCONNU])


def auberge_qualites(type_: str) -> list[str]:
    return AUBERGE_QUALITES_PAR_TYPE.get(
        type_ or "", AUBERGE_QUALITES_PAR_TYPE[TYPE_INCONNU])


def marchands(type_: str) -> list[str]:
    return MARCHANDS_PAR_TYPE.get(
        type_ or "", MARCHANDS_PAR_TYPE[TYPE_INCONNU])


def marchand_present(type_: str, marchand: str) -> bool:
    return (marchand or "").strip() in marchands(type_)