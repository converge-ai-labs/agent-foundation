"""Opt-in, process-local probe for Modal 1.5.5 desktop SDK calls; never shipped in Service."""

from __future__ import annotations

import asyncio
import inspect
import ssl
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from importlib.metadata import version
from time import perf_counter
from typing import Any
from uuid import uuid4

from a13n_harness.providers.environment.modal.diagnostics import _operation
from a13n_logging import get_logger

_parent: ContextVar[str | None] = ContextVar("modal_sdk_parent", default=None)
_logger = get_logger("a13n_harness.providers.environment.modal.sdk_timings")
_RPCS = frozenset(
    {
        "SandboxWait",
        "SandboxTagsGet",
        "SandboxGetTaskId",
        "TaskGetCommandRouterAccess",
        "SandboxGetTunnels",
        "TaskExecStart",
        "TaskExecWait",
        "AuthTokenGet",
    }
)


@contextmanager
def span(stage: str) -> Iterator[None]:
    operation = _operation.get()
    if operation is None:
        yield
        return
    identity, parent = "mspan_" + uuid4().hex, _parent.get()
    token = _parent.set(identity)
    started = perf_counter()
    outcome, error_type = "ok", None
    try:
        yield
    except BaseException as error:
        outcome = "interrupted" if isinstance(error, asyncio.CancelledError) else "error"
        error_type = type(error).__name__
        raise
    finally:
        elapsed = perf_counter() - started
        _parent.reset(token)
        _logger.info(
            "Modal SDK stage finished",
            extra={
                "desktop_operation_id": operation[0],
                "desktop_operation": operation[1],
                "sdk_stage": stage,
                "sdk_span_id": identity,
                "sdk_parent_id": parent,
                "duration_ms": round(elapsed * 1000, 3),
                "outcome": outcome,
                "error_type": error_type,
            },
        )


def instrument(function: Callable[..., Any], stage: str, *, nested: bool = False) -> Callable[..., Any]:
    def enabled() -> bool:
        return _operation.get() is not None and (not nested or _parent.get() is not None)

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            if not enabled():
                return await function(*args, **kwargs)
            with span(stage):
                return await function(*args, **kwargs)

        return asynchronous

    @wraps(function)
    def synchronous(*args: Any, **kwargs: Any) -> Any:
        if not enabled():
            return function(*args, **kwargs)
        with span(stage):
            return function(*args, **kwargs)

    return synchronous


def install() -> Callable[[], None]:
    """Install once before serving; return restoration for tests/process teardown.

    These private boundaries are deliberately version-locked. Timing wrappers preserve
    arguments, results, exceptions, cancellation, retries and timeouts verbatim. Global
    network hooks only emit inside this task's Modal spans, never for unrelated I/O.
    """
    if version("modal") != "1.5.5":
        raise RuntimeError("Modal SDK timing probe requires reviewed SDK version 1.5.5")
    import grpclib.client
    from modal._utils import grpc_utils, proxy_support, task_command_router_client
    from modal.client import _Client
    from modal.sandbox import _Sandbox

    patches: list[tuple[Any, str, Any]] = []

    def patch(owner: Any, name: str, replacement: Any) -> None:
        patches.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def restore() -> None:
        for owner, name, original in reversed(patches):
            setattr(owner, name, original)
        patches.clear()

    boundaries = [
        (_Client, "_open", "client_open", False),
        (_Sandbox, "_exec", "exec", False),
        (_Sandbox, "_get_task_id", "task_id", False),
        (_Sandbox, "_get_command_router_client", "router_init", False),
        (grpc_utils, "create_channel_with_fallbacks", "channel_with_fallbacks", False),
        (grpc_utils.ModalChannel, "__connect__", "channel_attempt", False),
        (grpc_utils.ModalChannel, "_create_connection", "channel_connection", False),
        (proxy_support, "get_proxy_url", "proxy_selection", True),
        (proxy_support, "create_proxied_connection", "proxy_connection", True),
        (urllib.request, "proxy_bypass", "proxy_bypass", True),
        (urllib.request, "getproxies", "proxy_settings", True),
        (ssl, "create_default_context", "tls_context", True),
        (asyncio.BaseEventLoop, "create_connection", "network_connection", True),
        (asyncio.BaseEventLoop, "getaddrinfo", "dns", True),
        (asyncio.BaseEventLoop, "_connect_sock", "tcp_connect", True),
        (asyncio.BaseEventLoop, "_create_connection_transport", "transport_tls", True),
        (task_command_router_client, "_connect_channel", "router_connect_with_retries", False),
    ]
    try:
        for owner, name, stage, nested in boundaries:
            patch(owner, name, instrument(getattr(owner, name), stage, nested=nested))
        original_rpc = grpclib.client.UnaryUnaryMethod.__call__

        @wraps(original_rpc)
        async def rpc(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Only static allowlisted method names; never record URLs, args, metadata or payloads.
            name = self.name.rsplit("/", 1)[-1]
            if _operation.get() is not None and name in _RPCS:
                with span("rpc_" + name):
                    return await original_rpc(self, *args, **kwargs)
            return await original_rpc(self, *args, **kwargs)

        patch(grpclib.client.UnaryUnaryMethod, "__call__", rpc)
    except BaseException:
        restore()
        raise
    return restore


def main() -> None:
    restore = install()
    try:
        from a13n_service.cli import main as service_main

        service_main()
    finally:
        restore()


if __name__ == "__main__":
    main()
