"""Default native MCP tools from protected Run contexts, with fresh authority and credentials."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx2
from a13n_harness import AgentContext
from pydantic import JsonValue
from pydantic_ai.capabilities import MCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session

from .accounts.providers import account_actions
from .connectors.management import decode_credentials
from .domain import JsonObject
from .native_actions import NativeAction, native_actions
from .native_context import AccountRunContext, InboundRunContext, NativeToolContext, authorized_account
from .providers.github.wire import CONTEXT_VERSION as GITHUB_CONTEXT_VERSION
from .providers.lark.wire import CONTEXT_VERSION as LARK_CONTEXT_VERSION
from .providers.slack.adapter import CONTEXT_VERSION as SLACK_CONTEXT_VERSION
from .toolsets import local_capability, source_key

if TYPE_CHECKING:
    from .execution import AttemptToolScope


async def native_capability(
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    scope: AttemptToolScope,
    context: NativeToolContext,
    guard: Callable[[], Awaitable[None]],
    endpoints: EndpointPolicy,
    http: httpx2.AsyncClient,
) -> MCP[AgentContext] | None:
    if not context.allowed_actions:
        return None
    if context.execution_principal_ref != scope.actor.principal:
        raise ValueError("native_context_incompatible")

    async def source():
        await guard()
        async with short_session(sessions) as session:
            account = await authorized_account(
                session,
                actor=scope.actor,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                account_id=context.account_id,
            )
            if account.provider_key != context.provider_key:
                raise ValueError("native_source_unavailable")
            if isinstance(context, InboundRunContext):
                _validate_context(context)
            configuration = dict(account.provider_config_json)
            credential = account.credential_snapshot()
            generation = account.credential_generation
        return configuration, decode_credentials(credential.decrypt(protector)), generation

    configuration, credentials, generation = await source()
    actions = _actions(context, configuration, credentials, http, endpoints)
    definitions = tuple(item.definition for item in actions.values())

    async def call(name: str, arguments: JsonObject) -> JsonValue:
        nonlocal actions, generation, configuration
        if name not in context.allowed_actions:
            raise ValueError("native_action_not_authorized")
        current_configuration, credentials, current_generation = await source()
        if current_generation != generation or current_configuration != configuration:
            configuration = current_configuration
            actions = _actions(context, configuration, credentials, http, endpoints)
            generation = current_generation
        selected = actions.get(name)
        if selected is None:
            raise ValueError("native_action_unavailable")
        await guard()
        return await selected.call(arguments)

    identifier = context.binding_id if isinstance(context, InboundRunContext) else context.account_id
    return await local_capability(
        key=source_key(context.kind, identifier), tools=definitions, allowed=context.allowed_actions, handler=call
    )


def _actions(
    context: NativeToolContext,
    configuration: JsonObject,
    credentials: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    if isinstance(context, AccountRunContext):
        return account_actions(context.provider_key, configuration, credentials, context.target_scope, http, endpoints)
    return native_actions(
        context.provider_key,
        context.provider_context,
        context.action_policy,
        configuration,
        credentials,
        http,
        endpoints,
    )


def _validate_context(context: InboundRunContext) -> None:
    supported_version = {
        "slack": SLACK_CONTEXT_VERSION,
        "lark": LARK_CONTEXT_VERSION,
        "github": GITHUB_CONTEXT_VERSION,
    }.get(context.provider_key)
    if context.provider_context_version != supported_version:
        raise ValueError("native_context_incompatible")
