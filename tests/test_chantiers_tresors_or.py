"""TESTS — Chantiers 0, 2 et 3 (trésors, or, XP).

Chantier 0 : la diagonale XP est FIDÈLE à la table DMG (le commentaire
  annonçait « 300 partout », ce qui était faux — corrigé en documentation).
Chantier 2 : l'or et les objets narrés sont crédités au PJ ACTIF, pas au
  premier de la liste.
Chantier 3 : le trésor canonique de la salle courante est crédité
  mécaniquement quand le MJ narre la fouille, une seule fois par salle.

    py -m pytest tests/test_chantiers_tresors_or.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.game import xp as gxp                                    # noqa: E402
from server.tools.base import ToolContext                             # noqa: E402
from server.tools.fiches import _load_fiche, _slug                    # noqa: E402


def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _orch():
    """Orchestrateur réel (les rattrapages d'objets passent par
    `execute_tool_direct`, qui lit `self.tools`)."""
    from server.llm.orchestrator import Orchestrator
    from server.tools.registry import discover_tools
    return Orchestrator(client=None, tools=discover_tools())


def _fiche(tmp: str, f: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(f['nom'])}.json").write_text(
        json.dumps(f, ensure_ascii=False), encoding="utf-8")


def _etat(tmp: str, grille=None, courant=(0, 0), pj=None) -> dict:
    return {
        "partie_id": "t",
        "phase": "exploration",
        "donjon": {"id": "dj_test", "courant": list(courant),
                   "grille": grille or []},
        "pj": pj or [{"nom": "Aldric", "pv": 10, "pv_max": 10},
                     {"nom": "Brann", "pv": 10, "pv_max": 10}],
    }


def _etat_persiste(tmp: str, pj: list) -> dict:
    """Persiste un état de partie AVEC la liste pj (les rattrapages lisent
    `etat['pj']` : sans état persisté, la liste est vide et rien ne se
    déclenche)."""
    etat = _etat(tmp, pj=pj)
    from server.game.state import PartyState
    PartyState(data_dir=tmp, partie_id="t").save(etat)
    return etat


# --------------------------------------------------------------------------- #
#  CHANTIER 0 — XP fidèle à la table DMG
# --------------------------------------------------------------------------- #
def test_c0_diagonale_xp_fidele():
    """Le commentaire corrigé décrit la réalité : la diagonale vaut 300 pour
    CR 1-8, 10, 12, 14, 16, 18, 19, 20, et 267 pour 9, 11, 13, 15, 17."""
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 19, 20):
        assert gxp.xp_pour_cr(str(n), n) == 300, f"niv {n} : attendu 300"
    for n in (9, 11, 13, 15, 17):
        assert gxp.xp_pour_cr(str(n), n) == 267, f"niv {n} : attendu 267 (valeur de la table)"


def test_c0_docstring_ne_ment_plus():
    """Le faux invariant « 300 partout » a été retiré du commentaire."""
    src = (ROOT / "server" / "game" / "xp.py").read_text(encoding="utf-8")
    assert "vaut 300 partout" not in src, (
        "le faux invariant est encore dans le commentaire de xp.py")
    assert "VALEUR DE LA TABLE" in src, (
        "la correction de documentation est absente")


# --------------------------------------------------------------------------- #
#  CHANTIER 2 — l'or crédité au PJ ACTIF
# --------------------------------------------------------------------------- #
class _Res:
    def __init__(self):
        self.narration = ""
        self.events: list = []
        self.patches: list = []
        self.state_patches: list = []
        self.tool_calls_trace: list = []


def test_c2_or_credite_au_pj_actif_pas_au_premier():
    """En 2J, le joueur 2 qui ramasse doit recevoir l'or — pas le joueur 1."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        _fiche(tmp, {"nom": "Brann", "race": "Nain", "classe": "Clerc",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        _etat_persiste(tmp, [{"nom": "Aldric", "pv": 10, "pv_max": 10},
                             {"nom": "Brann", "pv": 10, "pv_max": 10}])
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = (
            "**Inventaire mis à jour**\n- Pièces d'or : +50\n"
            "- Total : 50"
        )
        # Brann est le PJ ACTIF (deuxième de la liste).
        note = asyncio.run(m._appliquer_gains_inventaire_narres(
            orch, _ctx(tmp), None, res, nom_pj="Brann"))
        print("\n[chantier 2 — or au PJ actif] " + note)
        assert "Brann" in note, f"le PJ actif n'est pas crédité : {note}"
        assert "Aldric" not in note, "le premier PJ est crédité à la place"
        f_al = _load_fiche(_ctx(tmp), "Aldric")
        f_br = _load_fiche(_ctx(tmp), "Brann")
        assert f_al.get("or", 0) == 0, f"le joueur 1 a reçu l'or : {f_al.get('or')}"
        assert f_br.get("or", 0) == 500, f"le joueur 2 n'a pas reçu l'or : {f_br.get('or')}"


def test_c2_objet_credite_au_pj_actif():
    """Même règle pour les objets."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        _fiche(tmp, {"nom": "Brann", "race": "Nain", "classe": "Clerc",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        _etat_persiste(tmp, [{"nom": "Aldric", "pv": 10, "pv_max": 10},
                             {"nom": "Brann", "pv": 10, "pv_max": 10}])
        orch = _orch()
        res = _Res()
        res.narration = (
            "**Inventaire mis à jour**\n- Gemme de sardonyx : +1 objet\n"
            "- Total : 1"
        )
        note = asyncio.run(m._appliquer_gains_inventaire_narres(
            orch, _ctx(tmp), None, res, nom_pj="Brann"))
        print("\n[chantier 2 — objet au PJ actif] " + note)
        assert "Brann" in note, note
        f_al = _load_fiche(_ctx(tmp), "Aldric")
        f_br = _load_fiche(_ctx(tmp), "Brann")
        assert len(f_al.get("inventaire") or []) == 0, (
            "l'objet est allé au joueur 1")
        assert len(f_br.get("inventaire") or []) == 1, (
            "l'objet n'est pas arrivé au joueur 2")


def test_c2_repli_premier_pj_si_nom_inconnu():
    """Sans nom résolvable, le comportement antérieur est conservé."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        _etat_persiste(tmp, [{"nom": "Aldric", "pv": 10, "pv_max": 10}])
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = "**Inventaire mis à jour**\n- Pièces d'or : +10"
        note = asyncio.run(m._appliquer_gains_inventaire_narres(
            orch, _ctx(tmp), None, res, nom_pj=""))
        assert "Aldric" in note, note
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 100


# --------------------------------------------------------------------------- #
#  CHANTIER 3 — trésor canonique de la salle courante
# --------------------------------------------------------------------------- #
SALLE_TRESOR = {
    "x": 1, "y": 0, "type": "salle",
    "tresor": "Le coffre des offrandes à Kelemvor, au pied de la statue : "
              "gemmes et pièces pour 200 po au total.",
}


def test_c3_tresor_canonique_de_la_salle_courante():
    from server import main as m
    etat = _etat(None, grille=[SALLE_TRESOR, {"x": 0, "y": 0, "type": "entree"}],
                 courant=(1, 0))
    t = m.tresor_canonique_salle(etat)
    assert "Kelemvor" in t, t
    # Salle voisine : aucun trésor
    etat2 = _etat(None, grille=[SALLE_TRESOR, {"x": 0, "y": 0, "type": "entree"}],
                  courant=(0, 0))
    assert m.tresor_canonique_salle(etat2) == ""
    # Aucun donjon : ""
    assert m.tresor_canonique_salle({}) == ""


def test_c3_parse_montants_reels():
    """Les 10 formulations réelles des 197 salles."""
    from server import main as m
    cas = [
        ("gemmes et pièces pour 200 po au total", 2000),
        ("une bourse de 15 po et une fine épée courte", 150),
        ("bourse du culte (75 pc)", 75),
        ("pièces et gemmes pour 45 po", 450),
        ("Trois gemmes de sardonyx serties sous le couvercle, 10 po pièce.", 0),
        ("Dague dorée de cérémonie (50 pc)", 50),
        ("couronne de Sedrair II (200 pc)", 200),
    ]
    for texte, attendu in cas:
        or_pc, _ = m._montants_tresor(texte)
        print(f"   {texte[:55]!r:58} -> {or_pc:>5} pc (attendu {attendu})")
        assert or_pc == attendu, f"{texte!r} -> {or_pc}, attendu {attendu}"


def test_c3_tresor_credite_une_seule_fois():
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        etat = _etat(tmp, grille=[SALLE_TRESOR], courant=(1, 0))
        # Persiste l'état pour que le marqueur tresor_pris survive.
        m.PartyState(data_dir=tmp, partie_id="t").save(etat)
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = ("Vous fouillez le coffre des offrandes et en "
                         "retirez les gemmes et les pièces.")
        note = asyncio.run(m._appliquer_tresor_canonique(
            orch, _ctx(tmp), None, res, nom_pj="Aldric"))
        print("\n[chantier 3 — 1re fouille] " + note)
        assert "200 po" in note, note
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 2000

        # 2e fouille : AUCUN re-crédit (marqueur tresor_pris).
        res2 = _Res()
        res2.narration = ("Vous fouillez à nouveau le coffre, désormais vide.")
        note2 = asyncio.run(m._appliquer_tresor_canonique(
            orch, _ctx(tmp), None, res2, nom_pj="Aldric"))
        print("[chantier 3 — 2e fouille] " + repr(note2))
        assert note2 == "", "le trésor a été crédité deux fois"
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 2000, (
            "re-crédit : l'or a doublé")


def test_c3_pas_de_credit_sans_fouille():
    """Une simple mention du trésor en décor ne crédite rien."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        etat = _etat(tmp, grille=[SALLE_TRESOR], courant=(1, 0))
        m.PartyState(data_dir=tmp, partie_id="t").save(etat)
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = ("Au pied de la statue, un coffre des offrandes "
                         "attend. Vous hésitez encore.")
        note = asyncio.run(m._appliquer_tresor_canonique(
            orch, _ctx(tmp), None, res, nom_pj="Aldric"))
        print("\n[chantier 3 — mention sans fouille] " + repr(note))
        assert note == "", "un simple mention a crédité l'or"
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 0


def test_c3_borne_anti_fiction():
    """Un trésor canonique > 2000 po n'est pas crédité (parseur déréglé)."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        salle = dict(SALLE_TRESOR, tresor="un coffre contenant 50 000 po")
        etat = _etat(tmp, grille=[salle], courant=(1, 0))
        m.PartyState(data_dir=tmp, partie_id="t").save(etat)
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = "Vous fouillez le coffre et en sortez la fortune."
        note = asyncio.run(m._appliquer_tresor_canonique(
            orch, _ctx(tmp), None, res, nom_pj="Aldric"))
        print("\n[chantier 3 — borne 2000 po] " + repr(note))
        assert note == "", "la borne anti-fiction n'a pas tenu"
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 0


def test_c3_pas_de_double_credit_avec_rattrapage_generique():
    """Le chantier 3 ne double pas le rattrapage générique : si le MJ a
    écrit « +200 po » lui-même, le trésor canonique ne re-crédite pas."""
    from server import main as m
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {"nom": "Aldric", "race": "Humain", "classe": "Guerrier",
                     "niveau": 1, "or": 0, "xp": 0, "competences": {},
                     "dons": [], "equipement": [], "inventaire": []})
        etat = _etat(tmp, grille=[SALLE_TRESOR], courant=(1, 0))
        m.PartyState(data_dir=tmp, partie_id="t").save(etat)
        orch = m.Orchestrator.__new__(m.Orchestrator)
        res = _Res()
        res.narration = ("**Inventaire mis à jour**\n- Pièces d'or : +200\n"
                         "- Vous fouillez le coffre des offrandes.")
        # Le rattrapage générique crédite d'abord (+200 po = 2000 pc).
        note1 = asyncio.run(m._appliquer_gains_inventaire_narres(
            orch, _ctx(tmp), None, res, nom_pj="Aldric"))
        # Puis le chantier 3 ne doit PAS re-créditer.
        note2 = asyncio.run(m._appliquer_tresor_canonique(
            orch, _ctx(tmp), None, res, nom_pj="Aldric"))
        print("\n[générique] " + note1)
        print("[canonique] " + repr(note2))
        assert _load_fiche(_ctx(tmp), "Aldric").get("or", 0) == 2000, (
            f"double crédit : or = {_load_fiche(_ctx(tmp), 'Aldric').get('or')}"
        )


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-s"]))
