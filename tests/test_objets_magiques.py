"""Tests — objets magiques portés EFFECTIFS (anneaux, arme +N, gantelets…).

Avant ces correctifs, les objets magiques étaient cosmétiques : ajoutés en
jeu (achat, ramassage, trésor), ils n'avaient AUCUN effet mécanique.

Couvre :
- helpers : `bonus_arme_magique` (arme « +N »), `bonus_ca_magie` (anneau de
  protection / amulette / brassards / casque — max PAR type, types
  additionnés), `bonus_carac_equipement` (gantelets d'ogre de force, bottes
  de dextérité, casque de sagesse) ;
- `lancer_attaque` : arme +N au bonus officiel, FOR effective via gantelets,
  CA magique de la CIBLE (déflection) ;
- `lancer_degats` : +N dégâts de l'arme enchantée, FOR effective mêlée,
  ×1,5 à deux mains cumulé avec le bonus magique.

USAGE
-----
    py -m pytest tests/test_objets_magiques.py -v
    (ou : python tests/test_objets_magiques.py — runner intégré)
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

from server.tools.base import ToolContext                            # noqa: E402
from server.tools.dice import lancer_attaque, lancer_degats          # noqa: E402
from server.tools.fiches import (                                    # noqa: E402
    _slug, bonus_arme_magique, bonus_ca_magie, bonus_carac_equipement,
)


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _ecrire_fiche(tmp: str, fiche: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")


def _etat_exploration(tmp: str) -> None:
    from server.game.state import PartyState
    PartyState(data_dir=tmp, partie_id="t").save(
        {"phase": "exploration", "pj": [], "monstres_combat": []})


# --------------------------------------------------------------------------- #
#  Arme enchantée « +N »
# --------------------------------------------------------------------------- #
def test_bonus_arme_magique_plus_n() -> None:
    assert bonus_arme_magique("Épée longue +1") == 1
    assert bonus_arme_magique("Épée longue +3") == 3
    assert bonus_arme_magique("arc long +2") == 2
    assert bonus_arme_magique("Grande hache +1 flamboyante") == 1
    assert bonus_arme_magique("Épée longue") == 0
    assert bonus_arme_magique("") == 0
    assert bonus_arme_magique("Dague de glace") == 0


# --------------------------------------------------------------------------- #
#  CA magique — anneau de protection, amulette, brassards, casque
# --------------------------------------------------------------------------- #
def test_bonus_ca_magie_par_type() -> None:
    # Un seul objet.
    fiche = {"equipement": [{"nom": "Anneau de protection +2", "qte": 1}]}
    assert bonus_ca_magie(fiche) == 2
    # Types DIFFÉRENTS s'additionnent (déflection + naturelle).
    fiche2 = {"equipement": [
        {"nom": "Anneau de protection +1", "qte": 1},
        {"nom": "Amulette naturelle +1", "qte": 1},
    ]}
    assert bonus_ca_magie(fiche2) == 2
    # MÊME type : le meilleur seul (RAW : déflection ne s'empile pas).
    fiche3 = {"equipement": [
        {"nom": "Anneau de protection +1", "qte": 1},
        {"nom": "anneau de protection +3", "qte": 1},
    ]}
    assert bonus_ca_magie(fiche3) == 3
    # Brassards d'armure (armure) + anneau (déflection) : addition.
    fiche4 = {"equipement": [
        {"nom": "Brassards d'armure +3", "qte": 1},
        {"nom": "Anneau de protection +2", "qte": 1},
    ]}
    assert bonus_ca_magie(fiche4) == 5
    # Casque de protéger (déflection) : le max du type s'applique.
    fiche5 = {"equipement": [
        {"nom": "Casque de protéger +1", "qte": 1},
        {"nom": "Anneau de protection +2", "qte": 1},
    ]}
    assert bonus_ca_magie(fiche5) == 2
    # Objets SANS bonus magique → 0.
    fiche6 = {"equipement": [
        {"nom": "Anneau de mariage", "qte": 1},
        {"nom": "Corde de chanvre", "qte": 1},
    ]}
    assert bonus_ca_magie(fiche6) == 0
    assert bonus_ca_magie(None) == 0
    assert bonus_ca_magie({}) == 0
    # Objets magiques dans l'INVENTAIRE (pas equipement) : détectés aussi.
    fiche7 = {"inventaire": [{"nom": "Anneau de protection +1", "qte": 1}]}
    assert bonus_ca_magie(fiche7) == 1


# --------------------------------------------------------------------------- #
#  Caractéristiques d'équipement — gantelets de force, bottes, casque
# --------------------------------------------------------------------------- #
def test_bonus_carac_equipement() -> None:
    # Gantelets d'ogre de force +2 → +2 FOR.
    fiche = {"equipement": [{"nom": "Gantelets d'ogre de force +2", "qte": 1}]}
    assert bonus_carac_equipement(fiche, "FOR") == 2
    assert bonus_carac_equipement(fiche, "DEX") == 0
    # Bottes de dextérité +2 → +2 DEX.
    fiche2 = {"equipement": [{"nom": "Bottes de dextérité +2", "qte": 1}]}
    assert bonus_carac_equipement(fiche2, "DEX") == 2
    assert bonus_carac_equipement(fiche2, "FOR") == 0
    # Casque de sagesse +2 → +2 SAG.
    fiche3 = {"equipement": [{"nom": "Casque de sagesse +2", "qte": 1}]}
    assert bonus_carac_equipement(fiche3, "SAG") == 2
    # Mots-clés avec frontière de mot : « Épée longue +1 » ne donne pas de FOR.
    fiche4 = {"equipement": [{"nom": "Épée longue +1", "qte": 1}]}
    assert bonus_carac_equipement(fiche4, "FOR") == 0
    assert bonus_carac_equipement(fiche4, "DEX") == 0
    # Doublons : le max seul (pas d'empilement entre objets jumeaux).
    fiche5 = {"equipement": [
        {"nom": "Gantelets de force +2", "qte": 1},
        {"nom": "Gantelets d'ogre de force +2", "qte": 1},
    ]}
    assert bonus_carac_equipement(fiche5, "FOR") == 2
    # Aucun objet magique → 0.
    assert bonus_carac_equipement(None, "FOR") == 0
    assert bonus_carac_equipement({}, "FOR") == 0


# --------------------------------------------------------------------------- #
#  lancer_attaque — arme enchantée, FOR effective, CA magique de la cible
# --------------------------------------------------------------------------- #
FICHE_AUREL = {
    "nom": "Aurel", "race": "Humain", "classe": "Guerrier", "niveau": 1,
    "bab": 1,
    "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 12,
              "CHA": 10},
    "pv": 12, "pv_max": 12, "ca": 14,
    "dons": [],
    "equipement": [{"nom": "Épée longue +1", "qte": 1}],
    "sauvegardes": {"Vigueur": 2, "Reflexes": 0, "Volonte": 0},
}


def test_lancer_attaque_arme_enchantee() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        _ecrire_fiche(tmp, FICHE_AUREL)
        # BBA 1 + FOR 16 (+3) + arme +1 = +5 ; le LLM envoie +4 (BBA+FOR).
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4, ca_cible=15,
                                        nom_attaquant="Aurel",
                                        arme="Épée longue +1",
                                        nom_cible="Gobelin"))
        assert "Bonus officiel : +5" in tr.text
        assert "+1 arme enchantée" in tr.text
        # Dégâts : FOR +3 + 1 arme enchantée = +4.
        assert "Bonus dégâts officiel : +4" in tr.text
        assert "+ 1 arme enchantée" in tr.text


def test_lancer_attaque_gantelets_de_force() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = dict(FICHE_AUREL)
        fiche["equipement"] = [
            {"nom": "Épée longue", "qte": 1},
            {"nom": "Gantelets d'ogre de force +2", "qte": 1},
        ]
        _ecrire_fiche(tmp, fiche)
        # FOR 16 → 18 effective (+4) ; le LLM (ignorant les gantelets)
        # envoie +4 (BBA + FOR 16 de base) → corrigé vers +5 avec la note.
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4, ca_cible=15,
                                        nom_attaquant="Aurel",
                                        arme="Épée longue",
                                        nom_cible="Gobelin"))
        assert "Bonus officiel : +5" in tr.text
        assert "FOR effective 18" in tr.text
        # Dégâts : FOR effective 18 (+4).
        assert "Bonus dégâts officiel : +4" in tr.text


def test_lancer_attaque_ca_magique_de_la_cible() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        # La CIBLE (PJ) porte un anneau de protection +2 ajouté EN JEU.
        cible = {
            "nom": "Thalia", "race": "Humaine", "classe": "Clerc",
            "niveau": 1, "bab": 0,
            "carac": {"FOR": 10, "DEX": 12, "CON": 14, "INT": 10,
                      "SAG": 16, "CHA": 14},
            "pv": 8, "pv_max": 8, "ca": 14,
            "equipement": [{"nom": "Anneau de protection +2", "qte": 1}],
            "sauvegardes": {"Vigueur": 2, "Reflexes": 0, "Volonte": 4},
        }
        _ecrire_fiche(tmp, cible)
        tr = asyncio.run(lancer_attaque(ctx, bonus_attaque=4, ca_cible=14,
                                        nom_attaquant="Gobelin",
                                        arme="Gourdin",
                                        nom_cible="Thalia"))
        # CA imposée par les règles : 14 (fiche) + 2 (anneau) = 16.
        assert "CA 16" in tr.text
        assert "+2 CA magique" in tr.text


# --------------------------------------------------------------------------- #
#  lancer_degats — +N dégâts, FOR effective, ×1,5 cumulé
# --------------------------------------------------------------------------- #
def test_lancer_degats_arme_enchantee_mele() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        _ecrire_fiche(tmp, FICHE_AUREL)
        # FOR 16 (+3) + arme +1 = +4 ; le LLM envoie +3.
        tr = asyncio.run(lancer_degats(ctx, nb_des=1, faces=8, bonus=3,
                                       arme_ou_sort="Épée longue +1",
                                       cible="Gobelin",
                                       attaquant="Aurel"))
        assert "Bonus dégâts officiel : +4" in tr.text
        assert "+ 1 arme enchantée" in tr.text


def test_lancer_degats_arme_enchantee_distance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = dict(FICHE_AUREL)
        fiche["equipement"] = [{"nom": "Arc long +1", "qte": 1},
                               {"nom": "flèche", "qte": 20}]
        _ecrire_fiche(tmp, fiche)
        # Arme à distance : mod. DEX ne s'applique pas, mais arme +1 → +1.
        tr = asyncio.run(lancer_degats(ctx, nb_des=1, faces=8, bonus=0,
                                       arme_ou_sort="Arc long +1",
                                       cible="Gobelin",
                                       attaquant="Aurel"))
        assert "Bonus dégâts officiel : +1" in tr.text
        assert "+ 1 arme enchantée" in tr.text


def test_lancer_degats_gantelets_et_deux_mains() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = dict(FICHE_AUREL)
        fiche["equipement"] = [
            {"nom": "Hache à deux mains +1", "qte": 1},
            {"nom": "Gantelets d'ogre de force +2", "qte": 1},
        ]
        _ecrire_fiche(tmp, fiche)
        # FOR 16 → 18 (+4), ×1,5 → +6, + 1 arme enchantée = +7.
        tr = asyncio.run(lancer_degats(ctx, nb_des=1, faces=12, bonus=4,
                                       arme_ou_sort="Hache à deux mains +1",
                                       cible="Gobelin",
                                       attaquant="Aurel"))
        assert "Bonus dégâts officiel : +7" in tr.text
        assert "×1,5" in tr.text
        assert "+ 1 arme enchantée" in tr.text


def test_lancer_degats_sans_objet_magique_inchange() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _etat_exploration(tmp)
        fiche = dict(FICHE_AUREL)
        fiche["equipement"] = [{"nom": "Épée longue", "qte": 1}]
        _ecrire_fiche(tmp, fiche)
        # Aucun objet magique : calcul 3.5 standard (FOR +3).
        tr = asyncio.run(lancer_degats(ctx, nb_des=1, faces=8, bonus=3,
                                       arme_ou_sort="Épée longue",
                                       cible="Gobelin",
                                       attaquant="Aurel"))
        assert "Bonus dégâts officiel" not in tr.text
        assert "Bonus dégâts : +3" in tr.text


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
