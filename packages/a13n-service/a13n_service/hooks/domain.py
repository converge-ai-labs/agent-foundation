"""Public contracts for durable lifecycle Hook subscriptions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId
from a13n_service.secrets.domain import SecretId
from a13n_service.temporal import require_aware_utc

HookSubscriptionId = Annotated[str, StringConstraints(pattern=r"^hsub_[a-z0-9]{16,64}$")]
HookSubscriptionRevisionId = Annotated[str, StringConstraints(pattern=r"^hsubr_[a-z0-9]{16,64}$")]
HookName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", max_length=128)]
ThreadId = Annotated[str, StringConstraints(pattern=r"^thread-[a-f0-9]{32}$", max_length=39)]

RUN_HOOK_NAMES = frozenset(
    {
        "run.accepted",
        "run.running",
        "run.waiting",
        "run.completed",
        "run.failed",
        "run.cancelled",
    }
)
RUN_ATTEMPT_HOOK_NAMES = frozenset(
    {
        "run_attempt.leased",
        "run_attempt.running",
        "run_attempt.succeeded",
        "run_attempt.yielded",
        "run_attempt.failed",
        "run_attempt.cancelled",
    }
)
DURABLE_WEBHOOK_HOOK_NAMES = RUN_HOOK_NAMES | RUN_ATTEMPT_HOOK_NAMES
LIVE_HOOK_NAMES = frozenset(
    {
        "agui.text_message_start",
        "agui.text_message_content",
        "agui.text_message_end",
        "agui.reasoning_message_start",
        "agui.reasoning_message_content",
        "agui.reasoning_encrypted_value",
        "agui.reasoning_message_end",
        "agui.tool_call_start",
        "agui.tool_call_args",
        "agui.tool_call_end",
        "agui.tool_call_result",
        "agui.run_finished",
        "agui.run_error",
        "agui.custom",
        "item.completed",
        "item.failed",
        "item.interrupted",
        "environment.preparation.started",
        "environment.preparation.ready",
        "environment.preparation.failed",
        "environment.adapter.closed",
    }
)
HOOK_NAMES = DURABLE_WEBHOOK_HOOK_NAMES | LIVE_HOOK_NAMES

_MAX_HOOK_NAMES = 128


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WebhookDestinationConfig(_StrictModel):
    endpoint_url: Annotated[str, StringConstraints(min_length=1, max_length=8192)]
    signing_secret_id: SecretId
    signature_profile: Literal["hmac_sha256_v1"] = "hmac_sha256_v1"

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_safe_syntax(cls, value: str) -> str:
        try:
            normalized, _, _ = EndpointPolicy().validate_syntax(value)
        except EndpointPolicyError as error:
            raise ValueError(str(error)) from error
        return normalized


class InlineHookSubscriptionInput(_StrictModel):
    hook_names: tuple[HookName, ...] = Field(min_length=1, max_length=_MAX_HOOK_NAMES)
    webhook: WebhookDestinationConfig

    @field_validator("hook_names")
    @classmethod
    def validate_hook_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_durable_hook_names(value)

    def bind_run_scope(
        self,
        *,
        session_id: str,
        thread_id: str,
        run_id: str,
    ) -> CreateHookSubscriptionRequest:
        return CreateHookSubscriptionRequest(
            hook_names=self.hook_names,
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id,
            webhook=self.webhook,
        )


class InlineHookRequest(_StrictModel):
    """Run command selection: omission inherits, null opts out, an object replaces."""

    hook_subscription: InlineHookSubscriptionInput | None = None

    @model_serializer(mode="wrap")
    def preserve_hook_selection(self, handler: SerializerFunctionWrapHandler):
        payload = handler(self)
        if "hook_subscription" not in self.model_fields_set:
            payload.pop("hook_subscription", None)
        elif self.hook_subscription is None:
            payload["hook_subscription"] = None
        return payload


class CreateHookSubscriptionRequest(InlineHookSubscriptionInput):
    session_id: ObjectId | None = None
    thread_id: ThreadId | None = None
    run_id: ObjectId | None = None


class UpdateHookSubscriptionRequest(CreateHookSubscriptionRequest):
    pass


class UpdateHookSubscriptionStateRequest(_StrictModel):
    enabled: bool


class HookSubscriptionRevision(_StrictModel):
    id: HookSubscriptionRevisionId
    hook_subscription_id: HookSubscriptionId
    version: int = Field(ge=1)
    hook_names: tuple[HookName, ...]
    session_id: ObjectId | None = None
    thread_id: ThreadId | None = None
    run_id: ObjectId | None = None
    webhook: WebhookDestinationConfig
    created_by: PrincipalRef
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        try:
            return require_aware_utc(value)
        except ValueError as error:
            raise ValueError("created_at must include a UTC offset") from error


class HookSubscription(_StrictModel):
    id: HookSubscriptionId
    version: int = Field(ge=1)
    current_revision_id: HookSubscriptionRevisionId
    workspace_id: ObjectId
    enabled: bool
    inline_run_id: ObjectId | None = None
    expired_at: datetime | None = None
    deleted_at: datetime | None = None
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
    current_revision: HookSubscriptionRevision

    @model_validator(mode="after")
    def revision_matches_head(self) -> HookSubscription:
        if (
            self.current_revision.id != self.current_revision_id
            or self.current_revision.hook_subscription_id != self.id
            or self.current_revision.version != self.version
        ):
            raise ValueError("current HookSubscription Revision does not match its head")
        return self


class HookSubscriptionCollection(_StrictModel):
    items: tuple[HookSubscription, ...]
    next_cursor: str | None = None


def _canonical_durable_hook_names(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("hook names must be unique")
    unsupported = set(value) - DURABLE_WEBHOOK_HOOK_NAMES
    if unsupported:
        raise ValueError("durable Webhook subscriptions accept only lifecycle Hook names")
    return tuple(sorted(value))


__all__ = [
    "DURABLE_WEBHOOK_HOOK_NAMES",
    "HOOK_NAMES",
    "LIVE_HOOK_NAMES",
    "CreateHookSubscriptionRequest",
    "HookName",
    "HookSubscription",
    "HookSubscriptionCollection",
    "HookSubscriptionId",
    "HookSubscriptionRevision",
    "HookSubscriptionRevisionId",
    "InlineHookSubscriptionInput",
    "UpdateHookSubscriptionRequest",
    "UpdateHookSubscriptionStateRequest",
    "WebhookDestinationConfig",
]
