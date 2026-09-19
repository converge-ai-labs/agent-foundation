"""Worker publication and cancellation within one confirmed Attempt use."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Literal

from anyio import move_on_after
from pydantic import JsonValue

from a13n_service.ids import new_object_id

from .authority import DispatchDenied, LeaseDeadline, UseIdentity
from .relay_protocol import (
    CONTROL_OPERATIONS,
    RelayFailure,
    RelayInput,
    RelayLimits,
    RelayRequest,
)
from .relay_scope import RelayUseScope
from .relay_storage import RelayStoreError
from .relay_waiters import PendingRelayRequest, RelayOperationError


class RelayUseClient:
    """One binding's pending operations within its confirmed use scope."""

    def __init__(
        self,
        scope: RelayUseScope,
        *,
        mount_name: str = "workspace",
    ) -> None:
        self._scope = scope
        self._mount_name = mount_name
        self._mount_id = new_object_id("emt")
        self._closed = False
        self._pending: set[PendingRelayRequest] = set()

    @property
    def identity(self) -> UseIdentity:
        return self._scope.identity

    @property
    def mount_name(self) -> str:
        return self._mount_name

    def bind_mount(self, mount_id: str) -> None:
        self._mount_id = mount_id

    @property
    def limits(self) -> RelayLimits:
        return self._scope.store.limits

    @property
    def available(self) -> bool:
        return not self._closed and self._scope.available

    def fence(self) -> None:
        self._closed = True
        for pending in self._pending:
            pending.fail("environment_unavailable")

    def _check(self) -> None:
        if self._closed:
            raise DispatchDenied("Relay mount is closed")
        self._scope.require_current()

    async def call(
        self,
        operation: str,
        payload: dict[str, JsonValue] | None = None,
        *,
        timeout_seconds: float = 30,
    ) -> JsonValue:
        async with self.request(operation, payload or {}, timeout_seconds=timeout_seconds) as pending:
            return (await pending.result()).result

    @asynccontextmanager
    async def request(
        self,
        operation: str,
        payload: dict[str, JsonValue],
        *,
        timeout_seconds: float = 30,
        streaming: Literal["download", "upload"] | None = None,
    ) -> AsyncIterator[PendingRelayRequest]:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("Relay operation deadlines must be finite and at most 60 seconds")
        try:
            self._check()
        except DispatchDenied as error:
            raise RelayOperationError(
                RelayFailure(code="environment_unavailable", certainty="not_dispatched")
            ) from error
        deadline = LeaseDeadline(monotonic() + timeout_seconds)
        message = RelayRequest(
            request_id=new_object_id("erq"),
            scope=self.identity,
            operation=operation,
            mount_id=self._mount_id,
            mount_name=self._mount_name,
            payload=payload,
            deadline_ms=self._scope.server_ms + math.floor((deadline.monotonic_at - self._scope.received_at) * 1000),
        )
        with self._scope.responses.register(message, self._scope.authority, deadline, streaming=streaming) as pending:
            self._pending.add(pending)
            try:
                async with asyncio.timeout_at(deadline.monotonic_at):
                    await self._publish(pending)
                    yield pending
            except asyncio.CancelledError:
                pending.fail("environment_cancelled")
                raise
            except TimeoutError as error:
                pending.fail("environment_timeout")
                raise RelayOperationError(RelayFailure(code="environment_timeout", certainty="unknown")) from error
            finally:
                self._pending.discard(pending)
                if pending.needs_cancellation:
                    await self._cancel(message)

    async def _publish(self, pending: PendingRelayRequest) -> None:
        try:
            await pending.publish(self._scope.store, check_authority=self._check)
        except RelayOperationError as error:
            cause = error.__cause__
            if isinstance(cause, RelayStoreError) and cause.code in {
                "scope_lost",
                "response_scope_lost",
                "outcome_unknown",
                "request_conflict",
            }:
                await self._scope.invalidate()
            raise

    async def send_input(self, pending: PendingRelayRequest, frame: RelayInput) -> None:
        if pending not in self._pending:
            raise ValueError("Input publication requires this mount's pending request")
        for attempt in range(2):
            try:
                async with self._scope.authority.write(self.identity):
                    self._check()
                    await self._scope.store.send_input(pending.request, frame)
                return
            except RelayStoreError as error:
                if error.code == "relay_unavailable" and attempt == 0:
                    continue
                pending.fail("environment_transfer_incomplete")
                raise RelayOperationError(
                    RelayFailure(code="environment_transfer_incomplete", certainty="unknown")
                ) from error
            except DispatchDenied as error:
                pending.fail("environment_unavailable")
                raise RelayOperationError(RelayFailure(code="environment_unavailable", certainty="unknown")) from error

    async def _cancel(self, message: RelayRequest) -> None:
        if message.operation in CONTROL_OPERATIONS or not self._scope.available:
            return
        with move_on_after(0.25, shield=True):
            try:
                control = RelayUseClient(self._scope, mount_name=self._mount_name)
                await control.call("operation.cancel", {"request_id": message.request_id}, timeout_seconds=0.2)
            except Exception:
                # Cancellation ends local waiting; no failure here can establish
                # that a remote effect terminated or replace the caller's error.
                pass
