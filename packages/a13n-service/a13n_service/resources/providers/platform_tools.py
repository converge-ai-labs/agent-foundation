"""Explicit provider discovery and readiness contracts; credentials stay in trusted configuration."""

from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.providers import discovery, service
from a13n_service.resources.providers.schemas import Provider, ProviderModel, ProviderPage, ProviderTest
from a13n_service.resources.providers.tables import (
    BrowserProviderRow,
    ConnectorProviderRow,
    EnvironmentProviderRow,
    ModelProviderRow,
    WebProviderRow,
)
from a13n_service.resources.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_model_providers(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> ProviderPage:
    """List configured model providers and their credential readiness, without secret values."""
    return await service.list_providers(
        ctx.runtime.storage, ctx.actor, ModelProviderRow, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_model_provider(ctx: ToolContext, *, provider_id: str) -> Provider:
    """Read model provider configuration and readiness; credentials are never returned."""
    return await service.get_provider(ctx.runtime.storage, ctx.actor, ModelProviderRow, ctx.workspace_id, provider_id)


async def test_model_provider(ctx: ToolContext, *, provider_id: str) -> ProviderTest:
    """Test model provider connectivity. This may make an external call and requires write approval."""
    runtime = ctx.runtime
    return await service.test_provider(
        runtime.storage,
        ctx.actor,
        ModelProviderRow,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def list_environment_providers(
    ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None
) -> ProviderPage:
    """List configured environment providers and their credential readiness, without secret values."""
    return await service.list_providers(
        ctx.runtime.storage, ctx.actor, EnvironmentProviderRow, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_environment_provider(ctx: ToolContext, *, provider_id: str) -> Provider:
    """Read environment provider configuration and readiness; credentials are never returned."""
    return await service.get_provider(
        ctx.runtime.storage, ctx.actor, EnvironmentProviderRow, ctx.workspace_id, provider_id
    )


async def test_environment_provider(ctx: ToolContext, *, provider_id: str) -> ProviderTest:
    """Test environment provider connectivity. This may make an external call and requires write approval."""
    runtime = ctx.runtime
    return await service.test_provider(
        runtime.storage,
        ctx.actor,
        EnvironmentProviderRow,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def list_connector_providers(
    ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None
) -> ProviderPage:
    """List configured connector providers and their credential readiness, without secret values."""
    return await service.list_providers(
        ctx.runtime.storage, ctx.actor, ConnectorProviderRow, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_connector_provider(ctx: ToolContext, *, provider_id: str) -> Provider:
    """Read connector provider configuration and readiness; credentials are never returned."""
    return await service.get_provider(
        ctx.runtime.storage, ctx.actor, ConnectorProviderRow, ctx.workspace_id, provider_id
    )


async def test_connector_provider(ctx: ToolContext, *, provider_id: str) -> ProviderTest:
    """Test connector provider connectivity. This may make an external call and requires write approval."""
    runtime = ctx.runtime
    return await service.test_provider(
        runtime.storage,
        ctx.actor,
        ConnectorProviderRow,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def list_web_providers(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> ProviderPage:
    """List configured web providers and their credential readiness, without secret values."""
    return await service.list_providers(
        ctx.runtime.storage, ctx.actor, WebProviderRow, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_web_provider(ctx: ToolContext, *, provider_id: str) -> Provider:
    """Read web provider configuration and readiness; credentials are never returned."""
    return await service.get_provider(ctx.runtime.storage, ctx.actor, WebProviderRow, ctx.workspace_id, provider_id)


async def test_web_provider(ctx: ToolContext, *, provider_id: str) -> ProviderTest:
    """Test web provider connectivity. This may make an external call and requires write approval."""
    runtime = ctx.runtime
    return await service.test_provider(
        runtime.storage,
        ctx.actor,
        WebProviderRow,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def list_browser_providers(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> ProviderPage:
    """List configured browser providers and their credential readiness, without secret values."""
    return await service.list_providers(
        ctx.runtime.storage, ctx.actor, BrowserProviderRow, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_browser_provider(ctx: ToolContext, *, provider_id: str) -> Provider:
    """Read browser provider configuration and readiness; credentials are never returned."""
    return await service.get_provider(ctx.runtime.storage, ctx.actor, BrowserProviderRow, ctx.workspace_id, provider_id)


async def test_browser_provider(ctx: ToolContext, *, provider_id: str) -> ProviderTest:
    """Test browser provider connectivity. This may make an external call and requires write approval."""
    runtime = ctx.runtime
    return await service.test_provider(
        runtime.storage,
        ctx.actor,
        BrowserProviderRow,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def discover_model_provider_models(ctx: ToolContext, *, provider_id: str) -> list[ProviderModel]:
    """Discover upstream model identifiers through an authorized configured provider."""
    runtime = ctx.runtime
    return await discovery.discover_models(
        runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        provider_id,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
        catalog=ctx.request.app.state.model_catalog,
    )


TOOLS = (
    definition(list_model_providers, group="providers", effect="read"),
    definition(get_model_provider, group="providers", effect="read"),
    definition(test_model_provider, group="providers", effect="write"),
    definition(list_environment_providers, group="providers", effect="read"),
    definition(get_environment_provider, group="providers", effect="read"),
    definition(test_environment_provider, group="providers", effect="write"),
    definition(list_connector_providers, group="providers", effect="read"),
    definition(get_connector_provider, group="providers", effect="read"),
    definition(test_connector_provider, group="providers", effect="write"),
    definition(list_web_providers, group="providers", effect="read"),
    definition(get_web_provider, group="providers", effect="read"),
    definition(test_web_provider, group="providers", effect="write"),
    definition(list_browser_providers, group="providers", effect="read"),
    definition(get_browser_provider, group="providers", effect="read"),
    definition(test_browser_provider, group="providers", effect="write"),
    definition(discover_model_provider_models, group="providers", effect="read"),
)
