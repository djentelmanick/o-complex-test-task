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
    assert settings.embedding_dim == 1024
