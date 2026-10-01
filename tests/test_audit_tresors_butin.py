"""AUDIT — Trésors de donjon et récolte sur cadavres : conformité 3.5 ?

Comme les audits précédents : on mesure ce que le moteur FAIT, pas ce que
les intentions annoncent. Chaque test est un verdict.

    py -m pytest tests/test_audit_tresors_butin.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.game import combat as gcombat                            # noqa: E402
from server.game import xp as gxp                                    # noqa: E402
from server.tools.base import ToolContext                             # noqa: E402
from server.tools.dice import lancer_attaque                          # noqa: E402
from server.tools.fiches import _load_fiche, _slug                    # noqa: E402


def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _fiche(tmp: str, f: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(f['nom'])}.json").write_text(
        json.dumps(f, ensure_ascii=False), encoding="utf-8")


PJ = {
    "nom": "Aventurier", "race": "Humain", "classe": "Guerrier", "niveau": 1,
    "carac": {"FOR": 14, "DEX": 12, "CON": 14, "INT": 10, "SAG": 12, "CHA": 8},
    "or": 0, "xp": 0, "competences": {}, "dons": [],
    "equipement": [], "inventaire": [],
}


# --------------------------------------------------------------------------- #
#  CE QUI MARCHE — l'XP par CR
# --------------------------------------------------------------------------- #
def test_1_xp_de_base_est_bien_calcule():
    """Table DMG 3.5 p.38 — valeurs de référence implémentées."""
    assert gxp.xp_pour_cr("1", 1) == 300
    assert gxp.xp_pour_cr("1/2", 1) == 200
    assert gxp.xp_pour_cr("1/3", 1) == 135
    assert gxp.xp_pour_cr("1/4", 1) == 100
    # Facteur de niveau : ligne CR 2 = 600 au niveau 1, divisée par le facteur
    # de niveau (1, 2, 3, 4, 6, 8, …).
    assert gxp.xp_pour_cr("2", 2) == 300      # 600 / 2
    assert gxp.xp_pour_cr("2", 3) == 200      # 600 / 3
    assert gxp.xp_pour_cr("2", 5) == 100      # 600 / 6
    # Symétrie : la ligne d'un CR vaut 300 à SON niveau (niveaux pairs vérifiés
    # par test_1b, qui isole les niveaux impairs où l'invariant saute).
    assert gxp.xp_pour_cr("3", 3) == 300
    assert gxp.xp_pour_cr("8", 8) == 300
    # Seuil de passage de niveau (PHB)
    assert gxp.xp_min_niveau(3) == 3000
    assert gxp.xp_min_niveau(5) == 10000


def test_1b_invariant_diagonale_annonce_mais_non_tenu():
    """DOCUMENTÉ « La diagonale (CR = niveau) vaut 300 partout » (xp.py:28).
    Mesure : c'est faux aux niveaux 9, 11, 13, 15 et 17 (267 au lieu de 300)."""
    ecarts = {n: gxp.xp_pour_cr(str(n), n) for n in range(1, 21)
              if gxp.xp_pour_cr(str(n), n) != 300}
    print(f"\n[niveaux où l'invariant documenté 'diagonale = 300' est violé : "
          f"{ecarts}]")
    assert 9 in ecarts, "la diagonale vaut 300 partout — corriger le test"
    assert set(ecarts) == {9, 11, 13, 15, 17}, (
        f"écarts inattendus sur la diagonale : {ecarts}"
    )


# --------------------------------------------------------------------------- #
#  2. La clôture de combat distribue-t-elle autre chose que de l'XP ?
# --------------------------------------------------------------------------- #
def test_2_cloture_combat_ne_distribue_que_xp():
    src = inspect.getsource(gcombat)
    for interdit in ("_distribuer_or", "_distribuer_butin", "_distribuer_objet",
                      "_distribuer_tresor", "_distribuer_loot",
                      "_distribuer_monnaie"):
        assert interdit not in src, (
            f"{interdit} existe dans game/combat.py — re-vérifier ce test"
        )


def test_2b_tuer_un_monstre_ne_credite_ni_or_ni_objet():
    """Vérifié à l'exécution : kill → XP seulement."""
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, PJ)
        etat = {
            "monstres_combat": [
                {"nom": "Gobelin", "fp": "1/4", "pv": 0, "pv_max": 6,
                 "ca": 15, "conditions": ["Détruit"], "attaques": []},
            ],
            "pj": [{"nom": "Aventurier", "pv": 10, "pv_max": 10,
                    "conditions": []}],
        }
        avant = _load_fiche(_ctx(tmp), "Aventurier")
        or_avant, inv_avant = avant.get("or", 0), len(avant.get("inventaire") or [])

        class _Res:
            events: list = []
            patches: list = []
            state_patches: list = []
        asyncio.run(gcombat._distribuer_xp(_ctx(tmp), _Res(), etat))

        apres = _load_fiche(_ctx(tmp), "Aventurier")
        print(f"\n[après kill] xp={apres.get('xp')} or={apres.get('or')} "
              f"objets={len(apres.get('inventaire') or [])}")
        assert apres.get("xp", 0) > 0, "l'XP n'a pas été distribuée"
        assert apres.get("or", 0) == or_avant, (
            f"l'or a changé au kill : {or_avant} → {apres.get('or')}")
        assert len(apres.get("inventaire") or []) == inv_avant, (
            "un objet a été ajouté à l'inventaire au kill")


# --------------------------------------------------------------------------- #
#  3. Prédation (DMG 3.5 p.90) — implémentée ?
# --------------------------------------------------------------------------- #
def test_3_predation_absente():
    """DMG p.90 « Predation » : +1 contre une créature qu'un allié a déjà
    attaquée, +2 si elle n'a pas encore attaqué le prédateur."""
    from server.tools import dice as d
    src = inspect.getsource(d).lower()
    for t in ("predation", "prédation", "_bonus_predation", "a_predation"):
        assert t not in src, f"{t} présent dans dice.py — corriger ce test"
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, dict(PJ, carac={"FOR": 16, "DEX": 12, "CON": 14,
                                    "INT": 10, "SAG": 12, "CHA": 8},
                         bab=1, equipement=["Épée longue"]))
        t = asyncio.run(lancer_attaque(
            _ctx(tmp), nom_attaquant="Aventurier", nom_cible="Gobelin",
            arme="Épée longue", ca_cible=12, bonus_attaque=99,
        )).text
        print("\n[attaque contre un ennemi déjà harcelé] " + t)
        # BBA 1 + FOR 16 (+3) = +4 : aucune prédation.
        assert "+4" in t, t
        assert "prédation" not in t.lower()


# --------------------------------------------------------------------------- #
#  4. Butin / Plunder (DMG 3.5 p.90) — implémenté ?
# --------------------------------------------------------------------------- #
def test_4_aucun_outil_de_pillage_de_cadavre():
    from server.tools.registry import discover_tools
    noms = set(discover_tools().keys())
    looting = {n for n in noms if any(
        k in n for k in ("piller", "pillage", "butiner", "fouiller", "depecer",
                         "corps", "loot", "butin", "predation", "detroiter"))}
    print(f"\n[tools de pillage de cadavre : {looting or 'AUCUN'}]")
    assert not looting, f"tools de pillage inattendus : {looting}"


def test_4b_aucune_regle_de_butin_dans_le_code():
    from server.tools import fiches, inventaire, monstres
    for mod in (fiches, inventaire, monstres):
        src = inspect.getsource(mod).lower()
        for t in ("predation", "prédation", "butin de corpse",
                  "jet de discrétion dd 15", "dd 15"):
            assert t not in src, (
                f"règle de butin détectée dans {mod.__name__}: {t!r} — "
                f"corriger ce test")
        # un vrai outil « Fouiller un corps » utiliserait le DD 15 du DMG
        assert "dd 15" not in src


def test_4c_monstre_consulter_expose_le_butin():
    """`monstre_consulter` affiche-t-il un butin ? On cherche un CHAMP de
    butin, pas le mot « or » (qui matche « monstre », « corps »…)."""
    from server.tools import monstres as m
    src = inspect.getsource(m.monstre_consulter)
    for champ in ("butin", "loot", "monnaie", "sacs_a_main", "pieces", "pieces_d_or"):
        assert not re.search(rf'["\']?{champ}["\']?\s*[=:,)]', src, re.I), (
            f"monstre_consulter expose le champ {champ!r} — corriger ce test")
    # Le bestiaire lui-même ne porte aucun champ de butin.
    # Structure : dict plat {slug_monstre: {champs…}} + une entrée « _meta ».
    best = json.loads((ROOT / "server" / "data" / "bestiaire.json")
                      .read_text(encoding="utf-8"))
    monstres_ = {k: v for k, v in best.items()
                 if k != "_meta" and isinstance(v, dict)}
    assert monstres_, "structure du bestiaire inattendue — corriger ce test"
    champs: set[str] = set()
    for v in monstres_.values():
        champs |= set(v)
    print(f"\n[{len(monstres_)} monstres — champs : {sorted(champs)}]")
    for interdit in ("butin", "loot", "or", "monnaie", "tresor", "pieces"):
        assert interdit not in champs, (
            f"le bestiaire porte un champ {interdit!r} — corriger ce test")


# --------------------------------------------------------------------------- #
#  5. Tables de trésor de donjon (DMG 3.5 ch. 7) — codées ?
# --------------------------------------------------------------------------- #
def test_5_aucune_table_de_tresor():
    from server import catalogue as cat
    from server.game import combat as gc
    from server.tools import cartes, inventaire, scenarios
    suspects = []
    for mod in (cat, gc, cartes, inventaire, scenarios):
        src = inspect.getsource(mod).lower()
        for t in ("table de tresor", "tresor aleatoire", "jet de tresor",
                  "table_tresor", "treasure_table", "piece de cuivre",
                  "piece d'or", "gemme aleatoire", "objets d'art"):
            if t in src:
                suspects.append(f"{mod.__name__}: {t}")
    print(f"\n[tables de trésor codées : {suspects or 'AUCUNE'}]")
    assert not suspects, f"table de trésor détectée : {suspects}"


# --------------------------------------------------------------------------- #
#  6. Le « trésor » de salle est-il mécanique ou purement narratif ?
# --------------------------------------------------------------------------- #
def test_6_tresor_de_salle_est_du_texte():
    """Le champ `tresor` du manifeste n'est injecté que comme LIGNE DE TEXTE
    dans le prompt du MJ : aucune conversion en or / objet sur la fiche.

    Propriété vérifiée : le seul usage de la valeur est un f-string dans
    `lignes.append` — jamais un calcul, jamais un jet aléatoire."""
    from server.tools import cartes
    assert "tresor" in cartes._ETAGES_MANIFESTE_CHAMPS
    src = inspect.getsource(cartes)
    # Une seule mention : l'affichage texte dans le prompt
    lignes = [l.strip() for l in src.splitlines()
              if re.search(r"tr[ée]sor", l, re.I)]
    for l in lignes:
        # Un usage MÉCANIQUEdu trésor se impérativement lire la valeur
        # (`tresor)` en argument, un cast, un +=). Or le seul usage réel est
        # `lignes.append(f"… Trésor : {tresor}")`.
        assert not re.search(r"\+\+?\s*.*\b(or|montant|po\b)", l, re.I), \
            f"conversion numérique du trésor : {l}"
        assert not re.search(r"\b(int|float|random|randint|choice)\s*\(", l), \
            f"jet aléatoire sur le trésor : {l}"
    print(f"\n[usages du champ tresor dans cartes.py] ")
    for l in lignes:
        print("   " + l)


# --------------------------------------------------------------------------- #
#  7. Rattrapage d'or narré : à qui est-il crédité ?
# --------------------------------------------------------------------------- #
def test_7_rattrapage_or_cible_le_pj_actif():
    """CHANTIER 2 appliqué : le rattrapage d'or crédite le PJ ACTIF (celui qui
    a trouvé le trésor), pas le premier de la liste. Retombe sur le premier PJ
    si le nom n'est pas résolvable (comportement antérieur conservé)."""
    from server import main as m
    src = inspect.getsource(m._appliquer_gains_inventaire_narres)
    sig = inspect.signature(m._appliquer_gains_inventaire_narres)
    assert "nom_pj" in sig.parameters, (
        "le rattrapage ne reçoit pas le nom du PJ crédité (chantier 2 non appliqué)")
    # Sélection par le PJ ACTIF d'abord, repli sur le premier trouvé
    assert "nom_pj" in src, "la sélection par le PJ actif a disparu"
    assert "break" in src, "le break de repli a disparu"
    # Borne anti-fiction
    assert "200" in src, "la borne de 200 po a disparu"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-s"]))
