FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY docker/certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
RUN update-ca-certificates

FROM base AS deps
RUN pip install poetry poetry-plugin-export
WORKDIR /build
COPY pyproject.toml poetry.lock ./
RUN poetry export --only main -f requirements.txt -o requirements.txt \
 && poetry export --only dev -f requirements.txt -o requirements-dev.txt \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes -r requirements.txt

FROM deps AS deps-dev
RUN /opt/venv/bin/pip install --require-hashes -r requirements-dev.txt

FROM base AS runtime
RUN useradd --create-home --uid 10001 app
COPY --from=deps /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    EMBEDDING_CACHE_DIR=/opt/models
# Модель эмбеддингов скачивается при сборке, в рантайме сервис в интернет за ней не ходит
ARG LOCAL_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "import sys; from fastembed import TextEmbedding; TextEmbedding(sys.argv[1], cache_dir='/opt/models')" "$LOCAL_EMBEDDING_MODEL"
ENV HF_HUB_OFFLINE=1
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY data ./data
COPY docker/entrypoint.sh ./entrypoint.sh
USER app
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]

FROM runtime AS test
USER root
COPY --from=deps-dev /opt/venv /opt/venv
COPY tests ./tests
RUN chown -R app:app /app
USER app
ENTRYPOINT []
CMD ["pytest"]
