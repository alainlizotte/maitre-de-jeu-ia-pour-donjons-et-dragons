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

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from ..config import LLMConfig


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

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        return ChatResult(
            content=msg.get("content", "") or "",
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
                    if content:
                        yield content

    # ------------------------------------------------------------------ #
    async def list_models(self) -> list[dict[str, Any]]:
        """Liste les modèles disponibles côté Ollama (admin / debug)."""
        try:
            resp = await self._client.get("/models")
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("data", [])
        except Exception:
            return []
