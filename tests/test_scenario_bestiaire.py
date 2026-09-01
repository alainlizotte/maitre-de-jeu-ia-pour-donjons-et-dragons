"""Tests — les monstres d'un scénario sont garantis présents au bestiaire.

Couvre :
  - `_noms_monstres_scenario` : collecte les noms du dossier artwork.monstres
    du catalogue, dédoublonnés et sans entrées vides ;
  - `_assurer_monstres_au_bestiaire` : ajoute (via `_generer_monstre_generique`)
    les monstres absents, mais saute ceux déjà présents ou résolubles (alias
    humain inclus) ;
  - la nouvelle fiche est persistée dans `bestiaire.json` du contexte.

Usage : py -m pytest tests/test_scenario_bestiaire.py -q
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext  # noqa: E402
from server.tools.scenarios import (  # noqa: E402
    _assurer_monstres_au_bestiaire, _noms_monstres_scenario,
)

PID = "test_scenario_bestiaire"


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_scenbest_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _write_bestiaire(d: str, with_gobelin: bool = True) -> None:
    raw = {"_meta": {"nb_monstres": 1 if with_gobelin else 0}}
    if with_gobelin:
        raw["gobelin"] = {
            "nom": "Gobelin", "cle": "gobelin", "type": "gobelin",
            "taille": "P", "pv": 4, "pv_max": 4, "ca": 13, "fp": "1/3",
        }
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def _write_catalogue(d: str, scenario: dict) -> None:
    cata = {"universes": [{"nom": "Test", "scenarios": [scenario]}]}
    with open(os.path.join(d, "scenarios_catalogue.json"), "w", encoding="utf-8") as f:
        json.dump(cata, f, ensure_ascii=False, indent=2)


def _load_bestiaire_disk(d: str) -> dict:
    with open(os.path.join(d, "bestiaire.json"), encoding="utf-8") as f:
        return json.load(f)


def test_noms_monstres_scenario_dedoublonne_vides():
    s = {"artwork": {"monstres": [
        {"nom": "Gobelin"}, {"nom": "  "}, {}, {"nom": "Gobelin"},
        {"nom": "Dragonnet rouge"},
    ]}}
    noms = _noms_monstres_scenario(s)
    assert noms == ["Gobelin", "Dragonnet rouge"]
    # Aucun artwork → liste vide.
    assert _noms_monstres_scenario({}) == []
    assert _noms_monstres_scenario({"artwork": {}}) == []


def test_assurer_monstres_ajoute_absents_saute_presents():
    d = _fresh_dir()
    try:
        _write_bestiaire(d, with_gobelin=True)
        _write_catalogue(d, {"artwork": {"monstres": [
            {"nom": "Gobelin"},          # déjà présent → sauté
            {"nom": "Dragonnet rouge"},  # absent → ajouté
        ]}})
        ajoutes = _assurer_monstres_au_bestiaire(
            _ctx(d), ["Gobelin", "Dragonnet rouge"])
        assert ajoutes == ["Dragonnet rouge"]
        disk = _load_bestiaire_disk(d)
        assert "dragonnet_rouge" in disk
        assert disk["dragonnet_rouge"]["nom"] == "Dragonnet rouge"
        assert disk["dragonnet_rouge"].get("generique") is True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_assurer_monstres_saute_alias_humain():
    d = _fresh_dir()
    try:
        # La fiche canonique « garde » est présente → « Garde · de la ville »
        # se résout via l'alias humain générique et n'est PAS re-ajoutée.
        _write_bestiaire(d, with_gobelin=False)
        with open(os.path.join(d, "bestiaire.json"), "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw["garde"] = {"nom": "Garde", "cle": "garde", "type": "humanoïde",
                        "taille": "M", "pv": 8, "pv_max": 8, "ca": 14, "fp": "1/2"}
        with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        ajoutes = _assurer_monstres_au_bestiaire(_ctx(d), ["Garde de la ville"])
        assert ajoutes == []
        disk = _load_bestiaire_disk(d)
        assert "garde" in disk  # toujours la fiche canonique, pas de doublon
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_assurer_monstres_rien_si_liste_vide():
    d = _fresh_dir()
    try:
        _write_bestiaire(d, with_gobelin=False)
        assert _assurer_monstres_au_bestiaire(_ctx(d), []) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)
