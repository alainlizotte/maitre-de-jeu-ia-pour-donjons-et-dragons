# -*- coding: utf-8 -*-
"""Portrait PJ : l'équipement de création (1 arme, 1 armure, 1 bouclier)
est injecté dans le prompt ComfyUI — le personnage est montré équipé.

Demande utilisateur : « ajoute au prompt de génération : 1 arme, 1 armure
et 1 bouclier sélectionnés lors de la création du personnage, afin
d'améliorer le rendu du portrait. »

Usage : py -m pytest tests/test_portrait_equipement.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.persos import (  # noqa: E402
    _extraire_equipement_portrait,
    construire_prompt_portrait,
)


def _fiche(equipement: list, inventaire: list | None = None) -> dict:
    return {
        "nom": "Brunhild", "race": "Nain", "classe": "Guerrier",
        "apparence": {"sexe": "F", "yeux": "verts"},
        "equipement": equipement,
        "inventaire": inventaire or [],
    }


def test_les_trois_pieces_sont_injectees():
    fiche = _fiche([
        {"nom": "Hache à deux mains", "qte": 1},
        {"nom": "Cotte de mailles", "qte": 1},
        {"nom": "Bouclier bois lourd", "qte": 1},
        {"nom": "Ration de voyage", "qte": 5},   # pas de l'équipement montré
    ])
    arme, armure, bouclier = _extraire_equipement_portrait(fiche)
    assert arme == "greataxe", arme
    assert armure == "chainmail", armure
    assert bouclier == "heavy wooden shield", bouclier

    prompt = construire_prompt_portrait(fiche)
    assert "wielding a greataxe" in prompt, prompt
    assert "wearing chainmail" in prompt, prompt
    assert "heavy wooden shield strapped on the arm" in prompt, prompt
    # Cadre élargi pour que l'équipement soit visible.
    assert "upper body portrait" in prompt, prompt
    assert "head and shoulders" not in prompt, prompt


def test_sans_equipement_cadre_classique():
    fiche = _fiche([])
    prompt = construire_prompt_portrait(fiche)
    assert "wielding" not in prompt and "wearing" not in prompt, prompt
    assert "head and shoulders" in prompt, prompt


def test_noms_hors_catalogue_repasses_tels_quels():
    fiche = _fiche([
        {"nom": "Trident rustique", "qte": 1},
        {"nom": "Targe de fer", "qte": 1},
    ])
    arme, armure, bouclier = _extraire_equipement_portrait(fiche)
    assert arme == "Trident rustique", arme        # mot-clé « trident »
    assert armure == "", armure
    assert bouclier == "Targe de fer", bouclier    # mot-clé « targe »
    prompt = construire_prompt_portrait(fiche)
    assert "wielding a Trident rustique" in prompt, prompt


def test_inventaire_aussi_scanne():
    """L'équipement peut venir de `inventaire` (fiches anciennes/rejouées)."""
    fiche = _fiche([], inventaire=[
        {"nom": "Épée longue", "qte": 1},
        {"nom": "Armure de cuir", "qte": 1},
        {"nom": "Targe", "qte": 1},
    ])
    arme, armure, bouclier = _extraire_equipement_portrait(fiche)
    assert arme == "longsword" and armure == "leather armor"
    assert bouclier == "buckler"


def test_premiere_piece_de_chaque_type_gagne():
    """Plusieurs armes/armures : seule la PREMIÈRE de chaque type est
    montrée (1 arme, 1 armure, 1 bouclier — demande utilisateur)."""
    fiche = _fiche([
        {"nom": "Dague", "qte": 1},
        {"nom": "Épée longue", "qte": 1},
        {"nom": "Armure de cuir", "qte": 1},
        {"nom": "Harnois complet", "qte": 1},
        {"nom": "Targe", "qte": 1},
        {"nom": "Bouclier bois lourd", "qte": 1},
    ])
    arme, armure, bouclier = _extraire_equipement_portrait(fiche)
    assert arme == "dagger", arme
    assert armure == "leather armor", armure
    assert bouclier == "buckler", bouclier
