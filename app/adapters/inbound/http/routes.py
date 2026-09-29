import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter

from app.adapters.inbound.http.errors import error_response
from app.adapters.inbound.http.schemas import InquiryRequest, InquiryResponse, LeadOut
from app.adapters.inbound.http.security import require_api_key
from app.config import Settings
from app.container import Container
from app.domain.models import Inquiry

logger = logging.getLogger(__name__)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def build_api_router(settings: Settings, limiter: Limiter) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1", dependencies=[Depends(require_api_key(settings.app_api_key))]
    )

    @router.post("/inquiries", response_model=InquiryResponse)
    @limiter.limit(settings.rate_limit)
    async def create_inquiry(request: Request, body: InquiryRequest) -> InquiryResponse:
        started = time.perf_counter()
        answer = await get_container(request).answer_inquiry.execute(
            Inquiry(lead_id=body.lead_id, message=body.message)
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "inquiry processed request_id=%s lead_id=%s message_len=%d tokens=%d "
            "latency_ms=%d fallback=%s",
            request.state.request_id,
            body.lead_id,
            len(body.message),
            answer.usage.total,
            latency_ms,
            answer.fallback,
        )
        return InquiryResponse.from_domain(answer, latency_ms)

    @router.get("/leads", response_model=list[LeadOut])
    async def list_leads(request: Request) -> list[LeadOut]:
        leads = await get_container(request).crm.list_leads()
        return [LeadOut.from_domain(lead) for lead in leads]

    return router


def build_service_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready", response_model=None)
    async def ready(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            chunks = await get_container(request).knowledge.count()
        except Exception:
            logger.exception("readiness check failed")
            chunks = 0
        if chunks == 0:
            return error_response(request, 503, "Сервис не готов: база знаний недоступна")
        return {"status": "ready", "knowledge_chunks": chunks}

    return router
