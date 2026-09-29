from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_api_key: SecretStr = Field(min_length=16)
    llm_provider: Literal["gigachat", "fake"] = "gigachat"
    database_url: str = "postgresql+asyncpg://assistant:assistant@db:5432/assistant"

    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_model: str = "GigaChat-2-Pro"
    gigachat_embedding_model: str = "Embeddings"
    gigachat_ca_bundle: str | None = None
    gigachat_timeout_s: float = 30.0

    embedding_provider: Literal["local", "gigachat", "fake"] = "local"
    local_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: Path | None = None
    embedding_dim: int = Field(default=384, gt=0)
    retrieval_limit: int = Field(default=4, gt=0)
    retrieval_min_score: float = Field(default=0.3, ge=-1.0, le=1.0)
    dialog_max_messages: int = Field(default=10, gt=0)
    rate_limit: str = "10/minute"

    kb_dir: Path = Path("data/kb")
    crm_fixture_path: Path = Path("data/crm_dialogs.json")

    crm_provider: Literal["mock", "amocrm"] = "mock"
    amocrm_subdomain: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,63}$")
    amocrm_client_id: str | None = None
    amocrm_client_secret: SecretStr | None = None
    amocrm_redirect_uri: str = "https://example.com"
    amocrm_token_key: SecretStr | None = None
    amocrm_webhook_secret: SecretStr | None = Field(default=None, min_length=32)
    amocrm_tunnel_metrics_url: str | None = None
    amocrm_webhook_rate_limit: str = "60/minute"
    amocrm_timeout_s: float = 15.0

    @model_validator(mode="after")
    def _require_gigachat_key(self) -> Self:
        uses_gigachat = "gigachat" in (self.llm_provider, self.embedding_provider)
        if uses_gigachat and self.gigachat_auth_key is None:
            raise ValueError(
                "GIGACHAT_AUTH_KEY is required when LLM_PROVIDER or EMBEDDING_PROVIDER is gigachat"
            )
        return self

    @field_validator("amocrm_subdomain", mode="before")
    @classmethod
    def _normalize_subdomain(cls, value: object) -> object:
        # В .env часто вставляют адрес целиком — оставляем только имя аккаунта
        if not isinstance(value, str):
            return value
        host = value.strip().lower().removeprefix("https://").removeprefix("http://")
        return host.rstrip("/").removesuffix(".amocrm.ru")

    @model_validator(mode="after")
    def _require_amocrm_settings(self) -> Self:
        if self.crm_provider != "amocrm":
            return self
        required = (
            "amocrm_subdomain",
            "amocrm_client_id",
            "amocrm_client_secret",
            "amocrm_token_key",
            "amocrm_webhook_secret",
        )
        missing = [name.upper() for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"CRM_PROVIDER=amocrm requires: {', '.join(missing)}")
        return self
