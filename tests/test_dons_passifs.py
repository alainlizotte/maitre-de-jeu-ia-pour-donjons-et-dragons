"""Tests — dons passifs appliqués mécaniquement en jeu.

Couvre :
- `bonus_dons_effet` : mapping dons (catalogue FR, variantes EN, texte libre)
  → bonus (initiative, sauvegardes, compétences) ;
- `resume_dons_competences` : ligne injectée dans le récapitulatif MJ ;
- recoupements `lancer_d20` (Alerte → +2 Détection même à rang nul),
  `lancer_sauvegarde` (Volonté de fer → +2 Volonté) et
  `calculer_initiative` (Initiative améliorée → +4, valeur fiche prime).

USAGE
-----
    py -m pytest tests/test_dons_passifs.py -v
    (ou : python tests/test_dons_passifs.py — runner intégré)
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

from server.persos import resume_dons_competences                     # noqa: E402
from server.tools.base import ToolContext                            # noqa: E402
from server.tools.dice import (                                      # noqa: E402
    calculer_initiative, lancer_d20, lancer_sauvegarde,
)
from server.tools.fiches import _slug, bonus_dons_effet              # noqa: E402


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _ecrire_fiche(tmp: str, fiche: dict) -> None:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fiche_{_slug(fiche['nom'])}.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8"
    )


FICHE_GROTH = {
    "nom": "Groth",
    "race": "Nain",
    "classe": "Guerrier",
    "niveau": 1,
    "carac": {"FOR": 16, "DEX": 12, "CON": 14, "INT": 10, "SAG": 12, "CHA": 8},
    "competences": {"Intimidation": 4, "Escalade": 2},
    "dons": ["Alerte", "Initiative améliorée", "Volonté de fer"],
    "sauvegardes": {"Vigueur": 4, "Reflexes": 1, "Volonte": 3},
}


# --------------------------------------------------------------------------- #
#  bonus_dons_effet — mapping
# --------------------------------------------------------------------------- #
def test_bonus_dons_effet_catalogue() -> None:
    assert bonus_dons_effet(["Initiative améliorée"], "initiative") == 4
    assert bonus_dons_effet(["Volonté de fer"], "sauvegarde_volonte") == 2
    assert bonus_dons_effet(["Alerte"], "comp_detection") == 2
    assert bonus_dons_effet(["Alerte"], "comp_perception_auditive") == 2
    assert bonus_dons_effet(["Grande Fortitude"], "sauvegarde_vigueur") == 2
    assert bonus_dons_effet(["Réflexes surprenants"], "sauvegarde_reflexes") == 2


def test_bonus_dons_effet_variantes_en() -> None:
    assert bonus_dons_effet(["Improved Initiative"], "initiative") == 4
    assert bonus_dons_effet(["Iron Will"], "sauvegarde_volonte") == 2
    assert bonus_dons_effet(["Alertness"], "comp_detection") == 2
    assert bonus_dons_effet(["Great Fortitude"], "sauvegarde_vigueur") == 2
    assert bonus_dons_effet(["Lightning Reflexes"], "sauvegarde_reflexes") == 2


def test_bonus_dons_effet_negatifs() -> None:
    # Dons sans effet sur l'effet demandé → 0 (jamais de bonus fantôme).
    assert bonus_dons_effet(["Dur à cuire (+3 PV)"], "initiative") == 0
    assert bonus_dons_effet(["Alerte"], "initiative") == 0
    assert bonus_dons_effet(["Alerte"], "sauvegarde_volonte") == 0
    assert bonus_dons_effet([], "comp_detection") == 0
    assert bonus_dons_effet(None, "initiative") == 0
    assert bonus_dons_effet(["Vigilance"], "effet_inconnu") == 0


def test_bonus_dons_effet_texte_libre() -> None:
    # fiche_perso_creer_rapide peut stocker une chaîne ou un JSON.
    assert bonus_dons_effet('["Volonté de fer"]', "sauvegarde_volonte") == 2
    assert bonus_dons_effet("Volonté de fer, Alerte", "sauvegarde_volonte") == 2


# --------------------------------------------------------------------------- #
#  resume_dons_competences — ligne du récapitulatif MJ
# --------------------------------------------------------------------------- #
def test_resume_dons_competences() -> None:
    r = resume_dons_competences(FICHE_GROTH)
    assert "Dons : Alerte, Initiative améliorée, Volonté de fer" in r
    # Tri par rang décroissant.
    assert "Intimidation 4, Escalade 2" in r


def test_resume_dons_competences_vide() -> None:
    assert resume_dons_competences({"nom": "X"}) == ""
    # Rangs nuls omis.
    assert resume_dons_competences({"competences": {"Escalade": 0}}) == ""


# --------------------------------------------------------------------------- #
#  lancer_d20 — Alerte (+2 Détection) même à rang nul
# --------------------------------------------------------------------------- #
def test_lancer_d20_alerte_detection_sans_rangs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = dict(FICHE_GROTH)
        fiche["competences"] = {}  # aucun rang en Détection
        _ecrire_fiche(tmp, fiche)
        # Le LLM envoie mod=+1 (SAG 12 = +1) en ignorant le don :
        # la fiche impose +1 + 2 (Alerte) = +3.
        tr = asyncio.run(lancer_d20(
            ctx, modificateur=1, raison="Épier le couloir",
            difficulte=10, nom_personnage="Groth", competence="Détection",
        ))
        assert "+3" in tr.text and "dons" in tr.text


def test_lancer_d20_rangs_et_carac() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(FICHE_GROTH))
        # Intimidation : 4 rangs + CHA 8 (-1) = +3 ; le LLM envoie 0.
        tr = asyncio.run(lancer_d20(
            ctx, modificateur=0, raison="Intimider le gobelin",
            nom_personnage="Groth", competence="Intimidation",
        ))
        assert "Modificateur recalculé +0 → +3" in tr.text


# --------------------------------------------------------------------------- #
#  lancer_sauvegarde — Volonté de fer (+2 Volonté)
# --------------------------------------------------------------------------- #
def test_lancer_sauvegarde_volonte_de_fer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(FICHE_GROTH))
        # Fiche : Volonté +3 ; don Volonté de fer : +2 → total +5.
        tr = asyncio.run(lancer_sauvegarde(
            ctx, type_sauvegarde="Volonté", modificateur=3, difficulte=12,
            nom_personnage="Groth", source="Sort de charme",
        ))
        assert "+5" in tr.text and "dons" in tr.text


def test_lancer_sauvegarde_sans_don_reconnu() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        fiche = dict(FICHE_GROTH)
        fiche["dons"] = ["Dur à cuire (+3 PV)"]  # pas de don de sauvegarde
        _ecrire_fiche(tmp, fiche)
        tr = asyncio.run(lancer_sauvegarde(
            ctx, type_sauvegarde="Vigueur", modificateur=9, difficulte=12,
            nom_personnage="Groth", source="Poison",
        ))
        # Fiche Vigueur +4 : recoupé vers +4, aucun "+2 (dons)" fantôme.
        assert "→ +4" in tr.text and "dons" not in tr.text


# --------------------------------------------------------------------------- #
#  calculer_initiative — Initiative améliorée (+4), fiche prime
# --------------------------------------------------------------------------- #
def test_calculer_initiative_dons_pj() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _ctx(tmp)
        _ecrire_fiche(tmp, dict(FICHE_GROTH))
        # Groth : DEX 12 (+1) + Initiative améliorée (+4) = +5 imposé,
        # même si le LLM fournit +1.
        tr = asyncio.run(calculer_initiative(
            ctx, participants="Groth:+1, Gobelin:+2"
        ))
        assert "Groth : initiative recalculée +1 → +5" in tr.text
        assert "Gobelin" in tr.text  # le monstre garde son mod fourni


# --------------------------------------------------------------------------- #
#  resume_inventaire + récap minimal (bug « pas de fiole de guérison »)
# --------------------------------------------------------------------------- #
def test_resume_inventaire_quantites_et_charges() -> None:
    from server.persos import resume_inventaire
    fiche = {
        "nom": "Utturgut",
        "inventaire": [
            {"nom": "Hache à deux mains", "qte": 1},
            {"nom": "fiole de guérison", "qte": 4},
            {"nom": "Kit premiers secours", "qte": 1, "charges": 8},
        ],
    }
    r = resume_inventaire(fiche)
    assert r.startswith("Sac (permanent) : ")
    assert "fiole de guérison ×4" in r
    assert "Kit premiers secours (8 charges)" in r
    # Objet à qte 1 : pas de « ×1 ».
    assert "Hache à deux mains," in r and "Hache à deux mains ×1" not in r


def test_resume_inventaire_repli_equipement_et_plafond() -> None:
    from server.persos import resume_inventaire
    assert resume_inventaire({"nom": "X"}) == ""
    assert resume_inventaire({"nom": "X", "inventaire": []}) == ""
    # Repli sur `equipement` (fiches anciennes sans inventaire structuré).
    r = resume_inventaire({"equipement": [{"nom": "Dague", "qte": 2}]})
    assert "Dague ×2" in r
    # Plafond : au-delà de max_items, on signale le reste.
    gros = {"inventaire": [{"nom": f"objet {i}", "qte": 1} for i in range(40)]}
    r2 = resume_inventaire(gros, max_items=5)
    assert r2.count("objet ") == 5
    assert "+35 autres" in r2


def test_recap_exploration_inclut_le_sac_du_pj() -> None:
    """Le récap MINIMAL (utilisé en exploration) doit lister le contenu du
    sac : sinon le MJ nie des objets pourtant portés (bug « tu n'as pas de
    fiole de guérison » alors que le PJ en a 4)."""
    from server.config import load_config
    from server.game.state import PartyState
    from server.llm.prompt_builder import PromptBuilder

    tmp = tempfile.mkdtemp()
    fiche = dict(FICHE_GROTH)
    fiche["inventaire"] = [{"nom": "fiole de guérison", "qte": 4}]
    _ecrire_fiche(tmp, fiche)
    etat = {
        "meta": {"titre": "T"},
        "phase": "exploration",
        "pj": [{"nom": "Groth", "race": "Nain", "classe": "Guerrier",
                "niveau": 1, "pv": 8, "pv_max": 10, "ca": 15,
                "conditions": []}],
        "quete": {"titre": "Q", "pitch": "p"},
    }
    PartyState(data_dir=tmp, partie_id="t_sac").save(etat)
    cfg = load_config()
    cfg = replace_paths(cfg, tmp)
    recap = PromptBuilder(cfg).build_recap(
        PartyState(data_dir=tmp, partie_id="t_sac").load()
    )
    assert "fiole de guérison ×4" in recap
    assert "contenu OFFICIEL de l'inventaire" in recap
    # PV/CA utiles au MJ, eux aussi absents de l'entrée `pj` détaillée.
    assert "PV 8/10" in recap and "CA 15" in recap


def replace_paths(cfg, data_dir: str):
    from dataclasses import replace
    from server.config import PathsConfig
    return replace(cfg, paths=PathsConfig(
        data_dir=data_dir,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))


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
