from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.adapters.inbound.http.errors import RequestIdMiddleware, install_error_handlers
from app.adapters.inbound.http.routes import build_api_router, build_service_router
from app.adapters.inbound.http.security import (
    MAX_BODY_BYTES,
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.config import Settings
from app.container import Container

STATIC_DIR = Path(__file__).parent / "static"

ContainerFactory = Callable[[Settings], AbstractAsyncContextManager[Container]]


def create_app(settings: Settings, container_factory: ContainerFactory) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with container_factory(settings) as container:
            app.state.container = container
            yield

    app = FastAPI(title="O-complex Inquiry Assistant", version="0.1.0", lifespan=lifespan)

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    install_error_handlers(app)

    app.include_router(build_service_router())
    app.include_router(build_api_router(settings, limiter))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app
