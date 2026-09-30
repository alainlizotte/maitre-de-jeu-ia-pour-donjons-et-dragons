"""AUDIT — Les compétences des PJ sont-elles RÉELLEMENT effectives en jeu ?

Ce test ne vérifie pas des intentions, il mesure ce que le moteur fait
réellement. Chaque test est un verdict, pas une intention.

    py -m pytest tests/test_audit_competences_effectives.py -v
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

from server.llm.orchestrator import _PHASE_TOOLS                    # noqa: E402
from server.persos import CAPACITES_RACES                            # noqa: E402
from server.tools.base import ToolContext                             # noqa: E402
from server.tools.dice import lancer_d20                              # noqa: E402
from server.tools.fiches import _slug                                # noqa: E402
from server.tools.registry import tools_prompt_compact                # noqa: E402


def _ctx(tmp: str) -> ToolContext:
    return ToolContext(partie_id="t", joueur="Test", data_dir=tmp)


def _fiche(tmp: str, fiche: dict) -> str:
    d = Path(tmp) / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"fiche_{_slug(fiche['nom'])}.json"
    p.write_text(json.dumps(fiche, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _jet(tmp, **kw) -> str:
    return asyncio.run(lancer_d20(_ctx(tmp), **kw)).text


# --------------------------------------------------------------------------- #
#  1. Le cœur : le rang + le modificateur de carac sont-ils recoupés ?
# --------------------------------------------------------------------------- #
def test_1_le_rang_de_competence_est_bien_recoupe():
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Aurel", "race": "Humain", "classe": "Roublard",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 18, "CON": 12,
                                   "INT": 14, "SAG": 13, "CHA": 8},
            "competences": {"Discrétion": 5, "Escalade": 2},
        })
        # Le LLM annonce 0 (comportement documenté des petits modèles).
        t = _jet(tmp, modificateur=0, raison="Crochetage", difficulte=15,
                 nom_personnage="Aurel", competence="Discrétion")
        # DEX 18 → +4 ; 5 rangs → attendu +9
        assert "Modificateur recalculé +0 → +9" in t, t
        assert "+9" in t, t


# --------------------------------------------------------------------------- #
#  2. Compétence hors catalogue → modificateur DEX SILENT (bug ?)
# --------------------------------------------------------------------------- #
def test_2_nom_hors_catalogue_ne_bloque_pas():
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Brenn", "race": "Humain", "classe": "Barde",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 18, "CON": 12,
                                   "INT": 12, "SAG": 10, "CHA": 16},
            "competences": {"Croquetage": 4},   # nom INVENTÉ par le MJ
        })
        t = _jet(tmp, modificateur=0, raison="Crochetage", difficulte=15,
                 nom_personnage="Brenn", competence="Croquetage")
        # Le rang 4 est trouvé, mais la carac retombe sur DEX en silence.
        print("\n[hors catalogue] " + t)
        assert "recalculé" in t, t
        # DEX 18 → +4, +4 rangs = +8. Aucun signal que la carac est fausse.
        assert "8" in t, t


def test_2b_catalogue_connu_mais_pas_de_cle_dans_la_fiche():
    """Compétence du catalogue, PJ à 0 rang : le modificateur LLM passe tel quel."""
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Cyril", "race": "Humain", "classe": "Roublard",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 18, "CON": 12,
                                   "INT": 14, "SAG": 13, "CHA": 8},
            "competences": {"Discrétion": 5},
        })
        t = _jet(tmp, modificateur=0, raison="Esquiver une vague",
                 nom_personnage="Cyril", competence="Acrobaties")
        print("\n[0 rang, LLM dit 0] " + t)
        # Sans rangs ni don, AUCUN recoupement : le 0 du LLM est conservé.
        assert "Modificateur recalculé" not in t, t
        assert "Modificateur : +0" in t, t


# --------------------------------------------------------------------------- #
#  3. Les bonus de RACE annoncés sont-ils appliqués ?
# --------------------------------------------------------------------------- #
def test_3_bonus_de_race_appliques():
    """Un elfe doit avoir +2 Détection de race, même à rang nul.

    Comportement ACTUEL : Détection est HORS classe du Roublard → le rang
    compte pour moitié (RAW 3.5 : 1 rang saisi = ½ rang) — le total attendu
    est 0 (½ de 1) + 1 (SAG 13) + 2 (Sens aiguisés) = +3, la note doit
    porter les DEUX mentions (hors-classe et race)."""
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Sylve", "race": "Elfe", "classe": "Roublard",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 14, "CON": 12,
                                   "INT": 12, "SAG": 13, "CHA": 10},
            "competences": {"Détection": 1},
        })
        t = _jet(tmp, modificateur=0, raison="Écouter",
                 nom_personnage="Sylve", competence="Détection")
        print("\n[elfe, 1 rang Détection (hors classe), SAG 13] " + t)
        attendu = 0 + (13 - 10) // 2 + 2
        assert "recalculé" in t, t
        assert f"→ {attendu:+d}" in t, f"bonus de race NON appliqué (attendu {attendu:+d})\n{t}"
        assert "hors classe" in t, "le facteur hors-classe doit être signalé"
        assert "race/familier" in t, "le bonus de race doit être signalé"


# --------------------------------------------------------------------------- #
#  4. Les facultés de FAMILIER sont-elles appliquées ?
# --------------------------------------------------------------------------- #
def test_4_faculte_de_familier_appliquee():
    """Le familier « Chat » accorde +3 Déplacement silencieux au maître
    (table PHB 3.5 du projet, server/familiers.py) : doit se voir dans le
    modificateur. Le test initial citait « Discrétion » — une AUTRE
    compétence (Move Silently ≠ Stealth) : adapté à la table officielle."""
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Ned", "race": "Humain", "classe": "Roublard",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 14, "CON": 12,
                                   "INT": 12, "SAG": 13, "CHA": 10},
            "competences": {"Déplacement silencieux": 3},
            "familier": "Chat",
        })
        t = _jet(tmp, modificateur=0, raison="Se glisser dans la foule",
                 nom_personnage="Ned", competence="Déplacement silencieux")
        print("\n[chat attendu +3 deplacement silencieux] " + t)
        attendu = 3 + (14 - 10) // 2 + 3
        assert f"→ {attendu:+d}" in t, f"faculte de familier NON appliquee ({attendu:+d})\n{t}"
        assert "race/familier" in t, "le bonus du familier doit être signalé"


# --------------------------------------------------------------------------- #
#  5. Le routage dit-il au MJ d'utiliser lancer_d20 ?
# --------------------------------------------------------------------------- #
def test_5_lancer_d20_dans_le_routage():
    from server.tools.registry import discover_tools
    bloc = tools_prompt_compact(discover_tools())
    # On isole la section « ### Routage : » (jusqu'à « Tools disponibles »).
    routage = bloc.split("### Routage", 1)[1].split("Tools disponibles", 1)[0]
    lignes = [l for l in routage.splitlines()
              if "lancer_d20" in l and "competence" in l.lower()]
    print("\n[routage — lignes competence]")
    for l in lignes:
        print("   " + l)
    assert lignes, (
        "aucune ligne de routage nassocie un test de competence a lancer_d20 : "
        "le prompt court ne dit jamais au MJ de l'utiliser pour un jet de "
        "competence (il n'est cite que comme substitut INTERDIT a lancer_attaque)"
    )


def test_5b_lancer_d20_cite_comme_substitut_interdit():
    from server.tools.registry import discover_tools
    bloc = tools_prompt_compact(discover_tools())
    cites = [l for l in bloc.splitlines() if "lancer_d20" in l]
    print("\n[la seule facon dont lancer_d20 apparait dans le prompt court]")
    for l in cites:
        print("   " + l)
    for l in cites:
        if l.lstrip().startswith("-"):
            assert "interdit" in l.lower() or "pas lancer_d20" in l.lower(), (
                "ligne de routage inattendue pour lancer_d20 : " + l
            )


# --------------------------------------------------------------------------- #
#  6. lancer_d20 est-il disponible en phase COMBAT ?
# --------------------------------------------------------------------------- #
def test_6_lancer_d20_disponible_en_combat():
    assert "lancer_d20" in _PHASE_TOOLS["combat"], (
        "lancer_d20 absent de la phase combat : aucun jet de competence "
        "possible pendant une rencontre"
    )


# --------------------------------------------------------------------------- #
#  7. Le facteur hors-classe x0,5 est-il applique ?
# --------------------------------------------------------------------------- #
def test_7_facteur_hors_classe_non_applique():
    with tempfile.TemporaryDirectory() as tmp:
        # Voleur (classe: Roublard) plaçant 4 rangs en Natation (hors classe)
        _fiche(tmp, {
            "nom": "Filou", "race": "Humain", "classe": "Roublard",
            "niveau": 3, "carac": {"FOR": 10, "DEX": 14, "CON": 12,
                                   "INT": 12, "SAG": 13, "CHA": 10},
            "competences": {"Natation": 4},
        })
        t = _jet(tmp, modificateur=0, raison="Traverser la riviere",
                 nom_personnage="Filou", competence="Natation")
        print("\n[hors classe: 4 rangs, FOR 10 → attendu +2, obtenu +4] " + t)
        # 4 rangs + 0 (FOR 10) = +4 au lieu de +2 (4//2)
        assert "→ +2" in t, f"facteur hors-classe x0,5 NON applique\n{t}"


# --------------------------------------------------------------------------- #
#  8. La concentration (compétence de 7 classes) est-elle jamais jouée ?
# --------------------------------------------------------------------------- #
def test_8_concentration_jetee_ou_simplement_affichee():
    """Un sort à concentration doit faire lancer un jet de Concentration.

    today: le DD est calcule et affiche, aucun de n'est lance.
    attendu: le tool incanter_sort renvoie un jet de des effectif.
    """
    from server.tools.sorts import incanter_sort
    from server.sorts import SORTS
    cible = min(
        (s for s in SORTS
         if s.get("sauvegarde") and "concentration" in (s.get("duree") or "")),
        key=lambda s: int(s.get("niveau", 9) or 9),
    )
    niv_sort = int(cible.get("niveau", 1) or 1)
    with tempfile.TemporaryDirectory() as tmp:
        _fiche(tmp, {
            "nom": "Ilyana", "race": "Humain", "classe": "Sorcier",
            "niveau": 17, "carac": {"FOR": 8, "DEX": 14, "CON": 12,
                                                   "INT": 18, "SAG": 11, "CHA": 10},
            "competences": {"Concentration": 5},
            "sorts_known": [cible["nom"]],
        })
        r = asyncio.run(incanter_sort(
            _ctx(tmp),
            nom_personnage="Ilyana",
            nom_sort=cible["nom"],
        ))
        t = getattr(r, "text", str(r))
        print(f"\n[concentration — sort {cible['nom']!r}] " + t)
        assert "Concentration 5 rangs" in t or "Jet brut" in t, (
            "le DD de concentration est affiche mais AUCUN de n'est lance : "
            "la competence Concentration (7 classes sur 11) est inapplicable"
        )


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-s"]))
