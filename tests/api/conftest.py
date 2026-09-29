from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from asgi_lifespan import LifespanManager

from app.adapters.inbound.http.app import create_app
from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.application.ports import LLMClient
from app.config import Settings
from app.container import Container
from app.domain.models import DialogMessage, Lead, Role
from tests.fakes import InMemoryKnowledgeRepository, make_chunk, seed

API_KEY = "test-api-key-0123456789abcdef"
LEAD = Lead(
    id="lead-1",
    name="Анна",
    dialog=(DialogMessage(Role.CLIENT, "Хочу детокс"), DialogMessage(Role.MANAGER, "Подберём")),
)


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, app_api_key=API_KEY, llm_provider="fake", **overrides)  # type: ignore[arg-type]


async def make_container(llm: LLMClient | None = None, *, empty_kb: bool = False) -> Container:
    embedder = FakeEmbedder(dim=64)
    repo = InMemoryKnowledgeRepository()
    if not empty_kb:
        await seed(repo, embedder, [make_chunk("zeolite", content="Цеолит курс приёма 15 дней")])
    crm = MockCRMGateway([LEAD])
    return Container(
        answer_inquiry=AnswerInquiryUseCase(crm, embedder, repo, llm or FakeLLM(), min_score=0.0),
        ingest_knowledge=IngestKnowledgeUseCase(
            repo, embedder, embedding_model="fake", embedding_dim=64
        ),
        crm=crm,
        knowledge=repo,
    )


@asynccontextmanager
async def make_client(
    settings: Settings | None = None, container: Container | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    resolved = container or await make_container()

    @asynccontextmanager
    async def factory(_: Settings) -> AsyncIterator[Container]:
        yield resolved

    app = create_app(settings or make_settings(), factory)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
