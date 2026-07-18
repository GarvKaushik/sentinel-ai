# Sentinel AI application image.
#
# Runs the FastAPI service that exposes the investigation pipeline. Qdrant,
# Postgres, and Redis are separate services (see docker-compose.yml); this
# image is only the app.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install CPU-only torch FIRST, from the PyTorch CPU index. The default torch
# wheel is the CUDA build (~2-3 GB of NVIDIA libraries) — useless without a GPU
# and the main reason the build crawled for tens of minutes. Installing the CPU
# wheel up front means sentence-transformers finds torch already satisfied and
# pip does not pull the CUDA one.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Then the rest of the dependencies.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding model into the image so startup doesn't depend on a
# runtime download from Hugging Face. With CPU torch above, this step only
# imports a small torch and downloads the ~90 MB model.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"


COPY app ./app
COPY data ./data
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# Entrypoint waits for Qdrant, ingests runbooks (idempotent), then serves.
ENTRYPOINT ["./docker-entrypoint.sh"]
