FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Editable so domain packs and scripts resolve from /app like a checkout.
RUN pip install --no-cache-dir -e . \
    && useradd --system --home /data aether \
    && install -d -o aether /data
COPY scripts ./scripts
COPY examples ./examples
COPY domains ./domains

USER aether
WORKDIR /data
ENV DOCUMENTS_DIR=/data/documents \
    INDEX_DIR=/data/lancedb \
    LLM_CACHE_DIR=/data/llm-cache \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "--factory", "aether.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
