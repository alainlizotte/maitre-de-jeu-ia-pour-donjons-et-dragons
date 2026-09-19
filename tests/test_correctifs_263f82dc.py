"""Correctifs partie 263f82dc (analyse post-session) — 8 points.

1. PV de héros inventés par la narration (« 4 sur 16 » pour 8/15 réels)
   → patterns `_PJ_DEGATS_PROSE_PATTERNS` étendus + PV officiels injectés.
2. Bonus de dégâts incohérents (+4 puis +6 pour le même attaquant)
   → `lancer_attaque` affiche le bonus officiel à recopier.
3. Affichage ennemi « PV 3/3 — ☠️ DÉTRUIT » + résurrections fantômes
   → `_exces_degats_monstres` aligne les clés sur le monstre réellement
   touché et ne restaure jamais au-delà des dégâts jetés.
4. Cible détruite attaquable → redirection auto vers une cible vivante.
5. Narration désynchronisée du plateau → état factuel injecté dans
   `_narrer_mecaniques_serveur` (`_resume_plateau`).
6. Paragraphe de rencontre re-collé à chaque round → écho détecté au
   niveau paragraphe dans `trouve_repetition`.
7. État ↔ fiche désynchronisés → `etat_partie_patch("pj.0.pv", …)` met à
   jour aussi la fiche.
8. Événement d'histoire tronqué en pleine phrase → coupe propre.

Usage : py -m pytest tests/test_correctifs_263f82dc.py -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.client import Message  # noqa: E402
from server.llm.orchestrator import (  # noqa: E402
    _PJ_DEGATS_PROSE_PATTERNS,
    looks_like_simulation,
    trouve_repetition,
)
from server.tools.base import ToolContext  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools("server.tools")

from server.main import (  # noqa: E402  (après sys.path)
    _coupe_narration_evenement,
    _exces_degats_monstres,
    _resume_plateau,
)


# --------------------------------------------------------------------------- #
# P1 — PV de héros inventés détectés comme simulation
# --------------------------------------------------------------------------- #
def test_p1_vitalite_baissante_sur_16_detectee():
    texte = (
        "Sa griffe vous entaille. Le sang coule, votre vitalité "
        "baissant à **4 sur 16**, alors que la douleur vous rappelle "
        "la fragilité de votre existence."
    )
    assert looks_like_simulation(texte) is not None


def test_p1_vitalite_reduite_en_lettres_detectee():
    texte = (
        "Sa griffe trouve sa cible avec une efficacité terrifiante, "
        "réduisant votre vitalité à douze."
    )
    assert looks_like_simulation(texte) is not None


def test_p1_vie_chute_a_zero_detectee():
    texte = "Le coup de grâce porte : votre vie chute à 0."
    assert looks_like_simulation(texte) is not None


def test_p1_faux_positifs_epargnes():
    # Mesures et déplacements ne sont PAS des PV.
    assert looks_like_simulation(
        "Vous tombez à 3 mètres plus bas, sans mal.") is None
    assert looks_like_simulation(
        "La corde mesure douze mètres de long.") is None
    # « vos PV tombent » dans une reformulation légitime reste détecté
    # (le garde est désactivé par trust_damage_prose côté run()).
    assert looks_like_simulation(
        "Vos PV baissent après le choc.") is not None


def test_p1_patterns_liste_complet():
    assert any(
        p.search("votre vitalité baissant à 4 sur 16")
        for p in _PJ_DEGATS_PROSE_PATTERNS
    )


# --------------------------------------------------------------------------- #
# P3 — excès de dégâts : clés alignées, pas de résurrection fantôme
# --------------------------------------------------------------------------- #
def _monstres_263() -> list[dict]:
    return [
        {"nom": "Squelette", "pv": -4, "pv_max": 3,
         "ca": 15, "conditions": ["Détruit"]},
        {"nom": "Squelette (2)", "pv": 3, "pv_max": 3,
         "ca": 15, "conditions": []},
        {"nom": "Squelette (3)", "pv": 3, "pv_max": 3,
         "ca": 15, "conditions": []},
    ]


def test_p3_label_llm_prefixe_pas_de_faux_exces():
    # lancer_degats cible « Squelette » (label LLM) mais le tool a frappé
    # « Squelette (2) » (résolution préfixe, premier vivant) : appliqué ==
    # jeté sur (2) → AUCUN excès, surtout pas sur le cadavre « Squelette ».
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Squelette"},
         "text": "- **Dégâts infligés : 15**"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Squelette", "degats": 15},
         "text": "💥 Squelette (2) (monstre) subit 15 dégâts → PV -12/3"},
    ]
    exces = _exces_degats_monstres(trace, _monstres_263())
    assert exces == {}


def test_p3_double_application_detectee_avec_jetés():
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Squelette (3)"},
         "text": "- **Dégâts infligés : 11**"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Squelette (3)", "degats": 11},
         "text": "💥 Squelette (3) (monstre) subit 11 dégâts → PV -8/3"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Squelette (3)", "degats": 11},
         "text": "💥 Squelette (3) (monstre) subit 11 dégâts → PV -8/3"},
    ]
    exces = _exces_degats_monstres(trace, _monstres_263())
    assert exces["Squelette (3)"]["exces"] == 11
    # Plafond de restauration : pv_max - jetés = 3 - 11 = -8 → la
    # restauration ne peut PAS faire repasser au-dessus de 0.
    info = exces["Squelette (3)"]
    pv = -8 - 11  # état après la 2e application (double)
    plafond = info and (3 - info["jetes"])
    assert min(pv + info["exces"], plafond) <= 0


def test_p3_cadavre_ne_ressuscite_pas():
    # Un infliger en excès sur un monstre DÉTRUIT avant le tour ne le
    # ramène pas à la vie : pv_max - jetés borne la restauration sous 0.
    mons = [{"nom": "Squelette", "pv": -15, "pv_max": 3,
             "conditions": ["Détruit"]}]
    trace = [
        {"name": "lancer_degats", "ok": True,
         "args": {"cible": "Squelette"},
         "text": "- **Dégâts infligés : 7**"},
        # Le LLM a appliqué 7 (jet) PUIS 18 narrés à la main.
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Squelette", "degats": 7},
         "text": "💥 Squelette (monstre) subit 7 dégâts → PV -11/3"},
        {"name": "fiche_perso_infliger_degats", "ok": True,
         "args": {"nom": "Squelette", "degats": 18},
         "text": "💥 Squelette (monstre) subit 18 dégâts → PV -29/3"},
    ]
    exces = _exces_degats_monstres(trace, mons)
    info = exces["Squelette"]
    assert info["exces"] == 18
    # Restauration : -29 + 18 = -11, plafonné à 3 - 7 = -4 → ≤ 0.
    assert min(-29 + info["exces"], 3 - info["jetes"]) <= 0


# --------------------------------------------------------------------------- #
# P4 — cible détruite : redirection vers une cible vivante
# --------------------------------------------------------------------------- #
def test_p4_attaque_cible_detruite_redirigee(tmp_path):
    from server.game.state import PartyState
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263")
    etat = {
        "phase": "combat",
        "pj": [{"nom": "Utturgut", "pv": 8, "pv_max": 15}],
        "monstres_combat": _monstres_263(),
    }
    st.save(etat)
    ctx = ToolContext(partie_id="p263", joueur="alain", data_dir=data_dir)
    from server.tools.dice import lancer_attaque
    tr = asyncio.run(lancer_attaque.handler(ctx, **{
        "bonus_attaque": 5, "ca_cible": 15, "nom_attaquant": "Utturgut",
        "arme": "Hache à deux mains", "nom_cible": "Squelette",
    }))
    txt = tr.text
    assert "DÉTRUITE" in txt and "redirigée" in txt
    assert "vs Squelette (2)" in txt


def test_p4_attaque_cible_vivante_inchangee(tmp_path):
    from server.game.state import PartyState
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263b")
    st.save({
        "phase": "combat",
        "pj": [{"nom": "Utturgut", "pv": 8, "pv_max": 15}],
        "monstres_combat": _monstres_263(),
    })
    ctx = ToolContext(partie_id="p263b", joueur="alain", data_dir=data_dir)
    from server.tools.dice import lancer_attaque
    tr = asyncio.run(lancer_attaque.handler(ctx, **{
        "bonus_attaque": 5, "ca_cible": 15, "nom_attaquant": "Utturgut",
        "arme": "Hache à deux mains", "nom_cible": "Squelette (3)",
    }))
    assert "DÉTRUITE" not in tr.text
    assert "vs Squelette (3)" in tr.text


# --------------------------------------------------------------------------- #
# P2 — bonus de dégâts officiel affiché
# --------------------------------------------------------------------------- #
def test_p2_bonus_degats_officiel_deux_mains(tmp_path):
    from server.game.state import PartyState
    from server.tools.fiches import _chemin
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263c")
    st.save({"phase": "exploration", "pj": [], "monstres_combat": []})
    ctx = ToolContext(partie_id="p263c", joueur="alain", data_dir=data_dir)
    fiche = {
        "nom": "Utturgut", "niveau": 1, "bab": 1,
        "carac": {"FOR": 19, "DEX": 10, "CON": 11, "INT": 9,
                  "SAG": 12, "CHA": 9},
        "pv": 15, "pv_max": 15, "ca": 14,
        "equipement": [{"nom": "Hache à deux mains", "qte": 1}],
    }
    with open(_chemin(ctx, "Utturgut"), "w", encoding="utf-8") as f:
        import json
        json.dump(fiche, f, ensure_ascii=False)
    from server.tools.dice import lancer_attaque
    tr = asyncio.run(lancer_attaque.handler(ctx, **{
        "bonus_attaque": 4, "ca_cible": 15, "nom_attaquant": "Utturgut",
        "arme": "Hache à deux mains", "nom_cible": "Squelette (2)",
    }))
    # FOR 19 → +4 ; à deux mains ×1,5 → +6 affiché officiellement.
    assert "Bonus dégâts officiel : +6" in tr.text
    assert "×1,5" in tr.text


def test_p2_bonus_degats_officiel_distance_nul(tmp_path):
    from server.game.state import PartyState
    from server.tools.fiches import _chemin
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263d")
    st.save({"phase": "exploration", "pj": [], "monstres_combat": []})
    ctx = ToolContext(partie_id="p263d", joueur="alain", data_dir=data_dir)
    fiche = {
        "nom": "Archer", "niveau": 1, "bab": 1,
        "carac": {"FOR": 14, "DEX": 18, "CON": 10, "INT": 10,
                  "SAG": 10, "CHA": 10},
        "pv": 8, "pv_max": 8, "ca": 14,
    }
    with open(_chemin(ctx, "Archer"), "w", encoding="utf-8") as f:
        import json
        json.dump(fiche, f, ensure_ascii=False)
    from server.tools.dice import lancer_attaque
    tr = asyncio.run(lancer_attaque.handler(ctx, **{
        "bonus_attaque": 4, "ca_cible": 13, "nom_attaquant": "Archer",
        "arme": "Arc long", "nom_cible": "Cible",
    }))
    assert "Bonus dégâts officiel : +0" in tr.text


# --------------------------------------------------------------------------- #
# P5 — état du plateau pour la narration des mécaniques
# --------------------------------------------------------------------------- #
def test_p5_resume_plateau_marque_detruits_et_pv():
    etat = {
        "pj": [{"nom": "Utturgut", "pv": 8, "pv_max": 15,
                "conditions": []}],
        "monstres_combat": [
            {"nom": "Squelette (3)", "pv": -8, "pv_max": 3,
             "conditions": ["Détruit"]},
            {"nom": "Squelette (4)", "pv": 3, "pv_max": 3,
             "conditions": []},
        ],
    }
    txt = _resume_plateau(etat)
    assert "Utturgut : 8/15 PV" in txt
    assert "☠️ DÉTRUIT" in txt
    assert "Squelette (4) : 3/3 PV — vivant" in txt


# --------------------------------------------------------------------------- #
# P6 — écho au niveau paragraphe
# --------------------------------------------------------------------------- #
_RENCONTRE = (
    "Mais avant que vous puissiez vous reposer, un second squelette "
    "surgit des ombres, suivi d'un troisième, d'un quatrième et d'un "
    "cinquième. Ils semblent attendre votre attention, leurs regards "
    "vides se fixant sur vous avec une menace silencieuse."
)


def test_p6_paragraphe_recycle_detecte():
    ancien = (
        "Votre hache s'abat sur le premier ennemi. Le choc résonne.\n\n"
        + _RENCONTRE
        + "\n\nQue faites-vous ?"
    )
    nouveau = (
        "Le squelette s'effondre en poussière blanche. Vous reprenez "
        "votre souffle au milieu de la grotte froide.\n\n"
        + _RENCONTRE
        + "\n\nLe combat continue."
    )
    hist = [Message(role="assistant", content=ancien)]
    assert trouve_repetition(nouveau, hist) is not None


def test_p6_narration_fraiche_non_detectee():
    ancien = (
        "Votre hache s'abat sur le premier ennemi. Le choc résonne.\n\n"
        + _RENCONTRE
    )
    nouveau = (
        "Vous fouillez les ossements et trouvez une petite clef de "
        "laiton gravée d'un soleil. Au fond de la grotte, un couloir "
        "descend vers une porte de pierre sculptée de runes anciennes "
        "qui semblent pulser faiblement d'une lueur bleutée."
    )
    hist = [Message(role="assistant", content=ancien)]
    assert trouve_repetition(nouveau, hist) is None


# --------------------------------------------------------------------------- #
# P7 — patch pj.* répercuté sur la fiche
# --------------------------------------------------------------------------- #
def test_p7_patch_pv_miroité_sur_fiche(tmp_path):
    from server.game.state import PartyState
    from server.tools.fiches import _chemin
    import json
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263e")
    st.save({"phase": "exploration",
             "pj": [{"nom": "Utturgut", "pv": 1, "pv_max": 15}],
             "monstres_combat": []})
    ctx = ToolContext(partie_id="p263e", joueur="alain", data_dir=data_dir)
    with open(_chemin(ctx, "Utturgut"), "w", encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 15, "pv_max": 15}, f,
                  ensure_ascii=False)
    from server.tools.state import etat_partie_patch
    tr = asyncio.run(etat_partie_patch.handler(ctx, chemin="pj.0.pv",
                                               valeur="12"))
    assert "❌" not in tr.text
    fiche = json.load(open(_chemin(ctx, "Utturgut"), encoding="utf-8"))
    assert fiche["pv"] == 12


def test_p7_patch_hors_champ_mecanique_ne_touche_pas_la_fiche(tmp_path):
    from server.game.state import PartyState
    from server.tools.fiches import _chemin
    import json
    data_dir = str(tmp_path)
    st = PartyState(data_dir=data_dir, partie_id="p263f")
    st.save({"phase": "exploration",
             "pj": [{"nom": "Utturgut", "pv": 10, "pv_max": 15}],
             "monstres_combat": []})
    ctx = ToolContext(partie_id="p263f", joueur="alain", data_dir=data_dir)
    with open(_chemin(ctx, "Utturgut"), "w", encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 10, "pv_max": 15}, f,
                  ensure_ascii=False)
    from server.tools.state import etat_partie_patch
    asyncio.run(etat_partie_patch.handler(ctx, chemin="pj.0.histoire",
                                          valeur="Il fut un temps…"))
    fiche = json.load(open(_chemin(ctx, "Utturgut"), encoding="utf-8"))
    assert "histoire" not in fiche


# --------------------------------------------------------------------------- #
# P8 — coupe propre de l'événement d'ouverture
# --------------------------------------------------------------------------- #
def test_p8_coupe_sur_fin_de_phrase():
    texte = (
        "L'air frais de Silverymoon vous enveloppe. Le vent porte les "
        "murmures des marchands et les cris des enfants de la ville "
        "libre, mais ici, dans la cour intérieure, règne une atmosphère "
        "plus solennelle. Magister Thukmuul Teleshann vous attend sur "
        "le porche de l'entrée principale, son manteau violet flottant "
        "légèrement dans le courant d'air glacial de la matinée."
    )
    out = _coupe_narration_evenement(texte, 300)
    assert len(out) <= 300
    assert out.rstrip().endswith((".", "!", "?", "…"))
    assert not out.rstrip().endswith("dans la")


def test_p8_texte_court_inchange():
    assert _coupe_narration_evenement("Court récit. Voilà tout.") == (
        "Court récit. Voilà tout.")
