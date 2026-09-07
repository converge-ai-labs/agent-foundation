"""Resources shared only by Control and Worker process capabilities."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2
from anyio import to_thread

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.models.model_apis import BUILT_IN_MODEL_APIS
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.settings import settings_schema
from a13n_service.plugins.objects import PluginObjectStore
from a13n_service.process.runtime import SharedRuntime
from a13n_service.skills.objects import SkillPackageStore


@dataclass(frozen=True, slots=True)
class ExecutionResources:
    """Model, Skill, and Plugin resources shared by Control and Worker roles."""

    model_provider_registry: ProviderRegistry
    model_endpoint_policy: EndpointPolicy
    model_http_client: httpx2.AsyncClient
    native_model_factory: NativeModelFactory
    skill_package_store: SkillPackageStore
    plugin_objects: PluginObjectStore


async def build_execution_resources(
    shared: SharedRuntime,
    model_provider_registry: ProviderRegistry,
    model_endpoint_policy: EndpointPolicy,
    stack: AsyncExitStack,
) -> ExecutionResources:
    """Open the resources used by Control and Worker capabilities."""
    # Native schema generation reads installed source docs; warm its cache off the event loop.
    for model_api in BUILT_IN_MODEL_APIS:
        await to_thread.run_sync(settings_schema, model_api)

    async def validate_model_request(request: httpx2.Request) -> None:
        await model_endpoint_policy.validate(str(request.url), resolve_dns=True)

    model_http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            follow_redirects=False,
            event_hooks={"request": [validate_model_request]},
        )
    )
    native_model_factory = NativeModelFactory(model_http_client, model_provider_registry, model_endpoint_policy)
    return ExecutionResources(
        model_provider_registry=model_provider_registry,
        model_endpoint_policy=model_endpoint_policy,
        model_http_client=model_http_client,
        native_model_factory=native_model_factory,
        skill_package_store=SkillPackageStore(shared.storage.objects),
        plugin_objects=PluginObjectStore(shared.storage.objects),
    )


__all__ = ["ExecutionResources", "build_execution_resources"]
