# Prompts et catalogue de scénarios — univers **Laelith** (fallback embarqué)

Ce fichier sert de **catalogue de secours** si l'outil `lister_scenarios_laelith`
ne parvient pas à atteindre le site `donjondudragon.fr/univers/laelith/scénarios.html`
(timeout réseau, maintenance du site, etc.). Le Maître du Jeu (MJ) peut alors
présenter ces scénarios comme un **cataloge intégré** et compléter via le site
quand celui redevient accessible.

> Laelith est un univers de campagne Donjons & Dragons populaire dans la
> communauté francophone, avec une grande cité éponyme et sa région. Les
> scénarios ci-dessous sont des **hooks génériques** prêts à adapter au niveau
> des personnages et au cadre retenu ; le MJ peut les personnaliser.

---

## Liste de scénarios proposés (sélection)

### 1. La Voix sous les Pavés
- **Niveau conseillé** : 1-3
- **Thématique** : enquête urbaine, intrigue politique
- **Pitch** : À Laelith, des disparitions inquiètent le peuple du bas-quartier.
  Une voix murmure dans les égouts, appelant les âmes égarées. Les aventuriers
  sont mandatés par la milice locale. Qui tire les ficelles ?
- **Hook d'ouverture** : une vieille femme supplie le groupe de retrouver son
  fils disparu la veille. Une médaille au symbole étrange est le seul indice.

### 2. Les Échos de la Tour Brisée
- **Niveau conseillé** : 3-5
- **Thématique** : donjon, magie ancienne
- **Pitch** : Une arcane déchu a laissé une tour en ruines dans la Marche de
  l'Ouest. Les paysans parlent de lumières nocturnes et de bestioles
  mange-bétail. La guilde des magiciens offre une bonne somme pour
  l'exploration.
- **Hook d'ouverture** : un pacte magique à signer devant témoins, contre
  un versement immédiat.

### 3. Le Sel Noir de la Côte
- **Niveau conseillé** : 2-4
- **Thématique** : maritime, contrebande, combat
- **Pitch** : Sur la côte sud de Laelith, des brigands attaquent les convois
  de sel — ressource vitale pour les salaisons. Une escorte est offerte.
  Mais derrière les bandits se cache une organisation plus large.
- **Hook d'ouverture** : un capitaine de caravan offre le double du tarif
  habituel en échange d'une escorte discrète.

### 4. La Complainte des Bois-Ombres
- **Niveau conseillé** : 4-6
- **Thématique** : forêt maudite, druides, fey
- **Pitch** : Une forêt voisine de Laelith se meurt. Les druides implorent
  l'aide d'aventuriers : une corruption s'étend depuis un sanctuaire oublié.
  Maléfice ancien ou trahison récente ?
- **Hook d'ouverture** : un druide en larmes se présente à l'auberge du groupe
  avec une branche noircie qui fleure la mort.

### 5. Le Trône de Cendre
- **Niveau conseillé** : 6-9
- **Thématique** : politique de cour haute, trahison, longue intrigue
- **Pitch** : Le conseil de Laelith est вirement partagé après la mort
  suspectée d'un député. Les PJ (parfois appelés comme experts selon leur
  réputation) doivent découvrir l'assassin avant qu'il ne frappe à nouveau,
  sans tomber dans le piège des factions.
- **Hook d'ouverture** : une convocation scellée au cachet du magistrat en
  chef, sommant les PJ au palais avant l'aube.

### 6. Le Tombeau de Selvar le Sombre
- **Niveau conseillé** : 5-7
- **Thématique** : donjon funéraire, mort-vivant, énigmes
- **Pitch** : Le tombeau d'un ancien sorcier s'ouvre pour la première fois
  en mille ans. Des pillards y ont尝试é leur chance — aucun n'est revenu.
  Quel mal dort dessous ?
- **Hook d'ouverture** : une carte trouvée dans les affaires d'un
  contrebandier mort, indiquant l'entrée exacte.

### 7. La Caravane des Égarés
- **Niveau conseillé** : 1-2 (idéal pour débutants)
- **Thématique** : escorte, survie, première rencontre
- **Pitch** : Une caravane de réfugiés fuit une zone devastée et demande
  escorte jusqu'à Laelith. Le chemin regorge de dangers mineurs mais
  répétés, parfait pour se faire les dents.
- **Hook d'ouverture** : un groupe de familles apparements en pleurs attends
  des protecteurs à la porte nord.

---

## Utilisation par le MJ

- Au démarrage de partie (Étape 3 du GuideOuverture), si le catalogue
  distant est inaccessible, le MJ **présente 3 à 5 scénarios** ci-dessus
  en selon le niveau et le thème des personnages créés.
- Le MJ **adapte** le niveau (en ajustant les PV/FP des adversaires et les
  récompenses) et le lieu (pour cadrer avec la Côte des Épées si les PJ
  commencent là-bas — Laelith reste accessible par voyage).
- Une fois le scénario choisi, le MJ formalise le brief dans l'état
  persistant (`etat_partie_save`) et démarre l'ouverture narrative.

---

## Note sur la fidélité canonique

Ces hooks sont volontairement **adaptatifs** et n'écrasent pas le canon
officiel de Laelith. Si le site redevient accessible, le tool
`lister_scenarios_laelith` récupère la liste officielle mise à jour ; le MJ en
fait alors son premier choix. En attendant, ce fichier garantit que la partie
peut toujours démarrer.
