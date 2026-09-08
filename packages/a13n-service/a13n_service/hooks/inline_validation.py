"""Admission and acceptance validation for exact-Run inline Hooks."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import (
    AuthorizationError,
    PrincipalRef,
    WorkspaceAction,
    authorize_persisted_agent_principal_actions,
)

from .domain import InlineHookSubscriptionInput
from .invariants import HookSubscriptionInvariantError, require_active_workspace_secret
from .validation import EndpointValidator, HookEndpointValidationError, validate_hook_endpoint

_INLINE_HOOK_ACTIONS = frozenset(
    {
        WorkspaceAction.hook_subscription_create,
        WorkspaceAction.secrets_bind,
    }
)


class InlineHookValidationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InlineHookValidator:
    """Validate live destination policy and current durable Principal authority."""

    def __init__(
        self,
        endpoint_validator: EndpointValidator,
        *,
        endpoint_timeout_seconds: float = 10.0,
    ) -> None:
        if endpoint_timeout_seconds <= 0:
            raise ValueError("Hook endpoint validation timeout must be positive")
        self._endpoint_validator = endpoint_validator
        self._endpoint_timeout_seconds = endpoint_timeout_seconds

    async def validate_destination(self, subscription: InlineHookSubscriptionInput | None) -> None:
        if subscription is None:
            return
        try:
            await validate_hook_endpoint(
                self._endpoint_validator,
                subscription.webhook.endpoint_url,
                timeout_seconds=self._endpoint_timeout_seconds,
            )
        except HookEndpointValidationError as error:
            raise InlineHookValidationError(
                "invalid_webhook_endpoint",
                "The inline Hook Webhook endpoint is not allowed",
            ) from error

    async def authorize(
        self,
        database: AsyncSession,
        *,
        principal: PrincipalRef,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        subscription: InlineHookSubscriptionInput | None,
    ) -> None:
        if subscription is None:
            return
        try:
            await authorize_persisted_agent_principal_actions(
                database,
                principal=principal,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actions=_INLINE_HOOK_ACTIONS,
            )
            await require_active_workspace_secret(
                database,
                organization_id=organization_id,
                workspace_id=workspace_id,
                secret_id=subscription.webhook.signing_secret_id,
            )
        except AuthorizationError as error:
            raise InlineHookValidationError(
                "inline_hook_unauthorized",
                "The Run authority Principal cannot create this inline Hook",
            ) from error
        except HookSubscriptionInvariantError as error:
            raise InlineHookValidationError(
                "inline_hook_secret_unavailable",
                "The inline Hook signing Secret is unavailable",
            ) from error


__all__ = ["InlineHookValidationError", "InlineHookValidator"]
