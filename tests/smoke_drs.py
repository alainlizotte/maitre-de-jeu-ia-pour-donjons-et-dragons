# -*- coding: utf-8 -*-
"""Smoke test des chantiers DRS P1/P2/P3 — sans serveur ni LLM."""
import asyncio
import faulthandler
import io
import json
import os
import sys
import tempfile

faulthandler.dump_traceback_later(60, exit=True)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from server.tools.registry import discover_tools          # noqa: E402
from server.tools.base import ToolContext, invoke_tool    # noqa: E402
from server.game.state import PartyState                  # noqa: E402

TOOLS = discover_tools("server.tools")
print("tools enregistrés :", len(TOOLS))

NOUVEAUX = ["attaque_opportunite", "attaque_speciale", "combat_preparer_action",
            "combat_attendre", "danger_chute", "danger_noyade",
            "danger_environnement", "voyage_meteo_jour", "voyage_repas",
            "voyage_prospection"]
manquants = [n for n in NOUVEAUX if n not in TOOLS]
assert not manquants, f"tools manquants : {manquants}"
print("✔ nouveaux tools enregistrés :", len(NOUVEAUX))

TMP = tempfile.mkdtemp(prefix="smoke_drs_")
# Le bestiaire réel doit être présent dans le data_dir du test (les tools
# chargent data_dir/bestiaire.json).
import shutil
_shutil_src = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "server", "data", "bestiaire.json")
shutil.copy(_shutil_src, os.path.join(TMP, "bestiaire.json"))
PID = "smoke0001"


def ctx() -> ToolContext:
    return ToolContext(partie_id=PID, joueur="smoke", data_dir=TMP)


async def tool(nom_tool: str, **args):
    return await invoke_tool(TOOLS[nom_tool], ctx(), args)


async def main():
    # --- État de partie + fiche PJ --------------------------------------
    ps = PartyState(data_dir=TMP, partie_id=PID)
    etat = {"phase": "exploration", "pj": [{"nom": "Aldric", "joueur": "smoke",
                                            "pv": 12, "pv_max": 12,
                                            "ca": 16,
                                            "conditions": []}]}
    ps.save(etat)

    from server.tools.fiches import _chemin
    fiche = {
        "nom": "Aldric", "classe": "Guerrier", "niveau": 2, "race": "Humain",
        "bab": 2, "pv": 12, "pv_max": 12, "ca": 16, "or": 500,
        "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 10,
                  "CHA": 10},
        "sauvegardes": {"Vigueur": 5, "Reflexes": 1, "Volonte": 0},
        "competences": {"Survie": 2, "Saut": 2},
        "dons": [], "conditions": [],
        "equipement": [{"nom": "Épée longue", "qte": 1}],
        "inventaire": [{"nom": "Rations journalières", "qte": 2}],
    }
    with open(_chemin(ctx(), "Aldric"), "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False)

    # 1) Critique : confirmation serveur ---------------------------------
    found_crit = False
    for _ in range(120):
        tr = await tool("lancer_attaque", bonus_attaque=5, ca_cible=10,
                        nom_attaquant="Aldric", arme="Épée longue",
                        nom_cible="Gobelin de test")
        t = tr.text
        if "CRITIQUE CONFIRMÉ" in t or "NON confirmée" in t:
            found_crit = True
            assert ("CRITIQUE CONFIRMÉ" in t) == ("multiplicateur=2" in t)
            break
    print("✔ confirmation de critique résolue serveur :", found_crit)

    # 2) multiplicateur dans lancer_degats -------------------------------
    tr = await tool("lancer_degats", nb_des=1, faces=8, bonus=3,
                    arme_ou_sort="Épée longue", cible="Gobelin de test",
                    attaquant="Aldric", multiplicateur=2)
    assert "×2" in tr.text and "critique" in tr.text, tr.text
    print("✔ multiplicateur de critique dans lancer_degats")

    # 3) Conditions : Renversé (attaquant) --------------------------------
    from server.tools.fiches import _save_fiche
    fiche["conditions"] = ["Renversé"]
    _save_fiche(ctx(), "Aldric", fiche)
    tr = await tool("lancer_attaque", bonus_attaque=5, ca_cible=10,
                    nom_attaquant="Aldric", arme="Épée longue",
                    nom_cible="Gobelin de test")
    assert "Renversé (−4)" in tr.text, tr.text
    fiche["conditions"] = []
    _save_fiche(ctx(), "Aldric", fiche)
    print("✔ condition Renversé → −4 attaque appliquée")

    # 4) Non-létal ---------------------------------------------------------
    tr = await tool("fiche_perso_infliger_degats", nom="Aldric", degats=12,
                    non_letal=True)
    assert "NON LÉTAUX" in tr.text and "INCONSCIENT" in tr.text, tr.text
    tr = await tool("fiche_perso_soigner", nom="Aldric", soin=15,
                    interne=True)
    assert "Non-létal d'abord" in tr.text, tr.text
    print("✔ réserve non-létale + soin entame la réserve")

    # 5) RD monstre : parse + réduction -----------------------------------
    from server.game.regles35 import defenses_monstre, reduire_degats_monstre
    golem = {"nom": "Golem de fer (test)",
             "capacites": "réduction des dégâts (15/adamantium)",
             "faiblesses": ""}
    d = defenses_monstre(golem)
    assert d["rd"] and d["rd"][0][0] == 15, d
    red, note = reduire_degats_monstre(golem, 20, "Épée longue")
    assert red == 5 and "RD 15" in note, (red, note)
    red2, note2 = reduire_degats_monstre(golem, 20, "Épée longue (adamantium)")
    assert red2 == 20, (red2, note2)
    dragon = {"nom": "Dragon rouge (test)",
              "capacites": "immunité contre le feu, résistance au froid (10)"}
    red3, note3 = reduire_degats_monstre(dragon, 30, "Boule de feu")
    assert red3 == 0 and "IMMUNISÉ" in note3, (red3, note3)
    red4, _ = reduire_degats_monstre(dragon, 20, "Cône de froid")
    assert red4 == 10, red4
    print("✔ RD / immunités / résistances parsées et appliquées")

    # 6) Combat : PV monstres + RD appliquée via infliger -----------------
    # (monstre RÉEL du bestiaire : Golem de fer — RD 15/adamantium)
    etat = ps.load()
    etat["monstres_combat"] = [{
        "nom": "Golem de fer", "pv": 60, "pv_max": 60, "ca": 30,
        "fp": "13", "conditions": [], "degats_recus": 0}]
    ps.save(etat)
    tr = await tool("fiche_perso_infliger_degats", nom="Golem de fer",
                    degats=20, source="Épée longue")
    assert "RD 15/adamantium" in tr.text and "PV 55/60" in tr.text, tr.text
    tr = await tool("fiche_perso_infliger_degats", nom="Golem de fer",
                    degats=20, source="Épée longue (adamantium)")
    assert "PV 35/60" in tr.text, tr.text
    print("✔ RD appliquée à l'application des dégâts (Golem de fer réel, "
          "contournée par l'adamantium)")

    # 7) Butin par ND ------------------------------------------------------
    from server.game.regles35 import generer_tresor, decrire_tresor
    t1 = generer_tresor(3)
    assert "total_pc" in t1 and t1["total_pc"] >= 0
    txt = decrire_tresor(t1)
    assert "Trésor de la rencontre" in txt
    print("✔ générateur de trésor par ND :", t1["total_pc"], "pc")

    # 8) Chute -------------------------------------------------------------
    tr = await tool("danger_chute", nom_personnage="Aldric", hauteur_m=9,
                    surface_meuble=True)
    assert "Chute" in tr.text and ("létaux" in tr.text
                                   or "aucun dégât" in tr.text), tr.text
    print("✔ danger_chute")

    # 9) Noyade ------------------------------------------------------------
    tr = await tool("danger_noyade", nom_personnage="Aldric", sous_eau=True)
    assert "noyade" in tr.text.lower() and "souffle" in tr.text.lower()
    print("✔ danger_noyade (séquence 0/-1/mort)")

    # 10) Météo de voyage ---------------------------------------------------
    etat = ps.load()
    etat["voyage"] = {"jours": 2, "region_climat": "desert",
                      "jours_meteo": [
                          {"jour": 1, "effet": "canicule", "txt": "Chaud"},
                          {"jour": 2, "effet": "clair", "txt": "Calme"}]}
    ps.save(etat)
    tr = await tool("voyage_meteo_jour", jour=1)
    assert "Météo du jour 1" in tr.text, tr.text
    print("✔ voyage_meteo_jour (jets de chaleur serveur)")

    # 11) Repas -------------------------------------------------------------
    tr = await tool("voyage_repas", jour=1)
    assert "Repas du jour 1" in tr.text, tr.text
    print("✔ voyage_repas (rations consommées)")

    # 12) attaque_speciale : aider + consommation --------------------------
    etat = ps.load()
    etat["phase"] = "combat"
    etat["tour"] = 1
    etat["initiative"] = [{"nom": "Aldric", "init": 10},
                          {"nom": "Golem de fer", "init": 5}]
    etat["monstres_combat"] = [{
        "nom": "Golem de fer", "pv": 35, "pv_max": 60, "ca": 30,
        "fp": "13", "conditions": [], "degats_recus": 25}]
    etat["courant_tour_pour"] = "Aldric"
    ps.save(etat)
    tr = await tool("attaque_speciale", type_attaque="aider",
                    nom_attaquant="Aldric", nom_cible="Aldric",
                    bonus_attaquant=20)
    assert "+2" in tr.text and "Aider" in tr.text, tr.text
    tr = await tool("lancer_attaque", bonus_attaque=5, ca_cible=30,
                    nom_attaquant="Aldric", arme="Épée longue",
                    nom_cible="Golem de fer")
    assert "Aider autrui" in tr.text, tr.text
    print("✔ attaque_speciale(aider) + consommation du bonus")

    # 13) attendre + restaurer l'ordre -------------------------------------
    tr = await tool("combat_attendre")
    assert "ATTEND" in tr.text or "attend" in tr.text
    from server.game.combat import _avancer_curseur
    etat = ps.load()
    # courant = DERNIER de l'ordre → l'avance suivante déclenche le wrap de
    # round, qui doit restaurer l'ordre d'origine et purger la mémoire.
    etat["courant_tour_pour"] = etat["initiative"][-1]["nom"]
    _avancer_curseur(etat)
    assert etat.get("initiative_originale") is None, etat.get(
        "initiative_originale")
    assert [e["nom"] for e in etat["initiative"]] == ["Aldric",
                                                      "Golem de fer"]
    print("✔ combat_attendre + restauration de l'ordre au wrap")

    # 14) surprise ----------------------------------------------------------
    etat = ps.load()
    etat["surpris"] = ["Golem de fer"]
    etat["tour"] = 1
    ps.save(etat)
    tr = await tool("lancer_attaque", bonus_attaque=5, ca_cible=30,
                    nom_attaquant="Aldric", arme="Épée longue",
                    nom_cible="Golem de fer")
    assert "PRIS AU DÉPOURVU" in tr.text, tr.text
    print("✔ surprise : cible prise au dépourvu → −2 CA")

    print("\n✅ SMOKE TESTS COMPLETS : tout est vert")


asyncio.run(main())
