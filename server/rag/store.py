"""RAG — store vectoriel ChromaDB embarqué pour la base de connaissances D&D 3.5.

Le corpus est organisé en 3 KB distinctes (voir `knowledge_import/`), ici
matérialisées en 3 collections ChromaDB séparées pour préserver l'étiquetage
source « officiel vs narration » — cf. `MANIFESTE_KNOWLEDGE_BASE.md` et
`GUIDE_FONCTIONNEMENT.md` du projet source.

Cycle de vie :
- `py -m server.rag --ingest`      → granulation + embeddings + Insertion
- `py -m server.rag --ingest --force` → ré-embeddings force (utile après maj corpus)
- au runtime serveur, `get_store(cfg)` instancie en mode lecture seule, ni
  d'ingestion automatique (le vecteur est déjà persisté).

Le store est lazy-init (attend que le serveur démarre) et assorti d'un
manifeste de fingerprints pour ne ré-embedder que les fichiers modifiés.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import AppConfig
from .chunker import Chunk, chunk_file, iter_corpus_files
from .embeddings import Embedder

log = logging.getLogger("dnd35.rag.store")

# Noms de collection stables — n'incluent pas la version pour longévité.
_KB_TO_COLLECTION = {
    "KB1_Manuels_de_base": "kb1_manuels",
    "KB2_Aide_creation_perso": "kb2_aide_perso",
    "KB4_DRS_corpus": "kb4_drs",
}


@dataclass
class RagHit:
    """Un extrait retourné par la requête — métadonnées + score."""
    text: str
    kb: str
    file: str
    page: str
    title: str
    score: float


class RagStore:
    """Wrapping ChromaDB pour les 3 KB D&D 3.5 avec hébergement de fingerprints."""

    def __init__(self, cfg: AppConfig, embedder: Optional[Embedder] = None):
        self.cfg = cfg
        # Base URL configurée (hostname Docker interne « llamaembed:8080 » la
        # plupart du temps) ; on ajoute le port hôte publié 8081 en secours
        # pour un lancement hors conteneur (tests, dev local).
        principal = cfg.rag.embedding_base_url or cfg.llm.base_url
        secours = ("http://localhost:8081/v1",)
        if principal and principal.startswith("http://localhost:8081"):
            secours = ()
        self.embedder = embedder or Embedder(
            base_url=principal,
            api_key=cfg.llm.api_key,
            model=cfg.rag.embedding_model,
            fallbacks=secours,
        )
        self._persist_dir = cfg.abs(cfg.rag.persist_dir)
        self._source_dir = cfg.abs(cfg.rag.source_dir)
        self._manifest_path = self._persist_dir / "manifeste.json"
        self._client = None              # Lazy : ChromaDB chargé au premier usage.
        self._collections: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    #  ChromaDB lazy-init
    # ------------------------------------------------------------------ #
    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import chromadb                       # pylint: disable=import-outside-toplevel
            except ImportError as e:
                raise RuntimeError(
                    "chromadb n'est pas installé. Lancez "
                    "`pip install -r requirements.txt` avant d'utiliser le RAG."
                ) from e
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            for _kb, coll_name in _KB_TO_COLLECTION.items():
                self._collections[_kb] = self._client.get_or_create_collection(
                    name=coll_name,
                    metadata={"hnsw:space": "cosine"},
                )
        return self._client

    def _collection_for(self, kb: str) -> Any:
        self._ensure_client()
        return self._collections[kb]

    # ------------------------------------------------------------------ #
    #  Manifeste de fingerprints
    # ------------------------------------------------------------------ #
    def _load_manifest(self) -> dict[str, str]:
        if not self._manifest_path.is_file():
            return {}
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_manifest(self, manifest: dict[str, str]) -> None:
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _fingerprint(path: Path) -> str:
        h = hashlib.sha1()
        h.update(path.read_bytes())
        return h.hexdigest()

    # ------------------------------------------------------------------ #
    #  Ingestion
    # ------------------------------------------------------------------ #
    async def ingest(self, force: bool = False) -> dict[str, int]:
        """Ingère le corpus depuis `rag.source_dir`.

        Ne ré-embedde que les fichiers nouveaux ou modifiés (sauf `force=True`).
        Renvoie un résumé `{"ingested": int, "skipped": int, "errors": int}`.
        """
        self._ensure_client()
        if not self._source_dir.is_dir():
            raise FileNotFoundError(
                f"Corpus introuvable : {self._source_dir} "
                f"(vérifiez rag.source_dir dans config.yaml)"
            )
        manifest = {} if force else self._load_manifest()
        stats = {"ingested": 0, "skipped": 0, "errors": 0}

        for txt_path, kb in iter_corpus_files(self._source_dir):
            key = f"{kb}/{txt_path.name}"
            fp = self._fingerprint(txt_path)
            if not force and manifest.get(key) == fp:
                stats["skipped"] += 1
                continue

            try:
                chunks = chunk_file(
                    txt_path, kb=kb,
                    chunk_size_tokens=self.cfg.rag.chunk_size,
                    chunk_overlap_tokens=self.cfg.rag.chunk_overlap,
                )
                await self._upsert_collection(kb, chunks, txt_path.stem)
                manifest[key] = fp
                stats["ingested"] += len(chunks)
                log.info("RAG ingéré (%s/%s) : %d chunks — %s",
                         kb, txt_path.name, len(chunks), chunks[0].file if chunks else "")
            except Exception as e:                                   # noqa: BLE001
                stats["errors"] += 1
                log.error("RAG échec sur %s : %s", key, e)

        self._save_manifest(manifest)
        return stats

    async def _upsert_collection(self, kb: str, chunks: list[Chunk], file_stem: str) -> None:
        coll = self._collection_for(kb)
        if not chunks:
            return
        ids = [f"{file_stem}::{c.index}" for c in chunks]
        metadatas = [c.metadata() for c in chunks]
        texts = [c.text for c in chunks]
        # Embeddings : on batch par groupe de 64 (limite arbitraire pour ne pas
        # saturer l'API Ollama en une seule fois).
        embeddings: list[list[float]] = []
        BATCH = 64
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            embs = await self.embedder.embed_batch(batch)
            embeddings.extend(embs)
        # ChromaDB upsert (crée ou remplace à id identique — utile en --force).
        coll.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)

    # ------------------------------------------------------------------ #
    #  Requête
    # ------------------------------------------------------------------ #
    async def query(
        self,
        question: str,
        top_k: Optional[int] = None,
        kb_filter: Optional[list[str]] = None,
    ) -> list[RagHit]:
        """Renvoie les top_k extraits les plus pertinents pour `question`.

        Interroge les 3 KB sauf si `kb_filter` restreint à un sous-ensemble.
        On n'utilise PAS le `where` Chroma ici (les KB sont des collections
        séparées) — on filtre en interrogeant chacune.
        """
        if not question.strip():
            return []
        k = top_k or self.cfg.rag.top_k
        qemb = await self.embedder.embed_one(question)
        kbs = kb_filter if kb_filter is not None else list(_KB_TO_COLLECTION.keys())
        hits: list[RagHit] = []
        for kb in kbs:
            coll_name = _KB_TO_COLLECTION.get(kb)
            if not coll_name:
                continue
            coll = self._collection_for(kb)
            if coll.count() == 0:
                continue
            try:
                res = coll.query(query_embeddings=[qemb], n_results=k)
            except Exception as e:                                   # noqa: BLE001
                log.warning("RAG query échec sur %s : %s", kb, e)
                continue
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                hits.append(RagHit(
                    text=doc,
                    kb=kb,
                    file=str(meta.get("file", "")),
                    page=str(meta.get("page", "")),
                    title=str(meta.get("title", "")),
                    score=1.0 - float(dist),           # cosine distance → similarité
                ))
        # Tri global par similarité décroissante, top_k au total.
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    # ------------------------------------------------------------------ #
    #  Rendu pour le system prompt
    # ------------------------------------------------------------------ #
    def render_context(self, hits: list[RagHit]) -> str:
        """Produit le bloc injecté dans le system prompt.

        Format repris de `Filtre_EtatPartie_INJECT._consolider_rag` (proj source) :
            [Extrait Knowledge Base — <nom KB court>] (<fichier>, <page>)
            <texte>
            …
        Tronqué si > rag.query_max_tokens (~5 chars/token).
        """
        if not hits:
            return ""
        budget_chars = self.cfg.rag.query_max_tokens * 5  # approx
        kb_short = {
            "KB1_Manuels_de_base": "Manuels de base",
            "KB2_Aide_creation_perso": "Aide création perso",
            "KB4_DRS_corpus": "DRS corpus",
        }
        out_lines: list[str] = []
        used = 0
        for h in hits:
            label = kb_short.get(h.kb, h.kb)
            source = h.file or "?"
            if h.page:
                source += f", p.{h.page} — {h.title}" if h.title else f", p.{h.page}"
            header = f"[Extrait Knowledge Base — {label}] ({source})"
            block = f"{header}\n{h.text}"
            if used + len(block) + 2 > budget_chars:
                remaining = budget_chars - used - len(header) - 4
                if remaining <= 200:           # plus de place utile, on stoppe.
                    break
                block = f"{header}\n{h.text[:remaining]}…"
            out_lines.append(block)
            used += len(block) + 2
        return "\n\n".join(out_lines)

    async def render_for_prompt(
        self, question: str, kb_filter: Optional[list[str]] = None,
    ) -> str:
        """Requête + rendu combinés — accès direct pour `_handle_say`."""
        if not self.cfg.rag.enabled:
            return ""
        hits = await self.query(
            question,
            kb_filter=kb_filter if kb_filter is not None else self.cfg.rag.kb_filter,
        )
        return self.render_context(hits)

    # ------------------------------------------------------------------ #
    #  Stats (admin / debug)
    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, int]:
        try:
            self._ensure_client()
        except Exception:                               # noqa: BLE001
            return {kb: 0 for kb in _KB_TO_COLLECTION.values()}
        return {_KB_TO_COLLECTION[kb]: coll.count()
                for kb, coll in self._collections.items()}


# --------------------------------------------------------------------------- #
#  Singleton paresseux
# --------------------------------------------------------------------------- #
_store: Optional[RagStore] = None


def get_store(cfg: AppConfig) -> RagStore:
    global _store
    if _store is None:
        _store = RagStore(cfg)
    return _store


def reset_store() -> None:
    """Utile pour les tests (force re-créer avec une nouvelle config)."""
    global _store
    _store = None
