"""Enrichit data/bestiaire.json avec ~40 monstres classiques D&D 3.5 (SRD).

Fusionne sans écraser : les entrées existantes (créées par le MJ via le tool
monstre_ajouter_bestiaire) sont préservées. Rejouable — les monstres déjà
présents ne sont pas dupliqués.

Usage : py scripts/enrichir_bestiaire.py
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BESTIAIRE = os.path.join(ROOT, "server", "data", "bestiaire.json")


def _slug(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in nf if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only.strip())
    return ascii_only[:60].strip("_").lower() or "monstre"


def M(nom, type_, taille, dv, pv, ca, vitesse, bab, init, attaques, degs,
      sauvegardes, carac, comp, dons, capacites, faiblesses, fp, alignement):
    cle = _slug(nom)
    return cle, {
        "nom": nom, "type": type_, "taille": taille, "dv": dv, "pv": pv,
        "ca": ca, "vitesse": vitesse, "bab": bab, "init": init,
        "attaques": attaques, "degs": degs, "sauvegardes": sauvegardes,
        "carac": carac, "comp": comp, "dons": dons, "capacites": capacites,
        "faiblesses": faiblesses, "fp": fp, "alignement": alignement,
        "prompt_image": (
            f"fantasy {nom.lower()} creature, D&D 3.5 manual illustration, "
            f"ink style, dramatic lighting"
        ),
        "cle": cle,
    }


NOUVEAUX = dict([
    M("Ankheg", "Créature magique", "G", "3d8+9", 22, 18, "9 m (6 cases), creusement 6 m",
      "+2", "+0", "Morsure +5 (corps à corps)", "Morsure 2d6+4 plus 1d4 acide",
      "Réfl +3, Vig +5, Vol +2", "For 17, Dex 10, Con 16, Int 1, Sag 13, Cha 10",
      "Escalade +6, Écoute +5", "—",
      "Vision dans le noir 18 m ; jet d'acide 1/jour (4d4, cône 3 m, Réfl DD 13 demi)",
      "—", "3", "Neutre"),
    M("Basilic", "Créature magique", "M", "6d10+18", 51, 16, "6 m (4 cases)",
      "+6", "+1", "Morsure +8 (corps à corps)", "Morsure 2d6+3 plus poison",
      "Réfl +6, Vig +9, Vol +5", "For 15, Dex 12, Con 16, Int 2, Sag 12, Cha 11",
      "Discrétion +8, Écoute +6, Repérage +6", "—",
      "Regard pétrifiant (portée 9 m, Vig DD 13, pétrification) ; vision dans le noir 18 m",
      "—", "5", "Neutre"),
    M("Béhir", "Créature magique", "G", "9d10+45", 94, 20, "12 m (8 cases)",
      "+9", "+6", "Morsure +14 ; constriction +16", "Morsure 2d8+8 ; constriction 2d6+8",
      "Réfl +8, Vig +11, Vol +6", "For 23, Dex 14, Con 21, Int 7, Sag 14, Cha 16",
      "Escalade +12, Discrétion +3, Écoute +9", "Attaque en souplesse, Science de la constriction",
      "Souffle foudre 1/jour (7d6, ligne 18 m, Réfl DD 21 demi) ; peut avaler (2d8+8 par round) ; vision dans le noir 18 m",
      "—", "8", "Neutre"),
    M("Gars velu", "Humanoïde (gobelinoïde)", "M", "3d8+6", 19, 16, "9 m (6 cases)",
      "+3", "+2", "Masse d'armes +4 ; javelot +3", "Masse 1d8+3 ; javelot 1d6+3",
      "Réfl +3, Vig +3, Vol +1", "For 15, Dex 14, Con 13, Int 10, Sag 9, Cha 6",
      "Escalade +2, Écoute +2, Déplacement silencieux +5, Repérage +2", "Arme de prédilection (masse d'armes)",
      "Vision dans le noir 18 m ; odorat",
      "Sensibilité à la lumière du jour (-1 attaques)", "2", "Chaotique mauvais"),
    M("Bulette", "Créature magique", "G", "9d8+45", 85, 22, "12 m (8 cases)",
      "+9", "+2", "Morsure +14 ; 2 griffes +9", "Morsure 2d8+9 ; griffe 1d8+4",
      "Réfl +7, Vig +11, Vol +6", "For 23, Dex 15, Con 20, Int 2, Sag 12, Cha 11",
      "Écoute +9, Repérage +8", "—",
      "Creusement ; bond (attaque en charge 2d8+12) ; odorat ; vision dans le noir 18 m",
      "—", "7", "Neutre"),
    M("Centaure", "Créature monstrueuse", "G", "4d8+8", 26, 15, "15 m (10 cases)",
      "+4", "+2", "Épée longue +6 ; arc long +4", "Épée longue 2d6+4 ; flèche 2d6+4",
      "Réfl +5, Vig +5, Vol +5", "For 18, Dex 14, Con 15, Int 14, Sag 13, Cha 11",
      "Écoute +5, Repérage +5, Survie +5", "—",
      "Attaque au galop (charge) ; vision nocturne",
      "—", "3", "Neutre"),
    M("Chimère", "Dragon", "G", "9d10+27", 76, 17, "9 m (6 cases), vol 15 m",
      "+9", "+2", "Morsure de lion +11 ; corne +11 ; 2 griffes +9",
      "Morsure 2d6+3 ; corne 1d8+3 ; griffe 1d6+1",
      "Réfl +6, Vig +8, Vol +6", "For 17, Dex 11, Con 17, Int 6, Sag 13, Cha 10",
      "Écoute +6, Repérage +7", "—",
      "Souffle de dragon 1/jour (cône 6 m, 6d8 feu, Réfl DD 17 demi) ; vol ; vision dans le noir 18 m",
      "—", "7", "Chaotique mauvais"),
    M("Cocatrix", "Créature magique", "P", "4d10+8", 30, 15, "6 m (4 cases), vol 18 m",
      "+4", "+2", "Morsure +6 (corps à corps)", "Morsure 1d4-1 plus pétrification progressive",
      "Réfl +6, Vig +6, Vol +4", "For 6, Dex 15, Con 14, Int 2, Sag 12, Cha 7",
      "Vol +6", "—",
      "Pétrification progressive (Vig DD 12) ; vol",
      "—", "3", "Neutre"),
    M("Djinn", "Élémentaire (air)", "G", "9d8+18", 58, 15, "9 m (6 cases), vol 27 m (parfait)",
      "+9", "+8", "2 coups +10 (corps à corps)", "Coup 1d8+5",
      "Réfl +8, Vig +7, Vol +7", "For 21, Dex 17, Con 17, Int 16, Sag 15, Cha 16",
      "Écoute +7, Perception des affinités +7", "—",
      "Changement de forme (à volonté) ; invisibilité (à volonté) ; tourbillon 1/jour ; création de nourriture, d'eau et d'objets ; télépathie ; immunité à l'acide",
      "—", "5", "Chaotique bon"),
    M("Doppelganger", "Créature monstrueuse", "M", "4d8+8", 26, 15, "9 m (6 cases)",
      "+4", "+4", "Coup +5 (corps à corps)", "Coup 1d6+2",
      "Réfl +6, Vig +2, Vol +5", "For 14, Dex 15, Con 14, Int 15, Sag 14, Cha 15",
      "Déguisement +10, Écoute +9, Psychologie +9", "—",
      "Changement de forme (à volonté) ; immunité au sommeil et aux charmes ; télépathie 30 m",
      "—", "3", "Neutre"),
    M("Drider", "Créature aberrante", "G", "8d8+24", 60, 19, "9 m (6 cases), escalade 9 m",
      "+8", "+5", "Morsure +9 ; épée courte +9/+4", "Morsure 1d6+3 plus poison (For, DD 16) ; épée 1d6+3",
      "Réfl +6, Vig +7, Vol +6", "For 16, Dex 16, Con 17, Int 13, Sag 15, Cha 14",
      "Écoute +6, Repérage +7", "—",
      "Sorts d'ensorceleur niveau 6 ; toiles d'araignée ; vision dans le noir 36 m",
      "—", "6", "Chaotique mauvais"),
    M("Élémentaire d'air (moyen)", "Élémentaire (air)", "M", "4d8+4", 22, 17, "9 m (6 cases), vol 27 m (parfait)",
      "+5", "+5", "Coup +9 (corps à corps)", "Coup 1d6+4",
      "Réfl +7, Vig +2, Vol +2", "For 12, Dex 19, Con 14, Int 4, Sag 11, Cha 11",
      "Discrétion +6", "Attaque en vol",
      "Tourbillon (contacts 1d8+4, Réfl DD 15) ; immunités élémentaires ; ne peut être noyé",
      "—", "3", "Neutre"),
    M("Élémentaire de feu (moyen)", "Élémentaire (feu)", "M", "4d8+4", 22, 17, "15 m (10 cases)",
      "+5", "+4", "Coup +8 (corps à corps)", "Coup 1d6+3 plus 1d4 feu",
      "Réfl +6, Vig +3, Vol +2", "For 10, Dex 17, Con 12, Int 4, Sag 11, Cha 11",
      "—", "—",
      "Combustion (enflamme ce qu'il touche) ; immunité au feu",
      "Vulnérabilité au froid (dégâts x1,5)", "3", "Neutre"),
    M("Élémentaire de terre (moyen)", "Élémentaire (terre)", "M", "4d8+11", 29, 17, "6 m (4 cases), creusement 6 m",
      "+5", "-1", "Coup +10 (corps à corps)", "Coup 1d8+7",
      "Réfl +2, Vig +7, Vol +2", "For 21, Dex 8, Con 17, Int 4, Sag 11, Cha 11",
      "—", "—",
      "Poussée (recul 1,5 m) ; perception tellurique 9 m ; immunité aux projectiles",
      "—", "3", "Neutre"),
    M("Éttin", "Géant", "G", "10d8+30", 75, 18, "12 m (8 cases)",
      "+10", "+0", "2 masses d'armes +11/+11", "Masse 2d6+6",
      "Réfl +3, Vig +9, Vol +3", "For 23, Dex 11, Con 19, Int 6, Sag 10, Cha 7",
      "Écoute +8, Repérage +8", "Arme de prédilection (masse d'armes)",
      "Deux têtes (vigilance, difficile à surprendre) ; vision dans le noir 27 m ; odorat",
      "—", "6", "Chaotique mauvais"),
    M("Géant des collines", "Géant", "G", "12d8+48", 102, 20, "9 m (6 cases)",
      "+9", "-1", "Gourdin +16 (corps à corps) ; rocher +8 (distance)",
      "Gourdin 2d8+10 ; rocher 2d8+7",
      "Réfl +4, Vig +11, Vol +4", "For 25, Dex 10, Con 19, Int 6, Sag 10, Cha 7",
      "Escalade +3, Écoute +6", "—",
      "Vision nocturne ; lancer de rochers (portée 36 m)",
      "—", "7", "Neutre mauvais"),
    M("Géant du givre", "Géant", "G", "14d8+70", 133, 21, "9 m (6 cases)",
      "+11", "+0", "Hache de guerre +20 (corps à corps) ; rocher +12 (distance)",
      "Hache 3d6+12 ; rocher 2d8+9",
      "Réfl +5, Vig +12, Vol +6", "For 29, Dex 10, Con 21, Int 9, Sag 14, Cha 11",
      "Escalade +5, Écoute +8", "—",
      "Immunité au froid ; vision nocturne ; lancer de rochers (portée 36 m)",
      "—", "9", "Neutre mauvais"),
    M("Gnoll", "Humanoïde (gnoll)", "M", "2d8+2", 11, 15, "9 m (6 cases)",
      "+2", "+0", "Hache de bataille +3 ; arc long +1", "Hache 1d8+2 ; flèche 1d8",
      "Réfl +0, Vig +3, Vol +0", "For 14, Dex 10, Con 11, Int 8, Sag 9, Cha 8",
      "Écoute +2, Repérage +2", "—",
      "Vision dans le noir 18 m",
      "Lâcheté : fuit si en difficulté sans chef", "1", "Chaotique mauvais"),
    M("Golem de chair", "Construct", "G", "10d10+30", 85, 17, "9 m (6 cases)",
      "+10", "-1", "2 coups +13 (corps à corps)", "Coup 2d8+7",
      "—", "For 21, Dex 9, Con —, Int —, Sag —, Cha 1",
      "—", "—",
      "Frénésie (Rag DD 19) ; immunité à la magie ; ne peut être soigné que par réparation",
      "Ralenti 2d6 rounds par feu ou froid (au lieu d'être blessé)", "7", "Neutre"),
    M("Golem d'argile", "Construct", "G", "16d10+60", 148, 24, "6 m (4 cases)",
      "+16", "-1", "Coup +20 (corps à corps)", "Coup 2d10+9",
      "—", "For 27, Dex 8, Con —, Int —, Sag —, Cha 1",
      "—", "—",
      "Plaie incurable (blessures infligées ne se soignent pas naturellement, DD 21) ; frénésie ; immunité à la magie ; accéléré par les attaques acides",
      "—", "10", "Neutre"),
    M("Griffon", "Créature magique", "G", "5d10+15", 42, 17, "9 m (6 cases), vol 24 m (médiocre)",
      "+5", "+3", "Morsure +8 ; 2 griffes +6", "Morsure 2d6+3 ; griffe 1d4+1 ; fauchage en bond 1d6+1",
      "Réfl +6, Vig +7, Vol +4", "For 17, Dex 15, Con 16, Int 5, Sag 12, Cha 8",
      "Écoute +4, Repérage +8", "—",
      "Piqué (charge en vol) ; vision dans le noir 18 m",
      "—", "4", "Neutre"),
    M("Harpie", "Créature monstrueuse", "M", "7d8+7", 38, 14, "9 m (6 cases), vol 24 m (moyenne)",
      "+7", "+1", "2 griffes +9 (corps à corps)", "Griffe 1d6+4",
      "Réfl +6, Vig +3, Vol +5", "For 16, Dex 13, Con 12, Int 7, Sag 10, Cha 15",
      "Écoute +3, Représentation (chant) +8", "—",
      "Chant captivant (Vol DD 16 : fascination, marche vers la harpie) ; vol",
      "—", "4", "Chaotique mauvais"),
    M("Hippogriffe", "Créature magique", "G", "3d10+6", 22, 15, "12 m (8 cases), vol 30 m (moyenne)",
      "+4", "+4", "2 griffes +5 ; morsure +0", "Griffe 1d4+2 ; morsure 1d4",
      "Réfl +5, Vig +5, Vol +2", "For 15, Dex 13, Con 14, Int 2, Sag 12, Cha 8",
      "Écoute +4, Repérage +5", "—",
      "Piqué ; vision dans le noir 18 m",
      "—", "2", "Neutre"),
    M("Homme-lézard", "Humanoïde (reptilien)", "M", "2d8+2", 11, 15, "9 m (6 cases), nage 9 m",
      "+2", "+0", "Massue +3 ; morsure +0", "Massue 1d6+2 ; morsure 1d4+1",
      "Réfl +0, Vig +3, Vol +0", "For 13, Dex 10, Con 13, Int 8, Sag 9, Cha 9",
      "Équilibre +4, Natation +4, Escalade +2", "—",
      "Nature amphibie (respiration d'eau 4 heures) ; queue (natation)",
      "—", "1", "Neutre"),
    M("Hydre (5 têtes)", "Dragon", "G", "9d10+45", 94, 15, "6 m (4 cases), nage 6 m",
      "+9", "+0", "5 morsures +12 (corps à corps)", "Morsure 1d10+7",
      "Réfl +5, Vig +11, Vol +5", "For 21, Dex 10, Con 21, Int 2, Sag 10, Cha 9",
      "Escalade +6, Natation +12", "—",
      "Attaques multiples (une par tête, toutes sur des adversaires adjacents) ; une tête morte par 10 points de dégâts localisés ; régénération des têtes (pyrohydre : une tête tranchée = 2 têtes)",
      "—", "7", "Neutre"),
    M("Licorne", "Créature magique", "G", "4d10+12", 33, 15, "18 m (12 cases)",
      "+5", "+6", "Corne +7 ; 2 sabots +2", "Corne 2d6+6 ; sabot 1d8+2",
      "Réfl +5, Vig +7, Vol +6", "For 20, Dex 17, Con 21, Int 11, Sag 17, Cha 16",
      "Écoute +7, Perception des affinités +5, Repérage +7, Survie +7", "—",
      "Guérison 3/jour (comme soin léger) ; détection du mal ; téléportation 1/jour ; immunité aux charmes ; peut porter une cavalière légère",
      "—", "3", "Chaotique bon"),
    M("Manticore", "Créature monstrueuse", "M", "6d8+18", 45, 17, "9 m (6 cases), vol 15 m (médiocre)",
      "+6", "+4", "Morsure +8 ; 2 griffes +6 ; volée de pointes +7",
      "Morsure 1d8+5 ; griffe 1d6+2 ; 6 pointes 1d8+5 (portée 54 m)",
      "Réfl +6, Vig +7, Vol +4", "For 21, Dex 15, Con 16, Int 7, Sag 12, Cha 9",
      "Écoute +5, Repérage +8", "—",
      "Volée de pointes (6 par attaque, 24 en tout par jour) ; vol",
      "—", "5", "Loyal mauvais"),
    M("Méduse", "Créature monstrueuse", "M", "6d8+12", 39, 15, "9 m (6 cases)",
      "+6", "+3", "Petits serpents +9 ; dague +7", "Serpent 1d4-1 plus poison (For, DD 15) ; dague 1d4",
      "Réfl +6, Vig +4, Vol +6", "For 10, Dex 17, Con 12, Int 12, Sag 13, Cha 15",
      "Déguisement +9, Écoute +6, Repérage +6", "—",
      "Regard pétrifiant (9 m, Vig DD 15) ; chevelure de serpents",
      "—", "5", "Loyal mauvais"),
    M("Ogre-mage", "Géant", "G", "10d8+40", 85, 18, "9 m (6 cases), vol 12 m (moyenne)",
      "+10", "+1", "Grande épée +12/+7 ; arc long +7", "Épée 3d6+7 ; flèche 2d6+5",
      "Réfl +3, Vig +9, Vol +6", "For 21, Dex 10, Con 20, Int 14, Sag 14, Cha 15",
      "Écoute +8, Perception des affinités +7, Discrétion +8", "—",
      "Régénération 5 ; changement de forme (humanoïde, gazeux) ; invisibilité à volonté ; cône de froid 1/jour ; regard hypnotique ; ténèbres",
      "—", "8", "Loyal mauvais"),
    M("Gelée ocre", "Vase", "G", "6d10+24", 57, 14, "6 m (4 cases), escalade 6 m",
      "+8", "-5", "Coup +9 (corps à corps)", "Coup 2d6+7 plus 1d6 acide",
      "Réfl +2, Vig +8, Vol +0", "For 21, Dex 10, Con 17, Int —, Sag —, Cha 1",
      "Escalade +11", "—",
      "Se divise en deux quand elle subit foudre ou tranchant (2 gelées de 2 DV) ; immunité à la foudre et à l'acide",
      "—", "5", "Neutre"),
    M("Cube gélatineux", "Vase", "G", "4d10+19", 41, 13, "4,5 m (3 cases)",
      "+4", "-5", "Coup +5 (corps à corps)", "Coup 2d4 plus paralysie (Vig DD 16, 3d6 rounds)",
      "Réfl +1, Vig +7, Vol +0", "For 14, Dex 3, Con 20, Int —, Sag —, Cha 1",
      "—", "—",
      "Englobe (avale, 2d6 acide par round) ; translucide (Discrétion +12 en donjon) ; immunité à la foudre",
      "—", "3", "Neutre"),
    M("Ours sanguinaire", "Animal", "G", "12d8+36", 90, 17, "12 m (8 cases)",
      "+9", "+1", "2 griffes +13 ; morsure +8", "Griffe 1d8+8 ; morsure 1d8+4",
      "Réfl +8, Vig +11, Vol +5", "For 26, Dex 13, Con 19, Int 2, Sag 12, Cha 6",
      "Escalade +11, Natation +6, Écoute +8", "—",
      "Attaque en ourse (charge) ; odorat",
      "—", "7", "Neutre"),
    M("Loup atroce", "Animal", "G", "6d8+18", 45, 14, "15 m (10 cases)",
      "+6", "+5", "Morsure +8 (corps à corps)", "Morsure 1d8+6 plus renversement (rupture, lutte +14)",
      "Réfl +6, Vig +8, Vol +3", "For 19, Dex 15, Con 17, Int 2, Sag 12, Cha 10",
      "Écoute +6, Déplacement silencieux +5, Repérage +5, Survie +2", "—",
      "Renversement (adversaire touché : lutte ou chute) ; odorat ; course",
      "—", "3", "Neutre"),
    M("Salamandre", "Élémentaire (feu)", "M", "8d8+16", 52, 17, "9 m (6 cases)",
      "+8", "+4", "Lance +11/+6 ; morsure +7", "Lance 1d8+5 plus 1d6 feu ; morsure 2d6+5",
      "Réfl +9, Vig +6, Vol +5", "For 15, Dex 17, Con 16, Int 11, Sag 12, Cha 15",
      "Artisanat (métal) +9, Escalade +9", "—",
      "Chaleur (contact métal 1d6 feu ; adjacent fin de round 1d6 feu) ; étreinte (2d6+5 plus 1d6 feu) ; immunité au feu",
      "Vulnérabilité au froid (dégâts x1,5)", "6", "Neutre mauvais"),
    M("Satyre", "Créature feérique", "M", "4d8+4", 22, 15, "12 m (8 cases)",
      "+4", "+4", "Cornes +5 (corps à corps) ; dague +5", "Cornes 1d6+2 ; dague 1d4+2",
      "Réfl +6, Vig +2, Vol +5", "For 13, Dex 15, Con 12, Int 14, Sag 15, Cha 16",
      "Représentation (pipeau) +9, Tromperie +6, Escalade +6", "—",
      "Musique de pipeau (fascination, suggestion, terreur — DD 15) ; sorts innés (illusion sonore, image occulte, suggestion)",
      "—", "2", "Neutre"),
    M("Scorpion géant", "Animal", "G", "5d8+5", 27, 16, "15 m (10 cases)",
      "+4", "+0", "2 pinces +3 ; dard +3", "Pince 1d8+3 (lutte +8) ; dard 1d6+3 plus poison (Con 1d6/2d6, DD 15)",
      "Réfl +4, Vig +4, Vol +1", "For 17, Dex 13, Con 12, Int —, Sag 10, Cha 2",
      "Escalade +12, Discrétion +3", "—",
      "Poison du dard ; ne peut attaquer la même créature avec pinces et dard",
      "—", "3", "Neutre"),
    M("Sahuagin", "Humanoïde (aquatique)", "M", "2d8+2", 11, 15, "9 m (6 cases), nage 15 m",
      "+3", "+2", "Trident +4 ; 2 griffes +2 ; morsure +0",
      "Trident 1d8+3 ; griffe 1d4+1 ; morsure 1d4",
      "Réfl +3, Vig +2, Vol +1", "For 15, Dex 12, Con 12, Int 12, Sag 13, Cha 8",
      "Écoute +5, Perception des affinités +5, Repérage +6, Natation +8", "—",
      "Nature amphibie ; furie sanglante (+1 quand blessé) ; perception du sang (paralysie 1d4 rounds si sang, DD 15) ; Déplacement silencieux aquatique",
      "Sensibilité à la lumière du jour (-1 attaques)", "2", "Loyal mauvais"),
    M("Troll", "Géant", "G", "6d8+36", 63, 16, "9 m (6 cases)",
      "+6", "+2", "2 griffes +10 ; morsure +8", "Griffe 1d6+6 ; morsure 1d6+3",
      "Réfl +5, Vig +12, Vol +3", "For 23, Dex 13, Con 23, Int 6, Sag 9, Cha 6",
      "Écoute +4, Repérage +6", "Attaque en aveugle",
      "Régénération 5 ; vision dans le noir 27 m ; odorat ; fauchage (lutte)",
      "Feu et acide : annulent la régénération (blessures persistantes)", "5", "Chaotique mauvais"),
    M("Vampire", "Mort-vivant", "M", "8d12", 52, 22, "12 m (8 cases)",
      "+8", "+8", "2 coups +9 (corps à corps)", "Coup 1d6+4 plus drain d'énergie (1d4 niveaux)",
      "Réfl +10, Vig +8, Vol +10", "For 20, Dex 16, Con —, Int 14, Sag 15, Cha 18",
      "Bluff +11, Discrétion +15, Perception des affinités +11, Psychologie +11", "Alertness, Science de l'initiative",
      "Drain d'énergie ; domination (Vol DD 17) ; forme de gaz, de loup et de chauve-souris ; guérison rapide 5 ; réduction des dégâts 10/argent et magie ; résistance 10 au froid ; toiles d'araignée",
      "Lumière du soleil (destruction) ; fuit l'ail, les lieux sacrés, l'eau bénite ; doit être invité ; pieux au cœur", "8", "Mauvais (variable)"),
    M("Ver pourpre", "Ver", "C", "16d10+128", 216, 19, "6 m (4 cases), creusement 6 m",
      "+16", "-5", "Morsure +26 ; dard +21", "Morsure 2d8+13 plus englobement ; dard 2d6+8 plus poison (Con 1d6/2d6, DD 22)",
      "Réfl +9, Vig +18, Vol +9", "For 36, Dex 10, Con 26, Int —, Sag 9, Cha 9",
      "—", "—",
      "Englobement (avale LD-M, 2d6+8 broyage + acide par round) ; perception tellurique 18 m",
      "—", "12", "Neutre"),
    M("Worg", "Créature magique", "M", "3d10+6", 22, 14, "15 m (10 cases)",
      "+4", "+4", "Morsure +5 (corps à corps)", "Morsure 1d6+3 plus renversement (lutte +5)",
      "Réfl +5, Vig +5, Vol +2", "For 15, Dex 14, Con 15, Int 6, Sag 10, Cha 8",
      "Écoute +4, Déplacement silencieux +3, Repérage +4, Survie +3", "—",
      "Vision dans le noir 18 m ; odorat ; langue (comprend le commun, ne le parle pas)",
      "—", "2", "Neutre mauvais"),
    M("Wyverne", "Dragon", "G", "7d10+21", 59, 19, "9 m (6 cases), vol 18 m (médiocre)",
      "+9", "+3", "Morsure +9 ; dard +9", "Morsure 2d6+4 ; dard 2d6 plus poison (Con 2d6/2d6, DD 20)",
      "Réfl +6, Vig +9, Vol +5", "For 19, Dex 12, Con 17, Int 5, Sag 12, Cha 9",
      "Écoute +5, Repérage +8", "—",
      "Vol ; plongeon ; dard venimeux",
      "—", "6", "Neutre"),
])


def main() -> int:
    with open(BESTIAIRE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    ajoutes, ignores = [], []
    for cle, fiche in NOUVEAUX.items():
        if cle in raw and isinstance(raw[cle], dict) and "nom" in raw[cle]:
            ignores.append(cle)
        else:
            raw[cle] = fiche
            ajoutes.append(cle)

    if "_meta" in raw and isinstance(raw["_meta"], dict):
        raw["_meta"]["nb_monstres"] = sum(
            1 for k in raw if k != "_meta" and isinstance(raw[k], dict) and "nom" in raw[k]
        )

    with open(BESTIAIRE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(ajoutes)} monstre(s) ajouté(s) : {', '.join(ajoutes)}")
    if ignores:
        print(f"⏭️  {len(ignores)} déjà présent(s) (inchangés) : {', '.join(ignores)}")
    print(f"Total bestiaire : {raw['_meta']['nb_monstres']} monstres.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
