import asyncio
import json
import ssl

import httpx
import respx
from cryptography.fernet import Fernet

from app.adapters.outbound.amocrm.gateway import AmoCRMGateway
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.adapters.outbound.gigachat.client import GigaChatEmbedder, GigaChatLLM
from app.adapters.outbound.local.embedder import LocalEmbedder
from app.application.handle_incoming import HandleIncomingMessageUseCase
from app.bootstrap import amocrm_webhook_registration, build_container, make_ssl_context
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


def amocrm(**overrides: object) -> Settings:
    return make(
        llm_provider="fake",
        embedding_provider="fake",
        crm_provider="amocrm",
        amocrm_subdomain="demo",
        amocrm_client_id="cid",
        amocrm_client_secret="csecret",
        amocrm_token_key=Fernet.generate_key().decode(),
        amocrm_webhook_secret="w" * 32,
        **overrides,
    )


async def test_amocrm_provider_wires_gateway_and_handler() -> None:
    async with build_container(amocrm()) as container:
        assert isinstance(container.crm, AmoCRMGateway)
        assert isinstance(container.handle_incoming, HandleIncomingMessageUseCase)
        assert container.amocrm is not None


async def test_mock_provider_has_no_amocrm_parts() -> None:
    async with build_container(make(llm_provider="fake", embedding_provider="fake")) as c:
        assert c.handle_incoming is None
        assert c.amocrm is None


async def test_webhook_is_registered_and_removed(respx_mock: respx.MockRouter) -> None:
    settings = amocrm(amocrm_tunnel_metrics_url="http://tunnel:2000/quicktunnel")
    respx_mock.get("http://tunnel:2000/quicktunnel").mock(
        return_value=httpx.Response(200, json={"hostname": "abc.trycloudflare.com"})
    )
    register = respx_mock.post("https://demo.amocrm.ru/api/v4/webhooks").mock(
        return_value=httpx.Response(201, json={})
    )
    unregister = respx_mock.delete("https://demo.amocrm.ru/api/v4/webhooks").mock(
        return_value=httpx.Response(204)
    )
    async with build_container(settings) as container:
        assert container.amocrm is not None
        container.amocrm.oauth._cached = TokenPair("t", "r", 1e12)
        async with amocrm_webhook_registration(container.amocrm, settings, retry_delay_s=0):
            for _ in range(50):
                if register.call_count == 1:
                    break
                await asyncio.sleep(0.01)
            assert register.call_count == 1
        assert unregister.call_count == 1
    destination = json.loads(register.calls[0].request.content)["destination"]
    assert destination == "https://abc.trycloudflare.com/integrations/amocrm/webhook/" + "w" * 32


async def test_registration_failure_does_not_stop_startup(respx_mock: respx.MockRouter) -> None:
    settings = amocrm(amocrm_tunnel_metrics_url="http://tunnel:2000/quicktunnel")
    respx_mock.get("http://tunnel:2000/quicktunnel").mock(return_value=httpx.Response(503))
    async with build_container(settings) as container:
        assert container.amocrm is not None
        async with amocrm_webhook_registration(container.amocrm, settings, attempts=1):
            pass


async def test_registration_runs_after_startup_and_retries(respx_mock: respx.MockRouter) -> None:
    settings = amocrm(amocrm_tunnel_metrics_url="http://tunnel:2000/quicktunnel")
    respx_mock.get("http://tunnel:2000/quicktunnel").mock(
        return_value=httpx.Response(200, json={"hostname": "abc.trycloudflare.com"})
    )
    # AmoCRM проверяет адрес при регистрации: пока сервер не слушает, отвечает 400
    register = respx_mock.post("https://demo.amocrm.ru/api/v4/webhooks").mock(
        side_effect=[httpx.Response(400), httpx.Response(201, json={})]
    )
    unregister = respx_mock.delete("https://demo.amocrm.ru/api/v4/webhooks").mock(
        return_value=httpx.Response(204)
    )
    async with build_container(settings) as container:
        assert container.amocrm is not None
        container.amocrm.oauth._cached = TokenPair("t", "r", 1e12)
        async with amocrm_webhook_registration(container.amocrm, settings, retry_delay_s=0):
            assert register.call_count == 0
            for _ in range(50):
                if register.call_count == 2:
                    break
                await asyncio.sleep(0.01)
            assert register.call_count == 2
        assert unregister.call_count == 1
