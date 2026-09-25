# ⚡ Rapport d'optimisation — Rapidité des tours (Maître du Jeu IA)

**Date** : 25 septembre 2026
**Méthode** : mesures en conditions réelles (tours de jeu joués, timeline des logs serveur `docker logs dnd35-mj`, timestamps httpx), lecture du pipeline (`main.py`, `orchestrator.py`, `config.yaml`, `docker-compose.yml`).

---

## 📏 Mesures relevées

### Timeline d'un tour SIMPLE (exploration, sans combat) — mesuré
| Étape | Durée mesurée | Détail |
|---|---|---|
| RAG (embeddings) | ~0,2 s | 1 appel `llamaembed` |
| Reload du modèle | quasi instantané | `models/load` 200 immédiat (modèle en page-cache OS) — mais **contention observée** (400 « already loading ») quand ComfyUI/sortie de tour se chevauchent |
| **Appel LLM n°1** (boucle tools, itération 1) | **~19,6 s** | system prompt + historique + sections + outils (32k ctx) |
| **Appel LLM n°2** (narration finale / itération 2) | **~6,8 s** | réutilise le contenu de l'appel 1 si `stream_to_clients: false` (déjà optimisé) |
| Post-tour (patches, images en arrière-plan) | ~1 s | non bloquant |
| **TOTAL tour simple** | **~28-32 s** | mesuré 54 s sur un tour avec rattrapage inventaire (rejeu LLM supplémentaire) |

### Tours de COMBAT — mesuré (sessions de beta test)
- Tour de combat à 2 monstres : **~2 min** (6 itérations d'outils + relances `tool_choice='required'` + narration des mécaniques)
- Tour de combat complexe (victoire + XP + clôture) : **jusqu'à 4 min**
- Chaque round rejoué = 1 appel LLM narratif dédié supplémentaire (mécaniques serveur)

### Le joueur perçoit
- `stream_to_clients: false` → **rien ne s'affiche pendant TOUT le tour** (bloc unique à la fin) — la perception de latence est la latence réelle.

---

## 🎯 Leviers d'optimisation (classés par gain / effort)

### 1. 🔥 Activer le streaming (perception : latence réelle ÷ 2 ressentie) — effort : 1 ligne
`llm.stream_to_clients: true` dans `config/config.yaml`. L'architecture le supporte déjà (deltas token par token, watchdog 90/300 s, `stream_reset` pour les rejeux). Le texte apparaît **progressivement** pendant que le LLM génère : le joueur voit l'histoire s'écrire au lieu de fixer « Le MJ réfléchit… » pendant 30 s-4 min.
- **Coût** : un appel LLM supplémentaire pour la narration finale ? **Non** — l'orchestrateur réutilise déjà le contenu de l'appel streamé (optimisation présente, cf. commentaire 4289-4302 de `main.py`).
- **Gain perçu** : majeur — c'est le levier n°1 pour l'expérience joueur.

### 2. 🔥 Garder le modèle en VRAM entre les tours (gain : ~5-20 s/tour) — effort : 1 ligne
`llm.unload_after_turn: false` + `llm.unload_delay_minutes: 5` (délai au lieu d'unload immédiat). Le unload actuel retire le modèle de la VRAM **à chaque tour** pour ComfyUI ; le tour suivant le recharge (contention 400 « already loading » observée, et le reload coûte 5-20 s quand le fichier n'est plus en page cache).
- Avec `unload_delay_minutes`, les tours consécutifs **ne rechargent pas** (déjà supporté, cf. commentaire 6301-6308 de `main.py`).
- **Arbitrage** : la VRAM reste occupée 5 min — ComfyUI (génération d'images) peut attendre plus de VRAM. Si ComfyUI et les tours cohabitent mal, garder l'unload immédiat mais **stagger** : retarder l'unload de 30 s (le temps de voir si un tour reprend).

### 3. ⚡ Réduire le contexte (gain : ~20-40 % du prefill) — effort : moyen
`llm.max_context_tokens: 32768 → 16384` (et `-c 16368` côté llamacpp). Le prefill d'un contexte 32k sur un 9B local coûte plusieurs secondes **à chaque appel LLM** (×2-6 itérations par tour). L'historique est déjà borné (10000 chars, « 15 messages anciens omis » observé), les sections sont déjà filtrées par phase — le gros du contexte est le system prompt + la bible de scénario + la carte.
- **Risque** : troncature d'informations longues (scénarios à 4 parties, mémoire de campagne) — à tester avec le scénario le plus gros.
- **Mesure recommandée avant** : logger la taille réelle du prompt construit (1 ligne dans `prompt_builder.build_system_message`).

### 4. ⚡ Speculative decoding (gain : ~1,6-2x sur la génération) — effort : moyen (matériel)
Le `docker-compose.yml` le mentionne déjà en commentaire : `--spec-type draft-mtp --spec-draft-n-max 4` (fork XHToken « llamacpp-spark » déjà construit localement). Qwen3.5 supporte MTP (Multi-Token Prediction).
- **Gain** : la GÉNÉRATION (~6,8 s de narration) passe à ~3-4 s ; les itérations de tools aussi.
- **Prérequis** : un modèle draft compatible (MTP embarqué dans le GGUF Qwen3.5 — vérifier la variante téléchargée).

### 5. ✅ Réduire les itérations de tools (gain : ~5-20 s/tour en combat) — effort : moyen
`llm.max_tool_iterations: 6 → 4` (config actuelle : 6). Les tours qui épuisent la boucle (6 itérations + fallback) sont ceux qui durent 2-4 min. Un tour nominal en attaque = 3 appels (attaque, dégâts, terminer). À 4 itérations + 1 relance, le pire cas reste couvert.
- **Risque** : les tours complexes (plusieurs monstres + sorts) peuvent rester incomplets — la relance `tool_choice='required'` et les rattrapages serveur compensent déjà.

### 6. ✅ Narration des mécaniques : regrouper et borner (déjà fait, à vérifier en jeu) — effort : nul
`_narrer_mecaniques_serveur` regroupe TOUS les events d'un round en 1 appel (timeout 90 s). Les rounds de combat à plusieurs monstres génèrent plus d'events mais 1 seul appel — déjà optimal. Le repli bloc brut (sans LLM) est instantané.

### 7. 💡 Prompts plus courts pour les itérations 2+ (gain : ~30 % du prefill des itérations) — effort : moyen
La boucle tools réinjecte le system prompt COMPLET à chaque itération. Une version allégée (SystemPrompt_MaitreDuJeu_ALLEGE.md existe déjà dans `server/prompts/` !) pour les itérations > 1 (où les instructions longues ont déjà été suivies) réduirait le prefill de chaque itération. À câbler dans l'orchestrateur.

### 8. 💡 Modèle plus léger (gain : x1,5-3 sur tout) — effort : config (qualité en baisse)
`gemma-4-E4B-it-Q4_0` (~2,5 Go, mentionné dans le README) ou Qwen3.5 en Q4_0 plus léger. Gain de vitesse important, mais la qualité de narration/ suivi d'outils baisse (le README documente que le 9B a été choisi pour ça). **Non recommandé** sauf table très patiente sur le confort.

---

## ✅ Déjà optimisé (constaté, rien à faire)

- **Narration finale réutilisée** quand le streaming est coupé (économie d'un appel LLM complet par tour, ~la moitié de la latence de l'étape finale).
- **Mécanique d'abord, prose ensuite** : les tours de monstres/XP/clôtures sont résolus par le moteur serveur (déterministe, 0 LLM) — seul le récit coûte des tokens.
- **Sections dynamiques par phase** + historique borné (10000 chars) + RAG optionnel rapide (0,2 s).
- **Watchdog anti-blocage** (90/300 s) + repli bloc brut : un tour ne peut plus rester figé indéfiniment.
- **Génération d'images en arrière-plan** (jamais bloquantes) + cache (portrait/fiche).
- **Unload groupé** quand plus aucun tour n'est actif (compteur global `_turn_begin/_turn_end`).

---

## 🛠️ Plan recommandé (par ordre d'application)

1. **`stream_to_clients: true`** — 1 ligne, gain perçu majeur, zéro risque.
2. **`unload_after_turn: false` + `unload_delay_minutes: 5`** — 2 lignes, gain 5-20 s/tour (arbitrage VRAM/ComfyUI à valider).
3. **Logger la taille du prompt** puis, si > ~16k tokens, **`max_context_tokens: 16384`** — gain ~20-40 % du prefill, à valider sur le scénario le plus lourd.
4. **`max_tool_iterations: 4`** — gain en combat, rattrapages déjà en place.
5. **Speculative decoding MTP** si le matériel le permet — gain x1,6-2 sur la génération.
6. (Optionnel) Prompts allégés pour les itérations 2+.

**Gain attendu cumulé (1+2+4) : tour simple ~30 s → ~12-18 s ; tour de combat ~2 min → ~50-70 s — et surtout, le texte s'affiche dès les premières secondes (streaming).**

---

## 📎 Annexes

- **Timeline brute mesurée** (tour simple, 20:04) :
  ```
  20:04:03,355  RAG embeddings (0,2 s)
  20:04:03,554  models/load → 200 OK immédiat + "model loaded"
  20:04:23,199  chat/completions n°1 terminé (19,6 s)
  20:04:23,207  models/load → 400 (unload contention)
  20:04:29,990  chat/completions n°2 terminé (6,8 s) — narration finale
  ```
- **Fichiers concernés** : `config/config.yaml` (stream, unload, itérations, contexte), `docker-compose.yml` (flags llamacpp, MTP en commentaire), `server/llm/orchestrator.py` (boucle tools), `server/main.py` (post-tour, unload).
- **Mesures de session** : tour simple 54 s (avec rattrapage inventaire), tour combat 2 monstres ~2 min, victoire complète ~4 min.
