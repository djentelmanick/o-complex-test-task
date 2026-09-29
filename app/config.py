from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
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

    embedding_dim: int = Field(default=1024, gt=0)
    retrieval_limit: int = Field(default=4, gt=0)
    retrieval_min_score: float = Field(default=0.3, ge=-1.0, le=1.0)
    dialog_max_messages: int = Field(default=10, gt=0)
    rate_limit: str = "10/minute"

    kb_dir: Path = Path("data/kb")
    crm_fixture_path: Path = Path("data/crm_dialogs.json")

    @model_validator(mode="after")
    def _require_gigachat_key(self) -> Self:
        if self.llm_provider == "gigachat" and self.gigachat_auth_key is None:
            raise ValueError("GIGACHAT_AUTH_KEY is required when LLM_PROVIDER=gigachat")
        return self
