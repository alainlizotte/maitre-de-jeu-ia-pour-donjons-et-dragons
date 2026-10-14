# Notes d'audit en cours de partie — « L'Œil de Gruumsh », table 4 PJ (gruumsh4pj)

## Environnement
- App : conteneur dnd35-mj :8123, LLM Qwen3.5-9B-Q4_K_M-MTP (llamacpp), RAG actif, 76 tools.
- Table : 4 comptes (Bastien/Thorrak nain guerrier, Chloe/Solenne humaine clerc,
  Idris/Liriel elfe magicienne, Sacha/Fenn demi-elfe rôdeur), tous niveau 1, fiches
  créées par API avec or de départ tiré aux dés (170/110/60/160 po).
- Scénario : laelith_loeil_de_gruumsh_ch01 (niv 1-3, 4 PJ), manifeste de donjon canonique.

## Problèmes de conception relevés (en cours de partie)

### P1 — Préparation de sorts fantôme (bloquant pour la magie)
Tour 2 (Liriel) : narration « prépare Armure du mage, Sommeil… » + tools
`preparer_sorts` puis… `repos_long`. État final : `prepares = {}` dans la fiche.
Tour 3 (Solenne) : « mémorise Bénédiction et Soin léger » avec **zéro tool** ;
`prepares = {}`. → la préparation NARRÉE n'existe pas dans l'état ; au moment
d'incanter, le serveur refusera honnêtement (slots/préparation absents). Le
joueur qui fait confiance à la fiction est sanctionné. Le rattrapage serveur
re-valide l'incantation narrée mais PAS la préparation déclarée.

### P2 — `repos_long` utilisé comme bouton « remise à zéro »
Pour « je prépare mes sorts du jour », le MJ a enchaîné `preparer_sorts`,
`lancer_d20` puis `repos_long` : un repos long restaure PV + tous les emplacements
sans nuit de jeu. Le MJ dispose d'un soin/refresh gratuit et illégal qu'il
déclenche à sa guise (déjà vu en audit « À la chasse aux gobs » F1).

### P3 — Hallucinations d'état dans la prose
Fenn porte « Flèches (20) x2 » (40 flèches) ; le MJ narre « il en possède 20 ».
La prose n'est pas réécrite sur l'état serveur (seuls les PV le sont).

### P4 — Ouverture expédiée + PNJ inventé
Aucun brief de mission, aucune accroche : dès le 1er message le groupe est
directement devant la grille du repaire (le picker place déjà le départ au
donjon) et le MJ a inventé un « capitaine des gardes » absent du module
(2 gardes + 2 molosses, point). Le personnage a aussi un « il » pour Solenne
(féminin) — l'apparence/sexe n'arrive pas dans la narration.

### P5 — Fuite de tokens non-latins dans la narration finale
« des grognements低沉 » (tokens chinois) rendus au client, non purgés par
l'assainisseur.

### P6 — Bible de scénario bruitée
`quete.bible.etapes` = ["Copyright Notice", "End Of License", "La Graine De
Nicolak"] : l'extraction d'étapes du PDF prend du bruit de pagination/copyright
comme étapes de quête ; la trame réinjectée à chaque tour contient des déchets.
`memoire.missions[0].avancement` = « 0/4 » sans unité (4 quoi ?).

### P7 — (infrastructure de test) Les broadcasts WS croisés
Le `dm` est diffusé à TOUTES les connexions : un pilote naïf lit le dm d'un AUTRE
joueur. Le client web s'en sort (verrou + attribution), mais l'ambiguïté
émetteur/destinataire rend le protocole fragile (aucun champ destinataire).

## Chronologie des tours
- T1 Bastien 45,1 s (inventaire_ajouter, carte_donjon_entrer) — entrée donjon.
- T2 Idris 62,3 s (preparer_sorts, lancer_d20, repos_long) — P1/P2.
- T3 Chloe 14,1 s (aucun tool) — P1.
- T4 Sacha 13,7 s (aucun tool) — P3 (20 flèches), nuit étoilée.


### P8 — Le MJ ignore l action du joueur actif et joue les autres PJ
Tour de Thorrak : le MJ a narré Liriel en train de lancer « confusion » (sort
qu elle ne connait pas et n a pas préparé) et un chien mordant Thorrak, sans
exécuter l action déclarée. Tools du tour : lancer_d20, fiche_perso_infliger_degats,
engager_combat ×2 + boucle de correction (2 relances, « Met en place le combat » ×2).
Les dégâts narrés sur Thorrak (« ses crocs s enfoncent… perdant ses points de vie »)
ne sont PAS dans l état (17/17) — réécriture PV uniquement à la clôture ?

### P9 — La rencontre canonique est amputée par la narration
Salle (0,0) = Bandit ×2 + Chien ×2 (manifeste). Après engager_combat, l état
ne contient PLUS que Chien et Chien (2) : les deux gardes (bandits) ont disparu
sans mourir (ni PV, ni ☠️). Combat suivant = moitié moins dur, XP amputée.

### P10 — Un message refusé hors-tour bloque l émetteur indéfiniment
sys turn_blocked est diffusé mais la connexion reste ouverte sans réponse
finale : le client (ou pilote) attend un dm qui ne viendra jamais. Pendant ce
temps, le moteur continue (timeout du tour de Liriel par le serveur au bout de
~200-300 s) → le joueur sincère perd son tour à cause de la tentation d agir
hors tour. Dure leçon : le refus doit clore proprement le tour du demandeur.

### P11 — La rotation d initiative n est pas matérialisée dans l état
initiative[*].joue / .passe restent null après les tours joués (Liriel, Chien) ;
seul courant_tour_pour bouge. Le client ne peut pas afficher la progression du
round depuis l état seul.


### P15 — XP payée pour un combat jamais livré (bloquant)
Bastien puis Chloe tentent d attaquer les 2 bandits (engager_combat ×4 puis ×3).
La salle étant marquée « nettoyée » (signature anti-repopulation F3), le
ré-engagement est refusé MAIS les tours se terminent en « victoire » avec
clôture automatique : +270 XP/perso payés pour des bandits qui n ont JAMAIS eu
de PV, d initiative ou de jet d attaque (540 XP avant même d avoir quitté la
première salle — moitié du chemin vers le niveau 2 à 1000 XP).

### P16 — Narration dupliquée entre deux tours
Le dm de Chloe (Solenne) est la reprise quasi verbatim du dm de Bastien
(« anti-répétition » défaillante), y compris le bloc XP. L action de Chloe n a
jamais été résolue.

### P17 — Erreurs tool brutes exposées au client
Le bloc « 🎲 Jets officiels du tour » affiche l erreur Python du tool :
« ⚠️ type_sauvegarde invalide : Réflexion. Attendu : Vigueur, Réflexes ou
Volonté ». Le MJ invente aussi une sauvegarde contre une attaque au corps à
corps (une masse ne permet pas de jet de sauvegarde).

### P18 — Quantités d équipement non fidèles à la création
Fenn créé avec « Flèches (20) x2 » → la fiche contient « Flèches (20) x1 »
(perte d un lot à la création ou à la première écriture de fiche).


### P20 — Exploit XP : marteler une action sur une salle « nettoyée » farm l XP
Trois « victoires » automatiques (ré-engagement refusé mais clôture payée) :
270 + 270 + 270 = 810 XP/perso pour UN seul vrai combat (2 chiens, 270 XP
légitimes). À 1000 XP = niveau 2 : on peut passer niveau 2 sans jamais livrer
un second combat, en répétant « j attaque les gardes » dans la même salle.

### P21 — Le repos long devient un outil de confort (×4 en ~2 h de jeu)
Quatre « 8 heures de sommeil » narrés sans coût (rations, fatigue, alerte des
ennemis) pour débloquer la mémorisation ; Thorrak gagne +1 PV par repos
(règle 3.5 correcte pour 8 h, MAIS la prose promet une restauration complète).
Le temps de jeu n a plus de sens.

### P22 — Tour rendu au client = message d erreur technique brut
« ⚠️ Le MJ a rencontré un problème technique. Réessayez dans un instant. »
comme narration complète d un tour — pas de repli narratif ni d état.

### P23 — Contexte spatial en retard sur la carte
Après le passage à l étage 1 (rez-de-chaussée, correct sur la carte), la
narration retourne décrire la cour d entrée (cadavres des molosses) et
tente un ré-engagement. La « salle courante » injectée au prompt ne suffit
pas à ancrer le MJ.

### P19 (bis) — La préparation de sorts ne passe QUE par dictée du JSON exact
Après dictage du dict JSON par le joueur : Liriel {Armure du mage:1, Sommeil:1} ✓
et Solenne {Bénédiction:1, Soins légers:1} ✓. Autrement dit : la mécanique
existe mais est inatteignable en langage naturel (liste native refusée, nom de
sort strict, aucune application partielle, rattrapage qui écrit {« Bénédiction »:0}).


### P24 — L Armure du mage n a aucun effet mécanique
Liriel incante Armure du mage (tool incanter_sort, emplacement dépensé, prose
« CA passe à 18 ») : la fiche reste CA 14, conditions vides. L emplacement est
consommé, l effet n existe nulle part.

### P25 — La carte dérape de deux étages puis ne suit plus
« La porte à l est du palier du premier étage » → carte à l ÉTAGE 3, salle
(1,0) (chambre) : tout le premier étage (bibliothèque, bureau, garde) sauté.
Les redescendes narrées (2 tours) ne remontent jamais la carte (aucun tool
appelé) ; la fiction joue l étage 2 pendant que la carte reste à l étage 3.
La seule synchronisation carte/fiction fiable reste carte_donjon_explorer —
que le MJ n appelle que ~3 fois en 60 tours.

### P26 — Ré-engagement refusé mais XP payée (déjà noté P15) + boss « déjà vidé »
Le refus anti-repopulation s est aussi déclenché CONTRE le combat du boss
(« Salle déjà vidée : Mez'Arate ont DÉJÀ été éliminés » — accord pluriel
cassé), avant d être finalement contourné par le MJ.

### P27 — « Timeouts » fantômes narrés qui mangent les tours
En combat, plusieurs actions déclarées DANS le bon tour sont narrées comme
« tour expiré après 200 s / 20 s » … 2 à 8 s après l envoi. Config réelle :
combat_turn_timeout_seconds = 300. Résultat : Solenne et Fenn ont perdu des
tours entiers pour des actions exécutées à l instant.

### (P28 retirée — erreur de l auditeur : le nain 3.5 a bien FOR 16/CON 18
dès la création ; c est la PROSE du MJ qui gonfle les bonus, le moteur trace
correctement « FOR 16 (+3) », BAB +2, total +5.)

### P29 — Crédit d or : montants et destinataires faux, double crédit
Butin canonique de la chambre du boss : 600 pa + 100 po + 6 gemmes (184 po).
Crédits observés : « +100 po à Thorrak (total 371) » au tour de LIRIEL ; fiche
Liriel 60 → 1660 puis → 3260 po (soit ~3200 po crédités = ~20x la valeur,
double crédit). Solenne, à qui le MJ a narré « 15 po + clé », n a rien reçu
(or inchangé 110). pa/pc/pe jamais convertis de façon lisible.

### P30 — La progression de quête reste « 0/4 » jusqu à la fin
Boss vaincu, rituel déjoué, butin pris : memoire.missions.avancement reste
« 0/4 », quete.progression et objectifs = null. scenario_etape n a jamais été
appelé (même sur demande explicite du joueur). Le suivi de scénario est
décoratif.

### P31 — Narrations tronquées / erreur technique comme tour
Un dm livré = « L'étape « Fouiller la cha » (25 caractères) ; deux tours
livrés = « ⚠️ Le MJ a rencontré un problème technique » (70 caractères).

### P32 — Mémoire : l historique tronque 73 messages anciens
« Historique tronqué : 73 messages anciens omis (budget 30000 chars) » — à
4 joueurs, la mémoire utile part à la poubelle avant la fin du chapitre.
