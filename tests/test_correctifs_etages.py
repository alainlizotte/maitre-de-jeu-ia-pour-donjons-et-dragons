# -*- coding: utf-8 -*-
"""Navigation d'étages de donjon (partie 60446e2f — Unknown Tower, 7 niveaux).

Défauts observés en jeu :

1. **Étages supérieurs inatteignables** : `carte_donjon_etage` imposait la
   convention index figée (0 = sommet, « monter » = index-1, « descendre » =
   index+1) alors que les manifestes ordonnent leurs niveaux de façon
   NARRATIVE — Unknown Tower : 0=RDC, 1-2=étages SUPÉRIEURS, 3-6=sous-sols.
   « je monte l'escalier » depuis le RDC répondait « déjà au rez-de-chaussée »
   et la tour restait à l'étage 0 pour toujours. Correctif : liens verticaux
   explicites par étage dans le manifeste (`liens.au_dessus` /
   `liens.au_dessous`), fallback = ancienne convention.
2. **Filet étage mort-né** : `_INTENT_MONTER_RE` / `_INTENT_DESCENDRE_RE`
   étaient utilisées dans `main.py` sans y être définies ni importées →
   NameError avalé par le `except` silencieux → le rattrapage déterministe
   n'a JAMAIS fonctionné (aucun « ⚙️ Le serveur a fait changer d'étage » en
   20+ tours). Correctif : regexes définies localement dans main.py et
   `_deja_etage` ne compte que les appels REUSSIS (le 9B appelle avec une
   direction invalide : `{"direction": "est"}` → ok=False).
3. **Salles d'escalier dédiées** : 10 donjons multi-étages n'avaient AUCUNE
   salle de type « escaliers » (escaliers décrits en prose uniquement) ;
   ajout de salles dédiées avec portes RÉCIPROQUES (modèle « Dues for the
   Dead », où la mécanique fonctionnait).

Usage : python -m pytest tests/test_correctifs_etages.py -q
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
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
_MANIFESTE = {
    "id": "tour_test",
    "scenario": "t_etages",
    "donjon_id": "Tour de test",
    "titre": "La Tour de test",
    "note_convention": "fixture de test",
    "etages": [
        {
            "nom": "Rez-de-chaussée",
            "liens": {"au_dessus": 1, "au_dessous": 2},
            "salles": [
                {"x": 0, "y": 0, "type": "entrée",
                 "description": "Le hall pavé de la tour.",
                 "portes": {"nord": False, "sud": True, "est": False,
                            "ouest": False}},
            ],
        },
        {
            "nom": "Second niveau",
            "liens": {"au_dessus": None, "au_dessous": 0},
            "salles": [
                {"x": 0, "y": 0, "type": "escaliers",
                 "description": "Un escalier de pierre.",
                 "portes": {"nord": False, "sud": True, "est": False,
                            "ouest": False}},
            ],
        },
        {
            "nom": "Sous-sol",
            "liens": {"au_dessus": 0, "au_dessous": None},
            "salles": [
                {"x": 0, "y": 0, "type": "escaliers",
                 "description": "Un escalier qui descend.",
                 "portes": {"nord": False, "sud": False, "est": False,
                            "ouest": True}},
            ],
        },
    ],
}


def _ecrire_manifeste(d: str, liens: bool = True) -> None:
    sc = os.path.join(d, "scenarios")
    os.makedirs(sc, exist_ok=True)
    man = json.loads(json.dumps(_MANIFESTE))
    if not liens:
        for fl in man["etages"]:
            fl.pop("liens", None)
    with open(os.path.join(sc, "tour_test.donjon.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)


def _partie(d: str, *, etage: int = 0, courant: tuple = (0, 0),
            type_salle: str = "entrée",
            description: str = "Le hall pavé de la tour.",
            liens: bool = True) -> None:
    """Partie en exploration, donjon actif dérivé du manifeste."""
    grille = [{
        "x": courant[0], "y": courant[1], "type": type_salle,
        "visitee": True, "description": description,
        "portes": {"nord": False, "sud": False, "est": True, "ouest": False},
    }]
    etages_store = {
        str(i): {
            "nom": (fl["nom"] if liens else ""),
            "grille": [{"x": s["x"], "y": s["y"], "type": s["type"],
                        "visitee": True, "description": s["description"],
                        "portes": s.get("portes", {})}
                       for s in (fl["salles"] if liens else [])],
            "salles_visitees": [f"{courant[0]},{courant[1]}"],
            "portes_bloquees": [],
            "courant": list(courant),
        }
        for i, fl in enumerate(_MANIFESTE["etages"])
    }
    man = json.loads(json.dumps(_MANIFESTE))
    if not liens:
        for fl in man["etages"]:
            fl.pop("liens", None)
    PartyState(data_dir=d, partie_id="t_etages").save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Faelan", "pv": 9, "pv_max": 9, "joueur": "Testeur",
                "niveau": 1}],
        "pnj": [],
        "quete": {"source": "[t_etages] fixture"},
        "donjon": {
            "id": "Tour de test",
            "manifeste": man,
            "etage": etage,
            "courant": list(courant),
            "salles_visitees": [f"{courant[0]},{courant[1]}"],
            "portes_bloquees": [],
            "grille": grille,
            "etages": etages_store,
        },
        "histoire": [],
    })


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id="t_etages", joueur="Testeur", data_dir=d)


def _appeler_etage(d: str, direction: str):
    os.makedirs(os.path.join(d, "cartes"), exist_ok=True)
    tools = discover_tools()
    spec = tools.get("carte_donjon_etage")
    return asyncio.run(invoke_tool(spec, _ctx(d), {"direction": direction}))


# --------------------------------------------------------------------------- #
#  1. Liens verticaux du manifeste
# --------------------------------------------------------------------------- #
def test_monter_via_liens_manifeste():
    """Depuis le RDC (0=sol), « monter » doit atteindre l'étage lié
    `au_dessus` (1 = Second niveau) — l'ancienne convention répondait
    « déjà au rez-de-chaussée » (partie 60446e2f)."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0)
    tr = _appeler_etage(d, "monter")
    assert "Second niveau" in tr.text, tr.text
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 1, etat["donjon"]["etage"]
    # le nom du niveau est semé depuis le manifeste
    assert etat["donjon"]["etages"]["1"].get("nom") == "Second niveau"


def test_descendre_via_liens_manifeste():
    """Depuis le Second niveau, « descendre » doit revenir au RDC
    (`au_dessous: 0`) — et non à l'index+1."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=1)
    tr = _appeler_etage(d, "descendre")
    assert "Rez-de-chaussée" in tr.text, tr.text
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 0


def test_monter_depuis_sommet_refuse():
    """au_dessus=None (le sommet) → refus clair, pas de changement."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=1)
    tr = _appeler_etage(d, "monter")
    assert tr.text.startswith("🚫 Pas d'étage au-dessus"), tr.text
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 1


def test_descendre_depuis_bas_refuse():
    """au_dessous=None (le fond) → refus clair."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=2)
    tr = _appeler_etage(d, "descendre")
    assert tr.text.startswith("🚫 Pas d'étage plus bas"), tr.text
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 2


def test_manifeste_sans_liens_fallback_convention():
    """Sans `liens`, l'ancienne convention index est préservée (Dues for the
    Dead fonctionne toujours) : monter depuis l'index 0 → refus ; descendre
    → index+1."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d, liens=False)
    _partie(d, etage=0, liens=False)
    tr = _appeler_etage(d, "monter")
    assert "déjà au rez-de-chaussée" in tr.text, tr.text
    tr2 = _appeler_etage(d, "descendre")
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 1, etat["donjon"]["etage"]
    assert tr2 is not None


# --------------------------------------------------------------------------- #
#  2. Gate : où le changement d'étage est légitime
# --------------------------------------------------------------------------- #
def test_gate_jonction_entree_0_0():
    """La salle d'entrée (0,0) est une JONCTION d'étage même sans escalier
    décrit (les modules écrivent la liaison dans la première salle du
    niveau)."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0, description="Le hall pavé, sans escalier mentionné.")
    tr = _appeler_etage(d, "monter")
    assert "Second niveau" in tr.text, tr.text


def test_gate_refuse_salle_sans_escalier():
    """Hors jonction (1,0), une salle sans escalier → refus."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0, courant=(1, 0), type_salle="garde",
            description="L'écurie sombre.")
    tr = _appeler_etage(d, "monter")
    assert tr.text.startswith("🚫 Pas d'escalier"), tr.text


def test_gate_accepte_escalier_decrit_en_prose():
    """Une salle dont la DESCRIPTION mentionne un escalier passe le gate
    (Unknown Tower : l'escalier branlant du hall)."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0, courant=(1, 0), type_salle="garde",
            description="Un escalier branlant grimpe le long du mur est.")
    tr = _appeler_etage(d, "monter")
    assert "Second niveau" in tr.text, tr.text


# --------------------------------------------------------------------------- #
#  3. Tolérance des directions (le 9B invente : « haut », « est »…)
# --------------------------------------------------------------------------- #
def test_direction_synonyme_haut():
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0)
    tr = _appeler_etage(d, "haut")
    assert "Second niveau" in tr.text, tr.text


def test_direction_invalide_est_refusee():
    """`{"direction": "est"}` (observé chez le 9B) → refus EXPLICITE (le
    filet serveur reprend ensuite avec le sens du message joueur)."""
    d = tempfile.mkdtemp(prefix="dnd35_etages_")
    _ecrire_manifeste(d)
    _partie(d, etage=0)
    tr = _appeler_etage(d, "est")
    assert tr.text.startswith("❌ Direction invalide"), tr.text
    etat = PartyState(data_dir=d, partie_id="t_etages").load()
    assert etat["donjon"]["etage"] == 0


# --------------------------------------------------------------------------- #
#  4. Le filet étage de main.py est vivant
# --------------------------------------------------------------------------- #
def test_regexes_intention_definies_dans_main():
    """`_INTENT_MONTER_RE` / `_INTENT_DESCENDRE_RE` étaient utilisées dans
    main.py sans y être définies → NameError avalé → filet mort-né. Elles
    doivent exister et matcher les formulations observées en jeu."""
    import server.main as main_mod  # noqa: PLC0415
    txt_monter = [
        "je monte l escalier vers l etage superieur",
        "je remonte au rez-de-chaussée",
        "je grimpe vers le haut",
    ]
    for t in txt_monter:
        assert main_mod._INTENT_MONTER_RE.search(t), t
    txt_descendre = [
        "je redescends par l escalier a l etage inferieur",
        "je vais au sous-sol",
        "je descends vers le bas",
    ]
    for t in txt_descendre:
        assert main_mod._INTENT_DESCENDRE_RE.search(t), t


def test_filet_ne_compte_que_les_appels_reussis():
    """Le garde `_deja_etage` doit exiger `tc.get("ok")` : un appel en erreur
    (`{"direction": "est"}` → ok=False) ne doit PAS bloquer le rattrapage
    déterministe (partie 60446e2f : l'étage ne changeait jamais)."""
    chemin = os.path.join(sys_path, "server", "main.py")
    src = open(chemin, encoding="utf-8").read()
    i = src.find("_deja_etage = any(")
    assert i > 0, "bloc _deja_etage introuvable"
    bloc = src[i:i + 260]
    assert 'tc.get("ok")' in bloc, bloc
