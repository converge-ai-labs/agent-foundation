"""Host-owned, fail-open refresh of the official model data document."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from random import uniform
from threading import Lock
from time import monotonic

import anyio
import httpx2
from a13n_logging import get_logger
from anyio.to_thread import run_sync

from a13n_harness._official_data import parse_official_data, publish_official_data

logger = get_logger(__name__)

OFFICIAL_MODELS_URL = (
    "https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/refs/heads/main/"
    "packages/a13n-harness/a13n_harness/data/official-models.yaml"
)
FETCH_SECONDS = 20.0
MAX_BYTES = 2 * 1024 * 1024
REFRESH_SECONDS = 3600.0
RETRY_SECONDS = 60.0


def official_model_updates_enabled() -> bool:
    """Disable new refresh loops with A13N_OFFICIAL_MODELS_AUTO_UPDATE=0."""
    return os.environ.get("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def _fetch() -> bytes:
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(FETCH_SECONDS, connect=5)) as client:
        async with client.stream("GET", OFFICIAL_MODELS_URL) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("official model catalog exceeds the download limit")
            return bytes(content)


@dataclass
class _RefreshState:
    # Process-wide scheduling survives Host restarts. Never hold the lock across I/O.
    lock: Lock = field(default_factory=Lock)
    active: bool = False
    next_attempt: float = 0.0
    failures: int = 0


_state = _RefreshState()


async def _refresh(fetch: Callable[[], Awaitable[bytes]]) -> None:
    """Claim one due attempt across Hosts, including Hosts on different event loops."""
    with _state.lock:
        if _state.active or monotonic() < _state.next_attempt:
            return
        _state.active = True
    delay = RETRY_SECONDS
    try:
        with anyio.fail_after(FETCH_SECONDS):
            content = await fetch()
            if len(content) > MAX_BYTES:
                raise ValueError("official model catalog exceeds the download limit")
            candidate = await run_sync(parse_official_data, content)
        publish_official_data(candidate)
        _state.failures = 0
        delay = REFRESH_SECONDS * uniform(1, 1.1)
    except Exception as error:
        _state.failures = min(_state.failures + 1, 7)
        delay = min(REFRESH_SECONDS, RETRY_SECONDS * 2 ** (_state.failures - 1)) * uniform(0.8, 1)
        if isinstance(error, httpx2.HTTPStatusError):
            retry_after = error.response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, min(float(retry_after), 86400))
        logger.warning(
            "official_model_catalog_update_failed",
            extra={"error_type": type(error).__name__, "retry_seconds": delay},
        )
    finally:
        # Cancellation releases the claim but does not cause an immediate retry storm.
        with _state.lock:
            _state.next_attempt = monotonic() + delay
            _state.active = False


async def run_official_model_updates(fetch: Callable[[], Awaitable[bytes]] = _fetch) -> None:
    """Run until Host cancellation; imports, lookups and builds never start this loop.

    Hosts with an outbound policy supply a bounded async fetcher for the fixed document.
    Concurrent Hosts share one process-wide attempt and retry schedule, not one request per Agent.
    Stopping a Host or disabling updates does not discard an already published snapshot.
    """
    if not official_model_updates_enabled():
        return
    await anyio.sleep(uniform(0, 30))
    while True:
        await _refresh(fetch)
        with _state.lock:
            delay = max(1, _state.next_attempt - monotonic()) if not _state.active else RETRY_SECONDS
        await anyio.sleep(delay)
