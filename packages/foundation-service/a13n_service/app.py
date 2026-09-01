"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import httpx2
from a13n_environment_provider import EnvironmentProviderCatalog, build_environment_provider_catalog
from anyio import create_task_group, fail_after
from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text
from starlette.types import Receive, Scope, Send

from a13n_service.agent_presets.connector_resolution import AgentConnectorSelectionResolver
from a13n_service.agent_presets.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.agent_presets.references import AgentPresetConnectorReferenceChecker
from a13n_service.agent_presets.resolution import AgentPresetResolver
from a13n_service.agent_presets.router import router as agent_preset_router
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.api import install_api_conventions
from a13n_service.assets.cleanup import AssetCleanupReconciler
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.router import router as asset_router
from a13n_service.assets.service import AssetService
from a13n_service.assets.staging import AssetStaging
from a13n_service.connectors import (
    AttemptConnectorMCPAuthenticator,
    ConnectionService,
    ConnectorAttemptAuthorizer,
    ConnectorInvocationAuthorizer,
    ConnectorMCPGateway,
    ConnectorProviderCatalog,
    ConnectorProviderOperations,
    ConnectorProviderRuntime,
    ConnectorService,
    DatabaseConnectorSecretStore,
    LocalConnectorProviderOperations,
    PrincipalRef,
    RemoteConnectorProviderOperations,
    StandardConnectorMCPAuthenticator,
    build_connector_provider_catalog,
    create_connector_provider_operations_app,
)
from a13n_service.connectors.router import router as connector_router
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.router import router as environment_router
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.iam import RequestAuthenticator
from a13n_service.model_configs.connection_test import NativeModelConnectionTester
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.providers import built_in_provider_registry
from a13n_service.model_configs.router import router as model_config_router
from a13n_service.model_configs.runtime import (
    AcceptedModelSelector,
    NativeModelFactory,
    RuntimeSecretValueResolver,
)
from a13n_service.model_configs.secrets import DatabaseSecretValueResolver
from a13n_service.model_configs.service import (
    CandidateConnectionTester,
    ModelConfigService,
)
from a13n_service.observability import build_observability_runtime
from a13n_service.plugins.objects import PluginObjectStore
from a13n_service.plugins.router import router as plugin_router
from a13n_service.plugins.service import PluginService
from a13n_service.plugins.staging import PluginStaging
from a13n_service.settings import ServiceRole, ServiceSettings, get_settings
from a13n_service.skills.catalog import SkillCatalogService
from a13n_service.skills.credentials import DatabaseGitHubCredentialResolver
from a13n_service.skills.github import GitHubSkillAcquirer
from a13n_service.skills.objects import SkillPackageStore
from a13n_service.skills.publication import SkillPublicationService
from a13n_service.skills.router import router as skill_router
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.skills.selection import SkillSelectionResolver
from a13n_service.skills.sources import GitHubCredentialResolver, SkillSourcePreparer
from a13n_service.skills.uploads import SkillUploadService
from a13n_service.storage import StorageResources, open_storage, short_session
from a13n_service.trace_query import (
    LangfuseTraceQueryProvider,
    TraceAccessAuthorizer,
    TraceQueryProviderRegistry,
    TraceQueryService,
)
from a13n_service.trace_query.router import router as trace_query_router
from a13n_service.web import mount_web_application

logger = logging.getLogger("a13n_service.app")

_API_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
_CONTROL_PLANE_ROLES = {ServiceRole.all, ServiceRole.control}
_WORKER_ROLES = {ServiceRole.all, ServiceRole.worker}
_CONNECTOR_ROLES = {ServiceRole.all, ServiceRole.connector}


@dataclass(frozen=True, slots=True)
class ServiceComponents:
    request_authenticator: RequestAuthenticator | None = None
    agent_preset_resolver: AgentPresetResolver | None = None
    agent_preset_invocation_resolver: AgentPresetInvocationResolver | None = None
    agent_plugin_selection_resolver: AgentPluginSelectionResolver | None = None
    agent_connector_selection_resolver: AgentConnectorSelectionResolver | None = None
    model_connection_tester: CandidateConnectionTester | None = None
    model_secret_resolver: RuntimeSecretValueResolver | None = None
    connector_provider_catalog: ConnectorProviderCatalog | None = None
    connector_provider_operations: ConnectorProviderOperations | None = None
    environment_provider_catalog: EnvironmentProviderCatalog | None = None
    connector_invocation_authorizer: ConnectorInvocationAuthorizer | None = None
    connector_attempt_authorizer: ConnectorAttemptAuthorizer | None = None
    skill_github_acquirer: GitHubSkillAcquirer | None = None
    skill_credential_resolver: GitHubCredentialResolver | None = None
    trace_access_authorizer: TraceAccessAuthorizer | None = None
    trace_query_provider_registry: TraceQueryProviderRegistry | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: ServiceSettings = app.state.settings

    async def validate_model_request(request: httpx2.Request) -> None:
        await app.state.model_endpoint_policy.validate(str(request.url), resolve_dns=True)

    observability = build_observability_runtime(
        enabled=settings.observability_tracing,
        trace_content=settings.observability_trace_content,
        service_name=settings.service_name,
        service_version=settings.build_version,
        deployment_environment=settings.deployment_environment_name,
        service_role=settings.role.value,
        service_instance_id=settings.service_instance_id,
    )
    app.state.observability_runtime = observability
    try:
        async with open_storage(settings.storage_settings()) as storage, AsyncExitStack() as stack:
            app.state.storage = storage
            # Keep these names for service code that only needs relational access.
            app.state.db_engine = storage.engine
            app.state.db_session_factory = storage.sessions
            if settings.role in _CONTROL_PLANE_ROLES:
                provider_registry = app.state.trace_query_provider_registry.copy()
                if settings.observability_query_provider == "langfuse":
                    query_http_client = await stack.enter_async_context(
                        httpx2.AsyncClient(follow_redirects=False, timeout=10.0)
                    )
                    public_key = settings.observability_query_langfuse_public_key
                    secret_key = settings.observability_query_langfuse_secret_key
                    base_url = settings.observability_query_langfuse_base_url
                    if public_key is None or secret_key is None or base_url is None:
                        raise RuntimeError("validated Langfuse Trace Query configuration is incomplete")
                    provider_registry.register(
                        "langfuse",
                        lambda: LangfuseTraceQueryProvider(
                            query_http_client,
                            base_url=base_url,
                            public_key=public_key.get_secret_value(),
                            secret_key=secret_key.get_secret_value(),
                        ),
                    )
                trace_query_provider = (
                    None
                    if settings.observability_query_provider == "none"
                    else provider_registry.create(settings.observability_query_provider)
                )
                app.state.trace_query_service = TraceQueryService(
                    provider_key=settings.observability_query_provider,
                    provider=trace_query_provider,
                    authorizer=app.state.components.trace_access_authorizer,
                )
            secret_protector = settings.secret_protector()
            app.state.connector_secret_store = DatabaseConnectorSecretStore(storage.sessions, secret_protector)
            component_catalog = app.state.components.connector_provider_catalog
            if settings.role in _CONNECTOR_ROLES:
                app.state.connector_providers = (
                    component_catalog
                    if component_catalog is not None
                    else build_connector_provider_catalog(settings.connector_providers)
                )
                invocation_authorizer = app.state.components.connector_invocation_authorizer
                if invocation_authorizer is None:
                    invocation_authorizer = _BoundaryAuthorizedConnectorInvocationAuthorizer()
                app.state.connector_provider_runtime = ConnectorProviderRuntime(
                    storage.sessions,
                    app.state.connector_providers,
                    app.state.connector_secret_store,
                    invocation_authorizer,
                )
                standard_authenticator = None
                if app.state.request_authenticator is not None:
                    standard_authenticator = StandardConnectorMCPAuthenticator(
                        storage.sessions,
                        app.state.request_authenticator,
                        app.state.connector_provider_runtime,
                    )
                attempt_authenticator = None
                if app.state.components.connector_attempt_authorizer is not None:
                    attempt_authenticator = AttemptConnectorMCPAuthenticator(
                        settings.connector_capability_codec(),
                        app.state.components.connector_attempt_authorizer,
                    )
                app.state.connector_mcp_gateway = ConnectorMCPGateway(
                    app.state.connector_provider_runtime,
                    standard_authenticator=standard_authenticator,
                    attempt_authenticator=attempt_authenticator,
                    operation_timeout_seconds=settings.connector_mcp_operation_timeout_seconds,
                )
                await stack.enter_async_context(app.state.connector_mcp_gateway.run())
                app.state.local_connector_provider_operations = LocalConnectorProviderOperations(
                    app.state.connector_providers
                )
                if settings.role is ServiceRole.connector and settings.connector_internal_auth_token is None:
                    raise ValueError("FOUNDATION_CONNECTOR_INTERNAL_AUTH_TOKEN is required for the connector role")
                if settings.connector_internal_auth_token is not None:
                    app.state.connector_provider_operations_app = create_connector_provider_operations_app(
                        app.state.local_connector_provider_operations,
                        authentication_token=settings.connector_internal_auth_token,
                    )
            if settings.role is ServiceRole.all:
                app.state.connector_provider_operations = app.state.local_connector_provider_operations
            elif settings.role is ServiceRole.control:
                configured_operations = app.state.components.connector_provider_operations
                if configured_operations is not None:
                    app.state.connector_provider_operations = configured_operations
                else:
                    if settings.connector_internal_auth_token is None:
                        raise ValueError("FOUNDATION_CONNECTOR_INTERNAL_AUTH_TOKEN is required for the control role")
                    connector_http_client = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            base_url=settings.connector_service_base_url,
                            headers={
                                "Authorization": (f"Bearer {settings.connector_internal_auth_token.get_secret_value()}")
                            },
                            follow_redirects=False,
                        )
                    )
                    app.state.connector_provider_operations = RemoteConnectorProviderOperations(connector_http_client)
            if settings.role in _CONTROL_PLANE_ROLES:
                app.state.connector_service = ConnectorService(
                    storage.sessions,
                    app.state.connector_provider_operations,
                    AgentPresetConnectorReferenceChecker(),
                )
                app.state.connection_service = ConnectionService(
                    storage.sessions,
                    app.state.connector_provider_operations,
                    app.state.connector_secret_store,
                )
            model_http_client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    follow_redirects=False,
                    event_hooks={"request": [validate_model_request]},
                )
            )
            github_http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False))
            secret_resolver = app.state.components.model_secret_resolver
            if secret_resolver is None:
                secret_resolver = DatabaseSecretValueResolver(storage.sessions, secret_protector)
            connection_tester = app.state.components.model_connection_tester
            if connection_tester is None and secret_resolver is not None:
                connection_tester = NativeModelConnectionTester(
                    secret_resolver=secret_resolver,
                    endpoint_policy=app.state.model_endpoint_policy,
                    http_client=model_http_client,
                )
            app.state.model_secret_resolver = secret_resolver
            package_store = SkillPackageStore(storage.objects)
            asset_staging = await AssetStaging.create(storage.files_root, limiter=storage.file_limiter)
            asset_objects = AssetObjectStore(storage.objects, asset_staging)
            asset_cleanup_reconciler: AssetCleanupReconciler | None = None
            if settings.role in _CONTROL_PLANE_ROLES:
                selected_environment_providers = app.state.components.environment_provider_catalog
                if selected_environment_providers is None:
                    selected_environment_providers = build_environment_provider_catalog(
                        builtin_keys=settings.environment_provider_builtins,
                        extension_keys=settings.environment_provider_extensions,
                    )
                app.state.environment_provider_catalog = FoundationEnvironmentProviderCatalog(
                    selected_environment_providers
                )
                app.state.environment_service = EnvironmentManagementService(
                    storage.sessions,
                    app.state.environment_provider_catalog,
                )
                plugin_staging = await PluginStaging.create(storage.files_root, limiter=storage.file_limiter)
                app.state.plugin_service = PluginService(
                    storage.sessions,
                    PluginObjectStore(storage.objects),
                    plugin_staging,
                    runtime_mode=settings.plugin_runtime_mode,
                    max_wheel_bytes=settings.plugin_max_wheel_bytes,
                    max_expanded_bytes=settings.plugin_max_expanded_bytes,
                    max_archive_members=settings.plugin_max_archive_members,
                )
                await app.state.plugin_service.ensure_runtime_mode()
                github_acquirer = app.state.components.skill_github_acquirer or GitHubSkillAcquirer(github_http_client)
                credential_resolver = app.state.components.skill_credential_resolver
                if credential_resolver is None:
                    credential_resolver = DatabaseGitHubCredentialResolver(
                        storage.sessions,
                        settings.secret_protector(),
                    )
                app.state.skill_upload_service = SkillUploadService(storage.sessions, package_store)
                source_preparer = SkillSourcePreparer(
                    storage.sessions,
                    package_store,
                    github_acquirer,
                    credential_resolver,
                )
                app.state.skill_publication_service = SkillPublicationService(storage.sessions, source_preparer)
                app.state.skill_catalog_service = SkillCatalogService(storage.sessions, package_store)
                app.state.skill_selection_resolver = SkillSelectionResolver(storage.sessions)
                app.state.accepted_model_selector = AcceptedModelSelector(
                    storage.sessions,
                    app.state.model_provider_registry,
                    app.state.model_endpoint_policy,
                )
                app.state.agent_connector_selection_resolver = (
                    app.state.components.agent_connector_selection_resolver
                    or AgentConnectorSelectionResolver(
                        storage.sessions,
                        app.state.connector_provider_operations,
                        app.state.connector_secret_store,
                    )
                )
                app.state.agent_plugin_selection_resolver = (
                    app.state.components.agent_plugin_selection_resolver
                    or AgentPluginSelectionResolver(
                        storage.sessions,
                        runtime_mode=settings.plugin_runtime_mode,
                    )
                )
                app.state.agent_environment_selection_resolver = AgentEnvironmentSelectionResolver(
                    storage.sessions,
                    app.state.environment_provider_catalog,
                )
                app.state.agent_preset_resolver = app.state.components.agent_preset_resolver or AgentPresetResolver(
                    storage.sessions,
                    app.state.accepted_model_selector,
                    plugin_runtime_mode=settings.plugin_runtime_mode,
                    connector_resolver=app.state.agent_connector_selection_resolver,
                    environment_resolver=app.state.agent_environment_selection_resolver,
                    plugin_resolver=app.state.agent_plugin_selection_resolver,
                )
                app.state.agent_preset_invocation_resolver = (
                    app.state.components.agent_preset_invocation_resolver
                    or AgentPresetInvocationResolver(
                        storage.sessions,
                        app.state.accepted_model_selector,
                        plugin_runtime_mode=settings.plugin_runtime_mode,
                        connector_resolver=app.state.agent_connector_selection_resolver,
                        environment_resolver=app.state.agent_environment_selection_resolver,
                        plugin_resolver=app.state.agent_plugin_selection_resolver,
                    )
                )
                app.state.agent_preset_service = AgentPresetService(
                    storage.sessions,
                    app.state.agent_preset_resolver,
                    app.state.agent_preset_invocation_resolver,
                )
                app.state.model_config_service = ModelConfigService(
                    storage.sessions,
                    app.state.model_provider_registry,
                    app.state.model_endpoint_policy,
                    resolve_dns_on_save=settings.model_resolve_dns_on_save,
                    connection_tester=connection_tester,
                    connection_test_timeout_seconds=settings.model_connection_test_timeout_seconds,
                )
                app.state.asset_service = AssetService(
                    storage.sessions,
                    asset_objects,
                    asset_staging,
                    max_size_bytes=settings.asset_max_size_bytes,
                )
                asset_cleanup_reconciler = AssetCleanupReconciler(
                    storage.sessions,
                    asset_objects,
                    poll_interval_seconds=settings.asset_cleanup_poll_interval_seconds,
                    lease_seconds=settings.asset_cleanup_lease_seconds,
                    max_attempts=settings.asset_cleanup_max_attempts,
                )
                app.state.asset_cleanup_reconciler = asset_cleanup_reconciler
            if settings.role in _WORKER_ROLES:
                app.state.native_model_factory = NativeModelFactory(model_http_client)
                app.state.skill_runtime_preparer = SkillRuntimePreparer(storage.sessions, package_store)
            async with create_task_group() as background_tasks:
                if asset_cleanup_reconciler is not None:
                    background_tasks.start_soon(asset_cleanup_reconciler.run)
                logger.info(
                    "service_started",
                    extra={
                        "event": "service_started",
                        "service": settings.service_name,
                        "role": settings.role.value,
                        "build_version": settings.build_version,
                    },
                )
                try:
                    yield
                finally:
                    background_tasks.cancel_scope.cancel()
                    logger.info(
                        "service_stopped",
                        extra={
                            "event": "service_stopped",
                            "service": settings.service_name,
                            "role": settings.role.value,
                        },
                    )
    finally:
        await observability.aclose()


class _BoundaryAuthorizedConnectorInvocationAuthorizer:
    """Trust the standard or Attempt boundary that already made the policy decision."""

    async def authorize_connector_discovery(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
    ) -> None:
        del organization_id, workspace_id, principal, connector_id, connector_revision_id, connection_id

    async def authorize_connector_tool(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        tool_id: str,
        effects: Sequence[str],
        credential_audiences: Sequence[str],
    ) -> None:
        del (
            organization_id,
            workspace_id,
            principal,
            connector_id,
            connector_revision_id,
            connection_id,
            tool_id,
            effects,
            credential_audiences,
        )


class _LifespanMCPApp:
    def __init__(self, app: FastAPI, surface: str) -> None:
        self._app = app
        self._surface = surface

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        gateway: ConnectorMCPGateway | None = getattr(self._app.state, "connector_mcp_gateway", None)
        if gateway is None:
            raise RuntimeError("Connector MCP Gateway is unavailable")
        target = gateway.standard_app if self._surface == "standard" else gateway.internal_app
        await target(scope, receive, send)


class _LifespanConnectorProviderOperationsApp:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target: FastAPI | None = getattr(self._app.state, "connector_provider_operations_app", None)
        if target is None:
            raise RuntimeError("Connector Provider operations are unavailable")
        await target(scope, receive, send)


def create_app(settings: ServiceSettings | None = None, *, components: ServiceComponents | None = None) -> FastAPI:
    """Create an application without opening external resources."""

    resolved_settings = settings or get_settings()
    resolved_components = components or ServiceComponents()
    trace_query_provider_registry = resolved_components.trace_query_provider_registry or TraceQueryProviderRegistry()
    if "langfuse" in trace_query_provider_registry.keys():
        raise ValueError("Trace Query provider key is already registered: langfuse")
    resolved_settings.validate_trace_query_configuration(
        registered_provider_keys=(*trace_query_provider_registry.keys(), "langfuse")
    )
    serves_control_plane = resolved_settings.role in _CONTROL_PLANE_ROLES
    serves_connector_plane = resolved_settings.role in _CONNECTOR_ROLES
    app = FastAPI(
        title="Agent Foundation Service",
        version=resolved_settings.build_version,
        lifespan=_lifespan,
        openapi_url="/api/openapi.json" if serves_control_plane else None,
        docs_url="/api/docs" if serves_control_plane else None,
        redoc_url="/api/redoc" if serves_control_plane else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if serves_control_plane else None,
    )
    install_api_conventions(app)
    app.state.settings = resolved_settings
    app.state.components = resolved_components
    app.state.trace_query_provider_registry = trace_query_provider_registry.copy()
    app.state.request_authenticator = app.state.components.request_authenticator
    app.state.model_provider_registry = built_in_provider_registry()
    app.state.model_endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_domains=resolved_settings.model_private_endpoint_domains,
        private_cidrs=resolved_settings.model_private_endpoint_cidrs,
    )

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": resolved_settings.role.value}

    @app.get("/readyz", include_in_schema=False)
    async def readiness(request: Request) -> dict[str, str]:
        storage: StorageResources = request.app.state.storage
        try:
            with fail_after(resolved_settings.database_readiness_timeout_seconds):
                async with short_session(storage.sessions) as session:
                    await session.execute(text("SELECT 1"))
                await storage.redis.ping()
        except Exception as exc:
            logger.warning(
                "storage_readiness_failed",
                extra={"event": "storage_readiness_failed", "role": resolved_settings.role.value},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="required storage unavailable",
            ) from exc
        return {"status": "ready", "role": resolved_settings.role.value}

    if serves_connector_plane:
        app.mount("/mcp/connectors", _LifespanMCPApp(app, "standard"))
        app.mount("/internal/mcp/connectors", _LifespanMCPApp(app, "internal"))
        if resolved_settings.connector_internal_auth_token is not None:
            app.mount(
                "/internal/connector-provider-operations",
                _LifespanConnectorProviderOperationsApp(app),
            )

        @app.api_route("/mcp", methods=_API_METHODS, include_in_schema=False)
        @app.api_route("/mcp/{mcp_path:path}", methods=_API_METHODS, include_in_schema=False)
        async def unknown_mcp_path(mcp_path: str = "") -> None:
            del mcp_path
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP route not found")

        @app.api_route("/internal/mcp", methods=_API_METHODS, include_in_schema=False)
        @app.api_route("/internal/mcp/{mcp_path:path}", methods=_API_METHODS, include_in_schema=False)
        async def unknown_internal_mcp_path(mcp_path: str = "") -> None:
            del mcp_path
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP route not found")

    if serves_control_plane:
        app.include_router(agent_preset_router)
        app.include_router(connector_router)
        app.include_router(environment_router)
        app.include_router(asset_router)
        app.include_router(model_config_router)
        app.include_router(plugin_router)
        app.include_router(skill_router)
        app.include_router(trace_query_router)

        @app.api_route("/api", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_root() -> None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

        @app.api_route("/api/{api_path:path}", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_path(api_path: str) -> None:
            del api_path
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

        if resolved_settings.web_dist_dir is not None:
            mount_web_application(app, resolved_settings.web_dist_dir)

    return app
