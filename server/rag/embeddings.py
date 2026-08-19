"""RAG — embeddings via l'API Ollama (endpoint OpenAI-compatible `/embeddings`).

Aucune SDK embedding dédiée : on réutilise le même `httpx.AsyncClient` que
`llm.client`, en ciblant `POST {base_url}/embeddings` (convention Ollama).
Le nom du modèle embedding (`nomic-embed-text` par défaut) vient de
`rag.embedding_model` dans la config — indépendant du modèle Chat.

L'endpoint `/embeddings` d'Ollama attend :
    {"model": "<embedding_model>", "input": "<texte ou liste de textes>"}
et renvoie :
    {"embedding": [...]}              # input str
    {"embeddings": [[...], ...]}      # input list[str]  (selon version Ollama)

On gère les deux formes de réponse pour robustesse.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("dnd35.rag.embeddings")


class EmbeddingError(RuntimeError):
    """Échec d'obtenir un embedding depuis Ollama."""


class Embedder:
    """Client léger sur l'endpoint `/embeddings` d'Ollama.

    Pérenne sur l'exécution serveur : un seul `httpx.AsyncClient` réutilisé.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        model: str = "nomic-embed-text",
    ):
        # `base_url` peut déjà pointer sur `/v1` (config llm.base_url) ;
        # l'endpoint embeddings vit sous `/v1/embeddings` côté Ollama.
        self.base_url = base_url.rstrip("/")
        self.model = model
        # `/v1/embeddings` existe chez Ollama ; on le prend tel quel si base_url
        # finit par `/v1`, sinon on ajoute `/v1`.
        url = (
            self.base_url + "/embeddings"
            if self.base_url.endswith("/v1")
            else self.base_url + "/v1/embeddings"
        )
        self._url = url
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds une liste de textes en un seul appel. Renvoie les vecteurs.

        Ollama supporte `input` comme liste ; si la version refuse la liste,
        on retombe sur un appel par texte (plus lent mais fiable).
        """
        if not texts:
            return []
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
        return await self._embed_one(text)

    async def _embed_one(self, text: str) -> list[float]:
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
