from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, LargeBinary, SmallInteger, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc_id: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    # Размерность фиксируется миграцией (EMBEDDING_DIM), модель ORM от неё не зависит
    embedding: Mapped[list[float]] = mapped_column(Vector())


class AmoCRMTokenRow(Base):
    __tablename__ = "amocrm_tokens"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    access_token: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
