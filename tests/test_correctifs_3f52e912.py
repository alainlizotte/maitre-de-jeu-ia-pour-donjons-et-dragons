# -*- coding: utf-8 -*-
"""Régressions de la partie 3f52e912 (Crown of Mystra, solo Morgoth).

Chaîne du bug — la réponse du MJ « rembobinait » le récit en pleine
aventure (intro rejouée, baguette de téléportation remise en contredisant
le verrou de quête, action du joueur effacée) :

1. la quête choisie via l'interface positionne `phase=« exploration »`
   AVANT le premier tour, et le petit modèle ne déplace jamais `phase` ;
2. le détecteur 5bis-f exigeait `phase ∈ opening…` pour initialiser le
   plan de donjon au tour d'ouverture → plan jamais initialisé ;
3. bien plus tard, la prose « …cours vers la caverne » (mot « caverne » +
   verbe d'entrée dans la même phrase fusionnée avec le message joueur,
   resté sans ponctuation finale) a déclenché l'entrée forcée
   `carte_donjon_entrer` EN PLEINE AVENTURE (téléportation salle 0,0) ;
4. `_tour_ouverture`, calculé sur la phase obsolète, a REMPLACÉ la
   réponse par la scène d'ouverture canonique re-narrée SANS historique.

Correctifs couverts ici :
- `_journaliser_ouverture_si_besoin` normalise `phase` (opening →
  opening_complete) au premier tour narré ;
- `_renarrer_ouverture` nomme les héros et interdit de remettre les
  objets prévus « plus tard » ;
- `_retirer_phrases_meta` supprime les fuites méta de la prose
  (« Les objets de quête sont déjà persistés… », noms d'outils) ;
- le récap minimal embarque le fil de l'histoire (le modèle conservait
  mal le fil quand la fenêtre d'historique tronquait le début) ;
- l'objectif COURANT marqué « bloque » est affiché au MJ comme
  l'objectif ACTIF (à accomplir), pas comme une impase.

Usage : py -m pytest tests/test_correctifs_3f52e912.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.main import (  # noqa: E402
    _entree_donjon_narree,
    _journaliser_ouverture_si_besoin,
    _renarrer_ouverture,
)
from server.llm.orchestrator import _retirer_phrases_meta  # noqa: E402


# --------------------------------------------------------------------------- #
#  1. Journalisation d'ouverture : normalisation de la phase
# --------------------------------------------------------------------------- #
def test_journaliser_ouverture_passe_phase_a_opening_complete():
    """Premier tour narré : `phase` « opening » → « opening_complete ».

    Le petit modèle oublie souvent de déplacer `phase` ; la laisser à
    « opening » des heures durant faisait croire aux détecteurs de tour
    d'ouverture que la partie n'avait pas commencé (3f52e912)."""
    etat = {"phase": "opening", "histoire": []}
    assert _journaliser_ouverture_si_besoin(
        etat, "Teleshann confie la quête de la Couronne.") is True
    assert etat["phase"] == "opening_complete"
    assert etat["histoire"][0]["evenement"].startswith("Début de l'aventure")


def test_journaliser_ouverture_laisse_exploration_intacte():
    """`phase=« exploration »` (quête choisie via l'interface) ne doit PAS
    être rétrogradée ni modifiée par la journalisation."""
    etat = {"phase": "exploration", "histoire": []}
    assert _journaliser_ouverture_si_besoin(etat, "…") is True
    assert etat["phase"] == "exploration"


def test_journaliser_ouverture_noop_si_histoire_non_vide():
    """Histoire déjà journalisée → aucun effet (et phase inchangée)."""
    etat = {"phase": "opening",
            "histoire": [{"ts": "t", "tour": "", "evenement": "x"}]}
    assert _journaliser_ouverture_si_besoin(etat, "…") is False
    assert etat["phase"] == "opening"
    assert len(etat["histoire"]) == 1


# --------------------------------------------------------------------------- #
#  2. Re-narration d'ouverture : héros nommés, objets futurs interdits
# --------------------------------------------------------------------------- #
class _ClientNarre:
    """Client factice : capture les messages, renvoie une prose fixe."""

    def __init__(self):
        self.messages = None

    async def chat(self, messages, temperature=None, **_kw):
        self.messages = messages

        class _R:
            content = "La scène d'ouverture narra le décor."

        return _R()


def test_renarrer_ouverture_nomme_les_heros_et_interdit_objets_futurs():
    """La re-narration canonique doit ancrer les héros RÉELS de la table et
    interdire de remettre un objet prévu « plus tard » (3f52e912 : la
    baguette de téléportation remise dès l'ouverture, contredisant le
    verrou de quête côté serveur)."""
    client = _ClientNarre()
    app = SimpleNamespace(state=SimpleNamespace(client=client))
    nar = asyncio.run(_renarrer_ouverture(
        app,
        "La Couronne de Mystra — entrée (0,0)",
        "La salle d'audience de la Tour de l'Équilibre… la baguette de "
        "téléportation qu'elle remettra ensuite…",
        "La Couronne de Mystra a disparu.",
        pjs_noms=["Morgoth (Demi-orc Barbare)"],
    ))
    assert nar == "La scène d'ouverture narra le décor."
    systeme = client.messages[0].content
    utilisateur = client.messages[1].content
    assert "Héros présents : Morgoth (Demi-orc Barbare)" in utilisateur
    assert "plus tard" in systeme.lower()
    assert "ne le remets PAS" in systeme
    assert "persistance" in systeme.lower()


# --------------------------------------------------------------------------- #
#  3. Filtre des phrases méta (« déjà persistés… », outils)
# --------------------------------------------------------------------------- #
def test_filtre_meta_retire_la_fuite_3f52e912():
    """La fuite EXACTE de la partie : phrase méta retirée, prose autour
    intacte, aucun artefact « . . » résiduel."""
    t = ("Morgoth hoche la tête, acceptant la mission. La magesteresse lui "
         "remet un parchemin. Les objets de quête sont déjà persistés dans "
         "l'inventaire de Morgoth. La magesteresse lui tend la baguette.")
    r = _retirer_phrases_meta(t)
    assert "persistés" not in r
    assert ". ." not in r
    assert "parchemin" in r and "baguette" in r


def test_filtre_meta_retire_outils_et_tool():
    """Noms d'outils snake_case et mot anglais « tool » : retirés."""
    t = ("Vous entrez dans la salle. inventaire_ajouter a bien fonctionné. "
         "The tool confirms the damage. Le gobelin s'écroule.")
    r = _retirer_phrases_meta(t)
    assert "inventaire_ajouter" not in r
    assert "tool" not in r.lower()
    assert "gobelin" in r


def test_filtre_meta_preserve_prose_legitime():
    """Mots français légitimes épargnés : « persistante », « outils » du
    forgeron — aucun texte légitime ne doit être altéré."""
    t = ("Une odeur persistante flotte dans la grotte. Le forgeron range "
         "ses outils avec soin. Puis il forge une lame.")
    assert _retirer_phrases_meta(t) == t


# --------------------------------------------------------------------------- #
#  4. Détection d'entrée de donjon : le déclencheur exact de 3f52e912
# --------------------------------------------------------------------------- #
def test_action_joueur_3f52e912_declenche_detection_entree():
    """Documente le déclencheur : le message joueur, resté SANS ponctuation
    finale, est collé à la narration — « caverne » (lieu) et « s'engage
    dans » (verbe) se retrouvent dans la même phrase fusionnée. C'est cette
    détection qui, combinée à la phase obsolète, a causé l'entrée forcée en
    pleine aventure ; le correctif 5bis-f ne re-narre PLUS l'ouverture dans
    ce cas (histoire non vide)."""
    texte = "Je me libere des racines et cours vers la caverne"
    narration = "Morgoth s'arrache aux racines et s'engage dans la caverne."
    assert _entree_donjon_narree(texte + " " + narration) is True


# --------------------------------------------------------------------------- #
#  5. Récap MJ : objectif courant « bloque » affiché ACTIF + fil d'histoire
# --------------------------------------------------------------------------- #
_MANIFEST = {
    "id": "crown_3f52e912",
    "scenario": ["crown_3f52e912"],
    "donjon_id": "La Couronne de Mystra",
    "etages": [{"nom": "Parcours", "entree": [0, 0], "salles": [
        {"x": 0, "y": 0, "type": "entrée", "portes": {"est": True},
         "visitee": True, "description": "La Tour de l'Équilibre."},
        {"x": 4, "y": 0, "type": "salle du trône", "portes": {"ouest": True},
         "description": "Le groove de Nulentok."},
    ]}],
    "etapes": [
        {"cle": "couronne", "titre": "Récupérer la Couronne de Mystra chez "
         "Zendar Nulentok", "type": "objet", "salle": "4,0", "gate": True,
         "requis": [{"nom": "La Couronne de Mystra", "portee": "quete"}]},
        {"cle": "huit_gemmes", "titre": "Réunir les huit gemmes dispersées "
         "sur Faerûn", "type": "objet", "gate": True,
         "requis": [{"nom": "La baguette de téléportation",
                     "portee": "quete"}]},
        {"cle": "restauration", "titre": "Restaurer la Couronne et la "
         "rendre à Mystra", "type": "etape", "salle": "0,0", "gate": True,
         "requis": [{"nom": "La Couronne de Mystra", "portee": "quete"}]},
    ],
}


def _setup_data(tmp_path, monkeypatch, inv_quete: list[str]) -> str:
    """data_dir de test : manifeste + inventaire de quête piloté."""
    data = tmp_path / "data"
    (data / "scenarios" / "T").mkdir(parents=True)
    (data / "fiches").mkdir(parents=True)
    (data / "scenarios" / "T" / "crown_3f52e912.donjon.json").write_text(
        json.dumps(_MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")

    import server.game.objectifs as obj

    def _inv_quete_fake(_etat, _dd, partie_id=""):
        # Contrat réel : (par_nom: {clé → {nom, porteur}}, liste)
        noms = list(inv_quete)
        return ({obj._cle_objet(n): {"nom": n, "porteur": "Morgoth"}
                 for n in noms},
                [dict(nom=n) for n in noms])

    monkeypatch.setattr(obj, "inventaire_quete", _inv_quete_fake)
    return str(data)


def _etat_recap() -> dict:
    return {
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Morgoth", "race": "Demi-orc", "classe": "Barbare",
                "pv": 12, "pv_max": 19, "ca": 17, "joueur": "alain"}],
        "quete": {"source": "[crown_3f52e912] /data/x.pdf",
                  "titre": "The Crown Of Mystra", "pitch": "Test.",
                  "bible": {"titre": "The Crown Of Mystra 1/4",
                            "resume": "La Couronne a disparu."}},
        "histoire": [
            {"ts": "t1", "tour": "", "evenement":
                "Début de l'aventure : Teleshann confie la quête."},
            {"ts": "t2", "tour": "", "evenement":
                "Combat terminé — victoire. Ennemis : Gobelin"},
        ],
        "memoire": {},
    }


def _build_recap(data_dir: str, partie_id: str) -> str:
    from server.config import PathsConfig, load_config
    from server.game.state import PartyState
    from server.llm.prompt_builder import PromptBuilder

    cfg = load_config()
    cfg = replace(cfg, paths=PathsConfig(
        data_dir=data_dir,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    PartyState(data_dir=data_dir, partie_id=partie_id).save(_etat_recap())
    etat = PartyState(data_dir=data_dir, partie_id=partie_id).load()
    return PromptBuilder(cfg).build_recap(etat, partie_id=partie_id)


def test_objectif_courant_bloque_affiche_actif_au_mj(tmp_path, monkeypatch):
    """L'objectif COURANT (« bloque » : Couronne manquante) doit être
    présenté au MJ comme l'objectif ACTIF à guider — pas comme « ⛔ BLOQUÉ »
    qui faisait croire à une impase (3f52e912)."""
    dd = _setup_data(tmp_path, monkeypatch, [])
    recap = _build_recap(dd, "t_3f52e912_actif")
    assert "C'EST L'OBJECTIF ACTIF" in recap
    _lignes = [l for l in recap.splitlines()
               if "Récupérer la Couronne" in l]
    assert _lignes, "l'objectif courant doit figurer dans le récap"
    assert "⛔" not in _lignes[0], _lignes[0]


def test_recap_minimal_contient_fil_histoire(tmp_path, monkeypatch):
    """Récap minimal (exploration) : le fil de l'histoire journalisé est
    injecté — sans lui, le modèle perdait le fil quand la fenêtre
    d'historique tronquait les premiers tours (3f52e912)."""
    dd = _setup_data(tmp_path, monkeypatch, [])
    recap = _build_recap(dd, "t_3f52e912_fil")
    assert "Fil de l'histoire" in recap
    assert "Combat terminé" in recap
    assert "RE-NARRE PAS" in recap
