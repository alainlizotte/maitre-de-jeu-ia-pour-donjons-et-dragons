# 🐉 Maître du Jeu IA — Donjons & Dragons 3.5

###PROJET EN COURS DE DÉVELOPPEMENT###

Application web **multijoueur autonome** qui joue le rôle de **Maître du Jeu (MJ)** pour une
table de Donjons & Dragons 3.5 : un LLM local (Gemma 4, via **llama.cpp** par défaut ou
Ollama) narre l'aventure, arbitre les règles avec de **vrais jets de dés**, gère les combats
**tour par tour côté serveur** avec **XP et montées de niveau officiels (DMG 3.5)**, et peut
**générer les images** (monstres, portraits, donjons) via ComfyUI.

Aucun service externe ni compte requis : tout tourne en local (Docker), les joueurs se
connectent depuis leur navigateur sur le réseau local.

| | |
|---|---|
| **Backend** | FastAPI + WebSocket, ~65 tools Python (function-calling) |
| **Frontend** | React 18 + TypeScript + Vite + Tailwind |
| **LLM** | llama.cpp (par défaut) / Ollama (OpenAI-compatible) — Gemma 4 |
| **Règles** | Moteur de combat + XP/niveaux 3.5 officiels ; RAG ChromaDB (optionnel) |
| **Images** | ComfyUI (monstres, portraits PJ, cartes de donjon) — optionnel |
| **Données** | Inventaire & encombrement (poids PHB 3.5), mémoire de campagne persistante |

## ✨ Fonctionnalités

### Table multijoueur temps réel
- **Salon de jeu en ligne** : création/rejoindre une partie, optionnellement protégée par mot
  de passe ; chat partagé avec narration du MJ en streaming token par token.
- **Fiches de personnages complètes** : création guidée (caractéristiques, race, classe,
  compétences plafonnées selon INT/niveau, dons limités selon niveau) puis consultation et
  modification à tout moment — PV, conditions, sorts, équipement.
- **Apparence par race** : tirage officiel de l'âge (selon le groupe de classe), de la taille
  et du poids (selon le sexe) aux tables du PHB, alimentant le portrait.
- **Jets de dés réels** : attaque, dégâts, sauvegardes, initiative… toujours résolus par des
  tools Python (jamais « simulés » par le LLM).

### Combat & progression (règles officielles)
- **Combat tour par tour** suivi mécaniquement côté serveur : ordre d'initiative, PV des
  monstres, auto-avance, gestion des morts/soins, clôture automatique quand tous les ennemis
  sont à terre.
- **XP et niveaux officiels (DMG 3.5)** : chaque PJ vivant gagne l'XP de la table
  « Experience Point Awards » pour chaque ennemi vaincu, selon son propre niveau ; montées de
  niveau (jet de dé de vie) et pertes de niveau (energy drain) automatiques.
- **Barres de progression visibles** : XP (niveau actuel → suivant) et **charge transportée**
  (poids kg / charge max) sur les cartes PJ en jeu, les fiches et l'accueil.

### Inventaire & encombrement (PHB 3.5)
- Chaque arme, armure et objet du catalogue a un **poids officiel (PHB 3.5)** ; le formulaire
  de création indique le total porté par rapport à la capacité de transport (Force × taille)
  avec avertissement non bloquant en cas de dépassement.
- **Charge suivie en jeu** (`inventaire_*`) : poids transporté, consommation de munitions, et
  catégorie d'encombrement (Légère/Moyenne/Lourde/Dépassée) recalcuée automatiquement.

### Exploration & campagne
- **Mémoire de campagne persistante** : missions, lieux, PNJ, monstres combattus (remplie
  automatiquement à chaque victoire) et position du groupe — réinjectée dans le prompt du MJ
  pour une cohérence longue durée.
- **Voyage hors donjon** conforme au SRD : durée réelle (vitesse, terrain, marche forcée),
  rencontres aléatoires, risque de s'égarer et météo.
- **Carte du monde interactive** (Côte des Épées / Faerûn) et **donjon procédural** rendu en
  SVG : pièces explorées, portes, déplacements suivis par le MJ.
- **Bestiaire étendu (~340 monstres)** consultable, avec fiche détaillée et image générée.

### IA générative (ComfyUI — optionnel, désactivé par défaut)
- **Images de monstres générées automatiquement** dès leur apparition dans la narration
  (cache par description : l'image ne change pas tant que la fiche ne change pas).
- **Portraits de personnages** générés à la création.
- **Illustrations de scènes et lieux** à la demande.

### Base de connaissances (RAG — optionnel, désactivé par défaut)
- Les manuels D&D 3.5 (dépôt local `knowledge_import/`, textes OCR fournis par vos soins)
  peuvent être vectorisés dans ChromaDB ; chaque message joueur injecte alors les extraits de
  règles pertinents dans le contexte du MJ → réponses fidèles aux règles (modificateurs de
  carac, sauvegardes, états…).
- Activer `rag.enabled: true` dans `config/config.yaml` puis ingestion :
  `docker compose exec dnd35 python -m server.rag --ingest`.

## 🚀 Démarrage rapide (Docker)

Prérequis : Docker Desktop, et un modèle de chat pour **llama.cpp** (ou Ollama). Les images
(ComfyUI) et le RAG sont **optionnels** et désactivés par défaut.

```bash
# 1. Config : copier l'exemple puis ajuster si besoin (backend LLM, modèle, ports)
cp config/config.example.yaml config/config.yaml     # Windows: copy

# 2. (Optionnel) corpus RAG : déposez vos textes OCR dans knowledge_import/
#    puis activez rag.enabled=true et peupler la base :
docker compose exec dnd35 python -m server.rag --ingest

# 3. Démarrer (arrière-plan, redémarrage automatique)
docker compose up -d --build        # → http://localhost:8123

# Arrêt / journaux
docker compose down
docker compose logs -f dnd35
```

Port modifiable : `set DND35_PORT=9000` (ou `.env`). Accès hors réseau local : redirection de
port sur votre box ou reverse proxy HTTPS devant le port 8123.

### Sans Docker

```bash
py -m pip install -r requirements.txt
cd client && npm install && npm run build && cd ..   # frontend → server/static/
py -m uvicorn server.main:app --port 8000            # → http://127.0.0.1:8000
```

## ⚙️ Configuration (`config/config.yaml`)

| Clé | Défaut | Rôle |
|---|---|---|
| `llm.backend` | `llamacpp` | `llamacpp` ou `ollama` (endpoint OpenAI-compatible) |
| `llm.base_url` | `http://localhost:8080/v1` | Endpoint du backend LLM |
| `llm.model` | `gemma-4-E4B-it-Q4_0` | Modèle de chat (Qwen, Mistral… supportés) |
| `llm.tool_mode` | `prompt` | `native` / `prompt` (balises `<tool>`) / `auto` |
| `llm.detect_simulation` | `true` | Corrige les « simulations » textuelles d'outils |
| `rag.enabled` | `false` | Base de connaissances ChromaDB (règles D&D 3.5) |
| `rag.source_dir` | `./knowledge_import` | Corpus `.txt/.md` à ingérer |
| `rag.embedding_model` | `embeddinggemma` | Modèle d'embeddings dédié (llama.cpp CPU) |
| `image.enabled` | `false` | Génération d'images ComfyUI (monstres, portraits, donjons) |
| `paths.data_dir` | `./server/data` | Parties, fiches, caches d'images, ChromaDB |

Variables d'environnement : `DND35_CONFIG` (chemin config), `COMFYUI_BASE_URL`
(`http://host.docker.internal:8188` par défaut).

## 🧠 Comment ça marche

1. Le joueur écrit dans le chat → WebSocket `/ws/{partie_id}`.
2. Le **PromptBuilder** assemble le system prompt : instructions MJ + état de partie
   (phase, combat, quête, mémoire de campagne) + sections dynamiques + extraits RAG si actif.
3. L'**Orchestrator** boucle function-calling : le LLM choisit parmi ~65 tools Python
   (`lancer_attaque`, `engager_combat`, `perso_creer`, `inventaire_ajouter`,
   `monstre_consulter`, `voyager`, `memoire_*`, …) filtrés par phase de jeu.
4. Chaque tool touche l'état persistant JSON (partie, fiches, bestiaire) ; les patches
   résultants sont broadcastés à tous les clients (PV, XP, charge mis à jour en direct, etc.).
5. Le **moteur de combat serveur** applique automatiquement les dégâts, l'initiative et la
   **distribution d'XP/niveaux** ; la narration finale est streamée à toute la table et les
   hooks post-tour font avancer les tours et génèrent les images manquantes.

## 📁 Structure

```
├── client/                 ← React (pages accueil/partie/création perso)
├── server/
│   ├── main.py             ← FastAPI + WebSocket + routes REST
│   ├── config.py           ← chargement config/config.yaml
│   ├── catalogue.py        ← armes/armures/équipement (+ poids PHB 3.5), dons, compétences
│   ├── persos.py           ← calcul des caractéristiques, charge, apparence (fiches)
│   ├── llm/                ← client LLM, orchestrator (boucle tools), prompt_builder
│   ├── game/               ← PartyState, moteur de combat, XP/niveaux (xp.py, combat.py)
│   ├── tools/              ← dés, état, fiches, monstres, cartes, manuels, scénarios,
│   │                         inventaire, progression, mémoire de campagne, voyage
│   ├── rag/                ← chunker, embeddings, ChromaDB store, CLI ingestion
│   ├── image/              ← workflows ComfyUI + helpers génération
│   └── prompts/            ← SystemPrompt MJ + sections dynamiques par phase
├── cartes/                 ← cartes de référence servies aux joueurs
├── config/                 ← config.example.yaml (+ config.yaml gitignored)
├── knowledge_import/       ← corpus RAG local (gitignored, apportez vos textes)
└── screenshots/            ← captures pour ce README
```

## 🔒 Note juridique

Ce projet est un **outil de table** non officiel. Il ne distribue aucun contenu protégé :
déposez vous-même vos textes de règles dans `knowledge_import/` et vos PDF dans
`server/data/manuels/`. Donjons & Dragons est une marque de Wizards of the Coast.
