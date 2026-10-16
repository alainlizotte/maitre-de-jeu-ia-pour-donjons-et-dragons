# -*- coding: utf-8 -*-
"""Régression : POST /api/persos en erreur 500 (UnboundLocalError).

Le commit d61883d (« avancement : whitelist UI + garde serveur ») a inséré
le garde C6 AVANT la définition de `mode_avancement` (utilisé ligne
`if mode_avancement:` sans exister) ET référencé `_carac_base_c6`, jamais
définie : CHAQUE création/édition de personnage par l'interface
(POST /api/persos) répondait 500 UnboundLocalError. Les suites pytest
passaient car la création testée passait par le TOOL
`fiche_perso_creer_rapide`, pas par cette route (partie 48b239fa, signalé
par l'utilisateur : « je ne suis plus capable de créer des personnages »).

Réparation : le garde est déplacé APRÈS le calcul de `mode_avancement` et
reconstruit la base depuis l'existant (carac final − ajustements raciaux −
+1 des paliers précédents).

Usage : py -m pytest tests/test_correctifs_d61883d_avancement.py -q
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CARAC_PAYLOAD = {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10,
                 "SAG": 10, "CHA": 10}


def _client(tmp_path, monkeypatch):
    """TestClient avec data_dir isolé + compte authentifié fictif."""
    from fastapi.testclient import TestClient

    from server import main as srv

    data_tmp = str(tmp_path / "data")
    os.makedirs(data_tmp, exist_ok=True)
    monkeypatch.setattr(srv, "_dossier_donnees", lambda: data_tmp)
    app = srv.app
    app.dependency_overrides[srv.utilisateur_courant] = lambda: "test_c6"
    client = TestClient(app)
    return client, app, srv, data_tmp


def test_creation_perso_par_ui_ne_plante_plus(tmp_path, monkeypatch):
    """La CRÉATION d'un personnage par l'interface répond 200 — le garde
    C6 ne référence plus `mode_avancement` avant sa définition."""
    client, app, srv, data_tmp = _client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/persos", json={
            "nom": "TesteurC6", "race": "Humain", "classe": "Guerrier",
            "niveau": 1, "or": 50, "carac": dict(CARAC_PAYLOAD),
        })
        assert r.status_code == 200, (
            f"POST /api/persos → {r.status_code} : {r.text[:300]}")
        fiche = json.load(open(
            os.path.join(data_tmp, "fiches", "fiche_testeurc6.json"),
            encoding="utf-8"))
        assert fiche["niveau"] == 1
        # La base du payload est bien prise en compte à la création
        # (Humain : aucun ajustement racial).
        assert int(fiche["carac"]["FOR"]) == 16
        assert fiche["gains_carac"] == []
    finally:
        app.dependency_overrides.pop(srv.utilisateur_courant, None)


def test_avancement_base_reconstruite_depuis_existant(tmp_path, monkeypatch):
    """MODE AVANCEMENT (palier en attente + PJ déjà joué) : la base du
    payload est IGNORÉE et remplacée par la base reconstruite de
    l'existant (carac final − racial − gains précédents) ; le +1 du
    palier courant s'applique via `gain_carac`."""
    client, app, srv, data_tmp = _client(tmp_path, monkeypatch)
    try:
        # Fiche existante : Humain (mods raciaux nuls), palier niveau 2 en
        # attente, un +1 FOR déjà appliqué (FOR final 17 = base 16 + 1).
        fiches = os.path.join(data_tmp, "fiches")
        os.makedirs(fiches, exist_ok=True)
        existante = {
            "nom": "AvancéC6", "joueur": "test_c6",
            "proprietaire": "test_c6",
            "race": "Humain", "classe": "Guerrier", "niveau": 2,
            "xp": 350, "avancement_confirme": 1,
            "gains_carac": ["FOR"],
            "carac": {"FOR": 17, "DEX": 12, "CON": 14, "INT": 10,
                      "SAG": 10, "CHA": 8},
            "pv": 22, "pv_max": 22, "ca": 15,
        }
        open(os.path.join(fiches, "fiche_avancec6.json"), "w",
             encoding="utf-8").write(json.dumps(existante, ensure_ascii=False))

        r = client.post("/api/persos", json={
            "nom": "AvancéC6", "race": "Humain", "classe": "Guerrier",
            "niveau": 2,
            # Base TRICHEUSE du payload (bornée 3-25 mais libre) : elle doit
            # être ignorée au profit de la base reconstruite.
            "carac": {"FOR": 10, "DEX": 10, "CON": 10, "INT": 10,
                      "SAG": 10, "CHA": 10},
            "gain_carac": "CON",
            # Gains obligatoires du passage de niveau (Guerrier : 4 dons).
            "dons": ["Arme de prédilection (épée longue)",
                     "Science de la critique (épée longue)",
                     "Tir de près", "Esquive"],
        })
        assert r.status_code == 200, (
            f"POST /api/persos → {r.status_code} : {r.text[:300]}")
        fiche = json.load(open(
            os.path.join(fiches, "fiche_avancec6.json"), encoding="utf-8"))
        # Base reconstruite : FOR = 17 − 0 racial − 1 gain précédent = 16
        # (le payload FOR 10 a été ignoré).
        assert int(fiche["carac"]["FOR"]) == 16, fiche["carac"]
        # +1 du palier courant appliqué : CON = 14 − 0 − 0 + 1 = 15.
        assert int(fiche["carac"]["CON"]) == 15, fiche["carac"]
        # Histoire des gains : précédent + nouveau (niveau 2 % 4 != 0 → le
        # nouveau n'est PAS historisé, seul le +1 effectif de gain_carac
        # s'applique au calcul).
        assert fiche["gains_carac"] == ["FOR"]
    finally:
        app.dependency_overrides.pop(srv.utilisateur_courant, None)
