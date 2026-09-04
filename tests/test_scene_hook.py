"""Hook automatique post-tour des scènes prégénérées (univers pilote Laelith).

Quand le groupe change de lieu (ou un moment marquant est narré), le server
sert une image de scène PRÉGÉNÉRÉE depuis le manifest — sans jamais appeler
ComfyUI. Les chemins de matching sont testés ici :

- `serve_scene_si_pregen` : match EXACT via `_scene_pregen_cache`, sinon
  match FUZZY (normalisé accent/casse/apostrophe, sous-chaîne de mots) sur le
  manifest du scénario courant.
- Aucune correspondance → None (on ne bloque ni ne génère).

Usage : py -m pytest tests/test_scene_hook.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import (  # noqa: E402
    _slug_image,
    _scene_pregen_cache,
    serve_scene_si_pregen,
)

PID = "test_scene_hook"
SID = "scen_aelith"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def _setup(d: str, scenes: dict[str, str]) -> None:
    """Crée manifest + PNG placeholders pour un scénario de test."""
    pregen = os.path.join(d, "images_scenes", "pregen")
    os.makedirs(pregen, exist_ok=True)
    manifest = {}
    for i, (titre, desc) in enumerate(scenes.items()):
        slug = _slug_image(titre or desc)
        fname = f"{SID}_{i:02d}_{slug}.png"
        open(os.path.join(pregen, fname), "w").close()
        manifest[slug] = {"titre": titre, "description": desc, "file": fname}
    with open(os.path.join(pregen, f"{SID}.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


def test_serve_exact():
    d = tempfile.mkdtemp(prefix="dnd35_hook_")
    _setup(d, {"Le masque de bronze d'Utruz": "masque",
               "Les geôles du Roi-Dieu": "geôles"})
    url = serve_scene_si_pregen(_ctx(d), "Le masque de bronze d'Utruz", "", sid=SID)
    assert url and url.endswith("le_masque_de_bronze_d_utruz.png")
    url2 = _scene_pregen_cache(_ctx(d), "Le masque de bronze d'Utruz", "")
    assert url2 is not None and url2.endswith("le_masque_de_bronze_d_utruz.png")


def test_serve_fuzzy_casse_accent():
    """Le lieu narré par le MJ peut différer casse/accents → fuzzy matche."""
    d = tempfile.mkdtemp(prefix="dnd35_hook_")
    _setup(d, {"Les geôles du Roi-Dieu": "prison sombre"})
    url = serve_scene_si_pregen(_ctx(d), "les geôles du roi dieu", "", sid=SID)
    assert url is not None and url.endswith("les_geoles_du_roi_dieu.png")


def test_serve_fuzzy_sous_chaine():
    """Une phrase de narration contenant le libellé du lieu → fuzzy matche."""
    d = tempfile.mkdtemp(prefix="dnd35_hook_")
    _setup(d, {"Le trésor caché": "coffre d'or"})
    url = serve_scene_si_pregen(
        _ctx(d), "Je fouille le trésor caché dans le mur", "", sid=SID
    )
    assert url is not None and url.endswith("le_tresor_cache.png")


def test_serve_aucune_correspondance():
    """Lieu totalement inconnu du manifest → None (pas de blocage, pas de gen)."""
    d = tempfile.mkdtemp(prefix="dnd35_hook_")
    _setup(d, {"Le trésor caché": "coffre d'or"})
    url = serve_scene_si_pregen(_ctx(d), "Une ruelle inconnue de Laelith", "", sid=SID)
    assert url is None


def test_serve_sid_absent():
    """Pas de manifest du scénario → None, sans crash."""
    d = tempfile.mkdtemp(prefix="dnd35_hook_")
    url = serve_scene_si_pregen(_ctx(d), "Le trésor caché", "", sid="inconnu")
    assert url is None