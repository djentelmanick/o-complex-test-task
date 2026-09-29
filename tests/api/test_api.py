from collections.abc import AsyncIterator

from app.domain.errors import KnowledgeBaseUnavailable, LLMUnavailable
from tests.api.conftest import API_KEY, make_client, make_container, make_settings
from tests.fakes import InMemoryKnowledgeRepository, ScriptedLLM

AUTH = {"X-API-Key": API_KEY}
BODY = {"lead_id": "lead-1", "message": "Как принимать цеолит?"}


async def test_health_has_security_headers_and_request_id() -> None:
    async with make_client() as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert len(response.headers["X-Request-ID"]) == 32


async def test_ready_reflects_knowledge_base_state() -> None:
    async with make_client() as client:
        assert (await client.get("/ready")).status_code == 200
    async with make_client(container=await make_container(empty_kb=True)) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["request_id"]


async def test_inquiry_requires_valid_api_key() -> None:
    async with make_client() as client:
        missing = await client.post("/api/v1/inquiries", json=BODY)
        wrong = await client.post("/api/v1/inquiries", json=BODY, headers={"X-API-Key": "nope"})
        non_ascii = await client.post(
            "/api/v1/inquiries", json=BODY, headers={"X-API-Key": "ключ".encode()}
        )
    assert missing.status_code == wrong.status_code == non_ascii.status_code == 401


async def test_inquiry_returns_both_blocks_sources_and_usage() -> None:
    async with make_client() as client:
        response = await client.post("/api/v1/inquiries", json=BODY, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["client_reply"]
    assert data["manager_hint"]
    assert data["sources"] == [{"doc_id": "zeolite", "title": "Zeolite"}]
    assert data["usage"]["total_tokens"] == (
        data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]
    )
    assert data["latency_ms"] >= 0
    assert data["fallback"] is False


async def test_whitespace_only_message_is_rejected_without_echo() -> None:
    async with make_client() as client:
        response = await client.post(
            "/api/v1/inquiries", json={"lead_id": "lead-1", "message": "   \n  "}, headers=AUTH
        )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Некорректный запрос"
    assert all("input" not in error for error in body["errors"])


async def test_message_length_and_lead_id_format_are_validated() -> None:
    async with make_client() as client:
        too_long = await client.post(
            "/api/v1/inquiries", json={"lead_id": "lead-1", "message": "я" * 2001}, headers=AUTH
        )
        bad_lead = await client.post(
            "/api/v1/inquiries", json={"lead_id": "../etc/passwd", "message": "hi"}, headers=AUTH
        )
        extra = await client.post(
            "/api/v1/inquiries", json={**BODY, "system": "override"}, headers=AUTH
        )
    assert too_long.status_code == bad_lead.status_code == extra.status_code == 422


async def test_unknown_lead_is_404() -> None:
    async with make_client() as client:
        response = await client.post(
            "/api/v1/inquiries", json={"lead_id": "lead-404", "message": "hi"}, headers=AUTH
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Лид не найден"


async def test_llm_outage_is_503_without_internal_details() -> None:
    container = await make_container(llm=ScriptedLLM([LLMUnavailable("secret upstream detail")]))
    async with make_client(container=container) as client:
        response = await client.post("/api/v1/inquiries", json=BODY, headers=AUTH)
    assert response.status_code == 503
    assert "secret upstream detail" not in response.text


async def test_rate_limit() -> None:
    async with make_client(settings=make_settings(rate_limit="2/minute")) as client:
        codes = [
            (await client.post("/api/v1/inquiries", json=BODY, headers=AUTH)).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]


async def test_oversized_body_with_content_length_is_413() -> None:
    async with make_client() as client:
        response = await client.post(
            "/api/v1/inquiries",
            content=b"x" * (17 * 1024),
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 413


async def test_oversized_chunked_body_is_413() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"x" * 1024

    async with make_client() as client:
        response = await client.post(
            "/api/v1/inquiries",
            content=chunks(),
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 413


async def test_leads_endpoint_returns_dialogs() -> None:
    async with make_client() as client:
        response = await client.get("/api/v1/leads", headers=AUTH)
    assert response.status_code == 200
    [lead] = response.json()
    assert lead["id"] == "lead-1"
    assert lead["dialog"][0] == {"role": "client", "text": "Хочу детокс"}


async def test_index_page_is_served_with_csp() -> None:
    async with make_client() as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


async def test_docs_are_served_without_strict_csp() -> None:
    async with make_client() as client:
        response = await client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


async def test_unexpected_error_is_500_with_request_id_and_security_headers() -> None:
    container = await make_container(llm=ScriptedLLM([RuntimeError("secret internal detail")]))
    async with make_client(container=container) as client:
        response = await client.post("/api/v1/inquiries", json=BODY, headers=AUTH)
    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "Content-Security-Policy" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_broken_knowledge_base_is_503() -> None:
    container = await make_container()

    async def broken_search(*_: object) -> list[object]:
        raise KnowledgeBaseUnavailable("vector dimension mismatch")

    assert isinstance(container.knowledge, InMemoryKnowledgeRepository)
    container.knowledge.search = broken_search  # type: ignore[method-assign]
    async with make_client(container=container) as client:
        response = await client.post("/api/v1/inquiries", json=BODY, headers=AUTH)
    assert response.status_code == 503
    assert "dimension" not in response.text
