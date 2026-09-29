#!/bin/sh
set -eu

alembic upgrade head
# Ошибка загрузки базы знаний (например, GigaChat недоступен) не должна ронять сервис:
# /ready покажет 503, а загрузку можно повторить через `make ingest`
python -m app.adapters.inbound.cli ingest || echo "WARNING: knowledge base ingest failed" >&2
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header
