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
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY alembic.ini ./
COPY app ./app
COPY kb ./kb
COPY fixtures ./fixtures
COPY docker/entrypoint.sh ./entrypoint.sh
USER app
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]

FROM runtime AS test
USER root
COPY --from=deps-dev /opt/venv /opt/venv
COPY pyproject.toml ./
COPY tests ./tests
RUN chown -R app:app /app
USER app
ENTRYPOINT []
CMD ["pytest"]
