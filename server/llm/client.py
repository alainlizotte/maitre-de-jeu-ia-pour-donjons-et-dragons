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
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from ..config import LLMConfig

_log = logging.getLogger("dnd35.llm.client")


# --------------------------------------------------------------------------- #
#  Thinking stripping (Gemma 4 utilise <|channel>thought...<channel|>)
# --------------------------------------------------------------------------- #
import re
_THINK_RE = re.compile(r"<\|channel>thought\b.*?<channel\|>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    """Supprime les blocs de réflexion Gemma 4 du texte de réponse."""
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()


def _safe_split(buf: str) -> tuple[str, str]:
    """Détecte un éventuel début de tag thinking en fin de buffer.

    Renvoie (texte_sûre, reste_à_analyser). Le texte sûr peut être yield.
    """
    # Patterns partiels pouvant être le début de `<|channel>thought`
    markers = ("<|channel>tho", "<|channel>th", "<|channel>", "<|chan", "<|ch", "<|c", "<|")
    for m in markers:
        if buf.endswith(m):
            safe = buf[: -len(m)]
            return safe, buf[-len(m):]
    return buf, ""


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
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=httpx.Timeout(180.0, connect=10.0),
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
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature if temperature is not None else self.cfg.temperature,
            "top_p": self.cfg.top_p,
            "stream": False,
        }
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
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature if temperature is not None else self.cfg.temperature,
            "top_p": self.cfg.top_p,
            "stream": True,
        }
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
            async for line in resp.aiter_lines():
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
                    # Machine à états : détecter `<|channel>thought` et `<channel|>`
                    think_buf += content
                    if in_think:
                        # Chercher la fin du bloc thinking
                        idx = think_buf.find("<channel|>")
                        if idx >= 0:
                            in_think = False
                            think_buf = think_buf[idx + len("<channel|>"):]
                        continue
                    # Chercher le début du bloc thinking
                    think_idx = think_buf.find("<|channel>thought")
                    if think_idx >= 0:
                        in_think = True
                        # Yield le contenu avant le thinking
                        before = think_buf[:think_idx]
                        think_buf = think_buf[think_idx:]
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
