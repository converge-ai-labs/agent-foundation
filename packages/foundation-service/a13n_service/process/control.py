"""Control-plane process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2
from a13n_environment_provider import build_environment_provider_catalog

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.agents.service import AgentService
from a13n_service.assets.cleanup import AssetCleanupReconciler
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.service import AssetService
from a13n_service.assets.staging import AssetStaging
from a13n_service.connectivity.runtime import ConnectivityRuntime
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.models.connection_test import NativeModelConnectionTester
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelService
from a13n_service.plugins.commands import PluginRuntimeCandidateResolver
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime_commands import PluginRuntimeCommandCoordinator
from a13n_service.plugins.runtime_resolver import (
    FoundationPluginRuntimeCandidateResolver,
    HttpRuntimeDependencyArtifactRetainer,
    UvRuntimeDependencyResolver,
)
from a13n_service.plugins.service import PluginService
from a13n_service.plugins.staging import PluginStaging
from a13n_service.process.background import BackgroundTask
from a13n_service.process.components import ServiceComponents
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import ControlRuntime, SharedRuntime, WorkerRuntime
from a13n_service.settings import ServiceSettings
from a13n_service.skills.catalog import SkillCatalogService
from a13n_service.skills.credentials import DatabaseGitHubCredentialResolver
from a13n_service.skills.github import GitHubSkillAcquirer
from a13n_service.skills.publication import SkillPublicationService
from a13n_service.skills.sources import SkillSourcePreparer
from a13n_service.skills.uploads import SkillUploadService
from a13n_service.trace_query.langfuse import LangfuseTraceQueryProvider
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.service import TraceQueryService


@dataclass(frozen=True, slots=True)
class _PluginControl:
    service: PluginService
    candidate_resolver: PluginRuntimeCandidateResolver | None
    command_coordinator: PluginRuntimeCommandCoordinator | None


async def build_control_runtime(
    settings: ServiceSettings,
    components: ServiceComponents,
    shared: SharedRuntime,
    execution: ExecutionResources,
    worker: WorkerRuntime | None,
    connectivity: ConnectivityRuntime | None,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    stack: AsyncExitStack,
) -> tuple[ControlRuntime, tuple[BackgroundTask, ...]]:
    """Construct the services owned by a Control-capable role."""

    trace_queries = await _build_trace_queries(settings, components, trace_query_provider_registry, stack)
    environment_provider_catalog = _environment_provider_catalog(settings, components)
    environments = EnvironmentManagementService(
        shared.storage.sessions,
        environment_provider_catalog,
        attachment_tester=components.environment_attachment_tester,
    )
    agent_plugin_selection = components.agent_plugin_selection_resolver or AgentPluginSelectionResolver(
        shared.storage.sessions,
        runtime_mode=settings.plugin_runtime_mode,
        worker_release=settings.build_version,
    )
    plugin_staging = await PluginStaging.create(
        shared.storage.files_root,
        limiter=shared.storage.file_limiter,
    )
    plugin_control = await _build_plugin_control(
        settings,
        components,
        shared,
        execution,
        agent_plugin_selection,
        plugin_staging,
        worker.plugin_runner if worker is not None else None,
        stack,
    )
    await plugin_control.service.ensure_runtime_mode()
    for artifact in components.builtin_plugin_artifacts:
        await plugin_control.service.register_builtin(
            registration=artifact.registration,
            body=artifact.body_factory(),
            content_length=artifact.content_length,
        )

    github_acquirer = components.skill_github_acquirer
    if github_acquirer is None:
        github_http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False))
        github_acquirer = GitHubSkillAcquirer(github_http_client)
    credential_resolver = components.skill_credential_resolver
    if credential_resolver is None:
        credential_resolver = DatabaseGitHubCredentialResolver(
            shared.storage.sessions,
            shared.secret_protector,
        )
    skill_uploads = SkillUploadService(shared.storage.sessions, execution.skill_package_store)
    source_preparer = SkillSourcePreparer(
        shared.storage.sessions,
        execution.skill_package_store,
        github_acquirer,
        credential_resolver,
    )
    skill_publication = SkillPublicationService(shared.storage.sessions, source_preparer)
    skill_catalog = SkillCatalogService(shared.storage.sessions, execution.skill_package_store)

    accepted_models = AcceptedModelSelector(shared.storage.sessions, execution.model_provider_registry)
    agent_environment_selection = AgentEnvironmentSelectionResolver(
        shared.storage.sessions,
        environment_provider_catalog,
    )
    connectivity_selection = connectivity.control.selection_resolver if connectivity and connectivity.control else None
    agent_resolver = components.agent_resolver or AgentResolver(
        shared.storage.sessions,
        accepted_models,
        plugin_runtime_mode=settings.plugin_runtime_mode,
        environment_resolver=agent_environment_selection,
        plugin_resolver=agent_plugin_selection,
        connectivity_resolver=connectivity_selection,
    )
    agent_invocation_resolver = components.agent_invocation_resolver or AgentInvocationResolver(
        shared.storage.sessions,
        accepted_models,
        plugin_runtime_mode=settings.plugin_runtime_mode,
        environment_resolver=agent_environment_selection,
        plugin_resolver=agent_plugin_selection,
        connectivity_resolver=connectivity_selection,
    )
    agents = AgentService(shared.storage.sessions, agent_resolver, agent_invocation_resolver)
    live_model_provider_resolver = LiveProviderResolver(
        shared.storage.sessions,
        execution.model_provider_registry,
        execution.model_endpoint_policy,
        shared.secret_protector,
    )
    model_connection_tester = components.model_connection_tester or NativeModelConnectionTester(
        provider_resolver=live_model_provider_resolver,
        model_factory=execution.native_model_factory,
    )
    model_provider_operations = NativeProviderOperations(
        provider_resolver=live_model_provider_resolver,
        registry=execution.model_provider_registry,
        http_client=execution.model_http_client,
    )
    models = ModelService(
        shared.storage.sessions,
        execution.model_provider_registry,
        connection_tester=model_connection_tester,
        connection_test_timeout_seconds=settings.model_connection_test_timeout_seconds,
    )
    model_providers = ModelProviderService(
        shared.storage.sessions,
        execution.model_provider_registry,
        execution.model_endpoint_policy,
        shared.secret_protector,
        resolve_dns_on_save=settings.model_resolve_dns_on_save,
        operations=model_provider_operations,
        command_timeout_seconds=settings.model_connection_test_timeout_seconds,
    )
    asset_staging = await AssetStaging.create(shared.storage.files_root, limiter=shared.storage.file_limiter)
    asset_objects = AssetObjectStore(shared.storage.objects, asset_staging)
    assets = AssetService(
        shared.storage.sessions,
        asset_objects,
        asset_staging,
        max_size_bytes=settings.asset_max_size_bytes,
    )
    asset_cleanup = AssetCleanupReconciler(
        shared.storage.sessions,
        asset_objects,
        poll_interval_seconds=settings.asset_cleanup_poll_interval_seconds,
        lease_seconds=settings.asset_cleanup_lease_seconds,
        max_attempts=settings.asset_cleanup_max_attempts,
    )
    runtime = ControlRuntime(
        trace_queries=trace_queries,
        environments=environments,
        environment_provider_catalog=environment_provider_catalog,
        agent_plugin_selection=agent_plugin_selection,
        plugin_runtime_candidate_resolver=plugin_control.candidate_resolver,
        plugins=plugin_control.service,
        skill_uploads=skill_uploads,
        skill_publication=skill_publication,
        skill_catalog=skill_catalog,
        accepted_models=accepted_models,
        agent_environment_selection=agent_environment_selection,
        agent_resolver=agent_resolver,
        agent_invocation_resolver=agent_invocation_resolver,
        agents=agents,
        models=models,
        model_providers=model_providers,
        assets=assets,
    )
    background_tasks = [BackgroundTask("asset cleanup reconciler", asset_cleanup.run)]
    if plugin_control.command_coordinator is not None:
        background_tasks.append(
            BackgroundTask("plugin runtime command coordinator", plugin_control.command_coordinator.run)
        )
    return runtime, tuple(background_tasks)


async def _build_trace_queries(
    settings: ServiceSettings,
    components: ServiceComponents,
    registry: TraceQueryProviderRegistry,
    stack: AsyncExitStack,
) -> TraceQueryService:
    provider_registry = registry.copy()
    if settings.observability_query_provider == "langfuse":
        http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False, timeout=10.0))
        public_key = settings.observability_query_langfuse_public_key
        secret_key = settings.observability_query_langfuse_secret_key
        base_url = settings.observability_query_langfuse_base_url
        if public_key is None or secret_key is None or base_url is None:
            raise RuntimeError("validated Langfuse Trace Query configuration is incomplete")
        provider_registry.register(
            "langfuse",
            lambda: LangfuseTraceQueryProvider(
                http_client,
                base_url=base_url,
                public_key=public_key.get_secret_value(),
                secret_key=secret_key.get_secret_value(),
            ),
        )
    provider = (
        None
        if settings.observability_query_provider == "none"
        else provider_registry.create(settings.observability_query_provider)
    )
    return TraceQueryService(
        provider_key=settings.observability_query_provider,
        provider=provider,
        authorizer=components.trace_access_authorizer,
    )


def _environment_provider_catalog(
    settings: ServiceSettings,
    components: ServiceComponents,
) -> FoundationEnvironmentProviderCatalog:
    if components.environment_provider_catalog is not None:
        return components.environment_provider_catalog
    selected = build_environment_provider_catalog(
        builtin_keys=settings.environment_provider_builtins,
        extension_keys=settings.environment_provider_extensions,
    )
    return FoundationEnvironmentProviderCatalog.from_environment_provider_catalog(selected)


async def _build_plugin_control(
    settings: ServiceSettings,
    components: ServiceComponents,
    shared: SharedRuntime,
    execution: ExecutionResources,
    agent_plugin_selection: AgentPluginSelectionResolver,
    plugin_staging: PluginStaging,
    local_runner: PluginRunnerSupervisor | None,
    stack: AsyncExitStack,
) -> _PluginControl:
    runtime_dispatcher = components.plugin_runtime_command_dispatcher
    candidate_resolver = components.plugin_runtime_candidate_resolver
    staging_authority = components.plugin_runtime_staging_authority or local_runner
    if (candidate_resolver is not None or staging_authority is not None) and (
        settings.plugin_runtime_mode is not PluginRuntimeMode.runner
    ):
        raise RuntimeError("Plugin Runtime coordination is configured outside runner mode")
    if candidate_resolver is not None and staging_authority is None:
        raise RuntimeError("Plugin Runtime coordination requires both a candidate resolver and staging authority")
    if candidate_resolver is None and staging_authority is not None:
        default_index_url = settings.plugin_runtime_default_index_url.get_secret_value()
        index_urls = tuple(value.get_secret_value() for value in settings.plugin_runtime_index_urls)
        dependency_resolver = await UvRuntimeDependencyResolver.create(
            shared.storage.files_root,
            executable=settings.plugin_runtime_resolver_executable,
            default_index_url=default_index_url,
            index_urls=index_urls,
            timeout_seconds=settings.plugin_runtime_resolver_timeout_seconds,
            max_packages=settings.plugin_runtime_resolver_max_packages,
            limiter=shared.storage.file_limiter,
        )
        dependency_http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False))
        artifact_retainer = HttpRuntimeDependencyArtifactRetainer(
            dependency_http_client,
            plugin_staging,
            execution.plugin_objects,
            max_wheel_bytes=settings.plugin_max_wheel_bytes,
            max_expanded_bytes=settings.plugin_max_expanded_bytes,
            max_archive_members=settings.plugin_max_archive_members,
            index_urls=(default_index_url, *index_urls),
            limiter=shared.storage.file_limiter,
        )
        candidate_resolver = FoundationPluginRuntimeCandidateResolver(
            shared.storage.sessions,
            agent_plugin_selection.runtime_locks,
            dependency_resolver,
            artifact_retainer,
        )
    command_coordinator: PluginRuntimeCommandCoordinator | None = None
    if runtime_dispatcher is None and candidate_resolver is not None and staging_authority is not None:
        command_coordinator = PluginRuntimeCommandCoordinator(
            shared.storage.sessions,
            candidate_resolver,
            staging_authority,
            poll_interval_seconds=settings.plugin_runtime_command_poll_interval_seconds,
            lease_seconds=settings.plugin_runtime_command_lease_seconds,
        )
        runtime_dispatcher = command_coordinator
    service = PluginService(
        shared.storage.sessions,
        execution.plugin_objects,
        plugin_staging,
        runtime_mode=settings.plugin_runtime_mode,
        max_wheel_bytes=settings.plugin_max_wheel_bytes,
        max_expanded_bytes=settings.plugin_max_expanded_bytes,
        max_archive_members=settings.plugin_max_archive_members,
        runtime_command_dispatcher=runtime_dispatcher,
    )
    return _PluginControl(
        service=service,
        candidate_resolver=candidate_resolver,
        command_coordinator=command_coordinator,
    )


__all__ = ["build_control_runtime"]
