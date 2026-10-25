# -*- coding: utf-8 -*-
"""Régressions des correctifs #2, #4, #5, #6 (demande joueur).

- #2 : le canon monde agrège les DONJONS ARCHIVÉS — après
  `carte_donjon_sortir` (grille vidée), les ennemis du manifeste
  restent engageables hors donjon (le ch02 : kobolds/bandits refusés
  « hors module » à tort pendant le voyage).
- #4 : immunités à la sournoise (mort-vivant / plante / vase /
  animation — le `type` du bestiaire fait foi).
- #5 : le FLANC est une règle de mêlée — impossible avec une arme à
  distance (la sournoise sur cible surprise reste valable).
- #6 : « Encaissement » (nom FR 3.5 de Toughness) → +3 PV à la création.

Usage : py -m pytest tests/test_correctifs_1a7.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.fiches import _bonus_dons_pv, fiche_perso_creer_rapide  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402
from server.tools.state import _ennemis_manifeste  # noqa: E402
from server.main import _des_sournoise_det  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = discover_tools()

_ROUBLARD = {"classe": "Roublard", "niveau": 1}


# ------------------------------------------------------------------ #
# #2 — canon monde : donjons archivés
# ------------------------------------------------------------------ #

def test_canon_monde_agrege_les_donjons_archives():
    """Après sortie (grille vidée), les ennemis du manifeste archivé
    restent engageables hors donjon."""
    etat = {
        "donjon": {"id": None, "grille": [], "courant": []},
        "donjons_exploreres": {
            "divers_shadows_over_the_moonsea": {
                "id": "divers_shadows_over_the_moonsea",
                "grille": [
                    {"x": 0, "y": 2, "ennemis": ["Kobold ×6", "Bandit ×5"]},
                    {"x": 0, "y": 0, "ennemis": ["Loup terrible ×1"]},
                ],
            },
        },
        "quete": {"bible": {"ennemis": ["Acolyte", "Nécromancien rouge"]}},
    }
    canon = _ennemis_manifeste(etat)
    noms = [c.split("×")[0].strip().lower() for c in canon]
    assert "kobold" in noms, canon
    assert "bandit" in noms, canon
    assert "loup terrible" in noms, canon
    # Et le canon B25 monde accepte le Kobold.
    from server.tools.state import _ennemis_canoniques_salle
    from server.tools.fiches import _espece_cle as _ec
    _salle_canon = _ennemis_canoniques_salle(etat)
    _cles = {_ec(e.split("×")[0].strip()) for e in canon}
    assert _ec("Kobold") in _cles


# ------------------------------------------------------------------ #
# #4 — immunités à la sournoise
# ------------------------------------------------------------------ #

def test_sournoise_immunite_mort_vivant():
    """Un squelette (Mort-vivant) n'a pas de zone vitale : 0 dé."""
    from server.tools.base import ToolContext as _TC
    ctx = _TC(partie_id="t", joueur="t", data_dir=os.path.join(
        _REPO, "server", "data"))
    etat = {"surpris": ["Squelette"]}
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Squelette", etat, flanc=False,
        arme="Épée courte", ctx=ctx) == 0
    # L'immunité PRIME sur le flanc (un mort-vivant n'a pas de zone vitale).
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Squelette", {}, flanc=True,
        arme="Épée courte", ctx=ctx) == 0


def test_sournoise_vivant_surpris():
    from server.tools.base import ToolContext as _TC
    ctx = _TC(partie_id="t", joueur="t", data_dir=os.path.join(
        _REPO, "server", "data"))
    etat = {"surpris": ["Gobelin"]}
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Gobelin", etat, flanc=False,
        arme="Épée courte", ctx=ctx) == 1


# ------------------------------------------------------------------ #
# #5 — pas de flanc à distance
# ------------------------------------------------------------------ #

def test_pas_de_flanc_a_distance():
    etat = {}
    # Arc court + flanc → 0 (le flanc est une règle de mêlée).
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Gobelin", etat, flanc=True,
        arme="Arc court") == 0
    # Épée courte (mêlée) + flanc → 1.
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Gobelin", etat, flanc=True,
        arme="Épée courte") == 1
    # Surpris à l'arc → valable (≤ 9 m supposé, distance non suivie).
    assert _des_sournoise_det(
        dict(_ROUBLARD), "Gobelin", {"surpris": ["Gobelin"]}, flanc=False,
        arme="Arc court") == 1


# ------------------------------------------------------------------ #
# #6 — « Encaissement » = +3 PV
# ------------------------------------------------------------------ #

def test_encaissement_don_pv():
    assert _bonus_dons_pv(["Encaissement"], 1) == 3
    assert _bonus_dons_pv(["Dur à cuire"], 1) == 3
    assert _bonus_dons_pv(["Vigueur surhumaine"], 3) == 3
    assert _bonus_dons_pv(["Alerte"], 1) == 0


def test_fiche_roublard_encaissement_pv():
    """Création complète : don Encaissement → PV_max inclut +3."""
    async def _run():
        d = tempfile.mkdtemp(prefix="enc_pv_")
        try:
            ctx = ToolContext(partie_id="enc", joueur="e2e", data_dir=d)
            r = await invoke_tool(_TOOLS["fiche_perso_creer_rapide"], ctx, {
                "nom": "AuroTest", "race": "Humaine", "classe": "Clerc",
                "niveau": 1, "joueur": "e2e",
                "carac_texte": "FOR 12, DEX 10, CON 14, INT 10, SAG 16, "
                               "CHA 12",
                "dons_texte": "Encaissement"})
            assert r.text.startswith("✅"), r.text[:120]
            fiche = _load_fiche_dir(d)
            # Clerc 1 : d8 (8) + CON +2 = 10, +3 Encaissement = 13.
            assert int(fiche.get("pv_max", 0)) == 13, fiche.get("pv_max")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def _load_fiche_dir(d: str):
    import io as _io
    import json as _json
    return _json.load(_io.open(
        os.path.join(d, "fiches", "fiche_aurotest.json"),
        encoding="utf-8"))


def _load_fiche_dir(d: str):
    import io as _io
    import json as _json
    return _json.load(_io.open(
        os.path.join(d, "fiches", "fiche_aurotest.json"),
        encoding="utf-8"))


# ------------------------------------------------------------------ #
# #8 — seuil d'inertie selon la phase
# ------------------------------------------------------------------ #

from server.llm.prompt_builder import _bloc_inertie  # noqa: E402


def test_inertie_combat_ancrage_immediat():
    """En combat, UN SEUL tour sans tool → ré-ancrage (les monstres
    frappent quand même : chaque round perdu est un round subi)."""
    etat = {"phase": "combat", "inertie_mj": 1,
            "courant_tour_pour": "Brandt"}
    bloc = _bloc_inertie(etat)
    assert "INERTIE" in bloc
    assert "Brandt" in bloc


def test_inertie_exploration_seuil_2():
    assert _bloc_inertie({"phase": "exploration", "inertie_mj": 1}) == ""
    assert "INERTIE" in _bloc_inertie(
        {"phase": "exploration", "inertie_mj": 2})


# ------------------------------------------------------------------ #
# #9 — Réprouver déterministe (parseurs)
# ------------------------------------------------------------------ #

from server.main import (  # noqa: E402
    _intention_reprouver_chat,
    _morts_vivants_engages,
)


def test_intention_reprouver():
    oui = ("Aurora réprouve les morts-vivants !",
           "Brandt repousse les morts-vivants de son symbole",
           "Aurora invoque son dieu contre les morts",
           "je turn undead")
    for t in oui:
        assert _intention_reprouver_chat(t), t
    non = ("Aurora frappe le squelette de sa masse",
           "Brandt repousse la porte",
           "Aurora soigne Brandt")
    for t in non:
        assert not _intention_reprouver_chat(t), t


def test_morts_vivants_engages():
    assert _morts_vivants_engages([
        {"nom": "Squelette", "pv": 4, "conditions": []}])
    assert _morts_vivants_engages([
        {"nom": "Gobelin", "pv": 5, "conditions": []},
        {"nom": "Zombie", "pv": 9, "conditions": []}])
    # Mort OU allié : pas déclencheur.
    assert not _morts_vivants_engages([
        {"nom": "Squelette", "pv": 0, "conditions": ["Détruit"]}])
    assert not _morts_vivants_engages([
        {"nom": "Squelette", "pv": 4, "allie": True}])
    assert not _morts_vivants_engages([
        {"nom": "Gobelin", "pv": 5, "conditions": []}])
