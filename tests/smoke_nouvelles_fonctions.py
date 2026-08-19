"""Smoke test des nouvelles fonctionnalités serveur (mot de passe, modèles, fiches).

Exécuté ponctuellement en local — pas dans la suite pytest (il touche data_dir).
Nettoie ses propres artefacts à la fin.

Usage : py tests/smoke_nouvelles_fonctions.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "data")

ok_count = 0


def check(label: str, cond: bool) -> None:
    global ok_count
    if not cond:
        print(f"❌ {label}")
        sys.exit(1)
    ok_count += 1
    print(f"✅ {label}")


def receive_until(ws, event: str, max_iter: int = 10):
    for _ in range(max_iter):
        msg = ws.receive_json()
        if msg.get("event") == event:
            return msg
    raise AssertionError(f"event {event} jamais reçu")


def receive_type(ws, type_: str, max_iter: int = 10):
    """Consomme la file jusqu'à un message du type voulu (les broadcasts
    participant_joined s'intercalent entre les messages attendus)."""
    for _ in range(max_iter):
        msg = ws.receive_json()
        if msg.get("type") == type_:
            return msg
    raise AssertionError(f"type {type_} jamais reçu")


with TestClient(app) as client:
    # --- 1. Création de partie avec mot de passe ------------------------- #
    r = client.post("/api/parties", json={"titre": "Smoke test", "mot_de_passe": "secret123"})
    check("POST /api/parties → 200", r.status_code == 200)
    pid = r.json()["partie_id"]
    check("protegee=True au retour", r.json().get("protegee") is True)

    # --- 2. Listing : drapeau protegee, sans fuite du hash --------------- #
    r = client.get("/api/parties")
    det = r.json()["details"].get(pid, {})
    check("GET /api/parties → protegee=True", det.get("protegee") is True)
    check("hash jamais exposé", "mot_de_passe" not in json.dumps(det))

    # --- 3. Flux WS protégé ----------------------------------------------- #
    with client.websocket_connect(f"/ws/{pid}") as ws:
        msg = ws.receive_json()
        check("WS sans join → auth_required", msg.get("event") == "auth_required")

        ws.send_json({"type": "join", "player": "joueur 1", "password": "faux"})
        msg = ws.receive_json()
        check("join mauvais mdp → auth_failed", msg.get("event") == "auth_failed")

        ws.send_json({"type": "join", "player": "joueur 1", "password": "secret123"})
        joined = receive_until(ws, "joined")
        check("join bon mdp → joined (historique)", "history" in joined)
        check("participant enregistré", "joueur 1" in joined.get("participants", []))

        # Un say authentifié doit passer (le MJ répondra en erreur car pas
        # d'Ollama ici, mais le broadcast player doit partir).
        ws.send_json({"type": "say", "player": "joueur 1", "text": "test"})
        msg = receive_type(ws, "player")
        check("say authentifié → broadcast player", msg.get("player") == "joueur 1")

    # --- 4. Partie SANS mot de passe : accès direct ----------------------- #
    r = client.post("/api/parties", json={"titre": "Ouverte"})
    pid2 = r.json()["partie_id"]
    check("partie ouverte protegee=False", r.json().get("protegee") is False)
    with client.websocket_connect(f"/ws/{pid2}") as ws:
        msg = ws.receive_json()
        check("partie ouverte → joined direct", msg.get("event") == "joined")

    # --- 5. Modèles IA ----------------------------------------------------- #
    r = client.get("/api/models")
    check("GET /api/models → 200 + current", r.status_code == 200 and "current" in r.json())
    r = client.post("/api/model", json={"model": ""})
    check("POST /api/model vide → 400", r.status_code == 400)
    r = client.post("/api/model", json={"model": "smoke-model"})
    check("POST /api/model → ok (backend injoignable = pas de validation)", r.status_code == 200)
    r = client.get("/api/models")
    check("modèle courant basculé", r.json()["current"] == "smoke-model")

    # --- 6. Fiches ---------------------------------------------------------- #
    r = client.get("/api/fiches/Groth")
    check("GET /api/fiches/Groth → 200", r.status_code == 200)
    check("fiche.nom = Groth", r.json()["fiche"].get("nom") == "Groth")
    r = client.get("/api/fiches/Personnage%20Inexistant")
    check("fiche absente → 404", r.status_code == 404)

# --- Nettoyage des artefacts du test ---------------------------------------- #
for f in (
    f"partie_{pid}.json", f"partie_{pid2}.json",
    f"chat_{pid}.json", f"chat_{pid2}.json",
    "model_choice.json",
):
    p = os.path.join(DATA, f)
    if os.path.isfile(p):
        os.unlink(p)

print(f"\n🎉 {ok_count} vérifications passées — nettoyage effectué.")
