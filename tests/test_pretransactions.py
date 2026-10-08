# -*- coding: utf-8 -*-
"""Pré-exécution des transactions au démarrage du tour (2dfa9c75).

L'intention explicite du joueur (« j'achète… », « je vends… », « Repas
médiocre ») est exécutée PAR LE SERVEUR AVANT l'appel LLM — le modèle narre
ensuite le résultat officiel (à la façon des combats : l'initiative précède
la prose). Tests : le routage achat/auberge/vente et les garde-fous.

Usage : py -m pytest tests/test_pretransactions.py -q
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (           # noqa: E402
    _extraire_achat,
    _intention_achat,
)
from server import equipement_phb               # noqa: E402

_ARTICLES = list(equipement_phb.articles())


def test_preexecution_route_bloquee_si_transaction_deja_reussie():
    """Le rattrapage post-narration se SAUTE quand la pré-exécution a déjà
    réussi (✅ dans la trace) — pas d'achat en double."""
    from server.llm import orchestrator as orch
    src = inspect.getsource(orch)
    i = src.find("Pré-exécution des TRANSACTIONS")
    assert i > 0, "la pré-exécution doit exister"
    # et le rattrapage post-narration garde son skip ✅.
    j = src.find("rattrapage DÉTERMINISTE des ACHATS")
    bloc = src[j:j + 4200]
    assert 'in _txt_r[:20] and "DÉJÀ" not in' in bloc or \
        '"✅" in (tc.get("text") or "")' in bloc, (
        "le skip ✅ du rattrapage a disparu")


def test_preexecution_injecte_dans_work():
    """La pré-exécution insère le résultat + la consigne « ne rappelle pas »
    dans `work` AVANT l'appel LLM (le modèle narre le résultat officiel)."""
    from server.llm import orchestrator as orch
    src = inspect.getsource(orch)
    i = src.find("TRANSACTION DÉJÀ EXÉCUTÉE par le serveur")
    assert i > 0
    j = src.rfind("work.append", 0, i)
    assert j > 0, "le résultat doit être ajouté à work avant l'appel LLM"


def _extraire(msg):
    return _extraire_achat(_intention_achat([msg]) or msg, _ARTICLES) \
        if _intention_achat([msg]) else {}


def test_routage_complet_achat_auberge_vente():
    """Les trois routes de transaction s'extraient correctement."""
    a1 = _extraire("j'achète 50 flèches")
    assert a1.get("type") == "marche" and "Flèches (10)" in str(
        a1.get("article")), a1
    a2 = _extraire("Repas médiocre")
    assert a2.get("type") == "auberge" and a2.get("repas") == "mediocre", a2
    a3 = _extraire("je vends mes rations")
    assert a3.get("type") == "vente", a3
