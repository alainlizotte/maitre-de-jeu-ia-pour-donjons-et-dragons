# Image monorepo D&D 3.5 — Maître du Jeu (FastAPI + RAG ChromaDB).
# Construit depuis la racine du projet :
#   docker build -t dnd35-mj .
# Usage normal via docker-compose.yml (cf. ce fichier).

FROM python:3.12-slim

# ChromaDB embarque sqlite3 + a besoin de compilateurs C pour hnswlib.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation des dépendances Python. On pré-copie requirements.txt pour
# bénéficier du cache Docker quand le code change sans dépendances.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pymupdf   # extraction PDF OCR (server/rag/extract_ocr_books.py)

# Code applicatif. Le reste (config/, server/data/, corpus knowledge_import/)
# est monté en volumes au runtime, cf. docker-compose.yml.
COPY server/ ./server/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DND35_HOST=0.0.0.0 \
    DND35_PORT=8000

EXPOSE 8000

# Healthcheck : on s'appuie sur /api/health (renvoyé par FastAPI).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).status==200 else 1)"

# Uvicorn direct : pas de --reload en prod. La config YAML pilote le host/port.
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "30", "--ws-ping-timeout", "60"]
