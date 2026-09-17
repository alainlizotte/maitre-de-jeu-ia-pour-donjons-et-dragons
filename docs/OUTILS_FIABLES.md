# Appels d'outils fiables à 100 % — Qwen3.5-9B + llama.cpp

> Recette validée le 15/09/2026 sur le projet D&D 3.5 (MJ LLM), réutilisable
> pour tout projet combinaisant **llama.cpp server (`--jinja`)** + un modèle
> **Qwen3.5** (testé : `Qwen3.5-9B-Q4_K_M`) + le tool-calling OpenAI.

## Résultats mesurés

| Couche | Avant | Après |
|---|---|---|
| Arguments JSON valides (contexte court) | ~85 % | **100 %** (24/24) |
| Arguments JSON valides (contexte long, ~68 outils) | ~63 % | **100 %** (24/24) |
| Erreurs de format en vraie partie (80 appels exécutés) | fréquentes | **0** |
| Outil canonique appelé (actions mécaniques E2E) | ~40 % | **~100 %** quand l'outil est exposé et l'état du jeu le permet |
| Overhead reasoning (`<think>`) par appel | 130–1 576 chars | **0** |

---

## La recette (par ordre d'impact)

### 1. Laisser le modèle dans son FORMAT NATIF (le plus important)

**Ne PAS forcer `tool_call_format: "json"` (Hermes) sur Qwen3.5.**

Mesure en contexte long (68 schémas d'outils, system prompt réaliste) :

| Config | Args valides | Verdict |
|---|---|---|
| Natif (XML `<tool_call><function=…>`) | **24/24** | Le format est gravé dans le training |
| Hermes JSON forcé (`chat_template_kwargs`) | 15/24 | Le modèle CONTAMINE les valeurs JSON avec du XML natif : `{"nom":"Throkmar</parameter>\n<parameter=degats>\n5</parameter>"}` + appels SANS arguments |

Le modèle revert toujours vers son format d'entraînement ; un format
artificiel produit des hybrides invalides. C'est le renversement de
l'intuition initiale (issue llama.cpp #20837 laissait penser qu'il fallait
sortir du XML natif).

### 2. Corriger les résidus CÔTÉ APPLICATION (filet de sécurité)

Même en format natif, le 9B-Q4 fait ~4 % de débordements. Sans coût :

- **Nettoyage des arguments** (`_nettoyer_args_outils`, `server/llm/orchestrator.py`) :
  avant chaque invocation, garder le préfixe sain avant la première balise
  (`"Throk'mar</parameter>…"` → `"Throk'mar"`), strip des quotes parasites
  (`"1d20\""` → `"1d20"`), retirer les champs devenus vides.
- **Récupération des appels cachés dans le thinking** (`_segments_thinking`
  + étape Bbis0) : Qwen3.5 émet parfois l'appel DANS `<think>…</think>` ;
  llama.cpp ne le parse pas (#20837) et le strip-thinking le détruirait.
  Extraire depuis le contenu BRUT avant suppression, dédupliquer contre les
  appels natifs.
- **Extracteurs de secours** (déjà présents) : `<tool_call>{JSON}</tool_call>`,
  `<function=…><parameter=…>`, balises `<tool name="…">` en prose —
  silencieux quand le natif marche, salvateurs quand le modèle dévie.

### 3. `enable_thinking: false` DANS LA REQUÊTE

`think: false` côté config ne suffit pas : il faut le passer au template.

```json
"chat_template_kwargs": {"enable_thinking": false}
```

Effet mesuré : 0 token de reasoning (vs 130–1 576 chars par appel),
~15–25 % plus rapide, et moins de confusion de format (le thinking est un
terrain de contamination XML→JSON).

### 4. N'enseigner qu'UN SEUL canal d'appel dans le prompt

Ancien prompt (héritage Gemma) : « deux formats valides : tool_calls natif
OU balise `<tool name="…">` ». Résultat : le modèle MÉLANGE les syntaxes.
Nouveau prompt (`tools_prompt_compact`, `server/tools/registry.py`) :
natif uniquement + interdictions explicites (prose `nom(key=…)`, résultat
inventé, placeholders). Les extracteurs texte restent actifs côté serveur
comme parachutes, mais le prompt ne les mentionne plus.

### 5. Router les intentions vers l'outil CANONIQUE

Un 9B choisit des outils voisins s'il doit deviner (`repos_long` pour une
potion, `lancer_d20` pour une attaque, `etat_partie_patch` pour l'XP).
Bloc de routage dans le prompt compact (~15 lignes) :

```
- dégâts SUBIS par un PJ → fiche_perso_infliger_degats
- soin ponctuel (potion, sort) → fiche_perso_soigner (pas repos_long)
- gain d'XP → fiche_perso_gagner_xp
- repos_long UNIQUEMENT si le joueur annonce une nuit (8 h)
```

Gain mesuré : sélection canonique de ~40 % à ~90-100 % en partie réelle.

### 6. EXPOSER les outils nécessaires à la phase courante

Cause la plus sournoise : le filtrage d'outils par phase
(`_PHASE_TOOLS`, `server/llm/orchestrator.py`) cachait
`fiche_perso_infliger_degats/soigner/gagner_xp`, `inventaire_ramasser/ajouter`
en phase exploration. **Un modèle ne peut pas appeler un outil masqué** —
il improvise avec ce qu'il voit (jets de dés génériques, patches d'état).
Règle : tout ce qui peut arriver dans une phase doit y avoir son outil
canonique, même si le set grossit (le routage du point 5 compense).

### 7. Ce qui NE marche PAS (mesuré)

- `tool_choice: "required"` sur llama.cpp : force un appel mais ne
  contraint PAS les arguments (pas de grammaire sur le JSON dans cette
  version) — 4/24 args vides quand même.
- Template corrigé `froggeric/Qwen-Fixed-Chat-Templates` v22.5
  (`--chat-template-file`) : corrige l'abandon de tour (~80 %) et le
  poisoning du think en contexte court, MAIS injecte son propre bloc
  `# Tools` qui concurrence la documentation d'outils de l'application, et
  son mode Hermes dégrade les arguments en contexte long. Testé et écarté
  pour ce projet (le fichier reste dans `models/` si besoin).

---

## Anti-simulation : le complément indispensable

Un 9B **narre** parfois le résultat au lieu d'appeler (« Throkmar récupère
5 PV » sans `fiche_perso_soigner`). Le garde serveur `looks_like_simulation`
(`server/llm/orchestrator.py`) détecte la prose → relance avec correctif
ciblé nommant l'outil canonique. Couvertures :

- dégâts en prose (« inflige 12 dégâts », « +2 dégâts », « PV restant : 7 »)
- jets récités (« jet d'intimidation (18/15) », « 1d20+5 = 18 »)
- **gains d'état** (ajout 09/2026) : soins, XP, inventaire —
  `_GAIN_PROSE_PATTERNS` ; désactivés quand l'outil canonique a déjà tourné
  (`_GAIN_TOOLS`) pour tolérer la reformulation légitime.

Déclenché 7× en 2 parties E2E, chaque relance a abouti à l'appel réel.

## Phase de décision contrainte (09/2026 — partie abd81275)

L'anti-simulation rattrape, mais au prix de rejeux lents. Le correctif
structurel : **décider AVANT de narrer** (`server/llm/orchestrateur.py`,
`_phase_decision`, flag `game.decision_phase`).

1. **Appel de décision court** (~2 k chars : état minimal + outils
   mécaniques éligibles) avec `response_format: json_schema` — llama.cpp
   compile le schéma en grammaire et **masque les logits** : un nom d'outil
   hors `enum` ou une réponse en prose devient impossible au niveau des
   tokens (contrairement à `tool_choice: "required"`, dont l'application
   dépend du template — issue llama.cpp #27217).
2. **Exécution serveur** des outils retenus (max 2) via le pipeline
   existant (garde escalier, monstres, budget par tour).
3. **Boucle narrative normale**, nourrie des résultats officiels injectés.

Périmètre : exploration/voyage/roleplay, outils d'action du monde
(`_OUTILS_DECISION`) — PAS le combat (moteur serveur déterministe déjà en
place). Repli transparent sur la boucle historique au moindre pépin
(backend sans support, réponse non parsable) : zéro régression.

Validé en E2E réel (Qwen3.5-9B + llama.cpp) : « je vais à l'est » →
décision contrainte → `carte_donjon_explorer(est)` exécuté → salle (1,0)
atteinte → narration fondée sur le résultat officiel.

## Contexte borné : work + schémas d'outils (09/2026 — partie 5a9b99c8)

Symptôme : « le texte du MJ est tronqué » / « problème technique » en pleine
scène. Cause mesurée dans les logs llama.cpp : `request (21058 tokens) exceeds
the available context size (20224 tokens)`. Le budget de tour ne comptait que
`work`, alors que les **schémas d'outils natifs** (function-calling « auto »)
sont envoyés HORS de `work` : 39 schémas ≈ 20,7 k chars. Quand la requête
dépasse le ctx, soit llama.cpp renvoie 400, soit il **tronque lui-même le
début du prompt** — le system prompt (et ses règles) disparaît, et la
narration sort courte, coupée en plein mot.

Correctifs (`server/llm/orchestrator.py`, `server/tools/registry.py`,
`docker-compose.yml`) :

1. `_borner_work(work, reserve_chars=…)` réserve la place des schémas dans le
   budget : le TOKEN TOTAL de la requête reste sous le ctx du serveur.
2. Budget `_REQ_BUDGET_CHARS` calé sur le ctx réel (~2,9 chars/token mesurés
   pour le français Qwen ; 68 000 chars ≈ 23,4 k tokens).
3. Descriptions de schémas plafonnées (`_SUMMARY_MAX_CHARS`/`_PARAM_MAX_CHARS`
   dans `registry.py`) : `auto` envoie les schémas à CHAQUE requête.
4. Contexte llama.cpp porté à **32 768** (`-c`, ~+206 Mo de VRAM seulement,
   KV unifié q8_0) : ~30,7 k tokens de prompt disponibles.
5. `SystemPrompt_EXPLORATION_COURT.md` réellement compacté (14,6 k → 8,1 k
   chars) : c'est la plus grosse part fixe du system prompt.

Vérification : requête pleine taille (system + 39 schémas + work au budget)
= **21 377 tokens, `truncated = 0`**, HTTP 200.

## Piège d'évaluation (pour les testeurs)

Un scénario de test doit respecter l'ÉTAT DU JEU : un personnage mourant
refuse légitimement jets/soins ; en phase combat, XP/inventaire sont gérés
par le moteur serveur (outils volontairement absents) ; la création de
perso demande la pose de quête pour débloquer la phase exploration.
Conclure « outil raté » seulement si l'outil est EXPOSÉ et l'état neutre.

## Résumé en une ligne

> **Format natif + `enable_thinking:false` en payload + un seul canal au
> prompt + routage explicite + outils exposés par phase + nettoyage/
> récupération côté app + anti-simulation avec relance nommée.**
