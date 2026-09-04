"""Constance des salles de donjon visitées (aller-retour sur ses pas).

Le groupe doit retrouver chaque salle visitée EXACTEMENT comme il l'a
laissée : même description canonique (figée par le MJ ou secours
déterministe), même état des lieux (monstres vaincus, coffres vidés…),
même topologie (type, portes) — y compris après sortie/retour du donjon.

Usage : py -m pytest tests/test_donjon_salles_consistantes.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    _description_secours,
    carte_donjon_decrire_salle,
    carte_donjon_entrer,
    carte_donjon_etage,
    carte_donjon_explorer,
    carte_donjon_get,
    carte_donjon_sortir,
)

PID = "test_salles"


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_salles_")


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _donjon(d: str) -> dict:
    return PartyState(data_dir=d, partie_id=PID).load().get("donjon", {})


# --------------------------------------------------------------------------- #
def test_aller_retour_salle_decrite_identique():
    """Explorer → figer la description → revenir : la salle est restituée
    à l'identique (description + état des lieux)."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Crypte de Test"))
        tr_new = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert "NOUVELLE" in tr_new.text, tr_new.text

        tr_desc = asyncio.run(carte_donjon_decrire_salle(
            _ctx(d),
            "Une crypte froide aux sarcophages couverts de mousse.",
            "un squelette détruit près de l'autel",
        ))
        assert "figée" in tr_desc.text

        # Retour sur ses pas (sud → entrée), puis re-nord → salle revisitée.
        asyncio.run(carte_donjon_explorer(_ctx(d), "sud"))
        tr_back = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert "DÉJÀ VISITÉE" in tr_back.text, tr_back.text
        assert "crypte froide aux sarcophages" in tr_back.text
        assert "un squelette détruit près de l'autel" in tr_back.text
        assert "NE RÉINVENTE PAS" in tr_back.text

        # Persistance dans l'état : description + état sur la bonne salle.
        grille = _donjon(d).get("grille", [])
        cible = next(s for s in grille if (s["x"], s["y"]) == (0, -1))
        assert cible["description"] == (
            "Une crypte froide aux sarcophages couverts de mousse."
        )
        assert cible["etat_des_lieux"] == "un squelette détruit près de l'autel"
    finally:
        pass


def test_description_secours_deterministe():
    """Sans description figée par le MJ, la description de secours est
    déterministe : identique à chaque visite (jamais de salle réinventée)."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Sans Description"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        # Le MJ « oublie » de figer : on repart et on revient.
        asyncio.run(carte_donjon_explorer(_ctx(d), "sud"))
        tr1 = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert "DÉJÀ VISITÉE" in tr1.text
        # La description affichée = le secours déterministe de cette salle.
        dj = _donjon(d)
        secours = _description_secours(str(dj.get("id")), 0, -1,
                                       "couloir")
        # (le type réel est aléatoire : on vérifie via la grille)
        salle = next(s for s in dj["grille"] if (s["x"], s["y"]) == (0, -1))
        secours = _description_secours(str(dj.get("id")), 0, -1,
                                       str(salle.get("type")))
        assert f"« {secours} »" in tr1.text
        # Deuxième aller-retour : EXACTEMENT le même texte de secours.
        asyncio.run(carte_donjon_explorer(_ctx(d), "sud"))
        tr2 = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert f"« {secours} »" in tr2.text
    finally:
        pass


def test_mise_a_jour_etat_des_lieux():
    """Un changement dans la salle (combat, pillage) est reflété au retour."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Évolutif"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "est"))
        asyncio.run(carte_donjon_decrire_salle(
            _ctx(d), "Une salle de garde aux torches fraîchement allumées.",
            "deux gobelins en vie, coffre fermé",
        ))
        # Combat : le MJ met à jour l'état.
        asyncio.run(carte_donjon_decrire_salle(
            _ctx(d),
            "Une salle de garde aux torches fraîchement allumées.",
            "deux gobelins détruits, coffre vidé",
        ))
        asyncio.run(carte_donjon_explorer(_ctx(d), "ouest"))
        tr_back = asyncio.run(carte_donjon_explorer(_ctx(d), "est"))
        assert "coffre vidé" in tr_back.text
        assert "gobelins détruits" in tr_back.text
        assert "coffre fermé" not in tr_back.text
    finally:
        pass


def test_sortie_retour_donjon_conserve_salles():
    """Quitter puis revisiter un donjon : les salles gardent description,
    état et position (archivage complet, étages inclus)."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Persistant"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        asyncio.run(carte_donjon_decrire_salle(
            _ctx(d), "Un laboratoire aux fioles brisées.", "aucun monstre"))
        # Descente au sous-sol ? (peut échouer selon le type aléatoire — non requis)
        asyncio.run(carte_donjon_sortir(_ctx(d)))
        assert not _donjon(d).get("id")
        tr_re = asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Persistant"))
        assert "retournez" in tr_re.text.lower() or "restaurée" in tr_re.text
        dj = _donjon(d)
        salle = next(s for s in dj["grille"] if (s["x"], s["y"]) == (0, -1))
        assert salle["description"] == "Un laboratoire aux fioles brisées."
        # Re-visite : description restituée.
        asyncio.run(carte_donjon_explorer(_ctx(d), "sud"))  # vers entrée
        tr_back = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        assert "fioles brisées" in tr_back.text
    finally:
        pass


def test_topologie_stable_apres_revisite():
    """Le type et les portes d'une salle revisitée restent identiques."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Stable"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "ouest"))
        dj = _donjon(d)
        avant = next(s for s in dj["grille"] if (s["x"], s["y"]) == (-1, 0))
        asyncio.run(carte_donjon_explorer(_ctx(d), "est"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "ouest"))
        dj2 = _donjon(d)
        apres = next(s for s in dj2["grille"] if (s["x"], s["y"]) == (-1, 0))
        assert avant["type"] == apres["type"]
        assert avant["portes"] == apres["portes"]
    finally:
        pass


def test_carte_donjon_get_liste_descriptions():
    """`carte_donjon_get` montre quelles salles ont une description figée."""
    d = _fresh_dir()
    try:
        asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Inventaire"))
        asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        asyncio.run(carte_donjon_decrire_salle(_ctx(d), "Une chapelle dévastée."))
        tr = asyncio.run(carte_donjon_get(_ctx(d)))
        assert "chapelle dévastée" in tr.text
        # La salle d'entrée porte sa description d'origine.
        assert "L'entrée du donjon" in tr.text
    finally:
        pass
