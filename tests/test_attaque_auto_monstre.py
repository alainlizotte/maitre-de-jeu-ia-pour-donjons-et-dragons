"""Tests de l'auto-attaque des monstres : quand un tour de monstre n'a pas été
résolu par le LLM, le serveur joue lui-même l'attaque (déterminisme + tools),
au lieu de re-demander au modèle (qui bloque encore souvent à ce stade).

Usage : py -m pytest tests/test_attaque_auto_monstre.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.main import _attaque_auto_monstre  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_auto"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ScriptedClient:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        content = self.replies.pop(0) if self.replies else "(fin)"
        return ChatResult(content=content, tool_calls=[], finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        yield ""

    async def ensure_model_loaded(self) -> bool:
        return True


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_auto_")


def _etat(d: str) -> dict:
    p = os.path.join(d, f"partie_{PID}.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _setup(d: str) -> None:
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    from server.game.state import PartyState  # noqa: PLC0415
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["pj"] = [{
        "nom": "Brunhild", "joueur": "alain", "race": "Nain",
        "classe": "Guerrier", "niveau": 1, "pv": 12, "pv_max": 12,
        "ca": 16, "conditions": [],
    }]
    etat["initiative"] = [{"nom": "Gobelin", "init": 15},
                          {"nom": "Brunhild", "init": 10}]
    etat["courant_tour_pour"] = "Gobelin"
    etat["monstres_combat"] = [{
        "nom": "Gobelin", "pv": 6, "pv_max": 6, "ca": 15, "conditions": [],
    }]
    st.save(etat)
    # Fiche du PJ (l'attaque auto applique les dégâts via la fiche).
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_brunhild.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Brunhild", "pv": 12, "pv_max": 12, "ca": 16,
                   "bab": 1, "carac": {"FOR": 14, "DEX": 12}}, f,
                  ensure_ascii=False)


def _narration_seule(message: str, d: str):
    """Rejoue le cas bloqué : le LLM narre sans appeler d'outil."""
    orch = Orchestrator(client=ScriptedClient([message]), tools=TOOLS,
                        tool_mode="auto", detect_simulation=True,
                        max_iterations=4)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    result = asyncio.run(orch.run(
        [Message(role="system", content="Tu es le MJ.")], ctx))
    return orch, ctx, result


# --------------------------------------------------------------------------- #
def test_extrait_arme_bonus():
    from server.main import _extrait_arme_bonus  # noqa: PLC0415
    assert _extrait_arme_bonus("Cimeterre +2 (corps à corps)") == ("Cimeterre", 2)
    assert _extrait_arme_bonus("arc court +3 (distance)") == ("arc court", 3)
    assert _extrait_arme_bonus("1 attaque corpo") is None
    assert _extrait_arme_bonus("") is None


def test_tour_monstre_bloque_est_joue_automatiquement():
    d = _fresh_dir()
    try:
        _setup(d)
        orch, ctx, result = _narration_seule(
            "Le gobelin s'avance en grinçant des dents.", d)

        random.seed(6)  # jet déterministe : 1d20=19 → touché, puis 1d6+1
        auto = asyncio.run(_attaque_auto_monstre(
            orch, result, ctx, "Gobelin", None))

        assert auto, "le monstre doit produire une résolution mécanique"
        noms = [t["name"] for t in result.tool_calls_trace]
        assert "lancer_attaque" in noms
        assert "Gobelin" in auto and "Brunhild" in auto

        touche = ("✅ **Touché**" in auto) or ("⭐ **20 naturel**" in auto)
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_brunhild.json"),
            encoding="utf-8"))
        if touche:
            assert "lancer_degats" in noms
            assert "fiche_perso_infliger_degats" in noms
            assert "Dégâts infligés" in auto
            assert fiche["pv"] < 12
        else:
            print("(raté) — le jet auto a manqué, pas de dégâts attendus")

        # Avance du tour vers le PJ ensuite.
        tr = asyncio.run(orch.execute_tool_direct(
            "tour_suivant_combat", {}, ctx, None, result))
        assert tr is not None
        assert _etat(d)["courant_tour_pour"] == "Brunhild"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ennemi_detruit_pas_auto_attaque():
    d = _fresh_dir()
    try:
        _setup(d)
        etat = _etat(d)
        etat["monstres_combat"][0]["conditions"] = ["Détruit"]
        from server.game.state import PartyState  # noqa: PLC0415
        PartyState(data_dir=d, partie_id=PID).save(etat)

        orch, ctx, result = _narration_seule("Lentement, la mêlée s'éteint.", d)
        auto = asyncio.run(_attaque_auto_monstre(
            orch, result, ctx, "Gobelin", None))
        assert auto == "", "un monstre Détruit ne doit pas attaquer"
        assert "lancer_attaque" not in [t["name"]
                                        for t in result.tool_calls_trace]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_aucun_pj_vivant_pas_auto_attaque():
    d = _fresh_dir()
    try:
        _setup(d)
        etat = _etat(d)
        etat["pj"][0]["pv"] = -15
        etat["pj"][0]["conditions"] = ["Mort"]
        from server.game.state import PartyState  # noqa: PLC0415
        PartyState(data_dir=d, partie_id=PID).save(etat)

        orch, ctx, result = _narration_seule("Le silence retombe.", d)
        auto = asyncio.run(_attaque_auto_monstre(
            orch, result, ctx, "Gobelin", None))
        assert auto == "", "sans PJ vivant, pas d'attaque auto"
    finally:
        shutil.rmtree(d, ignore_errors=True)