# -*- coding: utf-8 -*-
"""R7 : test de NON-FUITE - le rattrapage mecanique post-tour.

Le bloc reel observe en partie (04/11) : le groupe emprunte un escalier
piege -> events_pre bruts apposes a la narration (consignes au MJ,
contenu canonique du piege, en-tetes serveur). Le filtre
`_assainir_texte_joueur` doit les neutraliser en gardant les resultats
de jets (vrais des, comme les blocs 🎲 combat).

Usage : py -m pytest tests/test_non_fuite_chat_r7.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from server.main import _assainir_texte_joueur  # noqa: E402

# Le bloc EXACT observe par l'utilisateur (04/11).
BLOC_REEL = """Le couloir descend en spirale vers les profondeurs.

⚔️ _Résolution automatique du round :_

🪜 _Le serveur a DÉJÀ fait changer d'étage au groupe — narre l'ARRIVÉE dans le nouvel étage (ne rappelle AUCUN tool d'étage ni de déplacement) :_ 🪤 **PIÈGE RÉSOLU PAR LE SERVEUR** (emprunt de l'escalier) : Escaliers branlants (zone 6) : Dextérité DD 10 par personnage sous peine de chute (1d6 dégâts) et de vacarme.
🛡️ Sauvegarde (Reflexes) DD 10 par personnage :

- ✅ Throk'mar : total 21 vs DD 10 → réussite.
ℹ️ Jets RÉSOLUS par le serveur (vrais résultats ci-dessus) : narre-les fidèlement, n'invente AUCUN autre jet ni d'autre conséquence chiffrée.
🪜 Vous empruntez l'escalier (descendez vers le sous-sol → Les Bas Tombeaux). Salle actuelle (0,0). Portes visibles ici : nord"""

_INTERDITS = [
    # en-tetes de plomberie
    "Résolution automatique du round",
    "_Mécanique serveur",
    # consignes de narration
    "narre l'ARRIVÉE",
    "n'invente AUCUN",
    "narre-les fidèlement",
    "intègre-le",
    "à ta narration",
    # contenu canonique du piège
    "PIÈGE RÉSOLU PAR LE SERVEUR",
    "Escaliers branlants (zone 6)",
    "Dextérité DD 10 par personnage sous peine",
    # notes d'outils
    "Jets RÉSOLUS par le serveur",
    "Salle actuelle (0,0)",
    "Portes visibles ici",
    "Vous empruntez l'escalier",
    "Le serveur a DÉJÀ fait changer",
]


def test_bloc_reel_assaini():
    """Le bloc reel ne laisse passer AUCUNE instruction ni canonique."""
    apres = _assainir_texte_joueur(BLOC_REEL)
    for mot in _INTERDITS:
        assert mot not in apres, (mot, apres[:300])


def test_resultat_de_jet_conserve():
    """Les vrais resultats de sauvegarde restent visibles (jets = info
    joueur, comme les blocs 🎲 combat)."""
    apres = _assainir_texte_joueur(BLOC_REEL)
    assert "✅ Throk'mar : total 21 vs DD 10 → réussite." in apres, (
        apres[:300])


def test_narration_autour_conservee():
    """La prose du MJ autour du bloc reste intacte."""
    apres = _assainir_texte_joueur(BLOC_REEL)
    assert "Le couloir descend en spirale" in apres, apres[:200]


def test_etage_ligne_neutre():
    """La consigne d'etage (emoji ⚙️ OU 🪜) devient une ligne neutre."""
    for emoj in ("⚙️", "🪜"):
        t = (
            "Descendez.\n"
            f"{emoj} _Le serveur a DÉJÀ fait changer d'étage au groupe — "
            "narre l'ARRIVÉE dans le nouvel étage :_ note tool\n"
            "Suite."
        )
        apres = _assainir_texte_joueur(t)
        assert "Le serveur a DÉJÀ" not in apres, (emoj, apres[:200])
        assert "note tool" not in apres, (emoj, apres[:200])
        assert "Le groupe change d'étage." in apres, (emoj, apres[:200])
        assert "Suite." in apres, (emoj, apres[:200])


def test_mecanique_serveur_header_stripped():
    """L'en-tete du repli pre-run ('⚙️ _Mécanique serveur :_') disparaît."""
    t = "Prose du MJ.\n\n⚙️ _Mécanique serveur :_\n\n- ✅ Kaelin : total 15 vs DD 12 → réussite."
    apres = _assainir_texte_joueur(t)
    assert "_Mécanique serveur" not in apres, apres[:200]
    assert "Prose du MJ." in apres
    assert "✅ Kaelin" in apres
