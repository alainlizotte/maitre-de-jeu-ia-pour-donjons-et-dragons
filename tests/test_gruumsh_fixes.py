# -*- coding: utf-8 -*-
"""Correctifs audit gruumsh4pj (32 défauts) — tests déterministes.

Couvre : XP prouvée par les dégâts réels (P15/P20), préparations en liste
(P1/P19), compteurs à 0 purgés (P26b), Armure du mage effective (P24),
trésor canonique sans double-crédit (P29), devise or (P29), purge des
timeouts fantômes / tokens non-latins / erreurs tool (P27/P5/P17)."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.tools.base import invoke_tool  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "gruumsh_fix"


def _ctx(d, joueur="J1"):
    from server.tools.base import ToolContext
    return ToolContext(partie_id=PID, joueur=joueur, data_dir=d)


def _ecrire_fiche(d, fiche):
    fiches = Path(d) / "fiches"
    fiches.mkdir(parents=True, exist_ok=True)
    slug = str(fiche["nom"]).lower()
    (fiches / f"fiche_{slug}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")


def _lire_fiche(d, nom):
    slug = str(nom).lower()
    return json.loads((Path(d) / "fiches" / f"fiche_{slug}.json")
                      .read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# P15/P20 — XP uniquement sur dégâts réels
# --------------------------------------------------------------------------- #
def test_victoire_sans_degats_ne_pai_pas_xp():
    from server.game.combat import cloturer
    from server.game.combat import ResultatBoucle
    d = tempfile.mkdtemp(prefix="gruumsh_xp_")
    try:
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({
            "phase": "combat",
            "monstres_combat": [
                {"nom": "Bandit", "pv": 0, "pv_max": 11, "ca": 12,
                 "fp": "1/4", "conditions": ["Détruit"], "degats_recus": 0},
            ],
            "monstres_derniers": [
                {"nom": "Bandit", "fp": "1/4", "degats_recus": 0},
            ],
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 10}],
        })
        _ecrire_fiche(d, {"nom": "Brunhild", "classe": "Guerrier",
                          "niveau": 1, "xp": 0, "pv": 10, "pv_max": 12,
                          "ca": 15, "carac": {"FOR": 14, "DEX": 12},
                          "equipement": []})
        res = ResultatBoucle()
        asyncio.run(cloturer(_ctx(d), res, "victoire"))
        assert _lire_fiche(d, "Brunhild")["xp"] == 0
        assert any("Aucun dégât infligé" in e for e in res.events)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_victoire_avec_degats_pai_xp():
    from server.game.combat import cloturer
    from server.game.combat import ResultatBoucle
    d = tempfile.mkdtemp(prefix="gruumsh_xp2_")
    try:
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({
            "phase": "combat",
            "monstres_combat": [
                {"nom": "Gobelin", "pv": 0, "pv_max": 5, "ca": 15,
                 "fp": "1/3", "conditions": ["Détruit"], "degats_recus": 7},
            ],
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 10}],
        })
        _ecrire_fiche(d, {"nom": "Brunhild", "classe": "Guerrier",
                          "niveau": 1, "xp": 0, "pv": 10, "pv_max": 12,
                          "ca": 15, "carac": {"FOR": 14, "DEX": 12},
                          "equipement": []})
        res = ResultatBoucle()
        asyncio.run(cloturer(_ctx(d), res, "victoire"))
        assert _lire_fiche(d, "Brunhild")["xp"] == 135
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# P1/P19 — preparer_sorts accepte une liste native
# --------------------------------------------------------------------------- #
def test_preparer_sorts_accepte_liste_native():
    d = tempfile.mkdtemp(prefix="gruumsh_prep_")
    try:
        fiche = {"nom": "Zarkon", "classe": "Magicien", "niveau": 1,
                 "carac": {"FOR": 8, "DEX": 14, "CON": 12, "INT": 16,
                           "SAG": 12, "CHA": 10},
                 "pv": 6, "pv_max": 6, "ca": 12,
                 "sorts": {"connus": ["Sommeil", "Armure du mage"],
                           "prepares": {}, "depenses": {}},
                 "equipement": []}
        _ecrire_fiche(d, fiche)
        # Format qui échouait : une LISTE JSON (le petit modèle l'envoyait
        # constamment) — désormais coercée en {nom: 1}.
        tr = asyncio.run(invoke_tool(
            TOOLS["preparer_sorts"], _ctx(d),
            {"nom_personnage": "Zarkon",
             "preparations_json": ["Sommeil", "Armure du mage"]}))
        assert "📖" in tr.text, tr.text
        prep = _lire_fiche(d, "Zarkon")["sorts"]["prepares"]
        assert prep.get("Sommeil") == 1
        assert prep.get("Armure du mage") == 1
        # Le message d'erreur montre maintenant le format attendu. Une
        # chaîne NON-JSON (« pas du json ») est traitée comme une liste de
        # noms de sorts — le refus honnête devient « Sort inconnu ».
        tr2 = asyncio.run(invoke_tool(
            TOOLS["preparer_sorts"], _ctx(d),
            {"nom_personnage": "Zarkon",
             "preparations_json": "pas du json"}))
        assert "Sort inconnu" in tr2.text or "Deux formats" in tr2.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# P24 — Armure du mage : +4 CA réels, purge à échéance
# --------------------------------------------------------------------------- #
def test_armure_du_mage_applique_ca_et_expire():
    d = tempfile.mkdtemp(prefix="gruumsh_am_")
    try:
        fiche = {"nom": "Zarkon", "classe": "Magicien", "niveau": 1,
                 "carac": {"FOR": 8, "DEX": 14, "CON": 12, "INT": 16,
                           "SAG": 12, "CHA": 10},
                 "pv": 6, "pv_max": 6, "ca": 12,
                 "sorts": {"connus": ["Armure du mage"], "prepares": {},
                           "depenses": {}},
                 "equipement": []}
        fiche["sorts"]["prepares"] = {"Armure du mage": 1}
        _ecrire_fiche(d, fiche)
        tr = asyncio.run(invoke_tool(
            TOOLS["incanter_sort"], _ctx(d),
            {"nom_personnage": "Zarkon", "nom_sort": "Armure du mage"}))
        f = _lire_fiche(d, "Zarkon")
        assert f["ca"] == 16, f["ca"]
        assert "🛡️ Armure magique" in tr.text
        # Expiration : le bonus est restitué au chargement.
        from datetime import datetime as _dt, timedelta as _td
        f["effets_actifs"][0]["fin_ts"] = (
            _dt.now() - _td(minutes=5)).isoformat()
        _ecrire_fiche(d, f)
        from server.tools.fiches import _load_fiche
        f2 = _load_fiche(_ctx(d), "Zarkon")
        assert f2["ca"] == 12
        assert not f2.get("effets_actifs")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# P29 — devise or + trésor canonique sans double-crédit
# --------------------------------------------------------------------------- #
def test_or_narre_multi_devise():
    from server.llm.orchestrator import _or_gagne_narre
    # Le gain retenu est le PLUS ÉLEVÉ en po (l'or domine l'argent).
    assert _or_gagne_narre(
        "Vous trouvez 600 pièces d'argent et 100 pièces d'or.") == 100.0
    assert _or_gagne_narre(
        "Dans le coffre, vous trouvez un sac contenant 50 po.") == 50.0
    assert _or_gagne_narre(
        "Il ramasse 20 pc négligemment dans la poussière.") == 0.2
    assert _or_gagne_narre(
        "Empchez 12 pièces d'or sur le corps.") == 12.0


def test_tresor_canonique_pas_de_double_credit():
    d = tempfile.mkdtemp(prefix="gruumsh_tres_")
    try:
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({
            "phase": "exploration",
            "pj": [{"nom": "Liriel", "joueur": "J1"}],
            "donjon": {
                "id": "test", "etage": 0, "courant": [0, 0],
                "grille": [{"x": 0, "y": 0, "type": "entrée",
                            "portes": {},
                            "tresor": "600 pa, 100 po et 6 gemmes de 4 po "
                                      "chacune, dans les deux sacs."}],
            },
        })
        _ecrire_fiche(d, {"nom": "Liriel", "classe": "Magicien", "niveau": 1,
                          "xp": 0, "pv": 6, "pv_max": 6, "ca": 12, "or": 60,
                          "carac": {"INT": 16}, "equipement": []})
        args = {"nom": "Liriel", "objet": "600 pièces d'argent",
                "portee": "permanent"}
        tr1 = asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"],
                                      _ctx(d), args))
        assert "Trésor canonique" in tr1.text, tr1.text
        or1 = _lire_fiche(d, "Liriel")["or"]
        # 100 po + 600 pa = 160 po = 1600 unités internes.
        assert or1 == 60 + 1600, or1
        # 2e ramassage du même trésor → refus, AUCUN nouveau crédit.
        tr2 = asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"],
                                      _ctx(d), args))
        assert "DÉJÀ" in tr2.text or "déjà" in tr2.text, tr2.text
        assert _lire_fiche(d, "Liriel")["or"] == or1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# P27/P5/P17 — purge des timeouts fantômes, tokens non-latins, erreurs tool
# --------------------------------------------------------------------------- #
def test_purge_timeout_fantome_et_non_latin():
    from server.main import _nettoyer_meta_narration
    txt = (
        "Solenne a laissé passer son tour, la limite de vingt secondes "
        "dépassée. L'inaction de la prêtresse ne génère aucun effet.\n\n"
        "Le chien grogne低沉 dans l'ombre.\n\n"
        "⚠️ type_sauvegarde invalide : 'Réflexion'. Attendu : Vigueur, "
        "Réflexes ou Volonté.\n\n"
        "Thorrak abat sa hache : l'ennemi encaisse 7 dégâts."
    )
    sortie = _nettoyer_meta_narration(txt)
    assert "vingt secondes" not in sortie
    assert "低沉" not in sortie
    assert "invalide" not in sortie
    assert "7 dégâts" in sortie


def test_rotation_initiative_blesse_seulement_avec_degats():
    """M6/P9 : l'engagement d'une espèce complète la rencontre canonique."""
    d = tempfile.mkdtemp(prefix="gruumsh_p9_")
    try:
        st = PartyState(data_dir=d, partie_id=PID)
        st.save({
            "phase": "exploration",
            "pj": [{"nom": "Brunhild", "joueur": "J1", "pv": 12,
                    "pv_max": 12, "ca": 15,
                    "carac": {"FOR": 14, "DEX": 12}, "conditions": []}],
            "donjon": {
                "id": "test", "etage": 0, "courant": [0, 0],
                "grille": [{"x": 0, "y": 0, "type": "entrée",
                            "portes": {"nord": True},
                            "ennemis": ["Bandit ×2", "Chien ×2"]}],
            },
        })
        _ecrire_fiche(d, {"nom": "Brunhild", "classe": "Guerrier",
                          "niveau": 1, "xp": 0, "pv": 12, "pv_max": 12,
                          "ca": 15, "bab": 1,
                          "carac": {"FOR": 14, "DEX": 12, "CON": 14},
                          "conditions": [], "equipement": []})
        shutil.copy2(
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "server", "data",
                "bestiaire.json"),
            os.path.join(d, "bestiaire.json"))
        tr = asyncio.run(invoke_tool(
            TOOLS["engager_combat"], _ctx(d), {"monstres": "Bandit ×2"}))
        etat = st.load()
        noms = [m["nom"] for m in etat.get("monstres_combat") or []]
        assert any("Chien" in n for n in noms), (tr.text, noms)
        assert any("Bandit" in n for n in noms)
        # Chaque monstre porte la trace de dégâts à zéro.
        assert all(int(m.get("degats_recus") or 0) == 0
                   for m in etat["monstres_combat"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
