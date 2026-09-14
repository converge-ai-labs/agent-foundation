from __future__ import annotations

import pytest
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from pydantic import ValidationError


def _webhook(endpoint_url: str = "https://EXAMPLE.com/hooks/") -> WebhookDestinationConfig:
    return WebhookDestinationConfig(
        endpoint_url=endpoint_url,
        signing_secret_id="sec_1234567890abcdef",
    )


def test_canonicalizes_hook_names_and_host_but_preserves_endpoint_path() -> None:
    value = InlineHookSubscriptionInput(
        hook_names=("run_attempt.failed", "run.accepted"),
        webhook=_webhook(),
    )

    assert value.hook_names == ("run.accepted", "run_attempt.failed")
    assert value.webhook.endpoint_url == "https://example.com/hooks/"


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


@pytest.mark.parametrize("kind", ["feedback", "waiting_continue", "retry", "submission"])
def test_run_request_json_roundtrip_preserves_omission_null_and_replacement(kind):
    from a13n_service.gateway.requests import RetryRunRequest
    from a13n_service.interactions.command_values import WaitingContinueRunCommand
    from a13n_service.interactions.control_domain import ThreadRunSubmissionRequest, WaitingRunFeedbackRequest

    request_type = {
        "feedback": WaitingRunFeedbackRequest,
        "waiting_continue": WaitingContinueRunCommand,
        "retry": RetryRunRequest,
        "submission": ThreadRunSubmissionRequest,
    }[kind]
    values = {"expected_thread_version": 2}
    if kind in {"waiting_continue", "submission"}:
        values["input"] = {"schema_version": "2", "content": [{"type": "text", "text": "next"}]}
    if kind in {"feedback", "waiting_continue"}:
        values["sealed_state_digest_sha256"] = "a" * 64
    replacement = InlineHookSubscriptionInput(hook_names=("run.completed",), webhook=_webhook())
    payloads = []
    for selection in ({}, {"hook_subscription": None}, {"hook_subscription": replacement}):
        request = request_type.model_validate({**values, **selection})
        payload = request.model_dump_json(exclude_none=True)
        restored = request_type.model_validate_json(payload)
        assert ("hook_subscription" in restored.model_fields_set) == bool(selection)
        assert restored.hook_subscription == selection.get("hook_subscription")
        payloads.append(payload)
    assert len(set(payloads)) == 3
    schema = request_type.model_json_schema()
    assert "hook_subscription" not in schema["required"]
    assert {"type": "null"} in schema["properties"]["hook_subscription"]["anyOf"]
