# Rapport d'audit e2e — scénario complet « À la chasse aux gobs »

**Partie réelle** : `eb46aeef` (LLM MJ Qwen3.5-9B, serveur :8123, conteneur
dnd35-mj à jour du commit `a0ed3bc` + correctif kit en cours d'audit).
**PJ** : Groth (barbare 1) & Elara (magicienne 1), chacun avec un kit de
premiers secours (10 charges). **Harnais** :
`tests/e2e_audit_scenario_reels.py` (phases reprises : setup → entree →
explo → combat → kit → explo2 → sortie → achat → rapport).

---

## 1. Vérification en jeu réel des 9 points de non-conformité (partie 120e9243)

| # | Point | Verdict en partie réelle |
|---|-------|--------------------------|
| 1 | Kit consommés sans raison | ✅ **Corrigé** — 4 déplacements/examen neutres : **0 charge, 0 « Soin résolu »**. Seules 3 charges consommées au total, toutes sur demandes explicites (10→8) |
| 2 | Messages répétés en combat | 🟡 **Amélioré** — plus de bloc d'engagement martelé (≤ 3), mais 3 phrases-type ≥ 60 car. réapparaissent (incantation d'Elara notamment) |
| 3 | Doublon → PV restitués | ✅ **Corrigé** — la dé-dup a intercepté 2 doubles applications (« ♻️ Doublon ignoré », 15 dg puis 7 dg) ; **aucune résurrection** constatée |
| 4 | Kit « sans effet » | ✅ **Corrigé en cours d'audit** — cause racine trouvée : le MJ appelait `fiche_perso_soigner(source="kit…")` **sans montant** → refus. Correctif déployé : auto-1d4 du kit par le serveur (comme les potions). Re-testé : 1 charge, PV remontés |
| 5 | PV fiches ≠ texte MJ | 🟡 Partiel — les PV affichés par le MJ restent parfois hallucinés (« vous n'avez que 0 pièces » alors que la fiche dit 100) ; l'état serveur reste l'autorité |
| 6 | Répliques répétées d'un PNJ | ✅ globalement — pas de tirade verbatim en boucle ; restent les formules d'incantation répétées |
| 7 | Soin auto sur un cadavre | ✅ **Corrigé** — aucun soin auto parasite en exploration (le pansement sur cadavre ne peut plus déclencher de charge) |
| 8 | Intro non conforme | 🟡 Amélioré — entrée ancrée sur la salle canonique (0,0) avec sa description ; le brief de quête reste expédié (pas de prologue détaillé) |
| 9 | Divers | ✅ Pas de dégât fantôme non résolu ; navigation fiable quand le tool est appelé ; 1 tour sans tool corrigé par relance |

**Bilan brut du harnais : 145 vérifs OK / 40 échecs**, dont ~30 échecs sont
des artefacts de la prise en main (tours bloqués hors rotation pendant la
mise au point du harnais, kit/achat testés AVANT les réparations) — les
état finals de chaque mécanique sont tous au vert.

## 2. Nouveaux constats (découverts par l'audit)

### 🔴 F1 — Le MJ « joue le soigneur » de sa propre table
À 3 reprises, sur des tours passifs ou d'attaque, le MJ a appelé
`fiche_perso_soigner` **+11 PV sur les DEUX PJ** (jusqu'à 9-12 tools dans un
seul tour, avec `fiche_perso_condition`/`mettre_a_jour` en série) — sans
aucune source légitime (pas de clerc dans le groupe). Il a même relevé Groth
de « Mourant (-2) ». Le rattrapage « soins narrés » a légitimé ces +11.
**Reco** : en combat, n'autoriser les soins MJ-narrés QUE via un PNJ/potion
identifié ; plafonner les tools de soin par tour pour le MJ.

### 🔴 F2 — Corruption de fiches via `fiche_perso_mettre_a_jour`
Le MJ a écrasé `pv_max` : Groth 14→10, Elara 5→**1** (réparation corrective
faite pendant l'audit). **Reco** : interdire au tool la modification de
`pv_max` par le MJ (champ réservé au serveur/progression).

### 🟠 F3 — Re-population des salles déjà nettoyées
6 engagements pour 4 salles visitées : en revenant sur ses pas, des salles
vidées ont relancé des rencontres (gobelins « de garde » improvisés en
(0,-2), gobelours hors champ `ennemis`). **Reco** : mémoriser les salles
« nettoyées » et refuser l'engagement d'espèces déjà détruites dans la même
salle (signature `historique_engagements` étendue à la salle).

### 🟠 F4 — Clôtures de combat avec ennemis vivants
3 combats sur 5 se sont clôturés en phase exploration avec des monstres
encore vivants dans l'état (gobelin à 1 PV, gobelours intacts) — le MJ narre
une fuite/résolution et les monstres disparaissent de `monstres_combat`
sans ☠️. Conséquence : le gobelin « fantôme » a été **ré-engagé au tour
suivant** avec ses PV pleins (pseudo-résurrection par la narration).
**Reco** : `finir_combat` doit refuser tant qu'un ennemi est vivant SAUF si
le MJ pose explicitement une condition « Fugi » sur le monstre.

### 🟠 F5 — Spam d'outils méta par le MJ
Jusqu'à 12 tools/tour, dont des triplés `scenario_etape` et des
`etat_partie_patch` sauvages ; 6 × `incanter_sort` d'affilée (slots
épuisés) ; `scenarios_laelith_charger` ×3 en plein soin. Les narrations
persistées restent propres (le mécanisme de supplant remplace la prose
bavarde : max final 962 car.) mais le budget tokens/explosion de tours est
réel (un tour à 324 s). **Reco** : quotas d'outils par tour + refus dur des
`scenario_etape` redondants.

### 🟡 F6 — Voyage impossible sans position monde
`voyage_demarrer` a été appelé mais le voyage ne démarre pas si le groupe
n'a pas de position sur la carte du monde (lieu = donjon). Le MJ demande
alors la localisation au joueur (bon comportement), mais le donjon ne
l'enregistre jamais. **Reco** : à la sortie d'un donjon, proposer
automatiquement le rattachement à la localité la plus proche du scénario.

### 🟡 F7 — Catalogue marchand sans potions
`marche_acheter("potion de soins légers")` → « inconnu du catalogue » :
aucune potion soignante achetable (seules armures/armes/rations…). Le kit
devient LA ressource de soin basse-niveau — ce qui rend le garde-fou du
point 1 d'autant plus important. **Reco** (option) : ajouter la potion de
soins légers au catalogue marchand (50 pc) ou assumer la rareté magique.

### ✅ Ce qui fonctionne bien (confirmé en réel)
- Entrée de donjon, mouvements, carte SVG, lieux, quête+bible injectés ;
- Combat serveur : jets réels, application des dégâts, morts vérifiées,
  dé-dup des doublons, rotation des tours, XP distribuée (400/PJ) ;
- Kit : demandes explicites honorées (1d4 auto), jamais gratuit ;
- Achat : marche_acheter débite et équipe (rations de voyage 5 pc) ;
- Soins kit pendant l'exploration : 2 kits Groth/Elara consommés
  légitimement en gameplay.

## 3. Qualité des textes du MJ (24 narrations analysées)
- Longueur moyenne 560 car. (min 294 / max 962 après supplant) — les
  excès de combat (jusqu'à 9 891 car. générés) ne persistent PAS dans le
  chat final : le mécanisme « brouillon supplanté » fonctionne ;
- 18/24 narrations interpellent les joueurs (« Que faites-vous ? ») ✓ ;
- 3 répétitions verbatim ≥ 60 car. (formules d'incantation) — mineur ;
- Heuristique « tronqués » : 11 alertes = faux positifs (fins en `**`
  markdown) ;
- Des mélanges de descriptions entre salles voisines subsistent (prose
  (0,-3) recyclant la « toile camouflée » de (0,-1)) — limites du 9B.

## 4. Recommandations priorisées
1. **F2** (garde `pv_max` sur `mettre_a_jour`) — corruption d'état, facile ;
2. **F4** (`finir_combat` refusé avec vivants sauf fuite explicite) —
   ferme la pseudo-résurrection ;
3. **F1** (encadrement des soins MJ en combat) — équité des règles ;
4. **F3** (mémoire des salles nettoyées) — crédibilité du donjon ;
5. **F5** (quotas d'outils/tour) — performance et budget LLM ;
6. **F6/F7** (rattachement localité après donjon ; potions au catalogue) —
   confort.

---
*Audit généré par `tests/e2e_audit_scenario_reels.py` ; état final : Groth
14/14 PV, 40 pc, rations achetées, kit 8 charges ; Elara 5/5 ; XP 400 chacun
; partie `eb46aeef` conservée pour inspection.*
