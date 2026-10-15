# Rapport passe 5 — vérification des correctifs (mystra2pj, Crown of Mystra ch.2)

> **But** : rejouer une partie APRÈS les correctifs R1-R6 + S1/S2 + R7 +
> garde cohérence + purge Rassasié + watchdog, et vérifier chaque réglage.
> Table : Thorvold (Guerrier niv 2), Aldemar (Clerc), Eliane (Druide),
> Finnick (Voleur) - XP 1035, ch.2 « huit gemmes ». 29 tours joués.

# 1. Vérification correctif par correctif

| Correctif | Verdict en jeu réel | Preuve |
|---|---|---|
| C1 prix marché | ✅ **corrigé** | Finnick demande le stock → marche_stock appelé → **prix corrects cités** (« 1 po la douzaine, 10 po pour 12 ») - plus de prix x11 inventé. L achat des « Flèches (20) » est refusé honnêtement (le marchand vend à la douzaine). |
| C3 voyage bloqué | ✅ **corrigé** | Le voyage a démarré immédiatement (ch.2, pas de garde de trame bloquante). |
| C5 conditions résiduelles | ✅ **corrigé** | Toutes les fiches repartent **propres** (conditions [], PV restaurés). |
| P2 repos en combat | ✅ **confirmé** | repos_long appelé par le MJ → **refus** du tool, cité dans la narration (« Le repos est impossible en plein combat ! »). |
| P15/P20 XP fantôme | ✅ **confirmé** | Gobelin 5 PV jamais tué → **aucune XP distribuée** (le filet P15 tient). |
| R2 dédup inter-tours | ✅ | Narrations non figées dans ch.2 (le bloc d ouverture du ch.1 n est PAS recopié). |
| R7 filet jet-joueur | 🟡 **partiel** | Le MJ lance ses propres jets (lancer_d20 pour Perception auditive du groupe) au lieu de poser jet_attendu. Le bouton client est prêt mais non déclenché par ce style de MJ. |
| Watchdog C8 | 🟠 **partiel** | Un tour de 240 s a encore pendu (Thorvold) : le watchdog couvre les **RequestError** (backend qui rejette) mais PAS les requêtes qui **pendent** (llamacpp reçoit et ne répond pas). Le timeout httpx (300 s) > timeout pilote (240 s). |

# 2. Problèmes restants observés (passe 5)

| # | Sévérité | Problème | Fix proposé |
|---|---|---|---|
| D1 | 🟠 | **Rotation bloquée après attaque ratée** : Thorvold attaque (lancer_attaque, raté) → le courant devrait avancer à Aldemar mais reste sur Thorvold. Les actions suivantes sont rejetées (tour bloqué) → boucle. | Vérifier que lancer_attaque raté consomme bien l action. |
| D2 | 🟠 | **Sommeil confondu avec repos** : Eliane lance Sommeil (préparé) → le MJ appelle repos_long au lieu d incanter_sort (mot « sommeil » dans le message → garde-fou repos déclenché). | Ne pas déclencher le garde-repos quand le texte contient aussi un verbe d incantation. |
| D3 | 🟡 | Le filet sorts (R3) est masqué par le rattrapage 5quater-s qui tourne en premier (même résultat : refus honnête). À unifier. |
| D4 | 🟡 | Timeout pilote (240 s) < timeout serveur (300 s) : les watchdogs ne se chevauchent pas. | Réduire le timeout httpx chat à 240 s. |

# 3. Ce qui fonctionne confirmé

- **Chapitre 2 démarre** : état propre, PV restaurés, conditions pures, quête chargée.
- **Marché** : prix corrects (1 po/douzaine), refus d achat honnête.
- **Voyage** : démarré sur demande explicite (forcer), jours narrés.
- **Préparations** : dict JSON dicté fonctionne (Aldemar : Soins légers 3, Bénédiction 1 - via le rattrapage).
- **Repos en combat** : refus honnête cité par le MJ.
- **Pas d XP fantôme** : gobelin jamais touché → 0 XP.
- **Filet jet-joueur** : bouton client prêt (TSC OK), détection active - le style de MJ actuel (self-rolls) ne le déclenche pas encore.

# 4. État final

Phase combat (gobelin 5 PV jamais touché - les dés n ont pas tourné en
faveur du groupe pendant la fenêtre de test), PJ intacts. Chapitre 2 jouable.

---

*Journal : docs/audit_mystra2pj_journal.jsonl. Partie : partie_mystra2pj.json.*