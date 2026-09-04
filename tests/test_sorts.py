"""Tests — mécanique de magie D&D 3.5 (server/sorts.py + tools/sorts.py).

Couvre :
- tables d'emplacements (slots) par classe/niveau + bonus de caractéristique
  et emplacement de domaine du clerc ;
- filtrage des sorts par classe et niveau castable ;
- `incanter_sort` : refus hors-classe / trop puissant / non préparé /
  emplacements épuisés ; consommation de la préparation et du slot ;
  dégâts appliqués à la cible ; soins ;
- `preparer_sorts` (capacité par niveau, hors-liste refusée) ;
- `repos_long` (restauration des emplacements) ;
- `resume_sorts` (ligne du récapitulatif MJ) ;
- budget de sorts connus des spontanés (Sorcier).

USAGE
-----
    py -m pytest tests/test_sorts.py -v
    (ou : python tests/test_sorts.py — runner intégré)
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

from server import sorts as cat                                        # noqa: E402
from server.tools.base import ToolContext                              # noqa: E402
from server.tools.fiches import _slug                                  # noqa: E402
from server.tools.sorts import incanter_sort, preparer_sorts, repos_long  # noqa: E402


def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _ecrire_fiche(tmp: str, fiche: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8"
    )


MAGE = {
    "nom": "Zarkon", "race": "Humain", "classe": "Magicien", "niveau": 3,
    "carac": {"FOR": 8, "DEX": 14, "CON": 12, "INT": 17, "SAG": 10, "CHA": 10},
    "pv": 12, "pv_max": 12, "ca": 12, "bab": 1,
    "sauvegardes": {"Vigueur": 1, "Reflexes": 2, "Volonte": 3},
    "sorts": {
        "connus": ["Rayon de givre", "Projectiles magiques", "Mains brûlantes"],
        "prepares": {},
        "depenses": {},
    },
}

CLERC = {
    "nom": "Aurora", "race": "Humaine", "classe": "Clerc", "niveau": 2,
    "carac": {"FOR": 14, "DEX": 10, "CON": 14, "INT": 10, "SAG": 16, "CHA": 12},
    "pv": 14, "pv_max": 14, "ca": 14, "bab": 1,
    "sauvegardes": {"Vigueur": 4, "Reflexes": 0, "Volonte": 5},
    "sorts": {"connus": [], "prepares": {}, "depenses": {}},
}

GOBELIN = {
    "nom": "Gobelin", "race": "Gobelin", "classe": "Guerrier", "niveau": 1,
    "carac": {"FOR": 11, "DEX": 13, "CON": 12, "INT": 10, "SAG": 9, "CHA": 8},
    "pv": 5, "pv_max": 5, "ca": 15, "bab": 1,
    "sauvegardes": {"Vigueur": 3, "Reflexes": 1, "Volonte": 0},
}


# --------------------------------------------------------------------------- #
#  Tables d'emplacements
# --------------------------------------------------------------------------- #
def test_emplacements_magicien() -> None:
    # Niv.1 : 3 cantrips + 1 sort de niv.1 ; mod INT +3 → +1 niv.1, +1 niv.2.
    assert cat.emplacements("Magicien", 1, 0) == {0: 3, 1: 1}
    assert cat.emplacements("Magicien", 5, 3) == {0: 4, 1: 4, 2: 3, 3: 1}


def test_emplacements_clerc_domaine() -> None:
    # Niv.2 : base (4,2) + mod SAG +2 → +1 niv.1 + domaine (0/1) → (5, 4).
    assert cat.emplacements("Clerc", 2, 2) == {0: 5, 1: 4}


def test_emplacements_paladin_depart_niv4() -> None:
    assert cat.emplacements("Paladin", 3, 4) == {}
    # Niv.4 : 1 emplacement de niv.1 (mod +4 → +1 supplémentaire).
    assert cat.emplacements("Paladin", 4, 4) == {1: 2}


def test_non_lanceur() -> None:
    assert not cat.est_lanceur("Guerrier")
    assert cat.niveau_sort_max("Guerrier", 10) == -1


def test_niveau_sort_max_et_liste() -> None:
    assert cat.niveau_sort_max("Sorcier", 5) == 3  # (6,6,4,1) : 3e niveau de sort
    noms = [s["nom"] for s in cat.sorts_pour("Magicien", 1)]
    assert "Boule de feu" not in noms and "Projectiles magiques" in noms
    assert "Soins légers" not in noms  # pas dans la liste magicien


def test_budget_connus_sorcier() -> None:
    # Budget niv.3 : (5,3) → 3 sorts de niv.1 connus ; 4 = 1 de trop.
    exces = cat.depassement_connus(
        "Sorcier", 3,
        ["Mains brûlantes", "Mains brûlantes", "Mains brûlantes", "Mains brûlantes"],
    )
    assert exces == {1: 1}
    assert cat.depassement_connus("Sorcier", 3, ["Mains brûlantes"]) == {}


# --------------------------------------------------------------------------- #
#  incanter_sort — validation stricte
# --------------------------------------------------------------------------- #
def test_incanter_refuse_hors_classe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(CLERC))
        tr = asyncio.run(incanter_sort(ctx, "Aurora", "Boule de feu", ""))
        assert tr.text.startswith("⛔") and "Clerc" in tr.text


def test_incanter_refuse_trop_puissant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = dict(MAGE)
        fiche["niveau"] = 1
        _ecrire_fiche(tmp, fiche)
        tr = asyncio.run(incanter_sort(ctx, "Zarkon", "Boule de feu", "Gobelin"))
        assert tr.text.startswith("⛔") and "trop puissant" in tr.text


def test_incanter_refuse_non_prepare() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(MAGE))
        tr = asyncio.run(incanter_sort(ctx, "Zarkon", "Projectiles magiques", "Gobelin"))
        assert tr.text.startswith("⛔") and "preparer_sorts" in tr.text


def test_incanter_preparation_puis_slot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = json.loads(json.dumps(MAGE))
        fiche["sorts"]["prepares"] = {"Projectiles magiques": 1}
        _ecrire_fiche(tmp, fiche)
        _ecrire_fiche(tmp, dict(GOBELIN))
        # Niv.3 + mod INT +3 : emplacements niv.1 = 2 (base) + 1 (bonus) = 3.
        tr = asyncio.run(incanter_sort(ctx, "Zarkon", "Projectiles magiques", "Gobelin"))
        assert "✨" in tr.text and "Emplacements niv.1 : 1/3" in tr.text
        # La préparation est consommée + le slot dépensé.
        d = json.loads((Path(tmp) / "fiches" / "fiche_zarkon.json").read_text(encoding="utf-8"))
        assert d["sorts"]["prepares"]["Projectiles magiques"] == 0
        assert d["sorts"]["depenses"]["1"] == 1
        # 2e incantation : plus de préparation → refus.
        tr2 = asyncio.run(incanter_sort(ctx, "Zarkon", "Projectiles magiques", "Gobelin"))
        assert tr2.text.startswith("⛔")
        # Le gobelin a encaissé les projectiles (2d4, auto) → PV < 5.
        g = json.loads((Path(tmp) / "fiches" / "fiche_gobelin.json").read_text(encoding="utf-8"))
        assert g["pv"] < 5


def test_incanter_soins_clerc() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = json.loads(json.dumps(CLERC))
        fiche["pv"] = 6  # blessée
        fiche["sorts"]["prepares"] = {"Soins légers": 1}
        _ecrire_fiche(tmp, fiche)
        tr = asyncio.run(incanter_sort(ctx, "Aurora", "Soins légers", "Aurora"))
        assert "Soins" in tr.text and "PV" in tr.text
        d = json.loads((Path(tmp) / "fiches" / "fiche_aurora.json").read_text(encoding="utf-8"))
        assert 7 <= d["pv"] <= 14  # 1d8+2 (niv.2) soigné, plafonné à 14


def test_incanter_emplacements_epuises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = json.loads(json.dumps(CLERC))
        fiche["sorts"]["prepares"] = {"Soins légers": 3}
        fiche["sorts"]["depenses"] = {"1": 3}  # niv.2 : 3+1 = 4 niv.1... dépense 3/4
        fiche["sorts"]["depenses"] = {"1": 4}  # épuisé
        _ecrire_fiche(tmp, fiche)
        tr = asyncio.run(incanter_sort(ctx, "Aurora", "Soins légers", "Aurora"))
        assert tr.text.startswith("⛔") and "Plus aucun emplacement" in tr.text


def test_repos_long_restaure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = json.loads(json.dumps(CLERC))
        fiche["pv"] = 5
        fiche["sorts"]["depenses"] = {"1": 4}
        _ecrire_fiche(tmp, fiche)
        tr = asyncio.run(repos_long(ctx, "Aurora"))
        assert "emplacements de sorts restaurés" in tr.text
        d = json.loads((Path(tmp) / "fiches" / "fiche_aurora.json").read_text(encoding="utf-8"))
        assert d["sorts"]["depenses"] == {}
        assert d["pv"] == 7  # +1 PV/niveau (clerc niv.2)


def test_preparer_sorts_capacite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(CLERC))
        # Clerc niv.2 + mod SAG +3 : niv.1 = base 2 + bonus 1 + domaine 1 = 4.
        tr = asyncio.run(preparer_sorts(
            ctx, "Aurora",
            json.dumps({"Soins légers": 3, "Bénédiction": 1}),
        ))
        assert "📖" in tr.text
        # 5 préparations de niv.1 → dépassement.
        tr2 = asyncio.run(preparer_sorts(
            ctx, "Aurora", json.dumps({"Soins légers": 5}),
        ))
        assert tr2.text.startswith("⛔") and "Trop de préparations" in tr2.text
        # Sort hors-liste clerc.
        tr3 = asyncio.run(preparer_sorts(ctx, "Aurora", json.dumps({"Boule de feu": 1})))
        assert tr3.text.startswith("⛔")


# --------------------------------------------------------------------------- #
#  resume_sorts — ligne du récapitulatif MJ
# --------------------------------------------------------------------------- #
def test_resume_sorts() -> None:
    fiche = json.loads(json.dumps(CLERC))
    fiche["sorts"]["prepares"] = {"Soins légers": 2}
    fiche["sorts"]["depenses"] = {"1": 1}
    r = cat.resume_sorts(fiche)
    # Clerc niv.2 mod SAG +3 : niv.0 5/5, niv.1 3/4 (1 dépensé sur 4).
    assert "n.0: 5/5" in r and "n.1: 3/4" in r
    assert "Soins légers x2" in r
    assert cat.resume_sorts(dict(GOBELIN)) == ""


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
        except Exception as e:  # noqa: BLE001
            echecs += 1
            print(f"  ERREUR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - echecs}/{len(fns)} tests OK")
    sys.exit(1 if echecs else 0)
