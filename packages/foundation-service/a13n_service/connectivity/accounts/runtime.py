"""Run-scoped account operations with fresh resource credentials."""

from collections.abc import Awaitable, Callable

import httpx2
from a13n_harness import AgentContext
from pydantic import JsonValue
from pydantic_ai.capabilities import MCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AccountToolSelection
from a13n_service.connectivity.connectors.management import decode_credentials
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.toolsets import local_capability, source_key
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session

from .actions import account_actions
from .queries import require_account


async def account_capability(
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    selection: AccountToolSelection,
    guard: Callable[[], Awaitable[None]],
    endpoints: EndpointPolicy,
) -> MCP[AgentContext] | None:
    async def source():
        await guard()
        async with short_session(sessions) as session:
            account = await require_account(session, selection.account_id)
            if account.status != "active":
                raise ValueError("account_unavailable")
            provider = account.provider_key
            configuration = dict(account.provider_config_json)
            credential = account.credential_snapshot()
        return provider, configuration, decode_credentials(credential.decrypt(protector))

    provider, configuration, credentials = await source()
    async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as http:
        actions = account_actions(provider, configuration, credentials, selection.target_scope, http, endpoints)
        definitions = tuple(item.definition for item in actions.values())

    async def call(name: str, arguments: JsonObject) -> JsonValue:
        if name not in selection.tools:
            raise ValueError("account_action_not_authorized")
        provider, configuration, credentials = await source()
        async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as http:
            actions = account_actions(provider, configuration, credentials, selection.target_scope, http, endpoints)
            selected = actions.get(name)
            if selected is None:
                raise ValueError("account_action_unavailable")
            await guard()
            return await selected.call(arguments)

    return await local_capability(
        key=source_key("account", selection.account_id),
        tools=definitions,
        allowed=selection.tools,
        handler=call,
        defer_loading=selection.defer_loading,
    )
