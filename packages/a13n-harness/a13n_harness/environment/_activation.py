"""Run-owned first-use activation of a Host source, separate from library execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from itertools import count

from a13n_environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_environment.models import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_environment.operations import EnvironmentOperations
from a13n_logging import get_logger

from a13n_harness.errors import EnvironmentActivationError

from ._cleanup import _await_cleanup_shielded
from .providers import _claim_execution
from .sources import EnvironmentScope, EnvironmentSource

_OPENING_ORDER = count()


class _MountExecution:
    """One mount's optional execution. Metadata reads never open it."""

    def __init__(
        self,
        source: EnvironmentSource,
        scope: EnvironmentScope,
        observer: Callable[[str, EnvironmentScope, BaseException | None], None] | None,
    ) -> None:
        self._source, self._scope, self._observer = source, scope, observer
        descriptor = source.descriptor
        if not isinstance(descriptor, EnvironmentDescriptor):
            raise EnvironmentError("Source returned an invalid descriptor.", code="environment_request_invalid")
        self._configured = descriptor.model_copy(update={"generation": "unprepared", "backing_identity": None})
        self._provider_key, self._environment_id = source.provider_key, source.environment_id
        self._last_descriptor = self._configured
        self._execution: EnvironmentExecution | None = None
        self._activation: asyncio.Task[None] | None = None
        self.opened_order = -1
        self._closed = False
        self._accepting_activation = True
        self._retired = False
        self._cleanup_failure: BaseException | None = None

    @property
    def provider_key(self) -> str:
        return self._provider_key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def execution_id(self) -> str:
        return self._execution.execution_id if self._execution is not None else "unprepared"

    @property
    def state(self) -> EnvironmentState | None:
        return self._execution.state if self._execution is not None else self._source.state

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._last_descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return (
            self._execution.availability if self._execution is not None else EnvironmentAvailability(status="preparing")
        )

    @property
    def operations(self) -> EnvironmentOperations:
        return self._execution.operations if self._execution is not None else EnvironmentOperations()

    def _observe(self, event: str, error: BaseException | None = None) -> None:
        if self._observer is not None:
            try:
                self._observer(event, self._scope, error)
            except Exception:
                get_logger(__name__).exception("Environment lifecycle observation failed")

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._closed:
            raise EnvironmentError("The mount is closed.", code="environment_closed")
        if self._activation is None:
            if not self._accepting_activation:
                raise EnvironmentError("The mount is closing.", code="environment_closed")
            self._activation = asyncio.create_task(self._open())
            self._activation.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        # A cancelled waiter cannot cancel preparation needed by another operation.
        # Success and failure stay cached for this mount incarnation.
        try:
            await asyncio.shield(self._activation)
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                # The waiter receives the cleanup failure together with the activation error.
                self._cleanup_failure = None
            raise
        assert self._execution is not None
        try:
            await self._execution.check_ready(operations)
        finally:
            descriptor = self._execution.descriptor
            previous = self._last_descriptor
            if descriptor.generation != previous.generation or descriptor.backing_identity != previous.backing_identity:
                raise EnvironmentError(
                    "Environment target changed within an execution.", code="environment_stale_mount"
                )
            self._last_descriptor = descriptor

    async def _open(self) -> None:
        self._observe("started")
        execution = None
        try:
            try:
                connector = await self._source.ensure_ready()
            except Exception as error:
                raise EnvironmentActivationError(
                    "Host environment preparation failed.",
                    code="environment_activation_failed",
                ) from error
            if not isinstance(connector, EnvironmentConnector):
                raise EnvironmentError("Source returned an invalid connector.", code="environment_provider_failure")
            if (connector.provider_key, connector.environment_id) != (self.provider_key, self.environment_id):
                raise EnvironmentError("Source changed its selected target.", code="environment_stale_mount")
            opened = await connector.open()
            if not _claim_execution(opened, self):
                raise EnvironmentError("Environment execution is already owned.", code="environment_execution_reused")
            execution = opened
            if (execution.provider_key, execution.environment_id) != (connector.provider_key, connector.environment_id):
                raise EnvironmentError("Connector changed its target.", code="environment_stale_mount")
            if not isinstance(execution.descriptor, EnvironmentDescriptor) or not isinstance(
                execution.availability, EnvironmentAvailability
            ):
                raise EnvironmentError("Execution returned invalid metadata.", code="environment_provider_failure")
            if (
                not isinstance(execution.execution_id, str)
                or not execution.execution_id
                or len(execution.execution_id) > 128
            ):
                raise EnvironmentError("Execution returned an invalid identity.", code="environment_provider_failure")
            _validate_operation_facets(execution.descriptor, execution.availability, execution.operations)
            if execution.availability.status not in {"available", "degraded"}:
                raise EnvironmentError("Connector returned an unready execution.", code="environment_unavailable")
            if not execution.descriptor.permissions.operations <= self._configured.permissions.operations:
                raise EnvironmentError(
                    "Execution broadened configured permissions.", code="environment_provider_failure"
                )
            if self._closed or self._retired:
                raise EnvironmentError("The mount closed during activation.", code="environment_closed")
            self._last_descriptor = execution.descriptor
            self._execution = execution
            self.opened_order = next(_OPENING_ORDER)
            self._observe("ready")
        except BaseException as error:
            self._observe("failed", error)
            if execution is not None:
                try:
                    await _await_cleanup_shielded(execution.close())
                except BaseException as cleanup:
                    self._cleanup_failure = cleanup
                    raise BaseExceptionGroup("Environment activation and cleanup failed", [error, cleanup]) from None
            raise

    def fence_activation(self, *, cancel: bool = False) -> None:
        self._accepting_activation = False
        self._retired = self._retired or cancel
        if cancel and self._activation is not None and not self._activation.done():
            self._activation.cancel()

    async def close(self) -> None:
        self._closed = True
        task = self._activation
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        execution, self._execution = self._execution, None
        if execution is not None:
            try:
                await execution.close()
            except BaseException as error:
                self._observe("closed", error)
                raise
            else:
                self._observe("closed")
        if self._cleanup_failure is not None:
            raise self._cleanup_failure


def _validate_operation_facets(
    descriptor: EnvironmentDescriptor,
    availability: EnvironmentAvailability,
    operations: EnvironmentOperations,
) -> None:
    if not isinstance(operations, EnvironmentOperations):
        raise EnvironmentError("Provider returned invalid operations.", code="environment_provider_failure")
    facet_families = {
        family
        for family in ("files", "shell", "processes", "ports", "outputs", "computer")
        if getattr(operations, family) is not None
    }
    advertised_facets = set(descriptor.operation_families)
    required_facets = availability.ready_families if availability.status == "preparing" else advertised_facets
    if not facet_families <= advertised_facets or not required_facets <= facet_families:
        raise EnvironmentError(
            "Provider descriptor and operation facets disagree.",
            code="environment_provider_failure",
        )
    for action in descriptor.permissions.operations:
        dispatch = ENVIRONMENT_ACTION_DISPATCH[action]
        if dispatch.facet not in facet_families:
            continue
        method = getattr(getattr(operations, dispatch.facet), dispatch.method, None)
        if not callable(method):
            raise EnvironmentError(
                "Provider permission has no executable semantic method.",
                code="environment_provider_failure",
                details={"action": action.value},
            )
