"""Réparation des entrées d'inventaire malformées (nom-porte-clé LLM).

Bug réel (fiche Barouk, partie dc4dd5aa) : pour enregistrer un objet trouvé,
le modèle a appelé `fiche_perso_mettre_a_jour(champ="equipement")` avec
`{"clé_de_fer_rouillee": 1, "description": "…"}` (nom porté en CLÉ du dict).
Le 1er essai (dict nu) fut rejeté par le schéma ; le 2e (liste contenant ce
dict) passa — le schéma n'exigeait pas `nom` — et a ÉCRASÉ tout
l'équipement : l'inventaire du joueur a disparu de la fiche.

Correctifs testés ici :
- `_inventaire()` répare les entrées nom-porte-clé au lieu de les jeter ;
- `_save_fiche()` auto-répare equipement/inventaire avant validation
  (sinon toute sauvegarde ultérieure de la fiche échoue) ;
- le schéma exige désormais `nom` dans chaque item.

Usage : py -m pytest tests/test_inventaire_reparation.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.tools.base import ToolContext       # noqa: E402
from server.tools.fiches import _save_fiche     # noqa: E402
from server.tools.inventaire import _inventaire  # noqa: E402

MALFORMEE = {"clé_de_fer_rouillee": 1,
             "description": "Clé de fer rouillée, tachée de sang séché"}


def test_inventaire_repare_nom_porte_cle():
    """L'entrée {nom-objet: qte} est réparée en {nom, qte, description}."""
    fiche = {"equipement": [MALFORMEE]}
    inv = _inventaire(fiche)
    assert inv == [{
        "nom": "clé de fer rouillee",
        "qte": 1,
        "description": "Clé de fer rouillée, tachée de sang séché",
    }]


def test_inventaire_canonique_intacte():
    """Les entrées déjà canoniques passent sans modification."""
    fiche = {"inventaire": [
        {"nom": "Hache à deux mains", "qte": 1},
        {"nom": "Torche", "qte": 3, "poids": 0.5},
    ]}
    assert _inventaire(fiche) == [
        {"nom": "Hache à deux mains", "qte": 1},
        {"nom": "Torche", "qte": 3, "poids": 0.5},
    ]


def test_inventaire_entree_irrecuperable_ignoree():
    """Sans nom récupérable (aucune clé exploitable), l'entrée est écartée —
    les autres survivent."""
    fiche = {"equipement": [
        {"nom": "Sac à dos", "qte": 1},
        {"description": "objet sans nom"},
        "chaîne nu non dict",
    ]}
    assert _inventaire(fiche) == [{"nom": "Sac à dos", "qte": 1}]


# --------------------------------------------------------------------------- #
#  _save_fiche : auto-réparation avant validation
# --------------------------------------------------------------------------- #
try:
    import jsonschema  # noqa: F401
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

needs_schema = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema non installé — validation fiche inactive côté backend",
)


def _ctx(tmp_path: Path) -> ToolContext:
    fiches_src = ROOT / "server" / "data" / "fiches" / "schema_fiche.json"
    fiches_dir = tmp_path / "fiches"
    fiches_dir.mkdir(parents=True, exist_ok=True)
    (fiches_dir / "schema_fiche.json").write_bytes(fiches_src.read_bytes())
    return ToolContext(partie_id="t", data_dir=str(tmp_path), joueur="Test")


def _fiche_base() -> dict:
    return {
        "nom": "Barouk", "race": "Demi-orc", "classe": "Barbare", "niveau": 1,
        "carac": {"FOR": 18, "DEX": 13, "CON": 17, "INT": 16, "SAG": 10, "CHA": 8},
        "pv": 18, "pv_max": 18, "ca": 15,
        "sauvegardes": {"Vigueur": 5, "Reflexes": 1, "Volonte": 0},
        "bab": 1,
    }


@needs_schema
def test_save_fiche_autorèpare_equipement_malformé(tmp_path: Path) -> None:
    """Le cas réel : equipement remplacé par [nom-porte-clé]. La sauvegarde
    doit passer et produire des entrées canoniques {nom, qte}."""
    ctx = _ctx(tmp_path)
    fiche = _fiche_base()
    fiche["equipement"] = [MALFORMEE]
    path = _save_fiche(ctx, "Barouk", fiche)
    import json
    ecrite = json.loads(Path(path).read_text(encoding="utf-8"))
    assert ecrite["equipement"] == [{"nom": "clé de fer rouillee", "qte": 1}]


@needs_schema
def test_save_fiche_conserve_equipement_canonique(tmp_path: Path) -> None:
    """Une fiche de formulaire (équipement déjà propre) n'est pas retouchée."""
    ctx = _ctx(tmp_path)
    fiche = _fiche_base()
    fiche["equipement"] = [{"nom": "Hache à deux mains", "qte": 1}]
    path = _save_fiche(ctx, "Barouk", fiche)
    import json
    ecrite = json.loads(Path(path).read_text(encoding="utf-8"))
    assert ecrite["equipement"] == [{"nom": "Hache à deux mains", "qte": 1}]


@needs_schema
def test_schema_rejette_item_equipement_sans_nom(tmp_path: Path) -> None:
    """Garde directe : le validateur (draft-07) refuse un item sans `nom` —
    le 2e essai du modèle n'aurait plus dû passer sans l'auto-réparation."""
    import json
    import jsonschema
    schema = json.loads(
        (ROOT / "server" / "data" / "fiches" / "schema_fiche.json")
        .read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft7Validator(schema)
    fiche = _fiche_base()
    fiche["equipement"] = [MALFORMEE]
    erreurs = [e for e in validator.iter_errors(fiche)
               if "equipement" in {str(p) for p in e.absolute_path}]
    assert erreurs, "le schéma doit exiger 'nom' dans les items d'équipement"
