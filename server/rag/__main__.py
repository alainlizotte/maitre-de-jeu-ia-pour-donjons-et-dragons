"""CLI d'administration du RAG — ingestion et requêtes de test.

Usage :
    py -m server.rag --ingest            # primo-ingestion (incrémental)
    py -m server.rag --ingest --force    # ré-embeddings force intégral
    py -m server.rag --query "modificateur carac 17 ?"
    py -m server.rag --stats             # nombre de chunks par collection
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ..config import get_config
from .store import get_store


def _setup_logs() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


async def _cmd_ingest(force: bool) -> int:
    store = get_store(get_config())
    stats = await store.ingest(force=force)
    print(f"Ingestion RAG terminée : "
          f"{stats['ingested']} chunks ajoutés, "
          f"{stats['skipped']} ignorés, "
          f"{stats['errors']} erreur(s).")
    return 1 if stats["errors"] else 0


async def _cmd_query(question: str) -> int:
    store = get_store(get_config())
    hits = await store.query(question)
    if not hits:
        print("Aucun extrait trouvé.")
        return 1
    print(store.render_context(hits))
    print("\n--- %d extrait(s), scores : %s ---" % (
        len(hits), ", ".join(f"{h.score:.3f}" for h in hits)
    ))
    return 0


async def _cmd_stats() -> int:
    store = get_store(get_config())
    for kb, n in store.stats().items():
        print(f"{kb:30s} : {n} chunks")
    return 0


def main() -> int:
    _setup_logs()
    parser = argparse.ArgumentParser(prog="server.rag", description=__doc__)
    parser.add_argument("--ingest", action="store_true",
                        help="Lance l'ingestion du corpus D&D 3.5")
    parser.add_argument("--force", action="store_true",
                        help="Avec --ingest : force la ré-embedding du corpus")
    parser.add_argument("--query", metavar="TXT",
                        help="Requête de test sur le vector store")
    parser.add_argument("--stats", action="store_true",
                        help="Affiche le nombre de chunks par collection")
    args = parser.parse_args()

    if args.ingest:
        return asyncio.run(_cmd_ingest(args.force))
    if args.query:
        return asyncio.run(_cmd_query(args.query))
    if args.stats:
        return asyncio.run(_cmd_stats())
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
