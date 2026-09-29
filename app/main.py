import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from app.adapters.inbound.http.app import create_app
from app.adapters.inbound.http.log_filters import install_log_filters
from app.bootstrap import amocrm_webhook_registration, build_container, warm_up
from app.config import Settings
from app.container import Container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
install_log_filters()


@asynccontextmanager
async def _app_container(settings: Settings) -> AsyncIterator[Container]:
    async with AsyncExitStack() as stack:
        container = await stack.enter_async_context(build_container(settings))
        await warm_up(container.answer_inquiry.embedder, settings)
        if container.amocrm is not None:
            await stack.enter_async_context(amocrm_webhook_registration(container.amocrm, settings))
        yield container


app = create_app(Settings(), _app_container)
