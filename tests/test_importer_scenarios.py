"""Tests du script d'import des scénarios/cartes (scripts/importer_scenarios.py).

Arborescence synthétique en tmp_path : on vérifie la construction du
catalogue (chapitres chaînés, scénario mono-fichier, cartes, annexe
originale), la PRÉSERVATION des entrées existantes (id/pitch intacts) et
l'exclusion des PDF non catalogués d'un dossier déjà géré.

Usage : py -m pytest tests/test_importer_scenarios.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "importer_scenarios.py",
)
_spec = importlib.util.spec_from_file_location("importer_scenarios", _SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)


# --------------------------------------------------------------------------- #
def _arbre(tmp_path):
    """Construit un arbre scénarios synthétique et renvoie (arbre, ancien)."""
    base = tmp_path / "scenarios"
    camp = base / "Mon Univers" / "Ma Campagne"
    camp.mkdir(parents=True)
    (camp / "ma campagne-01-debut.pdf").write_bytes(b"%PDF-1")
    (camp / "ma campagne-02-fin.pdf").write_bytes(b"%PDF-1")
    (camp / "Cartes").mkdir()
    (camp / "Cartes" / "carte A.png").write_bytes(b"png")
    (base / "Mon Univers" / "Ma Campagne.pdf").write_bytes(b"%PDF-1")
    (base / "Mon Univers" / "Aventure Solo.pdf").write_bytes(b"%PDF-1")
    uni_cartes = base / "Mon Univers" / "Cartes"
    uni_cartes.mkdir()
    (uni_cartes / "carte univers.jpg").write_bytes(b"jpg")
    return imp.scan_scenarios(str(base))


def test_nouveaux_scenarios_chaines_et_annexe(tmp_path):
    """Arbre vierge : chapitres chaînés + mono-fichier + cartes + annexe."""
    base = tmp_path / "scenarios"
    arbre = _arbre(tmp_path)
    rapport: list[str] = []
    cata = imp.construire_catalogue(arbre, {"universes": []}, rapport,
                                    base=str(base))

    assert len(cata["universes"]) == 1
    u = cata["universes"][0]
    assert u["id"] == "mon_univers"
    # Univers : 1 carte d'univers ; scénarios : 2 chapitres + 1 solo.
    assert [c["nom"] for c in u["cartes"]] == ["carte univers"]
    ids = [s["id"] for s in u["scenarios"]]
    # Les candidats (dossier de campagne) passent avant les PDF libres de
    # la racine de l'univers.
    assert ids == [
        "mon_univers_ma_campagne_ch01",
        "mon_univers_ma_campagne_ch02",
        "mon_univers_aventure_solo",
    ]
    ch1, ch2, solo = u["scenarios"]
    # Chaînage automatique.
    assert ch1["campagne"] == "Ma Campagne"
    assert ch1["chapitre"] == 1 and ch1["chapitre_total"] == 2
    assert ch1["chapitre_suivant"] == "mon_univers_ma_campagne_ch02"
    assert "chapitre_suivant" not in ch2  # dernier chapitre
    # Cartes du scénario (sous-dossier Cartes/).
    assert ch1["cartes"] == [{
        "nom": "carte A",
        "fichier": "/data/scenarios/Mon Univers/Ma Campagne/Cartes/carte A.png",
    }]
    # PDF original → annexe du chapitre 1.
    assert ch1["annexes"][0]["fichier"] == \
        "/data/scenarios/Mon Univers/Ma Campagne.pdf"
    # Le PDF original ne doit PAS être ajouté comme scénario.
    assert all(
        s["pdf"] != "/data/scenarios/Mon Univers/Ma Campagne.pdf"
        for s in u["scenarios"]
    )


def test_preservation_des_entrees_existantes(tmp_path):
    """Entrées déjà cataloguées : id/pitch intacts, aucun doublon."""
    arbre = _arbre(tmp_path)
    pdf_ch1 = "/data/scenarios/Mon Univers/Ma Campagne/ma campagne-01-debut.pdf"
    ancien = {"universes": [{
        "id": "mon_univers", "nom": "Mon Univers", "description": "ma desc",
        "cartes": [], "scenarios": [{
            "id": "mon_univers_ma_campagne_ch01",
            "titre": "Titre artisanal", "pitch": "mon pitch",
            "pdf": pdf_ch1,
        }],
    }]}
    rapport: list[str] = []
    cata = imp.construire_catalogue(arbre, ancien, rapport)
    u = cata["universes"][0]
    assert u["description"] == "ma desc"
    ch1 = next(s for s in u["scenarios"]
               if s["id"] == "mon_univers_ma_campagne_ch01")
    assert ch1["pitch"] == "mon pitch"
    assert ch1["titre"] == "Titre artisanal"  # pas écrasé par le généré
    # Le chapitre 1 n'est ajouté qu'une fois.
    assert sum(1 for s in u["scenarios"]
               if s["pdf"] == pdf_ch1) == 1
    assert not any("ajouté : mon_univers_ma_campagne_ch01" in r
                   for r in rapport)


def test_exclusion_dossier_deja_gere(tmp_path):
    """Un PDF non catalogué dans un dossier déjà couvert n'est PAS ajouté."""
    arbre = _arbre(tmp_path)
    pdf_ch1 = "/data/scenarios/Mon Univers/Ma Campagne/ma campagne-01-debut.pdf"
    pdf_ch2 = "/data/scenarios/Mon Univers/Ma Campagne/ma campagne-02-fin.pdf"
    ancien = {"universes": [{
        "id": "mon_univers", "nom": "Mon Univers", "cartes": [],
        "scenarios": [
            {"id": "mon_univers_ma_campagne_ch01", "pdf": pdf_ch1},
            {"id": "mon_univers_ma_campagne_ch02", "pdf": pdf_ch2},
        ],
    }]}
    # Annexe volontaire non cataloguée dans le dossier géré.
    extra = (tmp_path / "scenarios" / "Mon Univers" / "Ma Campagne"
             / "bonus-non-jouable.pdf")
    extra.write_bytes(b"%PDF-1")
    arbre = imp.scan_scenarios(str(tmp_path / "scenarios"))

    rapport: list[str] = []
    cata = imp.construire_catalogue(arbre, ancien, rapport,
                                    base=str(tmp_path / "scenarios"))
    u = cata["universes"][0]
    pdfs = [s["pdf"] for s in u["scenarios"]]
    bonus_url = str(extra).replace(os.sep, "/").replace(
        str(tmp_path / "scenarios"), "/data/scenarios")
    assert bonus_url not in pdfs
    # Les 2 chapitres catalogués restent ; « Aventure Solo.pdf » (nouveau,
    # hors dossier géré) est bien ajouté.
    assert pdfs.count(pdf_ch1) == 1 and pdfs.count(pdf_ch2) == 1
    assert "/data/scenarios/Mon Univers/Aventure Solo.pdf" in pdfs
    assert any("non catalogué (dossier déjà géré)" in r for r in rapport)


def test_importer_cartes_sans_ecraser(tmp_path, monkeypatch):
    """--cartes : copie les images, n'écrase pas sans --force."""
    dest = tmp_path / "cartes"
    monkeypatch.setattr(imp, "CARTES_DIR", str(dest))
    src = tmp_path / "mes cartes"
    src.mkdir()
    (src / "faerun.png").write_bytes(b"nouveau")
    (src / "deja_la.png").write_bytes(b"remplacant")
    dest.mkdir()
    (dest / "deja_la.png").write_bytes(b"ancien")

    lignes = imp.importer_cartes(str(src), force=False)
    assert (dest / "faerun.png").read_bytes() == b"nouveau"
    assert (dest / "deja_la.png").read_bytes() == b"ancien"  # préservé
    assert any("existe déjà" in l for l in lignes)

    imp.importer_cartes(str(src), force=True)
    # --force écrase.
    assert (dest / "deja_la.png").read_bytes() == b"remplacant"


def test_original_libre_inconnu_devient_scenario(tmp_path):
    """Un PDF libre à la racine d'un univers, sans dossier homonyme
    catalogué, est ajouté comme scénario mono-fichier."""
    base = tmp_path / "scenarios"
    (base / "Divers").mkdir(parents=True)
    (base / "Divers" / "Nouvelle Aventure.pdf").write_bytes(b"%PDF-1")
    arbre = imp.scan_scenarios(str(base))

    rapport: list[str] = []
    cata = imp.construire_catalogue(arbre, {"universes": []}, rapport)
    u = cata["universes"][0]
    assert [s["id"] for s in u["scenarios"]] == \
        ["divers_nouvelle_aventure"]
    assert u["scenarios"][0]["pdf"] == \
        "/data/scenarios/Divers/Nouvelle Aventure.pdf"
