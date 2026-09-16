"""Game over + trame de scénario + garde de séquence du voyage.

Suite à l'analyse de la partie 87b8f286 (The Crown of Mystra) :
1. TPK → flag `game_over` (la partie ne repart plus en exploration comme
   si de rien n'était) + bloc 💀 injecté au MJ (flag OU tous PJ morts) ;
2. `donjon.etapes` (manifeste) → trame injectée au récap avec statuts ;
3. `voyage_demarrer` refuse de sauter une étape à salle non accomplie
   (échappatoire : `forcer=true` pour un choix explicite de la table).

Usage : py -m pytest tests/test_gameover_trame.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import PathsConfig, load_config  # noqa: E402
from server.game.combat import ResultatBoucle, cloturer  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.llm.prompt_builder import PromptBuilder  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.cartes import _donjon_depuis_manifeste  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_go_trame"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ETAPES = [
    {"titre": "Récupérer la Couronne", "salle": "4,0",
     "detail": "Prérequis absolu (baguette inopérante sans la couronne)."},
    {"titre": "Réunir les huit gemmes", "detail": "Sarr, Aluarim…"},
]


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_go_")
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


async def tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


def _recap(d: str, etat: dict) -> str:
    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=d,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    return PromptBuilder(cfg).build_recap(etat)


def _etat_dungeon(etapes, visitees=("0,0",)) -> dict:
    salles = [
        {"x": x, "y": y, "type": "salle", "visitee": f"{x},{y}" in visitees,
         "portes": {"nord": False, "sud": False, "est": True, "ouest": False}}
        for x, y in ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0))
    ]
    return {
        "phase": "exploration",
        "pj": [{"nom": "Bargoum", "pv": 12, "pv_max": 12, "conditions": []}],
        "donjon": {
            "id": "Test", "grille": salles,
            "salles_visitees": list(visitees), "courant": [0, 0],
            "etapes": etapes,
        },
    }


# --------------------------------------------------------------------------- #
#  1. Game over
# --------------------------------------------------------------------------- #
def test_cloture_defaite_pose_game_over():
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save({
            "phase": "combat", "pj": [
                {"nom": "Bargoum", "pv": -10, "pv_max": 12,
                 "conditions": ["Mort"]},
            ],
            "monstres_combat": [],
        })
        res = ResultatBoucle()
        asyncio.run(cloturer(_ctx(d), res, "defaite"))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("game_over") is True
        assert res.combat_termine == "defaite"
        assert res.patches[-1].get("game_over") is True
        assert any("GAME OVER" in e for e in res.events)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cloture_victoire_pas_de_game_over():
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save({
            "phase": "combat",
            "pj": [{"nom": "Bargoum", "pv": 12, "pv_max": 12,
                    "conditions": []}],
            "monstres_combat": [
                {"nom": "Rat", "fp": "1/4", "pv": 0, "pv_max": 2,
                 "conditions": ["Détruit"], "allie": False},
            ],
        })
        res = ResultatBoucle()
        asyncio.run(cloturer(_ctx(d), res, "victoire"))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert "game_over" not in etat
        assert "game_over" not in res.patches[-1]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_recap_bloc_game_over_flag_et_pj_morts():
    d = _fresh_dir()
    try:
        # (a) Flag posé par la clôture.
        etat = _etat_dungeon(ETAPES)
        etat["game_over"] = True
        recap = _recap(d, etat)
        assert "GAME OVER" in recap
        assert "Résurrection" in recap or "résurrection" in recap.lower()

        # (b) Détection dynamique : tous les PJ morts SANS flag (mort hors
        # combat — piège, massive damage…).
        etat2 = _etat_dungeon(ETAPES)
        etat2["pj"][0]["pv"] = -10
        etat2["pj"][0]["conditions"] = ["Mort"]
        recap2 = _recap(d, etat2)
        assert "GAME OVER" in recap2

        # (c) PJ vivant sans flag : PAS de bloc.
        recap3 = _recap(d, _etat_dungeon(ETAPES))
        assert "GAME OVER" not in recap3
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  2. Trame du scénario injectée au récap
# --------------------------------------------------------------------------- #
def test_trame_injectee_avec_statuts():
    d = _fresh_dir()
    try:
        recap = _recap(d, _etat_dungeon(ETAPES, visitees=("0,0",)))
        assert "TRAME DU SCÉNARIO" in recap
        assert "Récupérer la Couronne" in recap
        assert "⬜ À FAIRE (salle 4,0)" in recap          # étape 1 en attente
        assert "scenario_etape" in recap                   # consigne de suivi

        # Salle (4,0) visitée → étape 1 ACCOMPLIE.
        etat = _etat_dungeon(ETAPES, visitees=("0,0", "4,0"))
        recap2 = _recap(d, etat)
        assert "✅ ACCOMPLIE" in recap2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_manifeste_etapes_passthrough():
    man = {
        "id": "test", "scenario": "sc_test", "donjon_id": "Donjon Test",
        "etapes": ETAPES,
        "etages": [{
            "nom": "Niveau 1", "entree": [0, 0],
            "salles": [{
                "x": 0, "y": 0, "type": "entrée", "description": "Départ.",
                "portes": {"nord": False, "sud": False, "est": False,
                           "ouest": False},
            }],
        }],
    }
    donjon = _donjon_depuis_manifeste(man)
    assert donjon is not None
    assert donjon.get("etapes") == ETAPES


# --------------------------------------------------------------------------- #
#  3. Garde de séquence sur le voyage
# --------------------------------------------------------------------------- #
async def test_voyage_refuse_si_etape_en_attente():
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save(_etat_dungeon(ETAPES))
        r = await tool(
            d, "voyage_demarrer",
            destination="Ruines de Sarr", distance_km=45.0, terrain="plaine",
        )
        assert "TRAME DU SCÉNARIO" in r.text, r.text
        assert "Récupérer la Couronne" in r.text
        assert "forcer=true" in r.text
        # Aucun voyage persisté.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert not (etat.get("voyage") or {}).get("destination")
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_voyage_passe_apres_etape_acccomplie_ou_avec_forcer():
    d = _fresh_dir()
    try:
        # (a) forcer=true : la table passe outre (voyage calculé).
        PartyState(data_dir=d, partie_id=PID).save(_etat_dungeon(ETAPES))
        r = await tool(
            d, "voyage_demarrer",
            destination="Ruines de Sarr", distance_km=45.0, terrain="plaine",
            forcer=True,
        )
        assert "Voyage vers Ruines de Sarr" in r.text, r.text

        # (b) Étape 1 accomplie (salle 4,0 visitée) : plus de blocage.
        d2 = _fresh_dir()
        try:
            PartyState(data_dir=d2, partie_id=PID).save(
                _etat_dungeon(ETAPES, visitees=("0,0", "4,0")))
            r2 = await tool(
                d2, "voyage_demarrer",
                destination="Ruines de Sarr", distance_km=45.0,
                terrain="plaine",
            )
            assert "Voyage vers Ruines de Sarr" in r2.text, r2.text
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_voyage_sans_trame_inchange():
    """Sans `donjon.etapes`, aucun garde : comportement historique."""
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save(_etat_dungeon(None))
        r = await tool(
            d, "voyage_demarrer",
            destination="Phandalin", distance_km=30.0, terrain="route",
        )
        assert "Voyage vers Phandalin" in r.text, r.text
        assert "TRAME" not in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)
