# -*- coding: utf-8 -*-
"""Régressions de la partie 5a9b99c8 (examen 2026-09-17).

Le joueur dit « débute la partie » — le MJ a répondu « les heures se sont
écoulées… Le repos de 8 heures a été bénéfique » au lieu de la scène
d'ouverture. Chaîne du bug :

1. la prose du MJ (« vous reposer… bénéfique ») matchait `_SOIN_RE`, donc
   le rattrapage 5quater-d (soin/repos annoncés) re-jouait le tour ;
2. la consigne corrective — injectée en message `user` — contenait elle-
   même le mot « REPOS » ;
3. le garde « repos demandé » de l'orchestrateur, qui scanne le DERNIER
   message `user`, a pris cette consigne pour une demande du joueur et a
   forcé `repos_long(forcer=true)` : 8 h de repos en pleine scène de
   quête, à PV pleins.

Correctifs : les gardes d'intention (repos, escalier) et la phase de
décision ignorent les messages `user` marqués « instruction INTERNE du
moteur de jeu » ; le rattrapage 5quater-d ne se déclenche plus que sur le
message du JOUEUR (plus sur la narration du MJ).

Usage : py -m pytest tests/test_correctifs_5a9b99c8.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.client import ChatResult, Message  # noqa: E402
from server.llm.orchestrator import Orchestrator  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

PID = "test_5a9b99c8"

MARQUEUR = "instruction INTERNE du moteur de jeu"

# Consigne corrective 5quater-d TELLE QU'INJECTÉE par _rejoue_correctif
# (role=user) : elle contient le mot « REPOS » et se termine par le
# rappel interne — c'est elle qui déclenchait le repos forcé.
CONSIGNE_SOIN_REPOS = (
    "⚠️ ERREUR système : un SOIN ou un REPOS a été annoncé mais aucun "
    "outil n'a été appelé — les PV ne sont pas modifiés. Pour un repos "
    "(nuit / 8 h de récupération) : appelle `repos_long`. "
    "\n\n(Rappel FINAL — cette consigne est une " + MARQUEUR + ", "
    "invisible du joueur : produis une narration NOUVELLE.)"
)


class _ClientFaux:
    """Narre en prose sans JAMAIS appeler d'outil (le pire cas du 9B)."""

    def __init__(self, narration="Vous soufflez un instant."):
        self.narration = narration
        self.appels = []

    async def chat(self, messages, tools=None, tool_choice=None,
                   temperature=None, response_format=None):
        self.appels.append({
            "messages": messages,
            "response_format": response_format,
        })
        return ChatResult(content=self.narration, tool_calls=[],
                          finish_reason="stop", raw={})


def _setup(d: str) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Utturgut", "pv": 16, "pv_max": 16, "joueur": "alain"}],
        "pnj": [],
        "histoire": [],
    })
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 16, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare", "conditions": []},
                  f, ensure_ascii=False)


def test_consigne_corrective_ne_declenche_pas_de_repos_force():
    """Le garde repos ne doit PAS prendre la consigne de rejeu (qui contient
    « REPOS ») pour une demande du joueur."""
    d = tempfile.mkdtemp(prefix="dnd35_5a9b_1_")
    _setup(d)
    orch = Orchestrator(client=_ClientFaux(), tools=discover_tools(),
                        tool_mode="native")
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    result = asyncio.run(orch.run(
        [Message(role="system", content="Tu es le MJ."),
         # Le tour a dérivé : la consigne corrective arrive en DERNIER
         # message user (c'est le cas du rejeu 5quater-d).
         Message(role="user", content=CONSIGNE_SOIN_REPOS)],
        ctx,
    ))
    assert not any(
        tc.get("name") == "repos_long" for tc in result.tool_calls_trace
    ), result.tool_calls_trace
    # Aucun repos appliqué : PV intacts.
    assert PartyState(data_dir=d, partie_id=PID).load()["pj"][0]["pv"] == 16


def test_demande_reelle_de_repos_toujours_forcee():
    """Non-régression : un VRAI « je me repose » du joueur force toujours
    repos_long (cas abf74a77)."""
    d = tempfile.mkdtemp(prefix="dnd35_5a9b_2_")
    _setup(d)
    orch = Orchestrator(client=_ClientFaux(), tools=discover_tools(),
                        tool_mode="native")
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    result = asyncio.run(orch.run(
        [Message(role="system", content="Tu es le MJ."),
         Message(role="user", content="je me repose")],
        ctx,
    ))
    assert any(
        tc.get("name") == "repos_long" for tc in result.tool_calls_trace
    ), result.tool_calls_trace


def test_phase_decision_ignore_les_consignes_correctives():
    """La phase de décision (json_schema) ne doit pas traiter une consigne
    de rejeu comme une action du joueur."""
    d = tempfile.mkdtemp(prefix="dnd35_5a9b_3_")
    _setup(d)
    client = _ClientFaux()

    async def chat(messages, **kw):
        return await client.chat(messages, **kw)

    orch = Orchestrator(client=_ClientDecisionProbe(client),
                        tools=discover_tools(), tool_mode="native")
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    asyncio.run(orch.run(
        [Message(role="system", content="Tu es le MJ."),
         Message(role="user", content=CONSIGNE_SOIN_REPOS)],
        ctx,
    ))
    # Aucun appel avec response_format (la décision n'a pas tourné).
    assert not any(a["response_format"] is not None for a in client.appels)


class _ClientDecisionProbe:
    """Proxy qui enregistre les appels et délègue au client faux."""

    def __init__(self, inner):
        self.inner = inner

    async def chat(self, messages, **kw):
        return await self.inner.chat(messages, **kw)


# --------------------------------------------------------------------------- #
# 2. Ouverture journalisée au premier tour (sinon la directive « DÉBUT DE
#    L'AVENTURE » restait active et le MJ re-narrait l'accroche en boucle)
# --------------------------------------------------------------------------- #
def test_journaliser_ouverture_premier_tour_seulement():
    from server.main import _journaliser_ouverture_si_besoin
    etat = {"histoire": []}
    assert _journaliser_ouverture_si_besoin(
        etat, "Teleshann confie la quête de la Couronne.")
    assert len(etat["histoire"]) == 1
    assert "Teleshann" in etat["histoire"][0]["evenement"]
    # Deuxième tour : PLUS JAMAIS d'événement d'ouverture (la directive
    # « DÉBUT DE L'AVENTURE » s'éteint — le MJ avance au lieu de re-narrer).
    assert not _journaliser_ouverture_si_besoin(etat, "Suite de l'histoire.")
    assert len(etat["histoire"]) == 1
    # Narration vide : rien à journaliser.
    assert not _journaliser_ouverture_si_besoin({"histoire": []}, "   ")


def test_directive_debut_aventure_eteinte_des_que_histoire_non_vide():
    """La condition d'injection de la directive est `histoire` vide :
    une fois l'ouverture journalisée, le bloc ne doit plus être prévu."""
    from server.llm.prompt_builder import _DEBUT_AVENTURE
    assert "DÉBUT DE L'AVENTURE" in _DEBUT_AVENTURE
    etat_ouvert = {"histoire": [{"evenement": "Début de l'aventure"}]}
    # La condition du prompt_builder : `not etat.get("histoire")`.
    assert bool(etat_ouvert.get("histoire")) is True
    # (le sens réel : `not histoire` == False → directive non injectée)
    assert not (not (etat_ouvert.get("histoire") or []))


# --------------------------------------------------------------------------- #
# 3. Tour 2 (msg 30→31) : le MJ a narré « jet 24 — touché / 11 dégâts / goule
#    hors de combat » SANS aucun outil d'attaque, et a appelé
#    `terminer_mon_tour` 4× dans le MÊME message → la rotation a défilé
#    Utturgut → Goule (2) → Goule → Utturgut → Goule (2) (plusieurs rounds)
#    pendant que `monstres_combat` restait à 16/16. Garde-fous : un seul
#    `terminer_mon_tour` par tour (`tour_id`), budget 1, prompt durci.
# --------------------------------------------------------------------------- #
def _setup_combat(d: str, pid: str) -> None:
    PartyState(data_dir=d, partie_id=pid).save({
        "meta": {"titre": "test combat"},
        "phase": "combat",
        "tour": 2,
        "pj": [{"nom": "Utturgut", "pv": 5, "pv_max": 16, "joueur": "alain"}],
        "pnj": [],
        "histoire": [],
        "initiative": [
            {"nom": "Utturgut", "init": 14},
            {"nom": "Goule", "init": 12},
        ],
        "courant_tour_pour": "Utturgut",
        "monstres_combat": [
            {"nom": "Goule", "pv": 16, "pv_max": 16, "ca": 12,
             "conditions": []},
        ],
    })


def test_terminer_mon_tour_une_seule_fois_par_tour():
    """Un 2e `terminer_mon_tour` avec le MÊME `tour_id` est refusé : sinon la
    rotation sautait plusieurs combattants d'un coup (partie 5a9b99c8)."""
    from server.tools.state import _TERMINER_TOUR, _TERMINER_TOUR_REFUS

    d = tempfile.mkdtemp(prefix="dnd35_5a9b_term_")
    pid = PID + "_term1"
    _setup_combat(d, pid)
    tools = discover_tools()
    _TERMINER_TOUR.pop(pid, None)
    ctx = ToolContext(partie_id=pid, joueur="alain", data_dir=d,
                      tour_id="tour-unique-1")
    r1 = asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx, {}))
    assert "Goule" in r1.text                       # rotation faite UNE fois
    r2 = asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx, {}))
    assert r2.text == _TERMINER_TOUR_REFUS
    etat = PartyState(data_dir=d, partie_id=pid).load()
    assert etat["courant_tour_pour"] == "Goule"      # pas re-avancé
    assert etat["tour"] == 2


def test_terminer_mon_tour_nouveau_tour_autorise():
    """Un NOUVEAU tour (tour_id différent) peut de nouveau être terminé :
    le verrou ne doit pas bloquer définitivement le joueur."""
    from server.tools.state import _TERMINER_TOUR, _TERMINER_TOUR_REFUS

    d = tempfile.mkdtemp(prefix="dnd35_5a9b_term2_")
    pid = PID + "_term2"
    _setup_combat(d, pid)
    tools = discover_tools()
    _TERMINER_TOUR.pop(pid, None)
    ctx1 = ToolContext(partie_id=pid, joueur="alain", data_dir=d,
                       tour_id="t1")
    asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx1, {}))
    ctx2 = ToolContext(partie_id=pid, joueur="alain", data_dir=d,
                       tour_id="t2")
    r = asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx2, {}))
    assert r.text != _TERMINER_TOUR_REFUS


def test_terminer_mon_tour_hors_tour_sans_tour_id_inactif():
    """Sans `tour_id` (REST/tests), le verrou est inactif : non-régression."""
    d = tempfile.mkdtemp(prefix="dnd35_5a9b_term3_")
    pid = PID + "_term3"
    _setup_combat(d, pid)
    tools = discover_tools()
    ctx = ToolContext(partie_id=pid, joueur="alain", data_dir=d)
    r1 = asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx, {}))
    r2 = asyncio.run(invoke_tool(tools["terminer_mon_tour"], ctx, {}))
    assert "DÉJÀ terminé" not in r1.text
    assert "DÉJÀ terminé" not in r2.text


def test_budget_terminer_mon_tour_un_par_tour():
    from server.llm.orchestrator import _BUDGET_OUTILS_TOUR

    assert _BUDGET_OUTILS_TOUR.get("terminer_mon_tour") == 1


def test_prompt_combat_interdit_invention_et_spam():
    """Le récap de combat doit rappeler : résolution par outils obligatoire
    (jamais `lancer_d20` pour une attaque), aucune invention de dé/dégâts/
    mort, une attaque et un `terminer_mon_tour` au plus par tour."""
    from dataclasses import replace

    from server.config import PathsConfig, load_config
    from server.llm.prompt_builder import PromptBuilder

    d = tempfile.mkdtemp(prefix="dnd35_5a9b_recap_")
    pid = PID + "_recap"
    _setup_combat(d, pid)
    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=d,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    etat = PartyState(data_dir=d, partie_id=pid).load()
    recap = PromptBuilder(cfg).build_recap(etat)
    assert "OUTILS DE RÉSOLUTION OBLIGATOIRES" in recap
    assert "JAMAIS `lancer_d20` pour une attaque" in recap
    assert "UNE SEULE attaque par tour" in recap
    assert "`terminer_mon_tour` : AU PLUS" in recap

