"""Trusted entry contexts for default native tools, independent of Agent configuration."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, PrincipalRef, WorkspaceAction, authorize_workspace
from a13n_service.iam.authorization import PrincipalPermissions

from .accounts.models import AccountRecord
from .accounts.queries import require_account
from .domain import JsonObject
from .ingress.admission_domain import PreparedIngressBatch
from .providers.registry import require_native_provider

ActionName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class _NativeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    execution_principal_ref: PrincipalRef
    provider_key: str
    allowed_actions: tuple[ActionName, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def unique_actions(self):
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("Native actions must be unique")
        return self


class AccountRunContext(_NativeContext):
    kind: Literal["account"] = "account"
    target_scope: JsonObject = Field(repr=False)

    @model_validator(mode="after")
    def valid_scope(self):
        _validate_scope(self.provider_key, self.target_scope, self.allowed_actions)
        return self


class InboundRunContext(_NativeContext):
    kind: Literal["inbound"] = "inbound"
    binding_id: str
    target_id: str | None = None
    provider_context_version: str
    provider_context: JsonObject = Field(repr=False)
    action_policy: JsonObject

    @classmethod
    def from_batch(cls, batch: PreparedIngressBatch, *, execution_principal: PrincipalRef) -> "InboundRunContext":
        config = batch.configuration
        return cls(
            binding_id=batch.binding_id,
            account_id=config.account_id,
            target_id=config.target_id,
            execution_principal_ref=execution_principal,
            provider_key=config.provider_key,
            provider_context_version=config.provider_context_version,
            provider_context=config.provider_context,
            action_policy=config.provider_policy,
            allowed_actions=config.native_actions,
        )


type NativeToolContext = Annotated[AccountRunContext | InboundRunContext, Field(discriminator="kind")]
_CONTEXTS = TypeAdapter(Annotated[tuple[NativeToolContext, ...], Field(max_length=128)])


def parse_native_contexts(value: object) -> tuple[NativeToolContext, ...]:
    contexts = _CONTEXTS.validate_python(value)
    sources = [
        (item.kind, item.binding_id if isinstance(item, InboundRunContext) else item.account_id) for item in contexts
    ]
    if len(sources) != len(set(sources)):
        raise ValueError("duplicate_native_tool_source")
    return contexts


async def authorized_account(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    account_id: str,
    snapshot: PrincipalPermissions | None = None,
) -> AccountRecord:
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.application_account_use,
        snapshot=snapshot,
    )
    account = await require_account(session, account_id)
    if account.organization_id != organization_id or account.workspace_id != workspace_id or account.status != "active":
        raise ValueError("native_source_unavailable")
    return account


async def bind_account_tools(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    account_id: str,
    allowed_actions: tuple[str, ...],
    target_scope: JsonObject,
) -> AccountRunContext:
    """Bind entry-authorized actions/targets inside the Run acceptance transaction.

    The trusted entry owns target authorization. This is not a public Run override
    and must never be populated directly from model arguments or user input.
    """
    account = await authorized_account(
        session, actor=actor, organization_id=organization_id, workspace_id=workspace_id, account_id=account_id
    )
    return AccountRunContext(
        account_id=account.id,
        execution_principal_ref=actor.principal,
        provider_key=account.provider_key,
        allowed_actions=allowed_actions,
        target_scope=target_scope,
    )


def _validate_scope(provider: str, scope: JsonObject, tools: tuple[str, ...]) -> None:
    definition = require_native_provider(provider).account_tools
    if not set(tools) <= definition.tools:
        raise ValueError("unsupported_account_tools")
    definition.scope.model_validate(scope)
