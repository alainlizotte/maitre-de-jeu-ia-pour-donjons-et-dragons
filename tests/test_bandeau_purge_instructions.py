"""Tests — bandeau : les consignes LLM n'apparaissent plus à la table.

Bug signalé par l'utilisateur : le bloc « 🎲 Jets officiels du tour »
(player-facing) affichait le texte BRUT des tools, y compris les
INSTRUCTIONS destinées au LLM : « 💪 Bonus dégâts officiel : +3 (FOR 17
(+3)) — recopie CE bonus dans `lancer_degats` (jamais un bonus improvisé). »

Comportement ACTUEL : les blocs player-facing (jets officiels du tour,
résolution automatique du tour, events REST combat) sont épurés par
`_epure_pour_table` — le chiffre et sa provenance restent, la consigne au
modèle disparaît. Le texte envoyé AU LLM garde ses consignes (recopie...).

USAGE
-----
    py -m pytest tests/test_bandeau_purge_instructions.py -v
    (ou : python tests/test_bandeau_purge_instructions.py — runner intégré)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.main import _epure_pour_table  # noqa: E402


# --------------------------------------------------------------------------- #
#  _epure_pour_table — consignes LLM retirées, chiffres conservés
# --------------------------------------------------------------------------- #
def test_epure_consigne_recopie_mele() -> None:
    t = ("⚔️ **Attaque** : Groth [Épée longue] vs Gobelin (CA 15)\n"
         "- Jet brut d'attaque : 13\n"
         "- Bonus total : +5\n"
         "- 💪 **Bonus dégâts officiel : +3** (FOR 17 (+3)) — recopie CE "
         "bonus dans `lancer_degats` (jamais un bonus improvisé).\n"
         "- CA 15 → ✅ **Touché**.")
    epure = _epure_pour_table(t)
    assert "recopie CE bonus" not in epure, epure
    assert "jamais un bonus improvisé" not in epure, epure
    # Le chiffre et sa provenance RESTENT.
    assert "**Bonus dégâts officiel : +3** (FOR 17 (+3))" in epure, epure
    assert "✅ **Touché**" in epure
    assert "Jet brut d'attaque : 13" in epure


def test_epure_consigne_recopie_distance() -> None:
    t = ("- 💪 **Bonus dégâts officiel : +0** (arme à distance : le mod. DEX "
         "ne s'applique pas aux dégâts) — recopie CE bonus dans "
         "`lancer_degats`.")
    epure = _epure_pour_table(t)
    assert "recopie CE bonus" not in epure, epure
    assert "arme à distance : le mod. DEX ne s'applique pas" in epure, epure


def test_epure_consigne_sans_backticks() -> None:
    # Les events du moteur peuvent porter le texte sans backticks.
    t = ("💪 Bonus dégâts officiel : +3 (FOR 17 (+3)) — recopie CE bonus "
         "dans lancer_degats (jamais un bonus improvisé).")
    epure = _epure_pour_table(t)
    assert "recopie CE bonus" not in epure, epure
    assert "Bonus dégâts officiel : +3 (FOR 17 (+3))" in epure, epure


def test_textes_sans_consigne_inchanges() -> None:
    # Les infos UTILES au joueur ne sont jamais touchées.
    t1 = "- ℹ️ Bonus officiel : +5 (fiche de Groth : BBA +1, FOR 16 (+3), +1 Arme de prédilection)."
    assert _epure_pour_table(t1) == t1
    t2 = "- 🎯 Zone de critique 17-20 (épée longue 19-20 ; Science de la critique : zone doublée)."
    assert _epure_pour_table(t2) == t2
    t3 = "- ⚠️ CA imposée par les règles : 11 → 12 (source : fiche de DoranMag (+1 CA magique))."
    assert _epure_pour_table(t3) == t3
    t4 = "- 🏹 **1 × flèche** consommée — restantes : 19"
    assert _epure_pour_table(t4) == t4
    t5 = "- ⭐ **20 naturel** → toucher automatique + menace de critique (effectuer un second jet d'attaque pour confirmer ; si réussi, dégâts doublés/triplés selon arme)."
    assert _epure_pour_table(t5) == t5
    t6 = "- ❌ **1 naturel** → maladresse : attaque ratée automatiquement (conséquences possibles : arme lâchée, etc.)."
    assert _epure_pour_table(t6) == t6


def test_texte_vide_et_bords() -> None:
    assert _epure_pour_table("") == ""
    # Texte terminant par la consigne : pas de débris (espaces/pontuation).
    t = "💪 **Bonus dégâts officiel : +3** (FOR 17 (+3)) — recopie CE bonus dans `lancer_degats` (jamais un bonus improvisé)."
    epure = _epure_pour_table(t)
    assert epure.endswith("(FOR 17 (+3))"), epure
    assert not epure.endswith("   "), epure


# --------------------------------------------------------------------------- #
#  Runner intégré (pytest absent)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    echecs = 0
    for fn in fns:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except AssertionError as e:
            echecs += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - echecs}/{len(fns)} tests OK")
    sys.exit(1 if echecs else 0)
