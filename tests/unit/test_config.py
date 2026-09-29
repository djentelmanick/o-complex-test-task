import pytest
from pydantic import ValidationError

from app.config import Settings

API_KEY = "test-api-key-0123456789abcdef"


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, app_api_key=API_KEY, **overrides)  # type: ignore[arg-type]


def test_fake_provider_does_not_need_gigachat_key() -> None:
    settings = make(llm_provider="fake")
    assert settings.gigachat_auth_key is None


def test_gigachat_provider_requires_auth_key() -> None:
    with pytest.raises(ValidationError, match="GIGACHAT_AUTH_KEY"):
        make(llm_provider="gigachat", gigachat_auth_key=None)


def test_short_api_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_api_key="short", llm_provider="fake")  # type: ignore[arg-type]


def test_secrets_are_hidden_in_repr() -> None:
    settings = make(llm_provider="gigachat", gigachat_auth_key="super-secret-key")
    assert "super-secret-key" not in repr(settings)
    assert API_KEY not in repr(settings)


def test_defaults_match_spec() -> None:
    settings = make(llm_provider="fake")
    assert settings.retrieval_limit == 4
    assert settings.dialog_max_messages == 10
    assert settings.rate_limit == "10/minute"
    assert settings.embedding_provider == "local"
    assert settings.embedding_dim == 384


def test_gigachat_embeddings_require_auth_key() -> None:
    with pytest.raises(ValidationError, match="GIGACHAT_AUTH_KEY"):
        make(llm_provider="fake", embedding_provider="gigachat")


AMOCRM = {
    "crm_provider": "amocrm",
    "amocrm_subdomain": "demo-shop",
    "amocrm_client_id": "client-id",
    "amocrm_client_secret": "client-secret",
    "amocrm_token_key": "k" * 44,
    "amocrm_webhook_secret": "w" * 32,
}


def test_crm_provider_defaults_to_mock() -> None:
    settings = make(llm_provider="fake")
    assert settings.crm_provider == "mock"
    assert settings.amocrm_redirect_uri == "https://example.com"
    assert settings.amocrm_webhook_rate_limit == "60/minute"


def test_amocrm_provider_accepts_full_settings() -> None:
    settings = make(llm_provider="fake", **AMOCRM)
    assert settings.amocrm_subdomain == "demo-shop"


@pytest.mark.parametrize(
    "missing", ["amocrm_subdomain", "amocrm_client_secret", "amocrm_token_key"]
)
def test_amocrm_provider_requires_settings(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing.upper()):
        make(llm_provider="fake", **{**AMOCRM, missing: None})


@pytest.mark.parametrize("subdomain", ["evil.com/x", "Demo", "a b", "x" * 64])
def test_amocrm_subdomain_is_restricted(subdomain: str) -> None:
    with pytest.raises(ValidationError):
        make(llm_provider="fake", **{**AMOCRM, "amocrm_subdomain": subdomain})


def test_short_webhook_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(llm_provider="fake", **{**AMOCRM, "amocrm_webhook_secret": "short"})
