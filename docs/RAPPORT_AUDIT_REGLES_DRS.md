# Rapport d'audit — couverture des règles DRS (D&D 3.5) dans l'application

**Date :** 14 octobre 2026
**Statut : ✅ IMPLÉMENTÉ ET VALIDÉ (15 octobre 2026)** — les chantiers P1, P2 et P3
de ce rapport ont été codés puis validés : **970 tests pytest verts** (0 échec,
3 sauts), smoke test dédié `tests/smoke_drs.py` (15/15) et **E2E piloté avec LLM
réel** `tests/e2e_regles_drs.py` (**33 vérifications vertes, 0 échec**) : combat
avec feinte (`attaque_speciale`), butin DMG par ND crédité, chute
(`danger_chute`), voyage avec climat par région (`voyage_demarrer` +
`jours_meteo` persistés) et repas (`voyage_repas`). Corrections de bugs réels
découvertes par les E2E : outils nouveaux masqués par les listes de phase de
l'orchestrateur (ajoutés aux 4 ensembles), ré-engagement en plein combat
(réinitialisation zombie → refus serveur avec orientation
`combat_ajouter_combattant`), alternatives d'attaques du bestiaire (« ou … »)
jouées comme des attaques successives (exclues), ajustement temporaire
d'équilibrage perdu sur les attaques multiples (réappliqué par attaque).

**Périmètre :** comparaison entre l'application (serveur Python/FastAPI + MJ-LLM, client React) et le
chapitre « Combat », le chapitre « Aventure » (dont « Environnement ») et la section « Trésors » de la
DRS (regles-donjons-dragons.com, pages 1530–1675 et 1716).
**Méthode :** lecture des pages de règles + exploration exhaustive du code (grep + lecture ciblée),
rattachement de chaque règle à son état : ✅ mécanique serveur, ⚠️ partiel / textuel (prompt au MJ),
❌ absent.

---

## 1. Synthèse générale

| Domaine DRS | Verdict | Ce qui est câblé | Ce qui manque |
|---|---|---|---|
| **Combat** | ✅ noyau solide, ⚠️ périphérie | Attaques anti-triche, dégâts validés, sauvegardes, initiative persistante, PV monstres suivis, mortalité 0/−10, XP + montée de niveau | Critiques non confirmés, RD/résistances inertes, itératives, AOO, surprise, conditions sans effet, tactique |
| **Environnement** (9 dangers) | ❌ majoritairement absent | Chaleur & faim/soif évoquées en voyage (texte) | Chutes, noyade, asphyxie, froid, fumée, acide : rien ; chaleur/faim jamais appliquées |
| **Aventure — voyage** | ✅ le mieux intégré | Allures, 7 terrains, rencontres, égarement, marche forcée, encombrement calculé | Effets de la charge, froid/neige, climat complet, se perdre simplifié, rations/prospection |
| **Aventure — pièges** | ✅ | Parsing + résolution serveur (jets de save, dégâts, anti-re-déclenchement) | Pièges à compétence non interceptés, déclencheurs/remise en place narratifs |
| **Trésors** | ⚠️ scripté | Trésor canonique de salle crédité mécaniquement (anti-double, anti-spoiler) | Aucune génération aléatoire par ND ; bestiaire sans trésor ; combats hors scénario ne rapportent rien |
| **Aventure — le reste** | ❌/⚠️ | — | Plans cosmologiques, classes de PNJ, matériaux spéciaux jouables (impact faible, voir §6) |

**Architecture constatée :** « le LLM raconte, le serveur décide ». Les règles non codées sont
déclarées comme telles dans `server/prompts/sections/combats.md` et confiées au MJ-LLM, avec des
filets de rattrapage dans `server/main.py` (attaque narrée sans jet, dégâts oubliés, pièges, trésors).
Les recommandations ci-dessous réutilisent systématiquement ces patterns existants.

---

## 2. Environnement (DRS pages 1633–1642)

| Danger | État | Détail |
|---|---|---|
| Les chutes | ❌ | Pas de règle 1d6/3 m (max 20d6), ni saut délibéré (Saut DD 15), ni surface meuble, ni chute dans l'eau. Seulement chiffré en dur dans des textes de pièges de scénarios |
| Asphyxie / noyade | ❌ | Pas de « 2 rounds × CON puis tests CON DD 10+1 ». Narratif uniquement (pièges eau/sables, bestiaire « retenir son souffle ») |
| L'eau | ❌ | Pas de courant (Natation/Force DD 15), pression en profondeur, eau glaciale, ni combat sous-marin. Seul le test de Natation générique existe |
| Le froid | ❌ | Absent de la table météo (pas de neige), pas de glace (case double, DD +5). Sorts seulement |
| La fumée | ❌ | Pas de Vigueur DD 15/round, toux, camouflage 20 % |
| L'acide | ❌ | Flasque en boutique + attaques de monstres descriptives ; pas de danger d'environnement (1d6/round, immersion 10d6, émanations VIG DD 13) |
| La chaleur | ⚠️ | Canicule dans `_METEO` (`server/tools/voyage.py`) mais effets purement textuels : jets de CON jamais forcés, dégâts jamais appliqués |
| La faim et la soif | ⚠️ | Rations en boutique (poids OK), repas d'auberge (Affamé/Rassasié), mais pas de consommation en voyage, pas de compteurs, pas de tests CON DD 10+1, pas de prospection (Survie) |
| L'obscurité | ⚠️ cosmétique | Vision nocturne/dans le noir descriptives, sorts de lumière OK ; aucune mécanique « aveuglé » (50 % de raté, −2 CA, vitesse ÷2) |

**Sections voisines :** Climat (1628–1632) ⚠️ table simplifiée 6 entrées, jour 1 seulement, non
persistée, pas de neige/vents ; Extérieur (1620–1627) ✅ se perdre (simplifié DD 10+n vs table
DD 6–15) + 7 terrains, ❌ dangers propres aux terrains (avalanches, crues…) ; Déplacements
(1610–1614) ✅ longue distance, ❌ tactique/courte distance ; Poids transportable (1609) ✅ calcul +
catégories, ⚠️ effets textuels.

---

## 3. Chapitre Combat (DRS pages 1530–1607)

### 3.1 Solide (ne pas toucher)
- Jet d'attaque : `lancer_attaque` (`server/tools/dice.py:527`) avec CA officielle recoupée serveur
  (`_ca_officielle`, dice.py:207), refus de cibles invalides, substitution d'arme, finesse.
- Dégâts validés (dice.py:943), FOR ×1,5 à deux mains, arme magique, mini 1 dégât.
- Sauvegardes (dice.py:1113) avec dons ; CA (persos.py:1096) armure/bouclier/Dex plafonné + magie par type.
- Initiative persistante + rotation serveur des monstres (`server/game/combat.py:777 boucle_auto`,
  `_attaque_auto`), PV monstres réels (`state.py:1079`), mortalité 0/−10/−10, mourant, stabilisation
  1d20≥10, coup de grâce auto, soins, XP DMG + montée de niveau avec jet de dé de vie (`game/xp.py`).
- Filets anti-omission dans `main.py` (attaque sans jet, dégâts oubliés, dégâts excessifs).

### 3.2 Manques constatés

| Règle | État | Où ça se joue |
|---|---|---|
| Confirmation des critiques (seuil, confirmation, ×2/×3/×4) | ⚠️ menace affichée, jamais confirmée ; monstres auto jamais | `dice.py:815-928`, `combat.py:405` |
| RD X/… et résistances énergie des monstres | ❌ texte inerte (75+ entrées bestiaire non parsées) | `combat.py` (`_attaque_auto`, `_infliger_degats_monstre`), `tools/sorts.py` |
| Attaques itératives (−5/−10) et naturelles multiples | ❌ 1 seule attaque/monstre/tour (`.split("/")[0]`) | `combat.py:240, 214-224, 390-430` |
| Effets mécaniques des conditions | ❌ un seul canal : skip de tour. Renversé/Aveuglé/Fatigué/Empêtré/Invisibilité = affichage sans effet | `combat.py:97-115`, `fiches.py:2261-2270` |
| Round de surprise + pris au dépourvu (perte Dex CA) | ❌ narratif | prompt `combats.md:122-125` |
| Attaques d'opportunité | ❌ narratif (déclaré « non codée ») | prompt |
| Modificateurs : couvert, camouflage 20/50 %, flanc, taille, tir en mêlée −4 | ❌ (seul tir de près +1 ≤ 9 m est mécanique) | `dice.py:516-523` |
| Attaques spéciales (désarmement, immobilisation, renversement, aider autrui…) | ❌ (dons au catalogue sans jet ni effet) | — |
| Combat à deux armes (malus −6/−4, FOR ×0,5) | ❌ | prompt seul |
| Déplacement tactique / position (pas de 1,50 m, grille) | ❌ théâtre de l'esprit | `retraite_combat` = seul déplacement |
| Préparer une action / attendre (ready/delay) | ❌ | `terminer_mon_tour` passe juste la main |
| Dégâts non-létaux (réserve dédiée) | ❌ | — |
| CA de contact, malus de portée, PV non-létaux cumulés | ❌ | — |

---

## 4. Trésors (DRS page 1716)

- ✅ **Trésor canonique de scénario** : champ `tresor` des 197 salles, parseur serveur
  (`main.py:722-1000`) qui crédite or + objets quand le MJ narre la fouille, avec anti re-crédit
  (`tresor_pris`), bornes anti-fiction, filtrage anti-spoiler. Objets de quête via
  `inventaire_ajouter` (portée « quête »).
- ❌ **Aucune génération aléatoire** : pas de tables ND 1–20 (3×d100 pièces/biens/objets), pas de
  tables gemmes/objets d'art, pas de champ trésor dans le bestiaire, pas de valeur moyenne par ND.
  `combat.py:569-577` refuse explicitement d'inventer du butin mécanique → les combats hors
  scénario (voyage, monstres errants) ne rapportent que de l'XP.
- ⚠️ Le catalogue boutique couvre une bonne partie des « objets non magiques » des tables, mais rien
  n'est *distribué* aléatoirement.

---

## 5. RECOMMANDATIONS PRIORITAIRES

Légende : effort S = quelques heures · M = ~1 jour · L = plusieurs jours. Impact = effet ressenti en jeu.

### P1 — Fort impact, effort faible (faire en premier)

| # | Recommandation | Où | Effort | Impact |
|---|---|---|---|---|
| 1 | **Confirmer les critiques** : à la menace, second jet serveur vs CA ; appliquer le multiplicateur (×2/×3/×4 via `catalogue.py`) ; étendre à `_attaque_auto` | `dice.py:815-928`, `combat.py:405` | S | Très fort — fréquent, attendu par les joueurs |
| 2 | **Parser les RD et résistances énergie du bestiaire** (« réduction des dégâts (10/fer froid) », « résistance au feu (10) ») au chargement du monstre, soustraire avant application, gérer le contournement par arme magique/matériau | `combat.py` (`_attaque_auto`, `_infliger_degats_monstre`), `tools/sorts.py:232-271` | M | Très fort — les dragons/golems/démons retrouvent leur identité |
| 3 | **Attaques itératives + naturelles multiples** : parser « +12/+7/+2 » et les segments « ; » du bestiaire, jouer les attaques multiples dans `boucle_auto` | `combat.py:214-266, 390-430` | M | Fort — équilibrage des gros monstres |
| 4 | **Effets mécaniques des conditions principales** : Renversé (−4 attaque, Dex perdue à la CA, AOO), Aveuglé (50 % de raté, −2 CA), Fatigué (−2 For/Dex), Épuisé (−6, vitesse), Empêtré/Agrippé (−2, pas de Dex), Invisible (20 % de rater), Pris en tenaille (+2) — brancher ces modificateurs dans `lancer_attaque`/`_ca_officielle`/`lancer_sauvegarde` | `dice.py`, `combat.py:97-115` | M | Fort — les sorts/monstres qui posent ces conditions deviennent réels |
| 5 | **Appliquer la météo/chaleur côté serveur** (rattrapage) : si canicule active en voyage, forcer les jets de CON/h (DD 15 +1/precedent) et appliquer 1d4 non-létaux ; réutiliser le pattern « rattrapage pièges » de `main.py` | `voyage.py` (`_METEO`), `main.py` | M | Fort — le voyage devient dangereux réellement |

### P2 — Fort impact, effort moyen

| # | Recommandation | Où | Effort | Impact |
|---|---|---|---|---|
| 6 | **Dégâts de chute** : règle générale 1d6/3 m (max 20d6), interopérer avec les pièges-fosses existants (le parseur connaît déjà « chute de 3 m, 1d6 ») et le sort Ralentissement de la chute | `cartes.py` (parseur pièges), nouveau module `game/dangers.py` | M | Fort — pièges fosses, corniches, donjons |
| 7 | **Noyade / asphyxie** : rounds de souffle 2×CON, tests CON DD 10+1, séquence 0/−1/mort ; brancher sur les pièges à liquide/gaz et la natation | `game/dangers.py`, `cartes.py`, `dice.py` (Natation) | M | Fort — souvent lié aux pièges existants |
| 8 | **Rations, eau, prospection** : consommer 1 ration/jour en voyage (si inventaire), tests CON (soif : 1 j + 1 h/CON, DD 10+1 ; faim : 3 j, DD 10+1) → 1d6 non-létaux + Fatigué ; Survie DD 10-15 pour se nourrir hors rations | `voyage.py`, `inventaire.py`, `marche.py` | M | Moyen-fort — donne un prix à la logistique |
| 9 | **Climat complet et persistant** : table d100 par région (froid/tempéré/désert), neige/blizzard, vents (pousser/renverser), météo tirée chaque jour de voyage et persistée dans `etat["voyage"]` | `voyage.py` | M | Moyen — variété + lien avec P1-#5 |
| 10 | **Round de surprise + pris au dépourvu** : test Perception/Discretion à l'engagement, flat-footed au round 1 (perte du bonus Dex à la CA), déjà calculable via la CA plafonnée Dex | `state.py:engager_combat`, `dice.py:207` | M | Moyen-fort — fréquent (embuscades) |
| 11 | **Générateur de trésor par ND** pour les combats hors scénario : tables ND 1–20 + gemmes + objets non magiques (réutiliser le catalogue boutique), appelé dans `cloturer()` à côté de `_distribuer_xp`, crédit via le parseur `tresor` existant | nouveau `game/tresor.py`, `combat.py:cloturer` | M | Moyen — récompense les rencontres libres |
| 12 | **Malus de charge appliqués** : Lourde → malus −6 type malus d'armure aux tests de For/Dex et vitesse réduite ; brancher dans `lancer_d20` (compétence) et affichage existant déjà complet | `inventaire.py:556-694`, `dice.py:278-380` | S-M | Moyen — le suivi existe déjà, il ne manque que l'effet |

### P3 — Backlog (utile mais secondaire)

| # | Recommandation | Effort | Remarque |
|---|---|---|---|
| 13 | « Se perdre » complet : DD par terrain/visibilité (6–15), reconnaissance d'égarement (DD 20 −1/h), fixation de direction (DD 15 +2/h) | S | `voyage.py:325-342` remplace la règle simplifiée |
| 14 | Pièges à compétence interceptés (Fouille DD 20 / Désamorçage DD 20 forçés comme les pièges à save) | S | `cartes.py:1536-1587` |
| 15 | Dégâts non-létaux (réserve dédiée + attaque à mains nues / arme à -4) | M | conditionne chaleur/faim (P1-#5, P2-#8) |
| 16 | AOO simplifiées (sans grille : 1/round si l'ennemi narré se déplace/incante au contact, jet gratuit du monstre dans `boucle_auto`) | M | peut rester semi-narratif |
| 17 | Froid/neige + glace dans `_METEO` ; acide/fumée dans `dangers.py` | S-M | complète le module dangers (P2-#6/7) |
| 18 | Concentration complète : comparer le jet au DD 10+dégâts serveur, perdre le sort en cas d'échec | S | `tools/sorts.py:203-225` |
| 19 | Bonus « Résistance +N » des objets aux sauvegardes (détection comme la CA magique) | S | `fiches.py:552-577` |
| 20 | Attaques spéciales : commencer par aider autrui + renversement (jets opposés génériques) ; désarmement/immobilisation ensuite | M-L | gros chantier, valeur moindre en théâtre de l'esprit |
| 21 | Préparer une action / attendre (ré-insertion dans le round) | M | modifie la boucle d'initiative |
| 22 | Matériaux spéciaux à la boutique (mithral, adamantium, argent alchimique, fer froid) + interaction avec les RD (dép. P1-#2) | S-M | |
| 23 | Effets de la charge sur la VD de voyage (already VD raciales) et « vitesse jamais réduite » du barbare | S | |
| 24 | Levels d'éclairage mécaniques (obscurité → aveuglé pour les non-voyants) | M | dép. P1-#4 ; le brouillard de guerre visuel du donjon suffit en partie |

### Non recommandé (laisser au RAG / narratif)

- **Plans cosmologiques** (Astral/Éthéré, échange planaire) : aucun scénario ne les exploite ; les
  sorts existent déjà. À câbler uniquement si un scénario planaire est écrit.
- **Classes de PNJ** (adepte, expert, gens du peuple, aristocrate) : le bestiaire + la génération
  LLM suffisent pour des PNJ narratifs.
- **Grille de combat / position exacte** : le théâtre de l'esprit est un choix d'architecture
  cohérent avec le format chat ; implémenter une grille coûterait très cher pour peu de gain.
  Le flanc (P1-#4) peut être tranché narrativement sans grille.
- **Combat à deux armes complet, CA de contact, malus de portée par incrément** : règles réelles
  mais rarement décisives en théâtre de l'esprit ; les prompts `combats.md` suffisent en attendant.

---

## 6. Ordre de marche suggéré

1. **P1-#1 → #4** (critiques, RD/résistances, itératives, conditions) : même zone de code
   (`dice.py` + `combat.py`), transforme la fidélité des combats sans toucher à l'architecture.
2. **P1-#5 + P2-#9** (météo appliquée et complète) : même fichier `voyage.py`.
3. **P2-#6 → #7** (module `game/dangers.py` : chutes, noyade, asphyxie) : réutilise le parseur de
   pièges et les tools de dégâts existants.
4. **P2-#8 → #12** (logistique de voyage et effets de charge).
5. **P2-#10, #11** (surprise, trésor par ND) puis le backlog P3 selon les besoins du jeu.

Chaque chantier est indépendant, testable via le harnais e2e existant (`tests/e2e_audit_scenario_reels.py`).

*Rapport généré à partir de l'audit des sources DRS (regles-donjons-dragons.com) et du code du dépôt.*
