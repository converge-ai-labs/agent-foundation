"""Evidence for inputs that must not follow a root operation into a later turn."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from a13n_harness import HarnessState
from a13n_harness.capabilities.steering import steering_input_ids, without_input
from a13n_harness.content import request_parts
from pydantic_ai.messages import ModelRequest, TextContent

InputDelivery = Literal["accepted", "not_delivered", "unknown"]


@dataclass(slots=True)
class InputOffer:
    native_id: str
    completion: asyncio.Future[InputDelivery]
    offered: bool = False

    def settle(self, delivery: InputDelivery) -> None:
        if not self.completion.done():
            self.completion.set_result(delivery)


class StrictRootInputs:
    """Process-local offers; only a selected primary request can commit them."""

    def __init__(self, accepted: Callable[[str], None]) -> None:
        self._accepted = accepted
        self._prompts: dict[str, str | None] = {}
        self.steers: dict[str, InputOffer] = {}
        self.closed = False

    def prompt(self, parts: Sequence[tuple[str, str]]) -> tuple[TextContent, ...]:
        result = []
        for input_id, text in parts:
            token = f"input-{uuid4().hex}"
            self._prompts[input_id] = token
            result.append(TextContent(text, metadata={"source_id": token}))
        return tuple(result)

    def offer(self, input_id: str) -> InputOffer | None:
        if self.closed or input_id in self._prompts or len(self.steers) >= 256:
            return None
        offer = self.steers.get(input_id)
        if offer is None:
            offer = InputOffer(f"input-{uuid4().hex}", asyncio.get_running_loop().create_future())
            self.steers[input_id] = offer
            return offer
        return None

    def prepare_cut(self, state: HarnessState, *, primary: bool = False) -> tuple[HarnessState, tuple[str, ...]]:
        """Strip pending inputs from both native history and recovery ledgers.

        At a primary boundary a native delivery ID in history is evidence of
        incorporation. At any other cut only already committed IDs may survive.
        """
        incorporated = set(steering_input_ids(state.message_history)) if primary else set()
        committed = []
        for input_id, offer in tuple(self.steers.items()):
            if offer.completion.done() and offer.completion.result() == "accepted":
                continue
            if offer.native_id in incorporated:
                committed.append(input_id)
            elif offer.offered:
                state = without_input(state, input_id=offer.native_id, source_id=offer.native_id)
        return state, tuple(committed)

    def commit(self, state: HarnessState, steers: Sequence[str]) -> None:
        pending = {token: input_id for input_id, token in self._prompts.items() if token is not None}
        if pending:
            for message in state.message_history:
                if isinstance(message, ModelRequest):
                    for _, items in request_parts(message):
                        for item in items or ():
                            source_id = item.metadata.source_id
                            if source_id is not None and (input_id := pending.pop(source_id, None)) is not None:
                                self._prompts[input_id] = None
                                self._accepted(input_id)
        for input_id in steers:
            self.steers[input_id].settle("accepted")
            self._accepted(input_id)

    def close(self, *, uncertain: bool = False) -> None:
        self.closed = True
        for offer in self.steers.values():
            offer.settle("unknown" if uncertain and offer.offered else "not_delivered")
