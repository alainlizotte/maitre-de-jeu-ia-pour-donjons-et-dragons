# -*- coding: utf-8 -*-
"""Régressions de la partie 2ca691ec (session « La Tour des Sables »,
2026-09-21, Qwen 3.5 9B). Trois familles de bugs observés en jeu :

1. SUR-APPLICATION de dégâts : le piège serveur (1d6=5) était suivi de
   3 appels LLM « 5 dégâts » sur la même cleresse 8 PV (3 → -2 → -7 →
   -10, morte) — le rattrapage serveur corrige les SOUS-applications,
   il fallait le garde miroir contre les ré-applications identiques.
2. RE-ENGAGEMENT immédiat d'une rencontre identique : « Faucon »
   ré-engagé 4 tours de suite → « Faucon (2)…(4) » empilés.
3. PIÈGES re-déclenchés à chaque repassage dans la salle (dalle à
   fléchettes résolue 2×) sans trace côté prompt pour le MJ.

Usage : py -m pytest tests/test_correctifs_2ca691ec.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_2ca691ec"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir(avec_bestiaire: bool = False) -> str:
    d = tempfile.mkdtemp(prefix="dnd35_2ca691ec_")
    if avec_bestiaire:
        shutil.copy2(
            os.path.join(_REPO, "server", "data", "bestiaire.json"),
            os.path.join(d, "bestiaire.json"),
        )
    return d


def _setup_fiche_utturgut(d: str, pv: int = 16, conds: list | None = None):
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": pv, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare", "conditions": conds or []},
                  f, ensure_ascii=False)


def _pv_utturgut(d: str) -> int:
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
              encoding="utf-8") as f:
        return int(json.load(f)["pv"])


# --------------------------------------------------------------------------- #
# 1. Anti sur-application de dégâts (même cible / même montant / même tour)
# --------------------------------------------------------------------------- #
def test_dedup_degats_identiques_meme_tour_refuse():
    from server.tools.fiches import _DEDUP_DEGATS
    _DEDUP_DEGATS.clear()
    d = _fresh_dir()
    _setup_fiche_utturgut(d)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-A")
    r1 = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_infliger_degats"], ctx,
        {"nom": "Utturgut", "degats": 5}))
    assert "subit 5 dégâts" in r1.text
    assert _pv_utturgut(d) == 11
    # Re-narration du MÊME coup (même montant, même tour) → refusé.
    r2 = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_infliger_degats"], ctx,
        {"nom": "Utturgut", "degats": 5}))
    assert "Doublon ignoré" in r2.text, r2.text
    assert _pv_utturgut(d) == 11, "les dégâts dupliqués ont été appliqués !"
    # Un montant DIFFÉRENT au même tour reste légitime.
    r3 = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_infliger_degats"], ctx,
        {"nom": "Utturgut", "degats": 3}))
    assert "subit 3 dégâts" in r3.text
    assert _pv_utturgut(d) == 8


def test_dedup_degats_nouveau_tour_autorise():
    from server.tools.fiches import _DEDUP_DEGATS
    _DEDUP_DEGATS.clear()
    d = _fresh_dir()
    _setup_fiche_utturgut(d)
    ctx1 = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                       tour_id="tour-1")
    asyncio.run(invoke_tool(TOOLS["fiche_perso_infliger_degats"], ctx1,
                            {"nom": "Utturgut", "degats": 5}))
    ctx2 = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                       tour_id="tour-2")
    r = asyncio.run(invoke_tool(TOOLS["fiche_perso_infliger_degats"], ctx2,
                                {"nom": "Utturgut", "degats": 5}))
    assert "Doublon ignoré" not in r.text
    assert _pv_utturgut(d) == 6


def test_dedup_inactif_sans_tour_id_non_regression():
    """REST / appels internes du serveur (pièges) : pas de tour_id → le
    garde doit rester INACTIF (sinon le 2e 1d6 identique d'un vrai combat
    serait refusé)."""
    from server.tools.fiches import _DEDUP_DEGATS
    _DEDUP_DEGATS.clear()
    d = _fresh_dir()
    _setup_fiche_utturgut(d)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d)
    for _ in range(2):
        r = asyncio.run(invoke_tool(TOOLS["fiche_perso_infliger_degats"],
                                    ctx, {"nom": "Utturgut", "degats": 5}))
        assert "Doublon ignoré" not in r.text
    assert _pv_utturgut(d) == 6


def test_acharnement_sur_pj_mort_refuse():
    d = _fresh_dir()
    _setup_fiche_utturgut(d, pv=-10, conds=["Mort"])
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-X")
    r = asyncio.run(invoke_tool(TOOLS["fiche_perso_infliger_degats"], ctx,
                                {"nom": "Utturgut", "degats": 4}))
    assert "déjà MORT" in r.text, r.text
    assert _pv_utturgut(d) == -10


def test_potion_sans_montant_formule_tiree_par_le_serveur():
    """Partie 2ca691ec (bis) : « fiche_perso_soigner(nom=…, source="potion
    de soins légers") » SANS `soin` — le 9B omettait le montant, le soin
    était refusé et deux mourants n'étaient pas soignés. Le serveur tire
    maintenant LUI-MÊME la formule (1d8+1)."""
    d = _fresh_dir()
    _setup_fiche_utturgut(d, pv=-4)
    # La potion doit être DANS l'inventaire du soigné (dose déduite).
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": -4, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare",
                   "conditions": ["Mourant", "Stabilisé"],
                   "inventaire": [{"nom": "potion de soins légers",
                                   "qte": 1, "poids": 0.14}]},
                  f, ensure_ascii=False)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-P")
    r = asyncio.run(invoke_tool(TOOLS["fiche_perso_soigner"], ctx,
                                {"nom": "Utturgut",
                                 "source": "potion de soins légers"}))
    assert "Formule de la potion tirée par le serveur" in r.text, r.text
    assert "récupère" in r.text and "Doublon" not in r.text
    fiche = json.load(open(os.path.join(d, "fiches", "fiche_utturgut.json"),
                           encoding="utf-8"))
    # 1d8+1 ∈ [2..9] ; la dose est consommée, Mourant levé si PV > 0.
    assert 1 + 1 <= fiche["pv"] - (-4) <= 9 + 1
    if fiche["pv"] > 0:
        assert fiche["conditions"] == []
    else:
        assert "Mourant" in fiche["conditions"]


def test_potion_sans_montant_sans_dose_toujours_refuse():
    """Sans la potion dans l'inventaire : le refus reste de mise (pas de
    soin fantôme)."""
    d = _fresh_dir()
    _setup_fiche_utturgut(d, pv=-4)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-P2")
    r = asyncio.run(invoke_tool(TOOLS["fiche_perso_soigner"], ctx,
                                {"nom": "Utturgut",
                                 "source": "potion de soins légers"}))
    assert "PAS dans son inventaire" in r.text, r.text
    assert _pv_utturgut(d) == -4


def test_monstre_detruit_refuse_toujours():
    """Non-régression fa4e7366 : un monstre Détruit ne subit plus rien."""
    d = _fresh_dir(avec_bestiaire=True)
    PartyState(data_dir=d, partie_id=PID).save({
        "phase": "combat",
        "tour": 1,
        "pj": [{"nom": "Utturgut", "pv": 16, "pv_max": 16, "joueur": "t",
                "carac": {"DEX": 10}}],
        "monstres_combat": [
            {"nom": "Gobelin", "pv": -3, "pv_max": 5, "ca": 13,
             "conditions": ["Détruit"]},
        ],
    })
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-M")
    r = asyncio.run(invoke_tool(TOOLS["fiche_perso_infliger_degats"], ctx,
                                {"nom": "Gobelin", "degats": 5}))
    assert "déjà DÉTRUIT" in r.text, r.text


# --------------------------------------------------------------------------- #
# 2. Anti re-engagement immédiat d'une rencontre identique
# --------------------------------------------------------------------------- #
def _etat_base(d: str) -> None:
    PartyState(data_dir=d, partie_id=PID).save({
        "phase": "exploration",
        "tour": 0,
        "pj": [{"nom": "Utturgut", "pv": 16, "pv_max": 16, "niveau": 1,
                "joueur": "t", "carac": {"DEX": 10}}],
        "monstres_combat": [],
        "historique_engagements": [],
    })


def test_re_engagement_identique_refuse():
    d = _fresh_dir(avec_bestiaire=True)
    _etat_base(d)
    ctx = ToolContext(partie_id=PID, joueur="t", data_dir=d)
    r1 = asyncio.run(invoke_tool(TOOLS["engager_combat"], ctx,
                                 {"monstres": "Gobelin, Gobelin"}))
    assert "Combat engagé" in r1.text or "Initiative" in r1.text, r1.text
    # Clôture simulée (l'historique des engagements, lui, persiste).
    etat = PartyState(data_dir=d, partie_id=PID).load()
    etat["phase"] = "exploration"
    etat["monstres_combat"] = []
    etat["initiative"] = []
    PartyState(data_dir=d, partie_id=PID).save(etat)
    # Re-engagement IDENTIQUE dans la fenêtre → refusé.
    r2 = asyncio.run(invoke_tool(TOOLS["engager_combat"], ctx,
                                 {"monstres": "Gobelin, Gobelin"}))
    assert "Rencontre identique" in r2.text, r2.text
    assert PartyState(data_dir=d, partie_id=PID).load()["phase"] \
        == "exploration"
    # Une rencontre DIFFÉRENTE reste possible.
    r3 = asyncio.run(invoke_tool(TOOLS["engager_combat"], ctx,
                                 {"monstres": "Gobelin"}))
    assert "Rencontre identique" not in r3.text, r3.text


def test_re_engagement_apres_fenetre_autorise():
    d = _fresh_dir(avec_bestiaire=True)
    _etat_base(d)
    sig = "gobelinx2"
    etat = PartyState(data_dir=d, partie_id=PID).load()
    etat["historique_engagements"] = [
        {"sig": sig, "ts": time.time() - 300},        # hors fenêtre (120 s)
    ]
    PartyState(data_dir=d, partie_id=PID).save(etat)
    ctx = ToolContext(partie_id=PID, joueur="t", data_dir=d)
    r = asyncio.run(invoke_tool(TOOLS["engager_combat"], ctx,
                                {"monstres": "Gobelin, Gobelin"}))
    assert "Rencontre identique" not in r.text, r.text


# --------------------------------------------------------------------------- #
# 3. Pièges mécaniques : un seul déclenchement + signal au prompt
# --------------------------------------------------------------------------- #
SALLE_PIEGEE = {
    "x": 0, "y": -1, "type": "piège",
    "description": "Galerie pavée.",
    "piege": ("Dalle à fléchettes : Réflexes DD 12 par personnage qui "
              "franchit la dalle, 1d6 dégâts perforants."),
}


def test_piege_declenche_une_seule_fois():
    from server.tools.cartes import _resoudre_piege_salle
    d = _fresh_dir()
    PartyState(data_dir=d, partie_id=PID).save({
        "phase": "exploration",
        "pj": [{"nom": "Utturgut", "pv": 16, "pv_max": 16, "joueur": "t"}],
    })
    salle = dict(SALLE_PIEGEE)
    ctx = ToolContext(partie_id=PID, joueur="t", data_dir=d,
                      tour_id="tour-P1")
    bloc1, _p1 = asyncio.run(_resoudre_piege_salle(ctx, salle, "entrée"))
    assert "PIÈGE RÉSOLU PAR LE SERVEUR" in bloc1
    assert salle.get("piege_declenche") is True
    # Second passage : PLUS aucun bloc (piège consommé).
    bloc2, p2 = asyncio.run(_resoudre_piege_salle(ctx, salle, "entrée"))
    assert bloc2 == "" and p2 == {}
    assert salle.get("piege_declenche") is True


def test_piege_sans_sauvegarde_ne_se_marque_pas():
    """Un piège à COMPÉTENCE (crochetage DD…) n'est pas mécanique : pas de
    flag, la résolution reste à l'initiative du MJ (comportement voulu)."""
    from server.tools.cartes import _resoudre_piege_salle
    d = _fresh_dir()
    PartyState(data_dir=d, partie_id=PID).save({
        "phase": "exploration", "pj": [],
    })
    salle = {"x": 0, "y": 0,
             "piege": "Serrure robuste : crochetage DD 14 (outils de voleur)."}
    ctx = ToolContext(partie_id=PID, joueur="t", data_dir=d)
    bloc, patches = asyncio.run(_resoudre_piege_salle(ctx, salle, "entrée"))
    assert bloc == "" and patches == {}
    assert "piege_declenche" not in salle


def test_bloc_contenu_salle_signale_piege_consomme():
    from server.tools.cartes import _bloc_contenu_salle
    salle = dict(SALLE_PIEGEE, piege_declenche=True)
    bloc = _bloc_contenu_salle(salle)
    assert "🪤 Piège :" in bloc
    assert "DÉJÀ DÉCLENCHÉ" in bloc
    assert "CONSOMMÉ" in bloc
    # Sans le flag : pas de mention (piège à venir, à résoudre normalement).
    bloc_vierge = _bloc_contenu_salle(dict(SALLE_PIEGEE))
    assert "DÉJÀ DÉCLENCHÉ" not in bloc_vierge


def test_piege_exploreur_persiste_le_flag_via_grille():
    """Le dict salle vit dans `donjon["grille"]` : la mutation du flag doit
    être visible depuis la grille (ce que le state_patch persiste)."""
    from server.tools.cartes import _grille_vers_dict
    grille = [dict(SALLE_PIEGEE), {"x": 0, "y": 0, "type": "entrée"}]
    donjon = {"grille": grille}
    salles = _grille_vers_dict(donjon["grille"])
    salles[(0, -1)]["piege_declenche"] = True
    assert donjon["grille"][0].get("piege_declenche") is True


# --------------------------------------------------------------------------- #
# 4. Anti contournement : un refus d'incanter_sort ne se « rachète » pas avec
#    fiche_perso_soigner(source="sort…") — soin magique déjà appliqué = doublon
# --------------------------------------------------------------------------- #
def test_soin_magique_sans_incantation_reussie_refuse():
    from server.tools.sorts import _SOINS_MAGIQUES_TOUR
    _SOINS_MAGIQUES_TOUR.clear()
    d = _fresh_dir()
    _setup_fiche_utturgut(d, pv=5)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-S")
    r = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_soigner"], ctx,
        {"nom": "Utturgut", "soin": 5, "source": "sort de soins"}))
    assert "Aucun sort de soins réussi" in r.text, r.text
    assert _pv_utturgut(d) == 5                     # soin fictif refusé


def test_soin_magique_deja_applique_signale():
    from server.tools.sorts import _SOINS_MAGIQUES_TOUR, _marquer_soin_magique
    _SOINS_MAGIQUES_TOUR.clear()
    d = _fresh_dir()
    _setup_fiche_utturgut(d, pv=5)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-S2")
    _marquer_soin_magique(ctx, "Utturgut", 7)       # incanter_sort a soigné
    r = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_soigner"], ctx,
        {"nom": "Utturgut", "soin": 7, "source": "sort de soins"}))
    assert "DÉJÀ été appliqué" in r.text, r.text
    assert _pv_utturgut(d) == 5                     # pas de double soin


def test_soin_potion_non_magique_inchange():
    """Non-régression : le garde ne touche PAS aux potions/kit (est_magie
    faux) — le chemin potion reste fonctionnel."""
    d = _fresh_dir()
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 5, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare", "conditions": [],
                   "inventaire": [{"nom": "potion de soins légers",
                                   "qte": 1, "poids": 0.5}]},
                  f, ensure_ascii=False)
    ctx = ToolContext(partie_id=PID, joueur="test", data_dir=d,
                      tour_id="tour-P3")
    r = asyncio.run(invoke_tool(
        TOOLS["fiche_perso_soigner"], ctx,
        {"nom": "Utturgut", "soin": 6, "source": "potion de soins légers"}))
    assert "récupère 6 PV" in r.text, r.text
    assert _pv_utturgut(d) == 11


# --------------------------------------------------------------------------- #
# 5. Poids normalisés : « potion de soins légers » ne doit plus déclencher
#    « poids inconnu » (_norm retire le « s » final → clé sans « s » requise)
# --------------------------------------------------------------------------- #
def test_poids_potion_normalisee_reconnue():
    from server.tools.inventaire import _poids_unitaire
    assert _poids_unitaire("potion de soins légers", None) is not None
    assert _poids_unitaire("potion de soins légers", None) \
        == _poids_unitaire("potion de soins legers", None)
    assert _poids_unitaire("kit premiers secours", None) is not None
    assert _poids_unitaire("cle d'argent", None) is not None


# --------------------------------------------------------------------------- #
# 6. Pénalités anti-dégénérescence : le payload doit porter les pénalités
#    configurées (cause racine des boucles intra-réponse du 9B)
# --------------------------------------------------------------------------- #
def test_payload_llm_porte_les_penalites():
    from server.config import LLMConfig
    from server.llm.client import _payload_base
    cfg = LLMConfig(presence_penalty=0.3, repetition_penalty=1.07)
    payload = _payload_base(cfg, [], stream=False)
    assert payload["presence_penalty"] == 0.3
    assert payload["repetition_penalty"] == 1.07
    # Défauts neutres (aucun changement de comportement si non configuré).
    payload_n = _payload_base(LLMConfig(), [], stream=False)
    assert payload_n["presence_penalty"] == 0.0
    assert payload_n["repetition_penalty"] == 1.0


# --------------------------------------------------------------------------- #
# 7. Compression des vieilles narrations dans le contexte (amorce de copie
#    verbatim du 9B) : les 2 dernières restent pleines, les anciennes sont
#    réduites ; user/tool intacts ; session.history jamais modifiée.
# --------------------------------------------------------------------------- #
def test_compression_narrations_anciennes():
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class Msg:
        role: str
        content: str = ""

    from server.main import _compresser_narrations_anciennes
    longue1 = "mot " * 120          # ~480 chars
    longue2 = "histoire " * 100    # ~800 chars
    longue3 = "recit " * 100      # ~600 chars
    fenetre = [
        Msg("user", "je regarde autour"),
        Msg("assistant", longue1),
        Msg("tool", "résultat mécanique"),
        Msg("assistant", longue2),
        Msg("user", "je continue"),
        Msg("assistant", longue3),
    ]
    copie_historique = [m.content for m in fenetre]
    out = _compresser_narrations_anciennes(fenetre)
    # Les 2 dernières narrations restent intégrales…
    assert out[5].content == longue3
    assert out[3].content == longue2
    # …la plus ancienne est compressée.
    assert out[1].content.endswith("[…]")
    assert len(out[1].content) < len(longue1)
    # User et tool intacts ; l'entrée d'origine n'est PAS mutée.
    assert out[0].content == "je regarde autour"
    assert out[2].content == "résultat mécanique"
    assert copie_historique[1] == longue1
    # Moins de 2 narrations : rien n'est touché.
    petit = [Msg("assistant", longue1)]
    assert _compresser_narrations_anciennes(petit)[0].content == longue1


def test_budget_anti_spam_sorts():
    from server.llm.orchestrator import _BUDGET_OUTILS_TOUR
    assert _BUDGET_OUTILS_TOUR.get("preparer_sorts") == 2
    assert _BUDGET_OUTILS_TOUR.get("incanter_sort") == 2
    assert _BUDGET_OUTILS_TOUR.get("fiche_perso_soigner") == 3


# --------------------------------------------------------------------------- #
# 8. Combat zombie : un ennemi à 0 PV SANS la condition « Détruit » (PV
#    patchés à la main par le LLM) doit compter comme vaincu — sinon le
#    combat reste en phase=combat à jamais (partie 2ca691ec, session 4).
# --------------------------------------------------------------------------- #
def test_verifier_fin_victoire_pv_zero_sans_condition():
    from server.game.combat import _verifier_fin
    etat = {
        "phase": "combat",
        "pj": [{"nom": "Utturgut", "pv": 9, "pv_max": 14}],
        "monstres_combat": [
            {"nom": "Gobelin", "pv": -2, "pv_max": 5,
             "conditions": ["Détruit"]},
            {"nom": "Gobelin (2)", "pv": 0, "pv_max": 5, "conditions": []},
        ],
    }
    assert _verifier_fin(etat) == "victoire"
    # Non-régression : un ennemi vivant bloque toujours la victoire.
    etat["monstres_combat"][1]["pv"] = 2
    assert _verifier_fin(etat) is None


def test_distribution_xp_compte_pv_zero_sans_condition():
    from server.game.combat import _verifier_fin, _monstre_ennemi_vivant
    etat = {
        "phase": "combat",
        "monstres_combat": [
            {"nom": "Gobelin", "pv": 0, "pv_max": 5, "conditions": []},
        ],
    }
    # Cohérence des deux fonctions : le moteur considère déjà ce monstre
    # comme mort ; la fin de combat doit le voir pareil.
    assert _monstre_ennemi_vivant(etat) is None
    assert _verifier_fin(etat) == "victoire"
