# -*- coding: utf-8 -*-
"""Régressions de la partie dfccc120 (analyse 2026-09-14).

1. « Apparition d'une Ombre » : le garde `_ennemis_annonces` engageait le
   monstre « Ombre » sur le simple mot « ombres » (décor) — désormais les
   noms génériques sont ignorés et une mention non quantifiée est du décor.
2. Combat : `_arme_du_bestiaire` ne parsait pas le format DRS
   « contact intangible (+3 …, 1d6 …) » → les monstres ne jouaient JAMAIS
   leur tour (305/342 entrées du bestiaire).
3. Jets simulés multi-modificateurs (« 1d20 + 5 (BBA) + 3 (FOR) = 18 »)
   non détectés ; placeholders « *(Appel de l'outil …)* » non nettoyés.
4. Dégâts jamais appliqués : `lancer_degats` réussi sur un ennemi suivi est
   désormais appliqué immédiatement par le serveur ; `lancer_attaque` sans
   attaquant/cible est refusé.

Usage : py -m pytest tests/test_correctifs_dfccc120.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.combat import _arme_du_bestiaire  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratedResult,
    _ennemis_annonces,
    looks_like_simulation,
    strip_narration_artifacts,
)
from server.tools.base import ToolContext, _TOOL_REGISTRY  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

PID = "test_dfccc120"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _bestiaire_minimal(d: str) -> None:
    """Bestiaire réduit : Ombre (mot générique) + Squelette + Gobelin."""
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {},
            "ombre": {"nom": "Ombre", "cle": "ombre", "fp": "3", "pv": 19,
                      "ca": 13, "init": "+2",
                      "attaques": "contact intangible (+3 contact au corps "
                                  "à corps, affaiblissement temporaire de "
                                  "1d6 points de Force)",
                      "degs": "contact intangible (+3 contact au corps à "
                              "corps, affaiblissement temporaire de 1d6 "
                              "points de Force)"},
            "squelette": {"nom": "Squelette", "cle": "squelette", "fp": "1/3",
                          "pv": 6, "ca": 12, "init": "+0"},
            "gobelin": {"nom": "Gobelin", "cle": "gobelin", "fp": "1/3",
                        "pv": 6, "ca": 15, "init": "+2"},
        }, f, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 1. Faux positif « Ombre » dans l'engagement forcé
# --------------------------------------------------------------------------- #
def test_ombres_decor_ne_declenchent_pas_engagement():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _bestiaire_minimal(d)
        # Scène dfccc120 : le joueur négocie avec les voleurs ; « les
        # ombres » est un mot du décor, « se ruent » un verbe d'attaque.
        txt = ("Les voleurs se dispersent et depuis les ombres de l'auberge "
               "des projectiles se ruent vers vous.")
        assert _ennemis_annonces(txt, _ctx(d)) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_monstres_sans_quantificateur_pas_engages():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _bestiaire_minimal(d)
        txt = ("Des squelettes décoratifs ornent la crypte quand la porte "
               "explose et que des flammes surgissent des braseros.")
        assert _ennemis_annonces(txt, _ctx(d)) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_monstres_quantifies_sont_toujours_engages():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _bestiaire_minimal(d)
        txt = ("Soudain, cinq squelettes se libèrent du plâtre et se ruent "
               "sur vous.")
        res = _ennemis_annonces(txt, _ctx(d))
        assert res is not None
        assert res.split(", ").count("Squelette") == 5, res
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_detecter_combat_prose_ignore_ombres_decor():
    from server.main import _detecter_combat_prose  # noqa: PLC0415
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _bestiaire_minimal(d)
        etat = {"phase": "exploration"}
        # Décor + prose de dégâts : PAS de rattrapage.
        txt_decor = ("Les flèches se ruent depuis les ombres de l'auberge "
                     "et vous subissez 5 points de dégâts.")
        assert _detecter_combat_prose(d, txt_decor, etat) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 2. Parsing des armes du bestiaire (format DRS)
# --------------------------------------------------------------------------- #
def test_arme_du_bestiaire_format_drs_ombre():
    m = {
        "attaques": "contact intangible (+3 contact au corps à corps, "
                    "affaiblissement temporaire de 1d6 points de Force)",
        "degs": "contact intangible (+3 contact au corps à corps, "
                "affaiblissement temporaire de 1d6 points de Force)",
    }
    assert _arme_du_bestiaire(m) == ("contact intangible", 3, 1, 6, 0)


def test_arme_du_bestiaire_format_classique():
    m = {"attaques": "Cimeterre +2 (corps à corps)",
         "degs": "Cimeterre (1d6+1)"}
    assert _arme_du_bestiaire(m) == ("Cimeterre", 2, 1, 6, 1)


def test_arme_du_bestiaire_multi_attaques_drs():
    m = {
        "attaques": "2 tentacules (+4 corps à corps, 1d6) et morsure "
                    "(-2 corps à corps, 1d4)",
        "degs": "2 tentacules (+4 corps à corps, 1d6) et morsure "
                "(-2 corps à corps, 1d4)",
    }
    assert _arme_du_bestiaire(m) == ("tentacules", 4, 1, 6, 0)


def test_arme_du_bestiaire_sans_attaque():
    assert _arme_du_bestiaire({"attaques": "1 attaque corpo"}) is None
    assert _arme_du_bestiaire({}) is None


def test_majorite_du_bestiaire_reel_est_parseable():
    """Garde de non-régression : l'ancien parseur laissait 305/342 monstres
    sans attaque (tour de monstre muet). On exige une large majorité."""
    raw = json.load(open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "server", "data", "bestiaire.json"),
        encoding="utf-8"))
    mons = [v for k, v in raw.items() if k != "_meta" and isinstance(v, dict)]
    avec_arme = [m for m in mons if str(m.get("attaques") or "").strip()]
    ok = sum(1 for m in avec_arme if _arme_du_bestiaire(m) is not None)
    assert avec_arme, "bestiaire vide ?"
    assert ok / len(avec_arme) >= 0.95, (
        f"{ok}/{len(avec_arme)} monstres parseables seulement")


# --------------------------------------------------------------------------- #
# 3. Détection des jets simulés + nettoyage des artefacts
# --------------------------------------------------------------------------- #
def test_formule_multi_modificateurs_detectee():
    txt = ("**Jet d'attaque : 1d20 + 5 (BBA) + 3 (FOR) = 18**\n"
           "Le coup touche l'Ombre !")
    frag = looks_like_simulation(txt)
    assert frag is not None
    assert "1d20" in frag


def test_formule_reformulation_legitime_apres_outils():
    """Un tool de dés a tourné : réciter la formule n'est plus une simulation."""
    txt = "La hache s'abat : 1d8+3 = 7, l'Ombre encaisse le coup."
    assert looks_like_simulation(
        txt, include_damage=False, include_checks=False) is None


def test_jet_competence_recite_detecte():
    assert looks_like_simulation(
        "**Résultat : Throk'mar réussit son jet d'intimidation (18/15).**")
    assert looks_like_simulation(
        "Il tente un jet de Discours : 16 / DD 15 — réussi.")


def test_placeholder_appel_de_l_outil_nettoye():
    txt = ("**Jet d'attaque :**\n"
           "*(Appel de l'outil lancer_attaque pour résoudre le combat)*\n"
           "L'Ombre encaisse le coup.")
    out = strip_narration_artifacts(txt)
    assert "Appel de l'outil" not in out, out
    assert "lancer_attaque" not in out, out
    assert "Ombre" in out


def test_balise_tool_orpheline_nettoyee():
    out = strip_narration_artifacts(
        "Throk'mar</tool>\n\nParfait ! Throk'mar est prêt.")
    assert "</tool>" not in out, out
    assert "Throk'mar" in out
    assert "Parfait" in out


# --------------------------------------------------------------------------- #
# 4. Dégâts appliqués côté serveur
# --------------------------------------------------------------------------- #
def test_lancer_attaque_sans_noms_est_refuse():
    from server.tools.dice import lancer_attaque  # noqa: PLC0415
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        tr = asyncio.run(lancer_attaque(_ctx(d)))
        assert tr.text.startswith("❌"), tr.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _etat_combat(d: str, pv_ombre: int = 19) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "meta": {"titre": "test"},
        "phase": "combat",
        "tour": 2,
        "pj": [{"nom": "Throk'mar", "joueur": "alain", "pv": 19,
                "pv_max": 19, "ca": 14, "conditions": []}],
        "pnj": [],
        "initiative": [{"nom": "Throk'mar", "init": 16},
                       {"nom": "Ombre", "init": 5}],
        "courant_tour_pour": "Throk'mar",
        "monstres_combat": [{"nom": "Ombre", "pv": pv_ombre,
                             "pv_max": pv_ombre, "ca": 13, "fp": "3",
                             "conditions": []}],
        "histoire": [],
    })


def test_lancer_degats_sur_monstre_suivi_est_auto_applique():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _etat_combat(d, pv_ombre=19)
        orch = Orchestrator(client=None, tools=discover_tools())
        spec = _TOOL_REGISTRY["lancer_degats"]
        result = OrchestratedResult()
        tr = asyncio.run(orch._run_one_tool(
            spec, _ctx(d),
            {"nb_des": 1, "faces": 8, "bonus": 3,
             "arme_ou_sort": "Hache lourde", "cible": "Ombre"},
            None, result,
        ))
        assert tr.text.startswith("💥"), tr.text
        # Les dégâts sont appliqués immédiatement dans l'état.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        mo = etat["monstres_combat"][0]
        total = int(tr.text.split("Dégâts infligés : ")[1].split("**")[0])
        assert mo["pv"] == 19 - total, (mo, total)
        # La trace contient l'appel d'application (file anti-double-application
        # et dé-duplication restent équilibrées : appliqué == jeté).
        noms = [tc["name"] for tc in result.tool_calls_trace]
        assert noms == ["lancer_degats", "fiche_perso_infliger_degats"], noms
        assert result.notes_mecaniques, "note mécanique absente"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lancer_degats_cible_hors_combat_ne_crash_pas():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _etat_combat(d)
        orch = Orchestrator(client=None, tools=discover_tools())
        spec = _TOOL_REGISTRY["lancer_degats"]
        result = OrchestratedResult()
        tr = asyncio.run(orch._run_one_tool(
            spec, _ctx(d),
            {"nb_des": 2, "faces": 6, "bonus": 0,
             "arme_ou_sort": "Piege", "cible": "Cadavre anonyme"},
            None, result,
        ))
        assert tr.text.startswith("💥")
        assert not result.notes_mecaniques
        noms = [tc["name"] for tc in result.tool_calls_trace]
        assert noms == ["lancer_degats"], noms
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_exces_apres_auto_application():
    """LLM applique EN PLUS ses propres chiffres → l'excès est restitué,
    l'auto-application reste exacte (appliqué LLM == jeté)."""
    from server.main import _exces_degats_monstres  # noqa: PLC0415
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Ombre"},
         "text": "💥 **Dégâts** : Hache → Ombre\n- **Dégâts infligés : 6**"},
        # Auto-application (trace par le serveur).
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "ombre", "degats": 6}},
        # Le LLM rappelle l'outil avec SES chiffres : double application.
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Ombre", "degats": 6}},
    ]
    mons = [{"nom": "Ombre", "pv": 7, "pv_max": 19}]
    exces = _exces_degats_monstres(trace, mons)
    assert exces == {"Ombre": 6}, exces


# --------------------------------------------------------------------------- #
# 5. Entrée de donjon narrée sans `carte_donjon_entrer` (partie 8a7c1f92)
# --------------------------------------------------------------------------- #
def test_entree_donjon_narree_detection():
    from server.main import _entree_donjon_narree  # noqa: PLC0415
    # Tournures réelles de la partie 8a7c1f92 (Dues For The Dead).
    assert _entree_donjon_narree(
        "Vous vous dirigez vers l'entrée des catacombes, la clé à la main. "
        "Vous entrez dans l'obscurité, votre lanterne éclairant le chemin.")
    assert _entree_donjon_narree(
        "Vous franchissez le seuil du donjon et descendez dans le noir.")
    assert _entree_donjon_narree(
        "Vous pénétrez dans la crypte en silence.")
    # Faux positifs : lieu sans mot de donjon, ou donjon juste mentionné.
    assert not _entree_donjon_narree(
        "Vous entrez dans l'auberge et commandez une bière.")
    assert not _entree_donjon_narree(
        "Yovir vous parle des catacombes sans que vous y alliez.")


def test_carte_donjon_entrer_utilise_le_manifeste_du_scenario():
    from server.tools.base import invoke_tool  # noqa: PLC0415
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        # Quête liée à un manifeste minimal.
        PartyState(data_dir=d, partie_id=PID).save({
            "meta": {"titre": "t"},
            "phase": "exploration",
            "pj": [{"nom": "Héros", "pv": 10, "joueur": "alain"}],
            "quete": {"titre": "Mini", "pitch": "",
                      "source": "[mini_scenario] /mini.pdf"},
            "histoire": [],
        })
        man_dir = os.path.join(d, "scenarios", "mini")
        os.makedirs(man_dir, exist_ok=True)
        with open(os.path.join(man_dir, "mini.donjon.json"), "w",
                  encoding="utf-8") as f:
            json.dump({
                "scenario": "mini_scenario",
                "donjon_id": "Catacombes de test",
                "etages": [{
                    "nom": "RDC",
                    "salles": [
                        {"x": 0, "y": 0, "type": "entrée",
                         "portes": {"nord": True},
                         "description": "Salle d'entrée."},
                        {"x": 0, "y": -1, "type": "salle",
                         "portes": {"sud": True},
                         "description": "Première salle."},
                    ],
                }],
            }, f, ensure_ascii=False)
        # donjon_id quelconque : le manifeste du scénario prime.
        tr = asyncio.run(invoke_tool(
            _TOOL_REGISTRY["carte_donjon_entrer"], _ctx(d),
            {"donjon_id": "nimporte"}))
        assert not tr.text.startswith(("❌", "⛔")), tr.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        donjon = etat.get("donjon") or {}
        assert donjon.get("id"), "donjon.id doit être initialisé"
        assert donjon.get("grille"), "grille vide : la carte resterait 404"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 6. Appels d'outils cachés DANS le bloc thinking (issue llama.cpp #20837)
# --------------------------------------------------------------------------- #
def _fake_client_qwen_think():
    """Client factice : 1er appel = tool call XML DANS <think> + contenu
    visible vide (le strip détruirait l'appel) ; 2e = narration."""
    from server.llm.client import ChatResult  # noqa: PLC0415

    brut = (
        "<think>\nJe résous l'attaque du joueur.\n<tool_call>\n"
        "<function=lancer_attaque>\n"
        "<parameter=nom_attaquant>Throk'mar</parameter>\n"
        "<parameter=nom_cible>Ombre</parameter>\n"
        "<parameter=bonus_attaque>5</parameter>\n"
        "<parameter=ca_cible>13</parameter>\n"
        "<parameter=arme>hache</parameter>\n"
        "</function>\n</tool_call>\n</think>"
    )

    class _Client:
        appels = 0

        async def chat(self, work, tools=None, tool_choice=None,
                       temperature=None):
            _Client.appels += 1
            if _Client.appels == 1:
                return ChatResult(
                    content="", tool_calls=[], finish_reason="tool_calls",
                    raw={}, raw_content=brut)
            return ChatResult(
                content="La hache s'abat dans le noir.",
                tool_calls=[], finish_reason="stop", raw={})

    return _Client()


def test_appels_outils_dans_thinking_recuperes_et_executes():
    d = tempfile.mkdtemp(prefix="dnd35_dfccc120_")
    try:
        _etat_combat(d)
        orch = Orchestrator(client=_fake_client_qwen_think(),
                            tools=discover_tools(), tool_mode="native")
        result = asyncio.run(orch.run(
            [Message(role="system", content="Tu es le MJ."),
             Message(role="user", content="je l'attaque")],
            _ctx(d),
        ))
        attq = [tc for tc in result.tool_calls_trace
                if tc.get("name") == "lancer_attaque" and tc.get("ok")]
        assert attq, result.tool_calls_trace
        assert attq[0]["args"].get("nom_cible") == "Ombre"
        assert "hache" in result.narration
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_segments_thinking():
    from server.llm.orchestrator import _segments_thinking  # noqa: PLC0415
    assert _segments_thinking("avant <think>un</think> après") == ["un"]
    assert _segments_thinking("<think>non fermé") == ["non fermé"]
    assert _segments_thinking("pas de think") == []
    assert _segments_thinking("") == []


# --------------------------------------------------------------------------- #
# 7. Contamination XML→JSON des arguments (Qwen3.5-9B, format Hermes)
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 8. Gains d'état en prose (soins / XP / inventaire) détectés comme simulations
# --------------------------------------------------------------------------- #
def test_gain_prose_patterns():
    from server.llm.orchestrator import (  # noqa: PLC0415
        _GAIN_PROSE_PATTERNS, looks_like_simulation,
    )
    cas = [
        "Throkmar récupère 5 points de vie. La chaleur le réconforte.",
        "La potion lui fait récupérer **5 PV** immédiatement.",
        "Le sort soigne 8 points de vie de ses blessures.",
        "Throkmar gagne 50 points d'expérience pour ce combat.",
        "Il gagne **100 XP** et se sent plus fort.",
        "Je vous ajoute l'épée courte à votre inventaire.",
        "Elle range la clef dans son sac avant de continuer.",
    ]
    for texte in cas:
        frag = looks_like_simulation(texte)
        assert frag is not None, texte
        assert any(p.search(frag) for p in _GAIN_PROSE_PATTERNS), \
            (texte, frag)
    # Reformulation légitime : désactivée avec include_gains=False
    assert looks_like_simulation(
        "Throkmar récupère 5 points de vie.", include_gains=False) is None


def test_nettoyer_args_outils():
    from server.llm.orchestrator import _nettoyer_args_outils  # noqa: PLC0415
    # Résidus <parameter=…> dans une valeur (observé en réel) :
    sales = {
        "nom": "Throk'mar</parameter>\n<parameter=degats>\n5</parameter>",
        "degats": 5,
    }
    propres = _nettoyer_args_outils(sales)
    assert propres["nom"] == "Throk'mar"
    assert propres["degats"] == 5
    # Quote parasite en bord (observé : « 1d20" ») :
    assert _nettoyer_args_outils({"expression": '1d20"'})["expression"] == "1d20"
    # Valeur saine intacte :
    assert _nettoyer_args_outils({"expression": "2d6+3"}) == {"expression": "2d6+3"}
    # Valeur qui devient vide → champ retiré :
    assert "nom" not in _nettoyer_args_outils({"nom": "</parameter>"})
    # Idempotent :
    once = _nettoyer_args_outils(sales)
    assert _nettoyer_args_outils(once) == once
