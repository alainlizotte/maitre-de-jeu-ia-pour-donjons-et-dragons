# -*- coding: utf-8 -*-
"""Régressions du run 090ec0af (Dues For The Dead, 3ᵉ partie complète).

Problèmes observés en jeu et corrigés ici :

1+3. **Rencontre canonique esquivée** : face à « Rorreth Monforoth,
     rendez-vous ! » le MJ a narré un REPOS dans le repaire du boss ; la
     zone 3 (squelette canonique) a été traversée en prose. Filet
     5bis-g `_rencontre_canonique_esquivee` : salle à `ennemis`
     canoniques non nettoyés + mécanique hostile ou confrontation du
     joueur + AUCUN engager_combat ce tour → rejeu correctif exigeant
     l'engagement.
4. **Salle canonique sans clé `ennemis`** : l'absence de la clé était un
     trou du garde B25 (squelette improvisé au pied des escaliers).
     `_salle_sans_rencontre_prevue` : salle de manifeste sans `ennemis`
     ET sans `ennemi_note` = aucune rencontre autorisée (les rencontres
     errantes explicites restent possibles).
5. **Réprouver non appelé** : le rappel ⚜️ est injecté dans le récap de
     combat dès que des morts-vivants sont engagés et qu'un clerc/paladin
     3 existe (le tool `reprouver_morts_vivants` était ignoré).
6. **Butin de quête non mécanisé** : `_butin_salle_courante` exigeait
     ≥ 2 mots forts communs — « journal » (seul mot fort de la canonique
     « Journal personnel de Rorreth ») était rejeté ; l'extraction
     ignorait l'article « des ». Un mot fort DISTINCTIF (≥ 6 lettres)
     suffit désormais, et `des` est extrait.
2. **Tours figés** : budget temps `_BUDGET_LLM_TOUR_S` dans la boucle
     d'outils (sortie anticipée avec la narration partielle).

Usage : py -m pytest tests/test_correctifs_090ec0af.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    _BUDGET_LLM_TOUR_S,
    _butin_salle_courante,
)
from server.main import _rencontre_canonique_esquivee  # noqa: E402
from server.tools.state import _salle_sans_rencontre_prevue  # noqa: E402


# --------------------------------------------------------------------------- #
#  4. Salle de manifeste sans clé `ennemis` — plus de trou
# --------------------------------------------------------------------------- #
def _etat_salle(ennemis=None, ennemi_note=None, donjon_id="d_test"):
    salle = {"x": 0, "y": 0}
    if ennemis is not None:
        salle["ennemis"] = ennemis
    if ennemi_note:
        salle["ennemi_note"] = ennemi_note
    return {
        "phase": "exploration",
        "donjon": {"id": donjon_id, "courant": [0, 0], "grille": [salle]},
    }


def test_salle_sans_cle_ennemis_bloque():
    """LE trou du run 090ec0af : pas de clé `ennemis` → canon VIDE,
    engagement refusé."""
    assert _salle_sans_rencontre_prevue(_etat_salle()) is not None


def test_salle_ennemis_vides_bloque_encore():
    assert _salle_sans_rencontre_prevue(
        _etat_salle(ennemis=[])) is not None


def test_salle_avec_ennemis_pas_bloquee():
    assert _salle_sans_rencontre_prevue(
        _etat_salle(ennemis=["Kobold"])) is None


def test_rencontres_errantes_explicites_autorisees():
    """Zone 5 du module : `ennemi_note` = rencontres errantes prévues →
    l'engagement y reste possible."""
    assert _salle_sans_rencontre_prevue(_etat_salle(
        ennemi_note="Rencontres errantes possibles (goules, zombies)")) \
        is None


def test_hors_donjon_pas_bloque():
    assert _salle_sans_rencontre_prevue({"phase": "exploration"}) is None


def test_engagement_refuse_en_salle_vide_sans_cle(tmp_path):
    """Integration : engager un monstre dans la salle sans clé → ⛔."""
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    d = tmp_path / "data"
    d.mkdir(parents=True)
    (d / "bestiaire.json").write_text(json.dumps({
        "kobold": {"cle": "kobold", "nom": "Kobold", "pv": 4, "pv_max": 4,
                   "ca": 15, "init": 1, "fp": "1/4"},
    }, ensure_ascii=False), encoding="utf-8")
    PartyState(data_dir=str(d), partie_id="test090").save({
        "phase": "exploration",
        "pj": [{"nom": "Brandt"}],
        "donjon": {"id": "d_test", "courant": [0, 0],
                   "grille": [{"x": 0, "y": 0}]},
    })
    ctx = ToolContext(partie_id="test090", joueur="alain", data_dir=str(d))
    r = asyncio.run(invoke_tool(
        discover_tools()["engager_combat"], ctx,
        {"monstres": "Kobold"}))
    assert r.text.startswith("⛔"), r.text
    assert "AUCUNE rencontre prévue" in r.text, r.text


# --------------------------------------------------------------------------- #
#  1+3. Filet « rencontre canonique non engagée » (5bis-g)
# --------------------------------------------------------------------------- #
def _etat_canon(monstres=None, nettoyes=None, phase="exploration",
                ouverture_jouee=True):
    etat = {
        "phase": phase,
        "pj": [{"nom": "Brandt", "classe": "Guerrier", "niveau": 1}],
        "donjon": {
            "id": "d_test", "courant": [0, -5],
            "grille": [{"x": 0, "y": -5,
                        "ennemis": ["Nécromancien rouge ×1",
                                    "Squelette ×2", "Zombie ×1",
                                    "Kobold ×2"]}],
        },
    }
    if monstres is not None:
        etat["monstres_combat"] = monstres
    if nettoyes:
        etat["donjon"]["salles_nettoyees"] = {"0,-5": nettoyes}
    if ouverture_jouee:
        etat["histoire"] = [{"evenement": "Début de l'aventure : …"}]
    return etat


def test_filet_confrontation_joueur_renvoie_canon():
    """LE cas Rorreth : « rendez-vous — ou nous vous arrêterons » + salle
    canonique non engagée → le filet renvoie le canon complet."""
    canon = _rencontre_canonique_esquivee(
        _etat_canon(), [], "Rorreth Monforoth ! Rendez-vous — ou nous "
                           "vous arrêterons.")
    assert canon, "le filet doit se déclencher"
    assert any("cromancien" in c for c in canon), canon
    assert any("Squelette" in c for c in canon), canon


def test_filet_mecanique_hostile_renvoie_canon():
    trace = [{"name": "lancer_attaque", "ok": True}]
    canon = _rencontre_canonique_esquivee(
        _etat_canon(), trace, "J'examine la pièce.")
    assert canon, canon


def test_filet_engagement_deja_fait_s_arrete():
    trace = [{"name": "engager_combat", "ok": True}]
    assert _rencontre_canonique_esquivee(
        _etat_canon(), trace, "Rendez-vous !") == []


def test_filet_neutre_sans_signal_ne_tire_pas():
    """Traversée paisible sans hostile ni confrontation : pas de filet
    (l'approche discrète reste possible)."""
    assert _rencontre_canonique_esquivee(
        _etat_canon(), [], "Nous observons la pièce silencieusement.") == []


def test_filet_especes_nettoyees_s_arrete():
    assert _rencontre_canonique_esquivee(
        _etat_canon(nettoyes=["necromancien rouge", "squelette", "zombie",
                              "kobold"]),
        [{"name": "lancer_attaque", "ok": True}], "Au combat !") == []


def test_filet_phase_combat_s_arrete():
    assert _rencontre_canonique_esquivee(
        _etat_canon(phase="combat"),
        [], "Rendez-vous !") == []


def test_filet_desarme_a_l_ouverture():
    assert _rencontre_canonique_esquivee(
        _etat_canon(ouverture_jouee=False),
        [], "Rendez-vous !") == []


# --------------------------------------------------------------------------- #
#  5. Rappel ⚜️ Réprouver dans le récap de combat
# --------------------------------------------------------------------------- #
def _recap(etat: dict) -> str:
    from server.config import load_config
    from server.llm.prompt_builder import PromptBuilder
    return PromptBuilder(load_config()).build_recap(etat)


def test_recap_combat_rappelle_reprouver():
    etat = {
        "phase": "combat", "tour": 2,
        "courant_tour_pour": "Aurora",
        "initiative": [{"nom": "Aurora", "init": 5},
                       {"nom": "Squelette", "init": 2}],
        "monstres_combat": [{"nom": "Squelette", "pv": 3, "pv_max": 3,
                             "conditions": [], "degats_recus": 1}],
        "pj": [{"nom": "Aurora", "classe": "Clerc", "niveau": 1}],
        "quete": {"titre": "", "pitch": "", "source": ""},
        "histoire": [{"evenement": "Début de l'aventure : …"}],
        "memoire": {},
    }
    recap = _recap(etat)
    assert "reprouver_morts_vivants" in recap, recap[-500:]
    assert "Aurora" in recap


def test_recap_sans_morts_vivants_pas_de_rappel():
    etat = {
        "phase": "combat", "tour": 2,
        "courant_tour_pour": "Brandt",
        "initiative": [{"nom": "Brandt", "init": 5},
                       {"nom": "Kobold", "init": 2}],
        "monstres_combat": [{"nom": "Kobold", "pv": 4, "pv_max": 4,
                             "conditions": [], "degats_recus": 0}],
        "pj": [{"nom": "Brandt", "classe": "Guerrier", "niveau": 1}],
        "quete": {"titre": "", "pitch": "", "source": ""},
        "histoire": [{"evenement": "Début de l'aventure : …"}],
        "memoire": {},
    }
    assert "reprouver_morts_vivants" not in _recap(etat)


# --------------------------------------------------------------------------- #
#  6. Butin de quête : un mot fort DISTINCTIF suffit + article « des »
# --------------------------------------------------------------------------- #
def _etat_tresor(tresor: str) -> dict:
    return {
        "phase": "exploration",
        "donjon": {
            "id": "d_test", "courant": [0, -5],
            "grille": [{"x": 0, "y": -5,
                        "tresor": tresor}],
        },
    }


_TRESOR_RORRETH = ("Journal personnel de Rorreth (plans sur la Piscine de "
                   "Radiance) ; ordres signés Rath Modar ; bourse du culte "
                   "(75 pc) ; cercle de téléportation")


def test_butin_journal_mot_distinctif():
    """LE cas raté en run 3 : « découvre un journal ancien » — un seul mot
    fort commun (« journal ») mais DISTINCTIF → mécanisé."""
    obj = _butin_salle_courante(
        "Flick ouvre le coffre et découvre un journal ancien couvert de "
        "runes d'argent.",
        _etat_tresor(_TRESOR_RORRETH))
    assert obj and "journal" in obj.lower(), obj


def test_butin_ordres_avec_article_des():
    """« des ordres signés » : l'article « des » était hors extraction."""
    obj = _butin_salle_courante(
        "Brandt fouille le bureau et découvre des ordres signés par un "
        "mage de Thay.",
        _etat_tresor(_TRESOR_RORRETH))
    assert obj and "ordre" in obj.lower(), obj


def test_butin_faible_reste_refuse():
    """Un mot faible (< 6 lettres) seul ne suffit toujours pas."""
    obj = _butin_salle_courante(
        "Le groupe trouve un culte ancien évoqué sur les murs.",
        _etat_tresor(_TRESOR_RORRETH))
    assert obj is None, obj


# --------------------------------------------------------------------------- #
#  7. Retargeting des dégâts (run gobs 74de6b94 — TPC voleur)
# --------------------------------------------------------------------------- #
def test_cible_degats_corrigee_vers_ennemi_unique():
    """Le modèle attaque « Gobelin » (écho du combat d'entrée) alors que
    l'ennemi suivi est « Voleur » : cible inconnue + UN SEUL vivant → les
    dégâts vont à LUI (le TPC du run gobs vient de là)."""
    from server.main import _resoudre_cible_degats

    def _nn(s):
        import unicodedata
        nf = unicodedata.normalize("NFKD", str(s or "").lower())
        return "".join(c for c in nf if not unicodedata.combining(c))

    etat = {
        "pj": [{"nom": "Brandt"}],
        "monstres_combat": [{"nom": "Voleur", "pv": 11, "pv_max": 11,
                             "conditions": []}],
    }
    assert _resoudre_cible_degats(etat, "Gobelin", _nn) == "Voleur"
    # Cibles exactes/préfixées : inchangées (le tool gère).
    assert _resoudre_cible_degats(etat, "Voleur", _nn) == "Voleur"
    assert _resoudre_cible_degats(etat, "Brandt", _nn) == "Brandt"
    # Plusieurs vivants + cible inconnue → pas de devinette.
    etat["monstres_combat"].append({"nom": "Goule", "pv": 5, "pv_max": 5,
                                    "conditions": []})
    assert _resoudre_cible_degats(etat, "Gobelin", _nn) == "Gobelin"


# --------------------------------------------------------------------------- #
#  2. Budget temps de la boucle LLM
# --------------------------------------------------------------------------- #
def test_budget_llm_tour_borne():
    """Le budget existe et reste raisonnable (les tours normaux : 20-90 s)."""
    assert 120 <= _BUDGET_LLM_TOUR_S <= 900
