import logging
import uuid
from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.errors import (
    CRMUnavailable,
    KnowledgeBaseUnavailable,
    LeadNotFound,
    LLMUnavailable,
)

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Входящий X-Request-ID не принимаем: id генерирует сервер, чтобы его нельзя было
        # подделать в логах
        request_id = uuid.uuid4().hex
        # Свой state на каждый запрос: не все ASGI-серверы копируют lifespan state, а slowapi
        # хранит в нём флаги, которые не должны переживать запрос
        scope["state"] = {**scope.get("state", {}), "request_id": request_id}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


class UnhandledErrorMiddleware:
    """Превращает неожиданные исключения в 500 внутри нашего стека middleware.

    Обработчик Exception в Starlette срабатывает во внешнем ServerErrorMiddleware, и такой
    ответ уходит без X-Request-ID и security-заголовков.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if response_started:
                raise
            logger.exception("unhandled error")
            request_id = scope.get("state", {}).get("request_id")
            response = JSONResponse(
                status_code=500,
                content={"detail": "Внутренняя ошибка сервера", "request_id": request_id},
            )
            await response(scope, receive, send)


def error_response(
    request: Request,
    status_code: int,
    detail: str,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    content = {"detail": detail, "request_id": getattr(request.state, "request_id", None), **extra}
    return JSONResponse(status_code=status_code, content=content, headers=headers)


async def _lead_not_found(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, 404, "Лид не найден")


async def _llm_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("LLM unavailable: %s", exc)
    return error_response(request, 503, "Сервис временно недоступен, попробуйте позже")


async def _knowledge_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.error("knowledge base unavailable: %s", exc)
    return error_response(request, 503, "База знаний недоступна, попробуйте позже")


async def _crm_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.error("CRM unavailable: %s", exc)
    return error_response(request, 503, "CRM временно недоступна, попробуйте позже")


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    return error_response(
        request, http_exc.status_code, str(http_exc.detail), headers=http_exc.headers
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    # Без поля input: не возвращаем клиенту его же данные (могут содержать ПДн)
    errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in validation_exc.errors()]
    return error_response(request, 422, "Некорректный запрос", errors=errors)


async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, 429, "Слишком много запросов, попробуйте позже")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LeadNotFound, _lead_not_found)
    app.add_exception_handler(LLMUnavailable, _llm_unavailable)
    app.add_exception_handler(KnowledgeBaseUnavailable, _knowledge_unavailable)
    app.add_exception_handler(CRMUnavailable, _crm_unavailable)
    app.add_exception_handler(RateLimitExceeded, _rate_limited)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
