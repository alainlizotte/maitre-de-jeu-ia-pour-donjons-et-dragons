# 🐉 Maître du Jeu IA — Donjons & Dragons 3.5

### PROJET EN COURS DE DÉVELOPPEMENT ###

Application web **multijoueur autonome** qui joue le rôle de **Maître du Jeu (MJ)** pour une
table de Donjons & Dragons 3.5 : un LLM local (Gemma 4 ou Qwen 3.5, via **llama.cpp** par
défaut ou Ollama) narre l'aventure, mais **la mécanique est décidée par le serveur** —
vrais jets de dés via des tools Python, **combat tour par tour conforme aux règles 3.5**
(initiative, monstres, mourants, coups de grâce, XP DMG officielle), **carte de donjon
synchronisée avec la narration**, mémoire de campagne persistante et génération d'images
(monstres, portraits, scènes) via ComfyUI.

Aucun service externe ni compte requis : tout tourne en local (Docker), les joueurs se
connectent depuis leur navigateur sur le réseau local.

| | |
|---|---|
| **Backend** | FastAPI + WebSocket, **68 tools Python** (function-calling) |
| **Frontend** | React 18 + TypeScript + Vite 6 + Tailwind 4 |
| **LLM** | llama.cpp (par défaut) / Ollama (OpenAI-compatible) — Gemma 4, Qwen 3.5… |
| **Règles** | Moteur de combat serveur + XP/niveaux 3.5 officiels (DMG) ; sorts, repos, voyage SRD |
| **Qualité** | **200 tests pytest** (moteur de combat, carte, fiches, scénarios, E2E sans LLM) |
| **Images** | ComfyUI (monstres, portraits, salles, scènes) — optionnel |
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
  tools Python. Un « jet simulé » par le LLM est **détecté et corrigé** automatiquement.

### Combat tour par tour (décidé par le serveur, narré par le LLM)
- **Initiative officielle respectée dès le round 1** : si un monstre gagne l'initiative, le
  serveur joue son tour **avant** l'action déclarée par le joueur ; le joueur ne peut agir
  que lorsque c'est son tour (garde de tour par joueur).
- **Rotation 100 % serveur** : attaques automatiques des monstres et alliés invoqués (fiche
  du bestiaire), passes des combattants incapables, **stabilisation officielle des mourants**
  (1d20/round), **coups de grâce** sur cible à terre, clôture automatique (victoire/défaite).
- **XP et niveaux officiels (DMG 3.5)** : chaque PJ vivant gagne l'XP de la table
  « Experience Point Awards » pour chaque ennemi vaincu, selon son propre niveau ; montées
  de niveau (jet de dé de vie) et pertes de niveau (energy drain) automatiques.
- **Rattrapages serveur** : un combat narré en prose sans `engager_combat`, des dégâts jetés
  mais non appliqués, une invoquation oubliée, un sort de soin sans tool… sont détectés et
  régularisés mécaniquement — la narration ne peut plus créer de fiction sans effet.
- **Barres de progression visibles** : XP (niveau actuel → suivant) et **charge transportée**
  (poids kg / charge max) sur les cartes PJ en jeu, les fiches et l'accueil.

### Exploration & carte du donjon (synchronisation garantie)
- **Carte du donjon procédurale rendue en SVG** : salles explorées, portes, étages
  (rez-de-chaussée → sous-sols), position du groupe en direct.
- **La carte est injectée dans le prompt du MJ** (salle courante, portes réellement
  ouvertes, descriptions figées des salles) : la narration ne peut plus inventer une porte
  ou une salle absente — `carte_donjon_explorer` refuse toute direction sans porte en
  listant les portes existantes.
- **Descriptions canoniques figées** par salle (protectées contre la réinvention) : en
  revenant sur ses pas, le groupe retrouve la salle **à l'identique** (décor + état des
  lieux : monstres vaincus, coffres vidés…), y compris après sortie/retour du donjon.
- **Voyage hors donjon** conforme au SRD : durée réelle (vitesse, terrain, marche forcée),
  rencontres aléatoires, risque de s'égarer et météo.
- **Carte du monde interactive** (Côte des Épées / Faerûn) : position du groupe par ville
  repère.

### Campagne, scénarios & magie
- **Choix du scénario depuis l'interface** (catalogue : Laelith, Royaumes Oubliés…) ; une
  **bible de scénario** (résumé, PNJ, ennemis, étapes) est réinjectée à chaque tour pour
  garder le MJ sur la trame — réinterprétée en règles 3.5 même si le module source est 5e.
- **Mémoire de campagne persistante** : missions, lieux, PNJ, monstres combattus (remplie
  automatiquement à chaque victoire) et position du groupe — réinjectée dans le prompt du MJ
  pour une cohérence longue durée.
- **Magie D&D 3.5** : incantation validée (classe, niveau, emplacements de sorts,
  préparation/mémorisation), repos long officiel (PV + sorts restaurés).
- **Inventaire & encombrement (PHB 3.5)** : poids officiels par objet, charge recalculée
  (Légère/Moyenne/Lourde/Dépassée), consommation de munitions.
- **Bestiaire étendu (339 monstres)** consultable, avec fiche détaillée ; tout combat est
  engagé contre une créature **du bestiaire officiel** (créatures inventées refusées).

### IA générative (ComfyUI — optionnel, désactivé par défaut)
- **Images de monstres générées automatiquement** dès leur apparition dans la narration
  (cache par fiche : l'image ne change pas tant que la fiche ne change pas) — en arrière-plan,
  sans jamais bloquer le tour de jeu.
- **Portraits de personnages** générés à la création ; **illustrations de salles** de donjon
  (cache + scènes prégénérées) et **scènes marquantes** à la demande (désactivables à chaud
  via la galerie « Scènes »).
- **Scènes cousues d'avance** : images prégénérées servies instantanément quand la narration
  rejoint un lieu connu du scénario (zéro latence, zéro appel GPU).

### Base de connaissances (RAG — optionnel, désactivé par défaut)
- Les manuels D&D 3.5 (dépôt local `knowledge_import/`, textes OCR fournis par vos soins)
  sont vectorisés dans **ChromaDB** (embeddings via un serveur llama.cpp dédié,
  conteneur `llamaembed`) ; chaque message joueur injecte les extraits de règles
  pertinents dans le contexte du MJ → réponses fidèles aux règles.
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

Trois conteneurs : `llamacpp` (chat, GPU), `llamaembed` (embeddings RAG, port 8081) et
`dnd35` (API + frontend, port 8123).

Port modifiable : `set DND35_PORT=9000` (ou `.env`). Accès hors réseau local : redirection de
port sur votre box ou reverse proxy HTTPS devant le port 8123.

### Sans Docker

```bash
py -m pip install -r requirements.txt
cd client && npm install && npm run build && cd ..   # frontend → server/static/
py -m uvicorn server.main:app --port 8000            # → http://127.0.0.1:8000
```

## 🧪 Tests

200 tests déterministes (sans LLM ni GPU) couvrent le moteur de combat complet (initiative,
morts par étapes 0/-10 PV, XP, stabilisation), la carte du donjon (constance des salles,
refus des portes inexistantes, séquencement round 1), les fiches/sorts/inventaire, les
scénarios et le pipeline d'orchestration :

```bash
py -m pytest tests -q
```

## ⚙️ Configuration (`config/config.yaml`)

| Clé | Défaut | Rôle |
|---|---|---|
| `llm.backend` | `llamacpp` | `llamacpp` ou `ollama` (endpoint OpenAI-compatible) |
| `llm.base_url` | `http://localhost:8080/v1` | Endpoint du backend LLM |
| `llm.model` | `gemma-4-E4B-it-Q4_0` | Modèle de chat (Qwen 3.5, Mistral… supportés) |
| `llm.tool_mode` | `prompt` | `native` / `prompt` (balises `<tool>`) / `auto` |
| `llm.detect_simulation` | `true` | Corrige les « simulations » textuelles d'outils |
| `llm.unload_after_turn` | `true` | Libère la VRAM après le tour (ou délai `unload_delay_minutes`) |
| `game.combat_turn_timeout_seconds` | `300` | Passe automatiquement le tour d'un joueur silencieux |
| `rag.enabled` | `false` | Base de connaissances ChromaDB (règles D&D 3.5) |
| `rag.source_dir` | `./knowledge_import` | Corpus `.txt/.md` à ingérer |
| `rag.embedding_model` | `embeddinggemma` | Modèle d'embeddings dédié (llama.cpp, conteneur `llamaembed`) |
| `image.enabled` | `false` | Génération d'images ComfyUI (monstres, portraits, donjons) |
| `image.scenes_enabled` | `true` | Illustrations de scènes (désactivable à chaud dans la galerie) |
| `paths.data_dir` | `./server/data` | Parties, fiches, caches d'images, ChromaDB |

Variables d'environnement : `DND35_CONFIG` (chemin config), `DND35_PORT` (port hôte),
`COMFYUI_BASE_URL` (`http://host.docker.internal:8188` par défaut).

## 🧠 Comment ça marche

1. Le joueur écrit dans le chat → WebSocket `/ws/{partie_id}`.
2. Le **pre-run du moteur de combat serveur** fait d'abord avancer la mécanique : tours de
   monstres, skips des incapables, stabilisation, clôture éventuelle — aucun LLM n'intervient.
3. Le **PromptBuilder** assemble le system prompt : instructions MJ + état de partie
   (phase, combat, quête/bible de scénario, **carte du donjon**, mémoire de campagne) +
   sections dynamiques par phase + extraits RAG si actif.
4. L'**Orchestrator** boucle en function-calling : le LLM choisit parmi **68 tools Python**
   (`lancer_attaque`, `engager_combat`, `fiche_perso_*`, `carte_donjon_*`, `incanter_sort`,
   `inventaire_*`, `memoire_*`, …) **filtrés par phase de jeu** ; toute « simulation » de jet
   est détectée et rejetée.
5. Chaque tool touche l'état persistant JSON (partie, fiches, bestiaire) ; les patches
   résultants sont broadcastés à tous les clients (PV, XP, carte, charge mis à jour en direct).
6. Le **post-tour serveur** avance la rotation (action consommée = tour suivant), applique
   les rattrapages (dégâts oubliés, combat narré en prose, exploration, soins, inventaire),
   clôture le combat avec **XP officielle** et lance les générations d'images en arrière-plan.

## 📁 Structure

```
├── client/                 ← React 18 + TS + Vite (accueil, partie, création perso)
├── server/
│   ├── main.py             ← FastAPI + WebSocket + routes REST + rattrapages post-tour
│   ├── config.py           ← chargement config/config.yaml
│   ├── catalogue.py        ← armes/armures/équipement (+ poids PHB 3.5), dons, compétences
│   ├── persos.py           ← calcul des caractéristiques, charge, apparence (fiches)
│   ├── sorts.py            ← sorts 3.5 (niveaux, écoles, préparation)
│   ├── llm/                ← client LLM, orchestrator (boucle tools), prompt_builder
│   ├── game/               ← PartyState, moteur de combat (combat.py), XP/niveaux (xp.py)
│   ├── tools/              ← dés, état, fiches, monstres, cartes (monde + donjon),
│   │                         inventaire, manuels, scénarios, mémoire de campagne, voyage
│   ├── rag/                ← chunker, embeddings, ChromaDB store, CLI ingestion
│   ├── image/              ← workflows ComfyUI + helpers génération
│   ├── prompts/            ← SystemPrompt MJ (par phase) + sections dynamiques
│   └── data/               ← parties, fiches, bestiaire (339 monstres), caches, ChromaDB
├── cartes/                 ← cartes de référence servies aux joueurs
├── config/                 ← config.example.yaml (+ config.yaml gitignored)
├── knowledge_import/       ← corpus RAG local (gitignored, apportez vos textes)
├── scripts/                ← utilitaires (import bestiaire, scènes prégénérées, simulation)
└── tests/                  ← 200 tests pytest déterministes (combat, carte, fiches, E2E)
```

## 🔒 Note juridique

Ce projet est un **outil de table** non officiel. Il ne distribue aucun contenu protégé :
déposez vous-même vos textes de règles dans `knowledge_import/` et vos PDF dans
`server/data/manuels/`. Donjons & Dragons est une marque de Wizards of the Coast.
