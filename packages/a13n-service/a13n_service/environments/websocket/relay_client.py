"""Worker publication and cancellation within one confirmed Attempt use."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Literal

from anyio import move_on_after
from pydantic import JsonValue

from a13n_service.ids import new_object_id

from .authority import DispatchAuthority, DispatchDenied, LeaseDeadline, UseIdentity
from .coordination import ConfirmedObservation
from .relay_protocol import CONTROL_OPERATIONS, RelayFailure, RelayInput, RelayLimits, RelayRequest, canonical_message
from .relay_storage import ConnectionRelayStore, RelayStoreError
from .relay_waiters import PendingRelayRequest, RelayOperationError, RelayResponseDispatcher


class RelayUseClient:
    def __init__(
        self,
        identity: UseIdentity,
        observation: ConfirmedObservation,
        store: ConnectionRelayStore,
        responses: RelayResponseDispatcher,
        *,
        check_authority: Callable[[], None],
    ) -> None:
        if (
            observation.value.use is None
            or observation.value.use.identity != identity
            or observation.value.status != "online"
            or observation.value.connection != identity.connection
            or store.connection != identity.connection
        ):
            raise ValueError("Relay client requires the exact confirmed use and connection")
        self.identity = identity
        self._authority = DispatchAuthority(identity, observation.deadline(use=True))
        self._store = store
        self._responses = responses
        self._check_authority = check_authority
        self._server_ms = observation.value.now_ms
        self._received_at = monotonic()
        self._closed = False
        self._authority.check(identity)

    @property
    def limits(self) -> RelayLimits:
        return self._store.limits

    async def renew(self, observation: ConfirmedObservation) -> None:
        if (
            observation.value.use is None
            or observation.value.use.identity != self.identity
            or observation.value.status != "online"
            or observation.value.connection != self.identity.connection
        ):
            await self.invalidate()
            raise DispatchDenied("Relay use was revoked")
        self._authority.renew(self.identity, observation.deadline(use=True))
        # Receipt time maps to an earlier server time, conservatively shortening
        # request deadlines. Delayed responses never extend dispatch authority.
        self._server_ms = observation.value.now_ms
        self._received_at = monotonic()

    async def invalidate(self) -> None:
        self._closed = True
        self._responses.fence_use(self.identity)
        await self._authority.fence()

    def _check(self) -> None:
        if self._closed:
            raise DispatchDenied("Relay use is closed")
        self._check_authority()
        self._authority.check(self.identity)

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
        self._check()
        deadline = LeaseDeadline(monotonic() + timeout_seconds)
        message = RelayRequest(
            request_id=new_object_id("erq"),
            use=self.identity,
            operation=operation,
            payload=payload,
            deadline_ms=self._server_ms + math.floor((deadline.monotonic_at - self._received_at) * 1000),
        )
        with self._responses.register(message, self._authority, deadline, streaming=streaming) as pending:
            try:
                async with asyncio.timeout_at(deadline.monotonic_at):
                    await self._publish(message, pending)
                    yield pending
            except asyncio.CancelledError:
                pending.fail("environment_cancelled")
                raise
            except TimeoutError as error:
                pending.fail("environment_timeout")
                raise RelayOperationError(RelayFailure(code="environment_timeout", certainty="unknown")) from error
            finally:
                if pending.needs_cancellation:
                    await self._cancel(message)

    async def _publish(self, message: RelayRequest, pending: PendingRelayRequest) -> None:
        canonical_message(
            message,
            max_bytes=self._store.limits.control_bytes
            if message.operation in CONTROL_OPERATIONS
            else self._store.limits.request_bytes,
        )
        pending.begin_publication()
        for attempt in range(2):
            try:
                async with self._authority.write(self.identity):
                    self._check()
                    evidence = await self._store.append(message)
                result = evidence.terminal()
                if evidence.phase == "completed":
                    if result is None or result.request_id != message.request_id or result.use != self.identity:
                        raise RelayStoreError("outcome_unknown")
                    self._responses.accept(result)
                return
            except RelayStoreError as error:
                if error.code == "relay_unavailable" and attempt == 0:
                    continue
                if error.code in {"scope_lost", "response_scope_lost", "outcome_unknown", "request_conflict"}:
                    await self.invalidate()
                known = attempt == 0 and error.code in {"relay_overloaded", "request_expired", "request_invalid"}
                code = {
                    "relay_overloaded": "environment_overloaded",
                    "request_expired": "environment_timeout",
                    "request_invalid": "environment_request_invalid",
                }.get(error.code, "environment_unknown_outcome")
                failure = RelayFailure.model_validate(
                    {"code": code, "certainty": "not_dispatched" if known else "unknown"}
                )
                pending.fail(failure.code, certainty=failure.certainty)
                raise RelayOperationError(failure) from error
            except DispatchDenied as error:
                pending.fail("environment_unavailable")
                raise RelayOperationError(RelayFailure(code="environment_unavailable", certainty="unknown")) from error

    async def send_input(self, pending: PendingRelayRequest, frame: RelayInput) -> None:
        if pending.request.use != self.identity:
            raise ValueError("Input publication requires this client's request")
        for attempt in range(2):
            try:
                async with self._authority.write(self.identity):
                    self._check()
                    await self._store.send_input(pending.request, frame)
                return
            except RelayStoreError as error:
                if error.code == "relay_unavailable" and attempt == 0:
                    continue
                pending.fail("environment_transfer_incomplete")
                raise RelayOperationError(
                    RelayFailure(code="environment_transfer_incomplete", certainty="unknown")
                ) from error

    async def _cancel(self, message: RelayRequest) -> None:
        if message.operation in CONTROL_OPERATIONS or self._closed:
            return
        with move_on_after(0.25, shield=True):
            try:
                await self.call("operation.cancel", {"request_id": message.request_id}, timeout_seconds=0.2)
            except Exception:
                # Cancellation ends local waiting; no failure here can establish
                # that a remote effect terminated or replace the caller's error.
                pass

    async def close(self) -> None:
        if self._closed:
            return
        try:
            with move_on_after(0.5, shield=True):
                try:
                    await self.call("scope.close", timeout_seconds=0.4)
                except Exception:
                    pass
        finally:
            await self.invalidate()
