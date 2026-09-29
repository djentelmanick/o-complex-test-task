import logging
import re

from app.adapters.outbound.amocrm.webhooks import WEBHOOK_PATH_PREFIX

_SECRET_IN_PATH = re.compile(rf"({re.escape(WEBHOOK_PATH_PREFIX)})[^/?\s]+")


class MaskWebhookSecretFilter(logging.Filter):
    """Секрет вебхука живёт в пути URL, а uvicorn пишет путь в access-лог."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SECRET_IN_PATH.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def install_log_filters() -> None:
    logging.getLogger("uvicorn.access").addFilter(MaskWebhookSecretFilter())
