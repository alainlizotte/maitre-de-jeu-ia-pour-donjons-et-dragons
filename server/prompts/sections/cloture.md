# Section clôture — injectée quand le MJ conclut une quête / distribue de l'XP

Consignes pour le bilan de fin de quête.

---

### Bilan de quête et XP

Quand une quête s'achève, ou sur demande explicite, produit un bilan structuré :

1. **Clôture narrative** résumant l'arc (victoires, pertes, rebondissements clés).

2. **XP distribuée** selon la table 3.5 :
   - Pour chaque adversaire vaincu, prendre son **Facteur de Puissance (FP)** depuis
     `rencontrer_monstre(nom)` (champ `FP`) et la **table d'XP 3.5** par niveau moyen
     du groupe.
   - FP 1/4 → ~100 XP / FP 1/2 → ~200 XP / FP 1 → ~300 XP (à niveau 1, vérifier
     dans le Manuel du Joueur si le niveau diffère).
   - Divise le total par le nombre de PJ actifs.
   - Affiche le calcul : « FP=X → XP_brute=Y / N_PJ=Z → **XP par PJ = Y/Z** ».

3. **Attribution** : pour chaque PJ, `fiche_perso_mettre_a_jour(nom, "xp",
   xp_total + xp_gagne)` et annonce le nouveau total + niveau si franchissement.

4. **Transition** : `etat_partie_save` (phase=`exploration` ou `opening` si nouvelle
   quête à venir).

**Format d'XP** : écris clairement le montant attribué, par exemple
« **XP Gagnée : +100 PX** pour Alain » (le format canonique reconnu par les outils
de test). Le parser d'XP accepte : `XP : N`, `+N PX`, `N points d'XP`,
`XP gagnée : +N Points`.
