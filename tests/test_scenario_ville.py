"""Test scénario — une journée à Waterdeep (PHB 3.5 + règles-maison).

Déroulé (outillé, déterministe, sans LLM) :
  Setup     : équipe de 4 PJ (Guerrier, Magicienne, Druide, Clerc) créée via
              `fiche_perso_creer_rapide` (carac explicites → pas de tirage),
              bourse créditée, partie positionnée à Waterdeep (Métropole).
  1. Marché  : `marche_consulter` lit la localité depuis l'état de la partie
              (etat.lieu.nom) → rang 6, coefficient ×0.95 ; achat d'une
              Épée longue (143 pc) et d'une Chemise de mailles (950 pc)
              dans l'inventaire PERMANENT ; poids et encombrement recalculés.
              La CA n'est PAS modifiée par l'achat (port calculé au
              formulaire — vérifié via `calculer_ca_armure`).
  2. Auberge : repas + logement « bonne » → condition Rassasié, marqueur de
              nuitée ; `repos_long` → +ceil(niveau/2) PV bonus, fatigue
              retirée, marqueur CONSOMMÉ.
  3. Familier: appel du familier (Magicienne, rituel 100 po débité) et du
              compagnon animal (Druide, gratuit).
  4. Magie   : préparation d'un emplacement, combat engagé (Gobelin +
              Squelette), épée longue achetée au corps à corps (formule 1d8
              conforme au catalogue), projectile magique lancé en combat.
  5. Vente   : revente à 50 % du prix de base chez le marchand local.

Usage : py -m pytest tests/test_scenario_ville.py -v
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.persos import calculer_ca_armure                         # noqa: E402
from server.tools.base import ToolContext, invoke_tool               # noqa: E402
from server.tools.registry import discover_tools                     # noqa: E402

TOOLS = discover_tools("server.tools")
PID = "t_sc_waterdeep"

TOUCHE = ("✅ **Touché**", "⭐ **20 naturel**")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _fresh_dir() -> str:
    d = tempfile.mkdtemp(prefix="dnd35_ville_")
    shutil.copy2(
        os.path.join(ROOT, "server", "data", "bestiaire.json"),
        os.path.join(d, "bestiaire.json"),
    )
    return d


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="test", data_dir=d)


async def _tool(d: str, nom_outil: str, **args):
    return await invoke_tool(TOOLS[nom_outil], _ctx(d), args)


def _etat(d: str) -> dict:
    with open(os.path.join(d, f"partie_{PID}.json"), encoding="utf-8") as f:
        return json.load(f)


def _fiche(d: str, nom: str) -> dict:
    import unicodedata
    nf = unicodedata.normalize("NFKD", nom)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_").lower()
    with open(os.path.join(d, "fiches", f"fiche_{slug}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _seed_for_d20(valeur: int) -> int:
    for seed in range(10000):
        random.seed(seed)
        if random.randint(1, 20) == valeur:
            return seed
    raise AssertionError(f"aucune graine trouvée pour d20={valeur}")


def _seed_for_dice(nb: int, faces: int, somme: int) -> int:
    cible = somme - nb
    for seed in range(10000):
        random.seed(seed)
        if cible == sum(random.randint(1, faces) - 1 for _ in range(nb)):
            return seed
    raise AssertionError(f"aucune graine trouvée pour {nb}d{faces}={somme}")


# --------------------------------------------------------------------------- #
#  Setup — une équipe à Waterdeep
# --------------------------------------------------------------------------- #
PERSOS = [
    # (nom, race, classe, niveau, carac)
    ("Hugo", "Humain", "Guerrier", 2,
     {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 10, "CHA": 10}),
    # NB : la classe technique doit être « Magicien » (formes canoniques de
    # PREPARE / sort["classes"] / CLASSES_FAMILIER). Le personnage reste
    # narrativement une « magicienne ».
    ("Elara", "Elfe", "Magicien", 1,
     {"FOR": 8, "DEX": 14, "CON": 12, "INT": 16, "SAG": 10, "CHA": 10}),
    ("Sylva", "Humaine", "Druide", 1,
     {"FOR": 12, "DEX": 12, "CON": 14, "INT": 10, "SAG": 16, "CHA": 10}),
    ("Luna", "Humaine", "Clerc", 1,
     {"FOR": 12, "DEX": 12, "CON": 14, "INT": 10, "SAG": 16, "CHA": 10}),
]

OR_PAR_PJ = {"Hugo": 5000, "Elara": 3000, "Sylva": 500, "Luna": 500}


async def _creer_equipe(d: str) -> None:
    for nom, race, classe, niveau, caracs in PERSOS:
        carac_texte = ", ".join(f"{k.title()} {v}" for k, v in caracs.items())
        await _tool(d, "fiche_perso_creer_rapide",
                    nom=nom, race=race, classe=classe, niveau=niveau,
                    joueur=nom.lower(), carac_texte=carac_texte)
    etat = _etat(d)
    assert len(etat["pj"]) == 4
    assert etat["phase"] == "opening_complete"


async def _crediter_or(d: str) -> None:
    for nom, montant in OR_PAR_PJ.items():
        r = await _tool(d, "fiche_perso_mettre_a_jour", nom=nom, champ="or",
                        valeur=str(montant))
        assert r.text.startswith("✅"), r.text


async def _localite_waterdeep(d: str) -> None:
    r = await _tool(d, "etat_partie_patch",
                    chemin="lieu.nom", valeur="Waterdeep")
    assert r.text.startswith("✅"), r.text
    assert _etat(d)["lieu"]["nom"] == "Waterdeep"


# --------------------------------------------------------------------------- #
#  Scénario
# --------------------------------------------------------------------------- #
async def _acheter_equipement(d: str) -> None:
    """1) Marché : épée longue + chemise de mailles à Waterdeep (×0.95)."""
    r = await _tool(d, "marche_consulter")
    assert "Waterdeep" in r.text and "Métropole (rang 6/6)" in r.text
    assert "×0.95" in r.text

    r = await _tool(d, "marche_stock", marchand="forge", categorie="arme")
    assert "**Épée longue** — 143 pc" in r.text, r.text

    r = await _tool(d, "marche_acheter", nom="Hugo", article="Épée longue")
    assert r.text.startswith("✅"), r.text
    f = _fiche(d, "Hugo")
    assert f["or"] == 5000 - 143
    inv = [e["nom"] for e in f["inventaire"]]
    assert "Épée longue" in inv
    equi = [e["nom"] for e in f["equipement"]]
    assert "Épée longue" in equi

    # Chemise de mailles : 100 po → 1000 pc ×0.95 = 950 pc.
    r = await _tool(d, "marche_acheter",
                    nom="Hugo", article="Chemise de mailles")
    assert r.text.startswith("✅"), r.text
    f = _fiche(d, "Hugo")
    assert f["or"] == 5000 - 143 - 950
    assert "Chemise de mailles" in [e["nom"] for e in f["inventaire"]]
    # Poids porté augmenté (épée 1,81 kg + maille 11,34 kg) + encombrement.
    assert f["poids_transporte"] > 0
    assert f["etat_encumbrance"] in ("Legere", "Moyenne")
    # La CA de la fiche n'est PAS auto-recalculée à l'achat : le port est
    # structuré au formulaire de création. Chemise (CA+4, dex_max 4), DEX 12
    # (+1) → porter l'armure donnerait 15 à la création.
    assert f["ca"] == 10 + (12 - 10) // 2          # 11 : sans armure
    assert calculer_ca_armure(1, ["Chemise de mailles"]) == 15


async def _auberge_et_repos(d: str) -> None:
    """2) Auberge + repos long avec bonus de nuitée « bonne »."""
    r = await _tool(d, "auberge_commander", nom="Hugo",
                    repas="bonne", logement="bonne")
    assert r.text.startswith("🏨"), r.text
    f = _fiche(d, "Hugo")
    assert f["or"] == 5000 - 143 - 950 - 25       # 5 pc repas + 20 pc logement
    conds = [str(x).lower() for x in f["conditions"]]
    assert "rassasié" in conds and "affamé" not in conds
    assert f["auberge"]["logement"] == "bonne"

    # Hugo est blessé (2 PV manquants par rapport à pv_max) et fatigué.
    f = _fiche(d, "Hugo")
    r = await _tool(d, "fiche_perso_mettre_a_jour", nom="Hugo", champ="pv",
                    valeur=str(int(f["pv_max"]) - 2))
    assert r.text.startswith("✅"), r.text
    await _tool(d, "fiche_perso_condition", nom="Hugo",
                condition="Fatigue", appliquer="true")

    r = await _tool(d, "repos_long", nom_personnage="Hugo")
    assert "nuitée d'auberge" in r.text, r.text
    f = _fiche(d, "Hugo")
    assert f["pv"] == f["pv_max"]                 # soin : niv.2 + ceil(2/2)=1
    assert "auberge" not in f                     # marqueur consommé
    assert "Fatigue" not in str(f["conditions"])


async def _appeler_familiers(d: str) -> None:
    """3) Familier de la Magicienne (100 po) et compagnon du Druide (gratuit)."""
    r = await _tool(d, "fiche_perso_mettre_a_jour", nom="Elara", champ="familier",
                    valeur=json.dumps({"type": "familier", "espece": "Chat"}))
    assert r.text.startswith("✅"), r.text
    r = await _tool(d, "appeler_familier", nom_personnage="Elara")
    assert "invoqué" in r.text.lower() or "familier" in r.text.lower()
    f = _fiche(d, "Elara")
    assert f["or"] == 3000 - 1000                 # rituel 100 po → 1000 pc
    assert f["familier"]["invoque"] is True

    r = await _tool(d, "fiche_perso_mettre_a_jour", nom="Sylva", champ="familier",
                    valeur=json.dumps({"type": "compagnon", "espece": "Loup"}))
    assert r.text.startswith("✅"), r.text
    r = await _tool(d, "appeler_familier", nom_personnage="Sylva")
    assert r.text.startswith("🌿") or "compagnon" in r.text.lower()
    f = _fiche(d, "Sylva")
    assert f["or"] == 500                          # aucun coût
    assert f["familier"]["invoque"] is True


async def _combat_avec_equipement(d: str) -> None:
    """4) Magie préparée puis combat : épée longue achetée + projectile."""
    r = await _tool(d, "preparer_sorts", nom_personnage="Elara",
                    preparations_json='{"Projectiles magiques": 1}')
    assert "Projectiles magiques" in r.text, r.text

    r = await _tool(d, "engager_combat", monstres="Gobelin, Squelette")
    assert "⚔️ **Combat engagé ! Tour 1" in r.text
    etat = _etat(d)
    noms_m = [m["nom"] for m in etat["monstres_combat"]]
    assert noms_m == ["Gobelin", "Squelette"]
    by_nom = {m["nom"]: m for m in etat["monstres_combat"]}
    assert by_nom["Gobelin"]["pv"] == 5 and by_nom["Squelette"]["pv"] == 3

    # Hugo frappe le Gobelin avec l'épée longue achetée (CA 15).
    bonus_hugo = 2 + 3                             # BBA 2 + mod FOR +3
    for _ in range(20):
        random.seed(_seed_for_d20(15))  # prochain d20 = 15 → 20 ≥ 15, touché
        ra = await _tool(d, "lancer_attaque", nom_attaquant="Hugo",
                         arme="Épée longue", bonus_attaque=bonus_hugo,
                         nom_cible="Gobelin", ca_cible=15)
        assert "Total attaque :" in ra.text
        if any(m in ra.text for m in TOUCHE):
            break
    assert any(m in ra.text for m in TOUCHE), "Hugo doit finir par toucher"
    # Formule conforme au catalogue (Épée longue = 1d8+3), dégât maximal.
    random.seed(_seed_for_dice(1, 8, 8))
    rd = await _tool(d, "lancer_degats", nb_des=1, faces=8, bonus=3,
                     arme_ou_sort="Épée longue", cible="Gobelin")
    assert "⛔" not in rd.text, rd.text            # formule conforme
    assert "Dégâts infligés" in rd.text
    # lancer_degats ne modifie PAS l'état : on applique le total (8+3=11).
    ri = await _tool(d, "fiche_perso_infliger_degats",
                     nom="Gobelin", degats=11)
    assert "Gobelin" in ri.text
    etat = _etat(d)
    gob = next(m for m in etat["monstres_combat"] if m["nom"] == "Gobelin")
    assert int(gob["pv"]) <= 0, f"Gobelin devrait être détruit : {gob}"

    # Elara lance un projectile magique sur le Squelette (3 PV).
    random.seed(_seed_for_dice(1, 4, 4))
    rs = await _tool(d, "incanter_sort", nom_personnage="Elara",
                     nom_sort="Projectiles magiques", cible="Squelette")
    assert "Projectiles magiques" in rs.text
    assert "Dégâts" in rs.text
    etat = _etat(d)
    skel = next(m for m in etat["monstres_combat"] if m["nom"] == "Squelette")
    assert skel["pv"] <= 0, f"Squelette devrait être détruit : {skel}"
    # Emplacement de niveau 1 consommé (`depenses` trace l'utilisation).
    f = _fiche(d, "Elara")
    assert int(f["sorts"]["depenses"].get("1", 0)) >= 1

    r = await _tool(d, "finir_combat")
    assert _etat(d)["phase"] == "exploration"


async def _vente(d: str) -> None:
    """5) Revente : 50 % du prix de base (Épée longue 15 po → 75 pc)."""
    r = await _tool(d, "marche_vendre", nom="Hugo", article="Épée longue")
    assert r.text.startswith("💰"), r.text
    assert "75 pc" in r.text
    f = _fiche(d, "Hugo")
    assert f["or"] == 5000 - 143 - 950 - 25 + 75
    assert "Épée longue" not in [e["nom"] for e in f["inventaire"]]


# --------------------------------------------------------------------------- #
#  Exécution du scénario (un fil continu, une seule partie)
# --------------------------------------------------------------------------- #
async def test_scenario_journee_a_waterdeep():
    d = _fresh_dir()
    try:
        await _creer_equipe(d)
        await _crediter_or(d)
        await _localite_waterdeep(d)
        await _acheter_equipement(d)
        await _auberge_et_repos(d)
        await _appeler_familiers(d)
        await _combat_avec_equipement(d)
        await _vente(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)