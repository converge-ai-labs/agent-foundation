"""Trusted WebUI Connector SDK. No Agent loop, SQL session or HTTP adapter is exposed."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, ClassVar

from anyio import sleep_forever
from pydantic import BaseModel, JsonValue
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorInput, ConnectorRule, StateChange
from a13n_harness_ui.errors import HarnessUiError

from .models import Capture, Declaration, Status, Step

if TYPE_CHECKING:
    from a13n_harness_ui.storage.connectors import ConnectorRepository

    from .runtime import Connectors


class Context:
    """Host-bound instance identity, private state, generation-fenced ingress and services."""

    def __init__(
        self, connector_id: str, repository: ConnectorRepository, runtime: Connectors, signal: Callable[[], None]
    ) -> None:
        self.connector_id, self._repository, self._runtime, self._signal = connector_id, repository, runtime, signal
        self.oauth = runtime.oauth

    async def configuration(self) -> ConnectorConfiguration:
        return await self._repository.configuration(self.connector_id)

    async def state(self, key: str):
        return await self._repository.state(self.connector_id, key)

    async def commit(
        self,
        *,
        configuration_version: int,
        state_changes: tuple[StateChange, ...] = (),
        bot_inputs: tuple[ConnectorInput, ...] = (),
    ) -> tuple[str, ...]:
        self._runtime.require_owner()
        result = await self._repository.commit(
            self.connector_id,
            configuration_version=configuration_version,
            state_changes=state_changes,
            bot_inputs=bot_inputs,
        )
        if bot_inputs:
            self._signal()
        return result

    async def submit(
        self, *, source_id: str, text: str, configuration_version: int, event_type: str = "external"
    ) -> tuple[str, ...]:
        return await self.commit(
            configuration_version=configuration_version,
            bot_inputs=(
                ConnectorInput(
                    connector_id=self.connector_id,
                    source_event_id=source_id,
                    text=text,
                    event_type=event_type,
                ),
            ),
        )

    async def credentials(self) -> tuple[int, dict[str, str]]:
        current = await self.configuration()
        return current.credential_version, dict(current.credentials)

    async def save_credentials(self, *, expected_version: int, credentials: dict[str, str]) -> int:
        self._runtime.require_owner()
        return await self._repository.save_credentials(
            self.connector_id, expected_version=expected_version, credentials=credentials
        )

    async def configure(
        self, candidate: ConnectorConfiguration, *, expected_version: int, credentials: dict[str, str] | None = None
    ) -> ConnectorConfiguration:
        if candidate.connector_id != self.connector_id:
            raise HarnessUiError("Configuration belongs to another instance.", code="connector_input_invalid")
        return await self._runtime.save(candidate, expected_version=expected_version, credentials=credentials)

    async def require_available(self, capture: Capture | None = None) -> ConnectorConfiguration:
        self._runtime.require_owner()
        current = await self.configuration()
        if not current.enabled or (capture is not None and current.version != capture.configuration_version):
            raise HarnessUiError(
                "Connector is disabled or its captured binding changed; start a fresh Run.",
                code="connector_binding_changed",
            )
        return current

    def service(self, connector_id: str, name: str, *, api_version: int, expected_type: type[Any]) -> Any:
        return self._runtime.service(connector_id, name, api_version=api_version, expected_type=expected_type)


class SetupFlow(ABC):
    """One ephemeral interaction. Reads observe; explicit actions or run() advance it."""

    @abstractmethod
    async def read(self) -> Step: ...

    @abstractmethod
    async def act(self, action: str, values: dict[str, JsonValue]) -> Step: ...

    async def run(self) -> None:
        await sleep_forever()

    async def close(self) -> None:
        """Release optional flow-owned resources."""
        return None


class Controller(ABC):
    def __init__(self, context: Context) -> None:
        self.context = context

    @abstractmethod
    async def status(self) -> Status: ...

    async def model_context(self) -> dict[str, JsonValue]:
        status = await self.status()
        return {"state": status.state, "summary": status.summary}

    async def capture(self) -> dict[str, JsonValue] | None:
        return None

    def capability(self, capture: Capture) -> AbstractCapability[Any] | None:
        return None

    async def begin_setup(self, intent: str) -> SetupFlow:
        raise HarnessUiError("This Connector has no setup interaction.", code="connector_setup_unavailable")

    async def run(self, configuration: ConnectorConfiguration) -> None:
        """Optional plugin-owned producer. Host restarts it on configuration change."""
        await sleep_forever()

    def services(self) -> dict[tuple[str, int], object]:
        return {}


class Definition(ABC):
    """No-argument entry-point factory; declarations perform no network or credential I/O."""

    configuration_model: type[BaseModel]
    condition_models: ClassVar[dict[str, type[BaseModel] | None]] = {}

    @abstractmethod
    def declaration(self) -> Declaration: ...

    def validate(self, settings: dict[str, JsonValue], rules: tuple[ConnectorRule, ...]) -> dict[str, JsonValue]:
        normalized = self.configuration_model.model_validate(settings).model_dump(mode="json")
        declared = {event.key for event in self.declaration().events}
        if len({rule.id for rule in rules}) != len(rules):
            raise ValueError("Rule IDs must be unique")
        for rule in rules:
            if rule.event not in declared:
                raise ValueError("Rule event is not declared")
            model = self.condition_models.get(rule.event)
            if model is None:
                if rule.conditions:
                    raise ValueError("This event has no conditions")
            else:
                model.model_validate(rule.conditions)
        return normalized

    @abstractmethod
    def open(self, context: Context) -> AbstractAsyncContextManager[Controller]: ...
