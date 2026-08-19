"""Point d'entrée FastAPI de l'application D&D 3.5 — Maître du Jeu.

Endpoints :
- GET  /                  → frontend statique (chat multijoueur)
- GET  /api/health        → sanity check (ping Ollama)
- GET  /api/parties       → liste les parties
- POST /api/parties       → crée une nouvelle partie (+ état initial)
- GET  /api/parties/{id}  → état persistant d'une partie
- WS   /ws/{partie_id}    → canal chat multijoueur temps réel

Au WS, format de messages reçus :
    {"type": "join", "player": "Alain"}
    {"type": "say", "player": "Alain", "text": "j'ouvre la porte"}
Réponse servant de déclencheur MJ : type=say.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, get_config, set_config
from .game.session import PartySession, registry as sessions
from .game.state import PartyState, SCHEMA_PARTIE
from .llm.client import OllamaClient
from .llm.orchestrator import EventCallback, Orchestrator
from .llm.prompt_builder import PromptBuilder
from .rag.store import RagStore
from .tools.base import ToolContext
from .tools.registry import discover_tools


# Logging : active le logger de l'orchestrateur (dnd35.orchestrator) qui trace
# chaque appel d'outil — indispensable pour diagnostiquer Gemma.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# --------------------------------------------------------------------------- #
#  Lifespan : initialise clients singleton, dispose proprement.
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    # Auto-discovery des @tool
    tools = discover_tools("server.tools")
    app.state.tools = tools
    print(f"[dnd35] {len(tools)} tools chargés : {', '.join(tools.keys())}")

    # Configure le registre des sessions avec le data_dir pour persistence
    # conversationnelle (history survit aux redémarrages serveur).
    sessions.configure(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        max_history_events=cfg.game.max_history_events,
    )

    # Singleton client Ollama
    client = OllamaClient(cfg.llm)
    available = await client.list_models()
    model_names = [m.get("id", "") for m in available]
    if available and cfg.llm.model not in model_names:
        print(
            f"[dnd35] ⚠️ Modèle '{cfg.llm.model}' absent d'Ollama "
            f"(disponibles : {', '.join(model_names)}). "
            f"Pensez à `ollama pull {cfg.llm.model}`."
        )
    else:
        print(f"[dnd35] Backend LLM OK : {cfg.llm.base_url} / {cfg.llm.model}")

    app.state.client = client
    app.state.prompt_builder = PromptBuilder(cfg)

    # Store RAG ChromaDB — désactivé si `rag.enabled: false` dans la config.
    rag_store: Optional[RagStore] = None
    if cfg.rag.enabled:
        try:
            rag_store = RagStore(cfg)
            dims = await rag_store.embedder.name_dims()
            if dims is None:
                print(
                    f"[dnd35] ⚠️ Embedding '{cfg.rag.embedding_model}' inaccessible "
                    f"via Ollama — RAG désactivé. (`ollama pull {cfg.rag.embedding_model}`)"
                )
                await rag_store.embedder.aclose()
                rag_store = None
            else:
                stats = rag_store.stats()
                total = sum(stats.values())
                print(
                    f"[dnd35] RAG OK : embeddings '{dims[0]}' (dim {dims[1]}) — "
                    f"{total} chunk(s) persistés dans {cfg.rag.persist_dir}"
                )
                if total == 0:
                    print("[dnd35] ⚠️ Aucun chunk dans le vector store — lancez "
                          "`py -m server.rag --ingest` pour populiser la base.")
        except Exception as e:                                   # noqa: BLE001
            print(f"[dnd35] ⚠️ Échec d'initialisation du RAG : {e}")
            rag_store = None
    app.state.rag_store = rag_store

    yield

    await client.aclose()
    if rag_store is not None:
        await rag_store.embedder.aclose()
    print("[dnd35] Arrêt propre terminé.")


app = FastAPI(title="D&D 3.5 — Maître du Jeu", lifespan=lifespan)

cfg = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _ctx(partie_id: str, player: str) -> ToolContext:
    return ToolContext(
        partie_id=partie_id,
        joueur=player,
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
    )


def _orchestrator(app: FastAPI) -> Orchestrator:
    return Orchestrator(
        client=app.state.client,
        tools=app.state.tools,
        tool_mode=cfg.llm.tool_mode,
        detect_simulation=cfg.llm.detect_simulation,
        max_iterations=cfg.llm.max_tool_iterations,
    )


# --------------------------------------------------------------------------- #
#  Routes REST
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        models = await app.state.client.list_models()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "ollama": False, "error": str(e)}, status_code=503
        )
    # Section RAG — opaque tant que le store est inactive (embeddings absents
    # ou base vide). Le frontend s'en sert pour afficher le badge RAG dans le bandeau.
    rag_store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    rag_info: Optional[dict[str, Any]] = None
    if rag_store is not None:
        try:
            rag_info = {"enabled": True, "collections": rag_store.stats()}
        except Exception as e:                                   # noqa: BLE001
            rag_info = {"enabled": True, "error": str(e)}
    else:
        rag_info = {"enabled": False, "collections": {}}
    return {
        "ok": True,
        "ollama": True,
        "ollama_base": cfg.llm.base_url,
        "model": cfg.llm.model,
        "model_available": any(m.get("id") == cfg.llm.model for m in models),
        "tools": sorted(app.state.tools.keys()),
        "tool_mode": cfg.llm.tool_mode,
        "rag": rag_info,
    }


@app.get("/api/parties")
async def list_parties() -> dict[str, Any]:
    ids = sessions.all_ids()
    # On inclut aussi les parties persistées sur disque (sans session active).
    data_dir = cfg.abs(cfg.paths.data_dir)
    on_disk = [
        p.stem[len("partie_") :]
        for p in data_dir.glob("partie_*.json")
    ]
    all_ids = list(set(ids + on_disk))
    details: dict[str, dict[str, Any]] = {}
    for pid in all_ids:
        state_obj = PartyState(
            data_dir=str(data_dir), partie_id=pid,
            max_history=cfg.game.max_history_events,
        )
        etat = state_obj.load()
        details[pid] = {
            "titre": etat.get("meta", {}).get("titre", "(sans titre)"),
            "phase": etat.get("phase", "opening"),
            "tour": etat.get("tour", 0),
            "pj": len(etat.get("pj", [])),
        }
    return {
        "active": ids,
        "persisted": list(set(on_disk) - set(ids)),
        "details": details,
    }


@app.post("/api/parties")
async def create_party(payload: dict[str, Any]) -> dict[str, Any]:
    titre = payload.get("titre") or cfg.game.default_title
    cadre = payload.get("cadre") or cfg.game.default_frame
    partie_id = payload.get("partie_id") or uuid.uuid4().hex[:8]
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    etat.setdefault("meta", {}).update({
        "titre": titre,
        "cadre": cadre,
        "regles": "D&D 3.5",
    })
    etat["phase"] = "opening"
    state.save(etat)
    sessions.get(partie_id)  # crée la session en mémoire
    return {"partie_id": partie_id, "titre": titre, "etat": etat}


@app.get("/api/parties/{partie_id}")
async def get_party(partie_id: str) -> dict[str, Any]:
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    if "_erreur" in etat:
        raise HTTPException(status_code=404, detail=etat["_erreur"])
    return {"partie_id": partie_id, "etat": etat}


@app.get("/api/tools")
async def list_tools() -> dict[str, Any]:
    """Introspection des tools (debug / doc frontend)."""
    from .tools.registry import tools_schemas_all
    return {
        "names": sorted(app.state.tools.keys()),
        "schemas": tools_schemas_all(app.state.tools),
    }


# --------------------------------------------------------------------------- #
#  Routes RAG (admin) — ingestion et stats du vector store ChromaDB.
# --------------------------------------------------------------------------- #
@app.get("/api/rag/stats")
async def rag_stats() -> dict[str, Any]:
    store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    if store is None:
        return {"enabled": False, "collections": {}}
    return {"enabled": True, "collections": store.stats()}


@app.post("/api/rag/ingest")
async def rag_ingest(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Déclenche l'ingestion incrémentale du corpus D&D 3.5 (admin).

    `{"force": true}` force la ré-embedding complète. Long-running en première
    exécution (~minutes selon le corpus) — prévoir de l'invoquer hors boucle
    utilisateur.
    """
    store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="RAG désactivé. Passez `rag.enabled: true` dans config.yaml "
                   "et assurez-vous que `ollama pull nomic-embed-text` a été lancé.",
        )
    force = bool((payload or {}).get("force"))
    stats = await store.ingest(force=force)
    return {"ingested": stats["ingested"], "skipped": stats["skipped"], "errors": stats["errors"]}


# --------------------------------------------------------------------------- #
#  WebSocket : canal chat multijoueur
# --------------------------------------------------------------------------- #
@app.websocket("/ws/{partie_id}")
async def ws_chat(ws: WebSocket, partie_id: str) -> None:
    await ws.accept()
    session: PartySession = sessions.get(partie_id)
    session.connections.add(ws)

    # Historique de la partie pour rafraîchir le client qui rejoint.
    # On envoie une forme simplifiée (role + content uniquement) pour que le
    # frontend puisse rejouer le fil de conversation sans réinterpreter les
    # tool_calls internes.
    history_payload = [
        {"role": m.role, "content": m.content}
        for m in session.history
        if m.role in ("user", "assistant") and m.content
    ]
    await ws.send_json({
        "type": "sys",
        "event": "joined",
        "partie_id": partie_id,
        "participants": session.participants,
        "history": history_payload,
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "sys", "event": "error",
                                    "detail": "payload non JSON"})
                continue

            mtype = msg.get("type")
            player = (msg.get("player") or "Joueur").strip()
            session.add_participant(player)

            if mtype == "join":
                session.connections.add(ws)
                await session.broadcast({
                    "type": "sys",
                    "event": "participant_joined",
                    "player": player,
                    "participants": session.participants,
                })
                continue

            if mtype == "say":
                await _handle_say(ws, session, partie_id, player, msg.get("text", ""))
                continue

            await ws.send_json({"type": "sys", "event": "error",
                                "detail": f"type inconnu: {mtype}"})
    except WebSocketDisconnect:
        pass
    finally:
        session.connections.discard(ws)


async def _handle_say(
    initiator: WebSocket,
    session: PartySession,
    partie_id: str,
    player: str,
    text: str,
) -> None:
    """Traite un message de joueur : invoque le MJ (orchestrateur) et broadcast."""
    if not text.strip():
        return

    # 1. Mémorise le message joueur + broadcast immédiat à tous (echo).
    session.remember_player_message(player, text)
    await session.broadcast({
        "type": "player",
        "player": player,
        "text": text,
    })

    # 2. Statut "thinking" aux clients connectés.
    await session.broadcast({"type": "status", "description": "Le MJ réfléchit..."})

    # 3. Construit le message système (system prompt + récap + sections + RAG).
    rag_context = ""
    rag_store: Optional[RagStore] = getattr(app.state, "rag_store", None)
    if rag_store is not None:
        try:
            rag_context = await rag_store.render_for_prompt(text)
        except Exception as e:                                   # noqa: BLE001
            # Le RAG ne doit jamais bloquer une narration ; on log et on continue.
            print(f"[dnd35] RAG requête échouée (ignoré) : {e}")
            rag_context = ""
    system_text, etat = app.state.prompt_builder.build_system_message(
        partie_id, rag_context=rag_context
    )
    # On re-construit la conversation à partir de l'historique (système en tête).
    messages = [__import__("server.llm.client", fromlist=["Message"]).Message(
        role="system", content=system_text
    )] + list(session.history)

    # 4. Boucle d'orchestration : LLM ↔ tools → narration + events + patches.
    ctx = _ctx(partie_id, player)

    async def on_event(ev: dict[str, Any]) -> None:
        await session.broadcast({"type": "tool_event", "event": ev})

    async def on_delta(token: str) -> None:
        # Stream des tokens de narration vers tous les clients connectés.
        if cfg.game.stream_to_clients:
            await session.broadcast({"type": "delta", "text": token})

    orch = _orchestrator(app)
    result = await orch.run(messages, ctx, on_event=on_event, on_delta=on_delta)

    # 5. On ajoute la narration finale à l'historique de la session.
    if result.narration:
        session.remember_assistant(result.narration)

    # 6. Broadcast final (= complet, même en streaming : permet le rendu MD).
    await session.broadcast({
        "type": "dm",
        "text": result.narration,
        "iterations": result.iterations,
        "corrections": result.corrections,
        "simulation_attempted": result.simulation_attempted,
        "tool_events": result.tool_events,
        "state_patches": result.state_patches,
        "tool_calls_trace": result.tool_calls_trace,
    })

    # 7. Patches d'état → re-synchronise l'UI avec l'état persistant final.
    await session.broadcast({"type": "status", "description": "", "done": True})


# --------------------------------------------------------------------------- #
#  Frontend statique (servi à /) + data mount pour images générées (monstres,
#  fiches portraits, cartes SVG donjon).
# --------------------------------------------------------------------------- #
_static = Path(__file__).resolve().parent / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

_data_dir = cfg.abs(cfg.paths.data_dir)
if _data_dir.is_dir():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")


@app.get("/")
async def index() -> FileResponse:
    path = _static / "index.html"
    if not path.is_file():
        return JSONResponse(
            {"detail": "static/index.html manquant — voir static/."},
            status_code=404,
        )
    return FileResponse(str(path))


# Catch-all SPA : toute route non-API/WS/static retourne index.html pour que
# le routeur côté client (e.g. `/partie/abc`) fonctionne en rechargement direct.
# On exclut explicitement les préfixes réservés pour ne pas masquer une vraie
# route FastAPI manquante.
@app.get("/{full_path:path}", response_model=None)
async def spa_fallback(full_path: str) -> FileResponse | JSONResponse:
    # Routes réservées : on ne capte pas (laisse FastAPI 404/405 proprement).
    reserved = ("api", "ws", "data", "static", "docs", "redoc", "openapi.json")
    if full_path.startswith(reserved) or not full_path:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Tente d'abord un fichier statique buildé (assets/index-*.js, favicon).
    candidate = _static / full_path
    if candidate.is_file() and ".." not in full_path:
        return FileResponse(str(candidate))
    # Sinon : index.html pour le client-side routing.
    idx = _static / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return JSONResponse(
        {"detail": "static/index.html manquant — voir static/."},
        status_code=404,
    )
