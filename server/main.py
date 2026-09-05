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
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from . import catalogue as catalogue_mod
from . import gpu as _gpu
from . import persos as persos_mod
from . import sorts as sorts_mod
from .config import AppConfig, get_config, set_config
from .game.session import PartySession, registry as sessions
from .game.state import PartyState, SCHEMA_PARTIE
from .llm.client import Message
from .llm.client import OllamaClient
from .llm.orchestrator import EventCallback, Orchestrator
from .llm.prompt_builder import PromptBuilder
from .rag.store import RagStore
from .tools.base import ToolContext
from .tools.monstres import image_pour
from .tools.registry import discover_tools
from .game.combat import boucle_auto as _boucle_combat

import re as _re_mod

# Détection d'une invoquation / renfort annoncé par un joueur en combat :
# déclenche le rattrapage 5bis-b si le MJ l'a narré sans tool.
_INVOKE_RE = _re_mod.compile(
    r"\b(invoqu\w*|convoqu\w*|invocation\w*|summon\w*|renforts?)\b",
    _re_mod.IGNORECASE,
)

# Détection d'une action de combat déclarée par le joueur mais non résolue
# par le MJ (aucun jet) → rejeu correctif 5bis-a (comme pour les monstres).
_ACTION_COMBAT_RE = _re_mod.compile(
    r"\b(attaqu\w*|frapp\w*|assén\w*|lanc\w+|incant\w*|tir\w*|soign\w*"
    r"|soins|guér\w*|charge\w*|degat\w*|dégâts?)\b",
    _re_mod.IGNORECASE,
)

# Détection d'un combat narré EN PROSE par le LLM (le petit modèle écrit
# parfois « Le combat commence ! Le zombie charge… » et enchaîne jets/dégâts
# dans la narration SANS appeler `engager_combat`). Le serveur rattrape alors
# la phase officielle pour que l'ordre d'initiative, le suivi des PV et la
# rotation restent conformes (cf. bloc 5ter sous le moteur de combat).
_COMBAT_PROSE_MARKERS = (
    "le combat commence", "le combat éclate", "le combat s'engage",
    "le combat est lancé", "combat engagé", "les hostilités",
    "charge vers vous", "se jette sur vous", "se précipite sur vous",
    "bondit vers vous", "vous attaque", "attaque toi",
    "t'attaque", "vous agresse", "se rue sur vous",
    "prend son tour", "c'est au tour de",
)
# Prose de DÉGÂTS infligés (attaque portée en narration) : un montant de
# dégâts narré hors combat signifie qu'une action hostile a été jouée —
# le combat DOIT être régularisé (initiative + suivi des PV), sinon les
# dégâts narrés restent de la fiction sans effet (observé en partie réelle :
# « **5 points de dégâts** sont infligés à cette créature » sans engager_combat).
_DEGATS_PROSE_RE = _re_mod.compile(
    r"\b\d{1,3}\s*(?:points?\s+de\s+)?d[ée]g[âa]ts\b", _re_mod.IGNORECASE,
)
# Marqueurs de combat déjà CLÔS dans la narration : on n'engage JAMAIS un
# combat rétroactivement si l'issue a déjà été racontée (victoire, défaite,
# fuite, mort…), pour ne pas écraser un combat terminé.
_COMBAT_PROSE_END_MARKERS = (
    "partie est perdue", "partie est gagnée", "combat est terminé",
    "combat terminé", "combat est clos", "tous les héros sont tombés",
    "vous êtes vaincu", "vous avez vaincu", "les monstres sont vaincus",
    "est vaincu", "sont vaincus", "est détruit", "sont détruits",
    "s'effondre à terre", "s'effondrent à terre", "le monstre s'écroule",
    "a été éliminé", "ont été éliminés", "prenez la fuite", "prend la fuite",
    "vous fuyez", "se rendent", "game over", "vous êtes mort",
)

# Marqueurs d'un déplacement / exploration narré EN PROSE par le LLM (le petit
# modèle décrit souvent une progression sans appeler `carte_donjon_*`).
_EXPLO_PROSE_MARKERS = (
    "avancez", "avancent", "avançons", "avance ", "vous traversez",
    "vous pénétrez", "vous entrez", "vous explorez", "vous vous enfoncez",
    "vous empruntez", "vous ouvrez la porte", "poussez la porte",
    "pénètrent", "explorent", "descendez le couloir", "remontez le couloir",
    "nouvelle salle", "la pièce suivante", "au détour du couloir",
    "vous suivez le passage", "vous franchissez",
)
# Marqueurs d'une exploration CLÔSE (retour, sortie, arrêt) : pas de rattrapage.
_EXPLO_PROSE_END_MARKERS = (
    "vous revenez", "vous rebroussez", "vous sortez", "vous repartez",
    "vous vous arrêtez", "la pièce s'arrête", "cul-de-sac",
)

# Outils de la phase d'exploration : dès qu'un de ces outils a été appelé,
# l'exploration a été correctement enregistrée côté serveur.
_EXPLORATION_TOOLS = {
    "carte_donjon_entrer", "carte_donjon_explorer", "carte_donjon_etage",
    "monstre_consulter", "carte_donjon_voir",
}

# Outils qui valident la phase d'ouverture (le chargement du scénario choisi).
_SCENARIO_LOAD_TOOLS = {"scenarios_laelith_charger", "scenarios_laelith_lister"}

# Détection d'une demande explicite de choisir/charger un scénario (tour de
# phase "load"). Sert de garde : on n'auto-charge JAMAIS le scénario pendant
# la création de personnages (aussi en phase "opening"), uniquement quand le
# joueur demande à voir/choisir les missions disponibles.
_SCENARIO_CHOICE_RE = _re_mod.compile(
    r"\b(scénario\w*|scenario\w*|missions?\s+proposees?|missions?\s+disponi\w*"
    r"|scenarios?_laelith|choisir\s+un\s+scénario|charger\s+le\s+scénario"
    r"|dues\s+for\s+the\s+dead|crypts\s+kelemvor|tombe\s+des\s+rois)\b",
    _re_mod.IGNORECASE,
)

# Outils qui persistent un objet dans l'inventaire d'un PJ. Utilisé par
# 5quater-c : si le MJ annonce une acquisition sans appeler l'un d'eux, on le
# ré-invoque pour forcer l'enregistrement.
_INVENTAIRE_TOOLS = {
    "inventaire_ajouter", "inventaire_retirer", "inventaire_consommer_munition",
}

# Détection d'une acquisition/looting d'objet annoncé par le joueur ou le MJ :
# déclenche le rattrapage 5quater-c si l'objet n'a pas été enregistré.
_ITEM_ACQUISITION_RE = _re_mod.compile(
    r"\b(ramass\w*|récup\w*|récupèr\w*|trouv\w*|obtien?t|obtenir|acquis\w*"
    r"|pill\w*|prise au|je prend|il prend|elle prend|gagne\w* un|obtient un"
    r"|ajout\w* à mon inventaire|dans mon inventaire|au trésor|butin|loot\w*"
    r"|donne\w* à|offre\w* à|cède\w* à)\b",
    _re_mod.IGNORECASE,
)

# Détection d'une demande de SOIN / guérison ou de REPOS (hors combat aussi) :
# le petit modèle 9B narre « Je lance les dés et soigne X » ou « vous vous
# reposez et récupérez vos PV » SANS appeler `fiche_perso_soigner` ni
# `repos_long`. On rejoue alors (5quater-d) pour que les PV changent.
_SOIN_RE = _re_mod.compile(
    r"\b(soign\w*|soins|guéri\w*|guéris\w*|guériss\w*|répar\w*|cicatris\w*"
    r"|soins\s+légers|lancer\s+des\s+et\s+soigne|ressusci\w*"
    r"|repos\w*|récupèr\w*|régénér\w*)\b",
    _re_mod.IGNORECASE,
)


# Tools qui CONSOMMENT l'action standard du personnage courant : dès que le
# joueur actif en a appelé un, le moteur serveur avance la rotation (le LLM
# n'a plus à se souvenir de tour_suivant_combat).
_ACTION_CONSOMMEE_TOOLS = {
    "lancer_attaque", "lancer_degats", "lancer_sauvegarde",
    "fiche_perso_infliger_degats", "fiche_perso_soigner",
    "fiche_perso_niveau_negatif", "inventaire_consommer_munition",
    "terminer_mon_tour",
}




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

    # Singleton client Ollama. Le choix de modèle persisté (via /api/model)
    # prime sur config.yaml — il vit dans data_dir (montage writable en Docker,
    # contrairement à config/ qui est monté read-only).
    choice_path = cfg.abs(cfg.paths.data_dir) / "model_choice.json"
    if choice_path.is_file():
        try:
            saved = json.loads(choice_path.read_text(encoding="utf-8"))
            m = (saved.get("model") or "").strip()
            if m:
                cfg.llm.model = m
        except (json.JSONDecodeError, OSError):
            pass
    # Réglages persistés via le GUI (bouton « scènes » de la galerie) —
    # même mécanique que model_choice : prime sur config.yaml au démarrage.
    # Exception : si config.yaml coupe `image.scenes_enabled` (verrou dur),
    # settings.json ne peut pas le réactiver — le fichier de config est
    # autoritaire et l'onglet « Scènes » disparaît de l'interface.
    settings_path = cfg.abs(cfg.paths.data_dir) / "settings.json"
    if not cfg.image.scenes_config:
        cfg.image.scenes_enabled = False
    elif settings_path.is_file():
        try:
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            scenes = (saved.get("images") or {}).get("scenes_enabled")
            if isinstance(scenes, bool):
                cfg.image.scenes_enabled = scenes
        except (json.JSONDecodeError, OSError):
            pass
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
                    f"sur '{cfg.rag.embedding_base_url or cfg.llm.base_url}' — RAG "
                    f"désactivé. (Vérifiez que le serveur d'embeddings tourne.)"
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


def _hash_mot_de_passe(mdp: str) -> str:
    """Hash SHA-256 du mot de passe de partie (jamais stocké en clair)."""
    return hashlib.sha256(mdp.encode("utf-8")).hexdigest()


def _party_password_hash(partie_id: str) -> Optional[str]:
    """Lit le hash du mot de passe dans l'état persistant de la partie."""
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    if "_erreur" in etat:
        return None
    h = etat.get("meta", {}).get("mot_de_passe_sha256")
    return h or None


def _model_choice_path() -> Path:
    return cfg.abs(cfg.paths.data_dir) / "model_choice.json"


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
    backend = cfg.llm.backend  # "ollama" | "llamacpp"
    try:
        models = await app.state.client.list_models()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "backend": backend, "error": str(e)}, status_code=503
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
        "backend": backend,
        "backend_url": cfg.llm.base_url,
        "model": cfg.llm.model,
        "model_available": any(
            cfg.llm.model in m.get("id", "") or m.get("id", "").endswith(cfg.llm.model)
            for m in models
        ),
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
            # Partie protégée par mot de passe (sans révéler le hash).
            "protegee": bool(etat.get("meta", {}).get("mot_de_passe_sha256")),
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
    mot_de_passe = (payload.get("mot_de_passe") or "").strip()
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
    if mot_de_passe:
        etat["meta"]["mot_de_passe_sha256"] = _hash_mot_de_passe(mot_de_passe)
    else:
        etat["meta"].pop("mot_de_passe_sha256", None)
    etat["phase"] = "opening"
    state.save(etat)
    sessions.get(partie_id)  # crée la session en mémoire
    return {
        "partie_id": partie_id,
        "titre": titre,
        "etat": etat,
        "protegee": bool(mot_de_passe),
    }


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


# --------------------------------------------------------------------------- #
#  Calepin du MJ (journal de notes) — persistance dans l'état de la partie.
# --------------------------------------------------------------------------- #
def _party_state(partie_id: str) -> PartyState:
    return PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )


@app.get("/api/parties/{partie_id}/calepin")
async def calepin_lire(partie_id: str) -> dict[str, Any]:
    """Liste les notes du calepin de la partie (id, texte, fait)."""
    return {"partie_id": partie_id, "notes": _party_state(partie_id).calepin_lire()}


@app.post("/api/parties/{partie_id}/calepin")
async def calepin_ajouter(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Ajoute une note au calepin. `texte` requis (str) ; `fait` (bool) optionnel."""
    texte = str(payload.get("texte") or "").strip()
    if not texte:
        raise HTTPException(status_code=400, detail="Champ 'texte' requis.")
    fait = bool(payload.get("fait", False))
    st = _party_state(partie_id)
    err, note_id = st.calepin_ajouter(texte, fait)
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "note_id": note_id, "notes": st.calepin_lire()}


@app.put("/api/parties/{partie_id}/calepin/{note_id}")
async def calepin_maj(
    partie_id: str, note_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Met à jour une note : `texte` (str) et/ou `fait` (bool)."""
    st = _party_state(partie_id)
    texte = payload.get("texte")
    fait = payload.get("fait")
    if texte is not None:
        texte = str(texte).strip()
        if not texte:
            raise HTTPException(status_code=400, detail="Texte vide.")
    if fait is not None:
        fait = bool(fait)
    err = st.calepin_maj(note_id, texte=texte, fait=fait)
    if err:
        raise HTTPException(status_code=404 if err == "Note introuvable" else 500,
                            detail=err)
    return {"ok": True, "notes": st.calepin_lire()}


@app.delete("/api/parties/{partie_id}/calepin/{note_id}")
async def calepin_supprimer(partie_id: str, note_id: str) -> dict[str, Any]:
    st = _party_state(partie_id)
    err = st.calepin_supprimer(note_id)
    if err:
        raise HTTPException(status_code=404 if err == "Note introuvable" else 500,
                            detail=err)
    return {"ok": True, "notes": st.calepin_lire()}


@app.delete("/api/parties/{partie_id}")
async def delete_party(partie_id: str) -> dict[str, Any]:
    """Supprime définitivement une partie : état persistant, historiques de
    chat (MJ + équipe), cartes SVG liées (donjon…) et session en mémoire.
    Les WebSockets encore connectés sont fermés — les joueurs voient la
    partie disparaître."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", partie_id):
        raise HTTPException(status_code=400, detail="Identifiant de partie invalide.")

    data_dir = cfg.abs(cfg.paths.data_dir)

    # Session en mémoire : fermeture propre des WebSockets puis retrait.
    sess = sessions.pop(partie_id)
    if sess is not None:
        for ws in list(sess.connections):
            try:
                await ws.close(code=1001, reason="Partie supprimée")
            except Exception:                                    # noqa: BLE001
                pass
        sess.connections.clear()

    # Fichiers sur disque (best-effort, on liste ce qui est réellement effacé).
    cibles = [
        data_dir / f"partie_{partie_id}.json",
        data_dir / f"chat_{partie_id}.json",
        data_dir / f"team_chat_{partie_id}.json",
        *list((data_dir / "cartes").glob(f"*_{partie_id}.svg")),
    ]
    supprimes = [c.name for c in cibles if c.is_file()]
    for c in cibles:
        try:
            c.unlink(missing_ok=True)
        except OSError as e:
            raise HTTPException(
                status_code=500, detail=f"Impossible de supprimer {c.name} : {e}"
            )

    if not supprimes and sess is None:
        raise HTTPException(status_code=404, detail=f"Partie « {partie_id} » introuvable.")
    return {"ok": True, "partie_id": partie_id, "supprimes": supprimes}


@app.get("/api/parties/{partie_id}/carte-donjon.svg")
async def get_carte_donjon_svg(partie_id: str) -> Response:
    """Carte du donjon rendue À LA VOLÉE depuis l'état live de la partie.

    Le fichier statique `/data/cartes/donjon_<partie>.svg` n'est réécrit que
    lorsqu'un outil tourne — il peut donc être périmé. Cette route re-rend
    le SVG depuis `etat["donjon"]` à chaque requête (style à jour garanti)
    et interdit la mise en cache navigateur.
    """
    from .tools.cartes import _rendre_svg_donjon
    state = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)),
        partie_id=partie_id,
        max_history=cfg.game.max_history_events,
    )
    etat = state.load()
    donjon = (etat or {}).get("donjon") or {}
    if not donjon.get("id"):
        raise HTTPException(status_code=404, detail="Aucun donjon actif")
    return Response(
        content=_rendre_svg_donjon(donjon),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/tools")
async def list_tools() -> dict[str, Any]:
    """Introspection des tools (debug / doc frontend)."""
    from .tools.registry import tools_schemas_all
    return {
        "names": sorted(app.state.tools.keys()),
        "schemas": tools_schemas_all(app.state.tools),
    }


# --------------------------------------------------------------------------- #
#  Modèles IA — sélection à chaud du modèle du MJ (menu déroulant frontend).
# --------------------------------------------------------------------------- #
@app.get("/api/models")
async def list_models() -> dict[str, Any]:
    """Liste les modèles disponibles sur le backend LLM + le modèle courant."""
    try:
        models = await app.state.client.list_models()
    except Exception as e:                                   # noqa: BLE001
        return {"models": [], "current": cfg.llm.model, "error": str(e)}
    return {"models": [m.get("id", "") for m in models], "current": cfg.llm.model}


@app.post("/api/model")
async def set_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Bascule le modèle du MJ à chaud et persiste le choix (data/model_choice.json).

    Le choix persisté prime sur config.yaml au démarrage suivant — la config
    reste montée read-only dans Docker alors que data_dir est writable.
    """
    model = (payload.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Champ 'model' requis.")
    models = await app.state.client.list_models()
    # Si le backend expose une liste non vide, on valide le choix pour éviter
    # les fautes de frappe qui feraient échouer silencieusement tous les tours.
    if models and not any(m.get("id") == model for m in models):
        dispo = ", ".join(m.get("id", "?") for m in models)
        raise HTTPException(
            status_code=404,
            detail=f"Modèle « {model} » introuvable. Disponibles : {dispo}",
        )
    cfg.llm.model = model
    try:
        _model_choice_path().write_text(
            json.dumps({"model": model}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # persistance best-effort ; le changement runtime reste actif
    return {"ok": True, "model": model}


# --------------------------------------------------------------------------- #
#  Réglages d'images — toggle runtime persisté (bouton GUI « scènes »)
# --------------------------------------------------------------------------- #
def _settings_path() -> Path:
    return cfg.abs(cfg.paths.data_dir) / "settings.json"


@app.get("/api/settings/images")
async def image_settings() -> dict[str, Any]:
    """État de la génération d'images : globale + scènes seules.

    `scenes_config_enabled` = verrou dur lu de config.yaml : à false, le front
    retire l'onglet « Scènes » et son bouton (seul l'onglet Monstres reste).
    """
    return {
        "enabled": cfg.image.enabled,
        "scenes_enabled": cfg.image.scenes_enabled,
        "scenes_config_enabled": cfg.image.scenes_config,
    }


@app.post("/api/settings/images/scenes")
async def set_image_scenes(payload: dict[str, Any]) -> dict[str, Any]:
    """Active/désactive à chaud l'illustration des scènes marquantes
    (outil `illustration_scene`). Monstres, portraits et illustrations de
    donjon restent actifs dans tous les cas. Le choix est persisté dans
    data/settings.json et prime sur config.yaml au redémarrage (config/
    est monté read-only en Docker, data_dir est writable).
    """
    if not cfg.image.scenes_config:
        raise HTTPException(
            status_code=403,
            detail="Illustration des scènes verrouillée à off par config.yaml "
                   "(image.scenes_enabled: false) — le toggle GUI est désactivé.",
        )
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Champ 'enabled' (bool) requis.")
    cfg.image.scenes_enabled = enabled
    try:
        data: dict[str, Any] = {}
        if _settings_path().is_file():
            try:
                data = json.loads(_settings_path().read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        images["scenes_enabled"] = enabled
        data["images"] = images
        _settings_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # persistance best-effort ; le changement runtime reste actif
    return {"ok": True, "enabled": cfg.image.enabled, "scenes_enabled": enabled}


# --------------------------------------------------------------------------- #
#  Authentification — comptes locaux + tokens Bearer
# --------------------------------------------------------------------------- #
def _dossier_donnees() -> str:
    return str(cfg.abs(cfg.paths.data_dir))


async def utilisateur_courant(
    authorization: str = Header(default=""),
) -> str:
    """Dépendance FastAPI : renvoie le nom d'utilisateur authentifié ou 401."""
    nom = auth_mod.utilisateur_depuis_header(_dossier_donnees(), authorization)
    if not nom:
        raise HTTPException(status_code=401, detail="Non authentifié.")
    return nom


@app.post("/api/auth/inscription")
async def auth_inscription(payload: dict[str, Any]) -> dict[str, Any]:
    """Crée un compte {nom, mot_de_passe} et renvoie directement un token."""
    nom = (payload.get("nom") or "").strip()
    mdp = payload.get("mot_de_passe") or ""
    ok, message = auth_mod.creer_utilisateur(_dossier_donnees(), nom, mdp)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {
        "token": auth_mod.generer_token(_dossier_donnees(), nom),
        "utilisateur": nom,
    }


@app.post("/api/auth/connexion")
async def auth_connexion(payload: dict[str, Any]) -> dict[str, Any]:
    """Connecte un compte existant → token Bearer."""
    nom = (payload.get("nom") or "").strip()
    mdp = payload.get("mot_de_passe") or ""
    if not auth_mod.verifier_identifiants(_dossier_donnees(), nom, mdp):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    return {
        "token": auth_mod.generer_token(_dossier_donnees(), nom),
        "utilisateur": nom,
    }


@app.get("/api/auth/moi")
async def auth_moi(utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    return {"utilisateur": utilisateur}


# --------------------------------------------------------------------------- #
#  Personnages joueurs — « Mes personnages » du frontend (auth requis).
# --------------------------------------------------------------------------- #
@app.get("/api/persos/modele")
async def persos_modele() -> dict[str, Any]:
    """Catalogues pour le formulaire : races, classes, alignements, dieux."""
    return {
        "races": [
            {"nom": nom, "mods": r["mods"], "taille": r["taille"], "vitesse": r["vitesse"]}
            for nom, r in persos_mod.RACES.items()
        ],
        "classes": [
            {
                "nom": nom,
                "de_vie": c["de_vie"],
                "bab": c["bab"],
                "sauves_bonnes": c["sauves_bonnes"],
            }
            for nom, c in persos_mod.CLASSES.items()
        ],
        "alignements": persos_mod.ALIGNEMENTS,
        "dieux": [
            {
                "nom": d["nom"],
                "titre": d["titre"],
                "alignement": d["alignement"],
                "races": d["races"],
                "classes": d["classes"],
                "mal": d["mal"],
            }
            for d in persos_mod.DIEUX
        ],
        # Catalogues d'équipement + maîtrises (le front grise l'indisponible).
        "proficiences": catalogue_mod.PROFICIENCES,
        "armes": catalogue_mod.ARMES,
        "armures": catalogue_mod.ARMURES,
        "equipement_aventurier": catalogue_mod.EQUIPEMENT,
        "dons": [
            {"nom": d["nom"], "condition": d["condition"], "prereq": d["prereq"]}
            for d in catalogue_mod.DONS
        ],
        "competences": catalogue_mod.COMPETENCES,
        "competences_classe": catalogue_mod.COMPETENCES_CLASSE,
        "points_competence": catalogue_mod.POINTS_COMPETENCE,
        "or_depart": catalogue_mod.OR_DEPART,
        # Magie 3.5 : catalogue des sorts (filtré par classe/niveau côté
        # client), tables d'emplacements par jour + règles de lancement.
        "sorts": [
            {
                "nom": s["nom"], "niveau": s["niveau"], "ecole": s["ecole"],
                "classes": s["classes"], "incantation": s["incantation"],
                "portee": s["portee"], "composantes": s["composantes"],
                "duree": s["duree"], "sauvegarde": s.get("sauvegarde", ""),
                "description": s.get("description", ""),
            }
            for s in sorts_mod.SORTS
        ],
        "sorts_emplacements": sorts_mod._E,
        "sorts_connus_max": sorts_mod.CONNUS,
        "sorts_carac": sorts_mod.CARAC_INCANTATION,
        "sorts_prepare": sorted(sorts_mod.PREPARE),
    }


@app.post("/api/persos/stats-aleatoires")
async def persos_stats_aleatoires(
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Tirage 4d6 (on retire le plus faible) ×6 — méthode classique PHB."""
    return {"carac": persos_mod.tirage_4d6(), "methode": "4d6 garder les 3 meilleurs"}


@app.post("/api/persos/or-depart")
async def persos_or_depart(
    payload: dict[str, Any],
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Or de départ PHB 3.5 pour la classe donnée.

    mode="tirage" (défaut) : lance les dés de la classe.
    mode="moyenne"         : valeur moyenne officielle (ex. guerrier 150 po).
    """
    classe = (payload.get("classe") or "").strip()
    classe_c = persos_mod.resoudre_classe(classe)
    if not classe_c or classe_c not in catalogue_mod.OR_DEPART:
        raise HTTPException(status_code=400, detail=f"Classe inconnue : « {classe} ».")
    mode = payload.get("mode") or "tirage"
    return {
        "or": catalogue_mod.tirer_or_depart(classe_c, mode),
        "formule": catalogue_mod.formule_or_depart(classe_c),
    }


@app.post("/api/persos/apparence-aleatoire")
async def persos_apparence_aleatoire(
    payload: dict[str, Any],
    utilisateur: str = Depends(utilisateur_courant),
) -> dict[str, Any]:
    """Tirage âge/taille/poids selon les tables officielles 3.5 (DRS).

    L'âge dépend de la race ET du groupe de classe ; taille/poids de la race
    et du sexe. Renvoie valeurs brutes + chaînes formatées pour le formulaire.
    """
    return persos_mod.tirer_apparence(
        race=(payload.get("race") or "").strip(),
        classe=(payload.get("classe") or "").strip(),
        sexe=(payload.get("sexe") or "").strip(),
    )


@app.get("/api/persos")
async def persos_liste(utilisateur: str = Depends(utilisateur_courant)) -> list[dict[str, Any]]:
    """Liste les personnages du compte connecté (+ URL de portrait)."""
    data_dir = _dossier_donnees()
    resultats = []
    for fiche in persos_mod.lister_fiches(data_dir, proprietaire=utilisateur):
        resultats.append({
            **fiche,
            "portrait": persos_mod.url_portrait(data_dir, str(fiche.get("nom", "")), utilisateur),
        })
    return resultats


@app.get("/api/persos/{slug}")
async def persos_detail(slug: str, utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Fiche d'un personnage du compte connecté (par slug de nom)."""
    from .tools.fiches import _slug as _slug_fn
    for fiche in persos_mod.lister_fiches(_dossier_donnees(), proprietaire=utilisateur):
        if _slug_fn(str(fiche.get("nom", ""))) == slug:
            return {
                **fiche,
                "portrait": persos_mod.url_portrait(
                    _dossier_donnees(), str(fiche.get("nom", "")), utilisateur
                ),
            }
    raise HTTPException(status_code=404, detail="Personnage introuvable.")


def _normaliser_equipement(brut: Any) -> list[dict[str, Any]]:
    """Accepte une chaîne multiligne ou une liste (chaînes « Nom x2 » / dicts)."""
    if isinstance(brut, str):
        lignes: list[Any] = brut.splitlines()
    elif isinstance(brut, list):
        lignes = brut
    else:
        return []
    resultat: list[dict[str, Any]] = []
    for item in lignes:
        if isinstance(item, dict) and str(item.get("nom", "")).strip():
            try:
                qte = max(1, int(item.get("qte", 1)))
            except (TypeError, ValueError):
                qte = 1
            resultat.append({"nom": str(item["nom"]).strip(), "qte": qte})
            continue
        s = str(item).strip()
        if not s:
            continue
        parts = s.rsplit(None, 1)
        if len(parts) == 2 and parts[1][:1].lower() == "x" and parts[1][1:].isdigit():
            resultat.append({"nom": parts[0].strip(), "qte": max(1, int(parts[1][1:]))})
        else:
            resultat.append({"nom": s, "qte": 1})
    return resultat


def _poids_catalogue(nom: str) -> Optional[float]:
    """Poids (kg) d'une unité d'objets du catalogue de création (PHB 3.5).

    Cherche d'abord dans armes/armures/équipement du catalogue ; se rabat sur
    le catalogue de poids du moteur d'inventaire pour les objets synonymes.
    Renvoie None si l'objet est inconnu (poids non compté, comme l'outil
    d'inventaire le fait).
    """
    try:
        from .tools import inventaire as inventaire_mod  # pylint: disable=import-outside-toplevel
    except Exception:                                     # noqa: BLE001
        inventaire_mod = None

    def _norm(s: str) -> str:
        import unicodedata as _u, re as _re
        s = _u.normalize("NFKD", str(s or "").lower())
        s = "".join(c for c in s if not _u.combining(c))
        s = _re.sub(r"[^a-z0-9]+", " ", s).strip()
        return s

    cible = _norm(nom)
    for entrepot in (*catalogue_mod.ARMES, *catalogue_mod.ARMURES, *catalogue_mod.EQUIPEMENT):
        if isinstance(entrepot, dict) and _norm(str(entrepot.get("nom") or "")) == cible:
            p = entrepot.get("poids")
            if isinstance(p, (int, float)):
                return float(p)
    if inventaire_mod is not None:
        info = inventaire_mod._POIDS_OFFICIELS.get(cible)  # noqa: SLF001
        if info:
            lot = int(info.get("lot") or 1)
            return round(float(info["poids_kg"]) / lot, 4)
    return None


def _calculer_charge_equipement(equipement: list[dict[str, Any]],
                                charge_max: float,
                                or_pc: int = 0) -> dict[str, Any]:
    """Calcule la charge portée depuis le catalogue (armes/armures/équipement).

    Renvoie `{poids_transporte, etat_encumbrance, charge_max}`. Chaque objet
    récupère son `poids` (kg/unité) s'il est connu du catalogue, auquel cas
    son poids est compté ; sinon `poids` reste absent et l'objet n'est pas
    compté (cohérent avec le moteur d'inventaire).
    """
    total = 0.0
    for e in equipement:
        if not isinstance(e, dict) or not e.get("nom"):
            continue
        qte = int(e.get("qte", 1) or 1)
        pu = _poids_catalogue(str(e["nom"]))
        if pu is None:
            continue
        e["poids"] = pu
        total += pu * qte
    # Monnaie : 50 pièces = 1 lb (PHB 3.5).
    if or_pc:
        total += or_pc / 50.0 * 0.4536
    total = round(total, 2)
    max_kg = max(1, int(charge_max or 0))
    tiers = max_kg / 3.0
    if total <= tiers:
        cat = "Legere"
    elif total <= 2 * tiers:
        cat = "Moyenne"
    elif total <= max_kg:
        cat = "Lourde"
    else:
        cat = "Depassee"
    return {"poids_transporte": total, "etat_encumbrance": cat, "charge_max": max_kg}


@app.post("/api/persos")
async def persos_sauver(payload: dict[str, Any], utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Crée ou met à jour un personnage du compte connecté.

    Les valeurs dérivées (PV, CA, BBA, sauvegardes…) sont TOUJOURS recalculées
    côté serveur d'après race/classe/niveau/caractéristiques. Le portrait est
    régénéré en arrière-plan à chaque enregistrement.
    """
    data_dir = _dossier_donnees()
    nom = (payload.get("nom") or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="Le nom du personnage est requis.")

    # Collision inter-comptes : le fichier est global (fiche_<slug>.json).
    existante = persos_mod.charger_fiche(data_dir, nom)
    if existante and str(existante.get("proprietaire", "")) != utilisateur:
        raise HTTPException(
            status_code=409,
            detail=f"Un personnage nommé « {nom} » existe déjà (autre compte). "
                   "Choisissez un autre nom.",
        )

    carac_saisi = payload.get("carac") or {}
    carac: dict[str, int] = {}
    for c in persos_mod.CARACS:
        try:
            carac[c] = int(carac_saisi.get(c, 10))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Caractéristique {c} invalide.")

    race = (payload.get("race") or "").strip()
    classe = (payload.get("classe") or "").strip()
    niveau = max(1, int(payload.get("niveau") or 1))
    equipement = _normaliser_equipement(payload.get("equipement"))
    # Armures/boucliers portés (présents au catalogue) → comptés dans la CA
    # (10 + armure + bouclier + Dex plafonnée par l'armure, règles PHB 3.5).
    noms_armures_catalogue = {a["nom"] for a in catalogue_mod.ARMURES}
    armures_portees = [e["nom"] for e in equipement if e["nom"] in noms_armures_catalogue]
    calculs = persos_mod.calculer_derivees(carac, race, classe, niveau, armures=armures_portees)

    # Dieu : s'il correspond à une divinité du panthéon, elle doit accepter le
    # personnage comme serviteur. Un nom libre (ancienne fiche…) est conservé.
    dieu = (payload.get("dieu") or "").strip()
    if dieu:
        connu = any(
            persos_mod._normaliser(d["nom"]) == persos_mod._normaliser(dieu)
            for d in persos_mod.DIEUX
        )
        if connu:
            eligibles = persos_mod.dieux_disponibles(
                race, classe, payload.get("alignement") or ""
            )
            if not any(
                persos_mod._normaliser(d["nom"]) == persos_mod._normaliser(dieu)
                for d in eligibles
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"« {dieu} » n'accepte pas ce personnage comme serviteur "
                           "(race / classe / alignement incompatibles).",
                )

    apparence_in = payload.get("apparence") or {}
    dons = payload.get("dons") or []
    if isinstance(dons, str):
        dons = [ligne.strip() for ligne in dons.splitlines() if ligne.strip()]
    # Budget de dons (règles 3.5) : 1 au niveau 1 puis 1 supplémentaire aux
    # niveaux 3, 6, 9… ; les humains gagnent +1 don.
    max_dons = 1 + max(0, niveau // 3) + (
        1 if (persos_mod.resoudre_race(race) or race) == "Humain" else 0
    )
    if len(dons) > max_dons:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de dons ({len(dons)}) : maximum {max_dons} au niveau "
                   f"{niveau} (bonus humain inclus le cas échéant).",
        )
    # Bonus de PV des dons (ex. « Dur à cuire » = +3 PV) appliqué ici, comme
    # dans fiche_perso_creer / fiche_perso_creer_rapide. Sans cela, pv/pv_max
    # omettaient l'effet des dons lors de la création via le formulaire.
    from .tools.fiches import _bonus_dons_pv
    bonus_pv = _bonus_dons_pv(dons, niveau)
    pv = int(calculs["pv"]) + bonus_pv
    pv_max = int(calculs["pv_max"]) + bonus_pv

    competences = payload.get("competences") or {}
    # Budget de points de compétence (même formule que le client) :
    # par_niveau = max(1, base_classe + mod_INT) ; total = par_niveau × (niveau+3)
    # (+niveau si humain). Les rangs saisis ne peuvent pas dépasser ce budget.
    if isinstance(competences, dict) and competences:
        base_pts = catalogue_mod.POINTS_COMPETENCE.get(
            persos_mod.resoudre_classe(classe) or classe, 0
        )
        mod_int = (int(calculs["carac_final"]["INT"]) - 10) // 2
        budget_comp = max(1, base_pts + mod_int) * (3 + niveau)
        if (persos_mod.resoudre_race(race) or race) == "Humain":
            budget_comp += niveau
        try:
            rangs_total = sum(max(0, int(v or 0)) for v in competences.values())
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="Rangs de compétence invalides."
            )
        if rangs_total > budget_comp:
            raise HTTPException(
                status_code=400,
                detail=f"Trop de rangs de compétence ({rangs_total}) : maximum "
                       f"{budget_comp} au niveau {niveau}.",
            )

    # --------------------------- Sorts (magie 3.5) ---------------------------
    # Validation stricte : chaque sort doit exister, appartenir à la liste de
    # CLASSE et être castable à ce niveau. Sorcier/Barde : budget de sorts
    # connus (table PHB). Magicien : grimoire de départ (tous les tours de
    # magicien + 3+mod INT sorts de niveau 1). Clerc/Druide/Paladin/Rodeur :
    # liste complète de classe (préparation quotidienne en jeu).
    classe_canon = persos_mod.resoudre_classe(classe) or classe
    sorts_payload = payload.get("sorts") or {}
    sorts_connus = sorts_payload.get("connus") or []
    sorts_prepares = sorts_payload.get("prepares") or {}
    if not isinstance(sorts_connus, list) or not isinstance(sorts_prepares, dict):
        raise HTTPException(status_code=400, detail="Champ sorts invalide.")
    if sorts_connus or sorts_prepares:
        if not sorts_mod.est_lanceur(classe_canon):
            raise HTTPException(
                status_code=400,
                detail=f"La classe {classe_canon} ne lance pas de sorts.",
            )
        nls = sorts_mod.niveau_sort_max(classe_canon, niveau)
        for s in sorts_connus + list(sorts_prepares.keys()):
            sp = sorts_mod.sort_par_nom(str(s))
            if sp is None:
                raise HTTPException(status_code=400, detail=f"Sort inconnu : « {s} ».")
            if classe_canon not in sp["classes"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"« {sp['nom']} » n'appartient pas à la liste de "
                           f"sorts de {classe_canon}.",
                )
            if sp["niveau"] > nls:
                raise HTTPException(
                    status_code=400,
                    detail=f"« {sp['nom']} » (niv. {sp['niveau']}) est trop "
                           f"puissant pour {classe_canon} niv.{niveau} (max : "
                           f"niveau de sort {nls}).",
                )
        if classe_canon == "Magicien" and niveau == 1:
            # Grimoire de départ PHB : 3 + mod INT sorts de niveau 1.
            mod_int = (int(calculs["carac_final"]["INT"]) - 10) // 2
            budget_liv1 = 3 + mod_int
            niv1 = [
                s for s in sorts_connus
                if (sorts_mod.sort_par_nom(str(s)) or {}).get("niveau") == 1
            ]
            if len(niv1) > budget_liv1:
                raise HTTPException(
                    status_code=400,
                    detail=f"Grimoire de départ : maximum {budget_liv1} sorts de "
                           f"niveau 1 (3 + mod INT {mod_int:+d}) — {len(niv1)} saisis.",
                )
        if classe_canon in sorts_mod.SPONTANE:
            exces = sorts_mod.depassement_connus(classe_canon, niveau, sorts_connus)
            if exces:
                det = ", ".join(f"niv.{l}: {n} de trop" for l, n in sorted(exces.items()))
                raise HTTPException(
                    status_code=400,
                    detail=f"Trop de sorts connus ({classe_canon} niv.{niveau}) : {det}.",
                )
    sorts_fiche = {
        "connus": [str(s) for s in sorts_connus],
        "prepares": {str(k): max(1, int(v or 1)) for k, v in sorts_prepares.items()},
        "depenses": {},
    }

    fiche = {
        "nom": nom,
        "joueur": utilisateur,
        "proprietaire": utilisateur,
        "race": persos_mod.resoudre_race(race) or race,
        "classe": persos_mod.resoudre_classe(classe) or classe,
        "niveau": niveau,
        "xp": 0,
        "carac": calculs["carac_final"],
        "pv": pv,
        "pv_max": pv_max,
        "ca": calculs["ca"],
        "sauvegardes": calculs["sauvegardes"],
        "bab": calculs["bab"],
        "initiative": calculs["initiative"],
        "charge_max": calculs["charge_max"],
        "competences": competences,
        "sorts": sorts_fiche,
        "dons": dons,
        "equipement": equipement,
        "or": int(payload.get("or") or 0),
        "alignement": payload.get("alignement") or "",
        "dieu": dieu,
        "histoire": payload.get("histoire") or "",
        "conditions": [],
        "apparence": {
            "sexe": apparence_in.get("sexe") or "",
            "age": apparence_in.get("age") or "",
            "taille_physique": apparence_in.get("taille") or "",
            "poids": apparence_in.get("poids") or "",
            "yeux": apparence_in.get("yeux") or "",
            "cheveux": apparence_in.get("cheveux") or "",
            "peau": apparence_in.get("peau") or "",
            "description": apparence_in.get("description") or "",
        },
    }
    # Charge transportée (kg) et catégorie d'encombrement D&D 3.5, calculées
    # depuis le catalogue de poids PHB 3.5.
    fiche.update(_calculer_charge_equipement(equipement, fiche["charge_max"], fiche["or"]))

    chemin = persos_mod.chemin_fiche(data_dir, nom)
    try:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(fiche, f, ensure_ascii=False, indent=2)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Écriture impossible : {e}")

    # Portrait généré d'après la fiche enregistrée + traits de la race.
    persos_mod.lancer_portrait_background(data_dir, fiche)

    return {"ok": True, "fiche": fiche, "calculs": calculs}


@app.delete("/api/persos/{slug}")
async def persos_supprimer(slug: str, utilisateur: str = Depends(utilisateur_courant)) -> dict[str, Any]:
    """Supprime un personnage du compte connecté (+ portraits en cache)."""
    from .tools.fiches import _slug as _slug_fn
    data_dir = _dossier_donnees()
    cible = None
    for fiche in persos_mod.lister_fiches(data_dir, proprietaire=utilisateur):
        if _slug_fn(str(fiche.get("nom", ""))) == slug:
            cible = fiche
            break
    if cible is None:
        raise HTTPException(status_code=404, detail="Personnage introuvable.")
    try:
        os.remove(persos_mod.chemin_fiche(data_dir, str(cible.get("nom"))))
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Portraits best-effort (perso_<user>_<slug> + slug nu si présent).
    cache = os.path.join(data_dir, "portraits_cache")
    for base in (
        f"perso_{_slug_fn(utilisateur)}_{slug}",
        slug,
    ):
        for ext in (".png", ".svg"):
            p = os.path.join(cache, base + ext)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Fiches personnages — consultation par le frontend (modal fiche PJ).
# --------------------------------------------------------------------------- #
@app.get("/api/fiches/{nom}")
async def get_fiche(nom: str, partie_id: Optional[str] = None) -> dict[str, Any]:
    """Renvoie la fiche persistante d'un personnage (data/fiches/)."""
    from .tools.fiches import _slug
    fiches_dir = cfg.abs(cfg.paths.data_dir) / "fiches"
    path = fiches_dir / f"fiche_{_slug(nom)}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Aucune fiche pour « {nom} ».")
    try:
        fiche = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Fiche illisible : {e}")
    portrait = None
    portraits_dir = cfg.abs(cfg.paths.data_dir) / "portraits_cache"
    # Cherche d'abord le portrait party_id-specific, puis le fallback generic.
    # Extensions : .png en priorité, puis .svg (placeholder).
    base_slugs = [f"{_slug(nom)}"]
    if partie_id:
        base_slugs.insert(0, f"{partie_id}_{_slug(nom)}")
    for base in base_slugs:
        for ext in (".png", ".svg"):
            candidate = f"{base}{ext}"
            png = portraits_dir / candidate
            if png.is_file():
                portrait = f"/data/portraits_cache/{candidate}"
                break
        if portrait:
            break
    return {"fiche": fiche, "portrait": portrait}


# --------------------------------------------------------------------------- #
#  Ressources — liens permanents (manuels, cartes, scénarios) pour le bandeau
#  de ressources affiché en permanence sur l'écran de jeu.
# --------------------------------------------------------------------------- #
@app.get("/api/ressources")
async def ressources(partie_id: Optional[str] = None) -> dict[str, Any]:
    """Liste les ressources consultables : manuels, cartes de référence
    (Faerûn, nord de Faerûn, Outreterre, Toril), scénarios PDF locaux
    (+ carte du donjon de la partie si `partie_id`). Les manuels pointent
    vers le serveur du projet quand les fichiers sont présents sous
    data/manuels/ (repli externe sinon)."""
    from .tools import scenarios as S
    from .tools.manuels import (
        CARTES_REFERENCE,
        FICHIERS_DEFAUT,
        url_manuel,
    )
    from urllib.parse import quote

    data_dir = cfg.abs(cfg.paths.data_dir)
    ctx = ToolContext(
        partie_id=partie_id or "_",
        joueur="",
        data_dir=str(data_dir),
    )

    manuels = [
        {
            "titre": f["titre"],
            "description": f["description"],
            "categorie": f.get("categorie", "Autre"),
            "url": url_manuel(ctx, f["public_name"]),
        }
        for f in FICHIERS_DEFAUT
    ]

    # Cartes de référence — fichiers PNG du dossier projet `cartes/`, copiés
    # au démarrage vers data/cartes/ et servis sous /data/cartes/…
    cartes = [
        {"titre": _titre, "libelle": _libelle, "url": f"/data/cartes/{quote(_f)}"}
        for _f, _titre, _libelle in CARTES_REFERENCE
        if (data_dir / "cartes" / _f).is_file()
    ]
    # Atlas externes — cartes interactives hébergées par AideDD
    # (liens internet, nouvel onglet ; complètent les PNG hors-ligne ci-dessus).
    cartes.append(
        {
            "titre": "Faerûn — Atlas interactif (AideDD, internet)",
            "libelle": "Atlas AideDD",
            "url": "https://www.aidedd.org/atlas/fr/faerun",
        }
    )
    cartes.append(
        {
            "titre": "Laelith — Atlas interactif (AideDD, internet)",
            "libelle": "Atlas Laelith",
            "url": "https://www.aidedd.org/atlas/fr/laelith",
        }
    )

    # Cartes d'univers — scénarios par univers (cartes communes)
    cata = S.charger_catalogue(ctx)
    for u in cata.get("universes", []):
        for c in u.get("cartes", []):
            cartes.append({
                "titre": f"{u.get('nom', '')} — {c.get('nom', 'Carte')}",
                "libelle": f"🗺️ {c.get('nom', 'Carte')}",
                "url": c.get("fichier", ""),
            })

    # Scénarios PDF (ancien format plat pour la RessourcesBar)
    scenarios = []
    for u in cata.get("universes", []):
        for s in u.get("scenarios", []):
            if s.get("pdf"):
                scenarios.append({
                    "id": s.get("id", ""),
                    "titre": s.get("titre", "?"),
                    "niveau": s.get("niveau", "?"),
                    "url": s["pdf"],
                })

    donjon: Optional[str] = None
    if partie_id:
        svg = data_dir / "cartes" / f"donjon_{partie_id}.svg"
        if svg.is_file():
            donjon = f"/data/cartes/{quote(svg.name)}"

    return {
        "manuels": manuels,
        "cartes": cartes,
        "scenarios": scenarios,
        "donjon": donjon,
    }


# --------------------------------------------------------------------------- #
#  Scénarios — catalogue structuré par univers pour le sélecteur de quête.
# --------------------------------------------------------------------------- #
@app.get("/api/scenarios")
async def list_scenarios(partie_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Retourne la liste des univers, chacun contenant ses scénarios.
    Le frontend affiche d'abord la sélection univers, puis les scénarios
    de l'univers choisi."""
    from .tools.scenarios import charger_catalogue
    data_dir = cfg.abs(cfg.paths.data_dir)
    ctx = ToolContext(
        partie_id=partie_id or "_",
        joueur="",
        data_dir=str(data_dir),
    )
    cata = charger_catalogue(ctx)
    return cata.get("universes", [])


@app.post("/api/parties/{partie_id}/quest")
async def set_quest(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Définit la quête courante d'une partie (sélecteur de quête au démarrage)."""
    titre = payload.get("titre", "")
    pitch = payload.get("pitch", "")
    source = payload.get("source", "")
    # Aventure libre (aucun scénario) : marquer la quête comme choisie pour
    # que le sélecteur de scénario ne réapparaisse pas.
    if not str(titre).strip() and not str(source).strip():
        source = "libre"
    state = PartyState(data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id)
    etat = state.load()
    if "_erreur" in etat:
        raise HTTPException(status_code=404, detail="Partie introuvable.")
    etat["quete"] = {"titre": titre, "pitch": pitch, "source": source}
    # Bible du scénario : construite ici (picker) comme le ferait
    # `scenarios_laelith_charger` — trame + ennemis du module réinjectés au
    # MJ à chaque tour (fidélité au scénario, même sans recharge du module).
    # Best-effort : sans PDF lisible, la bible reste absente.
    _sid = (
        source.split("]", 1)[0].lstrip("[").strip()
        if source.startswith("[") else ""
    )
    if _sid:
        try:
            from .tools.base import ToolContext as _TC
            from .tools.scenarios import (
                _charger_catalogue_plat as _ccp,
                _construire_bible as _cb,
                _ennemis_du_texte as _edt,
                extraire_pdf as _epdf,
            )
            _ctx_q = _TC(
                partie_id=partie_id, joueur="",
                data_dir=str(cfg.abs(cfg.paths.data_dir)),
            )
            _s = next(
                (x for x in _ccp(_ctx_q) if str(x.get("id", "")) == _sid),
                None,
            )
            if _s is not None:
                _txt = _epdf(_ctx_q, _s["pdf"]) if _s.get("pdf") else ""
                _bible = _cb(
                    _s, _txt,
                    str(etat.get("meta", {}).get("regles") or "D&D 3.5"),
                )
                _bible["ennemis"] = _edt(_ctx_q, _txt)
                etat["quete"]["bible"] = _bible
        except Exception as e:                                   # noqa: BLE001
            print(f"[dnd35] Bible scénario non construite (picker) : {e}")
    etat["phase"] = "exploration"
    err = state.save(etat)
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "quete": etat["quete"]}


# --------------------------------------------------------------------------- #
#  ⚔️ Combat server-driven — routes REST (tests E2E réels, clients riches).
#  Toute la mécanique (initiative, rotation, monstres, clôture, XP) est
#  résolue par le moteur serveur SANS LLM ; le LLM ne fait que narrer.
# --------------------------------------------------------------------------- #
@app.post("/api/parties/{partie_id}/combat/engager")
async def combat_engager(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Engage un combat (validation stricte du bestiaire + initiative
    officielle) puis exécute immédiatement la boucle serveur (tours de
    monstres, skips) jusqu'à un PJ actif ou la fin du combat."""
    monstres = str(payload.get("monstres") or "").strip()
    if not monstres:
        raise HTTPException(status_code=400, detail="monstres requis.")
    joueur = str(payload.get("joueur") or "")
    ctx = _ctx(partie_id, joueur)
    from .tools.base import _TOOL_REGISTRY, invoke_tool
    spec = _TOOL_REGISTRY.get("engager_combat")
    if spec is None:
        raise HTTPException(status_code=500, detail="tool engager_combat absent.")
    tr = await invoke_tool(spec, ctx, {"monstres": monstres})
    res_boucle = await _boucle_combat(
        ctx, timeout_secondes=cfg.game.combat_turn_timeout_seconds
    )
    etat = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    return {
        "ok": not tr.text.startswith("⛔") and not tr.text.startswith("❌"),
        "text": tr.text,
        "events": res_boucle.events,
        "patches": res_boucle.patches,
        "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "combat_termine": res_boucle.combat_termine,
    }


@app.post("/api/parties/{partie_id}/combat/boucle")
async def combat_boucle(partie_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Exécute une passe du moteur de combat serveur : joue les tours de
    monstres, passe les incapables, clôture si victoire/défaite (avec XP
    officielle + mémoire). `{"force": true}` termine le tour du PJ courant."""
    payload = payload or {}
    joueur = str(payload.get("joueur") or "")
    ctx = _ctx(partie_id, joueur)
    etat_avant = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    if etat_avant.get("phase") != "combat":
        return {"ok": False, "detail": "Aucun combat en cours.", "events": []}
    res = await _boucle_combat(
        ctx,
        force_avance=bool(payload.get("force")),
        timeout_secondes=cfg.game.combat_turn_timeout_seconds,
    )
    etat = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    return {
        "ok": True,
        "events": res.events,
        "patches": res.patches,
        "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "tour": etat.get("tour"),
        "combat_termine": res.combat_termine,
    }


@app.post("/api/parties/{partie_id}/combat/action")
async def combat_action(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Résout DÉTERMINISTEMENT l'attaque du personnage courant contre un
    monstre ennemi (jet officiel, dégâts de l'arme, application des PV),
    puis avance la rotation via le moteur serveur. Aucun LLM impliqué.

    Body: {"attaquant": "Brunhild", "cible": "Gobelin",
           "arme": "Hache de guerre", "nb_des": 1, "faces": 8, "bonus": 3}
    (arme/dés optionnels : arme improvisée 1d6, bonus = BBA + mod. FOR de la
    fiche si absents)."""
    from .tools.base import _TOOL_REGISTRY, invoke_tool

    etat_avant = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    if etat_avant.get("phase") != "combat":
        raise HTTPException(status_code=400, detail="Aucun combat en cours.")
    attaquant = str(payload.get("attaquant") or etat_avant.get("courant_tour_pour") or "")
    cible = str(payload.get("cible") or "").strip()
    if not attaquant or not cible:
        raise HTTPException(status_code=400, detail="attaquant et cible requis.")
    joueur = str(payload.get("joueur") or attaquant)
    ctx = _ctx(partie_id, joueur)

    arme = str(payload.get("arme") or "arme improvisée")
    nb_des = int(payload.get("nb_des") or 1)
    faces = int(payload.get("faces") or 6)
    bonus_degats = int(payload.get("bonus") or 0)

    # Bonus d'attaque depuis la fiche (BBA + mod FOR/DEX) si non fourni.
    bonus_attaque = payload.get("bonus_attaque")
    if bonus_attaque is None:
        bonus_attaque = 0
        try:
            from .tools.fiches import _load_fiche
            fiche = _load_fiche(ctx, attaquant)
            if fiche:
                caracs = fiche.get("carac") or {}
                arme_l = arme.lower()
                a_distance = any(
                    m in arme_l for m in
                    ("arc", "arbalète", "arbalet", "fronde", "javelot", "dard")
                )
                cle = "DEX" if a_distance else "FOR"
                mod = (int(caracs.get(cle, 10) or 10) - 10) // 2
                bonus_attaque = int(fiche.get("bab") or 0) + mod
        except Exception:                                        # noqa: BLE001
            bonus_attaque = 0

    # CA de la cible depuis l'état de combat.
    ca = 10
    for mo in etat_avant.get("monstres_combat") or []:
        if str(mo.get("nom") or "").lower() == cible.lower():
            try:
                ca = int(mo.get("ca") or 10)
            except (TypeError, ValueError):
                ca = 10
            break

    events: list[str] = []
    async def _run(name: str, args: dict[str, Any]):
        spec = _TOOL_REGISTRY.get(name)
        if spec is None:
            return None
        tr = await invoke_tool(spec, ctx, args)
        events.append(tr.text)
        return tr

    tr_atk = await _run("lancer_attaque", {
        "nom_attaquant": attaquant, "arme": arme,
        "bonus_attaque": int(bonus_attaque), "nom_cible": cible,
        "ca_cible": ca,
    })
    touche = tr_atk is not None and (
        "✅ **Touché**" in tr_atk.text or "⭐ **20 naturel**" in tr_atk.text
    )
    if touche:
        tr_dm = await _run("lancer_degats", {
            "nb_des": nb_des, "faces": faces, "bonus": bonus_degats,
            "arme_ou_sort": arme, "cible": cible,
        })
        m_total = _re_mod.search(r"[Dd]égâts infligés\s*:\s*(\d+)", tr_dm.text)
        if m_total:
            await _run("fiche_perso_infliger_degats", {
                "nom": cible, "degats": int(m_total.group(1)),
            })

    res = await _boucle_combat(ctx, force_avance=True)
    events.extend(res.events)
    etat = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    return {
        "ok": True, "touche": touche, "events": events,
        "patches": res.patches, "phase": etat.get("phase"),
        "courant": etat.get("courant_tour_pour"),
        "combat_termine": res.combat_termine,
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
                   "et vérifiez que le conteneur llamaembed est actif "
                   "(http://localhost:8081).",
        )
    force = bool((payload or {}).get("force"))
    stats = await store.ingest(force=force)
    return {"ingested": stats["ingested"], "skipped": stats["skipped"], "errors": stats["errors"]}


# --------------------------------------------------------------------------- #
#  WebSocket : canal chat multijoueur
# --------------------------------------------------------------------------- #
async def _send_joined(ws: WebSocket, session: PartySession, partie_id: str) -> None:
    """Envoie le payload « joined » (historique + participants) à un client.

    Envoyé uniquement aux clients authentifiés — pour une partie protégée par
    mot de passe, l'historique ne doit pas fuiter avant la vérification.
    """
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
        "team_history": session.team_history[-100:],
    })


@app.websocket("/ws/{partie_id}")
async def ws_chat(ws: WebSocket, partie_id: str) -> None:
    await ws.accept()
    session: PartySession = sessions.get(partie_id)
    pw_hash = _party_password_hash(partie_id)

    if pw_hash is None:
        # Partie ouverte : accès immédiat à l'historique + broadcasts.
        session.connections.add(ws)
        await _send_joined(ws, session, partie_id)
    else:
        # Partie protégée : on exige le mot de passe via un message "join"
        # avant de révéler quoi que ce soit.
        await ws.send_json({
            "type": "sys",
            "event": "auth_required",
            "partie_id": partie_id,
            "detail": "Cette partie est protégée par un mot de passe.",
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

            if mtype == "join":
                # Un personnage sélectionné est OBLIGATOIRE pour rejoindre :
                # pas de participant « fantôme » sans fiche rattachée.
                personnage = (msg.get("personnage") or "").strip()
                if not personnage:
                    await ws.send_json({
                        "type": "sys",
                        "event": "join_refused",
                        "detail": (
                            "Sélectionnez un personnage sur la page d'accueil "
                            "avant de rejoindre la partie."
                        ),
                    })
                    continue
                if pw_hash is not None:
                    mdp = msg.get("password") or ""
                    if _hash_mot_de_passe(mdp) != pw_hash:
                        await ws.send_json({
                            "type": "sys",
                            "event": "auth_failed",
                            "detail": "Mot de passe incorrect.",
                        })
                        continue
                    session.authenticated.add(ws)
                    session.connections.add(ws)
                # Rattachement du personnage choisi (menu déroulant côté
                # client) → le PJ rejoint l'état de partie (liste pj).
                fiche_enregistree = persos_mod.enregistrer_personnage_partie(
                    _dossier_donnees(), partie_id, personnage, player
                )
                if fiche_enregistree is None:
                    # Fiche inexistante ou n'appartenant pas au joueur : on
                    # refuse AVANT d'inscrire le participant.
                    await ws.send_json({
                        "type": "sys",
                        "event": "join_refused",
                        "detail": (
                            f"Personnage « {personnage} » introuvable ou non "
                            "rattache a votre compte. Choisissez-en un autre."
                        ),
                    })
                    continue
                session.add_participant(player)
                # Pour une partie protégée, l'historique n'arrive qu'ici.
                if pw_hash is not None:
                    await _send_joined(ws, session, partie_id)
                await session.broadcast({
                    "type": "sys",
                    "event": "participant_joined",
                    "player": player,
                    "participants": session.participants,
                    "personnage": (
                        fiche_enregistree.get("nom") if fiche_enregistree else None
                    ),
                })
                continue

            if mtype == "say":
                if pw_hash is not None and ws not in session.authenticated:
                    await ws.send_json({
                        "type": "sys",
                        "event": "auth_required",
                        "detail": "Partie protégée : rejoignez avec le mot de passe.",
                    })
                    continue
                await _handle_say(ws, session, partie_id, player, msg.get("text", ""))
                continue

            if mtype == "team_say":
                if pw_hash is not None and ws not in session.authenticated:
                    await ws.send_json({
                        "type": "sys",
                        "event": "auth_required",
                        "detail": "Partie protégée.",
                    })
                    continue
                team_text = msg.get("text", "").strip()
                if not team_text:
                    continue
                session.remember_team_message(player, team_text)
                # Renvoie à tous les joueurs connectés (y compris l'auteur).
                await session.broadcast({
                    "type": "team_msg",
                    "player": player,
                    "text": team_text,
                })
                continue

            if mtype == "audio_signal":
                # Relay WebRTC : signal ICE/offer/answer vers tous les AUTRES joueurs.
                signal_payload = msg.get("signal", {})
                for conn in session.connections:
                    if conn is not ws:
                        try:
                            await conn.send_json({
                                "type": "audio_signal",
                                "player": player,
                                "signal": signal_payload,
                            })
                        except Exception:
                            pass
                continue

            await ws.send_json({"type": "sys", "event": "error",
                                "detail": f"type inconnu: {mtype}"})
    except WebSocketDisconnect:
        pass
    finally:
        session.connections.discard(ws)
        session.authenticated.discard(ws)


# Compteur global de tours MJ actifs (toutes parties confondues) : géré par
# server/gpu.py qui ARBITRE aussi le GPU — un tour LLM attend la fin des
# générations ComfyUI en cours (et réciproquement), pour ne jamais charger
# llama.cpp ET ComfyUI en même temps sur la même carte graphique.
# Tâches d'arrière-plan (illustrations de monstres après le dm final) : la
# référence est gardée pour éviter le garbage-collect prématuré.
_bg_tasks: set[asyncio.Task] = set()
# Unload différé (llm.unload_after_turn = false) : tâche en attente, annulée si
# un nouveau tour démarre avant l'expiration du délai.
_pending_unload: Optional[asyncio.Task] = None
# Verrou autour du déchargement : un tour qui démarre pendant l'unload
# (≈1 s) l'attend au lieu de perdre le modèle en cours de route.
_unload_guard: asyncio.Lock = asyncio.Lock()


def _cancel_pending_unload() -> None:
    """Annule un unload différé en attente (un tour reprend la main)."""
    global _pending_unload
    if _pending_unload is not None and not _pending_unload.done():
        _pending_unload.cancel()
    _pending_unload = None


async def _turn_begin() -> None:
    _cancel_pending_unload()
    async with _unload_guard:
        await _gpu.turn_begin()


async def _turn_end() -> bool:
    """Décrémente le compteur de tours ; True s'il ne reste aucun tour actif."""
    return await _gpu.turn_end()


async def _delayed_unload_task(app: FastAPI, delay_s: float) -> None:
    """Décharge le modèle après `delay_s` secondes d'inactivité.

    Le garde `_unload_guard` est conservé pendant l'appel réseau d'unload :
    un tour qui démarre pendant l'unload attend sa fin (≈1 s) au lieu de
    perdre le modèle en cours de route. La tâche est annulée par
    `_cancel_pending_unload` si un tour reprend avant l'expiration du délai.
    """
    global _pending_unload
    try:
        await asyncio.sleep(delay_s)
        async with _unload_guard:
            if _gpu.turns_actifs() > 0:
                return  # un tour a repris — il reprogrammera l'unload
            _pending_unload = None
            await app.state.client.unload_model()
    except asyncio.CancelledError:
        pass
    except Exception:                                               # noqa: BLE001
        pass


def _extrait_arme_bonus(attaques: str) -> Optional[tuple[str, int]]:
    """Parse la première attaque du bestiaire : « Cimeterre +2 (corps à
    corps) ; arc court +3 ». Renvoie (arme, bonus) ou None si injouable.
    (Conservé pour compatibilité — le moteur de combat serveur
    `game.combat` possède sa propre implémentation généralisée.)"""
    m = _re_mod.match(r"(.+?)\s*([+-]\d+)\s*(?:\(|$)", (attaques or "").strip())
    if not m:
        return None
    arme = m.group(1).strip()
    return (arme, int(m.group(2))) if arme else None


async def _appliquer_degats_oublies(
    orch: Orchestrator,
    result: Any,
    ctx: ToolContext,
    on_event: Optional[Any],
) -> str:
    """Rattrapage mécanique : tout `lancer_degats` réussi dont les dégâts
    n'ont PAS été appliqués ensuite (`fiche_perso_infliger_degats` absent de
    la trace du tour) est appliqué ici par le serveur. Les dégâts jetés ne
    doivent jamais rester sans effet sur la cible.

    Anti double-application (file par cible, dans l'ordre de la trace) :
    - un `infliger(D)` consomme le plus ancien jet orphelin de même cible et
      de même total (cas nominal, touche par touche) ;
    - sinon, si la SOMME des jets orphelins de la cible vaut D, il les
      consomme tous (le LLM a appliqué les touches en un seul appel) ;
    - les jets restés orphelins en fin de tour sont appliqués par le serveur.

    Renvoie le texte mécanique à ajouter à la narration ("" si rien à faire).
    """
    import unicodedata as _uni

    def _norm_nom(s: str) -> str:
        n = _uni.normalize("NFKD", str(s or "").strip().lower())
        return "".join(c for c in n if not _uni.combining(c))

    trace = result.tool_calls_trace
    # File des jets orphelins : cible normalisée → liste de totaux.
    orphelins: dict[str, list[int]] = {}

    for tc in trace:
        nom_tc = tc.get("name")
        if nom_tc == "lancer_degats" and tc.get("ok"):
            cible = str((tc.get("args") or {}).get("cible") or "").strip()
            m_total = _re_mod.search(
                r"[Dd]égâts infligés\s*:\s*(\d+)", tc.get("text") or "")
            if cible and m_total:
                orphelins.setdefault(_norm_nom(cible), []).append(
                    int(m_total.group(1)))
        elif nom_tc == "fiche_perso_infliger_degats" and tc.get("ok"):
            args_j = tc.get("args") or {}
            try:
                d = int(args_j.get("degats") or 0)
            except (TypeError, ValueError):
                continue
            cle = _norm_nom(args_j.get("nom"))
            file_c = orphelins.get(cle) or []
            # 1) montant exact → consomme le plus ancien jet correspondant.
            if d in file_c:
                file_c.pop(file_c.index(d))
            # 2) somme des jets restants → le LLM a appliqué plusieurs
            #    touches en un seul appel.
            elif file_c and sum(file_c) == d:
                file_c.clear()
            if not file_c and cle in orphelins:
                del orphelins[cle]

    # Jets restés orphelins → le serveur les applique (toujours indiqués).
    lignes: list[str] = []
    for cible_norm, totaux in orphelins.items():
        for total in totaux:
            tr = await orch.execute_tool_direct(
                "fiche_perso_infliger_degats",
                {"nom": cible_norm, "degats": total},
                ctx, on_event, result,
            )
            if tr is not None and not tr.text.startswith("❌"):
                lignes.append(tr.text)

    # 2ᵉ filet (rattrapage « touché sans dégâts ») : une attaque RÉUSSIE
    # (lancer_attaque → Touché / 20 naturel) dont la cible n'a reçu AUCUN
    # lancer_degats NI aucune application (`fiche_perso_infliger_degats`).
    # Le LLM narre alors le montant en prose — on récupère le chiffre annoncé
    # près du nom de la cible (fenêtre courte) et on l'applique réellement.
    # S'il est absent ou ambigu, on n'invente rien : les PV restent cohérents.
    degats_jetes: set[str] = set()
    deja_applique: set[str] = set()
    for tc in trace:
        nom_tc = tc.get("name")
        if nom_tc == "lancer_degats" and tc.get("ok"):
            c = str((tc.get("args") or {}).get("cible") or "").strip()
            if c:
                degats_jetes.add(_norm_nom(c))
        elif nom_tc == "fiche_perso_infliger_degats" and tc.get("ok"):
            c = str((tc.get("args") or {}).get("nom") or "").strip()
            if c:
                deja_applique.add(_norm_nom(c))
    re_montant = _re_mod.compile(
        r"\+?\s*(\d{1,3})\s*(?:points?\s+de\s+)?d[ée]g[âa]ts",
        _re_mod.IGNORECASE,
    )
    narration = result.narration or ""
    cibles_traitees: set[str] = set()
    for tc in trace:
        if tc.get("name") != "lancer_attaque" or not tc.get("ok"):
            continue
        texte_tc = tc.get("text") or ""
        if ("✅ **Touché**" not in texte_tc) and ("⭐ **20 naturel**" not in texte_tc):
            continue
        cible = str((tc.get("args") or {}).get("nom_cible") or "").strip()
        cle_cible = _norm_nom(cible)
        if (not cible or cle_cible in degats_jetes
                or cle_cible in deja_applique or cle_cible in cibles_traitees):
            continue
        montants: list[int] = []
        for m_nom in _re_mod.finditer(_re_mod.escape(cible), narration,
                                      _re_mod.IGNORECASE):
            fenetre = narration[max(0, m_nom.start() - 100):m_nom.end() + 100]
            montants.extend(int(x) for x in re_montant.findall(fenetre))
        uniques = sorted(set(montants))
        if len(uniques) != 1:
            continue
        cibles_traitees.add(cle_cible)
        tr = await orch.execute_tool_direct(
            "fiche_perso_infliger_degats",
            {"nom": cible, "degats": uniques[0]},
            ctx, on_event, result,
        )
        if tr is not None and not tr.text.startswith("❌"):
            lignes.append(tr.text)
    return "\n\n".join(lignes)


def _detecter_combat_prose(data_dir: str, text: str, etat_avant: dict[str, Any]) -> list[str]:
    """Repère les monstres du bestiaire mentionnés dans une narration qui
    relate un combat SANS avoir appelé `engager_combat`.

    Le petit modèle narratif écrit parfois « Le combat commence ! Le zombie
    bondit… », puis enchaîne jets et dégâts dans la prose, oubliant d'appeler
    l'outil. Le serveur engage alors la phase officielle pour que l'ordre
    d'initiative, le suivi des PV et la rotation restent conformes.

    Renvoie la liste des noms de type de monstres détectés ([] si aucun).
    Précondition : `etat_avant.phase != "combat"` (sinon rien à rattraper).
    """
    if not text:
        return []
    # Déclencheurs : marqueur explicite de combat OU prose de dégâts (une
    # attaque a été narrée). Sans l'un des deux, on ne déclenche JAMAIS un
    # rattrapage : trop de faux positifs (monstre amical dans une taverne,
    # squelette décoratif dont on parle sans s'y battre…).
    bas = text.lower()
    if not (
        any(m in bas for m in _COMBAT_PROSE_MARKERS)
        or bool(_DEGATS_PROSE_RE.search(bas))
    ):
        return []
    # Si la narration indique déjà que le combat est TERMINÉ (victoire,
    # défaite, fuite, monstre vaincu/tombé…), ne rien rattraper : l'issue a
    # déjà été racontée, un ré-engagement écraserait un combat clos.
    if any(_f in bas for _f in _COMBAT_PROSE_END_MARKERS):
        return []
    try:
        best = _load_bestiaire_plain(data_dir)
    except Exception:
        return []
    trouves: list[str] = []
    # Vocabulaire de la prose (mots ≥ 4 lettres, normalisés sans accent,
    # singulier OU pluriel) pour le rapprochement flou des noms — le petit
    # modèle écrit parfois le nom ANGLAIS du monstre (« Ghoul ») là où le
    # bestiaire porte le nom français (« Goule ») : sans rapprochement,
    # l'attaque narrée restait sans combat et sans suivi de PV.
    import difflib as _difflib
    import unicodedata as _ud

    def _sans_accents(w: str) -> str:
        nf = _ud.normalize("NFKD", w)
        return "".join(c for c in nf if not _ud.combining(c))

    mots_prose: set[str] = set()
    for w in _re_mod.split(r"[^a-z']+", bas):
        w = w.strip("'")
        if len(w) < 4:
            continue
        w2 = _sans_accents(w)
        mots_prose.add(w2)
        if w2.endswith("s"):
            mots_prose.add(w2[:-1])
    for cle, m in (best.get("monstres", {}) or {}).items():
        if not isinstance(m, dict):
            continue
        nom = str(m.get("nom") or cle or "").strip()
        # On ne retient que les monstres explicitement nommés dans la prose
        # (insensible casse, minuscules normalisées). Un « zombie » mentionné
        # en passant compte, mais il faut aussi un marqueur de combat ou une
        # prose de dégâts (vérifiés plus haut) pour déclencher le rattrapage.
        if not nom or len(nom) < 3:
            continue
        nl = _sans_accents(nom.lower())
        if nl in _sans_accents(bas):
            trouves.append(nom)
            continue
        # Rapprochement flou : nom du bestiaire vs mot de la prose (ratio ≥
        # 0.8 → « Ghoul »/« Goule » = 0.8 exactement). Réservé aux noms
        # d'au moins 4 lettres pour limiter les faux positifs.
        if len(nl) >= 4:
            for w in mots_prose:
                if _difflib.SequenceMatcher(None, nl, w).ratio() >= 0.8:
                    trouves.append(nom)
                    break
    # Déduplique par nom (plusieurs clés du bestiaire peuvent pointer vers le
    # même affichage) pour un `engager_combat(nom, nom, …)` propre.
    _dedup = {_t: 1 for _t in trouves}
    return list(_dedup.keys())


def _load_bestiaire_plain(data_dir: str) -> dict[str, Any]:
    """Charge le bestiaire JSON directement (sans ToolContext).

    Le bestiaire source stocke les monstres en clés top-level (hors `_meta`) :
    on les enveloppe ici sous `{"monstres": {...}}`, même convention que
    `tools.monstres._load_bestiaire` — sinon la détection de combat en prose
    (5ter) itérait sur un dict vide et ne détectait JAMAIS rien.
    """
    import json as _json
    from pathlib import Path as _Path
    path = _Path(data_dir) / "bestiaire.json"
    try:
        with open(path, "r", encoding="utf-8") as _f:
            raw = _json.load(_f)
    except Exception:                                            # noqa: BLE001
        return {"monstres": {}}
    monstres: dict[str, Any] = {}
    for _k, _v in raw.items():
        if _k == "_meta":
            continue
        if isinstance(_v, dict) and "nom" in _v:
            monstres[_v.get("cle", _k)] = _v
    return {"monstres": monstres}


def _derive_scenario_id(data_dir: str, narration: str) -> Optional[str]:
    """Déduit l'identifiant du scénario à charger pour une écriture « opening ».

    Le petit modèle 9B raconte souvent l'ouverture en prose au lieu d'appeler
    `scenarios_laelith_charger`. On retrouve le scénario par correspondance du
    titre/ID mentionné dans la narration (best effort) ; sinon on retombe sur
    la mission active déjà mémorisée (le MJ a pu appeler `memoire_mission`).
    """
    import json as _json
    from pathlib import Path as _Path
    # 1. Correspondance titre/id mentionné dans la narration.
    try:
        cata = _json.load(open(_Path(data_dir) / "scenarios_catalogue.json",
                               "r", encoding="utf-8"))
    except Exception:
        cata = None
    if cata:
        base = (narration or "").strip().lower()
        for u in (cata.get("universes", []) or []):
            for s in (u.get("scenarios", []) or []):
                sid = str(s.get("id") or "").strip()
                stitre = str(s.get("titre") or "").strip()
                for frag in (sid, stitre):
                    if frag and len(frag) >= 4 and frag.lower() in base:
                        return sid or None
    # 2. Fallback : mission active déjà mémorisée (titre = titre du scénario).
    return None


def _estnarration_explo(narration: str) -> bool:
    """True si la narration relate un déplacement/exploration en prose."""
    if not narration:
        return False
    bas = narration.lower()
    # Pas de marqueur d'exploration → rien à rattraper (anti-faux-positifs).
    if not any(m in bas for m in _EXPLO_PROSE_MARKERS):
        return False
    # Exploration déjà clôse (retour/sortie) → ne relance rien.
    if any(m in bas for m in _EXPLO_PROSE_END_MARKERS):
        return False
    # Si la narration décrit déjà des combats engagés, on laisse le moteur
    # de combat (5ter) s'en occuper — pas d'exploration à forcer.
    if any(m in bas for m in _COMBAT_PROSE_MARKERS):
        return False
    return True


async def _rejoue_correctif(orch, messages, ctx, result, on_event,
                            consigne: str, tag: str) -> None:
    """Résout une action narrée EN PROSE par le MJ : ré-invoque une fois
    l'orchestrateur avec une consigne ferme et fusionne le résultat dans
    `result` s'il a produit des outils. Génère au plus UN rejeu (le supervise
    est là pour empêcher les boucles, mais on ajoute aussi une garde).
    """
    from .llm.client import Message
    try:
        corrective_messages = list(messages) + [
            Message(role="assistant", content=result.narration or ""),
            Message(role="user", content=consigne),
        ]
        result2 = await orch.run(corrective_messages, ctx, on_event=on_event,
                                 on_delta=None)
        if result2.tool_calls_trace:
            result.tool_calls_trace.extend(result2.tool_calls_trace)
            result.tool_events.extend(result2.tool_events)
            result.state_patches.extend(result2.state_patches)
            result.narration = result2.narration
            result.iterations += result2.iterations
            # (c) La narration finale remplace celle déjà streamée : on
            # demande aux clients d'effacer l'aperçu périmé avant le dm final.
            if on_event is not None:
                try:
                    await on_event({"type": "stream_reset"})
                except Exception:                                # noqa: BLE001
                    pass
            print(f"[dnd35] Rejeu {tag} réussi ({len(result2.tool_calls_trace)} tools)")
        else:
            print(f"[dnd35] Rejeu {tag} sans tool — avancement forcé")
    except Exception as e:                                               # noqa: BLE001
        print(f"[dnd35] Rejeu {tag} failed: {e}")


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

    # 0. Bloquer les messages pendant que le MJ traite (pensée/génération).
    if getattr(session, "thinking", False):
        await session.broadcast({
            "type": "sys",
            "event": "turn_blocked",
            "detail": "⏳ Le MJ est en train de travailler — patientez avant d'envoyer un nouveau message.",
        })
        return

    # 1. Mémorise le message joueur + broadcast immédiat à tous (echo).
    session.remember_player_message(player, text)
    await session.broadcast({
        "type": "player",
        "player": player,
        "text": text,
    })

    # 1bis. ⚔️ Garde de tour : DÉPLACÉE après le pre-run du moteur de combat
    # (voir dans le verrou de session) — la rotation doit d'abord être
    # corrigée par le serveur (skip des incapables, tours de monstres)
    # AVANT de décider qui a le droit de parler.

    # 2. Statut "thinking" aux clients connectés.
    session.thinking = True
    await session.broadcast({
        "type": "status",
        "description": "Le MJ réfléchit...",
        "thinking_blocked": True,
    })

    # 3→6. Tour du MJ — sérialisé par partie (un seul MJ à la fois) et compté
    # globalement (le unload n'a lieu que quand plus aucun tour n'est actif).
    await _turn_begin()
    try:
        async with session.turn_lock:
            # Callbacks définis AVANT le pre-run : le moteur de combat serveur
            # (et les tools qu'il exécute) doit pouvoir pousser ses patches
            # d'état en direct, pas seulement à la fin du tour.
            async def on_event(ev: dict[str, Any]) -> None:
                # (a)/(c) Les events de contrôle ont leur propre canal WS :
                # patches d'état immédiats et reset de l'aperçu streamé.
                etype = ev.get("type")
                if etype == "state_patches":
                    await session.broadcast({
                        "type": "state_patches",
                        "patches": ev.get("patches") or [],
                    })
                elif etype == "stream_reset":
                    await session.broadcast({"type": "stream_reset"})
                else:
                    await session.broadcast({"type": "tool_event", "event": ev})

            async def on_delta(token: str) -> None:
                # Stream des tokens de narration vers tous les clients connectés.
                if cfg.game.stream_to_clients:
                    await session.broadcast({"type": "delta", "text": token})

            async def reset_stream() -> None:
                """(c) Efface l'aperçu streamé chez les clients : à réserver
                aux cas où la narration finale va REMPLACER le texte déjà
                affiché (rejeu correctif), sinon le joueur voit un bloc
                disparaître puis un autre le remplacer sans transition."""
                if cfg.game.stream_to_clients:
                    await session.broadcast({"type": "stream_reset"})

            # 2.pre ⚙️ MOTEUR DE COMBAT SERVEUR (pre-run) : avant toute
            # décision de tour, le serveur fait avancer la mécanique —
            # saute les combattants incapables (mourants…), joue les tours
            # de monstres (attaque officielle du bestiaire), détecte
            # victoire/défaite (clôture + XP officielle + mémoire). Aucun
            # LLM n'intervient ici : c'est déterministe.
            ctx_pre = _ctx(partie_id, player)
            ctx_pre.on_event = on_event
            events_pre: list[str] = []
            patches_pre: list[dict[str, Any]] = []
            try:
                etat_pre = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_pre.get("phase") == "combat":
                    res_pre = await _boucle_combat(
                        ctx_pre,
                        timeout_secondes=cfg.game.combat_turn_timeout_seconds,
                    )
                    events_pre = res_pre.events
                    patches_pre = res_pre.patches
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Pre-run moteur de combat échoué (ignoré) : {e}")

            # 1bis. ⚔️ Application MÉCANIQUE du tour de jeu (D&D 3.5) : en
            # phase de combat, seul le joueur dont c'est le tour peut
            # déclencher le MJ. Les messages des autres joueurs sont
            # diffusés mais n'invoquent PAS le LLM.
            etat_avant = PartyState(
                data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
            ).load()
            actif_avant = str(etat_avant.get("courant_tour_pour") or "")
            if etat_avant.get("phase") == "combat":
                actif = actif_avant
                pj_actif = next(
                    (p for p in (etat_avant.get("pj") or [])
                     if p.get("nom") == actif),
                    None,
                )
                if pj_actif is not None:
                    joueur_actif = str(pj_actif.get("joueur") or "").strip().lower()
                    if joueur_actif and player.strip().lower() != joueur_actif:
                        # Les événements mécaniques du pre-run (monstres
                        # joués, tours passés…) sont montrés à la table même
                        # si le message n'ouvre pas un tour LLM.
                        if events_pre:
                            await session.broadcast({
                                "type": "dm",
                                "text": "⚙️ _Mécanique serveur :_\n\n"
                                        + "\n\n".join(events_pre),
                                "tool_events": [],
                                "state_patches": patches_pre,
                                "tool_calls_trace": [],
                            })
                        await session.broadcast({
                            "type": "sys",
                            "event": "turn_blocked",
                            "detail": (
                                f"⏳ {player} doit attendre : en combat, "
                                f"c'est le tour de {actif} (joué par "
                                f"{pj_actif.get('joueur')}) — round "
                                f"{etat_avant.get('tour', 1)}."
                            ),
                        })
                        return
                # Si l'actif est un PNJ/monstre restant (rare après pre-run),
                # le message passe : la boucle post-tour le gérera.

            # 3. Construit le message système (system prompt + récap + sections + RAG).
            rag_context = ""
            rag_store: Optional[RagStore] = getattr(app.state, "rag_store", None)
            if rag_store is not None:
                try:
                    rag_context = await rag_store.render_for_prompt(text)
                except Exception as e:                               # noqa: BLE001
                    # Le RAG ne doit jamais bloquer une narration ; on log et on continue.
                    print(f"[dnd35] RAG requête échouée (ignoré) : {e}")
                    rag_context = ""
            system_text, etat = app.state.prompt_builder.build_system_message(
                partie_id, rag_context=rag_context
            )

            # 3bis. ⚔️ Bannière de tour : rappel mécanique du combat en
            # cours, injecté dans le message système à chaque invocation du
            # MJ. Le rôle du LLM est STRICTEMENT narratif + résolution de
            # l'action du joueur actif : la rotation, les monstres, la
            # clôture et l'XP sont SERVEUR.
            if etat.get("phase") == "combat":
                actif = str(etat.get("courant_tour_pour") or "?")
                ordre = ", ".join(
                    f"{e.get('nom')} ({e.get('init', '?')})"
                    for e in (etat.get("initiative") or [])
                ) or "?"
                pj_actif = next(
                    (p for p in (etat.get("pj") or []) if p.get("nom") == actif),
                    None,
                )
                if pj_actif is not None:
                    qui = (
                        f"{actif} — JOUEUR {pj_actif.get('joueur')}. Résous "
                        "UNIQUEMENT les actions que CE joueur déclare pour "
                        f"{actif} (attaque, sort, soin…) avec les tools ; "
                        "s'il ne déclare rien d'actif, contente-toi de "
                        "narrer sa position/garde et rappelle-lui qu'il peut "
                        "dire « je termine mon tour »."
                    )
                else:
                    qui = (
                        f"{actif} (MONSTRE/PNJ) — le serveur joue déjà son "
                        "tour automatiquement. N'invente PAS ses actions : "
                        "reprends simplement les événements mécaniques "
                        "listés ci-dessous dans ta narration."
                    )
                system_text += (
                    f"\n\n⚔️ **TOUR EN COURS** — round {etat.get('tour', 1)}. "
                    f"Ordre d'initiative : {ordre}. C'est AU TOUR DE {qui}\n"
                    "⚙️ **GÉRÉ PAR LE SERVEUR (n'y touche PAS)** : rotation "
                    "des tours, attaques des monstres, stabilisation des "
                    "mourants, fin de combat, expérience. N'appelle NI "
                    "tour_suivant_combat NI finir_combat NI engager_combat "
                    "pendant un combat en cours.\n"
                    "🧭 Ton rôle : narrer ce qui vient de se passer "
                    "(notamment les événements mécaniques serveur listés "
                    "ci-dessous) puis, si c'est le tour d'un PJ, résoudre "
                    "son action déclarée — attaque : lancer_attaque puis "
                    "lancer_degats puis fiche_perso_infliger_degats ; soin : "
                    "lancer_des puis fiche_perso_soigner ; sort offensif : "
                    "lancer_degats (+ lancer_sauvegarde si la cible a droit "
                    "à un jet). Économie d'actions D&D 3.5 : max 1 action "
                    "standard + 1 mouvement par round.\n"
                    "✨ Invoquation / renfort en cours de mêlée : "
                    "combat_ajouter_combattant(nom, allie) AVANT de narrer "
                    "l'arrivée — jamais engager_combat."
                )
                if events_pre:
                    system_text += (
                        "\n\n⚙️ **ÉVÉNEMENTS MÉCANIQUES RÉSOLUS PAR LE "
                        "SERVEUR DEPUIS LE DERNIER MESSAGE** (déjà affichés "
                        "aux joueurs — intègre-les à ta narration sans les "
                        "répéter mot à mot, et tire-en les conséquences "
                        "dramatiques) :\n"
                        + "\n\n".join(events_pre)
                    )
            # On re-construit la conversation à partir de l'historique (système
            # en tête), avec un budget en caractères : le message système et
            # les schémas de tools consomment déjà ~6 k tokens, un historique
            # non borné saturerait le contexte sur les longues campagnes.
            budget_hist = cfg.game.max_history_chars
            hist = list(session.history)
            total = 0
            debut = len(hist)
            for i in range(len(hist) - 1, -1, -1):
                total += len(getattr(hist[i], "content", "") or "")
                if total > budget_hist:
                    debut = i + 1
                    break
                debut = i
            if debut < len(hist) - 1:  # garde au moins le dernier message
                print(f"[dnd35] Historique tronqué : {len(hist) - debut} messages "
                      f"anciens omis (budget {budget_hist} chars).")
            messages = [__import__("server.llm.client", fromlist=["Message"]).Message(
                role="system", content=system_text
            )] + hist[debut:]

            # 4. Boucle d'orchestration : LLM ↔ tools → narration + events + patches.
            ctx = _ctx(partie_id, player)
            ctx.on_event = on_event

            # Des dégâts viennent d'être résolus par le moteur serveur (pre-run)
            # ? Le LLM les reformule alors légitimement dans sa narration — on
            # lui fait confiance sur la prose de dégâts pour ce tour.
            trust_damage_prose = any(
                "dégâts" in str(ev).lower() for ev in events_pre
            )

            orch = _orchestrator(app)
            result = await orch.run(
                messages, ctx, on_event=on_event, on_delta=on_delta,
                trust_damage_prose=trust_damage_prose,
            )

            # Statut : la narration est écrite à l'écran — ce qui suit est du
            # post-traitement (vérifications anti-simulation, rejeus, images
            # en arrière-plan). Le libellé change pour que la table sache que
            # le MJ n'« écrit » plus.
            await session.broadcast({
                "type": "status",
                "description": "Le MJ finalise la scène…",
            })

            # 5bis. ⚔️ Post-traitement du tour LLM. Les tours de monstres ne
            # sont PLUS rejoués par le LLM : le moteur serveur (ci-dessous,
            # bloc ⚙️) les joue de façon déterministe. Ne restent ici que
            # les rattrapages liés à l'action DÉCLARÉE par le joueur actif.
            try:
                apres = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if (
                    etat_avant.get("phase") == "combat"
                    and apres.get("phase") == "combat"
                    and str(apres.get("courant_tour_pour") or "")
                    == actif_avant
                    and actif_avant
                ):
                    pj_actif_apres = next(
                        (p for p in (apres.get("pj") or [])
                         if p.get("nom") == actif_avant), None,
                    )
                    est_monstre = pj_actif_apres is None

                    # 5bis-a. ⚔️ Tour de PJ annoncé mais NON résolu : le
                    # joueur a déclaré une action de combat (attaque, sort,
                    # soin…) mais le MJ a narré sans AUCUN jet de dés. On
                    # ré-invoque le MJ une fois avec un correctif — sinon le
                    # tour avance et l'action est perdue (très frustrant).
                    if not est_monstre:
                        pj_a_agi = any(
                            tc.get("name") in (
                                "lancer_attaque", "lancer_degats",
                                "lancer_sauvegarde", "lancer_d20",
                                "lancer_des", "fiche_perso_infliger_degats",
                                "fiche_perso_soigner",
                            )
                            for tc in result.tool_calls_trace
                        )
                        if (
                            not pj_a_agi
                            and _ACTION_COMBAT_RE.search(text or "")
                        ):
                            print(
                                f"[dnd35] Tour PJ {actif_avant} : action "
                                "annoncée sans aucun jet — rejeu correctif"
                            )
                            corrective_pj = (
                                "(Rappel système MJ — ⚠️ ERREUR : le joueur "
                                "a annoncé une action de combat pour "
                                f"**{actif_avant}** mais tu n'as résolu "
                                "AUCUN jet de dés. Résous MAINTENANT cette "
                                "action avec les outils : attaque → "
                                "`lancer_attaque` puis `lancer_degats` puis "
                                "`fiche_perso_infliger_degats` ; sort de "
                                "soins → `lancer_des` puis "
                                "`fiche_perso_soigner` ; sort offensif → "
                                "`lancer_degats` (+ `lancer_sauvegarde` si "
                                "la cible a droit à un jet de sauvegarde). "
                                "NE narrate PAS un résultat sans jet — la "
                                "rotation des tours est automatique.)"
                            )
                            try:
                                corrective_messages = list(messages) + [
                                    Message(role="assistant",
                                            content=result.narration),
                                    Message(role="user",
                                            content=corrective_pj),
                                ]
                                result2 = await orch.run(
                                    corrective_messages, ctx,
                                    on_event=on_event, on_delta=None,
                                )
                                pj_a_agi2 = any(
                                    tc.get("name") in (
                                        "lancer_attaque", "lancer_degats",
                                        "lancer_sauvegarde", "lancer_d20",
                                        "lancer_des",
                                        "fiche_perso_infliger_degats",
                                        "fiche_perso_soigner",
                                    )
                                    for tc in result2.tool_calls_trace
                                )
                                if pj_a_agi2:
                                    result.tool_calls_trace.extend(
                                        result2.tool_calls_trace
                                    )
                                    result.tool_events.extend(
                                        result2.tool_events
                                    )
                                    result.state_patches.extend(
                                        result2.state_patches
                                    )
                                    result.narration = result2.narration
                                    result.iterations += result2.iterations
                                    await reset_stream()
                                    print(
                                        f"[dnd35] Rejeu PJ {actif_avant} "
                                        f"réussi ({len(
                                            result2.tool_calls_trace)} "
                                        f"tools appelés)"
                                    )
                                else:
                                    print(
                                        f"[dnd35] Rejeu PJ {actif_avant} "
                                        "toujours sans jet — avancement forcé"
                                    )
                            except Exception as e:                   # noqa: BLE001
                                print(f"[dnd35] Rejeu PJ failed: {e}")
            except Exception as e:
                print(f"[dnd35] 5bis rejeu failed: {e}")

            # 5bis-b. ⚔️ Rattrapage des invoquations non enregistrées.
            # Le joueur annonce une invoquation/renfort en combat mais le MJ
            # l'a narrée en prose sans appeler combat_ajouter_combattant (le
            # petit modèle 4B le fait souvent). On ré-invoque le MJ avec un
            # correctif ciblé — best effort, comme le rejeu monstre ci-dessus.
            try:
                _invoque_match = _INVOKE_RE.search(text or "")
                _deja_ajoute = any(
                    tc.get("name") == "combat_ajouter_combattant"
                    for tc in result.tool_calls_trace
                )
                if (
                    etat_avant.get("phase") == "combat"
                    and _invoque_match
                    and not _deja_ajoute
                ):
                    print("[dnd35] Invoquation narrée sans "
                          "combat_ajouter_combattant — rejeu avec correctif")
                    corrective_inv = (
                        "(Rappel système MJ — ⚠️ ERREUR : le joueur vient "
                        "d'annoncer une invoquation / un renfort, mais tu "
                        "as narré l'arrivée de la créature SANS "
                        "l'enregistrer mécaniquement. Appelle "
                        "IMMÉDIATEMENT "
                        "`combat_ajouter_combattant(nom=<créature invoquée>, "
                        "allie=true si elle combat pour les joueurs)` : "
                        "l'outil l'insère dans l'ordre d'initiative et suit "
                        "ses PV. N'appelle PAS engager_combat (il "
                        "réinitialiserait le combat en cours). Reprends "
                        "ensuite ta narration en t'appuyant sur le résultat "
                        "de l'outil.)"
                    )
                    corrective_messages = list(messages) + [
                        Message(role="assistant", content=result.narration),
                        Message(role="user", content=corrective_inv),
                    ]
                    result2 = await orch.run(
                        corrective_messages, ctx,
                        on_event=on_event, on_delta=None,
                    )
                    ok2 = any(
                        tc.get("name") == "combat_ajouter_combattant"
                        for tc in result2.tool_calls_trace
                    )
                    if ok2:
                        result.tool_calls_trace.extend(
                            result2.tool_calls_trace
                        )
                        result.tool_events.extend(result2.tool_events)
                        result.state_patches.extend(result2.state_patches)
                        result.narration = result2.narration
                        result.iterations += result2.iterations
                        await reset_stream()
                        print("[dnd35] Rejeu invoquation réussi")
                    else:
                        print("[dnd35] Rejeu invoquation toujours sans "
                              "outil — best effort accepté")
            except Exception as e:
                print(f"[dnd35] 5bis-b rejeu invoquation failed: {e}")

            # 5bis-c. 💥 Rattrapage dégâts non appliqués : tout lancer_degats
            # réussi du tour DOIT se traduire par une application effective
            # sur la cible (fiche PJ ou monstre suivi). Si le modèle a oublié
            # fiche_perso_infliger_degats, le serveur applique les dégâts
            # manquants exactement une fois (appariement anti double-application).
            try:
                txt_rattrapage = await _appliquer_degats_oublies(
                    orch, result, ctx, on_event)
                if txt_rattrapage:
                    result.narration += "\n\n" + txt_rattrapage
                    print("[dnd35] Dégâts non appliqués rattrapés "
                          "automatiquement (tools serveur).")
            except Exception as e:
                print(f"[dnd35] Rattrapage dégâts échoué (ignoré) : {e}")

            # ⚙️ MOTEUR DE COMBAT SERVEUR (post-tour).
            # 1) Les événements mécaniques résolus AVANT le tour LLM
            #    (pre-run : tours de monstres, skips…) sont ajoutés à la
            #    narration finale pour que la table les voie TOUJOURS, même
            #    si le LLM les a mal intégrés.
            # 2) Si le PJ courant a consommé son action standard pendant ce
            #    tour (attaque/soin/jet…), la rotation avance automatiquement ;
            #    le moteur joue les tours suivants (monstres, incapables)
            #    jusqu'au prochain PJ actif et clôture le combat
            #    (victoire/défaite) avec XP officielle + mémoire. Le LLM
            #    n'a PLUS à gérer la rotation : c'est garanti ici.
            try:
                if events_pre:
                    result.narration += (
                        "\n\n⚙️ _Mécanique résolue par le serveur :_\n\n"
                        + "\n\n".join(events_pre)
                    )
                    result.state_patches.extend(patches_pre)

                apres = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if apres.get("phase") == "combat":
                    action_consommee = any(
                        tc.get("name") in _ACTION_CONSOMMEE_TOOLS
                        for tc in result.tool_calls_trace
                    )
                    courant_est_pj = any(
                        str(p.get("nom") or "")
                        == str(apres.get("courant_tour_pour") or "")
                        for p in (apres.get("pj") or [])
                    )
                    # `actif_avant` est vide quand le combat a DÉBUTÉ pendant
                    # ce tour (le joueur attaquait hors combat) : l'action
                    # résolue doit alors aussi faire avancer la rotation,
                    # sinon le joueur restait actif et rejouait au tour
                    # suivant (double action, conformité 3.5 rompue).
                    force = bool(
                        action_consommee
                        and courant_est_pj
                        and (
                            not actif_avant
                            or str(apres.get("courant_tour_pour") or "")
                            == actif_avant
                        )
                    )
                    res_post = await _boucle_combat(
                        ctx,
                        force_avance=force,
                        timeout_secondes=cfg.game.combat_turn_timeout_seconds,
                    )
                    if res_post.events:
                        result.narration += (
                            "\n\n⚙️ _Mécanique du tour (serveur) :_\n\n"
                            + "\n\n".join(res_post.events)
                        )
                    if res_post.patches:
                        result.state_patches.extend(res_post.patches)
                    if res_post.combat_termine:
                        print(
                            "[dnd35] Combat clôturé par le moteur serveur "
                            f"({res_post.combat_termine})."
                        )
                    apres = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Moteur de combat post-tour échoué (ignoré) : {e}")

            # 5quater. 🖼️ Illustrations des monstres en jeu — DÉPORTÉES EN
            # ARRIÈRE-PLAN. La génération ComfyUI (jusqu'à ~100 s par lot)
            # ne doit ni prolonger le statut du tour ni retarder le dm final :
            # la tâche démarre ici, persiste les URLs (image_url + journal des
            # rencontres) et pousse les patches au fil de l'eau ; la narration
            # et les mises à jour de PV partent donc immédiatement.
            async def _illustrer_monstres_arriere_plan() -> None:
                try:
                    _t0 = time.time()
                    _vus: set[str] = set()
                    _nouvelles_rencontres: list[tuple[str, str]] = []
                    urls_par_type: dict[str, str] = {}
                    from .tools.monstres import _type_nom
                    etat_img = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    for mo in etat_img.get("monstres_combat") or []:
                        nom_mo = str((mo or {}).get("nom") or "").strip()
                        # Clé de dédup = NOM DE TYPE (« Gobelin (2) » == « Gobelin ») :
                        # un groupe de monstres identiques partage UNE seule illustration.
                        cle_mo = _type_nom(nom_mo).lower()
                        if not nom_mo or cle_mo in _vus:
                            continue
                        _vus.add(cle_mo)
                        if time.time() - _t0 > 180:
                            # Budget élargi (180 s) : l'arbitrage GPU peut
                            # faire ATTENDRE une image avant soumission (tour
                            # LLM en cours) — ce temps d'attente consomme le
                            # budget mais ne charge pas le PC.
                            print("[dnd35] Budget images monstres atteint — "
                                  "le reste sera généré au tour suivant.")
                            break
                        try:
                            url_img = await image_pour(ctx, nom_mo)
                        except Exception as e:                   # noqa: BLE001
                            print(f"[dnd35] Image {nom_mo} échouée (ignoré) : {e}")
                            continue
                        if url_img:
                            _nouvelles_rencontres.append((_type_nom(nom_mo), url_img))
                            urls_par_type[cle_mo] = url_img
                            # Galerie en direct, sans attendre la persistance.
                            await on_event({
                                "type": "state_patches",
                                "patches": [{"image_monstre": url_img}],
                            })
                    if not urls_par_type:
                        return
                    # Persistance : re-load → patch ciblé → save (la fenêtre de
                    # course avec un tour concurrent est réduite au save).
                    st_img = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    )
                    etat_img = st_img.load()
                    touche = False
                    for mo2 in etat_img.get("monstres_combat") or []:
                        cle2 = _type_nom(str((mo2 or {}).get("nom") or "")).lower()
                        url2 = urls_par_type.get(cle2)
                        # On applique la MÊME image à tous les monstres du même
                        # type (Gobelin, Gobelin (2), Gobelin (3)…).
                        if url2 and (mo2 or {}).get("image_url") != url2:
                            mo2["image_url"] = url2
                            touche = True
                    if _nouvelles_rencontres:
                        from .tools.monstres import _fusionner_rencontres
                        if _fusionner_rencontres(etat_img, _nouvelles_rencontres):
                            touche = True
                    if touche:
                        st_img.save(etat_img)
                except Exception as e:                           # noqa: BLE001
                    print(f"[dnd35] Illustrations arrière-plan échouées "
                          f"(ignoré) : {e}")

            _t_img = asyncio.create_task(_illustrer_monstres_arriere_plan())
            _bg_tasks.add(_t_img)
            _t_img.add_done_callback(_bg_tasks.discard)

            # 5quater-bis. 🖼️ Scènes cousues d'avance par univers pilote
            # (Laelith) : la galerie « Scènes » s'alimente automatiquement
            # quand le groupe change de lieu/un moment marquant est narré.
            # On ne sert QUE des prégénérées (cache, aucun appel ComfyUI) —
            # zéro latence, zéro risque. `memoire.scene_hook_dernier` mémorise
            # le dernier slug servi pour ne pas réafficher la même image à
            # chaque tour.
            try:
                from .tools.cartes import serve_scene_si_pregen
                etat_sc = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_sc.get("phase") != "combat":
                    mem_sc = etat_sc.setdefault("memoire", {})
                    pos_sc = mem_sc.get("position") or {}
                    lieu_sc = str(pos_sc.get("lieu") or "").strip()
                    # Champ de recherche : le lieu courant, sinon l'objectif, sinon le pitch.
                    src_sc = str((etat_sc.get("quete") or {}).get("source") or "")
                    sid_sc = src_sc.split("]", 1)[0].lstrip("[").strip() or ""
                    cible_sc = lieu_sc or str(
                        (etat_sc.get("quete") or {}).get("pitch") or ""
                    ).strip()
                    if lieu_sc and cible_sc:
                        url_sc = serve_scene_si_pregen(
                            ctx, lieu_sc, cible_sc, sid=sid_sc
                        )
                        if url_sc and mem_sc.get("scene_hook_dernier") != url_sc:
                            mem_sc["scene_hook_dernier"] = url_sc
                            PartyState(
                                data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                partie_id=partie_id,
                            ).save(etat_sc)
                            result.state_patches.append({"image_scene": url_sc})
                            cb_sc = getattr(ctx, "on_event", None)
                            if cb_sc is not None:
                                try:
                                    await cb_sc({
                                        "type": "image",
                                        "usage": "lieu",
                                        "image": url_sc,
                                        "msg": f"🖼️ Scène (cache) : {lieu_sc}",
                                    })
                                except Exception:                     # noqa: BLE001
                                    pass
                            print(f"[dnd35] Scène prégénérée servie : {lieu_sc} → {url_sc}")
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Hook scène prégénérée échoué (ignoré) : {e}")

            # 5ter. ⚔️ Rattrapage combat narré EN PROSE mais non engagé.
            # Le petit modèle écrit parfois « Le combat commence ! Le zombie
            # bondit et t'attaque… » avec les jets/dégâts dans la narration,
            # SANS appeler `engager_combat` → l'ordre d'initiative et le suivi
            # des PV restaient absents (phase exploration, side panel vide).
            # On détecte la prose de combat ET on engage la mécanique
            # officielle, pour que l'ordre de combat s'affiche et que la
            # rotation suive les règles 3.5.
            # Le rattrapage reste armé même si d'autres tools ont tourné dans
            # le tour (ex. `lancer_degats` hors combat : les dégâts d'un
            # monstre non suivi partaient dans le vide) — seuls
            # `engager_combat`/`combat_ajouter_combattant` prouvent que le
            # combat EST officiel.
            if not any(
                tc.get("name") in ("engager_combat", "combat_ajouter_combattant")
                for tc in result.tool_calls_trace
            ):
                etat_detect = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                if etat_detect.get("phase") != "combat":
                    try:
                        _types = _detecter_combat_prose(
                            str(cfg.abs(cfg.paths.data_dir)),
                            result.narration or "",
                            etat_detect,
                        )
                        if _types:
                            from .tools.base import (
                                _TOOL_REGISTRY, invoke_tool,
                            )
                            spec = _TOOL_REGISTRY.get("engager_combat")
                            if spec is not None:
                                tr = await invoke_tool(
                                    spec, ctx, {"monstres": ", ".join(_types)},
                                )
                                if tr is not None and not (
                                    tr.text.startswith("⛔")
                                    or tr.text.startswith("❌")
                                ):
                                    if tr.state_patch:
                                        result.state_patches.append(tr.state_patch)
                                    # Ajoute l'initiative officielle à la
                                    # narration pour que la table la voie.
                                    result.narration += (
                                        "\n\n⚙️ _Le serveur a régularisé ce "
                                        "combat porté en prose — initiative "
                                        "officielle engagée pour des "
                                        "monstres du bestiaire._\n\n"
                                        + "".join(
                                            line + "\n" for line in tr.text.splitlines()
                                        )
                                    )
                                    print(
                                        f"[dnd35] Combat prose rattrapé : "
                                        f"engager_combat({', '.join(_types)})"
                                    )
                                    # Recharge l'état pour que l'image soit
                                    # générée pour les monstres désormais suivis.
                                    apres = PartyState(
                                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                                        partie_id=partie_id,
                                    ).load()
                    except Exception as e:                             # noqa: BLE001
                        print(f"[dnd35] Rattrapage combat prose échoué "
                              f"(ignoré) : {e}")

            # 5quater. 🛠️ Correction des non-conformités signalées au test —
            # le petit modèle 9B narre en prose sans appeler les outils
            # spécifiques de la phase (chargement de scénario à l'ouverture,
            # exploration de donjon, inventaire). On ré-invoque une fois le MJ
            # avec une consigne très ferme ; le bloc rejoue uniquement s'il
            # est SÛR que l'action n'a PAS été résolue (outils de phase absents
            # alors que l'état l'exigeait encore).
            try:
                _etat_rejouer = PartyState(
                    data_dir=str(cfg.abs(cfg.paths.data_dir)),
                    partie_id=partie_id,
                ).load()
                _outils_appeles = {
                    str(tc.get("name") or "") and str(tc.get("name"))
                    for tc in result.tool_calls_trace
                }
                _outils_appeles.discard("")

                # --- 5quater-a. Ouverture SANS scénario chargé.
                # Phase "opening" + quête non choisie : le MJ doit avoir
                # appelé `scenarios_laelith_charger`. S'il n'a fait que de la
                # narration (memoire_mission / intrigue à la place), on le
                # force à charger le scénario correspondant à l'id/titre
                # mentionné.
                phase_ouverture = (
                    str(_etat_rejouer.get("phase") or "").strip().lower()
                    in ("opening", "opening_complete")
                )
                quete_pas_chargee = not str(
                    (_etat_rejouer.get("quete") or {}).get("titre") or ""
                ).strip()
                scena_pas_appele = not (
                    _outils_appeles & _SCENARIO_LOAD_TOOLS
                )
                # ⚠️ On ne force le chargement QUE si le joueur DEMANDE
                # explicitement de choisir/charger un scénario (le tour de
                # phase "load"). Pendant la pure création de personnages (aussi
                # en phase "opening"), on ne déclenche RIEN : il est normal que
                # la quête ne soit pas encore posée.
                _demande_scenario = bool(
                    _SCENARIO_CHOICE_RE.search(text or "")
                )
                if (phase_ouverture and quete_pas_chargee and scena_pas_appele
                        and _demande_scenario):
                    _scena_match = (
                        _derive_scenario_id(
                            str(cfg.abs(cfg.paths.data_dir)),
                            (text or "") + " " + (result.narration or ""),
                        )
                        or _derive_scenario_id(
                            str(cfg.abs(cfg.paths.data_dir)),
                            result.narration or "",
                        )
                    )
                    # 1er essai : on ré-invoque le MJ pour qu'il charge le
                    # scénario par lui-même (trace propre côté table).
                    _obj_open = (
                        "⚠️ ERREUR système : le MJ devait CHARGER le "
                        "scénario choisi avant de narrer l'ouverture. "
                        "Appelle MAINTENANT `scenarios_laelith_charger` "
                        f"avec scenario_id='{_scena_match or '_INFER_'}'. "
                        "Appelle impérativement l'outil (jamais à la place "
                        "`memoire_mission`/`memoire_intrigue`), puis narre "
                        "la scène d'ouverture en 2-4 paragraphes."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_open,
                                            "ouverture scénario")
                    # 2e essai : DÉTERMINISTE. Le petit modèle 9B refuse
                    # parfois catégoriquement d'appeler `scenarios_laelith_`
                    # (il confond avec `memoire_mission`/`etat_partie_patch`).
                    # On charge alors le scénario DIRECTEMENT côté serveur via
                    # le registre d'outils, comme le bloc 5ter fait pour
                    # `engager_combat` — la quête est ainsi TOUJOURS posée.
                    _etat_apres_open = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()
                    _quere_apres = str(
                        (_etat_apres_open.get("quete") or {}).get("titre")
                        or ""
                    ).strip()
                    if (not _quere_apres and _scena_match):
                        from .tools.base import (
                            _TOOL_REGISTRY, invoke_tool,
                        )
                        _spec_c = _TOOL_REGISTRY.get("scenarios_laelith_charger")
                        if _spec_c is not None:
                            _tr_c = await invoke_tool(
                                _spec_c, ctx, {"scenario_id": _scena_match},
                            )
                            if _tr_c is not None:
                                if _tr_c.state_patch:
                                    result.state_patches.append(_tr_c.state_patch)
                                # Le tool renvoie `quete` dans son state_patch,
                                # que le CLIENT applique normalement (persisté
                                # ici par le frontend). Comme le serveur a
                                # injecté le chargement, on persiste AUSSI la
                                # quête directement dans le fichier partie pour
                                # que le side panel et les phases suivantes la
                                # voient.
                                _patch_quete = (
                                    (_tr_c.state_patch or {}).get("quete")
                                    or {}
                                )
                                if _patch_quete.get("titre"):
                                    try:
                                        _st_q = PartyState(
                                            data_dir=str(
                                                cfg.abs(cfg.paths.data_dir)),
                                            partie_id=partie_id,
                                        )
                                        _etat_q = _st_q.load()
                                        _etat_q["quete"] = dict(_patch_quete)
                                        _st_q.save(_etat_q)
                                        print(
                                            f"[dnd35] Quête persistée : "
                                            f"{_patch_quete.get('titre')}"
                                        )
                                    except Exception as _e:            # noqa: BLE001
                                        print(
                                            f"[dnd35] Persistance quête "
                                            f"échouée : {_e}"
                                        )
                                if _tr_c.text and not (
                                    _tr_c.text.startswith("⛔")
                                    or _tr_c.text.startswith("❌")
                                ):
                                    result.narration += (
                                        "\n\n⚙️ _Le serveur a chargé le "
                                        "scénario « "
                                        + str(_scena_match)
                                        + " » — quête posée officiellement._"
                                    )
                                    print(
                                        f"[dnd35] Scénario chargé par le "
                                        f"serveur : "
                                        f"scenarios_laelith_charger("
                                        f"{_scena_match})"
                                    )
                    # Recharge l'état pour les phases suivantes.
                    _etat_rejouer = PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).load()

                # --- 5quater-b. Exploration narrée EN PROSE sans outil.
                # Phase d'exploration + déplacement annoncé mais aucun outil
                # `carte_donjon_*` appelé → on rejoue une fois.
                phase_explo = str(
                    _etat_rejouer.get("phase") or ""
                ).strip().lower() in ("exploration", "voyage", "roleplay")
                explo_pas_appele = not (_outils_appeles & _EXPLORATION_TOOLS)
                if phase_explo and explo_pas_appele and _estnarration_explo(
                        (text or "") + " " + (result.narration or "")):
                    _obj_explo = (
                        "⚠️ ERREUR système : le groupe se déplace / "
                        "explore mais aucun outil de déplacement n'a été "
                        "appelé. Utilise MAINTENANT l'outil approprié : "
                        "`carte_donjon_explorer(direction='est')` si un "
                        "donjon est actif, sinon `carte_donjon_entrer` "
                        "(s'il existe un donjon) ou mets à jour "
                        "`etat_partie_patch` (lieu) si la scène se passe "
                        "en ville. NE te contente PAS de narrer : appelle "
                        "l'outil puis narre le résultat."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_explo,
                                            "exploration donjon")

                # --- 5quater-c. Acquisition d'objet non enregistrée.
                # Le MJ (ou le joueur) annonce la récupération/le don d'un
                # objet mais n'appelle aucun outil d'inventaire → l'objet reste
                # introuvable côté serveur/side panel. On ré-invoque une fois.
                inv_pas_appele = not (_outils_appeles & _INVENTAIRE_TOOLS)
                if inv_pas_appele and _ITEM_ACQUISITION_RE.search(
                        (text or "") + " " + (result.narration or "")):
                    _obj_inv = (
                        "⚠️ ERREUR système : l'objet gagné/récupéré/donné "
                        "n'a pas été enregistré. Appelle MAINTENANT "
                        "`inventaire_ajouter` (nom d'un PJ, nom, quantité, "
                        "description) pour persister l'objet, puis narre la "
                        "suite. NE narrate PAS l'acquisition sans appeler "
                        "l'outil d'inventaire."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_inv,
                                            "inventaire objet")

                # --- 5quater-d. Soin ou repos narré mais non appliqué (hors
                # combat). Le joueur (ou MJ) demande un soin / un repos mais
                # aucun outil `fiche_perso_soigner` ni `repos_long` n'a été
                # appelé → les PV ne bougent pas. On ré-invoque une fois, y
                # compris hors combat.
                _soin_global_appele = any(
                    str(tc.get("name")) in (
                        "fiche_perso_soigner", "repos_long",
                        "fiche_perso_mettre_a_jour",
                    )
                    for tc in result.tool_calls_trace
                )
                if (not _soin_global_appele
                        and _SOIN_RE.search((text or "") + " "
                                            + (result.narration or ""))):
                    _obj_soin = (
                        "⚠️ ERREUR système : un SOIN ou un REPOS a été annoncé "
                        "mais aucun outil n'a été appelé — les PV ne sont pas "
                        "modifiés. Pour un soin ponctuel : relance les dés de "
                        "soin (`lancer_des` avec par ex. 1d8) puis appelle "
                        "`fiche_perso_soigner` (nom du PJ à soigner, montant). "
                        "Pour un repos (nuit / 8 h de récupération) : appelle "
                        "`repos_long`, qui applique officiellement la "
                        "récupération des PV et des sorts. NE narrate PAS la "
                        "guérison sans appeler l'outil."
                    )
                    await _rejoue_correctif(orch, messages, ctx, result,
                                            on_event, _obj_soin, "soins")
            except Exception as e:                                     # noqa: BLE001
                print(f"[dnd35] 5quater rattrapage échoué (ignoré) : {e}")

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
    except Exception as e:                                           # noqa: BLE001
        # Le tour a crashé (LLM injoignable, timeout…) : on prévient la table
        # plutôt que de laisser les clients attendre indéfiniment.
        print(f"[dnd35] Tour MJ échoué ({partie_id}/{player}) : {e}")
        await session.broadcast({
            "type": "dm",
            "text": "⚠️ Le MJ a rencontré un problème technique. "
                    "Réessayez dans un instant.",
        })
    finally:
        # Toujours lever le verrou "thinking", y compris si le tour est
        # annulé (WebSocket coupé au milieu du streaming) ou en erreur.
        # Sinon `session.thinking` reste True et BLOQUE tous les messages
        # suivants ("Le MJ est en train de travailler") définitivement.
        session.thinking = False
        last_turn = await _turn_end()

    # 7. Patches d'état → re-synchronise l'UI avec l'état persistant final.
    await session.broadcast({"type": "status", "description": "", "done": True})

    # 8. Décharge le modèle LLM de la VRAM pour libérer la place à ComfyUI —
    #    uniquement si plus AUCUN tour n'est actif (sinon un tour concurrent
    #    perdrait le modèle en cours de route). Le prochain message joueur
    #    rechargera le modèle automatiquement.
    #    - llm.unload_after_turn = true  → immédiat (historique, partage GPU).
    #    - llm.unload_after_turn = false → après llm.unload_delay_minutes
    #      d'inactivité (annulé si un tour reprend ; pratique avec plus de
    #      RAM/VRAM : les tours consécutifs n'ont plus à recharger le modèle).
    if last_turn:
        global _pending_unload
        if cfg.llm.unload_after_turn:
            try:
                await app.state.client.unload_model()
            except Exception:
                pass
        else:
            _cancel_pending_unload()
            _pending_unload = asyncio.create_task(
                _delayed_unload_task(app, cfg.llm.unload_delay_minutes * 60)
            )


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

# Cartes de référence du projet (Faerûn, nord de Faerûn, Outreterre, Toril) :
# copiées au démarrage de `cartes/` (dépôt) vers `data/cartes/` (servi sous
# /data/cartes/…). Idempotent — les fichiers existants ne sont pas écrasés.
_cartes_src = cfg.project_root / "cartes"
if _cartes_src.is_dir() and _data_dir.is_dir():
    _cartes_dst = _data_dir / "cartes"
    _cartes_dst.mkdir(parents=True, exist_ok=True)
    for _f in sorted(_cartes_src.glob("*.*")):
        _dest = _cartes_dst / _f.name
        if not _dest.is_file():
            import shutil as _shutil
            _shutil.copy2(_f, _dest)


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
