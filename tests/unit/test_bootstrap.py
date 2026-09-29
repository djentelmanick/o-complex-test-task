import ssl

from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.adapters.outbound.gigachat.client import GigaChatEmbedder, GigaChatLLM
from app.adapters.outbound.local.embedder import LocalEmbedder
from app.bootstrap import build_container, make_ssl_context
from app.config import Settings

API_KEY = "test-api-key-0123456789abcdef"


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, app_api_key=API_KEY, **overrides)  # type: ignore[arg-type]


def test_ssl_context_always_verifies() -> None:
    context = make_ssl_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


async def test_fake_providers_build_without_network() -> None:
    async with build_container(make(llm_provider="fake", embedding_provider="fake")) as container:
        assert isinstance(container.answer_inquiry._llm, FakeLLM)
        assert isinstance(container.answer_inquiry._embedder, FakeEmbedder)
        leads = await container.crm.list_leads()
        assert leads[0].id == "lead-new"


async def test_llm_and_embeddings_are_chosen_independently() -> None:
    settings = make(llm_provider="gigachat", embedding_provider="local", gigachat_auth_key="key")
    async with build_container(settings) as container:
        assert isinstance(container.answer_inquiry._llm, GigaChatLLM)
        assert isinstance(container.answer_inquiry._embedder, LocalEmbedder)


async def test_gigachat_embeddings_can_be_enabled() -> None:
    settings = make(llm_provider="fake", embedding_provider="gigachat", gigachat_auth_key="key")
    async with build_container(settings) as container:
        assert isinstance(container.answer_inquiry._embedder, GigaChatEmbedder)


async def test_fake_embeddings_use_own_threshold() -> None:
    settings = make(llm_provider="fake", embedding_provider="fake", retrieval_min_score=0.3)
    async with build_container(settings) as container:
        assert container.answer_inquiry._min_score < 0.3


async def test_real_embeddings_use_configured_threshold() -> None:
    settings = make(llm_provider="fake", embedding_provider="local", retrieval_min_score=0.3)
    async with build_container(settings) as container:
        assert container.answer_inquiry._min_score == 0.3


async def test_warm_up_loads_local_embeddings_only() -> None:
    from app.bootstrap import warm_up

    class Counting(FakeEmbedder):
        calls = 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            Counting.calls += 1
            return await super().embed(texts)

    await warm_up(Counting(8), make(llm_provider="fake", embedding_provider="local"))
    await warm_up(Counting(8), make(llm_provider="fake", embedding_provider="fake"))
    assert Counting.calls == 1
