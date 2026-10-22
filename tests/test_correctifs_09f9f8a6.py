# -*- coding: utf-8 -*-
"""Régressions de la partie 09f9f8a6 (Dues For The Dead, run complet 3 PJ).

Trois correctifs issus des problèmes observés en jeu :

1. **« Touché » sans dégâts à montants ambigus** : dans le rattrapage
   « touché sans dégâts », une extraction AMBIGUE de montant (plusieurs
   chiffres près du nom de la cible — « 3/3 PV » du monstre côtoyant le
   montant narré) ABANDONNAIT silencieusement : le coup réussi restait
   sans effet et la créature semblait invulnérable (run 0aef5a12 :
   3/3 PV pendant 12 rounds, TPC). Désormais le serveur jette lui-même
   les dégâts de l'arme (`_rouler_degats_attaque`) — PJ et monstres
   traités symétriquement.
2. **Renforts ennemis hors module** : « Loup-garou (humain) » (32 PV)
   ajouté via combat_ajouter_combattant au poste de sentinelle canonique
   (Kobold + 2 Zombies). Désormais tout renfort ENNEMI doit appartenir
   à la liste `ennemis` canonique de la salle (B25) ; les ALLIÉS
   (invoquations des PJ) restent libres.
3. **Inertie du MJ** : 3 tours consécutifs sans AUCUN tool mécanique →
   le récap ré-ancre le modèle (salle courante + objectif). Compteur
   `_maj_inertie_mj` + bloc ⚖️ du prompt_builder.

Usage : py -m pytest tests/test_correctifs_09f9f8a6.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.main import (  # noqa: E402
    _appliquer_degats_oublies,
    _maj_inertie_mj,
)


# --------------------------------------------------------------------------- #
#  1. « Touché » sans dégâts — plus d'impasse sur montants ambigus
# --------------------------------------------------------------------------- #
class _FakeOrch:
    """Orchestrateur factice : journalise les tools directs du serveur."""

    def __init__(self):
        self.appels: list[tuple[str, dict]] = []

    async def execute_tool_direct(self, name, args, ctx, on_event, result):
        self.appels.append((name, dict(args or {})))
        from server.tools.base import ToolResult
        if name == "lancer_degats":
            return ToolResult(text="🎲 Dégâts infligés : 7")
        return ToolResult(
            text=f"💥 {args.get('nom')} subit {args.get('degats')} dégâts.")


def _result(trace: list, narration: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(tool_calls_trace=trace, narration=narration)


_ATTAQUE_TOUCHEE = {
    "name": "lancer_attaque", "ok": True,
    "args": {"nom_attaquant": "Brandt", "nom_cible": "Squelette",
             "arme": "hache d'arme"},
    "text": "✅ **Touché**. (Total 9 vs CA 15… non : le filet ne lit que le "
            "marqueur Touché.)",
}


def _run_net(trace: list, narration: str):
    """Exécute le rattrapage avec un orchestrateur factice (les tools
    serveur « réussissent » : lancer_degats → 7 dégâts)."""
    orch = _FakeOrch()
    txt = asyncio.run(_appliquer_degats_oublies(
        orch, _result(trace, narration), ctx=None, on_event=None))
    return orch, txt


def test_montant_clair_applique_directement():
    """Cas nominal inchangé : montant narré unique → infliger direct,
    sans relancer de dés."""
    narration = ("Brandt frappe le Squelette qui subit 6 points de "
                 "dégâts et s'affaisse.")
    orch, _ = _run_net([dict(_ATTAQUE_TOUCHEE)], narration)
    inflige = [a for n, a in orch.appels if n == "fiche_perso_infliger_degats"]
    jete = [a for n, a in orch.appels if n == "lancer_degats"]
    assert inflige and inflige[0].get("degats") == 6, orch.appels
    assert not jete, "le serveur ne doit PAS relancer de dés ici"


def test_montant_ambigu_relance_les_des_du_serveur():
    """LA régression 0aef5a6 : deux montants près du nom (« 6 points de
    dégâts » du coup + « 4 dégâts » du voisin) → AVANT, abandon
    silencieux ; DÉSORMAIS le serveur jette et applique lui-même."""
    narration = ("Brandt frappe le Squelette. Le monstre encaisse 6 "
                 "points de dégâts, grince des dents et rend 4 dégâts "
                 "à son tour.")
    orch, _ = _run_net([dict(_ATTAQUE_TOUCHEE)], narration)
    jete = [a for n, a in orch.appels if n == "lancer_degats"]
    inflige = [a for n, a in orch.appels if n == "fiche_perso_infliger_degats"]
    assert jete, "montants ambigus → le serveur doit jeter les dégâts"
    assert inflige, "…puis les appliquer à la cible"
    assert inflige[0].get("degats") == 7, orch.appels


def test_monte_ambigu_fonctionne_aussi_pour_un_monstre_attaquant():
    """Symétrie PJ/monstres : un Squelette qui touche Brandt sans dégâts
    narrés → le serveur résout lui-même les dégâts (dés du bestiaire si
    l'arme du monstre est identifiée, sinon catalogue PHB — ici
    « épée courte », chemin catalogue testable sans contexte complet)."""
    attaque = {
        "name": "lancer_attaque", "ok": True,
        "args": {"nom_attaquant": "Squelette", "nom_cible": "Brandt",
                 "arme": "épée courte"},
        "text": "✅ **Touché**.",
    }
    narration = ("Le Squelette frappe Brandt ; la douleur traverse "
                 "l'armure de cuir.")
    orch, _ = _run_net([attaque], narration)
    jete = [a for n, a in orch.appels if n == "lancer_degats"]
    assert jete and jete[0].get("cible") == "Brandt", orch.appels
    inflige = [a for n, a in orch.appels if n == "fiche_perso_infliger_degats"]
    assert inflige, orch.appels


def test_attaque_manquee_napplique_rien():
    """Un lancer_attaque « Manqué » ne déclenche AUCUN dégât."""
    attaque = {
        "name": "lancer_attaque", "ok": True,
        "args": {"nom_attaquant": "Brandt", "nom_cible": "Squelette",
                 "arme": "hache d'arme"},
        "text": "❌ **Manqué**.",
    }
    orch, _ = _run_net(
        [attaque], "La hache s'abat dans le vide. 6 points de dégâts "
                   "auraient été jolis.")
    assert not orch.appels, orch.appels


# --------------------------------------------------------------------------- #
#  2. Renforts ennemis hors module refusés (combat_ajouter_combattant)
# --------------------------------------------------------------------------- #
_BESTIAIRE_TEST = {
    "_meta": {"source": "test"},
    "kobold": {"cle": "kobold", "nom": "Kobold", "pv": 4, "pv_max": 4,
               "ca": 15, "init": 2, "fp": "1/4"},
    "zombie": {"cle": "zombie", "nom": "Zombie", "pv": 12, "pv_max": 12,
               "ca": 12, "init": 0, "fp": "1/2"},
    "loup_garou": {"cle": "loup_garou", "nom": "Loup-garou", "pv": 32,
                   "pv_max": 32, "ca": 15, "init": 2, "fp": "3"},
}


def _data_dir_renfort(tmp_path, avec_canon: bool = True) -> str:
    data = tmp_path / "data"
    (data / "scenarios" / "test").mkdir(parents=True)
    (data / "bestiaire.json").write_text(
        json.dumps(_BESTIAIRE_TEST, ensure_ascii=False), encoding="utf-8")
    grille_salle = {"x": 0, "y": -4,
                    "ennemis": ["Kobold", "Zombie"] if avec_canon else []}
    (data / "partie_renfort_t.json").write_text(json.dumps({
        "phase": "combat",
        "tour": 2,
        "initiative": [{"nom": "Brandt", "init": 5},
                       {"nom": "Kobold", "init": 2}],
        "monstres_combat": [{"nom": "Kobold", "pv": 4, "pv_max": 4,
                             "ca": 15}],
        "pj": [{"nom": "Brandt"}],
        "donjon": {"id": "donjon_test", "courant": [0, -4],
                   "grille": [grille_salle]},
    }, ensure_ascii=False), encoding="utf-8")
    return str(data)


def _ajouter_combattant(dd: str, nom: str, allie: bool = False) -> str:
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    ctx = ToolContext(partie_id="renfort_t", joueur="e2e", data_dir=dd)
    r = asyncio.run(invoke_tool(
        discover_tools()["combat_ajouter_combattant"], ctx,
        {"nom": nom, "allie": allie}))
    return r.text


def test_renfort_hors_module_refuse(tmp_path):
    """LE cas du loup-garou : renfort ennemi absent du canon de la salle
    (Kobold + 2 Zombies) → refus."""
    refus = _ajouter_combattant(_data_dir_renfort(tmp_path), "Loup-garou")
    assert "hors module" in refus, refus
    assert "Kobold" in refus and "Zombie" in refus, refus


def test_renfort_canon_passe(tmp_path):
    """Un renfort APPARTENANT au canon de la salle n'est pas bloqué."""
    rep = _ajouter_combattant(_data_dir_renfort(tmp_path), "Zombie")
    assert "hors module" not in rep, rep


def test_renfort_allie_non_bloque(tmp_path):
    """Les invoquations des PJ (allie=True) restent libres : le garde ne
    vise que l'improvisation du MJ."""
    rep = _ajouter_combattant(_data_dir_renfort(tmp_path), "Loup-garou",
                              allie=True)
    assert "hors module" not in rep, rep


def test_salle_sans_canon_renforts_libres(tmp_path):
    """Salle sans liste `ennemis` canonique : comportement inchangé."""
    rep = _ajouter_combattant(_data_dir_renfort(tmp_path, avec_canon=False),
                              "Loup-garou")
    assert "hors module" not in rep, rep


# --------------------------------------------------------------------------- #
#  3. Inertie du MJ — compteur + bloc de ré-ancre du récap
# --------------------------------------------------------------------------- #
def test_inertie_compteur_sans_tool():
    """Le compteur ne mute pas l'état lui-même : l'appelant écrit la
    valeur retournée (contrat documenté)."""
    etat: dict = {}
    for attendu in (1, 2, 3):
        n = _maj_inertie_mj(etat, [])
        etat["inertie_mj"] = n
        assert n == attendu


def test_inertie_tools_passifs_comptent_comme_inertes():
    """Lire l'état / journaliser n'est pas de la mécanique : le tour reste
    inerte (c'était la fuite observée — le MJ « consultait » en boucle)."""
    trace = [{"name": "etat_partie_get", "ok": True},
             {"name": "memoire_evenement", "ok": True}]
    assert _maj_inertie_mj({}, trace) == 1


def test_inertie_tool_mecanique_remise_a_zero():
    etat = {"inertie_mj": 4}
    assert _maj_inertie_mj(etat, [{"name": "carte_donjon_explorer",
                                   "ok": True}]) == 0
    # Même un tool qui ÉCHOUE prouve une tentative mécanique.
    assert _maj_inertie_mj(etat, [{"name": "lancer_attaque",
                                   "ok": False}]) == 0


def test_inertie_plafonnee_a_9():
    etat = {"inertie_mj": 9}
    assert _maj_inertie_mj(etat, []) == 9


def _recap(etat: dict) -> str:
    from server.config import load_config
    from server.llm.prompt_builder import PromptBuilder
    return PromptBuilder(load_config()).build_recap(etat)


def _etat_exploration(inertie: int) -> dict:
    return {
        "phase": "exploration",
        "pj": [{"nom": "Brandt"}],
        "quete": {"titre": "Dues For The Dead", "pitch": "Test.",
                  "source": ""},
        "histoire": [{"evenement": "Début de l'aventure : …"}],
        "memoire": {},
        "donjon": {"id": "d", "courant": [0, -4]},
        "inertie_mj": inertie,
    }


def test_recap_renforce_au_seuil_dinertie():
    recap = _recap(_etat_exploration(3))
    assert "INERTIE" in recap, recap[-400:]
    assert "(0,-4)" in recap


def test_recap_normal_sans_inertie():
    recap = _recap(_etat_exploration(2))
    assert "INERTIE" not in recap
