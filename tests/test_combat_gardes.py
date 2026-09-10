"""Gardes de combat + spirale de corrections.

Deux bugs réels (partie fa4e7366, combat contre 3 squelettes) :

1. Les relances correctives du tour (anti-simulation puis anti-répétition)
   faisaient ré-appliquer les dégâts sur un monstre DÉJÀ DÉTRUIT : le tool
   `fiche_perso_infliger_degats` savait qu'il était mort (« ☠️ déjà détruit »)
   mais appliquait quand même — un Squelette 3 PV s'est retrouvé à -57.
2. Après une correction de simulation, la relance légitimement RE-NARRE la
   même scène avec les vrais chiffres des tools ; mais l'anti-répétition
   (D1ter) comparait cette narration à la copie INVALIDE que la correction
   venait d'ajouter au contexte → écho détecté → correction 2 → 3 → boucle
   épuisée, tour perdu.

Usage : py -m pytest tests/test_combat_gardes.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.fiches import _infliger_degats_monstre  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")


# --------------------------------------------------------------------------- #
#  Garde « monstre déjà détruit »
# --------------------------------------------------------------------------- #
def _etat_combat() -> dict:
    return {
        "phase": "combat",
        "tour": 2,
        "initiative": [
            {"nom": "Squelette", "init": 9},
            {"nom": "Barouk", "init": 5},
        ],
        "courant_tour_pour": "Barouk",
        "pj": [{"nom": "Barouk", "pv": 12, "pv_max": 18}],
        "monstres_combat": [
            {"nom": "Squelette", "pv": 0, "pv_max": 3, "ca": 15,
             "conditions": ["Détruit"]},
            {"nom": "Squelette (2)", "pv": 3, "pv_max": 3, "ca": 15,
             "conditions": []},
        ],
    }


def test_degats_sur_monstre_detruit_refuses():
    tmp = tempfile.mkdtemp()
    ctx = ToolContext(partie_id="t_combat", joueur="alain", data_dir=tmp)
    PartyState(data_dir=tmp, partie_id="t_combat").save(_etat_combat())

    r = _infliger_degats_monstre(ctx, "Squelette", 12)
    assert r is not None
    assert "DÉTRUIT" in r.text
    # Aucune écriture : le cadavre reste à 0 PV.
    etat = PartyState(data_dir=tmp, partie_id="t_combat").load()
    assert etat["monstres_combat"][0]["pv"] == 0
    # Le message oriente vers les cibles vivantes.
    assert "Squelette (2)" in r.text


def test_degats_sur_monstre_vivant_appliques():
    tmp = tempfile.mkdtemp()
    ctx = ToolContext(partie_id="t_combat", joueur="alain", data_dir=tmp)
    PartyState(data_dir=tmp, partie_id="t_combat").save(_etat_combat())

    r = _infliger_degats_monstre(ctx, "Squelette (2)", 5)
    assert r is not None
    assert "DÉTRUIT" in r.text          # freshly killed by these 5 damage
    etat = PartyState(data_dir=tmp, partie_id="t_combat").load()
    assert etat["monstres_combat"][1]["pv"] == -2


def test_tous_morts_message_cloture():
    tmp = tempfile.mkdtemp()
    ctx = ToolContext(partie_id="t_combat", joueur="alain", data_dir=tmp)
    etat = _etat_combat()
    for m in etat["monstres_combat"]:
        m["pv"], m["conditions"] = 0, ["Détruit"]
    PartyState(data_dir=tmp, partie_id="t_combat").save(etat)

    r = _infliger_degats_monstre(ctx, "Squelette", 3)
    assert r is not None
    assert "TOUS les ennemis sont détruits" in r.text


# --------------------------------------------------------------------------- #
#  Récap combat : les ennemis réels sont injectés (anti-hallucination)
# --------------------------------------------------------------------------- #
def test_recap_combat_liste_ennemis_reels():
    """Bug fa4e7366 : le récap ne contenait PAS `monstres_combat` — le MJ
    inventait un « squelette géant » absent de l'initiative et le combat ne
    se concluait plus. Le récap doit lister ennemis, PV et cibles valides."""
    from dataclasses import replace

    from server.config import PathsConfig, load_config
    from server.llm.prompt_builder import PromptBuilder

    tmp = tempfile.mkdtemp()
    PartyState(data_dir=tmp, partie_id="t_combat").save(_etat_combat())
    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=tmp,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    etat = PartyState(data_dir=tmp, partie_id="t_combat").load()
    recap = PromptBuilder(cfg).build_recap(etat)

    assert "Ennemis engagés" in recap
    assert "Squelette (2) : 3/3 PV" in recap
    assert "☠️ DÉTRUIT" in recap                     # le cadavre est marqué
    assert "Cibles VALIDES : Squelette (2)" in recap  # le vivant est nommé
    assert "squelette géant" not in recap.lower()


# --------------------------------------------------------------------------- #
#  Anti-répétition : les narrations corrigées ne comptent plus comme référence
# --------------------------------------------------------------------------- #
SCENE_INVALIDE = (
    "Votre hache à deux mains siffle dans l'air et trouve sa cible ! "
    "Le squelette encaisse de plein fouet, des os volent en éclats. "
    "Jet d'attaque : 19 — touché !"
)
# Même scène re-narrée APRÈS la correction (avec le chiffre du tool), sans
# aucun motif de simulation détectable.
SCENE_CORRIGEE = (
    "Votre hache à deux mains siffle dans l'air et trouve sa cible ! "
    "Le squelette encaisse de plein fouet, des os volent en éclats. "
    "Le jet de 19 le fait voler en morceaux. **Que faites-vous ?**"
)


class _ClientCorrige:
    """1er appel : narration avec jet en prose (→ correction simulation) ;
    2e appel : la même scène re-narrée proprement (→ ne doit PAS être
    traitée comme répétition)."""

    def __init__(self):
        self.appels = 0

    async def chat(self, messages, tools=None, tool_choice=None, temperature=None):
        self.appels += 1
        content = SCENE_INVALIDE if self.appels == 1 else SCENE_CORRIGEE
        return ChatResult(content=content, tool_calls=[],
                          finish_reason="stop", raw={})

    async def stream_chat(self, messages, tools=None, temperature=None):
        for m in (SCENE_CORRIGEE if self.appels >= 2 else SCENE_INVALIDE).split(" "):
            yield m + " "

    async def ensure_model_loaded(self) -> bool:
        return True


def test_relance_apres_simulation_n_est_pas_une_repetition():
    client = _ClientCorrige()
    orch = Orchestrator(client=client, tools=TOOLS, tool_mode="prompt",
                        detect_simulation=True, max_iterations=6)
    messages = [
        Message(role="system", content="Tu es le MJ."),
        Message(role="user", content="**[alain]** : j'attaque le squelette"),
    ]
    ctx = ToolContext(partie_id="t_repet", joueur="alain",
                      data_dir=tempfile.mkdtemp())
    result = asyncio.run(orch.run(messages, ctx))
    # La seule correction est celle de SIMULATION (itération 1) ; la re-narration
    # de la scène corrigée ne doit PAS déclencher l'anti-répétition.
    assert result.corrections == 1, result.corrections
    assert result.narration.strip() == SCENE_CORRIGEE.strip()


def test_echo_d_un_ancien_tour_reste_detecte():
    """La copie d'une narration d'un tour PRÉCÉDENT (historique réel) est
    toujours détectée et relancée."""
    ancienne = (
        "Vous progressez dans le couloir effondré, la torche crachote. Des "
        "ossements jonchent le sol et une odeur de moisissure flotte. Au "
        "fond, une porte dentelée resistance à vos épaules. Que faites-vous ?"
    )

    class _ClientEcho:
        def __init__(self):
            self.appels = 0

        async def chat(self, messages, tools=None, tool_choice=None,
                       temperature=None):
            self.appels += 1
            content = ancienne if self.appels == 1 else (
                "Le squelette s'effondre en un cliquetis d'os, la hache "
                "plantée dans sa cage thoracique. Le passage est libre. "
                "**Que faites-vous ?**"
            )
            return ChatResult(content=content, tool_calls=[],
                              finish_reason="stop", raw={})

        async def stream_chat(self, messages, tools=None, temperature=None):
            yield "Suite de la scène."

        async def ensure_model_loaded(self) -> bool:
            return True

    client = _ClientEcho()
    orch = Orchestrator(client=client, tools=TOOLS, tool_mode="prompt",
                        detect_simulation=True, max_iterations=6)
    messages = [
        Message(role="system", content="Tu es le MJ."),
        Message(role="assistant", content=ancienne),
        Message(role="user", content="**[alain]** : je passe la porte"),
    ]
    ctx = ToolContext(partie_id="t_echo", joueur="alain",
                      data_dir=tempfile.mkdtemp())
    result = asyncio.run(orch.run(messages, ctx))
    assert result.corrections >= 1
    assert "s'effondre" in result.narration
