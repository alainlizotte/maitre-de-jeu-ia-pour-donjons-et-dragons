"""Client Ollama — endpoint OpenAI-compatible.

L'app parle à Ollama via l'API `/v1/chat/completions` (compatible OpenAI),
ce qui rend le backend LLM interchangeable : on peut pointer vers OpenAI,
Anthropic (via proxy), ou tout autre endpoint OpenAI-compatible en changeant
`llm.base_url` dans la config — sans toucher au reste du code.

Supporte deux modes d'appel :
- non-streaming (`chat()`)        : utilisé par la boucle de function-calling
  où l'on doit parser une réponse complète avant de décider d'appeler un tool.
- streaming (`stream_chat()`)    : utilisé pour le retour de narration au
  client (tokens poussés en WebSocket au fur et à mesure).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from ..config import LLMConfig

_log = logging.getLogger("dnd35.llm.client")


# --------------------------------------------------------------------------- #
# Thinking stripping — deux formats :
# - Gemma 4 : <|channel>thought...<channel|>
# - Qwen 3/3.5 : <think>...</think> (raisonnement parfois aussi renvoyé par
#   llama.cpp --jinja dans `reasoning_content`, champ qu'on ignore).
# --------------------------------------------------------------------------- #
import re
_THINK_RE = re.compile(r"<\|channel>thought\b.*?<channel\|>", re.DOTALL)
# <think>...</think> fermé.
_THINK_QWEN_RE = re.compile(r"<think\s*>.*?</think\s*>", re.DOTALL)
# <think> NON fermé : tout ce qui suit l'ouverture est du raisonnement —
# la réponse visible est vide (relance corrective côté orchestrateur).
_THINK_QWEN_OUVERT_RE = re.compile(r"<think\s*>.*\Z", re.DOTALL)
# </think> orphelin (raisonnement déjà séparé par le backend, le close-tag
# fuit seul en tête de content).
_THINK_QWEN_ORPHELIN_RE = re.compile(r"</?think\s*>")


def _strip_thinking(text: str, strip_spaces: bool = True) -> str:
    """Supprime les blocs de réflexion (Gemma 4 / Qwen) du texte de réponse.

    `strip_spaces=False` : variante pour le streaming par delta — on ne
    touche PAS aux espaces/retours à la ligne en début/fin de fragment,
    sinon chaque token est dénudé et les mots arrivent collés à l'écran
    (les espaces entre deux tokens étant périphériques, ils disparaissent).
    """
    if not text:
        return text
    out = _THINK_RE.sub("", text)
    out = _THINK_QWEN_RE.sub("", out)
    out = _THINK_QWEN_OUVERT_RE.sub("", out)
    out = _THINK_QWEN_ORPHELIN_RE.sub("", out)
    return out.strip() if strip_spaces else out


def _safe_split(buf: str) -> tuple[str, str]:
    """Détecte un éventuel début de tag thinking en fin de buffer.

    Renvoie (texte_sûre, reste_à_analyser). Le texte sûr peut être yield.
    """
    # Patterns partiels pouvant être le début de `<|channel>thought` ou `<think>`
    markers = (
        "<|channel>tho", "<|channel>th", "<|channel>", "<|chan", "<|ch", "<|c", "<|",
        "<think>", "<think", "<thin", "<thi", "<th", "<t",
        "</think>", "</think", "</thin", "</thi", "</th",
    )
    for m in markers:
        if buf.endswith(m):
            safe = buf[: -len(m)]
            return safe, buf[-len(m):]
    return buf, ""


# Tags d'OUVERTURE / de FERMETURE des canaux thinking (Gemma et Qwen), pour
# la machine à états du streaming.
_THINK_START_TAGS = ("<|channel>thought", "<think>")
_THINK_END_TAGS = ("<channel|>", "</think>")


def _debut_thinking(buf: str) -> int:
    """Index du premier tag d'OUVERTURE thinking dans `buf`, -1 si absent."""
    idxs = [buf.find(t) for t in _THINK_START_TAGS if t in buf]
    return min(idxs) if idxs else -1


def _fin_thinking(buf: str) -> int:
    """Index (après tag) de la première FERMETURE thinking dans `buf`, -1 sinon."""
    idxs = [buf.find(t) + len(t) for t in _THINK_END_TAGS if t in buf]
    return min(idxs) if idxs else -1


def _normaliser_messages(messages: list[Message]) -> list[Message]:
    """Rend la conversation compatible avec les templates strictes (Qwen 3.5).

    La template Jinja du Qwen 3.5 lève une exception dès qu'un message
    `role="system"` n'est pas à l'index 0 (« System message must be at the
    beginning »). Or le serveur injecte des correctifs `role="system"` en fin
    de conversation (reformulation des simulations de tools, narration finale
    forcée…), ce qui faisait planter l'appel.

    Règles appliquées (sans modifier la liste passée en entrée) :
    - tout message `system` hors index 0 → transformé en `role="user"` (le
      texte reste présent dans le contexte, seul le rôle change) ;
    - tout message `role="tool"` « orphelin » (pas immédiatement précédé d'un
      `assistant` avec `tool_calls` — rencontré en mode prompt/compat) →
      transformé en `role="user"` pour que la template puisse le rendre.
    """
    if not messages:
        return messages
    out: list[Message] = []
    precedente_avait_tool_calls = False
    for i, m in enumerate(messages):
        if m.role == "system":
            if i == 0:
                out.append(m)
            else:
                content = m.content or ""
                out.append(
                    Message(
                        role="user",
                        content=(
                            "⚠️ Consigne du Maître du Jeu (contexte) : " + content
                            if content
                            else "⚠️ (Consigne du Maître du Jeu vide.)"
                        ),
                    )
                )
            precedente_avait_tool_calls = False
        elif m.role == "tool":
            if precedente_avait_tool_calls:
                # Résultat natif de function-calling : la template le rattache
                # au tool_call correspondant. On reste « ouvert » car un même
                # message assistant peut déclencher plusieurs tool_calls.
                out.append(m)
            else:
                content = m.content or ""
                out.append(
                    Message(
                        role="user",
                        content=(
                            "📎 Résultat d'outil : " + content
                            if content
                            else "📎 (Résultat d'outil vide.)"
                        ),
                    )
                )
        elif m.role == "assistant":
            precedente_avait_tool_calls = bool(m.tool_calls)
            out.append(m)
        else:  # "user" — l'assistant (rare) reste tel quel
            precedente_avait_tool_calls = False
            out.append(m)

    # Passe finale : ALTERNANCE STRICTE user/assistant. Les templates Jinja
    # strictes (Ministral : « conversation roles must alternate user and
    # assistant roles except for tool calls and results ») lèvent une
    # exception (500) dès que deux messages consécutifs portent le même rôle
    # — typiquement les correctifs système requalifiés en `user` injectés en
    # fin de conversation. On fusionne les doublons consécutifs (contenus
    # concaténés) et on jette les messages vides.
    #
    # ⚠️ NON-MUTATION : la fusion crée un NOUVEAU Message au lieu d'écrire
    # dans `precedent.content`. `messages` partage ses objets avec
    # `session.history` (main.py : `[system] + hist[debut:]`) : muter
    # `precedent` CORROMPAIT l'historique de session puis le fichier
    # chat_<id>.json — bug réel (partie dc4dd5aa) : les correctifs
    # « ⚠️ Consigne du Maître du Jeu » injectés en fin de tour se sont
    # retrouvés fusionnés DANS le message joueur, affichés à la table au
    # rechargement.
    fusion: list[Message] = []
    for m in out:
        if not (m.content or "").strip() and not m.tool_calls:
            continue  # message vide : du bruit de plus pour la template
        precedent = fusion[-1] if fusion else None
        if (
            precedent is not None
            and precedent.role == m.role
            and precedent.role in ("user", "assistant")
            and not precedent.tool_calls
            and not m.tool_calls
        ):
            sep = "\n\n" if precedent.content and m.content else ""
            fusion[-1] = Message(
                role=precedent.role,
                content=(precedent.content or "") + sep + (m.content or ""),
                tool_calls=precedent.tool_calls,
                tool_call_id=precedent.tool_call_id,
                name=precedent.name,
            )
            continue
        fusion.append(m)
    return fusion


# --------------------------------------------------------------------------- #
#  Modèles de messages
# --------------------------------------------------------------------------- #
@dataclass
class Message:
    role: str                   # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: Optional[list[dict[str, Any]]] = None  # fonction-calling natif
    tool_call_id: Optional[str] = None                  # pour role="tool"
    name: Optional[str] = None                           # pour role="tool"

    def to_openai(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class ChatResult:
    """Résultat non-streaming d'un appel `chat()`."""
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    raw: dict[str, Any]


# --------------------------------------------------------------------------- #
#  Client
# --------------------------------------------------------------------------- #
class OllamaClient:
    """Client léger pour l'endpoint OpenAI-compatible d'Ollama."""

    def __init__(self, config: LLMConfig):
        self.cfg = config
        # Timeout généreux pour les LLM locaux sur GPU/CPU ; 0 = infini.
        # 300 s : au-delà, une génération bloquée (slot llama.cpp partagé,
        # modèle en rechargement…) libère le tour au lieu de le figer 10 min.
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatResult:
        """Appel non-streaming. `tools` est le schéma JSON des fonctions."""
        await self.ensure_model_loaded()
        messages = _normaliser_messages(messages)
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature if temperature is not None else self.cfg.temperature,
            "top_p": self.cfg.top_p,
            "presence_penalty": self.cfg.presence_penalty,
            "repetition_penalty": self.cfg.repetition_penalty,
            "stream": False,
        }
        # Sampling llama.cpp (doc Unsloth Qwen3.5) : min_p toujours, top_k si
        # > 0. Ces champs sont ignorés/neutralisés par d'éventuels backends
        # ne les reconnaissant pas (ollama gère les siens via `options`).
        if self.cfg.backend == "llamacpp":
            payload["min_p"] = self.cfg.min_p
            if self.cfg.top_k and self.cfg.top_k > 0:
                payload["top_k"] = self.cfg.top_k
        # Budget de génération (llama.cpp : -1 par défaut ; on borne pour
        # éviter les réponses interminables et libérer le tour plus vite).
        if getattr(self.cfg, "max_tokens", 0) and self.cfg.max_tokens > 0:
            payload["max_tokens"] = self.cfg.max_tokens
        # Options natives Ollama (num_ctx, top_k, …) — calibrées dans config.yaml.
        if self.cfg.options:
            payload["options"] = dict(self.cfg.options)
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        # Retry sur 500 : le modèle peut avoir été déchargé (course multi-tours)
        # ou être en concurrence VRAM avec ComfyUI (illustrations de salles).
        # Backoff croissant pour laisser le temps au chargement / à ComfyUI.
        delays = (0, 3.0, 8.0)
        last_exc: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
                await self.ensure_model_loaded()
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                if resp.status_code != 500:
                    break
                _log.warning("chat 500 (tentative %d/%d)", attempt, len(delays))
                last_exc = None
            except httpx.RequestError as e:
                last_exc = e
                _log.warning("chat réseau erreur (tentative %d/%d): %s", attempt, len(delays), e)
        else:
            if last_exc:
                raise last_exc
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        content = _strip_thinking(msg.get("content", "") or "")
        # Debug: log thinking leaks
        raw_content = msg.get("content", "") or ""
        if raw_content != content:
            _log.info("thinking stripped: %d → %d chars", len(raw_content), len(content))
        # Qwen + llama.cpp --jinja : le raisonnement peut arriver séparément
        # dans `reasoning_content`. Si TOUTE la réponse y est passée (content
        # vide), on le trace — l'orchestrateur relancera (narration vide).
        reasoning = str(msg.get("reasoning_content") or "")
        if not content and reasoning:
            _log.info(
                "réponse thinking-only : %d chars dans reasoning_content, "
                "content vide — relance corrective à prévoir",
                len(reasoning),
            )
        return ChatResult(
            content=content,
            tool_calls=msg.get("tool_calls", []) or [],
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )

    # ------------------------------------------------------------------ #
    async def stream_chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """Streaming SSE. Yield le contenu delta-tokens à mesure qu'ils arrivent.

        Note : on ignore les `tool_calls` en streaming (utilisés seulement par
        la boucle non-streaming de l'orchestrateur).
        """
        await self.ensure_model_loaded()
        messages = _normaliser_messages(messages)
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature if temperature is not None else self.cfg.temperature,
            "top_p": self.cfg.top_p,
            "presence_penalty": self.cfg.presence_penalty,
            "repetition_penalty": self.cfg.repetition_penalty,
            "stream": True,
        }
        if self.cfg.backend == "llamacpp":
            payload["min_p"] = self.cfg.min_p
            if self.cfg.top_k and self.cfg.top_k > 0:
                payload["top_k"] = self.cfg.top_k
        if getattr(self.cfg, "max_tokens", 0) and self.cfg.max_tokens > 0:
            payload["max_tokens"] = self.cfg.max_tokens
        # Options natives Ollama (num_ctx, top_k, …) — calibrées dans config.yaml.
        if self.cfg.options:
            payload["options"] = dict(self.cfg.options)
        if tools:
            payload["tools"] = tools

        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            # Buffer pour striper les blocs thinking en streaming.
            # Les tokens `<|channel>thought`...`<channel|>` arrivent chunk par chunk.
            think_buf = ""
            in_think = False
            # ⏱️ Watchdogs anti-blocage : le read-timeout httpx (600 s) ne
            # protège pas contre un flux qui continue d'envoyer des keep-alives
            # SSE sans jamais produire de contenu (observé en partie réelle :
            # génération Qwen dégénérée, tour MJ figé « en réflexion » pendant
            # des minutes). Deux limites applicatives : durée TOTALE et durée
            # SANS token de contenu. Au déclenchement on conserve le texte
            # partiel déjà émis — le tour se termine au lieu de hang.
            debut = time.monotonic()
            dernier_token = debut
            aiter = resp.aiter_lines().__aiter__()
            while True:
                # ⏳ Borne l'attente de la PROCHAINE ligne : en silence total
                # (requête en file derrière une autre génération du llama.cpp
                # partagé, aucun keep-alive), `aiter_lines()` bloquait jusqu'au
                # timeout httpx (600 s) SANS jamais exécuter les checks
                # ci-dessous — le tour restait figé « Le MJ réfléchit... ».
                try:
                    line = await asyncio.wait_for(
                        anext(aiter), timeout=self.cfg.stale_stream_seconds
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    _log.warning(
                        "stream_chat : aucune ligne depuis %d s — arrêt, "
                        "narration partielle conservée",
                        self.cfg.stale_stream_seconds,
                    )
                    return
                maintenant = time.monotonic()
                if maintenant - debut > self.cfg.max_stream_seconds:
                    _log.warning(
                        "stream_chat : durée totale dépassée (%.0f s > %d s) — "
                        "arrêt, narration partielle conservée",
                        maintenant - debut, self.cfg.max_stream_seconds,
                    )
                    return
                if maintenant - dernier_token > self.cfg.stale_stream_seconds:
                    _log.warning(
                        "stream_chat : aucun token de contenu depuis %.0f s "
                        "(> %d s) — arrêt, narration partielle conservée",
                        maintenant - dernier_token, self.cfg.stale_stream_seconds,
                    )
                    return
                if not line or not line.startswith("data:"):
                    continue
                chunk_str = line[len("data:") :].strip()
                if chunk_str == "[DONE]":
                    return
                try:
                    chunk = json.loads(chunk_str)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") or ""
                    if not content:
                        continue
                    dernier_token = time.monotonic()
                    # Machine à états : détecter les blocs thinking (Gemma
                    # `<|channel>thought...<channel|>` ET Qwen `<think>...
                    # </think>`) et ne yielder que le texte visible.
                    think_buf += content
                    if in_think:
                        # Chercher la fin du bloc thinking
                        fin = _fin_thinking(think_buf)
                        if fin >= 0:
                            in_think = False
                            think_buf = think_buf[fin:]
                        continue
                    # Chercher le début du bloc thinking
                    deb = _debut_thinking(think_buf)
                    if deb >= 0:
                        in_think = True
                        # Yield le contenu avant le thinking
                        before = think_buf[:deb]
                        think_buf = think_buf[deb:]
                        if before:
                            yield before
                        continue
                    # Pas de thinking : yield le contenu sauf le dernier fragment
                    # (qui pourrait être le début d'un tag)
                    safe, think_buf = _safe_split(think_buf)
                    if safe:
                        yield safe

    # ------------------------------------------------------------------ #
    async def unload_model(self) -> bool:
        """Décharge le modèle de la VRAM pour libérer de la place à ComfyUI.

        - Ollama : `POST /api/generate` avec `keep_alive: 0`
        - llama.cpp : `POST /models/unload` (router mode)

        Le prochain appel chat()/stream_chat() rechargera le modèle
        automatiquement (Ollama lazy reload / llama.cpp /models/load).
        """
        if self.cfg.backend == "llamacpp":
            return await self._llamacpp_unload()
        return await self._ollama_unload()

    # ------------------------------------------------------------------ #
    async def ensure_model_loaded(self) -> bool:
        """S'assure que le modèle est chargé en VRAM (utile après un unload).

        - Ollama : pas besoin (lazy reload natif)
        - llama.cpp : `POST /models/load` (router mode)
        """
        if self.cfg.backend == "llamacpp":
            return await self._llamacpp_load()
        return True  # Ollama recharge automatiquement

    # ------------------------------------------------------------------ #
    #  Ollama
    # ------------------------------------------------------------------ #
    async def _ollama_unload(self) -> bool:
        native_base = self.cfg.base_url.rsplit("/v1", 1)[0] if self.cfg.base_url.endswith("/v1") else self.cfg.base_url
        try:
            async with httpx.AsyncClient(timeout=10.0) as tmp:
                r = await tmp.post(
                    f"{native_base.rstrip('/')}/api/generate",
                    json={"model": self.cfg.model, "keep_alive": 0},
                )
                ok = r.status_code == 200
                if ok:
                    _log.info("ollama model unloaded: %s", self.cfg.model)
                else:
                    _log.warning("ollama unload failed (%s): %s", r.status_code, r.text[:200])
                return ok
        except Exception as e:
            _log.warning("ollama unload error: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  llama.cpp (router mode)
    # ------------------------------------------------------------------ #
    async def _llamacpp_unload(self) -> bool:
        """POST /models/unload — libère toute la VRAM (poids + KV cache)."""
        # L'endpoint est à la racine du serveur, pas sous /v1
        root = self.cfg.base_url.rsplit("/v1", 1)[0] if self.cfg.base_url.endswith("/v1") else self.cfg.base_url
        try:
            async with httpx.AsyncClient(timeout=15.0) as tmp:
                r = await tmp.post(
                    f"{root.rstrip('/')}/models/unload",
                    json={"model": self.cfg.model},
                )
                if r.status_code == 200:
                    _log.info("llamacpp model unloaded: %s", self.cfg.model)
                    return True
                # "model is not running" = déjà déchargé, pas une erreur
                if r.status_code == 400 and "not running" in r.text:
                    _log.debug("llamacpp model already unloaded: %s", self.cfg.model)
                    return True
                _log.warning("llamacpp unload failed (%s): %s", r.status_code, r.text[:200])
                return False
        except Exception as e:
            _log.warning("llamacpp unload error: %s", e)
            return False

    async def _llamacpp_load(self) -> bool:
        """POST /models/load — recharge le modèle en VRAM.

        Si le serveur répond 400 « already loading » (chargement concurrent
        en cours), on ne traite pas ça comme un succès immédiat : on interroge
        le endpoint `/models` jusqu'à ce que le statut du modèle passe à
        « loaded » (timeout ~60s). Retourne True si le modèle est prêt à
        servir des requêtes au moment du retour.
        """
        root = self.cfg.base_url.rsplit("/v1", 1)[0] if self.cfg.base_url.endswith("/v1") else self.cfg.base_url
        try:
            async with httpx.AsyncClient(timeout=30.0) as tmp:
                r = await tmp.post(
                    f"{root.rstrip('/')}/models/load",
                    json={"model": self.cfg.model},
                )
                if r.status_code == 200:
                    _log.info("llamacpp model loaded: %s", self.cfg.model)
                    return True
                # "model is already loaded" (charging terminé) n'est pas une erreur
                if r.status_code == 400 and "already loaded" in r.text.lower():
                    _log.debug("llamacpp model already loaded: %s", self.cfg.model)
                    return True
                # "model is already running" = le SLOT est occupé (génération en
                # cours, seule une requête à la fois sur le GGUF) : le modèle est
                # BIEN chargé — pas une erreur. Sans cette clause, chaque appel
                # concurrent (open-webui partageant localhost:8080, retry d'une
                # retransmission) spammait « load failed (400) » à chaque tour.
                if r.status_code == 400 and "already running" in r.text.lower():
                    _log.debug("llamacpp model busy (déjà chargé): %s", self.cfg.model)
                    return True
                # "model is already loading" → chargement en cours (course multi-tours
                # ou contention VRAM avec ComfyUI). On ne peut pas encore servir de
                # requêtes : on poll /models jusqu'à statut "loaded" ou timeout.
                if r.status_code == 400 and "already loading" in r.text.lower():
                    return await self._llamacpp_wait_loaded(root, timeout=60.0)
                _log.warning("llamacpp load failed (%s): %s", r.status_code, r.text[:200])
                return False
        except Exception as e:
            _log.warning("llamacpp load error: %s", e)
            return False

    async def _llamacpp_wait_loaded(self, root: str, timeout: float = 60.0) -> bool:
        """Poll /models jusqu'à ce que le modèle cible soit « loaded ».

        llamacpp router expose `/v1/models` (OpenAI-compatible) avec le statut
        de chaque modèle. Tant que le chargement est en cours, on attend ; dès
        que le statut passe à « loaded », on retourne True. Retourne False si
        le timeout est atteint (la liste reste interrogée au plus 90 fois).
        """
        import time
        deadline = time.monotonic() + timeout
        interval = 1.5
        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(timeout=10.0) as tmp:
                    r = await tmp.get(f"{root.rstrip('/')}/v1/models")
                    if r.status_code == 200:
                        data = r.json()
                        models = data.get("data", []) or data.get("models", [])
                        for m in models:
                            mid = m.get("id") or m.get("name") or ""
                            st = (m.get("status") or m.get("state") or "").lower()
                            if mid == self.cfg.model and st in ("loaded", "ready", "running"):
                                _log.info("llamacpp model ready after poll: %s", self.cfg.model)
                                return True
            except Exception as e:
                _log.debug("llamacpp poll error (continuing): %s", e)
            await asyncio.sleep(interval)
        _log.warning("llamacpp model wait timeout (%.0fs): %s", timeout, self.cfg.model)
        return False

    # ------------------------------------------------------------------ #
    async def list_models(self) -> list[dict[str, Any]]:
        """Liste les modèles disponibles."""
        try:
            resp = await self._client.get("/models")
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("data", [])
        except Exception:
            return []
