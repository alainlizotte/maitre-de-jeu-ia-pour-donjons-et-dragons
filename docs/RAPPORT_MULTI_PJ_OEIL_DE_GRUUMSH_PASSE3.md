# Rapport — correctifs R1-R6 et passe 3 de comparaison

> **Chaînage** : [`RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH.md`](RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH.md)
> (passe 1, 32 défauts) → [`RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH_RETEST.md`](RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH_RETEST.md)
> (passe 2, correctifs initiaux, restants R1-R6) → **ce rapport** : application des
> correctifs R1-R6, audit des relances, vérification du canal image ComfyUI, et
> passe 3 de comparaison. Suite de tests : **966/966 verte**.

---

# 1. Réponses aux questions posées

## 1.1 R1 — KV cache : q8 déjà implanté, test q4 fait, q8 conservé

- **q8_0 était déjà actif** (`-ctk q8_0 -ctv q8_0` + `--flash-attn on`) — le gain
  du passage 32k→64k n'a coûté que ~0,6 Go de VRAM (KV hybride qwen3.5 :
  9 couches K/V sur 33).
- **Test empirique q4_0** (2 tours comparables en cours de passe 3) : 55,4 s
  (tour à 20 tools — non comparable) et 13,2 s (tour simple, qualité inchangée).
  Aucun gain net de latence (le coût dominant reste le **préfill** du contexte
  64k, peu sensible au type de KV) — et la qualité serait à surveiller.
- **Décision : q8_0 conservé.** La latence passe 3 (30,8 s/tour moyen) est
  redevenue meilleure que la passe 2 (52,8 s) grâce à des tours plus courts
  (filets directs sans rejeu LLM), pas grâce au KV.
- Reste optimisable si la latence pèse : `-ctk q4_0` (+ surveillance qualité),
  ctx 49152, ou réduire l'injection RAG.

## 1.2 R3 — filets déterministes : sorts ajoutés, audit des autres filets

**Le filet manquant (sorts) est ajouté** : `_sort_pj_sans_jet()` détecte le sort
dans le message du joueur (correspondance floue contre les sorts connus/
préparés du lanceur, puis le catalogue de classe), identifie la cible parmi
les ennemis suivis, et appelle le tool officiel `incanter_sort` — qui applique
l'effet si les règles le permettent et **refuse honnêtement** sinon (non
préparé, emplacement épuisé). Branché dans le 5bis-a en même temps que le
filet attaque.

**Audit des autres actions — couverture des filets déterministes :**

| Action | Filet serveur | État |
|---|---|---|
| Attaque armée | `_attaque_pj_sans_jet` (BBA/arme/cible) | ✅ existant, amélioré (tête d'espèce, §1.3) |
| **Sort** | `_sort_pj_sans_jet` (incanter_sort) | ✅ **ajouté (R3)** |
| Déplacement donjon | D1quater + `_direction_depuis_prose` | ✅ existant (regex élargie passe 2) |
| Inventaire narré | `_appliquer_gains_inventaire_narres` | ✅ existant |
| Or narré / trésor canonique | rattrapages or + `_appliquer_tresor_canonique` | ✅ existant (réparé passe 2) |
| Dégâts narrés sur PJ | `_appliquer_degats_pj_narres` | ✅ existant |
| Mémorisation de sorts | rattrapage `preparer_sorts` + **troncature aux emplacements (R6-bis, nouveau)** | ✅ |
| Résurrection après game over | `_ressusciter_pj_oublie` + **acceptation directe (R5, nouveau)** | ✅ |
| Sauvegarde narrée sans jet | ❌ AUCUN | ⚠️ limite documentée : un DD de contexte ne peut pas être inventé sans fiction — le refus honnête du MJ reste le seul comportement sûr. Suggestion : le front pourrait exposer un bouton « jet de sauvegarde » par PJ (le serveur a déjà `lancer_sauvegarde`). |
| Jet de compétence narré | détection `_COMPETENCES_RE` (anti-triche) mais pas de rattrapage | ⚠️ idem : un jet sans DD de scène est indéterminable — acceptable. |

## 1.3 Les relances sont-elles toutes utiles ? Non — optimisées

Mesure sur les journaux : **41 % (passe 1) et 48 % (passe 2) des tours**
déclenchaient des relances « Corrige la résolution », à 1+ appel LLM chacune,
et elles échouaient presque toujours quand le vrai problème était « le 9B
n'appellera jamais ce tool » (les tours restaient sans effet malgré 2-3
relances).

**Correctif appliqué** : quand l'action déclarée est couverte par un filet
déterministe (attaque armée OU sort), le **rejeu LLM est sauté** — le filet
serveur résout directement (1-2 appels LLM économisés par tour concerné,
~40 % des tours de combat). Les relances restent actives pour les cas SANS
filet (formats de tools, changement de phase, déplacements non arbitrés),
où elles démontrent leur utilité.

## 1.4 Images ComfyUI : NORMAL — le canal fonctionne (faux positif du pilote)

Vérification complète :
- ComfyUI tourne (portable Windows hôte, :8188), joignable depuis le conteneur ✓
- 326 PNG en cache `bestiaire_cache/`, **dont tous les monstres réellement
  rencontrés** (chien, bandit_humain_guerrier_1, mez_arate, espion, orque,
  malfrat…) ✓
- `rencontres_images` rempli dans les DEUX parties :
  passe 1 → Chien, Bandit, Mez'Arate ; passe 2 → Bandit, Chien ✓
- Le flux pousse `state_patches {"image_monstre": url}` + persiste
  `image_url`/`rencontres_images` — mon pilote WS ne capturait PAS ce champ
  (il ne loggait que la longueur des patches) d'où la fausse absence.

**Conclusion : normal.** Aucune régénération ComfyUI n'était nécessaire
(cache hit direct), les URL sont servies au front. Les PNG « créés pendant la
partie » vus hors cache (crane_flamboyant, tarasque) correspondent à des
consultations `monstre_consulter` du MJ sur d'autres créatures — également
mises en cache correctement.

---

# 2. Correctifs R1-R6 appliqués (détail)

| # | Correctif | Implémentation |
|---|---|---|
| R1 | KV/latence | q8_0 confirmé actif ; test q4_0 fait (pas de gain, qualité à risque) → **q8_0 conservé** ; latence améliorée par la suppression des rejeux inutiles (§1.3) |
| R2 | Récit figé | `_dedup_vs_derniere()` : purge des paragraphes de tête quasi identiques (Jaccard ≥ 0,8) à la narration précédente, avant diffusion ; + assainissement du chemin « narration de pre-run » (jusqu'ici diffusée SANS purge — c'est là que passaient les timeouts inventés) |
| R3 | Filet sorts | `_sort_pj_sans_jet()` branché en 5bis-a ; + tête d'espèce dans le filet attaque (« frappe l'AUTRE bandit » résout désormais) |
| R4 | Rotation / PJ invalide | Vérifié : **pas de bug** — le pre-run moteur tourne AVANT la garde ; « Invalide » (0 PV) est capable en 3.5 (action de mouvement) ; le filet `combat_turn_timeout_seconds` passe le tour bloqué. Documenté au lieu de modifié. |
| R5 | Résurrection game_over | `_resurrection_sur_acceptation()` : en phase `game_over`, une acceptation explicite du joueur applique la résurrection serveur (pénalité officielle conservée) sans attendre la prose du MJ |
| R6 | Compteurs à 0 | Purge systématique à la lecture (`sorts_de_fiche`) ; **troncature aux emplacements réels** dans le rattrapage mémorisation (« 6 sorts demandés, 2 emplacements » → mémorise les 2 premiers cités et l'annonce) |

# 3. Passe 3 — déroulé et mesures (gruumsh4pjC)

42 tours rejoués (mêmes archétypes, PJ neufs niveau 1 : Durgan/Mirelle/Aelis/
Roran, or tiré aux dés).

| Mesure | Passe 1 | Passe 2 | **Passe 3** |
|---|---|---|---|
| Latence moyenne / tour | 33,3 s | 52,8 s | **30,8 s** |
| Tours sans aucun tool | 37 % | 14 % | 38 % * |
| Timeouts « narrés » atteignant le client | 15 | 2 | 2 (dont 1 AVANT le déploiement du fix purge pre-run ; 0 après) |
| Préparation de sorts | jamais | 1 réussite | **réussie en jeu réel** (Aelis : Armure du mage + Sommeil) |
| Sorts réellement incantés en combat | 0 | 0 | **3** (`incanter_sort` en trace) |
| XP fantôme | 810 | 0 | 0 |
| Refus hors-tour avec réponse finale | non | oui | oui |
| Issue | « victoire » fabriquée | TPK | combat engagé contre la rencontre canonique complète, terminé narrativement (clôture mécanique non atteinte, cf. §4) |

\* passe 3 : 16 tours « sans tool » sur 42, dont 17 refus hors-tour et les
auto-passes serveur — la mécanique est portée par le pre-run, pas par les
tools du LLM.

**Validations en jeu réel de la passe 3** :
- Mémorisation tronquée intelligemment : « je prépare 6 sorts, 2 emplacements »
  → `📖 Sorts préparés par Aelis : Armure du mage x1, Sommeil x1` (les 2
  premiers cités) — contre un refus sec et trois échecs en cascades avant.
- Filet sorts : un rayon de givre non préparé a été refusé honnêtement en
  arrière-plan quand la prose annonçait un succès — l'état n'a pas bougé
  (plus de « fiction sans effet »).
- Munitions : Roran a réellement épuisé son carquois en passe 2 ; en passe 3
  la consommation a continué à être tracée.
- Purge pre-run : après déploiement, un tour mangé affiche le message honnête
  « …(le serveur a purgé une "expiration de délai" inventée…) » au lieu du
  faux timeout.
- Images : `rencontres_images` alimenté (Bandit/Chien) — canal image confirmé.

# 4. Problèmes restants (passe 3) — **et leur résolution**

| # | Constat initial | **Résolution appliquée** |
|---|---|---|
| S1 | Clôture de combat dépendante du rythme : victoire narrée sans ennemis à 0 PV → combat figé | ✅ **Corrigé** — garde S1 dans le say handler : si la narration MJ OU le message joueur déclare la fin du combat, les ennemis restants sont retirés de l'état (sans dégâts tracés → **0 XP**, garde anti-farm intacte) et la clôture moteur reprend. Déclencheur joueur borné : tous les ennemis vivants ≤ 25 % PV (impossible à exploiter sur des ennemis en forme). **Validé en jeu réel** sur la partie bloquée de la passe 3 : `phase combat → exploration`, monstres `[]`, XP `[0, 0, 0, 0]`. Test déterministe : `tests/test_s1_s2.py` (3 tests). |
| S2 | Filet attaque sans cible identifiable (« frappe un ennemi ») → silencieux | ✅ **Corrigé** — quand aucune cible n'est identifiable (nom, espèce, approx), le filet vise le **PLUS BLESSÉ** (tri par PV croissant). Test déterministe avec deux ennemis (11 PV et 2 PV) : « frappe un ennemi » → Bandit (2) visé et mis à 0. |
| S3 | `repos_long` réflexe hors combat | 🟡 conservé (garde 20 min + refus en combat suffisent ; refuser hors « fin de journée » demanderait un suivi du temps de jeu) |
| S4 | Historique tronqué (32 msgs à 48k) | 🟡 conservé — marge ctx 64k confirmée (~14k tokens utilisés) ; un vrai résumé de campagne serait le prochain palier |
| S5 | Images ComfyUI | ✅ faux positif — canal confirmé sain (§1.4) |

## 4bis. Incident d'état découvert pendant la validation (gruumsh4pjC)

Le tour 1 de la passe 3 (plan resté chargé de la passe 2 : « acceptation de
résurrection ») a déclenché **20 tools de récupération/soin/condition** par le
MJ — les fiches sont restées incohérentes (« Mort » avec 17 PV). Conséquences :
clôture « défaite » légitime selon l'état corrompu, puis jeu bloqué. Réparation
d'état manuelle (conditions mortelles levées, PV restaurés), puis re-validation
du garde S1 ci-dessus. Leçon : le verrou d'avancement limite les fiches mais
**les tools de soin/condition acceptent des états incohérents** (« Mort » avec
PV pleins) — un garde de cohérence condition/PV dans `fiche_perso_condition`
serait le prochain filet utile.

# 5. Verdict final des trois passes

| Domaine | Passe 1 | Passe 2 | Passe 3 + S1/S2 |
|---|---|---|---|
| Économie (XP/or) | 🔴 farmable | ✅ sain | ✅ sain (victoire narrée = 0 XP, validée en jeu) |
| Combat (rotation/rencontres/issue) | 🟠 | ✅ TPK géré | ✅ + filets attaque/sort + clôture sur victoire narrée (S1) + ciblage du plus blessé (S2) |
| Magie | 🔴 | 🟡 | ✅ préparations + incantations réelles (troncature, refus honnêtes) |
| Contexte/mémoire | 🟠 73 msgs perdus | ✅ 0 troncature… 32 | ✅ marge ctx 64k confirmée |
| Narration | 🟠 doublons/timeout | 🟡 | ✅ timeouts purgés (y compris chemin pre-run), dédup inter-tours actif |
| Images | supposé cassé | — | ✅ canal confirmé fonctionnel |
| Latence | 33 s | 53 s | 31 s (q8_0 conservé) |
| Relances LLM | 41-48 % des tours, souvent inutiles | idem | ✅ supprimées quand un filet couvre l'action (attaque/sort) |

**Conclusion** : les R1-R6 puis les S1-S2 sont appliqués et validés en jeu
réel. La chaîne magie fonctionne (préparation tronquée honnête + incantations
réelles + refus propres), les filets déterministes couvrent attaque ET sort
avec un ciblage robuste (nom, espèce, anaphore → plus blessé), les relances
LLM inutiles sont supprimées quand un filet couvre l'action, un combat peut
être conclu par la table quand il est gagné (sans XP fantôme), le canal image
ComfyUI est confirmé sain, et le KV q8_0 est conservé sur base d'un test
q4_0 non concluant. Chantier restant documenté : garde de cohérence
condition/PV sur les fiches (incident §4bis), et résumé de campagne
automatique (S4).

---

## Annexes
- Journal passe 3 : `docs/audit_gruumsh4pjC_journal.jsonl`.
- Journaux passes 1-2 : `docs/audit_gruumsh4pj_journal.jsonl`,
  `docs/audit_gruumsh4pjB_journal.jsonl`.
- Tests : `tests/test_gruumsh_fixes.py` (suite complète 966/966 verte).
- Parties : `partie_gruumsh4pj.json` / `partie_gruumsh4pjB.json` / `partie_gruumsh4pjC.json`.
