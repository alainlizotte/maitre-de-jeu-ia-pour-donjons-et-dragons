# -*- coding: utf-8 -*-
"""Régressions des parties 2052e3ae & 22d78403 (Dues For The Dead, 21 oct).

« Debute la partie » → l'INTRODUCTION (briefing au porche du cimetière :
Yovir Glandon confie la mission, Cassyt l'acolyte se présente comme
GUIDE) s'est terminée en COMBAT contre l'Acolyte (initiative, phase=
combat, image bestiaire) — DEUX parties de suite.

Chaîne du bug (⚙️ « Le serveur a régularisé ce combat porté en prose »
affiché en direct, puis disparu au rechargement de la page) :

1. la prose d'intro regorge de formulations AMICALES qui matchent
   `_COMBAT_PROSE_MARKERS` : « Il s'approche de vous » (Yovir qui vient
   parler) et « Cassyt … s'avance vers vous » (l'acolyte qui se
   présente) — marqueurs ajoutés en Bêta M1 pour des zombies MENAÇANTS ;
2. `_detecter_combat_prose` résout le MOT « acolyte » (Cassyt, guide
   ALLIÉE de Kelemvor) vers l'entrée bestiaire « Acolyte » (FP ¼) ;
3. le rattrapage 5ter engage `engager_combat("Acolyte")` À L'OUVERTURE :
   b401206f désarmait l'hostilité imminente mais laissait ARMÉS les
   marqueurs/dégâts dits « explicites » — or ici ils ne sont explicites
   que pour un ZOMBIE, pas pour un PNJ qui vient parler aux héros ;
4. cas aggravé 2052e3ae : Throk'mar ressuscité → `histoire` contenait
   déjà « ⚠️ … arrive dans cette nouvelle partie » (48b239fa) → la
   directive « DÉBUT DE L'AVENTURE » (interdiction de combat au tour
   d'intro) n'était PAS injectée (porte `histoire` vide) et le marqueur
   « Début de l'aventure » n'était JAMAIS journalisé ;
5. la bannière ⚙️ n'était pas persistée : `remember_assistant` stocke la
   prose SANS les blocs mécaniques (5b4e2bbe) → disparition au F5.

Correctifs couverts ici :
- `ouverture_non_jouee` (game.state) : l'ouverture se détecte au
  MARQUEUR « Début de l'aventure », les seuls événements tolérés avant
  le premier tour narré étant les arrivées structurelles de PJ ;
- `_journaliser_ouverture_si_besoin` : marqueur journalisé même si un
  PJ ressuscité a déjà écrit son événement d'arrivée (et JAMAIS sur une
  vieille partie déjà jouée sans marqueur) ;
- `prompt_builder` : directive DÉBUT DE L'AVENTURE injectée tant que
  l'ouverture n'est pas jouée (pj ressuscité OU histoire vide) ;
- rattrapage 5ter : `_rattrapage_desarme_ouverture` désarme les
  marqueurs/dégâts NARRÉS PAR LE MJ à l'ouverture (l'attaque DÉCLARÉE
  par le joueur et les salles à ennemis canoniques restent armés) ;
- persistance : `Message.mecanique` transporte les blocs ⚙️ pour le
  rejeu au rechargement (jamais renvoyés au LLM) ;
- suite (e2e 3747c9ae/a47471a5) : `carte_donjon_entrer` refuse l'entrée
  AU tour d'ouverture des scénarios à manifeste
  `ouverture_hors_donjon` (règle GÉNÉRIQUE, donnée PAR SCÉNARIO :
  `depart` + drapeau du manifeste — Dues For The Dead et Crypts
  Kelemvor ; les 29 manifestes à ouverture en donjon restent inchangés).

Usage : py -m pytest tests/test_correctifs_2052e3ae.py -q
"""

from __future__ import annotations

import json
import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.game.session import PartySession  # noqa: E402
from server.game.state import ouverture_non_jouee  # noqa: E402
from server.llm.client import Message  # noqa: E402
from server.main import (  # noqa: E402
    _detecter_combat_prose,
    _journaliser_ouverture_si_besoin,
    _narration_mecanique_seule,
    _narration_prose_seule,
    _rattrapage_desarme_ouverture,
)

# --------------------------------------------------------------------------- #
#  Proses d'intro RÉELLES (extraits des deux parties fautives)
# --------------------------------------------------------------------------- #
# 2052e3ae — Cassyt s'avance pour se présenter (marqueur « s'avance vers
# vous ») et se dit « acolyte » deux fois.
_INTRO_2052E3AE = (
    "Le Doomguide Yovir Glandon, un homme aux traits tirés par les nuits "
    "sans sommeil, se tient devant vous avec une jeune acolyte nommée "
    "Cassyt à ses côtés. « Les morts-vivants rôdent dans les catacombes "
    "sous le cimetière, » dit-il d'une voix rauque. Cassyt, une jeune "
    "femme aux yeux brillants de détermination, s'avance vers vous. "
    "« Je suis Cassyt, acolyte de Kelemvor. Je vous guiderai dans les "
    "galeries hautes du cimetière. Ensemble, nous enquêterons sur ce "
    "fléau. »"
)
# 22d78403 — Yovir s'approche pour le briefing (marqueur « s'approche de
# vous ») et présente « Cassyt, notre acolyte ».
_INTRO_22D78403 = (
    "Il s'approche de vous, son regard grave se posant sur chacun de "
    "vous. « Je suis le Doomguide Yovir, et voici Cassyt, notre acolyte. "
    "Elle nous guidera à travers les galeries hautes des catacombes. »"
)

# Événement structurel écrit à la CRÉATION d'une partie rejointe par un
# PJ ressuscité (persos.py, cas 97e94bd1/48b239fa).
_EVENEMENT_ARRIVEE = {
    "ts": "2026-10-21T15:06:18.378700",
    "tour": "",
    "evenement": "⚠️ Throk’mar arrive dans cette nouvelle partie : son "
                 "décès dans la partie précédente est oublié (nouveau "
                 "départ — PV rendus à 16).",
}

_QUETE_DFTD = {
    "titre": "Dues For The Dead",
    "pitch": "Un jeune clerc sollicite l'aide des PJ pour purifier un "
             "ancien sanctuaire profane par des morts-vivants.",
    "source": "",
}


def _etat_ouverture_2052e3ae() -> dict:
    """État AU tour d'ouverture de la partie 2052e3ae : l'événement
    d'arrivée du ressuscité existe DÉJÀ (l'histoire n'est jamais vide)."""
    return {
        "phase": "opening_complete",
        "pj": [{"nom": "Throk’mar", "joueur": "alain"}],
        "quete": dict(_QUETE_DFTD),
        "histoire": [dict(_EVENEMENT_ARRIVEE)],
        "memoire": {},
    }


def _etat_ouverture_22d78403() -> dict:
    """État AU tour d'ouverture de la partie 22d78403 : histoire vide."""
    return {
        "phase": "opening_complete",
        "pj": [{"nom": "Throk’mar", "joueur": "Alain"}],
        "quete": dict(_QUETE_DFTD),
        "histoire": [],
        "memoire": {},
    }


# --------------------------------------------------------------------------- #
#  1. ouverture_non_jouee — le marqueur, pas le vide de l'histoire
# --------------------------------------------------------------------------- #
def test_ouverture_non_jouee_histoire_vide():
    assert ouverture_non_jouee(_etat_ouverture_22d78403()) is True


def test_ouverture_non_jouee_avec_pj_ressuscite():
    """Le CAS 2052e3ae : l'arrivée structurelle du ressuscité NE compte
    PAS comme une ouverture jouée."""
    assert ouverture_non_jouee(_etat_ouverture_2052e3ae()) is True


def test_ouverture_jouee_apres_marqueur():
    etat = _etat_ouverture_2052e3ae()
    etat["histoire"].append({
        "ts": "2026-10-21T15:08:00",
        "tour": "",
        "evenement": "Début de l'aventure : Le porche du cimetière de "
                     "Valhingen, à Phlan…",
    })
    assert ouverture_non_jouee(etat) is False


def test_vieille_partie_sans_marqueur_pas_une_ouverture():
    """Les parties ANTÉRIEURES au marqueur ont une histoire jouée sans
    « Début de l'aventure » : elles ne doivent JAMAIS être traitées en
    ouverture (sinon directive re-injectée et rattrapage désarmé à vie)."""
    etat = {
        "phase": "exploration",
        "histoire": [
            {"evenement": "Le groupe quitte Phlan vers le cimetière."},
            {"evenement": "Combat : 2 Squelettes détruits."},
            {"evenement": "Déplacement : salle (0,-1) rejointe."},
        ],
    }
    assert ouverture_non_jouee(etat) is False


# --------------------------------------------------------------------------- #
#  2. Journalisation de l'ouverture (correctif du cas ressuscité)
# --------------------------------------------------------------------------- #
def test_journaliser_ouverture_apres_arrivee_ressuscite():
    """2052e3ae : l'arrivée du ressuscité ne doit plus EMPÊCHER
    l'écriture du marqueur « Début de l'aventure »."""
    etat = _etat_ouverture_2052e3ae()
    assert _journaliser_ouverture_si_besoin(etat, _INTRO_2052E3AE) is True
    assert etat["histoire"][-1]["evenement"].startswith("Début de l'aventure")
    # Le marqueur désarme à la fois la directive du prompt et le test
    # d'ouverture : pas de re-journalisation, pas de re-narration (5a9b99c8).
    assert _journaliser_ouverture_si_besoin(etat, _INTRO_22D78403) is False
    assert ouverture_non_jouee(etat) is False


def test_journaliser_ouverture_histoire_vide_inchange():
    """Comportement d'origine (partie normale) préservé."""
    etat = _etat_ouverture_22d78403()
    assert _journaliser_ouverture_si_besoin(etat, _INTRO_22D78403) is True
    assert etat["histoire"][0]["evenement"].startswith("Début de l'aventure")


def test_journaliser_jamais_sur_vieille_partie_jouee():
    """Une vieille partie déjà jouée (histoire réelle, sans marqueur) ne
    doit pas recevoir un faux « Début de l'aventure » en cours de route."""
    etat = {
        "phase": "exploration",
        "histoire": [
            {"evenement": "Le groupe quitte Phlan vers le cimetière."},
            {"evenement": "Combat : 2 Squelettes détruits."},
        ],
    }
    assert _journaliser_ouverture_si_besoin(etat, _INTRO_2052E3AE) is False
    assert len(etat["histoire"]) == 2


def test_journaliser_narration_vide():
    etat = _etat_ouverture_22d78403()
    assert _journaliser_ouverture_si_besoin(etat, "   ") is False
    assert etat["histoire"] == []


# --------------------------------------------------------------------------- #
#  3. Directive DÉBUT DE L'AVENTURE du prompt_builder
# --------------------------------------------------------------------------- #
def _recap(etat: dict) -> str:
    from server.config import load_config
    from server.llm.prompt_builder import PromptBuilder
    return PromptBuilder(load_config()).build_recap(etat)


def test_directive_injectee_histoire_vide():
    recap = _recap(_etat_ouverture_22d78403())
    assert "DÉBUT DE L'AVENTURE" in recap


def test_directive_injectee_pj_ressuscite():
    """Le CAS 2052e3ae : malgré l'événement d'arrivée, la directive
    « INTERDIT ce tour : combat, monstre, rencontre » doit être injectée."""
    recap = _recap(_etat_ouverture_2052e3ae())
    assert "DÉBUT DE L'AVENTURE" in recap


def test_directive_retiree_apres_premier_tour():
    """Après le premier tour narré (marqueur journalisé), la directive
    disparaît — pas de boucle de re-narration (5a9b99c8)."""
    etat = _etat_ouverture_2052e3ae()
    _journaliser_ouverture_si_besoin(etat, _INTRO_2052E3AE)
    assert "DÉBUT DE L'AVENTURE" not in _recap(etat)


def test_directive_absente_en_cours_daventure():
    etat = {
        "phase": "exploration",
        "pj": [{"nom": "Throk’mar"}],
        "quete": dict(_QUETE_DFTD),
        "histoire": [
            {"evenement": "Début de l'aventure : Le porche du cimetière…"},
            {"evenement": "Le groupe entre dans les catacombes."},
        ],
    }
    assert "DÉBUT DE L'AVENTURE" not in _recap(etat)


# --------------------------------------------------------------------------- #
#  4. Rattrapage combat-prose désarmé à l'ouverture
# --------------------------------------------------------------------------- #
def test_prose_intro_2052e3ae_declenche_encore_la_detection():
    """Documente le DÉCLENCHEUR d'origine : hors ouverture (partie déjà
    jouée), la prose 2052e3ae fait détecter « Acolyte » via le marqueur
    « s'avance vers vous » + le mot « acolyte » du bestiaire. Le correctif
    est la GARDE d'ouverture, pas un affaiblissement de la détection."""
    from server.config import load_config
    data_dir = str(load_config().abs(load_config().paths.data_dir))
    bestiaire = os.path.join(data_dir, "bestiaire.json")
    if not os.path.exists(bestiaire):
        import pytest
        pytest.skip("bestiaire.json absent de ce data_dir")
    etat = {
        "phase": "exploration",
        "histoire": [
            {"evenement": "Début de l'aventure : …"},
            {"evenement": "Le groupe a fini d'écouter le briefing."},
        ],
        "memoire": {},
    }
    types = _detecter_combat_prose(
        data_dir, _INTRO_2052E3AE, etat, forcer_declencheur=False,
        exclure=["Throk’mar"],
    )
    assert "Acolyte" in types, types


def test_rattrapage_desarme_ouverture_2052e3ae():
    """LA régression : au tour d'ouverture avec PJ ressuscité (histoire
    NON vide mais ouverture non jouée), le rattrapage est désarmé."""
    assert _rattrapage_desarme_ouverture(
        _etat_ouverture_2052e3ae(), attaque_joueur=False,
        ennemis_canoniques=False,
    ) is True


def test_rattrapage_desarme_ouverture_22d78403():
    assert _rattrapage_desarme_ouverture(
        _etat_ouverture_22d78403(), attaque_joueur=False,
        ennemis_canoniques=False,
    ) is True


def test_rattrapage_armed_apres_ouverture():
    etat = _etat_ouverture_2052e3ae()
    _journaliser_ouverture_si_besoin(etat, _INTRO_2052E3AE)
    assert _rattrapage_desarme_ouverture(
        etat, attaque_joueur=False, ennemis_canoniques=False,
    ) is False


def test_attaque_joueur_reste_armee_a_l_ouverture():
    """L'attaque DÉCLARÉE par le joueur engage toujours la mécanique
    (4d4b4557), même au tour d'ouverture."""
    assert _rattrapage_desarme_ouverture(
        _etat_ouverture_22d78403(), attaque_joueur=True,
        ennemis_canoniques=False,
    ) is False


def test_salle_ennemis_canoniques_reste_armee_a_l_ouverture():
    """Réserve b401206f : 13/31 manifestes placent des ennemis DÈS la
    salle d'entrée — le rattrapage y reste armé à l'ouverture."""
    etat = _etat_ouverture_22d78403()
    etat["donjon"] = {
        "id": "a_la_chasse_aux_gobs",
        "courant": [0, 0],
        "grille": [{"x": 0, "y": 0, "ennemis": ["Gobelin", "Gobelin"]}],
    }
    assert _rattrapage_desarme_ouverture(
        etat, attaque_joueur=False, ennemis_canoniques=True,
    ) is False


# --------------------------------------------------------------------------- #
#  5. Persistance des blocs mécaniques (demande joueur)
# --------------------------------------------------------------------------- #
_BANNIERE = (
    "⚙️ _Le serveur a régularisé ce combat porté en prose — initiative "
    "officielle engagée pour des monstres du bestiaire._\n\n"
    "🎲 **Initiative du combat** : Cassyt 21 · Throk’mar 8"
)
_PROSE = ("Cassyt, une jeune femme aux yeux brillants de détermination, "
          "s'avance vers vous. « Je vous guiderai. »")
_NARRATION_COMPLETE = _PROSE + "\n\n" + _BANNIERE


def test_narration_mecanique_seule_complete_la_prose():
    assert _narration_prose_seule(_NARRATION_COMPLETE) == _PROSE
    mec = _narration_mecanique_seule(_NARRATION_COMPLETE)
    assert mec.startswith("⚙️")
    assert "Initiative du combat" in mec
    # Sans bloc mécanique : complément vide (pas de doublon).
    assert _narration_mecanique_seule(_PROSE) == ""


def test_mecanique_survit_au_rechargement(tmp_path):
    """Le chat persisté doit rejouer les blocs ⚙️ au rechargement — c'était
    LE symptôme rapporté (« le message de mécanique s'efface au
    rechargement de la page »)."""
    sid = "2052e3ae_t"
    s1 = PartySession(partie_id=sid, data_dir=str(tmp_path))
    s1.remember_assistant(_PROSE, mecanique=_BANNIERE)
    # Nouvelle instance = ce qui se passe au rechargement/redémarrage.
    s2 = PartySession(partie_id=sid, data_dir=str(tmp_path))
    s2.hydrate_history()
    assert len(s2.history) == 1
    msg = s2.history[0]
    assert msg.content == _PROSE          # contexte LLM intact
    assert msg.mecanique == _BANNIERE     # mécanique re-jouée à l'écran


def test_mecanique_jamais_envoyee_au_llm():
    msg = Message(role="assistant", content=_PROSE, mecanique=_BANNIERE)
    payload = msg.to_openai()
    assert "mecanique" not in payload
    assert payload["content"] == _PROSE


def test_mecanique_dans_le_payload_joined(tmp_path):
    """`_send_joined` doit exposer `mecanique` pour le rejeu client —
    vérifié via la forme de sérialisation du message."""
    s = PartySession(partie_id="joined_t", data_dir=str(tmp_path))
    s.remember_assistant(_PROSE, mecanique=_BANNIERE)
    d = s._msg_to_dict(s.history[0])
    assert d["mecanique"] == _BANNIERE
    # Et le round-trip complet :
    back = s._msg_from_dict(d)
    assert back.mecanique == _BANNIERE
    assert back.content == _PROSE


# --------------------------------------------------------------------------- #
#  6. Suite — carte_donjon_entrer verrouillé à l'ouverture HORS DONJON
# --------------------------------------------------------------------------- #
import asyncio  # noqa: E402

from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

_MANIFESTE_HORS_DJ = {
    "id": "scen_test_ouv",
    "scenario": "scen_test_ouv",
    "donjon_id": "Catacombes de test",
    "titre": "Test hors donjon",
    "ouverture_hors_donjon": True,
    "depart": {"nom": "Bourg de test", "x": 10, "y": 20},
    "etages": [
        {"nom": "Niveau -1",
         "salles": [{"x": 0, "y": 0, "nom": "Entrée"}]},
    ],
    "etapes": [],
}


def _data_dir_ouv(tmp_path, histoire: list) -> str:
    """data_dir minimal : partie liée à un scénario à ouverture hors
    donjon + son manifeste sous scenarios/."""
    data = tmp_path / "data"
    (data / "scenarios" / "test").mkdir(parents=True)
    (data / "partie_ouv_t.json").write_text(json.dumps({
        "phase": "opening_complete",
        "pj": [{"nom": "Throk"}],
        "quete": {"titre": "Test Ouv", "pitch": "Test.",
                  "source": "[scen_test_ouv] /data/scenarios/test/x.pdf"},
        "histoire": histoire,
    }, ensure_ascii=False), encoding="utf-8")
    (data / "scenarios" / "test" / "scen_test_ouv.donjon.json").write_text(
        json.dumps(_MANIFESTE_HORS_DJ, ensure_ascii=False), encoding="utf-8")
    return str(data)


def _entrer(dd: str) -> str:
    ctx = ToolContext(partie_id="ouv_t", joueur="Alain", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["carte_donjon_entrer"], ctx,
        {"donjon_id": "Catacombes de test"}))
    return r.text


def test_entree_refusee_a_l_ouverture_hors_donjon(tmp_path):
    """Cas 22d78403/3747c9ae : histoire VIDE à l'ouverture → l'appel du
    modèle est refusé, le briefing doit se jouer dehors."""
    refus = _entrer(_data_dir_ouv(tmp_path, []))
    assert "HORS DONJON" in refus, refus
    assert "Bourg de test" in refus, refus


def test_entree_refusee_avec_pj_ressuscite(tmp_path):
    """Cas 2052e3ae : l'événement d'arrivée du ressuscité ne doit pas
    ouvrir le donjon avant la scène d'intro."""
    refus = _entrer(_data_dir_ouv(tmp_path, [dict(_EVENEMENT_ARRIVEE)]))
    assert "HORS DONJON" in refus, refus


def test_entree_libre_apres_ouverture_jouee(tmp_path):
    """Marqueur « Début de l'aventure » journalisé → la descente
    redevient libre (décision des joueurs)."""
    dd = _data_dir_ouv(tmp_path, [])
    from server.game.state import PartyState
    st = PartyState(data_dir=dd, partie_id="ouv_t")
    etat = st.load()
    etat["histoire"].append({
        "ts": "2026-10-21T16:00:00", "tour": "",
        "evenement": "Début de l'aventure : Le porche du cimetière…",
    })
    st.save(etat)
    rep = _entrer(dd)
    assert "HORS DONJON" not in rep, rep


def test_scenarios_ouverture_en_donjon_non_bloques(tmp_path):
    """Les 29/31 manifestes SANS `ouverture_hors_donjon` (entrée en
    donjon dès le départ) ne sont PAS affectés par le verrou."""
    data = tmp_path / "data"
    (data / "scenarios" / "test").mkdir(parents=True)
    (data / "partie_ouv_t.json").write_text(json.dumps({
        "phase": "opening_complete",
        "pj": [{"nom": "Throk"}],
        "quete": {"titre": "Test Dj", "pitch": "Test.",
                  "source": "[scen_test_dj] /data/scenarios/test/y.pdf"},
        "histoire": [],
    }, ensure_ascii=False), encoding="utf-8")
    man = dict(_MANIFESTE_HORS_DJ)
    man["id"] = "scen_test_dj"
    man["scenario"] = "scen_test_dj"
    man["donjon_id"] = "Donjon de test"
    man.pop("ouverture_hors_donjon", None)
    (data / "scenarios" / "test" / "scen_test_dj.donjon.json").write_text(
        json.dumps(man, ensure_ascii=False), encoding="utf-8")
    ctx = ToolContext(partie_id="ouv_t", joueur="Alain", data_dir=str(data))
    rep = asyncio.run(invoke_tool(
        discover_tools()["carte_donjon_entrer"], ctx,
        {"donjon_id": "Donjon de test"}))
    assert "HORS DONJON" not in rep.text, rep.text
