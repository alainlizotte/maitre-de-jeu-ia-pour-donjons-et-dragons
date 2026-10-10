# -*- coding: utf-8 -*-
"""Correctifs issus des parties jouées au navigateur (97e94bd1 / e50d6a97 /
86c9c971 — « À la chasse aux gobs », « Dues For The Dead », « Le Dragon de
Hurlemont »).

Rappel des défauts observés en jeu :

1. **Rencontre gonflée + mort instantanée** (97e94bd1) : le MJ a inventé un
   « Hobgobelin chef » et un « Gobelours » ; le garde « Rencontre hors
   module » les a laissé PASSER car la correspondance flue (~0.82) les
   traitait comme des coquilles de « Gobelin ». Gorak (niv 1, solo) a
   affronté 4 créatures au lieu de 2 → mort au round 2. Correctif : un nom
   qui résout STRICTEMENT au bestiaire vers une espèce DIFFÉRENTE des
   attendues est une invention → refus ; une vraie coquille (« Pierceur »
   pour « Perceur ») résout VERS l'espèce attendue → tolérée.
2. **Attaque du joueur jamais résolue** (97e94bd1) : « j'attaque le
   hobgobelin » a été narré en prose sans aucun jet, le tour est passé à
   vide (délai 200 s) et le PJ est mort sans avoir frappé. Correctifs : le
   filet déterministe tourne même après un échec du rejeu correctif, et il
   cible le monstre NOMMÉ par le joueur.
3. **Personnage mort silencieusement ressuscité** (97e94bd1 → e50d6a97) : un
   perso mort (PV -10, « Mort ») rejoignait une nouvelle partie à PV pleins
   sans AUCUN signal. Correctif : événement d'histoire « nouveau départ »
   journalisé.

Usage : py -m pytest tests/test_correctifs_97e94bd1.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import Orchestrator, OrchestratedResult  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_BESTIAIRE = {
    "Gobelin": {
        "nom": "Gobelin", "cle": "gobelin", "fp": "1/3",
        "pv": 5, "pv_max": 5, "ca": 15, "type": "Humanoïde (gobelinin)",
        "prompt_image": "portrait custom gobelin",
    },
    "Hobgobelin": {
        "nom": "Hobgobelin", "cle": "hobgobelin", "fp": "1/2",
        "pv": 6, "pv_max": 6, "ca": 15, "type": "Humanoïde (gobelinin)",
        "prompt_image": "portrait custom hobgobelin",
    },
    "Gobelours": {
        "nom": "Gobelours", "cle": "gobelours", "fp": "1",
        "pv": 16, "pv_max": 16, "ca": 14, "type": "Bête",
        "prompt_image": "portrait custom gobelours",
    },
    "Perceur": {
        "nom": "Perceur", "cle": "perceur", "fp": "1/2",
        "pv": 10, "pv_max": 10, "ca": 15, "type": "Aberration",
        "prompt_image": "portrait custom perceur",
    },
}


def _ecrire_bestiaire(d: str) -> None:
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump(_BESTIAIRE, f, ensure_ascii=False)


def _partie_donjon(d: str, ennemis_canoniques: list[str]) -> str:
    """Partie en exploration, donjon actif dont la salle (0,0) porte la
    liste canonique `ennemis` du module."""
    PartyState(data_dir=d, partie_id="t_97e94bd1").save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Gorak", "pv": 13, "pv_max": 13, "joueur": "Testeur",
                "niveau": 1}],
        "pnj": [],
        "donjon": {
            "id": "L'antre des gobelins",
            "courant": [0, 0],
            "salles_visitees": ["0,0"],
            "grille": [{
                "x": 0, "y": 0, "type": "entrée", "visitee": True,
                "description": "La grotte des gobelins.",
                "ennemis": ennemis_canoniques,
                "portes": {"nord": False, "sud": False, "est": True,
                           "ouest": False},
            }],
        },
        "histoire": [],
    })
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id="t_97e94bd1", joueur="Testeur", data_dir=d)


# --------------------------------------------------------------------------- #
#  1. Garde anti-hors-module : les inventions réelles sont refusées
# --------------------------------------------------------------------------- #
def test_engage_refuse_especes_reelles_hors_module():
    """« Hobgobelin » et « Gobelours » sont des ESPÈCES RÉELLES du bestiaire
    absentes de la salle canonique [Gobelin ×2] : refusés (plus jamais
    traités comme des coquilles de « Gobelin » — partie 97e94bd1)."""
    d = tempfile.mkdtemp(prefix="dnd35_garde97_")
    _ecrire_bestiaire(d)
    _partie_donjon(d, ["Gobelin ×2"])
    tools = discover_tools()
    spec = tools.get("engager_combat")
    tr = asyncio.run(invoke_tool(
        spec, _ctx(d),
        {"monstres": "Hobgobelin, Gobelin, Gobelours"},
    ))
    assert "Rencontre hors module refusée" in tr.text, tr.text
    assert "Hobgobelin" in tr.text and "Gobelours" in tr.text, tr.text


def test_engage_coquille_espece_attendue_tolerree():
    """Une VRAIE coquille (« Pierceur » pour « Perceur », la seule espèce
    canonique de la salle) reste tolérée — l'engagement n'est pas refusé
    (comportement ae358455 préservé)."""
    d = tempfile.mkdtemp(prefix="dnd35_garde97b_")
    _ecrire_bestiaire(d)
    _partie_donjon(d, ["Perceur ×2"])
    tools = discover_tools()
    spec = tools.get("engager_combat")
    tr = asyncio.run(invoke_tool(
        spec, _ctx(d), {"monstres": "Pierceur"},
    ))
    assert "Rencontre hors module refusée" not in tr.text, tr.text


def test_engage_espece_canonique_toujours_ok():
    """L'espèce canonique de la salle s'engage normalement."""
    d = tempfile.mkdtemp(prefix="dnd35_garde97c_")
    _ecrire_bestiaire(d)
    _partie_donjon(d, ["Gobelin ×2"])
    tools = discover_tools()
    spec = tools.get("engager_combat")
    tr = asyncio.run(invoke_tool(
        spec, _ctx(d), {"monstres": "Gobelin, Gobelin"},
    ))
    assert "Rencontre hors module refusée" not in tr.text, tr.text
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    assert etat.get("phase") == "combat", tr.text
    noms = [str(m.get("nom")) for m in (etat.get("monstres_combat") or [])]
    # 🔧 Partie 97e94bd1 (M6) : la quantité canonique est appliquée UNE fois
    # par espèce (« Gobelin, Gobelin » ne multipliait pas la quantité).
    # 🔧 Partie fc6f4d0e : la quantité est SCALÉE au groupe — solo (1 PJ,
    # facteur 1/4) et canon « Gobelin ×2 » → 1 seul gobelin
    # (ceil(2 × 0.25) = 1) au lieu de 2.
    assert noms == ["Gobelin"], noms


# --------------------------------------------------------------------------- #
#  2. Résolution déterministe : le monstre NOMMÉ par le joueur est ciblé
# --------------------------------------------------------------------------- #
def _fiche_gorak_arme(d: str) -> None:
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    with open(os.path.join(d, "fiches", "fiche_gorak.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Gorak", "race": "Demi-orc", "classe": "Barbare",
            "niveau": 1, "bab": 1, "pv": 13, "pv_max": 13, "ca": 12,
            "carac": {"FOR": 18, "DEX": 15, "CON": 13, "INT": 12,
                      "SAG": 12, "CHA": 9},
            "equipement": [{"nom": "Grande hache", "qte": 1}],
        }, f, ensure_ascii=False)


def test_attaque_sans_jet_cible_le_monstre_nomme():
    """« j attaque le hobgobelin avec ma hache » → la résolution
    déterministe cible HOBGOBELIN (et non la première cible vivante)."""
    from server.main import _attaque_pj_sans_jet

    d = tempfile.mkdtemp(prefix="dnd35_atk97_")
    _ecrire_bestiaire(d)
    _fiche_gorak_arme(d)
    PartyState(data_dir=d, partie_id="t_97e94bd1").save({
        "meta": {"titre": "test"},
        "phase": "combat",
        "pj": [{"nom": "Gorak", "pv": 13, "pv_max": 13, "joueur": "Testeur",
                "niveau": 1}],
        "monstres_combat": [
            {"nom": "Gobelin", "pv": 5, "pv_max": 5, "ca": 15, "fp": "1/3"},
            {"nom": "Hobgobelin", "pv": 6, "pv_max": 6, "ca": 15,
             "fp": "1/2"},
        ],
        "initiative": [{"nom": "Gorak", "init": 10}, {"nom": "Gobelin",
                                                      "init": 14}],
        "histoire": [],
    })
    orch = Orchestrator(client=None, tools=discover_tools())
    result = OrchestratedResult()
    note = asyncio.run(_attaque_pj_sans_jet(
        orch, _ctx(d), None, result, "Gorak",
        texte_declare="j attaque le hobgobelin avec ma hache",
    ))
    assert note, "l'attaque déterministe doit produire une note mécanique"
    appels = [tc.get("args", {}).get("nom_cible") or tc.get("args", {}).get(
        "cible") for tc in result.tool_calls_trace]
    assert "Hobgobelin" in [a for a in appels if a], appels


# --------------------------------------------------------------------------- #
#  3. Personnage mort → nouvelle partie : événement « nouveau départ »
# --------------------------------------------------------------------------- #
def test_perso_mort_nouvelle_partie_evenement_nouveau_depart():
    """Un perso MORT (PV -10, « Mort ») qui rejoint une nouvelle partie
    repart à PV pleins avec un événement d'histoire EXPLICITE (plus de
    résurrection silencieuse — partie 97e94bd1 → e50d6a97)."""
    from server.persos import enregistrer_personnage_partie

    d = tempfile.mkdtemp(prefix="dnd35_mort97_")
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    with open(os.path.join(d, "fiches", "fiche_gorak.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Gorak", "race": "Demi-orc", "classe": "Barbare",
            "niveau": 1, "bab": 1, "pv": -10, "pv_max": 13, "ca": 12,
            "conditions": ["Mort"], "proprietaire": "Testeur",
            "joueur": "Testeur",
            "carac": {"FOR": 18, "DEX": 15, "CON": 13, "INT": 12,
                      "SAG": 12, "CHA": 9},
        }, f, ensure_ascii=False)
    PartyState(data_dir=d, partie_id="t_97e94bd1").save({
        "meta": {"titre": "test"}, "phase": "exploration", "pj": [],
        "histoire": [],
    })
    fiche = enregistrer_personnage_partie(d, "t_97e94bd1", "Gorak", "Testeur")
    assert fiche is not None
    assert fiche.get("pv") == 13 and not fiche.get("conditions"), fiche.get(
        "pv")
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    evts = [str(e.get("evenement") or "") for e in (etat.get("histoire") or [])]
    assert any("nouveau départ" in e and "décès" in e for e in evts), evts


def test_perso_vivant_pas_d_evenement_nouveau_depart():
    """Un perso VIVANT qui rejoint une nouvelle partie : aucun événement
    « nouveau départ » (le fil d'histoire reste propre)."""
    from server.persos import enregistrer_personnage_partie

    d = tempfile.mkdtemp(prefix="dnd35_vivant97_")
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    with open(os.path.join(d, "fiches", "fiche_gorak.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Gorak", "race": "Demi-orc", "classe": "Barbare",
            "niveau": 1, "bab": 1, "pv": 8, "pv_max": 13, "ca": 12,
            "conditions": [], "proprietaire": "Testeur",
            "joueur": "Testeur",
            "carac": {"FOR": 18, "DEX": 15, "CON": 13, "INT": 12,
                      "SAG": 12, "CHA": 9},
        }, f, ensure_ascii=False)
    PartyState(data_dir=d, partie_id="t_97e94bd1").save({
        "meta": {"titre": "test"}, "phase": "exploration", "pj": [],
        "histoire": [],
    })
    fiche = enregistrer_personnage_partie(d, "t_97e94bd1", "Gorak", "Testeur")
    assert fiche is not None
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    evts = [str(e.get("evenement") or "") for e in (etat.get("histoire") or [])]
    assert not any("nouveau départ" in e for e in evts), evts


def test_engage_solo_quantite_scalee():
    """🔧 Partie fc6f4d0e : la quantité canonique est SCALÉE à la taille du
    groupe — un SOLO (facteur 1/4) face à « Gobelin ×4 » n'affronte qu'UN
    gobelin (ceil(4 × 0.25) = 1) au lieu de la meute entière (les 6
    Perceurs qui ont tué la Magicienne de la partie 75f5e74c)."""
    d = tempfile.mkdtemp(prefix="dnd35_scale97_")
    _ecrire_bestiaire(d)
    _partie_donjon(d, ["Gobelin ×4"])   # canon : 4 (module pour 4-5 joueurs)
    tools = discover_tools()
    spec = tools.get("engager_combat")
    tr = asyncio.run(invoke_tool(
        spec, _ctx(d), {"monstres": "Gobelin"},
    ))
    assert "Rencontre hors module refusée" not in tr.text, tr.text
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    noms = [str(m.get("nom")) for m in (etat.get("monstres_combat") or [])]
    assert len(noms) == 1, noms  # ceil(4 × 0.25) = 1 pour un solo


def test_engage_groupe_complet_quantite_canonique_integrale():
    """Un groupe de 4 PJ (facteur 1.0) affronte la quantité canonique
    ENTIÈRE — le scaling ne réduit que les petits groupes."""
    d = tempfile.mkdtemp(prefix="dnd35_scale97b_")
    _ecrire_bestiaire(d)
    _partie_donjon(d, ["Gobelin ×2"])
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    etat["pj"] = [
        {"nom": f"P{i}", "pv": 13, "pv_max": 13, "joueur": "Testeur",
         "niveau": 1}
        for i in range(4)
    ]
    PartyState(data_dir=d, partie_id="t_97e94bd1").save(etat)
    tools = discover_tools()
    spec = tools.get("engager_combat")
    tr = asyncio.run(invoke_tool(
        spec, _ctx(d), {"monstres": "Gobelin"},
    ))
    etat = PartyState(data_dir=d, partie_id="t_97e94bd1").load()
    noms = [str(m.get("nom")) for m in (etat.get("monstres_combat") or [])]
    assert len(noms) == 2, noms  # ×2 intégral pour 4 PJ


def test_condition_invalide_refusee():
    """🔧 Partie 384a5dd2 : les conditions hors règles (« Invalide » — mot
    de la prose du MJ) sont REFUSÉES par la liste blanche 3.5."""
    d = tempfile.mkdtemp(prefix="dnd35_cond97_")
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    with open(os.path.join(d, "fiches", "fiche_faelan.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Faelan", "pv": 10, "pv_max": 11,
            "conditions": [], "joueur": "Testeur",
        }, f, ensure_ascii=False)
    tools = discover_tools()
    spec = tools.get("fiche_perso_condition")
    tr = asyncio.run(invoke_tool(
        spec, ToolContext(partie_id="t_97e94bd1", joueur="Testeur",
                          data_dir=d),
        {"nom": "Faelan", "condition": "Invalide"},
    ))
    assert "inconnue" in tr.text and "refusée" in tr.text, tr.text
    fiche = json.load(open(os.path.join(d, "fiches", "fiche_faelan.json"),
                           encoding="utf-8"))
    assert "Invalide" not in (fiche.get("conditions") or []), fiche


def test_condition_valide_acceptee():
    """Une condition 3.5 légitime (« Endormi ») reste acceptée."""
    d = tempfile.mkdtemp(prefix="dnd35_cond97b_")
    os.makedirs(os.path.join(d, "fiches"), exist_ok=True)
    with open(os.path.join(d, "fiches", "fiche_faelan.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "nom": "Faelan", "pv": 10, "pv_max": 11,
            "conditions": [], "joueur": "Testeur",
        }, f, ensure_ascii=False)
    tools = discover_tools()
    spec = tools.get("fiche_perso_condition")
    tr = asyncio.run(invoke_tool(
        spec, ToolContext(partie_id="t_97e94bd1", joueur="Testeur",
                          data_dir=d),
        {"nom": "Faelan", "condition": "Endormi"},
    ))
    assert "⛔" not in tr.text, tr.text
    fiche = json.load(open(os.path.join(d, "fiches", "fiche_faelan.json"),
                           encoding="utf-8"))
    assert "Endormi" in (fiche.get("conditions") or []), fiche
