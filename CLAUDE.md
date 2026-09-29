# CLAUDE.md

Сервис-ассистент менеджера O-complex: обращение клиента → RAG по `data/kb` → GigaChat → ответ клиенту + подсказка по допродаже. Подробности в `README.md`, дизайн в `docs/superpowers/specs/`.

## Команды

- `make up` / `make down` / `make logs`: стек в Docker (db + app, порт 8000).
- `make test`: unit и API тесты в Docker. Локально: `poetry run pytest`.
- `make test-integration`: тесты с Postgres и вшитой моделью эмбеддингов, только в Docker.
- `make lint`: `ruff check`, `ruff format --check`, `mypy app`.
- `make ingest`: перезагрузить базу знаний. `make migrate` / `make revision m="..."`: Alembic.
- AmoCRM: `make up-amocrm` (стек + туннель cloudflared), `make amocrm-auth code=…`, `make amocrm-seed`, `make client-says lead=… text="…"`.

Перед коммитом должны быть зелёными `poetry run pytest`, `poetry run ruff check .`, `poetry run ruff format --check .`, `poetry run mypy app`. Если менялись БД, миграции или Docker, дополнительно прогнать `make test-integration`.

## Архитектура (гексагональная)

- `app/domain`: dataclass-модели и доменные ошибки, без внешних зависимостей.
- `app/application`: use case'ы и порты (`Protocol` в `ports.py`), промпт, разбор ответа модели. Зависит только от `domain` (и pydantic для валидации). mypy strict.
- `app/adapters/inbound`: FastAPI (`http/`) и CLI. `app/adapters/outbound`: GigaChat, local (fastembed), Postgres/pgvector, mock CRM, fake-адаптеры.
- `app/bootstrap.py`: единственное место, где адаптеры связываются с use case'ами. Выбор провайдеров задают `LLM_PROVIDER` и `EMBEDDING_PROVIDER`.
- Новая внешняя система подключается через новый адаптер под существующий порт, ядро при этом не трогаем.

## Соглашения

- Код, идентификаторы и коммиты пишутся на английском. Коммиты по Conventional Commits.
- Тексты для пользователя и промпты пишутся на русском.
- Комментарии только там, где нужно объяснить «почему», и на русском.
- Работаем по TDD: сначала падающий тест, потом код. Для внешних HTTP используем `respx`, для портов фейки из `tests/fakes.py`.
- Настройки берутся только из `app/config.py` (pydantic-settings), секреты хранятся как `SecretStr`. `.env` не коммитим.

## Важные инварианты

- Пользовательские данные попадают в промпт только через `build_user_prompt` (экранирование и напоминание после блока клиента).
- Ответ модели проходит только через `parse_answer`: схема, лимиты длины, очистка артефактов, `sources` только из найденных фрагментов.
- В базе знаний нет цен, это проверяет тест `tests/unit/test_kb_content.py`.
- TLS к GigaChat всегда с проверкой, корневой сертификат лежит в `docker/certs/`.
- `EMBEDDING_DIM` фиксируется миграцией `0001`. Смена модели эмбеддингов требует пересоздать таблицу.
- При изменении `data/kb` ingest пересчитывает только изменённые статьи: хэш учитывает содержимое и имя модели.
- В AmoCRM ассистент реагирует только на примечания `sms_in` и пишет результат как `extended_service_message`. Вебхук отвечает 200 сразу, а обработка идёт в фоне, с дедупликацией по id примечания.
- Через туннель (запросы с заголовками Cloudflare) доступен только путь вебхука, это обеспечивает `TunnelGuardMiddleware`.
