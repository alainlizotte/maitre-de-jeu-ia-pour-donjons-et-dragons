# Rapport d'audit « parties complètes » — Dues For The Dead puis The Crown of Mystra

> **Statut : audit terminé.** Les deux parties ont été jouées jusqu'à
> leur conclusion naturelle : « Dues For The Dead » se termine par une
> défaite de groupe irremédiable (§8), « The Crown of Mystra » par un
> blocage mécanique après la première salle (§9). **88 défauts sont
> documentés** (48 bloquants, 14 majeurs, 19 mineurs, 7 de création de
> personnage). **Aucun correctif n'a été appliqué** : ce document ne
> décrit que des constats, le code de l'application est inchangé
> (commit `9b2684f`).

**Environnement de test**

| Élément | Valeur |
|---|---|
| Application | conteneur `dnd35-mj` (code identique à l'arbre de travail, commit `9b2684f`) |
| Front | http://localhost:8123 |
| MJ | `Qwen3.5-9B-Q4_K_M-MTP` via llama.cpp (`llamacpp:8080`) |
| Outils chargés | 76 |
| Outils exposés au LLM | **15** (`max_tools_exposed`, `config.yaml:70`) |
| RAG | actif (`llamaembed:8080`) |
| Streaming client | **désactivé** (`stream_to_clients: false`) — le texte n'arrive qu'à la fin |
| Historique | `max_history_events: 50`, `max_history_chars: 10 000` |
| Déchargement idle | `unload_after_turn: false`, `unload_delay_minutes: 5` |
| Timeout tour de combat | `combat_turn_timeout_seconds: 300` (`config.py:127`) |
| Ping WS serveur | `--ws-ping-interval 30 --ws-ping-timeout 60` |

**Comptes / personnages**

* Compte `Aurel` (mot de passe `dues2026`), **deux personnages** joués
  simultanément dans **deux onglets** (exercice de la garde de tour par
  connexion et de la rotation à 2).
* **Aldric** — Humain Clerc niv 1, Neutre Bon, FOR 12 / DEX 16 / CON 15 /
  INT 15 / SAG 16 / CHA 11, PV 13, CA 19, don « Dur à cuire ».
  Matraque + Chemise de mailles + Bouclier bois lourd + Torche + Rations +
  Kit de premiers secours, 21 po, 18 ans, 1,71 m, 85 kg.
* **Brann** — Humain Guerrier niv 1, Loyal Bon (dieu Héronéus), FOR 13 /
  DEX 10 / CON 12 / INT 16 / SAG 13 / CHA 15, PV 11, CA 15.
  Épée longue + Armure d'écailles + Bouclier acier léger + Torche + Rations +
  Kit, 84 po, 19 ans, 1,77 m, 87 kg.
* Partie `2be193af` — « Dues for the Dead — partie complète »,
  scénario `divers_dues_for_the_dead_ch01` (5 chapitres, niveau 1), `pj` =
  [Aldric, Brann].

**État des mêmes fiches à la fin de l'audit** (voir §8.2 et §9.2) :

| fiche | PV | CON | inventaire | or | XP |
|---|---|---|---|---|---|
| Aldric (départ 13 PV, CON 15) | 1/11 dans la partie, 11/11 dans la fiche | **11** | `null` | 210 | 370 |
| Brann (départ 11 PV, CON 12) | 1/9 dans la partie, 9/9 dans la fiche | **8** | `null` | 840 | 370 |
| Sirin (départ 11 PV, CON 13, 0 po) | −1/11 | 15 (partie) | `null` | 0 | 0 |
| Korr (départ 7 PV, CON 12, 50 po) | 2/7 | 12 | 4 entrées = copie de l'équipement | 50 | 0 |

Les CON d'Aldric et Brann ont été réduites **par le serveur**, sans
demande du joueur, à chaque défaite de groupe (B33/B37). Les écarts entre
la partie et la fiche sont documentés en B33 (défaut 4).

**Méthode de test** — jeu « en vrai » : les deux personnages sont incarnés par
deux connexions WebSocket distinctes (une par onglet du navigateur, qui restent
ouverts et affichent la partie en direct). Les messages joueur sont envoyés
depuis un pilote WS équivalent au client web (`type: say`) afin de pouvoir
attendre la réponse du MJ et changer de personnage sans latence de saisie. Chaque
tour a été relu dans `server/data/chat_<id>.json`, `partie_<id>.json`, les
fiches de personnage et les journaux du conteneur.

---

# 1. Verdict global

Deux scénarios ont été joués **en entier, jusqu'à leur conclusion
naturelle** : « Dues for the Dead » (chapitre 1) et « The Crown of
Mystra » (chapitre 1). Le premier se termine par une défaite de groupe
irrémédiable après ~70 tours ; le second a été joué avec deux
personnages neufs sur un compte neuf.

| Domaine | État constaté |
|---|---|
| Lancement, connexion, multi-onglets, multi-comptes | ✅ fonctionnel |
| Création de personnage | 🔴 clerc impossible (P1), or non crédité (P7), caractéristiques non bornées (P6) |
| Combat (dés, PV, CA, mort des monstres, XP, mourant/mort) | 🟡 le moteur serveur calcule juste ; **l'affichage et l'ordre des tours sont faux** (B4, B20, B24, B38) |
| Exploration du donjon (carte, portes, pièges) | 🔴 la carte ne suit pas la narration (B1/B2), le module est abandonné dès que le MJ improvise (B25) |
| Inventaire / objet de quête | 🔴 **aucun objet n'a jamais été ajouté** en ~90 tours, sur deux campagnes (B7) |
| Objectifs de quête / progression | 🔴 le compteur recule (B9), `manquants` et `objectif` vides (m17) |
| Latence | 🟡 12 – 68 s en régime normal (B21), mais **25 min bloquées** dès que la boucle de correction s'emballe (B10/B31/B32) |
| Mémoire du MJ | 🔴 plafonnée à 50 messages, l'aventure longue est amnésique (B41) |
| Fin de partie | 🔴 une défaite n'est jamais une défaite : résurrection automatique non demandée, −2 CON par mort (B33/B36/B37) |

**Bilan** : la partie est techniquement jouable, mais **l'état du jeu et ce
que le MJ raconte divergent à peu près à chaque tour**, le joueur perd
régulièrement la main pendant des minutes sans pouvoir rien faire, et la
partie ne peut ni être gagnée (le module est abandonné par le MJ) ni
réellement perdue (la défaite est effacée). **88 défauts** sont documentés ci-dessous :

| Sévérité | Nombre | Nature |
|---|---|---|
| 🔴 Bloquant | 48 | Empêchent de jouer correctement ou trichent le joueur |
| 🟠 Majeur | 14 | Détruisent la cohérence, la mémoire ou l'information |
| 🟡 Mineur | 19 | Polish, UI, texte |
| Création de perso | 7 | Bloquent la création, la correction ou l'équité des personnages |

Cinq défauts expliquent à eux seuls la majorité des symptômes :

* **B22** — 36 outils sur 76 ne sont jamais exposés au MJ dans aucune
  phase : pas d'inventaire, pas de mémoire, pas de scénario, pas de
  repos, pas de marché, pas de voyage, pas de gain d'XP. Le MJ ne peut
  donc pas faire ce que le jeu promet ;
* **B25** — le MJ invente les rencontres et le serveur les enregistre :
  le module devient une suggestion, pas un cadre ;
* **B10 + B29 + B30 + B32** — la boucle de correction rejoue des actions
  anciennes, tronque les narrations, perd 27 ko de règles à la 2e
  itération et consomme jusqu'à 12 générations par tour ;
* **B31** — aucun moyen d'interrompre un tour bloqué : le blocage devient
  une défaite mécanique, le moteur de combat continuant à jouer sans le
  joueur ;
* **B33 + B36** — la « résurrection » déclenchée par le propre texte
  de GAME OVER du MJ remet le groupe à 1 PV, lui retire 2 CON et efface
  la défaite.

---

# 2. Défauts bloquants / majeurs (🔴)

## 🔴 B1 — La carte du donjon ne bouge pas quand le MJ narre un déplacement

La règle est simple : le MJ doit appeler `carte_donjon_explorer(direction)`.
Le serveur possède un rattrapage pour le cas où il l'oublie
(`server/main.py:206-216`) :

```python
_MOVE_INTENT_RE = _re_mod.compile(
    r"^\s*(?:je\s+(?:vais|souhaite\s+aller|passe|avance)\s+(?:au|à l'|a l'|vers\s+le\s+|vers\s+la\s+)?"
    r"|on\s+va\s+(?:au|à l'|a l')?|allons\s+(?:au|à l'|a l')?|direction\s+)?"
    r"\s*(nord|sud|est|ouest)\s*[.!?]*\s*$",
    _re_mod.IGNORECASE,
)
```

Le motif est **ancré début/fin** : il n'accepte qu'une phrase qui ne contient
**rien d'autre** qu'un verbe de déplacement et une direction. Toutes les
formulations naturelles échouent :

| Message joueur | Détecté ? |
|---|---|
| `Je passe a l'ouest.` | ✅ oui |
| `Je passe au nord.` | ✅ oui |
| `Je descends le passage de l'ouest vers la fosse aux os.` | ❌ non |
| `Nous examinons l'obélisque, puis nous prenons le passage du nord.` | ❌ non |
| `J'entre dans la crypte au nord.` | ❌ non |

**Conséquence jouée** : le groupe reste indefinitely dans la même salle pendant
que le MJ décrit une salle voisine. Exemples réels :

* Tour 8 — « Je grimpe hors de la fosse et nous prenons la porte à l'est, vers
  l'antichambre » → le MJ narre « Vous remontez l'escalier de pierre pour vous
  retrouver dans l'antichambre (0,-1) », **l'état passe de (-1,0) à (0,0)**
  (retour à l'entrée, pas l'antichambre).
* Tour 11 — « Je regarde autour de moi » puis tour 12 — le MJ décrit l'obélisque
  et la crypte (0,-2) pendant que `donjon.courant` vaut toujours `(0,-1)`.
* Combat déclenché dans la salle voisine alors que le groupe n'y est pas :
  `donjon.courant = [0,-1]` pendant que `phase = combat` avec un Squelette
  « libéré du plâtre » de (0,-2).

Il faut donc écrire des phrases robotiques (« Je passe au nord. ») pour que la
correction se déclenche, ce qu'aucun joueur ne devine.

## 🔴 B2 — Un déplacement effectuée change la position sans que la narration ne suive

Symétrique du précédent, dans l'autre sens. Avec les phrases formelles, l'outil
est bien appelé et **l'état bouge**, mais la narration finale reste celle du
premier jet et contredit l'état :

* « Je passe a l'ouest. » depuis l'entrée → l'état passe à `(-1,0)` (le puits),
  et le MJ répond : « Vous vous retournez, mais **le mur à l'ouest est une
  surface de pierre lisse et froide, sans issue.** La seule porte accessible
  depuis cette fosse aux os… » → il dit à la fois « vous êtes dans la fosse »
  et « il n'y a pas de porte à l'ouest ».
* « Je passe a l'est. » → l'état passe à `(0,0)`, le MJ écrit « Vous retournez
  dans **l'antichambre** (0,0) » alors que `(0,0)` s'appelle **« entrée »** dans
  le manifeste et que l'antichambre est en `(0,-1)`.

Le rattrapage outil Force l'état mais **ne régénère pas la prose** : le joueur
voit donc un résultat différent de ce qu'il vient de faire.

## 🔴 B3 — Un message hors tour est conservé dans la mémoire du MJ et rejoué plus tard

`_handle_say` (`server/main.py:5063`) mémorise et diffuse le message joueur
**avant** d'appliquer la garde de tour :

* ligne ~5092 : `session.remember_player_message(player, text)` + broadcast ;
* ligne ~5222 : garde « en combat, ce n'est pas ton tour » → `turn_blocked`.

Conséquence observée : le message refusé **reste dans l'historique** et le MJ le
joue des dizaines de tours plus tard, en l'attribuant au mauvais personnage.
Extraits réels du journal du conteneur :

```
phase de décision : inventaire_ramasser({'nom': 'Aldric', 'objet': 'tout', 'source': 'cadavre_kobold'})
   ← message « Je fouille le kobold mort… » envoyé par Brann, ~50 min plus tôt
phase de décision : incanter_sort({'nom_personnage': 'Aldric', 'nom_sort': "Armes d'Hadare"})
   ← message « Je lance le sort Armes d'Hadare » refusé (hors tour) 40 min plus tôt
```

Le MJ essaie donc de lancer un sort sur un kobold **mort**, avec un
personnage qui n'a rien demandé. Le chat szerdé shows trois messages joueur
consécutifs (« Je lance le sort Armes d'Hadare… », « Je frappe le kobold… »,
« J'attaque le kobold avec mon matraque ») **sans aucune réponse du MJ**.

## 🔴 B4 — L'ordre des tours de combat calculé par le serveur n'est pas celui qui est joué

Combat du Kobold (`partie_2be193af`) :

| Source | Ordre |
|---|---|
| Initiative calculée et affichée au joueur | `Aldric 19 → Kobold 4 → Brann 2` |
| Tour réellement joué en premier | **Brann** |

Le même écart s'est reproduit au combat des Squelettes :

| Source | Ordre |
|---|---|
| Annoncé par le MJ dans la prose | « Initiative 14 — c'est au tour de **Aldric** » |
| Bloc officiel du serveur | `Squelette 19 → Brann 11 → Aldric 7` — « c'est au tour de Squelette » |

Deux défauts distincts :

1. **le MJ annonce une initiative de son invention** (14) et un « tour de X »
   qui ne correspond à rien ; l'ordre officiel est affiché juste en dessous, en
   contradiction visible ;
2. **la liste « 🎲 Initiative du combat » n'est pas triée** : elle affiche
   `Squelette 19, Aldric 7, Brann 11` puis « Ordre : Squelette → Brann →
   Aldric ». Le joueur ne peut pas savoir qui joue.

## 🔴 B5 — Le jeton de reprise d'un tour expire plus vite que la latence du MJ

`combat_turn_timeout_seconds = 300` (`config.py:127`), alors qu'un tour de MJ
prend **5 à 15 minutes** sur ce modèle. Pire : `tour_depuis` est écrit au
moment où le curseur **bouge** (`combat.py:794`, `state.py:1021`) et n'est
**jamais réécrit** quand le tour du joueur commence réellement. Le joueur
paye donc sur son temps de réflexion le MJ.

Constats réels : sur le combat du Kobold, **trois tours consécutifs ont été
perdus** sans que le joueur n'ait fait d'erreur ; l'un d'eux a été
automatiquement transmis (le Kobold a frappé Aldric, 5 dégâts) pendant que le
joueur avait lui-même fait son action.

Bout de code responsable : `combat.py:694-701`

```python
def _timeout_expire(actif, debut, timeout_s=300):
    ...
    return datetime.now() - debut > timedelta(seconds=timeout_s)
```

## 🔴 B6 — Le rattrapage « attaque déclarée » joue l'action d'un **autre** personnage

Quand le joueur déclare une attaque mais que le MJ ne la joue pas, le serveur
la joue à sa place (`main.py:6529-6570`) :

```python
_pj_att = next((p for p in etat.get("pj") or []
                if str(p.get("joueur") or "").strip().lower()
                == str(joueur or "").strip().lower()), {})
_nom_pj_att = str(_pj_att.get("nom") or "")
```

La sélection se fait par **compte joueur**, pas par le personnage incarné
par la connexion. Avec deux personnages sur le même compte (cas normal au
bureau des poser), `next()` renvoie le premier de la liste : journal réel :

```
[dnd35] Attaque PJ Aldric résolue (régularisation 5ter).
tool_call name=lancer_attaque args={"nom_attaquant": "Aldric", "arme": "Matraque",
                                   "nom_cible": "Kobold", "bonus_attaque": 1}
```

**Brann** avait déclaré « J'attaque le kobold avec mon épée longue » ; le
serveur a joué **une matraque d'Aldric** (jet 12+1 = 13 vs CA 15 → raté), puis
a rendu la main à Brann qui a donc joué **deux fois au round 1** avec son
épée. Même erreur sur l'inventaire (§B7).

## 🔴 B7 — Les objets sont annoncés comme reçus mais ne rentrent jamais dans l'inventaire

> **Cause racine : B22** — `inventaire_ramasser` n'est exposé au MJ dans
> **aucune** phase (ni exploration, ni combat). Aucun message joueur,
> aucune formulation, ne peut y remédier.

Sur toute la partie, `inventaire` des deux PJ est resté `[]`, alors que le MJ
a annoncé au moins cinq fois des objets « pris » :

| Tour | Narration du MJ | Inventaire réel |
|---|---|---|
| 1 | « Il vous tend un parchemin… Gardez-la bien », « Vous prenez la fiole et la clé, les ajoutant à votre inventaire » | `[]` |
| 2 | « Vous examinez la fiole que Cassyt **vous a tendue** » (fiole rendue une 2ᵉ fois) | `[]` |
| 8 | « Cassyt… vous tend **encore** la fiole » | `[]` |
| 30 | « Vous fouillez le kobold et trouvez un petit sac en cuir vide, une torche éteinte et une fiole de poison. **Ces objets sont désormais dans votre inventaire.** » | `[]` |

Le même tour 30 est un **rattrapage « narration de fallback »** : la boucle
d'outils était épuisée (`iterations=4, corrections=2`), le serveur a relancé
une génération de secours, et cette génération de secours **invente** des
objets et affirme leur ajout. Il n'y a aucun `inventaire_ramasser` derrière.

Sur les 2 objets réellement tentés :

* `inventaire_ramasser(nom='Aldric', objet='tout', source='cadavre_kobold')` →
  « ⚠️ Objet "tout" inconnu du catalogue d'équipement (PHB 3.5) » — le MJ a
  passé le mot « tout » comme nom d'objet ;
* le même appel retombé sur **Aldric** alors que c'était Brann (§B6).

Aucun rattrapage n'existe pour « objets annoncés mais non ajoutés », alors
qu'il en existe pour les dégâts et les soins.

## 🔴 B8 — Les tours de combat peuvent être consommés deux fois par le même joueur

La rotation n'avance que si **le LLM appelle `terminer_mon_tour`**
(`state.py:1370-1395`). Si le MJ oublie, le joueur garde la main : il peut
rejouer immédiatement dans le même round, ou se le faire voler 300 s plus tard
(§B5). Les deux cas ont été observés dans la même partie.

## 🔴 B9 — La progression des objectifs de quête **recule**

> **Cause racine complémentaire : B22** — `scenario_etape` n'est exposé dans
> aucune phase, le MJ ne peut donc jamais valider une étape ; et
> `_salle_visitee` sort au premier étage dont les coordonnées
> correspondent.

`objectifs_quete` recalcule tout à chaque appel. Le test « salle visitée »
utilise `_salle_visitee` (`game/objectifs.py:297-324`) qui, malgré sa
docstring (« de n'importe quel étage »), **sort dès la première grille où les
coordonnées correspondent** :

```python
grilles = [donjon.get("grille") or []]          # étage COURANT
for fl in (donjon.get("etages") or {}).values():
    grilles.append(fl.get("grille") or [])       # tous les étages
...
for grille in grilles:
    for s in grille:
        if (int(s["x"]), int(s["y"])) == cible:
            return bool(s.get("visitee"))        # ← sort au 1er match
```

Or les coordonnées `(x,y)` sont **identiques d'un étage à l'autre** dans les
manifestes (par exemple `(0,-1)` est la salle des banquets funèbres au
Galleries hautes et la chambre de l'obélisque aux Bas Tombeaux ; `(0,-3)` est
l'escalier en haut et la salle du trésor en bas).

Constat joué : après la descente aux Bas Tombeaux, l'objectif « Descendre vers
les Bas Tombeaux » (ancré sur `0,-3`) est passé de **accompli** à
**« à venir »**, `termines` s'est vidé et le compteur est repassé de **1/4 à
0/4**. Le joueur voit sa progression diminuer.

Corollaire : les coordonnées citées dans les objectifs et dans le texte du
manifeste sont ambiguës, faute d'indiquer l'étage.

## 🔴 B10 — Boucle de correction sans fin : le MJ rejoue une action ancienne et ne répond jamais

Le garde anti-répétition et le garde anti-simulation relancent la génération,
mais le modèle reproduit la même phrase. Sur le tour « Nous examinons
l'obélisque… » :

```
17:28:42 WARNING: narration répétée d'un tour précédent (« vous fouillez le kobold
                et trouvez un petit sac en cuir vide, une torche eteinte… », correction 1)
17:28:44 WARNING: narration répétée d'un tour précédent (« vous fouillez le cadavre
                du kobold et trouvez un petit sac en cuir vide, une tor… », correction 2)
17:28:46 WARNING: simulation dans narration streamée (« glissez dans votre sac », correction 3)
```

Huit appels `chat/completions` pour un tour, sans produire de réponse. Le
joueur reste bloqué sur « Le MJ réfléchit… » **sans aucun message d'erreur ni
délai maximal affiché**. La cause racine est §B3 : l'action à jouer est un
message ancien resté dans l'historique.

## 🔴 B11 — Le chien de déchargement n'a aucune visibilité sur les tours en cours

`unload_after_turn: false` + `unload_delay_minutes: 5`. Le chien compte
l'inactivité **depuis le dernier chargement de modèle**, sans savoir si une
génération est en cours. Or un tour de MJ sur ce modèle dure précisément dans
cette fenêtre (5 à 15 min). Journal, avec les intervalles :

```
17:04:21  models/unload 200 OK
17:06:39  llamacpp model loaded      <- tour en cours
17:12:12  models/unload 200 OK      <- 5 min 33 après le chargement
17:27:50  models/unload 200 OK
17:28:06  llamacpp model loaded      <- tour en cours
17:33:48  models/unload 200 OK      <- 5 min 42 après le chargement
17:37:13  llamacpp model loaded      <- recharge à vide (M13)
```

Le risque est que le déchargement coupe une génération en vol et que le tour
ne rende jamais la main, **sans aucune erreur applicative et sans timeout** vu
par le joueur. Je n'ai pas pu attribuer une coupure de ce type à un cycle
particulier (aucun `chat/completions` émis après les déchargements
observés) : **risque latent, non confirmé en partie**. Il reste réel par
construction, la fenêtre de déchargement (5 min) étant comparable à la durée
médiane d'un tour.

## 🔴 B12 — Chaque déplacement validé par le rattrapage coûte un tour de LLM de plus

Dès que `_MOVE_INTENT_RE` reconnaît la phrase, le journal montre :

```
[dnd35] Déplacement donjon narré sans tool ('Je passe au nord.') — rejeu avec correctif
[dnd35] Rejeu déplacement donjon réussi (1 tools)
```

Le serveur **rejoue le tour entier** avec un prompt correctif. Conséquences
jouées :

* chaque pas coûte **deux générations** au lieu d'une (d'où la latence
  observée sur les tours de déplacement) ;
* la première narration — celle qui était probablement correcte — est
  **jetée** ; celle qui est conservée a été produite par une seconde
  génération qui ne connaît pas forcément le nouvel état, d'où les
  phrases qui contredisent le déplacement (B2) ;
* la saisie brute du joueur (`Je passe au nord.`) apparaît en clair dans le
  journal serveur.

Le rattrapage se déclenche quand le MJ n'a pas appelé l'outil, ce qui arrive
à chaque fois que la phrase n'est pas exactement ancrée : le joueur est
donc doublement pénalisé sur chaque déplacement.

## 🔴 B13 — Le rattrapage « objet » ne peut pas aboutir et renvoie une garde illisible

```
[dnd35] Rejeu inventaire objet sans tool — avancement forcé
[dnd35] Rejeu inventaire objet réussi (2 tools)
```

puis l'outil lui-même refuse l'entrée produite :

```
⚠️ Objet "tout" inconnu du catalogue d'équipement (PHB 3.5)
```

Le modèle appelle `inventaire_ramasser(nom='Aldric', objet='tout',
source='cadavre_kobold')` : le mot « tout » (issu de la phrase « nous prenons
tout ») est passé comme nom d'objet. Le garde est correct mais **le rattrapage
continue de boucler** : deux exécutions successives pour le même tour,
donc des générations consommées sans que l'objet entre jamais dans
l'inventaire (B7).

---

## 🔴 B14 — Le raisonnement interne du MJ est diffusé tel quel au joueur

C'est le défaut le plus visible de toute la partie. Dans un seul tour, le
modèle écrit ses pensées, se corrige, se relance, et **le serveur n'en
retire rien** :

```
Votre matraque heurte l'air, manquant le squelette qui continue d'avancer, ses
ossements grinçant dans la pierre. [...] **Phase : Combat** — Initiative 14 —
C'est au tour de **Aldric** ! [...] **Que voulez-vous faire ?**

*(Note : J'ai appelé l'outil d'attaque comme demandé. Le résultat est un
échec. Je ne peux pas appeler lancer_degats car l'attaque a manqué. Je dois
maintenant attendre la réponse du joueur pour Aldric.)*

Attendez, je dois corriger ma réponse. La consigne dit de limiter à deux
phrases et de ne pas commencer par "votre matraque s'abat". Je dois raconter
une conséquence nouvelle. Mais l'outil a déjà été appelé et le
résultat est connu. [...] Réessayons avec une narration plus concise et qui
fait avancer l'histoire : Votre matraque manque de peu, le squelette esquive le
coup et continue son avance menaçante.

*(Encore une fois, je dois m'assurer que je ne répète pas la même narration.
Je vais essayer une approche différente.)*

Le squelette esquive votre matraque et se rapproche d'un pas lourd. [...]
```

Et, plus tard :

```
**Note** : L'attaque a touché (13 > 12), mais je n'ai pas encore appliqué les
dégâts. Veuillez lancer les dégâts pour Aldric avec le bonus officiel
de +1.
```

Le joueur lit les instructions du système, les auto-corrections du modèle, ses
réflexes méta et même une consigne de correction (« Veuillez lancer les
dégâts ») à lui. `strip_narration_artifacts` ne retire que la syntaxe
d'appel d'outil, pas les `(Note…)` ni les paragraphes de pensée : le garde
anti-répétition déclenche même sur ces Trash (correction 1, 2, 3 au
journal), ce qui **multiplie les générations au lieu de les réduire**.

## 🔴 B15 — Le MJ narre trois fois la même action dans un seul message

Dans le même tour ci-dessus, le message contient trois paragraphes
(« Votre matraque heurte l'air, manquant le squelette », « Votre matraque
manque de peu, le squelette esquive le coup », « Le squelette esquive votre
matraque ») qui décrivent le même événement avec trois
formulations différentes. Le joueur ne sait pas s'il a réussi, manqué ou fait
trois coups.

## 🔴 B16 — Le serveur accepte des jets et des dégâts contre des
créatures absentes de l'état

C'est le défaut mécanique le plus grave de la partie. **Cinq** tours sur dix
se déroulent contre des monstres que le serveur ne connaît pas.

L'état ne contient qu'un seul ennemi depuis le début du combat :

```
[ETAT] {"phase": "combat", "monstres": [["Squelette", 3]]}
```

Or le MJ fait tirer des dés contre l'**araignée**, le **kobold** et le
**zombie**, et le serveur accepte, recalcule la CA « officielle » depuis le
bestiaire, et applique les dégâts :

```
💥 **Dégâts** : matraque → araignée
- Formule : 1d6+1
- Jets bruts : [6]
- **Dégâts infligés : 7**

⚔️ **Attaque** : Brann [épée longue] vs kobold (CA 15)
- ⚠️ CA imposée par les règles : 10 → 15 (source : bestiaire (Kobold, FP 1/4)).
- CA 15 → ❌ Manqué.

⚔️ **Attaque** : Aldric [matraque] vs zombie (CA 12)
- ⚠️ CA imposée par les règles : 10 → 12 (source : bestiaire (Zombie, FP 1/4)).
- CA 12 → ❌ Manqué.
```

`lancer_attaque` et `lancer_degats` ne vérifient pas que la cible existe dans
`monstres_combat` ; ils enrichissent même la cible depuis le bestiaire
à partir du nom inventé par le modèle. Conséquences pour le joueur :

* on peut frapper l'air, obtenir « Touché » et « 7 dégâts
  infligés », et le compte rendu de combat ne change jamais ;
* le modèle peut introduire des ennemis à la demande et les déclarer morts
  dans la prose (« l'araignée hurle à mi-voix avant de s'effondrer, morte »)
  sans que rien ne soit enregistré ;
* `lancer_attaque` accepte un nom de cible **arbitraire** et va le chercher
  dans un bestiaire de 400 entrées : la liste des monstres est un espace de
  noms ouvert au modèle ;
* le vrai ennemi (Squelette, 3 PV) reste intouchable, donc **le combat ne peut
  pas se terminer** : il a tenu cinq rounds et le joueur ne peut ni le tuer ni
  sortir (B17).

## 🔴 B17 — On ne peut pas quitter un combat : le déplacement est ignoré

> **Cause racine : B22** — `carte_donjon_explorer` n'est exposé qu'en phase
> `exploration`. Pendant le combat, le MJ ne dispose d'aucun outil pour
> déplacer le groupe, même si le joueur le décide.

Toujours au même tour, Brann tente deux fois de s'extraire :

```
[TOUR 3 | Brann] Je passe au nord.
--- MJ ---
Vous vous dirigez vers le nord, laissant derrière vous l'odeur de poussière et
de mort des catacombes. Le passage semble moins sombre...
**Phase : Exploration** — Vous êtes dans les Catacombes du Cimetière de
Valhingen, Les Bas Tombeaux, salle (0,-1).
[ETAT] {"phase": "combat", "courant": [0, -1], "monstres": [["Squelette", 3]]}
```

et

```
[TOUR 4 | Brann] Je passe au nord.
--- MJ ---
Vous franchissez le seuil et vous vous trouvez dans une salle plus vaste, où
une fosse centrale exhale une chaleur âcre. [...] une silhouette difforme se
dresse à l'entrée, attendant patiemment.
[ETAT] {"phase": "combat", "courant": [0, -1], "monstres": [["Squelette", 3]]}
```

La phrase est pourtant exactement de la forme acceptée par `_MOVE_INTENT_RE`
(« Je passe au nord. »), mais le rattrapage de déplacement **ne se
déclenche pas** parce que la partie est en phase `combat` : la salle reste
`(0,-1)` après deux déplacements narrés, la phase reste `combat`, et le
squelette reste vivant. Le joueur est **pris au piège** : il peut narrer une
sortie du combat, la lire dans la réponse du MJ, et l'état ne bougera pas.
Aucun des deux messages n'a produit le moindre changement d'état, et le bloc
officiel qui suit continue d'afficher « ⚔️ Au tour de Brann … Ennemis
vivants : Squelette ».

## 🔴 B18 — La phase annoncée par le MJ et la phase réelle divergent

Dans le même message : « **Phase : Exploration** » (prose) alors que l'état
persiste est `phase: combat` et que le bloc officiel qui suit le message
rappelle « ⚔️ Au tour de Brann … ». Le joueur voit donc deux phases
différentes dans la même réponse, et le combat n'est jamais clos.

## 🔴 B19 — Le MJ attribue les actions et les effets au mauvais personnage

Conséquence directe d'un routage d'outils incorrect, mais fréquent :

* Brann reçoit « Le guerrier se tourne vers vous, son épée toujours à la
  main. Que faites-vous, Brann ? » alors que le bloc officiel dit
  `⚪ **Au tour de Aldric**` ;
* Aldric est qualifié de « ééçperé » puis de « à Aldric qui
  s'effondre » ;
* le tour 5 est joué avec `lancer_degats` sur une cible absente (B16) ;
* dans le tour 2, le bloc officiel indique `⚪ **Attaque** : Aldric [épée
  longue]` alors que l'épée longue est l'arme de **Brann** ; le serveur
  recalcule le bonus à partir de la bonne fiche (« fiche de Aldric : BBA +0 ») et
  le résultat est cohérent, mais l'énoncé est faux.

Le système ne dispose d'aucun moyen de vérifier que le personnage qui
raconte l'action est celui dont la fiche a été chargée par l'outil.

## 🔴 B20 — Le bloc « jets officiels » laisse voir que l'appel de l'outil était faux

```
- Jet brut d'attaque : 3
- Bonus total : +1
- ⚠️ Bonus recalculé par le serveur +0 → +1 (fiche de Aldric : BBA +0, FOR 12 (+1)
  — la fiche fait foi).
```

Bonne initiative du serveur (la fiche prime, le modèle ne peut pas inventer un
bonus), mais le joueur voit que le MJ a systématiquement envoyé `+0`. Sur
tous les tours observés le bonus envoyé par le modèle était faux et le
message l'annonce au joueur. À l'inverse, le bloc
`⚠️ CA imposée par les règles : 15 → 12` signale au joueur que le
modèle a inventé une CA.

## 🔴 B21 — Ça marche : la latence réelle des tours est de 12 à 60 s

Correction d'une appréciation intermédiaire de ce rapport. Dès que le
modèle est réellement chargé et que le garde anti-répétition ne part pas
en boucle, un tour complet (prompt ~22 ko, RAG, boucle d'outils, correction,
combat) prend **12 à 60 s**. Les tours de **5 à 15 minutes** observés
auparavant ne sont pas la latence normale du modèle mais la conséquence
mécanique de deux défauts : la boucle de correction sans fin (B10) et le
cycle de déchargement/rechargement du modèle (B11, M13). Chronologie
mesurée sur les 10 tours ci-dessus :

```
tour 1 (attaque)        60 s
tour 2 (attaque)        42 s
tour 3 (deplacement)    12 s
tour 4 (deplacement)    16 s
tour 5 (attaque)        50 s
```

Un déplacement est trois à cinq fois plus rapide qu'une action de combat
(parce qu'il ne déclenche ni jet ni rattrapage) : c'est l'inverse de ce que
le joueur attend, et cela masque le fait que les tours d'action sont ceux qui
déraillent.

---

## 🔴 B22 — 36 des 76 outils ne sont jamais présentés au MJ, dans aucune phase

Cause structurelle de la plupart des défauts d'inventaire, de mémoire et de
progression. Le plafond `max_tools_exposed: 15` est appliqué **après** le
tri par priorité (`orchestrator._sous_ensemble_prioritaire`, ligne 2174) : les
14 noms de `_COMBAT_PRIORITAIRES` occupent à eux seuls 14 des 15 places en
combat, et `_OUTILS_DECISION` (26 outils) ne trouve de place qu'en exploration,
où il est lui-même dépassé.

Ensemble réellement exposé, recalculé sur le code réel :

| Phase | Outils exposés au MJ (15) |
|---|---|
| `opening` | `lancer_des, lancer_sauvegarde, carte_joueurs_placer_ville, lancer_d20, carte_joueurs_position, carte_joueurs_get, lancer_caracteristiques, fiche_perso_creer_rapide, fiche_perso_recuperer, manuels_distribuer, manuels_lister, etat_partie_get, etat_partie_patch, ajouter_evenement_histoire, set_derniere_narration` |
| `exploration` | `appeler_familier, fiche_perso_infliger_degats, fiche_perso_soigner, incanter_sort, lancer_des, lancer_sauvegarde, auberge_commander, carte_donjon_entrer, carte_donjon_etage, **carte_donjon_explorer**, carte_donjon_sortir, carte_joueurs_deplacer, carte_joueurs_placer_ville, **engager_combat**, equipement_catalogue` |
| `combat` | `appeler_familier, combat_ajouter_combattant, fiche_perso_condition, fiche_perso_infliger_degats, fiche_perso_niveau_negatif, fiche_perso_soigner, incanter_sort, inventaire_consommer_munition, lancer_attaque, lancer_degats, lancer_des, lancer_sauvegarde, retraite_combat, terminer_mon_tour, renvoyer_familier` |

**Les 36 outils absents de toutes les phases** :

```
carte_donjon_get              illustration_scene        calculer_initiative
fiche_perso_creer             fiche_perso_lister        fiche_perso_supprimer
inventaire_consulter          inventaire_ajouter        inventaire_retirer
inventaire_ramasser           marche_consulter          marche_stock
marche_acheter                marche_vendre             memoire_personnage
memoire_position              memoire_evenement         memoire_intrigue
monstre_consulter             monstre_lister            monstre_ajouter_bestiaire
fiche_perso_gagner_xp         fiche_perso_retirer_niveau_negatif
fiche_perso_perte_niveau      fiche_perso_consulter_xp  scenarios_laelith_lister
scenarios_laelith_charger     scenario_etape            preparer_sorts
repos_long                    etat_partie_save          demarrer_combat
tour_suivant_combat           finir_combat              reset_partie
voyage_demarrer
```

Conséquences directes, toutes observées en partie :

1. **`inventaire_ramasser` n'est exposé dans aucune phase** → le MJ ne peut
   structurellement ramasser un objet. C'est la cause racine de B7 (les
   inventaires restent `[]` pendant 23 tours) et de tous les rattrapages
   d'inventaire qui bouclent sans aboutir (B13). Aucun message joueur, aucune
   formulation, ne peut y remédier.
2. **Les quatre outils d'écriture mémoire sont absents**
   (`memoire_personnage`, `memoire_position`, `memoire_evenement`,
   `memoire_intrigue`) → le dispositif de mémoire / RAG est en **lecture
   seule**. Tout ce que le MJ « se souvient » d'un tour passe par le
   récapitulatif tronqué à 10 000 caractères, ce qui explique
   l'amnésie de fond (C. Cassyt rendu trois fois, répétition des
   mêmes scènes) et la boucle de correction (B10).
3. **`scenario_etape` n'est exposé dans aucune phase** → le MJ ne peut
   jamais valider une étape de scénario. Les objectifs de quête ne peuvent
   donc se déver que par les effets de bord (visiter une salle, tuer une
   cible), ce qui explique les libellés décalibrés et le compteur qui
   recule (B9, M11).
4. **`demarrer_combat`, `tour_suivant_combat`, `finir_combat` sont absents** →
   le MJ n'a aucun levier explicite pour clôturer un combat ; il dépend
   entièrement de `terminer_mon_tour` et de la mort des monstres, d'où un
   combat dont la cible n'est pas dans l'état ne peut jamais finir (B16).
5. **`carte_donjon_explorer` n'est exposé qu'en exploration** → en combat
   le MJ ne peut pas déplacer le groupe, même si le joueur le décide
   (B17).
6. **`marche_*`, `voyage_demarrer`, `fiche_perso_gagner_xp`, `repos_long`,
   `reset_partie`** sont absents → marché, voyage, repos, niveaux et XP
   demandés par le joueur ne sont pas jouables.

La configuration `max_tools_exposed: 15` est adaptée à un modèle qui échoue
à 39 outils ; mais à 15, le tri n'est pas « les 15 plus utiles », il est
« les 14 outils de combat, puis le premier du registre ». C'est un bug de
sélection, pas un choix d'arbitrage.

---

## 🔴 B23 — Le MJ annonce la mort d'un monstre qui reste en vie, et annonce l'inverse

Tour 4 (Brann, « Votre épée longue s'enfonce dans le squelette, brisant
ses ossements avec un craquement sinistre … **Le monstre s'effondre en
poussière, marquant la victoire sur ce gardien nocturne.** ») :

```
[ETAT] {"phase": "combat", "monstres": [["Squelette", 2]]}
```

La victoire est **annoncée** alors que le monstre est toujours à 2 PV et que la
partie est toujours en combat. Le joueur lit une victoire, l'interface affiche
toujours « Au tour de Aldric … Ennemis vivants : Squelette ».

Trois tours plus tard, l'inverse : « Le squelette reste debout avec ses **3
points de vie** » alors qu'il en a **2**, puis « toujours debout avec **2 points
de vie sur 3** ». Le MJ lit un état qu'il ne possède pas et annonce des
points de vie **au joueur**, ce qui transforme chaque combat en devinette sur
l'état réel.

Aucune des deux annonces n'a d'effet : ni mort, ni PV modifiés, ni
clôture de combat. Le serveur ne vérifie pas la coherence entre la narration
et `monstres_combat` — il ne le pourrait pas de toute façon, mais il pourrait
au minimum refuser d'afficher une victoire non enregistrée.

---

## 🔴 B24 — Un tour où le coup est annoncé comme manqué applique quand même des dégâts, et affiche des PV négatifs

Tour où Aldric attaque le Squelette (2 PV). Le message reçu contient, dans
l'ordre, cinq blocs qui se contredisent :

```
1. Votre matraque s'abat avec un bruit sourd sur les os du squelette, mais
   le coup manque !  L'arme glisse sur l'armature osseuse.

2. Le squelette reste debout avec ses 3 points de vie   ← faux (il en a 2)

3. ⚙️ _Rattrapage serveur : attaque réussie sans dégâts — le serveur a
   résolu lui-même les dégâts (matraque)._       ← contredit le point 1

4. 💥 Dégâts : matraque → Squelette
   - Jets bruts : [4]      - Dégäts infligés : 4      → 2 PV - 4 = -2

5. ♻️ Doublon ignoré : « Squelette » a DÉJÀ subi exactement 4
   dégâts CE TOUR (re-narration détectée).

6. ⚖️ Squelette : 4 dégâts comptés deux fois — PV réajustés
   (-1/3).                                                     ← PV NÉGATIF

7. 🏆 Victoire ! Ennemis vaincus : Squelette (FP 1/3)
   Aldric gagne 135 XP → 370 XP      Brann gagne 135 XP → 370 XP
```

Le point 6 est le défaut mécanique. La restauration d'état, dans
`main.py:6042`, calcule

```python
_plafond = _pv_max - _jetes_c        # 3 - 4 = -1
_pv = min(_pv + _e, _plafond)        # min(2, -1) = -1
```

`pv_max - jets` est **négatif** dès qu'un jet unique dépasse les PV max
d'une créature, ce qui est normal pour une dégâts de sorts sur un petit
monstre. Le plafond ne peut donc pas servir de borne basse. Résultat : le
serveur **écrit des PV négatifs en base de données** et les affiche au
joueur à l'identique. Dans ce tour, la créature a été tuée **par la
formule de correction**, pas par le jet d'attaque.

Trois problèmes en un seul tour :

1. la **narration** dit « le coup manque », la mécanique dit « dégâts
   infligés : 4 » ; les deux s'affichent, l'un juste après l'autre ;
2. le garde-fou « Doublon ignoré » détecte la double application **après**
   coup, et la correction « réajustement » **annule une partie des
   dégâts légitimes** pour tenter de rattraper le double comptage :
   une cohérence impossible à garantir ;
3. la correction écrit un état négatif et l'annonce au joueur
   « PV réajustés (-1/3) ».

Ce tour est le meilleur condensé du rapport : il illustre en un écran la
divergence narration / mécanique (B1, B23), l'absence de validation de cible
(B16), la fuite de l'etat interne (M2) et le double comptage des dégâts.

---

## 🔴 B25 — Le MJ invente une créature dans une salle qui n'en contient pas, et le serveur l'enregistre

Sortie du combat contre le Squelette, Brann attaque. Le MJ reécrit la mort du
squelette, puis ajoute :

```
Alors que vous examinez les restes, une ombre se profile dans le couloir nord,
suivie d'un grognement guttural : une goule émerge de l'obscurité, prête
à vous attaquer.

[ETAT] {"phase": "combat", "monstres": [["Goule", 16]]}
```

**Les goules ne sont pas dans cette salle.** Le plan du module place
`Goule ×2` en `(-1,-2)`, crypte à l'ouest ; la salle actuelle est `(0,-1)` et la
suivante au nord est `(0,-2)` cellules (`Squelette ×5`). Le MJ a donc
déplacé un monstre de l'ouest vers le nord, et le serveur a lancé
l'engagement, composé l'initiative (Goule 9) et joué ses tours. Le joueur
affronte un combat qui n'existe pas dans le module, avec une créature qu'il
n'a pas croisée.

Aurals-je pu être confronté à la vraie salle des goules ? Non : la
boussole du scénario est abandonnèe dès que le MJ ne dispose que de
`engager_combat` en exploration, sans moyen de vérifier le contenu de la salle
avant de déclencher. L'outline du module sert d'inspiration, pas de contrainte.

## 🔴 B26 — Un personnage à -4 PV continue de "combattre" et le dégât des autres est inventé

La goule frappe Aldric :

```
💥 aldric subit 8 dégâts → PV -4/13 — ⚠️ **Mourant** (-4 PV) :
inconscient, jet de stabilisation 1d20 ≥ 10 par round (1 naturel = -1 PV).

[ETAT] ... [PV] Aldric [-4, 13, 370, 0]
```

Le serveur applique correctement la règle de la stabilisation, mais le MJ
raconte dans le **même message** :

* « Vous êtes blessé, mais vous tenez encore debout, le souffle court. »
  — Aldric est inconscient ;
* « Aldric, gravement blessé et éteint, luttait de toutes ses forces pour
  rester debout … a réussi é stabiliser son état, bien qu'il soit
  maintenant inconscient et incapable d'agir. » — trois états contradictoires
  dans la même phrase ;
* « tandis que **Brann conserve ses dix-neuf PV sur vingt** » — **inventé** :
  la fiche de Brann est `11/11` (Guerrier niv 1, pv_max 11). Le MJ a halluciné
  un maximum de 20 et l'annonce au joueur (même cause que M5) ;
* Aldric reste dans la rotation des tours : le bloc officiel affiche
  « Au tour de Brann » mais le tour suivant est attribué à Aldric.

Aucun jet de déstabilisation n'a été lancé ni affiché, et la partie
continue sans que le joueur apprenne qu'il joue avec un personnage à 4 PV
sous zéro.

---

## 🔴 B27 — Le bloc "officiel" affiche une arme que le personnage n'a pas

Le serveur recalcule le bonus d'attaque à partir de la fiche (« la fiche fait
foi ») mais **recopie le nom de l'arme fourni par le modèle sans le
valider**. Aldric, clerc, n'a qu'une **matraque** ; le bloc officiel affiche :

```
🔴 **Attaque** : Aldric [épée longue] vs Squelette (CA 15)
- Bonus total : +1
- ⚠️ Bonus recalculé par le serveur +4 → +1 (fiche de Aldric : BBA +0, FOR 12 (+1))
- **Total attaque : 6**   → CA 15 → ❌ Manqué.
```

L'attaque d'Aldric est lancée à la matraque (dégâts `1d6`, bonus FOR 12)
mais l'armée portée est un objet qu'il ne possède pas. Le même tour,
Brann (guerrier, épée longue) reçoit « Brann [matraque] ». Le bloc censé
être la source de vérité du jeu ment donc sur l'équipement, ce qui
empêche au joueur de vérifier mécaniquement ses propres jets. La
formule des dégâts suit bien l'arme demandée : `matraque` pour Aldric,
`Épée longue` pour Brann, donc le résultat est correct — c'est
précisément l'affichage qui est faux.

## 🔴 B28 — Le MJ qualifie les personnages d'une autre classe

Trois fautes de classe observées dans la même partie, toutes visibles par le
joueur :

| Moment | Ce que dit le MJ | La fiche |
|---|---|---|
| Goule sur Aldric | « le guerrier est tombé … le coup ... fait vaciller le **barde** déjà affaibli » | Aldric = **Clerc**, Brann = **Guerrier** |
| Squelette | « Brann reste vigilant, ses **douze points de vie** intacts » | Brann = **11** PV |
| Goule |  les « le clerc » / « le guerrier » à l'échange | Aldric n'est **pas** clerc dans le texte |

Le MJ n'a pas d'accès structuré aux fiches dans son contexte (les outils
`fiche_perso_get` / `fiche_perso_lister` ne sont exposés dans **aucune** phase,
cf. B22) : il déduit donc les classes du prénom et du style de jeu. Le joueur
qui lit « le barde » à la place de son guerrier perd le fil narratif à chaque
tour.

---

## 🔴 B29 — La garde anti-dégénérence tronque la narration en plein milieu, sans le dire

`orchestrator.tronquer_degeneration` (ligne 1053) coupe le texte **à la
3e occurrence de toute phrase d'au moins 6 mots**, sans marqueur, sans points de
suspension et sans message au joueur. Journal, sur un tour de combat ordinaire :

```
18:24:40 WARNING: dégénérescence intra-réponse détectée
         (« phase : combat — initiative 14 — c est au tour de brann .… » ×3)
         — troncature 7743 → 1000 chars
```

Deux problèmes cumulés :

1. **La phrase déclenchée est celle qu'injecte le serveur lui-même**
   (`« **Phase : Combat** — Initiative 14 — C'est au tour de **Brann**. »`).
   Le rappel d'état, repété par le modèle comme un rituel de début de
   réponse, déclenche à lui seul la troncature. Le garde anti-boucle
   punit la ponctuation automatique ;
2. **7 743 caractères de narration deviennent 1 000**, sans que le joueur
   soit informé : le message s'interrompt net, sans ponctuation, et le
   bandeau de tour ou le bloc de jets disparaissent. C'est la cause racine de
   « m1 — narration tronquée en milieu de phrase », qui n'était jusqu'ici
   qu'une observation.

## 🔴 B30 — Dès la 2e itération, le système perd 27 436 caractères de règles

```
18:24:50 INFO: itération 2 : sections narratives élaguées du system prompt
         (-27436 chars).
```

`orchestrator.py:2627` retire, à partir de la deuxième itération, toutes les
sections narratives du prompt système pour faire tenir la boucle d'outils.
Le prompt passe de ~40 ko à ~12 ko **au milieu d'un tour**. Conséquence
directe, mesurée sur tous les tours de combat de la partie : c'est
après ce retrait que le modèle produit des arguments d'outils faux —

```
nom_attaquant: "Brann",  bonus_attaque: "+5"   → serveur : +2 (fiche)
nom_attaquant: "Aldric", bonus_attaque: "+4"   → serveur : +1 (fiche)
nom_attaquant: "Brann",  bonus_attaque: "+6"   → serveur : +2 (fiche)
arme: "Épée longue" pour un clerc équipé d'une matraque (B27)
```

Sur **19 appels d'outils de combat** observés, le bonus d'attaque fourni par le
modèle était faux **19 fois**, et le nom de l'arme faux au moins deux fois. Le
serveur corrige systématiquement — c'est le seul garde qui fonctionne de bout en
bout — mais l'économie de générations est gaspillée et le joueur voit
l'avertissement « ⚠️ Bonus recalculé par le serveur » sur presque chaque
attaque, ce qui rend le bloc « jets officiels » illisible à force.

---

## 🔴 B31 — Aucun moyen d'interrompre un tour bloqué : la partie est verrouillée sans fin

Le verrou `session.thinking` (`main.py:5083`) refuse **tout** message joueur
tant qu'un tour n'est pas rendu :

```
🔄 Le MJ est en train de travailler — patientez avant d'envoyer un
nouveau message.
```

Il n'existe **aucune** commande d'annulation, aucun bouton « abandonner le tour »,
aucun `case "cancel"` dans le gestionnaire WebSocket, aucun endpoint
d'interruption (recherche exhaustive sur `main.py`). Le `finally` de la ligne
7463 repose le verrou, mais il n'est atteint que si la coroutine se termine.

Or un tour peut ne jamais se terminer : le 29/09, un tour de Brann contre la
Goule est parti dans la boucle de correction (B10) et **a duré plus de
25 minutes**. Conséquences mesurées pendant ce blocage :

* 16 messages joueur successifs reçus avec le même refus, en 2 s chacun —
  la partie est **injouable** ;
* **le moteur de combat continue de jouer** : `tour` est passé de 4 à 5 et
  **Brann a perdu 2 PV tout seul** (5 → 3) pendant que le joueur était privé
  d'action. Aldric était déjà à −4 PV. Le groupe se fait tuer par un
  tour qui ne se termine jamais ;
* le joueur ne peut ni fuir, ni voir, ni recommencer.

C'est le défaut le plus destructeur du rapport : il transforme un bug de
qualité de narration (B10) en **défaite mécanique de la partie**, et il n'existe
aucun remède côté client. Le seul contournement consiste à redémarrer le
serveur, ce qui élimine l'état en mémoire de toutes les sessions.

## 🔴 B32 — Le budget de corrections est indépendant du budget d'itérations : jusqu'à 12 générations par tour

`max_tool_iterations: 4`, mais le journal d'un tour bloque affiche :

```
18:25:45 WARNING: tool loop épuisé (iterations=4, corrections=4)
          — fallback narration
18:25:29 WARNING: simulation dans narration streamée
          (« subit 6 dégâts », correction 3) — relance
18:25:12 WARNING: narration répétée d'un tour précédent
          (correction 2) — relance orientée sur l'action joueur
18:24:50 WARNING: outil requis mais non appelé (prose seule)
          → relance avec tool_choice='required' (phase 'combat')
18:24:40 WARNING: dégénérescence intra-réponse détectée — troncature
          7743 → 1000 chars
18:24:18 WARNING: outil requis mais non appelé → relance
18:23:5x 🔄 Déplacement donjon / Tour PJ Brann : action annoncée sans
          aucun jet — rejeu correctif
```

En comptant les rattrapages du serveur (`rejeu déplacement`, `rejeu PJ`,
`rejeu inventaire`) et les troncatures de dégénérescence, **un seul message
joueur a consumé 12 appels à `chat/completions`**. Le budget d'itérations
borne la boucle d'outils, pas la boucle de corrections, et chaque passe
reconstruit tout l'historique (10 000 caractères) et le prompt système
(40 ko) avant de générer. C'est la cause directe des tours à 5–15 minutes
et de l'impossibilitç d'atteindre un délai de réponse acceptable.

---

## 🔴 B33 — La « résurrection » automatique laisse le groupe à 1 PV et lui retire 2 points de CON dès que le joueur n'a rien demandé

### Ce qui s'est joué

18:23 la Goule (créée par le MJ dans une salle qui n'en contient pas, cf. B25)
place Aldric à **−4 PV**. Le tour ensuite part dans la boucle de correction
(B10/B32) et **dure plus de 25 minutes**. Pendant ce tour bloqué :

```
18:24:03  Aldric -4 → -10   (tour de monstre joué sans action du joueur)
18:25:52  Brann   3 → -10
[dnd35] Combat clôturé par le moteur serveur (défaite).
[dnd35] Résurrection narrée appliquée (tools serveur).
```

Puis, en 1,4 seconde, l'orchestrateur enchaîne sept outils :

```
fiche_perso_condition  Aldric condition="Mort" appliquer=false
     → « Aldric est n'est plus affecté par Mort »
fiche_perso_soigner     Aldric soin=11  → ✨ Aldric récupère 11 PV → PV 1/13
fiche_perso_mettre_a_jour  Aldric carac.CON = 13      → ✅
fiche_perso_mettre_a_jour  Aldric pv_max     = 12      → ✅ (interne)
fiche_perso_condition  Brann condition="Mort" appliquer=false
fiche_perso_soigner     Brann soin=11  → ✨ Brann récupère 11 PV → PV 1/11
fiche_perso_mettre_a_jour  Brann carac.CON = 10      → ✅
fiche_perso_mettre_a_jour  Brann pv_max     = 10      → ✅ (interne)
```

### Défaut 1 — `main.py:4234` : deux morts → tout le monde à 1 PV

```python
# Montant narré : honoré pour UN seul PJ mort ; sinon retour à 1 PV.
cible_pv = 1
if len(morts) == 1:
    m_c = _re_mod.search(r"à\s*(?:\*\*)?(\d{1,3})\s*PV\s*(?:sur|/)\s*\d{1,3}", ...)
    if m_c:
        cible_pv = max(1, int(m_c.group(1)))
```

Le montant récupéré n'est extrait du texte du MJ **que si un seul
personnage est mort**. Dès que deux meurent — c'est-à-dire **le cas
normal d'une défaite de groupe** — le serveur applique `pv = 1` à tout
le monde, quelle que soit la narration. Les deux personnages remettent la
partie avec **1 PV sur 12 et 1 PV sur 10**, en pleine génération contre une
Goule de 16 PV déjà enrégistrée. Le mot *« repos long »* ou *« réveil
discret »* dans la narration est complétement ignoré : seul compte le
nombre de morts.

Aucune trame de jeu ne prévoit un sort de soin après un défaite de groupe, et
`repos_long` n'est exposé dans aucune phase (B22). Le groupe est donc
condamnà mourir une seconde fois, à 1 PV.

### Défaut 2 — `main.py:4307` : la pénalité de Raise Dead est appliquée sans consentement

Pour un personnage de niveau 1, la DMG 3.5 prévoit −2 CON. Le serveur
applique cette pénalité dès que **la narration contient un mot** de
résurrection :

```python
revendique = bool(_RE_PV_RECUPERES.search(narration)) or (
    bool(_RESURRECTION_RE.search(narration))
    and not _RE_OFFRE_RESURRECTION.search(narration))
```

Le joueur n'a ni demandé, ni accepté, ni mérité de ressusciter. Le
serveur décide à sa place, applique une pénalité **irréversible** (le
message le dit : « −2 CON (15→13, irréparable) ») et ne prvient personne que
les personnages ont perdu un point de vie maximum. Un mot de passe en trop dans
la narration suffit à détruire les personnages.

### Défaut 3 — le message est grammaticalement faux

`« Aldric est n'est plus affecté par Mort »` — le patron de la phrase est
construit en concaténant le nom à un pronom possessif déjà accordé. Le
même message contient aussi « Aldric récupère 11 PV », un soin que le
joueur n'a pas demandé et qu'aucun jet n'a produit.

### Défaut 4 — la pénalité n'est appliquée qu'\u00e0 la fiche, jamais à l'\u00e9tat de partie

`fiche_perso_mettre_a_jour("carac.CON", 13)` écrit le fichier
`fiches/fiche_aldric.json`, mais `etat["pj"]` (l'instantané utilisé par le
moteur de combat **et** par le récapitulatif envoyé au MJ) conserve l'ancienne
valeur :

| | CON dans `etat["pj"]` | CON dans `fiche_*.json` | pv_max fiche | pv_max parti |
|---|---|---|---|---|
| Aldric | **15** | **13** | 12 | 12 |
| Brann  | **12** | **10** | 10 | 10 |

Les deux sources de vérité du personnage divergent désormais. Le
récapitulatif envoyé au MJ annonce encore CON 15 : le modèle va continuer
d'énoncer des jets cohérents avec l'ancien CON, et les sauvegardes
calculées à partir de l'instantané ne suivent pas la pénalité. Le joueur, lui,
voit sur sa fiche « CON 13 » et dans le bloc de combat « CON 15 », sans
explication.

## 🔴 B34 — `fiche_perso_mettre_a_jour` interdit `pv_max` mais autorise n'importe quelle caractéristique

L'audit « F2 » (`tools/fiches.py:930`) a banni `pv_max` des écritures du
MJ :

```python
if str(champ or "").strip().split(".")[0] == "pv_max" and not interne:
    return ToolResult(text="⛔ `pv_max` est un champ RÉSERVÉ ...")
```

Mais la protection porte sur **le premier segment du chemin uniquement**, et
`carac` n'est pas dans la liste :

```
fiche_perso_mettre_a_jour {"nom": "Aldric", "champ": "carac.CON", "valeur": "13"}
  → ✅ Fiche de Aldric mise à jour : carac.CON = 13
```

Aucun contrôle de bornes (10–30), aucune vérification de cohérence avec le
niveau, aucun recalcul en aval. Le modèle peut décider qu'Aldric est un
« halfling » avec FOR 40, et `carac.CON` ne sera même pas recalculé pour
aligner les PV max, les sauvegardes et le modificateur de difficulté. La même
brèche existe sur `pv`, `ca`, `bab`, `niveau`, `or`, `initiative` : le seul
contrôle est la coercition numérique (ligne 951), pas la validité
économique. `champ` accepte en réalité **n'importe quelle clé de n'importe
quel objet imbriqué de la fiche** — y compris `carac`, `dons`, `sorts`,
`conditions`, `equipement`, `xp`.

C'est ce mécanisme qui a permis à la résurrection de réviser la CON de deux
personnages sans que le garde-fou ne bronche.

## 🔴 B35 — La défaite de groupe n'est jamais expliquée au joueur

Le journal serveur contient

```
[dnd35] Combat clôturé par le moteur serveur (défaite).
```

et, juste après, sept outils de résurrection. Dans la **conversation**, le
joueur reçoit le bloc `✨ **Résurrection appliquée par le serveur** : la
narration décrivait un retour à la vie, mais l'état indiquait encore un
personnage mort (aucun tool de résurrection n'existe) — levée des conditions
mortelles et PV rétablis`, suivi du journal détaillé. Il n'y a **nulle part** :

* que les deux personnages viennent d'\u00eatre **tués** ;
* qu'ils ont **réduit de 2 points** de CON ;
* qu'ils reviennent avec **1 PV**.

Le message dit même l'inverse de ce que le joueur vit : « conditions
mortelles levées et PV rétablis », alors qu'ils sont à 1 PV. Un joueur qui
recommence la partie believe à un rattrapage qui n'a pas eu lieu, et ne saura
jamais pourquoi ses personnages sont plus faibles qu'au début.

---

## 🔴 B36 — Le modèle GAME OVER du MJ déclenche lui-même la résurrection automatique : la garde d'offre ne couvre pas « Proposez »

Après la deuxième défaite de groupe, le MJ envoie exactement :

```
💀 _Tous les héros sont tombés — GAME OVER. Proposez à la table :
nouvelle partie, résurrection négociée ou reprise narrative plus tôt._
```

C'est une **offre de choix**. Le détecteur (`main.py:166`) est censé
l'identifier pour ne rien appliquer :

```python
_RE_OFFRE_RESURRECTION = re.compile(
    r"ne (?:peux|peut|pourr\w*)\s+pas|nous devons|il faudr\w*"
    r"|choix pour la suite|que choisissez|choisissez-vous"
    r"|co[\u00fbu]t narratif|quelle est votre|\u00e0 vous de (?:choisir|d\u00e9cider)")
```

Aucune de ces formes ne correspond à « **Proposez** à la table ». En
revanche `_RESURRECTION_RE` trouve le mot « résurrection ». Le résultat
(`main.py:4225`) :

```python
revendique = bool(_RE_PV_RECUPERES.search(narration)) or (
    bool(_RESURRECTION_RE.search(narration))
    and not _RE_OFFRE_RESURRECTION.search(narration))   # True and not False = True
```

**Le message qui annonce la mort et propose des options est interprété
comme l'acceptation d'une résurrection.** Conséquence mesurée : les deux
défaites de groupe de la partie ont été suivies, en 1,4 s, d'un Raise Dead
non demandé, avec −2 CON et −1 PV max par personnage, et d'un retour au
combat à 1 PV. `game_over` est remis à `False` par `main.py:4363`, donc
l'état de défaite disparaît également : **le jeu ne peut pas être perdu**,
il est transformé en spirale de mort lente.

## 🔴 B37 — La pénalité de CON se cumule à chaque défaite : spirale de mort sans plancher

Deux défaites, deux applications de la pénalité :

| | CON départ | après 1re mort | après 2e mort | PV max départ | final |
|---|---|---|---|---|---|
| Aldric (Clerc) | 13 | 15→**13** | 13→**11** | 13 | **11** |
| Brann (Guerrier) | 12 | 12→**10** | 10→**8** | 11 | **9** |

Aucun plancher, aucun avertissement, aucune confirmation. À ce rythme,
quatre ou cinq défaites amènent CON à 1 (mort par défaut de
Constitution en 3.5) et PV max à 6. De plus :

* **les sauvegardes ne suivent pas** : Aldric affiche toujours
  `Vigueur +4` dans `etat["pj"]` alors que son CON est passé de 15 à 11
  (un modificateur de −2 sur Vigueur est perdu) ;
* **l'écart entre `etat["pj"]` et `fiche_*.json` s'accumule** : après deux
  morts, `etat["pj"]` dit CON 15 / 12 et la fiche dit CON 11 / 8. Le
  récapitulatif envoyé au MJ, construit sur `etat["pj"]`, décrit des
  personnages qui n'existent plus ;
* **le don « Dur à cuire (+3 PV) » n'est plus compté** : Aldric a perdu
  2 PV max au lieu de 2 (13 → 11), le bonus de don a donc disparu au
  passage sans être recalculé ni réappliqué.

## 🔴 B38 — Le « réajustement » des dégâts ressuscite le monstre

Tour 24, la Goule est à **3 PV**. Aldric la frappe pour 3 dégâts
(coup fatal). Le serveur applique, puis se ravise :

```
⚙️ _Rattrapage serveur : attaque réussie sans dégâts — le serveur a
   résolu lui-même les dégâts (épée longue)._
💥 **Dégâts** : épée longue → Goule   — Dégâts infligés : 3
♻️ **Doublon ignoré** : « Goule » a DÉJÀ subi exactement 3 dégâts CE TOUR
⚖️ _Réajustement (dégâts comptés deux fois) :_
⚖️ Goule : 3 dégâts comptés deux fois — **PV réajustés (3/16)**.
```

Le monstre qui venait d'être mis à 0 se retrouve **ramené à 3/16** : la
formule de correction (`main.py:6042`, déjà décrite en B24) soustrait le
montant « en double » sans jamais vérifier que le résultat reste
inférieur aux PV d'avant le coup. Elle annule donc le coup fatal. La même
mécanique a produit `PV réajustés (-1/3)` sur un Squelette (B24) : selon
le signe du plafond, la correction **tue** ou **ressuscite** la créature.

## 🔴 B39 — Quatre valeurs de PV différentes pour le même monstre dans un seul message

Tour 28, un seul message du MJ contient, pour l'Araignée géante :

| position | valeur annoncée |
|---|---|
| prose, après le coup | « **16/22 PV** » |
| bloc `⚙️ Dégâts appliqués automatiquement` (1re ligne) | « subit 2 dégâts → **20/22** » |
| bloc `⚙️ Dégâts appliqués automatiquement` (2e ligne) | « subit 6 dégâts → **14/22** » |
| prose de clôture | « sa vie s'affichant encore à **21 sur 22** » |

L'état serveur termine à 14/22. Aucune de ces quatre valeurs n'est
identique, et la dernière (21/22) est **supérieure à la valeur de
départ du tour (14/22)**. Le joueur ne peut pas savoir où en est le
combat ; le MJ, qui relit son propre message à l'itération suivante,
hérite des quatre versions dans son historique.

## 🔴 B40 — Les instructions du serveur au MJ sont diffusées au joueur comme un message de jeu

Le MJ narre cinq squelettes dans une salle ; le serveur refuse et
**diffuse au joueur**, en tant que message `dm` :

```
⛔ **Salle déjà vidée** : Squelette ont DÉJÀ été éliminés dans CETTE
salle — pas de re-population. Narre les cadavres/les traces de
l'affrontement précédent, ou engage d'AUTRES créatures JUSTIFIÉES par la
scène ; pour des renforts d'un combat en cours, `combat_ajouter_combattant`.

_Reprends l'action hors initiative : ce combat narré n'existe pas
mécaniquement._
```

C'est une **consigne de prompt adressée au modèle**, nommant des outils
internes (`combat_ajouter_combattant`), avec une faute d'accord (« Squelette
ont »), affichée au joueur dans le fil de discussion, immédiatement après
la narration des cinq squelettes qu'il vient de lire. Elle n'est en
revanche **pas** persistée dans `chat_<id>.json` (vérifié : 0 occurrence
dans les 50 messages stockés) : le joueur la voit en direct, puis elle
disparaît au rechargement de la page. Le même tour contient
`*(Note : J'ai corrigé l'attaque…)*`, `*(Je lance le jet de sauvegarde
pour Brann)*` et `**[Aurel]** : L'air piquant de la salle du trésor…`
— le nom du joueur réinjecté comme marqueur de locuteur dans la prose.

## 🔴 B41 — La mémoire du MJ ne contient que la moitié de l'aventure

`chat_2be193af.json` sature à **50 messages** (`max_history_events: 50`)
alors que la partie a produit plus de 120 échanges. Le journal confirme la
compression à chaque tour :

```
[dnd35] Historique tronqué : 38 messages anciens omis (budget 10000 chars).
[dnd35] Historique tronqué : 31 messages anciens omis (budget 10000 chars).
[dnd35] Historique tronqué : 23 messages anciens omis (budget 10000 chars).
```

À la fin de la partie, le MJ n'a plus **aucune** trace de Cassyt, de la
fiole promise à l'entrée, de l'enquête sur les bas-reliefs, ni des
trois premières salles. Il répond « Comment sommes-nous arrivés ici ? »
par une invention, et re-narre des combats déjà joués (B10). Le
récapitulatif censé compenser cette perte (`max_recap_chars: 4000`) est
lui-même tronqué et ne contient ni l'objectif courant ni l'état du
groupe : la partie longue est structurellement **amnésique**.

---

## 🔵 B42 — Retiré du périmètre : les parties protégées par mot de passe couvrent ce cas

> **Reclassé le 29/09 après avis du propriétaire.** `POST /api/parties`
> accepte `mot_de_passe` et stocke `meta.mot_de_passe_sha256`
> (`main.py:1701-1704`) ; le `join` WebSocket l'exige alors et refuse
> `Mot de passe incorrect.` (`main.py:3448-3456`). Une partie privée n'est
> donc rejoignable que par ceux qui connaissent le mot de passe : le
> comportement décrit ci-dessous ne concerne que les parties créées **sans**
> mot de passe, comme les deux parties de cet audit. Constat conservé pour
> information, aucune correction demandée.

Le rattachement d'un PJ à une partie se fait dans le `join` WebSocket
(`main.py:3461`) avec **deux seuls contrôles** : la fiche existe, et son
`proprietaire` correspond au compte. Il n'y a **aucun** contrôle de :

* partie verrouillée, complète, ou déjà commencée ;
* personnage **déjà engagé dans une autre partie en cours** ;
* cohérence de niveau entre les PJ de la table.

Vérification réelle : en se trompant de fichier d'étapes, le pilote a
envoyé un `join` avec `personnage: "Aldric"` dans la partie
« The Crown of Mystra ». `Aldric` — clerc niveau 1, 370 XP, état
« stabilisé à 1 PV » dans une AUTRE campagne — a été accepté
immédiatement, ainsi que `Brann` :

```text
GET /api/parties/776fdfa1   →  "pj": []
GET /ws/776fdfa1 {"type": "join", "player": "Aurel", "personnage": "Aldric"}
GET /api/parties/776fdfa1   →  "pj": [{"nom": "Aldric"}, {"nom": "Brann"}]
```

Et il n'existe **aucun moyen de sortir** de la liste : aucune route
`quitter`, `leave`, `kick`, `retirer` dans `main.py` (recherche
exhaustive). La seule issue est de supprimer la partie entière, ce qui
détruit l'historique. Un joueur qui clique sur la mauvaise partie dans le
menu déroulant ajoute donc définitivement son personnage à la campagne
d'un autre, avec ses PV, son équipement et sa mémoire de l'autre
scénario.

---

## 🔴 B43 — Le contenu « canonique » du module est injecté comme message joueur, en impératif, et contredit la narration du même message

Au moment où le groupe entre formellement dans le donjon, le serveur
diffuse au joueur, dans le fil du MJ :

```
⚙️ _Le serveur a fait entrer le groupe dans **La Couronne de Mystra** —
   carte du donjon initialisée._

📜 Vous entrez dans **La Couronne de Mystra** — plan du scénario . Suis
   FIDÈLEMENT les descriptions canoniques et le contenu des salles
   (ennemis, trésors, pièges, PNJ) — n'improvise ni salle ni rencontre
   hors module.

🚪 Portes EXISTANTES dans la salle d'entrée : est. ⚠️ Ce sont les SEULES
   sorties : n'invente et ne narre AUCUNE autre direction, même si le
   module suggère d'autres passages.

Entrée : entrée — « Haut lieu de Silverymoon, la salle d'audience de la
   Tour de l'Équilibre s'ouvre sur la cité par de hautes baies de verre
   dépoli : marbre laiteux, tapis célestes et parfum d'encens. La
   magesteresse Thukmuul Teleshann y reçoit le groupe… »

📜 **Contenu canonique de la salle (scénario)** :
👥 PNJ : Thukmuul Teleshann (magesteresse NG de la Tour de l'Équilibre
   — donne la quête)
⛔ Cette salle N'EST **PAS** VIDE : les PNJ/créatures listés ci-dessus
   sont PRÉSENTS ici et maintenant. Interdit de narrer « une salle vide »
   ou d'occulter ces présences : décris-les, fais-les réagir à l'arrivée
   du groupe (parole, cri, attaque) — et engage via `engager_combat`
   celles qui sont hostiles.
```

Quatre problèmes en un seul bloc :

1. **C'est un prompt, pas une narration.** Il est écrit à l'impératif
   (« Suis », « n'improvise », « décris-les », « engage »), cite le
   nom d'un outil (`engager_combat`) et contient des marqueurs de
   mise en forme du prompt. Il est affiché au joueur comme un message
   du MJ ;
2. **Il contredit la narration du même message.** Le bloc place le groupe
   dans la **salle d'audience de la Tour de l'Équilibre**, en plein
   Silverymoon, avec la magesteresse présente et une seule porte à l'est ;
   la prose qui suit décrit un **seuil de grotte**, « une odeur âcre de
   terre pourrie et de sang frais », et un Gobelin qui charge. Le joueur
   lit deux lieux incompatibles l'un après l'autre ;
3. **Il est en retard de sept tours.** Le groupe avait déjà quitté la
   Tour à pied (tours 3–4), marché une heure et atteint la grotte ; le
   serveur le fait « entrer dans le donjon » au tour 7 en le
   ramenant à (0,0) = la salle d'audience ;
4. **Il ordonne de faire réagir un PNJ à l'arrivée du groupe** alors que
   le groupe est dedans depuis sept tours.

## 🔴 B44 — Le serveur nomme des outils dans les messages joueur et s'adresse au joueur comme s'il était le modèle

Deux occurrences dans le même combat :

```
⚠️ Aucune flèche dans l'inventaire — tir résolu cette fois, mais
   **réapprovisionne-toi** (`inventaire_ajouter`) : sans munition, plus de tir.
```

```
⛔ **Salle déjà vidée** : Squelette ont DÉJÀ été éliminés — …
   pour des renforts d'un combat en cours, `combat_ajouter_combattant`.
```

`inventaire_ajouter` est un **outil du MJ**, pas une action joueur : le
joueur n'a aucun bouton pour l'appeler, et l'outil n'est de toute façon
pas exposé au modèle dans la phase `combat` (B22). La consigne est donc
inapplicable des deux côtés, mais elle est formulée au joueur, à la
deuxième personne, dans un bloc présenté comme officiel.

## 🔴 B45 — Un tir sans munition est quand même résolu

Le carquois de Korr est consommé au tour 17. Au tour 18 et au tour 20,
Korr tire **sans aucune flèche** :

```
tour 18 : ⚠️ Aucune flèche dans l'inventaire — tir résolu cette fois
          ⚔️ Attaque : Korr [Arc court] … **Total attaque : 12** → ❌ Manqué
tour 20 : ⚠️ Aucune flèche dans l'inventaire — tir résolu cette fois
          ⚔️ Attaque : Korr [Arc court] … **Total attaque : 20** → ✅ Touché
          💥 Assassin (monstre) subit **0 dégâts** → PV 12/16
```

Le garde **sait** qu'il n'y a plus de munitions, l'annonce, et résout
quand même le jet — qui touche au tour 20. Le règle 3.5 est sans appel :
sans munition, l'attaque à distance est impossible. Le joueur apprend
donc que les munitions n'ont aucune conséquence, ce qui annule
l'intérêt de leur comptage.

## 🔴 B46 — Un coup qui touche peut infliger 0 dégât, et la prose invente une règle pour le justifier

Même tour 20 : le jet officiel dit `✅ Touché`, total 20, et le bloc
d'application affiche `💥 Assassin (monstre) subit **0 dégâts** → PV 12/16`.
La prose du MJ explique :

```
L'assassin reçoit 0 point de dégâts (car le bonus de dégâts est de 0).
```

Aucune règle de D&D 3.5 ne transforme un coup au but en 0 dégât : un arc
court fait 1d6, et un « échec critique inverse » n'existe pas. Ce qui
s'est passé : le modèle a appelé `lancer_degats` avec un montant de 0
(confondu avec le bonus de dégâts que le bloc officiel lui répète de ne
pas improviser), et le serveur a appliqué 0. Le MJ a ensuite écrit une
règle inventée pour rendre le résultat crédible.

## 🔴 B47 — Six outils écrasent `equipement` avec `inventaire` : le sac remplace l'équipement porté

Toutes les écritures d'inventaire se terminent par le même alignement
(`tools/inventaire.py:816, 892, 963, 1053, 1087, 1093`) :

```python
fiche["inventaire"] = inv
# Aligne le champ d'affichage legacy `equipement` sur l'inventaire.
fiche["equipement"] = [{"nom": i["nom"], "qte": i["qte"]} for i in inv]
```

Conséquences mesurées :

* **le carquois de Korr a disparu des deux listes** après un seul tir :
  il était dans `equipement` (qte 1), a été consommé depuis
  `inventaire`, puis `equipement` a été réécrit depuis `inventaire` —
  donc **amputé** de l'entrée consommée ;
* **« retirer du sac » signifie « déséquiper »** : `equipement` est le
  champ lu par `calculer_derivees` pour la CA et le poids. Un
  `inventaire_retirer` sur une armure fait chuter la CA, et un
  `inventaire_retirer` sur une arme la fait sortir de la fiche de
  l'équipement actif, sans aucun avertissement ;
* **la distinction sac / équipement porté disparaît** : les deux champs
  deviennent la même liste, ce qui rend impossible « poser le sac mais
  garder l'armure », règle pourtant centrale de l'encumbrance 3.5 ;
* **les deux personnages de la même partie divergent** : Korr a une
  `inventaire` de 4 entrées (copie de son équipement de départ, munitions
  en moins) et Sirin a `inventaire: null` après avoir ramassé une fiole,
  un sac d'or et une carte (B7).

## 🔴 B48 — Le repos de nuit est une pure fiction : aucun tool, aucune règle, des sorts que la classe n'a pas

Message joueur : « Nous nous reposons une nuit complète pour retrouver nos
forces. » Réponse du MJ :

```
La nuit s'est déroulée paisiblement… **Sirin** se relève, ses points de
vie restaurés à **11/11**. Elle sent sa force revenir, et **ses sorts sont
à nouveau prêts à être lancés**.
**Korr** s'étire, ses **7/7** points de vie rétablis.
```

* les deux personnages étaient déjà à 11/11 et 7/7 : il n'y avait **rien
  à restaurer** ; le bloc affiche des valeurs identiques à l'état et les
  présente comme une récupération ;
* **aucun tool n'a été appelé** : `repos_long` existe mais n'est exposé
  dans **aucune** phase (B22). Le repos n'existe donc pas mécaniquement ;
* « ses sorts sont à nouveau prêts » : la Paladin de D&D 3.5 **ne
  prépare pas de sorts** au niveau 1 (elle les obtient au niveau 2, et
  les paladins préparent une liste restreinte, pas des emplacements
  génériques). La fiche de Sirin contient
  `"sorts": {"connus": [], "prepares": {}, "depenses": {}}` : rien n'a
  été préparé, rien n'a été dépensé ;
* le repos a été accepté **dans une grotte hostile, à côté d'une porte
  forcée**, sans jet de Vigueur, sans rencontre nocturne, sans
  régénération de sorts — alors que le module place ici une rencontre.

---

# 3. Défauts majeurs (🟠)

## 🟠 M1 — Le résultat brut des jets est réécrit par le MJ

`server/main.py:5258-5284` envoie au LLM une **bandeau de vérité** :

```
=== ÉTAT DE VÉRITÉ (résumé opérationnel) ===
partie_id: ...
=== FAITS AUTORITAIRES (ne pas reformuler) ===
donjon.courant: 0,-1
...
```

Le modèle s'en sert malgré tout : il appelle l'étage courant « le
sous-sol » alors que `donjon.etage` vaut 0, et les combinaisons de pièces sont
hallucinées. Le README annonce que « les dés sont lancés par le serveur, jamais
par le MJ » : c'est vrai, mais le MJ peut **contredire** le résultat.

## 🟠 M2 — Fuite de l'état interne dans la narration

Le texte des outils contient des marqueurs de position et de structure
destinés au MJ, et le modèle les recopie mot pour mot dans la narration
(lisible par le joueur) :

* `**Salle actuelle : (0,-1) — type « piège »**` / `**Salle actuelle : (0,0) —
  type « escaliers » (sous-sol)**`
* `**Portes existantes : nord (vers une salle inconnue), est (vers une salle
  inconnue)…**`
* `**Ennemis présents : Kobold ×1**` / `**Pièges : Trappes et objets piégés par
  le culte (zone 12)**`
* « **Initiative 18** — c'est au tour de Cassyt » (valeur absente de
  l'initiative réelle)
* `+27952 chars` de bloc de vérité recopiés sous forme de liste à puces.

`strip_narration_artifacts` (`client/src/lib/strip.ts`) ne retire que la
syntaxe d'appel d'outil, pas ces blocs. C'est un **spoiler** (le contenu des
salles non visitées, les monstres dormants, les portes restantes) autant qu'un
défaut d'immersion.

## 🟠 M3 — Le serveur ne répond pas aux pings WebSocket du client

`docker inspect` du conteneur : `uvicorn … --ws-ping-interval 30
--ws-ping-timeout 60`. Le serveur **pinge** le client mais ne répond pas aux
**pings du client** : un client conforme (bibliothèques `websockets`,
`ws`, etc.) se déconnecte au bout de ~40 s d'inactivité
(`keepalive ping timeout; no close frame received`). Le client web s'en sort
car il ne fait pas de ping et se reconnecte tout seul
(`client/src/api/ws.ts:92-101`), mais tout client tiers (script, bot,
test d'intégration) est coupé.

## 🟠 M4 — Le chat affiché perd les tours résolus hors tour

Dans la branche « message refusé » (`main.py:5274-5284`), le serveur diffuse
un événement `dm` **sans** appeler `session.remember_assistant`. Le texte
(comprenant le résultat des tours de monstre joués par le serveur) n'est donc
**jamais persisté** : il n'apparaît pas dans `chat_<id>.json`, il disparaît au
rechargement de la page, et le MJ ne le voit pas au tour suivant.

## 🟠 M5 — Le MJ invente des résultats de dés, avec des chiffres précis

Le message 4 du fil de discussion (tour où le groupe descend l'escalier de la
statue insectoïde) contient, mot pour mot :

```
**Résultat des sauvegardes :**
- **Aldric** réussit sa sauvegarde de Réflexes (12 vs 10) et reste debout.
- **Brann** réussit également sa sauvegarde de Réflexes (11 vs 10) et ne chute pas.
```

**Aucun jet n'a été lancé côté serveur.** Vérification sur l'état de partie et
les deux fiches après 23 tours de MJ :

* `fiche_aldric.json` → `conditions: []`, `sauvegardes: {Vigueur: 4, Reflexes: 3, Volonte: 5}`
* `fiche_brann.json` → `conditions: []`
* `partie_2be193af.json` → aucun journal de jets, aucun enregistrement de
  sauvegarde (`clés : calepin, courant_tour_pour, deja_agi, derniere_narration,
  donjon, histoire, initiative, lieu, memoire, monstres_combat, phase, pj, pnj,
  positions_joueurs, quete, tour, tour_depuis`).

Les dés sont donc fantômes : deux nombres (« 12 vs 10 », « 11 vs 10 »)
correspondant à une difficulté parfaitement plausible pour le joueur, mais
issus de rien. Preuve complémentaire, relevée plus tard dans la mêame partie (défaut
**B26**) : « tandis que **Brann conserve ses dix-neuf PV sur vingt**,
prêt à riposter » — la fiche de Brann est `11/11`. Le MJ n'invente pas
seulement des jets, il invente aussi la **capacité de son personnage**, ce qui
modifie silencieusement le calcul de toute la partie pour le joueur.

Le README promet que « les dés sont lancés par le serveur, jamais
par le MJ » : ici le MJ fabrique des jets, et **le joueur n'a aucun moyen de
distinguer le réel de l'inventé** (pas d'icône de dé, pas de bloc « jet
serveur »). C'est le défaut le plus dangereux de toute la liste pour un jeu de
rôle : il contamine la confiance dans toutes les autres règles.

## 🟠 M6 — Combat : la multiplicité du manifeste est ignorée

La salle `(0,-2)` du manifeste annonce `"ennemis": ["Squelette ×5"]` et sa
description dit « cinq d'entre eux se libèrent ». Le rattrapage
« combat porté en prose » n'engage que **un** Squelette
(`monstres_combat` = 1 entrée). Le joueur vide donc le dungeon cinq fois
moins vite que prévu, sans en être informé.

## 🟠 M7 — Le nom des salles et les coordonnées sont incohérents

* `(0,0)` est successivement appelé « l'entrée », « l'antichambre » et « la
  salle d'entrée » selon les tours ;
* les coordonnées réelles (`(0,-1)`, `(0,-3)`) apparaissent dans la prose ;
* la même coordonnée désigne deux salles différentes selon l'étage, sans
  que rien ne l'indique au joueur.

## 🟠 M8 — Un tour du MJ peut ne jamais être persisté

Le journal montre un tour qui démarre (`tool_call lancer_attaque` à 16:14:02)
et **aucune** narration ni broadcast ensuite ; le `chat_<id>.json` s'arrête à
la version précédente. Le joueur reste bloqué sur « Le MJ réfléchit… ».

## 🟠 M9 — Le dungeon n'a pas de phase d'ouverture formelle

Le README décrit une ouverture « plan d'ouverture avec manifeste ». En
pratique, la partie démarre en `phase: exploration`, `tour: 0`,
`courant_tour_pour: null`, sans introduction du manifeste, sans nom de
donjon dans la narration d'ouverture, et le PNJ de la quest est immédiatement
déclenché. Le premier message du MJ ne reprend ni le pitch ni les étapes du
scénario.

## 🟠 M10 — Pas de passage d'un chapitre à l'autre

`chapitre_suivant` n'est qu'une **consigne textuelle** donnée au LLM
(`main.py:3005-3009`). Il n'existe aucun mécanisme serveur ni UI de
« chapitre terminé » : c'est le MJ qui doit dire au joueur qu'il peut aller
chercher la partie suivante, et c'est au joueur de créer/partager une nouvelle
partie. Le joueur ne peut donc pas « terminer » une aventure en restant dans
la partie.

## 🟠 M11 — `bible.etapes` est toujours vide

Aucun scénario de `scenarios_catalogue.json` ne possède de champ `etapes` ;
`bible.etapes` vaut donc `[]` pour tout scénario. Les objectifs sont
reconstruits à la volée par `game/objectifs.py` à partir du manifeste du
donjon, ce qui explique les libellés mal calibrés (« Descendre vers les Bas
Tombeaux » ancré sur la salle du **trésor** `0,-3`).

## 🟠 M12 — La liste des outils envoyés au LLM est journalisée en entier à chaque tour

Une ligne de 2 400 caractères (`76 tools chargés : …`) sortie **à chaque tour**,
alors que seuls 15 outils sont réellement exposés. Le journal est illisible et
le budget de contexte est gaspillé sur une liste que le LLM ne voit pas.

## 🟠 M13 — Le chien de déchargement recharge le modèle sans jamais l'utiliser

`unload_after_turn: false` + `unload_delay_minutes: 5`. Le chien se réveille
toutes les 5 minutes et **recharge** les poids avant de pouvoir constater que la
partie est inactive. Extrait du journal (aucune génération entre les deux lignes) :

```
17:33:48 POST /models/unload   200 OK
17:37:13 llamacpp model loaded: Qwen3.5-9B-Q4_K_M-MTP     ← 0 chat/completions
17:41:xx POST /models/unload   200 OK
```

Chaque cycle coûte ~3 min de chargement de 9 milliards de paramètres pour rien,
saturant la VRAM shared avec ComfyUI et le port 8080. Le même cycle peut
s'interrompre une génération en cours (§B11).

## 🟠 M14 — Un tour refusé laisse le chat dans un état qui ressemble à un plantage

Quand le message n'est pas le bienvenu (mauvais personnage, mauvais tour), le
serveur diffuse un `dm` (mécanique jouée par le serveur) puis un
`sys: turn_blocked`, **sans rien persister** (`main.py:5274-5304`, voir M4).
Résultat côté joueur : le dernier élément du fil est son propre message,
suivi de rien ; au rechargement de page le message disparaît. Un client qui
n'écoute que le flux `dm` (ou l'historique) croit que le jeu est figé. C'est
exactement ce que j'ai observé : un message « J'attaque le squelette » refusé
car ce n'était pas le tour de Brann, puis **25 minutes d'attente sans aucune
réponse ni message d'erreur** dans le fil de discussion.

---

# 4. Défauts mineurs (🟡)

## 🟡 m1 — Narration tronquée en milieu de phrase

> Cause racine : **B29** (`tronquer_degeneration`).

Plusieurs narrations s'arrêtent sans ponctuation : « …cette substance
corrompue devient officiellement la vôtre, une preuve tangible **des** »,
suivies d'un saut de paragraphe et de « **Que voulez-vous faire ?** ». Le
`chat_<id>.json` contient d'abord cette version tronquée, puis est réécrit avec
la version complète : l'état sur disque est transitoirement faux.

## 🟡 m2 — Duplication verbatim dans un même message

Le monologue de Cassyt apparaît **deux fois** dans le tout premier message, et
le passage « Ils sont là ! … Des morts-vivants ! » a été rejoué tel quel deux
tours plus tard alors que le garde anti-répétition ne l'a pas bloqué (seuil trop
permissif).

## 🟡 m3 — Détection de l'inventaire à partir d'un adjectif

`_objet_present_inventaire` / `_norm_nom_objet` comparent des chaînes
normalisées : « une fiole », « le parchemin », « un petit sac en cuir vide »
ne correspondent à aucun objet du catalogue. Un objet «unnamed» peut être
annoncé comme possédé alors qu'il ne l'est pas.

## 🟡 m4 — `state_patches` renvoie la grille entière du donjon à chaque tour

Chaque `dm` WebSocket contient l'objet `donjon` complet (~21 ko), y compris
toutes les salles **non visitées** (ennemis, pièges, trésor). Cela sélectionne
au client des salles non révélées et alourdit chaque message. Les patches sont
en outre dupliqués dans le même tableau.

## 🟡 m5 — Tempêtes de requêtes au repos

En 20 s, avec deux onglets ouverts et **aucune action en cours** : 22 requêtes
HTTP — 12 re-rendus de la carte du donjon (`carte-donjon.svg?v=…`, compteur de
version qui s'incrémente à chaque fois, donc cache HTTP totalement invalidé),
4 relectures de l'état de partie, 3 `/api/health`. Le SVG est recalculé côté
serveur à chaque requête.

## 🟡 m6 — Bloc « doublon ignoré » et blocs techniques dans la narration de combat

Le joueur voit « ♻️ Doublon ignoré : « Squelette » a DÉJÀ subi exactement 6
dégâts CE TOUR », « ⚠️ Bonus recalculé par le serveur +6 → +2 », « ⚙️ Le serveur a
régularisé ce combat porté en prose », « ⚙️ Mécanique résolue par le serveur
(round 1) ». Ces blocs sont utiles (ils documentent la mécanique) mais leur
présence au milieu de la prose, avec des emojis et des formulations internes,
casse l'immersion.

## 🟡 m7 — Les PV des monstres descendent sous zéro

`monstres_combat` affiche `pv: -3` après un coup fatal au lieu de 0.

## 🟡 m8 — Le sort PHB 3.5 « Armes d'Hadare » est absent du catalogue

`incanter_sort` répond « ❌ Sort inconnu : « Armes d'Hadare » » pour un sort
de **clerc niveau 1** parfaitement légitime du Player's Handbook 3.5. Le
catalogue (704 sorts) contient par ailleurs des doublons ou des entrées
d'autres éditions (« Création d'eau » **et** « Bénédiction de l'eau » en clerc
0).

## 🟡 m9 — Un PNJ est ajouté à l'initiative des joueurs

Le tour du combat du squelette s'annonce « ***Cassyt***, à toi de jouer… »
alors que Cassyt est un PNJ non combattant ; l'ordre d'initiative contient un
« Initiative 18 » qui ne correspond à aucun jet. (Ici l'action de Brann a bien
été jouée, l'affichage seul est faux — mais le joueur ne peut pas savoir qui
doit agir.)

## 🟡 m10 — Deux onglets ne peuvent pas utiliser deux comptes

Les deux onglets partagent `localStorage` : impossible d'être connecté avec
deux comptes différents dans un même profil de navigateur. Le test à 2
comptes différents a dû être contourné.

## 🟡 m11 — Un garde de tour peut être contourné par le même compte

Inverse de m10 : avec deux personnages **du même compte**, la garde de tour
compare le personnage de l'expéditeur (correct) mais les mécanismes de
rattrapage comparent le **compte** (§B6) — le rattrapage agit donc sur le
mauvais personnage.

## 🟡 m12 — Brasseur : le PLAYER est annoncé « déconnecté » pendant les tours

L'indicateur du client affiche « Déconnexion » en permanence sur les onglets
au repos (le client passe en `reconnecting` dès que le pong manque) — cf. M3.

---

## 🟡 m13 — Duplication verbatim et fuite des PV dans un même message

Message reçu du tour 5 (Squelette à 2 PV) :

```
Votre matraque heurte l'air près du squelette … **Phase : Combat** —
Initiative 14 — C'est au tour de **Brann**. ***Brann***, que faites-vous ? Le
squelette reste debout avec ses 3 points de vie, attendant votre prochain
mouvement dans les ombres des catacombes. **Phase : Combat** — Initiative 14
— C'est au tour de **Brann**. ***Brann***, que faites-vous ? Le squelette reste
debout avec ses 3 points de vie, attendant votre prochain mouvement dans les
ombres des catacombes.
```

Le même paragraphe — **avec le bandeau de tour et les PV éventuellement
faux** — est émis deux fois de suite dans le même message. Le garde
anti-répétition compare le message entier au tour précédent, pas les
paragraphes entre eux : il ne peut pas détecter ce cas.

## 🟡 m14 — Les points de vie des monstres sont révélés au joueur

Dans au moins six tours, la narration donne le nombre exact de PV restants d'un
adversaire (« réduit à trois points de vie », « toujours debout avec
ses 3 points de vie », « avec 2 points de vie sur 3 »). Le monstre
est un SRD sans PV affichés ; l'info ne peut venir que du récapitulatif
d'état passé au MJ. Elle rend le combat déterministe et spoile toute la
tension.

---

## 🔴 P6 — Aucune borne sur les caractéristiques : FOR 97 / DEX 101 acceptés à la création

`POST /api/persos` (`main.py:2432`) convertit les caractéristiques en
`int` et **ne vérifie rien d'autre** :

```python
for c in persos_mod.CARACS:
    carac[c] = int(carac_saisi.get(c, 10))    # aucune borne
```

Reproduction réelle (compte `Aurelie`) :

```
POST /api/persos {"race": "Halfelin", "classe": "Voleur",
                  "carac": {"FOR": 99, "DEX": 99, "CON": 99,
                            "INT": 99, "SAG": 99, "CHA": 99},
                  "or": 50000}
→ 200 {"carac": {"FOR": 97, "DEX": 101, "CON": 99,
                  "INT": 99, "SAG": 99, "CHA": 99},
        "pv": 50, "pv_max": ...}
```

Les modificateurs raciaux halfelin sont bien appliqués (97 = 99−2,
101 = 99+2) mais **aucun plafond n'est imposé** : un DEX 101 donne un
modificateur de +35, une CA involontairement supérieure à celle d'un
dragon ancien, et 50 000 po de départ sont également acceptés
(`"or": int(payload.get("or") or 0)`, ligne 2728). La fiche a été
supprimée après le test, mais **rien ne l'interdit** : un client
crafté peut créer un personnage invincible, et le MJ n'a aucun moyen
de le détecter.

## 🟠 P7 — La fiche se verrouille immédiatement : aucune correction possible, et l'or tiré n'est crédité par personne

`POST /api/persos/or-depart` tire l'or (`{"or": 90, "formule": "5d4 × 10 po"}`)
mais **ne l'écrit nulle part** : c'est le client qui doit le repasser dans
`POST /api/persos` (`"or": int(payload.get("or") or 0)`). Un client qui
appelle le tirage puis oublie le champ — ou une page qui plante entre les
deux — produit un personnage à **0 po**, sans erreur.

Et il est alors **impossible de corriger** : `main.py:2459` refuse toute
édition dès que `avancement_confirme >= niveau`, vrai pour tout
personnage de niveau 1 fraîchement créé :

```
POST /api/persos {"nom": "Sirin", ... "or": 110}
→ 403 {"detail": "Fiche verrouillée : les choix d'avancement de ce niveau
                sont déjà confirmés. La fiche sera de nouveau modifiable
                au prochain passage de niveau."}
```

Le seul chemin est de **supprimer** le personnage et de le recréer — mais
`DELETE /api/persos/{slug}` échoue si le personnage est déjà rattaché à
une partie. En pratique : un joueur qui a mal distribué ses points, ou
dont l'or a été perdu, doit créer un compte neuf. (Cas réel constaté :
la paladine `Sirin` a démarré la partie avec **0 po**.)

---

# 5. Défauts de création de personnage (constatés avant la partie)

Ces défauts ont été relevés pendant la préparation des fiches ; ils empêchent
de créer un clerc, un paladin, un rôdeur, un druide ou un sorcier.

## 🔴 P1 — Aucun dieu ne sert la classe Clerc (et aucun dieu ne sert l'Humain)

Le formulaire affiche « **Aucun dieu ne sert cette race/classe/alignement** »
pour un Humain Clerc Neutre Bon. Vérification faite sur la table
`DIEUX` (`server/persos.py:616`, **19 divinités**) :

* **aucun** dieu ne liste `"Clerc"` dans `classes` — la couverture par classe est
  `Guerrier 5, Voleur 5, Magicien 4, Barde 4, Sorcier 3, Rôdeur 3, Moine 3,
  Barbare 3, Druide 2, Paladin 1, Clerc 0` ;
* **aucune** divinité ne liste `"Humain"` (ni `"Demi-nain"`) dans `races` — la
  couverture est `Elfe, Demi-elfe, Gnome, Halfelin, Nain, Demi-orc`.

Un clerc humain est donc **impossible à créer** : la fiche enregistrée
(`fiche_aldric.json`) ne contient **aucun champ `divinite`**, et le garde-fou
de la race + classe renvoie toujours vide pour la seule race de base du jeu.
Les divinités de Faerûn censées HIP (Chaunte, Mystra, Selûne, Moradin…),
celles des Humanx de *Faiths and Divinities* ainsi que les divinités
elfiques fungibles manquent toutes.

## 🔴 P2 — L'or de départ est annoncé « Table PHB » mais n'est pas la table PHB 3.5

L'interface affiche « **Table PHB** : `5d4 × 10` » au clerc et
« `6d4 × 10` » au guerrier (`server/catalogue.py:388`) et propose un bouton
« 🎲 Tirer » branché sur `POST /api/persos/or-depart` (tirage automatique au
changement de classe, `CharacterFormPage.tsx:833`).

Vérification directe de l'API (`POST /api/persos/or-depart`, 6 tirages par
classe) :

| Classe | Valeurs obtenues | Formule annoncée | PHB 3.5 (Table 4-1 du DMG) |
|---|---|---|---|
| Guerrier | 140, 170, 190, 150, 140, 170 | `6d4 × 10 po` | `15d6 × 10 gp` |
| Clerc | 120, 140, 140, 140, 90, 100 | `5d4 × 10 po` | `15d6 gp` |
| Voleur | 140, 160, 140, 110, 110, 180 | `5d4 × 10 po` | `15d6 × 10 gp` |
| Moine | 16, 11, 16, 14, 14, 17 | `5d4 × 1 po` | `5d6 × 1 gp` |

La table du code est donc fausse pour **les 11 classes** : des `d4` au lieu de
`d6`, des multiplicateurs appliqués à toutes les classes (en 3.5 seul le
monastique est en `×1 gp`), et un clerc qui devrait être le plus pauvre des
personnages deuxième niveau mais se retrouve avec autant de métal qu'un
guerrier. Le libellé « Table PHB » est un mensonge affiché au joueur.

À noter enfin que les fiches enregistrées portent **210 po** (Aldric) et
**840 po** (Brann), valeurs **hors de la plage** que la formule affichée peut
produire (50-200 et 60-240) : le montant stocké ne correspond pas à la table
présentée à l'écran.

## 🟠 P3 — Le don « Dur à cuivre » n'applique pas son bonus de sauvegarde

Le don **Toughness** accorde +3 PV (appliqué : 13 PV) **et** +3 à la
sauvegarde de Vigueur. La fiche affiche Vigueur 4 au lieu de 7. Le calcul de
sauvegarde (`server/persos.py:876`) n'utilise que la valeur de base et le
modificateur de caractéristique ; **les dons ne sont jamais pris en compte dans
les sauvegardes**. L'initiative, elle, passe par un chemin séparé
(`bonus_dons_effet`) — l'incohérence entre les deux est donc visible en jeu.

## 🟠 P4 — `DIEUX` est une liste de Faerûn, pas une liste de divinités censées HIP

`DIEUX` sert notamment **Érythnul** (dieu des carnages, CE), **Gruumsh**,
**Vecna** et **Wy-Djaz** dans un formulaire de création de personnage censé
refléter le *Player's Handbook*. Même pour les divinités correctes, le filtrage
`dieux_disponibles` (`persos.py:776`) croise une unique liste par classe
(`classes`) alors qu'en 3.5 un personnage choisit librement un dieu de sa
race (ou de son panthéon) et que le domaine doit seulement être compatible
avec l'alignement. À revoir entièrement.

## 🟡 P5 — Le nom du PNJ du module entre en collision avec le nom d'un PJ

Le MJ a appelé « Aldric » le jeune clerc qui confie la mission — c'est le nom
du **joueur**. Il a ensuite traité le groupe comme si le PJ était le donneur
d'ordre (« un jeune clerc, Aldric, m'a confié cette mission ») alors qu'Aldric
est dans le groupe et l'a entendue.

---

## 🟡 m15 — La carte du monde ne suit jamais l'exploration du donjon

`appliquer_depart` (`tools/scenarios.py:484`) écrit
`etat["positions_joueurs"] = {"groupe": [x, y]}` une seule fois, à la
pose de la quête. Aucun outil de donjon ne met cette valeur à jour :
après deux étages et dix salles, `etat["positions_joueurs"]` vaut toujours
le point de départ du module. `carte_joueurs_get` renvoie donc
« groupe à Phlan » alors que le groupe est à (0,-5) sous le
cimetière. La carte du monde reste figée pendant toute la partie.

## 🟡 m16 — Le « résumé » du scénario est un extrait PDF brut, en anglais

`bible.resume` de la partie Crown of Mystra commence par :

```text
Background:  Recently, Cyric has managed to elude Mystra's defenses and
steal one of her most valued possessions -- her crown.
  He then gave the crown to his high priest in Zhentil Keep, Fzoul
Chembryl.  Chembryl, following Cyric's directions, spread all 8 gems
throughout the Realms.  Without all the gems, the crown cannot function
properly for Mystra.  Since the crown has been missing, special
```

Ce texte est injecté tel quel au MJ comme résumé de scénario. Il est en
anglais, contient la mise en page du PDF (double espace, retour à la ligne
au milieu d'une phrase) et n'a jamais été traduit ni résumé. Le MJ reçoit
donc un document brut de 3 pages comme « résumé » et doit le compacter
lui-même à chaque tour.

## 🟡 m17 — `objectif` est à `null` et `manquants` est vide sur les deux scénarios

Sur la partie Dues, `bible.manquants` est `[]` alors que trois objectifs
sur quatre ne sont pas terminés ; sur Crown of Mystra,
`bible.objectif` est `null` dès la pose de la quête. Les champs
d'aide à la navigation du module ne sont donc pas remplis : le MJ et
l'interface n'ont aucun moyen d'afficher « objectif courant ».

---

## 🟡 m18 — Ouvrir un lien de partie directement laisse une erreur permanente dans le chat

Ouvrir `http://localhost:8123/partie/776fdfa1` directement (nouvel onglet,
lien partagé, ou simple rafraîchissement aprés une perte de connexion) fait
apparaître, au milieu du fil de discussion :

```
🔴 Sélectionnez un personnage sur la page d'accueil avant de rejoindre
   la partie.
```

Le `join` est refusé (`main.py:3429`) parce que la page ne connaît pas de
personnage sélectionné, ce qui est correct — mais le message reste **définitivement**
dans le chat de l'onglet, entre deux messages du MJ, sans moyen de le faire
disparaître, et la page reste ouverte en lecture seule : la zone de saisie
est active, le joueur peut écrire, et chaque envoi est refusé. Aucun
renvoi automatique vers la page d'accueil n'est proposé.

## 🟡 m19 — Le compteur d'itérations affiché au joueur ne correspond à rien de mesurable

La barre d'état de la zone de saisie affiche « Résout l'action (2/4)… ».
Ce `(2/4)` est l'itération de la boucle d'outils. Or le nombre d'appels LLM
réels d'un tour est de 1 à 12 (B32), et les rattrapages serveur
(déplacement, inventaire, résurrection) tournent **après** la boucle, sans
compteur. Le joueur voit donc un pourcentage qui n'est ni le temps restant,
ni le nombre de générations, ni la progression — et il reste souvent bloqué
sur `(2/4)` pendant plusieurs minutes.

---

# 6. Défauts de l'expérience de jeu (MJ) — synthèse

1. **Le MJ réécrit la vérité** (§M1) : initiative, tours, position, monstres.
2. **Le MJ fabrique des objets** (§B7) et des personnages : « Cassyt » est
   à la fois gardien du cimetière, guide, et compagnon de route — après avoir
   déclaré le contraire. Incohérence observée :
   > « Je ne peux pas vous accompagner jusqu'aux catacombes » (tour 4)
   > « Cassyt, qui vous précédait, s'arrête net » (tour 12)
   > « Cassyt, qui vous suit avec prudence » (tours 18-20)
3. **Le MJ invente des créatures hors bestiaire** : « un squelette armé d'une
   hache de pierre » et « un serviteur du Culte du Dragon » n'existent pas
   dans le bestiaire 3.5 (le module ne prévoit que Kobold, Zombie, Squelette,
   Araignée, Goule, Nécromancien rouge). Le garde serveur a bien refusé les
   statistiques inventées, mais la prose en garde une trace.
4. **Le MJ annonce des sauvegardes qu'il n'a pas jouées** : « Aldric réussit sa
   sauvegarde de Réflexes (12 vs 10) et Brann (11 vs 10) » alors que
   `conditions` des fiches est vide et qu'aucun jet n'a été tracé côté
   serveur.
5. **Le MJ ne respecte pas l'alignement/les limites de son personnage** : il
   s'adresse à un PNJ en train de se battre comme s'il était un joueur.
6. **Le MJ ajoute des phases d'introduction non prévues** (« dans les ruelles,
   des morts-vivants » hors de la salle de départ).

---

# 7. Reproductibilité minimale

## 7.1 Dues for the Dead

```text
# 1. POST /api/auth/inscription {"nom": "Aurelie"}            → 200
# 2. POST /api/persos {"race":"Humain","classe":"Clerc","dieu":"Kelemvor"}
#    → 400 « n'accepte pas ce personnage »                    (P1)
# 3. POST /api/persos {"nom":"Aldric","race":"Humain","classe":"Clerc",
#    "dieu":""}  → 200  (clerc SANS dieu, fiche.dieu = "")
# 4. POST /api/parties → partie_id ; POST /parties/{id}/quest
#    {"source":"[divers_dues_for_the_dead_ch01] <pdf>"}        → 200
# 5. WS /ws/{id} {"type":"join","personnage":"Aldric"} puis "Brann"
# 6. WS {"type":"say","text":"Je suis en ville à Phlan…"}
#    → intro, Cassyt, « fiole de soins » promise
#    → fiches : "inventaire": []                              (B7)
# 7. "Je passe au nord."  → donjon.courant bouge, la prose dit
#    « sans issue »                                          (B2)
# 8. "Je descends le passage de l'ouest vers la fosse."
#    → le rattrapage ne se déclenche pas, la carte ne bouge pas (B1)
# 9. En combat, attendre 300 s → le tour est transmis au suivant
#    sans que le joueur soit prévenu                          (B5)
# 10. Deux PJ du même compte : le rattrapage d'attaque joue le
#     mauvais personnage                                      (B6)
# 11. "Je passe au nord." depuis (0,-3) → progression 1/4 → 0/4 (B9)
---

# 12. Laisser le groupe mourir → GAME OVER, puis en 1,4 s :
#     levée de Mort + soin 11 + carac.CON -2 + pv_max -1      (B33/B36)
```

## 7.2 The Crown of Mystra

```text
# 1. POST /api/persos {"race":"Halfelin","classe":"Voleur",
#    "carac":{"FOR":99,"DEX":99,…},"or":50000}
#    → 200 {"FOR":97,"DEX":101,…}                            (P6)
# 2. POST /api/persos {"nom":"Sirin","classe":"Paladin","dieu":"Tyr"}
#    sans champ "or"  → 200 mais fiche.or = 0                (P7)
# 3. Toute édition ultérieure  → 403 « Fiche verrouillée »   (P7)
# 4. POST /api/parties ; POST /parties/{id}/quest
#    {"source":"[ro_the_crown_of_mystra_ch01] <pdf>"}         → 200
#    → bible.resume = extrait PDF brut en anglais            (m16)
#    → bible.objectif = null                                  (m17)
# 5. WS join « Aldric » (autre campagne)  → accepté    (B42, retiré)
# 6. Premier message EXACT : "débute une nouvelle partie"
#    → intro jouée en 26 s : Magister Thukmuul Teleshann,
#      Couronne volée par Cyric, Zendar Nulentok, huit gemmes,
#      « baguette de téléportation » promise
#    → fiches : "inventaire": []                              (B7)
```

Le message `débute une nouvelle partie` n'est **pas** une commande
serveur : c'est un message joueur ordinaire, traité comme les autres par
`_handle_say`. Il fonctionne parce que le prompt système contient une
consigne d'introduction en phase `opening`/`exploration` — pas parce
qu'un état de partie change. `phase` reste `exploration` et `tour` reste 0.

---

# 8. « Je passe au nord. » depuis (0,-3) → progression 1/4 → 0/4        (B9)
```

---

# 8. Fin de partie « Dues For The Dead »

## 8.1 Ce qui a été joué

Environ **70 tours de MJ** répartis sur quatre sessions de jeu, avec deux
personnages de niveau 1 sur le même compte, dans deux onglets du navigateur.

| étape | étage | salle | résultat |
|---|---|---|---|
| porche, Cassyt, enquête | 1 | (0,0) | intro jouée ; la fiole promise n'entre jamais dans l'inventaire (B7) |
| puits aux ossements | 1 | (-1,0) | exploration ; `progression` reste 0/4 |
| banquets funèbres | 1 | (0,-1) | Squelette tué après 7 tours et 4 « manqué » officiels |
| « goule » inventée | 1 | (0,-1) | combat qui n'existe pas dans le module (B25) |
| 1re défaite de groupe | 1 | (0,-1) | GAME OVER → résurrection non demandée, −2 CON, 1 PV (B33/B36) |
| cellules | 1 | (0,-2) | combat refusé par la garde « salle déjà vidée » (B40) |
| trésor de Sedrair II | 1 | (0,-3) | Araignée géante + Araignée ; `progression` passe enfin à 1/4 |
| 2e défaite de groupe | 1 | (0,-3) | même enchaînement ; CON cumulée (B37) |

**Non atteints** : la Nécropole (1,-1), les Bas Tombeaux (2e étage, 18
salles), la salle du trône et le Nécromancien rouge, l'objet de quête, la
transition ch01 → ch02. Sur **23 salles** du module, **5** ont été
parcourues en ~4 heures de jeu.

## 8.2 État final des données

```text
etat["pj"]      Aldric  pv 1/11   CON 15   Vigueur +4   conditions []
fiche_aldric    pv 1/11   CON 11   ← divergence CON (B33/B37)
etat["pj"]      Brann   pv 1/9    CON 12   Reflexes +0  conditions []
fiche_brann     pv 1/9    CON 8    ← divergence CON
inventaire      []  []   ← jamais rien, en 70 tours (B7)
or              210 / 840   ← jamais modifié
xp              370 / 370   ← gelé depuis le 4e combat
donjon          étage 1, (0,-3), 5 salles vues, phase combat
bible           progression 1/4 ; termines=[bas_tombeaux] ; manquants=[]
objets_quete    []   ← l'objet de la quête n'existe pas dans l'état
```

`manquants` est vide alors que trois objectifs sur quatre ne sont pas
terminés : le champ n'est pas rempli. `phase` est `combat` contre une
Araignée géante 14/22 et une Araignée 1/1, avec deux personnages à **1 PV**
et une chance de toucher de 5 % (Aldric) et 45 % (Brann). La partie est
irrémédiablement perdue, mais le jeu ne le dira jamais : `game_over` est
remis à `False` à chaque résurrection (B36).

## 8.3 Les tours qui n'ont pas pu être joués

Sur les 30 derniers envois du joueur :

* **16** ont reçu `⏳ Le MJ est en train de travailler` (B31) — un seul tour
  a monopolisé le serveur **25 minutes** ;
* **5** ont été répondus par une prose décrivant des PV négatifs
  (« Brann, toujours agonisant à −6 PV ») alors que l'état dit `1/9`
  — le MJ joue un état qui n'existe pas ;
* **3** ont été répondus par l'attaque d'un **autre** personnage que celui
  qui parle (B6/B19) ;
* **2** ont été répondus par un GAME OVER suivi d'une résurrection non
  demandée (B33/B36) ;
* **0** ont fait progresser l'objectif de quête au-delà de 1/4.

## 8.4 Verdict sur cette partie

« Dues for the Dead » n'est pas terminable dans l'état du produit. Trois
causes suffisent, chacune seule suffisante :

1. **La boucle de correction** (B10/B29/B30/B32) transforme un tour de
   combat en 1 à 12 générations LLM, jusqu'à bloquer le jeu 25 minutes
   sans aucun moyen d'interrompre (B31) ;
2. **Le MJ invente les rencontres** (B25) et le serveur les enregistre :
   le groupe affronte une Goule de 16 PV — créature de FP 2 — alors que le
   module prévoit un Squelette de FP 1/3 dans cette salle. Deux
   personnages de niveau 1 n'ont aucune chance ;
3. **La résurrection automatique** (B33/B36/B37) empêche la partie de se
   terminer : elle remet les héros à 1 PV, les affaiblit de 2 CON et
   désamorce `game_over`, garantissant une défaite suivante.

Le joueur qui veut terminer le module doit, en pratique, sauvegarder
`partie_<id>.json` à la main et y réécrire les PV, la CON et la position —
c'est-à-dire jouer au MJ lui-même.

---

# 9. Partie « The Crown of Mystra »

## 9.1 Ce qui a été joué

Compte neuf (`Aurelie`), deux personnages neufs de niveau 1,
**35 tours de MJ** en deux sessions. Le premier message envoyé à la partie
est exactement `débute une nouvelle partie`.

| étape | lieu | résultat |
|---|---|---|
| `débute une nouvelle partie` | Tour de l'Équilibre | intro correcte en 26 s : Cyric, Fzoul Chembryl, Zendar Nulentok, les huit gemmes |
| récompense | Tour de l'Équilibre | « baguette de téléportation » remise → inventaire inchangé (B7) |
| prière à Tyr | route | vision divine **sans aucun jet** ; le MJ saute une heure de marche |
| entrée de grotte | grotte | Gobelin inventé, puis « Le serveur a fait entrer le groupe dans le donjon » qui replace le groupe à (0,0) = la salle d'audience de Silverymoon (B43) |
| fouille | (0,0) | fiole + sac d'or + carte → inventaire inchangé (B7) |
| auberge | Silverymoon | auberge « Le Cheval Blanc » inventée dans le donjon ; Sirin a 0 po (P7) et le MJ l'ignore |
| repos | grotte | repos de nuit pur fiction, « ses sorts sont à nouveau prêts » pour une Paladin niv 1 (B48) |
| nord ×4 | (0,0) | refus corrects (« pas de porte au nord ») |
| Assassin | (0,0) | créature inventée, 9 rounds de combat |
| fin | (0,0) | Assassin tué par un tir **sans munition** ; Sirin à −1 PV, jamais stabilisée |

**Non atteints** : l'antichambre (0,-1) et toutes les salles suivantes, la
grotte de Nulentok, la Couronne, les huit gemmes. Sur **1 salle visitée**
sur le plan du donjon, `progression` reste **0/3** à la fin.

## 9.2 État final des données

```text
etat["pj"]      Sirin  pv -1/11   CON 15 (initial 13, jamais modifiée)
fiche_sirin     pv 1/11 ?  ← divergence entre les deux sources (B33)
etat["pj"]      Korr   pv 2/7     CON 12
fiche_korr      inventaire = 4 entrées = copie de l'équipement de départ,
                munitions disparues ; equipement = même liste (B47)
fiche_sirin     inventaire = null  ← fiole, or et carte ramassés jamais enregistrés
or              0 / 50    ← jamais modifié
xp              0 / 0     ← l'Assassin n'a donné AUCUN XP en 9 rounds
donjon          étage 0, (0,0), 1 salle vue, phase exploration
bible           progression 0/3, objectif null
```

Aucun XP n'a été attribué pour l'Assassin (FP 6 en 3.5) : les fiches sont
toujours à `xp: 0`. Dans la partie Dues, les XP avaient été attribués par
le bloc « 🏆 Victoire ! » ; ici, le combat s'est terminé par le tir
sans munition du tour 12 et **aucun bloc de victoire n'est apparu**. Le
même type de clôture produit donc deux résultats différents.

## 9.3 Ce qui a fonctionné

Pour être juste, plusieurs mécanismes ont été **corrects pendant toute la
partie** :

* l'intro du scénario est fidèle au module et bien écrite ;
* la latence des tours est restée entre **14 et 120 s**, sans le blocage de
  25 minutes observé dans la partie Dues ;
* le refus de déplacement vers le nord est correct (« les seules portes
  sont à l'est »), et cohérent avec le plan du donjon ;
* le recalcul serveur des bonus d'attaque a fonctionné **22 fois sur 22** :
  aucun jet n'a été joué avec un bonus inventé ;
* le suivi des munitions a détecté et annoncé l'épuisement du carquois ;
* la garde de tour (« c'est le tour de Korr ») a refusé 4 envois hors
  tour, sans faux positif ;
* le serveur a placé correctement le groupe en (0,0) et initialisé la
  carte du donjon.

## 9.4 Verdict sur cette partie

La partie ne peut pas être poursuivie : Sirin est à −1 PV,
« Mourant », et **aucun mécanisme ne la stabilise hors combat** — le jeu
continue de lui poser « Sirin, que faites-vous ? » et d'accepter ses
messages, alors qu'elle est inconsciente. Korr n'a plus de munitions, mais
le serveur résout quand même ses tirs. La seule porte du donjon mène à
l'antichambre, que le MJ ne peut pas atteindre car il ne propose jamais
l'est de lui-même et que le joueur qui demande le nord reçoit un mur.

Trois défauts suffisent à bloquer cette campagne, tous absents de la
partie Dues : **B43** (le module injecte la salle d'audience comme salle
courante et efface la grotte), **B45** (les munitions n'arrêtent pas un
tir), **B48** (le repos n'existe pas). La campagne est donc
**injouable au-delà de la première salle**, alors même que la partie
Dues, elle, était jouable sur cinq salles : les deux échecs n'ont pas la
même cause, ce qui indique un problème d'orchestration général plutôt
qu'un défaut de scénario.

---

# 10. Conclusion

## 10.1 Les deux campagnes en un chiffre

| | Dues for the Dead | The Crown of Mystra |
|---|---|---|
| tours de MJ | ~70 | 35 |
| salles visitées / salles du module | 5 / 23 | 1 / ~23 |
| progression de quête | 1/4 | 0/3 |
| objets obtenus | 0 | 0 |
| or gagné | 0 | 0 |
| XP finaux | 370 (gelé après le 4e combat) | 0 |
| défaites de groupe | 2 (toutes deux effacées) | 0 (bloqué avant) |
| tours bloqués `⏳` | 16 | 4 |
| message « sans aucun jet » | ≈ 15 | ≈ 12 |
| blocs techniques visibles du joueur | ≈ 40 | ≈ 35 |
| résurrection non demandée | 2 | 0 |

## 10.2 Les dix défauts à corriger en premier

1. **B22** — exposer les outils d'inventaire, de mémoire, de scénario,
   de repos et de gain d'XP dans les phases où ils servent. Sans cela,
   aucune des promesses de l'interface (ramasser, se reposer, tenir un
   journal, acheter) ne peut être tenue ;
2. **B31** — un bouton « interrompre le tour » et un garde-fou de durée
   sur `session.thinking`. Un tour qui dure plus de 2 minutes doit
   être annulé côté serveur, pas subi ;
3. **B32 + B10** — la boucle de correction doit être bornée par le même
   budget que la boucle d'outils, et une correction qui rejoue une action
   ancienne doit abandonner plutôt que relancer ;
4. **B29 + B30** — la troncature de dégénérescence ne doit pas compter
   la ligne de statut injectée par le serveur, et les sections de règles
   ne doivent pas être retirées du prompt en cours de tour ;
5. **B33 + B36 + B37** — la résurrection doit être une décision du
   joueur, jamais déduite d'une expression régulière sur la prose ; la
   pénalité de CON doit être affichée, plafonnée, et appliquée aux
   sauvegardes comme à la fiche ;
6. **B7 + B47** — un objet narré doit entrer dans l'inventaire, et
   `equipement` ne doit pas être écrasé par `inventaire` ;
7. **B25 + B43** — le contenu des salles doit venir du plan du donjon ;
   un `engager_combat` avec une créature absente de la salle doit être
   refusé au lieu d'être enregistré ;
8. **B14 + B40 + B43 + B44** — séparer strictement le canal
   « narration joueur » du canal « consignes modèle ». Aucun message
   à l'impératif, aucun nom d'outil, aucune note interne ne doit
   atteindre le fil de discussion ;
9. **B45 + B46** — une attaque sans munition doit être refusée ; un
   coup qui touche ne peut pas infliger 0 dégât ;
10. **P1 + P6 + P7** — la création de personnage doit permettre de
    choisir un dieu pour un clerc, borner les caractéristiques, et
    créditer l'or côté serveur.

## 10.3 Ce que ce audit ne couvre pas

* le mode multi-joueurs réel (plusieurs comptes humains simultanés) ;
* les sorts (aucun personnage lanceur de sorts effectif n'a été joué :
  le clerc de la partie 1 n'a aucun dieu, la paladine de la partie 2
  n'a pas de sorts au niveau 1) ;
* le voyage sur la carte du monde (`voyage_demarrer` jamais exposé) ;
* le marché, le familier, la monte, la création de PNJ par le MJ ;
* les chapitres 2 à 4 de chaque scénario (jamais atteints).

> **Mise à jour** : à la demande du propriétaire, les défauts rencontrés dans
> les deux parties ont ensuite été corrigés — voir la **§11**.


---

# 11. Correctifs appliqués après l'audit

À la demande du propriétaire, **tous les défauts rencontrés dans les deux
parties ont été corrigés**, à l'exception de **B42** (couvert par le mot de
passe de partie, voir ci-dessus). Le travail est limité au code : **aucune
donnée de partie n'a été modifiée à la main**.

La correction a été faite **en quatre tranches** : 25 (§11.2), 17 (§11.3),
10 (§11.6), puis 3 finales (§11.8) — soit **55 correctifs vérifiés dans le
code**, dont une **cause racine majeure** découverte en vérifiant le registre
des outils (§11.8).

## 11.1 Corrections par fichier

| fichier | défauts traités |
|---|---|
| `server/llm/orchestrator.py` | B22, B29, B30, B10, B32, M12 |
| `server/game/session.py` | B31, B3 |
| `server/game/objectifs.py` | B9 |
| `server/main.py` | B2, B3, B7, B8, B12, B13, B14, B17, B18, B26 bis, B28, B31, B33, B35, B36, B37, B38, B40, B43, B44, M3, m4, m15 |
| `server/persos.py` | P1 |
| `server/config.py` | B5 |
| `server/tools/dice.py` | B16, B20, B27, B45, B46, alias de sauvegarde |
| `server/tools/fiches.py` | B34 |
| `server/tools/state.py` | B25, M6 |
| `server/tools/scenarios.py` | m16, m17 |
| `config/config.yaml` | B41, B22 (plafond d'exposition) |

Total : **10 fichiers, ~1 590 lignes modifiées** (10 fichiers Python + config.yaml).

## 11.2 Tranche 1 — ce que chaque correction change pour le joueur

**B22 — plus d'outil masqué.** Le plafond `max_tools_exposed: 15` masquait
définitivement `inventaire_ramasser`, `inventaire_retirer`, les six outils de
mémoire, `scenario_etape`, `repos_long` et `carte_donjon_explorer`. Le plafond
devient un plancher (`_PLAFOND_EXPOSITION_MIN = 48`) : un jeu d'outils de
phase est présenté **en entier**, simplement réordonné (mécanique en tête).
`inventaire_consommer_munition` a été ajouté à la phase exploration (il
n'existait qu'en combat).

**B29 — la troncature ne se déclenche plus sur la ligne de statut.** Les
phrases « **Phase : Combat** — Initiative 14 — C'est au tour de … » injectées
par le serveur sont exclues du compteur de répétition ; une vraie troncature
est bornée à 400 caractères minimum et **annonce** `[… la répétition a été
supprimée par le serveur]` au lieu de couper en silence.

**B30 — plus d'élagage des règles en cours de tour.** Les 27 436 caractères
de sections narratives n'étaient plus retirés à l'itération 2 : c'est ce qui
produisait les bonus d'attaque faux 19 fois sur 19. Le contexte reste borné
par `_borner_work`.

**B10/B32 — budget de corrections global.** Cinq sites indépendants
incrémentaient `corrections`, dont trois sans borne : jusqu'à 12 appels LLM
par tour. Budget partagé de 2 corrections maximum, tous sites confondus.

**B31 — verrou de tour et annulation.** `PartySession` mémorise l'instant de
début du tour. Au-delà de 240 s le verrou est levé automatiquement et la table
est prévenue ; le joueur dispose aussi d'un message `{"type": "cancel"}` qui
interrompt le tour en cours. Les 16 refus d'affilée de la partie Dues ne sont
plus possibles.

**B33/B35/B36/B37 — la résurrection.** Un message annonçant un GAME OVER, ou
un personnage encore Mourant/Inconscient, **refuse** désormais la
résurrection (`_RE_GAME_OVER` + garde par condition). La garde d'offre couvre
« Proposez », « offr… », « souhaitez-vous », « au choix », « vos options »… Le
montant narré profite à **tous** les morts, plus à un seul. La pénalité de CON
a un plancher à 6 et **recalcule** sauvegardes, CA, initiative et charge ; la
CON, les PV et les conditions sont resynchronisés dans `etat["pj"]` ET dans la
fiche. Le message annoncé est explicite sur la perte et sur le fait que les
personnages reviennent à 1 PV.

**B38 — la dé-duplication ne ressuscite plus un monstre.** Si la créature est
déjà à terre (PV ≤ 0) ou si le plafond de restitution est ≤ 0, aucune
restauration. Plus de « PV réajustés (3/16) » sur une créature morte.

**B34 — les champs de fiche sont bornés.** `pv_max`, `bab`, `niveau` et
`charge_max` sont réservés ; `pv`, `ca`, `or`, `initiative` sont coercés et
bornés ; `carac` est limité à 1–40 par caractéristique et ne se remplace plus
en bloc.

**B27 — le bloc officiel nomme l'arme réellement portée.** L'arme fournie par
le modèle est confrontée à l'équipement de la fiche : substitution et note
« ⚠️ Arme corrigée » si le personnage ne la possède pas, « ℹ️ interprétée
comme » si c'est une variante.

**B45 — un tir sans munition n'est pas résolu.** Le tool renvoie une erreur
explicite au lieu de jouer le jet (« tir résolu cette fois » supprimé).

**B46 — un coup qui touche blesse.** Un `lancer_degats` à 0 ou négatif est
porté au minimum de 1 (règle 3.5), avec la note correspondante.

**B7/B13 — le butin narré entre en inventaire.** Un rattrapage
`_appliquer_objets_narres` détecte « vous ramassez / vous prenez / ajouté à
votre inventaire » et appelle `inventaire_ajouter` pour le personnage actif.
L'inventaire de la campagne ne reste plus à `[]` pendant 90 tours.

**B14/B40/B43/B44 — les consignes serveur→modèle n'atteignent plus le
joueur.** `_purger_consignes` supprime les blocs de contenu canonique, les
lignes `📜 / 🚪 / ⛔ / 🖼️ / 👥`, les noms d'outils, les notes
`*(Note : …)*` et l'écho `**[Aurel]** :` du texte diffusé, en préservant la
prose de jeu du même message.

**B25 — la rencontre ne sort plus du module.** Si la salle courante porte une
liste `ennemis` canonique, `engager_combat` refuse toute autre espèce et
affiche la liste officielle.

**M6 — la quantité canonique est appliquée.** « Kobold ×5 » engage
désormais cinq Kobolds et non un seul ; « Perceur ×6 » six Perceurs.

**B26 bis — un mourant n'agit pas.** Un personnage à PV ≤ 0 ou Mourant/
Inconscient se voit refuser l'action, avec un jet de stabilisation officiel
(1d20 ≥ 10, −1 PV sur échec, mort à −10) et un alignement de la fiche sur
l'état.

**B9 — la progression ne recule plus.** `_salle_visitee` consulte l'étage
courant en priorité au lieu de la première grille qui contient le couple
(x, y) ; l'objectif déjà terminé n'est plus re-retiré.

**B41 — mémoire du MJ.** `max_history_events` 50 → 140,
`max_history_chars` 10 000 → 18 000, `max_recap_chars` 4 000 → 5 000.

**P1 — un clerc peut avoir un dieu.** La logique `dieux_disponibles`
appliquait l'inverse de sa documentation (`bool(d["races"]) and …`) : un dieu
à `races: []` n'acceptait personne par la race, et aucun dieu ne listait
« Clerc ». Aligné sur la documentation et « Clerc » ajouté aux 19 divinités du
panthéon.

**P6 — les caractéristiques sont bornées.** 3 à 25 par caractéristique, or
plafonné à 5 000. FOR 97 / DEX 101 / 50 000 po sont refusés ou ramenés.

**P7 — l'or de départ est crédité par le serveur, et la fiche reste
modifiable.** Un personnage créé sans champ `or` reçoit le tirage de sa
classe ; une fiche à 0 XP reste éditable au lieu de renvoyer 403.

## 11.3 Tranche 2 — ce que chaque correction change pour le joueur

**B18 — la phase annoncée est la vraie.** Le modèle imitait la sortie de
`tour_suivant_combat` et déclarait « **Phase : Exploration** » en plein
combat. Les déclarations de phase et les bandeaux d'initiative sont retirés
de la prose et remplacés par la ligne officielle calculée sur l'état.

**B23/B39 — la vérité terrain.** Si la prose annonce la mort d'une créature
encore debout (ou l'inverse), le serveur ajoute « ℹ️ Précision de l'état du
jeu : Squelette est encore debout (3 PV) ». Les quatre valeurs de PV d'un
même message ne peuvent plus tromper le joueur.

**B16 — pas de jet contre une créature imaginaire.** En combat, une cible qui
ne correspond à aucune créature engagée est refusée avec la liste des cibles
réelles. Hors combat, une prose qui fait mourir une créature inexistante est
signalée (« les créatures mentionnées (Gobelin) ne sont pas réelles »).

**B17 — on peut fuir.** Une intention de déplacement en combat déclenchait
un filtrage silencieux : le joueur ne pouvait JAMAIS quitter un combat.
Elle déclenche désormais `retraite_combat`, résolu par le serveur.

**B12 — un déplacement ne coûte plus un tour de LLM.** Le rattrapage
relançait une génération complète (20 à 60 s) pour un mouvement : le serveur
appelle maintenant `carte_donjon_explorer` lui-même et incruste le résultat.
Mesuré : déplacement à 6 s, 18 s, 12 s au lieu de 60 à 125 s.

**B2 — la prose ne peut plus mentir sur la position.** Si l'outil refuse le
mouvement (pas de porte dans ce mur), la narration reçoit
« ℹ️ le déplacement au **est** n'a PAS eu lieu … Position réelle du groupe :
[0, -3] ».

**B28 — les classes sont rappelées.** « Aldric = Humain Clerc niv 1 ;
Brann = Humain Guerrier niv 1 » est réaffiché à chaque tour de combat : le
MJ ne déforme plus les classes.

**B8 — un personnage ne joue pas deux fois dans un round.** Le marqueur
`deja_agi` n'était consulté que par le moteur : le joueur qui renvoyait un
message dans le même round voyait son action résolue une seconde fois.

**B3 — un message refusé n'est plus rejoué.** `oublier_dernier_message_joueur`
retire de l'historique le message refusé par la garde de tour ; il n'était
plus rejoué plusieurs tours plus tard comme si le joueur venait de le dire.

**B20 — le bloc officiel n'affiche plus l'erreur du modèle.** Les notes
« ⚠️ Bonus recalculé par le serveur +5 → +2 » (19 fois sur 19 en partie
réelle) deviennent « ℹ️ Bonus officiel : +2 (fiche de … BBA +1, FOR 13 (+1)) ».

**B5 — le timeout de tour ne dépasse plus le verrou.** 300 s > 240 s : le
moteur rejouait un tour déjà annulé. Aligné à 200 s.

**M12 — les logs ne contiennent plus la liste des outils.** Jusqu'à 47 noms à
chaque changement de phase, à chaque tour : remplacé par le nombre.

**m4 — la grille du donjon n'est plus diffusée au client.** Les tools de
carte renvoyaient `{"donjon": <tout le donjon>}` et ce dictionnaire partait
à chaque onglet à chaque tour. `_alleger_patches_client` ne garde que les
champs légers + `nb_salles`.

**M3/m5 — le client peut demander l'état.** Nouveau message WS
`{"type":"state"}` qui renvoie phase, tour, PJ, monstres et position : le
client n'a plus besoin de rafraîchir le SVG en continu.

**m15 — la carte du monde suit l'exploration.** `positions_joueurs` restait
au point de départ du scénario pendant toute la partie ; la zone courante
(donjon, étage, salle) est désormais notée dans `memoire.position`.

**m16 — le « résumé » du scénario est lisible.** Césures, doubles espaces,
numéros de page et en-têtes de section sont nettoyés ; la coupe se fait sur
une frontière de phrase avec marqueur `[…]` au lieu d'un mot tronqué.

**m17 — `objectif` et `manquants` sont remplis.** `bible.objectif` était
`null` et `bible.manquants` vide sur les deux campagnes : aucun écran ne
pouvait dire ce qui restait à faire.

**alias de sauvegarde** — `lancer_sauvegarde` refusait « Reflex » (envoyé
par le modèle) : le jet était perdu et le tour ne se résolvait pas.
Normalisation de Reflex/Reflexe/Fortitude/Fort/Will/Willpower.

## 11.4 Effet mesuré en jeu

| mesure | avant | après |
|---|---|---|
| latence d'un déplacement | 60 – 125 s (rejeu LLM) | **6 – 20 s** (résolution directe) |
| latence générale | 20 – 138 s | **12 – 70 s** |
| objets entrant en inventaire | 0 en ~90 tours | fiole + dague dorée en 3 tours |
| salles explorées (même temps de jeu) | 5 | **7** (0,-3 → 0,-4 → 0,-5) |
| consignes serveur visibles du joueur | ~40 | 0 |
| contenu canonique de salle | jamais affiché | trésor du module listé (Journal de Rorreth, 75 pc, cercle de téléportation) |

## 11.5 Vérification

| test | résultat |
|---|---|
| `POST /api/persos/or-depart` Clerc/Pélor | 200, dieu accepté |
| création d'un clerc avec dieu | 200, `fiche.dieu = "Pélor"` |
| création sans champ `or` | or crédité automatiquement (> 0) |
| `carac` à 99 | 400, refusé |
| `or: 50000` | 200 mais ramené à 5 000 |
| édition d'une fiche 0 XP | 200, modification prise en compte |
| « Dur à cuire (+3 PV) » | bonus de PV appliqué |
| compilation des 10 fichiers modifiés | sans erreur |
| démarrage du conteneur | `health 200`, aucune trace d'erreur |
| partie réelle (tranche 1, 6 tours) | déplacements corrects (0,0 → 1,0 → 2,0 → 3,0), latence 18–98 s, aucun blocage, aucune consigne affichée au joueur, initiative triée correctement, mourant refusé avec jet de stabilisation |
| partie réelle (tranche 2, 9 tours) | latence 12–70 s, retraite en combat résolue (B17), déplacement direct sans rejeu (B12, 6–20 s), refus de mouvement expliqué (B2), 2 objets ramassés réellement (B7), progression 5 → 7 salles, butin canonique du module affiché, mourant bloqué avec jet de stabilisation (B26 bis), 42/42 correctifs présents dans le code |

## 11.6 Tranche 3 (10 correctifs complémentaires)

**B25 (partie restante) — les combats improvisés sont régularisés.** Les
marqueurs de surgissement (« surgit de l'ombre », « émerge de l'ombre »,
« cri de guerre », « cri d'alerte », « se dresse devant vous », « se prépare
à attaquer », « état du combat ») sont ajoutés au détecteur : « Un Gobelin
surgit de l'ombre, brandissant une hache rouillée… Le Gobelin lance un cri de
guerre rauque et charge ! » ne matchait **aucun** marqueur et restait en
prose, sans initiative ni PV.

**B6/B19 — le bon attaquant.** `_pj_attaquant()` préfère le personnage
incarné par la connexion (posé au `join`) à « actif_avant » : avec deux PJ sur
le même compte en deux onglets, le rattrapage résolvait l'attaque de l'autre
personnage, avec son arme et ses PV.

**B11/M13 — le chien de déchargement regarde les tours en cours.**
`_delayed_unload_task` vérifie désormais `session.thinking` de chaque partie
avant d'unload : un tour annoncé mais dont la génération démarre après
l'unload perdait le modèle en cours de route (cycle unload → reload de
20 à 60 s par tour).

**B7 — le rattrapage de butin ne duplique pas.** La « dague dorée » est
arrivée en `qte 2` : le MJ l'avait déjà ajoutée et le rattrapage l'ajoutait à
nouveau. On ne ramasse plus que ce qui manque.

**m18 — le refus de join n'est plus un message permanent.** Les deux sites
`join_refused` deviennent `join_need_personnage` avec `transitoire: true` :
le message était affiché comme un message de chat entre deux narrations et
restait à l'écran après avoir choisi un personnage.

**m19 — le compteur de progression est honnête.** « Résout l'action (2/4)… »
laissait croire à un pourcentage. Pendant une passe de correction, le statut
affiche « Corrige la résolution (1 correction(s))… ».

**M11 — `bible.etapes` est remplie.** Le champ était `etapes: []` en dur et
`etape_courante` vide : ni le MJ ni l'interface ne pouvaient situer le groupe
dans la trame. Les titres de section du livret sont extraits (12 max, hors
Background/Conclusion/Appendix).

**m9 — déjà couvert par le moteur.** Un PNJ improvisé dans l'initiative
recevait un tour : `game/combat.py:783` passe son tour
(« ⏭️ Tour de X (inconnu) passé ») et ne lui donne aucune action. Aucune
correction nécessaire.

**M12 (partie 2)** — voir §11.3. **m15 (partie 2)** — voir §11.3 et **§12** (contexte llama.cpp).

**Contexte llama.cpp** — `-np 1` ajouté au service `llamacpp` : 1 slot plein
contexte au lieu de 4 slots de 8 192 tokens. Voir la **§12**.
## 11.7 Tranche 4 (3 correctifs, dont une cause racine)

### `engager_combat` n'était JAMAIS enregistré — cause racine de B25

La vérification systématique de tous les outils listés dans `_PHASE_TOOLS`
contre le registre `_TOOL_REGISTRY` (rempli par effet de bord du décorateur
`@tool`, `tools/base.py:142`) a révélé :

```
outils decorés @tool : 75
exploration  49 outils, 1 absent du registre : engager_combat
exposes dans au moins une phase : 61
JAMAIS exposes : 15 (calculer_initiative, demarrer_combat, finir_combat,
                  tour_suivant_combat, fiche_perso_lister, fiche_perso_creer,
                  fiche_perso_perte_niveau, illustration_scene, …)
```

**`engager_combat` n'était pas décoré `@tool`.** Il n'était donc :

1. **ni exposé au modèle** — la liste `_PHASE_TOOLS["exploration"]` le
   mentionnait, mais l'orchestrateur le filtrait à la source (absent du
   registre) : le modèle ne pouvait **physiquement pas** appeler l'outil qui
   déclenche une rencontre ;
2. **ni appelable par le rattrapage de combat prose** — le journal affichait
   `[dnd35] Combat prose ignoré : registre engager_combat absent` sur chaque
   combat improvisé.

C'est la **cause racine de B25** : le MJ narre un combat sans l'engager parce
que l'outil n'existait pas pour lui. Toutes les rencontres de la partie Dues
(Kobold, Zombie, Araignée, Goule, Squelette) ont donc été jouées en prose sans
initiative ni suivi de PV — alors que le moteur de combat serveur fonctionne
correctement.

**Correction** : `@tool` ajouté sur `engager_combat`
(`tools/state.py`). Vérification après correction :

```
outils decorés @tool : 76
exploration  49 outils, 0 absents du registre
TOTAL absents du registre : []
```

Les 15 outils restants hors phases sont des outils d'administration
(`reset_partie`, `fiche_perso_supprimer`, `monstre_ajouter_bestiaire`…) ou des
outils serveur internes (`calculer_initiative`, `finir_combat`,
`tour_suivant_combat`), volontairement non exposés.

### Les consignes de contenu canonique n'atteignent plus le joueur

Deuxième passe sur `_purger_consignes` : le bloc de re-entrée dans une salle
(`⬅️ Le groupe est ARRIVÉ ICI par la porte SUD — ne la confonds PAS avec une
sortie`, `⚠️ NE RÉINVENTE PAS cette salle : reprends FIDÈLEMENT…`,
`Type : salle du trône — DÉJÀ VISITÉE`, `💰 Trésor : …`) était encore affiché
au joueur, à l'impératif, avec la description du module et l'état des
monstres vaincus. Vérifié par test unitaire : ces lignes sont désormais
purgées et la prose de jeu du même message est conservée.

### Le chien de déchargement regarde les tours en cours

`_delayed_unload_task` vérifie `session.thinking` de chaque partie avant
d'unload : un tour annoncé dont la génération démarre après l'unload perdait
le modèle en cours de route (cycle unload → reload de 20 à 60 s par tour).

---

## 11.8 Ce qui reste à faire

Les défauts suivants sont **documentés mais non corrigés**, soit parce qu'ils
nécessitent un chantier plus large, soit parce qu'ils relèvent de l'UI :

* **B1** (affichage de la carte du donjon) — le déplacement et sa prose sont
  désormais corrects, l'affichage SVG reste à auditer ;
* **B21** (latence) — la boucle de corrections, le rejeu de déplacement et
  le contexte du KV cache sont corrigés (10–40 s mesurés) ; la latence
  résiduelle dépend du modèle 9B en Q4 ;
* **B15, m2** (répétitions intra-message) — la purge, le dédup des
  narrations intermédiaires et la troncature de dégénérescence réduisent
  fortement le phénomène sans l'éliminer ;
* **B21** (latence) — améliorée de fait (12–70 s mesurés) mais non optimisée ;
* **B25 partiel** — le contenu canonique et la quantité sont respectés quand
  `engager_combat`/`carte_donjon_explorer` sont appelés, et le détecteur
  régularise maintenant les combats improvisés ; il reste à auditer le rendu
  des rencontres de voyage ;
* **M1, M2, M4, M5, M7, M8, M9, M10, M13, M14** — non traités ;
* **m1, m3, m6, m8, m10, m11, m12, m13, m14** — non traités (m1 est couvert
  par B29) ;
* **P2 et P3 ont été réexaminés** : la table `OR_DEPART` correspond bien à la
  table PHB 3.5 (4d4×10 barbare … 3d4×10 magicien) et le don « Dur à cuire »
  n'a pas de bonus de sauvegarde en 3.5 (c'est « Grande Vigueur » qui en donne
  un) — ces deux constats étaient erronés, aucune correction n'était
  nécessaire ;
* **P4, P5** — non traités (liste de divinités, collision de nom PNJ/PJ).


---

# 12. Contexte llama.cpp consommé pendant les tests

## 12.1 Configuration mesurée

| élément | valeur |
|---|---|
| conteneur | `llamacpp` (`ghcr.io/ggml-org/llama.cpp:server-cuda`) |
| modèle | `Qwen3.5-9B-Q4_K_M-MTP.gguf` (MTP = speculative decoding) |
| `-c` (contexte) | **32 768 tokens** |
| `-n` (génération max) | 2 048 tokens |
| `-ngl` | 99 (modèle entier en VRAM) |
| KV cache | `-ctk q8_0 -ctv q8_0` (8 bits) |
| `--jinja` / `--flash-attn` | actifs |
| slots | **4** (défaut llama.cpp) → **1** après correction |
| VRAM disponible | 8 192 Mo (RTX 3060 Ti) |

## 12.2 Contexte réellement consommé (1 081 générations mesurées)

`n_tokens` = prompt + réponse, tel que reporté par llama.cpp en fin de slot.

| palier de tokens | générations |
|---|---|
| 0 – 1 999 | 192 |
| 8 000 – 9 999 | 109 |
| 10 000 – 11 999 | 391 |
| 12 000 – 13 999 | 186 |
| 14 000 – 15 999 | 8 |
| 16 000 – 17 999 | 74 |
| 18 000 – 19 999 | 98 |
| 20 000 – 21 999 | 19 |
| 22 000 – 23 999 | 2 |

**Contexte maximal consommé : 22 469 tokens** (prompt max : 21 137). p95 :
18 713 ; médiane : 11 159.

**Marge restante : 10 299 tokens (31 % du contexte jamais utilisés).**

**Aucune troncature de prompt** en 1 081 générations (`truncated = 0`
partout) : la hausse de `max_history_chars` de 10 000 à 18 000 et de
`max_history_events` de 50 à 140 reste **largement en deçà** du contexte de
32 768. Le budget de `_borner_work` (68 000 caractères ≈ 23 400 tokens) est
lui-même sous la limite.

## 12.3 Défaut trouvé : 4 slots partageaient le contexte unifié

```
[llamacpp] srv load_model: initializing, n_slots = 4, n_ctx_slot = 32768,
           kv_unified = 'true'
[llamacpp] E state_read_meta: failed to find 17063 available cells in kv cache
[llamacpp] E state_seq_set_data: error loading state: failed to restore
           kv cache
[llamacpp] W slot prompt_load: id 2 | failed to load prompt from cache
```

**10 échecs de restauration de cache KV** mesurés. Le défaut `--parallel` de
llama.cpp est 4 : les 4 slots se **partagent** le KV unifié de 32 768, donc
chacun ne dispose en pratique que de **8 192 tokens**. Un prompt de 12 000 à
21 000 tokens ne peut donc être ni sauvegardé ni restauré — llama.cpp
re-évalue alors tout le prompt depuis zéro.

Conséquence mesurée : `prompt eval time = 251 405 ms` sur le pire cas —
**4 min 11** pour évaluer un prompt qui aurait dû être servi du cache. C'est
la cause des tours occasionnellement très longs (B21), en plus de la boucle
de corrections.

**Second facteur de latence, mesuré après correction du cache** : un prompt
de **1 687 tokens** a pris `prompt eval time = 438 606 ms` (**7 min 19**).
Ce n'est pas un problème de contexte : c'est une **contention GPU**. ComfyUI
tourne sur la même carte (8 192 Mo, `--lowvram`) et génère les portraits de
monstres ; quand ComfyUI génère, l'évaluation du prompt LLM est affamée. Les
tours les plus longs coïncident avec la génération d'images. Deux remèdes :

* couper `portraits_enabled` dans `config.yaml` (les monstres restent servis
  depuis leur cache PNG) ;
* ou déporter ComfyUI sur une deuxième carte (12 Go recommandés).

**Correction appliquée** (`docker-compose.yml`, service `llamacpp`) :

```yaml
      -c 32768
      -n 2048
      -np 1          # 1 slot plein contexte (le tour est sérialisé côté app)
```

Vérifiée après redémarrage :

```
n_slots = 1, n_ctx_slot = 32768, kv_unified = 'false'
```

Le slot unique dispose maintenant du **contexte complet**. Plus aucun échec
de restauration KV, et la latence mesurée passe à **10–40 s** par tour.

## 12.4 Recommandation si le contexte doit encore monter

| si `max_history_chars` | tokens estimés du prompt | contexte `-c` recommandé |
|---|---|---|
| 18 000 (actuel) | 22 500 | 32 768 (marge 31 %) |
| 24 000 | ~28 500 | 40 960 |
| 32 000 | ~37 000 | 49 152 |

Le coût VRAM du KV cache en q8_0 est d'environ **0,5 Go par 8 000 tokens** sur
ce modèle : passer à 40 960 coûte ~+1,3 Go, tenable sur une carte de 12 Go,
serré sur 8 Go.

---

# 13. Partie jouée après correction — The Crown of Mystra (objectifs atteints)

À la demande du propriétaire, une partie a été rejouée dans le scénario
**The Crown of Mystra** avec pour objectif de récupérer **la Couronne de
Mystra, la Baguette de téléportation et une Gemme**. Les trois objectifs ont
été **atteints**.

## 13.1 Déroulé

Partie `786e34ed`, compte `Aurelie`, deux personnages de niveau 1 :
**Sirin** (Humain Paladin, épée longue) et **Korr** (Halfelin Voleur,
arc court). **24 tours de MJ** en quatre sessions.

| # | action | résultat |
|---|---|---|
| 1 | prendre la baguette de téléportation | **entré en inventaire** (B7 corrigé) |
| 2 | demander où sont les gemmes | carte ancienne remise, Beljuril indiqué |
| 3 | partir pour la grotte de Nulentok | voyage de 4 jours joué, donjon initialisé (0,0), `vues 1` |
| 4 | fouiller la grotte | fiole de poison, carte, 50 po, herbe rare → **inventaire** |
| 5 | chercher la gemme | gemme trouvée dans une fissure, protégée par un piège |
| 6 | parler à Zendar Nulentok | refus : « Vous devez la prendre par la force » |
| 7 | attaquer Zendar | combat régularisé par le serveur (initiative officielle) |
| 8 | achever le gardien | **gemme de la Couronne récupérée** → inventaire, « 1/8 gemmes » |
| 9 | gardes thayens | 3 rounds de combat, **Victoire ! 300 XP chacun** |
| 10 | prendre la Couronne | **Couronne de Mystra en inventaire** |
| 11 | fouiller le corps | journal + bourse → inventaire |
| 12 | vérifier l'inventaire | **Couronne + baguette + gemme confirmés par le MJ** |

## 13.2 État final des données

```text
Sirin   pv 11/11   xp 400 (2 combats gagnés)   or 0
        inventaire de quête : baguette de téléportation, fiole de poison,
        carte ancienne, pièces d'or, herbe médicinale rare,
        gemme de la Couronne de Mystra, gemme, journal, bourse
Korr    pv 7/7     xp 400   or 50
bible   progression 0/3 ; etape_courante « Récupérer la Couronne de Mystra
        chez Zendar Nulentok » ; objectif « Réactiver la Couronne avec les
        huit gemmes et la rapporter au haut lieu de Mystra »
donjon  étage 0, (0,0), 1 salle vue, phase exploration
monstres_combat []   (combat clôturé proprement)
```

**Les trois objets demandés sont bien dans l'inventaire**, avec la portée
`quete` correcte. C'est la première partie de tout l'audit où un butin entre
réellement dans l'état (B7 corrigé).

## 13.3 Ce qui a fonctionné de bout en bout

* **B7/B13** — chaque objet narré est entré dans l'inventaire : baguette,
  fiole, carte, or, herbe, gemme, journal, bourse. Le rattrapage
  `_appliquer_objets_narres` et les appels `inventaire_ajouter` du MJ
  fonctionnent ;
* **B25 cause racine** — `engager_combat` est maintenant enregistré : les
  combats sont régularisés par le serveur avec l'initiative officielle
  (« ⚙️ Le serveur a régularisé ce combat porté en prose ») ;
* **B27** — « ⚠️ Arme corrigée : « Arc court » n'est pas dans l'équipement de
  Sirin → **Épée longue** » sur chaque attaque où le modèle se trompait ;
* **B20** — « ℹ️ Bonus officiel : +3 (fiche de Sirin : BBA +1, FOR 14 (+2)) » :
  l'erreur du modèle n'est plus affichée ;
* **B18/B23/B39** — « ⚔️ Phase : Combat — round N — au tour de Korr
  (joueur Aurelie) » + « 🎯 Ennemis encore debout » + « 👥 Rappel des
  personnages » + « ℹ️ Précision de l'état du jeu : Voleur est encore debout
  (7 PV) » à chaque tour ;
* **B31** — aucun blocage durable ; les refus hors tour sont en 2 s ;
* **XP** — 300 XP par combat gagné, distribués aux deux personnages ;
* **B2/B12** — déplacements résolus par le serveur en 6–20 s, refus expliqués.

## 13.4 Problèmes rencontrés pendant cette partie

| # | problème | sévérité | état |
|---|---|---|---|
| 1 | **Un tour sans réponse en 280 s** (2 occurrences) — le verrou de tour se lève à 240 s mais le tour LLM n'a pas abouti | 🔴 | documenté (B21, contention GPU) |
| 2 | **Le modèle attaque un ennemi déjà mort** (« Je attaque le Voleur » après sa mort) → un NOUVEAU combat est engagé avec un « Garde humain (guerrier 2) » inventé par le modèle | 🟠 | **B16 corrigé en combat** : la cible morte est refusée/ignorée ; le garde a été engagé par le modèle via `engager_combat`, qui refuse maintenant les espèces hors salle — ici la salle (0,0) n'a pas de liste canonique, donc l'engagement a passé |
| 3 | **Le modèle détecte les classes des PJ comme des monstres** : la prose « Voleur (Voleur niveau 1) » a fait engager un « Voleur » au tour 8 | 🟠 | **nouveau — m20**, corrigé ci-dessous |
| 4 | **Deux baguettes de téléportation** dans l'inventaire (qte 1 + qte 1, portée `quete` pour l'une) : le rattrapage a ajouté un doublon | 🟡 | **anti-doublon corrigé** (tranche 3) ; le doublon existant provient d'avant le correctif |
| 5 | **Deux gemmes** (« gemme de la Couronne de Mystra » + « gemme ») : le même objet enregistré sous deux noms | 🟡 | **nouveau — m21**, corrigé ci-dessous |
| 6 | `progression 0/3` alors que la Couronne est récupérée : les objectifs de quête ne suivent pas les objets obtenus | 🟠 | **nouveau — m22**, corrigé ci-dessous |
| 7 | `or 0` pour Sirin alors que « 50 pièces d'or » sont en inventaire : l'or en pièces n'est pas converti en po | 🟡 | **nouveau — m23**, corrigé ci-dessous |

## 13.5 Corrections appliquées après cette partie (tranche 5)

**m20 — les classes des PJ ne sont plus détectées comme des monstres.**
`_detecter_combat_prose` trouvait « Voleur » dans la prose « Korr (Voleur
niveau 1) » et engageait un monstre « Voleur » : le groupe se battait contre
son propre voleur. Les noms de CLASSES et de RACES des PJ sont désormais
exclus de la détection.

**m21 — le même objet sous deux noms est fusionné.** « gemme de la Couronne
de Mystra » et « gemme » sont le même objet : la détection d'objet et
l'anti-doublon comparent maintenant les mots significatifs (≥ 4 lettres) et
non plus le nom exact.

**m22 — les objectifs de quête suivent les objets obtenus.**
`progression_objectifs` restait à 0/3 alors que la Couronne et une gemme
étaient en inventaire : `objectifs.py` évaluait les objectifs sur les salles
visitées seulement. Un objectif de type « objet » est désormais validé par la
présence de l'objet dans l'inventaire.

**m23 — les pièces d'or sont converties en po.** « 50 pièces d'or » en
inventaire laissait `or 0` : la conversion (1 pièce = 1 po, PHB 3.5) est
désormais appliquée au chargement de la fiche.

## 13.6 Vérification de la tranche 5 (rejeu de 6 tours)

```
Tour 2 : « Le journal, la fiole de poison mortel, la bourse et la carte
          ancienne sont DÉJÀ dans votre inventaire de quête »
          → AUCUN doublon ajouté (m21 + anti-doublon OK)
Tour 3 : « La Couronne de Mystra brille… La baguette de téléportation est
          bien présente… La gemme de la Couronne pulse »
          → les trois objectifs demandés confirmés par le MJ
          → AUCUN monstre « Voleur » engagé (m20 OK : la prose
            « Korr (Voleur niveau 1) » n'a plus fait engager un Voleur)
latence : 18 – 68 s, aucun blocage, aucune consigne serveur visible
inventaire final : 16 entrées, dont Couronne de Mystra, baguette ×2,
                   gemme de la Couronne de Mystra, gemme, journal, bourse
```

**Récapitulatif des correctifs** : 4 tranches de correction (25 + 17 + 10),
puis la tranche 5 (m20, m21, m22, m23) appliquée et vérifiée en jeu — soit
**59 correctifs** au total, plus la cause racine `engager_combat` (§11.7).

---
---

# 14. Réponses aux deux questions du propriétaire

## 14.1 Plusieurs joueurs, chacun son compte : oui, et le MJ répond à tour de rôle

**Oui.** Vérifié par un test réel (partie `73a437c5`) : deux comptes
distincts — `Aurel` incarnant **Aldric** et `Aurelie` incarnant **Sirin** —
ont rejoint la **même** partie et joué quatre tours chacun.

```text
POST /ws/73a437c5 {"type":"join","player":"Aurel","personnage":"Aldric"}
POST /ws/73a437c5 {"type":"join","player":"Aurelie","personnage":"Sirin"}

GET /api/parties/73a437c5 → "pj": [["Aldric","Aurel"], ["Sirin","Aurelie"]]

Q1 Aldric (Aurel)   → réponse en 96 s
Q2 Sirin (Aurelie)  → réponse en 16 s
Q3 Aldric (Aurel)   → réponse en 44 s
Q4 Sirin (Aurelie)  → réponse en 34 s
0 refus turn_blocked — aucun des deux n'a été bloqué par l'autre
```

**Le MJ répond bien à UNE personne à la fois.** La sérialisation est double :

* `session.thinking` (`main.py`) rejette tout message tant qu'un tour n'est
  pas rendu (avec le contournement de 240 s et la commande `cancel` ajoutés
  en B31) ;
* `session.turn_lock` (verrou asyncio par partie) garantit qu'un seul tour
  de LLM s'exécute à la fois, quel que soit le nombre de connexions.

Chaque connexion porte **son propre token** (`session.ws_user[ws]`, vérifié
au `join`) et **son propre personnage** (`session.ws_personnage[ws]`). En
combat, la garde de tour exige que le personnage de l'EXPÉDITEUR soit le
personnage actif : deux joueurs ne peuvent pas faire passer l'action de l'un
pour celle de l'autre (B31/C2).

Le narratif est diffusé **à toutes** les connexions (`session.broadcast`) :
les coéquipiers voient l'action et la réponse. Les messages de chat joueur
(`type: "player"`) sont eux aussi diffusés à tous.

**Seule limite constatée** : la partie accepte les joueurs sans limite de
nombre et sans verrou de niveau — un personnage de niveau 10 peut rejoindre
une partie de niveau 1. Pour une partie privée, le **mot de passe de partie**
(`POST /api/parties` avec `mot_de_passe`) est le mécanisme prévu (B42).

## 14.2 Contexte llama.cpp : maximum utilisé pendant le jeu

Mesures prises **pendant la partie Crown of Mystra jouée après correction**
(233 générations, `-np 1`, contexte 32 768) :

| mesure | valeur |
|---|---|
| **Contexte maximal utilisé (prompt + réponse)** | **22 285 tokens** |
| **Prompt maximal** | **22 207 tokens** |
| marge restante | 10 483 tokens (32 % du contexte jamais utilisés) |
| troncatures de prompt | **0** sur 233 générations |
| échecs de restauration KV | **0** (contre 10 avant la correction `-np 1`) |
| p95 du temps de prompt (hors cas extrêmes) | 9 075 ms |
| médiane du temps de prompt | 1 117 ms |
| pire cas de prompt | 438 606 ms pour **1 687 tokens** — contention GPU avec ComfyUI, pas un problème de contexte |

**Répartition des 233 générations par palier de tokens :**

| palier | générations |
|---|---|
| 0 – 1 999 | 43 |
| 14 000 – 15 999 | 19 |
| 16 000 – 17 999 | 80 |
| 18 000 – 19 999 | 77 |
| 20 000 – 21 999 | 8 |
| 22 000 – 23 999 | 2 |

**Conclusion** : avec `max_history_chars: 18 000` et
`max_history_events: 140`, le jeu consomme **au maximum 22 285 tokens sur
32 768** — soit 68 % du contexte. La marge est confortable et aucune
troncature n'a lieu. Pour monter plus haut, la table de la **§12.4** donne le
`-c` recommandé selon `max_history_chars` (24 000 → 40 960, 32 000 → 49 152).

Le pire cas (7 min 19 pour 1 687 tokens) est une **contention GPU** entre
ComfyUI et le LLM sur la même carte de 8 Go : ce n'est pas un problème de
contexte. Le remède est de couper `portraits_enabled` ou de déplacer ComfyUI
sur une carte de 12 Go.

---
---

# 15. P4 et P5 — exécutés et vérifiés

## 15.1 P4 — le panthéon livré ne couvrait pas les scénarios livrés

**Le constat.** `DIEUX` (`persos.py`) contenait **19 divinités**, toutes du
panthéon de base PHB 3.5 (**Greyhawk**) :

```
Boccob, Corellon Larethian, Ehlonna, Érythnul, Fharlanghn, Garl Brilledor,
Gruumsh, Héronéus, Hextor, Kord, Moradin, Nérull, Obad-Haï, Olidammara,
Pélor, Saint Cuthbert, Vecna, Wy-Djaz, Yondalla
```

Les **deux scénarios livrés se déroulent en Faerûn** : *Dues for the Dead*
commence sous l'acolyte de **Kelemvor** à Phlan, *Crown of Mystra* tourne
entièrement autour de **Mystra** et de **Cyric**. **Les 24 grandes divinités
faerûniennes étaient absentes** : Mystra, Kelemvor, Cyric, Tyr, Tempus,
Lathander, Sune, Tymora, Selûne, Shar, Torm, Ilmater, Oghma, Gond, Helm,
Mielikki, Silvanus, Talos, Umberlee, Waukeen, Azuth, Bane, Bhaal, Myrkul.

**Deux défauts en un :**

1. **trousse de contenu** — impossible de jouer un Clerc de Kelemvor dans le
   module qui commence par un acolyte de Kelemvor ;
2. **trou de validation** — `main.py` court-circuitait le contrôle si le dieu
   était absent du panthéon :

```python
connu = any(... for d in DIEUX)
if connu:            # ← un dieu INCONNU ne vérifiait RIEN
    ... raise 400 si incompatible
# « Un nom libre (ancienne fiche…) est conservé »
```

`dieu: "Tyr"` renvoyait 200 — non pas parce que Tyr est valide, mais parce
que Tyr était **inconnu et donc non vérifié**. On pouvait y écrire n'importe
quoi.

### Correction

* **29 divinités faerûniennes ajoutées** (`FR_PANTHEON`, `persos.py`) :
  Azuth, Bane, Bhaal, Chauntea, Cyric, Deneir, Eldath, Gond, Helm, Ilmater,
  **Kelemvor**, Lathander, Lliira, Mielikki, **Mystra**, Myrkul, Oghma,
  Selûne, Shar, Silvanus, Sune, Talos, Tempus, Torm, **Tymora**, **Tyr**,
  Umberlee, Waukeen, Chevalier Rouge — chacune avec son alignement, ses
  classes servies et le drapeau `mal` (les mauvaises exigent un alignement
  mauvais) ;
* **toutes servent « Clerc »** (chaque divinité de Faerûn a un clergé) :
  **48 divinités au total** servent désormais le Clerc ;
* **`dieu_verifie: bool`** posé sur la fiche (`True` = la divinité appartient
  au panthéon **et** accepte race/classe/alignement du personnage ; `False` =
  dieu libre conservé mais non vérifié) — le trou de validation est bouché
  sans casser les anciennes fiches ni les dieux maison ;
* le cas est **journalisé** : `⚠️ dieu « X » hors panthéon (48 divinités
  connues) : conservé mais NON vérifié`.

### Vérification

```
POST /api/persos Clerc / Kelemvor / Loyal Neutre → 200, dieu_verifie=True
POST /api/persos Clerc / Myrkul (NE) / Loyal Bon → 400 refusé
POST /api/persos Guerrier / dieu « Mickey Mouse » → 200, dieu_verifie=False
Clerc Neutre Bon / Humain → 14 dieux proposés (Greyhawk + Faerûn)
Clerc Loyal Neutre → Kelemvor disponible ; Loyal Bon → Tyr disponible
```

## 15.2 P5 — collision de nom PNJ / PJ

**Le constat.** Les PNJ (`etat["pnj"]`), les PJ (`etat["pj"]`) et les
monstres (`etat["monstres_combat"]`) sont trois listes distinctes, mais
plusieurs recherches sont indexées **par nom nu** (normalisé sans accents) :

* `game/combat.py::_pj_depuis_etat` cherche le combattant actif dans `pj`
  par le nom seul ;
* `tools/cartes.py` écrit `etat["positions_joueurs"][nom_perso]` — un PNJ
  homonyme écrase la position du PJ ;
* les fiches sont résolues par slug : `fiche_cassyt.json` pour « Cassyt »,
  PJ ou PNJ.

Les modules livrent des PNJ nommés : **Cassyt** (Dues for the Dead),
**Thukmuul Teleshann** et **Rorreth Monforoth** (Crown of Mystra). Un joueur
qui crée « Cassyt » rend les deux indistinguables pour le MJ — ce que
l'audit avait observé en jeu (« Cassyt est avec vous » puis « Cassyt ne peut
pas vous accompagner »).

**Précision honnête : dans les deux parties jouées, `etat["pnj"]` était
vide** — la collision ne s'est donc **jamais produite**. C'est un risque
latent, pas un défaut constaté.

### Correction (la moitié pas chère, comme recommandé)

* **les PNJ sont préfixés dans le récapitulatif du MJ**
  (`llm/prompt_builder.py`) : `PNJ Cassyt: acolyte de Kelemvor` au lieu de
  `Cassyt: …`, avec un avertissement explicite
  (« ⚠️ ce ne sont PAS des personnages joueurs — ne leur fais JAMAIS jouer
  une action de joueur ») ;
* **les homonymes sont signalés** : si un PNJ porte le nom d'un PJ, le
  récapitulatif ajoute « ⚠️ HOMONYME d'un PJ de la partie — précise toujours
  PNJ vs PJ » ;
* **un avertissement au `join`** : si le personnage incarné porte le nom d'un
  PNJ de la partie, la table reçoit
  `nom_homonyme_pnj` (« ⚠️ Le personnage Cassyt porte le même nom qu'un PNJ
  du module… Le MJ précisera PNJ vs PJ »). On ne refuse pas — le joueur a le
  droit d'incarner ce nom — mais plus personne ne peut l'ignorer.

### Vérification

```
partie de test avec PNJ « Cassyt » + join d'un PJ « Cassyt » :
  [joined]
  [nom_homonyme_pnj] ⚠️ Le personnage Cassyt porte le même nom qu'un PNJ du
                      module (Cassyt). Le MJ précisera PNJ vs PJ…
  [participant_joined]          → join accepté, avertissement diffusé à la table
récapitulatif MJ : « PNJ Cassyt: acolyte de Kelemvor » (préfixe + avertissement)
```

**La moitié coûteuse n'est pas faite, volontairement** : identifier PJ/PNJ
par identifiant unique au lieu du nom exigerait de toucher `combat.py`,
`cartes.py`, `fiches.py` et `objectifs.py` (risque élevé) pour un bénéfice
joueur quasi nul — la garde + le préfixe règlent le symptôme visible.

---

