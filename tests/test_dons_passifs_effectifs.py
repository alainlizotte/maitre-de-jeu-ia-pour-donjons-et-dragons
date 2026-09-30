"""Tests — dons passifs EFFECTIFS en jeu (Arme de prédilection & cie).

Avant ces correctifs, seuls 6 dons avaient un effet mécanique ; « Arme de
prédilection », « Science de la critique », « Attaque en finesse », « Tir de
près », « Esquive » étaient inertes, et « Réflexes surhumains » (nom du
catalogue) ne matchait jamais (« reflexes surprenants » seul reconnu).

Couvre :
- mapping `bonus_dons_effet` : Réflexes surhumains (régression), Persuasion,
  Négociateur, Athlète ;
- helpers « arme au choix » : `bonus_attaque_dons`, `seuil_crit_arme`,
  `seuil_crit_dons`, `attaque_en_finesse_possible`, `a_don_tir_de_pres`,
  `a_don_esquive` ;
- recoupements `lancer_attaque` (prédilection +1, zone de critique, finesse
  DEX à l'attaque mais pas aux dégâts, Tir de près à distance fournie) ;
- `lancer_degats` (Tir de près : +1 dégâts à ≤ 9 m) ;
- CA à la création : « Esquive » +1 via `calculer_ca_armure` /
  `calculer_derivees` (miroir client : caEsquiveDons).

USAGE
-----
    py -m pytest tests/test_dons_passifs_effectifs.py -v
    (ou : python tests/test_dons_passifs_effectifs.py — runner intégré)
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.persos import calculer_ca_armure, calculer_derivees        # noqa: E402
from server.tools.base import ToolContext                             # noqa: E402
from server.tools.dice import lancer_attaque, lancer_degats           # noqa: E402
from server.tools.fiches import (                                     # noqa: E402
    _slug, attaque_en_finesse_possible, a_don_esquive, a_don_tir_de_pres,
    bonus_attaque_dons, bonus_dons_effet, seuil_crit_arme, seuil_crit_dons,
)


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _seed_for_d20(valeur: int) -> int:
    """Trouve une graine random telle que le prochain randint(1,20) == valeur
    (même utilitaire que tests/test_combats_complets.py)."""
    import random as _r
    for seed in range(10000):
        _r.seed(seed)
        if _r.randint(1, 20) == valeur:
            return seed
    raise AssertionError(f"aucune graine trouvée pour d20={valeur}")


def _ecrire_fiche(tmp: str, fiche: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8"
    )


FICHE_GROTH = {
    "nom": "Groth",
    "race": "Nain",
    "classe": "Guerrier",
    "niveau": 1,
    "bab": 1,
    "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 12, "CHA": 8},
    "competences": {"Escalade": 2},
    "dons": ["Arme de prédilection (épée longue)",
             "Science de la critique (épée longue)"],
    "sauvegardes": {"Vigueur": 4, "Reflexes": 1, "Volonte": 3},
    "ca": 15,
    "equipement": [{"nom": "Épée longue", "qte": 1}],
}


# --------------------------------------------------------------------------- #
#  bonus_dons_effet — régression + nouveaux effets de compétence
# --------------------------------------------------------------------------- #
def test_reflexes_surhumains_nom_catalogue_reconnu() -> None:
    # Régression : le catalogue dit « Réflexes surhumains », le mapping ne
    # reconnaissait que « reflexes surprenants » → don inerte.
    assert bonus_dons_effet(["Réflexes surhumains"], "sauvegarde_reflexes") == 2


def test_persuasion_negociateur_athlete_competences() -> None:
    # Persuasion → +2 Bluff, Diplomatie, Intimidation.
    assert bonus_dons_effet(["Persuasion"], "comp_bluff") == 2
    assert bonus_dons_effet(["Persuasion"], "comp_diplomatie") == 2
    assert bonus_dons_effet(["Persuasion"], "comp_intimidation") == 2
    # Négociateur → +2 Diplomatie, Psychologie.
    assert bonus_dons_effet(["Négociateur"], "comp_diplomatie") == 2
    assert bonus_dons_effet(["Négociateur"], "comp_psychologie") == 2
    # Cumul Persuasion + Négociateur sur la Diplomatie : +4.
    assert bonus_dons_effet(
        ["Persuasion", "Négociateur"], "comp_diplomatie") == 4
    # Athlète → +2 Escalade, Natation, Saut.
    for comp in ("comp_escalade", "comp_natation", "comp_saut"):
        assert bonus_dons_effet(["Athlète"], comp) == 2
    # Pas d'effet fantôme ailleurs.
    assert bonus_dons_effet(["Persuasion"], "initiative") == 0
    assert bonus_dons_effet(["Athlète"], "comp_discretion") == 0


# --------------------------------------------------------------------------- #
#  Arme de prédilection — bonus_attaque_dons
# --------------------------------------------------------------------------- #
def test_bonus_attaque_dons_par_arme() -> None:
    dons = ["Arme de prédilection (épée longue)"]
    assert bonus_attaque_dons(dons, "Épée longue") == 1
    assert bonus_attaque_dons(dons, "épée longue de maître") == 1
    assert bonus_attaque_dons(dons, "Hache d'arme") == 0
    # Sans arme précisée (fiches anciennes / saisie libre) → s'applique.
    assert bonus_attaque_dons(["Arme de prédilection"], "Rapière") == 1
    # Variantes EN.
    assert bonus_attaque_dons(["Weapon Focus (dagger)"], "Dague") == 1


def test_bonus_attaque_dons_superieure_sans_double_comptage() -> None:
    # La variante « supérieure » vaut +2 (pas +1+2 : l'ordre des mots-clés
    # évite que le don simple matche aussi).
    assert bonus_attaque_dons(
        ["Arme de prédilection supérieure (épée longue)"], "Épée longue") == 2
    # Deux dons pour deux armes différentes : seul celui de l'arme utilisée.
    dons = ["Arme de prédilection (épée longue)",
            "Arme de prédilection (arc long)"]
    assert bonus_attaque_dons(dons, "Épée longue") == 1
    assert bonus_attaque_dons(dons, "Arc long") == 1


# --------------------------------------------------------------------------- #
#  Science de la critique — seuil_crit_arme / seuil_crit_dons
# --------------------------------------------------------------------------- #
def test_seuil_crit_arme_bases_phb() -> None:
    assert seuil_crit_arme("Cimeterre") == 18
    assert seuil_crit_arme("Rapière") == 18
    assert seuil_crit_arme("Épée longue") == 19
    assert seuil_crit_arme("Dague") == 19
    assert seuil_crit_arme("Grande hache") == 20   # ×3 : le seuil reste 20
    assert seuil_crit_arme("arme inconnue") == 20
    assert seuil_crit_arme("") == 20


def test_seuil_crit_dons_doublage_de_zone() -> None:
    # 18-20 → 15-20 ; 19-20 → 17-20 ; 20 → 19-20.
    assert seuil_crit_dons(
        ["Science de la critique (cimeterre)"], "Cimeterre") == 15
    assert seuil_crit_dons(
        ["Science de la critique (épée longue)"], "Épée longue") == 17
    assert seuil_crit_dons(["Science de la critique"], "Grande hache") == 19
    # Don pour une AUTRE arme → zone de base de l'arme utilisée.
    assert seuil_crit_dons(
        ["Science de la critique (cimeterre)"], "Épée longue") == 19
    # Pas de cumul du don avec lui-même.
    assert seuil_crit_dons(
        ["Science de la critique (cimeterre)",
         "Science de la critique (cimeterre)"], "Cimeterre") == 15


# --------------------------------------------------------------------------- #
#  Attaque en finesse / Tir de près / Esquive — prédicats
# --------------------------------------------------------------------------- #
def test_attaque_en_finesse_armes_eligibles() -> None:
    dons = ["Attaque en finesse"]
    assert attaque_en_finesse_possible(dons, "Rapière")
    assert attaque_en_finesse_possible(dons, "Dague")
    assert attaque_en_finesse_possible(dons, "Cimeterre")
    assert not attaque_en_finesse_possible(dons, "Épée longue")   # pas légère
    assert not attaque_en_finesse_possible(dons, "Arc long")      # distance
    assert not attaque_en_finesse_possible(["Esquive"], "Dague")  # sans le don


def test_predicats_tir_de_pres_et_esquive() -> None:
    assert a_don_tir_de_pres(["Tir de près"])
    assert a_don_tir_de_pres(["Point Blank Shot"])
    assert not a_don_tir_de_pres(["Tir précis"])
    assert a_don_esquive(["Esquive"])
    assert not a_don_esquive(["Esquive extraordinaire"])   # capacité de classe
    assert not a_don_esquive(["Esquive totale"])
    assert not a_don_esquive(["Mobilité"])


# --------------------------------------------------------------------------- #
#  CA à la création — Esquive +1
# --------------------------------------------------------------------------- #
def test_calculer_ca_armure_esquive() -> None:
    # DEX 14 (+2), armure de cuir (2) → 14 ; Esquive → 15.
    base = calculer_ca_armure(2, ["Armure de cuir"])
    assert base == 14
    assert calculer_ca_armure(2, ["Armure de cuir"], dons=["Esquive"]) == 15
    assert calculer_ca_armure(
        2, ["Armure de cuir"], dons=["Esquive extraordinaire"]) == 14
    # Rétrocompatibilité : appel sans dons.
    assert calculer_ca_armure(2, ["Armure de cuir"]) == base


def test_calculer_derivees_passe_les_dons_a_la_ca() -> None:
    calc = calculer_derivees(
        {"FOR": 14, "DEX": 14, "CON": 12, "INT": 10, "SAG": 10, "CHA": 10},
        "Humain", "Guerrier", 1,
        armures=["Armure de cuir"], dons=["Esquive"],
    )
    assert calc["ca"] == 15
    calc_sans = calculer_derivees(
        {"FOR": 14, "DEX": 14, "CON": 12, "INT": 10, "SAG": 10, "CHA": 10},
        "Humain", "Guerrier", 1, armures=["Armure de cuir"],
    )
    assert calc_sans["ca"] == 14


# --------------------------------------------------------------------------- #
#  lancer_attaque — Arme de prédilection + zone de critique
# --------------------------------------------------------------------------- #
def _etat_exploration(tmp: str) -> None:
    from server.game.state import PartyState
    PartyState(data_dir=tmp, partie_id="t").save(
        {"phase": "exploration", "pj": [], "monstres_combat": []})


def test_lancer_attaque_bonus_predilection_et_zone_critique() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        _ecrire_fiche(tmp, FICHE_GROTH)
        # BBA 1 + FOR 16 (+3) + Arme de prédilection (+1) = +5, même si le
        # LLM envoie +4 (il ignore le don). Zone de critique : épée longue
        # 19-20 doublée par Science de la critique → 17-20.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4, ca_cible=15,
                                        nom_attaquant="Groth",
                                        arme="Épée longue",
                                        nom_cible="Gobelin"))
        assert "Bonus officiel : +5" in tr.text
        assert "Arme de prédilection" in tr.text
        assert "Zone de critique 17-20" in tr.text
        assert "Science de la critique" in tr.text


def test_lancer_attaque_predilection_mauvaise_arme() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = dict(FICHE_GROTH)
        fiche["equipement"] = [{"nom": "Hache d'arme", "qte": 1}]
        _ecrire_fiche(tmp, fiche)
        # Le don vise l'épée longue : aucune bonification à la hache d'arme
        # (BBA 1 + FOR +3 = +4). Zone : hache d'arme = 20, pas de note.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4, ca_cible=15,
                                        nom_attaquant="Groth",
                                        arme="Hache d'arme",
                                        nom_cible="Gobelin"))
        assert "Bonus total : +4" in tr.text
        assert "Arme de prédilection" not in tr.text
        assert "Zone de critique" not in tr.text


def test_lancer_attaque_monstre_sans_fiche_inchange() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        # Monstre (pas de fiche) : le bonus du bestiaire est conservé tel
        # quel (ses statblocks intègrent déjà ses dons).
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=3, ca_cible=14,
                                        nom_attaquant="Gobelin",
                                        arme="Gourdin",
                                        nom_cible="Groth"))
        assert "Bonus total : +3" in tr.text
        assert "Bonus officiel" not in tr.text
        assert "Zone de critique" not in tr.text


# --------------------------------------------------------------------------- #
#  lancer_attaque — Attaque en finesse (DEX à l'attaque, FOR aux dégâts)
# --------------------------------------------------------------------------- #
def test_lancer_attaque_finesse_dex_attaque_for_degats() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = {
            "nom": "Sylvaris", "race": "Elfe", "classe": "Voleur", "niveau": 1,
            "bab": 0,
            "carac": {"FOR": 12, "DEX": 18, "CON": 10, "INT": 14,
                      "SAG": 12, "CHA": 10},
            "pv": 6, "pv_max": 6, "ca": 14,
            "dons": ["Attaque en finesse"],
            "equipement": [{"nom": "Rapière", "qte": 1}],
            "sauvegardes": {"Vigueur": 0, "Reflexes": 2, "Volonte": 0},
        }
        _ecrire_fiche(tmp, fiche)
        # FOR 12 (+1) < DEX 18 (+4) : la rapière attaque à +4 (DEX), et le
        # LLM qui envoie +1 est corrigé avec la mention de la finesse.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=1, ca_cible=15,
                                        nom_attaquant="Sylvaris",
                                        arme="Rapière",
                                        nom_cible="Gobelin"))
        assert "Bonus officiel : +4" in tr.text
        assert "Attaque en finesse (DEX au lieu de FOR)" in tr.text
        # Les dégâts RESTENT au mod. FOR (+1) : la note doit le rappeler.
        assert "l'Attaque en finesse ne s'applique pas aux dégâts" in tr.text
        assert "Bonus dégâts officiel : +1" in tr.text
        # Zone de la rapière : 18-20 de base, note affichée sans le don.
        assert "Zone de critique 18-20" in tr.text


def test_lancer_attaque_finesse_sans_gain_si_for_superieur() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = {
            "nom": "Brann", "race": "Humain", "classe": "Guerrier",
            "niveau": 1, "bab": 1,
            "carac": {"FOR": 18, "DEX": 14, "CON": 12, "INT": 10,
                      "SAG": 10, "CHA": 10},
            "pv": 12, "pv_max": 12, "ca": 14,
            "dons": ["Attaque en finesse"],
            "equipement": [{"nom": "Dague", "qte": 1}],
            "sauvegardes": {"Vigueur": 2, "Reflexes": 0, "Volonte": 0},
        }
        _ecrire_fiche(tmp, fiche)
        # FOR 18 (+4) > DEX 14 (+2) : la finesse ne sert jamais à perdre.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=5, ca_cible=15,
                                        nom_attaquant="Brann", arme="Dague",
                                        nom_cible="Gobelin"))
        assert "Bonus total : +5" in tr.text
        assert "Attaque en finesse (DEX" not in tr.text


# --------------------------------------------------------------------------- #
#  lancer_attaque / lancer_degats — Tir de près (distance fournie)
# --------------------------------------------------------------------------- #
FICHE_ARCHER = {
    "nom": "Archer", "race": "Humain", "classe": "Rodeur", "niveau": 1,
    "bab": 1,
    "carac": {"FOR": 12, "DEX": 18, "CON": 12, "INT": 10, "SAG": 14,
              "CHA": 10},
    "pv": 8, "pv_max": 8, "ca": 14,
    "dons": ["Tir de près"],
    "equipement": [{"nom": "Arc long", "qte": 1},
                   {"nom": "flèche", "qte": 20}],
    "sauvegardes": {"Vigueur": 2, "Reflexes": 2, "Volonte": 0},
}


def test_lancer_attaque_tir_de_pres_distance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        _ecrire_fiche(tmp, FICHE_ARCHER)
        # ≤ 9 m : BBA 1 + DEX 18 (+4) + Tir de près (+1) = +6.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=5, ca_cible=15,
                                        nom_attaquant="Archer",
                                        arme="Arc long", nom_cible="Gobelin",
                                        distance_m=6))
        assert "Bonus officiel : +6" in tr.text
        assert "Tir de près (≤ 9 m)" in tr.text
        # Hors portée (> 9 m) : retour à +5, sans la mention.
        tr2 = asyncio.run(lancer_attaque(ctx, bonus_attaque=5, ca_cible=15,
                                         nom_attaquant="Archer",
                                         arme="Arc long",
                                         nom_cible="Gobelin",
                                         distance_m=15))
        assert "Bonus total : +5" in tr2.text
        assert "Tir de près" not in tr2.text
        # Distance absente : aucun bonus, aucune mention (compat ARRIÈRE).
        tr3 = asyncio.run(lancer_attaque(ctx, bonus_attaque=5, ca_cible=15,
                                         nom_attaquant="Archer",
                                         arme="Arc long",
                                         nom_cible="Gobelin"))
        assert "Bonus total : +5" in tr3.text
        assert "Tir de près" not in tr3.text


def test_lancer_degats_tir_de_pres_distance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        _ecrire_fiche(tmp, FICHE_ARCHER)
        # ≤ 9 m : +1 dégâts (arme à distance : mod. FOR non appliqué).
        tr = asyncio.run(lancer_degats(ctx, nb_des=1, faces=8, bonus=0,
                                       arme_ou_sort="Arc long",
                                       cible="Gobelin",
                                       attaquant="Archer", distance_m=5))
        assert "Bonus dégâts : +1" in tr.text
        assert "Tir de près" in tr.text
        # Sans distance : +0 (comportement inchangé).
        tr2 = asyncio.run(lancer_degats(ctx, nb_des=1, faces=8, bonus=0,
                                        arme_ou_sort="Arc long",
                                        cible="Gobelin",
                                        attaquant="Archer"))
        assert "Bonus dégâts : +0" in tr2.text
        assert "Tir de près" not in tr2.text


# --------------------------------------------------------------------------- #
#  Zone de critique de base — menace au-delà du 20 sans don
# --------------------------------------------------------------------------- #
def test_lancer_attaque_menace_zone_base_rapiere() -> None:
    """Un 19 à la rapière est déjà une MENACE (18-20) : le message doit le
    signaler comme un 20 naturel le fait depuis l'origine."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = {
            "nom": "Sylvaris", "race": "Elfe", "classe": "Voleur", "niveau": 1,
            "bab": 0,
            "carac": {"FOR": 12, "DEX": 18, "CON": 10, "INT": 14,
                      "SAG": 12, "CHA": 10},
            "pv": 6, "pv_max": 6, "ca": 14,
            "dons": ["Attaque en finesse"],
            "equipement": [{"nom": "Rapière", "qte": 1}],
            "sauvegardes": {"Vigueur": 0, "Reflexes": 2, "Volonte": 0},
        }
        _ecrire_fiche(tmp, fiche)
        # Jet figé à 19 (hook de seed du projet) : la rapière (18-20) menace.
        import random as _r
        etat = _r.getstate()
        _r.seed(_seed_for_d20(19))
        try:
            tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4,
                                            ca_cible=15,
                                            nom_attaquant="Sylvaris",
                                            arme="Rapière",
                                            nom_cible="Gobelin"))
        finally:
            _r.setstate(etat)
        assert "⭐ **19 naturel**" in tr.text
        assert "menace de critique" in tr.text


# --------------------------------------------------------------------------- #
#  Runner intégré (pytest absent)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    echecs = 0
    for fn in fns:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except AssertionError as e:
            echecs += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - echecs}/{len(fns)} tests OK")
    sys.exit(1 if echecs else 0)
