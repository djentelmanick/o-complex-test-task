import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.gateway import AmoCRMGateway
from app.adapters.outbound.amocrm.oauth import AmoCRMOAuth
from app.adapters.outbound.amocrm.token_store import PgTokenStore
from app.adapters.outbound.amocrm.tools import AmoCRMTools
from app.adapters.outbound.amocrm.webhooks import (
    AmoCRMWebhookRegistrar,
    resolve_tunnel_url,
    webhook_destination,
)
from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.adapters.outbound.gigachat.auth import GigaChatTokenProvider
from app.adapters.outbound.gigachat.client import GigaChatClient, GigaChatEmbedder, GigaChatLLM
from app.adapters.outbound.local.embedder import LocalEmbedder
from app.adapters.outbound.postgres.processed_events import PgProcessedEvents
from app.adapters.outbound.postgres.repository import PgVectorKnowledgeRepository
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.handle_incoming import HandleIncomingMessageUseCase
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.application.ports import CRMGateway, Embedder, LLMClient
from app.config import Settings
from app.container import Container
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

# Порог RETRIEVAL_MIN_SCORE подобран под семантические эмбеддинги; у bag-of-words эмбеддера
# сходство заметно ниже, и без своего порога демо-режим никогда не находил бы статьи
_FAKE_MIN_SCORE = 0.05


def make_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    # Системное хранилище, а не certifi: в образ добавлен корневой сертификат НУЦ Минцифры,
    # которым подписаны сертификаты GigaChat
    return ssl.create_default_context(cafile=ca_bundle)


async def warm_up(embedder: Embedder, settings: Settings) -> None:
    # Локальная модель грузится с диска несколько секунд — пусть это случится до первого запроса
    if settings.embedding_provider == "local":
        await embedder.embed(["прогрев"])


def _gigachat_client(settings: Settings, stack: AsyncExitStack) -> GigaChatClient:
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
    return GigaChatClient(http, settings.gigachat_base_url, tokens)


def _amocrm_tools(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], stack: AsyncExitStack
) -> AmoCRMTools:
    subdomain, client_id = settings.amocrm_subdomain, settings.amocrm_client_id
    secret, token_key = settings.amocrm_client_secret, settings.amocrm_token_key
    if not (subdomain and client_id and secret and token_key):
        raise RuntimeError("AMOCRM_* settings are incomplete")
    http = httpx.AsyncClient(timeout=settings.amocrm_timeout_s)
    stack.push_async_callback(http.aclose)
    oauth = AmoCRMOAuth(
        http,
        subdomain=subdomain,
        client_id=client_id,
        client_secret=secret,
        redirect_uri=settings.amocrm_redirect_uri,
        store=PgTokenStore(sessionmaker, token_key),
    )
    client = AmoCRMClient(http, subdomain, oauth)
    return AmoCRMTools(
        http=http,
        oauth=oauth,
        gateway=AmoCRMGateway(client),
        registrar=AmoCRMWebhookRegistrar(client),
        processed_events=PgProcessedEvents(sessionmaker),
    )


@asynccontextmanager
async def amocrm_webhook_registration(
    tools: AmoCRMTools, settings: Settings, *, attempts: int = 15
) -> AsyncIterator[None]:
    destination: str | None = None
    if settings.amocrm_tunnel_metrics_url and settings.amocrm_webhook_secret:
        try:
            public_url = await resolve_tunnel_url(
                tools.http, settings.amocrm_tunnel_metrics_url, attempts=attempts
            )
            destination = webhook_destination(
                public_url, settings.amocrm_webhook_secret.get_secret_value()
            )
            await tools.registrar.register(destination)
            logger.info("AmoCRM webhook registered at %s", public_url)
        except DomainError as exc:
            destination = None
            logger.warning("AmoCRM webhook registration skipped: %s", exc)
    try:
        yield
    finally:
        if destination is not None:
            try:
                await tools.registrar.unregister(destination)
            except DomainError as exc:
                logger.warning("AmoCRM webhook was not removed: %s", exc)


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        stack.push_async_callback(engine.dispose)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        amocrm: AmoCRMTools | None = None
        crm: CRMGateway
        if settings.crm_provider == "amocrm":
            amocrm = _amocrm_tools(settings, sessionmaker, stack)
            crm = amocrm.gateway
        else:
            crm = MockCRMGateway.from_json_file(settings.crm_fixture_path)

        gigachat: GigaChatClient | None = None
        if "gigachat" in (settings.llm_provider, settings.embedding_provider):
            gigachat = _gigachat_client(settings, stack)

        llm: LLMClient
        if gigachat is not None and settings.llm_provider == "gigachat":
            llm = GigaChatLLM(gigachat, settings.gigachat_model)
        else:
            llm = FakeLLM()

        embedder: Embedder
        min_score = settings.retrieval_min_score
        if gigachat is not None and settings.embedding_provider == "gigachat":
            embedder = GigaChatEmbedder(gigachat, settings.gigachat_embedding_model)
            embedding_model = settings.gigachat_embedding_model
        elif settings.embedding_provider == "local":
            embedder = LocalEmbedder.from_settings(settings)
            embedding_model = settings.local_embedding_model
        else:
            embedder = FakeEmbedder(settings.embedding_dim)
            embedding_model = "fake"
            min_score = _FAKE_MIN_SCORE

        knowledge = PgVectorKnowledgeRepository(sessionmaker, embedding_model=embedding_model)
        answer_inquiry = AnswerInquiryUseCase(
            crm,
            embedder,
            knowledge,
            llm,
            retrieval_limit=settings.retrieval_limit,
            min_score=min_score,
            dialog_max_messages=settings.dialog_max_messages,
        )
        handle_incoming = None
        if amocrm is not None:
            handle_incoming = HandleIncomingMessageUseCase(
                crm=crm,
                answer_inquiry=answer_inquiry,
                publisher=amocrm.gateway,
                processed_events=amocrm.processed_events,
            )

        yield Container(
            answer_inquiry=answer_inquiry,
            ingest_knowledge=IngestKnowledgeUseCase(
                knowledge,
                embedder,
                embedding_model=embedding_model,
                embedding_dim=settings.embedding_dim,
            ),
            crm=crm,
            knowledge=knowledge,
            handle_incoming=handle_incoming,
            amocrm=amocrm,
        )
