"""RAG — embeddings via un endpoint OpenAI-compatible `/v1/embeddings`.

Aucune SDK embedding dédiée : on réutilise le même `httpx.AsyncClient` que
`llm.client`, en ciblant `POST {base_url}/embeddings`. Le serveur peut être
llama.cpp (conteneur `llamaembed`, flag `--embedding`) ou Ollama — les deux
exposent le même format OpenAI : `{"data": [{"embedding": [...]}]}`.

Le nom du modèle embedding vient de `rag.embedding_model` dans la config —
indépendant du modèle Chat. La base URL vient de `rag.embedding_base_url`
(serveur dédié) avec repli sur `llm.base_url`.

Modèles nomic-embed v1.5 : la doc officielle exige un préfixe de tâche
(`search_query: ` / `search_document: `) pour un retrieval optimal — on
l'ajoute automatiquement quand le nom du modèle contient « nomic ».
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("dnd35.rag.embeddings")


class EmbeddingError(RuntimeError):
    """Échec d'obtention d'un embedding depuis le serveur llama.cpp."""


class Embedder:
    """Client léger sur l'endpoint `/v1/embeddings` (OpenAI-compatible).

    Pérenne sur l'exécution serveur : un seul `httpx.AsyncClient` réutilisé.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8081/v1",
        api_key: str = "none",
        model: str = "embeddinggemma",
    ):
        # `base_url` peut déjà pointer sur `/v1` (config llm.base_url) ;
        # l'endpoint embeddings vit sous `/v1/embeddings`.
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Préfixes de tâche selon la famille du modèle — requis pour un
        # retrieval de qualité (nomic et embeddinggemma), ignorés sinon (bge,
        # e5, MiniLM…).
        m = model.lower()
        self._family = (
            "nomic" if "nomic" in m
            else "embeddinggemma" if "embeddinggemma" in m
            else ""
        )
        self._url = (
            self.base_url + "/embeddings"
            if self.base_url.endswith("/v1")
            else self.base_url + "/v1/embeddings"
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def _with_task(self, text: str, kind: str) -> str:
        """Ajoute le préfixe de tâche requis par la famille du modèle.

        - nomic v1.5     : `search_query: ` / `search_document: `
        - embeddinggemma : `query: ` / `title: none | text: `
        - autres         : aucun préfixe
        """
        if not text or not self._family:
            return text
        if self._family == "nomic":
            if text.startswith(("search_query:", "search_document:")):
                return text  # déjà préfixé — pas de double
            prefix = "search_query" if kind == "query" else "search_document"
            return f"{prefix}: {text}"
        # embeddinggemma
        if kind == "query":
            return text if text.startswith("query:") else f"query: {text}"
        if text.startswith("title:"):
            return text  # déjà préfixé
        return f"title: none | text: {text}"

    # Budget chars maximal par input (filet de sécurité). Ratio tokenizer réel
    # mesuré : ~2.9 chars/token sur le corpus FR OCR → 4800 chars ≈ 1650 tokens,
    # sous la limite serveur de 2048 quel que soit le ratio. Les chunks issus
    # du chunker (800 tokens cibles ≈ 4000 chars) passent sans troncature.
    MAX_INPUT_CHARS = 4800

    def _clip(self, text: str) -> str:
        if len(text) <= self.MAX_INPUT_CHARS:
            return text
        cut = text[: self.MAX_INPUT_CHARS].rfind(" ")
        return text[: cut if cut > 0 else self.MAX_INPUT_CHARS]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds une liste de textes en un seul appel. Renvoie les vecteurs.

        Ollama supporte `input` comme liste ; si la version refuse la liste,
        on retombe sur un appel par texte (plus lent mais fiable).
        """
        if not texts:
            return []
        texts = [self._clip(self._with_task(t, "document")) for t in texts]
        try:
            resp = await self._client.post(
                "/embeddings",                              # relatif à base_url
                json={"model": self.model, "input": texts},
            )
            if resp.status_code != 200:
                # Retombe sur le mode un-par-un si l'endpoint rejette la liste.
                log.warning(
                    "embeddings batch %d rejeté (%d) — repli un-par-un",
                    len(texts), resp.status_code,
                )
                return [v for t in texts for v in [await self._embed_one(t)]]
            data = resp.json()
        except (httpx.RequestError, httpx.HTTPError) as e:
            raise EmbeddingError(f"ollama embeddings indisponible: {e}") from e

        # Forme 0 (OpenAI standard, Ollama `/v1`) : `data: [{"embedding":[...]}]`.
        rows = data.get("data")
        if isinstance(rows, list) and rows:
            out: list[list[float]] = []
            for r in rows:
                e = r.get("embedding") if isinstance(r, dict) else None
                if not isinstance(e, list):
                    log.warning("forme de réponse embeddings inattendue — repli un-par-un")
                    return [await self._embed_one(t) for t in texts]
                out.append(list(map(float, e)))
            # Cohérence longueur : si le serveur a tronqué, repli un-par-un.
            if len(out) == len(texts):
                return out
            log.warning("batch embeddings tronqué (%d/%d) — repli un-par-un",
                        len(out), len(texts))
            return [await self._embed_one(t) for t in texts]
        # Forme 1 : champ `embeddings` (list[list[float]]) quand input était une liste.
        embs = data.get("embeddings")
        if isinstance(embs, list) and embs and isinstance(embs[0], list):
            return [list(map(float, v)) for v in embs]
        # Forme 2 : champ `embedding` (list[float]) quand input était un str.
        emb = data.get("embedding")
        if isinstance(emb, list) and (not texts or len(texts) == 1):
            return [list(map(float, emb))]
        # Si la réponse est ambigüe, repli un-par-un pour chaque texte restant.
        log.warning("forme de réponse embeddings inattendue — repli un-par-un")
        return [await self._embed_one(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        """Embedding d'une REQUÊTE utilisateur (préfixe `search_query:` si nomic)."""
        return await self._embed_one(self._clip(self._with_task(text, "query")), kind="query")

    async def _embed_one(self, text: str, kind: str = "document") -> list[float]:
        try:
            return await self._embed_one_raw(text, kind)
        except EmbeddingError as e:
            # 400 = input trop long pour le contexte serveur (ex : tableaux OCR
            # de nombres, tokenisation ~1.5 chars/token). On divise par deux et
            # on retente — le début du texte porte l'essentiel du signal.
            if "400" not in str(e) or len(text) < 500:
                raise
            log.warning("input trop long (%d chars) — troncature adaptative", len(text))
            half = text[: len(text) // 2]
            cut = half.rfind(" ")
            text = half[: cut if cut > 0 else len(half)]
            return await self._embed_one(text, kind)

    async def _embed_one_raw(self, text: str, kind: str = "document") -> list[float]:
        try:
            resp = await self._client.post(
                "/embeddings",
                json={"model": self.model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.RequestError, httpx.HTTPError) as e:
            raise EmbeddingError(f"ollama embeddings indisponible: {e}") from e
        emb = data.get("embedding") or data.get("embeddings")
        # Forme OpenAI standard (Ollama `/v1`) : `data[0].embedding`.
        if not isinstance(emb, list) or (emb and not isinstance(emb[0], (int, float))):
            rows = data.get("data")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                emb = rows[0].get("embedding")
        if not isinstance(emb, list) or (emb and not isinstance(emb[0], (int, float))):
            raise EmbeddingError("réponse embeddings malformée")
        return list(map(float, emb))

    async def name_dims(self) -> tuple[str, int] | None:
        """Renvoie (model, dim) si Ollama répond — utile au premier démarrage."""
        try:
            v = await self._embed_one("ping")
            return self.model, len(v)
        except EmbeddingError:
            return None
