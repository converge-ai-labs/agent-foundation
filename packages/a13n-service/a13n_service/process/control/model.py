"""Model control-plane construction."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2

from a13n_service.models.catalog import ModelsDevCatalog
from a13n_service.models.connection_test import NativeModelConnectionTester
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.service import ModelService
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


@dataclass(frozen=True, slots=True)
class _ModelBundle:
    models: ModelService
    providers: ModelProviderService


async def build_model_bundle(
    settings: Settings,
    components: Components,
    shared: SharedRuntime,
    execution: ExecutionResources,
    stack: AsyncExitStack,
) -> _ModelBundle:
    """Construct Model and Model Provider APIs."""

    live_provider_resolver = execution.live_model_providers
    connection_tester = components.model_connection_tester or NativeModelConnectionTester(
        provider_resolver=live_provider_resolver,
        model_factory=execution.native_model_factory,
    )
    provider_operations = NativeProviderOperations(
        provider_resolver=live_provider_resolver,
        registry=execution.model_provider_registry,
        http_client=execution.model_http_client,
        endpoint_policy=execution.model_endpoint_policy,
    )
    catalog = components.model_catalog
    if catalog is None:
        catalog_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(follow_redirects=False, trust_env=False, timeout=10.0)
        )
        catalog = ModelsDevCatalog(catalog_http_client, released_since=settings.models.catalog_released_since)
    return _ModelBundle(
        models=ModelService(
            shared.storage.sessions,
            execution.model_provider_registry,
            connection_tester=connection_tester,
            connection_test_timeout_seconds=settings.models.connection_test_timeout_seconds,
            catalog=catalog,
        ),
        providers=ModelProviderService(
            shared.storage.sessions,
            execution.model_provider_registry,
            execution.model_endpoint_policy,
            shared.secret_protector,
            resolve_dns_on_save=settings.models.resolve_dns_on_save,
            operations=provider_operations,
            command_timeout_seconds=settings.models.connection_test_timeout_seconds,
        ),
    )


__all__ = ["build_model_bundle"]
