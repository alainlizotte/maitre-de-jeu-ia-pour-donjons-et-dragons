# -*- coding: utf-8 -*-
"""Test : le jet de compétence demandé à un PJ est DÉVIÉ vers le JOUEUR.

Bug observé (08/11) : le MJ appelait lancer_d20 POUR le PJ — le tool
résolvait le jet (total + verdict affichés, conséquences narrées) puis
jet_demande faisait clignoter le dé APRÈS coup : le jet du joueur
n'avait plus aucun effet.

Nouveau flux : interception AVANT exécution (hors combat) -> le tool ne
tourne pas, jet_attendu est posé avec la DD, le joueur lance son dé
(bouton 🎲), le verdict réussite/échec est calculé au jet.

Usage : py -m pytest tests/test_jet_pj_devie.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.main import _jet_detecte, _jet_poser_attente  # noqa: E402
from server.main import _jet_resoudre  # noqa: E402
from server.tools.base import ToolContext, ToolSpec  # noqa: E402

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


class _FakeSession:
    """Capture les broadcasts (jet_demande / jet_resultat)."""

    def __init__(self):
        self.broadcasts: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)

    def remember_player_message(self, *_a, **_k) -> None:
        pass


def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="jetpj", joueur="e2e", data_dir=tmp)


def _preparer_partie(tmp: str, phase: str = "exploration") -> None:
    """Partie + fiche PJ avec proprietaire."""
    st = PartyState(data_dir=tmp, partie_id="jetpj")
    etat = st.load()
    etat["phase"] = phase
    etat["pj"] = [{"nom": "Flick", "joueur": "pip", "pv": 10, "pv_max": 10}]
    st.save(etat)
    d_fiches = os.path.join(tmp, "fiches")
    os.makedirs(d_fiches, exist_ok=True)
    fiche = {
        "nom": "Flick", "niveau": 1, "proprietaire": "pip",
        "ca": 13, "pv": 10, "pv_max": 10,
        "carac": {"FOR": 8, "DEX": 17, "CON": 12, "INT": 14, "SAG": 10,
                  "CHA": 12},
        "competences": {"Fouille": 3},
        "sauvegardes": {"Reflexes": 3, "Vigueur": 0, "Volonte": 0},
    }
    with open(os.path.join(d_fiches, "fiche_flick.json"), "w",
              encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False)


class _Res:
    tool_calls_trace: list = []


def _spec(nom: str) -> ToolSpec:
    return ToolSpec(name=nom, func=None,  # type: ignore[arg-type]
                    signature=None,  # type: ignore[arg-type]
                    docstring="", is_async=False)


# ------------------------------------------------------------------ #
# 1. Le garde _jet_detecte : blocs déjà résolus
# ------------------------------------------------------------------ #


def test_bloc_piege_resolu_n_est_pas_une_demande():
    """« ✅ Throk'mar : total 21 vs DD 10 → réussite » + en-têtes de piège
    = jet DÉJÀ résolu : _jet_detecte renvoie None."""
    blocs = [
        "🛡️ Sauvegarde (Reflexes) DD 10 par personnage :\n\n"
        "- ✅ Throk'mar : total 21 vs DD 10 → réussite.",
        "🪤 **PIÈGE RÉSOLU PAR LE SERVEUR** (emprunt de l'escalier) : "
        "Escaliers branlants : Dextérité DD 10.",
        "ℹ️ Jets RÉSOLUS par le serveur (vrais résultats ci-dessus).",
    ]
    for b in blocs:
        assert _jet_detecte(b) is None, b[:80]
    # une vraie demande passe toujours
    assert _jet_detecte("Fouillez la pièce (jet de Fouille DD 15).") == (
        "competence", "Fouille")


# ------------------------------------------------------------------ #
# 2. L'interception orchestrator
# ------------------------------------------------------------------ #


def test_interception_devie_le_jet_vers_le_joueur():
    """lancer_d20 pour un PJ hors combat : NON exécuté, jet_attendu posé
    (avec la DD), résultat 'Jet demandé AU JOUEUR'."""

    async def _run():
        d = tempfile.mkdtemp(prefix="jetpj_")
        try:
            _preparer_partie(d)
            from server.llm.orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            ctx = _ctx(d)
            res = _Res()
            tr = await orch._intercepter_jet_pj(
                _spec("lancer_d20"), ctx,
                {"nom_personnage": "Flick", "competence": "Fouille",
                 "difficulte": 15, "raison": "Fouille du sol"},
                res)
            assert tr is not None and "AU JOUEUR" in tr.text, tr.text[:150]
            # pas de jet serveur dans le résultat
            assert "1d20=" not in tr.text, tr.text[:150]
            # l'état porte l'attente avec la DD
            etat = PartyState(data_dir=d, partie_id="jetpj").load()
            att = etat.get("jet_attendu") or {}
            assert att.get("personnage") == "Flick", att
            assert att.get("categorie") == "competence", att
            assert att.get("type") == "Fouille", att
            assert att.get("difficulte") == 15, att
            assert att.get("joueur") == "pip", att
            assert not att.get("diffuse"), att
            # trace écrite
            assert res.tool_calls_trace, "trace manquante"
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_interception_refuse_le_deuxieme_appel():
    """Un 2e lancer_d20 dans le même tour : refus SANS exécution (sinon le
    serveur résolvait le jet malgré l'attente)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="jetpj2_")
        try:
            _preparer_partie(d)
            from server.llm.orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            ctx = _ctx(d)
            res = _Res()
            tr1 = await orch._intercepter_jet_pj(
                _spec("lancer_d20"), ctx,
                {"nom_personnage": "Flick", "competence": "Fouille",
                 "difficulte": 15}, res)
            assert tr1 is not None
            tr2 = await orch._intercepter_jet_pj(
                _spec("lancer_d20"), ctx,
                {"nom_personnage": "Flick", "competence": "Discrétion",
                 "difficulte": 12}, res)
            assert tr2 is not None and "DÉJÀ ATTENDU" in tr2.text, (
                tr2.text[:150] if tr2 else "None")
            assert "1d20=" not in tr2.text, tr2.text[:150]
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


def test_interception_laisse_tourner_pnj_et_combat():
    """PNJ (fiche sans proprietaire) et phase combat : exécution normale."""

    async def _run():
        d = tempfile.mkdtemp(prefix="jetpj3_")
        try:
            _preparer_partie(d)
            # PNJ : fiche sans proprietaire
            d_fiches = os.path.join(d, "fiches")
            with open(os.path.join(d_fiches, "fiche_gobelin.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"nom": "Gobelin", "niveau": 1, "pv": 6}, f)
            from server.llm.orchestrator import Orchestrator
            orch = Orchestrator.__new__(Orchestrator)
            ctx = _ctx(d)
            assert await orch._intercepter_jet_pj(
                _spec("lancer_d20"), ctx,
                {"nom_personnage": "Gobelin", "difficulte": 10},
                _Res()) is None
            # combat : sauvegarde serveur (affichée par design)
            st = PartyState(data_dir=d, partie_id="jetpj")
            etat = st.load()
            etat["phase"] = "combat"
            st.save(etat)
            assert await orch._intercepter_jet_pj(
                _spec("lancer_sauvegarde"), ctx,
                {"nom_personnage": "Flick", "type_sauvegarde": "Reflexes",
                 "difficulte": 12}, _Res()) is None
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 3. La diffusion de l'attente posée mid-tour
# ------------------------------------------------------------------ #


def test_attente_mid_tour_diffusee_une_seule_fois():
    """jet_attendu posé par l'interception (sans flag diffuse) : le 1er
    appel à _jet_poser_attente diffuse jet_demande, le 2e ne duplique pas."""

    async def _run():
        d = tempfile.mkdtemp(prefix="jetpj4_")
        try:
            _preparer_partie(d)
            st = PartyState(data_dir=d, partie_id="jetpj")
            etat = st.load()
            etat["jet_attendu"] = {
                "personnage": "Flick", "joueur": "pip",
                "categorie": "competence", "type": "Fouille",
                "difficulte": 15,
            }
            st.save(etat)
            sess = _FakeSession()
            await _jet_poser_attente(sess, "jetpj", "Fouillez le sol.",
                                     data_dir=d)
            await _jet_poser_attente(sess, "jetpj", "Fouillez le sol.",
                                     data_dir=d)
            demandes = [b for b in sess.broadcasts
                        if b.get("type") == "jet_demande"]
            assert len(demandes) == 1, len(demandes)
            assert demandes[0]["personnage"] == "Flick"
            assert demandes[0]["type_jet"] == "Fouille"
            assert "DD 15" in demandes[0]["detail"], demandes[0]["detail"]
            etat = PartyState(data_dir=d, partie_id="jetpj").load()
            assert etat["jet_attendu"].get("diffuse") is True
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())


# ------------------------------------------------------------------ #
# 4. Le verdict au jet (DD stockée)
# ------------------------------------------------------------------ #


def test_verdict_reussite_et_echec_au_jet():
    """_jet_resoudre calcule le verdict vs la DD stockée (le jet du joueur
    décide du sort de l'action)."""

    async def _run():
        d = tempfile.mkdtemp(prefix="jetpj5_")
        try:
            _preparer_partie(d)
            # DD 1 → toujours réussite ; DD 40 → toujours échec.
            for dd, attendu in ((1, "réussite"), (40, "échec")):
                st = PartyState(data_dir=d, partie_id="jetpj")
                etat = st.load()
                etat["jet_attendu"] = {
                    "personnage": "Flick", "joueur": "pip",
                    "categorie": "competence", "type": "Fouille",
                    "difficulte": dd,
                }
                st.save(etat)
                sess = _FakeSession()
                ok = await _jet_resoudre(sess, "jetpj", "pip", data_dir=d)
                assert ok, dd
                res = [b for b in sess.broadcasts
                       if b.get("type") == "jet_resultat"]
                assert res, dd
                txt = str(res[-1].get("texte") or "")
                assert attendu in txt, (dd, txt)
                assert f"DD {dd}" in txt, (dd, txt)
                # l'attente est consommée
                etat = PartyState(data_dir=d, partie_id="jetpj").load()
                assert not (etat.get("jet_attendu")), dd
        finally:
            shutil.rmtree(d, ignore_errors=True)
    asyncio.run(_run())
