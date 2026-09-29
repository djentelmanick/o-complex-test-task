import json

import httpx
import pytest
import respx

from app.adapters.outbound.gigachat.client import GigaChatClient, GigaChatEmbedder, GigaChatLLM
from app.domain.errors import LLMInvalidOutput, LLMUnavailable
from app.domain.models import TokenUsage

BASE = "https://api.test/api/v1"
SCHEMA = {"type": "object", "properties": {}}
ARGS = {"client_reply": "Здравствуйте", "manager_hint": "hint", "used_chunk_ids": []}


class StubTokens:
    def __init__(self) -> None:
        self.token = "t1"
        self.invalidated = 0

    async def get(self) -> str:
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1
        self.token = "t2"


async def no_sleep(_: float) -> None:
    return None


def chat_response(arguments: object, name: str = "submit_answer") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "function_call": {"name": name, "arguments": arguments},
                    },
                    "finish_reason": "function_call",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        },
    )


def make_llm(http: httpx.AsyncClient, tokens: StubTokens | None = None) -> GigaChatLLM:
    client = GigaChatClient(http, BASE, tokens or StubTokens(), backoff_s=0, sleep=no_sleep)
    return GigaChatLLM(client, "GigaChat-2-Pro")


async def test_llm_sends_forced_function_call_and_parses_dict_arguments(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(f"{BASE}/chat/completions").mock(return_value=chat_response(ARGS))
    async with httpx.AsyncClient() as http:
        result = await make_llm(http).complete_structured("sys", "user", "submit_answer", SCHEMA)

    assert result.arguments == ARGS
    assert result.usage == TokenUsage(prompt=120, completion=30)
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer t1"
    body = json.loads(request.content)
    assert body["model"] == "GigaChat-2-Pro"
    assert body["function_call"] == {"name": "submit_answer"}
    assert body["functions"][0]["parameters"] == SCHEMA
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


async def test_llm_parses_arguments_given_as_json_string(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE}/chat/completions").mock(
        return_value=chat_response(json.dumps(ARGS, ensure_ascii=False))
    )
    async with httpx.AsyncClient() as http:
        result = await make_llm(http).complete_structured("s", "u", "submit_answer", SCHEMA)
    assert result.arguments == ARGS


@pytest.mark.parametrize(
    "response",
    [
        chat_response("{not json"),
        chat_response(ARGS, name="other_function"),
        chat_response(["list", "not", "dict"]),
        httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "текст"}}]}
        ),
        httpx.Response(200, json={"choices": []}),
    ],
)
async def test_llm_rejects_malformed_function_calls(
    respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    respx_mock.post(f"{BASE}/chat/completions").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMInvalidOutput):
            await make_llm(http).complete_structured("s", "u", "submit_answer", SCHEMA)


async def test_retries_on_server_error_then_succeeds(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("x"), chat_response(ARGS)]
    )
    async with httpx.AsyncClient() as http:
        await make_llm(http).complete_structured("s", "u", "submit_answer", SCHEMA)
    assert route.call_count == 3


async def test_gives_up_after_retries_on_rate_limit(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailable):
            await make_llm(http).complete_structured("s", "u", "submit_answer", SCHEMA)
    assert route.call_count == 3


async def test_unauthorized_refreshes_token_once(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.Response(401), chat_response(ARGS)]
    )
    tokens = StubTokens()
    async with httpx.AsyncClient() as http:
        await make_llm(http, tokens).complete_structured("s", "u", "submit_answer", SCHEMA)
    assert tokens.invalidated == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer t2"


async def test_client_error_is_not_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(400))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailable):
            await make_llm(http).complete_structured("s", "u", "submit_answer", SCHEMA)
    assert route.call_count == 1


async def test_embedder_orders_vectors_by_index(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [0.0, 1.0], "index": 1},
                    {"object": "embedding", "embedding": [1.0, 0.0], "index": 0},
                ],
                "model": "Embeddings",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = GigaChatClient(http, BASE, StubTokens(), backoff_s=0, sleep=no_sleep)
        vectors = await GigaChatEmbedder(client, "Embeddings").embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert json.loads(route.calls[0].request.content) == {
        "model": "Embeddings",
        "input": ["a", "b"],
    }
