"""Tests — validation JSON Schema des fiches personnages D&D 3.5.

Validate que `_save_fiche` accepte une fiche conforme et rejette (ValueError)
une fiche invalide (champ manquant / type erronné). Le schéma draft-07 vit à
`server/data/fiches/schema_fiche.json`.

USAGE
-----
    py -m pytest tests/test_fiches_schema.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.tools.base import ToolContext        # noqa: E402
from server.tools.fiches import _save_fiche      # noqa: E402

try:
    import jsonschema  # noqa: F401
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


# skip global si jsonschema n'est pas installé (skip silencieux du schéma).
pytestmark = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema non installé — validation fiche inactive côté backend",
)


# --------------------------------------------------------------------------- #
#  Contexte de test — data_dir temporaire sous tests/.
# --------------------------------------------------------------------------- #
@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    # On référencie le schema_fiche.json du dépôt via _fiches_dir : le helper
    # cherche `schema_fiche.json` dans <data_dir>/fiches/. On symlinke (ou copie).
    fiches_src = ROOT / "server" / "data" / "fiches" / "schema_fiche.json"
    fiches_dir = tmp_path / "fiches"
    fiches_dir.mkdir(parents=True, exist_ok=True)
    (fiches_dir / "schema_fiche.json").write_bytes(fiches_src.read_bytes())
    return ToolContext(partie_id="t", data_dir=str(tmp_path), joueur="Test")


# --------------------------------------------------------------------------- #
#  Fiche conforme — doit être écrite sans erreur.
# --------------------------------------------------------------------------- #
def test_fiche_valide_ecrite(ctx: ToolContext, tmp_path: Path) -> None:
    fiche = {
        "nom": "Groth",
        "joueur": "Test",
        "race": "Demi-orque",
        "classe": "Barbare",
        "niveau": 1,
        "carac": {"FOR": 17, "DEX": 13, "CON": 16, "INT": 10, "SAG": 9, "CHA": 8},
        "pv": 12, "pv_max": 12, "ca": 15,
        "sauvegardes": {"Vigueur": 4, "Reflexes": 1, "Volonte": 0},
        "bab": 1,
        "competences": {}, "dons": [], "equipement": [],
        "or": 0, "alignement": "CN", "histoire": "",
        "conditions": [],
    }
    path = _save_fiche(ctx, "Groth", fiche)
    assert Path(path).is_file(), "la fiche valide doit être écrite sur disque"


# --------------------------------------------------------------------------- #
#  Fiche invalide — doit lever ValueError (champ obligatoire manquant).
# --------------------------------------------------------------------------- #
def test_fiche_sans_niveau_rejetee(ctx: ToolContext) -> None:
    fiche = {
        "nom": "SansNiv", "race": "Humain", "classe": "Guerrier",
        # "niveau": absent (required)
        "carac": {"FOR": 14, "DEX": 12, "CON": 14, "INT": 10, "SAG": 11, "CHA": 10},
        "pv": 10, "pv_max": 10, "ca": 14,
        "sauvegardes": {"Vigueur": 3, "Reflexes": 0, "Volonte": 0},
        "bab": 1,
    }
    with pytest.raises(ValueError, match="niveau"):
        _save_fiche(ctx, "SansNiv", fiche)


def test_fiche_carac_manquante_rejetee(ctx: ToolContext) -> None:
    """Il manque FOR dans carac (required par le draft-07)."""
    fiche = {
        "nom": "SansFor", "race": "Humain", "classe": "Guerrier", "niveau": 1,
        "carac": {"DEX": 12, "CON": 14, "INT": 10, "SAG": 11, "CHA": 10},  # FOR absente
        "pv": 10, "pv_max": 10, "ca": 14,
        "sauvegardes": {"Vigueur": 3, "Reflexes": 0, "Volonte": 0},
        "bab": 1,
    }
    with pytest.raises(ValueError, match=r"carac\.FOR|FOR"):
        _save_fiche(ctx, "SansFor", fiche)


def test_fiche_pv_negatif_rejetee(ctx: ToolContext) -> None:
    """pv_max doit être minimum 1."""
    fiche = {
        "nom": "Mort", "race": "Humain", "classe": "Guerrier", "niveau": 1,
        "carac": {"FOR": 14, "DEX": 12, "CON": 14, "INT": 10, "SAG": 11, "CHA": 10},
        "pv": 0, "pv_max": 0, "ca": 14,            # pv_max: 0 < 1
        "sauvegardes": {"Vigueur": 3, "Reflexes": 0, "Volonte": 0},
        "bab": 1,
    }
    with pytest.raises(ValueError, match="pv_max"):
        _save_fiche(ctx, "Mort", fiche)


def test_fiche_pv_negatifs_acceptes(ctx: ToolContext) -> None:
    """Règles officielles Injury and Death 3.5 : les PV descendent sous 0
    (mourant entre -1 et -9, mort à -10). Le schéma doit les accepter."""
    for pv in (-1, -5, -9, -10):
        fiche = {
            "nom": f"Agonisant{abs(pv)}", "race": "Humain",
            "classe": "Guerrier", "niveau": 1,
            "carac": {"FOR": 14, "DEX": 12, "CON": 14, "INT": 10,
                      "SAG": 11, "CHA": 10},
            "pv": pv, "pv_max": 12, "ca": 14,
            "sauvegardes": {"Vigueur": 3, "Reflexes": 0, "Volonte": 0},
            "bab": 1,
        }
        path = _save_fiche(ctx, fiche["nom"], fiche)
        assert Path(path).is_file(), f"pv={pv} doit être accepté (règle 3.5)"
