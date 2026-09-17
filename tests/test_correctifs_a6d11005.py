# -*- coding: utf-8 -*-
"""Régressions de la partie a6d11005 (examen 2026-09-17).

1. « ⚠️ Le MJ a rencontré un problème technique » : le `work` de la boucle
   grossissait sans limite (résultats d'outils ≤ 4 000 chars × itérations +
   correctifs) → prompt 18 709 tokens pour un ctx de 20 000 (n_predict
   2 048) → llama.cpp renvoyait 400. Désormais `_borner_work` borne le
   total AVANT chaque appel (en réservant aussi la place des schémas
   d'outils natifs envoyés hors de `work` — partie 5a9b99c8 : work 40 k +
   schémas 22 k = 21 058 tokens → 400 ; llama.cpp tronquait alors le début
   du prompt, le system était perdu et la narration sortait courte/coupée).
2. Combat contre un « monstre » générique : le rattrapage de combat en
   prose (`_detecter_combat_prose`) extrayait le MOT « monstre » et
   engageait un placeholder du bestiaire (fiche `generique: true`, nom
   vide). Ces fiches et ces mots sont désormais exclus, et
   `_est_monstre_generique` les refuse partout.
3. Ennemis du module annotés de LEUR salle dans la bible : plus d'Ombre
   d'endgame (salle 0,3) attaquée dans le temple d'entrée.
4. Consommables selon les règles : potion bue → dose déduite
   (`fiche_perso_soigner(source=…)`), flèche/carreau tirés → déduits par
   `lancer_attaque`.

Usage : py -m pytest tests/test_correctifs_a6d11005.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import _borner_work, _REQ_BUDGET_CHARS  # noqa: E402
from server.llm.prompt_builder import _ennemis_donjon  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.dice import lancer_attaque  # noqa: E402
from server.tools.fiches import fiche_perso_soigner  # noqa: E402
from server.tools.monstres import _est_monstre_generique  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

PID = "test_a6d11005"

# Placeholder tel qu'il existait dans bestiaire.json (fiche sans identité).
FICHE_MONSTRE_PLACEHOLDER = {
    "nom": "", "type": "inconnu", "taille": "M", "dv": "1d8",
    "pv": 10, "pv_max": 10, "ca": 10, "vitesse": "9m", "bab": "+0",
    "init": "+0", "attaques": "1 attaque corpo", "degs": "1d6",
    "fp": "1/4", "alignement": "Neutre", "cle": "monstre",
    "generique": True,
}


# --------------------------------------------------------------------------- #
# 1. Borne de contexte `_borner_work`
# --------------------------------------------------------------------------- #
def test_borner_work_tronque_les_plus_anciens():
    system = Message(role="system", content="S" * 5000)
    gros = [Message(role="user", content="X" * 9000) for _ in range(5)]
    recent = Message(role="user", content="R" * 2000)
    sortie = _borner_work([system] + gros + [recent])
    total = sum(len(m.content or "") for m in sortie)
    assert total <= _REQ_BUDGET_CHARS + 500  # budget + marqueur
    # Le system et le message le plus RÉCENT sont conservés.
    assert sortie[0].content.startswith("SSS")
    assert sortie[-1].content.startswith("RRR")
    # Sous le budget : liste inchangée.
    petit = [Message(role="system", content="sys"),
             Message(role="user", content="bonjour")]
    assert _borner_work(petit) == petit


def test_borner_work_reserve_la_place_des_schemas_natifs():
    """Les schémas d'outils sont envoyés HORS de `work` : ils doivent être
    réservés dans le budget, sinon la requête dépasse le ctx du serveur
    (partie 5a9b99c8 : 21 058 tokens > 20 224 → 400)."""
    system = Message(role="system", content="S" * 5000)
    gros = [Message(role="user", content="X" * 9000) for _ in range(6)]
    # Sans réserve : budget plein.
    sans = _borner_work([system] + gros)
    # Avec 20 000 chars réservés (schémas) : budget réduit de 20 000.
    avec = _borner_work([system] + gros, reserve_chars=20_000)
    t_sans = sum(len(m.content or "") for m in sans)
    t_avec = sum(len(m.content or "") for m in avec)
    assert t_avec < t_sans
    assert t_avec <= (_REQ_BUDGET_CHARS - 20_000) + 500
    # La réserve ne descend jamais sous le plancher (system + derniers échanges).
    enorme = _borner_work([system] + gros, reserve_chars=999_999)
    assert sum(len(m.content or "") for m in enorme) >= 5_000


# --------------------------------------------------------------------------- #
# 2. Placeholder « monstre » refusé partout
# --------------------------------------------------------------------------- #
def test_fiche_generique_et_sans_nom_sont_refusees():
    assert _est_monstre_generique(FICHE_MONSTRE_PLACEHOLDER)
    assert _est_monstre_generique({"generique": True, "nom": "x", "type": "?"})
    assert _est_monstre_generique({"nom": "", "type": "?"})
    assert not _est_monstre_generique({"nom": "Goule", "type": "Mort-vivant"})


def test_prose_monstre_surgit_ne_declenche_rien(tmp_path):
    from server.main import _detecter_combat_prose
    d = str(tmp_path)
    best = {"_meta": {}, "monstre": dict(FICHE_MONSTRE_PLACEHOLDER),
            "goule": {"nom": "Goule", "cle": "goule", "fp": "1",
                      "pv": 11, "ca": 13, "init": "+2"}}
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False)
    # « un monstre surgit » : le mot générique ne doit résoudre AUCUNE
    # créature (avant le fix : engageait le placeholder « monstre »).
    assert _detecter_combat_prose(
        d, "Soudain, un monstre surgit des ténèbres et vous attaque !",
        {"phase": "exploration"},
    ) == []
    # À l'inverse, une vraie créature nommée reste détectée.
    assert _detecter_combat_prose(
        d, "Une goule surgit de la crypte et vous attaque !",
        {"phase": "exploration"},
    ) == ["Goule"]


def test_bestiaire_ne_contient_plus_le_placeholder_monstre():
    import server.config as cfg_mod
    cfg = cfg_mod.get_config()
    chemin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "server", "data", "bestiaire.json",
    )
    b = json.load(open(chemin, encoding="utf-8"))
    assert "monstre" not in b
    # Les vraies créatures sont intactes.
    assert b["squelette"]["nom"] == "Squelette"


# --------------------------------------------------------------------------- #
# 3. Ennemis annotés de leur salle dans la bible
# --------------------------------------------------------------------------- #
def test_ennemis_annotes_par_salle_dans_la_bible():
    from server.llm.prompt_builder import _scenario_bible_bloc
    quete = {"bible": {"ennemis": ["Squelette"], "resume": "", "etapes": []}}
    etat = {"donjon": {"grille": [
        {"x": 0, "y": 0},
        {"x": 0, "y": 3, "ennemis": ["Ombre ×2"]},
        {"x": 4, "y": 3, "ennemis": ["Ombre ×2", "Squelette gelé ×6"]},
    ]}}
    bloc = _scenario_bible_bloc(quete, etat=etat)
    ligne = next(l for l in bloc.splitlines() if "DU SCÉNARIO" in l)
    assert "Ombre (salles 0,3, 4,3)" in ligne
    assert "PLACEMENT" in bloc


# --------------------------------------------------------------------------- #
# 4. Consommables : potions et munitions
# --------------------------------------------------------------------------- #
def _fiche_avec_inventaire(d: str, nom: str, objets: list[dict]) -> dict:
    fiche = {
        "nom": nom, "joueur": "alain", "proprietaire": "alain",
        "race": "Demi-orc", "classe": "Barbare", "niveau": 1,
        "pv": 8, "pv_max": 16, "ca": 14, "bab": 1,
        "carac": {"FOR": 19, "DEX": 10, "CON": 13, "INT": 9, "SAG": 12,
                  "CHA": 9},
        "conditions": [], "xp": 0, "or": 0,
        "inventaire": objets,
        "equipement": [{"nom": o["nom"], "qte": o["qte"]} for o in objets],
    }
    from server.persos import chemin_fiche
    with open(chemin_fiche(d, nom), "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=2)
    return fiche


def test_potion_bue_est_deduite_de_linventaire(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Utturgut", [
        {"nom": "potion de soins légers", "qte": 2, "poids": 0.5},
        {"nom": "Hache à deux mains", "qte": 1, "poids": 5.44},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 5, source="potion de soins légers"))
    assert not tr.text.startswith("❌"), tr.text
    assert "consommé" in tr.text and "restant : 1" in tr.text
    fiche = json.load(open(
        os.path.join(d, "fiches", "fiche_utturgut.json"),
        encoding="utf-8"))
    pot = next(e for e in fiche["inventaire"]
               if "potion" in e["nom"].lower())
    assert pot["qte"] == 2 - 1
    assert fiche["pv"] == 8 + 5


def test_potion_absente_refuse_le_soin(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Utturgut", [
        {"nom": "Hache à deux mains", "qte": 1, "poids": 5.44},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    pv_avant = 8
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 5, source="potion de soins légers"))
    assert tr.text.startswith("❌") and "REFUSÉ" in tr.text
    fiche = json.load(open(
        os.path.join(d, "fiches", "fiche_utturgut.json"),
        encoding="utf-8"))
    assert fiche["pv"] == pv_avant  # aucun soin appliqué


def test_derniere_potion_est_retiree_de_linventaire(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Utturgut", [
        {"nom": "fiole de guérison", "qte": 1, "poids": 0.3},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 4, source="fiole de guérison"))
    assert "dose épuisée" in tr.text
    fiche = json.load(open(
        os.path.join(d, "fiches", "fiche_utturgut.json"),
        encoding="utf-8"))
    assert not any("fiole" in e["nom"].lower()
                   for e in fiche["inventaire"])


def test_tir_a_larc_consomme_une_fleche(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Archer", [
        {"nom": "Arc court", "qte": 1, "poids": 0.9},
        {"nom": "Flèches", "qte": 20, "poids": 0.05},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(lancer_attaque(
        ctx, bonus_attaque=1, ca_cible=12, nom_attaquant="Archer",
        arme="Arc court", nom_cible="Squelette"))
    assert "1 × Flèches** consommée" in tr.text, tr.text
    fiche = json.load(open(
        os.path.join(d, "fiches", "fiche_archer.json"), encoding="utf-8"))
    fleches = next(e for e in fiche["inventaire"]
                   if "fleche" in e["nom"].lower()
                   or "flèche" in e["nom"].lower())
    assert fleches["qte"] == 19


def test_tir_sans_munition_avertit_sans_bloquer(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Archer", [
        {"nom": "Arc court", "qte": 1, "poids": 0.9},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(lancer_attaque(
        ctx, bonus_attaque=1, ca_cible=12, nom_attaquant="Archer",
        arme="Arc court", nom_cible="Squelette"))
    assert "Aucune flèche" in tr.text
    assert "Total attaque" in tr.text  # le jet reste résolu


def test_attaque_melee_ne_consomme_rien(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Utturgut", [
        {"nom": "Flèches", "qte": 20, "poids": 0.05},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(lancer_attaque(
        ctx, bonus_attaque=5, ca_cible=12, nom_attaquant="Utturgut",
        arme="Hache à deux mains", nom_cible="Squelette"))
    assert "consommée" not in tr.text
    fiche = json.load(open(
        os.path.join(d, "fiches", "fiche_utturgut.json"),
        encoding="utf-8"))
    assert fiche["inventaire"][0]["qte"] == 20


# --------------------------------------------------------------------------- #
# 5. Charges du kit de premiers secours (10 utilisations, D&D 3.5)
# --------------------------------------------------------------------------- #
def _fiche_avec_kit(d: str, nom: str, charges: int = 10) -> dict:
    return _fiche_avec_inventaire(d, nom, [
        {"nom": "Kit premiers secours", "qte": 1, "poids": 0.45,
         "charges": charges},
    ])


def _lire_fiche(d: str, nom: str) -> dict:
    return json.load(open(
        os.path.join(d, "fiches", f"fiche_{nom.lower()}.json"),
        encoding="utf-8"))


def test_kit_charge_deduite_source_explicite(tmp_path):
    d = str(tmp_path)
    _fiche_avec_kit(d, "Utturgut", charges=10)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 3, source="kit de premiers secours"))
    assert "1 charge consommée (9/10" in tr.text, tr.text
    fiche = _lire_fiche(d, "Utturgut")
    kit = next(e for e in fiche["inventaire"] if "kit" in e["nom"].lower())
    assert kit["charges"] == 9
    assert fiche["pv"] == 8 + 3


def test_kit_charge_auto_sur_petit_soin_sans_source(tmp_path):
    d = str(tmp_path)
    _fiche_avec_kit(d, "Utturgut", charges=7)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(ctx, "Utturgut", 3))
    assert "1 charge consommée (6/10" in tr.text, tr.text
    assert _lire_fiche(d, "Utturgut")["inventaire"][0]["charges"] == 6


def test_grand_soin_sans_source_ne_consomme_pas_de_charge(tmp_path):
    d = str(tmp_path)
    _fiche_avec_kit(d, "Utturgut", charges=10)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(ctx, "Utturgut", 8))
    assert "charge" not in tr.text
    assert _lire_fiche(d, "Utturgut")["inventaire"][0]["charges"] == 10


def test_source_sort_ne_consomme_pas_de_charge(tmp_path):
    d = str(tmp_path)
    _fiche_avec_kit(d, "Utturgut", charges=10)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 8, source="sort de soins"))
    assert "charge" not in tr.text
    assert not tr.text.startswith("❌")  # pas de refus pour la magie
    assert _lire_fiche(d, "Utturgut")["inventaire"][0]["charges"] == 10


def test_kit_epuise_est_retire_et_le_soin_refuse(tmp_path):
    d = str(tmp_path)
    _fiche_avec_kit(d, "Utturgut", charges=0)
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 3, source="kit de premiers secours"))
    assert tr.text.startswith("❌") and "REFUSÉ" in tr.text
    fiche = _lire_fiche(d, "Utturgut")
    # Le kit épuisé a été retiré de l'inventaire.
    assert not any("kit" in e["nom"].lower() for e in fiche["inventaire"])
    assert fiche["pv"] == 8  # aucun soin appliqué


def test_sans_kit_du_tout_source_kit_refuse(tmp_path):
    d = str(tmp_path)
    _fiche_avec_inventaire(d, "Utturgut", [
        {"nom": "Hache à deux mains", "qte": 1, "poids": 5.44},
    ])
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)
    tr = asyncio.run(fiche_perso_soigner(
        ctx, "Utturgut", 3, source="kit"))
    assert tr.text.startswith("❌") and "REFUSÉ" in tr.text


def test_charges_preservees_au_roundtrip_inventaire():
    from server.tools.inventaire import _inventaire, _reparer_entree
    entree = {"nom": "Kit premiers secours", "qte": 1, "charges": 4}
    reparee = _reparer_entree(entree)
    assert reparee["charges"] == 4
    fiche = {"inventaire": [dict(entree)]}
    assert _inventaire(fiche)[0]["charges"] == 4
