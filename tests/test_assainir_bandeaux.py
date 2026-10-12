# -*- coding: utf-8 -*-
# 🧹 Assainissement « joueur » (demande explicite) : les bandeaux diffusés
# au client ne doivent PLUS contenir le contenu canonique (piège + solution,
# trésor, ennemis à venir), les descriptions des salles voisines, ni les
# instructions au MJ — le LLM, lui, continue de tout recevoir.

from __future__ import annotations

import os
import sys

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys_path)

from server.main import _assainir_texte_joueur  # noqa: E402

# L'EXEMPLE EXACT du joueur (bandeau vu en partie 60446e2f) :
_EXEMPLE_JOUEUR = (
    "🗺️ _Retour vers « bibliothèque » résolu par le serveur (premier pas du "
    "chemin ; 1 salle(s) au total) — redemandez le même déplacement pour "
    "continuer._\n"
    "↩️ Vous REPARCOURREZ la salle (0,0) — type : bibliothèque — DÉJÀ "
    "VISITÉE.\n"
    "Description enregistrée : « Chambre carrée au toit en arche, où un "
    "portique de belle pierre scintillant occupe le mur est. Les autres murs "
    "sont tapissés d'étagères couvertes de crânes de toutes tailles, du plus "
    "petit en haut au plus grand en bas, chacun frappé d'une petite plaque "
    "de laiton. »\n"
    "🚪 Portes de cette salle (ce qui est CONNU derrière chacune) :\n"
    "⬅️ Le groupe est ARRIVÉ ICI par la porte EST — ne la confonds PAS avec "
    "une sortie.\n"
    "porte EST → (1,0) « antichambre » DÉJÀ VISITÉE : « Petite pièce "
    "identique à la précédente, mais sans portique ni décoration macabre : "
    "elle est d'une chaleur insupportable »\n"
    "⚠️ NE RÉINVENTE PAS cette salle : reprends FIDÈLEMENT la description et "
    "l'état ci-dessus (ce qui a été fait reste fait : monstres vaincus, "
    "coffres vidés…), puis narre seulement ce que le groupe y trouve "
    "maintenant.\n"
    "📜 **Contenu canonique de la salle (scénario)** :\n"
    "🪤 Piège : Un levier ouvre le sol ; une statue de femme à coiffe de "
    "scorpion de fer se dresse, tenant un diamant. Tirer la queue du scorpion "
    "déchaîne la lumière qui révèle l'énigme : « Qui marche à quatre pattes "
    "le matin, deux à midi, trois le soir ? » (réponse : l'homme).\n"
    "💰 Trésor : Diamant au sommet de la statue (valeur à déterminer) ; les "
    "crânes peuvent être retirés.\n"
    "\n\n🖼️ Carte : /data/cartes/donjon_60446e2f.svg"
)


def test_exemple_joueur_assaini():
    """TOUT ce que le joueur a signalé disparaît : méta du filet, description
    stockée, bloc portes (descriptions voisines), instructions au MJ, contenu
    canonique (piège + RÉPONSE de l'énigme, trésor)."""
    t = _assainir_texte_joueur(_EXEMPLE_JOUEUR)
    # rien de ce qui spoile/ne sert au joueur :
    assert "résolu par le serveur" not in t
    assert "Description enregistrée" not in t
    assert "Portes de cette salle" not in t
    assert "ARRIVÉ ICI" not in t
    assert "NE RÉINVENTE PAS" not in t
    assert "Contenu canonique" not in t
    assert "Piège" not in t and "piège" not in t
    assert "Diamant" not in t
    assert "réponse : l'homme" not in t
    assert "chaleur insupportable" not in t  # description de la salle voisine
    # ce qui reste utile :
    assert "bibliothèque" in t  # la position/destination
    assert "Carte :" in t  # le lien carte
    assert "0,0" in t or "REPARCOURREZ" in t  # la salle courante


def test_texte_exploration_nouvelle_salle():
    """Salle DU SCÉNARIO : la description canonique (que le MJ doit NARRER)
    n'apparaît pas brute au joueur ; les instructions non plus."""
    txt = (
        "🚶 Vous avancez au nord → salle (0,-1) — type : **antichambre**. "
        "Portes EXISTANTES ici : sud — n'invente AUCUNE autre sortie. Le "
        "groupe poursuit son exploration du donjon.\n\n"
        "📜 **Salle DU SCÉNARIO** — description canonique du module "
        "(narre-la à l'identique, sans rien inventer) : « Petite pièce "
        "sombre. »\n\n"
        "📜 **Contenu canonique de la salle (scénario)** :\n"
        "⚔️ Ennemis DU SCÉNARIO : Perceur ×2 — engage-les via "
        "`engager_combat` dès qu'ils sont aperçus."
    )
    t = _assainir_texte_joueur(txt)
    assert "description canonique" not in t
    assert "Petite pièce sombre" not in t
    assert "Ennemis DU SCÉNARIO" not in t
    assert "Perceur" not in t
    assert "n'invente AUCUNE autre sortie" not in t
    assert "Le groupe poursuit son exploration" not in t
    assert "antichambre" in t  # le type reste (info de navigation)
    assert "Portes visibles ici : sud" in t  # portes utiles conservées


def test_jets_et_carte_conserves():
    """Les infos UTILES au joueur ne sont pas touchées : jets de dés,
    carte, butin (méta « Rattrapage serveur » épurée). Le bloc piège réel
    sépare l'ÉNONCÉ (ligne 🪤, DM) des JETS (lignes 🛡️/-, joueur)."""
    txt = (
        "⚔️ Gorak attaque le Gobelin — 🎲 Jets officiels : d20=14+4=18 vs "
        "CA 15 → ✅ Touché. Dégâts : 1d6+2=5.\n"
        "\n\n🪤 **PIÈGE RÉSOLU PAR LE SERVEUR** (emprunt de l'escalier) : "
        "Escaliers branlants (zone 6) : Dextérité DD 10 par personnage sous "
        "peine de chute (1d6 dégâts) et de vacarme.\n"
        "🛡️ Sauvegarde (Reflexes) DD 10 par personnage :\n"
        "- ❌ Gorak : total 7 vs DD 10 → ÉCHEC.\n"
        "\n\n📦 **Rattrapage serveur** : +12 po (or)\n"
        "\n\n⚙️ _Rattrapage serveur (sort narré sans outil — accepté)_\n\n"
        "✨ Le sort jaillit.\n"
        "\n\n🖼️ Carte : /data/cartes/donjon_x.svg"
    )
    t = _assainir_texte_joueur(txt)
    assert "d20=14+4=18" in t  # les jets restent
    assert "1d6+2=5" in t  # les dégâts restent
    assert "PIÈGE RÉSOLU PAR LE SERVEUR" not in t  # l'énoncé du piège part
    assert "Escaliers branlants (zone 6)" not in t  # le détail du piège part
    assert "total 7 vs DD 10" in t  # le JET du joueur reste visible
    assert "ÉCHEC" in t
    assert "Butin : +12 po" in t  # le butin épuré
    assert "Rattrapage serveur" not in t  # la méta disparaît
    assert "✨ Le sort jaillit" in t  # le résultat du sort reste
    assert "Carte :" in t
    assert "Gorak attaque le Gobelin" in t  # la narration combat reste


def test_notes_filet_serveur_raccourcies():
    """Les notes des filets serveur deviennent des mentions neutres et
    courtes (demande : pas de résolution technique affichée)."""
    t1 = _assainir_texte_joueur(
        "Le groupe avance.\n\n⚙️ _Le serveur a fait changer d'étage au "
        "groupe._\n\n🪜 Vous empruntez l'escalier…")
    assert "Le serveur a fait changer" not in t1
    assert "Le groupe change d'étage" in t1
    t2 = _assainir_texte_joueur(
        "Vous foncez.\n\nℹ️ _Précision de l'état du jeu : AUCUN combat "
        "réel n'a été engagé (ni `engager_combat` ni `demarrer_combat`) — "
        "la phase « combat » inventée a été retirée. La menace décrite "
        "reste à gérer par la narration (fuite, parlementage, attaque "
        "déclarée avec les tools)._")
    assert "Le combat annoncé n'a pas lieu" in t2
    assert "engager_combat" not in t2
