# -*- coding: utf-8 -*-
"""Régressions de la partie 7177d819 (test 2026-10-02).

Le groupe (Margoth, barbare niv 1) avait accepté la quête de la Couronne
de Mystra et s'était mis en route vers le repère de Zendar Nulentok. Le MJ
a alors :
1. annoncé la MAUVAISE destination (« Les runes indiquent la direction à
   prendre : vers les ruines de Sarr, situées à Beljuril (4,1) » —
   hallucination collée sur la liste des sites-gemmes du détail de
   l'objectif FUTUR, injectée telle quelle dans le prompt ; le parchemin
   de la route mène en réalité à la grotte de Nulentok (1,0), et Sarr
   (4,1) est le premier site-gemme, scellé tant que la Couronne n'est pas
   reprise) ;
2. au tour suivant, RE-NARRÉ la scène d'ouverture (quête re-acceptée,
   parchemin et fiole re-remis) alors que l'ouverture était jouée depuis
   cinq tours — l'anti-répétition ne voit pas ces reformulations
   (chevauchement bigrammes 20 % < seuil 40 %) et l'ancre « Salle
   COURANTE (0,0) — Description figée (reprends-la à l'identique) » (la
   description de la salle d'entrée EST la scène d'ouverture) a re-attiré
   le MJ vers l'intro.

Correctifs (server/llm/prompt_builder.py) :
- garde « [ÉTAPE À VENIR — …] » sur le `detail` des étapes/objectifs non
  courants (trame du manifeste + bloc OBJECTIFS DE QUÊTE) : la liste des
  sites-gemmes n'est plus une destination actuelle tentante ;
- avertissement « ⚠️ SCÈNE D'OUVERTURE DÉJÀ JOUÉE » dans le bloc CARTE DU
  DONJON tant que le groupe se trouve dans la salle d'entrée avec
  l'ouverture déjà journalisée dans `histoire` ;
- l'étape ancrée à la salle d'entrée (0,0) n'est plus marquée
  « ✅ ACCOMPLIE » via sa `visitee=True` (faux positif : l'étape finale
  « Restaurer la Couronne — (0,0) » s'affichait accomplie dès la création,
  en contradiction avec le bloc OBJECTIFS « ⚪ à venir »).

Usage : py -m pytest tests/test_correctifs_7177d819.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.prompt_builder import (  # noqa: E402
    _donjon_bloc,
    _est_salle_entree,
    _ouverture_deja_jouee,
    _scenario_bible_bloc,
)

PID = "test_7177d819"

SALLE_ENTREE_DESC = (
    "Haut lieu de Silverymoon, la salle d'audience de la Tour de "
    "l'Équilibre s'ouvre sur la cité par de hautes baies de verre dépoli : "
    "marbre laiteux, tapis célestes et parfum d'encens. La magesteresse "
    "Thukmuul Teleshann y reçoit le groupe, le regard grave, et lui confie "
    "la quête de la Couronne de Mystra."
)

NOTE_ENTREE = (
    "⚠️ La porte EST n'est PAS un couloir intérieur : la franchir = "
    "PRENDRE LA ROUTE hors de Silverymoon — narrez le VOYAGE (plusieurs "
    "jours, la vie animale se raréfie à l'approche du groove) qui mène à "
    "la bouche de la grotte de Nulentok, salle suivante (1,0)."
)


def _etat_7177(histoire=None, courant=(0, 0)) -> dict:
    """Réplique l'état de la partie 7177d819 : donjon « La Couronne de
    Mystra » (plan du scénario), quête choisie, ouverture jouée + entrée
    journalisée, groupe dans la salle (0,0)."""
    donjon = {
        "id": "La Couronne de Mystra",
        "grille": [
            {
                "x": 0, "y": 0, "type": "entrée",
                "description": SALLE_ENTREE_DESC,
                "visitee": True,
                "portes": {"nord": False, "sud": False, "est": True,
                           "ouest": False},
                "note": NOTE_ENTREE,
                "pnj": ["Thukmuul Teleshann (magesteresse NG de la Tour de "
                        "l'Équilibre — donne la quête)"],
            },
            {
                "x": 1, "y": 0, "type": "salle vide",
                "description": "Au terme du voyage depuis Silverymoon, la "
                               "grotte de Nulentok s'ouvre dans le flanc de "
                               "la colline.",
                "visitee": False,
                "portes": {"nord": False, "sud": False, "est": True,
                           "ouest": True},
            },
        ],
        "salles_visitees": ["0,0"],
        "portes_bloquees": [],
        "courant": list(courant),
        "etage": 0,
        "etages": {},
        "etapes": [
            {
                "cle": "couronne",
                "titre": "Récupérer la Couronne de Mystra chez Zendar "
                         "Nulentok",
                "type": "objet",
                "salle": "4,0",
                "detail": "PRÉREQUIS ABSOLU : vaincre Nulentok dans son "
                          "groove et reprendre la Couronne.",
                "gate": True,
            },
            {
                "cle": "huit_gemmes",
                "titre": "Réunir les huit gemmes dispersées sur Faerûn",
                "type": "objet",
                "salle": "",
                "detail": "Au retour de la Couronne, Teleshann confie la "
                          "BAGUETTE DE TÉLÉPORTATION (inventaire_ajouter, "
                          "portee=\"quete\") : elle ne s'active qu'au "
                          "contact de la Couronne. Beljuril — Ruines de "
                          "Sarr (4,1) ; Diamant — échoppe d'Aluarim (3,1).",
                "gate": True,
            },
            {
                "cle": "restauration",
                "titre": "Restaurer la Couronne et la rendre à Mystra",
                "type": "etape",
                "salle": "0,0",
                "detail": "Réactiver la Couronne avec les huit gemmes et la "
                          "rapporter au haut lieu de Mystra (0,0).",
                "gate": True,
            },
        ],
        "arrivee_par": None,
        "localite_entree": "Silverymoon",
    }
    return {
        "meta": {"titre": "test 02 oct", "regles": "D&D 3.5"},
        "phase": "exploration",
        "pj": [{"nom": "Margoth", "pv": 17, "pv_max": 17, "joueur": "alain"}],
        "pnj": [],
        "lieu": {"nom": "La Couronne de Mystra", "type": "donjon",
                 "description": SALLE_ENTREE_DESC, "position_x": 0,
                 "position_y": 0},
        "donjon": donjon,
        "quete": {
            "titre": "The Crown Of Mystra",
            "pitch": "La Couronne de Mystra, artefact de pouvoir magique "
                     "immense, a disparu.",
            "bible": {
                "titre": "The Crown Of Mystra 1/4",
                "resume": "Magister Thukmuul Teleshann asks the PCs to "
                          "recover the Crown of Mystra from Zendar "
                          "Nulentok, then gather the eight gems.",
                "ennemis": ["Squelette"],
                "objectif_courant": "Récupérer la Couronne de Mystra chez "
                                    "Zendar Nulentok",
                "etape_courante": "Récupérer la Couronne de Mystra chez "
                                  "Zendar Nulentok",
                "etapes_terminees": [],
                "termines": [],
            },
        },
        "histoire": histoire if histoire is not None else [
            {
                "ts": "2026-10-02T15:22:37",
                "tour": "",
                "evenement": "Début de l'aventure : Margoth, demi-orc "
                             "barbare, se tient dans la salle d'audience de "
                             "la Tour de l'Équilibre.",
            },
            {
                "ts": "2026-10-02T15:29:49",
                "tour": "",
                "evenement": "Entrée dans le donjon « La Couronne de "
                             "Mystra » — salle (0,0).",
            },
        ],
    }


def _lignes_trame(bloc: str) -> dict[str, str]:
    """Lignes de la section TRAME DU SCÉNARIO indexées par numéro."""
    trame: dict[str, str] = {}
    dans_trame = False
    for ligne in bloc.splitlines():
        if "TRAME DU SCÉNARIO" in ligne:
            dans_trame = True
            continue
        if dans_trame:
            if ligne.strip().startswith("⚠️"):
                break
            numero = ligne.strip().split(".", 1)[0]
            if numero.isdigit():
                trame[numero] = ligne.strip()
    return trame


# --------------------------------------------------------------------------- #
# 1. Marqueurs d'aide : ouverture déjà jouée / salle d'entrée canonique.
# --------------------------------------------------------------------------- #
def test_ouverture_deja_jouee_marqueur_intro_seul():
    """`_ouverture_deja_jouee` : True dès que l'événement « Début de
    l'aventure » est journalisé — False sinon (histoire vide, ou seule une
    entrée de donjon journalisée)."""
    assert _ouverture_deja_jouee(_etat_7177()) is True
    assert _ouverture_deja_jouee({"histoire": []}) is False
    assert _ouverture_deja_jouee({"histoire": None}) is False
    # Une entrée de donjon seule n'est PAS l'ouverture (le marqueur est
    # l'événement « Début de l'aventure » journalisé par main.py).
    assert _ouverture_deja_jouee({
        "histoire": [{"evenement": "Entrée dans le donjon « X » — salle (0,0)."}],
    }) is False


def test_est_salle_entree_canonique():
    """`_est_salle_entree` : True pour la première salle visitée du plan
    (l'entrée), False pour une autre salle ou une salle inconnue."""
    etat = _etat_7177()
    donjon = etat["donjon"]
    assert _est_salle_entree(donjon, {"x": 0, "y": 0}) is True
    # (1,0) existe mais n'est pas visitée : l'entrée reste (0,0).
    assert _est_salle_entree(donjon, {"x": 1, "y": 0}) is False
    # Salle absente de la grille.
    assert _est_salle_entree(donjon, {"x": 9, "y": 9}) is False


# --------------------------------------------------------------------------- #
# 2. Avertissement « SCÈNE D'OUVERTURE DÉJÀ JOUÉE » (bloc CARTE DU DONJON) :
#    c'est lui qui empêche le MJ de rejouer l'intro (partie 7177d819, tour 7).
# --------------------------------------------------------------------------- #
def test_avertissement_ouverture_deja_jouee_injecte():
    """Le groupe est dans la salle d'entrée (0,0), l'ouverture est jouée et
    journalisée : le bloc CARTE DU DONJON doit porter l'avertissement."""
    bloc = _donjon_bloc(_etat_7177())
    assert "SCÈNE D'OUVERTURE DÉJÀ JOUÉE" in bloc
    assert "ne la REJOUE PAS" in bloc
    assert "ni remise d'objets de quête une seconde fois" in bloc


def test_avertissement_absent_partie_neuve():
    """Partie NEUVE (histoire vide) : pas d'avertissement — la scène
    d'ouverture doit au contraire être JOUÉE (directive `_DEBUT_AVENTURE`,
    partie 5a9b99c8)."""
    bloc = _donjon_bloc(_etat_7177(histoire=[]))
    assert "SCÈNE D'OUVERTURE DÉJÀ JOUÉE" not in bloc


def test_avertissement_absent_groupe_ailleurs():
    """Le groupe a quitté la salle d'entrée (courant (1,0)) : pas
    d'avertissement — la description figée de (1,0) est la bonne ancre."""
    bloc = _donjon_bloc(_etat_7177(courant=(1, 0)))
    assert "SCÈNE D'OUVERTURE DÉJÀ JOUÉE" not in bloc


# --------------------------------------------------------------------------- #
# 3. Garde de trame : le détail des étapes À VENIR n'est plus un leurre
#    (liste des sites-gemmes « Beljuril — Ruines de Sarr (4,1) »).
# --------------------------------------------------------------------------- #
def test_trame_detail_etape_courante_non_marquee():
    """Le détail de l'étape COURANTE (1ʳᵉ ⬜ À FAIRE) reste l'instruction
    active — sans marque « ÉTAPE À VENIR »."""
    trame = _lignes_trame(_donjon_bloc(_etat_7177()))
    assert "PRÉREQUIS ABSOLU" in trame["1"]
    assert "ÉTAPE À VENIR" not in trame["1"]


def test_trame_detail_etapes_futures_marquees():
    """Le détail des étapes à venir est TRONQUÉ à la première phrase avec le
    garde « [ÉTAPE À VENIR — …] » : la liste des sites-gemmes (« Beljuril —
    Ruines de Sarr (4,1) ; … ») ne doit PLUS apparaître — le modèle la
    recollait comme destination actuelle même sur partie neuve (partie
    ae358455 : « Le Groove de Nulentok se trouve dans les ruines de Sarr »)."""
    trame = _lignes_trame(_donjon_bloc(_etat_7177()))
    assert "ÉTAPE À VENIR" in trame["2"]
    # La liste des sites-gemmes est TRONQUÉE : plus de leurre de destination.
    assert "Ruines de Sarr" not in trame["2"], trame["2"][:300]
    assert "ÉTAPE À VENIR" in trame["3"]


def test_trame_etape_accomplie_garde_son_detail_historique():
    """Une étape ✅ ACCOMPLIE garde son détail SANS marque « À VENIR »
    (marquer une étape accomplie « à venir » serait contradictoire)."""
    etat = _etat_7177()
    # La grotte de Nulentok (1,0) explorée + une étape ancrée dessus.
    for s in etat["donjon"]["grille"]:
        if (s.get("x"), s.get("y")) == (1, 0):
            s["visitee"] = True
    etat["donjon"]["salles_visitees"] = ["0,0", "1,0"]
    etat["donjon"]["etapes"].append({
        "cle": "grotte",
        "titre": "Traverser la grotte de Nulentok",
        "type": "etape",
        "salle": "1,0",
        "detail": "Passer par la grotte.",
        "gate": True,
    })
    trame = _lignes_trame(_donjon_bloc(etat))
    assert "Traverser la grotte de Nulentok — ✅ ACCOMPLIE" in trame["4"]
    assert "ÉTAPE À VENIR" not in trame["4"]


# --------------------------------------------------------------------------- #
# 4. Faux positif corrigé : une étape ancrée à la salle d'entrée (0,0) ne
#    doit plus être marquée ✅ ACCOMPLIE via sa `visitee=True`.
# --------------------------------------------------------------------------- #
def test_etape_salle_entree_pas_marquee_accomplie():
    """L'étape finale « Restaurer la Couronne — (0,0) » s'affichait ✅
    ACCOMPLIE dès la création (la salle d'entrée est visitée d'office), en
    contradiction avec le bloc OBJECTIFS « ⚪ à venir ». Elle est désormais
    ⬜ À FAIRE."""
    trame = _lignes_trame(_donjon_bloc(_etat_7177()))
    assert "Restaurer la Couronne et la rendre à Mystra — ⬜ À FAIRE " \
           "(salle 0,0)" in trame["3"]
    assert "Restaurer la Couronne et la rendre à Mystra — ✅ ACCOMPLIE" \
        not in trame["3"]


def test_etape_salle_visitee_non_entree_toujours_accomplie():
    """Non-régression : une étape ancrée à une salle visitée QUI N'EST PAS
    la salle d'entrée reste ✅ ACCOMPLIE."""
    etat = _etat_7177()
    for s in etat["donjon"]["grille"]:
        if (s.get("x"), s.get("y")) == (1, 0):
            s["visitee"] = True
    etat["donjon"]["salles_visitees"] = ["0,0", "1,0"]
    etat["donjon"]["etapes"].append({
        "cle": "grotte",
        "titre": "Traverser la grotte de Nulentok",
        "type": "etape",
        "salle": "1,0",
        "detail": "Passer par la grotte.",
        "gate": True,
    })
    trame = _lignes_trame(_donjon_bloc(etat))
    assert "Traverser la grotte de Nulentok — ✅ ACCOMPLIE" in trame["4"]


# --------------------------------------------------------------------------- #
# 5. Garde sur le bloc OBJECTIFS DE QUÊTE : le détail des objectifs non
#    courants est marqué (même source de leurre que la trame).
# --------------------------------------------------------------------------- #
def test_objectifs_futurs_marques_dans_bible():
    """`_scenario_bible_bloc` : le détail de l'objectif COURANT reste
    l'instruction active (avec sa liste complète) ; ceux des objectifs À
    VENIR sont tronqués et marqués (la liste des sites ne doit plus fuir)."""
    d = tempfile.mkdtemp(prefix="dnd35_7177_bible_")
    etat = _etat_7177()
    bloc = _scenario_bible_bloc(etat["quete"], etat=etat, data_dir=d)
    lignes = bloc.splitlines()
    couronne = next(l for l in lignes if l.strip().startswith("↳ PRÉREQUIS"))
    gemmes = next(l for l in lignes if "ÉTAPE À VENIR" in l and "BAGUETTE" in l)
    restauration = next(
        l for l in lignes if "rapporter au haut lieu de Mystra" in l
        and l.strip().startswith("↳")
    )
    assert "ÉTAPE À VENIR" not in couronne
    # Troncature : la liste des sites-gemmes ne fuit plus.
    assert "Ruines de Sarr" not in gemmes, gemmes[:300]
    assert "ÉTAPE À VENIR" in restauration


# --------------------------------------------------------------------------- #
# 6. Bout en bout : le récap reconstruit pour l'état EXACT de la partie
#    7177d819 porte tous les gardes (c'est le prompt que le MJ reçoit).
# --------------------------------------------------------------------------- #
def _make_cfg(d: str):
    from dataclasses import replace

    from server.config import PathsConfig, load_config
    cfg = load_config()
    return replace(cfg, paths=PathsConfig(
        data_dir=d,
        prompts_dir=str(cfg.paths.prompts_dir),
        sections_dir=str(cfg.paths.sections_dir),
    ))


def test_recap_complet_injecte_les_gardes():
    """`build_recap` pour l'état de la partie 7177d819 (exploration, PJ créé)
    doit injecter : l'avertissement d'ouverture déjà jouée, les marques
    « ÉTAPE À VENIR » sur les sites-gemmes futurs, et l'étape finale ⬜ À
    FAIRE (plus jamais « ✅ ACCOMPLIE »)."""
    from server.game.state import PartyState
    from server.llm.prompt_builder import PromptBuilder

    d = tempfile.mkdtemp(prefix="dnd35_7177_recap_")
    etat = _etat_7177()
    PartyState(data_dir=d, partie_id=PID).save(etat)
    etat = PartyState(data_dir=d, partie_id=PID).load()
    recap = PromptBuilder(_make_cfg(d)).build_recap(etat)
    assert "SCÈNE D'OUVERTURE DÉJÀ JOUÉE" in recap
    assert "ÉTAPE À VENIR" in recap
    assert "⬜ À FAIRE (salle 0,0)" in recap
    assert "Restaurer la Couronne et la rendre à Mystra — ✅ ACCOMPLIE" \
        not in recap


# --------------------------------------------------------------------------- #
# 7. Correctifs partie ae358455 (nouvelle partie) : genre des PNJ injecté,
#    garde « déplacement narré sans outil », budget inventaire_ajouter.
# --------------------------------------------------------------------------- #
def test_genre_pnj_injecte_dans_bible():
    """Le genre des PNJ du manifeste (marqueurs non ambigus) est injecté dans
    le bloc bible : Thukmuul Teleshann (magesteresse) = FÉMININ — le MJ
    disait « Il tend », « murmure le magicien » (partie ae358455)."""
    from server.llm.prompt_builder import _genres_pnj_donjon

    etat = _etat_7177()
    genres = _genres_pnj_donjon(etat)
    assert any("Teleshann" in g and "FÉMININ" in g for g in genres), genres


def test_genre_pnj_inconnu_jamais_suppose():
    """Un PNJ sans marque de genre non ambigu n'est PAS listé (jamais de
    supposition — « capitaine », « agent » sont ambigus)."""
    from server.llm.prompt_builder import _genres_pnj_donjon

    etat = _etat_7177()
    etat["donjon"]["grille"][0]["pnj"].append(
        "Werpafel (capitaine et mage mineur de Meredoth — accueille)")
    genres = _genres_pnj_donjon(etat)
    assert not any("Werpafel" in g for g in genres), genres
    # Le PNJ avec marque reste listé.
    assert any("Teleshann" in g for g in genres)


def test_deplacement_narre_detecte():
    """`_deplacement_narre` : détecte les entrées de lieu narrées (« pénètre
    dans la grotte », « entre dans la salle ») — et PAS les micro-déplacements
    libres (« il entre dans une auberge »)."""
    from server.llm.orchestrator import _deplacement_narre

    assert _deplacement_narre(
        "Margoth'r pénètre dans la grotte de Nulentok. L'air est humide.")
    assert _deplacement_narre(
        "Le groupe arrive enfin devant la grotte de Nulentok.")
    assert _deplacement_narre(
        "Il entre dans la salle d'audience et s'arrête.")
    # Micro-déplacements libres d'une même zone : PAS un déplacement de lieu.
    assert _deplacement_narre(
        "Margoth quitte l'auberge et se dirige vers l'atelier du forgeron."
    ) is None
    # Départ d'une salle du donjon (partie ae358455 : « quitte la salle
    # (1,0) par la porte est » narré sans outil — la carte restait en (1,0)).
    assert _deplacement_narre(
        "Margoth'r quitte la salle des myconides et se dirige vers le puits."
    )


def test_budget_inventaire_ajouter_4():
    """La remise d'ouverture porte DEUX objets de quête : le quota passe
    3→4 (partie ae358455 : le parchemin refusé à 3/3, narré « ajouté »
    pourtant)."""
    from server.llm.orchestrator import _BUDGET_OUTILS_TOUR

    assert _BUDGET_OUTILS_TOUR.get("inventaire_ajouter") == 4


def test_action_deplacement_dans_donjon_detectee():
    """D0 (partie ae358455) : « j'avance prudemment dans la grotte » dans un
    donjon actif avec portes ouvertes réclame une résolution mécanique — la
    décision « narrer » se fiait à la narration désynchronisée et la carte
    restait figée en (0,0)."""
    from server.llm.orchestrator import _action_deplacement_dans_donjon

    etat = _etat_7177()   # donjon actif, salle (0,0), porte EST ouverte
    assert _action_deplacement_dans_donjon(
        "J'avance prudemment dans la grotte", etat) is True
    assert _action_deplacement_dans_donjon(
        "Je continue sur la route vers la grotte de Nulentok", etat) is True
    assert _action_deplacement_dans_donjon(
        "J'examine la grotte et j'avance prudemment", etat) is True
    # Pas un déplacement : dialogue, perception seule, ou combat.
    assert _action_deplacement_dans_donjon(
        "Je continue le combat", etat) is False
    assert _action_deplacement_dans_donjon(
        "J'écoute aux portes", etat) is False
    assert _action_deplacement_dans_donjon(
        "Je salue Teleshann", etat) is False
    # Hors donjon (aucun donjon actif) : la re-consultation ne s'applique pas.
    etat["donjon"] = {}
    assert _action_deplacement_dans_donjon(
        "J'avance prudemment dans la grotte", etat) is False


def test_objets_remettes_narration():
    """Partie e55cc855 : la remise était narrée (« range la fiole de vérité…
    dans son sac ») sans JAMAIS appeler `inventaire_ajouter`. L'extracteur
    retrouve les objets de quête remis ; une mention sans remise ne compte
    pas."""
    from server.llm.orchestrator import _objets_remettes_narration

    objets = _objets_remettes_narration(
        "Margoth range la fiole de vérité, le parchemin de la route et la "
        "baguette de téléportation dans son sac à dos.")
    cles = [o.lower() for o in objets]
    assert any("fiole" in c for c in cles), objets
    assert any("parchemin" in c for c in cles), objets
    assert any("baguette" in c for c in cles), objets

    # Remise main à la main.
    assert _objets_remettes_narration(
        "Thukmuul tend la fiole de vérité vers Margoth.")

    # Mention SANS remise : rien.
    assert _objets_remettes_narration(
        "La Couronne de Mystra est entre les mains de Nulentok.") == []
    # Futur (« donnera ») : pas une remise présente.
    assert _objets_remettes_narration(
        "Elle vous donnera la baguette au retour de la Couronne.") == []


def test_objets_remettes_narration_verbes_tirage_et_anaphore():
    """Partie b59b4a9a (intro Crown avec le code ancré) : « Thukmuul SORT de
    ses vêtements une fiole de vérité et un parchemin de la route. Elle
    DÉPOSE les objets dans vos mains. » — ni « sort » ni « dépose » n'étaient
    des verbes de remise, et « les objets » (anaphore) ne nommait rien :
    l'inventaire de quête restait VIDE après l'intro."""
    from server.llm.orchestrator import _objets_remettes_narration

    # Verbe « sort » (tirer un objet) + anaphore « dépose les objets ».
    objets = _objets_remettes_narration(
        "Thukmuul sort de ses vêtements une fiole de vérité et un parchemin "
        "de la route. Elle dépose les objets dans vos mains.")
    cles = [o.lower() for o in objets]
    assert any("fiole" in c for c in cles), objets
    assert any("parchemin" in c for c in cles), objets

    # Le NOM « un sort » (sortilège) n'est PAS le verbe : pas de tirage
    # fantôme, et la mention de la Couronne ne doit pas être rapatriée par
    # l'anaphore de la phrase suivante.
    assert _objets_remettes_narration(
        "La Couronne est entre les mains de Nulentok. Il lance un sort de "
        "feu puis s'avance. Elle dépose les objets sur la table.") == []

    # « sort une dague » reste un verbe de tirage (« confie la cle » captée).
    objets2 = _objets_remettes_narration(
        "Il sort une dague de sa ceinture. Elle confie la cle de fer à Margoth.")
    assert any("cle" in o.lower() for o in objets2), objets2


def test_tolérance_frappe_noms_bestiaire():
    """Partie ae358455 : le MJ a passé « Perceur, Perceur, Perceur, Percer,
    Perceur, Perce » — la coquille « Percer » REFUSAIT l'engagement EN ENTIER
    et le combat était narré en prose sans mécanique. La résolution tolère
    désormais les coquilles (difflib ≥ 0.85) tout en refusant les inventions
    réelles."""
    import asyncio

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools
    from server.tools.monstres import _find_monstre_strict

    # Bestiaire RÉEL (data/bestiaire.json) — le test cible la résolution
    # contre les créatures officielles.
    d = os.path.abspath(os.path.join("server", "data"))
    tools = discover_tools()
    ctx = ToolContext(partie_id=PID, joueur="alain", data_dir=d)

    # Une coquille → résolue vers le monstre du bestiaire.
    m = _find_monstre_strict(ctx, "Percer")
    assert m is not None, "« Percer » doit résoudre vers « Perceur »"
    assert "perceur" in str(m.get("nom", "")).lower(), m.get("nom")

    # Une invention réelle → toujours refusée.
    assert _find_monstre_strict(ctx, "Gobeleen géant des glaces") is None
    assert _find_monstre_strict(ctx, "Dragon de feu volant") is None

    # Bout en bout : l'engagement avec une coquille n'est plus refusé.
    r = asyncio.run(invoke_tool(
        tools["engager_combat"], ctx, {"monstres": "Squelette, Squelete"}))
    assert "refusés" not in r.text[:200], r.text[:200]


def test_inventaire_objet_quete_hors_catalogue():
    """Parties 7177d819 / ae358455 / e55cc855 : la fiole de vérité et le
    parchemin de la route sont HORS catalogue PHB — `inventaire_ajouter`
    REFUSAIT l'ajout sans `poids` (ok=True, avertissement, mais RIEN ajouté)
    et l'objet narré « remis » n'atteignait JAMAIS l'inventaire. Un objet de
    quête est désormais ajouté avec un poids léger par défaut (100 g)."""
    import asyncio
    import json as _json

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    # Un data_dir avec le manifeste du scénario (les requis) + une fiche.
    d = os.path.abspath(os.path.join("server", "data"))
    fiches = os.path.join(d, "fiches")
    os.makedirs(fiches, exist_ok=True)
    pid_frappe = "test_frappe_inv"
    fiche_path = os.path.join(fiches, "fiche_utturgut.json")
    with open(fiche_path, "w", encoding="utf-8") as f:
        _json.dump({"nom": "Utturgut", "race": "Demi-orc",
                    "classe": "Barbare", "niveau": 1, "pv": 16,
                    "pv_max": 16, "ca": 15,
                    "carac": {"FOR": 17, "DEX": 10, "CON": 14,
                              "INT": 11, "SAG": 9, "CHA": 16},
                    "sauvegardes": {"Vigueur": 4, "Reflexes": 0,
                                    "Volonte": -1},
                    "bab": 1,
                    "conditions": [], "inventaire": [],
                    "joueur": "alain"}, f, ensure_ascii=False)
    tools = discover_tools()
    try:
        ctx = ToolContext(partie_id=pid_frappe, joueur="alain", data_dir=d)

        # Objet de quête HORS catalogue, sans poids : ajouté (poids défaut).
        r = asyncio.run(invoke_tool(tools["inventaire_ajouter"], ctx, {
            "nom": "Utturgut", "objet": "fiole de vérité",
            "portee": "quete"}))
        with open(fiche_path, encoding="utf-8") as f:
            fic = _json.load(f)
        inv = [i for i in (fic.get("inventaire") or [])
               if "fiole" in str(i.get("nom", "")).lower()]
        assert inv, f"la fiole doit être dans l'inventaire : {r.text[:200]}"
        assert inv[0].get("portee") == "quete"
        assert inv[0].get("partie") == pid_frappe
    finally:
        try:
            os.remove(fiche_path)
        except OSError:
            pass


def test_inventaire_objet_requis_portee_forcee_quete():
    """Partie e55cc855 : la baguette de téléportation (objet REQUIS de
    l'étape 2) ajoutée portee="permanent" par le MJ — invisible de
    l'inventaire de quête, la progression restait bloquée. La portée d'un
    objet REQUIS est forcée à « quête »."""
    import asyncio
    import json as _json

    from server.tools.base import ToolContext, invoke_tool
    from server.tools.registry import discover_tools

    d = os.path.abspath(os.path.join("server", "data"))
    fiches = os.path.join(d, "fiches")
    pid_frappe = "test_frappe_portee"
    fiche_path = os.path.join(fiches, "fiche_utturgut.json")
    with open(fiche_path, "w", encoding="utf-8") as f:
        _json.dump({"nom": "Utturgut", "race": "Demi-orc",
                    "classe": "Barbare", "niveau": 1, "pv": 16,
                    "pv_max": 16, "ca": 15,
                    "carac": {"FOR": 17, "DEX": 10, "CON": 14,
                              "INT": 11, "SAG": 9, "CHA": 16},
                    "sauvegardes": {"Vigueur": 4, "Reflexes": 0,
                                    "Volonte": -1},
                    "bab": 1,
                    "conditions": [], "inventaire": [],
                    "joueur": "alain"}, f, ensure_ascii=False)
    tools = discover_tools()
    try:
        # L'état de partie porte la quête + le donjon : la découverte du
        # manifeste (requis de l'étape 2) en a besoin pour le forçage quête.
        from server.game.state import PartyState as _PS
        _PS(data_dir=d, partie_id=pid_frappe).save({
            "meta": {"titre": "test"}, "phase": "exploration",
            "quete": {"source": "[ro_the_crown_of_mystra] /data/scenarios"},
            "donjon": {"id": "La Couronne de Mystra"},
        })
        ctx = ToolContext(partie_id=pid_frappe, joueur="alain", data_dir=d)

        r = asyncio.run(invoke_tool(tools["inventaire_ajouter"], ctx, {
            "nom": "Utturgut", "objet": "Le Beljuril",
            "portee": "permanent"}))
        with open(fiche_path, encoding="utf-8") as f:
            fic = _json.load(f)
        inv = [i for i in (fic.get("inventaire") or [])
               if "beljuril" in str(i.get("nom", "")).lower()]
        assert inv, f"le Beljuril doit être dans l'inventaire : {r.text[:200]}"
        # La portée est FORCÉE à quête (objet REQUIS du scénario).
        assert inv[0].get("portee") == "quete", inv[0]
        assert inv[0].get("partie") == pid_frappe
    finally:
        try:
            os.remove(fiche_path)
        except OSError:
            pass
