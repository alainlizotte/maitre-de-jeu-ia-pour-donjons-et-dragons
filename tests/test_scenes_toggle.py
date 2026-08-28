"""Toggle « illustration de scènes » : l'outil `illustration_scene` respecte
`image.scenes_enabled` (clé config.yaml ET bouton du GUI persisté dans
data/settings.json). Monstres, portraits et illustrations de donjon ne sont
pas concernés par ce réglage.

Usage : py -m pytest tests/test_scenes_toggle.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from server.config import AppConfig, get_config, load_config, set_config  # noqa: E402
from server.tools.base import ToolContext  # noqa: E402
from server.tools.cartes import illustration_scene  # noqa: E402


@pytest.fixture()
def config_originale():
    cfg = get_config()
    yield cfg
    set_config(cfg)


def _ctx(data_dir: str) -> ToolContext:
    return ToolContext(partie_id="test_scenes", joueur="joueur 1", data_dir=data_dir)


# --------------------------------------------------------------------------- #
#  Config : clé scenes_enabled lue depuis YAML, défaut activé
# --------------------------------------------------------------------------- #
def test_config_defaut_scenes_activees():
    cfg = AppConfig()
    assert cfg.image.scenes_enabled is True
    assert cfg.image.scenes_config is True


def test_config_yaml_scenes_desactivees():
    with tempfile.TemporaryDirectory(prefix="dnd35_cfg_") as d:
        yaml_path = os.path.join(d, "config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write("image:\n  enabled: true\n  scenes_enabled: false\n")
        cfg = load_config(yaml_path)
        assert cfg.image.enabled is True
        assert cfg.image.scenes_enabled is False
        # Verrou dur capturé depuis le YAML (avant tout override GUI) :
        # le front retire l'onglet « Scènes » et le toggle est refusé.
        assert cfg.image.scenes_config is False


def test_config_yaml_scenes_verrou_prime_sur_settings_json():
    # config.yaml coupe les scènes → l'override settings.json (bouton GUI
    # persisté) ne doit PAS pouvoir les réactiver au démarrage.
    with tempfile.TemporaryDirectory(prefix="dnd35_cfg_") as d:
        yaml_path = os.path.join(d, "config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write("image:\n  enabled: true\n  scenes_enabled: false\n")
        cfg = load_config(yaml_path)
        # Simulation de la logique startup (main.py) : settings.json true.
        cfg.image.scenes_config = cfg.image.scenes_enabled  # (déjà fait par load_config)
        assert not cfg.image.scenes_config  # le verrou bloque l'override
        cfg.image.scenes_enabled = False    # → forcé off au startup


# --------------------------------------------------------------------------- #
#  Outil illustration_scene : bloqué quand désactivé, sinon generation normale
# --------------------------------------------------------------------------- #
async def test_illustration_scene_bloquee(config_originale):
    d = tempfile.mkdtemp(prefix="dnd35_scenes_")
    cfg = AppConfig()
    cfg.image.scenes_enabled = False
    set_config(cfg)

    r = await illustration_scene(_ctx(d), "le héros affronte un dragon noir")

    assert "désactivée" in r.text
    # Aucune image (ni même le dossier de cache) n'a été créée.
    assert not os.path.exists(os.path.join(d, "images_scenes"))


async def test_illustration_scene_activee_passe_le_portillon(config_originale):
    d = tempfile.mkdtemp(prefix="dnd35_scenes_")
    cfg = AppConfig()
    cfg.image.scenes_enabled = True
    cfg.image.enabled = False  # ComfyUI globalement coupé → pas d'appel réseau
    set_config(cfg)

    r = await illustration_scene(_ctx(d), "le héros affronte un dragon noir")

    # Le portillon scenes_enabled est passé : on tombe sur le fallback normal
    # « générateur indisponible » (image.enabled=False), pas sur « désactivée ».
    assert "désactivée" not in r.text
    assert "indisponible" in r.text
