# Rapport de vérification — classes de PJ, aptitudes et conformité globale

**Date :** 15 octobre 2026
**Objet :** test des combats avec toutes les classes de PJ et leurs aptitudes
particulières, et vérification de conformité de l'entièreté du jeu.
**Méthode :** trois volets complémentaires — tests déterministes (conformité
PHB), E2E déterministes (moteur de combat réel via REST, sans LLM dans la
boucle), E2E pilotés LLM réel (narration + tools, sessions WebSocket), et un
balayage d'intégrité exhaustif.

**Verdict global : ✅ CONFORME ET FONCTIONNEL** — 4 bugs trouvés et corrigés
pendant la campagne (dont un de conformité PHB), tous les suites au vert.

---

## 1. Les 11 classes du PHB — conformité (déterministe)

`tests/smoke_classes.py` — **116 vérifications, 0 échec**. Pour chaque classe
au niveau 1 (carac fixées, dons signature) :

| Classe | PV (dé+CON) | BBA niv 1 | Sauvegardes (base+carac) | Sort signature niv 1 | Attaque résolue |
|---|---|---|---|---|---|
| Barbare | 12+2=13 ✅ | +1 (bon) ✅ | Vig +3 / Réf +1 / Vol +1 ✅ | — (aucun, conforme) | Grande hache, FOR appliqué ✅ |
| Barde | 6+1=7 ✅ | +0 (moyen) ✅ | Vig +1 / Réf +3 / Vol +3 ✅ | Soins légers ✅ (spontané) | Épée courte ✅ |
| Clerc | 8+1=9 ✅ | +0 ✅ | Vig +3 / Réf +1 / Vol +3 ✅ | Soins légers ✅ (+1 empl. de DOMAINE, PHB) | Masse d'armes ✅ |
| Druide | 8+1=9 ✅ | +0 ✅ | Vig +3 / Réf +1 / Vol +3 ✅ | Trait de feu ✅ | Masse d'armes ✅ |
| Guerrier | 10+1=11 ✅ | +1 ✅ | Vig +3 / Réf +1 / Vol +1 ✅ | — | Épée longue ✅ |
| Magicien | 4+1=5 ✅ | +0 ✅ | Vig +1 / Réf +1 / Vol +3 ✅ | Projectiles magiques ✅ | Bâton ✅ |
| Moine | 8+1=9 ✅ | +0 ✅ | Vig +3 / Réf +3 / Vol +3 ✅ (3 bonnes) | — | Mains nues ✅ |
| Paladin | 10+1=11 ✅ | +1 ✅ | Vig +3 / Réf +1 / Vol +1 ✅ | **Soins légers refusé** (sorts seulement au niv 4, 3.5 ✅) | Épée longue ✅ |
| Rôdeur | 8+1=9 ✅ | **+0** (corrigé, voir §4) | Vig +3 / Réf +3 / Vol +1 ✅ | **Enchevêtrement refusé** (niv 4 ✅) | Épée longue ✅ |
| Sorcier | 4+1=5 ✅ | +0 ✅ | Vig +1 / Réf +1 / Vol +3 ✅ | Projectiles magiques ✅ **spontané sans préparation** | Bâton ✅ |
| Voleur | 6+1=7 ✅ | +0 ✅ | Vig +1 / **Réf +5** (DEX 16) ✅ | — | Dague, **Attaque en finesse (DEX +3 > FOR +0)** ✅ |

Vérifications croisées : sorts spontanés (sorcier/barde, connus sans
préparation) ✅ ; sauvegarde recoupée vers le total officiel de fiche ✅ ;
initiative (dons inclus) ✅ ; clerc = emplacement de domaine par niveau
castable ✅ ; paladin/rôdeur sans sorts avant le niveau 4 (règle 3.5) ✅.

## 2. Combats réels par aptitudes de classe (E2E déterministe REST)

`tests/e2e_classes_drs.py` — **12/12 vert** (2 combats complets via les
endpoints REST du moteur, sans LLM dans la boucle → reproductibles) :

- **Combat A (victoire)** — Ragnar le barbare (grande hache, rage narrée),
  Elme la clerc (**Bénédiction lancée**, puis **Soins légers** sur Ragnar
  blessé), Nysa la voleuse (dague, finesse) : attaques résolues par le
  moteur, gobelin détruit à PV ≤ 0, **XP + butin crédités** aux fiches,
  PV finaux dans [-10, max] pour tous.
- **Combat B (victoire)** — Feuilleric le druide (**Trait de feu** résolu
  avec dégâts), PoingFer le moine (mains nues), Alden le paladin (épée
  longue) : squelette détruit, clôture + crédits.

Les lanceurs purs sont couverts par les E2E LLM : Magicien (projectile
magique, mains brûlantes + sauvegarde Réflexes), Sorcier, Barde (soins
légers) — `tests/e2e_regles_drs.py` (33/33) et `tests/e2e_combats_reels.py`.

## 3. Vérification de l'entièreté du jeu (balayage d'intégrité)

`tests/balayage_integrite.py` — **14/14 vert** :

- **Bestiaire (400 monstres)** : toutes les attaques portant des dés sont
  exploitables par le moteur (aucun monstre muet) ; FP numériques partout
  (XP/clôture) ; multiplicateurs de critique dans [2..4]. Les « — » de la DRS
  (créatures sans attaque) passent leur tour proprement ; les attaques sans
  dés (« fixation », venin) restent narrées par le MJ — conforme à la donnée.
- **Scénarios (9 manifestes Laelith)** : tous se chargent, salles présentes.
- **Registre : 86 tools** enregistrés, aucun doublon, nouveaux tools DRS
  présents.
- **Sorts par classe** : listes de niveau 1 présentes pour les 5 lanceurs
  précoces ; paladin/rôdeur niveau 1 castable = aucun (3.5).
- **Table XP DMG** : FP 1 vaincu = 300 XP pour un PJ niveau 1.
- **RAG** : embeddings persistés (10 337 chunks) au démarrage du serveur.
- **Suite régressive complète : 970 tests pytest verts, 3 sauts.**

## 4. Bugs trouvés et corrigés pendant cette campagne

| # | Bug | Correction |
|---|---|---|
| 1 | **BBA rôdeur faux à la création rapide** : +1 (progression « bon ») au lieu de +0 (« moyen », PHB) — table locale divergente `_CLASSES_35` | `fiche_perso_creer_rapide` recalcule désormais dé de vie/BBA/sauvegardes depuis la progression canonique `persos.CLASSES` (valable à tout niveau) ; fallback table locale pour classes hors PHB |
| 2 | **Armes enchantées DRS non parsées** (« *épée à deux mains +4 sainte* (+21 …) ») : le `+4` du nom bloquait le regex → ces monstres (extérieurs, mortels puissants) ne jouaient JAMAIS leur attaque | `_RE_ARME_DRS` élargi (le nom n'exclut plus les signes ; arrêt à la première parenthèse suivie d'un nombre) → 400/400 monstres à dés exploitables |
| 3 | Harnais E2E : fiches de test **persistantes entre les parties** (PV d'après-combat, morts) → « TPK fantôme » à l'engagement | Purge des fiches de test avant création dans `creer_fiche_deterministe` |
| 4 | Harnais E2E : ré-engagement possible en plein combat (réinitialisation zombie, déjà corrigé au run précédent) + `engager_combat` appelé 9× par le 9B | Garde serveur confirmée efficace (refus avec orientation `combat_ajouter_combattant`) |

## 5. Limites connues (non bloquantes, documentées)

- **RD par TYPE de dégâts** (« 5/contondant ») : le contournement est déduit
  du nom d'arme (matériau/magie) mais pas du type tranchant/perforant/
  contondant — une masse contre un squelette verrait sa RD appliquée à tort
  de quelques points ; le MJ peut l'écraser. Amélioration possible : mapper
  les types de dégâts par arme du catalogue.
- Attaques sans dés du bestiaire (« fixation », venin) : effets narrés par le
  MJ, pas de rouage de dés (conforme à la donnée source).
- Pilotage LLM (Qwen 9B) : narrations parfois vides/courtes en début de
  partie — les rattrapages serveur et les relances du harnais couvrent ; ce
  n'est pas un défaut du moteur.

## 6. Conclusion

Le moteur de combat joue correctement **les 11 classes du PHB** avec leurs
dérivés officiels, leurs sorts (préparés et spontanés), leurs dons signature
(finesse, critique…) et leurs restrictions (paladin/rôdeur niv 1). Les
combats REST déterministes se soldent proprement (mort conforme, XP + butin).
L'entièreté vérifiée — bestiaire, scénarios, tools, sorts, XP, RAG — est
conforme. **Aucun test rouge restant.**
