from app.domain.models import TokenUsage


def test_token_usage_total_and_sum() -> None:
    usage = TokenUsage(prompt=10, completion=5) + TokenUsage(prompt=1, completion=2)
    assert usage == TokenUsage(prompt=11, completion=7)
    assert usage.total == 18


def test_dialog_message_id_is_optional() -> None:
    from app.domain.models import DialogMessage, Role

    assert DialogMessage(Role.CLIENT, "привет").id == ""
    assert DialogMessage(Role.CLIENT, "привет", id="42").id == "42"
