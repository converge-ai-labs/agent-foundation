from __future__ import annotations

import pytest
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from pydantic import ValidationError


def _webhook(endpoint_url: str = "https://EXAMPLE.com/hooks/") -> WebhookDestinationConfig:
    return WebhookDestinationConfig(
        endpoint_url=endpoint_url,
        signing_secret_id="sec_1234567890abcdef",
    )


def test_canonicalizes_durable_hook_names_and_endpoint() -> None:
    value = InlineHookSubscriptionInput(
        hook_names=("run_attempt.failed", "run.accepted"),
        webhook=_webhook(),
    )

    assert value.hook_names == ("run.accepted", "run_attempt.failed")
    assert value.webhook.endpoint_url == "https://example.com/hooks"


@pytest.mark.parametrize("hook_name", ["agui.run_finished", "item.completed", "unknown.event"])
def test_rejects_live_or_unknown_names_for_durable_webhook(hook_name: str) -> None:
    with pytest.raises(ValidationError, match="only lifecycle Hook names"):
        InlineHookSubscriptionInput(hook_names=(hook_name,), webhook=_webhook())


def test_rejects_duplicate_names_and_unsafe_endpoint_syntax() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        InlineHookSubscriptionInput(
            hook_names=("run.accepted", "run.accepted"),
            webhook=_webhook(),
        )
    with pytest.raises(ValidationError, match="user information"):
        _webhook("https://user:password@example.com/hooks")
