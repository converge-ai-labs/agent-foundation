"""A package-loadable Harness middleware plugin with fresh per-run state."""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence
from typing import Any

from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    HarnessRunResult,
    PluginOrdering,
)
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
)
from a13n_harness.plugins import (
    PluginRunExchange,
    PluginRunNext,
)
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from a13n_plugin_examples.records import ObservedRunStatus, RunObservation

PLUGIN_KEY = "example.run-recorder"
DEFAULT_PLUGIN_ID = "recorder-default"


class RunRecorderConfiguration(BaseModel):
    """The package-owned portion of the standardized factory context."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    record_usage: bool = True


class RunRecorderPlugin(AbstractHarnessPlugin):
    """Observe the complete run boundary and append one bounded record."""

    def __init__(
        self,
        *,
        plugin_id: str,
        observation_sink: MutableSequence[RunObservation] | None = None,
        run_id: str | None = None,
        record_usage: bool = True,
    ) -> None:
        self._plugin_id = plugin_id
        self._observations = observation_sink if observation_sink is not None else []
        self._run_id = run_id
        self._record_usage = record_usage

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def observations(self) -> tuple[RunObservation, ...]:
        """Return a detached view suitable for this in-memory example."""

        return tuple(self._observations)

    def get_ordering(self) -> PluginOrdering:
        return PluginOrdering(position="outermost")

    async def for_run(self, context: AgentContext) -> RunRecorderPlugin:
        # A new exact-type instance owns run identity for each logical run.
        return RunRecorderPlugin(
            plugin_id=self._plugin_id,
            observation_sink=self._observations,
            run_id=context.run_id,
            record_usage=self._record_usage,
        )

    async def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext[Any],
    ) -> HarnessRunResult[Any]:
        status: ObservedRunStatus = "interrupted"
        requests = 0
        try:
            result = await call_next(exchange)
            status = result.status
            if self._record_usage:
                requests = result.usage.requests
            return result
        finally:
            self._observations.append(
                RunObservation(
                    run_id=self._run_id or exchange.context.run_id,
                    status=status,
                    model_requests=requests,
                )
            )


class RunRecorderPluginFactory(HarnessPluginFactory):
    """Create recorder plugins from the Harness-owned build envelope."""

    @classmethod
    def plugin_key(cls) -> str:
        return PLUGIN_KEY

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> BaseModel:
        try:
            return RunRecorderConfiguration.model_validate(dict(configuration))
        except ValidationError as exc:
            # The catalog replaces this cause with a stable, sanitized public error.
            raise ValueError("Invalid example.run-recorder configuration.") from exc

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        parsed = RunRecorderConfiguration.model_validate(dict(context.configuration))
        return RunRecorderPlugin(
            plugin_id=context.plugin_id,
            record_usage=parsed.record_usage,
        )
