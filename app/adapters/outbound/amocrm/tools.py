from dataclasses import dataclass

import httpx

from app.adapters.outbound.amocrm.gateway import AmoCRMGateway
from app.adapters.outbound.amocrm.oauth import AmoCRMOAuth
from app.adapters.outbound.amocrm.webhooks import AmoCRMWebhookRegistrar


@dataclass(frozen=True, slots=True)
class AmoCRMTools:
    """Части интеграции, нужные вне use case'ов: CLI и регистрация вебхука."""

    http: httpx.AsyncClient
    oauth: AmoCRMOAuth
    gateway: AmoCRMGateway
    registrar: AmoCRMWebhookRegistrar
