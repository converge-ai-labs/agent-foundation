"""Protected current-Ingress binding for local MCP native actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx2
from a13n_harness import AgentContext
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.capabilities import MCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import WorkspaceAction, authorize_workspace
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session

from .connectors.management import decode_credentials
from .domain import JsonObject
from .ingress.admission_domain import PreparedIngressBatch
from .ingress.models import IngressRecord, RouteRecord
from .native_actions import native_actions
from .providers.github.wire import CONTEXT_VERSION as GITHUB_CONTEXT_VERSION
from .providers.lark.wire import CONTEXT_VERSION as LARK_CONTEXT_VERSION
from .providers.slack.adapter import CONTEXT_VERSION as SLACK_CONTEXT_VERSION
from .toolsets import local_capability, source_key

if TYPE_CHECKING:
    from .execution import AttemptToolScope


class IngressRunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ingress_id: str
    account_id: str
    route_id: str | None = None
    execution_principal_ref: PrincipalRef
    provider_key: str
    provider_context_version: str
    provider_context: JsonObject = Field(repr=False)
    action_policy: JsonObject
    allowed_actions: tuple[str, ...] = Field(max_length=128)

    @classmethod
    def from_batch(cls, batch: PreparedIngressBatch) -> IngressRunContext:
        """Retain only native authority from a trusted admission batch."""
        return cls(
            ingress_id=batch.ingress_id,
            account_id=batch.account_id,
            route_id=batch.route_id,
            execution_principal_ref=PrincipalRef(
                principal_type=PrincipalType.service_account, principal_id=batch.execution_service_account_id
            ),
            provider_key=batch.provider_key,
            provider_context_version=batch.provider_context_version,
            provider_context=batch.provider_context,
            action_policy=batch.provider_policy,
            allowed_actions=batch.native_actions,
        )


async def native_capability(
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    scope: AttemptToolScope,
    guard: Callable[[], Awaitable[None]],
    endpoints: EndpointPolicy,
) -> MCP[AgentContext] | None:
    context = IngressRunContext.model_validate(scope.ingress_context)
    if not context.allowed_actions:
        return None
    supported_version = {
        "slack": SLACK_CONTEXT_VERSION,
        "lark": LARK_CONTEXT_VERSION,
        "github": GITHUB_CONTEXT_VERSION,
    }.get(context.provider_key)
    if (
        context.provider_context_version != supported_version
        or context.execution_principal_ref != scope.actor.principal
    ):
        raise ValueError("native_context_incompatible")

    async def source():
        await guard()
        async with short_session(sessions) as session:
            ingress = await session.get(IngressRecord, context.ingress_id)
            if (
                ingress is None
                or ingress.account_id != context.account_id
                or ingress.account.status != "active"
                or ingress.account.deleted_at is not None
                or ingress.organization_id != scope.organization_id
                or ingress.workspace_id != scope.workspace_id
                or ingress.account.provider_key != context.provider_key
                or ingress.execution_service_account_id != scope.actor.principal.principal_id
            ):
                raise ValueError("native_source_unavailable")
            if context.route_id is not None:
                route = await session.get(RouteRecord, context.route_id)
                if route is None or route.ingress_id != ingress.id or not route.enabled:
                    raise ValueError("native_route_unavailable")
            await authorize_workspace(
                session,
                actor=scope.actor,
                workspace_id=scope.workspace_id,
                action=WorkspaceAction.application_account_use,
            )
            configuration = dict(ingress.account.provider_config_json)
            credential_context = ingress.account.credential_snapshot()
        return configuration, decode_credentials(credential_context.decrypt(protector))

    configuration, credentials = await source()
    async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as http:
        actions = native_actions(
            context.provider_key,
            context.provider_context,
            context.action_policy,
            configuration,
            credentials,
            http,
            endpoints,
        )
        definitions = tuple(item.definition for item in actions.values())

    async def call(name: str, arguments: JsonObject) -> JsonValue:
        if name not in context.allowed_actions:
            raise ValueError("native_action_not_authorized")
        configuration, credentials = await source()
        async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as http:
            actions = native_actions(
                context.provider_key,
                context.provider_context,
                context.action_policy,
                configuration,
                credentials,
                http,
                endpoints,
            )
            selected = actions.get(name)
            if selected is None:
                raise ValueError("native_action_unavailable")
            await guard()
            return await selected.call(arguments)

    return await local_capability(
        key=source_key("ingress", context.ingress_id), tools=definitions, allowed=context.allowed_actions, handler=call
    )
