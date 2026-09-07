"""Verrou « 1 déplacement de donjon par tour de joueur ».

Le joueur demande « j'entre dans le donjon » : le groupe doit rester dans
la salle d'entrée, même si le modèle enchaîne `carte_donjon_entrer` PUIS
`carte_donjon_explorer` (ou deux `explorer`) dans le même tour. Hors tour
(`tour_id` vide), les tools restent libres (usages REST / tests).

Usage : py -m pytest tests/test_donjon_un_mouvement_par_tour.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    carte_donjon_entrer,
    carte_donjon_explorer,
)
from server.tools.cartes import _MOUVEMENTS_TOUR  # noqa: E402

PID = "test_verrou_mvt"


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_verrou_")


def _ctx_tour(d: str, tour: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d, tour_id=tour)


def _position(d: str) -> list:
    donjon = PartyState(data_dir=d, partie_id=PID).load().get("donjon", {})
    return list(donjon.get("courant", [0, 0]))


def setup_function(func) -> None:
    _MOUVEMENTS_TOUR.pop(PID, None)


def test_entrer_puis_explorer_refuse_dans_le_meme_tour():
    """« J'entre dans le donjon » + appel parasite `explorer` : le groupe
    reste en (0,0), le tool renvoie un refus explicite."""
    d = _fresh_dir()
    try:
        tour = "tour-abc"
        r = asyncio.run(carte_donjon_entrer(_ctx_tour(d, tour), "Crypte du Verrou"))
        assert "🚪" in r.text, r.text
        assert _position(d) == [0, 0]

        r2 = asyncio.run(carte_donjon_explorer(_ctx_tour(d, tour), "nord"))
        assert "UN SEUL" in r2.text, r2.text
        assert _position(d) == [0, 0], "le groupe a bougé malgré le verrou !"
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_deux_explorer_refuses_dans_le_meme_tour():
    """Deux `explorer` successifs au même tour : le second est refusé
    (le donjon a été entré au tour précédent)."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx_tour(d, "tour-0"), "Crypte Double"))
        tour = "tour-def"
        r1 = asyncio.run(carte_donjon_explorer(_ctx_tour(d, tour), "nord"))
        assert "🚶" in r1.text, r1.text
        pos1 = _position(d)
        assert pos1 != [0, 0]

        r2 = asyncio.run(carte_donjon_explorer(_ctx_tour(d, tour), "est"))
        assert "UN SEUL" in r2.text, r2.text
        assert _position(d) == pos1, "deuxième déplacement du même tour !"
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_nouveau_tour_deverrouille_le_deplacement():
    """Au tour suivant (tour_id différent), explorer redevient possible."""
    d = _fresh_dir()
    try:
        tour1, tour2 = "tour-1", "tour-2"
        asyncio.run(carte_donjon_entrer(_ctx_tour(d, tour1), "Crypte Nouveau Tour"))
        refus = asyncio.run(carte_donjon_explorer(_ctx_tour(d, tour1), "nord"))
        assert "UN SEUL" in refus.text, refus.text

        r2 = asyncio.run(carte_donjon_explorer(_ctx_tour(d, tour2), "nord"))
        assert "🚶" in r2.text, r2.text
        assert _position(d) != [0, 0]
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_hors_tour_les_tools_restent_libres():
    """`tour_id` vide (REST, tests, scripts) : aucun changement de comportement."""
    d = _fresh_dir()
    try:
        ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        asyncio.run(carte_donjon_entrer(ctx, "Crypte Libre"))
        r1 = asyncio.run(carte_donjon_explorer(ctx, "nord"))
        assert "🚶" in r1.text, r1.text
        r2 = asyncio.run(carte_donjon_explorer(ctx, "est"))
        assert "🚶" in r2.text, r2.text
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)
