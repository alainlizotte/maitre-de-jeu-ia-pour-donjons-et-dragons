# -*- coding: utf-8 -*-
"""Correctifs combat issus de l'E2E navigateur (partie 48b239fa).

Défaillances réelles observées en jouant au navigateur (2 runs, 46 tours
de chat) :

1. **Dédup trop large** : la signature d'anti-re-engagement ne portait que
   sur (espèce, quantité DEMANDÉE) — les squelettes de l'embuscade
   canonique étaient confondus avec le squelette solitaire déjà vaincu et
   REFUSÉS à jamais (NC-4). La signature inclut désormais la SALLE
   (`donjon.id@x,y|espècexq`).
2. **Résultats d'attaque invisibles** : les jets officiels tournaient
   (`lancer_attaque ok=True`) mais la narration finale ne les reflétait
   jamais — le joueur ne voyait pas son dé (NC-1). Le texte officiel du
   jet est déposé dans `notes_mecaniques` (file qui survit aux rejeux) et
   un filet 5bis-atk l'ajoute à la prose quand elle l'ignore.
3. **Forçage `engager_combat` refusé** : le détecteur « combat narré sans
   engager » re-vérifie désormais l'état VIVANT avant de forcer (un combat
   engagé depuis le début du tour rendait le forçage systématiquement
   refusé — NC-5).

NB : la dilatation de la quantité canonique (« Squelette ×5 » → N selon le
plafond d'équilibre) existait déjà (M6/P9, state.py) — le présent test
documents son comportement réel pour partie solo/duo.

Usage : py -m pytest tests/test_correctifs_combat_e2e48.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

import pytest

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_combat_e2e48"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_c48_")
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


async def tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


async def _creer_pj(d: str, nom: str = "Bargoum") -> None:
    r = await tool(
        d, "fiche_perso_creer_rapide",
        nom=nom, race="Demi-orc", classe="Guerrier", niveau=1,
        joueur="alain",
        carac_texte="For 16, Dex 14, Con 11, Int 9, Sag 13, Cha 9",
    )
    assert r.text.startswith("✅"), r.text


def _sauver_etat(d: str, etat: dict) -> None:
    PartyState(data_dir=d, partie_id=PID).save(etat)


def _etat_base(salle: tuple[int, int], ennemis: list[str]) -> dict:
    """État minimal : 1 PJ, donjon actif, salle courante canonique."""
    return {
        "phase": "exploration",
        "pj": [{"nom": "Bargoum", "niveau": 1, "pv": 10, "pv_max": 10,
                "joueur": "alain"}],
        "donjon": {
            "id": "Donjon du test 48",
            "courant": list(salle),
            "grille": [
                {"x": salle[0], "y": salle[1], "type": "crypte",
                 "visitee": True, "portes": {"est": True},
                 "ennemis": ennemis},
            ],
        },
        "quete": {"titre": "Test 48", "source": ""},
    }


def _squelettes(etat: dict) -> list[dict]:
    return [m for m in (etat.get("monstres_combat") or [])
            if "quelette" in str(m.get("nom", "")).lower()]


# --------------------------------------------------------------------------- #
#  M6 — dilatation canonique documentée (comportement réel)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_m6_dilate_la_rencontre_canonique():
    """« Squelette » demandé ×1 dans une salle canonique ×5 → la dilatation
    M6 complète au-delà du demandé (plafond d'équilibre pour 1 PJ)."""
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        await _creer_pj(d, "Alba")   # 2 PJ : facteur M6 ≥ 2 créatures
        _etat48 = _etat_base((0, -2), ["Squelette ×5"])
        _etat48["pj"].append({"nom": "Alba", "niveau": 1, "pv": 10,
                              "pv_max": 10, "joueur": "alain"})
        _sauver_etat(d, _etat48)

        r = await tool(d, "engager_combat", monstres="Squelette")
        assert "Combat engag" in r.text, r.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        sq = _squelettes(etat)
        # La dilatation M6 dépasse le ×1 demandé (plafond d'équilibre 2 PJ).
        assert len(sq) >= 2, f"M6 n'a pas dilaté : {len(sq)} squelette(s)"
        # Copies indépendantes (jamais le même dict partagé).
        assert len({id(m) for m in sq}) == len(sq)
        assert all(int(m["pv"]) > 0 for m in sq)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  F2 — dédup : la SALLE fait partie de la signature
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_signature_dedup_comporte_la_salle():
    """La signature écrite dans `historique_engagements` porte la salle :
    `donjon.id@x,y|espècexq` (48b239fa NC-4)."""
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        _sauver_etat(d, _etat_base((0, -2), ["Squelette ×5"]))
        await tool(d, "engager_combat", monstres="Squelette")
        etat = PartyState(data_dir=d, partie_id=PID).load()
        hist = etat.get("historique_engagements") or []
        assert hist, "aucune signature d'engagement écrite"
        assert "Donjon du test 48@0,-2|" in str(hist[-1].get("sig")), hist
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_dedup_signe_par_salle():
    """Un historique d'engagement pour la salle (1,2) ne bloque PAS une
    rencontre identique dans la salle (3,4) — et bloque toujours la même
    salle dans la fenêtre de temps."""
    d = _fresh_dir()
    try:
        await _creer_pj(d)

        # a) Salle A (1,2) : historique récent → refus (comportement conservé).
        etat_a = _etat_base((1, 2), ["Squelette ×5"])
        etat_a["historique_engagements"] = [{
            "sig": "Donjon du test 48@1,2|squelettex1",
            "ts": time.time(),
        }]
        _sauver_etat(d, etat_a)
        r_a = await tool(d, "engager_combat", monstres="Squelette")
        assert r_a.text.startswith("♻️"), (
            "le dédup même-salle doit refuser : " + r_a.text[:150])

        # b) Salle B (3,4) : même espèce, AUTRE salle → autorisé.
        etat_b = _etat_base((3, 4), ["Squelette ×5"])
        etat_b["historique_engagements"] = [{
            "sig": "Donjon du test 48@1,2|squelettex1",
            "ts": time.time(),
        }]
        _sauver_etat(d, etat_b)
        r_b = await tool(d, "engager_combat", monstres="Squelette")
        assert "Combat engag" in r_b.text, (
            "le dédup ne doit PAS bloquer une autre salle : " + r_b.text[:150])
        assert len(_squelettes(PartyState(
            data_dir=d, partie_id=PID).load())) >= 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_dedup_hors_donjon_inchange():
    """Hors donjon (signature « monde ») : le dédup historique continue de
    fonctionner (rencontres de voyage, tavernes…)."""
    d = _fresh_dir()
    try:
        await _creer_pj(d)
        etat = _etat_base((0, -2), [])          # salle sans ennemis canoniques
        etat["donjon"] = {}                     # pas de donjon → « monde »
        etat["historique_engagements"] = [{
            "sig": "monde|squelettex5",
            "ts": time.time(),
        }]
        _sauver_etat(d, etat)
        r = await tool(d, "engager_combat",
                       monstres="Squelette, Squelette, Squelette, "
                                "Squelette, Squelette")
        assert r.text.startswith("♻️"), (
            "le dédup hors donjon doit refuser : " + r.text[:150])
    finally:
        shutil.rmtree(d, ignore_errors=True)
