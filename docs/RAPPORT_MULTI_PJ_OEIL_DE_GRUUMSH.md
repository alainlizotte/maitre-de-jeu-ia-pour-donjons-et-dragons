# Rapport d'audit en conditions réelles — partie multi-PJ « L'Œil de Gruumsh » (chapitre 1/2)

> **Statut : partie jouée de bout en bout** (ouverture → enceinte → rez-de-chaussée →
> premier étage → chambres privées → boss vaincu → butin → clôture demandée).
> **32 problèmes de conception documentés** (notes P1-P32), dont plusieurs
> *nouveaux* par rapport aux audits précédents (`RAPPORT_AUDIT_PARTIES_COMPLETES.md`,
> `RAPPORT_AUDIT_SCENARIO.md`). Le code de l'application n'a pas été modifié.

**Environnement de test**

| Élément | Valeur |
|---|---|
| Application | conteneur `dnd35-mj` :8123, RAG actif (3 KB), images activées (ComfyUI absent) |
| MJ | `Qwen3.5-9B-Q4_K_M-MTP` via llama.cpp (`llamacpp:8080`), tools 76, `max_tools_exposed: 15` |
| Historique | `max_history_events: 140`, `max_history_chars: 30 000` |
| Scénario | `laelith_loeil_de_gruumsh_ch01` (niv 1-3, **4 PJ**), manifeste `Oeil-de-Gruumsh.donjon.json` |
| Méthode | 4 comptes WebSocket distincts (un par joueur), pilote type client (`say`/`join` + token), tours séquentiels, journal JSONL complet, vérification des fiches/état via API après chaque tour |

**La table** (4 comptes, 4 personnages niveau 1, or de départ tiré aux dés officiellement)

| Compte | PJ | Race/Classe | PV ¹ | CA | Or départ | Or final ² |
|---|---|---|---|---|---|---|
| Bastien | Thorrak | Nain Guerrier | 17/17 | 16 | 170 po | **371 po** ³ |
| Chloe | Solenne | Humaine Clerc | 14/14 | 16 | 110 po | 110 po ⁴ |
| Idris | Liriel | Elfe Magicienne | 4/4 | 14 | 60 po | **3 260 po** ⁵ |
| Sacha | Fenn | Demi-elfe Rôdeur | 9/9 | 15 | 160 po | 160 po |

¹ PV de départ. ² fin d'audit, niveau 2. ³ crédits d'or narrés reçus dont un bloc « +100 po » attribué **à Thorrak au tour de Liriel**. ⁴ le « 15 po + clé » narré pour Solenne n'a jamais été crédité. ⁵ butin canonique de la chambre du boss = 600 pa + 100 po + 6 gemmes (≈184 po) ; **≈3 200 po crédités, deux fois**.

**État final** : les 4 PJ niveau 2 (XP 1310, prochain palier 3000), Mez'Arate vaincu,
journal du rituel + symbole du culte + 6 gemmes + grimoire en inventaire, mission
« L'Œil de Gruumsh » toujours **avancement 0/4**, `bible.progression` = `None`,
`memoire.monstres_combat` = `null` (jamais rempli malgré 4 combats).

---

# 1. Verdict global

| Domaine | État constaté |
|---|---|
| Table multi-comptes (4 WS, tokens, fiches) | ✅ le socle fonctionne : join vérifié, roster, garde de tour (7 refus hors-tour corrects) |
| Création de personnage via API | ✅ bornes/verrous efficaces (2 refus valides) ; ⚠️ quantité « x2 » d'un objet perdue à la création (P18) |
| Combat tour par tour | 🟡 le moteur calcule juste quand il est appelé ; **le MJ mange les tours des joueurs** (timeouts fantômes P27, actions ignorées P8), rencontres amputées (P9), re-engagements en boucle (15 appels `engager_combat`) |
| Magie 3.5 | 🔴 **la magie préparée est inutilisable en langage naturel** (P1/P19) ; effet de sort non appliqué (P24) ; compteur préparé corrompu à 0 |
| XP & économie | 🔴 **exploit d'XP : marteler une salle « nettoyée » paie 270 XP/perso à chaque tentative** (P15/P20) — niveau 2 acquis sans livrer de vrai combat ; crédit d'or faux en montant, en destinataire et en devise (P29) |
| Carte & fidélité au module | 🔴 la carte a suivi le groupe **3 fois en ~60 tours** ; saut de deux étages en un tour (P25) ; PNJ/objets inventés sauvés en état (carnet, grimoire, potion, porte secrète) |
| Suivi de quête | 🔴 `avancement` figé à « 0/4 » du premier au dernier tour (P30), `objectifs`/`progression` vides |
| Mémoire de campagne | 🟡 « Historique tronqué : 73 messages anciens omis » (P32) ; `monstres_combat` jamais alimenté |
| Latence | 🟡 moyenne 33 s/tour (min 1,8 s — refus hors-tour —, max 330 s) ; 2 tours « ⚠️ problème technique », 1 narration tronquée à 25 caractères |

**Bilan** : la table tient debout (4 joueurs connectés, tours attribués, combats
résolus par le serveur, XP DMG correcte à la clôture), mais le jeu réel diverge
de l'état en permanence : la magie ne fonctionne pas sans dictée de JSON par le
joueur, l'XP se farm en martelant une action, l'or est crédité au mauvais joueur
au mauvais montant, et la carte ne suit le groupe que par accident.

---

# 2. Les 6 défauts qui expliquent l'essentiel

1. **P20 (bloquant, exploit)** — le refus anti-repopulation (`F3`) transforme chaque
   tentative de combat dans une salle « nettoyée » en **clôture automatique payée** :
   +270 XP/perso par tentative. 3 tentatives = niveau 2 sans combat réel. La garde
   anti-repopulation et la clôture-XP ne se parlent pas.
2. **P1/P19 (bloquant, magie)** — `preparer_sorts` exige `preparations_json` en
   *chaîne JSON dict* (`'{"Sommeil": 1}'`) : le LLM envoie une liste native → refus
   « JSON dict attendu » sans exemple, sans coercition, sans nouvelle tentative.
   Le rattrapage « Mémorisation narrée appliquée » écrit ensuite des compteurs
   corrompus (`{"Bénédiction": 0}`) et prétend avoir réussi. Aucune préparation
   n'a survécu à un tour sans dictée explicite du JSON par le joueur.
3. **P24 (bloquant, magie)** — un sort **réussi** (`Armure du mage`, emplacement
   dépensé, prose « CA 18 ») ne modifie ni la fiche (CA 14) ni les conditions :
   l'effet mécanique n'existe pas ; le slot est perdu pour rien.
4. **P27 (majeur, combat)** — le MJ narre des « timeouts » inventés (« la limite de
   vingt secondes », « deux cents secondes ») **2 à 8 s après** réception de l'action
   déclarée dans le bon tour : des tours entiers de joueurs sont consommés sans jet.
5. **P25/P23 (majeur, carte)** — `carte_donjon_explorer` n'est appelé que 3 fois en
   ~60 tours ; un appel a fait sauter le groupe de deux étages ; les redescendes
   narrées ne remontent jamais la carte ; après changement d'étage, la prose rejoue
   la salle précédente et tente de ré-engager des cadavres.
6. **P29 (majeur, économie)** — le crédit d'or narré/canonique est faux trois fois :
   mauvais destinataire (+100 po à Thorrak au tour de Liriel), montant ~20× la
   valeur (3 260 po au lieu de ~244), double crédit du même trésor, conversion
   pa/pc/pe opaque, et rien pour Solenne malgré une bourse narrée.

---

# 3. Catalogue complet des problèmes constatés

## A. Magie (le sous-système est mort en pratique)

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P1 | 🔴 | Préparation narrée sans effet : `prepares = {}` après narration « je mémorise X et Y » (clerc : **aucun tool appelé** ; magicienne : tool en échec non retenté) | fiches après tours 2-3 |
| P19 | 🔴 | Schéma `preparer_sorts` inatteignable en langage naturel : liste native refusée (« JSON dict attendu », sans format montré), nom de sort strict (« Soin leger » ≠ « Soins légers », aucune suggestion ni application partielle) ; n'a réussi **qu'avec le JSON dicté mot à mot par le joueur** | logs orchestrator 15:28 & 15:44 |
| P24 | 🔴 | Sort incanté avec succès mais **aucun effet mécanique** : Armure du mage → emplacement dépensé, fiche inchangée (CA 14, conditions []) | fiche Liriel après cast |
| P2 | 🟠 | `repos_long` utilisé comme bouton « remise à zéro » : **7 appels** en ~60 tours, dont 3 pour « préparer des sorts » et 2 **en plein combat** (boss à 10 PV) ; PV +1 restaurés correctement mais prose « restaurés » fausse ; aucun coût (rations, temps, alerte) | journal, logs |
| P26b | 🟡 | Compteur `prepares` corrompu à **0** après usage (jamais nettoyé) : `{"Armure du mage": 0, "Sommeil": 1}`, `{"Bénédiction": 0, "Soins légers": 1}` | fiches finales |

## B. XP, butin & économie

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P15/P20 | 🔴 | **Exploit XP** : refus anti-repopulation → clôture « victoire » → +270 XP/perso par tentative de combat contre une salle déjà « nettoyée ». 3 tentatives = 810 XP (sur 1000) pour 1 seul vrai combat | XP 270 → 540 → 810 sans combat |
| P29 | 🔴 | Crédit d'or faux : mauvais destinataire, ~20× la valeur (3 260 po), **double crédit**, pa/gemmes jamais converties lisiblement, bourse narrée de Solenne jamais créditée | fiches + bloc « 💰 Or narré crédité » |
| P9b | 🟠 | XP payée pour des ennemis « vaincus » **sans jamais avoir eu de PV** (bandits de la salle 0,0) | narrations tours 5-6 |
| P14 | 🟡 | « Flèches (20) x2 » à la création → « x1 » en fiche (quantité non propagée) | fiche Fenn |
| P18 | 🟡 | Le MJ narre des montants (« quelques pièces d'or », « 39 flèches ») sans jamais interroger l'état ; le carquois a compté 20 puis 40 puis 39 flèches selon les tours | tours 4, 9, 12 |

## C. Combat & tours de jeu

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P8 | 🟠 | Le MJ **ignore l'action du joueur actif** et joue les autres PJ (narration de « confusion » lancée par Liriel — sort qu'elle ne connaît pas — au tour de Thorrak) | tour 5 |
| P27 | 🟠 | Timeouts fantômes : « limite de vingt secondes », « deux cents secondes » narrés 2-8 s après l'action ; 4 tours de PJ mangés pendant le combat du boss | tours Solenne/Fenn (combat final) |
| P9 | 🟠 | Rencontre canonique amputée : salle 0,0 = Bandit ×2 + Chien ×2 ; le combat n'a engagé **que les chiens** ; les bandits ont ensuite été déclarés « déjà vaincus » sans combat | état après T5 |
| P13 | 🟠 | Faux souvenirs de combat : « le chien, déjà blessé par l'attaque de Liriel » (elle n'a jamais agi) ; « guerrier orc endormi » (Mez'Arate est un humain éveillé) ; boss « inconscient à 0 PV » puis repéré à 16/28 en état | tours 8, 19-21 |
| P17 | 🟡 | Erreurs tool brutes exposées au client : « ⚠️ type_sauvegarde invalide : 'Réflexion' » dans le bloc « 🎲 Jets officiels » ; sauvegarde de Volonté demandée contre un Sommeil 3.5 (sans jet) puis contre une attaque de masse (sans jet non plus) | tour 8 |
| P22/P31 | 🟡 | 2 tours livrés = « ⚠️ Le MJ a rencontré un problème technique » ; 1 dm tronqué à 25 caractères (« L'étape « Fouiller la cha ») | journal 11:56, 12:23, 13:32 |
| — | ✅ | **Points positifs** : garde de tour par personnage incarné (7 refus hors-tour corrects), initiative round 1 officielle, dégâts/CA/PV moteurs justes quand résolus, clôture DMG (FP 2 → 300 XP/niv 2), coup de grâce et PV à 0 gérés par le serveur | journal |

## D. Carte, donjon & fidélité au module

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P25 | 🔴 | La carte ne suit la narration que 3 fois en ~60 tours ; « porte à l'est du palier du premier étage » → **étage 3** (2 niveaux sautés) ; les redescendes narrées sans tool ne remontent rien | snapshots donjon |
| P23 | 🟠 | Après le changement d'étage, la prose rejoue la salle précédente (cadavres des molosses dans la cour) et retente l'engagement | tours 14-15 |
| P4 | 🟠 | Ouverture expédiée : aucun brief/accroche, le groupe démarre devant la grille (départ posé par le picker) ; PNJ inventé (« capitaine des gardes ») absent du module | tour 1 |
| P25b | 🟠 | Improvisations **sauvées en état** : carnet de notes, grimoire, potion de soins, « porte secrète derrière le tableau » — aucune ne figure dans le manifeste | inventaires finaux |
| P6 | 🟡 | Bible de scénario bruitée : `etapes` = ["Copyright Notice", "End Of License", "La Graine De Nicolak"] ; `memoire.missions.avancement` = « 0/4 » sans unité, jamais mis à jour | état quête |
| P30 | 🔴 | Suivi de quête décoratif : boss vaincu, rituel déjoué, butin pris → `avancement` toujours « 0/4 », `progression`/`objectifs` = None, `scenario_etape` jamais appelé même sur demande explicite | état final |

## E. Mémoire & narration

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P32 | 🟠 | « Historique tronqué : 73 messages anciens omis » — à 4 joueurs, la mémoire part avant la fin du chapitre | logs |
| P3 | 🟡 | La prose n'est pas réécrite sur l'état (seuls les PV le sont) : or, flèches, objets narrés ≠ état | plusieurs tours |
| P5 | 🟡 | Fuite de tokens non-latins : « des grognements低沉 » rendus au client | tour 1 |
| P16 | 🟡 | Narration dupliquée quasi verbatim d'un tour à l'autre (anti-répétition défaillante), y compris le bloc XP | tours 5-6 |
| P22b | 🟡 | Genre grammatical : Solenne (F) systématiquement « Il » ; le sexe/apparence de la fiche n'arrive pas dans la prose | tous les tours |

## F. Multi-joueurs (le point fort, avec deux failles)

| # | Sévérité | Problème | Preuve |
|---|---|---|---|
| P10 | 🟠 | Un message refusé hors-tour laisse l'émetteur **sans réponse finale** (broadcast `turn_blocked` non clôturé par un `dm` ni une fermeture) : un client naïf attend indéfiniment, pendant que le moteur continue (le tour du joueur suivant a été auto-passé pendant notre attente de 330 s) | journal 10:41 |
| P7 | 🟡 | Le `dm` est diffusé à **toutes** les connexions sans champ destinataire : toute app doit ré-attribuer le texte parheuristique (verrou + horodatage). Un champ `pour` ou un id de tour rendrait le protocole non ambigu | protocole WS |
| — | ✅ | Multi-comptes solides : tokens vérifiés au `join`, 4 incarnations simultanées, roster correct, suppression/propriété non testées cette fois | 4 WS × ~15 reconnexions |

---

# 4. Statistiques de session

| Mesure | Valeur |
|---|---|
| Actions envoyées à la table | 81 (dont 7 refusées hors-tour, 1 timeout client 330 s) |
| Narrations MJ effectives | 74 |
| Durée moyenne d'un tour MJ | **33,3 s** (min 1,8 s, max 330 s) |
| Tours sans **aucun** tool (prose sans effet) | 30 / 74 (**37 %**) |
| `engager_combat` appelés | 15 (pour 4 combats réellement livrés) |
| `repos_long` appelés | 7 (dont 2 en plein combat) |
| Narrations vides / erreurs techniques | 2 « problème technique », 1 troncature à 25 caractères |
| Combats livrés | 4 (2 chiens ; 2 bandits ; boss Mez'Arate ; 0 XP légitime de plus) |
| XP finale | 1 310/perso (≈ 740 « légitimes », ≈ 570 issues de l'exploit P20) |

---

# 5. Recommandations (priorisées)

1. **Fermer l'exploit XP** : une clôture de combat ne paie l'XP que si des PV ont
   réellement été retirés à chaque ennemi payé (`dégâts_infligés > 0` par monstre) ;
   le refus anti-repopulation doit mener à un état `exploree`, jamais à une victoire.
2. **Réparer la chaîne magie** : accepter liste *ou* dict pour `preparations_json`
   (coercition serveur), retourner l'erreur **avec le format attendu**, retenir
   l'échec comme « à retenter » dans la boucle de correction, supprimer les
   compteurs à 0, et appliquer réellement les effets de sorts (Armure du mage →
   bonus CA par condition horodatée).
3. **Interdire `repos_long` en phase `combat`** (refus tool) et le plafonner à
   1/24 h de temps de jeu ; un « je prépare mes sorts » ne doit jamais déclencher
   de repos.
4. **Tours mangés** : toute action envoyée par le joueur dont c'est le tour doit
   produire un jet ou un refus motivé — jamais une narration « ton tour a expiré »
   avec un délai inventé ; synchroniser le texte du MJ sur `combat_turn_timeout_seconds`.
5. **Carte** : rattrapage des déplacements narrés (relance `carte_donjon_explorer`
   comme pour l'inventaire) + refus de sauter un étage sans escalier déclaré ;
   exposer la salle courante canonique en tête de prompt à chaque tour.
6. **Or** : créditer au PJ actif uniquement, convertir pa/pc/pe explicitement
   (et le dire dans le bloc de crédit), plafonner au montant canonique de la salle,
   idempotence par salle (le double crédit vient probablement d'un re-jeu du
   rattrapage sans marqueur).
7. **Suivi de quête** : appeler `scenario_etape` automatiquement quand les
   conditions canoniques de l'étape sont réunies (boss vaincu + documents) ;
   alimenter `memoire.monstres_combat` à chaque clôture (toujours `null` ici).
8. **Protocole WS** : ajouter `pour` (destinataire) ou `tour_id` aux `dm`, et
   répondre **immédiatement** un événement terminal au demandeur d'un message
   refusé hors-tour.

---

# 6. Annexes

- Journal brut : `docs/audit_gruumsh4pj_journal.jsonl` (81 entrées horodatées, narrations complètes, tools, patches, snapshots d'état par tour).
- Notes d'audit en cours de partie : `docs/audit_gruumsh4pj_notes.md`.
- État de partie : `server/data/partie_gruumsh4pj.json`, chat : `server/data/chat_gruumsh4pj.json`.
- Fiches finales : via `GET /api/persos` (comptes Bastien/Chloe/Idris/Sacha, mot de passe `gruumsh2026`).
