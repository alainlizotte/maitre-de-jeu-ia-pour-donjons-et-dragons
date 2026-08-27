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
from . import persos as persos_mod
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

import re as _re_mod

# Détection d'une invoquation / renfort annoncé par un joueur en combat :
# déclenche le rattrapage 5bis-b si le MJ l'a narrée sans tool.
_INVOKE_RE = _re_mod.compile(
    r"\b(invoqu\w*|convoqu\w*|invocation\w*|summon\w*|renforts?)\b",
    _re_mod.IGNORECASE,
)


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

    fiche = {
        "nom": nom,
        "joueur": utilisateur,
        "proprietaire": utilisateur,
        "race": persos_mod.resoudre_race(race) or race,
        "classe": persos_mod.resoudre_classe(classe) or classe,
        "niveau": niveau,
        "carac": calculs["carac_final"],
        "pv": calculs["pv"],
        "pv_max": calculs["pv_max"],
        "ca": calculs["ca"],
        "sauvegardes": calculs["sauvegardes"],
        "bab": calculs["bab"],
        "initiative": calculs["initiative"],
        "charge_max": calculs["charge_max"],
        "competences": competences,
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
    etat["phase"] = "exploration"
    err = state.save(etat)
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "quete": etat["quete"]}


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


# Compteur global de tours MJ actifs (toutes parties confondues) : le déchargement
# du modèle LLM ne doit survenir que lorsque PLUS AUCUN tour n'est en cours —
# sinon un tour concurrent verrait le modèle disparaître sous ses pieds (HTTP 500).
_active_turns: int = 0
_turns_guard: asyncio.Lock = asyncio.Lock()
# Unload différé (llm.unload_after_turn = false) : tâche en attente, annulée si
# un nouveau tour démarre avant l'expiration du délai.
_pending_unload: Optional[asyncio.Task] = None


def _cancel_pending_unload() -> None:
    """Annule un unload différé en attente (un tour reprend la main)."""
    global _pending_unload
    if _pending_unload is not None and not _pending_unload.done():
        _pending_unload.cancel()
    _pending_unload = None


async def _turn_begin() -> None:
    global _active_turns
    async with _turns_guard:
        # Un nouveau tour démarre : le modèle doit rester en VRAM.
        _cancel_pending_unload()
        _active_turns += 1


async def _turn_end() -> bool:
    """Décrémente le compteur de tours ; True s'il ne reste aucun tour actif."""
    global _active_turns
    async with _turns_guard:
        _active_turns = max(0, _active_turns - 1)
        return _active_turns == 0


async def _delayed_unload_task(app: FastAPI, delay_s: float) -> None:
    """Décharge le modèle après `delay_s` secondes d'inactivité.

    Le garde `_turns_guard` est conservé pendant l'appel réseau d'unload :
    un tour qui démarre pendant l'unload attend sa fin (≈1 s) au lieu de
    perdre le modèle en cours de route. La tâche est annulée par
    `_cancel_pending_unload` si un tour reprend avant l'expiration du délai.
    """
    global _pending_unload
    try:
        await asyncio.sleep(delay_s)
        async with _turns_guard:
            if _active_turns > 0:
                return  # un tour a repris — il reprogrammera l'unload
            _pending_unload = None
            await app.state.client.unload_model()
    except asyncio.CancelledError:
        pass
    except Exception:                                               # noqa: BLE001
        pass


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

    # 1bis. ⚔️ Application MÉCANIQUE du tour de jeu (D&D 3.5) : en phase de
    # combat, seul le joueur dont c'est le tour peut déclencher le MJ. Les
    # messages des autres joueurs sont diffusés mais n'invoquent PAS le LLM.
    etat_avant = PartyState(
        data_dir=str(cfg.abs(cfg.paths.data_dir)), partie_id=partie_id
    ).load()
    actif_avant = str(etat_avant.get("courant_tour_pour") or "")
    if etat_avant.get("phase") == "combat":
        actif = actif_avant
        pj_actif = next(
            (p for p in (etat_avant.get("pj") or []) if p.get("nom") == actif),
            None,
        )
        if pj_actif is not None:
            joueur_actif = str(pj_actif.get("joueur") or "").strip().lower()
            if joueur_actif and player.strip().lower() != joueur_actif:
                await session.broadcast({
                    "type": "sys",
                    "event": "turn_blocked",
                    "detail": (
                        f"⏳ {player} doit attendre : en combat, c'est le tour "
                        f"de {actif} (joué par {pj_actif.get('joueur')}) — "
                        f"round {etat_avant.get('tour', 1)}."
                    ),
                })
                return
        # Si l'actif est un PNJ/monstre : le message passe, la bannière de tour
        # injectée plus bas force le MJ à jouer le monstre puis avancer.

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

            # 3bis. ⚔️ Bannière de tour : rappel mécanique du combat en cours,
            # injecté dans le message système à chaque invocation du MJ.
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
                        f"{actif} — JOUEUR {pj_actif.get('joueur')}. "
                        "Résous UNIQUEMENT les actions de CE personnage "
                        "(ne fais agir aucun autre PJ), puis appelle "
                        "OBLIGATOIREMENT tour_suivant_combat."
                    )
                else:
                    qui = (
                        f"{actif} (MONSTRE/PNJ). C'est TOI, le MJ, qui "
                        "décides et joues ses actions toi-même — n'attends "
                        "AUCUNE instruction des joueurs et ne leur demande "
                        "JAMAIS ce que fait le monstre. Ne t'adresse JAMAIS "
                        f"au monstre (« {actif}, que faites-vous ? » est "
                        "INTERDIT) : résous ses actions avec les tools puis "
                        "raconte-les à la 3ᵉ personne. Son tour est un VRAI "
                        "tour : aucune action résumée entre parenthèses — il "
                        "attaque (lancer_attaque), se déplace ou agit "
                        "clairement. Tactique simple : il attaque le héros "
                        "le plus proche/mençant. Les dés et les règles "
                        f"décident du résultat : lancer_attaque(attaquant="
                        f"\"{actif}\", cible=<nom du PJ visé>) → "
                        "lancer_degats → infliger_degats(nom=<PJ>, "
                        "degats=N) si touché ; si le jet rate, narre "
                        "l'échec. Puis appelle tour_suivant_combat. "
                        "OBLIGATION ABSOLUE : Tu DOIS appeler lancer_attaque "
                        "ET infliger_degats (si touché) AVANT de narrer quoi "
                        "que ce soit. NE PAS écrire l'action en prose sans "
                        "tool — chaque attaque MUST passer par les tools."
                    )
                system_text += (
                    f"\n\n⚔️ **TOUR EN COURS** — round {etat.get('tour', 1)}. "
                    f"Ordre d'initiative : {ordre}. C'est AU TOUR DE {qui} "
                    "Économie d'actions D&D 3.5 : max 1 action standard + 1 "
                    "action de mouvement (+ actions libres) par round. Toute "
                    "attaque passe par lancer_attaque, toute sauvegarde par "
                    "lancer_sauvegarde, tout dégât appliqué par infliger_degats. "
                    "Invoquation / renfort en cours de mêlée (sort "
                    "d'invocation, squelettes, allié appelé) : appelle "
                    "combat_ajouter_combattant(nom, allie) AVANT de narrer "
                    "l'arrivée de la créature — jamais engager_combat."
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

            async def on_event(ev: dict[str, Any]) -> None:
                await session.broadcast({"type": "tool_event", "event": ev})

            async def on_delta(token: str) -> None:
                # Stream des tokens de narration vers tous les clients connectés.
                if cfg.game.stream_to_clients:
                    await session.broadcast({"type": "delta", "text": token})

            orch = _orchestrator(app)
            result = await orch.run(messages, ctx, on_event=on_event, on_delta=on_delta)

            # 5bis. ⚔️ Rejeu forcé des tours monstres non résolus mécaniquement.
            # Si c'est le tour d'un monstre et qu'aucun `lancer_attaque` ou
            # `lancer_degats` n'a été appelé, on ré-invoque le MJ avec un
            # message correctif qui le force à jouer le monstre via les tools.
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

                    monstre_a_attaque = False
                    if est_monstre:
                        for tc in result.tool_calls_trace:
                            if tc.get("name") in (
                                "lancer_attaque", "lancer_degats",
                                "fiche_perso_infliger_degats",
                            ):
                                monstre_a_attaque = True
                                break

                    # Si le monstre n'a pas attaqué → rejeu avec correctif
                    if est_monstre and not monstre_a_attaque:
                        print(
                            f"[dnd35] Tour monstre {actif_avant} sans "
                            f"attaque outillée — rejeu avec correctif"
                        )
                        # Injecter un correctif dans l'historique conversationnel
                        corrective = (
                            "⚠️ ERREUR : tu n'as pas joué le tour de "
                            f"**{actif_avant}**. C'est SON tour de combat. "
                            "Tu DOIS jouer CE monstre maintenant. "
                            "Choisis une action d'attaque et appelle les outils "
                            "`lancer_attaque` puis `lancer_degats` puis "
                            "`fiche_perso_infliger_degats` pour appliquer les "
                            "dégâts. NE demandez PAS aux joueurs ce qu'ils "
                            "font — c'est au tour du monstre d'agir. "
                            "NE narrate PAS en prose sans d'abord appeler "
                            "ces outils. Si le monstre rate, passez au "
                            "tour suivant avec `tour_suivant_combat`."
                        )
                        # Reconstituer les messages avec le correctif
                        corrective_messages = list(messages) + [
                            Message(role="assistant",
                                    content=result.narration),
                            Message(role="system",
                                    content=corrective),
                        ]
                        result2 = await orch.run(
                            corrective_messages, ctx,
                            on_event=on_event, on_delta=None,
                        )
                        # Si la 2e tentative a réussi → fusionner les résultats
                        has_attack2 = any(
                            tc.get("name") in (
                                "lancer_attaque", "lancer_degats",
                                "fiche_perso_infliger_degats",
                            )
                            for tc in result2.tool_calls_trace
                        )
                        if has_attack2:
                            # Fusionner les tool events et patches
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
                            print(
                                f"[dnd35] Rejeu monstre {actif_avant} "
                                f"réussi ({len(result2.tool_calls_trace)} "
                                f"tools appelés)"
                            )
                        else:
                            print(
                                f"[dnd35] Rejeu monstre {actif_avant} "
                                f"toujours sans attaque — avancement forcé"
                            )
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
                        "⚠️ ERREUR : le joueur vient d'annoncer une "
                        "invoquation / un renfort, mais tu as narré "
                        "l'arrivée de la créature SANS l'enregistrer "
                        "mécaniquement. Appelle IMMÉDIATEMENT "
                        "`combat_ajouter_combattant(nom=<créature invoquée>, "
                        "allie=true si elle combat pour les joueurs)` : "
                        "l'outil l'insère dans l'ordre d'initiative et suit "
                        "ses PV. N'appelle PAS engager_combat (il "
                        "réinitialiserait le combat en cours). Reprends ensuite "
                        "ta narration en t'appuyant sur le résultat de l'outil."
                    )
                    corrective_messages = list(messages) + [
                        Message(role="assistant", content=result.narration),
                        Message(role="system", content=corrective_inv),
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
                        print("[dnd35] Rejeu invoquation réussi")
                    else:
                        print("[dnd35] Rejeu invoquation toujours sans "
                              "outil — best effort accepté")
            except Exception as e:
                print(f"[dnd35] 5bis-b rejeu invoquation failed: {e}")

            # 5bis suite : avancement mécanique du tour
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
                    ordre = apres.get("initiative") or []
                    idx = next(
                        (i for i, e2 in enumerate(ordre)
                         if e2.get("nom") == actif_avant),
                        -1,
                    )
                    if idx >= 0 and ordre:
                        idx += 1
                        if idx >= len(ordre):
                            idx = 0
                            apres["tour"] = (apres.get("tour", 1) or 1) + 1
                        nouveau = ordre[idx].get("nom", "")
                        apres["courant_tour_pour"] = nouveau
                        PartyState(
                            data_dir=str(cfg.abs(cfg.paths.data_dir)),
                            partie_id=partie_id,
                        ).save(apres)
                        result.state_patches.append({
                            "tour": apres.get("tour"),
                            "courant_tour_pour": nouveau,
                        })
                        result.narration += (
                            f"\n\n➡️ _Fin du tour de {actif_avant} — "
                            f"au tour de **{nouveau}** "
                            f"(round {apres.get('tour', 1)})._"
                        )
                        print(f"[dnd35] Tour avancé automatiquement : "
                              f"{actif_avant} → {nouveau}")

                # 5ter. Clôture automatique : quand tous les monstres suivis
                # par engager_combat sont détruits, le combat se termine
                # mécaniquement (le modèle oublie souvent finir_combat).
                # NB : les alliés invoqués (combat_ajouter_combattant
                # allie=True) ne comptent pas — seuls les ennemis cloturent.
                if apres.get("phase") == "combat":
                    mons = apres.get("monstres_combat") or []
                    ennemis = [
                        m2 for m2 in mons if not m2.get("allie")
                    ]
                    if ennemis and all(
                        "Détruit" in (m2.get("conditions") or [])
                        for m2 in ennemis
                    ):
                        apres["phase"] = "exploration"
                        apres["initiative"] = []
                        apres["courant_tour_pour"] = None
                        apres["tour"] = 0
                        apres["monstres_combat"] = []
                        PartyState(
                            data_dir=str(cfg.abs(cfg.paths.data_dir)),
                            partie_id=partie_id,
                        ).save(apres)
                        result.state_patches.append({
                            "phase": "exploration",
                            "tour": 0,
                            "courant_tour_pour": None,
                            "initiative": [],
                            "monstres_combat": [],
                        })
                        result.narration += (
                            "\n\n⚔️ _Tous les ennemis sont à terre — "
                            "le combat est terminé !_"
                        )
                        print("[dnd35] Combat clôturé automatiquement "
                              "(tous les monstres détruits).")

                # 5quater. 🖼️ Illustrations des monstres en jeu : le modèle
                # oublie souvent d'appeler monstre_consulter à l'annonce d'une
                # rencontre ; on garantit ici le portrait de chaque monstre
                # nouveau (cache instantané s'il existe déjà, budget temps
                # sinon pour ne pas bloquer la table). L'URL est persistée
                # dans monstres_combat[i].image_url ET le journal
                # rencontres_images : le front peut ainsi réafficher les
                # portraits après un rechargement de page, jusqu'à la mort
                # du monstre.
                _t0 = time.time()
                _vus: set[str] = set()
                _img_persist = False
                _nouvelles_rencontres: list[tuple[str, str]] = []
                for mo in apres.get("monstres_combat") or []:
                    nom_mo = str((mo or {}).get("nom") or "").strip()
                    cle_mo = nom_mo.lower()
                    if not nom_mo or cle_mo in _vus:
                        continue
                    _vus.add(cle_mo)
                    if time.time() - _t0 > 100:
                        print("[dnd35] Budget images monstres atteint — "
                              "le reste sera généré au tour suivant.")
                        break
                    try:
                        url_img = await image_pour(ctx, nom_mo)
                    except Exception as e:                           # noqa: BLE001
                        print(f"[dnd35] Image {nom_mo} échouée (ignoré) : {e}")
                        continue
                    if url_img:
                        result.state_patches.append({"image_monstre": url_img})
                        if (mo or {}).get("image_url") != url_img:
                            mo["image_url"] = url_img
                            _img_persist = True
                        _nouvelles_rencontres.append((nom_mo, url_img))
                if _nouvelles_rencontres:
                    from .tools.monstres import _fusionner_rencontres
                    if _fusionner_rencontres(apres, _nouvelles_rencontres):
                        _img_persist = True
                if _img_persist:
                    PartyState(
                        data_dir=str(cfg.abs(cfg.paths.data_dir)),
                        partie_id=partie_id,
                    ).save(apres)
            except Exception as e:                                   # noqa: BLE001
                print(f"[dnd35] Avancement de tour auto échoué (ignoré) : {e}")

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
        last_turn = await _turn_end()

    # 7. Patches d'état → re-synchronise l'UI avec l'état persistant final.
    session.thinking = False
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
