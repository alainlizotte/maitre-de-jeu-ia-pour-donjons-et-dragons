"""Arbitrage GPU — ComfyUI (images) et llama.cpp (LLM) ne doivent jamais
générer SIMULTANÉMENT sur la même carte graphique (VRAM/CPU partagés).

Primitives :
- `turn_begin()` / `turn_end()` : délimitent un tour LLM (toutes parties) ;
- `comfy_begin()` / `comfy_end()` : délimitent UNE génération ComfyUI.

Règles d'exclusion :
1. une génération d'image lancée HORS tour LLM (illustrations d'arrière-plan,
   portrait à la création de perso, illustrations de salles…) attend la fin
   du tour LLM en cours ;
2. un tour LLM attend (garde-fou temporel — mieux vaut un peu de latence
   qu'un blocage infini si ComfyUI est hangé) la fin des générations déjà
   soumises avant de démarrer ;
3. une génération demandée PENDANT un tour LLM (tool appelé par le MJ, ex.
   `monstre_consulter`) est séquentielle par nature (le LLM attend l'outil) :
   elle ne s'attend JAMAIS elle-même — reconnue via la tâche asyncio qui
   porte le tour ;
4. le modèle LLM est DÉCHARGÉ de la VRAM avant une génération d'image
   demandée hors tour, et RECHARGÉ peu après (`llm.unload_before_image`).
   Ce n'est pas une optimisation mais une nécessité : mesuré sur la config
   de référence (RTX 3060 Ti 8 Go, Qwen3.5-9B-Q4_K_M-MTP, ctx 32k q8_0,
   MTP actif) le modèle chargé occupe 6,27 Go et ne laisse que ~950 Mo —
   de quoi tenir le contexte CUDA mais pas un checkpoint SD. Sans unload,
   ComfyUI bascule en fallback CPU (il tourne en `--lowvram`).

Verrou d'état du modèle (`model_lock()`) : sérialise TOUTES les transitions
VRAM — unload avant image, reload après, unload différé de fin de tour.
`turn_begin()` l'acquiert aussi : un tour ne peut donc pas démarrer au milieu
d'un unload et se retrouver sans modèle. `main.py` réutilise ce verrou pour
son unload différé, ce qui évite deux verrous concurrents (et donc un
interleaving qui perdrait le modèle en plein tour).

Invariant de deadlock : `_cv` n'est JAMAIS tenu pendant l'acquisition de
`_model_lock` (les appels réseau d'unload/reload se font hors verrou) ;
`_model_lock` peut en revanche être tenu pendant l'acquisition de `_cv`
(`turn_begin`).
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

_cv: asyncio.Condition = asyncio.Condition()
_turns: int = 0           # tours LLM actifs (toutes parties confondues)
_jobs: int = 0            # générations ComfyUI en cours
_tour_tasks: set = set()  # tâches asyncio portant un tour LLM actif

# Verrou des transitions d'état du modèle (unload / load réseau).
_model_lock: asyncio.Lock = asyncio.Lock()

# Hooks LLM (branchés par main.py au démarrage, via `configure_llm`).
_unload_llm: Optional[Callable[[], Awaitable[bool]]] = None
_load_llm: Optional[Callable[[], Awaitable[bool]]] = None
_unload_before_comfy: bool = False
_reload_delay_s: float = 3.0
_reload_task: Optional[asyncio.Task] = None
_reload_needed: bool = False   # un unload a eu lieu, le reload est dû


# --------------------------------------------------------------------------- #
#  Branchement des hooks LLM (main.py)
# --------------------------------------------------------------------------- #
def configure_llm(
    unload: Optional[Callable[[], Awaitable[bool]]] = None,
    load: Optional[Callable[[], Awaitable[bool]]] = None,
    before_comfy: bool = False,
    reload_delay_s: float = 3.0,
) -> None:
    """Branche les hooks de (dé)chargement du modèle LLM.

    - `unload` : async () -> bool, décharge le modèle de la VRAM
      (`LLMClient.unload_model`).
    - `load` : async () -> bool, garantit le modèle chargé en VRAM
      (`LLMClient.ensure_model_loaded`).
    - `before_comfy` : décharge avant une génération d'image hors tour.
    - `reload_delay_s` : délai avant rechargement après la dernière image
      (un tour qui démarre dans la fenêtre annule le rechargement).
    """
    global _unload_llm, _load_llm, _unload_before_comfy, _reload_delay_s
    _unload_llm = unload
    _load_llm = load
    _unload_before_comfy = bool(before_comfy and unload is not None)
    _reload_delay_s = max(0.0, float(reload_delay_s))


def model_lock() -> asyncio.Lock:
    """Verrou des transitions d'état du modèle (à réutiliser par l'appelant)."""
    return _model_lock


def _cancel_pending_reload() -> None:
    """Annule un rechargement programmé (un tour ou une image reprend la main)."""
    global _reload_task
    if _reload_task is not None and not _reload_task.done():
        _reload_task.cancel()
    _reload_task = None


async def _decharger_avant_image() -> bool:
    """Décharge le modèle de la VRAM. Vrai si le déchargement a réussi."""
    if _unload_llm is None:
        return False
    async with _model_lock:
        return bool(await _unload_llm())


async def _recharger_apres_image() -> None:
    """Recharge le modèle APRÈS que ComfyUI a relâché la VRAM.

    On attend que la VRAM libre soit suffisante pour charger le modèle LLM
    (~6,4 Go sur cette config), plutôt qu'un délai fixe arbitraire. Cela évite
    de recharger pendant que ComfyUI conserve encore ses allocations CUDA.

    Si un tour démarre (ou une autre image est soumise) dans la fenêtre,
    le rechargement est annulé — le tour/flux se chargera lui-même au bon moment.
    """
    global _reload_task, _reload_needed

    async def _vram_libre_mo() -> int:
        try:
            import subprocess

            p = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, _ = await p.communicate()
            return int(out.decode("utf-8", "replace").strip())
        except Exception:
            return 0

    # Seuil de sécurité : ~6,5 Go libres pour charger Qwen3.5-9B-Q4_K_M-MTP
    # (6,42 Go mesurés) sans pousser ComfyUI à basculer au mauvais moment.
    SEUIL_VRAM_LIBRE_MO = 6500
    POLL = 0.5
    timeout_max = max(0.0, float(_reload_delay_s)) if "_reload_delay_s" in globals() else 30.0
    t0 = asyncio.get_running_loop().time()

    try:
        while True:
            libre = await _vram_libre_mo()
            if libre >= SEUIL_VRAM_LIBRE_MO:
                break

            # Conditions d'annulation
            async with _cv:
                if _turns > 0 or _jobs > 0:
                    return
                if not _reload_needed:
                    return

            # 🛡️ Chantier sécurité : la borne devait s'appliquer « après le
            # délai », mais le garde `timeout_max > 0` la court-circuitait
            # quand `reload_delay_s = 0` — la boucle d'attente VRAM ne
            # sortait alors JAMAIS (reload_delay_s=0 signifie « recharger
            # immédiatement », pas « attendre indéfiniment la VRAM »).
            # Observé en test : test_image_attend_la_fin_du_tour bloqué.
            if (asyncio.get_running_loop().time() - t0) >= timeout_max:
                break  # sécurité : on recharge malgré tout après le délai

            await asyncio.sleep(POLL)

        async with _cv:
            if _turns > 0 or _jobs > 0:
                return
            if not _reload_needed:
                return
            _reload_needed = False

        async with _model_lock:
            if _load_llm is not None:
                await _load_llm()
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        _reload_task = None


def turns_actifs() -> int:
    """Nombre de tours LLM actuellement en cours (toutes parties)."""
    return _turns


async def turn_begin(timeout_comfy: float = 120.0) -> None:
    """Démarre un tour LLM : attend d'abord la fin des générations ComfyUI
    en cours (borné à `timeout_comfy` — ComfyUI hangé ne doit pas tuer le MJ)."""
    global _turns
    # Un tour reprend la main : un rechargement programmé ne sert plus à rien.
    _cancel_pending_reload()
    task = asyncio.current_task()
    # Verrou du modèle : on n'entre jamais dans le tour pendant qu'un unload
    # est en vol (le modèle pourrait disparaître sous les pieds du tour).
    async with _model_lock:
        async with _cv:
            if _jobs > 0:
                try:
                    await asyncio.wait_for(
                        _cv.wait_for(lambda: _jobs == 0), timeout_comfy
                    )
                except asyncio.TimeoutError:
                    pass  # on y va quand même : du lag vaut mieux qu'un MJ mort
            _turns += 1
            if task is not None:
                _tour_tasks.add(task)


async def turn_end() -> bool:
    """Termine un tour LLM. Renvoie True s'il ne reste AUCUN tour actif."""
    global _turns
    task = asyncio.current_task()
    async with _cv:
        _turns = max(0, _turns - 1)
        _tour_tasks.discard(task)
        _cv.notify_all()
        return _turns == 0


async def comfy_begin(timeout_llm: float = 900.0) -> None:
    """Réserve le GPU pour une génération ComfyUI : attend la fin des tours
    LLM actifs — SAUF si la génération est demandée PAR le tour courant
    (même tâche asyncio : re-entrance légitime, le LLM attend déjà l'outil).

    Décharge aussi le modèle LLM quand la génération est demandée HORS tour
    (`llm.unload_before_image`) : sans cette marge, ComfyUI n'a pas assez de
    VRAM pour son checkpoint et bascule en fallback CPU.
    """
    global _jobs, _reload_needed
    task = asyncio.current_task()
    async with _cv:
        if _turns > 0 and task not in _tour_tasks:
            try:
                await asyncio.wait_for(
                    _cv.wait_for(lambda: _turns == 0), timeout_llm
                )
            except asyncio.TimeoutError:
                pass  # table bavarde : on génère quand même après 15 min
        dans_tour = task in _tour_tasks
        turns_apres = _turns
        _jobs += 1
    # Hors verrou `_cv` : l'appel réseau d'unload (≈1 s) ne doit pas geler
    # les tours ni les générations en attente de `_cv`.
    #
    # On ne décharge QUE si plus aucun tour n'est actif : une génération
    # demandée par le tour lui-même (tool du MJ) a le modèle en cours de
    # streaming, et le décharger tuerait le tour ; un timeout de `turns`
    # laisse aussi `_turns > 0` → on s'abstient (plus sûr que de deviner).
    if _unload_before_comfy and not dans_tour and turns_apres == 0:
        try:
            if await _decharger_avant_image():
                _reload_needed = True
        except Exception:                                      # noqa: BLE001
            pass  # génération tout de même : mieux vaut une image lente
            # qu'une image qui n'arrive jamais


async def comfy_end() -> None:
    """Libère le GPU et réveille les tours LLM en attente."""
    global _jobs
    async with _cv:
        _jobs = max(0, _jobs - 1)
        _cv.notify_all()
    # Recharge proactive : le prochain tour ne paie pas les 5-20 s de
    # chargement du modèle. Programmée seulement si un unload a eu lieu, et
    # annulée si un tour démarre avant l'expiration du délai.
    if _unload_before_comfy and _reload_needed:
        _cancel_pending_reload()
        _reload_task = asyncio.create_task(_recharger_apres_image())
