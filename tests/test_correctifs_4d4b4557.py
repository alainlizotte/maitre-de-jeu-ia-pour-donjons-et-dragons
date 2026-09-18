# -*- coding: utf-8 -*-
"""Correctifs issus de l'analyse de la partie 4d4b4557.

1. `carte_donjon_entrer` réinitialisait le donjon quand le MJ l'appelait
   avec un id « libre » (« Grotte de Nulentok ») pendant une quête à
   manifeste : `_manifest_pour` priorise le scénario de la quête, le même
   donjon était reconstruit en salle (0,0) SANS archivage — le groupe
   « revenait » à la Tour de l'Équilibre alors que la narration le plaçait
   dans la grotte (3 réinitialisations, fiole re-offerte à chaque tour).
2. Le correctif 5quater-b suggérait `carte_donjon_entrer` même pour un
   voyage hors donjon : `_suggestion_outil_explo` oriente maintenant vers
   `voyage_demarrer` / `carte_donjon_entrer` / `carte_donjon_explorer`
   selon la scène.
3. Le combat contre le loup gris ne s'engageait pas : « bondit sur vous »
   était absent des marqueurs, et une attaque DÉCLARÉE par le joueur
   hors combat n'avait aucun déclencheur. `_detecter_combat_prose`
   accepte désormais `forcer_declencheur` (attaque du joueur) et les
   variantes « sur vous/sur toi » sont couvertes.
4. L'inventaire accumulait le même objet sous plusieurs libellés
   (« fiole de guérison » + « potion de soins légers ») : fusion par
   synonymes (`_OBJETS_SYNONYMES`).

Usage : py -m pytest tests/test_correctifs_4d4b4557.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game.state import PartyState  # noqa: E402
from server.main import (  # noqa: E402
    _ATTAQUE_JOUEUR_RE,
    _detecter_combat_prose,
    _direction_intention,
    _rejeu_inventaire_necessaire,
    _suggestion_outil_explo,
)
from server.llm.prompt_builder import _donjon_bloc  # noqa: E402
from server.tools.base import ToolContext, invoke_tool  # noqa: E402
from server.tools.inventaire import _cle_objet  # noqa: E402
from server.tools.inventaire import _OBJETS_SYNONYMES  # noqa: E402
from server.tools.registry import discover_tools  # noqa: E402

TOOLS = discover_tools()
PID = "test_4d4b4557"


def _ctx(d: str, pid: str = PID) -> ToolContext:
    return ToolContext(partie_id=pid, joueur="alain", data_dir=d)


async def _tool(d: str, outil: str, pid: str = PID, **args):
    return await invoke_tool(TOOLS[outil], _ctx(d, pid), args)


def _etat_simple(d: str, pid: str = PID, **extra) -> None:
    PartyState(data_dir=d, partie_id=pid).save({
        "meta": {"titre": "test"},
        "phase": "exploration",
        "pj": [{"nom": "Utturgut", "pv": 16, "pv_max": 16,
                "joueur": "alain"}],
        "pnj": [],
        "histoire": [],
        **extra,
    })


# --------------------------------------------------------------------------- #
# 1. carte_donjon_entrer : même scénario → refus ; autre donjon → archivage
# --------------------------------------------------------------------------- #
MANIFESTE = {
    "scenario": "scena_test",
    "donjon_id": "Donjon Canonique",
    "etages": [
        {
            "nom": "RDC",
            "entree": [0, 0],
            "salles": [
                {"x": 0, "y": 0, "type": "entrée",
                 "description": "Le seuil.",
                 "portes": {"nord": False, "sud": False, "est": True,
                            "ouest": False}},
                {"x": 1, "y": 0, "type": "salle vide",
                 "description": "La suite.",
                 "portes": {"nord": False, "sud": False, "est": False,
                            "ouest": True}},
            ],
        }
    ],
}


def _manifeste_ecrire(d: str) -> None:
    sc = os.path.join(d, "scenarios", "sc")
    os.makedirs(sc, exist_ok=True)
    with open(os.path.join(sc, "test.donjon.json"), "w",
              encoding="utf-8") as f:
        json.dump(MANIFESTE, f, ensure_ascii=False)


def test_entrer_meme_scenario_refuse_et_preserve():
    """Un `entrer` avec un id « libre » pendant une quête à manifeste est
    REFUSÉ (même scénario) : l'exploration en cours est préservée."""
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_entrer1_")
    pid = PID + "_e1"
    _manifeste_ecrire(d)
    _etat_simple(d, pid, quete={
        "titre": "Test", "source": "[scena_test] /x/test.pdf",
    })
    r1 = asyncio.run(_tool(d, "carte_donjon_entrer", pid,
                           donjon_id="Donjon Canonique"))
    assert "❌" not in r1.text[:3]
    # Progression : salle (1,0) visitée.
    r2 = asyncio.run(_tool(d, "carte_donjon_explorer", pid, direction="est"))
    assert "❌" not in r2.text[:3]
    # Tentative de ré-entrée avec un AUTRE id (le cas du 9B) : refus poli.
    r3 = asyncio.run(_tool(d, "carte_donjon_entrer", pid,
                           donjon_id="Grotte de Nulentok"))
    assert "déjà dans" in r3.text
    etat = PartyState(data_dir=d, partie_id=pid).load()
    assert etat["donjon"]["id"] == "Donjon Canonique"
    assert etat["donjon"]["courant"] == [1, 0]
    assert sorted(etat["donjon"]["salles_visitees"]) == ["0,0", "1,0"]
    assert not (etat.get("donjons_exploreres") or {})


def test_entrer_autre_donjon_archive_puis_restaure():
    """Un VRAI autre donjon (aucun manifeste) : l'actif est archivé, et
    retourner dedans restaure la grille (progrès conservé)."""
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_entrer2_")
    pid = PID + "_e2"
    _etat_simple(d, pid)
    r1 = asyncio.run(_tool(d, "carte_donjon_entrer", pid,
                           donjon_id="Donjon A"))
    assert "❌" not in r1.text[:3]
    r2 = asyncio.run(_tool(d, "carte_donjon_entrer", pid,
                           donjon_id="Donjon B"))
    assert "❌" not in r2.text[:3]
    etat = PartyState(data_dir=d, partie_id=pid).load()
    assert etat["donjon"]["id"] == "Donjon B"
    assert "Donjon A" in (etat.get("donjons_exploreres") or {})
    r3 = asyncio.run(_tool(d, "carte_donjon_entrer", pid,
                           donjon_id="Donjon A"))
    assert "retournez" in r3.text
    etat = PartyState(data_dir=d, partie_id=pid).load()
    assert etat["donjon"]["id"] == "Donjon A"
    assert etat["donjon"]["grille"], "grille restaurée"


# --------------------------------------------------------------------------- #
# 2. _suggestion_outil_explo : le bon outil selon la scène
# --------------------------------------------------------------------------- #
def test_suggestion_explorer_seul_si_donjon_actif():
    """Direction explicite dans un donjon : la direction DU JOUEUR est
    imposée au correctif (plus de direction inventée par le LLM)."""
    s = _suggestion_outil_explo(
        {"donjon": {"id": "Donjon Canonique"}},
        "Je vais au est",
    )
    assert "carte_donjon_explorer(direction='est')" in s
    assert "n'appelle PAS `carte_donjon_entrer`" in s


def test_suggestion_none_si_mouvement_interieur():
    """« je me dirige au centre de la salle » : AUCUNE direction → aucun
    outil forcé (6746fc6c : le rejeu faisait passer à la salle de l'Est)."""
    for txt in (
        "je me dirige au centre de la salle",
        "je continue à la marche",
        "j'examine les myconides",
    ):
        assert _suggestion_outil_explo(
            {"donjon": {"id": "Donjon", "arrivee_par": "ouest"}}, txt
        ) is None, txt


def test_suggestion_retour_par_la_porte_d_entree():
    """« je retourne dans la salle précédente » : direction = la porte par
    laquelle le groupe est entré (`arrivee_par`), pas une invention."""
    s = _suggestion_outil_explo(
        {"donjon": {"id": "Donjon", "arrivee_par": "ouest"}},
        "je retourne dans la salle précédente",
    )
    assert "carte_donjon_explorer(direction='ouest')" in s


def test_direction_intention():
    assert _direction_intention("Je vais au est") == "est"
    assert _direction_intention("direction nord") == "nord"
    assert _direction_intention("je me dirige au centre de la salle") is None
    assert _direction_intention("j'examine l'eau") is None


def test_suggestion_entrer_si_seuil_narre():
    s = _suggestion_outil_explo(
        {"donjon": {}},
        "Vous franchissez le seuil sombre de la grotte. Vous entrez.",
    )
    assert "`carte_donjon_entrer(donjon_id=…)`" in s
    assert "voyage_demarrer" not in s


def test_suggestion_voyage_hors_donjon():
    s = _suggestion_outil_explo(
        {"donjon": {}},
        "Vous quittez Silverymoon. La route vous mène vers le nord-ouest "
        "pendant plusieurs jours, à travers collines et forêts.",
    )
    assert "voyage_demarrer" in s
    # Le piège 4d4b4557 : plus AUCUNE suggestion d'entrer pour un voyage.
    assert "carte_donjon_entrer(donjon_id" not in s


# --------------------------------------------------------------------------- #
# 3. Détection de combat : marqueurs élargis + déclencheur joueur
# --------------------------------------------------------------------------- #
BESTIAIRE_MIN = {
    "_meta": {"version": 1},
    "loup": {"cle": "loup", "nom": "Loup", "pv": 13, "pv_max": 13,
             "ca": 12, "generique": False},
}


def _bestiaire_ecrire(d: str) -> None:
    with open(os.path.join(d, "bestiaire.json"), "w", encoding="utf-8") as f:
        json.dump(BESTIAIRE_MIN, f, ensure_ascii=False)


NARR_LOUP = (
    "Un loup gris émerge des fourrés. Le loup gris bondit sur vous, mais "
    "votre hache à deux mains rencontre une résistance inattendue. "
    "Il semble prêt à vous attaquer à nouveau."
)


def test_marqueur_bondit_sur_vous():
    """Le cas exact du loup (4d4b4557) : « bondit sur vous » déclenche."""
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_loup1_")
    _bestiaire_ecrire(d)
    types = _detecter_combat_prose(d, NARR_LOUP, {"phase": "exploration"})
    assert types == ["Loup"]


def test_declencheur_attaque_joueur_sans_marqueur():
    """Le joueur déclare l'attaque mais le MJ narre SANS aucun marqueur :
    `forcer_declencheur` (déclenché par `_ATTAQUE_JOUEUR_RE`) suffit."""
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_loup2_")
    _bestiaire_ecrire(d)
    narr_sans_marqueur = (
        "Vous brandissez votre hache. Le loup gris observe, circonspect. "
        "Que décidez-vous de faire ?"
    )
    assert _detecter_combat_prose(
        d, narr_sans_marqueur, {"phase": "exploration"}) == []
    assert _detecter_combat_prose(
        d, narr_sans_marqueur, {"phase": "exploration"},
        forcer_declencheur=True) == ["Loup"]


def test_fin_narree_bloque_meme_avec_declencheur_joueur():
    """Combat déjà clos dans la prose (« vous avez vaincu ») : jamais de
    ré-engagement, même si le joueur a déclaré une attaque."""
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_loup3_")
    _bestiaire_ecrire(d)
    narr_fuite = (
        "Le loup gris se retire dans les buissons. Vous avez vaincu cette "
        "menace."
    )
    assert _detecter_combat_prose(
        d, narr_fuite, {"phase": "exploration"},
        forcer_declencheur=True) == []


def test_regex_attaque_joueur_sans_lance():
    """« J'attaque le loup » déclenche ; « je lance un sort » non."""
    assert _ATTAQUE_JOUEUR_RE.search("J'attaque le loup avec ma hache")
    assert _ATTAQUE_JOUEUR_RE.search("je tire à l'arc sur la goule")
    assert _ATTAQUE_JOUEUR_RE.search("Je frappe le coffre")
    assert not _ATTAQUE_JOUEUR_RE.search("je lance un sort de lumière")
    assert not _ATTAQUE_JOUEUR_RE.search("je lance les dés")


# --------------------------------------------------------------------------- #
# 4. Inventaire : fusion des synonymes (fiole/potion de soins)
# --------------------------------------------------------------------------- #
def test_synonymes_table():
    assert _OBJETS_SYNONYMES["fiole de guerison"] == "potion de soins legers"
    assert (_OBJETS_SYNONYMES["potion de soin leger"]
            == "potion de soins legers")


def test_inventaire_fusionne_fiole_et_potion():
    d = tempfile.mkdtemp(prefix="dnd35_4d4b_inv_")
    pid = PID + "_inv"
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 16, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare", "conditions": [],
                   "equipement": [], "inventaire": []},
                  f, ensure_ascii=False)
    r1 = asyncio.run(_tool(d, "inventaire_ajouter", pid, nom="Utturgut",
                           objet="fiole de guérison", quantite=1, poids=0.1))
    assert "✅" in r1.text
    r2 = asyncio.run(_tool(d, "inventaire_ajouter", pid, nom="Utturgut",
                           objet="potion de soins légers", quantite=2,
                           poids=0.1))
    assert "✅" in r2.text
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
              encoding="utf-8") as f:
        fiche = json.load(f)
    potions = [e for e in fiche["inventaire"]
               if _cle_objet(e.get("nom")) == "potion de soins legers"]
    assert len(potions) == 1, fiche["inventaire"]
    assert potions[0]["qte"] == 3
    # Le retrait par un libellé synonyme consomme la ligne fusionnée.
    r3 = asyncio.run(_tool(d, "inventaire_retirer", pid, nom="Utturgut",
                           objet="fiole de soins légers", quantite=1,
                           poids=0.1))
    assert "🗑️" in r3.text
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
              encoding="utf-8") as f:
        fiche = json.load(f)
    potions = [e for e in fiche["inventaire"]
               if _cle_objet(e.get("nom")) == "potion de soins legers"]
    assert len(potions) == 1 and potions[0]["qte"] == 2


# --------------------------------------------------------------------------- #
# 8. Inventaire de quête par partie (bb4c4fb9) : un PJ réutilisé dans
#    plusieurs parties garde l'inventaire de quête de CHACUNE.
# --------------------------------------------------------------------------- #
def _fiche_min(d: str) -> None:
    fiches_dir = os.path.join(d, "fiches")
    os.makedirs(fiches_dir, exist_ok=True)
    with open(os.path.join(fiches_dir, "fiche_utturgut.json"), "w",
              encoding="utf-8") as f:
        json.dump({"nom": "Utturgut", "pv": 16, "pv_max": 16, "niveau": 1,
                   "classe": "Barbare", "conditions": [],
                   "equipement": [], "inventaire": []},
                  f, ensure_ascii=False)


def _lire_inventaire(d: str) -> list[dict]:
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
              encoding="utf-8") as f:
        return json.load(f).get("inventaire") or []


def test_portee_auto_classification():
    from server.tools.inventaire import _portee_auto

    assert _portee_auto("Hache à deux mains") == "permanent"
    assert _portee_auto("Armure d'écailles") == "permanent"
    assert _portee_auto("gemme de Beljuril") == "permanent"
    assert _portee_auto("fiole de guérison") == "quete"
    assert _portee_auto("clé de fer") == "quete"
    assert _portee_auto("carte de la grotte") == "quete"
    assert _portee_auto("lettre de recommandation") == "quete"


def test_inventaire_quete_isole_par_partie():
    """Deux parties utilisent le même PJ : chaque partie voit (et consomme)
    UNIQUEMENT son inventaire de quête ; l'autre reste intacte."""
    d = tempfile.mkdtemp(prefix="dnd35_quete_multi_")
    _fiche_min(d)
    ctx_a = ToolContext(partie_id="partie_A", joueur="alain", data_dir=d)
    ctx_b = ToolContext(partie_id="partie_B", joueur="alain", data_dir=d)

    # Thukmuul donne une fiole dans CHAQUE partie (portée quête par défaut).
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx_a, {
        "nom": "Utturgut", "objet": "fiole de guérison", "quantite": 1,
        "poids": 0.1}))
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx_b, {
        "nom": "Utturgut", "objet": "fiole de guérison", "quantite": 2,
        "poids": 0.1}))

    inv = _lire_inventaire(d)
    quetes = [e for e in inv if e.get("portee") == "quete"]
    assert len(quetes) == 2, inv
    par_partie = {e["partie"]: e["qte"] for e in quetes}
    assert par_partie == {"partie_A": 1, "partie_B": 2}

    # Consommation dans A : la fiole de B reste intacte.
    r = asyncio.run(invoke_tool(TOOLS["inventaire_retirer"], ctx_a, {
        "nom": "Utturgut", "objet": "fiole de guérison", "quantite": 1}))
    assert "🗑️" in r.text
    inv = _lire_inventaire(d)
    par_partie = {e["partie"]: e["qte"] for e in inv
                  if e.get("portee") == "quete"}
    assert par_partie == {"partie_B": 2}, inv

    # Le récap de chaque partie ne montre QUE son inventaire de quête.
    from server.persos import resume_inventaire
    with open(os.path.join(d, "fiches", "fiche_utturgut.json"),
              encoding="utf-8") as f:
        fiche = json.load(f)
    recap_a = resume_inventaire(fiche, partie_id="partie_A")
    recap_b = resume_inventaire(fiche, partie_id="partie_B")
    assert "partie_A" not in recap_a and "partie_B" not in recap_a
    assert "Inventaire de quête" not in recap_a  # fiole A consommée
    assert "Inventaire de quête" in recap_b and "×2" in recap_b


def test_inventaire_permanent_survis_aux_parties():
    """Achat/arme (portee permanent) : une seule ligne, visible dans toutes
    les parties, jamais taguée quête."""
    d = tempfile.mkdtemp(prefix="dnd35_quete_perm_")
    _fiche_min(d)
    ctx_a = ToolContext(partie_id="partie_A", joueur="alain", data_dir=d)
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx_a, {
        "nom": "Utturgut", "objet": "hache", "quantite": 1}))
    inv = _lire_inventaire(d)
    haches = [e for e in inv if "hache" in _cle_objet(e["nom"])]
    assert len(haches) == 1 and haches[0].get("portee") != "quete"
    ctx_b = ToolContext(partie_id="partie_B", joueur="alain", data_dir=d)
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx_b, {
        "nom": "Utturgut", "objet": "hache", "quantite": 1}))
    inv = _lire_inventaire(d)
    haches = [e for e in inv if "hache" in _cle_objet(e["nom"])]
    assert len(haches) == 1 and haches[0]["qte"] == 2  # fusion permanente


def test_retirer_auto_consume_quete_dabord():
    """portee=auto : le don de PNJ est consommé avant l'équipement durable."""
    d = tempfile.mkdtemp(prefix="dnd35_quete_auto_")
    _fiche_min(d)
    ctx = ToolContext(partie_id="partie_A", joueur="alain", data_dir=d)
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx, {
        "nom": "Utturgut", "objet": "potion de soins", "quantite": 1,
        "poids": 0.1, "portee": "quete"}))
    asyncio.run(invoke_tool(TOOLS["inventaire_ajouter"], ctx, {
        "nom": "Utturgut", "objet": "potion de soins", "quantite": 3,
        "poids": 0.1, "portee": "permanent"}))
    asyncio.run(invoke_tool(TOOLS["inventaire_retirer"], ctx, {
        "nom": "Utturgut", "objet": "potion de soins", "quantite": 1}))
    inv = _lire_inventaire(d)
    quete = [e for e in inv if e.get("portee") == "quete"]
    perm = [e for e in inv if e.get("portee") != "quete"
            and "potion" in _cle_objet(e.get("nom"))]
    assert quete == []  # le don de PNJ part en premier
    assert len(perm) == 1 and perm[0]["qte"] == 3



# --------------------------------------------------------------------------- #
# 5. 5quater-c : plus de rejeu inventaire sur les mentions de possession
#    (6746fc6c : rejeu 6 tours sur 8, fiole re-ajoutée)
# --------------------------------------------------------------------------- #
def test_rejeu_inventaire_sur_declaration_joueur():
    assert _rejeu_inventaire_necessaire(
        "je ramasse la clé", "Vous ramassez la clé rouillée.")


def test_rejeu_inventaire_pas_sur_possession_narree():
    """Le cas exact de « j'examine l'eau » (6746fc6c) : le sac tissé dans
    la prose n'est PAS une acquisition."""
    assert not _rejeu_inventaire_necessaire(
        "j'examine l'eau",
        "La fiole de soins légers glisse maintenant dans votre sac à dos, "
        "bien rangée. Votre dur à cuire vous a permis de résister.",
    )
    assert not _rejeu_inventaire_necessaire(
        "Je vais au est",
        "Votre main tremble légèrement en refermant la fiole de soins "
        "légers dans votre sac. Cet objet précieux pèse dans votre "
        "inventaire.",
    )


def test_rejeu_inventaire_sur_acquisition_ancree():
    """Acquisition réelle NARRÉE par le MJ (objet indéfini) : le rejeu reste
    armé pour enregistrer le butin."""
    assert _rejeu_inventaire_necessaire(
        "je fouille la pièce",
        "Au fond du coffre, vous trouvez un pendentif en or.",
    )
    assert _rejeu_inventaire_necessaire(
        "", "Thukmuul vous donne une lettre de recommandation.")


def test_rejeu_inventaire_pas_sur_article_defini():
    """« vous trouvez le passage / la sortie » : pas un objet d'inventaire."""
    assert not _rejeu_inventaire_necessaire(
        "j'avance", "En longeant le mur, vous trouvez le passage caché.")


# --------------------------------------------------------------------------- #
# 6. Bloc donjon : le contenu canonique est exhaustif (anti « première clé »)
# --------------------------------------------------------------------------- #
def test_bloc_donjon_interdit_invention():
    etat = {
        "donjon": {
            "id": "Donjon Test",
            "grille": [{"x": 0, "y": 0, "type": "entrée",
                        "description": "Le seuil.",
                        "portes": {"est": True}}],
            "courant": [0, 0],
            "salles_visitees": ["0,0"],
        }
    }
    bloc = _donjon_bloc(etat)
    assert "SEULE réalité de la salle" in bloc
    assert "NI clé" in bloc


# --------------------------------------------------------------------------- #
# 7. Ouverture : ancre du lieu de départ canonique (c1f4e547 — ouverture
#    improvisée dans le « donjon de Khundrukar »)
# --------------------------------------------------------------------------- #
def test_lieu_depart_canonique_ancree_la_scene():
    from server.llm.prompt_builder import _lieu_depart_canonique

    etat = {
        "histoire": [],
        "donjon": {
            "id": "La Couronne de Mystra",
            "grille": [{"x": 0, "y": 0, "type": "entrée",
                        "description": "La salle d'audience de la Tour "
                                       "de l'Équilibre, à Silverymoon.",
                        "visitee": True,
                        "portes": {"est": True}}],
            "courant": [0, 0],
        },
    }
    ancre = _lieu_depart_canonique(etat)
    assert "LIEU DE DÉPART CANONIQUE" in ancre
    assert "Tour de l'Équilibre" in ancre
    assert "N'invente AUCUN autre lieu" in ancre


def test_lieu_depart_canonique_absent_si_histoire_non_vide():
    from server.llm.prompt_builder import _lieu_depart_canonique

    etat = {
        "histoire": [{"evenement": "Début de l'aventure"}],
        "donjon": {"id": "D", "grille": [{"x": 0, "y": 0,
                                          "description": "x",
                                          "visitee": True}]},
    }
    assert _lieu_depart_canonique(etat) == ""
    assert _lieu_depart_canonique({"histoire": []}) == ""
