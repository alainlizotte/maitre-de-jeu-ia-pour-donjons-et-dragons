# Licences tierces et mentions de propriété

Ce projet (code serveur, client et scripts) est distribué sous licence
**MIT** — voir [LICENSE](LICENSE). Il ne distribue **aucun modèle d'IA**,
**aucun texte ni artwork des ouvrages D&D** : chaque hébergeur fournit les
siens et en accepte les licences correspondantes.

---

## 1. Modèles d'IA (optionnels, non distribués, téléchargés par chaque hébergeur)

La génération d'images est **optionnelle et désactivée par défaut**
(`image.enabled: false`). Elle délègue à un serveur [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
installé séparément par l'hébergeur, qui télécharge lui-même le modèle de
son choix et en accepte la licence sur Hugging Face.

### ⚠️ Recommandation licence — Qwen-Image 1.0 ou FLUX.1-schnell

Pour un usage sans contrainte, privilégiez un modèle **permissif** :

| Modèle | Licence | Note |
|---|---|---|
| **[Qwen-Image (1.0)](https://huggingface.co/Qwen/Qwen-Image)** | **Apache 2.0** | Même famille que le 2.1 : les workflows fournis s'y adaptent en changeant les poids |
| **[FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)** | Apache 2.0 | Génération rapide, excellente qualité |

Le modèle **Qwen-Image-2.1 (et sa variante Turbo)** fonctionne très bien avec
ce projet, mais il est distribué sous la **Qwen Research License**
(recherche/évaluation uniquement, usage commercial sur licence séparée) —
chaque hébergeur doit vérifier que son usage respecte cette licence. Les
workflows fournis dans `server/image/workflows/` le référencent par défaut ;
ils restent adaptables en changeant les fichiers de modèles.

### Exemple de modèles référencés (configuration de l'auteur)

| Composant | Fichiers | Licence |
|---|---|---|
| Qwen-Image-2.1 Turbo (UNET) | `qwen_image_2.1_turbo_int8_convrot.safetensors` | [Qwen Research License](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) |
| VAE Qwen-Image-2.1 | `qwen_image_2.1_vae_bf16.safetensors` | Qwen Research License |
| Qwen3-VL-8B (text-encoder) | `qwen3vl_8b_int8_convrot.safetensors` | [Apache 2.0](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) |

### Qwen Research License — points essentiels

- Usage du modèle limité aux **fins de recherche et d'évaluation** ;
  tout usage commercial requiert une licence séparée de Qwen
  (contact : `model-business@notice.qwencloud.com`).
- **Les images générées (sorties du modèle) appartiennent à l'utilisateur**
  qui les produit — confirmation officielle de l'équipe Qwen. Le jeu ne
  revendique aucun droit sur elles et n'en embarque aucune.
- Notice d'attribution requise par la section 3.c de la licence, reproduite
  telle quelle :

  > Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT,
  > Copyright (c) 2026 Hangzhou Tongyi Laboratory Technology Co., Ltd.
  > All Rights Reserved.

- Si vous utilisez les sorties du modèle pour entraîner ou améliorer un
  autre modèle distribué, la section 4.b impose la mention « Built with
  Qwen » dans la documentation du produit concerné (hors périmètre de ce
  projet, qui n'entraîne aucun modèle).

### Aucune image pré-générée dans ce dépôt

Par prudence juridique, **ce dépôt ne distribue aucune image générée par
modèle** : les caches (`bestiaire_cache/`, `images_salles/`,
`images_scenes/`) sont ignorés par git et régénérables localement à la
demande (les sorties des modèles restent la propriété de leur auteur, mais
leur diffusion au public n'est pas un acte neutre pour un projet open
source — chacun apprécie). ⚠️ Les historiques de commit antérieurs à cette
décision peuvent encore contenir d'anciennes images pré-générées : une
purge d'historique (BFG / git filter-repo) reste possible si nécessaire.

## 2. Logiciels tiers (conteneurs et dépendances)

| Logiciel | Usage | Licence |
|---|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Serveurs LLM/embeddings (`llamacpp`, `llamaembed`) | MIT |
| [ChromaDB](https://github.com/chroma-core/chroma) | Vector store RAG | Apache 2.0 |
| FastAPI, Uvicorn, HTTPX, Websockets | Serveur HTTP/WebSocket | BSD-3 |
| Pydantic, jsonschema, pytest | Validation/tests | MIT |
| PyYAML | Configuration | MIT |

ComfyUI (installé séparément par l'hébergeur s'il souhaite la génération
d'images) est sous **GPL-3.0** — il n'est ni inclus ni modifié par ce projet.

## 3. Contenu Donjons & Dragons

- **Règles** : le moteur implémente les mécaniques du **SRD 3.5**
  ( Wizards of the Coast, sous [OGL 1.0a](https://media.wizards.com/2016/downloads/DND/SRD-OGL_V5.1.pdf))
  et du DMG — le code ne reproduit aucun texte des ouvrages.
- **Manuels** (RAG) : textes OCR des ouvrages D&D 3.5 **fournis par
  l'hébergeur** dans `knowledge_import/` (non distribué, ignoré par git).
  © Wizards of the Coast — usage personnel.
- **Cartes de référence** (`cartes/`, non distribué) : cartes officielles
  Faerûn/Toril/Outreterre — © Wizards of the Coast, fournies par
  l'hébergeur. Voir le README pour l'emplacement attendu.
- **Carte de Laelith** (`server/data/scenarios/Laelith/Cartes/laelith.jpg`,
  non distribué) : plan de la Cité Sainte — © Laelith (Donjon du Dragon,
  donjondudragon.fr), fourni par l'hébergeur. Les repères de
  `server/tools/reperes_laelith.py` (noms de lieux + %) en sont dérivés
  pour un usage personnel.
- **Scénarios** (`server/data/scenarios/`, non distribué) : aventures
  tierces et leurs artworks, propriété de leurs auteurs respectifs.
- **Bestiaire** (`server/data/bestiaire.json`) : données de jeu dérivées
  des ouvrages D&D 3.5 (SRD + Manuel des Monstres), destinées à un usage
  personnel — © Wizards of the Coast pour les textes sources.
- **Portraits de monstres** (`server/data/bestiaire_cache/`) : images
  générées localement par l'hébergeur avec ses propres modèles — sorties
  lui appartenant ; rien n'est pré-généré dans ce dépôt.
