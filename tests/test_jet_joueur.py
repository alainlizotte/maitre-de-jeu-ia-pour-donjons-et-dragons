# -*- coding: utf-8 -*-
"""Filet JET-JOUEUR (R7) : détection, bonus fiche, sauvegarde/compétence."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = Path(__file__).resolve().parents[1]


def test_detecte_sauvegarde():
    from server.main import _jet_detecte
    assert _jet_detecte(
        "Le gaz vert se répand ! Roran, jet de sauvegarde de Réflexes !") \
        == ("sauvegarde", "reflexes")
    assert _jet_detecte(
        "Le poison attaque ton corps : sauvegarde de Vigueur ou tu tombes.") \
        == ("sauvegarde", "vigueur")
    assert _jet_detecte("Test de Volonté contre la peur !") \
        == ("sauvegarde", "volonte")
    # Jet DÉJÀ résolu dans la narration → pas de demande.
    assert _jet_detecte(
        "Tu réussis ta sauvegarde de Réflexes et esquives le souffle.")
    assert _jet_detecte("Vous échouez votre jet de Vigueur : -3 PV.")


def test_detecte_competence():
    from server.main import _jet_detecte
    assert _jet_detecte("Test de Discrétion pour passer inaperçu.") \
        == ("competence", "Discrétion")
    assert _jet_detecte("Jet de Perception auditive pour écouter la porte.") \
        == ("competence", "Perception auditive")
    assert _jet_detecte("Fouille : rien à signaler.") is None


def test_jet_resolu_sauvegarde_bonus_fiche():
    import asyncio
    import json
    import tempfile
    import shutil
    from types import SimpleNamespace
    from server.game.state import PartyState
    from server.tools.base import ToolContext

    d = tempfile.mkdtemp(prefix="gruumsh_roll_")
    try:
        st = PartyState(data_dir=d, partie_id="rj")
        st.save({
            "phase": "exploration",
            "courant_tour_pour": "Brunhild",
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 10}],
            "jet_attendu": {
                "personnage": "Brunhild", "joueur": "J1",
                "categorie": "sauvegarde", "type": "vigueur",
                "ts": "2026-10-14T00:00:00",
            },
        })
        fdir = Path(d) / "fiches"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "fiche_brunhild.json").write_text(json.dumps(
            {"nom": "Brunhild", "classe": "Guerrier", "niveau": 1,
             "sauvegardes": {"Vigueur": 5, "Reflexes": 1, "Volonte": 0},
             "pv": 10, "pv_max": 12}, ensure_ascii=False), encoding="utf-8")

        # Session factice : capture broadcasts + historique.
        class FakeSession:
            broadcasts: list = []

            async def broadcast(self, msg):
                self.broadcasts.append(msg)

            def remember_player_message(self, player, text):
                self.broadcasts.append({"type": "remember", "player": player,
                                        "text": text})

        fs = FakeSession()
        ok = asyncio.run(__import__("server.main", fromlist=["_jet_resoudre"])
                         ._jet_resoudre(fs, "rj", "J1", data_dir=d))
        assert ok
        jet = [b for b in fs.broadcasts if b.get("type") == "jet_resultat"][0]
        assert jet["bonus"] == 5, jet          # bonus officiel depuis la fiche
        assert 1 <= jet["d20"] <= 20
        assert jet["total"] == jet["d20"] + 5
        assert "Vigueur" in jet["texte"]
        # jet_attendu vidé.
        assert not (st.load().get("jet_attendu"))
        # Injecté dans l'historique pour l'arbitrage du MJ.
        rem = [b for b in fs.broadcasts if b.get("type") == "remember"]
        assert rem and "5" in rem[0]["text"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_jet_resolu_competence_rangs_carac():
    import asyncio
    import json
    import tempfile
    import shutil
    from server.game.state import PartyState

    d = tempfile.mkdtemp(prefix="gruumsh_roll2_")
    try:
        st = PartyState(data_dir=d, partie_id="rj2")
        st.save({
            "phase": "exploration",
            "pj": [{"nom": "Roran", "joueur": "Sacha"}],
            "jet_attendu": {
                "personnage": "Roran", "joueur": "Sacha",
                "categorie": "competence", "type": "Discrétion",
                "ts": "2026-10-14T00:00:00",
            },
        })
        fdir = Path(d) / "fiches"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "fiche_roran.json").write_text(json.dumps(
            {"nom": "Roran", "classe": "Rodeur", "niveau": 1,
             "carac": {"FOR": 14, "DEX": 15},
             "competences": {"Discrétion": 4},
             "pv": 9, "pv_max": 9}, ensure_ascii=False), encoding="utf-8")

        class FakeSession:
            broadcasts: list = []

            async def broadcast(self, msg):
                self.broadcasts.append(msg)

            def remember_player_message(self, player, text):
                self.broadcasts.append({"type": "remember", "text": text})

        fs = FakeSession()
        ok = asyncio.run(__import__("server.main", fromlist=["_jet_resoudre"])
                         ._jet_resoudre(fs, "rj2", "Sacha", data_dir=d))
        assert ok
        jet = [b for b in fs.broadcasts if b.get("type") == "jet_resultat"][0]
        # rangs 4 + DEX (15) +2 = +6
        assert jet["bonus"] == 6, jet
        assert "Discrétion" in jet["texte"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
