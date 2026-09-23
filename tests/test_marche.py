"""Tests — marché (achat/vente en ville) et auberge (PHB 3.5 + règles-maison).

Couvre :
- conversion monétaire pc (`_pc`), affichage prix, seuil minimum 1 pc ;
- gating par type de peuplement : armes simples/martiales/exotiques, armures,
  substances rares, sorts de service (rang de ville ≥ rang requis) ;
- `marche_acheter` : coefficient local, débit d'or, inventaire permanent ;
- `marche_vendre` : revente à 50 % du prix de base, refus sans objet / sans
  acheteur local ;
- auberge (`auberge_commander`) : qualités disponibles par ville, tarifs,
  conditions « Affamé »/« Rassasié », marqueur de nuitée « bonne » ;
- `repos_long` avec le marqueur d'auberge : bonus +ceil(niveau/2) PV et
  retrait de « fatigue »/« épuisé » (règle-maison documentée).

USAGE
-----
    py -m pytest tests/test_marche.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import equipement_phb as phb                            # noqa: E402
from server import villes                                           # noqa: E402
from server.game.state import PartyState                            # noqa: E402
from server.tools.base import ToolContext, invoke_tool              # noqa: E402
from server.tools.fiches import _slug                               # noqa: E402
from server.tools.marche import repos_bonus_auberge                 # noqa: E402
from server.tools.registry import discover_tools                    # noqa: E402

TOOLS = discover_tools("server.tools")
_PID = "t_mar"

MARCHAND = {
    "nom": "Hugo", "race": "Humain", "classe": "Guerrier", "niveau": 2,
    "carac": {"FOR": 15, "DEX": 12, "CON": 14, "INT": 10, "SAG": 10, "CHA": 10},
    "pv": 2, "pv_max": 12, "ca": 16, "bab": 2, "or": 5000,
    "sauvegardes": {"Vigueur": 4, "Reflexes": 1, "Volonte": 1},
    "conditions": ["Fatigue"],
}


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=_PID, joueur="Test", data_dir=d)


def _fresh_dir() -> str:
    return tempfile.mkdtemp(prefix="dnd35_mar_")


def _ecrire_fiche(d: str, fiche: dict) -> None:
    fd = Path(d) / "fiches"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")


def _lire_fiche(d: str, nom: str) -> dict:
    p = Path(d) / "fiches" / f"fiche_{_slug(nom)}.json"
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  Monnaie / prix
# --------------------------------------------------------------------------- #
def test_conversion_monetaire_pc():
    assert phb._pc(2) == 20                     # 2 po = 20 pc
    assert phb._pc(0, 3) == 3                   # 3 pa = 3 pc
    assert phb._pc(1, 0, 6) == 11               # 1 po + 0,6 pc → 11 pc
    assert phb._pc(1, 0, 4) == 10               # 0,4 pc → 0 (arrondi tombé)
    assert phb._pc(0) == 1                      # gratuit officiel → 1 pc (min)
    assert phb._prix_str(0) == "0 po"


def test_prix_article_multiplicateur_local():
    torche = phb.article("Torche")              # 1 pc (1 pc officiel)
    assert phb.prix_article(torche, "Hameau") == 2     # ×1,25 → ceil(2)
    assert phb.prix_article(torche, "Village") == 2    # ×1,15 → ceil(2)
    assert phb.prix_article(torche, "Métropole") == 1  # ×0,95 → ceil(1)
    epee = phb.article("Épée longue")           # 15 po → 150 pc
    assert phb.prix_article(epee, "Métropole") == 143  # ceil(150×0,95)
    assert phb.prix_revente(epee) == 75                 # moitié du prix de base
    assert phb.prix_revente(phb.article("Torche")) == 1  # minimum 1 pc


# --------------------------------------------------------------------------- #
#  Type de peuplement & gating
# --------------------------------------------------------------------------- #
def test_types_et_classification():
    assert villes.type_de_ville("Phandalin") == "Village"
    assert villes.type_de_ville("Secomber") == "Bourg"
    assert villes.type_de_ville("Everlund") == "Ville"
    assert villes.type_de_ville("Baldur's Gate") == "Cité"
    assert villes.type_de_ville("Waterdeep") == "Métropole"
    assert villes.type_de_ville("Localité inconnue") == "Bourg"  # repli
    assert villes.multiplicateur("Hameau") == 1.25
    assert villes.multiplicateur("Métropole") == 0.95
    assert villes.sorts_max_niveau("Bourg") == 1
    assert villes.sorts_max_niveau("Cité") == 6
    assert villes.auberge_qualites("Village") == ["mediocre", "convenable"]
    assert "chantier naval" in villes.marchands("Cité")
    assert "forge" not in villes.marchands("Hameau")


def test_gating_marchand_par_rang():
    epee = phb.article("Épée longue")           # martiale → rng ≥ 3
    assert phb.marchands_pour(epee, "Métropole") == ["forge"]
    assert phb.marchands_pour(epee, "Village") == []
    baton = phb.article("Bâton")                # simple → partout avec forge
    assert phb.marchands_pour(baton, "Hameau") == []
    assert phb.marchands_pour(baton, "Village") == ["forge"]
    bolas = phb.article("Bolas")                # exotique → rng ≥ 5 (Cité+)
    assert phb.marchands_pour(bolas, "Cité") == ["forge"]
    assert phb.marchands_pour(bolas, "Grande ville") == []
    harnois = phb.article("Harnois complet")    # armure lourde → rng ≥ 4
    assert phb.marchands_pour(harnois, "Grande ville")
    assert phb.marchands_pour(harnois, "Bourg") == []
    # Sorts de service : limités par le type de localité.
    assert phb.sorts_service("Ville", 2) is not None
    assert phb.sorts_service("Ville", 3) is None
    assert phb.sorts_service("Bourg", 2) is None
    assert phb.sorts_service("Cité", 5) is not None


# --------------------------------------------------------------------------- #
#  Achat / vente (integration tools)
# --------------------------------------------------------------------------- #
def test_achat_revente_et_gating():
    d = _fresh_dir()
    _ecrire_fiche(d, dict(MARCHAND))
    c = _ctx(d)

    # Refus : arme martiale absente d'un village.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Épée longue", "ville": "Phandalin"}))
    assert r.text.startswith("❌") and "PAS vendu" in r.text

    # Refus : exotique interdite même en métropole SANS assez d'or ? Non —
    # d'abord le gating par rang : bolas vendue en métropole.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_stock"], c,
        {"ville": "Waterdeep", "marchand": "forge", "categorie": "arme"}))
    assert "Épée longue" in r.text and "Bolas" in r.text
    r = asyncio.run(invoke_tool(
        TOOLS["marche_stock"], c,
        {"ville": "Phandalin", "marchand": "forge", "categorie": "arme"}))
    assert "Épée longue" not in r.text and "Bolas" not in r.text

    # Achat réussi en métropole : 150 pc ×0,95 → 143 pc.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Épée longue", "ville": "Waterdeep"}))
    assert r.text.startswith("✅")
    assert "143 pc" in r.text
    f = _lire_fiche(d, "Hugo")
    assert f["or"] == 5000 - 143
    assert any(e["nom"] == "Épée longue" and e["qte"] == 1
               for e in f.get("inventaire", []))

    # Achèter un second exemplaire → fusion de quantité.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Épée longue", "ville": "Waterdeep"}))
    assert r.text.startswith("✅")
    f = _lire_fiche(d, "Hugo")
    epee = next(e for e in f.get("inventaire", [])
                if e["nom"] == "Épée longue")
    assert epee["qte"] == 2

    # Refus : pas assez d'or à 1 pc l'unité.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Bolas",
         "quantite": 100000, "ville": "Waterdeep"}))
    assert r.text.startswith("❌")

    # Refus de vente : aucun marchand n'achète une arme au village.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_vendre"], c,
        {"nom": "Hugo", "article": "Épée longue", "ville": "Phandalin"}))
    assert r.text.startswith("❌") and "achète" in r.text

    # Vente en métropole : 75 pc l'unité.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_vendre"], c,
        {"nom": "Hugo", "article": "Épée longue", "quantite": 2,
         "ville": "Waterdeep"}))
    assert r.text.startswith("💰")
    f = _lire_fiche(d, "Hugo")
    assert f["or"] == 5000 - 143 * 2 + 75 * 2
    assert not any(e["nom"] == "Épée longue" for e in f.get("inventaire", []))

    # Refus de vente : objet possédé nulle part.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_vendre"], c,
        {"nom": "Hugo", "article": "Bolas", "ville": "Waterdeep"}))
    assert r.text.startswith("❌") and "ne possède pas" in r.text


def test_sort_de_service_dynamique():
    d = _fresh_dir()
    fiche = dict(MARCHAND)
    fiche["or"] = 1000
    _ecrire_fiche(d, fiche)
    c = _ctx(d)

    r = asyncio.run(invoke_tool(
        TOOLS["marche_stock"], c,
        {"ville": "Everlund", "categorie": "service"}))  # Ville (sorts niv 2)
    assert "Sort de service (niveau 1)" in r.text
    assert "Sort de service (niveau 2)" in r.text
    assert "Sort de service (niveau 3)" not in r.text

    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Sort de service (niveau 2)",
         "ville": "Everlund"}))
    assert r.text.startswith("✅")
    assert "210 pc" in r.text                 # 20 po×10 pc = 200 → ×1,05 Ville = 210
    f = _lire_fiche(d, "Hugo")
    assert f["or"] == 1000 - 210              # service : aucun objet ajouté
    assert not any(e["nom"].startswith("Sort de service")
                   for e in f.get("inventaire", []))

    # Hors de portée : niveau 3 refusé dans une simple ville.
    r = asyncio.run(invoke_tool(
        TOOLS["marche_acheter"], c,
        {"nom": "Hugo", "article": "Sort de service (niveau 3)",
         "ville": "Everlund"}))
    assert r.text.startswith("❌")


# --------------------------------------------------------------------------- #
#  Auberge & repos (règles-maison)
# --------------------------------------------------------------------------- #
def test_auberge_commandes():
    d = _fresh_dir()
    fiche = dict(MARCHAND)
    fiche["conditions"] = ["Affamé"]
    fiche["or"] = 1000
    _ecrire_fiche(d, fiche)
    c = _ctx(d)

    # Repas « bonne » indisponible dans un village (mediocre/convenable).
    r = asyncio.run(invoke_tool(
        TOOLS["auberge_commander"], c,
        {"nom": "Hugo", "repas": "bonne", "ville": "Phandalin"}))
    assert r.text.startswith("❌") and "indisponible" in r.text

    # Nuitée médiocre à un village → pas de marqueur (repos normal).
    r = asyncio.run(invoke_tool(
        TOOLS["auberge_commander"], c,
        {"nom": "Hugo", "logement": "mediocre", "ville": "Phandalin"}))
    assert r.text.startswith("🏨")
    f = _lire_fiche(d, "Hugo")
    assert f["or"] == 1000 - 2                   # 2 pa → 2 pc/nuit
    assert "auberge" not in f

    # Métropole : repas + logement « bonne » (5 pc repas + 20 pc logement).
    c2 = _ctx(d)
    r = asyncio.run(invoke_tool(
        TOOLS["auberge_commander"], c2,
        {"nom": "Hugo", "repas": "bonne", "logement": "bonne",
         "ville": "Waterdeep"}))
    assert r.text.startswith("🏨")
    f = _lire_fiche(d, "Hugo")
    assert f["or"] == 1000 - 2 - 25
    conds = [str(x).lower() for x in f.get("conditions", [])]
    assert "affamé" not in conds and "rassasié" in conds
    assert f["auberge"]["logement"] == "bonne"


def test_repos_long_avec_bonus_auberge():
    d = _fresh_dir()
    fiche = dict(MARCHAND)                       # Guerrier niv.2, 2/12 PV
    fiche["conditions"] = ["Fatigue"]
    fiche["or"] = 1000
    _ecrire_fiche(d, fiche)
    c = _ctx(d)

    asyncio.run(invoke_tool(
        TOOLS["auberge_commander"], c,
        {"nom": "Hugo", "logement": "bonne", "ville": "Waterdeep"}))

    r = asyncio.run(invoke_tool(
        TOOLS["repos_long"], c, {"nom_personnage": "Hugo"}))
    assert "auberge" in r.text
    f = _lire_fiche(d, "Hugo")
    assert f["pv"] == 5                          # 2 + niveau 2 + ceil(2/2) = 5
    assert "auberge" not in f                    # marqueur consommé
    assert "Fatigue" not in f.get("conditions", [])
    assert repos_bonus_auberge(f) == 0


def test_repos_long_sans_auberge():
    d = _fresh_dir()
    fiche = dict(MARCHAND)
    fiche["conditions"] = ["Fatigue"]
    fiche["or"] = 1000
    _ecrire_fiche(d, fiche)
    c = _ctx(d)

    r = asyncio.run(invoke_tool(
        TOOLS["repos_long"], c,
        {"nom_personnage": "Hugo", "forcer": True}))
    assert "bonus auberge" not in r.text
    f = _lire_fiche(d, "Hugo")
    assert f["pv"] == 4                          # 2 + niveau (2) seulement
    assert "Fatigue" in f.get("conditions", [])  # pas de repos d'auberge → reste


def test_catalogue_marchand_boucle_sur_articles():
    noms = [a["nom"] for a in phb.articles()]
    assert len(noms) == len(set(_n.lower() for _n in noms))
    assert "Épée longue" in noms and "Bolas" in noms and "Shuriken (lot de 5)" in noms
    assert "Sort de service (niveau 1)" not in noms   # dynamique uniquement