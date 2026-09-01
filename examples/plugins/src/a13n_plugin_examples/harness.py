"""A package-loadable Harness middleware plugin with fresh per-run state."""

from __future__ import annotations

from collections.abc import MutableSequence
from typing import Any

from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    HarnessEvent,
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
    PluginRunResponse,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from a13n_plugin_examples.records import ObservedRunStatus, RunObservation

PLUGIN_KEY = "example.run-recorder"
DEFAULT_PLUGIN_ID = "recorder-default"


class RunRecorderConfiguration(BaseModel):
    """The package-owned portion of the standardized factory context."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    count_events: bool = True


class RunRecorderPlugin(AbstractHarnessPlugin):
    """Observe the complete run boundary and append one bounded record."""

    def __init__(
        self,
        *,
        plugin_id: str,
        observation_sink: MutableSequence[RunObservation] | None = None,
        run_id: str | None = None,
        count_events: bool = True,
    ) -> None:
        self._plugin_id = plugin_id
        self._observations = observation_sink if observation_sink is not None else []
        self._run_id = run_id
        self._count_events = count_events

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
        # A new exact-type instance owns mutable counters for each logical run.
        return RunRecorderPlugin(
            plugin_id=self._plugin_id,
            observation_sink=self._observations,
            run_id=context.run_id,
            count_events=self._count_events,
        )

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext[Any],
    ) -> PluginRunResponse[Any]:
        async def iterate():
            event_count = 0
            status: ObservedRunStatus = "interrupted"
            try:
                async for item in call_next(exchange):
                    if self._count_events and isinstance(item, HarnessEvent):
                        event_count += 1
                    elif isinstance(item, HarnessRunResult):
                        status = item.status
                    yield item
            finally:
                self._observations.append(
                    RunObservation(
                        run_id=self._run_id or exchange.context.run_id,
                        status=status,
                        event_count=event_count,
                    )
                )

        return PluginRunResponse(iterate())


class RunRecorderPluginFactory(HarnessPluginFactory):
    """Create recorder plugins from the Harness-owned build envelope."""

    @classmethod
    def plugin_key(cls) -> str:
        return PLUGIN_KEY

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        try:
            parsed = RunRecorderConfiguration.model_validate(dict(context.configuration))
        except ValidationError as exc:
            # The catalog replaces this cause with a stable, sanitized public error.
            raise ValueError("Invalid example.run-recorder configuration.") from exc
        return RunRecorderPlugin(
            plugin_id=context.plugin_id,
            count_events=parsed.count_events,
        )
