.PHONY: up down logs test test-integration lint ingest migrate downgrade revision up-amocrm amocrm-auth amocrm-seed client-says

up:
	docker compose up --build -d

down:
	docker compose --profile amocrm down

logs:
	docker compose logs -f app

test:
	docker compose --profile test run --rm --build test pytest

test-integration:
	docker compose --profile test run --rm --build test pytest -m integration

lint:
	docker compose --profile test run --rm --build test sh -c "ruff check . && ruff format --check . && mypy app"

ingest:
	docker compose exec app python -m app.adapters.inbound.cli ingest

migrate:
	docker compose run --rm --entrypoint alembic app upgrade head

downgrade:
	docker compose run --rm --entrypoint alembic app downgrade -1

# Автогенерация пишет файл в смонтированный каталог миграций, поэтому запускается из test-образа
revision:
	docker compose --profile test run --rm --build \
		-v ./app/adapters/outbound/postgres/migrations/versions:/app/app/adapters/outbound/postgres/migrations/versions \
		test alembic revision --autogenerate -m "$(m)"

up-amocrm:
	docker compose --profile amocrm up --build -d

amocrm-auth:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-auth "$(code)"

amocrm-seed:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-seed

client-says:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-say "$(lead)" "$(text)"
