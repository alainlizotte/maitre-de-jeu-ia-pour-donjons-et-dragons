"""Correctifs issus de l'analyse de la partie c74b0277 (Dues for the Dead).

1. Garde de difficulté : `engager_combat` refuse une créature dont le FP
   dépasse le plafond du groupe (niveau max + marge selon la taille) — le
   dragon rouge FP 7 contre un Guerrier niveau 1 ne doit plus être jouable.
2. Ennemis de scénario : `ennemis_du_resume` détecte les créatures du
   bestiaire dans un résumé FR **ou EN** (modules Adventurers League).
3. Dé-duplication : `_exces_degats_monstres` repère les dégâts appliqués
   en double (jet serveur + chiffres narrés par le LLM).
4. Jets annoncés mais non résolus (« Je lance un jet de Force… » sans dé)
   détectés par l'anti-simulation, sauf après un vrai jet.
5. Toggles images par catégorie (config.yaml × maître GUI).

Usage : py -m pytest tests/test_correctifs_c74b0277.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import AppConfig  # noqa: E402
from server.game.state import PartyState  # noqa: E402
from server.llm.orchestrator import looks_like_simulation  # noqa: E402
from server.main import _exces_degats_monstres  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402
from server.tools.scenarios import ennemis_du_resume  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "test_c74b0277"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_c74b_")
    shutil.copy2(
        os.path.join(_REPO, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


async def tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


async def _creer_pj(d: str, nom: str = "Bargoum", niveau: int = 1) -> None:
    r = await tool(
        d, "fiche_perso_creer_rapide",
        nom=nom, race="Demi-orc", classe="Guerrier", niveau=niveau,
        joueur="alain",
        carac_texte="For 16, Dex 14, Con 11, Int 9, Sag 13, Cha 9",
    )
    assert r.text.startswith("✅"), r.text


# --------------------------------------------------------------------------- #
#  1. Garde de difficulté dans engager_combat
# --------------------------------------------------------------------------- #
async def test_garde_fp_refuse_dragon_solo():
    d = _fresh_dir()
    try:
        await _creer_pj(d)  # 1 PJ niveau 1 → plafond FP 5
        r = await tool(d, "engager_combat", monstres="Dragon rouge (jeune)")
        assert r.text.startswith("⛔"), r.text
        assert "trop puissants" in r.text.lower() or "Trop puissants" in r.text
        assert "sans espoir" in r.text
        assert "Dragon rouge (jeune)" in r.text
        # Suggestions de créatures adaptées présentes.
        assert "Créatures plausibles" in r.text
        # AUCUN combat engagé : phase inchangée, initiative vide.
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") != "combat"
        assert not (etat.get("initiative") or [])
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_garde_fp_laisse_passer_ogre_groupe_de_quatre():
    d = _fresh_dir()
    try:
        for nom in ("Groth", "Mélodie", "Elara", "Zarkon"):
            await _creer_pj(d, nom)  # 4 PJ niveau 1 → plafond FP 7
        r = await tool(d, "engager_combat", monstres="Ogre")  # FP 5 ≤ 7
        assert not r.text.startswith("⛔"), r.text
        assert "Combat engagé" in r.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        assert etat.get("phase") == "combat"
        assert [m["nom"] for m in etat["monstres_combat"]] == ["Ogre"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  2. Ennemis de scénario : résumé FR et ANGLAIS (Adventurers League)
# --------------------------------------------------------------------------- #
def test_ennemis_du_resume_anglais_dues_for_the_dead():
    # Résumé RÉEL du module (le PDF ne cite que « Red Wizard necromancer ») :
    resume = (
        "The adventure begins with characters hearing about the reappearance "
        "of undead creatures in the cemetery next to the city of Phlan. Much "
        "of the adventure involves exploring the catacombs, facing revived "
        "undead, until characters discover that a Red Wizard necromancer is "
        "responsible for the trouble."
    )
    types = ennemis_du_resume(resume)
    assert "Nécromancien rouge" in types, types
    # Un texte de module citant les morts-vivants courants :
    types2 = ennemis_du_resume(
        "The catacombs hold zombies, skeletons and ghouls; a necromancer "
        "leads them."
    )
    assert {"Zombie", "Squelette", "Goule", "Nécromancien rouge"} <= set(types2), types2


def test_ennemis_du_resume_francais_et_sans_faux_positifs():
    types = ennemis_du_resume(
        "Le donjon abrite des zombies et un squelette près de l'orchestre "
        "de la fête ; la porte du trésor est gardée par une goule.")
    assert "Zombie" in types and "Squelette" in types and "Goule" in types
    # « orchestre » ne doit PAS déclencher « Orc ».
    assert not any(t.lower() == "orc" for t in types), types
    # Texte sans créature → liste vide.
    assert ennemis_du_resume("Une aventure paisible sans rencontre.") == []


# --------------------------------------------------------------------------- #
#  3. Dé-duplication des dégâts doublés sur les monstres
# --------------------------------------------------------------------------- #
def test_exces_degats_detecte_le_double_application():
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Zombie"},
         "text": "💥 **Dégâts infligés : 4**"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Zombie", "degats": 4}, "text": "PV 1/5"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Zombie", "degats": 5}, "text": "PV -4/5"},
    ]
    monstres = [{"nom": "Zombie", "pv": -4, "pv_max": 5, "conditions": []}]
    exces = _exces_degats_monstres(trace, monstres)
    assert exces == {"Zombie": 5}, exces


def test_exces_degats_aucun_faux_positif_sur_application_normale():
    # Full attack : 2 jets, une seule application de la somme → OK.
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Ogre"}, "text": "**Dégâts infligés : 3**"},
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Ogre"}, "text": "**Dégâts infligés : 4**"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Ogre", "degats": 7}, "text": "PV 15/22"},
    ]
    monstres = [{"nom": "Ogre", "pv": 15, "pv_max": 22, "conditions": []}]
    assert _exces_degats_monstres(trace, monstres) == {}
    # Dégâts appliqués à une créature SUIVIE seulement : cible inconnue → rien.
    assert _exces_degats_monstres(
        [{"name": "fiche_perso_infliger_degats", "ok": True,
          "args": {"nom": "Porte", "degats": 9}, "text": ""}],
        monstres,
    ) == {}


# --------------------------------------------------------------------------- #
#  4. Jets de compétence annoncés mais non résolus
# --------------------------------------------------------------------------- #
def test_jet_annonce_sans_de_detecte():
    frag = looks_like_simulation(
        "Je lance un jet de Force pour forcer la porte de pierre.")
    assert frag is not None
    assert "jet" in frag.lower()


def test_jet_annonce_legitime_apres_un_vrai_de():
    # Un lancer_d20 a déjà tourné dans le tour : reformuler « jet de Force »
    # avec le résultat est légitime → pas de correction.
    assert looks_like_simulation(
        "Jet de Force : 14 contre DD 16 — échec, la porte résiste.",
        include_checks=False,
    ) is None


# --------------------------------------------------------------------------- #
#  5. Toggles images par catégorie (config × maître)
# --------------------------------------------------------------------------- #
def test_toggles_images_effectifs():
    cfg = AppConfig()
    # Défaut : tout activé.
    assert cfg.image.effective("monstres")
    assert cfg.image.effective("salles")
    assert cfg.image.effective("scenes")
    # Catégorie coupée dans config.yaml → effective off, même maître actif.
    cfg.image.salles_enabled = False
    assert not cfg.image.effective("salles")
    assert cfg.image.effective("monstres")
    # Maître coupé (bouton GUI) → tout off, mais les clés config restent.
    cfg.image.set_all(False)
    assert not cfg.image.effective("monstres")
    assert not cfg.image.effective("salles")
    assert not cfg.image.effective("scenes")
    cfg.image.set_all(True)
    assert cfg.image.effective("monstres")
    assert not cfg.image.effective("salles")  # verrou catégorie maintenu
    # Catégorie inconnue → False (robustesse).
    assert not cfg.image.effective("nawak")


# --------------------------------------------------------------------------- #
#  6. Le Nécromancien rouge est dans le bestiaire et engageable (FP 3)
# --------------------------------------------------------------------------- #
async def test_necromancien_rouge_engageable():
    d = _fresh_dir()
    try:
        for nom in ("Groth", "Mélodie", "Elara"):
            await _creer_pj(d, nom)  # 3 PJ niveau 1 → plafond FP 7
        r = await tool(d, "engager_combat",
                       monstres="Nécromancien rouge, Squelette")
        assert not r.text.startswith("⛔"), r.text
        etat = PartyState(data_dir=d, partie_id=PID).load()
        noms = [m["nom"] for m in etat["monstres_combat"]]
        assert "Nécromancien rouge" in noms and "Squelette" in noms
        necro = next(m for m in etat["monstres_combat"]
                     if m["nom"] == "Nécromancien rouge")
        assert necro["pv"] == 17 and necro["ca"] == 13
    finally:
        shutil.rmtree(d, ignore_errors=True)
