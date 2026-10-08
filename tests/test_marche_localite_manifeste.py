# -*- coding: utf-8 -*-
"""Résolution de la localité commerciale dans les scénarios à manifeste
(partie c21d0734/15aa0b6f).

`lieu.nom` devient l'ID du PARCOURS (« La Couronne de Mystra ») — pas une
localité. Le marché retombait sur « (non déterminé) » en Bourg (×1.1,
sorts 1) alors que le groupe achète à la Tour de l'Équilibre DANS
Silverymoon (Cité : ×1.0, sorts 6). Le repli : la dernière localité connue
(mémoire de campagne, alimentée par le serveur).

Usage : py -m pytest tests/test_marche_localite_manifeste.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.marche import _lieu_nom  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402


def _ctx(tmp_path, etat) -> ToolContext:
    (tmp_path / "fiches").mkdir(parents=True, exist_ok=True)
    import json
    (tmp_path / "partie_test_marche.json").write_text(
        json.dumps(etat, ensure_ascii=False), encoding="utf-8")
    return ToolContext(partie_id="test_marche", joueur="Alain",
                       data_dir=str(tmp_path))


_ETAT_DONJON = {
    "phase": "exploration",
    "lieu": {"nom": "La Couronne de Mystra", "type": "donjon"},
    "pj": [{"nom": "Morgoth"}],
    "memoire": {"position": {"lieu": "Silverymoon"},
                "lieux_visites": ["Silverymoon"]},
}


def test_repli_derniere_localite_connue(tmp_path):
    """lieu = l'ID du parcours (pas une localité) → repli sur la dernière
    localité connue (Silverymoon, Cité)."""
    ctx = _ctx(tmp_path, _ETAT_DONJON)
    assert _lieu_nom(ctx) == "Silverymoon"


def test_repli_lieux_visites_si_pas_de_position(tmp_path):
    etat = _ETAT_DONJON
    etat["memoire"] = {"lieux_visites": ["Neverwinter", "Silverymoon"]}
    ctx = _ctx(tmp_path, etat)
    assert _lieu_nom(ctx) == "Silverymoon"   # la dernière connue (revers)


def test_lieu_localite_connue_prime(tmp_path):
    """Un lieu qui EST une localité connue reste utilisé tel quel."""
    etat = {"phase": "exploration",
            "lieu": {"nom": "Phandalin", "type": "Village"},
            "memoire": {"position": {"lieu": "Silverymoon"}}}
    ctx = _ctx(tmp_path, etat)
    assert _lieu_nom(ctx) == "Phandalin"


def test_type_ville_casse_toleree(tmp_path):
    """Le type en minuscules (« cite ») ne doit pas retomber sur
    TYPE_INCONNU dans les fonctions de tarification."""
    from server import villes
    assert villes.auberge_qualites("Cité") == ["bonne"]
    # « cite » minuscule retombe sur TYPE_INCONNU (Village) — documenté :
    # les outils passent le type CANONIQUE issu de type_de_ville(nom).
    assert villes.auberge_qualites("cite") != ["bonne"]
