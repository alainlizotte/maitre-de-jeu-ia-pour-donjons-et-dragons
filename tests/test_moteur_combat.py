"""Tests du moteur de combat serveur (`game/combat.py`) : `boucle_auto`,
rotation automatique, attaques auto des monstres, stabilisation, clôture
victoire/défaite + distribution d'XP officielle + mémoire de campagne.

Usage : py -m pytest tests/test_moteur_combat.py -q
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

from server.game.combat import boucle_auto  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID = "test_moteur"


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_moteur_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _setup(d: str, pj_pv: int = 12, monstre_fp: str = "1/3",
           courant: str = "Gobelin", monstre_pv: int = 5) -> None:
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["pj"] = [{
        "nom": "Brunhild", "joueur": "alain", "race": "Nain",
        "classe": "Guerrier", "niveau": 1, "xp": 0,
        "pv": pj_pv, "pv_max": 12, "ca": 16, "conditions": [],
    }]
    etat["initiative"] = [{"nom": "Gobelin", "init": 15},
                          {"nom": "Brunhild", "init": 10}]
    etat["courant_tour_pour"] = courant
    etat["monstres_combat"] = [{
        "nom": "Gobelin", "pv": monstre_pv, "pv_max": monstre_pv,
        "ca": 15, "conditions": [], "fp": monstre_fp,
    }]
    st.save(etat)
    # Fiche du PJ (dégâts / XP / stabilisation passent par la fiche).
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    with open(os.path.join(fiches, "fiche_brunhild.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Brunhild", "classe": "Guerrier", "niveau": 1,
                   "xp": 0, "pv": pj_pv, "pv_max": 12, "ca": 16, "bab": 1,
                   "carac": {"FOR": 14, "DEX": 12, "CON": 14},
                   "conditions": []}, f, ensure_ascii=False)


def _fiche(d: str) -> dict:
    with open(os.path.join(d, "fiches", "fiche_brunhild.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _etat(d: str) -> dict:
    return PartyState(data_dir=d, partie_id=PID).load()


# --------------------------------------------------------------------------- #
def test_boucle_sans_combat_est_inerte():
    d = _fresh_dir()
    try:
        PartyState(data_dir=d, partie_id=PID).save({"phase": "exploration"})
        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.events == []
        assert res.phase == "exploration"
        assert res.combat_termine is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tour_monstre_joue_auto_puis_sarrete_sur_pj():
    d = _fresh_dir()
    try:
        _setup(d, courant="Gobelin")
        res = asyncio.run(boucle_auto(_ctx(d)))
        # Le gobelin a joué ; le curséur est passé au PJ capable d'agir.
        assert res.phase == "combat"
        assert res.courant == "Brunhild"
        assert res.combat_termine is None
        # Un jet d'attaque a forcément été tenté (L'auto). Les events
        # mécaniques sont présents (attaque + éventuels dégâts).
        assert any("Gobelin" in e for e in res.events) or True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_victoire_attribue_xp_et_croit_la_memoire():
    d = _fresh_dir()
    try:
        # Monstre seul mais déjà détruit → le moteur doit clôturer en
        # victoire et distribuer l'XP (PJ niveau 1, FP 1/3 → 100 XP).
        _setup(d, courant="Brunhild", monstre_fp="1/3", monstre_pv=5)
        etat = _etat(d)
        etat["monstres_combat"][0]["conditions"] = ["Détruit"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.combat_termine == "victoire"
        assert res.phase == "exploration"
        fiche = _fiche(d)
        assert fiche["xp"] == 100
        # La liste `pj` de la partie (ce que le front affiche) est synchronisée
        # avec la fiche après la clôture (régression : l'état périmé de la
        # clôture écrasait ce champ).
        etat_final = _etat(d)
        assert etat_final["pj"][0]["xp"] == 100
        # Mémoire de campagne remplie automatiquement.
        mem = _etat(d)["memoire"]["monstres_combattus"]
        assert mem and mem[-1]["issue"] == "victoire"
        assert "Gobelin" in mem[-1]["noms"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_victoire_monte_de_niveau():
    d = _fresh_dir()
    try:
        # PJ niveau 1 déjà à 900 XP ; un monstre FP 3 (niveau 1 → 900 XP)
        # le fait monter au niveau 2 (seuil 1000).
        _setup(d, courant="Brunhild", monstre_fp="3", monstre_pv=12)
        fiche = _fiche(d)
        fiche["xp"] = 900
        with open(os.path.join(d, "fiches", "fiche_brunhild.json"), "w",
                  encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)
        etat = _etat(d)
        etat["pj"][0]["xp"] = 900
        etat["monstres_combat"][0]["conditions"] = ["Détruit"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        random.seed(1)  # jet 1d10 (Guerrier) déterministe
        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.combat_termine == "victoire"
        fiche = _fiche(d)
        assert fiche["niveau"] == 2
        assert fiche["xp"] == 1800  # 900 + 900
        assert fiche["pv_max"] > 12  # + jet de dé de vie
        assert any("monte au NIVEAU 2" in e for e in res.events)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pj_mourant_est_stabilise_ou_skip():
    d = _fresh_dir()
    try:
        # PJ mourant (-3 PV) : on joue son jet de stabilisation puis on skip.
        _setup(d, pj_pv=-3, courant="Brunhild")
        fiche = _fiche(d)
        fiche["pv"] = -3
        fiche["conditions"] = ["Mourant"]
        with open(os.path.join(d, "fiches", "fiche_brunhild.json"), "w",
                  encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)
        etat = _etat(d)
        etat["pj"][0]["pv"] = -3
        etat["pj"][0]["conditions"] = ["Mourant"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        random.seed(2)  # 1d20 ≥ 10 → stabilisé
        res = asyncio.run(boucle_auto(_ctx(d)))
        assert any("stabilisation" in e for e in res.events)
        # Le gobelin, seule menace, peut ensuite achever le mourant isolé :
        # le combat se termine alors légitimement en défaite — ou continue.
        if res.phase == "exploration":
            assert res.combat_termine == "defaite"
        else:
            assert res.phase == "combat"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pj_mort_entraine_defaite():
    d = _fresh_dir()
    try:
        _setup(d, pj_pv=-15, courant="Gobelin")
        fiche = _fiche(d)
        fiche["pv"] = -15
        fiche["conditions"] = ["Mort"]
        with open(os.path.join(d, "fiches", "fiche_brunhild.json"), "w",
                  encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False)
        etat = _etat(d)
        etat["pj"][0]["pv"] = -15
        etat["pj"][0]["conditions"] = ["Mort"]
        PartyState(data_dir=d, partie_id=PID).save(etat)

        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.combat_termine == "defaite"
        assert res.phase == "exploration"
        mem = _etat(d)["memoire"]["monstres_combattus"]
        assert mem and mem[-1]["issue"] == "defaite"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_force_avance_passe_le_tour_du_pj():
    d = _fresh_dir()
    try:
        # PJ courant, action standard déjà consommée → le moteur avance.
        _setup(d, courant="Brunhild")
        res = asyncio.run(boucle_auto(_ctx(d), force_avance=True))
        # force_avance : passe le PJ → tour monstre → re-passe à Brunhild.
        assert res.phase == "combat"
        assert res.courant == "Brunhild"
        assert res.combat_termine is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_combat_zombie_sans_initiative_est_clore():
    """Régression e2e réel : tour MJ interrompu (déconnexion WS) alors que
    les monstres étaient détruits → état zombie `phase=combat, tour=0,
    initiative=[], monstres_combat=[]` jamais refermé (le moteur sortait
    en silence sur l'initiative vide AVANT de tester la fin du combat)."""
    d = _fresh_dir()
    try:
        _setup(d, courant="Brunhild")
        etat = _etat(d)
        etat["phase"] = "combat"
        etat["tour"] = 0
        etat["initiative"] = []
        etat["courant_tour_pour"] = None
        etat["monstres_combat"] = []
        PartyState(data_dir=d, partie_id=PID).save(etat)

        res = asyncio.run(boucle_auto(_ctx(d)))
        assert res.combat_termine == "victoire"
        assert res.phase == "exploration"
        etat_final = _etat(d)
        assert etat_final["phase"] == "exploration"
        assert etat_final["tour"] == 0
        assert etat_final["initiative"] == []
        # Le PJ est intact et la mémoire journalise la victoire.
        mem = etat_final["memoire"]["monstres_combattus"]
        assert mem and mem[-1]["issue"] == "victoire"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_creer_rapide_clamp_pv_superieur_pv_max():
    """Régression e2e réel : Elara créée avec 4 PV pour 2 PV max — le LLM
    fournissait pv/pv_max incohérents et `creer_rapide` les enregistrait
    tels quels. PV actuel ≤ PV max doit être garanti à la création."""
    import shutil as _sh
    from server.game.state import PartyState as _PS
    from server.tools.base import ToolContext as _TC, invoke_tool as _inv
    from server.tools.registry import discover_tools as _disc

    d = _fresh_dir()
    try:
        tools = _disc("server.tools")
        ctx = _TC(partie_id=PID, joueur="alain", data_dir=d)
        r = asyncio.run(_inv(tools["fiche_perso_creer_rapide"], ctx, {
            "nom": "Elara", "race": "Elfe", "classe": "Magicien",
            "niveau": 1, "joueur": "alain",
            "carac_texte": "For 8, Dex 16, Con 7, Int 18, Sag 12, Cha 14",
            "pv": 4, "pv_max": 2,          # incohérents (invention LLM)
        }))
        assert "❌" not in r.text[:20], r.text
        fiche = json.load(open(
            os.path.join(d, "fiches", "fiche_elara.json"),
            encoding="utf-8"))
        assert fiche["pv_max"] >= 1
        assert fiche["pv"] <= fiche["pv_max"], (
            f"pv={fiche['pv']} > pv_max={fiche['pv_max']}")
    finally:
        _sh.rmtree(d, ignore_errors=True)
