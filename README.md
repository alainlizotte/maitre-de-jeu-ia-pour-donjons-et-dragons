# D&D 3.5 — Maître du Jeu (application web autonome)

Application web **fullstack** qui remplace OpenWebUI comme interface pour le
Maître du Jeu D&D 3.5. Le backend FastAPI orchestre les tools Python et un
LLM local via Ollama, sans dépendre des réglages fragiles de tool-calling
d'OpenWebUI. Le frontend est servi à `/`.

## Pourquoi cette app (vs OpenWebUI)
- **Function-calling fiable** : le backend pilote la boucle LLM ↔ tools
  (mode natif OpenAI /mode prompt-based adapté à Gemma + détection des
  simulations textuelles). Plus de `*(Simulation de l'appel ...)*`.
- **Déploiement en une commande** : `uvicorn server.main:app --reload`.
- **Réutilise le projet Dongeon dragon** : prompts, sections dynamiques,
  bestiaire, fiches, schéma d'état JSON — tout est porté tel quel.
- **Backend LLM interchangeable** : Ollama (Gemma 4 12B par défaut), mais
  basculable vers OpenAI / Anthropic via `config.yaml` en changeant `base_url`
  et `model`.

## Démarrage rapide

```bash
# 1. Dépendances Python
py -m pip install -r requirements.txt

# 2. (Option) Copier la config
copy config\config.example.yaml config\config.yaml   # Windows
# cp config/config.example.yaml config/config.yaml   # Unix

# 3. Lancer Ollama avec Gemma 4 12B (autre terminal)
ollama pull igorls/gemma-4-12B-it-qat-q4_0-unquantized-heretic:latest
ollama pull nomic-embed-text           # embeddings pour le RAG
ollama serve

# 4. (Recommandé) Pré-ingérer la base de connaissances RAG (ChromaDB)
#    La première ingestion dure ~10-20 min (16 fichiers, 2 M mots) via Ollama.
py -m server.rag --ingest
# Vérifier : py -m server.rag --stats

# 5. Démarrer le serveur
py -m uvicorn server.main:app --reload --port 8000

# 6. Ouvrir le chat
#    http://127.0.0.1:8000
```

Cliquez sur **« Nouvelle partie »** puis **« Rejoindre »**, saisissez votre
prénom, et écrivez « Bonjour, on commence une partie ». Le MJ doit déclencher
de **vrais** jets de dés (pas de `*(Simulation...)*`) et s'appuie sur la base
de connaissances RAG pour les règles (mod carac 17 → +3, sauvegardes, etc.).

## Configuration (`config/config.yaml`)
| Clé | Defaut | Rôle |
|---|---|---|
| `llm.base_url` | `http://localhost:11434/v1` | Endpoint Ollama OpenAI-compat. |
| `llm.model` | `gemma4:12b` | Modèle. (`qwen2.5:9b` et autres supportés) |
| `llm.tool_mode` | `prompt` | `native` (tool_calls OpenAI) / `prompt` (balises `<tool ...>`) / `auto` |
| `llm.detect_simulation` | `true` | Détecte et corrige les simulations textuelles |
| `llm.options.num_ctx` | `60000` | Fenêtre de contexte Ollama (tokens) — calibré Gemma 4 12B |
| `llm.options.top_k` | `64` | Sampling diversifié Ollama — calibré Gemma 4 12B |
| `rag.enabled` | `true` | Active la Knowledge Base vectorielle (ChromaDB) |
| `rag.source_dir` | `../Dongeon dragon/…/knowledge_import` | Corpus `.txt` à ingérer (KB1/KB2/KB4) |
| `rag.embedding_model` | `nomic-embed-text` | Modèle d'embeddings (`ollama pull` requis) |
| `rag.chunk_size` | `1500` | Taille de chunk en tokens (cf. MANIFESTE_KNOWLEDGE_BASE) |
| `rag.top_k` | `5` | Nombre d'extraits récupérés par requête |
| `paths.data_dir` | `./server/data` | État persistant + bestiaire + fiches + ChromaDB |
| `paths.prompts_dir` | `./server/prompts` | SystemPrompt + sections dynamiques |

## API
| Méthode | Route | Rôle |
|---|---|---|
| GET  | `/api/health` | Statut serveur + Ollama + tools |
| GET  | `/api/parties` | Liste parties actives + persistées |
| POST | `/api/parties` | Crée une nouvelle partie |
| GET  | `/api/parties/{id}` | État persistant d'une partie |
| GET  | `/api/tools` | Introspection des tools (schéma JSON) |
| GET  | `/api/rag/stats` | Nombre de chunks par collection ChromaDB |
| POST | `/api/rag/ingest` | Déclenche l'ingestion du corpus (admin, long) |
| WS   | `/ws/{partie_id}` | Canal chat multijoueur temps réel |

## Cycle d'un message joueur (pipeline)
1. WebSocket `/ws/{partie_id}` reçoit `{"type":"say","player":"Alain","text":"..."}`.
2. Le serveur préfixe le commentaire joueur (`**[Alain]** : ...`) et l'ajoute à
   l'historique de la session.
3. `PromptBuilder.build_system_message` construit le message système
   (SystemPrompt allégé + récap d'état + sections dynamiques selon `phase`
   **+ contexte RAG** : `RagStore.query(question)` récupère les `top_k=5`
   extraits pertinents de la Knowledge Base et les injecte dans le bloc
   `=== CONTEXTE RÈGLES ===`).
4. `Orchestrator.run` boucle :
   - appel LLM (Ollama via API OpenAI-compatible),
   - détection de simulation textuelle → corrective system-message + retry,
   - extraction des tool_calls (natif ou balises `<tool ...>`),
   - exécution des tools Python (qui touchent l'état persistant, génèrent
     des images monstres / cartes, etc.),
   - renvoi du résultat en message `role=tool` au LLM,
   - boucle jusqu'à une réponse narration finale.
5. Le narration finale est broadcastée en WebSocket à tous les joueurs
   connectés à la partie (avec événements d'outils le cas échéant).

## Tools disponibles (~40 outils)

Outils routés par phase dans `orchestrator._PHASE_TOOLS` (un modèle 12B ne gère
fiablement que ~10 tools visibles à la fois).

- **Dés** (`dice.py`) — `lancer_d20`, `lancer_attaque`, `lancer_degats`,
  `lancer_sauvegarde`, `lancer_caracteristiques`, `calculer_initiative`, `lancer_des`.
- **État** (`state.py`) — `etat_partie_get/save/patch`, `demarrer_combat`,
  `tour_suivant_combat`, `finir_combat`, `ajouter_evenement_histoire`,
  `set_derniere_narration`, `reset_partie`.
- **Fiches personnages** (`fiches.py`, 9 outils) — création complète / rapide,
  modification de champ, dégâts / soins, conditions, consultation, liste.
  Validation JSON Schema draft-07 (`data/fiches/schema_fiche.json`) à chaque
  écriture ; portraits PJ générés en arrière-plan via ComfyUI.
- **Monstres** (`monstres.py`, 3 outils) — lister / consulter le bestiaire
  JSON + génération d'image (ComfyUI workflow « monstre »).
- **Cartes** (`cartes.py`, 7 outils) — carte du monde (consultation,
  déplacement), donjon (exploration salle/cellule, portes bloquées, rendu SVG),
  distribution aux joueurs.
- **Manuels & scénarios** (`manuels.py` 2, `scenarios.py` 2) — distribution
  des manuels D&D 3.5, lancement du scénario Laelith.

## Images générées (ComfyUI)

`server/image/` embarque les workflows ComfyUI (monstre, portrait, carte de
salle) et les helpers `generer_averti`. Les images sont écrites sous
`data/<sous-dossier>/` et servies sur la route **`/data/`** (mount StaticFiles).
Les tools `_url_for(path, data_dir)` rendent le chemin relatif et préfixent
par `/data/`.

## Base de connaissances RAG (ChromaDB)

`server/rag/` — vector store embarqué ChromaDB, 3 collections (KB1 Manuels,
KB2 Aide perso, KB4 DRS corpus). Corpus source : `knowledge_import/` du projet
OpenWebUI (16 fichiers `.txt`, ~2 M mots).

- **Ingestion** : `py -m server.rag --ingest` (incrémental via manifeste de
  fingerprints ; `--force` pour ré-embedding complet).
- **Requête runtime** : à chaque message joueur, `_handle_say` rappelle
  `RagStore.render_for_prompt(text)` et injecte le bloc `=== CONTEXTE RÈGLES ===`
  dans le system prompt.
- **Embeddings** : `nomic-embed-text` via Ollama (endpoint `/embeddings`),
  chunk_size 1500 tokens, overlap 200, Top K 5 — paramètres calqués sur
  `MANIFESTE_KNOWLEDGE_BASE.md` du projet source.
- **Tests d'acceptation** : `py -m pytest tests/test_rag_qualite.py -v`
  (6 questions canoniques : mod carac 17, sauvegardes magicien niv.1, rang
  hors-classe, Massive Damage DD 15, flat-footed, achat de points).

## Structure

```
d&d app/
├── config/                 ← config.example.yaml + config.yaml (gitignored)
├── requirements.txt
├── pytest.ini              ← tests (asyncio_mode=auto)
├── tests/
│   ├── test_rag_qualite.py ← 6 questions canoniques RAG
│   └── e2e_gemma.py        ← scénario E2E via WebSocket
├── client/                 ← (Phase 2 — frontend React riche, Vite/TS)
│   ├── src/                  ← composants, pages, hooks API/WS
│   └── vite.config.ts        ← dev port 5173 + proxy /api + /ws → 8000
└── server/
    ├── main.py             ← FastAPI + WS + routes REST (+ /data + /api/rag/*)
    ├── config.py           ← chargement YAML (sections llm/server/paths/game/rag)
    ├── tools/
    │   ├── base.py         ← @tool, ToolContext, ToolResult
    │   ├── registry.py     ← auto-discovery + schéma JSON + section prompt
    │   ├── dice.py         ← 7 outils de jets de dés
    │   ├── state.py        ← mémoire de partie persistante
    │   ├── fiches.py       ← 9 outils fiches (validation JSON Schema)
    │   ├── monstres.py     ← 3 outils bestiaire + image ComfyUI
    │   ├── cartes.py       ← 7 outils carte monde + donjon SVG
    │   ├── manuels.py      ← 2 outils distribution manuels
    │   └── scenarios.py    ← 2 outils scénario Laelith
    ├── llm/
    │   ├── client.py       ← client Ollama OpenAI-compatible (stream/non-stream
    │   │                     + options num_ctx/top_k passées au runtime)
    │   ├── orchestrator.py ← boucle function-calling + détection simulation
    │   │                     + filtre tools par phase (workaround Gemma 12B)
    │   └── prompt_builder.py ← SystemPrompt + sections + récap + RAG context
    ├── game/
    │   ├── state.py        ← PartyState (JSON persistant atomique)
    │   └── session.py      ← sessions WS + persistance history (chat_<id>.json)
    ├── rag/
    │   ├── chunker.py      ← découpage .txt respecte séparateurs page DRS
    │   ├── embeddings.py   ← wrapper Ollama /embeddings (nomic-embed-text)
    │   ├── store.py        ← ChromaDB persistant + 3 collections + render_context
    │   └── __main__.py     ← CLI ingestion/query/stats
    ├── image/              ← workflows ComfyUI + helpers generer_averti
    ├── prompts/            ← SystemPrompt + sections/ (copié du projet source)
    ├── data/               ← bestiaire, fiches+(schema), parties, chroma/, caches
    └── static/             ← frontend build React (prod) ou fallback chat vanilla
```

## Frontend (React riche — Phase 2)

L'application React vit dans `client/` :

```bash
# Développement : proxy Vite /api + /ws vers http://127.0.0.1:8000
cd client && npm install && npm run dev    # → http://localhost:5173

# Production : build statique vers server/static/, servi par FastAPI à /
cd client && npm run build                 # puis http://127.0.0.1:8000
```

Stack : Vite + React 18 + TypeScript + Tailwind + react-router + zustand +
`@tanstack/react-query` + `marked` (rendu Markdown narration). Pages : accueil
(choix/création de partie), partie (sidebar état temps réel via
`state_patches`, chat/narration avec streaming `delta` + images `/data`,
sidebar droite avec fiche PJ / dés visuels / carte & donjon SVG / bestiaire /
manuels).
