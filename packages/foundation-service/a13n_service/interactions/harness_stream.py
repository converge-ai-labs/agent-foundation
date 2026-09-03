"""Single-consumer projection and terminal selection for a Harness Run stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from a13n_harness import (
    HarnessEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
)
from a13n_harness.errors import RunError

from .harness_results import HarnessOutcomeAdapter, RunTerminalCommitter, RunTerminalReceipt
from .run_control import FoundationRunControlCoordinator


class HarnessEventProjector(Protocol):
    """Await one non-terminal canonical Harness observation with backpressure."""

    async def project(self, event: HarnessEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class HarnessTerminalConsumption[OutputT]:
    result: HarnessRunResult[OutputT]
    terminal: RunTerminalReceipt


@dataclass(frozen=True, slots=True)
class HarnessHandoffConsumption[OutputT]:
    result: HarnessRunResult[OutputT]


type HarnessStreamConsumption[OutputT] = HarnessTerminalConsumption[OutputT] | HarnessHandoffConsumption[OutputT]


class HarnessStreamConsumer:
    """Consume exactly one entered Harness stream and select its sole result."""

    def __init__(
        self,
        *,
        projector: HarnessEventProjector,
        outcome_adapter: HarnessOutcomeAdapter,
        terminal_committer: RunTerminalCommitter,
    ) -> None:
        self._projector = projector
        self._outcome_adapter = outcome_adapter
        self._terminal_committer = terminal_committer
        self._consumed = False

    async def consume[OutputT](
        self,
        stream: HarnessRunStream[OutputT],
        coordinator: FoundationRunControlCoordinator,
    ) -> HarnessStreamConsumption[OutputT]:
        if self._consumed:
            raise RunError(
                "Foundation Harness stream consumer cannot be reused.",
                code="foundation_stream_consumer_reused",
            )
        self._consumed = True
        terminal_event: HarnessRunResultEvent[OutputT] | None = None
        terminal_receipt: RunTerminalReceipt | None = None
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                if terminal_event is not None:
                    raise RunError(
                        "Harness stream emitted more than one terminal result.",
                        code="foundation_stream_terminal_duplicate",
                    )
                terminal_event = item
                if terminal_event.thread_id != stream.thread_id or terminal_event.run_id != stream.run_id:
                    raise RunError(
                        "Harness terminal result does not match the entered stream.",
                        code="foundation_control_identity_mismatch",
                    )
                terminal_receipt = await coordinator.commit_terminal_result(
                    terminal_event.result,
                    adapter=self._outcome_adapter,
                    committer=self._terminal_committer,
                )
                continue
            if terminal_event is not None:
                raise RunError(
                    "Harness stream emitted an observation after its terminal result.",
                    code="foundation_stream_event_after_terminal",
                )
            await self._projector.project(item)
        if terminal_event is None:
            raise RunError(
                "Harness stream ended without a terminal result.",
                code="foundation_stream_terminal_missing",
            )
        if terminal_receipt is None:
            return HarnessHandoffConsumption(result=terminal_event.result)
        return HarnessTerminalConsumption(
            result=terminal_event.result,
            terminal=terminal_receipt,
        )


__all__ = [
    "HarnessEventProjector",
    "HarnessHandoffConsumption",
    "HarnessStreamConsumer",
    "HarnessStreamConsumption",
    "HarnessTerminalConsumption",
]
