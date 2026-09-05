"""Carte du donjon INFAILLIBLE + séquencement de combat conforme D&D 3.5.

Régressions couvertes (partie réelle « d3f17c6f ») :

1. Le MJ narrait une porte « au nord » ABSENTE de la grille : la narration
   inventait la géographie car le prompt ne contenait AUCUN état du donjon.
   → le prompt embarque désormais la carte (salle courante, portes ouvertes,
   descriptions figées) et `carte_donjon_explorer` refuse toute direction
   sans porte en listant les portes réelles.
2. `carte_donjon_decrire_salle` écrasait la description figée d'une salle :
   une narration erronée (salle inventée) corrompait la mémoire du donjon.
3. Combat : le joueur attaquait DEUX fois avant toute riposte (le premier
   assaut narré — « Ghoul » vs bestiaire « Goule » — ne déclenchait pas
   `engager_combat`), et l'action du joueur était résolue AVANT celle du
   monstre gagnant d'initiative (round 1 dans le désordre).

Usage : py -m pytest tests/test_carte_infaillible.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.llm.prompt_builder import _donjon_bloc  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    carte_donjon_decrire_salle,
    carte_donjon_entrer,
    carte_donjon_explorer,
)
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_carte_inf"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir(avec_bestiaire: bool = False) -> str:
    d = tempfile.mkdtemp(prefix="dnd35_carte_inf_")
    if avec_bestiaire:
        shutil.copy2(
            os.path.join(_REPO, "server", "data", "bestiaire.json"),
            os.path.join(d, "bestiaire.json"),
        )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _etat(d: str) -> dict:
    return PartyState(data_dir=d, partie_id=PID).load()


async def tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


def _force_portes(d: str, portes: dict[str, bool]) -> None:
    """Fixe les portes de la salle courante (0,0) de façon déterministe."""
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    for s in etat["donjon"]["grille"]:
        if (s.get("x"), s.get("y")) == (0, 0):
            s["portes"] = dict(portes)
    st.save(etat)


# --------------------------------------------------------------------------- #
#  1. Carte : refus des directions sans porte (avec portes réelles listées)
# --------------------------------------------------------------------------- #
def test_refus_direction_sans_porte_liste_les_portes_reelles():
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Crypte Fermée"))
        _force_portes(d, {"nord": False, "sud": True, "est": False,
                          "ouest": False})
        tr = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert tr.text.startswith("🚫"), tr.text
        assert "nord" in tr.text
        assert "sud" in tr.text, (
            f"les portes réelles doivent être listées : {tr.text}"
        )
        # La position n'a PAS changé (aucun déplacement fantôme).
        assert _etat(d)["donjon"]["courant"] == [0, 0]
        # La direction valide passe toujours.
        tr_ok = asyncio.run(carte_donjon_explorer(_ctx(d), "sud"))
        assert not tr_ok.text.startswith("🚫"), tr_ok.text
        assert _etat(d)["donjon"]["courant"] == [0, 1]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  2. Carte : la description figée n'est JAMAIS réinventée
# --------------------------------------------------------------------------- #
def test_decrire_salle_conserve_la_description_figee():
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Crypte Figée"))
        # La salle d'entrée porte une description par défaut : on repart
        # d'une salle SANS description figée (cas réel d'une salle nouvelle).
        st = PartyState(data_dir=d, partie_id=PID)
        etat = st.load()
        for s in etat["donjon"]["grille"]:
            if (s.get("x"), s.get("y")) == (0, 0):
                s["description"] = ""
        st.save(etat)

        canonique = "Une salle voûtée aux piliers de basalte noir."
        tr1 = asyncio.run(carte_donjon_decrire_salle(
            _ctx(d), canonique, "rien à signaler"))
        assert "figée" in tr1.text, tr1.text

        # 2e passage : le MJ « réinvente » la salle (narration erronée) →
        # refus, description canonique conservée, état des lieux mis à jour.
        tr2 = asyncio.run(carte_donjon_decrire_salle(
            _ctx(d),
            "Un couloir étroit couvert de graffitis sanglants.",
            "graffitis frais, traces de pas",
        ))
        assert "CONSERVÉE" in tr2.text, tr2.text
        assert "REFUSÉE" in tr2.text, tr2.text
        salle = next(
            s for s in _etat(d)["donjon"]["grille"]
            if (s.get("x"), s.get("y")) == (0, 0)
        )
        assert salle["description"] == canonique, (
            f"description figée écrasée : {salle['description']!r}"
        )
        assert salle["etat_des_lieux"] == "graffitis frais, traces de pas"
        # Re-décrire avec la MÊME description reste accepté (idempotent).
        tr3 = asyncio.run(carte_donjon_decrire_salle(
            _ctx(d), canonique, "coffre vidé"))
        assert "figée" in tr3.text, tr3.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  3. Carte : le prompt système embarque la carte (portes + salle courante)
# --------------------------------------------------------------------------- #
def test_prompt_donne_la_carte_au_mj():
    etat = {
        "donjon": {
            "id": "Catacombes de Test",
            "etage": 0,
            "courant": [0, -1],
            "grille": [
                {"x": 0, "y": 0, "type": "entrée", "visitee": True,
                 "description": "L'entrée du donjon.",
                 "portes": {"nord": True, "sud": False, "est": False,
                            "ouest": False}},
                {"x": 0, "y": -1, "type": "abattoir", "visitee": True,
                 "description": "Un abattoir macabre.",
                 "portes": {"nord": False, "sud": True, "est": True,
                            "ouest": False}},
            ],
        },
    }
    bloc = _donjon_bloc(etat)
    assert bloc, "le bloc carte doit exister quand un donjon est actif"
    assert "CARTE DU DONJON" in bloc
    assert "Salle COURANTE : (0,-1)" in bloc
    assert "abattoir" in bloc
    # Les portes RÉELLES de la salle courante, pas une de plus.
    assert "Portes EXISTANTES : est, sud" in bloc, bloc
    assert "RESPECT STRICT DE LA CARTE" in bloc
    # Sans donjon : aucun bloc (pas de bruit en ville).
    assert _donjon_bloc({"donjon": {}}) == ""
    assert _donjon_bloc({}) == ""


# --------------------------------------------------------------------------- #
#  4. Combat : prose de dégâts + nom anglais du monstre → engager_combat
# --------------------------------------------------------------------------- #
def test_detecter_combat_prose_nom_anglais_et_degats():
    from server.main import _detecter_combat_prose

    d = _fresh_dir(avec_bestiaire=True)
    try:
        # Cas réel observé : « Ghoul » (anglais) + dégâts narrés, AUCUN
        # marqueur explicite de combat → le rattrapage doit trouver « Goule ».
        types = _detecter_combat_prose(
            d,
            "La boule d'énergie frappe le Ghoul de plein fouet. "
            "**5 points de dégâts** sont infligés à cette créature. "
            "Il s'apprête à contre-attaquer.",
            {"phase": "exploration"},
        )
        assert "Goule" in types, types

        # Nom exact : toujours détecté.
        types2 = _detecter_combat_prose(
            d,
            "Le combat commence ! La goule se rue sur vous !",
            {"phase": "exploration"},
        )
        assert "Goule" in types2, types2

        # Pas de marqueur ni de dégâts → aucun rattrapage (faux positifs).
        types3 = _detecter_combat_prose(
            d,
            "Vous vous souvenez de la goule vaincue il y a un an, "
            "en repensant à cette histoire ancienne.",
            {"phase": "exploration"},
        )
        assert types3 == [], types3
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def _creer_pj(d: str, nom: str = "Groth") -> None:
    """Crée UN PJ (fiche déterministe) dans la partie de CE module de test."""
    r = await tool(
        d, "fiche_perso_creer_rapide",
        nom=nom, race="Humain", classe="Guerrier", niveau=1, joueur="alain",
        carac_texte="For 16, Dex 14, Con 16, Int 10, Sag 12, Cha 8",
    )
    assert r.text.startswith("✅"), r.text


# --------------------------------------------------------------------------- #
#  5. Combat : le monstre gagnant d'initiative joue AVANT le joueur
# --------------------------------------------------------------------------- #
async def test_engager_monstre_premier_joue_son_tour_avant_le_joueur(
        monkeypatch):
    d = _fresh_dir(avec_bestiaire=True)
    try:
        await _creer_pj(d)

        # Initiative déterministe : le Kobold tire 20, le PJ tire 1.
        import random as _random
        real_randint = _random.randint
        compteur = {"n": 0}

        def _randint(a: int, b: int) -> int:
            compteur["n"] += 1
            if compteur["n"] == 1:
                return 20          # kobold : initiative maximale
            if compteur["n"] == 2:
                return 1           # PJ : initiative minimale
            return real_randint(a, b)

        monkeypatch.setattr(_random, "randint", _randint)

        r = await tool(d, "engager_combat", monstres="Kobold")
        etat = _etat(d)
        parts = etat["initiative"]
        assert parts[0]["nom"] == "Kobold", (
            f"le kobold doit gagner l'initiative : {parts}"
        )
        # ⚔️ Le tour du kobold (round 1) a DÉJÀ été joué par le serveur :
        # le curseur est repassé sur un PJ capable d'agir.
        assert etat["courant_tour_pour"] in {
            p["nom"] for p in etat["pj"]
        }, etat["courant_tour_pour"]
        # Les événements mécaniques du round 1 sont dans le résultat du tool
        # (le MJ doit les narrer).
        assert "Mécanique résolue par le serveur" in r.text, r.text
        assert "Attaque" in r.text, r.text
        # ...et le combat est toujours en cours.
        assert etat["phase"] == "combat"
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_engager_joueur_premier_agit_en_premier(monkeypatch):
    d = _fresh_dir(avec_bestiaire=True)
    try:
        await _creer_pj(d)

        # Le PJ gagne l'initiative : AUCUN tour de monstre ne doit être
        # joué lors de l'engagement (la boucle est inerte).
        import random as _random
        real_randint = _random.randint
        compteur = {"n": 0}

        def _randint(a: int, b: int) -> int:
            compteur["n"] += 1
            if compteur["n"] == 1:
                return 1           # kobold : initiative minimale
            if compteur["n"] == 2:
                return 20          # PJ : initiative maximale
            return real_randint(a, b)

        monkeypatch.setattr(_random, "randint", _randint)

        r = await tool(d, "engager_combat", monstres="Kobold")
        etat = _etat(d)
        assert etat["initiative"][0]["nom"] == "Groth"
        assert etat["courant_tour_pour"] == "Groth"
        assert "Mécanique résolue par le serveur" not in r.text, (
            "aucun tour de monstre ne doit être joué quand le PJ est premier"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)
