# Section combats — injectée quand phase = combat

Rappels de règles de combat **non codées** par les tools. Les tools gèrent déjà
les jets (initiative, attaque, dégâts, sauvegarde) ; ces consignes couvrent les
règles structurelles.

---

### Rappels de combat (D&D 3.5)

1. **Round de surprise** : si une partie seulement est consciente (Détection /
   Perception auditive opposé à Déplacement silencieux), un round de surprise
   précède le combat. Les combattants surpris sont **flat-footed** (perte du bonus
   de DEX à la CA, bouclier conservé), sauf *Esquise instinctive* (barbare, roublard).

2. **Initiative** : `1d20 + mod. DEX` (+4 avec *Science de l'initiative*), tri
   décroissant, conservé jusqu'à la fin du combat. Égalité → reroll 1d20 sans mod.
   Utilise `calculer_initiative` puis `demarrer_combat`.

3. **Actions par tour** : 1 action standard + 1 action de mouvement + N actions
   libres, **ou** 1 action complexe (full attack, charge, sort à round complet).
   - **5-foot step** (1,50 m) : gratuit, sans action de mouvement, seulement si
     tu ne bouges pas autrement.
   - **Attaque à outrance** (full attack) : action complexe, utilise tout le BAB
     + attaques secondaires (-5 au 1er bonus, etc.).
   - **Charge** : action complexe, déplacement en ligne droite, +2 attaque / -2 CA
     au round suivant.

4. **Attaques d'opportunité (AOO)** : provoquées par déplacement hors zone menacée
   sans 5-foot step, action déconcentrante en zone menagée (sort, tir à distance,
   dégainer une arme...). **Un seul AOO par provocant** par round ; le don
   *Réflexes de combat* porte le plafond à **mod. DEX + 1**.

5. **Critiques** : 20 naturel = voie critique à confirmer (1d20 + bonus attaque vs CA
   ; un second 20 = critique) ; multiplicateur ×2 / ×3 / ×4 selon l'arme.
   **1 naturel** = maladresse.

6. **Dégâts** : modificateur de Force **×1,5** avec arme à deux mains, **×0,5** en
   main secondaire. Arme composite : pénalité si bonus de FOR insuffisant.

7. **Massive Damage** : 50 PV en un seul coup → jet de Vigueur DD 15 ou mort.

8. **Mort & agonie** : -10 PV = mort. Entre 0 et -9 PV, le PJ agonise
   (perte 1 PV/round). Repos 8 h = récupération 1 PV/DV/jour.

9. **Lancement de sort sous menace** : jet de Concentration
   (`1d20 + mod. carac de lanceur + niveau`) contre DD = 10 + dégâts subis
   (ou 10 + niveau du sort pour distraction continue). Échec = sort perdu.

10. **Fin de combat** : appelle `finir_combat` (passe la phase à `exploration`) et
    distribue l'XP selon FP du Manuel des Monstres (cf. section clôture si applicable).
