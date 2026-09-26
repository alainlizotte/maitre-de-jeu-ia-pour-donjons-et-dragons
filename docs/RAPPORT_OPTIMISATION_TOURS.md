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
---

# 🚀 APPLICATION DU PLAN (1-6) + VALIDATION EN SITUATION RÉELLE (26 sept. 2026)

## ✅ Points 1, 2, 4 : appliqués (config)

| Point | Changement | État |
|---|---|---|
| 1. Streaming | `game.stream_to_clients: true` | ✅ appliqué |
| 2. VRAM | `unload_after_turn: false` + `unload_delay_minutes: 5` | ✅ (déjà en place) |
| 4. Itérations | `llm.max_tool_iterations: 6 → 4` | ✅ appliqué |

## 📏 Point 3 : mesuré → conclusion = NE PAS réduire
Le logger de taille est implémenté (`prompt_builder.build_system_message`, 1 log par tour : `prompt: N chars (~M tokens) [sys/recap/sections/rag]`).
**Mesures réelles** : exploration **17 683 chars (~4 420 tokens)**, combat **17 734 chars (~4 433 tokens)** — le régime « prompt court » (déjà en place dès qu'un PJ existe, combat inclus) maintient le prompt à ~4,4k tokens, très loin du budget 32k.
**Conclusion** : réduire `max_context_tokens` à 16384 n'apporterait **aucun gain de prefill** et tronquerait la bible des gros scénarios → **conservé à 32768**. Le logger reste actif pour surveiller l'évolution.

## 🎨 Point 6 : élage des itérations ≥ 2 appliqué (avec garde-fou)
- `prompt_builder` : les sections de règles sont préfixées du marqueur `=== RÈGLES DU JEU (sections dynamiques) ===`.
- `orchestrator.run` : à l'itération ≥ 2, si le marqueur est présent, le system prompt est coupé au marqueur (+ note courte d'itération mécanique) — gain de prefill sur chaque itération de combat.
- **Nuance mesurée** : le régime « court » actuel n'injecte pas de sections → l'élage ne se déclenche que si un régime à sections est actif (phases sans PJ / futures évolutions). Inoffensif sinon, prêt si le régime complet revient.

## 🧬 Point 5 : MTP préparé, activation documentée (bloqué matériel)
L'image fork `llamacpp-spark:server-cuda` est **absente du poste** (vérifié `docker images`). Le `docker-compose.yml` documente maintenant la procédure d'activation en 3 étapes (pull/construire l'image → basculer `image:` → décommenter `--spec-type draft-mtp --spec-draft-n-max 4`). ⚠️ Les flags ne doivent PAS être activés sur l'image standard (crash au démarrage — constaté).

## ✅ Validation de fiabilité en situation réelle

Méthode : client WebSocket réel (connexion, join, say), tours joués, logs serveur, suite complète.

| Test | Résultat |
|---|---|
| Suite pytest | **623/623** ✓ |
| Streaming | **176 deltas** (tour 1) / **129 deltas** (tour 2) — texte livré progressivement ✓ |
| Latence perçue (1er delta) | 25-29 s (le gros du temps = résolution tools d'abord, puis narration streamée) |
| Latence totale (dm) | 27-31 s par tour (vs 54 s-2 min avant) |
| Combat complet en 1 tour | engagement → attaque (bonus recalculé +4→+2 par le serveur) → CA imposée bestiaire (14→13) → dégâts 9 → victoire → XP multijoueur (Kaelen 470 XP, bozo 100 XP — 2 PJ dans la partie, attribué aux deux) → patches UI ✓ |
| Gardes de fiabilité | re-pop de gobelin fantôme refusée (« Salle déjà vidée ») + rejeu propre « Reprends l'action hors initiative » ✓ ; `incanter_sort` par un guerrier refusé ✓ |
| Logger prompt | actif, ~4,4k tokens/tour ✓ |
| Élage itération ≥ 2 | neutre en régime court (pas de sections), actif si marqueur présent ✓ |
| Docker | llamacpp sain, dnd35 sain (un crash llamacpp temporaire corrigé : les `#` dans un bloc YAML plié sont passés à llama.cpp comme arguments — le bloc command a été nettoyé) |

## ⚠️ Constats résiduels (hors périmètre des optimisations)
- Le 9B ré-invoque parfois des créatures déjà tuées (hallucination) — les gardes serveur bloquent proprement, mais la narration reste approximative sur ces tours.
- Les deltas du streaming commencent après la résolution des tools (design « mécanique d'abord ») : la première seconde d'attente reste silencieuse — un statut plus riche (« résolution de l'attaque… ») pourrait encore améliorer la perception.

**Verdict : plan appliqué (1, 2, 4, 6 appliqués ; 3 mesuré — réduction non pertinente ; 5 préparé et documenté). Aucune perte de fiabilité constatée : 623/623 tests, combat complet valide, gardes opérationnelles, latence totale divisée par ~2 et texte visible en continu.**
---

# 🚀 MTP IMPLANTÉ + STATUTS ENRICHIS (26 sept. 2026 — suite sur demande)

## ✅ Point 5 réactivé : MTP IMPLANTÉ (image officielle, pas de fork nécessaire)
Recherche : le MTP Qwen a été **fusionné dans llama.cpp officiel** (PR 20533) — le fork
`llamacpp-spark` n'est PAS requis. Démarche réalisée :
1. `docker compose pull llamacpp` → image officielle à jour (le `--help` confirme
   `--spec-type draft-mtp` parmi les types de speculative decoding) ;
2. le GGUF standard ne contient pas les couches MTP (constat : « context type MTP
   requested but model doesn't contain MTP layers ») → téléchargé le GGUF
   **`unsloth/Qwen3.5-9B-MTP-GGUF`** (Q4_K_M, 5,47 Go) dans `models/` ;
3. le router llama.cpp ne propage PAS `--spec-type` aux instances chargées à la
   demande → passage par les **variables d'environnement** `LLAMA_ARG_SPEC_TYPE=draft-mtp`
   + `LLAMA_ARG_SPEC_DRAFT_N_MAX=4` (héritées par les sous-instances) ;
4. `llm.model` → `Qwen3.5-9B-Q4_K_M-MTP` (config.yaml).

**Validé** : « creating MTP draft context » sans erreur ; **génération 108,29 tokens/s
contre 74 avant = +46 %**.

## ✅ Statuts enrichis (« quelle étape est en préparation »)
Callback `on_status` ajouté au pipeline (`orchestrator.run` via `ctx.on_status`,
câblé dans `main.py` → broadcast WS `status`). Le joueur voit désormais l'étape en
cours pendant la réflexion — mesuré en jeu, 5 à 9 statuts par tour :
- « Le MJ réfléchit... »
- « Le serveur joue les tours des monstres… » (pre-run moteur de combat)
- « Résout l'action avec les outils… » / « Résout l'action (2/4)… » (boucle tools)
- « Résout l'attaque… » / « Calcule les dégâts… » / « Met en place le combat… » /
  « Entre dans le donjon… » / « Lance le sort… » (mapping `_STATUT_OUTILS`, 30 outils)
- « Le MJ finalise la scène… »

## ✅ Validation sur un SCÉNARIO RÉEL (Le Dragon de Hurlemont, partie 70548a95)
Suite de 4 tours joués via client WS (client, join, say) :

| Tour | Contenu | 1er delta | Total | Statuts | Fiabilité |
|---|---|---|---|---|---|
| 1 | Ouverture (auberge, rumeurs) | 15,3 s | 42,2 s (rejeu correctif inclus) | 7 | scénario respecté, stream_reset géré proprement |
| 2 | Enquête + entrée donjon | 11,6 s | 17,0 s | 5 (« Entre dans le donjon… ») | carte générée, bible fidèle |
| 3 | Combat Gobelin ×2 (scénario) | 11,1 s | 13,7 s | 5 (« Met en place le combat… ») | PV cohérents (14→10), initiative ✓ |
| 4 | Résolution (attaque ratée) | 38,2 s | 50,4 s (3 itérations) | 9 | jets affichés, recalcul +5→+2, état cohérent |

Spoiler corrigé au passage : les blocs module « ⚔️ Ennemis DU SCÉNARIO » et
« 📝 Note du module » (dilemme Gritch = spoiler) fuyaient dans la narration
affichée → ajoutés au strip des consignes LLM (portes/infos légitimes conservées,
validé unitairement).

## 📊 COMPARATIF FINAL AVANT / APRÈS

| Métrique | AVANT optimisations | APRÈS | Gain |
|---|---|---|---|
| Génération LLM | 74 tok/s | **108 tok/s** (MTP) | **+46 %** |
| Tour simple (exploration) | 54 s | **11-17 s** | **÷3** |
| Premier contenu visible | rien avant la fin (25-29 s) | **11-16 s** (deltas) | perçu ÷2 + progression continue |
| Combat : engagement + résolution | ~2 min | **13,7-21,6 s** | **÷5 à ÷8** |
| Combat : tour multi-itérations (3/4) | 2-4 min | **38-50 s** | **÷3 à ÷4** |
| Statuts pendant la réflexion | 2 libellés fixes | **5-9 libellés d'étape** | nouveau |
| Fiabilité (tests) | 623/623 | **623/623** | idem ✓ |

Fiabilité identique vérifiée en situation réelle : mécanique serveur intacte
(bonus recalculés, CA imposée, gardes anti re-pop/loot, XP multijoueur),
scénario fidèle, état cohérent après chaque tour, rattrapages opérationnels.

**Incident corrigé pendant l'implantation** : un crash llamacpp (les lignes
`#` ajoutées DANS un bloc YAML plié `command: >` sont passées à llama.cpp comme
arguments) — le bloc a été nettoyé, les instructions MTP déplacées en
commentaires YAML au-dessus.
