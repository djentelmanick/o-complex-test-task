import ssl
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.adapters.outbound.gigachat.auth import GigaChatTokenProvider
from app.adapters.outbound.gigachat.client import GigaChatClient, GigaChatEmbedder, GigaChatLLM
from app.adapters.outbound.postgres.repository import PgVectorKnowledgeRepository
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.application.ports import Embedder, LLMClient
from app.config import Settings
from app.container import Container

# Порог RETRIEVAL_MIN_SCORE подобран под эмбеддинги GigaChat; у bag-of-words эмбеддера
# сходство заметно ниже, и без своего порога демо-режим никогда не находил бы статьи
_FAKE_MIN_SCORE = 0.05


def make_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    # Системное хранилище, а не certifi: в образ добавлен корневой сертификат НУЦ Минцифры,
    # которым подписаны сертификаты GigaChat
    return ssl.create_default_context(cafile=ca_bundle)


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        stack.push_async_callback(engine.dispose)
        knowledge = PgVectorKnowledgeRepository(async_sessionmaker(engine, expire_on_commit=False))
        crm = MockCRMGateway.from_json_file(settings.crm_fixture_path)

        llm: LLMClient
        embedder: Embedder
        if settings.llm_provider == "fake":
            llm, embedder, embedding_model = FakeLLM(), FakeEmbedder(settings.embedding_dim), "fake"
            min_score = _FAKE_MIN_SCORE
        else:
            if settings.gigachat_auth_key is None:
                raise RuntimeError("GIGACHAT_AUTH_KEY is not set")
            http = httpx.AsyncClient(
                verify=make_ssl_context(settings.gigachat_ca_bundle),
                timeout=settings.gigachat_timeout_s,
            )
            stack.push_async_callback(http.aclose)
            tokens = GigaChatTokenProvider(
                http,
                settings.gigachat_auth_url,
                settings.gigachat_auth_key,
                settings.gigachat_scope,
            )
            client = GigaChatClient(http, settings.gigachat_base_url, tokens)
            llm = GigaChatLLM(client, settings.gigachat_model)
            embedder = GigaChatEmbedder(client, settings.gigachat_embedding_model)
            embedding_model = settings.gigachat_embedding_model
            min_score = settings.retrieval_min_score

        yield Container(
            answer_inquiry=AnswerInquiryUseCase(
                crm,
                embedder,
                knowledge,
                llm,
                retrieval_limit=settings.retrieval_limit,
                min_score=min_score,
                dialog_max_messages=settings.dialog_max_messages,
            ),
            ingest_knowledge=IngestKnowledgeUseCase(
                knowledge,
                embedder,
                embedding_model=embedding_model,
                embedding_dim=settings.embedding_dim,
            ),
            crm=crm,
            knowledge=knowledge,
        )
