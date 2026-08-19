"""RAG D&D 3.5 — base de connaissances vectorielle embarquée (ChromaDB).

Package `server.rag` — migré du pipeline RAG d'OpenWebUI. Comparez avec
`knowledge_import/MANIFESTE_KNOWLEDGE_BASE.md` du projet source pour les
paramètres de chunking (chunk_size=1500, overlap=200, top_k=5) et le
découpage du corpus en 3 KB (KB1 Manuels, KB2 Aide perso, KB4 DRS).

Étapes d'utilisation :
    py -m server.rag --ingest            # primo-ingestion du corpus
    py -m server.rag --ingest --force    # force la ré-embedding du corpus
    py -m server.rag --query "..."       # requête ad-hoc de test

Au runtime, le store est instancié par `main.py` au démarrage et injecté dans
`build_system_message(rag_context=...)` du prompt_builder.
"""

from .store import RagStore, get_store

__all__ = ["RagStore", "get_store"]
