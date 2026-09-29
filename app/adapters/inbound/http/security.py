import secrets
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import SecretStr
from slowapi.util import get_remote_address
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
# Swagger UI грузит скрипты с CDN и использует inline-код — строгий CSP его ломает
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(expected: SecretStr) -> Callable[..., Awaitable[None]]:
    expected_bytes = expected.get_secret_value().encode()

    async def dependency(api_key: str | None = Security(_api_key_header)) -> None:
        if api_key is None or not secrets.compare_digest(api_key.encode(), expected_bytes):
            raise HTTPException(status_code=401, detail="Неверный или отсутствующий API-ключ")

    return dependency


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(_DOCS_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                if not is_docs:
                    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(HTTPException):
    # Наследуемся от HTTPException: FastAPI превращает прочие ошибки чтения тела в 400
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Слишком большой запрос")


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None and (
            not content_length.isdigit() or int(content_length) > self.max_bytes
        ):
            await _too_large_response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _too_large_response(scope, receive, send)


async def _too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    request_id = scope.get("state", {}).get("request_id")
    response = JSONResponse(
        status_code=413, content={"detail": "Слишком большой запрос", "request_id": request_id}
    )
    await response(scope, receive, send)


_TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")


def client_ip(request: Request) -> str:
    # За туннелем все запросы приходят с адреса cloudflared — настоящий IP в заголовке Cloudflare
    return request.headers.get("cf-connecting-ip") or get_remote_address(request)


class TunnelGuardMiddleware:
    """Через публичный туннель доступен только вебхук: демо-страница и API остаются локальными."""

    def __init__(self, app: ASGIApp, allowed_prefix: str) -> None:
        self.app = app
        self.allowed_prefix = allowed_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not scope["path"].startswith(self.allowed_prefix):
            headers = Headers(scope=scope)
            if any(name in headers for name in _TUNNEL_HEADERS):
                request_id = scope.get("state", {}).get("request_id")
                response = JSONResponse(
                    status_code=404, content={"detail": "Not Found", "request_id": request_id}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
