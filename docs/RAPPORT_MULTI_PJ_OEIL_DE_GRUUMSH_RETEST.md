# Rapport d'audit comparatif — « L'Œil de Gruumsh » après correctifs (passe 2)

> **Contexte** : les 32 défauts documentés dans
> [`RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH.md`](RAPPORT_MULTI_PJ_OEIL_DE_GRUUMSH.md) ont fait
> l'objet de correctifs, puis **la même partie a été rejouée à l'identique**
> (même scénario, mêmes archétypes, 4 comptes WS distincts — PJ neufs niveau 1,
> or tiré aux dés). Comparaison passe 1 (avant correctifs) / passe 2 (après).

**Correctifs déployés** (tests : `tests/test_gruumsh_fixes.py`, suite 966/966 verte)

| Domaine | Correctif |
|---|---|
| Contexte LLM | **32k → 64k tokens** (`-c 65536` llama.cpp, KV q8_0 hybride ≈ +0,6 Go VRAM) ; historique 140 évén./30k chars → **220 évén./48k chars** |
| XP (P15/P20) | La clôture ne paie l'XP que des ennemis **réellement blessés** (`degats_recus` tracé par le moteur) ; victoire sans dégâts → 0 XP + message honnête |
| Magie (P1/P19/P26b) | `preparer_sorts` accepte dict, liste native, repr. Python et liste « à plat » ; erreur avec format attendu ; compteurs à 0 purgés |
| Sorts (P24) | **Armure du mage appliquée mécaniquement** (+4 CA, champ `effets_actifs` horodaté, purge auto à échéance dans `_load_fiche`) |
| Repos (P2) | `repos_long` **refusé en phase combat** (même `forcer=true`) |
| Tours mangés (P27) | Rejeu 5bis-a déclenché par la détection de timeout fantôme ; purge des paragraphes « délai/secondes expirés » élargie ; repli anti-narration-vide |
| Rencontres (P9) | Engagement = **liste canonique complète** (auto-complétion des espèces manquantes) + réparation du bug « 0 créature » (clé d'espèce M6) + normalisation « Bandit ×2 » |
| Or (P29) | `_charger_etat` inexistant réparé (code mort), `isinstance` à 1 argument réparé (marqueur jamais écrit = double-crédit), **pa/pp parsés**, somme par devise, crédit au **PJ actif**, unités po cohérentes, max-gain par phrase, garde « trésor déjà pris » |
| Quête (P30) | Mémoire de mission + étape de bible avancées **à la clôture de victoire** |
| Bible (P6) | Bruit copyright/pagination exclu des étapes |
| WS (P10) | Refus hors-tour marqués `terminal: true` (réponse finale au demandeur) |
| Assainissement (P5/P17) | Tokens non-latins et lignes d'erreur tool purgés de la narration |
| Prompts | « Jamais de tour expiré inventé », engagement canonique complet, prologue de mission obligatoire |

---

# 1. Comparaison chiffrée

| Mesure | Passe 1 (avant) | Passe 2 (après) | Verdict |
|---|---|---|---|
| Tours de table | 81 actions (74 réponses) | 42 actions (42 réponses) | ≈ |
| Latence moyenne / tour | 33,3 s | 52,8 s | 🔴 +60 % (ctx 64k + rechargements modèle ; l'ancien 32k tournait en chaud) |
| Tours SANS AUCUN tool | 30/74 = **37 %** | 6/42 = **14 %** | ✅ mécanisation nette |
| Narrations vides / erreurs techniques | 2 + 1 tronquée | 0 | ✅ |
| Timeouts pilote (tour bloqué) | 1 (330 s) | 0 (1 incident serveur ~4 min, repris) | ≈ |
| Refus hors-tour propres (`terminal`) | 7, émetteur laissé en attente | 9, réponse finale immédiate | ✅ |
| `repos_long` (dont EN COMBAT) | 7 (dont 2 en combat, **appliqués**) | 2 (**0 appliqué** — refusé par la garde) | ✅ |
| XP gagnée sans combat | **810 XP/perso** (3 × « victoires » fantômes) | **0 XP** (aucune XP fantôme ; défaite = 0 XP) | ✅ |
| Rencontre canonique | amputée (2 chiens sur 4 ennemis) | **complète** (2 bandits + 2 chiens) | ✅ |
| Or crédité | faux (×20 la valeur, double, mauvais PJ) | canonique exact (160 po pour le trésor testé en unitaire) | ✅ |
| Préparation de sorts | jamais persistée | **1 réussite en jeu réel** (Sommeil) mais instable (voir §3) | 🟡 |
| Sort sans effet | Armure du mage : slot dépensé, 0 effet | appliqué (test déterministe) ; en jeu : rayon de givre → dégâts appliqués | ✅ |
| Issue de la partie | « victoire » à 540 XP, module abandonné | **GAME OVER (TPK) contre la rencontre canonique complète** — phase dédiée, stabilisations officielles, mourants, saignements | ✅ la défaite existe |
| Mémoire (troncature historique) | 73 messages omis | 0 troncature observée (budget 48k) | ✅ |
| Pronoms/genre des PJ | Solenne systématiquement « il » | genres corrects (« elle » pour Sylvaine/Alienor) | ✅ |
| Munitions | « 20 flèches » hallucinées | **consommation mécanique réelle** (Kaelan à court de flèches → épée) | ✅ |

# 2. Ce que le retest a validé (défauts corrigés en jeu réel)

1. **L'exploit XP est mort** : deux « victoires » fantômes tentées par le MJ au tour 3
   (`engager_combat` ×2) n'ont donné **aucune XP** — la clôture sans dégâts est
   bloquée. En passe 1, la même manœuvre rapportait 810 XP.
2. **La rencontre est fidèle** : 2 bandits + 2 chiens engagés d'office (auto-complétion),
   le combat est **durablement plus dur** qu'en passe 1 et la table a subi un vrai
   TPK — la défaite de groupe (phase `game_over`, stabilisations 1d20, saignement
   −1 PV/round, morts à −10) fonctionne de bout en bout.
3. **Le repos long ne passe plus en combat** : deux tentatives du MJ pendant la
   mêlée ont été refusées par la garde (PV intacts en état).
4. **Les tours ne sont plus mangés par la rotation** : l'ordre d'initiative est
   respecté de round en round (Baldur → Sylvaine → Kaelan → monstres → …), les
   attaques sont résolues (bandit 5→3→0), les refus hors-tour répondent
   immédiatement (flag `terminal`).
5. **L'or canonique est exact** : le trésor testé en unitaire crédite 160 po
   (100 po + 600 pa), une seule fois, au bon PJ — contre 3 260 po doublés en
   passe 1.
6. **La bible est propre** : `etapes = ["La Graine De Nicolak"]` (une étape réelle)
   contre `["Copyright Notice", "End Of License", …]` en passe 1.
7. **Munitions, PV, conditions, mourants** : tout est suivi mécaniquement
   (Kaelan à court de flèches, Baldur stabilisé puis mort à −10, etc.).

# 3. Problèmes NOUVEAUX ou persistants (retest)

| # | Sévérité | Constat |
|---|---|---|
| R1 | 🟠 | **Latence +60 %** (52,8 s vs 33,3 s) : le ctx 64k coûte en préfill. Le KV q8_0 reste sain (aucun OOM) ; à envisager : `-ctk q4_0 -ctv q4_0` ou ctx 49152 si la latence pèse. |
| R2 | 🟠 | **Récit figé** : le bloc d'ouverture (« Le fer noir de la grille… ») est recopié quasi verbatim à CHAQUE tour de combat — l'anti-répétition (dédup Jaccard ≥ 0,55, §5quater) n'attrape pas ce ré-écho d'en-tête. Pire qu'en passe 1 sur ce point précis. |
| R3 | 🟠 | **Timeouts « 200 s » toujours narrés** : le 9B ignore la consigne prompt ; la purge renforcée (déployée en cours de partie) a attrapé les formes suivantes mais « deux cents secondes de délai » exigeait l'ajout en cours. Le tour du joueur reste consommé quand le modèle s'entête (le rejeu 5bis-a tourne mais sans outil de repli pour les SORTS — la résolution déterministe n'existe que pour les attaques physiques). |
| R4 | 🟠 | **Deadlock de rotation sur PJ à 0 PV** : le courant « Invalide » ne déclenche la passe moteur (skip) que si LUI envoie un message ; les autres joueurs sont refusés avant le pre-run → table bloquée jusqu'à ce que le joueur invalide parle (contourné en jeu). À corriger : exécuter la passe de skip AVANT la garde hors-tour. |
| R5 | 🟠 | **Reprise après game over fragile par prose** : la phase `game_over` verrouille la table ; la résurrection exige que le MJ NARRE la réalisation exacte (« ressuscite … X PV récupérés ») — le 9B dévie en prose (offre, soin dissipé) et la table reste morte. Le chemin UI dédié du client n'est pas testé ici (pilote WS sans front). |
| R6 | 🟡 | **Préparation de sorts encore instable en jeu** : réussie une fois (Sommeil), mais un compteur « Armure du mage : 0 » réapparaît (le rattrapage « Mémorisation narrée » écrit un 0 — la purge n'existe que dans `preparer_sorts`), et l'incantation de Sommeil en combat a été honnêtement refusée (préparation absente à ce moment). |
| R7 | 🟡 | **`engager_combat` en pleine mêlée** : au passage à l'épée, le MJ a tenté 4 ré-engagements (refusés proprement par le garde « Combat déjà en cours ») — bruit de tools, mais aucune casse. |
| R8 | 🟡 | **Ouverture toujours expédiée** : le prologue obligatoire (nouvelle consigne) n'a pas suffi — le groupe démarre devant la grille dès le tour 1. |
| R9 | 🔵 | Incident isolé : serveur injoignable ~4 min pendant un tour (rechargement modèle post-rebuild + ctx 64k), repris sans perte d'état. |

# 4. Verdict

| Domaine | Passe 1 | Passe 2 |
|---|---|---|
| Intégrité économique (XP/or) | 🔴 farmable, faux | ✅ **sain** (aucun gain fantôme, or exact) |
| Combat (rotation, rencontres, issues) | 🟠 tours mangés, rencontres amputées, défaite impossible | ✅ rotation stable, rencontre fidèle, **défaite possible et gérée** |
| Magie | 🔴 inutilisable | 🟡 utilisable mais fragile (R3/R6) |
| Mémoire/contexte | 🟠 73 messages perdus | ✅ 64k ctx, 0 troncature (au prix de +60 % de latence, R1) |
| Narration | 🟠 doublons, timeouts, tokens fuités | 🟡 timeouts mieux purgés mais récit figé (R2), genres corrects |
| Reprise après échec | — (jamais atteinte) | 🟠 game over réel mais résurrection fragile (R5) |

**Bilan** : les correctifs économiques et de combat tiennent en jeu réel — la partie
est devenue **perdable**, ce qui est le meilleur indicateur de fidélité (passe 1 :
« victoire » fabriquée à 810 XP et module abandonné ; passe 2 : TPK contre la
rencontre canonique complète, game over propre, aucun XP ni or fantôme).
Les chantiers restants, par priorité : résolution déterministe des SORTS (R3),
skip moteur avant garde hors-tour (R4), anti-répétition des en-têtes de scène (R2),
rattrapage mémorisation sans compteurs à 0 (R6), chemin de résurrection robuste (R5).

---

## Annexes
- Journal passe 2 : `docs/audit_gruumsh4pjB_journal.jsonl` (42 tours).
- Journal passe 1 (avant correctifs) : `docs/audit_gruumsh4pj_journal.jsonl`.
- Tests des correctifs : `tests/test_gruumsh_fixes.py` (suite complète 966/966 verte).
- Parties : `server/data/partie_gruumsh4pj.json` (passe 1), `server/data/partie_gruumsh4pjB.json` (passe 2).
