"""Arbitrage VRAM LLM ↔ ComfyUI — unload du modèle AVANT une génération
d'image, rechargement APRÈS (server/gpu.py).

Ces tests verrouillent le comportement qui a motivé la feature : avec
Qwen3.5-9B-Q4_K_M-MTP chargé (6 423 Mo mesurés sur 8 192 Mo), ComfyUI n'a
que ~950 Mo et bascule en fallback CPU.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import gpu  # noqa: E402


class _Journal:
    """Enregistre les appels unload/load du modèle simulé."""

    def __init__(self, delai: float = 0.0, echec: bool = False):
        self.appels: list[str] = []
        self._delai = delai
        self._echec = echec

    async def unload(self) -> bool:
        if self._delai:
            await asyncio.sleep(self._delai)
        self.appels.append("unload")
        return not self._echec

    async def load(self) -> bool:
        self.appels.append("load")
        return True


@pytest.fixture(autouse=True)
def _etat_propre():
    """gpu.py garde des compteurs et des tâches en globals : on remet à zéro.

    Le `Condition` et le `Lock` module-level sont liés à la PREMIÈRE event
    loop qui les utilise : pytest-asyncio en crée une par test. Il faut donc
    les recréer, sinon `RuntimeError: bound to a different event loop`.
    """
    gpu._cv = asyncio.Condition()
    gpu._model_lock = asyncio.Lock()
    gpu._turns = 0
    gpu._jobs = 0
    gpu._tour_tasks = set()
    gpu._unload_llm = None
    gpu._load_llm = None
    gpu._unload_before_comfy = False
    gpu._reload_delay_s = 3.0
    gpu._reload_task = None
    gpu._reload_needed = False
    yield
    if gpu._reload_task is not None and not gpu._reload_task.done():
        gpu._reload_task.cancel()
    gpu._reload_task = None
    gpu._turns = 0
    gpu._jobs = 0
    gpu._tour_tasks = set()


# --------------------------------------------------------------------------- #
#  1. Unload avant image / rechargement après
# --------------------------------------------------------------------------- #
async def test_unload_avant_image_hors_tour():
    """Image en arrière-plan (aucun tour actif) → modèle déchargé d'abord."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    await gpu.comfy_begin()
    # Le unload doit avoir eu lieu AVANT que la génération démarre.
    assert j.appels == ["unload"]
    await gpu.comfy_end()

    # Rechargement après la génération.
    await asyncio.sleep(0.05)
    assert j.appels == ["unload", "load"]


async def test_aucun_rechargement_si_desactive():
    """`before_comfy: false` → aucun unload, aucun reload (comportement actuel)."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=False, reload_delay_s=0)

    await gpu.comfy_begin()
    await gpu.comfy_end()
    await asyncio.sleep(0.05)
    assert j.appels == []


# --------------------------------------------------------------------------- #
#  2. 🛡️ NON-réentrance : une génération demandée PAR le tour LLM
#     ne doit JAMAIS décharger le modèle (il est en cours de streaming)
# --------------------------------------------------------------------------- #
async def test_pas_dunload_pour_image_demandee_par_le_tour():
    """Tool du MJ (illustration_scene…) : le modèle est en flux, on n'y touche pas."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    await gpu.turn_begin()          # le tour LLM est actif (même tâche)
    await gpu.comfy_begin()         # ré-entrance légitime : n'attend pas
    assert j.appels == []           # ← pas de unload
    await gpu.comfy_end()
    await gpu.turn_end()

    await asyncio.sleep(0.05)
    assert j.appels == []           # ni unload ni reload


async def test_image_attend_la_fin_du_tour_avant_de_decharger():
    """Règle 1 : une image hors tour attend la fin du tour PUIS décharge."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    async def _tour():
        await gpu.turn_begin()
        await asyncio.sleep(0.1)     # tour un peu long
        assert j.appels == []        # rien pendant le tour
        await gpu.turn_end()

    t = asyncio.create_task(_tour())
    await asyncio.sleep(0.02)
    await gpu.comfy_begin()          # doit attendre la fin du tour
    assert j.appels == ["unload"]    # déchargé seulement après
    await gpu.comfy_end()
    await t


# --------------------------------------------------------------------------- #
#  3. 🛡️ Course entre unload d'image et tour concomitant
# --------------------------------------------------------------------------- #
async def test_tour_attend_un_unload_en_vol():
    """🛡️ Course : un tour lancé PENDANT l'unload d'image attend la fin de
    l'appel réseau au lieu d'entrer dans le tour sans modèle.

    Les deux opérations sont lancées concurremment (comme en production : une
    image d'arrière-plan et un message joueur qui arrivent ensemble). L'ordre
    d'ENTRÉE est libre — ce qui doit être garanti, c'est l'exclusion : le tour
    n'est jamais actif pendant l'unload.
    """
    journal: list[str] = []

    async def _unload_lent() -> bool:
        journal.append("unload_debut")
        await asyncio.sleep(0.3)
        journal.append("unload_fin")
        return True

    gpu.configure_llm(unload=_unload_lent, load=_Journal().load,
                      before_comfy=True, reload_delay_s=0)

    async def _tour():
        await gpu.turn_begin()
        # ⚠️ Point de contrôle : si le verrou modèle ne marchait pas, le tour
        # serait entré ici, PENDANT l'unload → modèle perdu en cours de route.
        journal.append("tour_actif_pendant_unload"
                       if "unload_debut" in journal and "unload_fin" not in journal
                       else "tour_propre")
        await gpu.turn_end()

    async def _image():
        await gpu.comfy_begin()      # décharge (0,3 s) puis « génère »
        await asyncio.sleep(0.3)
        await gpu.comfy_end()        # libère le GPU → le tour peut démarrer

    t_img = asyncio.create_task(_image())
    await asyncio.sleep(0.05)        # l'unload est en vol
    t_tour = asyncio.create_task(_tour())
    await asyncio.gather(t_img, t_tour)

    assert "tour_actif_pendant_unload" not in journal
    assert gpu.turns_actifs() == 0


async def test_reload_annule_si_tour_demarre_pendant_le_delai():
    """Un tour dans la fenêtre de délai annule le rechargement inutile."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=5)

    await gpu.comfy_begin()
    await gpu.comfy_end()            # programme un reload dans 5 s
    await gpu.turn_begin()           # le tour reprend la main
    await asyncio.sleep(0.05)
    assert j.appels == ["unload"]    # toujours pas de load
    await gpu.turn_end()


# --------------------------------------------------------------------------- #
#  4. Robustesse
# --------------------------------------------------------------------------- #
async def test_unload_echoue_la_generation_procede():
    """Un unload en erreur ne doit pas empêcher l'image d'être générée."""
    j = _Journal(echec=True)
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    await gpu.comfy_begin()          # ne lève pas
    await gpu.comfy_end()
    await asyncio.sleep(0.05)
    # Échec → pas de reload programmé (le modèle est supposé encore chargé).
    assert j.appels == ["unload"]


async def test_sans_hooks_aucun_unload():
    """Hooks non branchés (tests, backend sans unload) : aucun crash."""
    gpu.configure_llm(unload=None, load=None, before_comfy=True, reload_delay_s=0)
    await gpu.comfy_begin()
    await gpu.comfy_end()
    assert gpu.turns_actifs() == 0


async def test_images_simultanees_rechargement_unique():
    """Deux images qui se chevauchent → un seul rechargement final."""
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    await gpu.comfy_begin()
    await gpu.comfy_begin()
    await gpu.comfy_end()
    await gpu.comfy_end()
    await asyncio.sleep(0.1)
    assert j.appels.count("load") == 1


# --------------------------------------------------------------------------- #
#  5. 🛡️ RÉGRESSION — deadlock du verrou modèle (trouvé en test réel)
# --------------------------------------------------------------------------- #
async def test_turn_begin_comme_main_py_pas_de_deadlock():
    """Reproduit EXACTEMENT l'appel de main.py::_turn_begin.

    Historique : `_turn_begin` faisait
        async with _unload_guard: await _gpu.turn_begin()
    alors que `_unload_guard` EST le verrou modèle et que `turn_begin`
    l'acquiert déjà. asyncio.Lock n'étant pas réentrant, le MJ deadlockait
    sur le premier message joueur. Le timeout court fait échouer le test au
    lieu de le laisser pendre.
    """
    j = _Journal()
    gpu.configure_llm(unload=j.unload, load=j.load, before_comfy=True, reload_delay_s=0)

    async def _turn_begin_main_py():
        # main.py: _cancel_pending_unload() puis turn_begin — SANS le verrou.
        await asyncio.wait_for(gpu.turn_begin(), timeout=2.0)
        await gpu.turn_end()

    await _turn_begin_main_py()          # ne doit pas expirer
    assert gpu.turns_actifs() == 0


async def test_unload_differend_sous_verrou_modele_reste_possible():
    """Le chemin `main.py::_delayed_unload_task` (verrou + unload direct) doit
    toujours fonctionner : il n'imbrique PAS `turn_begin`, donc pas de
    deadlock — c'est le seul endroit où `_unload_guard` reste légitime."""
    j = _Journal()

    async def _unload():
        j.appels.append("unload")
        return True

    gpu.configure_llm(unload=_unload, load=j.load, before_comfy=True, reload_delay_s=0)

    async def _unload_diffe(_app=None):
        async with gpu.model_lock():
            if gpu.turns_actifs() > 0:
                return
            await _unload()

    await asyncio.wait_for(_unload_diffe(), timeout=2.0)
    assert j.appels == ["unload"]
