# -*- coding: utf-8 -*-
"""Correctifs P1 (demande joueur, post-run 090ec0af) :

1. ⚖️ AUTO-ÉQUILIBRAGE : `engager_combat` applique automatiquement un
   ajustement proportionnel quand le groupe est plus petit que le minimum
   recommandé du module (`bible.joueurs_recommandes`) — le gouverneur
   manuel dépendait du MJ, qui ne le demandait jamais (TPC gobs/sea).
2. 🛡️ Filet 5bis-g étendu à l'ENTRÉE d'une salle canonique non nettoyée
   (les traversées en prose n'esquivent plus la rencontre), sauf note
   furtive du manifeste (endormis/infiltration/surprise possible).
3. 🛡️ GAME OVER : les `say` sans acceptation de résurrection ne
   déclenchent plus de tour MJ (20+ tours zombies observés) ;
   `fiche_perso_mettre_a_jour` refuse de modifier un PJ mort
   (résurrection sauvage via tools observée) et verrouille les stats
   de combat côté MJ.

Usage : py -m pytest tests/test_correctifs_p1.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.main import (  # noqa: E402
    _rencontre_canonique_esquivee,
)
from server.tools.state import (  # noqa: E402
    _note_salle_courante,
    _salle_sans_rencontre_prevue,
)

_BESTIAIRE = {
    "_meta": {"source": "test"},
    "gobelin": {"cle": "gobelin", "nom": "Gobelin", "type": "Humanoïde",
                "dv": "1d8+1", "pv": 5, "pv_max": 5, "ca": 15, "init": 1,
                "fp": "1/3"},
    "orque": {"cle": "orque", "nom": "Orque", "type": "Humanoïde",
              "dv": "1d8+3", "pv": 7, "pv_max": 7, "ca": 13, "init": 0,
              "fp": "1/2"},
}


# --------------------------------------------------------------------------- #
#  1. Auto-équilibrage à l'engagement
# --------------------------------------------------------------------------- #
def _env_equilibre(tmp_path, nb_pj: int = 3, joueurs_rec: str = "4"):
    data = tmp_path / "data"
    (data / "scenarios" / "t").mkdir(parents=True, exist_ok=True)
    (data / "bestiaire.json").write_text(
        json.dumps(_BESTIAIRE, ensure_ascii=False), encoding="utf-8")
    pjs = [{"nom": f"PJ{i}", "classe": "Guerrier", "niveau": 1,
            "pv": 10, "pv_max": 10} for i in range(nb_pj)]
    (data / f"partie_eq_t.json").write_text(json.dumps({
        "phase": "exploration",
        "pj": pjs,
        "quete": {"titre": "T", "pitch": "T.",
                  "source": "[scen_eq] /x.pdf",
                  "bible": {"joueurs_recommandes": joueurs_rec}},
        "donjon": {"id": "d_eq", "courant": [0, -5],
                   "grille": [{"x": 0, "y": -5, "ennemis": ["Gobelin"]}]},
    }, ensure_ascii=False), encoding="utf-8")
    return str(data)


def _engager(dd: str) -> str:
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    ctx = ToolContext(partie_id="eq_t", joueur="e2e", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["engager_combat"], ctx,
        {"monstres": "Gobelin"}))
    return r.text


def test_auto_equilibrage_3pj_module_4(tmp_path):
    rep = _engager(_env_equilibre(tmp_path, nb_pj=3, joueurs_rec="4"))
    assert "Ajustement automatique" in rep, rep
    assert "PV ×0.75" in rep, rep
    etat = PartyState(data_dir=_env_equilibre(tmp_path, 3, "4"),
                      partie_id="eq_t").load()


def test_auto_equilibrage_fort_sous_60pct(tmp_path):
    rep = _engager(_env_equilibre(tmp_path, nb_pj=2, joueurs_rec="4"))
    assert "PV ×0.5" in rep, rep
    assert "attaque -2" in rep, rep
    assert "CA -1" in rep, rep


def test_pas_d_ajustement_dans_la_fourchette(tmp_path):
    """« 3-4 » avec 3 PJ : dans la fourchette → aucun ajustement."""
    rep = _engager(_env_equilibre(tmp_path, nb_pj=3, joueurs_rec="3-4"))
    assert "Ajustement automatique" not in rep, rep


def test_ajustement_du_modele_prioritaire(tmp_path):
    """Un ajustement fourni par le MJ n'est PAS écrasé."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    dd = _env_equilibre(tmp_path, nb_pj=2, joueurs_rec="4")
    ctx = ToolContext(partie_id="eq_t", joueur="e2e", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["engager_combat"], ctx,
        {"monstres": "Gobelin", "ajustement": "pv 90%, attaque -3"}))
    assert "Ajustement automatique" not in r.text, r.text
    assert "PV 5→4" in r.text or "90 %" in r.text, r.text


# --------------------------------------------------------------------------- #
#  2. Filet 5bis-g étendu à l'entrée des salles canoniques
# --------------------------------------------------------------------------- #
def _etat_canon_entree(note: str = "") -> dict:
    return {
        "phase": "exploration",
        "pj": [{"nom": "Brandt"}],
        "donjon": {
            "id": "d_test", "courant": [0, -5],
            "grille": [{"x": 0, "y": -5, "ennemis": ["Orque ×2"],
                        "note": note}],
        },
        "histoire": [{"evenement": "Début de l'aventure : …"}],
    }


def test_entree_salle_agressive_declenche_le_filet():
    canon = _rencontre_canonique_esquivee(
        _etat_canon_entree(),
        [{"name": "carte_donjon_explorer", "ok": True}],
        "Nous observons la pièce.")
    assert canon == ["Orque ×2"], canon


def test_entree_salle_furtive_exemptee():
    """Note « endormis » : l'approche discrète reste possible (les signaux
    classiques — attaque, confrontation — déclenchent toujours)."""
    canon = _rencontre_canonique_esquivee(
        _etat_canon_entree(note="Deux gobelins endormis, surprise possible "
                               "au premier round."),
        [{"name": "carte_donjon_explorer", "ok": True}],
        "Nous observons la pièce.")
    assert canon == [], canon


def test_note_salle_courante_lue():
    assert "endormis" in _note_salle_courante(
        _etat_canon_entree(note=" gobelins endormis "))
    assert _note_salle_courante(_etat_canon_entree()) == ""


# --------------------------------------------------------------------------- #
#  3. Verrous de mort + champs verrouillés
# --------------------------------------------------------------------------- #
def _fiche_morte(tmp_path) -> str:
    data = tmp_path / "data"
    (data / "fiches").mkdir(parents=True)
    (data / "fiches" / "fiche_bargoum.json").write_text(json.dumps({
        "nom": "Bargoum", "classe": "Guerrier", "niveau": 1,
        "pv": -10, "pv_max": 13, "conditions": ["Mort"],
        "carac": {"FOR": 16, "DEX": 12, "CON": 16, "INT": 10,
                  "SAG": 12, "CHA": 8},
    }, ensure_ascii=False), encoding="utf-8")
    return str(data)


def test_mettre_a_jour_refuse_un_mort(tmp_path):
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    dd = _fiche_morte(tmp_path)
    ctx = ToolContext(partie_id="p1_t", joueur="e2e", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["fiche_perso_mettre_a_jour"], ctx,
        {"nom": "Bargoum", "champ": "pv", "valeur": "20"}))
    assert r.text.startswith("☠️"), r.text


def test_mettre_a_jour_vivant_pv_toujours_possible(tmp_path):
    """Contrat F2 preserve : les « autres champs » (pv inclus) restent
    modifiables sur un PJ VIVANT - seule la condition « Mort » verrouille
    (la resurrection legitime leve « Mort » AVANT de soigner/ajuster)."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    dd = _fiche_morte(tmp_path)
    ctx = ToolContext(partie_id="p1_t", joueur="e2e", data_dir=dd)
    # Fiche VIVANTE.
    chemin_fiche = os.path.join(dd, "fiches", "fiche_bargoum.json")
    fiche_vivante = json.load(open(chemin_fiche, encoding="utf-8"))
    fiche_vivante.update({"pv": 10, "conditions": []})
    with open(chemin_fiche, "w", encoding="utf-8") as f:
        json.dump(fiche_vivante, f, ensure_ascii=False)
    r = asyncio.run(invoke_tool(
        discover_tools()["fiche_perso_mettre_a_jour"], ctx,
        {"nom": "Bargoum", "champ": "pv", "valeur": "3"}))
    assert r.text.startswith("✅"), r.text


def test_soigner_un_mort_refuse_existant(tmp_path):
    """Le garde préexistant (dfbb4846) : soigner un MORT → ⚰️. Le flux de
    résurrection légitime (5f3e31c9) retire « Mort » AVANT de soigner un
    corps à -10 : il n'est PAS cassé par les verrous du jour."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    dd = _fiche_morte(tmp_path)
    ctx = ToolContext(partie_id="p1_t", joueur="e2e", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["fiche_perso_soigner"], ctx,
        {"nom": "Bargoum", "soin": 8}))
    assert "MORT" in r.text, r.text


# --------------------------------------------------------------------------- #
#  4. Borne de canon sur les montées de variante (run shadows 090ec0af)
# --------------------------------------------------------------------------- #
def test_variante_bornee_au_canon_de_la_salle():
    """Loup terrible canonique ne doit PAS monter en Loup-garou (humain)
    (32 PV) par la seule mention de loup : la variante doit figurer dans
    le canon de la salle."""
    from server.tools.monstres import _maj_variante_specifique
    mons = {
        "loup": {"cle": "loup", "nom": "Loup"},
        "loup_garou_humain": {"cle": "loup_garou_humain",
                              "nom": "Loup-garou (humain)",
                              "alias": ["lycanthrope"]},
        "loup_terrible": {"cle": "loup_terrible", "nom": "Loup terrible"},
    }
    best = {"monstres": mons}
    # Canon = Loup terrible uniquement -> montee REFUSEE.
    m1 = [dict(mons["loup_terrible"])]
    notes1 = _maj_variante_specifique(
        m1, best, "Un loup-garou surgit de l'ombre !",
        cles_canon={"loup terrible"})
    assert m1[0]["nom"] == "Loup terrible", (m1, notes1)
    assert notes1 == [], notes1
    # Canon incluant la variante -> montee appliquee.
    m2 = [dict(mons["loup"])]
    notes2 = _maj_variante_specifique(
        m2, best, "Un lycanthrope attaque !",
        cles_canon={"loup-garou (humain)"})
    assert m2[0]["nom"] == "Loup-garou (humain)", (m2, notes2)
    # Hors canon de salle (None) -> comportement historique conserve.
    m3 = [dict(mons["loup"])]
    _maj_variante_specifique(m3, best, "Un lycanthrope attaque !",
                             cles_canon=None)
    assert m3[0]["nom"] == "Loup-garou (humain)", m3



