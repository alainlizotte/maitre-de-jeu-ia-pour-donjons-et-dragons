"""Fidélité au scénario : bible structurée, suivi d'étapes, cohérence
d'édition 3.5, et injection dans le récap du MJ.

Le MJ doit rester sur la trame du scénario choisi : il reçoit à chaque tour
un bloc `SCÉNARIO (bible)` (accroche, niveaux, étapes, objectif) + un
avertissement si le scénario est d'une autre édition (5e) pour qu'il
réinterprète monstres/DD en 3.5 à la portée du groupe.

Usage : py -m pytest tests/test_scenario_bible.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.prompt_builder import PromptBuilder  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.scenarios import (  # noqa: E402
    _detecter_edition,
    _extraire_niveau,
    scenarios_laelith_charger,
    scenario_etape,
)

PID = "test_scenario_bible"


def _setup(tmp: str) -> str:
    """Crée un data_dir isolé avec le catalogue + le PDF Divers pour pouvoir
    charger un scénario réel."""
    d = tempfile.mkdtemp(prefix="dnd35_scen_")
    shutil.copy(
        os.path.abspath("server/data/scenarios_catalogue.json"),
        os.path.join(d, "scenarios_catalogue.json"),
    )
    os.makedirs(os.path.join(d, "scenarios"), exist_ok=True)
    shutil.copytree(
        os.path.abspath("server/data/scenarios/Divers"),
        os.path.join(d, "scenarios", "Divers"),
        dirs_exist_ok=True,
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _make_cfg(d: str):
    """Construit une config pointant le data_dir de test vers `d` (les
    répertoires prompts/sections restent les vrais, non utilisés par
    `build_recap` directement)."""
    from dataclasses import replace
    from server.config import PathsConfig, load_config
    cfg = load_config()
    new_paths = PathsConfig(
        data_dir=d,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    )
    return replace(cfg, paths=new_paths)


# --------------------------------------------------------------------------- #
def test_bible_edition_avertissement_3_5():
    """Un scénario Adventurers League 5e chargé dans une partie 3.5 déclenche
    la détection 5e + l'avertissement de réinterprétation."""
    d = _setup(None)
    try:
        # Partie marquée 3.5
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({"meta": {"regles": "D&D 3.5"}, "phase": "exploration", "quete": {}})
        tr = asyncio.run(scenarios_laelith_charger(_ctx(d), "divers_dues_for_the_dead"))
        bible = tr.state_patch["quete"]["bible"]
        assert bible["edition_detectee"] == "5e"
        assert "3.5" in bible["edition_partie"]
        assert "avertissement" in bible and bible["avertissement"]
        assert "réinterprète" in bible["avertissement"].lower() or "reinterprete" in bible["avertissement"].lower()
    finally:
        pass


def test_bible_persistee_dans_etat():
    """La bible est persistée dans `quete.bible` de l'état de la partie."""
    d = _setup(None)
    try:
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({"meta": {"regles": "D&D 3.5"}, "phase": "exploration", "quete": {}})
        asyncio.run(scenarios_laelith_charger(_ctx(d), "divers_dues_for_the_dead"))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        bible = (etat.get("quete") or {}).get("bible") or {}
        assert bible.get("titre") == "Dues For The Dead"
        assert "edition_detectee" in bible
        assert "etapes" in bible and "etape_courante" in bible
    finally:
        pass


def test_detecter_edition_et_niveau():
    """Les heuristiques d'édition et de niveau répondent sur le PDF réel."""
    d = _setup(None)
    try:
        from server.tools.scenarios import extraire_pdf
        ctx = _ctx(d)
        texte = extraire_pdf(ctx, "/data/scenarios/Divers/Dues for the Dead.pdf")
        assert _detecter_edition(texte) == "5e"
        # Niveau : fourchette ou niveau simple (1..n) — non vide.
        assert _extraire_niveau(texte) != ""
    finally:
        pass


def test_detecter_edition_modules_francais_5e():
    """Les modules Laelith (français, « d&d 5 » / « niveau » dans le texte) sont
    bien détectés 5e — indispensable pour déclencher l'avertissement en partie 3.5."""
    texte_fr = (
        "Introduction aux règles de D&D 5. Chaque salle, rencontre ou "
        "piège est calibrée pour des personnages de niveau 1 à 3. "
        "Adventurers League, logsheet, passive wisdom."
    )
    assert _detecter_edition(texte_fr) == "5e"
    # Une partie 3.5 verra bien l'écart d'édition se refléter dans la bible.
    assert _detecter_edition("Un scénario pour 3.5, d&d3.5, SRD 3.5") == "3.5"


def test_scenario_etape_suit_avancement():
    """`scenario_etape` enregistre l'étape en cours puis l'étape accomplie,
    et persiste le tout dans la bible."""
    d = _setup(None)
    try:
        asyncio.run(scenarios_laelith_charger(_ctx(d), "divers_dues_for_the_dead"))
        asyncio.run(scenario_etape(
            _ctx(d), etape="Explorer les catacombes",
            avancement="entrées sécurisées", objectif="trouver l'origine",
        ))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        bible = (etat.get("quete") or {}).get("bible") or {}
        assert bible["etape_courante"] == "Explorer les catacombes"
        assert bible["objectif"] == "trouver l'origine"
        asyncio.run(scenario_etape(_ctx(d), etape="Explorer les catacombes", terminée=True))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        bible = (etat.get("quete") or {}).get("bible") or {}
        assert "Explorer les catacombes" in (bible.get("etapes_terminees") or [])
        assert bible["etape_courante"] == ""
    finally:
        pass


def test_recap_injecte_bible():
    """`build_recap` insère le bloc `SCÉNARIO (bible)` (accroche, niveaux,
    étapes, avertissement) côté MJ."""
    d = _setup(None)
    try:
        asyncio.run(scenarios_laelith_charger(_ctx(d), "divers_dues_for_the_dead"))
        asyncio.run(scenario_etape(_ctx(d), etape="Trouver le nécromancien"))
        etat = PartyState(data_dir=d, partie_id=PID).load()
        # Force un récap « complet » (PJ présent, phase exploration).
        etat["meta"] = {"regles": "D&D 3.5", "titre": "Test"}
        etat["phase"] = "exploration"
        st = PartyState(data_dir=d, partie_id=PID)
        st.save(etat)
        cfg = _make_cfg(d)
        pb = PromptBuilder(cfg)
        recap = pb.build_recap(etat)
        assert "SCÉNARIO (bible)" in recap
        assert "reste sur cette trame" in recap
        assert "Explorer les catacombes" in recap or "Trouver le nécromancien" in recap
    finally:
        pass


def test_outils_scenarios_autorises_exploration():
    """Les tools scénario sont bien autorisés en phase exploration (le MJ peut
    relire le livret et suivre les étapes en cours de partie)."""
    import server.llm.orchestrator as orch
    allowed = set(orch._PHASE_TOOLS["exploration"])
    for tool in ("scenarios_laelith_lister", "scenarios_laelith_charger",
                 "scenario_etape"):
        assert tool in allowed, tool
