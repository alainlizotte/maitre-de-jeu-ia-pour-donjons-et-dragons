"""Pré-génération des scènes de scénario : cache par slug + lecture par
`illustration_scene`.

Le MJ/os déploie `scripts/pregen_scenes.py` pour chauffer à l'avance les
illustrations des scènes marquantes d'un scénario. `illustration_scene` doit
servir ensuite ces images INSTANTANÉMENT (cache hit) plutôt que de relancer
ComfyUI à chaud.

Vérifié ici :
- `_slug_image` est le même pour titre/description → clé de cache stable.
- `_scene_pregen_cache` renvoie le chemin d'un PNG prégénéré quand le slug du
  manifest correspond et que le fichier existe ; None sinon.
- `_candidats_du_pdf` extrait des candidats narratifs d'un texte de type PDF.

Usage : py -m pytest tests/test_pregen_scenes.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import _slug_image, _scene_pregen_cache  # noqa: E402

PID = "test_pregen"


def _ctx(d: str) -> ToolContext:
    return ToolContext(partie_id=PID, joueur="alain", data_dir=d)


def test_slug_stable():
    """Le slug ne dépend ni des accents, ni de la casse, ni des espaces —
    c'est la clé qui relie le manifest de pré-génération et l'appel runtime."""
    assert _slug_image("L'autel maudit") == "l_autel_maudit"
    assert _slug_image("L'Autel Maudit") == "l_autel_maudit"
    assert _slug_image("l’autel maudit  ") == "l_autel_maudit"
    # Description seule, plus longue → slug tronqué mais stable.
    desc = "Le héros affronte le gardien de pierre dans la caverne inondée"
    assert _slug_image(desc) == _slug_image(desc + "   ")
    assert _slug_image("") == "scene"


def test_scene_pregen_cache_hit():
    """Un manifest + un PNG présents → `_scene_pregen_cache` renvoie le chemin."""
    d = tempfile.mkdtemp(prefix="dnd35_pregen_")
    pregen = os.path.join(d, "images_scenes", "pregen")
    os.makedirs(pregen, exist_ok=True)
    slug = _slug_image("Le trésor du dragon")
    fname = f"scen_00_{slug}.png"
    open(os.path.join(pregen, fname), "w").close()  # placeholder PNG
    with open(os.path.join(pregen, "scen.json"), "w", encoding="utf-8") as f:
        json.dump({slug: {"titre": "Le trésor du dragon",
                          "description": "un tas d'or immense",
                          "file": fname}}, f)
    ctx = _ctx(d)
    hit = _scene_pregen_cache(ctx, "", "Le trésor du dragon")
    assert hit is not None and hit.endswith(fname)


def test_scene_pregen_cache_titre_ou_desc():
    """Titre OU description doivent matcher le même slug que le manifest."""
    d = tempfile.mkdtemp(prefix="dnd35_pregen_")
    pregen = os.path.join(d, "images_scenes", "pregen")
    os.makedirs(pregen, exist_ok=True)
    slug = _slug_image("L'autel maudit")
    fname = f"a_{slug}.png"
    open(os.path.join(pregen, fname), "w").close()
    with open(os.path.join(pregen, "scen.json"), "w", encoding="utf-8") as f:
        json.dump({slug: {"titre": "L'autel maudit",
                          "description": "autel maudit",
                          "file": fname}}, f)
    assert _scene_pregen_cache(_ctx(d), "L'autel maudit", "") is not None
    assert _scene_pregen_cache(_ctx(d), "", "L'Autel maudit") is not None


def test_scene_pregen_cache_absent():
    """Aucun manifest / slug inconnu → renvoie None (on génère à chaud)."""
    d = tempfile.mkdtemp(prefix="dnd35_pregen_")
    assert _scene_pregen_cache(_ctx(d), "scène inconnue", "") is None
    pregen = os.path.join(d, "images_scenes", "pregen")
    os.makedirs(pregen, exist_ok=True)
    with open(os.path.join(pregen, "scen.json"), "w", encoding="utf-8") as f:
        json.dump({"autre_slug": {"file": "x.png"}}, f)  # fichier absent
    assert _scene_pregen_cache(_ctx(d), "Le trésor du dragon", "") is None


def test_candidats_du_pdf():
    """Un texte narratif produit des candidats de scène non vides et propres."""
    from scripts.pregen_scenes import _candidats_du_pdf
    texte = (
        "Overview The adventure begins with characters hearing about the "
        "Shroud disturbing necromancer. Then the group travels to the crypt. "
        "\n---\n\n"
        "La lumière révèle enfin l'autel maudit au cœur de la caverne. "
        "Les héros préparent l'assaut final contre le gardien de pierre."
    )
    candidats = _candidats_du_pdf(texte, limite=3)
    assert isinstance(candidats, list)
    assert len(candidats) >= 1
    assert all(isinstance(c, str) and len(c) >= 25 for c in candidats)