import os
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.token_store import PgTokenStore
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.adapters.outbound.postgres.processed_events import PgProcessedEvents
from tests.integration.test_pgvector_repository import alembic

pytestmark = pytest.mark.integration

PAIR = TokenPair(access_token="access-secret", refresh_token="refresh-secret", expires_at=2e9)


@pytest.fixture
async def sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await alembic(monkeypatch, "downgrade", "base")
    await alembic(monkeypatch, "upgrade", "head")
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


async def test_tokens_roundtrip_and_are_encrypted_at_rest(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PgTokenStore(sessionmaker, key())
    assert await store.load() is None
    await store.save(PAIR)
    await store.save(TokenPair("access-2", "refresh-2", 2e9 + 10))

    assert await store.load() == TokenPair("access-2", "refresh-2", 2e9 + 10)
    async with sessionmaker() as session:
        rows = (
            await session.execute(text("SELECT access_token, refresh_token FROM amocrm_tokens"))
        ).all()
    assert len(rows) == 1
    assert b"access-2" not in bytes(rows[0][0])
    assert b"refresh-2" not in bytes(rows[0][1])


async def test_tokens_with_another_key_are_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await PgTokenStore(sessionmaker, key()).save(PAIR)
    with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
        await PgTokenStore(sessionmaker, key()).load()


async def test_processed_events_are_seen_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    events = PgProcessedEvents(sessionmaker)
    assert await events.first_seen("crm:message:1") is True
    assert await events.first_seen("crm:message:1") is False
    assert await events.first_seen("crm:message:2") is True
