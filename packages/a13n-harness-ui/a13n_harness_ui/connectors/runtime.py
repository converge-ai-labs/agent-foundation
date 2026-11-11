"""App-owned Connector controllers, setup sessions and generation-scoped producers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from a13n_logging import get_logger
from anyio import TASK_STATUS_IGNORED, CancelScope, Event, Lock, fail_after, move_on_after
from anyio.abc import TaskGroup, TaskStatus
from pydantic import JsonValue, ValidationError
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness_ui.connector_models import ConnectorConfiguration
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.storage import LocalStore

from .catalog import Catalog
from .forms import safe_values, validate_form
from .models import Capture, Instance, SetupCommand, SetupSnapshot, Status, Step
from .sdk import Context, Controller, SetupFlow


@dataclass
class _Worker:
    scope: CancelScope
    done: Event


@dataclass
class _Resource:
    release: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)


@dataclass
class _Session:
    session_id: str
    connector_id: str
    intent: str
    flow: SetupFlow
    expires_at: datetime
    version: int = 1
    lock: Lock = field(default_factory=Lock)
    operations: dict[str, tuple[str, SetupSnapshot]] = field(default_factory=dict)
    terminal: Step | None = None
    worker: _Worker | None = None
    action_scope: CancelScope | None = None
    active_operation: str | None = None
    observed: Step | None = None
    error: str | None = None


class Connectors:
    def __init__(
        self,
        store: LocalStore,
        catalog: Catalog,
        *,
        owned: bool,
        signal: Callable[[], None],
        changed: Callable[[], Any],
    ) -> None:
        self.store, self.catalog, self.owned = store, catalog, owned
        self._signal, self._changed = signal, changed
        self._controllers: dict[str, Controller] = {}
        self._workers: dict[str, _Worker] = {}
        self._diagnostics: dict[str, str] = {}
        self._sessions: dict[str, _Session] = {}
        self._resources: list[_Resource] = []
        self._resource_group: TaskGroup | None = None
        self._group: TaskGroup | None = None
        self._lock = Lock()
        self._setup_lock = Lock()
        self._sealed = False

    def require_owner(self) -> None:
        if not self.owned or self._sealed:
            raise HarnessUiError(
                "Connector runtime is unavailable in this App; use the owning WebUI instance.",
                code="connector_runtime_unavailable",
            )

    async def start(self, group: TaskGroup, *, resources: TaskGroup) -> None:
        self._group, self._resource_group = group, resources
        if self.owned:
            for configuration in await self.store.connectors.configurations():
                await self._open(configuration)
            for configuration in await self.store.connectors.configurations():
                await self._restart(configuration)

    async def _open(self, configuration: ConnectorConfiguration) -> None:
        if configuration.connector_id in self._controllers or configuration.kind not in self.catalog.definitions:
            return
        try:
            self.catalog.definitions[configuration.kind].validate(configuration.settings, configuration.rules)
            context = Context(configuration.connector_id, self.store.connectors, self, self._signal)
            assert self._resource_group is not None
            controller, resource = await self._resource_group.start(self._hold_controller, configuration.kind, context)
            self._resources.append(resource)
            self._controllers[configuration.connector_id] = controller
        except Exception as exc:
            self._diagnostics[configuration.connector_id] = f"Controller failed to open ({type(exc).__name__})."

    async def _hold_controller(
        self,
        kind: str,
        context: Context,
        *,
        task_status: TaskStatus[tuple[Controller, _Resource]] = TASK_STATUS_IGNORED,
    ) -> None:
        resource = _Resource()
        # The context is entered and exited in this task, including plugin-owned
        # task groups. Root users drain before close() releases these resources.
        with CancelScope(shield=True):
            try:
                async with self.catalog.definitions[kind].open(context) as controller:
                    task_status.started((controller, resource))
                    await resource.release.wait()
            finally:
                resource.done.set()

    async def _stop_worker(self, connector_id: str) -> None:
        previous = self._workers.pop(connector_id, None)
        if previous is not None:
            previous.scope.cancel()
            await previous.done.wait()

    async def _restart(self, configuration: ConnectorConfiguration) -> None:
        await self._stop_worker(configuration.connector_id)
        controller = self._controllers.get(configuration.connector_id)
        if configuration.enabled and controller is not None and self._group is not None:
            self._workers[configuration.connector_id] = await self._group.start(
                self._produce, controller, configuration
            )

    async def _produce(
        self,
        controller: Controller,
        configuration: ConnectorConfiguration,
        *,
        task_status: TaskStatus[_Worker] = TASK_STATUS_IGNORED,
    ) -> None:
        with CancelScope() as scope:
            worker = _Worker(scope, Event())
            task_status.started(worker)
            try:
                await controller.run(configuration)
            except Exception as exc:
                self._diagnostics[configuration.connector_id] = (
                    f"Background source failed ({type(exc).__name__}); save configuration to restart it."
                )
                get_logger(__name__).warning(
                    "connector_source_failed",
                    extra={"connector_id": configuration.connector_id, "error_type": type(exc).__name__},
                )
                await self._changed()
            finally:
                worker.done.set()

    async def instances(self) -> tuple[Instance, ...]:
        return tuple([await self.instance(item.connector_id) for item in await self.store.connectors.configurations()])

    async def instance(self, connector_id: str) -> Instance:
        configuration = await self.store.connectors.configuration(connector_id)
        controller = self._controllers.get(connector_id)
        if configuration.kind not in self.catalog.definitions:
            status = Status(
                state="plugin_unavailable",
                summary="The installed Connector implementation is unavailable; instance data is preserved.",
            )
        elif not self.owned:
            status = Status(
                state="runtime_unavailable", summary="Another App owns this data root's automation runtime."
            )
        elif connector_id in self._diagnostics:
            status = Status(state="degraded", summary=self._diagnostics[connector_id])
        elif controller is None:
            status = Status(state="degraded", summary="Controller is unavailable.")
        else:
            try:
                status = await controller.status()
            except Exception as exc:
                status = Status(state="degraded", summary=f"Status unavailable ({type(exc).__name__}).")
        return Instance(
            connector_id=connector_id,
            kind=configuration.kind,
            name=configuration.name or configuration.kind,
            version=configuration.version,
            enabled=configuration.enabled,
            deliver_to_bot=configuration.deliver_to_bot,
            settings=safe_values(
                self.catalog.definitions[configuration.kind].declaration().configuration_schema, configuration.settings
            )
            if configuration.kind in self.catalog.definitions
            else {},
            rules=configuration.rules,
            configured_secrets=tuple(configuration.credentials),
            status=status,
        )

    async def save(
        self,
        candidate: ConnectorConfiguration,
        *,
        expected_version: int | None,
        credentials: dict[str, str] | None = None,
    ) -> ConnectorConfiguration:
        self.require_owner()
        definition = self.catalog.definitions.get(candidate.kind)
        if definition is None:
            raise HarnessUiError("Connector implementation is unavailable.", code="connector_plugin_unavailable")
        try:
            settings = definition.validate(candidate.settings, candidate.rules)
        except (ValidationError, ValueError) as exc:
            raise HarnessUiError("Connector configuration is invalid.", code="connector_configuration_invalid") from exc
        if candidate.deliver_to_bot and not definition.declaration().bot_delivery:
            raise HarnessUiError(
                "This Connector does not declare Bot delivery.", code="connector_configuration_invalid"
            )
        async with self._lock:
            saved = await self.store.connectors.configure(
                candidate.model_copy(update={"settings": settings}),
                expected_version=expected_version,
                credentials=credentials,
            )
            self._diagnostics.pop(saved.connector_id, None)
            await self._open(saved)
            await self._restart(saved)
        await self._changed()
        self._signal()
        return saved

    async def delete(self, connector_id: str, *, expected_version: int) -> None:
        self.require_owner()
        async with self._lock:
            await self.store.connectors.delete(connector_id, expected_version=expected_version)
            await self._stop_worker(connector_id)
            for session in tuple(self._sessions.values()):
                if session.connector_id == connector_id:
                    await self._finish_session(session, "cancelled")
            # Controller resources stay alive until admitted root users have drained.
            self._controllers.pop(connector_id, None)
        await self._changed()

    async def capture(self) -> tuple[Capture, ...]:
        if not self.owned or self._sealed:
            return ()
        result = []
        for configuration in await self.store.connectors.configurations():
            controller = self._controllers.get(configuration.connector_id)
            if configuration.enabled and controller is not None and configuration.connector_id not in self._diagnostics:
                try:
                    binding = await controller.capture()
                except Exception as exc:
                    self._diagnostics[configuration.connector_id] = f"Tool binding unavailable ({type(exc).__name__})."
                    continue
                if binding is not None:
                    result.append(
                        Capture(
                            connector_id=configuration.connector_id,
                            connector_key=configuration.kind,
                            configuration_version=configuration.version,
                            implementation=self.catalog.items[configuration.kind].implementation,
                            binding=binding,
                        )
                    )
        return tuple(result)

    def capabilities(self, captures: tuple[Capture, ...]) -> dict[str, AbstractCapability[Any]]:
        result = {}
        for capture in captures:
            item = self.catalog.items.get(capture.connector_key)
            controller = self._controllers.get(capture.connector_id)
            if item is None or item.implementation != capture.implementation or controller is None:
                raise HarnessUiError(
                    "Captured Connector implementation is unavailable or changed.", code="connector_binding_changed"
                )
            capability = controller.capability(capture)
            if capability is not None:
                result[capture.connector_id] = capability
        return result

    def service(self, connector_id: str, name: str, *, api_version: int, expected_type: type[Any]) -> Any:
        self.require_owner()
        controller = self._controllers.get(connector_id)
        service = controller.services().get((name, api_version)) if controller is not None else None
        if not isinstance(service, expected_type):
            raise HarnessUiError(
                "Connector service is unavailable or incompatible.", code="connector_service_unavailable"
            )
        return service

    async def model_context(self, captures: tuple[Capture, ...]) -> dict[str, JsonValue]:
        instances: list[JsonValue] = []
        captured = {item.connector_id for item in captures}
        for instance in await self.instances():
            controller = self._controllers.get(instance.connector_id)
            safe: dict[str, JsonValue] = {}
            if controller is not None:
                try:
                    safe = await controller.model_context()
                except Exception:
                    safe = {"state": "unavailable"}
            instances.append(
                {
                    "id": instance.connector_id,
                    "kind": instance.kind,
                    "name": instance.name,
                    "status": instance.status.model_dump(mode="json"),
                    "captured": instance.connector_id in captured,
                    "context": safe,
                }
            )
        return {
            "types": [
                {
                    "key": item.key,
                    "description": item.declaration.description if item.declaration else "Unavailable",
                    "available": item.available,
                }
                for item in self.catalog.items.values()
            ],
            "instances": instances,
            "setup_tool": "offer_connector_setup",
            "authority": "Current status does not change captured tools. Setup and configuration edits start no Run.",
        }

    async def begin_setup(self, connector_id: str, *, intent: str = "configure") -> SetupSnapshot:
        self.require_owner()
        # Setup observation must not hold the configuration lock: actions hold
        # their session lock while committing configuration through save().
        async with self._setup_lock:
            self.require_owner()
            for session in tuple(self._sessions.values()):
                if (
                    session.connector_id == connector_id
                    and session.intent == intent
                    and session.terminal is None
                    and session.expires_at > datetime.now(UTC)
                ):
                    async with session.lock:
                        snapshot = await self._snapshot(session)
                    if session.terminal is None:
                        return snapshot
            if len(self._sessions) >= 128:
                for key in [
                    key
                    for key, session in self._sessions.items()
                    if session.terminal is not None or session.expires_at <= datetime.now(UTC)
                ]:
                    await self._finish_session(self._sessions.pop(key), "expired")
            if len(self._sessions) >= 128:
                raise HarnessUiError(
                    "Too many active setup sessions; close an existing session.", code="connector_setup_full"
                )
            configuration = await self.store.connectors.configuration(connector_id)
            definition = self.catalog.definitions.get(configuration.kind)
            controller = self._controllers.get(connector_id)
            if definition is None or controller is None or intent not in definition.declaration().setup_intents:
                raise HarnessUiError("Setup is unavailable for this Connector.", code="connector_setup_unavailable")
            flow = await controller.begin_setup(intent)
            if self._sealed:
                await flow.close()
                self.require_owner()
            session = _Session(
                f"setup-{uuid4().hex}", connector_id, intent, flow, datetime.now(UTC) + timedelta(minutes=30)
            )
            self._sessions[session.session_id] = session
            assert self._group is not None
            session.worker = await self._group.start(self._run_setup, session)
            return await self._snapshot(session)

    async def _run_setup(self, session: _Session, *, task_status: TaskStatus[_Worker] = TASK_STATUS_IGNORED) -> None:
        with CancelScope() as scope:
            worker = _Worker(scope, Event())
            task_status.started(worker)
            try:
                with move_on_after(max(0, (session.expires_at - datetime.now(UTC)).total_seconds())) as deadline:
                    await session.flow.run()
                if deadline.cancel_called:
                    async with session.lock:
                        session.terminal = Step(
                            key="expired",
                            title="Setup expired",
                            state="expired",
                            description="Start a new setup session. Saved configuration is preserved.",
                        )
                        session.version += 1
            except Exception as exc:
                session.terminal = Step(
                    key="failed",
                    title="Setup failed",
                    state="failed",
                    error=f"Setup worker failed ({type(exc).__name__}). Start again.",
                )
                session.version += 1
            finally:
                with CancelScope(shield=True):
                    with move_on_after(10):
                        async with session.lock:
                            await session.flow.close()
                worker.done.set()

    async def _snapshot(self, session: _Session) -> SetupSnapshot:
        if session.terminal is None and datetime.now(UTC) >= session.expires_at:
            session.terminal = Step(key="expired", title="Setup expired", state="expired")
        step = session.terminal or await session.flow.read()
        if step.state in {"completed", "failed", "cancelled", "expired"}:
            session.terminal = step
            if session.worker is not None:
                session.worker.scope.cancel()
        if session.terminal is None and step.wait is not None and datetime.now(UTC) >= step.wait.expires_at:
            step = Step(
                key="expired",
                title="Setup expired",
                state="expired",
                description="Start a new setup session. Saved configuration is preserved.",
            )
            session.terminal = step
            if session.worker is not None:
                session.worker.scope.cancel()
        if step.form is not None:
            validate_form(step.form)
            step = step.model_copy(update={"values": safe_values(step.form, step.values)})
        if session.observed is not None and step != session.observed:
            session.version += 1
            session.error = None
        session.observed = step
        return SetupSnapshot(
            session_id=session.session_id,
            connector_id=session.connector_id,
            version=session.version,
            expires_at=session.expires_at,
            step=step.model_copy(update={"error": session.error}) if session.error else step,
        )

    def _session(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise HarnessUiError(
                "Setup session is unavailable after restart; start again.", code="connector_setup_missing"
            )
        return session

    async def setup(self, session_id: str) -> SetupSnapshot:
        session = self._session(session_id)
        async with session.lock:
            return await self._snapshot(session)

    async def act(self, session_id: str, command: SetupCommand) -> SetupSnapshot:
        self.require_owner()
        session = self._session(session_id)
        digest = hashlib.sha256(
            json.dumps(
                {"action": command.action, "version": command.version, "values": command.values},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        # A valid explicit cancel must interrupt the action before waiting for
        # its serialization lock. The interrupted action records its own outcome.
        interrupted = None
        if (
            command.action == "cancel"
            and command.operation_id not in session.operations
            and command.operation_id != session.active_operation
            and command.version == session.version
            and session.terminal is None
            and datetime.now(UTC) < session.expires_at
        ):
            interrupted = session.action_scope
            if interrupted is not None:
                interrupted.cancel()
        async with session.lock:
            if previous := session.operations.get(command.operation_id):
                if previous[0] != digest:
                    raise HarnessUiError("Setup operation ID has different content.", code="connector_setup_conflict")
                return previous[1]
            snapshot = await self._snapshot(session)
            if (
                interrupted is None and (command.version != session.version or session.terminal is not None)
            ) or datetime.now(UTC) >= session.expires_at:
                raise HarnessUiError(
                    "Setup step changed or expired; refresh before acting.", code="connector_setup_conflict"
                )
            if len(session.operations) >= 128:
                session.terminal = Step(key="expired", title="Start a new setup session", state="expired")
                if session.worker is not None:
                    session.worker.scope.cancel()
                raise HarnessUiError("Setup action limit reached; start again.", code="connector_setup_conflict")
            if command.action == "cancel":
                session.terminal = Step(
                    key="cancelled",
                    title="Setup cancelled",
                    state="cancelled",
                    description="Inspect the saved connection; cancellation does not undo an upstream operation.",
                )
            elif command.action not in {
                action.id for action in snapshot.step.actions if action.kind in {"submit", "confirm"}
            }:
                raise HarnessUiError(
                    "Setup action is not available in this step.", code="connector_setup_action_invalid"
                )
            else:
                # Reconcile through this ephemeral operation ID, never replay an uncertain provider action.
                step = Step(
                    key="interrupted",
                    title="Setup action interrupted",
                    state="failed",
                    error="Inspect the saved connection before starting again.",
                )
                session.error = None
                with CancelScope(shield=True) as action_scope:
                    session.action_scope = action_scope
                    session.active_operation = command.operation_id
                    try:
                        with fail_after(min(30, max(0.01, (session.expires_at - datetime.now(UTC)).total_seconds()))):
                            step = await session.flow.act(command.action, command.values)
                    except (ValidationError, ValueError):
                        # Validation exceptions may contain private submitted values.
                        session.error = "Check the form values and try again with a new action."
                        step = snapshot.step.model_copy(update={"error": session.error})
                    except HarnessUiError as exc:
                        session.error = str(exc)
                        step = snapshot.step.model_copy(update={"error": session.error})
                    except Exception as exc:
                        step = Step(
                            key="failed",
                            title="Setup action interrupted",
                            state="failed",
                            error=f"Action outcome is unavailable ({type(exc).__name__}); inspect the saved connection before starting again.",
                        )
                    finally:
                        session.action_scope = None
                        session.active_operation = None
                if step.state in {"completed", "failed", "cancelled", "expired"}:
                    session.terminal = step
                else:
                    snapshot = snapshot.model_copy(update={"step": step})
            session.version += 1
            result = SetupSnapshot(
                session_id=session_id,
                connector_id=session.connector_id,
                version=session.version,
                expires_at=session.expires_at,
                step=session.terminal or snapshot.step,
            )
            if result.step.form is not None:
                validate_form(result.step.form)
                result = result.model_copy(
                    update={
                        "step": result.step.model_copy(
                            update={"values": safe_values(result.step.form, result.step.values)}
                        )
                    }
                )
            if session.error is None:
                session.observed = result.step
            session.operations[command.operation_id] = (digest, result)
            if session.terminal is not None and session.worker is not None:
                session.worker.scope.cancel()
            await self._changed()
            return result

    async def _finish_session(self, session: _Session, state: str) -> None:
        if session.action_scope is not None:
            session.action_scope.cancel()
        async with session.lock:
            if session.terminal is None:
                session.terminal = Step(
                    key=state, title=f"Setup {state}", state="expired" if state == "expired" else "cancelled"
                )
                session.version += 1
        if session.worker is not None:
            session.worker.scope.cancel()
            await session.worker.done.wait()

    async def stop(self) -> None:
        self._sealed = True
        for session in tuple(self._sessions.values()):
            await self._finish_session(session, "cancelled")
        for connector_id in tuple(self._workers):
            await self._stop_worker(connector_id)

    async def close(self) -> None:
        await self.stop()
        for resource in self._resources:
            resource.release.set()
        for resource in self._resources:
            await resource.done.wait()
