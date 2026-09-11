"""Pièges mécaniques des salles — application serveur des sauvegardes.

Régression observée en partie 54de40ed (Dues for the Dead) : le champ
`piege` des salles (« Escaliers branlants : Dextérité DD 10 par personnage
sous peine de chute (1d6 dégâts) et de vacarme ») était injecté AU MJ comme
texte narratif, mais aucun jet n'était jamais résolu : le piège n'existait
que dans la prose. Désormais :
- `carte_donjon_etage` résout le piège de la salle de départ ET d'arrivée ;
- `carte_donjon_explorer` résout le piège de la salle dans laquelle on
  ENTRE (sauf salles escaliers : leur piège se déclenche à l'usage) ;
- les pièges à COMPÉTENCE (« crochetage DD 15 », « détection DD 15 »)
  restent narratifs (résolus à l'initiative du MJ).

Usage : py -m pytest tests/test_pieges_salles.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, _TOOL_REGISTRY, invoke_tool  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    _MOUVEMENTS_TOUR,
    _parser_piege_mecanique,
)
from server.tools.fiches import _chemin  # noqa: E402

PID = "test_pieges_salles"

PIEGE_ESCALIERS = (
    "Escaliers branlants (zone 6) : Dextérité DD 10 par personnage "
    "sous peine de chute (1d6 dégâts) et de vacarme."
)
PIEGE_PUITS = (
    "Pente glissante d'ossements : chute dans la fosse (Dextérité DD 10) "
    "— pas de monstre, mais bruit et fouille possible."
)
PIEGE_GRILLES = (
    "Grilles verrouillées (crochetage DD 15, forcage Force DD 15) ; "
    "alarme des gardes du cimetière si forcées bruyamment."
)

# Géométrie : entrée (0,0) → puits (0,-1) → crypte (0,-2) → escaliers (0,-3)
_SALLES = [
    {
        "x": 0, "y": 0, "type": "entrée", "visitee": True,
        "portes": {"nord": True, "sud": False, "est": False, "ouest": False},
    },
    {
        "x": 0, "y": -1, "type": "puits", "visitee": False,
        "piege": PIEGE_PUITS,
        "portes": {"nord": True, "sud": True, "est": False, "ouest": False},
    },
    {
        "x": 0, "y": -2, "type": "crypte", "visitee": False,
        "piege": PIEGE_GRILLES,
        "portes": {"nord": True, "sud": True, "est": False, "ouest": False},
    },
    {
        "x": 0, "y": -3, "type": "escaliers", "visitee": True,
        "piege": PIEGE_ESCALIERS,
        "portes": {"nord": False, "sud": True, "est": False, "ouest": False},
    },
]


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_pieges_")


def _partie(d: str, courant: tuple = (0, -3)) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Barbouk", "pv": 15}],
        "pnj": [],
        "donjon": {
            "id": "Donjon Test",
            "grille": [dict(s) for s in _SALLES],
            "salles_visitees": ["0,0", "0,-3"],
            "portes_bloquees": [],
            "courant": list(courant),
            "etage": 0,
            "etages": {},
            "arrivee_par": "nord",
        },
        "histoire": [],
    })


def _fiche(d: str) -> None:
    """Fiche minimale de Barbouk : PV 15, Réflexes +2 (recoupement officiel)."""
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    path = _chemin(ctx, "Barbouk")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "nom": "Barbouk",
            "pv": 15,
            "pv_max": 15,
            "conditions": [],
            "sauvegardes": {"Reflexes": 2, "Vigueur": 1, "Volonte": 0},
        }, f, ensure_ascii=False)


def _position(d: str) -> list:
    donjon = PartyState(data_dir=d, partie_id=PID).load().get("donjon", {})
    return list(donjon.get("courant", [0, 0]))


def _pv(d: str) -> int:
    etat = PartyState(data_dir=d, partie_id=PID).load()
    for p in etat.get("pj", []):
        if str(p.get("nom", "")).lower() == "barbouk":
            return int(p.get("pv", 0))
    return -999


_ORIG_RANDINT = random.randint


def _des_fixes(seq: list[int]) -> None:
    """Remplace `random.randint` (module partagé dice/cartes) par une file
    déterministe ; file épuisée → renvoie la borne haute (comportement
    « toujours réussir » sans StopIteration)."""
    it = iter(seq)
    random.randint = lambda a, b: next(it, b)  # type: ignore[assignment]


def _des_reels() -> None:
    random.randint = _ORIG_RANDINT


def setup_function(func) -> None:
    _MOUVEMENTS_TOUR.pop(PID, None)


def teardown_function(func) -> None:
    _des_reels()


# --------------------------------------------------------------------------- #
#  Parseur
# --------------------------------------------------------------------------- #

def test_parser_pieges_mecaniques():
    p = _parser_piege_mecanique(PIEGE_ESCALIERS)
    assert p is not None and p["cle_sauv"] == "Reflexes", p
    assert p["dd"] == 10 and p["degats"] == (1, 6) and p["vacarme"] is True, p

    p = _parser_piege_mecanique(PIEGE_PUITS)
    assert p is not None and p["cle_sauv"] == "Reflexes" and p["dd"] == 10, p
    assert p["degats"] is None and p["vacarme"] is True, p

    # Pièges à COMPÉTENCE / caractéristique : jamais interceptés.
    assert _parser_piege_mecanique(PIEGE_GRILLES) is None
    assert _parser_piege_mecanique(
        "Glyphe sur la couronne (détection DD 15, désamorçage DD 13).") is None
    assert _parser_piege_mecanique(
        "Trappes et objets piégés : attaque au bruit qui réveille la "
        "sentinelle.") is None
    assert _parser_piege_mecanique("Aucun — salle de jonction.") is None
    assert _parser_piege_mecanique("") is None


# --------------------------------------------------------------------------- #
#  carte_donjon_etage : usage de l'escalier piégé
# --------------------------------------------------------------------------- #

def test_etage_echec_inflige_degats():
    """Échec à la sauvegarde → 1d6 dégâts appliqués + vacarme, étage changé."""
    d = _fresh_dir()
    try:
        _partie(d)
        _fiche(d)
        _des_fixes([1, 3])         # jet de sauvegarde = 1 (échec auto), 1d6 = 3
        tr = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_etage"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"direction": "descendre"},
        ))
        assert not tr.text.startswith(("❌", "🚫")), tr.text
        donjon = PartyState(data_dir=d, partie_id=PID).load()["donjon"]
        assert donjon["etage"] == 1, donjon["etage"]
        assert "PIÈGE RÉSOLU PAR LE SERVEUR" in tr.text, tr.text
        assert "ÉCHEC" in tr.text, tr.text
        assert "3 dégâts" in tr.text, tr.text
        assert "VACARME" in tr.text, tr.text
        assert _pv(d) == 12, _pv(d)
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_etage_reussite_pas_de_degats():
    d = _fresh_dir()
    try:
        _partie(d)
        _fiche(d)
        _des_fixes([20])           # 20 naturel → réussite auto
        tr = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_etage"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"direction": "descendre"},
        ))
        assert not tr.text.startswith(("❌", "🚫")), tr.text
        assert "réussite" in tr.text, tr.text
        assert "VACARME" not in tr.text, tr.text
        assert _pv(d) == 15, _pv(d)
        donjon = PartyState(data_dir=d, partie_id=PID).load()["donjon"]
        assert donjon["etage"] == 1, donjon["etage"]
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


# --------------------------------------------------------------------------- #
#  carte_donjon_explorer : piège à l'entrée d'une salle
# --------------------------------------------------------------------------- #

def test_explorer_puits_piege_sans_formule_degats():
    """Pente glissante (Dextérité DD 10, pas de dés de dégâts) : échec →
    chute + vacarme narratifs, AUCUN dégât mécanique."""
    d = _fresh_dir()
    try:
        _partie(d, courant=(0, -2))       # depuis la crypte, puits (0,-1) au sud
        _fiche(d)
        _des_fixes([5])            # jet 5 + 2 (fiche) = 7 < 10 → échec
        tr = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_explorer"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"direction": "sud"},   # NORD = y-1 dans ce moteur
        ))
        assert not tr.text.startswith(("❌", "🚫")), tr.text
        assert _position(d) == [0, -1], _position(d)
        assert "PIÈGE RÉSOLU PAR LE SERVEUR" in tr.text, tr.text
        assert "ÉCHEC" in tr.text, tr.text
        assert "VACARME" in tr.text, tr.text
        assert "💥" not in tr.text, "aucune formule de dégâts dans ce piège"
        assert _pv(d) == 15, _pv(d)
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_explorer_salle_escaliers_pas_de_double_piege():
    """(Ré)entrer dans la salle escaliers ne déclenche PAS son piège : celui-ci
    se résout à l'USAGE (`carte_donjon_etage`) — sinon double taxation."""
    d = _fresh_dir()
    try:
        _partie(d)
        _fiche(d)
        _des_fixes([20])           # réussite auto à chaque jet résolu
        ctx = lambda: ToolContext(partie_id=PID, joueur="alain", data_dir=d)
        tr_desc = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_etage"], ctx(),
            {"direction": "descendre"},
        ))
        assert "PIÈGE RÉSOLU" in tr_desc.text       # départ (0,-3) piégé
        tr_montee = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_etage"], ctx(),
            {"direction": "monter"},
        ))
        assert "PIÈGE RÉSOLU" in tr_montee.text     # arrivée (0,-3) piégée
        # Sortir au sud puis RE-ENTRER dans la salle escaliers : pas de jet.
        tr_sud = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_explorer"], ctx(),
            {"direction": "sud"},
        ))
        assert _position(d) == [0, -2], _position(d)
        tr_entree = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_explorer"], ctx(),
            {"direction": "nord"},
        ))
        assert _position(d) == [0, -3], _position(d)
        assert "PIÈGE RÉSOLU" not in tr_entree.text, tr_entree.text
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)


def test_explorer_grilles_pas_de_jet_serveur():
    """Piège à compétence (« crochetage DD 15, forcage Force DD 15 ») :
    laissé au MJ, AUCUN jet serveur à l'entrée."""
    d = _fresh_dir()
    try:
        _partie(d)
        _fiche(d)
        _des_fixes([1])
        tr = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_explorer"],
            ToolContext(partie_id=PID, joueur="alain", data_dir=d),
            {"direction": "sud"},          # escaliers (0,-3) → crypte (0,-2)
        ))
        assert not tr.text.startswith(("❌", "🚫")), tr.text
        assert _position(d) == [0, -2], _position(d)
        assert "PIÈGE RÉSOLU" not in tr.text, tr.text
        assert "🪤 Piège :" in tr.text, tr.text   # contenu canonique intact
    finally:
        _MOUVEMENTS_TOUR.pop(PID, None)
