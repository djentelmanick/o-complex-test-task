from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.postgres.models import ProcessedEventRow


class PgProcessedEvents:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def first_seen(self, key: str) -> bool:
        # Атомарно через уникальный ключ: два параллельных вебхука не пройдут оба
        stmt = (
            insert(ProcessedEventRow)
            .values(key=key)
            .on_conflict_do_nothing()
            .returning(ProcessedEventRow.key)
        )
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.scalar(stmt)
        return inserted is not None
