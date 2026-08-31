"""Tests — poids PHB 3.5 du catalogue de création + calcul de charge portée.

Couvre :
  - chaque arme / armure / objet du catalogue de création possède un `poids`
    (kg) non négatif, cohérent avec les valeurs officielles PHB 3.5 ;
  - `_calculer_charge_equipement` calcule poids transporté + catégorie
    d'encombrement (Légère/Moyenne/Lourde/Dépassée) depuis le catalogue ;
  - chaque objet connu se voit attribuer son `poids` unitaire.

Usage : py -m pytest tests/test_catalogue_poids.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import catalogue as catalogue_mod  # noqa: E402


def test_toutes_les_armes_ont_un_poids():
    for a in catalogue_mod.ARMES:
        assert "poids" in a, f"Arme sans poids : {a['nom']}"
        assert isinstance(a["poids"], (int, float)), f"Poids invalide : {a['nom']}"
        assert a["poids"] >= 0, f"Poids négatif : {a['nom']}"


def test_toutes_les_armures_ont_un_poids():
    for a in catalogue_mod.ARMURES:
        assert "poids" in a, f"Armure sans poids : {a['nom']}"
        assert isinstance(a["poids"], (int, float))
        assert a["poids"] >= 0


def test_tout_lequipement_a_un_poids():
    for o in catalogue_mod.EQUIPEMENT:
        assert "poids" in o, f"Objet sans poids : {o['nom']}"
        assert isinstance(o["poids"], (int, float))
        assert o["poids"] >= 0


def test_poids_references_phb():
    par_nom = {a["nom"]: a for a in catalogue_mod.ARMES}
    # Poids officiels PHB 3.5 (kg = lb × 0,4536), arrondis à 2 décimales.
    refs = {
        "Bâton": 1.81, "Dague": 0.45, "Javeline": 0.91, "Fronde": 0.0,
        "Épée longue": 1.81, "Épée courte": 0.91, "Espadon": 3.63,
        "Hallebarde": 5.44, "Glaive": 4.54, "Arc court": 0.91, "Arc long": 1.36,
        "Hache à deux mains": 5.44,
    }
    for nom, attendu in refs.items():
        assert abs(par_nom[nom]["poids"] - attendu) < 0.01, f"{nom}: {par_nom[nom]['poids']}"


def test_catalogue_poids_coherent_avec_inventaire():
    """Le poids du catalogue de création doit matcher celui du moteur
    d'inventaire (armes/armures, poids par unité = lot 1)."""
    from server.tools import inventaire as inv_mod  # noqa: E402

    for entrepot in (catalogue_mod.ARMES, catalogue_mod.ARMURES):
        for e in entrepot:
            if e["poids"] <= 0:
                continue  # objets négligeables (fronde…), sans entrée dédiée
            key = inv_mod._norm(e["nom"])
            info = inv_mod._POIDS_OFFICIELS.get(key)
            assert info is not None, f"{e['nom']} ({key}) absent du moteur d'inventaire"
            assert int(info.get("lot") or 1) == 1, \
                f"{e['nom']} doit être un objet unitaire (lot 1)"
            assert abs(e["poids"] - info["poids_kg"]) < 0.01, \
                f"{e['nom']}: catalogue {e['poids']} ≠ moteur {info['poids_kg']}"


def test_calcul_charge_depuis_catalogue():
    from server.main import _calculer_charge_equipement  # noqa: E402

    equip = [
        {"nom": "Épée longue", "qte": 1},
        {"nom": "Arc long", "qte": 1},
        {"nom": "Sac à dos", "qte": 1},
        {"nom": "Armure rembourrée", "qte": 1},
    ]
    r = _calculer_charge_equipement(equip, charge_max=90, or_pc=0)
    # 1,81 + 1,36 + 0,91 + 4,54 = 8,62 kg
    assert abs(r["poids_transporte"] - 8.62) < 0.01, r
    assert r["charge_max"] == 90
    assert r["etat_encumbrance"] == "Legere"
    # Chaque objet connu s'est vu attribuer son poids.
    assert all("poids" in e for e in equip)


def test_calcul_charge_depassee():
    from server.main import _calculer_charge_equipement  # noqa: E402

    equip = [{"nom": "Harnois complet", "qte": 1}]  # 22,68 kg > 15 kg max
    r = _calculer_charge_equipement(equip, charge_max=15, or_pc=0)
    assert r["etat_encumbrance"] == "Depassee", r
    assert abs(r["poids_transporte"] - 22.68) < 0.01


def test_poids_inconnu_non_compte():
    from server.main import _calculer_charge_equipement  # noqa: E402

    equip = [{"nom": "Caillou mystérieux", "qte": 3}]  # hors catalogue
    r = _calculer_charge_equipement(equip, charge_max=90, or_pc=0)
    assert r["poids_transporte"] == 0.0
    assert "poids" not in equip[0]  # poids non attribué si inconnu
