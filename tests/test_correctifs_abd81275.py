# -*- coding: utf-8 -*-
"""Régressions de la partie abd81275 (examen 2026-09-16/17).

Le MJ y était confus pour quatre causes racines, désormais corrigées :

1. PV hérités d'une AUTRE partie : la fiche (partagée entre parties) était
   recopiée telle quelle à l'enregistrement du PJ — nouvelle partie
   démarrée à 4/16 PV sans aucun combat, et rejoin écrasant les PV de la
   partie par ceux de la fiche.
2. Repos longs en rafale (4 en 4 minutes) : `repos_long` était devenu le
   bouton de soin du MJ.
3. Tenue de livre au néant : `histoire` vide, `lieu` resté « (non
   déterminé)/ville » au fond de la grotte, étape de trame figée — le LLM
   n'appelle jamais `ajouter_evenement_histoire`/`memoire_position`.
   Désormais le serveur journalise lui-même à chaque entrée/déplacement.
4. `bible.ennemis` trop pauvre (extrait PDF) : les ennemis canoniques des
   salles du donjon (manifeste) sont fusionnés dans le bloc bible.

Usage : py -m pytest tests/test_correctifs_abd81275.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.prompt_builder import (  # noqa: E402
    _ennemis_donjon,
    _scenario_bible_bloc,
)
from server.persos import enregistrer_personnage_partie  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    _journaliser_lieu,
    _sauver_etat,
)
from server.tools.sorts import repos_long  # noqa: E402

PID = "test_abd81275"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


# --------------------------------------------------------------------------- #
# 1. PV : nouveau départ à pleine santé, partie prioritaire au rejoin
# --------------------------------------------------------------------------- #
def _fiche_sur_disque(d: str, nom: str, pv: int, pv_max: int,
                      conditions: list[str] | None = None) -> None:
    from server.persos import chemin_fiche
    with open(chemin_fiche(d, nom), "w", encoding="utf-8") as f:
        json.dump({
            "nom": nom, "joueur": "alain", "proprietaire": "alain",
            "race": "Demi-orc", "classe": "Barbare", "niveau": 1,
            "pv": pv, "pv_max": pv_max, "ca": 14,
            "xp": 0, "conditions": conditions or [],
            "carac": {"FOR": 19, "DEX": 10, "CON": 13, "INT": 9,
                      "SAG": 12, "CHA": 9},
        }, f, ensure_ascii=False)


def test_nouvelle_partie_dmarre_pleine_sant_mm_si_la_fiche_est_blesse(tmp_path):
    d = str(tmp_path)
    # Fiche blessée par une AUTRE partie (le bug abd81275 : 4/16 PV).
    _fiche_sur_disque(d, "Utturgut", pv=4, pv_max=16, conditions=["-1 PV"])
    fiche = enregistrer_personnage_partie(d, PID, "Utturgut", "alain")
    assert fiche is not None
    etat = PartyState(data_dir=d, partie_id=PID).load()
    pj = next(p for p in etat["pj"] if p["nom"] == "Utturgut")
    # Nouvelle partie = nouveau départ : PV au maximum, conditions nettoyées.
    assert pj["pv"] == 16 and pj["conditions"] == []
    # La fiche est aussi soignée (les tools réécrivent pj.pv depuis la fiche).
    assert fiche["pv"] == 16 and fiche.get("conditions") == []


def test_rejoin_ne_crase_pas_les_pv_vcus_dans_la_partie(tmp_path):
    d = str(tmp_path)
    _fiche_sur_disque(d, "Utturgut", pv=4, pv_max=16)
    enregistrer_personnage_partie(d, PID, "Utturgut", "alain")
    # La partie avance : le perso encaisse des dégâts (état de la PARTIE).
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["pj"][0]["pv"] = 6
    st.save(etat)
    # En parallèle, la fiche est soignée à part (divergence inévitable).
    from server.persos import chemin_fiche
    chemin = chemin_fiche(d, "Utturgut")
    fiche = json.load(open(chemin, encoding="utf-8"))
    fiche["pv"] = 16
    json.dump(fiche, open(chemin, "w", encoding="utf-8"), ensure_ascii=False)
    # Rejoin / refresh de la page : les PV VÉCUS doivent rester à 6.
    enregistrer_personnage_partie(d, PID, "Utturgut", "alain")
    etat2 = PartyState(data_dir=d, partie_id=PID).load()
    assert etat2["pj"][0]["pv"] == 6


# --------------------------------------------------------------------------- #
# 2. Garde anti-repos-spam
# --------------------------------------------------------------------------- #
def test_repos_long_refuse_en_rafale_et_passe_avec_forcer(tmp_path):
    d = str(tmp_path)
    _fiche_sur_disque(d, "Testos", pv=5, pv_max=10)
    PartyState(data_dir=d, partie_id=PID).save({
        "phase": "exploration",
        "pj": [{"nom": "Testos", "pv": 5, "pv_max": 10}],
    })
    ctx = _ctx(d)
    r1 = asyncio.run(repos_long(ctx))
    assert "Repos long" in r1.text
    r2 = asyncio.run(repos_long(ctx))
    assert "Repos refusé" in r2.text
    # Le refus n'applique AUCUN soin.
    assert PartyState(data_dir=d, partie_id=PID).load()["pj"][0]["pv"] == 6
    # Échappatoire explicite (temps réellement écoulé).
    r3 = asyncio.run(repos_long(ctx, forcer=True))
    assert "Repos long" in r3.text
    assert PartyState(data_dir=d, partie_id=PID).load()["pj"][0]["pv"] == 7


# --------------------------------------------------------------------------- #
# 3. Auto-journalisation des déplacements de donjon
# --------------------------------------------------------------------------- #
def test_journaliser_lieu_remplit_lieu_memoire_histoire_et_trame(tmp_path):
    d = str(tmp_path)
    st = PartyState(data_dir=d, partie_id=PID)
    etat = {
        "phase": "exploration",
        "quete": {"bible": {"etapes_terminees": []}},
        "memoire": {"position": {"lieu": "", "zone": "", "detail": ""}},
    }
    donjon = {
        "id": "Grotte de Nulentok", "etage": 0, "courant": [3, 0],
        "etages": {"0": {"nom": "Grotte de Nulentok"}},
        "etapes": [{
            "titre": "Récupérer la Couronne", "salle": "4,0",
            "detail": "Vaincre Nulentok.",
        }],
    }
    salle = {"x": 3, "y": 0, "type": "puits", "description": "Une fosse ronde."}
    _journaliser_lieu(_ctx(d), etat, donjon, salle,
                      "Déplacement : salle (3,0) — puits.")
    # etat["lieu"] suit le donjon (fini le « (non déterminé)/ville »).
    assert etat["lieu"]["nom"] == "Grotte de Nulentok"
    assert etat["lieu"]["type"] == "donjon"
    assert etat["lieu"]["position_x"] == 3
    # memoire.position renseignée.
    assert etat["memoire"]["position"]["lieu"] == "Grotte de Nulentok"
    assert "salle (3,0)" in etat["memoire"]["position"]["detail"]
    # Journal d'histoire alimenté (le LLM n'appelle jamais le tool).
    assert etat["histoire"] and "salle (3,0)" in etat["histoire"][0]["evenement"]
    # Trame : la salle d'arrivée n'est PAS celle d'une étape → objectif inchangé.
    assert "etape_courante" not in etat["quete"]["bible"]
    # Arrivée dans la salle-étape (4,0) → l'objectif de la bible suit.
    donjon["courant"] = [4, 0]
    _journaliser_lieu(_ctx(d), etat, donjon,
                      {"x": 4, "y": 0, "type": "piège"}, "Salle (4,0).")
    assert etat["quete"]["bible"]["etape_courante"] == "Récupérer la Couronne"
    assert "Nulentok" in etat["quete"]["bible"]["objectif"]


def test_journalisation_branche_sur_les_tools_donjon(tmp_path):
    """entrer/explorer écrivent bien lieu + histoire dans l'état persistant."""
    from server.tools.cartes import carte_donjon_entrer
    d = str(tmp_path)
    PartyState(data_dir=d, partie_id=PID).save({"phase": "exploration"})
    tr = asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon de test"))
    assert not tr.text.startswith("❌")
    etat = PartyState(data_dir=d, partie_id=PID).load()
    assert etat["lieu"]["nom"] == "Donjon de test"
    assert etat["lieu"]["type"] == "donjon"
    assert etat["histoire"] and "Entrée dans le donjon" in \
        etat["histoire"][-1]["evenement"]


# --------------------------------------------------------------------------- #
# 4. Fusion des ennemis du donjon dans la bible scénario
# --------------------------------------------------------------------------- #
def test_ennemis_donjon_extraits_de_la_grille():
    etat = {"donjon": {"grille": [
        {"x": 0, "y": 0},
        {"x": 4, "y": 1, "ennemis": ["Squelette ×4"]},
        {"x": 2, "y": 2, "ennemis": ["Gobelin x2"]},
        {"x": 0, "y": 3, "ennemis": ["Ombre ×2"]},
        {"x": 4, "y": 3, "ennemis": ["Ombre ×2"]},
    ]}}
    places = _ennemis_donjon(etat)["places"]
    assert places["squelette"][0] == "Squelette"
    assert places["gobelin"][0] == "Gobelin"
    # L'Ombre apparaît dans DEUX salles : les deux sont annotées.
    assert sorted(places["ombre"][1]) == ["0,3", "4,3"]


def test_bible_fusionne_les_ennemis_du_donjon():
    quete = {"bible": {
        "ennemis": ["Squelette"],          # détection PDF trop pauvre
        "resume": "", "etapes": [],
    }}
    etat = {"donjon": {"grille": [
        {"x": 3, "y": 2, "ennemis": ["Goule ×2"]},
        {"x": 0, "y": 3, "ennemis": ["Ombre ×2"]},
        {"x": 2, "y": 3, "ennemis": ["Nécromancien_rouge ×1"]},
    ]}}
    bloc = _scenario_bible_bloc(quete, etat=etat)
    ligne = next(l for l in bloc.splitlines() if "DU SCÉNARIO" in l)
    # Les ennemis canoniques du module sont TOUS ancrés dans le prompt.
    for nom in ("Squelette", "Goule", "Ombre", "Nécromancien_rouge"):
        assert nom in ligne
    # Sans etat : comportement historique conservé.
    assert "Goule" not in _scenario_bible_bloc(quete)


# --------------------------------------------------------------------------- #
# 5. Le donjon bloc expose la convention téléportation (données)
# --------------------------------------------------------------------------- #
def test_manifeste_crown_of_mystra_convention_teleportation():
    chemin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "server", "data", "scenarios", "Les Royaumes Oubliés",
        "The Crown of Mystra.donjon.json",
    )
    man = json.load(open(chemin, encoding="utf-8"))
    salles = {
        (s.get("x"), s.get("y")): s
        for et in man.get("etages", []) for s in et.get("salles", [])
    }
    # Les salles-gemmes portent la convention « lieu de Faerûn / porte =
    # téléportation scellée avant l'étape 1 ».
    for xy in [(4, 1), (0, 2), (0, 3)]:
        assert "TÉLÉPORTATION" in str(salles[xy].get("note", "")).upper(), xy
    # La grotte de Nulentok reste un vrai donjon à explorer à pied.
    assert "VOYAGE" in str(salles[(0, 0)].get("note", "")).upper()
    # La trame est bien portée par le donjon (injection prompt_builder).
    assert man.get("etapes") and man["etapes"][0].get("salle") == "4,0"
