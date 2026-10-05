# -*- coding: utf-8 -*-
"""Régressions de la partie b59b4a9a (test navigateur complet 2026-10-05).

Partie de test complète sur Crown of Mystra après les correctifs d'ancrage
d'intro (commit b358502). L'intro est désormais PARFAITE (décor canonique,
Teleshann femme, Margoth non fusionné), la carte avance via les outils, mais
trois fuites mécaniques sont apparues :

1. **Remise d'objets manquée à l'intro** : « Thukmuul SORT de ses vêtements
   une fiole de vérité et un parchemin de la route. Elle DÉPOSE les objets
   dans vos mains. » — ni « sort » ni « dépose » n'étaient des verbes de
   remise et « les objets » (anaphore) ne nommait rien → inventaire de
   quête VIDE après l'intro.
2. **Embuscade canonique esquivée** : la salle (2,0) déclare `ennemis:
   Perceur ×6` ; le modèle a narré l'attaque SANS nommer la créature
   (« des pierres tombent du plafond, frappant Margoth avec violence ») puis
   l'a RÉSOLUE en prose (« désintégrés par une attaque magique inattendue »)
   sans `engager_combat`, sans dés, avec une magie inventée (le PJ barbare).
3. **D0 aveugle aux actions composées** : « Je demande aux myconides…, puis
   j'explore la galerie vers l'est » — la décision a répondu « narrer », le
   déplacement a été narré sans `carte_donjon_explorer` (carte figée en
   (1,0)) et une porte a été inventée.

Correctifs (server/llm/orchestrator.py) :
- `_REMISE_VERBE_RE` + anaphore inter-phrases dans `_objets_remettes_narration` ;
- `_ennemis_salle_courante` : repli sur les `ennemis` du manifeste de la
  salle courante quand l'attaque est narrée sans nom de créature (hors
  ennemis déjà vaincus — mémoire de campagne) ; `_NARRATION_ATTAQUE_RE`
  étendu (embuscade, chute du plafond, frappe violente) ;
- `_ACTION_DEPLACEMENT_RE` étendu (j'explore, je pars, j'emprunte,
  je prends la route, on/nous avançons).

Usage : py -m pytest tests/test_correctifs_b59b4a9a.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (            # noqa: E402
    _NARRATION_ATTAQUE_RE,
    _action_deplacement_dans_donjon,
    _ennemis_salle_courante,
    _objets_remettes_narration,
)

PID = "test_b59b4a9a"


# --------------------------------------------------------------------------- #
#  1. Remises d'objets : verbes de tirage + anaphore
# --------------------------------------------------------------------------- #

def test_remise_verbes_tirage_et_anaphore():
    """« sort de ses vêtements… / dépose les objets » → fiole + parchemin."""
    objets = _objets_remettes_narration(
        "Thukmuul sort de ses vêtements une fiole de vérité et un parchemin "
        "de la route. Elle dépose les objets dans vos mains.")
    cles = [o.lower() for o in objets]
    assert any("fiole" in c for c in cles), objets
    assert any("parchemin" in c for c in cles), objets


def test_remise_pas_de_faux_positif_sort_sortilege():
    """Le NOM « un sort » (sortilège) ne déclenche pas le verbe, et une
    simple mention de la Couronne n'est pas rapatriée par l'anaphore."""
    assert _objets_remettes_narration(
        "La Couronne est entre les mains de Nulentok. Il lance un sort de "
        "feu puis s'avance. Elle dépose les objets sur la table.") == []


def test_remise_anaphore_exige_verbe_precedent():
    """L'anaphore ne tire les noms que si la phrase PRÉCÉDENTE portait un
    verbe de remise/tirage (sinon mention isolée → ajout fantôme)."""
    # Mention SANS verbe puis « passe » isolé : rien ne doit être ajouté.
    assert _objets_remettes_narration(
        "La clé de fer brille sur l'autel. Il passe la porte.") == []


# --------------------------------------------------------------------------- #
#  2. Embuscade canonique de la salle (ennemis du manifeste)
# --------------------------------------------------------------------------- #

def _etat_b59b4a9a(courant=(2, 0), memoire_battus=None) -> dict:
    """État minimal : donjon Crown, salle (2,0) « Perceur ×6 », porte est."""
    donjon = {
        "id": "La Couronne de Mystra",
        "courant": list(courant),
        "grille": [
            {"x": courant[0], "y": courant[1], "type": "couloir",
             "visitee": True, "portes": {"est": True, "ouest": True},
             "ennemis": ["Perceur ×6 (pendus au plafond — tombent en "
                         "surprise sur la proie)"]},
            {"x": courant[0] + 1, "y": courant[1], "type": "puits",
             "portes": {"ouest": True}},
        ],
    }
    etat = {
        "phase": "combat" if False else "exploration",
        "donjon": donjon,
        "pj": [{"nom": "Margoth"}],
        "memoire": {"monstres_combattus": memoire_battus or []},
    }
    return etat


class _Ctx:
    """Contexte minimal (partie_id/data_dir) pour PartyState."""

    def __init__(self, data_dir: str, partie_id: str = PID):
        self.data_dir = data_dir
        self.partie_id = partie_id


def test_attaque_sans_nom_perceur_engage_salle(tmp_path):
    """Narration d'attaque SANS le mot « perceur » dans la salle qui déclare
    Perceur ×6 → l'engagement forcé utilise les ennemis du manifeste."""
    (tmp_path / f"partie_{PID}.json").write_text(
        json.dumps(_etat_b59b4a9a(), ensure_ascii=False), encoding="utf-8")
    narration = (
        "Soudain, une présence menaçante au-dessus. Des pierres tombent du "
        "plafond, frappant Margoth avec violence. Les pierres continuent de "
        "tomber, chacune causant des dégâts.")
    monstres = _ennemis_salle_courante(narration, _Ctx(str(tmp_path)))
    assert monstres, "l'embuscade canonique doit être engagée"
    parties = [m.strip() for m in monstres.split(",")]
    assert len(parties) == 6, monstres
    assert all(p.lower().startswith("perceur") for p in parties), monstres


def test_attaque_salle_sans_ennemis_rien(tmp_path):
    """Salle SANS `ennemis` déclarés : pas d'engagement fabriqué."""
    etat = _etat_b59b4a9a()
    etat["donjon"]["grille"][0].pop("ennemis")
    (tmp_path / f"partie_{PID}.json").write_text(
        json.dumps(etat, ensure_ascii=False), encoding="utf-8")
    assert _ennemis_salle_courante(
        "Des pierres tombent du plafond, frappant Margoth avec violence.",
        _Ctx(str(tmp_path))) is None


def test_ennemis_deja_vaincus_pas_de_re_engagement(tmp_path):
    """Perceurs déjà vaincus (mémoire de campagne) → pas de re-engagement."""
    etat = _etat_b59b4a9a(memoire_battus=[{"noms": ["Perceur"]}])
    (tmp_path / f"partie_{PID}.json").write_text(
        json.dumps(etat, ensure_ascii=False), encoding="utf-8")
    assert _ennemis_salle_courante(
        "Des pierres tombent du plafond, frappant Margoth avec violence.",
        _Ctx(str(tmp_path))) is None


def test_motifs_attaque_etendus():
    """Les nouvelles formes d'embuscade déclenchent le garde-fou."""
    for phrase in (
        "Une embuscade vous attend.",
        "Des pierres tombent du plafond.",
        "frappant Margoth avec violence",
        "Des fléchettes s'abattent sur le groupe.",
        "Prises sous coups, les héros reculent.",
    ):
        assert _NARRATION_ATTAQUE_RE.search(phrase), phrase


def test_narration_pacifique_pas_d_engagement(tmp_path):
    """Une narration SANS attaque (fouille, dialogue) n'engage rien, même
    dans une salle à ennemis."""
    (tmp_path / f"partie_{PID}.json").write_text(
        json.dumps(_etat_b59b4a9a(), ensure_ascii=False), encoding="utf-8")
    assert _ennemis_salle_courante(
        "Margoth fouille les champignons luminescents et écoute les "
        "myconides.", _Ctx(str(tmp_path))) is None


# --------------------------------------------------------------------------- #
#  3. D0 : actions composées et départs
# --------------------------------------------------------------------------- #

def test_d0_actions_composees_et_departs():
    """« …, puis j'explore la galerie vers l'est » doit réclamer un outil de
    déplacement comme « j'avance »."""
    etat = _etat_b59b4a9a(courant=(1, 0))
    for action in (
        "Je demande aux myconides ce qui leur est arrivé, puis j'explore la "
        "galerie vers l'est",
        "Je pars de Silverymoon et je prends la route vers la grotte",
        "J'emprunte la porte est",
        "Je prends le sentier qui descend",
        "On avance prudemment dans le couloir",
        "Nous avançons vers la sortie",
    ):
        assert _action_deplacement_dans_donjon(action, etat), action


def test_d0_roleplay_pur_ne_declenche_pas():
    """Une action SANS déplacement (dialogue, fouille) ne force pas l'outil."""
    etat = _etat_b59b4a9a(courant=(1, 0))
    for action in (
        "Je demande aux myconides ce qui leur est arrivé",
        "Je fouille les champignons et je ramasse un éclat",
        "Qui êtes-vous ? racontez-moi Nulentok",
    ):
        assert not _action_deplacement_dans_donjon(action, etat), action


# --------------------------------------------------------------------------- #
#  4. Inventaire : fusion des libellés abrégés d'objets de quête
# --------------------------------------------------------------------------- #

def test_inventaire_fusion_libelle_abrege_quete(tmp_path):
    """« la fiole » ajouté alors que « la fiole de vérité » existe (même
    partie, portée quête) → PAS de doublon : fusion vers le libellé long."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    data = tmp_path / "data"
    (data / "fiches").mkdir(parents=True)
    fiche = {
        "nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
        "niveau": 1, "xp": 0, "pv": 17, "pv_max": 17,
        "inventaire": [
            {"nom": "la fiole de vérité", "qte": 1, "poids": 0.1,
             "portee": "quete", "partie": PID},
        ],
    }
    (data / "fiches" / "fiche_margoth.json").write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")
    ctx = ToolContext(partie_id=PID, joueur="Alain", data_dir=str(data))
    tools = discover_tools()

    res = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Margoth", "objet": "la fiole", "portee": "quete"}))
    assert "fusion" in res.text.lower() or "DÉJÀ" in res.text, res.text
    fiche2 = json.loads(
        (data / "fiches" / "fiche_margoth.json").read_text(encoding="utf-8"))
    quete = [e for e in fiche2.get("inventaire", [])
             if e.get("portee") == "quete"]
    noms = [e.get("nom") for e in quete]
    assert noms == ["la fiole de vérité"], noms  # pas de « la fiole » en plus


def test_inventaire_prefixe_sans_collision_potions():
    """La fusion par préfixe ne touche PAS les potions (les synonymes
    normalisent déjà « fiole de soins légers » → « potion de soins legers »)."""
    from server.tools.inventaire import _cles_prefixe, _cle_objet
    assert not _cles_prefixe(
        _cle_objet("la fiole"), _cle_objet("une fiole de soins légers"))
    assert _cles_prefixe(
        _cle_objet("un parchemin"), _cle_objet("le parchemin de la route"))


# --------------------------------------------------------------------------- #
#  5. Anti-triche : un objet requis se gagne dans SA salle d'étape
# --------------------------------------------------------------------------- #

MANIFEST_COURONNE = {
    "id": "test_anti_triche_donjon",
    "scenario": ["test_anti_triche"],
    "donjon_id": "Le test anti-triche",
    "etages": [
        {"nom": "Étage 1", "entree": [0, 0], "salles": [
            {"x": 3, "y": 0, "type": "puits", "portes": {"est": True},
             "description": "La fosse."},
            {"x": 4, "y": 0, "type": "salle du trône", "portes": {"ouest": True},
             "description": "L'antre du boss."},
        ]},
    ],
    "etapes": [
        {"cle": "objet", "titre": "Reprendre le trophée chez le boss",
         "type": "objet", "salle": "4,0", "gate": True,
         "requis": [{"nom": "Le Trophée du boss", "portee": "quete"}]},
    ],
}


def _ctx_anti_triche(tmp_path, courant=(3, 0)):
    """data_dir complet : manifeste (étape salle 4,0), partie, fiche."""
    data = tmp_path / "data"
    (data / "scenarios" / "T").mkdir(parents=True)
    (data / "fiches").mkdir(parents=True)
    (data / "scenarios" / "T" / "anti_triche.donjon.json").write_text(
        json.dumps(MANIFEST_COURONNE, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (data / "partie_test_b59b4a9a.json").write_text(json.dumps({
        "phase": "exploration",
        "quete": {"source": "[test_anti_triche] /data/x.pdf",
                  "titre": "Test anti-triche"},
        "donjon": {"id": "Le test anti-triche", "courant": list(courant),
                   "grille": MANIFEST_COURONNE["etages"][0]["salles"]},
        "pj": [{"nom": "Margoth"}],
    }, ensure_ascii=False), encoding="utf-8")
    (data / "fiches" / "fiche_margoth.json").write_text(json.dumps({
        "nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
        "niveau": 1, "pv": 17, "pv_max": 17, "inventaire": [],
    }, ensure_ascii=False), encoding="utf-8")
    return data


def test_objet_requis_refuse_hors_sa_salle(tmp_path):
    """Le Trophée (requis, étape salle (4,0)) ajouté depuis le puits (3,0)
    → REFUS explicite (partie b59b4a9a : la Couronne s'ajoutait en (3,0))."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    data = _ctx_anti_triche(tmp_path, courant=(3, 0))
    ctx = ToolContext(partie_id="test_b59b4a9a", joueur="Alain",
                      data_dir=str(data))
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Margoth", "objet": "Le Trophée du boss",
         "portee": "quete"}))
    assert "ne peut pas être gagné ICI" in r.text, r.text
    assert "(4,0)" in r.text, r.text
    # Rien n'a été ajouté.
    fiche = json.loads(
        (data / "fiches" / "fiche_margoth.json").read_text(encoding="utf-8"))
    assert fiche.get("inventaire") == [], fiche.get("inventaire")


def test_objet_requis_autorise_dans_sa_salle(tmp_path):
    """Même objet, groupe dans la salle de l'étape (4,0) → ajout OK."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    data = _ctx_anti_triche(tmp_path, courant=(4, 0))
    ctx = ToolContext(partie_id="test_b59b4a9a", joueur="Alain",
                      data_dir=str(data))
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Margoth", "objet": "Le Trophée du boss",
         "portee": "quete"}))
    assert "ne peut pas être gagné ICI" not in r.text, r.text
    fiche = json.loads(
        (data / "fiches" / "fiche_margoth.json").read_text(encoding="utf-8"))
    noms = [e.get("nom") for e in fiche.get("inventaire") or []]
    assert any("trophée" in str(n).lower() for n in noms), noms


def test_objet_non_requis_non_contraind(tmp_path):
    """Un objet NON requis (potion) reste ajoutable partout (pas de faux
    refus)."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    data = _ctx_anti_triche(tmp_path, courant=(3, 0))
    ctx = ToolContext(partie_id="test_b59b4a9a", joueur="Alain",
                      data_dir=str(data))
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["inventaire_ajouter"], ctx,
        {"nom": "Margoth", "objet": "corde (15 m)", "poids": 5}))
    assert "ne peut pas être gagné ICI" not in r.text, r.text


# --------------------------------------------------------------------------- #
#  6. Re-création de fiche : refusée (perte d'inventaire de quête)
# --------------------------------------------------------------------------- #

def test_fiche_perso_creer_refuse_existant(tmp_path):
    """fiche_perso_creer sur une fiche EXISTANTE → refus (l'ancien bug
    écrasait l'inventaire : objets de quête perdus)."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    data = _ctx_anti_triche(tmp_path)
    (data / "fiches" / "fiche_margoth.json").write_text(json.dumps({
        "nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
        "niveau": 1, "pv": 17, "pv_max": 17,
        "inventaire": [
            {"nom": "la fiole de vérité", "qte": 1, "poids": 0.1,
             "portee": "quete", "partie": "test_b59b4a9a"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    ctx = ToolContext(partie_id="test_b59b4a9a", joueur="Alain",
                      data_dir=str(data))
    tools = discover_tools()
    r = asyncio.run(invoke_tool(
        tools["fiche_perso_creer"], ctx,
        {"nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
         "niveau": 1, "carac_json": '{"FOR":17,"DEX":13,"CON":16,"INT":10,'
                                    '"SAG":9,"CHA":12}',
         "pv": 17, "pv_max": 17, "ca": 15,
         "sauvegardes_json": '{"Vigueur":5,"Reflexes":1,"Volonte":0}',
         "bab": 1, "competences_json": "{}", "dons_json": "[]",
         "equipement_json": "[]", "or_total": 0, "alignement": "Chaotique Bon",
         "joueur": "Alain"}))
    assert "existe déjà" in r.text, r.text
    # L'inventaire de quête est INTACT.
    fiche = json.loads(
        (data / "fiches" / "fiche_margoth.json").read_text(encoding="utf-8"))
    quete = [e for e in fiche.get("inventaire") or []
             if e.get("portee") == "quete"]
    assert len(quete) == 1, fiche.get("inventaire")


# --------------------------------------------------------------------------- #
#  7. Genre du PJ : consigne de pronoms dans le récap ALLÉGÉ
# --------------------------------------------------------------------------- #

def _recap_avec_pj(sexe: str) -> str:
    """Récap allégé (exploration + PJ) pour un PJ du sexe donné — c'est le
    SEUL chemin utilisé en jeu normal (garde `build_recap`)."""
    from dataclasses import replace

    from server.config import PathsConfig, load_config
    from server.llm.prompt_builder import PromptBuilder

    cfg = load_config()
    cfg2 = replace(cfg, paths=PathsConfig(
        data_dir=str(Path(tempfile.gettempdir()) / "dnd35_genre_test"),
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    etat = {
        "phase": "exploration",
        "pj": [{"nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
                "niveau": 1, "pv": 17, "pv_max": 17, "ca": 15,
                "joueur": "Alain", "conditions": [],
                "apparence": {"sexe": sexe}}],
    }
    return PromptBuilder(cfg2).build_recap(etat, partie_id="test_genre")


def test_recap_allege_identite_pj_masculin():
    """Partie b59b4a9a : « Margoth… Elle franchit l'entrée » pour un PJ
    sexe M — la ligne IDENTITÉ DE RÔLE du récap complet n'était JAMAIS
    atteinte (la garde `build_recap` bascule exploration+PJ sur le récap
    allégé). Elle doit figurer dans le chemin allégé avec les pronoms."""
    recap = _recap_avec_pj("M")
    assert "IDENTITÉ DE RÔLE" in recap, "ligne absente du récap allégé"
    assert "MASCULIN" in recap
    assert "JAMAIS « elle »" in recap
    assert "il avance" in recap
    # Rappel FINAL en queue de récap (effet de récence contre l'écho du
    # mauvais genre dans l'historique — des dizaines de « Elle »).
    assert "RAPPEL IDENTITÉ" in recap
    assert recap.index("IDENTITÉ DE RÔLE") < recap.index("RAPPEL IDENTITÉ")
    assert recap.rindex("Margoth = MASCULIN") > recap.index("IDENTITÉ DE RÔLE")


def test_recap_allege_identite_pj_feminin():
    """Variante féminine : pronoms « elle » imposés, « il » interdit."""
    recap = _recap_avec_pj("F")
    assert "IDENTITÉ DE RÔLE" in recap
    assert "FÉMININ" in recap
    assert "JAMAIS « il »" in recap
    assert "elle avance" in recap
    assert "FÉMININ → « elle », JAMAIS « il »" in recap


def test_recap_riche_identite_et_rappel_en_combat():
    """Phase combat : le récap RICHE est utilisé — la ligne IDENTITÉ + le
    rappel final doivent y figurer aussi (b59b4a9a : le combat narrait
    « Elle » à chaque tour)."""
    from dataclasses import replace

    from server.config import PathsConfig, load_config
    from server.llm.prompt_builder import PromptBuilder

    cfg = load_config()
    cfg2 = replace(cfg, paths=PathsConfig(
        data_dir=str(Path(tempfile.gettempdir()) / "dnd35_genre_test"),
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))
    etat = {
        "phase": "combat",
        "tour": 4,
        "courant_tour_pour": "Margoth",
        "pj": [{"nom": "Margoth", "race": "Demi-orc", "classe": "Barbare",
                "niveau": 1, "pv": 2, "pv_max": 17, "ca": 15,
                "joueur": "Alain", "conditions": [],
                "apparence": {"sexe": "M"}}],
        "monstres_combat": [{"nom": "Zendar Nulentok (anti-druide)",
                             "pv": 24, "pv_max": 24}],
        "initiative": [
            {"nom": "Zendar Nulentok (anti-druide)", "init": 12},
            {"nom": "Margoth", "init": 1},
        ],
    }
    recap = PromptBuilder(cfg2).build_recap(etat, partie_id="test_genre")
    assert "IDENTITÉ DE RÔLE" in recap
    assert "RAPPEL IDENTITÉ" in recap
    assert "Margoth = MASCULIN" in recap


def test_bloc_identite_pj_sans_genre_vide():
    """Pas de genre connu → pas de ligne (pas d'invention)."""
    from server.llm.prompt_builder import _bloc_identite_pj, _rappel_final_pj
    assert _bloc_identite_pj("Margoth", "") == ""
    assert _bloc_identite_pj("", "Masculin") == ""
    assert _rappel_final_pj("", [{"nom": "Margoth"}]) == ""
    assert _rappel_final_pj("x", [{"nom": ""}]) == ""


# --------------------------------------------------------------------------- #
#  8. Correction déterministe des pronoms PJ (fichier + état)
# --------------------------------------------------------------------------- #

_ETAT_MARGOTH_M = {
    "pj": [{"nom": "Margoth", "apparence": {"sexe": "M"}}],
    "donjon": {"grille": [{"x": 0, "y": 0, "pnj": [
        "Thukmuul Teleshann (magesteresse NG — donne la quête)"]}]},
}


def test_correction_pronoms_pj_masculin():
    """« Margoth …, elle » / « … Margoth. Elle se relève » → « il » ;
    les accords et possessifs ne sont PAS touchés."""
    from server.llm.orchestrator import _corriger_pronoms_pj

    n = ("Margoth se relève difficilement, mais elle n'en reste pas moins "
         "blessée. Elle se tient prêt à frapper. Son armure d'écailles "
         "absorbe le choc.")
    corr = _corriger_pronoms_pj(n, _ETAT_MARGOTH_M, "")
    assert "mais il n'en reste pas moins blessée" in corr
    assert "Il se tient prêt à frapper" in corr
    # Possessif « Son armure » : correct en français (accord avec l'objet).
    assert "Son armure" in corr


def test_correction_pronoms_veto_pnj_feminin():
    """La phrase qui cite un PNJ FÉMININ connu (Teleshann) n'est pas
    retouchée — le pronom peut se rapporter à ELLE."""
    from server.llm.orchestrator import _corriger_pronoms_pj

    n = ("Margoth regarde Thukmuul Teleshann, elle semble inquiète. "
         "Margoth frappe. Elle sourit.")
    corr = _corriger_pronoms_pj(n, _ETAT_MARGOTH_M, "")
    # Phrase 1 : veto (Teleshann citée) — inchangée.
    assert "elle semble inquiète" in corr
    # Phrase 3 : Margoth sujet à la phrase précédente → corrigée.
    assert "Il sourit" in corr


def test_correction_pronoms_pj_feminin_miroir():
    """PJ féminin mal narré « il » → « elle » (miroir du cas Margoth)."""
    from server.llm.orchestrator import _corriger_pronoms_pj

    etat = {"pj": [{"nom": "Elandra", "apparence": {"sexe": "F"}}],
            "donjon": {"grille": []}}
    n = "Elandra avance d'un pas ferme, il lève son épée. Il frappe."
    corr = _corriger_pronoms_pj(n, etat, "")
    assert "elle lève son épée" in corr
    assert "Elle frappe" in corr


def test_correction_pronoms_complement_pas_de_fix():
    """« la hache de Margoth, elle tranche » — Margoth est COMPLÉMENT :
    le pronom peut désigner la hache → pas de correction (conservateur)."""
    from server.llm.orchestrator import _corriger_pronoms_pj

    n = "La hache de Margoth vibre, elle tranche l'air."
    corr = _corriger_pronoms_pj(n, _ETAT_MARGOTH_M, "")
    assert corr == n


def test_rappel_final_et_note_echo_dans_recap():
    """Le récap allégé contient : IDENTITÉ DE RÔLE + note anti-écho sur
    `derniere_narration` + RAPPEL final."""
    recap = _recap_avec_pj("M")
    etat_dbg = recap  # la note n'apparaît que si derniere_narration existe
    # Sans derniere_narration : pas de note, mais les deux autres oui.
    assert "IDENTITÉ DE RÔLE" in etat_dbg
    assert "RAPPEL IDENTITÉ" in etat_dbg
