import ssl

from app.adapters.outbound.fake.llm import FakeLLM
from app.adapters.outbound.gigachat.client import GigaChatLLM
from app.bootstrap import build_container, make_ssl_context
from app.config import Settings

API_KEY = "test-api-key-0123456789abcdef"


def test_ssl_context_always_verifies() -> None:
    context = make_ssl_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


async def test_fake_provider_builds_without_network() -> None:
    settings = Settings(_env_file=None, app_api_key=API_KEY, llm_provider="fake")  # type: ignore[arg-type]
    async with build_container(settings) as container:
        assert isinstance(container.answer_inquiry._llm, FakeLLM)
        leads = await container.crm.list_leads()
        assert leads[0].id == "lead-new"


async def test_gigachat_provider_wires_gigachat_adapters() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_api_key=API_KEY,  # type: ignore[arg-type]
        llm_provider="gigachat",
        gigachat_auth_key="key",  # type: ignore[arg-type]
    )
    async with build_container(settings) as container:
        assert isinstance(container.answer_inquiry._llm, GigaChatLLM)


async def test_fake_provider_finds_relevant_articles_despite_gigachat_threshold() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_api_key=API_KEY,  # type: ignore[arg-type]
        llm_provider="fake",
        retrieval_min_score=0.3,
    )
    async with build_container(settings) as container:
        assert container.answer_inquiry._min_score < 0.3
