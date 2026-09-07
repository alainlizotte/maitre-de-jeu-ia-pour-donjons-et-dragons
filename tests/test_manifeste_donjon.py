"""Manifestes de donjons par scénario (plans pré-générés fidèles au module).

`carte_donjon_entrer` doit charger le plan canonique du scénario quand un
`*.donjon.json` existe (champ `scenario` = id de la quête) : disposition,
descriptions figées et contenu des salles (ennemis/trésors/pièges/PNJ)
restitués par `carte_donjon_explorer`. Révélation salle par salle conservée
(`visitee`), repli procédural sans manifeste.

Le générateur `scripts/generer_donjon_scenario.py` produit des brouillons
déterministes et connexes pour les autres scénarios.

Usage : py -m pytest tests/test_manifeste_donjon.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = Path(__file__).resolve().parent.parent

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    carte_donjon_entrer,
    carte_donjon_explorer,
    _donjon_depuis_manifeste,
)
from scripts.generer_donjon_scenario import (  # noqa: E402
    construire_manifeste,
)

PID = "test_manifeste"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _etat(d: str) -> dict:
    return PartyState(data_dir=d, partie_id=PID).load()


MANIFESTE_TEST = {
    "id": "test_scenario_donjon",
    "scenario": "test_scenario_x",
    "donjon_id": "Crypte du Scénario Test",
    "etages": [
        {
            "nom": "Nef supérieure",
            "entree": [0, 0],
            "salles": [
                {
                    "x": 0, "y": 0, "type": "entrée",
                    "portes": {"nord": True, "sud": False, "est": False,
                               "ouest": False},
                    "description": "Le vestibule du scénario test.",
                    "pnj": ["Guide du module"],
                },
                {
                    "x": 0, "y": -1, "type": "garde",
                    "portes": {"nord": True, "sud": True, "est": False,
                               "ouest": False},
                    "description": "La salle de garde du module.",
                    "ennemis": ["Zombie ×2"],
                },
                {
                    "x": 0, "y": -2, "type": "salle du trône",
                    "portes": {"nord": False, "sud": True, "est": False,
                               "ouest": False},
                    "description": "L'antre du boss.",
                    "ennemis": ["Goule ×1"],
                    "tresor": "Coffre du module (100 pc)",
                },
            ],
        },
        {
            "nom": "Crypte profonde",
            "entree": [0, 0],
            "salles": [
                {
                    "x": 0, "y": 0, "type": "escaliers",
                    "portes": {"nord": True, "sud": False, "est": False,
                               "ouest": False},
                    "description": "Le pied de l'escalier.",
                },
                {
                    "x": 0, "y": -1, "type": "trésor",
                    "portes": {"nord": False, "sud": True, "est": False,
                               "ouest": False},
                    "description": "Le vrai trésor.",
                    "tresor": "Relique du module",
                },
            ],
        },
    ],
}


def _setup_scenario(d: str) -> None:
    """Partie avec quête `test_scenario_x` + manifeste posé dans scenarios/."""
    st = PartyState(data_dir=d, partie_id=PID)
    etat = st.load()
    etat["quete"] = {
        "titre": "Module Test",
        "pitch": "",
        "source": "[test_scenario_x] /data/scenarios/Test/Module Test.pdf",
    }
    st.save(etat)
    dossier = Path(d) / "scenarios" / "Test"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "Module Test.donjon.json").write_text(
        json.dumps(MANIFESTE_TEST, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
def test_entree_charge_le_plan_du_scenario():
    d = tempfile.mkdtemp(prefix="dnd35_manifeste_")
    try:
        _setup_scenario(d)
        r = asyncio.run(carte_donjon_entrer(_ctx(d), "Peu importe le nom"))
        assert "plan du scénario chargé" in r.text, r.text
        dj = _etat(d)["donjon"]
        # Grille = manifeste (3 salles étage 0), pas l'entrée procédurale.
        assert len(dj["grille"]) == 3
        assert dj["courant"] == [0, 0]
        entree = next(s for s in dj["grille"]
                      if (s["x"], s["y"]) == (0, 0))
        assert entree["description"] == "Le vestibule du scénario test."
        assert entree["visitee"] is True
        # Les autres salles démarrent masquées (révélation progressive).
        autres = [s for s in dj["grille"] if not (s["x"] == 0 and s["y"] == 0)]
        assert all(s.get("visitee") is False for s in autres)
        # Les 2 étages du manifeste sont pré-chargés (avec leur nom).
        assert set(dj["etages"].keys()) == {"0", "1"}
        assert dj["etages"]["1"]["nom"] == "Crypte profonde"
        # PNJ de l'entrée restitué.
        assert "Cassyt" not in r.text and "Guide du module" in r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_entree_liste_les_portes_existantes():
    """Le résultat de `carte_donjon_entrer` DOIT lister les portes réelles de
    la salle d'entrée : au tour d'entrée, le bloc « CARTE DU DONJON » du
    prompt système n'existe pas encore (donjon créé mid-tour) — sans ligne
    dédiée dans le tool, le petit modèle inventait des sorties (bug observé
    en partie : narration « nord, sud, est » pour une entrée nord+ouest)."""
    d = tempfile.mkdtemp(prefix="dnd35_manifeste_portes_")
    try:
        _setup_scenario(d)
        r = asyncio.run(carte_donjon_entrer(_ctx(d), "Crypte Portes"))
        # L'entrée (0,0) du manifeste n'ouvre QU'AU NORD : la ligne doit
        # la lister et interdire toute autre direction.
        assert "Portes EXISTANTES dans la salle d'entrée : nord." in r.text, (
            r.text)
        assert "SEULES sorties" in r.text, r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_explorer_reste_contenu_canonique():
    d = tempfile.mkdtemp(prefix="dnd35_manifeste_")
    try:
        _setup_scenario(d)
        asyncio.run(carte_donjon_entrer(_ctx(d), "Peu importe"))
        r = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        # Description canonique du module (pas « Salle NOUVELLE à figer »).
        assert "Salle DU SCÉNARIO" in r.text, r.text
        assert "salle de garde du module" in r.text
        # Contenu canonique : ennemis du scénario.
        assert "Contenu canonique de la salle (scénario)" in r.text
        assert "Zombie ×2" in r.text
        assert "engager_combat" in r.text
        # La salle est maintenant révélée sur la carte.
        dj = _etat(d)["donjon"]
        garde = next(s for s in dj["grille"] if (s["x"], s["y"]) == (0, -1))
        assert garde["visitee"] is True
        # La protection anti-réinvention est active : re-décrire → refus.
        from server.tools.cartes import carte_donjon_decrire_salle
        r2 = asyncio.run(carte_donjon_decrire_salle(
            _ctx(d), "Une autre description inventée."))
        assert "CONSERVÉE" in r2.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_explorer_precise_arrivee_et_contenu_des_portes():
    """Anti-bug réel : en entrant au NORD dans la salle des banquets, le MJ
    narrait « vous êtes entrés par l'EST » et inventait le contenu des
    passages (« au sud, un escalier… »). Le résultat du tool DOIT préciser
    la porte PAR LAQUELLE le groupe arrive et ce qui est connu derrière
    chaque porte (visité = description figée ; inexploré = rien ne est su)."""
    d = tempfile.mkdtemp(prefix="dnd35_manifeste_arrivee_")
    try:
        _setup_scenario(d)
        asyncio.run(carte_donjon_entrer(_ctx(d), "Crypte Arrivée"))
        r = asyncio.run(carte_donjon_explorer(_ctx(d), "nord"))
        # Direction d'arrivée explicite (l'entrée était au SUD de la salle).
        assert "ARRIVÉ ICI par la porte SUD" in r.text, r.text
        # Derrière la porte SUD : l'entrée, DÉJÀ VISITÉE, avec sa description.
        assert "porte SUD → (0,0) « entrée » DÉJÀ VISITÉE" in r.text, r.text
        assert "Le vestibule du scénario test." in r.text, r.text
        # Derrière la porte NORD : inexploré — rien ne doit être improvisé.
        assert "porte NORD" in r.text and "NON exploré" in r.text, r.text
        # L'info persiste dans l'état pour le bloc « source de vérité ».
        assert _etat(d)["donjon"]["arrivee_par"] == "sud"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_repli_procedural_sans_manifeste():
    d = tempfile.mkdtemp(prefix="dnd35_manifeste_")
    try:
        r = asyncio.run(carte_donjon_entrer(_ctx(d), "Donjon Sans Plan"))
        assert "plan du scénario chargé" not in r.text
        assert "rez-de-chaussée" in r.text.lower()
        dj = _etat(d)["donjon"]
        assert len(dj["grille"]) == 1 and not dj.get("manifeste")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_manifeste_dues_for_the_dead_valide():
    """Le manifeste réel de Dues for the Dead : schéma, topologie, équilibrage."""
    p = (_REPO / "server" / "data" / "scenarios" / "Divers"
         / "Dues for the Dead.donjon.json")
    assert p.is_file(), "le manifeste de Dues for the Dead doit exister"
    man = json.loads(p.read_text(encoding="utf-8"))
    assert man["scenario"] == "divers_dues_for_the_dead"
    assert len(man["etages"]) == 2
    import re as _re
    from server.tools.scenarios import ennemis_du_resume  # monstres connus
    bestiaire = json.loads(
        (_REPO / "server" / "data" / "bestiaire.json").read_text(
            encoding="utf-8"))
    noms_bestiaire = {
        str(v.get("nom") or "").lower()
        for k, v in bestiaire.items()
        if k != "_meta" and isinstance(v, dict)
    }
    for fl in man["etages"]:
        salles = fl["salles"]
        cles = {(s["x"], s["y"]) for s in salles}
        # Portes valides et menant à des salles existantes.
        dirs = {"nord": (0, -1), "sud": (0, 1), "est": (1, 0), "ouest": (-1, 0)}
        for s in salles:
            for d, (dx, dy) in dirs.items():
                if s["portes"].get(d):
                    assert (s["x"] + dx, s["y"] + dy) in cles, (
                        f"porte {d} orpheline depuis {(s['x'], s['y'])}")
        # Connexité depuis l'entrée.
        entree = tuple(fl["entree"])
        vus = {entree}
        pile = [entree]
        while pile:
            x, y = pile.pop()
            s = next(r for r in salles if (r["x"], r["y"]) == (x, y))
            for d, (dx, dy) in dirs.items():
                if s["portes"].get(d):
                    v = (x + dx, y + dy)
                    if v in cles and v not in vus:
                        vus.add(v)
                        pile.append(v)
        assert vus == cles, f"salle(s) inaccessible(s) : {cles - vus}"
        # Tous les ennemis existent dans le bestiaire et sont ≤ FP 5
        # (plafond d'un groupe solo niveau 1).
        for s in salles:
            for e in (s.get("ennemis") or []):
                nom = _re.sub(r"\s*×\d+$", "", str(e)).strip()
                assert nom.lower() in noms_bestiaire, (
                    f"ennemi hors bestiaire : {nom}")
                from server.tools.state import _cr_numerique
                fiche = next(
                    (v for v in bestiaire.values()
                     if isinstance(v, dict)
                     and str(v.get("nom") or "").lower() == nom.lower()),
                    None,
                )
                cr = _cr_numerique((fiche or {}).get("fp"))
                assert cr is not None and cr <= 5, (
                    f"{nom} (FP {fiche.get('fp')}) dépasse le plafond FP 5")


def test_donne_depuis_manifeste_normalise():
    dj = _donjon_depuis_manifeste(MANIFESTE_TEST)
    assert dj is not None
    assert dj["id"] == "Crypte du Scénario Test"
    assert dj["courant"] == [0, 0]
    assert dj["salles_visitees"] == ["0,0"]
    fl1 = dj["etages"]["1"]
    assert fl1["nom"] == "Crypte profonde"
    # Sans étages → None (repli procédural).
    assert _donjon_depuis_manifeste({"etages": []}) is None


def test_generateur_brouillons_deterministes_et_connexes():
    import sys as _sys
    _sys.path.insert(0, str(_REPO / "scripts"))
    bestiaire = json.loads(
        (_REPO / "server" / "data" / "bestiaire.json").read_text(
            encoding="utf-8"))
    best = {
        "monstres": {
            v.get("cle", k): v
            for k, v in bestiaire.items()
            if k != "_meta" and isinstance(v, dict) and "nom" in v
        }
    }
    texte = ("The crypt holds zombies and skeletons. A ghoulish necromancer "
             "leads them from his throne room.")
    m1 = construire_manifeste("scen_test", texte, best, nb_salles=10)
    m2 = construire_manifeste("scen_test", texte, best, nb_salles=10)
    assert m1 == m2, "la génération doit être déterministe (graine = sid)"
    salles = m1["etages"][0]["salles"]
    assert salles[0]["type"] == "entrée"
    assert salles[-1]["type"] == "salle du trône"
    # Boss (créature de plus haut FP détectée) dans la salle finale.
    assert salles[-1].get("ennemis"), "le boss doit être placé"
    # Connexité : BFS depuis l'entrée.
    dirs = {"nord": (0, -1), "sud": (0, 1), "est": (1, 0), "ouest": (-1, 0)}
    cles = {(s["x"], s["y"]) for s in salles}
    vus = {(0, 0)}
    pile = [(0, 0)]
    while pile:
        x, y = pile.pop()
        s = next(r for r in salles if (r["x"], r["y"]) == (x, y))
        for d, (dx, dy) in dirs.items():
            if s["portes"].get(d):
                v = (x + dx, y + dy)
                if v in cles and v not in vus:
                    vus.add(v)
                    pile.append(v)
    assert vus == cles, f"salle(s) inaccessible(s) : {cles - vus}"
    # Chargable par le loader.
    dj = _donjon_depuis_manifeste(m1)
    assert dj is not None and len(dj["grille"]) == 10
