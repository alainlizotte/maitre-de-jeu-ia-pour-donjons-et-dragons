# Rapport E2E « Une journée à Waterdeep » — marché, auberge, repos, familiers, combat, vente

> Session du 23/09/2026. Serveur réel : `http://localhost:8123` (MJ LLM
> Qwen3.5-9B-Q4_K_M, llama.cpp natif). Partie de référence : **`dc2fab62`**.
> Commit des correctifs : **`85cdb4c`**.

## Objectif

Valider de bout en bout, sur le **vrai LLM-MJ** (pas un mock), les nouvelles
fonctionnalités utilisateur PHB 3.5 + règles-maison : marché boutiquier,
auberge, repos long avec bonus de nuitée, familiers/compagnons, combat, et
revente d'équipement — sur un scénario rejouable phase par phase.

## Méthode

- **Setup déterministe** (`phase_setup`) : 4 PJ (Hugo Guerrier niv 2,
  Elara Magicien, Sylva Druide, Luna Clerc), caracs explicites, bourses en pc
  (`Hugo 5000, Elara 3000, Sylva 500, Luna 100`), localité Waterdeep
  (Métropole, prix ×0.95), phase exploration. La mécanique de création n'est
  pas le sujet du test.
- **Gameplay pilotée par le vrai LLM** via le chat WebSocket (`tour_dm`) :
  le MJ doit choisir et appeler les bons outils (`marche_*`, `auberge_commander`,
  `repos_long`, `appeler_familier`, `engager_combat`, `marche_vendre`).
- Les checks lisent l'**état serveur réel** (fiches sur disque → bind-mount).
- Bilan persistant (`%TEMP%\e2e_ville_reels\bilan.json`), rapport final
  `py tests/e2e_ville_reels.py rapport`.

## Résultat final (partie neuve, une passe)

**23 vérifs OK, 0 échec.**

| Phase | Outil choisi par le MJ LLM | Vérification |
|---|---|---|
| marche | `marche_consulter` + `marche_acheter` (+ `equipement_catalogue`) | article acheté, **or débité**, poids transporté > 0 |
| auberge | `auberge_commander` | or −25 pc, condition **Rassasié**, marqueur de nuitée posé |
| repos | `repos_long` | PV → max, fatigue retirée, **marqueur d'auberge consommé** (bonus ×1,3) |
| familier | `appeler_familier` ×2 | Chat d'**Elara** (rituel **−1000 pc**, 3000→2000), Loup de **Sylva** (compagnon **gratuit**) |
| combat | `engager_combat` (Gobelin + Squelette) | 2 monstres engagés, **tous détruits**, finir_combat, phase exploration |
| vente | `marche_vendre` | épée longue retirée, **+75 pc**, or final **3957** |

État final : Elara 2000 pc (familier Chat invoqué), Sylva 500 (Loup invoqué),
Luna 100, Hugo 3957 pc / chemise de mailles conservée.

## Bugs d'intégration découverts et corrigés (cause racine)

1. **`_PHASE_TOOLS["exploration"]` masquait l'outillage marchand au LLM** —
   `marche_consulter/stock/acheter/vendre`, `equipement_catalogue`,
   `auberge_commander` n'étaient **jamais exposés** en phase exploration (et
   absents de `_OUTILS_DECISION`). Le MJ n'avait que `inventaire_ajouter`
   pour « acheter » → objets offerts **sans débit** d'or.
   *Correctif* : ajout à `_PHASE_TOOLS["exploration"]` + `_OUTILS_DECISION`,
   `appeler_familier`/`renvoyer_familier` en exploration & combat, budgets
   anti-spam 1/tour.
2. **`tools_prompt_compact` (fiche de routage) sans ligne commerce** — le
   modèle ne savait pas quel outil appeler pour acheter/vendre/loger.
   *Correctif* : routes ACHETER→`marche_consulter`/`stock` puis
   `marche_acheter` (JAMAIS `inventaire_ajouter` pour un achat), VENDRE,
   catalogue, repas/logement, familier.
3. **Découvert** : `appeler_familier` **re-débite** le rituel à chaque appel —
   comportement conforme (on ne rituelle pas son familier deux fois gratuitement),
   documenté pour le harnais (ne pas re-soumettre la phase en boucle).

*Déploiement* : le code du conteneur `dnd35-mj` n'est pas bind-mounté
(seuls cartes/config/knowledge_import/server/data le sont) → `docker cp` des
deux fichiers + `docker restart dnd35-mj`, contenu vérifié dans le conteneur.

## Comportements du MJ LLM 9B observés (à garder en tête)

- **Re-achat en boucle sur relance** : tant que le check de fin n'est pas
  satisfait, le MJ rachète (empile les quantités `qte` et vide l'or). Le
  harnais abandonne dès que la **mécanique** est démontrée (or débité + ≥1
  article ciblé) et **normalise l'économie du scénario** (or=3907, 1× chaque
  article).
- **Noms stockés tels que passés** : le MJ appelle `marche_acheter` avec des
  minuscules (`épée longue`, pas `Épée longue`) → la fiche garde ce nom ;
  les comparaisons doivent être normalisées (accents/casse). `marche_*`
  matchant via `_norm`/`_cle_objet`, ce n'est pas un blocage.
- **Sorts hors contexte** : `incanter_sort` appelé pendant un repas ou un
  achat (bruit, sans conséquence mécanique).
- **`repos_long` anti-spam** (< 20 min) refuse un 2ᵉ repos rapproché — c'est
  voulu (8 h de sommeil ne s'enchaînent pas), pas une régression.
- **Conflit d'identité en combat** : un PJ déjà « joué » par le harnais
  (identité `e2e_ville` via HTTP) est bloqué si le harnais tente de le faire
  jouer ensuite par WebSocket (« doit attendre : joué par e2e_ville »).
  → résolution des tours de combat **déterministes via HTTP** (l'engagement
  LLM + la résolution serveur restent réels).

## Fichiers

- `tests/e2e_ville_reels.py` — harnais (setup déterministe + 9 phases).
- `server/llm/orchestrator.py`, `server/tools/registry.py` — correctifs
  d'exposition (commit `85cdb4c`).
- Référence déterministe verte : `tests/test_scenario_ville.py`,
  `tests/test_marche.py`, `tests/test_familiers.py` (suite complète
  606 passed).
- Parties : `dc2fab62` (e2e courant), résidus de debug `b3d524c1`
  (à supprimer si besoin), `68bc0f6a`, `8098fd7e` (préexistantes, intactes).