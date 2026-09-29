import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.adapters.inbound.http.app import create_app
from app.bootstrap import build_container, warm_up
from app.config import Settings
from app.container import Container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def _warm_container(settings: Settings) -> AsyncIterator[Container]:
    async with build_container(settings) as container:
        await warm_up(container.answer_inquiry.embedder, settings)
        yield container


app = create_app(Settings(), _warm_container)
