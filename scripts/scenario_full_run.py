# -*- coding: utf-8 -*-
"""Parties complètes réelles sur D'AUTRES scénarios (demande joueur).

Réutilise le moteur de `dues_full_run.py` (nav par waypoints, driver de
combat, transcription) avec une config PAR SCÉNARIO :

  py scripts/scenario_full_run.py gobs setup   # À la chasse aux gobs (Laelith)
  py scripts/scenario_full_run.py gobs run
  py scripts/scenario_full_run.py sea setup    # The Sea Curse (Royaumes Oubliés)
  py scripts/scenario_full_run.py sea run
  py scripts/scenario_full_run.py statut gobs
  py scripts/scenario_full_run.py rapport gobs

Même table : Brandt (Nain Guerrier), Aurora (Clerc — sorts préparés au
setup), Flick (Halfelin Roublard, carquois plein). Fiches recréées à neuf
par scénario (niveau 1, conditions d'origine effacées).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import sys
import unicodedata

if sys.platform == "win32" and not getattr(sys.stdout, "_dnd35_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace")
    try:
        sys.stdout._dnd35_utf8 = True  # type: ignore[attr-defined]
        sys.stderr._dnd35_utf8 = True  # type: ignore[attr-defined]
    except Exception:                                        # pragma: no cover
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tests"))

import e2e_combats_reels as _c  # noqa: E402
import scripts.dues_full_run as dfu  # noqa: E402  (moteur réutilisé)

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "scenario_runs")
os.makedirs(_TMP, exist_ok=True)
_c.BILAN_FILE = os.path.join(_TMP, "bilan.json")

from e2e_combats_reels import Bilan, etat_partie, fiche_pj  # noqa: E402

BASE = "http://localhost:8123"

# --------------------------------------------------------------------------- #
#  Config par scénario
# --------------------------------------------------------------------------- #
SCENARIOS = {
    "hurl": {
        "titre": "Le Dragon de Hurlemont 1/4 (E2E 3 PJ)",
        "quete": {
            "titre": "Le Dragon de Hurlemont 1/4 - Les Terres de l'Eternelle",
            "pitch": "La vallee de Hurlemont : la mine est envahie par les "
                     "gobelins du Dragon - scenario d'initiation.",
            "source": ("[terres_dragon_hurlemont_ch01] /data/scenarios/"
                       "Les Terres de l'Eternel/Le Dragon de Hurlemont/"
                       "le dragon de hurlemont-01-l e s t e r r e s d e l "
                       "e t e.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "entree de la mine (0,0)",
             "texte": "L'entree de la mine est gardee - deux gobelins ! "
                      "Brandt fonce le premier.",
             "nudge": "(MJ : ouvre le donjon du scenario avec "
                      "carte_donjon_entrer, puis engage la rencontre "
                      "canonique de la salle avec engager_combat.)"},
            {"vers": [1, 0], "label": "salle du trone secondaire (1,0)"},
            {"pj": "Aurora", "label": "confrontation des soldats (1,0)",
             "texte": "Deux soldats en faction - Aurora les somme de se "
                      "rendre, Brandt est pret.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [2, 0], "label": "antichambre (2,0)"},
            {"vers": [3, 0], "label": "cuisine (3,0)"},
            {"vers": [3, 1], "label": "bibliotheque (3,1)"},
            {"vers": [4, 1], "label": "garde avancee (4,1)"},
            {"pj": "Flick", "label": "confrontation de la garde (4,1)",
             "texte": "Un gobelin isole monte la garde - Flick s'en "
                      "charge, dague au poing.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [3, 1], "label": "retour bibliotheque"},
            {"vers": [2, 1], "label": "couloir (2,1)"},
            {"vers": [1, 1], "label": "salle vide (1,1)"},
            {"vers": [0, 1], "label": "autel profane (0,1)"},
            {"pj": "Aurora", "label": "confrontation a l'autel (0,1)",
             "texte": "Un gobeline profane l'autel de Kelemvor - Aurora "
                      "l'interpelle, symbole leve.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, 2], "label": "escaliers (0,2)"},
            {"vers": [1, 2], "label": "crypte (1,2)"},
            {"pj": "Aurora", "label": "confrontation de l'ombre (1,2)",
             "texte": "Une ombre se detache des murailles ! Aurora leve "
                      "son symbole, Brandt la charge.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat - l'Ombre resiste aux armes "
                      "non magiques, utilise les sorts si besoin.)"},
            {"vers": [2, 2], "label": "bibliotheque (2,2)"},
            {"vers": [3, 2], "label": "tresor (3,2)"},
            {"pj": "Flick", "label": "butin final",
             "texte": "Flick fouille le tresor et empoche ce qui brille.",
             "nudge": "(MJ : le butin passe par inventaire_ramasser.)"},
            {"pj": "Brandt", "label": "cloture",
             "texte": "La mine est nettoyee. Nous remontons rapporter la "
                      "victoire au village.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
    "shadows": {
        "titre": "Shadows Over The Moonsea 1/7 (E2E 3 PJ)",
        "quete": {
            "titre": "Shadows Over The Moonsea 1/7 - Partie",
            "pitch": "Phlan : un comptoir profane pres du port - loup "
                     "terrible a l'entree, garde de kobolds et bandits "
                     "au fond.",
            "source": ("[divers_shadows_over_the_moonsea_ch01] /data/"
                       "scenarios/Divers/Shadows Over The Moonsea/"
                       "shadows over the moonsea-01-partie partie 1 7.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "entree du comptoir (0,0)",
             "texte": "Un loup terrible garde l'entree - Brandt l'attend, "
                      "hache haute.",
             "nudge": "(MJ : ouvre le donjon du scenario avec "
                      "carte_donjon_entrer, puis engage la rencontre "
                      "canonique de la salle avec engager_combat.)"},
            {"vers": [1, 0], "label": "antichambre (1,0)"},
            {"vers": [2, 0], "label": "salle vide (2,0)"},
            {"vers": [2, 1], "label": "autel (2,1)"},
            {"pj": "Aurora", "label": "examen de l'autel",
             "texte": "Aurora examine l'autel profane (Connaissances "
                      "religion).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Connaissances religion).)"},
            {"vers": [1, 1], "label": "bibliotheque (1,1)"},
            {"vers": [0, 1], "label": "couloir (0,1)"},
            {"vers": [0, 2], "label": "la garde (0,2)"},
            {"pj": "Brandt", "label": "la grande garde (0,2)",
             "texte": "Kobolds et bandits en nombre ! Brandt bloque le "
                      "couloir, Aurora lance Benediction.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [1, 2], "label": "salle du trone (1,2)"},
            {"pj": "Aurora", "label": "le trone (1,2)",
             "texte": "La garde du trone ! Aurora leur somme de se "
                      "rendre, Brandt pret a frapper.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [2, 2], "label": "tresor (2,2)"},
            {"pj": "Flick", "label": "butin final",
             "texte": "Flick fouille le tresor du chef et empoche le "
                      "butin.",
             "nudge": "(MJ : le butin passe par inventaire_ramasser.)"},
            {"pj": "Brandt", "label": "cloture",
             "texte": "Le comptoir est repris. Nous rapportons la "
                      "nouvelle a Phlan.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
    "shadows2": {
        "titre": "Shadows Over The Moonsea 2/7 (E2E 3 PJ)",
        "quete": {
            "titre": "Shadows Over The Moonsea 2/7 - Partie",
            "pitch": "La Route de Fer vers l'île : Warsh et sa péage, "
                     "les sorcières, la mère louve — puis Ixas et son "
                     "équipage.",
            "source": ("[divers_shadows_over_the_moonsea_ch02] /data/"
                       "scenarios/Divers/Shadows Over The Moonsea/"
                       "shadows over the moonsea-02-partie partie 2 7.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "Route de Fer (0,0)",
             "texte": "Le chapitre 2 commence - nous reprenons la route "
                      "vers l'ile depuis Phlan.",
             "nudge": "(MJ : si le groupe n'est pas sur la carte du "
                      "chapitre, carte_donjon_entrer. La mere louve peut "
                      "etre calmee (DD 10) - le combat est EVITABLE.)"},
            {"vers": [1, 0], "label": "peage de Warsh (1,0)"},
            {"pj": "Brandt", "label": "le peage",
             "texte": "Warsh exige un peage - Brandt regle le passage "
                      "pour le groupe, sans histoire.",
             "nudge": "(MJ : la scene passe par la narration - le canon "
                      "de la salle ne prevoit pas de combat.)"},
            {"vers": [2, 0], "label": "les insulaires (2,0)"},
            {"vers": [3, 0], "label": "les sorcieres (3,0)"},
            {"vers": [4, 0], "label": "le fumoir d'Ulburto (4,0)"},
            {"vers": [5, 0], "label": "la grange (5,0)"},
            {"vers": [5, 1], "label": "l'acte du fou (5,1)"},
            {"pj": "Aurora", "label": "examen de l'acte",
             "texte": "Aurora ecoute le fou et tente de percer son "
                      "delire (Psychologie).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Psychologie), difficulte 20.)"},
            {"vers": [4, 1], "label": "la masure du lemure (4,1)"},
            {"vers": [3, 1], "label": "le fetiche (3,1)"},
            {"vers": [2, 1], "label": "le parchemin (2,1)"},
            {"vers": [1, 1], "label": "le tome (1,1)"},
            {"vers": [0, 1], "label": "la brume de l'Audacity (0,1)"},
            {"vers": [0, 2], "label": "l'equipage (0,2)"},
            {"pj": "Brandt", "label": "l'equipage du culte (0,2)",
             "texte": "Kobolds du culte et pirates s'entretuent - "
                      "nous intervenons, Brandt bloque la route.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [1, 2], "label": "les quartiers d'Ixas (1,2)"},
            {"pj": "Aurora", "label": "Ixas (1,2)",
             "texte": "Ixas et sa garde - Aurora somme le culte de se "
                      "rendre, Brandt est pret a frapper.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [2, 2], "label": "les navires (2,2)"},
            {"pj": "Flick", "label": "butin final",
             "texte": "Flick fouille les quartiers d'Ixas et empoche le "
                      "butin.",
             "nudge": "(MJ : le butin passe par inventaire_ramasser.)"},
            {"pj": "Brandt", "label": "cloture",
             "texte": "L'ile est securisee. Nous rapportons la nouvelle "
                      "a Phlan.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
    "shadows3": {
        "titre": "Shadows Over The Moonsea 3/7 (E2E 3 PJ)",
        "quete": {
            "titre": "Shadows Over The Moonsea 3/7 - Partie",
            "pitch": "L'ile après les cultistes : les liens avec les "
                     "infernaux se précisent (Warsh, les sorcières, le "
                     "lémure) — la piste de l'Audacity.",
            "source": ("[divers_shadows_over_the_moonsea_ch03] /data/"
                       "scenarios/Divers/Shadows Over The Moonsea/"
                       "shadows over the moonsea-03-partie partie 3 7.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "reprise sur l'ile (2,2)",
             "texte": "Le chapitre 3 commence - nous fouillons ce que "
                      "les cultistes ont laisse sur l'ile.",
             "nudge": "(MJ : le groupe est sur la carte de l'ile - "
                      "poursuis le scénario du chapitre 3.)"},
            {"vers": [1, 2], "label": "les quartiers d'Ixas (1,2)"},
            {"vers": [0, 2], "label": "l'équipage (0,2)"},
            {"vers": [0, 1], "label": "la brume de l'Audacity (0,1)"},
            {"pj": "Aurora", "label": "l'Audacity (0,1)",
             "texte": "Aurora examine l'épave fantôme dans la brume "
                      "(Connaissances religion).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Connaissances religion).)"},
            {"vers": [1, 1], "label": "le tome (1,1)"},
            {"pj": "Flick", "label": "étude du tome (1,1)",
             "texte": "Flick étudie le tome trouvé chez les cultistes - "
                      "que révèle-t-il ?",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Décryptage).)"},
            {"vers": [2, 1], "label": "le parchemin (2,1)"},
            {"vers": [3, 1], "label": "le fétiche (3,1)"},
            {"pj": "Aurora", "label": "le fétiche (3,1)",
             "texte": "Aurora examine le fétiche de la masure ("
                      "Connaissances plans).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Connaissances plans).)"},
            {"vers": [4, 1], "label": "la masure du lémure (4,1)"},
            {"pj": "Brandt", "label": "le lémure (4,1)",
             "texte": "Une masse de chair liquide rôde ici - Brandt se "
                      "tient prêt.",
             "nudge": "(MJ : engage la rencontre canonique avec "
                      "engager_combat si le lémure attaque.)"},
            {"vers": [5, 1], "label": "l'acte du fou (5,1)"},
            {"vers": [5, 0], "label": "la grange (5,0)"},
            {"vers": [4, 0], "label": "le fumoir d'Ulburto (4,0)"},
            {"pj": "Brandt", "label": "Ulburto (4,0)",
             "texte": "Brandt cherche l'aide d'Ulburto sur les tunnels - "
                      "avec précaution.",
             "nudge": "(MJ : jet officiel avec lancer_d20 si une "
                      "négociation est nécessaire.)"},
            {"vers": [3, 0], "label": "les sorcières (3,0)"},
            {"pj": "Aurora", "label": "les sorcières (3,0)",
             "texte": "Aurora interpelle les sorcières sur le feu et le "
                      "pacte - diplomatie d'abord.",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Diplomatie) si nécessaire.)"},
            {"vers": [2, 0], "label": "les insulaires (2,0)"},
            {"vers": [1, 0], "label": "le péage de Warsh (1,0)"},
            {"pj": "Brandt", "label": "Warsh (1,0)",
             "texte": "Brandt confronte Warsh sur ses liens avec les "
                      "infernaux.",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Intimidation) si nécessaire.)"},
            {"vers": [0, 0], "label": "la Route de Fer (0,0)"},
            {"pj": "Brandt", "label": "la mère louve (0,0)",
             "texte": "La mère louve protège ses petits - Brandt tente "
                      "de la calmer d'une voix douce.",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(competence=Dressage), difficulte 10 - réussite : "
                      "elle se calme, PAS de combat.)"},
            {"pj": "Brandt", "label": "cloture",
             "texte": "Le chapitre est accompli - nous campons et "
                      "tirons les conclusions.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
    "gobs": {
        "titre": "À la chasse aux gobs (E2E 3 PJ)",
        "quete": {
            "titre": "À la chasse aux gobs",
            "pitch": "L'antre des gobelins près de Laelith : la guilde met "
                     "une prime sur la bande qui rançonne les routes.",
            "source": ("[laelith_à_la_chasse_aux_gobs] /data/scenarios/"
                       "Laelith/À la chasse aux gobs/Chasse-aux-gobs.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "entrée dans l'antre",
             "texte": "Voici l'antre. Nous entrons, armes en main, en "
                      "silence — la prime est pour la bande entière.",
             "nudge": "(MJ : ouvre le donjon du scénario avec "
                      "carte_donjon_entrer.)"},
            {"pj": "Brandt", "label": "les deux gobelins endormis (0,0)",
             "texte": "Deux gobelins dorment là — Brandt les abats "
                      "discrètement avant qu'ils ne se réveillent.",
             "nudge": "(MJ : engage la rencontre canonique de la salle avec "
                      "engager_combat, surprise=Brandt, Aurora, Flick si "
                      "le groupe surprend.)"},
            {"vers": [0, -1], "label": "couloir piégé (0,-1)"},
            {"vers": [0, -2], "label": "poste de garde (0,-2)"},
            {"vers": [0, -3], "label": "salle du voleur (0,-3)"},
            {"pj": "Flick", "label": "confrontation du voleur",
             "texte": "Un voleur ! Flick l'interpelle : « Fuis, ou nous "
                      "t'attachons aux barreaux. »",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, -4], "label": "salle vide (0,-4)"},
            {"vers": [0, -5], "label": "salle aux gobelins (0,-5)"},
            {"pj": "Brandt", "label": "confrontation des gobelins (0,-5)",
             "texte": "Deux gobelins en sentinelle — Brandt charge le "
                      "premier, la masse d'Aurora couvre le second.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, -6], "label": "antichambre des hobgobelins (0,-6)"},
            {"pj": "Aurora", "label": "confrontation (0,-6)",
             "texte": "Aurora brandit son symbole : « Rendez-vous, serviteurs "
                      "des gobs ! »",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, -7], "label": "garde du gobelours (0,-7)"},
            {"pj": "Brandt", "label": "confrontation du gobelours",
             "texte": "Le gobelours ! Brandt l'attend de front, hache "
                      "haute — Aurora le bénit.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat, et applique Bénédiction si "
                      "elle est lancée.)"},
            {"vers": [0, -8], "label": "salle du trône (0,-8)"},
            {"pj": "Aurora", "label": "assaut du trône (0,-8)",
             "texte": "Le chef et sa garde ! Aurora lance Bénédiction sur "
                      "le groupe, puis Brandt fonce.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, -9], "label": "laboratoire (0,-9)"},
            {"vers": [0, -10], "label": "antichambre finale (0,-10)"},
            {"pj": "Flick", "label": "confrontation finale (0,-10)",
             "texte": "Le dernier du groupe est là — Flick l'interpelle, "
                      "dague tirée.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"pj": "Brandt", "label": "retour victorieux",
             "texte": "L'antre est nettoyée. Nous rentrons toucher la "
                      "prime à la guilde.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
    "sea": {
        "titre": "The Sea Curse (E2E 3 PJ)",
        "quete": {
            "titre": "The Sea Curse",
            "pitch": "Mintarn : des créatures marines rôdent autour d'un "
                     "comptoir profané — la malédiction de la mer remonte "
                     "des profondeurs.",
            "source": ("[ro_the_sea_curse] /data/scenarios/Les Royaumes "
                       "Oubliés/The Sea Curse.pdf"),
        },
        "steps": [
            {"pj": "Brandt", "label": "entrée du comptoir",
             "texte": "L'air sent la marée basse et le sang. Nous entrons, "
                      "prudents.",
             "nudge": "(MJ : ouvre le donjon du scénario avec "
                      "carte_donjon_entrer.)"},
            {"vers": [-1, 0], "label": "puits (-1,0)"},
            {"pj": "Flick", "label": "examen du puits",
             "texte": "Flick jette un regard dans le puits — quelque chose "
                      "brille au fond (Détection).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(compétence=Détection).)"},
            {"vers": [0, 0], "label": "retour à l'entrée"},
            {"vers": [1, 0], "label": "autel (1,0)"},
            {"pj": "Aurora", "label": "examen de l'autel",
             "texte": "Aurora examine l'autel — des offrandes marines y "
                      "sont encore fraîches (Connaissances religion).",
             "nudge": "(MJ : jet officiel avec lancer_d20 "
                      "(compétence=Connaissances religion).)"},
            {"vers": [0, 0], "label": "retour à l'entrée"},
            {"vers": [0, -1], "label": "couloir des sahuagins (0,-1)"},
            {"pj": "Brandt", "label": "confrontation (0,-1)",
             "texte": "Des sahuagins ! Brandt fait rempart dans le couloir "
                      "étroit.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [1, -1], "label": "antichambre (1,-1)"},
            {"vers": [1, -2], "label": "bibliothèque du locathah (1,-2)"},
            {"pj": "Flick", "label": "butin de la bibliothèque",
             "texte": "Flick fouille la bibliothèque et emporte ce qui a "
                      "de la valeur.",
             "nudge": "(MJ : le butin passe par inventaire_ramasser.)"},
            {"vers": [1, -1], "label": "retour antichambre"},
            {"vers": [0, -1], "label": "retour couloir"},
            {"vers": [0, -2], "label": "garde sahuagin (0,-2)"},
            {"pj": "Aurora", "label": "confrontation (0,-2)",
             "texte": "Deux sahuagins en garde — Aurora les somme de "
                      "retourner aux abîmes, Brandt fait rempart.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"vers": [0, -3], "label": "cellules (0,-3)"},
            {"vers": [1, -3], "label": "laboratoire (1,-3)"},
            {"pj": "Flick", "label": "butin du laboratoire",
             "texte": "Flick empoche fioles et instruments du laboratoire "
                      "sahuagin.",
             "nudge": "(MJ : le butin passe par inventaire_ramasser.)"},
            {"vers": [0, -3], "label": "retour cellules"},
            {"vers": [0, -4], "label": "salle du trône (0,-4)"},
            {"pj": "Aurora", "label": "assaut final (0,-4)",
             "texte": "Le chef sahuagin et sa garde ! Aurora lance "
                      "Bénédiction, Brandt fonce sur le chef.",
             "nudge": "(MJ : engage la rencontre canonique de la salle "
                      "avec engager_combat.)"},
            {"pj": "Brandt", "label": "retour victorieux",
             "texte": "La malédiction est levée — nous rentrons à Mintarn "
                      "toucher la récompense.",
             "nudge": "(MJ : accorde l'XP d'histoire via "
                      "fiche_perso_gagner_xp si l'objectif est accompli.)"},
        ],
    },
}


def _redirect(key: str) -> None:
    """Pointe les fichiers du moteur dfu vers les fichiers du scénario."""
    dfu.PID_FILE = os.path.join(_TMP, f"pid_{key}.txt")
    dfu.CURSOR_FILE = os.path.join(_TMP, f"cursor_{key}.json")
    dfu.TRANSCRIPT_FILE = os.path.join(_TMP, f"transcript_{key}.json")
    dfu.PROBLEMS_FILE = os.path.join(_TMP, f"problems_{key}.json")
    _c.BILAN_FILE = os.path.join(_TMP, f"bilan_{key}.json")


def _slug(nom: str) -> str:
    nf = unicodedata.normalize("NFKD", nom)
    return re.sub(r"[^a-z0-9_-]+", "_",
                  "".join(c for c in nf
                          if not unicodedata.combining(c)).lower()).strip("_")


def _compte() -> str:
    import urllib.error
    import urllib.request
    compte, mdp = "e2e_scenarios", "scenarios-2026!"

    def _post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{BASE}{path}", data=data,
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())

    try:
        return _post("/api/auth/inscription",
                     {"nom": compte, "mot_de_passe": mdp})["token"]
    except urllib.error.HTTPError:
        return _post("/api/auth/connexion",
                     {"nom": compte, "mot_de_passe": mdp})["token"]


async def phase_setup(key: str) -> None:
    cfg = SCENARIOS[key]
    _redirect(key)
    for f in (dfu.PID_FILE, dfu.CURSOR_FILE, dfu.TRANSCRIPT_FILE,
              dfu.PROBLEMS_FILE, _c.BILAN_FILE):
        if os.path.isfile(f):
            os.remove(f)
    # Fiches neuves (niveau 1, conditions d'origine effacées).
    for pj in dfu.PJS:
        fp = os.path.join(_c.DATA, "fiches", f"fiche_{_slug(pj)}.json")
        if os.path.isfile(fp):
            os.unlink(fp)
    bilan = Bilan()
    token = _compte()
    import urllib.request
    data = json.dumps({"titre": cfg["titre"]}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/parties", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    pid = json.loads(urllib.request.urlopen(req, timeout=20).read())[
        "partie_id"]
    with open(dfu.PID_FILE, "w", encoding="utf-8") as f:
        f.write(pid)
    print(f"=== Partie {key} : {pid} ===")
    qdata = json.dumps(cfg["quete"]).encode()
    qreq = urllib.request.Request(
        f"{BASE}/api/parties/{pid}/quest", data=qdata,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(qreq, timeout=20)
    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    tools = discover_tools()
    ctx = ToolContext(partie_id=pid, joueur="e2e", data_dir=_c.DATA)
    for i, (nom, pcfg) in enumerate(dfu.PJS.items()):
        # 🔧 Dons de départ (run shadows #5) : sans « Arme en finesse », le
        # roublard FOR 8/DEX 17 attaquait à -1 (la règle 3.5 est correcte
        # — c'est la fiche qui n'avait pas le don) ; Brandt gagne son
        # Arme de prédilection (+1 hache), Aurora Encaissement (+3 PV).
        _dons = {
            "Brandt": "Arme de prédilection (hache d'arme)",
            "Aurora": "Encaissement",
            "Flick": "Arme en finesse",
        }.get(nom, "")
        r = await invoke_tool(tools["fiche_perso_creer_rapide"], ctx, {
            "nom": nom, "race": pcfg["race"], "classe": pcfg["classe"],
            "niveau": 1, "joueur": pcfg["joueur"],
            "carac_texte": pcfg["carac"],
            **({"dons_texte": _dons} if _dons else {})})
        bilan.check(f"[setup] fiche {nom}", r.text.startswith("✅"),
                    r.text[:120])
        # 🔧 Équipement de départ (post-correctif #1) : sans arme du
        # catalogue portée, l'attaque déterministe retombait sur
        # « arme improvisée 1d6 » — Brandt perdait 1d8 → 1d6. IMPORTANT :
        # `equipement` est une PROJECTION de `inventaire` (inventaire.py
        # 1140) — passer par inventaire_ajouter (portee permanent), pas
        # par equipement_texte, sinon le premier ajout d'objet ÉCRASE
        # l'équipement créé (constaté : Flick perdait épée + arc).
        _eq = {
            "Brandt": [("Hache d'arme", 1), ("Cotte de maille", 1)],
            "Aurora": [("Masse d'armes lourde", 1), ("Armure de cuir", 1)],
            "Flick": [("Épée courte", 1), ("Arc court", 1),
                      ("Armure de cuir", 1)],
        }.get(nom, [])
        for _obj, _q in _eq:
            r2 = await invoke_tool(tools["inventaire_ajouter"], ctx, {
                "nom": nom, "objet": _obj, "quantite": _q,
                "portee": "permanent"})
            # ⚠️ = objet inconnu du catalogue (NON ajouté) → échec réel.
            bilan.check(f"[setup] équipement {nom} : {_obj}",
                        not r2.text.startswith(("❌", "⚠️")),
                        r2.text[:80])
        await invoke_tool(tools["etat_partie_patch"], ctx,
                          {"chemin": f"pj.{i}.joueur",
                           "valeur": pcfg["joueur"]})
    r = await invoke_tool(tools["preparer_sorts"], ctx, {
        "nom_personnage": "Aurora",
        "preparations_json": json.dumps({
            "Lumière": 1, "Résistance": 1, "Soin mineur": 1,
            "Soins légers": 2, "Bénédiction": 1})})
    bilan.check("[setup] sorts d'Aurora préparés", "✅" in r.text,
                r.text[:120])
    r = await invoke_tool(tools["inventaire_ajouter"], ctx, {
        "nom": "Flick", "objet": "flèche", "quantite": 20})
    bilan.check("[setup] flèches de Flick", "✅" in r.text, r.text[:80])
    await invoke_tool(tools["etat_partie_patch"], ctx,
                      {"chemin": "phase", "valeur": "exploration"})
    snap = dfu.snapshot(pid)
    bilan.check("[setup] 3 PJ", set(snap["pj"]) == set(dfu.PJS),
                f"pj={list(snap['pj'])}")
    bilan.sauver()
    print(f"=== PJ : {list(snap['pj'])} — quête attachée ===")


async def phase_run(key: str) -> None:
    cfg = SCENARIOS[key]
    _redirect(key)
    pid = dfu.lire_pid()
    bilan = Bilan.charger()

    # 🧪 Assistance de TEST (documentée) : après CHAQUE combat, Aurora
    # soigne l'équipe à plein (outils serveur déterministes). Sans elle,
    # le pilote — qui ne retraite jamais — mourait systématiquement en
    # milieu de donjon (3 TPC sur 4 runs) et le test du MJ ne pouvait pas
    # aller au bout. Les combats restent intégralement réels.
    _jour_original = dfu.jour_combat

    async def _jour_soigne(pid_, socks_, bilan_, titre, max_rounds=16):
        detruits = await _jour_original(pid_, socks_, bilan_, titre,
                                        max_rounds)
        from server.tools.base import ToolContext, invoke_tool
        from server.tools.registry import discover_tools
        tools = discover_tools()
        ctx = ToolContext(partie_id=pid_, joueur="e2e", data_dir=_c.DATA)
        st = etat_partie(pid_) or {}
        for p in st.get("pj") or []:
            pv, pvmax = int(p.get("pv") or 0), int(p.get("pv_max") or 0)
            if pv < pvmax:
                r = await invoke_tool(tools["fiche_perso_soigner"], ctx,
                                      {"nom": p.get("nom"), "soin": pvmax})
                print(f"    [soin inter-combat] {p.get('nom')}: "
                      f"{r.text[:70]}")
        return detruits

    dfu.jour_combat = _jour_soigne

    # ✨ (runs ch02/ch03) : préparation des sorts de soin au démarrage —
    # les emplacements d'un chapitre précédent sont épuisés (le soin
    # déterministe les dépense pour de vrai) ; sans re-préparation, les
    # soins du chapitre sont refusés 4 fois d'affilée (shadows3.3).
    from server.tools.base import (
        ToolContext as _TC_s3, invoke_tool as _inv_s3)
    _tools_s3 = discover_tools()
    _ctx_s3 = ToolContext(partie_id=pid, joueur="e2e", data_dir=_c.DATA)
    _r_s3 = await invoke_tool(_tools_s3["preparer_sorts"], _ctx_s3, {
        "nom_personnage": "Aurora",
        "preparations_json": json.dumps({
            "Soins légers": 2, "Soin mineur": 1, "Bénédiction": 1,
            "Résistance": 1, "Lumière": 1})})
    print("[setup] sorts d'Aurora (run):", _r_s3.text[:90])

    socks = await dfu._ouvrir(pid)
    try:
        await dfu.jouer_phase(pid, socks, bilan, f"{key}", cfg["steps"])
    except dfu.PartieTerminee as e_go:
        # 🛡️ (run shadows #3) : TPK/game_over → arrêt PROPRE de la run
        # (bilan sauvé) au lieu d'enchaîner des étapes zombies contre un
        # garde qui répond en 0 s.
        print(f"\n⚑ PARTIE TERMINÉE — {e_go}")
        print("   (résurrection négociée possible via le bouton 🕯️ / "
              "l'acceptation chat, ou nouvelle partie)")
    finally:
        await dfu._fermer(socks)
    bilan.sauver()


async def phase_go(key: str, pj: str, texte: str) -> None:
    """Tour ad-hoc : scenario_full_run.py go <cle> <PJ> « texte »."""
    _redirect(key)
    pid = dfu.lire_pid()
    bilan = Bilan.charger()
    socks = await dfu._ouvrir(pid)
    snap = dfu.snapshot(pid)
    label = "go"
    if snap["phase"] == "combat":
        cible = min(
            (m for m, st in snap["monstres"].items()
             if "Détruit" not in st["conds"]),
            key=lambda m: snap["monstres"][m]["pv"] or 0, default=None)
        texte = texte.replace("{cible}", cible or "l'ennemi")
    await dfu.action_pj(pid, socks, bilan, pj, texte, label,
                        "(MJ : résous avec les tools officiels.)")
    for _ in range(20):
        if dfu.snapshot(pid)["phase"] != "combat":
            break
        snap = dfu.snapshot(pid)
        courant = snap["courant"]
        pj_c = next((p for p in dfu.PJS if p.lower() in courant.lower()
                     or courant.lower() in p.lower()), None)
        if pj_c is None:
            await asyncio.sleep(12)
            if dfu.snapshot(pid)["courant"] == courant:
                dfu.noter_probleme("tour_monstre_bloque",
                                   f"go : tour de « {courant} » sans rotation")
            break
        tourne = {}
        texte2, nudge2 = dfu._action_combat(
            pj_c, dfu.snapshot(pid), min(
                (m for m, st in snap["monstres"].items()
                 if "Détruit" not in st["conds"]),
                key=lambda m: snap["monstres"][m]["pv"] or 0, default=None),
            tourne)
        await dfu.action_pj(pid, socks, bilan, pj_c, texte2, label, nudge2)
        await dfu.sante_sockets(pid, socks)
    await dfu._fermer(socks)
    bilan.sauver()


async def phase_statut(key: str) -> None:
    _redirect(key)
    pid = dfu.lire_pid()
    st = etat_partie(pid) or {}
    snap = dfu.snapshot(pid)
    print(f"Phase={st.get('phase')} | salle=({snap['donjon'].get('courant')}) "
          f"| étage={snap['donjon'].get('etage')}")
    for n, s in snap["pj"].items():
        print(f"  🛡️ {n}: pv={s['pv']}/{s['pv_max']} {s['conds']}")
    for m, s in snap["monstres"].items():
        print(f"  👹 {m}: {s['pv']}/{s['pv_max']} {s['conds']}")
    pr = os.path.join(_TMP, f"problems_{key}.json")
    if os.path.isfile(pr):
        with open(pr, encoding="utf-8") as f:
            pl = json.load(f)
        print(f"Problèmes : {len(pl)}")
        for p in pl[-8:]:
            print(f"  ⚠️ [{p['type']}] {p['detail'][:110]}")


async def phase_rapport(key: str) -> None:
    _redirect(key)
    bilan = Bilan.charger()
    pid = dfu.lire_pid()
    st = etat_partie(pid) or {}
    print(f"=== RAPPORT {key} — phase={st.get('phase')} "
          f"tour={st.get('tour')} ===")
    for nom in dfu.PJS:
        f = fiche_pj(nom) or {}
        print(f"  🛡️ {nom}: pv={f.get('pv')}/{f.get('pv_max')} "
              f"xp={f.get('xp')}")
    print(f"Vérifs : {len(bilan.oks)} OK, {len(bilan.fails)} échecs")
    for f_ in bilan.fails:
        print("  ✗ " + f_[:150])
    pr = os.path.join(_TMP, f"problems_{key}.json")
    if os.path.isfile(pr):
        with open(pr, encoding="utf-8") as f:
            pl = json.load(f)
        print(f"PROBLÈMES ({len(pl)}) :")
        for p in pl:
            print(f"  ⚠️ [{p['type']}] {p['detail'][:170]}")


async def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "gobs"
    cmd = sys.argv[2] if len(sys.argv) > 2 else "statut"
    if key not in SCENARIOS:
        print(f"Scénario inconnu : {key}. Options : {list(SCENARIOS)}")
        sys.exit(2)
    if cmd == "go":
        await phase_go(key, sys.argv[3] if len(sys.argv) > 3 else "Brandt",
                       sys.argv[4] if len(sys.argv) > 4 else "Nous continuons.")
    elif cmd == "setup":
        await phase_setup(key)
    elif cmd == "run":
        await phase_run(key)
    elif cmd == "statut":
        await phase_statut(key)
    elif cmd == "rapport":
        await phase_rapport(key)
    else:
        print("Commandes : setup | run | statut | rapport  (+ clé scénario)")


if __name__ == "__main__":
    asyncio.run(main())
