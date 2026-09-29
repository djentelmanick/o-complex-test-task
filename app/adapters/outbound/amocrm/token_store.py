from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.adapters.outbound.postgres.models import AmoCRMTokenRow

# Интеграция одна на аккаунт — храним единственную строку
_ROW_ID = 1


class PgTokenStore:
    """Токены AmoCRM в Postgres, зашифрованные Fernet: дамп БД не раскрывает доступ к CRM."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], encryption_key: SecretStr
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fernet = Fernet(encryption_key.get_secret_value().encode())

    async def load(self) -> TokenPair | None:
        async with self._sessionmaker() as session:
            row = await session.get(AmoCRMTokenRow, _ROW_ID)
        if row is None:
            return None
        try:
            return TokenPair(
                access_token=self._fernet.decrypt(row.access_token).decode(),
                refresh_token=self._fernet.decrypt(row.refresh_token).decode(),
                expires_at=row.expires_at.timestamp(),
            )
        except InvalidToken as exc:
            raise AmoCRMAuthError(
                "stored AmoCRM tokens cannot be decrypted (AMOCRM_TOKEN_KEY changed?); "
                "run make amocrm-auth code=<code>"
            ) from exc

    async def save(self, pair: TokenPair) -> None:
        values = {
            "access_token": self._fernet.encrypt(pair.access_token.encode()),
            "refresh_token": self._fernet.encrypt(pair.refresh_token.encode()),
            "expires_at": datetime.fromtimestamp(pair.expires_at, UTC),
        }
        stmt = (
            insert(AmoCRMTokenRow)
            .values(id=_ROW_ID, **values)
            .on_conflict_do_update(
                index_elements=[AmoCRMTokenRow.id], set_={**values, "updated_at": func.now()}
            )
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(stmt)
