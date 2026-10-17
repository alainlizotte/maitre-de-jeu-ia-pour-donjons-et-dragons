# Rapport E2E navigateur — partie 4c58b42b (run 1) / 811cdea3 (run 2)

**Date** : 16 oct 2026 · **Testeur** : agent (navigateur réel, compte `e2e_48b_testeur`)
**Scénario** : Dues For The Dead (ch. 1) — PJ solo **Krask**, Demi-orc Barbare niv. 1
**Serveur** : dnd35-mj (build post-correctifs 48b239fa) · LLM Qwen3.5-9B · ComfyUI local
**Durée** : ~2 h de jeu réel, ~46 tours de chat · **Issue** : ⚠️ scénario **non complété** (2 morts du PJ)

---

## 1. Ce qui a été vérifié et FONCTIONNE

| Mécanique | Vérification | Verdict |
|---|---|---|
| Connexion / compte | Login SPA (React) | ✅ |
| Création de personnage (UI) | Formulaire complet, tirage 4d6, ajustements raciaux appliqués (FOR 16+2=18, INT 17−2=15), PV d12+CON | ✅ (après fix C6, cf. §3.4) |
| Sélecteur de scénario | Univers Divers → Dues For The Dead, quête attachée, PJ rattaché | ✅ |
| **Ouverture hors donjon** (correctif 48b239fa-1) | 2/2 runs : scène au porche à Phlan, Yovir Glandon + Cassyt, mission exposée, `donjon.id` vide, aucun événement d'entrée | ✅ |
| Entrée explicite du joueur | `carte_donjon_entrer` → salle (0,0) | ✅ |
| Déplacements + carte | Toutes les salles suivies : (-1,0), (0,-1), (0,-2), (0,-3), étage 1 (0,0)→(0,-2)… ; carte SVG rafraîchie en direct | ✅ |
| Piège à sauvegarde | Fosse (-1,0) : jet DEX DD 10 → **4 = échec**, chute narrée, pas de dégâts (conforme module), `piege_declenche=True` | ✅ |
| Fouille sur place (correctif 48b239fa-2) | « Je fouille la fosse » ×2 → **aucune téléportation**, recherche narrée sur place, indice trouvé | ✅ |
| Engagement de combat | `engager_combat` → initiative réelle (Krask 21/Squelette 11 ; run 2 : 3/17), PV canoniques | ✅ |
| Jets d'attaque officiels | `lancer_attaque` : jet 2+5=7 vs CA 15, jet 4+5=9 vs CA 15 — **CA corrigée par le tool** (10 inventé → 15 réel) | ✅ |
| Clôture de combat + XP | Squelette détruit → **XP 135** (FP 1/3, allocation solo correcte), phase rendue à l'exploration | ✅ |
| Inventaire | Remises interceptées (huile sacrée, fiole d'échantillon, bague) ; **fusion sans doublon** (bague re-narrée → 1 exemplaire) | ✅ |
| Game over | -10 PV → phase `game_over`, 💀 affiché à l'UI | ✅ |
| Résurrection | Rejoindre une nouvelle partie repart à pleine santé (comportement documenté de l'UI) | ✅ |
| Arbitrage VRAM | Génération ComfyUI manuelle de l'utilisateur → tour suivant mis en attente (comportement attendu) ; verrou récupéré après reload | ✅ (voir NC-31) |

## 2. Non-conformités (par gravité)

### 🔴 Graves (bloquent le jeu)

- **NC-1 — Le combat en flux chat ne livre jamais ses résultats**. Les jets officiels tournent côté serveur (`lancer_attaque ok=True`, 2/2 ratés légitimement) mais la narration finale ne les reflète pas : le modèle recopie de la prose parasite (la bague qui vibre) au lieu de « votre attaque rate (9 vs CA 15) ». Le squelette reste 3/3 après 4+ rounds d'échanges narrés. 3 tours sur 4 de combat sans AUCUN tool dans la trace du chat.
- **NC-2 — Dégâts infligés par les filets, hors moteur** : le PJ passe 12 → 11 → 7 → 1 → -10 PV par des attaques de monstres **narrées en prose** (rattrapage dégâts) sans initiative ni jet affiché. Deux morts consécutives dans ce mode. Le joueur ne voit jamais les jets qui le frappent.
- **NC-3 — Quantités d'ennemis ignorées** : la fosse canonique « Squelette ×5 » (embuscade, salle (0,-2)) a été engagée **×1** ; le « Zombie ×2 » du poste → **×1**. L'embuscade n'a jamais été déclenchée (le MJ narré des squelettes DÉJÀ MORTS + du butin inventé).
- **NC-4 — Anti-re-engagement trop large** : le dédup `historique_engagements` (signature `squelettex1`) **bloque la rencontre canonique d'une autre salle** du même type — impossible de (re)combattre les squelettes restants.
- **NC-5 — `engager_combat` forcé refusé à chaque tour de combat** : le détecteur « combat narré sans engager_combat » force l'appel, qui est REFUSÉ (combat déjà engagé) — un tour de bruit à chaque round, qui nourrit la confusion du modèle.

### 🟠 Modérées (dérives de narration / règles de table)

- **NC-6 — Souveraineté du PJ** (règle N°5) : le MJ fait accepter/parler Krask (« Je vais la garder précieusement, dit Krask »), et prend des décisions à sa place.
- **NC-7 — Répétition verbatim** (règle « aucune répétition ») : la prose « La bague, froide au toucher, vibre légèrement… » recopiée mot pour mot entre deux tours.
- **NC-8 — Jets manquants** : fouille narrée sans jet de Détection/Fouille ; forçage de grille narré sans jet de Force (résultat inventé) ; piège des escaliers branlants réussi sans jet de DEX (DD 10). Règle « aucune réussite sans jet » violée 3×.
- **NC-9 — Incohérences narration ↔ état** : narration « galeries hautes » pendant que l'état est à l'étage 1 ; « 📍 Vous êtes dans la cellule (0,-2) » injecté dans la prose alors que la carte est en (0,0) ; « déjà visitée » halluciné dans un run neuf.
- **NC-10 — Déplacement ignoré** : « Je vais au nord » a reçu 302 caractères de mysticisme sur la bague, sans mouvement ni question (D0 a répondu « narrer » malgré la consigne ; 5quater-b sans marqueur de déplacement dans la prose).
- **NC-11 — Action du joueur réécrite** : « Je garde la bague… je vais au nord » → seule la partie bague traitée ; le mouvement est tombé dans un angle mort.

### 🟡 Mineures

- **NC-12 — Double déplacement au tour d'entrée** (run 1) : `entrer` + `etage` enchaînés → étage 1 direct (le verrou « un déplacement par tour » n'a pas retenu le second).
- **NC-13 — Hallucinations de décor** : « graffiti anciens » (absents), lanternes « flottantes », squelettes morts inventés, butin hors module (bague en or, parchemin froissé, bague en argent).
- **NC-14 — Objet d'ouverture improvisé** : « fiole d'huile sacrée » hors module (le module ne remet rien au briefing) ; ajoutée à l'inventaire au tour 2 seulement (remise différée).
- **NC-15 — Confusion d'identité** : « Krask tombe… Cassyt et vous êtes sur le bord » — le PJ est parlé à la 3ᵉ personne puis dissocié de « vous ».
- **NC-16 — Typo récurrente** « ossemens » (2×).
- **NC-17 — Journal du chat muet** : `tool_calls_trace` vide dans le chat alors que les tools ont tourné (traçabilité joueur nulle).

## 3. Bugs corrigés PENDANT la session

- **POST /api/persos → 500** (signalé par l'utilisateur, réglé avant cette session : commit `d762df1` — garde C6 du commit `d61883d` avant définition + `_carac_base_c6`/`_equip_rest` fantômes). La création UI a ensuite fonctionné en réel (fiche Krask créée via le formulaire, une deuxième fiche Throk'mar par l'utilisateur).

## 4. Verdict sur le scénario

**Non complété** — 2 morts du PJ (fosse squelette niv.1 ; araignée/poste-kobold+zombie).
Causes combinées :

1. **Équilibrage solo** : le module vise 3-4 PJ niv. 1. L'ajustement League prévu (`ajustement="pv 30%, attaque -4…"`) n'a été appliqué **nulle part** (Squelette 3/3 canon, Araignée 1/1, Kobold 4/4, Zombie 5/5). À noter : l'Araignée affichait 1/1 = signe d'un ajustement partiel incohérent.
2. **Combat chat non fonctionnel** (NC-1/2/3/5) : sans moteur visible ni dégâts officiels sur les ennemis, un combat long est ingagnable — les PV du PJ s'érodent par les filets pendant que les ennemis restent intacts.
3. La mécanique sous-jacente (dés, CA, XP, clôture) est **correcte** : c'est la couche narration qui la masque.

## 5. Recommandations (priorisées)

1. **Combat : injecter le résultat des tools dans la narration finale** — quand `lancer_attaque`/`lancer_degats` ont tourné, la narration finale doit partir de LEUR texte (bloquer la prose libre tant que des résultats d'attaque existent dans le tour). C'est le correctif n°1.
2. **Respecter les quantités du manifeste** dans `engager_combat` (« Squelette ×5 » → 5 entrées) et appliquer l'`ajustement` du scénario automatiquement pour les groupes sous-dimensionnés.
3. **Exempter les rencontres canoniques du dédup** quand la salle diffère (signature = type + salle, pas seulement type).
4. **Silencieux le forçage `engager_combat`** quand un combat est déjà actif (pas de tool_call refusé injecté au modèle).
5. **Jets obligatoires pour les pièges/actions à DD** (escaliers branlants, forçage, fouille) : filet qui force `lancer_sauvegarde`/`lancer_d20` quand une action à DD canonique est narrée.
6. **Consolider la souveraineté** : interdire les répliques du PJ dans les filets de rejeu (post-traitement `_corriger_pronoms_pj` étendu aux actions « Krask dit/Range/accepte »).
7. **UI** : un bouton d'action de combat officiel (attaquer/retraite) dans le panneau ⚔️ éviterait la dépendance à la volonté du 9B.

## 6. Re-test apres correctifs (17 oct 2026)

Correctifs implémentés puis re-testés (suite 996 verts + 4 skips) :

| Correctif | NC visée | Vérification |
|---|---|---|
| **F1** - filet 5bis-f + verrou orchestrator : detection d'ouverture par le marqueur "Debut de l'aventure" (un PJ ressuscite porte l'evenement d'arrivee : histoire jamais vide) | ouverture forcee | E2E navigateur run 4 (Krask ressuscite) : AUCUNE entree forcee, ouverture au porche - OK |
| **F2** - signature de dedup = espece + quantite + SALLE (donjon.id@x,y) | NC-4 | 4 tests unitaires (meme salle refusee, autre salle autorisee, hors donjon inchange, signature ecrite) - OK |
| **F4b** - le texte officiel de lancer_attaque depose dans notes_mecaniques (file qui survit aux rejeux) + filet 5bis-atk | NC-1 | E2E navigateur : le verdict officiel ("Attaque : Krask [hache d'armes] vs Squelette (CA 15) - Jet brut : 4 - Total : +5") est VISIBLE dans la narration - OK |
| **F3** - 5bis-a elargi : l'attaque declaree DANS le tour d'engagement est resolue (etat_avant.phase etait encore "exploration") | NC-1/2 | Code + suite verte |
| **F5** - regex de deplacement etendues ("je continue AU nord") | NC-10/11 | Deplacement reussi en E2E navigateur apres extension - OK |
| **F6** - traceback complet sur crash de tour | diagnostic | En place - OK |

Decouvertes complementaires :

- **La dilatation de quantite canonique EXISTAIT deja** (M6/P9, state.py) : "Squelette x5" -> 3 creatures pour 2 PJ (plafond d'equilibre), 1 pour un solo. Le x1 observe en session venait du combat JAMAIS engage (le MJ narrait des squelettes morts) - pas d'un defaut M6. Un essai d'extension F3 a ete ANNULE (redondant, et une premiere decoupe avait avale le garde B25 - restaure via git).
- **Le combat reste instable sur tours longs** : passe ~50k chars de contexte, le 9B narre des tours fantomes ("votre tour expire", sorts improvises refuses par le filet) et n'appelle plus les tools de facon fiable. Les filets (F4b, 5bis-a, 5bis-atk) garantivent desormais que l'ETAT et le VERDICT officiel restent corrects et visibles, mais l'experience reste degradee sur les combats longs en solo.

### Verdict mis a jour

Le scenario complet en solo reste difficile (equilibrage 3-4 PJ + derive du 9B en combat long), mais les resultats officiels sont desormais TOUJOURS visibles et l'etat toujours correct : les des sont jetes, les CA corrigees, l'XP attribuee, les morts conformes. La recommandation structurante reste le MODE COMBAT 100 POUR CENT DETERMINISTE (boutons d'action UI ou resolution serveur complete du round), independant de la fiabilite du 9B.

## 6. Traçabilité

- Partie run 1 : `4c58b42b` (chat `server/data/chat_4c58b42b.json`, 27 messages)
- Partie run 2 : `811cdea3` (chat 46 messages)
- Commits de référence : `b52bd14`, `8084cc9` (correctifs 48b239fa), `d762df1` (fix création 500)
- Le hors-donjon est actif sur 2/31 manifestes (Dues, Crypts Kelemvor) — 29 scénarios canoniques inchangés (audit testé).
