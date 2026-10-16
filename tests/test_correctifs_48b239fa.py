# -*- coding: utf-8 -*-
"""Régressions de la partie 48b239fa (« Test 15 oct » — Dues For The Dead).

Deux dérapages réels :

1. **« j'explore » = téléportation** : « J'explore la fosse minutieusement »
   (groupe DANS la fosse (-1,0), aucune direction) a déclenché DEUX
   déplacements (retour (0,0) puis nord → salle des banquets) sans jamais
   fouiller la fosse. Chaîne : `_ACTION_DEPLACEMENT_RE` classait le nu
   « j'explore » comme un déplacement → la décision D0 FORCAIT
   `carte_donjon_explorer` (« ne réponds PAS narrer ») → le modèle
   inventait une direction.

2. **Ouverture DIRECTEMENT dans le donjon** : Dues For The Dead démarre À
   PHLAN (briefing des fidèles de Kelemvor au porche du cimetière), mais
   l'ancre « LIEU DE DÉPART CANONIQUE » plaçait la scène d'ouverture en
   salle (0,0) des catacombes et le filet d'entrée forcée
   (`or not histoire`) téléportait le groupe au premier tour.

Correctifs couverts ici :
- `_ACTION_DEPLACEMENT_RE` : « j'explore » n'est un déplacement QUE avec
  une direction cardinale explicite (b59b4a9a conservé) ;
- workflows ComfyUI : `PreviewImage` partout, plus aucun `SaveImage*` —
  rien n'est écrit dans le dossier Output de ComfyUI (`type: "temp"`) ;
- `_lieu_depart_canonique` : ancre HORS DU DONJON quand le manifeste porte
  `ouverture_hors_donjon` (comportement inchangé sinon) ;
- manifeste Dues For The Dead : étape pré-donjon au porche, lisible par
  `objectifs_quete` (pnj sans salle).

Usage : py -m pytest tests/test_correctifs_48b239fa.py -q
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.llm.orchestrator import (               # noqa: E402
    _action_deplacement_dans_donjon,
)

# Racine data réelle du dépôt (manifestes enrichis).
DATA_REEL = Path(__file__).resolve().parent.parent / "server" / "data"

PID = "test_48b239fa"


def _etat_d0(courant=(-1, 0)) -> dict:
    """État minimal : donjon actif, salle courante à porte(s) ouverte(s)."""
    return {
        "phase": "exploration",
        "donjon": {
            "id": "Catacombes du Cimetière de Valhingen",
            "courant": list(courant),
            "grille": [
                {"x": courant[0], "y": courant[1], "type": "puits",
                 "visitee": True, "portes": {"est": True}},
                {"x": courant[0] + 1, "y": courant[1], "type": "entrée",
                 "portes": {"ouest": True, "nord": True}},
            ],
        },
        "pj": [{"nom": "Morgoth"}],
    }


# --------------------------------------------------------------------------- #
#  1. D0 : « j'explore la fosse » = fouille SUR PLACE, pas un déplacement
# --------------------------------------------------------------------------- #

def test_d0_explore_sans_direction_n_est_pas_un_deplacement():
    """« J'explore la fosse minutieusement » (SANS direction) ne doit plus
    forcer `carte_donjon_explorer` — c'est une fouille sur place."""
    etat = _etat_d0()
    for action in (
        "J'explore la fosse minutieusement",
        "j'explore la salle",
        "J'explore minutieusement la fosse aux os",
        "J'explore les ossements, puis je remonte sur la lèvre de la fosse",
    ):
        assert not _action_deplacement_dans_donjon(action, etat), action


def test_d0_explore_avec_direction_reste_un_deplacement():
    """Le cas d'origine b59b4a9a (« j'explore la galerie vers l'est ») doit
    TOUJOURS réclamer l'outil de déplacement."""
    etat = _etat_d0(courant=(1, 0))
    for action in (
        "Je demande aux myconides ce qui leur est arrivé, puis j'explore la "
        "galerie vers l'est",
        "j'explore la galerie au nord",
        "J'explore le couloir à l'ouest",
        "J'explore le passage est",
    ):
        assert _action_deplacement_dans_donjon(action, etat), action


def test_d0_formes_canoniques_conservees():
    """Les verbes de déplacement historiques restent détectés."""
    etat = _etat_d0()
    for action in (
        "Je vais au ouest",
        "On avance prudemment dans le couloir",
        "Nous avançons vers la sortie",
        "Je pars de Silverymoon et je prends la route vers la grotte",
    ):
        assert _action_deplacement_dans_donjon(action, etat), action


def test_d0_fouille_et_dialogue_ne_declenchent_pas():
    """Fouille / dialogue : pas de déplacement forcé (b59b4a9a conservé)."""
    etat = _etat_d0()
    for action in (
        "Je fouille les champignons et je ramasse un éclat",
        "Je demande aux myconides ce qui leur est arrivé",
        "Qui êtes-vous ? racontez-moi Nulentok",
    ):
        assert not _action_deplacement_dans_donjon(action, etat), action


# --------------------------------------------------------------------------- #
#  2. Ancre d'ouverture HORS DU DONJON (`ouverture_hors_donjon`)
# --------------------------------------------------------------------------- #

MANIFEST_HORS_DJ = {
    "id": "t48b_donjon",
    "scenario": ["t48b_scenario", "t48b_scenario_ch01"],
    "donjon_id": "Catacombes du test 48b",
    "titre": "Test 48b239fa",
    "depart": {"nom": "Phlan", "x": 96, "y": 44},
    "ouverture_hors_donjon": True,
    "etages": [
        {"nom": "Galleries hautes", "entree": [0, 0], "salles": [
            {"x": 0, "y": 0, "type": "entrée",
             "portes": {"nord": True, "ouest": True},
             "description": (
                 "L'escalier de pierre descend depuis le porche du "
                 "cimetière vers une galerie voûtée."),
             "pnj": ["Cassyt (acolyte de Kelemvor, guide des visiteurs)"],
             "note": "Le clerc confie le groupe à Cassyt."},
        ]},
    ],
    "etapes": [],
}


def _data_dir_48b(tmp_path: Path, manifest: dict) -> Path:
    d = tmp_path / "scenarios" / "Test"
    d.mkdir(parents=True)
    (d / "t48b.donjon.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return tmp_path


def _etat_48b(quete_source: str = "[t48b_scenario] /data/scenarios/x.pdf",
              histoire=None, donjon=None) -> dict:
    return {
        "histoire": histoire or [],
        "donjon": donjon or {},
        "quete": {"source": quete_source, "titre": "Test 48b239fa"},
    }


def test_ancre_hors_donjon(tmp_path):
    """Manifeste `ouverture_hors_donjon` → l'ancre place l'ouverture À
    PHLAN, DEVANT le seuil, et interdit `carte_donjon_entrer`."""
    from server.llm.prompt_builder import _lieu_depart_canonique
    dd = _data_dir_48b(tmp_path, MANIFEST_HORS_DJ)
    ancre = _lieu_depart_canonique(_etat_48b(), str(dd))
    assert ancre, "l'ancre doit rester produite"
    assert "LIEU DE DÉPART CANONIQUE" in ancre
    assert "HORS DU DONJON" in ancre
    assert "Phlan" in ancre
    assert "N'EST PAS encore entré" in ancre
    assert "n'appelle PAS `carte_donjon_entrer`" in ancre
    # PNJ du module toujours injectés (désambiguïsation PJ/PNJ).
    assert "Cassyt" in ancre


def test_ancre_hors_donjon_decor_en_premiere_guillemet(tmp_path):
    """Le premier bloc « … » de l'ancre est la description du seuil —
    `_decor_ancre_absent` s'y accroche pour valider le décor d'intro."""
    from server.llm.prompt_builder import _lieu_depart_canonique
    dd = _data_dir_48b(tmp_path, MANIFEST_HORS_DJ)
    ancre = _lieu_depart_canonique(_etat_48b(), str(dd))
    premier = ancre[ancre.index("«"):]
    assert "escalier de pierre" in premier[:400], ancre[:300]


def test_ancre_hors_donjon_muette_apres_ouverture(tmp_path):
    """Ouverture jouée (histoire non vide) → ancre muette (jamais re-narrée)."""
    from server.llm.prompt_builder import _lieu_depart_canonique
    dd = _data_dir_48b(tmp_path, MANIFEST_HORS_DJ)
    etat = _etat_48b(histoire=[{"evenement": "Début de l'aventure : …"}])
    assert _lieu_depart_canonique(etat, str(dd)) == ""


def test_ancre_hors_donjon_dj_initialise_ancre_normale(tmp_path):
    """Donjon DÉJÀ initialisé (le groupe est entré) → ancre normale, même
    si le manifeste porte le flag (pas de « N'EST PAS encore entré »)."""
    from server.llm.prompt_builder import _lieu_depart_canonique
    dd = _data_dir_48b(tmp_path, MANIFEST_HORS_DJ)
    donjon = {
        "id": "Catacombes du test 48b",
        "grille": [
            {"x": 0, "y": 0, "type": "entrée", "visitee": True,
             "description": "L'escalier de pierre descend depuis le porche.",
             "portes": {"nord": True}},
        ],
    }
    ancre = _lieu_depart_canonique(_etat_48b(donjon=donjon), str(dd))
    assert ancre and "s'y déroule" in ancre
    assert "N'EST PAS encore entré" not in ancre


def test_manifeste_sans_flag_comportement_inchange(tmp_path):
    """SANS le flag : ancre canonique inchangée (scène DANS la salle)."""
    from server.llm.prompt_builder import _lieu_depart_canonique
    man = json.loads(json.dumps(MANIFEST_HORS_DJ))
    man.pop("ouverture_hors_donjon")
    dd = _data_dir_48b(tmp_path, man)
    ancre = _lieu_depart_canonique(_etat_48b(), str(dd))
    assert ancre and "la scène d'ouverture s'y déroule" in ancre
    assert "HORS DU DONJON" not in ancre
    assert "Cassyt" in ancre


def test_manifest_scenario_disque_expose_le_flag(tmp_path):
    """`_manifest_scenario_disque` renvoie le manifeste complet (flag +
    depart) et l'identité d'objet de la salle est conservée entre deux
    lectures rapprochées (test cache de test_correctifs_intro_ancre)."""
    from server.llm.prompt_builder import (
        _entree_manifeste_disque, _manifest_scenario_disque)
    dd = _data_dir_48b(tmp_path, MANIFEST_HORS_DJ)
    etat = _etat_48b()
    man = _manifest_scenario_disque(etat, str(dd))
    assert man and man["ouverture_hors_donjon"] is True
    assert man["depart"]["nom"] == "Phlan"
    s1 = _entree_manifeste_disque(etat, str(dd))
    s2 = _entree_manifeste_disque(etat, str(dd))
    assert s1 is s2 and s1 is man["etages"][0]["salles"][0]


# --------------------------------------------------------------------------- #
#  3. Workflows ComfyUI : aperçu, zéro écriture dans Output
# --------------------------------------------------------------------------- #

def test_workflows_aperceu_sans_sauvegarde_disque():
    """AUCUN workflow ne contient de node de SAUVEGARDE (`SaveImage*`) :
    tout passe par `PreviewImage` → dossier temp de ComfyUI, le dossier
    output/ ne grossit plus (partie 48b239fa)."""
    import server.image.comfyui as comfy
    wf_dir = Path(comfy.__file__).parent / "workflows"
    manifestes = sorted(wf_dir.glob("*.json"))
    assert manifestes, "workflows introuvables"
    for chemin in manifestes:
        graphe = json.loads(chemin.read_text(encoding="utf-8"))
        types = [v.get("class_type") for v in graphe.values()
                 if isinstance(v, dict)]
        assert not any(t.startswith("SaveImage") for t in types), (
            f"{chemin.name} : node de sauvegarde encore présent {types}")
        assert "PreviewImage" in types, (
            f"{chemin.name} : aucun node d'aperçu {types}")


def test_download_png_retransmet_le_type_temp(tmp_path):
    """`_download_png` retransmet `type` de l'entrée d'historique à `/view`
    : un aperçu PreviewImage (type « temp ») se télécharge depuis le
    dossier temp de ComfyUI."""
    from server.image.comfyui import ComfyUIBackend

    class _Resp:
        status_code = 200
        content = b"PNGFICE"

        def raise_for_status(self):
            return None

    captures: list[dict] = []

    class _Client:
        async def get(self, url, params=None):
            captures.append({"url": url, "params": dict(params or {})})
            return _Resp()

    be = ComfyUIBackend.__new__(ComfyUIBackend)
    be._client = _Client()
    dest = str(tmp_path / "sortie.png")
    entry = {"outputs": {"461": {"images": [
        {"filename": "PreviewImage_00001_.png", "subfolder": "",
         "type": "temp"}]}}}
    chemin = asyncio.run(be._download_png(entry, dest))
    assert chemin == dest
    assert Path(dest).read_bytes() == b"PNGFICE"
    assert captures[0]["params"]["type"] == "temp"
    assert captures[0]["params"]["filename"] == "PreviewImage_00001_.png"


# --------------------------------------------------------------------------- #
#  4. Manifeste Dues For The Dead : trame pré-donjon lisible
# --------------------------------------------------------------------------- #

def test_manifest_dues_ouverture_hors_donjon():
    """Le manifeste réel de Dues For The Dead porte le flag + une étape
    pré-donjon AU PORCHE, sans salle, type pnj (évaluable par
    `objectifs_quete`)."""
    man = json.loads(
        (DATA_REEL / "scenarios" / "Divers" / "Dues for the Dead.donjon.json")
        .read_text(encoding="utf-8"))
    assert man.get("ouverture_hors_donjon") is True
    assert str(man.get("depart", {}).get("nom") or "") == "Phlan"
    etapes = man.get("etapes") or []
    assert etapes[0]["cle"] == "porche_cimetiere"
    assert etapes[0]["type"] == "pnj"
    assert not str(etapes[0].get("salle") or "").strip()
    assert "porche du cimetière" in str(etapes[0].get("detail") or "")
    assert etapes[1]["cle"] == "enquete_cassyt"


def test_objectifs_dues_etape_porche_sans_salle():
    """`objectifs_quete` évalue la trame enrichie : l'objectif courant est
    le porche (pnj sans salle → jamais « complet » par visite de salle),
    le reste de la trame suit."""
    from server.game.objectifs import objectifs_quete
    etat = {
        "phase": "exploration",
        "donjon": {},
        "quete": {
            "source": "[divers_dues_for_the_dead] /data/scenarios/x.pdf",
            "titre": "Dues For The Dead",
            "bible": {"etapes_terminees": []},
        },
    }
    res = objectifs_quete(etat, str(DATA_REEL), partie_id=PID)
    titres = [o["titre"] for o in res["objectifs"]]
    assert titres, "trame introuvable depuis le manifeste réel"
    assert titres[0] == "Rencontrer les fidèles de Kelemvor au porche du " \
                        "cimetière"
    assert res["objectif_courant"] == titres[0]
    assert res["objectifs"][0]["statut"] == "en_cours"
    assert res["objectifs"][0]["type"] == "pnj"
    assert res["objectifs"][0]["salle"] == ""
    assert len(titres) == 5 and titres[1].startswith("Enquêter dans les")
