# -*- coding: utf-8 -*-
"""Catalogue curé de scènes à pré-générer par scénario (univers pilote : Laelith).

Chaque scène = (titre, description_pour_image). Le `titre` sert de clé de
slug : il doit correspondre à un lieu / nom que le MJ emploiera en jeu pour
que le hook post-tour puisse retrouver l'image par `_slug_image(titre)`.

Champs riches : lieux, événements marquants, objets/trésor, PNJ importants.
"""
SCENES_LAELITH: dict[str, list[tuple[str, str]]] = {
    # ------------------------------------------------------------------ #
    "laelith_loeil_de_gruumsh": [
        ("Le temple maudit de Gruumsh",
         "un temple orc souterrain dédié à Gruumsh, autel de pierre noire "
         "gravé d'yeux, torches rouges, crânes et tribus orques en prière"),
        ("La caverne du rituel",
         "une vaste caverne où un rituel de destruction orc est lancé, "
         "cercle runique rougeoyant, chamane orc en transe, énergie sombre"),
        ("Le repaire du culte",
         "le quartier général du culte orc, une salle pillée pleine de "
         "trophées, armes rouillées et Goblins gardiens"),
        ("Le combat contre le champion orc",
         "les héros affrontent le champion orc de Gruumsh dans une arène "
         "de pierre, épée ensanglantée, hurlements de guerre"),
        ("La relique de Gruumsh",
         "un lourd médaillon de bronze à l'effigie d'un œil, posé sur "
         "l'autel, irradiant une lueur mauve menaçante"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_la_tombe_des_rois_serpents": [
        ("L'entrée de la tombe des Rois Serpents",
         "l'entrée sombre d'un tombeau sous la cité-fantôme, portail de "
         "pierre à têtes de serpents, torches vacillantes"),
        ("La salle des Rois Serpents",
         "la chambre funéraire des anciens Rois Serpents, sarcophages de "
         "pierre, fresques de serpents, poussière et toiles d'araignée"),
        ("Le passage piégé",
         "un couloir étroit criblé de pièges, flèches et fosses, un "
         "aventurier tendu, torche à la main"),
        ("Le gardien serpent",
         "un gigantesque serpent gardien se dresse devant les héros dans "
         "la pénombre, écailles luisantes, œil reptilien"),
        ("Le trésor des rois serpents",
         "un trésor de bijoux, couronnes et or entassé, un diadème de "
         "serpent étincelant sur un piédestal"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_à_la_chasse_aux_gobs": [
        ("La route ravagée par les gobelins",
         "une route de campagne dévastée par un raid gobelin, charrette "
         "renversée, fumée, corps de marchands"),
        ("Le camp gobelin",
         "un camp gobelin animé dans les collines, feux de camp, huttes "
         "de toile, gobelins armés"),
        ("La tanière du chef gobelin",
         "la grotte du chef gobelin, trône de bois, butins entassés, "
         "trophées de voyageurs"),
        ("Le chef gobelin et sa garde",
         "les héros affrontent le chef gobelin et sa garde dans sa "
         "tanière, combat brutal à la lueur des torches"),
        ("La carte secrète des gobelins",
         "une carte griffonnée révélant un repaire bien plus vaste, "
         "posée sur une table de camp éclairée"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_douze_fontaines": [
        ("La fontaine empoisonnée",
         "une des douze fontaines de Laelith, eau trouble et verdâtre, "
         "des villageois malades autour, ambiance inquiétante"),
        ("Les catacombes de l'Empereur-Démon",
         "le repaire souterrain du vampire, un domaine funèbre de "
         "catacombes, cercueils, bougies noires"),
        ("Le laboratoire des empoisonneurs",
         "un laboratoire secret d'alchimie sombre, fioles de poison, "
         "cornues, un sbire masqué au travail"),
        ("L'Empereur-Démon",
         "le vampire empereur-démon en cape écarlate, se dressant dans "
         "son antre, yeux rouges, crocs luisants"),
        ("La place empoisonnée de Laelith",
         "la grande place de Laelith en émeute naissante, foule inquiète, "
         "fontaine au centre, gardes vigilants"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_duel_au_pinceau": [
        ("La place des Sept Royaumes",
         "la grande place des Sept Royaumes de Laelith, marché animé, "
         "couleurs vives, badauds et étals"),
        ("Le duel pictural",
         "deux artistes peignent face à face sur la place, chevalets, "
         "toiles, foule en cercle qui observe"),
        ("Le portrait ensorcelé",
         "un portrait d'apparence innocente qui fascine, une lueur "
         "magique dans les yeux du tableau, un spectateur ensorcelé"),
        ("La guilde des peintres",
         "l'atelier de la guilde des peintres, toiles partout, palettes, "
         "lumière de lanterne, peintres au travail"),
        ("Le maître peintre comploteur",
         "un peintre à l'air perfide enveloppé d'ombres, un pinceau "
         "magique à la main, des toiles vaporeuses derrière lui"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_lauberge_du_sanglier_gris": [
        ("L'auberge du Sanglier Gris",
         "une auberge rustique au bord de la route, enseigne au sanglier, "
         "lumière chaude aux fenêtres, forêt autour"),
        ("La salle commune de l'auberge",
         "la salle commune cosy de l'auberge, feu de cheminée, tables de "
         "bois, clients attablés, servantes"),
        ("La cave secrète",
         "le réseau souterrain sous l'auberge, celliers humides, barreaux, "
         "prisonniers capturés, cloaque"),
        ("Les geôles des ravisseurs",
         "une geôle cachée où les héros sont capturés, barreaux, paille, "
         "obscurité, une lanterne lointaine"),
        ("L'évasion de la cave",
         "les héros s'échappent par un passage secret de la cave, lueur "
         "de torche, coursive de pierre étroite"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_la_secte_du_crane": [
        ("Les quais de Laelith",
         "les quais animés de Laelith, bateaux, cordages, marchandises, "
         "brume sur l'eau, ambiance portuaire"),
        ("Le temple du Crâne",
         "un temple sombre en forme de crâne, lueur violette aux fenêtres, "
         "adeptes encapuchonnés à l'entrée"),
        ("L'antre de la secte du Crâne",
         "la salle de culte de la secte, crânes empilés, cierges, autel "
         "macabre, encapuchonnés en cercle"),
        ("Le fouineur Lamb",
         "un fouineur maigre et nerveux en cape, tenant une lanterne, dans "
         "une ruelle sombre de Laelith"),
        ("La relique du crâne",
         "un artefact en forme de crâne d'argent posé sur un autel, "
         "émanant une lueur malveillante"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_le_collier_de_zark": [
        ("Les ruelles de Laelith",
         "les ruelles étroites et colorées de Laelith, maisons penchées, "
         "linges suspendus, marché animé"),
        ("Zark le fox-terrier",
         "un petit fox-terrier blanc et écossais au collier de laiton "
         "garni de boules de verre multicolores, courant dans une ruelle"),
        ("Le marché de Laelith",
         "le marché animé de Laelith, étals colorés, marchands criant, "
         "foule, tissus et fruits exposés"),
        ("Le voleur au collier",
         "un voleur ravisseur fuyant avec le chien au collier étincelant, "
         "à travers ruelles et toits"),
        ("La restitution du collier",
         "le marchand Amlarag heureux, le fox-terrier Zark dans ses bras, "
         "le collier de laiton brillant, devant sa boutique"),
    ],
    # ------------------------------------------------------------------ #
    "laelith_le_masque_de_utruz": [
        ("Les geôles du Roi-Dieu",
         "les geôles sombres du Roi-Dieu, barreaux, chaînes, un demi-elfe "
         "prisonnier à la sortie"),
        ("Le voleur demi-elfe",
         "un voleur demi-elfe filant dans les ruelles de Laelith, un masque "
         "de bronze serré contre lui, regard furtif"),
        ("Le masque de bronze d'Utruz",
         "un masque de bronze ancien gravé de runes, posé sur une table, "
         "à la lueur d'une bougie"),
        ("Le trésor caché",
         "une cachette secrète révélant un trésor d'or et de joyaux, "
         "derrière une pierre mouvante du mur"),
        ("L'ennemi du passé",
         "un ancien ennemi surgit de l'ombre devant les héros, cape déchirée, "
         "regard menaçant, dans une ruelle nocturne"),
    ],
}
