"""Trace Query control-plane construction."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx2

from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings
from a13n_service.trace_query.authorization import RunTraceAccessAuthorizer
from a13n_service.trace_query.langfuse import LangfuseTraceQueryProvider
from a13n_service.trace_query.logfire import LogfireTraceQueryProvider
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.service import TraceQueryService


async def build_trace_query_service(
    settings: Settings,
    components: Components,
    shared: SharedRuntime,
    registry: TraceQueryProviderRegistry,
    stack: AsyncExitStack,
) -> TraceQueryService:
    """Construct the selected Trace Query provider and service."""

    provider_registry = registry.copy()
    if settings.observability.query.provider == "langfuse":
        http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False, timeout=10.0))
        public_key = settings.observability.query.langfuse_public_key
        secret_key = settings.observability.query.langfuse_secret_key
        base_url = settings.observability.query.langfuse_base_url
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
    if settings.observability.query.provider == "logfire":
        query = settings.observability.query
        if query.logfire_base_url is None or query.logfire_read_token is None or query.logfire_history_from is None:
            raise RuntimeError("validated Logfire Trace Query configuration is incomplete")
        logfire_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False, timeout=10.0))
        logfire_base_url = query.logfire_base_url
        read_token = query.logfire_read_token.get_secret_value()
        history_from = query.logfire_history_from
        provider_registry.register(
            "logfire",
            lambda: LogfireTraceQueryProvider(
                logfire_client, base_url=logfire_base_url, read_token=read_token, history_from=history_from
            ),
        )
    provider = (
        None
        if settings.observability.query.provider == "none"
        else provider_registry.create(settings.observability.query.provider)
    )
    return TraceQueryService(
        provider_key=settings.observability.query.provider,
        provider=provider,
        authorizer=(
            components.trace_access_authorizer
            if components.trace_access_authorizer is not None
            else RunTraceAccessAuthorizer(shared.storage.sessions)
        ),
    )


__all__ = ["build_trace_query_service"]
