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
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from . import catalogue as catalogue_mod
from . import persos as persos_mod
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
    competences = payload.get("competences") or {}

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
    """Liste les ressources consultables : manuels, cartes du monde,
    scénarios PDF locaux (+ carte du donjon de la partie si `partie_id`).
    Les manuels et cartes pointent vers le serveur du projet quand les
    fichiers sont présents sous data/manuels/ (repli externe sinon)."""
    from .tools import scenarios as S
    from .tools.manuels import (
        FICHIERS_DEFAUT,
        url_carte_hires,
        url_carte_lowres,
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
    cartes = [
        {"titre": "Carte de la Côte des Épées (basse résolution)",
         "url": url_carte_lowres(ctx)},
        {"titre": "Carte de la Côte des Épées (haute résolution)",
         "url": url_carte_hires(ctx)},
    ]
    scenarios = [
        {
            "id": s.get("id", ""),
            "titre": s.get("titre", "?"),
            "niveau": s.get("niveau", "?"),
            "url": S._url_data(s["fichier"]),
        }
        for s in S._charger_scenarios_locaux(ctx)
    ]

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
#  Scénarios — catalogue pour le sélecteur de quête côté frontend.
# --------------------------------------------------------------------------- #
@app.get("/api/scenarios")
async def list_scenarios(partie_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Liste les scénarios disponibles (Laelith + PDF locaux) pour le sélecteur."""
    from .tools.scenarios import _charger_catalogue
    data_dir = cfg.abs(cfg.paths.data_dir)
    ctx = ToolContext(
        partie_id=partie_id or "_",
        joueur="",
        data_dir=str(data_dir),
    )
    cat = _charger_catalogue(ctx)
    return [
        {
            "id": s.get("id", ""),
            "titre": s.get("titre", "?"),
            "niveau": s.get("niveau", "?"),
            "theme": s.get("theme", ""),
            "pitch": s.get("pitch", ""),
            "source": s.get("source", ""),
            "fichier": ("/data/" + s["fichier"]) if s.get("fichier") else None,
        }
        for s in cat
    ]


@app.post("/api/parties/{partie_id}/quest")
async def set_quest(partie_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Définit la quête courante d'une partie (sélecteur de quête au démarrage)."""
    titre = payload.get("titre", "")
    pitch = payload.get("pitch", "")
    source = payload.get("source", "")
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
                session.add_participant(player)
                # Rattachement du personnage choisi (menu déroulant côté
                # client) → le PJ rejoint l'état de partie (liste pj).
                personnage = (msg.get("personnage") or "").strip()
                fiche_enregistree = None
                if personnage:
                    fiche_enregistree = persos_mod.enregistrer_personnage_partie(
                        _dossier_donnees(), partie_id, personnage, player
                    )
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


async def _turn_begin() -> None:
    global _active_turns
    async with _turns_guard:
        _active_turns += 1


async def _turn_end() -> bool:
    """Décrémente le compteur de tours ; True s'il ne reste aucun tour actif."""
    global _active_turns
    async with _turns_guard:
        _active_turns = max(0, _active_turns - 1)
        return _active_turns == 0


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
    await session.broadcast({"type": "status", "description": "", "done": True})

    # 8. Décharge le modèle LLM de la VRAM pour libérer la place à ComfyUI —
    #    uniquement si plus AUCUN tour n'est actif (sinon un tour concurrent
    #    perdrait le modèle en cours de route). Le prochain message joueur
    #    rechargera le modèle automatiquement.
    if last_turn:
        try:
            await app.state.client.unload_model()
        except Exception:
            pass


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
