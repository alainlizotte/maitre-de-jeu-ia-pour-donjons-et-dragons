"""Catalogue des sorts D&D 3.5 + tables d'emplacements (PHB 3.5).

Ce module alimente :
- le formulaire de création (section Sorts, filtrée par classe et niveau) ;
- la mécanique d'incantation (tool `incanter_sort`) : classe autorisée,
  niveau de sort castable, emplacements par jour (slots) + bonus de
  caractéristique, sorts préparés vs spontanés ;
- le récapitulatif MJ (sorts prêts / épuisés).

Les descriptions sont des résumés mécaniques originaux (une ligne) : seule
l'effet encodable sert au moteur (dégâts, soin, condition). Les tables
d'emplacements reproduisent les tables officielles PHB 3.5 (sorts par jour
par classe et niveau ; bonus de carac. : +1 emplacement par niveau de sort
si mod >= 2×niveau − 1, seulement si le niveau de sort est déjà castable).

Structure d'une entrée de SORTS :
    nom, niveau (0-9), ecole, classes (list), incantation, portee,
    composantes (V/S/M/F/XP), duree, sauvegarde (str|""), effet (dict|None)
effet : {"type": "degats"|"soin"|"etat"|"buff"|"utilitaire",
         "des": "1d6", "par_niveau": 1, "max_des": 10,     # degats/soin
         "auto": True,                                       # touche sans jet
         "condition": "Étourdi"}                             # etat
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------- #
#  Classes lanceuses de sorts
# --------------------------------------------------------------------------- #
# Type de lancement : préparateurs (mémorisation quotidienne) vs spontanés
# (sorts connus à vie, emplacements libres).
PREPARE: set[str] = {"Magicien", "Clerc", "Druide", "Paladin", "Rodeur"}
SPONTANE: set[str] = {"Sorcier", "Barde"}

# Caractéristique d'incantation (pour les bonus d'emplacements).
CARAC_INCANTATION: dict[str, str] = {
    "Magicien": "INT", "Sorcier": "CHA", "Barde": "CHA",
    "Clerc": "SAG", "Druide": "SAG", "Paladin": "CHA", "Rodeur": "SAG",
}

# --------------------------------------------------------------------------- #
#  Emplacements de sorts par jour (PHB 3.5) — index [niveau de classe - 1]
#  = tuple (slots niv.0, niv.1, …). Les « 0 » officiels (niveau accessible
#  via bonus uniquement) sont encodés 0 ; paladin/rodeur démarrent au niv.4.
# --------------------------------------------------------------------------- #
_E: dict[str, list[tuple[int, ...]]] = {
    # Magicien / Clerc / Druide : même progression (3/1, 4/2, 4/2/1, …).
    "Magicien": [
        (3, 1), (4, 2), (4, 2, 1), (4, 3, 2), (4, 3, 2, 1), (4, 3, 3, 2),
        (4, 4, 3, 2, 1), (4, 4, 3, 3, 2), (4, 4, 4, 3, 2, 1),
        (4, 4, 4, 3, 3, 2), (4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 4, 3, 3), (4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    ],
    # Clerc : base identique + 1 emplacement de domaine par niveau castable.
    "Clerc": [
        (3, 1), (4, 2), (4, 2, 1), (4, 3, 2), (4, 3, 2, 1), (4, 3, 3, 2),
        (4, 4, 3, 2, 1), (4, 4, 3, 3, 2), (4, 4, 4, 3, 2, 1),
        (4, 4, 4, 3, 3, 2), (4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 4, 3, 3), (4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    ],
    "Druide": [
        (3, 1), (4, 2), (4, 2, 1), (4, 3, 2), (4, 3, 2, 1), (4, 3, 3, 2),
        (4, 4, 3, 2, 1), (4, 4, 3, 3, 2), (4, 4, 4, 3, 2, 1),
        (4, 4, 4, 3, 3, 2), (4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 3, 2, 1), (4, 4, 4, 4, 4, 4, 4, 3, 3, 2),
        (4, 4, 4, 4, 4, 4, 4, 4, 3, 3), (4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    ],
    "Sorcier": [
        (5, 3), (6, 4), (6, 5), (6, 6, 3), (6, 6, 4, 1), (6, 6, 5, 3),
        (6, 6, 6, 4, 1), (6, 6, 6, 5, 3), (6, 6, 6, 6, 4, 1),
        (6, 6, 6, 6, 5, 3), (6, 6, 6, 6, 6, 4, 1), (6, 6, 6, 6, 6, 5, 3),
        (6, 6, 6, 6, 6, 6, 4, 1), (6, 6, 6, 6, 6, 6, 5, 3),
        (6, 6, 6, 6, 6, 6, 6, 4, 1), (6, 6, 6, 6, 6, 6, 6, 5, 3),
        (6, 6, 6, 6, 6, 6, 6, 6, 4, 1), (6, 6, 6, 6, 6, 6, 6, 6, 5, 3),
        (6, 6, 6, 6, 6, 6, 6, 6, 6, 4), (6, 6, 6, 6, 6, 6, 6, 6, 6, 6),
    ],
    "Barde": [
        (0, 2), (0, 3), (0, 3, 1), (1, 3, 2), (1, 3, 3), (2, 3, 3, 1),
        (2, 3, 3, 2), (2, 3, 3, 3, 1), (2, 3, 3, 3, 2), (3, 3, 3, 3, 3, 1),
        (3, 3, 3, 3, 3, 2), (3, 3, 3, 3, 3, 3, 1), (3, 3, 3, 3, 3, 3, 2),
        (4, 3, 3, 3, 3, 3, 3), (4, 3, 3, 3, 3, 3, 3), (4, 3, 3, 3, 3, 3, 3),
        (4, 3, 3, 3, 3, 3, 3), (4, 3, 3, 3, 3, 3, 3), (4, 3, 3, 3, 3, 3, 3),
        (4, 3, 3, 3, 3, 3, 3),
    ],
    # Paladin / Rodeur : sorts à partir du niveau 4 (max 4e niveau de sort).
    "Paladin": [
        (), (), (), (0, 1), (0, 1), (0, 1), (0, 1, 0), (0, 1, 1), (0, 1, 1),
        (0, 1, 1, 0), (0, 1, 1, 1), (0, 1, 1, 1), (0, 1, 1, 1, 0),
        (0, 1, 1, 1, 1), (0, 2, 1, 1, 1), (0, 2, 2, 1, 1), (0, 2, 2, 2, 1),
        (0, 2, 2, 2, 2), (0, 2, 2, 2, 2), (0, 2, 2, 2, 2),
    ],
    "Rodeur": [
        (), (), (), (0, 1), (0, 1), (0, 1), (0, 1, 0), (0, 1, 1), (0, 1, 1),
        (0, 1, 1, 0), (0, 1, 1, 1), (0, 1, 1, 1), (0, 1, 1, 1, 0),
        (0, 1, 1, 1, 1), (0, 2, 1, 1, 1), (0, 2, 2, 1, 1), (0, 2, 2, 2, 1),
        (0, 2, 2, 2, 2), (0, 2, 2, 2, 2), (0, 2, 2, 2, 2),
    ],
}

# Sorts connus par jour (spontanés uniquement) — PHB 3.5.
CONNUS: dict[str, list[tuple[int, ...]]] = {
    "Sorcier": [
        (4, 2), (5, 2), (5, 3), (6, 4, 2), (6, 4, 3), (6, 4, 4, 2),
        (6, 5, 4, 3), (6, 5, 4, 4, 2), (6, 5, 5, 4, 3), (6, 6, 5, 5, 4, 2),
        (6, 6, 5, 5, 4, 3), (6, 6, 5, 5, 4, 4, 2), (6, 6, 6, 5, 5, 4, 3),
        (6, 6, 6, 5, 5, 4, 4, 2), (6, 6, 6, 6, 5, 5, 4, 3),
        (6, 6, 6, 6, 5, 5, 4, 4, 2), (6, 6, 6, 6, 6, 5, 5, 4, 3),
        (6, 6, 6, 6, 6, 5, 5, 4, 4, 2), (6, 6, 6, 6, 6, 6, 5, 5, 4, 3),
        (6, 6, 6, 6, 6, 6, 5, 5, 5, 4),
    ],
    "Barde": [
        (0, 4), (0, 5), (0, 6, 2), (4, 6, 3), (4, 6, 4, 2), (5, 6, 4, 3),
        (5, 6, 5, 3), (5, 6, 5, 4, 2), (5, 6, 5, 4, 3), (5, 6, 5, 5, 4, 2),
        (5, 6, 5, 5, 4, 3), (5, 6, 5, 5, 5, 4, 2), (5, 6, 5, 5, 5, 4, 3),
        (6, 6, 5, 5, 5, 5, 4, 2), (6, 6, 6, 5, 5, 5, 5, 3),
        (6, 6, 6, 6, 5, 5, 5, 4, 2), (6, 6, 6, 6, 6, 5, 5, 5, 4, 2),
        (6, 6, 6, 6, 6, 6, 5, 5, 5, 3), (6, 6, 6, 6, 6, 6, 6, 5, 5, 4, 2),
        (6, 6, 6, 6, 6, 6, 6, 5, 5, 5, 3),
    ],
}

# --------------------------------------------------------------------------- #
#  Catalogue des sorts (niveaux 0-5, liste curatée PHB 3.5)
# --------------------------------------------------------------------------- #
S_ = "Sorcier"
W_ = "Magicien"
C_ = "Clerc"
D_ = "Druide"
B_ = "Barde"
P_ = "Paladin"
R_ = "Rodeur"

SORTS: list[dict[str, Any]] = [
    # ------------------------------ Niveau 0 ------------------------------ #
    {"nom": "Résistance", "niveau": 0, "ecole": "Abjuration",
     "classes": [W_, S_, C_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 minute",
     "sauvegarde": "", "effet": None,
     "description": "+1 aux jets de sauvegarde du porteur."},
    {"nom": "Détection de la magie", "niveau": 0, "ecole": "Divination",
     "classes": [W_, S_, C_, D_, B_], "incantation": "1 action simple",
     "portee": "18 m", "composantes": "V, S", "duree": "concentration, 1 round",
     "sauvegarde": "", "effet": None,
     "description": "Perçoit les auras magiques dans la zone."},
    {"nom": "Lecture de la magie", "niveau": 0, "ecole": "Divination",
     "classes": [W_, S_, C_, D_, B_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S, M", "duree": "10 minutes",
     "sauvegarde": "", "effet": None,
     "description": "Lit les écrits magiques et les inscriptions occultes."},
    {"nom": "Étourdi", "niveau": 0, "ecole": "Enchantement",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V", "duree": "1 round",
     "sauvegarde": "Volonté annule", "effet": {"type": "etat", "condition": "Étourdi"},
     "description": "Humanoïde de 4 DV ou moins perd son tour (réussite partielle)."},
    {"nom": "Lumières dansantes", "niveau": 0, "ecole": "Évocation",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S", "duree": "1 minute",
     "sauvegarde": "", "effet": None,
     "description": "Quatre lumières vacillantes éclairent ou signalent."},
    {"nom": "Éblouissement", "niveau": 0, "ecole": "Évocation",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V", "duree": "instantanée",
     "sauvegarde": "non", "effet": None,
     "description": "Éclat de lumière qui éblouit une créature (-1 à l'attaque)."},
    {"nom": "Rayon de givre", "niveau": 0, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "non",
     "effet": {"type": "degats", "des": "1d3", "par_niveau": 0, "max_des": 0, "element": "froid"},
     "description": "Rayon de froid infligeant 1d3 dégâts."},
    {"nom": "Prestidigitation", "niveau": 0, "ecole": "Universal",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "3 m", "composantes": "V, S", "duree": "1 heure",
     "sauvegarde": "", "effet": None,
     "description": "Petites illusions et tours de passe-passe inoffensifs."},
    {"nom": "Réparation", "niveau": 0, "ecole": "Transmutation",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "3 m", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "", "effet": None,
     "description": "Répare un petit objet brisé."},
    {"nom": "Lumière", "niveau": 0, "ecole": "Évocation",
     "classes": [C_, D_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, M/F", "duree": "10 minutes",
     "sauvegarde": "Volonté annule", "effet": None,
     "description": "Illumine un objet comme une torche."},
    {"nom": "Soin mineur", "niveau": 0, "ecole": "Nécromancie",
     "classes": [C_, D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "1", "par_niveau": 0, "max_des": 0, "fixe": 1},
     "description": "Restaure 1 PV (ne soigne pas les blessures d'attributs)."},
    {"nom": "Orientation", "niveau": 0, "ecole": "Divination",
     "classes": [C_, D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "concentration",
     "sauvegarde": "", "effet": None,
     "description": "+1 à un jet (attaque, compétence, sauvegarde) du sujet."},
    {"nom": "Vertu", "niveau": 0, "ecole": "Transmutation",
     "classes": [C_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 minute",
     "sauvegarde": "", "effet": None,
     "description": "+1 PV temporaires au sujet."},
    {"nom": "Message", "niveau": 0, "ecole": "Transmutation",
     "classes": [B_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S, M/F", "duree": "10 rounds",
     "sauvegarde": "", "effet": None,
     "description": "Conversation chuchotée à distance."},

    # ------------------------------ Niveau 1 ------------------------------ #
    {"nom": "Armure du mage", "niveau": 1, "ecole": "Conjuration",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 heure/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+4 de CA d'armure (force l'armure sur le contact)."},
    {"nom": "Bouclier", "niveau": 1, "ecole": "Abjuration",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Bouclier invisible : +4 CA, +2 contre les rayons."},
    {"nom": "Projectiles magiques", "niveau": 1, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "moyenne (45 m)", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "non",
     "effet": {"type": "degats", "des": "1d4", "par_niveau": 1, "max_des": 5,
               "element": "force", "auto": True, "par_multiple": 1,
               "desc_special": "1 projectile au niv.1, +1 tous les 2 niveaux de lanceur (2 au niv.3, 3 au niv.5…)"},
     "description": "1-5 projectiles de force touchant automatiquement (1d4+1 chacun)."},
    {"nom": "Mains brûlantes", "niveau": 1, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "5 m (cône)", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Réflexes demi",
     "effet": {"type": "degats", "des": "1d4", "par_niveau": 1, "max_des": 5, "element": "feu"},
     "description": "Cône de feu (1d4/niveau, max 5d4)."},
    {"nom": "Charme-personne", "niveau": 1, "ecole": "Enchantement",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S", "duree": "1 heure/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Charmé"},
     "description": "Un humanoïde vous considère comme un ami bienveillant."},
    {"nom": "Sommeil", "niveau": 1, "ecole": "Enchantement",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Endormi"},
     "description": "4 DV de créatures s'endorment (les plus faibles d'abord)."},
    {"nom": "Rayon affaiblissant", "niveau": 1, "ecole": "Nécromancie",
     "classes": [W_, S_], "incantation": "1 action de contact",
     "portee": "contact", "composantes": "V, S", "duree": "1 round/niveau",
     "sauvegarde": "non",
     "effet": {"type": "buff"},
     "description": "1d4+1 de malus de FOR à la cible (jet d'attaque de toucher)."},
    {"nom": "Graisse", "niveau": 1, "ecole": "Conjuration",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "Réflexes annule",
     "effet": {"type": "etat", "condition": "À terre"},
     "description": "Sol glissant : chute, désarmement, évasion difficile."},
    {"nom": "Saut", "niveau": 1, "ecole": "Transmutation",
     "classes": [W_, S_, D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+10 au jet de Saut du sujet."},
    {"nom": "Compréhension des langues", "niveau": 1, "ecole": "Divination",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S, M", "duree": "10 minutes/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Comprend toute langue parlée ou écrite."},
    {"nom": "Brume d'obscurcissement", "niveau": 1, "ecole": "Conjuration",
     "classes": [W_, S_, D_], "incantation": "1 action simple",
     "portee": "zone (rayon 6 m)", "composantes": "V, S", "duree": "10 minutes/niveau",
     "sauvegarde": "", "effet": None,
     "description": "Brouillard obscurcissant la vision autour du lanceur."},
    {"nom": "Trait de feu", "niveau": 1, "ecole": "Évocation",
     "classes": [D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S/DF", "duree": "1 minute/niveau",
     "sauvegarde": "non",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 1, "element": "feu"},
     "description": "Torche enflammée : 1d6 feu au contact (1d6/niveau)."},
    {"nom": "Enchevêtrement", "niveau": 1, "ecole": "Transmutation",
     "classes": [D_, R_], "incantation": "1 action simple",
     "portee": "proche (120 m)", "composantes": "V, S, DF", "duree": "1 minute/niveau",
     "sauvegarde": "Réflexes annule",
     "effet": {"type": "etat", "condition": "Immobilisé"},
     "description": "La végétation retient les créatures dans un rayon de 12 m."},
    {"nom": "Bénédiction", "niveau": 1, "ecole": "Enchantement",
     "classes": [C_, P_], "incantation": "1 action simple",
     "portee": "zone (rayon 15 m)", "composantes": "V, S, DF", "duree": "2 minutes",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+1 à l'attaque et contre la peur pour les alliés."},
    {"nom": "Soins légers", "niveau": 1, "ecole": "Conjuration",
     "classes": [C_, D_, B_, P_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "1d8", "par_niveau": 1, "max_des": 5},
     "description": "Restaure 1d8+1/niveau PV (max 5d8+5)."},
    {"nom": "Fléau", "niveau": 1, "ecole": "Nécromancie",
     "classes": [C_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S, DF", "duree": "1 round/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Effrayé"},
     "description": "Une créature de 5 DV ou moins fuit, tremblante."},
    {"nom": "Arme magique", "niveau": 1, "ecole": "Transmutation",
     "classes": [C_, P_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S/DF", "duree": "1 minute/niveau",
     "sauvegarde": "Volonté annule (objet)",
     "effet": {"type": "buff"},
     "description": "Une arme devient magique (+1) — touche les créatures immunisées."},
    {"nom": "Protection contre le mal", "niveau": 1, "ecole": "Abjuration",
     "classes": [C_, P_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M/DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Défense contre les créatures mauvaises (+2 CA, blocage du contact)."},
    {"nom": "Faveur divine", "niveau": 1, "ecole": "Évocation",
     "classes": [C_, P_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S", "duree": "1 round",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+1/niveau (max +3) aux attaques et dégâts."},
    {"nom": "Injonction", "niveau": 1, "ecole": "Enchantement",
     "classes": [C_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V", "duree": "1 round",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Ordre suivi"},
     "description": "Impose une action simple (« tombe », « fuis », « halte »…)."},
    {"nom": "Alarme", "niveau": 1, "ecole": "Abjuration",
     "classes": [R_], "incantation": "1 round",
     "portee": "proche (7,50 m)", "composantes": "V, S, M/F/DF", "duree": "2 heures/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Zone d'alarme mentale ou sonore."},
    {"nom": "Détection des animaux ou des plantes", "niveau": 1, "ecole": "Divination",
     "classes": [D_, R_], "incantation": "1 action simple",
     "portee": "proche (45 m)", "composantes": "V, S", "duree": "concentration, 10 min/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Localise animaux ou plantes dans la zone."},

    # ------------------------------ Niveau 2 ------------------------------ #
    {"nom": "Invisibilité", "niveau": 2, "ecole": "Illusion",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 minute/niveau",
     "sauvegarde": "non", "effet": {"type": "buff"},
     "description": "Le sujet devient invisible (rompu par une action offensive)."},
    {"nom": "Image répétée", "niveau": 2, "ecole": "Illusion",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S", "duree": "1 round",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "1d4+1/niveau (max 8) duplicatas décalés : +4 en défense."},
    {"nom": "Détection de l'invisibilité", "niveau": 2, "ecole": "Divination",
     "classes": [W_, S_, C_, B_], "incantation": "1 action simple",
     "portee": "personnelle", "composantes": "V, S", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Voit les créatures et objets invisibles."},
    {"nom": "Toile d'araignée", "niveau": 2, "ecole": "Conjuration",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "moyenne (60 m)", "composantes": "V, S, M", "duree": "10 minutes/niveau",
     "sauvegarde": "Réflexes annule",
     "effet": {"type": "etat", "condition": "Empêtré"},
     "description": "Zone toile d'araignée de 6 m de rayon : piège et gêne."},
    {"nom": "Lévitation", "niveau": 2, "ecole": "Transmutation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "personnelle/proche", "composantes": "V, S, F", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Se déplace verticalement en lévitation."},
    {"nom": "Sombre vision", "niveau": 2, "ecole": "Transmutation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 heure/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Vision dans le noir sur 18 m."},
    {"nom": "Force de taureau", "niveau": 2, "ecole": "Transmutation",
     "classes": [C_, D_, W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M/DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+4 de FOR au sujet."},
    {"nom": "Endurance de l'ours", "niveau": 2, "ecole": "Transmutation",
     "classes": [C_, D_, W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M/DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+4 de CON au sujet (+2 PV/niveau)."},
    {"nom": "Sagesse du hibou", "niveau": 2, "ecole": "Transmutation",
     "classes": [C_, D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M/DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+4 de SAG au sujet."},
    {"nom": "Dextérité du chat", "niveau": 2, "ecole": "Transmutation",
     "classes": [B_, D_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M/DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+4 de DEX au sujet."},
    {"nom": "Soins modérés", "niveau": 2, "ecole": "Conjuration",
     "classes": [C_, D_, B_, P_, R_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "2d8", "par_niveau": 1, "max_des": 10},
     "description": "Restaure 2d8+1/niveau PV (max 10d8+10)."},
    {"nom": "Restauration partielle", "niveau": 2, "ecole": "Conjuration",
     "classes": [C_, D_, P_], "incantation": "3 rounds",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "", "effet": {"type": "soin", "des": "1d4", "par_niveau": 0,
                                "max_des": 0, "fixe": 0,
                                "desc_special": "supprime 1d4 PV de dégâts d'attribut"},
     "description": "Supprime 1d4 de dégâts d'attribut ou un malus magique mineur."},
    {"nom": "Retardement du poison", "niveau": 2, "ecole": "Conjuration",
     "classes": [C_, D_, P_, R_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, DF", "duree": "1 heure/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Le sujet résiste au poison pendant la durée (+4 de compétence)."},
    {"nom": "Silence", "niveau": 2, "ecole": "Illusion",
     "classes": [C_, B_], "incantation": "1 round",
     "portee": "longue (120 m)", "composantes": "V, S", "duree": "1 round/niveau",
     "sauvegarde": "non",
     "effet": {"type": "etat", "condition": "Réduit au silence"},
     "description": "Zone de 6 m où aucun son n'existe — bloque la magie verbale."},
    {"nom": "Résistance aux éléments", "niveau": 2, "ecole": "Abjuration",
     "classes": [C_, D_, R_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, DF", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Le sujet encaisse chaleur ou froid extrêmes."},
    {"nom": "Arme spirituelle", "niveau": 2, "ecole": "Évocation",
     "classes": [C_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S, DF", "duree": "1 round/niveau",
     "sauvegarde": "non",
     "effet": {"type": "degats", "des": "1d8", "par_niveau": 0, "max_des": 0,
               "element": "force", "auto": True,
               "desc_special": "attaque magique à distance automatique chaque round"},
     "description": "Arme de force frappant seule (1d8 + mod. SAG)."},
    {"nom": "Suggestion", "niveau": 2, "ecole": "Enchantement",
     "classes": [B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, M", "duree": "1 heure/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Charmé"},
     "description": "Impose un cours d'action raisonnable à une créature."},
    {"nom": "Convocation des animaux I", "niveau": 2, "ecole": "Conjuration",
     "classes": [D_, R_], "incantation": "1 round",
     "portee": "proche (7,50 m)", "composantes": "V, S, DF", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire",
                                "desc_special": "invoque 1 animal (loup, aigle…) allié"},
     "description": "Invoque un animal qui combat pour le lanceur."},

    # ------------------------------ Niveau 3 ------------------------------ #
    {"nom": "Boule de feu", "niveau": 3, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "longue (120 m)", "composantes": "V, S, M", "duree": "instantanée",
     "sauvegarde": "Réflexes demi",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 10,
               "element": "feu"},
     "description": "Explosion de feu (rayon 6 m) : 1d6/niveau, max 10d6."},
    {"nom": "Éclair", "niveau": 3, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "longue (36 m)", "composantes": "V, S, M", "duree": "instantanée",
     "sauvegarde": "Réflexes demi",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 10,
               "element": "électricité"},
     "description": "Foudre en ligne de 36 m : 1d6/niveau, max 10d6."},
    {"nom": "Vol", "niveau": 3, "ecole": "Transmutation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, F", "duree": "1 minute/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Le sujet vole à 18 m/round."},
    {"nom": "Dissipation de la magie", "niveau": 3, "ecole": "Abjuration",
     "classes": [W_, S_, C_, D_, B_, P_, R_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "",
     "effet": {"type": "utilitaire",
               "desc_special": "jet 1d20+ niveau de lanceur vs 11 + NLS de l'effet"},
     "description": "Annule les effets magiques actifs (sorts, buffs…)."},
    {"nom": "Accélération", "niveau": 3, "ecole": "Transmutation",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "+1 attaque, +4 CA, réflexes accrus, déplacement x2."},
    {"nom": "Lenteur", "niveau": 3, "ecole": "Transmutation",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Ralenti"},
     "description": "Une créature est ralentie (-1 attaque/CA/réflexes, action unique)."},
    {"nom": "Vampirisation", "niveau": 3, "ecole": "Nécromancie",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "non",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 5,
               "element": "nécrotique", "soigne_lanceur": True},
     "description": "Toucher : 1d6/niveau dégâts, le lanceur récupère autant."},
    {"nom": "Lumière du jour", "niveau": 3, "ecole": "Évocation",
     "classes": [C_, D_, R_], "incantation": "1 action simple",
     "portee": "zone (rayon 18 m)", "composantes": "V, S", "duree": "10 minutes/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Lumière vive — gêne les créatures des ténèbres."},
    {"nom": "Soins sérieux", "niveau": 3, "ecole": "Conjuration",
     "classes": [C_, D_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "3d8", "par_niveau": 1, "max_des": 15},
     "description": "Restaure 3d8+1/niveau PV (max 15d8+15)."},
    {"nom": "Prière", "niveau": 3, "ecole": "Enchantement",
     "classes": [C_], "incantation": "1 action simple",
     "portee": "zone (rayon 12 m)", "composantes": "V, S, DF", "duree": "1 round",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Alliés +1 (attaque/dégâts/sauves), ennemis -1."},
    {"nom": "Convocation des animaux II", "niveau": 3, "ecole": "Conjuration",
     "classes": [D_, R_], "incantation": "1 round",
     "portee": "proche (7,50 m)", "composantes": "V, S, DF", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire",
                                "desc_special": "invoque 1d3 animaux alliés"},
     "description": "Invoque plusieurs animaux qui combattent pour le lanceur."},

    # ------------------------------ Niveau 4 ------------------------------ #
    {"nom": "Tempête de glace", "niveau": 4, "ecole": "Évocation",
     "classes": [W_, S_, D_], "incantation": "1 action simple",
     "portee": "longue (120 m)", "composantes": "V, S", "duree": "1 round",
     "sauvegarde": "non",
     "effet": {"type": "degats", "degats_fixes": "3d6+2d6",
               "element": "froid/perforant"},
     "description": "Grêle sur une zone : 3d6 froid + 2d6 perforant, sol glissant."},
    {"nom": "Peur", "niveau": 4, "ecole": "Nécromancie",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "proche (9 m, cône)", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Paniqué"},
     "description": "Les créatures dans le cône s'enfuient en panique."},
    {"nom": "Porte dimensionnelle", "niveau": 4, "ecole": "Conjuration",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "longue (120 m)", "composantes": "V", "duree": "instantanée",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Téléportation courte du lanceur (ou objet léger)."},
    {"nom": "Invisibilité supérieure", "niveau": 4, "ecole": "Illusion",
     "classes": [W_, S_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Invisibilité qui ne rompt pas en attaquant."},
    {"nom": "Soins critiques", "niveau": 4, "ecole": "Conjuration",
     "classes": [C_, D_, B_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "4d8", "par_niveau": 1, "max_des": 20},
     "description": "Restaure 4d8+1/niveau PV (max 20d8+20)."},
    {"nom": "Restauration", "niveau": 4, "ecole": "Conjuration",
     "classes": [C_, P_], "incantation": "3 rounds",
     "portee": "contact", "composantes": "V, S, M/XP", "duree": "instantanée",
     "sauvegarde": "", "effet": {"type": "soin", "des": "0", "par_niveau": 0,
                                "max_des": 0, "fixe": 0,
                                "desc_special": "restaure attributs et niveaux drainés"},
     "description": "Annule dégâts d'attributs, drains de niveau et malus magiques."},
    {"nom": "Liberté de mouvement", "niveau": 4, "ecole": "Abjuration",
     "classes": [C_, D_, B_, P_, R_], "incantation": "1 action simple",
     "portee": "contact", "composantes": "V, S, M", "duree": "10 minutes/niveau",
     "sauvegarde": "", "effet": {"type": "buff"},
     "description": "Immunité à l'immobilisation, à la paralysie et aux entraves."},
    {"nom": "Convocation des animaux III", "niveau": 4, "ecole": "Conjuration",
     "classes": [D_, R_], "incantation": "1 round",
     "portee": "proche (7,50 m)", "composantes": "V, S, DF", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire",
                                "desc_special": "invoque 1 animal puissant allié"},
     "description": "Invoque un animal de haut niveau qui combat pour le lanceur."},

    # ------------------------------ Niveau 5 ------------------------------ #
    {"nom": "Cône de froid", "niveau": 5, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "18 m (cône)", "composantes": "V, S, M", "duree": "instantanée",
     "sauvegarde": "Réflexes demi",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 15,
               "element": "froid"},
     "description": "Cône de froid : 1d6/niveau, max 15d6."},
    {"nom": "Mur de force", "niveau": 5, "ecole": "Évocation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S, M", "duree": "1 round/niveau",
     "sauvegarde": "", "effet": {"type": "utilitaire"},
     "description": "Barrière de force invisible et indestructible."},
    {"nom": "Télékinésie", "niveau": 5, "ecole": "Transmutation",
     "classes": [W_, S_], "incantation": "1 action simple",
     "portee": "longue (120 m)", "composantes": "V, S", "duree": "concentration",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "utilitaire"},
     "description": "Déplace mentalement des objets (jusqu'à 11 kg/niveau)."},
    {"nom": "Domination (personne)", "niveau": 5, "ecole": "Enchantement",
     "classes": [W_, S_, B_], "incantation": "1 round",
     "portee": "proche (7,50 m)", "composantes": "V, S, M", "duree": "1 jour/niveau",
     "sauvegarde": "Volonté annule",
     "effet": {"type": "etat", "condition": "Dominé"},
     "description": "Contrôle mental d'un humanoïde par télépathie."},
    {"nom": "Résurrection", "niveau": 5, "ecole": "Conjuration",
     "classes": [C_], "incantation": "1 minute",
     "portee": "contact", "composantes": "V, S, M, DF", "duree": "instantanée",
     "sauvegarde": "", "effet": {"type": "soin", "des": "0", "par_niveau": 0,
                                "max_des": 0, "fixe": 1,
                                "desc_special": "ramène un mort (-1 niveau) à la vie avec 1 PV"},
     "description": "Rend la vie à une créature morte depuis moins d'une journée/niveau."},
    {"nom": "Colonne de feu", "niveau": 5, "ecole": "Évocation",
     "classes": [C_, D_], "incantation": "1 action simple",
     "portee": "moyenne (30 m)", "composantes": "V, S, DF", "duree": "instantanée",
     "sauvegarde": "Réflexes demi",
     "effet": {"type": "degats", "des": "1d6", "par_niveau": 1, "max_des": 15,
               "element": "feu"},
     "description": "Colonne de feu de 3 m de rayon : 1d6/niveau, max 15d6."},
    {"nom": "Soins de masse légers", "niveau": 5, "ecole": "Conjuration",
     "classes": [C_, B_], "incantation": "1 action simple",
     "portee": "proche (7,50 m)", "composantes": "V, S", "duree": "instantanée",
     "sauvegarde": "Volonté annule (contre-soin)",
     "effet": {"type": "soin", "des": "1d8", "par_niveau": 1, "max_des": 5,
               "masse": True},
     "description": "Soins légers sur toutes les créatures alliées d'un rayon de 9 m."},
]

_SORTS_PAR_NOM: dict[str, dict[str, Any]] = {s["nom"].lower(): s for s in SORTS}


# --------------------------------------------------------------------------- #
#  Logique : disponibilité, emplacements, bonus de caractéristique
# --------------------------------------------------------------------------- #
def est_lanceur(classe: str) -> bool:
    """True si la classe apparaît dans au moins une liste de sorts."""
    return classe in _E


def carac_incantation(classe: str) -> str:
    """Caractéristique clé d'incantation (INT/SAG/CHA) — 'INT' par défaut."""
    return CARAC_INCANTATION.get(classe, "INT")


def type_lancement(classe: str) -> str:
    """« préparé » (mémorisation quotidienne) ou « spontané » (connus)."""
    return "spontané" if classe in SPONTANE else "préparé"


def _base(classe: str, niveau: int) -> tuple[int, ...]:
    n = max(1, min(20, int(niveau or 1)))
    table = _E.get(classe)
    return table[n - 1] if table else ()


def niveau_sort_max(classe: str, niveau: int) -> int:
    """Niveau de sort le plus haut que cette classe/niveau peut lancer."""
    base = _base(classe, niveau)
    for i in range(len(base) - 1, -1, -1):
        if base[i] > 0:
            return i
    return -1  # aucun sort


def emplacements(classe: str, niveau: int, mod_carac: int = 0) -> dict[int, int]:
    """Emplacements de sorts par jour, bonus de caractéristique inclus.

    Bonus PHB : +1 emplacement de niveau L si mod >= 2L-1 (ex. mod +3 →
    +1 du 1er et +1 du 2e), seulement si le niveau L est déjà castable.
    Le clerc reçoit en plus 1 emplacement de domaine par niveau castable.
    """
    base = _base(classe, niveau)
    total: dict[int, int] = {i: v for i, v in enumerate(base)}
    nls = niveau_sort_max(classe, niveau)
    for lvl in range(1, nls + 1):
        if mod_carac >= 2 * lvl - 1:
            total[lvl] = total.get(lvl, 0) + 1
    if classe == "Clerc":  # emplacement de domaine par niveau castable
        for lvl in range(nls + 1):
            total[lvl] = total.get(lvl, 0) + 1
    return {k: v for k, v in sorted(total.items()) if v > 0}


def sorts_connus_max(classe: str, niveau: int) -> dict[int, int]:
    """Nombre max de sorts distincts connus (spontanés uniquement)."""
    table = CONNUS.get(classe)
    if not table:
        return {}
    n = max(1, min(20, int(niveau or 1)))
    return {i: v for i, v in enumerate(table[n - 1]) if v > 0}


def sorts_pour(classe: str, niveau_sort_max_autorise: Optional[int] = None) -> list[dict[str, Any]]:
    """Liste des sorts accessibles à la classe (filtrée par niveau de sort)."""
    res = [dict(s) for s in SORTS if classe in s["classes"]]
    if niveau_sort_max_autorise is not None:
        res = [s for s in res if s["niveau"] <= niveau_sort_max_autorise]
    res.sort(key=lambda s: (s["niveau"], s["nom"]))
    return res


def sort_par_nom(nom: str) -> Optional[dict[str, Any]]:
    """Recherche insensible casse/accents d'un sort par son nom."""
    cible = _norm(nom)
    for s in SORTS:
        if _norm(s["nom"]) == cible:
            return s
    return None


def _norm(s: str) -> str:
    import unicodedata
    nf = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nf if not unicodedata.combining(c)).strip()


def sorts_de_fiche(fiche: dict[str, Any]) -> dict[str, Any]:
    """Champ `sorts` normalisé d'une fiche (tolérant aux fiches anciennes)."""
    s = fiche.get("sorts") if isinstance(fiche.get("sorts"), dict) else {}
    connus = s.get("connus") or []
    if isinstance(connus, str):
        connus = [x.strip() for x in connus.split(",") if x.strip()]
    prepares = s.get("prepares") if isinstance(s.get("prepares"), dict) else {}
    depenses = s.get("depenses") if isinstance(s.get("depenses"), dict) else {}
    return {"connus": list(connus), "prepares": dict(prepares), "depenses": dict(depenses)}


def resume_sorts(fiche: dict[str, Any]) -> str:
    """Ligne récap magie pour le prompt MJ : emplacements restants + sorts
    prêts. Renvoie '' si le personnage n'a aucune magie."""
    classe = str(fiche.get("classe") or "")
    if not est_lanceur(classe):
        return ""
    nls = niveau_sort_max(classe, int(fiche.get("niveau") or 1))
    if nls < 0:
        return ""
    carac = carac_incantation(classe)
    val = int((fiche.get("carac") or {}).get(carac, 10) or 10)
    mod = (val - 10) // 2
    slots = emplacements(classe, int(fiche.get("niveau") or 1), mod)
    s = sorts_de_fiche(fiche)
    restants = {lvl: slots.get(lvl, 0) - int(s["depenses"].get(str(lvl), 0))
                for lvl in slots}
    parties = [
        "Emplacements " + ", ".join(f"n.{lvl}: {r}/{slots[lvl]}"
                                    for lvl, r in sorted(restants.items()))
    ]
    if classe in SPONTANE:
        if s["connus"]:
            parties.append("Sorts connus : " + ", ".join(s["connus"]))
    elif s["prepares"]:
        parties.append(
            "Préparés : " + ", ".join(
                f"{nom} x{n}" for nom, n in sorted(s["prepares"].items()) if n > 0))
    return " · ".join(parties)


def depassement_connus(classe: str, niveau: int, connus: list[str]) -> dict[int, int]:
    """Niveaux de sorts où la liste `connus` dépasse le budget (spontanés)."""
    budget = sorts_connus_max(classe, niveau)
    exces: dict[int, int] = {}
    for s in connus:
        sp = sort_par_nom(s) if isinstance(s, str) else None
        if sp is None:
            continue
        lvl = sp["niveau"]
        if lvl in budget:
            exces[lvl] = exces.get(lvl, 0) + 1
    return {lvl: n - budget[lvl] for lvl, n in exces.items() if n > budget[lvl]}
