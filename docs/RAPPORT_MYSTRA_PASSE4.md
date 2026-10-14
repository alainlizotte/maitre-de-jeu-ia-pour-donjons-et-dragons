# Rapport passe 4 — « The Crown of Mystra » ch.1 (Druide + Clerc, boutique, auberge, voyage)

> **Contexte** : correctifs R1-R6 + S1/S2 + filet jet-joueur (sauvegardes/
> compétences), garde de cohérence condition/PV, résumé S4 des troncatures.
> Table 4 PJ niveau 1 : **Thorvold** (Humain Guerrier), **Aldemar** (Humain
> Clerc de Pélor), **Eliane** (Humaine **Druide** d'Obad-Haï), **Finnick**
> (Halfelin **Voleur**). Scénario `ro_the_crown_of_mystra_ch01` (niv 1-8,
> 4 chapitres). Suite : **973/973 verte** avant partie.

---

# 1. Chaîne de tests demandée — résultats

| Test | Résultat | Détails |
|---|---|---|
| Ouverture/briefing | ✅ | La Tor d'Équilibre à Silverymoon, la magesteresse Thukmuul Teleshann engage le groupe — prologue joué, pas de téléportation front. |
| **Boutique (achat)** | ✅/🟠 | `marche_stock` + `marche_acheter` ×6. Le stock du bourg (rang 2/6, coefficient ×1.1) **n'avait pas de potions** — refus correct du tool. MAIS le MJ a ensuite **inventé un prix de 550 po** (PHB : 50 po) au lieu de citer le résultat du tool. L'or de Finnick (160 po) n'a pas été débité à tort. |
| **Vente** | ❌ non testée | Aucun butin vendable avant la fin du chapitre ; `marche_vendre` non déclenché. |
| **Auberge (repas + nuit)** | ✅ | `auberge_commander` ×4 (un par PJ), **25 po débités**, condition « Rassasié » posée, nuit narrée — chaîne complète. |
| **Voyage inter-cités** | 🟠 | `voyage_demarrer` ×6 : 1er refus (mode « marche normale » inconnu — allure « marche » attendue), 2e refus par la **garde de trame du scénario** (salle de départ non marquée visitée), débloqué par exploration + `forcer=true`. Jour 1 narré (orage, 24 km), rencontre gobelin officielle, combat gagné (XP 135). MAIS après le combat, le MJ a **re-téléporté le groupe dans la Tour** (`carte_donjon_entrer`) — la position monde a sauté (C2). |
| **Druide — sorts** | ✅ | Liste druidique réelle chargée à la création (13 tours + 21 sorts niv 1). **Trait de feu** lancé en combat ×2 : `lancer_sauvegarde` serveur avec DD 11 explicite, gobelin et garde réussis — refus honnête, état inchangé. |
| **Druide — compagnon animal** | ✅/🟠 | `appeler_familier` a refusé d'abord : l'espèce se choisit **à la création** (champ `familier.espece`). Après pose du champ (Corbeau, type compagnon), l'appel réussit : **corbeau invoqué** (`invoque: true`), utilisé en vol de reconnaissance. L'API GET /api/persos ne renvoie PAS le champ (C8). |
| **Clerc — sorts** | ✅ | Mémorisation Bénédiction + Soins légers ×2 (dict JSON dicté). **Bénédiction lancée** au final (trace tool complète), Soins légers sur les mourants en combat. |
| **Combat final + butin** | ✅ | Garde humain (guerrier 2) + Crawler charognard vaincus, **Couronne de Mystra récupérée** (`inventaire_ramasser`), `retraite_combat` utilisé pour fuir avec la Couronne — puis retour gagnant. |
| **Clôture de chapitre** | ✅/🟠 | `scenario_etape` appelé sur demande, baguette de téléportation remise, récap narré. MAIS `avancement` de mission resté « 0/3 » et conditions résiduelles incohérentes sur les fiches (C5). |

**État final** : XP 1035/perso, Couronne en poche, chapitre 1 clôturé,
phase exploration, 4 PJ vivants.

# 2. Problèmes rencontrés (nouveaux + persistants)

| # | Sévérité | Problème | Correctif proposé |
|---|---|---|---|
| C1 | 🟠 | **Prix inventé par le MJ** : potion 550 po (PHB : 50) malgré le tool appelé — le MJ narra le prix au lieu du résultat. | Consigne prompt : « le prix affiché par le tool fait foi » + rappel du bloc mécanique après le dm. |
| C2 | 🟠 | **Voyage vs donjon** : après un combat de voyage, le MJ re-téléporte le groupe dans le donjon de départ (`carte_donjon_entrer`) — position monde perdue. | Bloquer `carte_donjon_entrer` pendant un voyage actif (`voyage` non-null). |
| C3 | 🟠 | **Garde de trame trop stricte au départ** : l'étape exigeait une salle de départ jamais marquée « visitée » bien que la scène soit jouée → 2 refus de voyage. | À la pose de la quête, marquer la salle de départ « visitée ». |
| C4 | 🟡 | **Compagnon animal inaccessible hors formulaire** : `appeler_familier` exige `familier.espece` posé À LA CRÉATION ; impossible via le MJ en jeu ; l'API GET masque le champ. | Autoriser `fiche_perso_mettre_a_jour("familier.espece")` hors combat + exposer `familier` dans l'API GET. |
| C5 | 🟡 | **Conditions résiduelles incohérentes** : après soins remontant les PV > 0, « Mourant »/« Inconscient » restent posés (Eliane 12 PV + « Mourant » ; Thorvold 2 PV + « Inconscient » + « Rassasié »). | Étendre le garde condition/PV à `fiche_perso_soigner` (PV ≥ 1 → lever Mourant/Inconscient) et purger « Rassasié » au combat. |
| C6 | 🟡 | **Préparation de sorts** : le MJ classe mal les niveaux (Bénédiction classée niv 0) et a besoin du dict JSON dicté. | Injecter la table des emplacements par classe/niveau dans le prompt, ou laisser le tool redistribuer automatiquement niv 0/1. |
| C7 | 🔵 | La bible du scénario contient des étapes-poubelles anglaises (noms de sorts : « Beljuril », « Wall Of Fog »…) — même famille de bruit que passe 3. | Renforcer le filtre anti-bruit (mots-clés de sorts/magie dans les titres). |
| C8 | 🔵 | Deux timeouts serveur (240 s) pendant les combats après plusieurs heures de session (llamacpp instable) — résolu par un restart, état conservé. | Surveiller ; un watchdog llama.cpp serait utile. |

# 3. Constats positifs

- **Le filet jet-joueur (R7) a fonctionné en conditions réelles** : jets de
  Perception auditive résolus avec le bonus fiche officiel (« 1d20=14 + 4 = 18 »).
- **Le filet sorts (R3) a tenu** : Trait de feu → `lancer_sauvegarde` serveur
  (DD 11), gobelin et garde réussis — refus honnête, état inchangé.
- **`retraite_combat`** a clôturé proprement un combat intenable (Crawler 25 PV
  vs groupe agonisant) — la fuite est sortie du cycle victoire/mort.
- **XP officielle en cascade** : 135 (gobelin) puis 435 puis 1035 cumulés —
  table DMG respectée.
- **L'auberge** : repas + nuit + or débité + « Rassasié » — chaîne complète.
- **Le garde de trame du voyage** protège la séquence du scénario — il a fait
  son travail ; c'est la pose de la quête qui doit marquer la salle de départ.

# 4. État final de la table

| PJ | XP | PV | Notes |
|---|---|---|---|
| Thorvold (Guerrier) | 1035 | 2 | conditions résiduelles à purger (C5) |
| Aldemar (Clerc) | 1035 | 17 | préparations fonctionnelles |
| Eliane (Druide) | 1035 | 12 | corbeau compagnon invoqué ; Trait de feu opérationnel |
| Finnick (Voleur) | 1035 | 6 | Couronne de Mystra en sac ; flèches épuisées ×2 |

Objets de quête : Couronne de Mystra ✅, parchemin de la route ✅, fiole de
vérité ✅, baguette de téléportation ✅. Objectifs restants : huit gemmes (2/3)
et restauration (3/3) — chapitres 2 à 4.

---

*Journal : `docs/audit_mystra4pj_journal.jsonl`. Partie : `partie_mystra4pj.json`.*
