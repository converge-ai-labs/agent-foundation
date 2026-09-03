"""Model control-plane construction."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.models.connection_test import NativeModelConnectionTester
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelService
from a13n_service.process.components import ServiceComponents
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import ServiceSettings


@dataclass(frozen=True, slots=True)
class _ModelBundle:
    accepted: AcceptedModelSelector
    models: ModelService
    providers: ModelProviderService


def build_model_bundle(
    settings: ServiceSettings,
    components: ServiceComponents,
    shared: SharedRuntime,
    execution: ExecutionResources,
) -> _ModelBundle:
    """Construct Model and Model Provider APIs."""

    live_provider_resolver = LiveProviderResolver(
        shared.storage.sessions,
        execution.model_provider_registry,
        execution.model_endpoint_policy,
        shared.secret_protector,
    )
    connection_tester = components.model_connection_tester or NativeModelConnectionTester(
        provider_resolver=live_provider_resolver,
        model_factory=execution.native_model_factory,
    )
    provider_operations = NativeProviderOperations(
        provider_resolver=live_provider_resolver,
        registry=execution.model_provider_registry,
        http_client=execution.model_http_client,
    )
    return _ModelBundle(
        accepted=AcceptedModelSelector(shared.storage.sessions, execution.model_provider_registry),
        models=ModelService(
            shared.storage.sessions,
            execution.model_provider_registry,
            connection_tester=connection_tester,
            connection_test_timeout_seconds=settings.model_connection_test_timeout_seconds,
        ),
        providers=ModelProviderService(
            shared.storage.sessions,
            execution.model_provider_registry,
            execution.model_endpoint_policy,
            shared.secret_protector,
            resolve_dns_on_save=settings.model_resolve_dns_on_save,
            operations=provider_operations,
            command_timeout_seconds=settings.model_connection_test_timeout_seconds,
        ),
    )


__all__ = ["build_model_bundle"]
