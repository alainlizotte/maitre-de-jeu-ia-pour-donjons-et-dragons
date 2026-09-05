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
   porte le tour.
"""
from __future__ import annotations

import asyncio

_cv: asyncio.Condition = asyncio.Condition()
_turns: int = 0           # tours LLM actifs (toutes parties confondues)
_jobs: int = 0            # générations ComfyUI en cours
_tour_tasks: set = set()  # tâches asyncio portant un tour LLM actif


def turns_actifs() -> int:
    """Nombre de tours LLM actuellement en cours (toutes parties)."""
    return _turns


async def turn_begin(timeout_comfy: float = 120.0) -> None:
    """Démarre un tour LLM : attend d'abord la fin des générations ComfyUI
    en cours (borné à `timeout_comfy` — ComfyUI hangé ne doit pas tuer le MJ)."""
    global _turns
    task = asyncio.current_task()
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
    (même tâche asyncio : re-entrance légitime, le LLM attend déjà l'outil)."""
    global _jobs
    task = asyncio.current_task()
    async with _cv:
        if _turns > 0 and task not in _tour_tasks:
            try:
                await asyncio.wait_for(
                    _cv.wait_for(lambda: _turns == 0), timeout_llm
                )
            except asyncio.TimeoutError:
                pass  # table bavarde : on génère quand même après 15 min
        _jobs += 1


async def comfy_end() -> None:
    """Libère le GPU et réveille les tours LLM en attente."""
    global _jobs
    async with _cv:
        _jobs = max(0, _jobs - 1)
        _cv.notify_all()
