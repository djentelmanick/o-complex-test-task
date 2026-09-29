import logging

from app.adapters.inbound.http.log_filters import MaskWebhookSecretFilter


def test_access_log_masks_webhook_secret() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", "/integrations/amocrm/webhook/SUPERSECRET?x=1", "1.1", 200),
        None,
    )
    assert MaskWebhookSecretFilter().filter(record) is True
    message = record.getMessage()
    assert "SUPERSECRET" not in message
    assert "/integrations/amocrm/webhook/***" in message
