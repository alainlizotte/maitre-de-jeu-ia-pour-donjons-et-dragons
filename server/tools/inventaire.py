"""Outil Inventaire & Charge — gestion d'inventaire et d'encombrement D&D 3.5.

Permet de suivre la charge transportée par un personnage de façon MÉCANIQUE et
conforme au Manuel du Joueur 3.5 :
  - inventaire structuré : liste d'objets `{nom, qte}` (même forme que le champ
    `equipement` des fiches existantes, avec un poids par unité issu d'un
    catalogue d'équipement officiel ou fourni explicitement) ;
  - poids transporté recalculé automatiquement à chaque modification (kg) ;
  - catégorie d'encombrement calculée d'après la table de charge du PHB 3.5
    (Légère ≤ ⅓ de la charge max, Moyenne ≤ ⅔, Lourde ≤ max, Dépassée au-delà) ;
  - consommation d'ammunition (flèches, balles de fronde, carreaux) quand un PJ
    tire à distance.

Les fiches peuvent être créées soit par le formulaire (qui écrit `charge_max`),
soit par les tools LLM `fiche_perso_creer*` (qui n'écrivent PAS `charge_max`).
Pour être robuste, on recalcule toujours la capacité max à la volée depuis la
Force + la catégorie de taille de la race, en écrivant `charge_max` si absent.
"""

from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from typing import Any, Optional

from .base import ToolContext, ToolResult, tool


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
_LB_EN_KG = 0.4536


def _norm(s: Any) -> str:
    """Normalise un nom d'objet : minuscule, sans accent, singulier approx."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    # Singulier grossier (retire le 's' final / 'aux' → 'au') pour matcher
    # « flèches » avec « flèche ».
    if len(s) > 2 and s.endswith(("aux", "eaux")):
        s = s[:-3] + "au"
    elif len(s) > 2 and s.endswith("s"):
        s = s[:-1]
    return s


# Objets désignés sous PLUSIEURS noms par le modèle : la même potion était
# ajoutée 3× sous 3 libellés (« fiole de guérison », « fiole de soins
# légers », « potion de soins légers » — partie 4d4b4557 : inventaire
# pollué, fioles ×5). Clé = `_norm(libellé)` → nom canonique de fusion.
# NB : `_norm` ne retire que le « s » FINAL (« ...soins legers » →
# « ...soins leger ») : les variantes avec/sans « s » interne sont listées.
_OBJETS_SYNONYMES: dict[str, str] = {
    # Potions de soins (cures légères).
    "potion de soins leger": "potion de soins legers",
    "potion de soin leger": "potion de soins legers",
    "potion de soin": "potion de soins legers",
    "potion de guerison": "potion de soins legers",
    "fiole de soins leger": "potion de soins legers",
    "fiole de soin leger": "potion de soins legers",
    "fiole de soin": "potion de soins legers",
    "fiole de guerison": "potion de soins legers",
    "flacon de soins leger": "potion de soins legers",
    "flacon de soin leger": "potion de soins legers",
    "flacon de soin": "potion de soins legers",
    "flacon de guerison": "potion de soins legers",
    "potion de cure light wound": "potion de soins legers",
    "potion of cure light wound": "potion de soins legers",
    # Potions de soins modérés.
    "potion de soins modere": "potion de soins moderes",
    "potion de soin modere": "potion de soins moderes",
    "fiole de soins modere": "potion de soins moderes",
    "fiole de soin modere": "potion de soins moderes",
    "potion de cure moderate wound": "potion de soins moderes",
}


def _cles_prefixe(a: str, b: str) -> bool:
    """Vrai si les clés normalisées `a`/`b` désignent probablement le MÊME
    objet par relation de préfixe (≥ 4 caractères communs au début) :
    « fiole » ⊂ « fiole de verite », « parchemin » ⊂ « parchemin de la
    route » — les libellés abrégés du modèle (partie b59b4a9a)."""
    x, y = str(a or "").strip(), str(b or "").strip()
    if not x or not y or x == y:
        return False
    court, long = sorted((x, y), key=len)
    return len(court) >= 4 and long.startswith(court + " ")


def _cle_objet(nom: Any) -> str:
    """Clé de fusion d'un objet : synonymes d'abord, `_norm` sinon — SANS les
    articles, la même normalisation que `objectifs._cle_objet` : les
    comparaisons inventaire ↔ requis du scénario doivent coïncider (partie
    e55cc855 : « Le Beljuril » ajouté — requis « beljuril » — n'était pas
    reconnu requis, la portée restait « permanent » et l'objet était
    invisible de l'inventaire de quête : progression bloquée)."""
    n = _norm(nom)
    try:
        from ..game.objectifs import _sans_article  # lazy : évite un cycle
        n = _sans_article(n)
    except Exception:                                    # noqa: BLE001
        pass
    return _OBJETS_SYNONYMES.get(n, n)


# --------------------------------------------------------------------------- #
#  Portée des objets (demande utilisateur, partie bb4c4fb9) :
#  - PERMANENT : armes, armures, munitions, trésors, achats chez un marchand
#    → restent au PJ d'une partie à l'autre ;
#  - QUÊTE : dons de PNJ et objets de l'aventure (potions, fioles, or donné,
#    cartes, clés, parchemins, lettres…) → liés à LA PARTIE en cours
#    (tagués `portee="quete"` + `partie=<id>` dans la fiche, et purgés de la
#    fiche dès qu'une AUTRE partie démarre — cf. purge dans main.py).
# --------------------------------------------------------------------------- #
_PORTEE_PERMANENT_RE = re.compile(
    r"\b(arme|armes|epee|sabre|hache|marteau|dague|lance|arc|arbalete"
    r"|fleche|carreau|balle de fronde"
    r"|armure|armures|ecailles|cotte de mailles|bouclier|pavois|casque"
    r"|heaume|gemme|joyau|diamant|rubis|emeraude|saphir|beljuril|jacinthe"
    r"|tresor|magique|gauntlet|gantelet|bague|amulette|cape|baton|baguette)\b"
)


def _portee_auto(objet: str) -> str:
    """Portée par défaut d'un objet selon sa nature : arme/armure/trésor →
    permanent ; le reste (potions, cartes, clés, documents, or donné…) →
    inventaire de quête."""
    n = _norm(objet)
    return "permanent" if _PORTEE_PERMANENT_RE.search(n) else "quete"


def _portee_de(e: dict[str, Any]) -> str:
    """Portée enregistrée d'une entrée d'inventaire (sans tag = permanent :
    tout l'équipement antérieur au système reste au PJ)."""
    return "quete" if str(e.get("portee") or "") == "quete" else "permanent"


def _requis_scenario(ctx: ToolContext) -> set[str]:
    """Objets REQUIS par la trame en cours (manifeste `requis`), normalisés.
    Sert à forcer `portee="quete"` pour les objets de l'aventure (gemmes,
    couronnes, clés…) même quand l'heuristique les rangerait « permanent »."""
    try:
        from ..game.objectifs import requis_scenario_noms  # pylint: disable=import-outside-toplevel
        from ..game.state import PartyState                # pylint: disable=import-outside-toplevel
        etat = PartyState(data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
        return requis_scenario_noms(etat or {}, ctx.data_dir)
    except Exception:                                       # noqa: BLE001
        return set()


def _entree_portee_ok(e: dict[str, Any], portee: str, partie_id: str) -> bool:
    """L'entrée `e` appartient-elle à la portée demandée ?

    - "quete" : taguée quête ET taguée de LA partie courante (les quêtes
      antérieures ne sont ni visibles ni consommables — elles sont purgées
      à part) ;
    - "permanent" : tout ce qui n'est pas un objet de quête ;
    - "auto" : les deux (consommation : quête de la partie d'abord)."""
    ep = _portee_de(e)
    if portee == "quete":
        return ep == "quete" and str(e.get("partie") or "") == partie_id
    if portee == "permanent":
        return ep == "permanent"
    return True


def _chemin_fiche(ctx: ToolContext, nom: str) -> Optional[str]:
    """Chemin vers la fiche (résolution PJ/pseudo-joueur comme dans fiches.py)."""
    try:
        from .fiches import _chemin  # pylint: disable=import-outside-toplevel
        return _chemin(ctx, nom)
    except Exception:                                                # noqa: BLE001
        return None


def _charge_max(fiche: dict[str, Any]) -> int:
    """Charge max transportable (kg) d'une fiche — Force + catégorie de taille.

    Priorité à `charge_max` déjà écrit (formulaire) ; sinon on le calcule via
    `persos.charge_maximale`. Pour une fiche sans race connue, taille Moyenne.
    """
    cm = fiche.get("charge_max")
    if cm:
        try:
            return int(cm)
        except (TypeError, ValueError):
            pass
    try:
        from ..persos import RACES, charge_maximale, resoudre_race  # pylint: disable=import-outside-toplevel
        race_canon = resoudre_race(str(fiche.get("race") or "")) or \
            str(fiche.get("race") or "")
        taille = (RACES.get(race_canon, {}) or {}).get("taille", "M")
        for_ = int((fiche.get("carac") or {}).get("FOR", 10) or 10)
        return int(charge_maximale(for_, taille))
    except Exception:                                                # noqa: BLE001
        # Repli : Force 10, taille Moyenne→ charge max ~90 kg (PHB 3.5).
        for_ = int((fiche.get("carac") or {}).get("FOR", 10) or 10)
        return int(round((for_ * 10 * 0.4536) * 1.0))


# --------------------------------------------------------------------------- #
#  Catalogue d'équipement (PHB 3.5)
# --------------------------------------------------------------------------- #
# Poids officiels D&D 3.5 (Manuel du Joueur, chapitre Équipement) en livres,
# convertis en kg (arrondis à 2 décimales). Clés normalisées (singulier, sans
# accent). `qte_pour_poids` = nombre d'unités pour le poids indiqué (les
# munitions sont vendues par lots : 20 flèches, 10 balles de fronde…).
_LIVRES = 0.4536


def _kg(lb: float, qte_pour_poids: int = 1) -> dict[str, Any]:
    """Construit l'entrée catalogue `{poids_kg, lot}` depuis un poids en livres."""
    return {"poids_kg": round(lb * _LIVRES, 2), "lot": qte_pour_poids}


_POIDS_OFFICIELS: dict[str, dict[str, Any]] = {
    # --- Munitions (vendues par lots, poids par lot officiel PHB) ----------
    # PHB 3.5 : flèches (10) = 1 lb (0,45 kg) — partie d9f65ed2 : l'entrée
    # « 3 lb pour 20 » gonflait le poids et le prix apparent du lot.
    "fleche":       _kg(1.0, qte_pour_poids=10),    # 10 flèches = 1 lb
    "carreau":      _kg(1.0, qte_pour_poids=10),    # 10 carreaux = 1 lb
    "balle de fronde": _kg(3.0, qte_pour_poids=10), # 10 balles = 3 lb
    "pierre de fronde": _kg(0.0, qte_pour_poids=1),
    "balle":        _kg(3.0, qte_pour_poids=10),
    # --- Armes de corps à corps -------------------------------------------
    "arc":          _kg(2.0),                        # arc court
    "arc court":    _kg(2.0),
    "arc long":     _kg(3.0),
    "grande epee":  _kg(8.0),                         # grande épée
    "espadon":      _kg(8.0),
    "grande hache": {"poids_kg": 6.0, "lot": 1},
    "hache a deux main": _kg(12.0),
    "epee courte":  _kg(2.0),
    "epee longue":  _kg(4.0),
    "rapiere":      _kg(2.0),
    "dague":        _kg(1.0),
    "baton":        _kg(4.0),                         # bâton (quarterstaff)
    "matraque":     _kg(3.0),
    "gourdin":      {"poids_kg": 1.5, "lot": 1},
    "faucille":     _kg(2.0),
    "masse":        _kg(8.0),                         # masse d'armes lourde
    "masse legere": _kg(4.0),
    "masse d armes": _kg(8.0),
    "masse d armes lourde": _kg(8.0),
    "masse d armes legere": _kg(4.0),
    "marteau de guerre": _kg(5.0),
    "hache d arme": _kg(6.0),
    "hache":        _kg(3.0),                         # hachette
    "hachette":     _kg(3.0),
    "fleau d arme": _kg(10.0),
    "hallebarde":   _kg(12.0),
    "glaive":       _kg(10.0),
    "guisarme":     _kg(12.0),
    "ransueur":     _kg(10.0),
    "faux":         _kg(10.0),
    "fau":          _kg(10.0),                        # faux (scythe) — _norm retire le s
    "trident":      _kg(4.0),
    "epee batarde": _kg(6.0),
    "etoile du matin": _kg(6.0),
    "lance longue": _kg(9.0),
    "flechette":    _kg(0.5),
    "sarbacane":    _kg(2.0),
    "arc court composite": _kg(2.0),
    "arc long composite": _kg(3.0),
    "javelot":      _kg(2.0),
    "javeline":     _kg(2.0),
    "lance":        _kg(6.0),                         # lance (spear)
    "lance courte": _kg(3.0),
    "lanterne a capuchon": _kg(2.0),                  # objet équipement
    "arbalete legere": _kg(4.0),
    "arbalete lourde": _kg(8.0),
    "fronde":       _kg(0.0),
    "cimeterre":    {"poids_kg": 2.0, "lot": 1},
    "flamberge":    _kg(8.0),
    "tronconneuse": _kg(8.0),
    # --- Armes complémentaires (catalogue de création — poids officiels) ----
    "gantelet": {"poids_kg": 0.5, "lot": 1},
    "dague coup de poing": {"poids_kg": 0.5, "lot": 1},
    "gantelet cloute": {"poids_kg": 0.5, "lot": 1},
    "epieu": {"poids_kg": 1.5, "lot": 1},
    "pique": {"poids_kg": 4.5, "lot": 1},
    "kukri": {"poids_kg": 1.0, "lot": 1},
    "marteau leger": {"poids_kg": 1.0, "lot": 1},
    "pic de guerre leger": {"poids_kg": 1.5, "lot": 1},
    "pic de guerre lourd": {"poids_kg": 3.0, "lot": 1},
    "massue": {"poids_kg": 4.0, "lot": 1},
    "cimeterre a deux main": {"poids_kg": 4.0, "lot": 1},
    "fleau d arme leger": {"poids_kg": 2.5, "lot": 1},
    "fleau d arme lourd": {"poids_kg": 5.0, "lot": 1},
    "fleau d armes leger": {"poids_kg": 2.5, "lot": 1},
    "fleau d armes lourd": {"poids_kg": 5.0, "lot": 1},
    "corseque": {"poids_kg": 6.0, "lot": 1},
    "coutille": {"poids_kg": 5.0, "lot": 1},
    "lance d arcon": {"poids_kg": 5.0, "lot": 1},
    "kama": {"poids_kg": 1.0, "lot": 1},
    "nunchaku": {"poids_kg": 1.0, "lot": 1},
    "sai": {"poids_kg": 0.5, "lot": 1},
    "siangham": {"poids_kg": 0.5, "lot": 1},
    "fouet": {"poids_kg": 1.0, "lot": 1},
    "hache de guerre naine": {"poids_kg": 4.0, "lot": 1},
    "chaine cloutee": {"poids_kg": 5.0, "lot": 1},
    "double lame": {"poids_kg": 5.0, "lot": 1},
    "fleau double": {"poids_kg": 5.0, "lot": 1},
    "hache double orque": {"poids_kg": 7.5, "lot": 1},
    "marteau piolet gnome": {"poids_kg": 3.0, "lot": 1},
    "urgrosh nain": {"poids_kg": 6.0, "lot": 1},
    "arbalete de poing": {"poids_kg": 1.0, "lot": 1},
    "arbalete legere a repetition": {"poids_kg": 3.0, "lot": 1},
    "arbalete lourde a repetition": {"poids_kg": 6.0, "lot": 1},
    "bola": {"poids_kg": 1.0, "lot": 1},
    "filet": {"poids_kg": 3.0, "lot": 1},
    "shuriken lot de 5": {"poids_kg": 0.25, "lot": 1},
    # --- Armures et boucliers (poids pour une taille Moyenne) -------------
    "armure rembourree": _kg(10.0),
    "armure de cuir": _kg(15.0),
    "cuir cloute":  _kg(20.0),
    "chemise de maille": _kg(25.0),
    "cuir epai":    _kg(25.0),
    "armure d ecaille": _kg(30.0),
    "cotte de maille": _kg(40.0),
    "plastron":     _kg(30.0),
    "harnois complet": _kg(50.0),
    "armure a lame": _kg(45.0),
    "armure a bande": _kg(35.0),
    "demi harnoi":  _kg(50.0),
    "targe":        _kg(5.0),
    "bouclier bois leger": _kg(5.0),
    "bouclier bois lourd": _kg(10.0),
    "bouclier acier leger": _kg(6.0),
    "bouclier acier lourd": _kg(15.0),
    "pavois":       _kg(45.0),
    "pavoi":        _kg(45.0),                        # pavois — _norm retire le s
    # --- Équipement d'aventurier -------------------------------------------
    "sac a dos":    _kg(2.0),
    "torche":       _kg(1.0),
    "outre":        _kg(4.0),
    "outre a eau":  _kg(4.0),
    "rations de voyage": _kg(1.0),
    "ration":       _kg(1.0),
    "ration journaliere": _kg(1.0),
    "corde":        _kg(10.0),                        # corde 15 m
    "corde de chanvre": _kg(10.0),
    "corde de soie": _kg(5.0),
    "pioche":       _kg(6.0),
    "pelle":        _kg(8.0),
    "barre a mine": _kg(5.0),
    "marteau":      _kg(2.0),
    "miroir":       _kg(0.5),
    "tente":        _kg(20.0),
    "echelle":      _kg(10.0),
    "seau":         _kg(2.0),
    "chaine":       _kg(20.0),
    "cadenas":      _kg(1.0),
    "crochets de voleur": _kg(2.0),
    "ustensiles d alchimiste": _kg(10.0),
    "tenue":        _kg(4.0),
    "tenue de voyage": _kg(5.0),
    "couverture":   _kg(5.0),
    "sac de couchage": _kg(5.0),
    "lit de camp":  _kg(5.0),
    "pierre a feu": _kg(0.0),
    "briquet":      _kg(0.0),
    "luth":         _kg(3.0),
    "instrument":   _kg(3.0),
    "grimoire":     _kg(3.0),
    "livre de sorts": _kg(3.0),
    "composantes":  _kg(2.0),
    "potion de soins legers": _kg(0.5),
    "potion":       _kg(0.5),
    # ⚠️ Variantes NORMALISÉES : `_norm` retire le « s » FINAL — les clés
    # avec « s » final ci-dessus ne matchaient donc JAMAIS (« potion de
    # soins légers » → « potion de soin leger ») : chaque ajout de potion
    # déclenchait le warning « poids inconnu » (partie 2ca691ec).
    "potion de soins leger": _kg(0.5),
    "potion de soins modere": _kg(0.5),
    "potion de soins serieux": _kg(0.5),
    "potion de soins critique": _kg(0.5),
    "fiole de soins leger": _kg(0.5),
    "potion de guerison": _kg(0.5),
    "fiole de guerison": _kg(0.5),
    "kit premiers secour": _kg(1.0),
    "trousse de soin": _kg(1.0),
    "cle d argent": _kg(0.1),
    "clef d argent": _kg(0.1),
    "bandage":      _kg(1.0),
    "bandages":     _kg(1.0),
    "trousse de soins": _kg(1.0),
    "kit premiers secours": _kg(1.0),
    "pics":         _kg(5.0),
    "piolet":       _kg(5.0),
    "couteau de survie": _kg(1.0),
    "lanterne":     _kg(2.0),
    "huile":        _kg(1.0),
    "cartes":       _kg(0.5),
    "messager":     _kg(0.0),
    "bourse":       _kg(0.5),
    "gibeciere":    _kg(0.5),
    "relique":      _kg(1.0),
    "statuette":    _kg(1.0),
    "amulette":     _kg(0.1),
    "clef":         _kg(0.1),
    "cle":          _kg(0.1),
    "pierre a aiguiser": _kg(1.0),
    "savon":        _kg(0.5),
    "craie":        _kg(0.1),
}

# Monnaie : 50 pièces de même type = 1 lb (PHB 3.5).
_MONNAIE_PC_POIDS = round(50.0 * _LIVRES, 2)  # poids de 50 pc


def _poids_unitaire(nom: str, explicite: Optional[float]) -> Optional[float]:
    """Poids (kg) d'une unité d'objet : poids explicite sinon catalogue.

    Pour une munition du catalogue, on divise le poids du lot par la taille de
    lot (ex. 20 flèches = 3 lb → 0,15 lb ≈ 0,07 kg chacune).
    """
    if explicite is not None:
        try:
            return max(0.0, float(explicite))
        except (TypeError, ValueError):
            return None
    info = _POIDS_OFFICIELS.get(_norm(nom)) or _POIDS_OFFICIELS.get(
        _norm(nom).replace("_", " "))
    if not info:
        # Lot numéroté (« Flèches (50) », « Carreaux (10) ») : la taille du
        # lot ne change pas le poids UNITAIRE — on retire le nombre final et
        # on retente (partie d9f65ed2 : « Flèches (10) » restait sans poids).
        n_sans_lot = re.sub(r"[\s_]*\d+$", "", _norm(nom)).strip("_")
        # pluriel grossier mot à mot (« balles de fronde » → « balle de
        # fronde ») : le pluriel n'est pas toujours le dernier mot.
        n_sans_lot = " ".join(
            w[:-3] + "au" if w.endswith(("aux", "eaux"))
            else w[:-1] if w.endswith("s") and len(w) > 3
            else w
            for w in n_sans_lot.split(" ")
        )
        if n_sans_lot:
            info = (
                _POIDS_OFFICIELS.get(n_sans_lot)
                # les clés du dictionnaire sont écrites avec espaces
                or _POIDS_OFFICIELS.get(n_sans_lot.replace("_", " "))
            )
    if info:
        lot = int(info.get("lot") or 1)
        return round(float(info["poids_kg"]) / lot, 4)
    # 🛡️ (fix 10/11, partie b1152b10) : l'article EXISTE dans le catalogue
    # PHB (« Âne ou mulet », kg 0.0 — une monture vous porte, elle n'est pas
    # portée) mais son poids n'était pas dans `_POIDS_OFFICIELS` → l'ajout
    # était refusé « inconnu du catalogue ». On consulte l'article lui-même.
    try:
        from .. import equipement_phb as _eq_pu
        _art_pu = _eq_pu.article(nom)
        if isinstance(_art_pu, dict):
            try:
                return max(0.0, float(_art_pu.get("poids_kg") or 0.0))
            except (TypeError, ValueError):
                return 0.0
    except Exception:                                        # noqa: BLE001
        pass
    # Objet inconnu du catalogue → poids inconnu (None) ; le MJ doit fournir
    # un poids explicite pour ne pas fausser l'encombrement.
    return None


def _taille_monnaie(quantite_pc: int) -> float:
    """Poids (kg) de `quantite_pc` pièces de cuivre (50 pc = 1 lb)."""
    return round(quantite_pc / 50.0 * _LIVRES, 3)


# --------------------------------------------------------------------------- #
#  Lecture / calcul d'encombrement
# --------------------------------------------------------------------------- #
def _reparer_entree(e: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Répare une entrée d'inventaire malformée écrite par le LLM.

    Forme observée en partie réelle (fiche Barouk, partie dc4dd5aa) :
    `{"clé_de_fer_rouillee": 1, "description": "..."}` — le nom de l'objet
    est utilisé comme CLÉ au lieu du champ `nom`. Sans réparation, l'entrée
    est silencieusement perdue à la lecture. Renvoie toujours une entrée
    canonique `{nom, qte, …}` ou None si rien de récupérable.
    """
    if not isinstance(e, dict):
        return None
    nom = str(e.get("nom") or "").strip()
    if not nom:
        # Nom-porte-clé : la première clé ni méta ni qte/poids porte l'objet.
        _META = {"nom", "qte", "quantite", "quantité", "poids", "description"}
        for k, v in e.items():
            if k in _META or not isinstance(v, (int, float, str)):
                continue
            if isinstance(v, str) and not re.fullmatch(r"\d+", v.strip()):
                continue
            nom = str(k).replace("_", " ").strip()
            if isinstance(v, str):
                v = int(v.strip())
            e = {"nom": nom, "qte": max(1, int(v)),
                 **{kk: vv for kk, vv in e.items() if kk in ("description", "poids")}}
            break
        if not nom:
            return None
    try:
        qte = max(1, int(e.get("qte", 1) or 1))
    except (TypeError, ValueError):
        qte = 1
    sortie: dict[str, Any] = {"nom": nom, "qte": qte}
    # Charges d'objets à usages limités (kit de premiers secours : 10) :
    # champ de jeu légitime, préservé au round-trip (sinon le compteur
    # repartait à 10 à CHAQUE lecture de l'inventaire).
    if e.get("charges") is not None:
        try:
            sortie["charges"] = int(e["charges"])
        except (TypeError, ValueError):
            pass
    if e.get("poids") is not None:
        try:
            sortie["poids"] = float(e["poids"])
        except (TypeError, ValueError):
            pass
    if e.get("description"):
        sortie["description"] = str(e["description"])
    # Portée de l'objet (permanent / quête d'une partie donnée) : tag de jeu
    # légitime, préservé au round-trip (sinon l'inventaire de quête perdait
    # sa partie à CHAQUE lecture et redevenait permanent).
    if str(e.get("portee") or "") == "quete":
        sortie["portee"] = "quete"
        sortie["partie"] = str(e.get("partie") or "")
    return sortie


def _inventaire(fiche: dict[str, Any]) -> list[dict[str, Any]]:
    """Inventaire structuré : le champ `inventaire` si présent, sinon on le
    dérive de `equipement` (même forme `{nom, qte}`). Chaîne → liste d'objets.
    Les entrées malformées (nom-porte-clé LLM) sont réparées, pas jetées."""
    inv = fiche.get("inventaire")
    if not isinstance(inv, list):
        equip = fiche.get("equipement") or []
        if isinstance(equip, str):
            equip = _parse_lignes_equip(equip)
        inv = list(equip)
    sortie: list[dict[str, Any]] = []
    for e in inv:
        repare = _reparer_entree(e) if isinstance(e, dict) else None
        if repare:
            sortie.append(repare)
    return sortie


def _parse_lignes_equip(texte: str) -> list[dict[str, Any]]:
    """Parse un texte "Épée x2, Corde" vers [{nom, qte}] (rétrocompat)."""
    objets: list[dict[str, Any]] = []
    for chunk in str(texte or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r"x(\d+)$", chunk, re.IGNORECASE)
        if m:
            objets.append({"nom": chunk[:m.start()].strip(), "qte": int(m.group(1))})
        else:
            objets.append({"nom": chunk, "qte": 1})
    return objets


def _recalculer_charge(fiche: dict[str, Any],
                       monnaie: bool = True) -> tuple[float, str, int]:
    """Calcule poids transporté (kg) + catégorie d'encombrement D&D 3.5.

    Retourne `(poids_kg, catégorie, charge_max_kg)`.
    Catégories (PHB 3.5) : Légère ≤ ⅓ max, Moyenne ≤ ⅔ max, Lourde ≤ max,
    Dépassée au-delà.
    """
    inv = _inventaire(fiche)
    poids = 0.0
    inconnus: list[str] = []
    for e in inv:
        nom = e.get("nom", "")
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_unitaire(nom, e.get("poids"))
        if pu is None:
            inconnus.append(nom)
            continue
        poids += pu * qte
    if monnaie:
        pc = int(fiche.get("or", 0) or 0)
        if pc:
            poids += _taille_monnaie(pc)

    max_kg = _charge_max(fiche)
    tiers = max_kg / 3.0
    # Catégories ASCII (sans accent), conformes au schéma de fiche.
    if poids <= tiers:
        cat = "Legere"
    elif poids <= 2 * tiers:
        cat = "Moyenne"
    elif poids <= max_kg:
        cat = "Lourde"
    else:
        cat = "Depassee"
    return round(poids, 2), cat, max_kg


# Étiquette d'affichage (accentuée) d'une catégorie canonique ASCII.
_LIBELLE_ENCUMBRANCE = {"Legere": "Légère", "Moyenne": "Moyenne",
                        "Lourde": "Lourde", "Depassee": "Dépassée"}


def _libelle_cat(cat: str) -> str:
    return _LIBELLE_ENCUMBRANCE.get(cat, cat)


def _syncroniser(ctx: ToolContext, nom: str, fiche: dict[str, Any],
                 poids: float, cat: str, max_kg: int) -> dict[str, Any]:
    """Sauvegarde la fiche et répercute poids/encombrement dans l'état PJ.

    Renvoie le `state_patch` à diffuser (signal `pj_updated` + path patches).
    Lève `ValueError` si la fiche ne respecte pas le schéma (échec de
    sauvegarde) — l'appelant le convertit en `ToolResult` d'erreur.
    """
    patch = {"pj_updated": nom}
    idx = None
    from .fiches import _save_fiche, _sync_pj  # pylint: disable=import-outside-toplevel
    _save_fiche(ctx, nom, fiche)
    champs = {
        "poids_transporte": float(poids),
        "etat_encumbrance": cat,
        "charge_max": int(max_kg),
    }
    try:
        idx = _sync_pj(ctx, nom, champs)
    except Exception:                                    # noqa: BLE001
        idx = None  # synchro PJ best-effort (partie absente…) — non bloquant
    if idx is not None:
        for k, v in champs.items():
            patch[f"pj.{idx}.{k}"] = v
    return patch


def _finaliser(ctx: ToolContext, nom: str,
               fiche: dict[str, Any]) -> tuple[Optional[float],
                                               Optional[str],
                                               Optional[int],
                                               Optional[dict],
                                               Optional[ToolResult]]:
    """Recalcule la charge, synchronise et rend explicite tout échec.

    Retourne `(poids, cat, max_kg, state_patch, err)`. Si la sauvegarde de
    fiche échoue (schéma D&D 3.5 non respecté → `ValueError`), `err` contient
    un `ToolResult` d'erreur à renvoyer tel quel à l'utilisateur (au lieu de
    masquer l'échec en succès silencieux).
    """
    try:
        poids, cat, max_kg = _recalculer_charge(fiche)
        patch = _syncroniser(ctx, nom, fiche, poids, cat, max_kg)
        return poids, cat, max_kg, patch, None
    except ValueError as e:                             # noqa: BLE001
        return None, None, None, None, ToolResult(
            text=f"❌ Échec de sauvegarde de la fiche de {nom} :\n{e}")


def _format_inventaire(fiche: dict[str, Any], poids: float, cat: str,
                       max_kg: int, partie_id: str = "") -> str:
    lignes = [f"🧺 **Inventaire & charge de {fiche.get('nom', '?')}**"]
    inv = _inventaire(fiche)
    if not inv:
        lignes.append("- _(vide)_")
    quest = [
        e for e in inv
        if _portee_de(e) == "quete"
        and (not partie_id or str(e.get("partie") or "") == partie_id)
    ]
    perm = [e for e in inv if _portee_de(e) == "permanent"]

    def _ligne(e: dict[str, Any]) -> str:
        nom = e.get("nom", "?")
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_unitaire(nom, e.get("poids"))
        if pu is None:
            return f"- {nom} ×{qte} — poids inconnu (fournir un poids)"
        return (
            f"- {nom} ×{qte} — {round(pu * qte, 2)} kg "
            f"({pu:.3f} kg / unité)"
        )

    if quest:
        lignes.append("📜 Inventaire de quête (cette partie) :")
        lignes.extend(_ligne(e) for e in quest)
        lignes.append("🎒 Équipement permanent (reste au PJ) :")
        lignes.extend(_ligne(e) for e in perm)
    else:
        lignes.extend(_ligne(e) for e in inv)
    pc = int(fiche.get("or", 0) or 0)
    if pc:
        lignes.append(f"- Or : {pc} pc — {_taille_monnaie(pc)} kg")
    pct = round(poids / max_kg * 100, 1) if max_kg else 0
    lignes.append(
        f"\n⚖️ **Charge transportée : {poids} kg / {max_kg} kg "
        f"({pct}%) — encombrement : {_libelle_cat(cat)}.**"
    )
    lignes.append(
        "_Règles (PHB 3.5) : Légère ≤ ⅓ de la charge max, Moyenne ≤ ⅔, "
        "Lourde ≤ max. Une charge Lourde limite les déplacements et donne un "
        "malus DEX/compétences ; Dépassée interdit d'avancer._"
    )
    return "\n".join(lignes)


def _charger_fiche(ctx: ToolContext, nom: str) -> tuple[Optional[dict], Optional[ToolResult]]:
    try:
        from .fiches import _load_fiche   # pylint: disable=import-outside-toplevel
        fiche = _load_fiche(ctx, nom)
    except Exception as e:                                        # noqa: BLE001
        return None, ToolResult(text=f"❌ Erreur fiche : {e}")
    if fiche is None:
        return None, ToolResult(
            text=f"❌ Aucune fiche trouvée pour '{nom}' — crée le personnage "
            "d'abord (fiche_perso_creer / fiche_perso_creer_rapide)."
        )
    return fiche, None


# --------------------------------------------------------------------------- #
#  Montures (bât / sacoche de selle) et objets au sol
# --------------------------------------------------------------------------- #
# 🐴 (fix 12/11) : capacités PHB 3.5 des montures en kg — (légère,
# moyenne, lourde). Le cavalier et son équipement porté s'ajoutent en
# voyage (le MJ arbitre) ; ici on borne le CARGO de selle.
_CAPACITE_MONTURES = {
    "ane ou mulet": (104, 209, 313),     # 230 / 460 / 690 lb
    "cheval leger": (68, 136, 204),      # 150 / 300 / 450 lb
    "cheval lourd": (91, 181, 272),      # 200 / 400 / 600 lb
    "destrier leger": (68, 136, 204),
    "destrier lourd": (91, 181, 272),
    "poney": (34, 68, 102),              # 75 / 150 / 225 lb
    "chien de selle": (45, 91, 136),     # 100 / 200 / 300 lb
    "chien de garde": (45, 91, 136),
}
_CAPACITE_MONTURE_DEFAUT = (104, 209, 313)


def _cle_lieu_sol(etat: dict[str, Any]) -> str:
    """Clé du lieu courant (objets jetés au sol dans l'état de partie)."""
    dj = etat.get("donjon") or {}
    if dj.get("id"):
        cour = list(dj.get("courant") or [0, 0])
        return f"donjon:{dj.get('id')}:{cour[0]},{cour[1]}"
    return "monde:" + str((etat.get("lieu") or {}).get("nom") or "inconnu")


def _charge_monture(monture: dict[str, Any]) -> float:
    """Poids (kg) du cargo de selle d'une monture."""
    total = 0.0
    for e in (monture.get("inventaire") or []):
        try:
            qte = int(e.get("qte", 1) or 1)
        except (TypeError, ValueError):
            qte = 1
        pu = _poids_unitaire(e.get("nom"), e.get("poids"))
        if pu is not None:
            total += pu * qte
    return round(total, 2)


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #
# 🏃 (fix 13/11) : vitesse de déplacement PHB 3.5 (m par round) — base par
# race, réduite par l'armure moyenne/lourde (30 ft → 20 ft) et par la
# charge moyenne/lourde (20 ft → 15 ft). Le nain ne perd rien à l'armure.
_VITESSES_RACES = {
    "humain": 9.0, "demi-elfe": 9.0, "demi-orc": 9.0, "elfe": 9.0,
    "halfelin": 6.0, "halfling": 6.0, "nain": 6.0, "gnome": 6.0,
}


def _vitesse_deplacement(fiche: dict[str, Any]) -> float:
    """Vitesse (m par round) d'un PJ : race − armure − charge (PHB 3.5)."""
    race = _norm(fiche.get("race"))
    v = 9.0
    for _r, _vit in _VITESSES_RACES.items():
        if _r in race:
            v = _vit
            break
    base_haute = abs(v - 9.0) < 0.01      # 30 ft (humain et assimilés)
    # armure portée moyenne/lourde (le nom canonique vient du catalogue)
    _armure_lourde = False
    try:
        from .. import catalogue as _cat_v
        _par_nom = {
            _norm(a.get("nom")): a for a in _cat_v.ARMURES
        }
        for e in (fiche.get("equipement") or []):
            _a = _par_nom.get(_norm(e.get("nom")))
            if isinstance(_a, dict) and str(_a.get("categorie") or "") in (
                    "Moyenne", "Lourde"):
                _armure_lourde = True
                break
    except Exception:                                        # noqa: BLE001
        pass
    _, _cat_enc, _ = _recalculer_charge(fiche)
    _charge_peni = _cat_enc in ("Moyenne", "Lourde", "Depassee")
    if base_haute and (_armure_lourde or _charge_peni):
        v = 6.0
    elif (not base_haute) and _cat_enc in ("Lourde", "Depassee"):
        v = 4.5
    return v


@tool
async def inventaire_consulter(ctx: ToolContext, nom: str) -> ToolResult:
    """
    Affiche l'inventaire et la charge transportée d'un personnage : liste des
    objets (avec quantité et poids), poids total (kg), capacité maximale et
    catégorie d'encombrement D&D 3.5 (Légère / Moyenne / Lourde / Dépassée).
    À utiliser pour vérifier les munitions (ex. flèches restantes) ou le
    fardeau d'un PJ avant/reprès ramassage.

    :param nom (str): nom du personnage ou du joueur qui l'incarne.
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    poids, cat, max_kg = _recalculer_charge(fiche)
    texte = _format_inventaire(fiche, poids, cat, max_kg,
                               partie_id=ctx.partie_id)
    # 🛠️ Nudge : les modèles n'utilisent JAMAIS spontanément les outils
    # d'écriture (observé en e2e : « je ramasse la clé » → 4× consulter,
    # inventaire jamais rempli). On rappelle leur existence et la syntaxe.
    # 🏃 (fix 13/11) : la vitesse de déplacement effective.
    try:
        _v_eff = _vitesse_deplacement(fiche)
        texte = (texte.rstrip() + "\n\n🏃 Vitesse de déplacement : "
                 f"{_v_eff} m par round"
                 + (" (réduite par l'armure ou la charge)" if _v_eff < 9.0
                    else "") + ".")
    except Exception:                                        # noqa: BLE001
        pass
    # 🐴 (fix 12/11) : le CARGO DE SELLE de chaque monture possédée.
    for _m_c in (fiche.get("montures") or []):
        _cargo_c = _charge_monture(_m_c)
        _cap_c = _CAPACITE_MONTURES.get(
            _norm(_m_c.get("nom")), _CAPACITE_MONTURE_DEFAUT)
        _niv_c = ("légère" if _cargo_c <= _cap_c[0]
                  else "moyenne" if _cargo_c <= _cap_c[1] else "lourde")
        texte += (
            f"\n\n🐴 **{_m_c.get('nom')}** — cargo de selle : "
            f"{_cargo_c} kg / {_cap_c[2]} kg max (charge {_niv_c}) :")
        for _e_c in (_m_c.get("inventaire") or []):
            texte += f"\n   - {_e_c.get('nom')} x{_e_c.get('qte', 1)}"
        if not (_m_c.get("inventaire") or []):
            texte += "\n   (bât vide)"
    texte += (
        "\n\n🛠️ POUR MODIFIER cet inventaire, appelle l'un de ces outils "
        "(une consultation ne suffit pas) : `inventaire_ramasser(nom, "
        "objet, quantite?, poids?, source?)` pour ramasser · "
        "`inventaire_ajouter(nom, objet, quantite?, poids?, portee?)` · "
        "`inventaire_retirer(nom, objet, quantite?, portee?)` · "
        "`inventaire_consommer_munition(nom, munition, quantite?)` · "
        "`inventaire_transferrer(nom, objet, quantite?, destination?)` "
        "pour ALLÉGER le dos : destination='monture' (bât/sacoche de "
        "selle), 'sol' (jeter au sol), le nom d'un autre PJ (don) ou "
        "d'un PNJ — et pour la monnaie objet='or' (lourde : 50 pc = "
        "0,45 kg). "
        "PORTÉE : don de PNJ / objet d'aventure (potion, fiole, or donné, "
        "carte, clé, parchemin) → portee=\"quete\" (perdus à la fin de la "
        "partie) ; achat en marchand, arme, armure, trésor → "
        "portee=\"permanent\" (reste au PJ)."
    )
    return ToolResult(
        text=texte,
        state_patch={"pj_updated": nom},
    )


# 🔧 Partie fc6f4d0e : les MONNAIES (« pièce d'or », « 25 pc », « or »…)
# vont au COMPTEUR D'OR de la fiche (unités internes = pc, 1 po = 10) au
# lieu d'encombrer l'inventaire d'items « pièce d'or » fictifs.
# Valeurs officielles 3.5 : 1 po = 10 pa = 100 pc ; 1 pp = 10 po.
# Unite interne : 0.1 po (= 1 piece d'argent) ; le cuivre = 0.1 unite.
_VALEUR_MONNAIE_PC = {
    "or": 10, "po": 10, "cuivre": 0.1, "pc": 0.1, "argent": 1, "pa": 1,
    "platine": 100, "pp": 100, "electrum": 5, "électrum": 5,
}


_RE_MONNAIE = re.compile(
    # Le nom ENTIER doit etre la monnaie (count/bourse optionnels).
    # Un mot ARGENT perdu dans un nom d objet (cle en argent) ne
    # declenche PAS le routage (partie fc6f4d0e).
    r"^\s*(?:\d{1,6}\s+|bourse\s+(?:de|d['' ])\s+)?"
    r"(?:pi[èe]ces?\s+(?:d['' ]|de\s+)\s*)?"
    r"(or|po|pc|pa|pp|cuivre|argent|platine|electrum|électrum)"
    r"(?:\s+(?:d['' ]|de\s+)\s*(or|po|pc|pa|pp|cuivre|argent|platine"
    r"|electrum|électrum))?\s*$",
    re.IGNORECASE,
)



def _recalc_ca_fiche(fiche: dict, ctx=None) -> None:
    """Recalcule la CA de la fiche depuis l equipement PORTE.

    Fix (campagne Crown of Mystra) : la CA etait calculee A LA CREATION
    (10 + DEX, sans armure) et JAMAIS recalculee quand l armure etait
    ajoutee via inventaire_ajouter - les PJ combattaient a CA 11 avec
    une cotte de mailles (+5) dans l inventaire.
    """
    try:
        from .. import persos as _persos_ca
        armures = [
            str(e.get("nom")) for e in (fiche.get("equipement") or [])
            if isinstance(e, dict) and e.get("nom")
        ]
        carac = fiche.get("carac") or {}
        try:
            dex = int(carac.get("DEX", 10) or 10)
        except (TypeError, ValueError):
            dex = 10
        dons = fiche.get("dons")
        fiche["ca"] = _persos_ca.calculer_ca_armure(
            (dex - 10) // 2, armures, dons=dons)
        # (fix) : la CA nouvelle est repercutee sur l ETAT de partie
        # (l entree pj est celle lue par les gardes ennemies).
        if ctx is not None and fiche.get("nom"):
            try:
                from .fiches import _maj_etat_pj as _mep
                _mep(ctx, str(fiche.get("nom")), {"ca": fiche["ca"]})
            except Exception:                                # noqa: BLE001
                pass
    except Exception:                                        # noqa: BLE001
        pass


def _router_monnaie(fiche: dict, objet: str, quantite: int):
    """Si l'objet est de la MONNAIE, crédite l'or de la fiche et renvoie la
    note ; sinon None (l'objet rejoint l'inventaire normalement)."""
    m = _RE_MONNAIE.search(str(objet or ""))
    if not m:
        return None
    devise = next((g for g in m.groups() if g), "or")
    devise_n = devise.lower()
    pc_unite = _VALEUR_MONNAIE_PC.get(devise_n, 10)
    # Montant : premier nombre de la chaîne, sinon la quantité demandée.
    m_nb = re.search(r"(\d{1,6})", str(objet or ""))
    nb = int(m_nb.group(1)) if m_nb else max(1, int(quantite or 1))
    pc_total = nb * pc_unite
    if pc_total <= 0:
        return None
    fiche["or"] = int(fiche.get("or", 0) or 0) + pc_total
    _normaliser_fiche(fiche)
    return (
        f"🪙 {nb} × {devise} crédité(s) à {fiche.get('nom', '?')} : "
        f"+{round(pc_total / 10, 1)} po "
        f"(or total : {round(fiche["or"] / 10, 1)} po). "
        "La monnaie va au compteur d'or, pas à l'inventaire."
    )


@tool
async def inventaire_ajouter(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
    portee: str = "auto",
) -> ToolResult:
    """
    Ajoute un objet à l'inventaire d'un personnage et recalcule la charge.

    Le poids par unité est pris dans le catalogue d'équipement D&D 3.5
    (PHB) si l'objet est connu (flèches, armes, sac à dos, corde…) ; pour un
    objet hors catalogue (trésor de donjon inventé), fournissez `poids`
    (kg/unité) pour ne pas fausser l'encombrement. Les quantités d'un même
    objet s'additionnent (ex. 20 + 10 flèches = 30).

    :param nom (str): nom du personnage (ou du joueur qui l'incarne).
    :param objet (str): nom de l'objet (ex. "flèche", "émeraude", "relique").
    :param quantite (int): nombre d'unités à ajouter (défaut 1).
    :param poids (float): poids en kg d'UNE unité (optionnel, sinon catalogue).
    :param portee (str): "quete" — don d'un PNJ ou objet de l'aventure
        (potion, fiole, or donné, carte, clé, parchemin, lettre) : lié à LA
        PARTIE en cours, retiré de la fiche quand une autre partie démarre ·
        "permanent" — achat chez un marchand, arme, armure, trésor : reste
        au PJ d'une partie à l'autre · "auto" (défaut) : arme/armure/trésor
        → permanent, le reste → quête.
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    if not objet or not _norm(objet):
        return ToolResult(text="❌ Donne un nom d'objet valide.")
    # 🛡️ (run shadows4) : garde ANTI-PHRASE — le 9B rangeait des
    # fragments de narration comme objets (« les étapes du pacte ou
    # l'emplacement », « les marchandises en vrac sont en », « la statue
    # de dracoliche de taille »… 12 déchets sur 14 objets). Un objet est
    # un NOM COURT : pas de verbe conjugué, pas de conjonction
    # composée, pas de phrase de 45+ caractères. L'ambiance passe par
    # `memoire_evenement` / `ajouter_evenement_histoire`.
    _o = str(objet or "").strip()
    _mots = _o.split()
    _verbe = re.search(
        r"\b(?:sont|est|était|étaient|seront|a\s+été|peut|doit|se\s+"
        r"trouve\w*)\b", _o, re.IGNORECASE)
    _compose = re.search(r"\b(?:et|ou)\b", _o, re.IGNORECASE) and (
        len(_mots) >= 5)
    if _verbe or _compose or len(_o) > 48:
        return ToolResult(text=(
            f"⚠️ Objet invalide : « {_o} » ressemble à un passage de "
            "narration (phrase composée ou trop longue), pas à un objet "
            "inventoriable. Range un OBJET au nom COURT (ex. « couteau "
            "rituel », « fiole ») ; le texte d'ambiance va dans "
            "`memoire_evenement` ou `ajouter_evenement_histoire`."))
    # 🧹 (fix 10/11, partie b1152b10) : ANTI-ARTICLE — « un parchemin »,
    # « la carte », « la sacoche du mulet » : l'article narratif part avant
    # le rangement. Sans lui, l'équipement du PJ collectait des déchets de
    # prose ('un parchemin', 'la carte') au lieu d'objets propres, et la
    # fusion (`_cle_objet`) marchait au hasard selon la forme narrée.
    # (Le nom n'est PAS re-casé ni remplacé au-delà du catalogue : un objet
    # légitime garde sa forme — seule l'article narratif disparaît.)
    _o = re.sub(
        r"^(?:un|une|le|la|les|l'|du|de\s+la|des)\s+", "", _o,
        flags=re.IGNORECASE).strip(" \t,.;:!")
    if _o:
        try:
            from .. import equipement_phb as _eq_art
            _art_canon = _eq_art.article(_o)
            if isinstance(_art_canon, dict) and _art_canon.get("nom"):
                _o = str(_art_canon["nom"])
        except Exception:                                    # noqa: BLE001
            pass
    objet = _o
    # 🔧 Partie ead406b2 : la VALEUR CANONIQUE du tresor de la salle
    # prime sur une bourse inventee par le modele (le canon de (2,0) disait
    # "32 po" : le modele a ajoute "bourse de 25 pc" inventee). Si l'objet
    # ajoute est une bourse/monnaie ET que le tresor canonique de la salle
    # courante porte un montant, on credite LE MONTANT CANONIQUE.
    if re.search(r"bourse|pi[èe]ce|monnaie|pi[èe]ces?\s+d[''’ ]?(or|po|pc|pa|pp)|\b(po|pc|pa|pp)\b",
                      str(objet or ""), re.IGNORECASE):
        try:
            # 🛡️ gruumsh4pj (P29) : `_charger_etat` était appelé SANS exister
            # (NameError avalé par le `except Exception: pass`) — le crédit
            # du trésor canonique était du code mort, tout l'or passait par
            # la route des montants narrés (destinataire et devise faux).
            from ..game.state import PartyState as _PS_canon  # noqa: E501 PLC0415
            _etat_canon = _PS_canon(
                data_dir=ctx.data_dir, partie_id=ctx.partie_id).load()
            _dj_canon = _etat_canon.get("donjon") or {}
            _cour_canon = list(_dj_canon.get("courant") or [0, 0])
            _salle_canon_t = next(
                (s for s in (_dj_canon.get("grille") or [])
                 if isinstance(s, dict)
                 and (s.get("x"), s.get("y")) == (_cour_canon[0], _cour_canon[1])),
                None,
            )
            _tresor_txt = str((_salle_canon_t or {}).get("tresor") or "")
            _m_po = re.search(r"(\d{1,5})\s*po\b", _tresor_txt,
                                   re.IGNORECASE)
            _m_pc = re.search(r"(\d{1,5})\s*pc\b", _tresor_txt,
                                   re.IGNORECASE)
            # 🛡️ gruumsh4pj (P29) : l'argent (pa) et le platine
            # (pp) du trésor canonique étaient ignorés — « 600 pa,
            # 100 po » ne créditait que 100 po (60 po perdus).
            _m_pa = re.search(r"(\d{1,5})\s*pa\b", _tresor_txt,
                                   re.IGNORECASE)
            _m_pp = re.search(r"(\d{1,5})\s*pp\b", _tresor_txt,
                                   re.IGNORECASE)
            if _m_po or _m_pc or _m_pa or _m_pp:
                _pc_canon = (int(_m_po.group(1)) * 10 if _m_po else 0) + (
                    int(_m_pc.group(1)) if _m_pc else 0)
                _pc_canon += int(_m_pa.group(1)) if _m_pa else 0
                _pc_canon += (int(_m_pp.group(1)) * 100 if _m_pp else 0)
                if _pc_canon > 0:
                    # 🛡️ gruumsh4pj (P29) : MARQUEUR D'ABORD, crédit ensuite.
                    # L'ancien ordre (crédit fiche → sync → marqueur) perdait
                    # le marqueur à la moindre exception intermédiaire : le
                    # 2e `inventaire_ramasser` du même tour re-créditait le
                    # même trésor (160 po créditées DEUX fois). Le marqueur
                    # persistant fait désormais foi dès l'écriture d'état.
                    _deja_pris_inv = bool(
                        (_salle_canon_t or {}).get("tresor_pris"))
                    if _deja_pris_inv:
                        return ToolResult(text=(
                            "🪙 Le trésor canonique de cette salle a DÉJÀ été "
                            "crédité — pas de double crédit. Narre la fouille "
                            "sans nouveau gain, ou invente AUTRE CHOSE "
                            "(indice, objet de quête)."
                        ))
                    for _s_mq in (_dj_canon.get("grille") or []):
                        if isinstance(_s_mq, dict) and (_s_mq.get("x"),
                                                  _s_mq.get("y")) == (
                                                      _cour_canon[0],
                                                      _cour_canon[1]):
                            _s_mq["tresor_pris"] = True
                            break
                    _err_canon = _PS_canon(
                        data_dir=ctx.data_dir,
                        partie_id=ctx.partie_id).save(_etat_canon)
                    if _err_canon:
                        return ToolResult(text=f"❌ {_err_canon}")
                    from .fiches import (  # noqa: PLC0415
                        _load_fiche as _lf_canon, _save_fiche as _sf_canon,
                        _sync_pj as _sp_canon)
                    fiche = _lf_canon(ctx, nom)
                    fiche["or"] = int(fiche.get("or", 0) or 0) + _pc_canon
                    _normaliser_fiche(fiche)
                    try:
                        _sf_canon(ctx, nom, fiche)
                    except ValueError as e:
                        return ToolResult(text=f"❌ {e}")
                    _idx_c = _sp_canon(ctx, nom, {"or": fiche.get("or", 0)})
                    return ToolResult(
                        text=(
                            f"🪙 **Trésor canonique de la salle crédité** : "
                            f"{_pc_canon // 10} po (or total : "
                            f"{fiche.get('or', 0) // 10} po) — la valeur du "
                            "module fait foi, pas un montant inventé."
                        ),
                        state_patch={"pj_updated": nom,
                                     f"pj.{_idx_c}.or": fiche.get("or", 0)},
                    )
        except Exception:                                    # noqa: BLE001
            pass
    # 🔧 Partie fc6f4d0e : la monnaie est créditée sur l'or, pas en items.
    _note_monnaie = _router_monnaie(fiche, objet, quantite)
    if _note_monnaie:
        from .fiches import _save_fiche as _sf_m, _sync_pj as _sp_m  # noqa: E501 pylint: disable=import-outside-toplevel
        from .fiches import _patch_pj as _pp_m  # pylint: disable=import-outside-toplevel
        try:
            _sf_m(ctx, nom, fiche)
        except ValueError as e:
            return ToolResult(text=f"❌ {e}")
        _idx_m = _sp_m(ctx, nom, {"or": fiche.get("or", 0)})
        return ToolResult(
            text=_note_monnaie,
            state_patch=_pp_m(nom, _idx_m, {"or": fiche.get("or", 0)}),
        )
    qte = max(1, int(quantite or 1))
    portee_n = _norm(portee)
    if portee_n not in ("quete", "permanent"):
        portee_n = _portee_auto(objet)
    # ❤️ Objets REQUIS au scénario (manifeste `.donjon.json`, étapes
    # structurées) : quel que soit leur nom (même une gemme, un joyau ou un
    # trésor) et QUELLE QUE SOIT la portée demandée par le MJ, ils vivent
    # dans l'inventaire de QUÊTE de LA PARTIE — la mécanique de déblocage
    # (objectifs.py) les y cherche. Partie e55cc855 : la baguette de
    # téléportation (objet REQUIS de l'étape 2) ajoutée portee="permanent"
    # — invisible de l'inventaire de quête, la progression restait bloquée.
    _requis_objets = _requis_scenario(ctx)
    # Partie d9f65ed2 : l'ajout « la Couronne » (clé « couronne ») ne
    # matchait AUCUN requis « La Couronne de Mystra » (clé « couronne de
    # mystra ») — l'anti-triche était contourné par le nom abrégé et
    # l'alias des objectifs marquait l'acte 1 « accompli ». La contrainte
    # s'applique aussi par RELATION DE PRÉFIXE (≥ 4 car.) avec un requis.
    _est_requis = _cle_objet(objet) in _requis_objets or any(
        _cles_prefixe(_cle_objet(objet), _r)
        or _cles_prefixe(_r, _cle_objet(objet))
        for _r in _requis_objets
    )
    if _est_requis:
        portee_n = "quete"
        # 🔒 Partie b59b4a9a : un objet REQUIS ne se gagne que dans la salle
        # de SON étape (`salle` du manifeste — la Couronne de Mystra chez
        # Nulentok (4,0), pas dans le puits (3,0)). Le modèle s'était ajouté
        # la Couronne en plein combat ailleurs : l'acte 1 passait « accompli
        # » et la zone-gemme se débloquait sans le boss. Refus explicite.
        try:
            from ..game.objectifs import salle_objet_requis  # noqa: E501 pylint: disable=import-outside-toplevel
            from ..game.state import PartyState              # noqa: E501 pylint: disable=import-outside-toplevel
            _etat_i = PartyState(
                data_dir=ctx.data_dir, partie_id=ctx.partie_id,
            ).load()
            _salles_gain = salle_objet_requis(
                _etat_i or {}, ctx.data_dir, objet)
            if _salles_gain:
                _courant = ((_etat_i.get("donjon") or {}).get("courant")
                            or [0, 0])
                try:
                    _ici = (int(_courant[0]), int(_courant[1]))
                except (TypeError, ValueError, IndexError):
                    _ici = None
                if _ici is not None and _ici not in _salles_gain:
                    _ou = " ou ".join(f"({x},{y})" for x, y in _salles_gain)
                    return ToolResult(text=(
                        f"🚫 **« {objet} » ne peut pas être gagné ICI** : "
                        "cet objet de quête se remporte dans la salle "
                        f"{_ou} du scénario (mécanique anti-triche : "
                        "l'objectif exige d'y parvenir réellement). "
                        "Narrez l'obtention seulement une fois la salle "
                        "atteinte et la rencontre résolue — sans appeler "
                        "l'outil d'inventaire avant."
                    ))
                # 🔒 Partie d9f65ed2 : même dans la BONNE salle, l'objet ne
                # se gagne qu'une fois la rencontre RÉSOLUE — pas « à
                # l'entrée », pas en plein combat (le boss vivant gardait
                # encore la Couronne que le modèle s'était déjà mise).
                if _etat_i.get("monstres_combat"):
                    return ToolResult(text=(
                        f"🚫 **« {objet} » ne peut pas être gagné "
                        "MAINTENANT** : un combat est EN COURS — la "
                        "rencontre doit être résolue (monstres vaincus via "
                        "`engager_combat` + attaques officielles) avant que "
                        "l'objet de quête ne soit gagné. Narre le combat "
                        "d'abord ; l'ajout se fera une fois la victoire "
                        "acquise."
                    ))
        except Exception:                                    # noqa: BLE001
            pass

    inv = _inventaire(fiche)
    # 🐴 (fix 10/11, partie b1152b10) : une MONTURE ne s'empile pas — le
    # mulet acheté (portée permanent) PUIS re-ajouté par le MJ (portée
    # quête) faisait « Âne ou mulet x2 » : la fusion classique exige la
    # MÊME portée, une monture non. Si une monture du même nom (clé
    # équivalente) est déjà possédée, l'ajout est refusé poliment.
    try:
        from .. import equipement_phb as _eq_mnt
        _art_mnt = _eq_mnt.article(objet)
        if isinstance(_art_mnt, dict) and \
                str(_art_mnt.get("categorie") or "").lower() == "monture":
            _cle_mnt = _cle_objet(objet)
            _deja_mnt = next(
                (e for e in inv if _cle_objet(e.get("nom")) == _cle_mnt),
                None)
            if _deja_mnt is not None and \
                    int(_deja_mnt.get("qte", 1) or 1) >= 1:
                return ToolResult(text=(
                    f"ℹ️ **{objet}** est déjà dans l'inventaire de "
                    f"{fiche.get('nom', nom)} — une monture ne s'empile pas "
                    "(elle vous suit, elle n'est pas portée sur le dos). "
                    "Narre son usage sans nouvel achat."))
    except Exception:                                        # noqa: BLE001
        pass
    cible = _cle_objet(objet)
    # 🔒 Partie 82a77cbe : un objet requis d'une étape À VENIR ne se gagne
    # pas — la baguette de téléportation (requis de l'étape 2) était remise
    # dès l'INTRO alors que le module ne la confie qu'au retour de la
    # Couronne. Les étapes antérieures non accomplies verrouillent l'objet.
    if portee_n == "quete" and _est_requis:
        try:
            from ..game.objectifs import etape_future_pour_objet  # noqa: E501 pylint: disable=import-outside-toplevel
            from ..game.state import PartyState                  # noqa: E501 pylint: disable=import-outside-toplevel
            _etat_f = PartyState(
                data_dir=ctx.data_dir, partie_id=ctx.partie_id,
            ).load()
            _cles_inv = {
                _cle_objet(str(_e.get("nom") or ""))
                for _e in inv
                if _entree_portee_ok(_e, "quete", ctx.partie_id)
            }
            _refus_futur = etape_future_pour_objet(
                _etat_f or {}, ctx.data_dir, objet, _cles_inv)
            if _refus_futur:
                return ToolResult(text=(
                    f"🚫 **« {objet} » ne peut pas être gagné MAINTENANT** : "
                    f"{_refus_futur}. (Le module la prévoit plus tard — ne "
                    "l'anticipe pas : narre la suite de l'étape courante.)"
                ))
        except Exception:                                    # noqa: BLE001
            pass
    portee_lbl = (
        "📜 inventaire de quête (cette partie)" if portee_n == "quete"
        else "🎒 équipement permanent"
    )
    # Déduplication (portée QUÊTE uniquement) : un objet de l'aventure DÉJÀ
    # enregistré pour CETTE partie avec la MÊME quantité est une
    # RE-PERSISTATION (ré-jeu inventaire, boucle du LLM — partie 5b4e2bbe :
    # carte ×2 alors qu'une seule carte existe dans le module). Les objets
    # uniques du scénario ne se cumulent pas. Les objets permanents restent
    # cumulables : butin légitime (5 gobelins = 5 épées courbes) et survie
    # inter-parties (hache gagnée dans chaque partie, test 4d4b4557).
    # AVANT la validation du poids : refuser une ré-acquisition ne requiert
    # pas de connaître le poids de l'objet.
    if portee_n == "quete":
        for e in inv:
            if (
                _cle_objet(e.get("nom")) == cible
                and _entree_portee_ok(e, portee_n, ctx.partie_id)
                and int(e.get("qte", 1) or 1) == qte
            ):
                return ToolResult(
                    text=(
                        f"ℹ️ **{qte} × {objet}** est DÉJÀ enregistré dans "
                        f"l'inventaire de {fiche.get('nom', nom)} "
                        f"({portee_lbl}) — objet unique de l'aventure, rien "
                        "à ajouter (déduplication d'une ré-acquisition déjà "
                        "persistée)."
                    )
                )
        # Partie b59b4a9a : le modèle ajoute le MÊME objet de quête sous un
        # libellé ABRÉGÉ (« la fiole », « un parchemin ») alors que la forme
        # longue existe déjà (« la fiole de vérité », « le parchemin de la
        # route ») — clés différentes, l'inventaire se polluait de doublons
        # approximatifs. Une entrée de quête de CETTE partie dont la clé est
        # un PRÉFIXE de la clé demandée (ou l'inverse) désigne le même objet
        # : on fusionne vers le libellé LE PLUS PRÉCIS (le plus long).
        _pre = next(
            (
                e for e in inv
                if _entree_portee_ok(e, portee_n, ctx.partie_id)
                and _cles_prefixe(_cle_objet(e.get("nom")), cible)
            ),
            None,
        )
        if _pre is not None:
            _cle_ex = str(_cle_objet(_pre.get("nom")))
            _long = _pre.get("nom") if len(_cle_ex) >= len(cible) else objet
            _pre["nom"] = _long
            _pre["qte"] = int(_pre.get("qte", 1) or 1)
            return ToolResult(
                text=(
                    f"ℹ️ **{objet}** désigne déjà « {_pre.get('nom')} » dans "
                    f"l'inventaire de {fiche.get('nom', nom)} ({portee_lbl}) "
                    "— même objet de quête sous un libellé abrégé, fusion "
                    "effectuée (pas de doublon)."
                )
            )
    pu = _poids_unitaire(objet, poids)
    if pu is None:
        # 🛡️ Défaut LÉGER pour les objets de quête (parties 7177d819,
        # ae358455, e55cc855) : la fiole de vérité, le parchemin de la route
        # et la baguette de téléportation sont HORS catalogue PHB — le tool
        # REFUSAIT l'ajout sans `poids` (ok=True, message d'avertissement,
        # mais RIEN n'était ajouté) et l'objet narré « remis » n'atteignait
        # JAMAIS l'inventaire, 3 parties d'affilée. Un objet de quête
        # (fiole, parchemin, gemme, clé, baguette…) est toujours léger :
        # 100 g par défaut — l'encombrement reste juste à l'approximation
        # près, et l'ajout n'est JAMAIS bloqué.
        if portee_n == "quete" or _cle_objet(objet) in _requis_objets:
            pu = 0.1
        else:
            return ToolResult(
                text=(
                    f"⚠️ Objet « {objet} » inconnu du catalogue d'équipement "
                    "(PHB 3.5) : fournis `poids` (kg par unité) pour que la "
                    "charge soit correctement comptée — sinon "
                    "l'encombrement sera faux."
                )
            )
    fusionne = False
    for e in inv:
        if (
            _cle_objet(e.get("nom")) == cible
            and _entree_portee_ok(e, portee_n, ctx.partie_id)
        ):
            neuf = int(e.get("qte", 1) or 1) + qte
            e["qte"] = neuf
            if e.get("poids") is None:
                e["poids"] = pu
            fusionne = True
            break
    if not fusionne:
        e = {"nom": objet, "qte": qte, "poids": pu}
        if portee_n == "quete":
            e["portee"] = "quete"
            e["partie"] = ctx.partie_id
        inv.append(e)

    fiche["inventaire"] = inv
    # Aligne le champ d'affichage legacy `equipement` sur l'inventaire.
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    _recalc_ca_fiche(fiche, ctx)
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"✅ **{qte} × {objet}** ajouté(s) à l'inventaire de "
            f"{fiche.get('nom', nom)} ({round(pu * qte, 2)} kg) — "
            f"portée : {portee_lbl}.\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg,
                                 partie_id=ctx.partie_id)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_retirer(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
    portee: str = "auto",
) -> ToolResult:
    """
    Retire un objet de l'inventaire d'un personnage (ou en réduit la quantité)
    et recalcule la charge transportée.

    :param nom (str): nom du personnage (ou du joueur qui l'incarne).
    :param objet (str): nom de l'objet à retirer.
    :param quantite (int): nombre d'unités à retirer (défaut 1).
    :param poids (float): poids en kg par unité (optionnel, sinon catalogue).
    :param portee (str): "quete" (inventaire de quête de cette partie),
        "permanent" (équipement durable du PJ) ou "auto" (défaut : consomme
        l'inventaire de quête de la partie D'ABORD, sinon le permanent).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    qte = max(1, int(quantite or 1))
    cible = _cle_objet(objet)
    portee_n = _norm(portee)
    if portee_n not in ("quete", "permanent"):
        portee_n = "auto"
    inv = _inventaire(fiche)
    candidates = [
        e for e in inv
        if _cle_objet(e.get("nom")) == cible
        and _entree_portee_ok(e, portee_n, ctx.partie_id)
    ]
    if portee_n == "auto":
        # Consomme l'inventaire de quête de la partie d'abord : les dons de
        # PNJ sont faits pour être utilisés pendant la partie (et disparaître
        # avec elle), avant de toucher au matériel durable du PJ.
        candidates.sort(
            key=lambda e: 0 if _portee_de(e) == "quete" else 1
        )
    trouve = candidates[0] if candidates else None
    if trouve is None:
        ou = ("inventaire de quête de cette partie ni dans l'équipement "
              "permanent" if portee_n == "auto" else
              ("inventaire de quête de cette partie" if portee_n == "quete"
               else "équipement permanent"))
        return ToolResult(
            text=f"❌ « {objet} » n'est ni dans l'{ou} de {nom}."
        )
    reste = int(trouve.get("qte", 1) or 1) - qte
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)

    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    _recalc_ca_fiche(fiche, ctx)
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"🗑️ **{qte} × {objet}** retiré(s) de l'inventaire de "
            f"{fiche.get('nom', nom)}.\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg,
                                 partie_id=ctx.partie_id)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_consommer_munition(
    ctx: ToolContext,
    nom: str,
    munition: str,
    quantite: int = 1,
) -> ToolResult:
    """
    Consomme de l'ammunition (flèche, carreau, balle de fronde…) après un tir
    à distance : réduit la quantité en inventaire et met à jour la charge.
    Idéal après `lancer_attaque`/`lancer_degats` sur un adversaire à l'arc ou
    à la fronde.

    :param nom (str): nom du personnage.
    :param munition (str): "flèche" | "carreau" | "balle de fronde" | ….
    :param quantite (int): nombre de munitions tirées (défaut 1).
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    if not _poids_unitaire(munition, None):
        return ToolResult(
            text=f"⚠️ « {munition} » n'est pas une munition connue du catalogue."
        )
    qte = max(1, int(quantite or 1))
    cible = _norm(munition)
    inv = _inventaire(fiche)
    trouve = None
    for e in inv:
        if _norm(e.get("nom")) == cible:
            trouve = e
            break
    if trouve is None:
        return ToolResult(
            text=(
                f"❌ {nom} n'a plus de {munition} en inventaire — il ne peut "
                "pas tirer à distance ! (recharge via inventaire_ajouter)"
            )
        )
    dispo = int(trouve.get("qte", 0) or 0)
    if dispo < qte:
        return ToolResult(
            text=(
                f"❌ {nom} n'a que {dispo} {munition} — impossible d'en "
                f"dépenser {qte}."
            )
        )
    reste = dispo - qte
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)

    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    _recalc_ca_fiche(fiche, ctx)
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err
    return ToolResult(
        text=(
            f"🏹 **{qte} {munition}(s)** consommée(s) par {fiche.get('nom', nom)} "
            f"— restantes : {trouve["qte"] if reste > 0 else 0}.\n\n"
            + _format_inventaire(fiche, poids, cat, max_kg)
        ),
        state_patch=patch,
    )


@tool
async def inventaire_ramasser(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    poids: Optional[float] = None,
    source: str = "",
    portee: str = "auto",
) -> ToolResult:
    """
    Ramasse un objet trouvé dans le donjon (trésor, butin, relique, monnaie…)
    et l'ajoute à l'inventaire en recalculant la charge. À utiliser quand le
    MJ octroie un objet trouvé pendant l'exploration ou après un combat.

    :param nom (str): nom du personnage qui ramasse.
    :param objet (str): nom de l'objet (ex. "émeraude", "bourse de 25 pc",
        "clé", "potion de soins légers").
    :param quantite (int): nombre d'unités (défaut 1).
    :param poids (float): poids en kg par unité si objet hors catalogue.
    :param source (str): d'où vient l'objet (ex. "salle du trésor", "gobelin 1").
    :param portee (str): "quete" | "permanent" | "auto" (cf.
        `inventaire_ajouter` ; butin de donjon = trésor → permanent).
    """
    # 🛍️ (fix 12/11) : un objet JETÉ AU SOL dans CE lieu est ramassable
    # en priorité (le transfert au sol l'a déposé ici).
    try:
        from ..game.state import PartyState as _PS_rp
        _et_rp = _PS_rp(data_dir=ctx.data_dir,
                        partie_id=ctx.partie_id).load()
        _cle_rp = _cle_lieu_sol(_et_rp)
        _sol_rp = (_et_rp.get("objets_au_sol") or {}).get(_cle_rp) or []
        _frag_rp = _cle_objet(objet)
        _idx_rp = next(
            (k for k, _e in enumerate(_sol_rp)
             if (_nc := _cle_objet(_e.get("nom")))
             and (_nc == _frag_rp or _frag_rp in _nc or _nc in _frag_rp)),
            None)
        if _idx_rp is not None:
            _e_rp = _sol_rp[_idx_rp]
            _qte_rp = min(int(_e_rp.get("qte", 1) or 1),
                          max(1, int(quantite or 1)))
            _reste_rp = int(_e_rp.get("qte", 1) or 1) - _qte_rp
            if _reste_rp > 0:
                _e_rp["qte"] = _reste_rp
            else:
                _sol_rp.pop(_idx_rp)
            if not _sol_rp:
                (_et_rp.get("objets_au_sol") or {}).pop(_cle_rp, None)
            _PS_rp(data_dir=ctx.data_dir,
                   partie_id=ctx.partie_id).save(_et_rp)
            _portee_rp = str(_e_rp.get("portee") or "quete")
            _r = await inventaire_ajouter(
                ctx, nom, str(_e_rp.get("nom")), quantite=_qte_rp,
                poids=_e_rp.get("poids"),
                portee=_portee_rp if _portee_rp in ("quete", "permanent")
                else "auto")
            if not _r.text.startswith(("❌", "⚠️")):
                _r = ToolResult(
                    text="🛍️ (ramassé du sol) " + _r.text,
                    events=_r.events, state_patch=_r.state_patch)
            return _r
    except Exception:                                        # noqa: BLE001
        pass
    return await inventaire_ajouter(
        ctx, nom, objet, quantite=quantite, poids=poids, portee=portee
    )


def _assurer_monture(fiche: dict[str, Any]) -> Optional[dict[str, Any]]:
    """La première monture possédée (champ `montures`), enregistrée
    automatiquement depuis l'inventaire (article catalogue catégorie
    « monture ») si absent. None = aucune monture possédée."""
    montures = fiche.setdefault("montures", [])
    if montures:
        return montures[0]
    try:
        from .. import equipement_phb as _eq_am
        for e in _inventaire(fiche):
            _a_am = _eq_am.article(e.get("nom"))
            if (isinstance(_a_am, dict)
                    and str(_a_am.get("categorie") or "").lower()
                    == "monture"):
                montures.append({"nom": str(_a_am["nom"]), "inventaire": []})
                return montures[0]
    except Exception:                                        # noqa: BLE001
        pass
    return None


@tool
async def inventaire_transferrer(
    ctx: ToolContext,
    nom: str,
    objet: str,
    quantite: int = 1,
    destination: str = "",
) -> ToolResult:
    """
    Déplace des objets HORS du dos du personnage : vers la MONTURE (bât /
    sacoche de selle — la charge du PJ s'allège RÉELLEMENT), vers un AUTRE
    PJ de la table (don), vers un PNJ, ou AU SOL (jeté — ramassable plus
    tard dans le même lieu avec `inventaire_ramasser`).

    :param nom (str): nom du personnage qui donne / dépose.
    :param objet (str): nom de l'objet (ex. "Tente", "Ration de voyage").
        Pour de la MONNAIE : objet="or" (ou "po"/"pc") et `quantite` =
        le MONTANT EN PO (1 po = 10 pc) — les pièces sont lourdes
        (50 pc = 0,45 kg), le bât de selle est fait pour elles.
    :param quantite (int): nombre d'unités (défaut 1) ; pour la monnaie,
        le montant.
    :param destination (str): "monture" — la monture possédée (enregistrée
        automatiquement depuis l'inventaire si besoin) · "sol" — jeté au
        sol du lieu courant · le NOM d'un autre PJ de la table (don) · le
        NOM d'un PNJ (l'objet quitte la table, ex. "Cassyt").

    Règles 3.5 : tendre/donner un objet = action libre (le destinataire
    doit avoir une main libre — en combat, le MJ arbitre) ; jeter un objet
    au sol = action libre ; ramasser = action de mouvement (attaque
    d'opportunité). La monture a sa propre capacité PHB (un mulet porte
    ~104 kg en charge légère) : au-delà de sa charge lourde, refusé.
    """
    fiche, err = _charger_fiche(ctx, nom)
    if err:
        return err
    if not objet or not _norm(objet):
        return ToolResult(text="❌ Donne un nom d'objet valide.")
    dest = str(destination or "").strip()
    if not dest:
        return ToolResult(text=(
            '❌ Donne une DESTINATION : "monture" (bât/sacoche de selle), '
            '"sol" (jeter au sol), le nom d\'un AUTRE PJ de la table '
            "(don), ou le nom d'un PNJ."))
    try:
        qte = max(1, int(quantite or 1))
    except (TypeError, ValueError):
        qte = 1
    if _norm(dest) == _norm(str(fiche.get("nom") or nom)):
        return ToolResult(
            text="❌ Le destinataire est le personnage lui-même — rien à "
                 "transférer.")

    _dest_monture = _norm(dest) in ("monture", "bat", "mulet", "cheval")
    _dest_sol = _norm(dest) == "sol"

    # ── Monnaie : objet="or"/"po"/"pc" + quantite (en po ; 1 po = 10 pc) ──
    _m_unite = re.search(r"\b(pc|po|or)\b", objet, re.IGNORECASE)
    if _m_unite:
        unite = _m_unite.group(1).lower()
        pc_dep = int(qte) * (1 if unite == "pc" else 10)
        or_actuel = int(fiche.get("or", 0) or 0)
        if pc_dep <= 0 or pc_dep > or_actuel:
            return ToolResult(text=(
                f"❌ {fiche.get('nom', nom)} n'a que {or_actuel} pc — "
                f"impossible de transférer {pc_dep} pc."))
        fiche["or"] = or_actuel - pc_dep
        fiche = _normaliser_fiche(fiche)
        poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
        if err:
            return err
        _msg_pc = (f"🪙 **{pc_dep} pc** quittent la bourse de "
                   f"{fiche.get('nom', nom)} (reste : {fiche['or']} pc).")
        if _dest_monture:
            _m0 = _assurer_monture(fiche)
            if _m0 is None:
                return ToolResult(text=(
                    "❌ Aucune monture possédée — achète-en d'abord au "
                    "marché (`marche_acheter`)."))
            _m0.setdefault("inventaire", []).append({
                "nom": "Bourse de pièces", "qte": 1, "poids": 0.0,
                "pc": pc_dep})
            from .fiches import _save_fiche as _sf_mnt  # noqa: PLC0415
            _sf_mnt(ctx, nom, fiche)
            return ToolResult(text=(
                _msg_pc + " Elles sont rangées dans le bât de **"
                + str(_m0.get("nom")) + "** (hors du dos du cavalier)."),
                state_patch=patch)
        _cible_pc = None
        try:
            from ..game.state import PartyState as _PS_pc
            _etat_pc = _PS_pc(data_dir=ctx.data_dir,
                              partie_id=ctx.partie_id).load()
            for p in (_etat_pc.get("pj") or []):
                if (isinstance(p, dict) and _norm(str(p.get("nom") or ""))
                        == _norm(dest)):
                    _cible_pc = str(p.get("nom"))
                    break
        except Exception:                                # noqa: BLE001
            _cible_pc = None
        if _cible_pc is not None:
            _f_dest, _err_pc = _charger_fiche(ctx, _cible_pc)
            if _err_pc:
                return _err_pc
            _f_dest["or"] = int(_f_dest.get("or", 0) or 0) + pc_dep
            _f_dest = _normaliser_fiche(_f_dest)
            _finaliser(ctx, _cible_pc, _f_dest)
            return ToolResult(text=(
                _msg_pc + f" **{_cible_pc}** les reçoit (total : "
                f"{_f_dest['or']} pc)."), state_patch=patch)
        return ToolResult(text=(
            _msg_pc + f" Données à **{dest}** (PNJ — l'argent quitte la "
            "table)."), state_patch=patch)

    # ── Objet classique ──
    inv = _inventaire(fiche)
    frag = _norm(objet)
    trouve = next(
        (e for e in inv
         if (ne := _norm(e.get("nom")))
         and (ne == frag or frag in ne or ne in frag)),
        None)
    if trouve is None:
        return ToolResult(text=(
            f"❌ « {objet} » n'est pas dans l'inventaire de "
            f"{fiche.get('nom', nom)} — `inventaire_consulter` liste ce "
            "qui est réellement porté."))
    qte_dep = min(qte, int(trouve.get("qte", 1) or 1))
    nom_obj = str(trouve.get("nom"))
    pu = _poids_unitaire(nom_obj, trouve.get("poids"))
    if pu is None:
        pu = 0.1

    _cible: Optional[str] = None
    # ── Destination : la MONTURE ──
    if _dest_monture:
        _m0 = _assurer_monture(fiche)
        if _m0 is None:
            return ToolResult(text=(
                "❌ Aucune monture possédée — achète-en d'abord au marché "
                "(`marche_acheter`), puis re-tente le transfert."))
        cap = _CAPACITE_MONTURES.get(
            _norm(_m0.get("nom")), _CAPACITE_MONTURE_DEFAUT)
        cargo = _charge_monture(_m0)
        ajout = pu * qte_dep
        if cargo + ajout > cap[2]:
            return ToolResult(text=(
                f"❌ **{_m0.get('nom')}** porterait "
                f"{round(cargo + ajout, 1)} kg (max {cap[2]} kg en charge "
                "lourde) — la bête s'effondrerait. Allège le bât "
                "(`inventaire_transferrer` depuis la monture) ou laisse "
                "au sol."))
        _e_cargo = next(
            (c for c in (_m0.get("inventaire") or [])
             if _cle_objet(c.get("nom")) == _cle_objet(nom_obj)), None)
        if _e_cargo is not None:
            _e_cargo["qte"] = int(_e_cargo.get("qte", 1) or 1) + qte_dep
        else:
            _m0.setdefault("inventaire", []).append(
                {"nom": nom_obj, "qte": qte_dep, "poids": pu})
    else:
        # ── Destination : un AUTRE PJ de la table ──
        try:
            from ..game.state import PartyState as _PS_tr
            _etat_tr = _PS_tr(data_dir=ctx.data_dir,
                              partie_id=ctx.partie_id).load()
            for p in (_etat_tr.get("pj") or []):
                if (isinstance(p, dict) and _norm(str(p.get("nom") or ""))
                        == _norm(dest)
                        and _norm(str(p.get("nom") or ""))
                        != _norm(str(fiche.get("nom") or nom))):
                    _cible = str(p.get("nom"))
                    break
        except Exception:                                # noqa: BLE001
            _cible = None
        if _cible is not None:
            _f_dest, _err_d = _charger_fiche(ctx, _cible)
            if _err_d:
                return _err_d
            _inv_d = _inventaire(_f_dest)
            _e_d = next(
                (c for c in _inv_d
                 if _cle_objet(c.get("nom")) == _cle_objet(nom_obj)), None)
            if _e_d is not None:
                _e_d["qte"] = int(_e_d.get("qte", 1) or 1) + qte_dep
                if _e_d.get("poids") is None:
                    _e_d["poids"] = pu
            else:
                _n_d = {"nom": nom_obj, "qte": qte_dep, "poids": pu}
                if _portee_de(trouve) == "quete":
                    _n_d["portee"] = "quete"
                    _n_d["partie"] = ctx.partie_id
                _inv_d.append(_n_d)
            _f_dest["inventaire"] = _inv_d
            _f_dest["equipement"] = [
                {"nom": i["nom"], "qte": i["qte"]} for i in _inv_d
                if i.get("nom")]
            _recalc_ca_fiche(_f_dest, ctx)
            _f_dest = _normaliser_fiche(_f_dest)
            _finaliser(ctx, _cible, _f_dest)
        elif _dest_sol:
            # ── Destination : le SOL du lieu courant ──
            try:
                from ..game.state import PartyState as _PS_sol
                _et_sol = _PS_sol(data_dir=ctx.data_dir,
                                  partie_id=ctx.partie_id).load()
                _cle_sol = _cle_lieu_sol(_et_sol)
                _sol_liste = (_et_sol.setdefault("objets_au_sol", {})
                              .setdefault(_cle_sol, []))
                _e_sol = next(
                    (c for c in _sol_liste
                     if _cle_objet(c.get("nom")) == _cle_objet(nom_obj)),
                    None)
                if _e_sol is not None:
                    _e_sol["qte"] = (int(_e_sol.get("qte", 1) or 1)
                                     + qte_dep)
                else:
                    _n_sol = {"nom": nom_obj, "qte": qte_dep, "poids": pu}
                    if _portee_de(trouve) == "quete":
                        _n_sol["portee"] = "quete"
                        _n_sol["partie"] = ctx.partie_id
                    _sol_liste.append(_n_sol)
                _PS_sol(data_dir=ctx.data_dir,
                        partie_id=ctx.partie_id).save(_et_sol)
            except Exception as e_sol:                   # noqa: BLE001
                return ToolResult(
                    text=f"❌ Dépôt au sol impossible : {e_sol}")
        # (sinon : PNJ — l'objet quitte simplement la table)

    # ── Décrémente le donneur (commun à toutes les destinations) ──
    reste = int(trouve.get("qte", 1) or 1) - qte_dep
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)
    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    _recalc_ca_fiche(fiche, ctx)
    fiche = _normaliser_fiche(fiche)
    poids, cat, max_kg, patch, err = _finaliser(ctx, nom, fiche)
    if err:
        return err

    _charge_txt = (
        "\n\n📦 Charge de " + str(fiche.get("nom", nom)) + " : "
        f"{poids} kg / {max_kg} kg ({_LIBELLE_ENCUMBRANCE.get(cat, cat)}).")
    if _dest_monture:
        _m0 = (fiche.get("montures") or [{}])[0]
        _cargo = _charge_monture(_m0)
        _cap = _CAPACITE_MONTURES.get(
            _norm(_m0.get("nom")), _CAPACITE_MONTURE_DEFAUT)
        _niveau = ("légère" if _cargo <= _cap[0]
                   else "moyenne" if _cargo <= _cap[1] else "lourde")
        _dest_txt = (
            f"🐴 **{qte_dep} × {nom_obj}** sur le bât de "
            f"**{_m0.get('nom')}** (cargo : {_cargo} kg / {_cap[2]} kg "
            f"max — charge {_niveau}).")
    elif _cible is not None:
        _dest_txt = (f"🤝 **{qte_dep} × {nom_obj}** donné(s) à "
                     f"**{_cible}**.")
    elif _dest_sol:
        _dest_txt = (f"🛍️ **{qte_dep} × {nom_obj}** jeté(s) au sol — "
                     "ramassable ici plus tard avec `inventaire_ramasser`.")
    else:
        _dest_txt = (f"🤝 **{qte_dep} × {nom_obj}** donné(s) à "
                     f"**{dest}** (PNJ).")
    return ToolResult(
        text=_dest_txt + _charge_txt,
        state_patch=patch,
    )


@tool
async def identifier_objet(
    ctx: ToolContext,
    nom: str,
    objet: str,
    methode: str = "sort",
    lanceur: str = "",
) -> ToolResult:
    """
    IDENTIFIE un objet magique (D&D 3.5) — AVANT l'identification, le MJ
    ne révèle que l'apparence ; APRÈS, il révèle les propriétés
    canoniques dans sa narration (l'objet est marqué identifié sur la
    fiche).

    :param nom (str): le personnage qui porte/étudie l'objet.
    :param objet (str): le nom de l'objet dans l'inventaire.
    :param methode (str): "sort" — le sort Identification (niv. 1
        clerc/magicien : un emplacement est consommé chez `lanceur`,
        composante perle 100 po payée sur son or si disponible) · "essai"
        — 1 heure d'étude + jet d'Art de la magie contre une difficulté
        de base quinze, majorée du niveau estimé du créateur : plus lent,
        peut échouer (retenter la nuit suivante).
    :param lanceur (str): QUI lance le sort / fait l'essai (le PJ
        lanceur ; défaut = `nom`).
    """
    from .fiches import _load_fiche, _save_fiche  # noqa: PLC0415

    fiche = _load_fiche(ctx, nom)
    if not fiche:
        return ToolResult(text=f"❌ Aucune fiche trouvée pour '{nom}'.")
    inv = _inventaire(fiche)
    frag = _norm(objet)
    trouve = next(
        (e for e in inv
         if (ne := _norm(e.get("nom")))
         and (ne == frag or frag in ne or ne in frag)),
        None)
    if trouve is None:
        return ToolResult(text=(
            f"❌ « {objet} » n'est pas dans l'inventaire de "
            f"{fiche.get('nom', nom)}."))

    qui = str(lanceur or nom).strip()
    f_lanceur = _load_fiche(ctx, qui) or fiche
    _m_essai = re.search(r"\b(sort|essai|magie)\b",
                         str(methode or ""), re.IGNORECASE)

    if _m_essai and _m_essai.group(1).lower() == "sort":
        # Le sort Identification : un emplacement de sort niv. 1 chez le
        # lanceur + la composante matérielle (perle 100 po).
        from .. import sorts as _cat_s  # noqa: PLC0415
        _classe = str(f_lanceur.get("classe") or "")
        _s_l = _cat_s.sorts_de_fiche(f_lanceur)
        _prep = _s_l.get("prepares") or {}
        _cle_ident = next(
            (k for k in _prep
             if "identif" in str(k).lower()
             and int(_prep[k] or 0) > 0), None)
        if _cle_ident is None:
            return ToolResult(text=(
                f"❌ **{f_lanceur.get('nom', qui)}** n'a PAS le sort "
                "Identification préparé (niv. 1 clerc/magicien). Utilise "
                "`preparer_sorts`, ou tente l'essai d'Art de la magie "
                "(methode='essai', 1 h d'étude, difficulté de base quinze)."))
        _prep[_cle_ident] = int(_prep[_cle_ident] or 1) - 1
        _s_l["prepares"] = _prep
        f_lanceur["sorts"] = _s_l
        _note_compo = ""
        if int(f_lanceur.get("or", 0) or 0) >= 1000:
            f_lanceur["or"] = int(f_lanceur.get("or", 0) or 0) - 1000
            _note_compo = " Perle de 100 po consommée."
        elif int(f_lanceur.get("or", 0) or 0) > 0:
            _note_compo = " ⚠️ L'or ne suffit pas pour la perle (100 po) — "                           "composante empruntée : le MJ arbitre."
        else:
            _note_compo = " ⚠️ Aucune perle (100 po) : composante "                           "manquante — le MJ arbitre."
        _save_fiche(ctx, str(f_lanceur.get("nom") or qui), f_lanceur)
    else:
        # Essai : Art de la magie (INT) contre difficulté 15 + niveau.
        _int_v = int((f_lanceur.get("carac") or {}).get("INT", 10) or 10)
        _mod_int = (_int_v - 10) // 2
        _rangs = int((f_lanceur.get("competences") or {})
                     .get("Art de la magie", 0) or 0)
        _jet = random.randint(1, 20) + _rangs + _mod_int
        _dd_essai = 15 + max(1, int(f_lanceur.get("niveau") or 1))
        if _jet < _dd_essai:
            return ToolResult(text=(
                f"🔍 **Essai d'Art de la magie raté** : d20="
                f"{_jet - _rangs - _mod_int} + {_rangs} (rangs) + "
                f"{_mod_int:+d} (INT) = **{_jet}** contre DD {_dd_essai} — "
                "l'objet garde ses secrets. Une nouvelle étude (1 h) "
                "peut être tentée après un repos."))
        _note_compo = ""
        _preface = (
            f"🔍 **Essai d'Art de la magie réussi** : d20="
            f"{_jet - _rangs - _mod_int} + {_rangs} + {_mod_int:+d} = "
            f"**{_jet}** vs DD {_dd_essai}.")

    trouve["identifie"] = True
    # 🔧 (fix 14/11) : `_inventaire` renvoie une COPIE réparée — la
    # mutation de `trouve` ne touchait pas la fiche (le flag ne
    # persistait pas). Réassigne comme les autres outils.
    fiche["inventaire"] = inv
    _save_fiche(ctx, str(fiche.get("nom") or nom), fiche)
    if _m_essai and _m_essai.group(1).lower() == "sort":
        _preface = ("✨ **Sort d'Identification lancé** par "
                    f"{f_lanceur.get('nom', qui)}." + _note_compo)
    return ToolResult(text=(
        _preface
        + f"\n\n✅ **{trouve.get('nom')}** est identifié (marqué sur la "
        "fiche). RÉVÈLE MAINTENANT ses propriétés canoniques dans la "
        "narration — et seulement maintenant : avant l'identification, "
        "l'objet n'était que « une arme qui luit faiblement »."
    ))


def _normaliser_fiche(fiche: dict[str, Any]) -> dict[str, Any]:
    """Rappelle les champs dérivés d'encombrement pour un affichage homogène."""
    poids, cat, max_kg = _recalculer_charge(fiche)
    fiche.setdefault("poids_transporte", round(poids, 2))
    fiche["poids_transporte"] = round(poids, 2)
    fiche["etat_encumbrance"] = cat
    fiche["charge_max"] = int(max_kg)
    # ☠️ (fix 13/11) : les dégâts de CON réduisent les PV max (PHB 3.5 :
    # -1 PV par niveau et par point de CON endommagée). La base est
    # conservée (`pv_max_base`) pour restaurer quand la CON guérit.
    _dmg_con = int((fiche.get("degats_carac") or {}).get("CON", 0) or 0)
    if _dmg_con > 0:
        _base = int(fiche.get("pv_max_base") or fiche.get("pv_max") or 0)
        if _base > 0:
            fiche["pv_max_base"] = _base
            _niv = max(1, int(fiche.get("niveau") or 1))
            fiche["pv_max"] = max(1, _base - _niv * _dmg_con)
            try:
                if int(fiche.get("pv") or 0) > fiche["pv_max"]:
                    fiche["pv"] = fiche["pv_max"]
            except (TypeError, ValueError):
                pass
    elif fiche.get("pv_max_base"):
        fiche["pv_max"] = int(fiche.get("pv_max_base")
                              or fiche.get("pv_max") or 0)
        fiche.pop("pv_max_base", None)
    return fiche


def _consommer_fragment(
    fiche: dict[str, Any], fragment: str, quantite: int = 1
) -> Optional[str]:
    """Décrémente dans l'inventaire DE LA FICHE (sans sauvegarder) l'objet
    correspondant à `fragment` — recherche floue normalisée (l'objet
    « Flèches » répond au fragment « flèche », « potion de soins légers » à
    « potion »). Renvoie le nom de l'objet consommé, ou None si introuvable
    ou quantité insuffisante.

    Appelé par l'application des règles côté serveur : potion bue
    (`fiche_perso_soigner(source=…)`), munition tirée (`lancer_attaque`
    arc/arbalète/fronde/sarbacane/javelot) — la quantité en fiche reflète
    l'usage RÉEL sans dépendre de la mémoire du LLM (a6d11005, demande
    joueur : potions, flèches, carreaux… se soustraient à l'utilisation)."""
    frag = _norm(fragment)
    if not frag:
        return None
    inv = _inventaire(fiche)
    trouve = None
    for e in inv:
        ne = _norm(e.get("nom"))
        if ne == frag or frag in ne or ne in frag:
            trouve = e
            break
    if trouve is None:
        return None
    qte = int(trouve.get("qte", 1) or 1)
    if qte < quantite:
        return None
    reste = qte - quantite
    if reste > 0:
        trouve["qte"] = reste
    else:
        inv.remove(trouve)
    fiche["inventaire"] = inv
    fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                           for i in inv if i.get("nom")]
    _recalc_ca_fiche(fiche)
    _normaliser_fiche(fiche)
    return str(trouve.get("nom") or fragment)


def _consommer_charge_kit(fiche: dict[str, Any]) -> tuple[bool, int]:
    """Consomme 1 CHARGE du kit de premiers secours (D&D 3.5 : 10
    utilisations par kit). Le compteur vit dans `inventaire[].charges`
    (initialisé à 10 à la première utilisation pour les fiches existantes).

    Renvoie `(True, charges_restantes)` si une charge a été dépensée,
    `(False, 0)` si aucun kit n'est porté ou si le kit est épuisé
    (auquel cas il est retiré de l'inventaire — à remplacer ou réparer :
    un kit ne se « recharge » pas, on rachète les charges en ville).

    Appelé par `fiche_perso_soigner` : source explicite « kit » OU petit
    soin non-magique conventionnel (≤ 4 PV, la rule-maison 1d4 du kit)."""
    inv = _inventaire(fiche)
    for e in inv:
        nom_n = _norm(e.get("nom"))
        # « secours » → norm « secour » (singulier approx) : matcher le RADICAL.
        if not (("kit" in nom_n or "trousse" in nom_n)
                and ("secour" in nom_n or "soin" in nom_n)):
            continue
        brut = e.get("charges")
        try:
            charges = 10 if brut is None else int(brut)
        except (TypeError, ValueError):
            charges = 10
        if charges <= 0:
            # Kit épuisé : retiré de l'inventaire (son poids aussi).
            inv.remove(e)
            fiche["inventaire"] = inv
            fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                                   for i in inv if i.get("nom")]
            _recalc_ca_fiche(fiche)
            _normaliser_fiche(fiche)
            return False, 0
        e["charges"] = charges - 1
        fiche["inventaire"] = inv
        fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]}
                               for i in inv if i.get("nom")]
        _recalc_ca_fiche(fiche)
        _normaliser_fiche(fiche)
        return True, charges - 1
    return False, 0
