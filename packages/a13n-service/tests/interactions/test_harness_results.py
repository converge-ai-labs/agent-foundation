from __future__ import annotations

import hashlib

import pytest
import rfc8785
from a13n_harness import HarnessRunResult
from a13n_service.interactions.domain import PendingCallKind, RunWaitReason
from a13n_service.interactions.harness_results import HarnessOutcomeProjectionError, StoredHarnessOutcomeAdapter
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.state import CompletedOutcomeCandidate, WaitingOutcomeCandidate
from a13n_service.storage import ObjectStore
from pydantic import TypeAdapter
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage

from .conftest import ORGANIZATION_ID, RUN_ID, initial_state

pytestmark = pytest.mark.anyio


async def test_completed_output_uses_inline_and_object_representations(
    interaction_object_store: ObjectStore,
) -> None:
    payloads = RunPayloadStore(interaction_object_store)
    inline = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=payloads,
        max_output_bytes=1024,
        inline_output_bytes=128,
    )

    inline_projection = await inline.project(_completed({"answer": 42}))

    assert isinstance(inline_projection.candidate, CompletedOutcomeCandidate)
    assert inline_projection.candidate.output == {"answer": 42}
    assert inline_projection.candidate.output_object is None
    assert inline_projection.deferred is None

    object_backed = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=payloads,
        max_output_bytes=1024,
        inline_output_bytes=8,
    )
    object_projection = await object_backed.project(_completed("a longer output"))

    assert isinstance(object_projection.candidate, CompletedOutcomeCandidate)
    assert "output" not in object_projection.candidate.model_fields_set
    reference = object_projection.candidate.output_object
    assert reference is not None
    stored = await payloads.read(ORGANIZATION_ID, reference)
    assert stored.run_id == RUN_ID
    assert stored.payload == "a longer output"
    assert object_projection.candidate.output_text == "a longer output"


async def test_completed_output_rejects_values_beyond_accepted_limit(
    interaction_object_store: ObjectStore,
) -> None:
    adapter = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=RunPayloadStore(interaction_object_store),
        max_output_bytes=8,
        inline_output_bytes=8,
    )

    with pytest.raises(HarnessOutcomeProjectionError, match="accepted output limit"):
        await adapter.project(_completed("too long"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_completed_output_rejects_non_finite_numbers(
    interaction_object_store: ObjectStore,
    value: float,
) -> None:
    adapter = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=RunPayloadStore(interaction_object_store),
        max_output_bytes=1024,
        inline_output_bytes=128,
    )

    with pytest.raises(HarnessOutcomeProjectionError, match="not finite JSON"):
        await adapter.project(_completed({"value": value}))


async def test_suspended_result_preserves_native_requests_and_classifies_pending_calls(
    interaction_object_store: ObjectStore,
) -> None:
    client_tool_surface = [{"toolset_id": "service", "tools": [{"name": "client_action"}]}]
    deferred = DeferredToolRequests(
        calls=[
            ToolCallPart(
                tool_name="ask_user_question",
                args={"questions": []},
                tool_call_id="question-1",
            ),
            ToolCallPart(
                tool_name="client_action",
                args={"value": 7},
                tool_call_id="client-1",
            ),
        ],
        approvals=[
            ToolCallPart(
                tool_name="dangerous_action",
                args={},
                tool_call_id="approval-1",
            )
        ],
    )
    adapter = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=RunPayloadStore(interaction_object_store),
        max_output_bytes=1024,
        inline_output_bytes=128,
        client_tool_surface=client_tool_surface,
    )

    projection = await adapter.project(_suspended(deferred))

    assert isinstance(projection.candidate, WaitingOutcomeCandidate)
    assert projection.candidate.wait_reason is RunWaitReason.multiple
    assert [call.kind for call in projection.candidate.pending.calls] == [
        PendingCallKind.user_input,
        PendingCallKind.client_tool,
        PendingCallKind.approval,
    ]
    assert projection.deferred is not None
    assert projection.deferred.requests == TypeAdapter(DeferredToolRequests).dump_python(
        deferred,
        mode="json",
    )
    assert projection.deferred.effective_client_tool_surface == client_tool_surface
    assert (
        projection.deferred.effective_surface_digest_sha256
        == hashlib.sha256(rfc8785.dumps(client_tool_surface)).hexdigest()
    )


async def test_client_tool_suspension_requires_frozen_effective_surface(
    interaction_object_store: ObjectStore,
) -> None:
    adapter = StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=RunPayloadStore(interaction_object_store),
        max_output_bytes=1024,
        inline_output_bytes=128,
    )
    deferred = DeferredToolRequests(calls=[ToolCallPart(tool_name="client_action", args={}, tool_call_id="client-1")])

    with pytest.raises(HarnessOutcomeProjectionError, match="effective surface"):
        await adapter.project(_suspended(deferred))


def _completed(output: object) -> HarnessRunResult[object]:
    state = initial_state().harness
    return HarnessRunResult(
        thread_id=state.thread_id,
        run_id="harness-run-1",
        status="completed",
        output=output,
        state=state,
        usage=RunUsage(),
    )


def _suspended(deferred: DeferredToolRequests) -> HarnessRunResult[object]:
    state = initial_state().harness
    return HarnessRunResult(
        thread_id=state.thread_id,
        run_id="harness-run-1",
        status="suspended",
        output=None,
        state=state,
        usage=RunUsage(),
        suspend_reason="deferred",
        deferred=deferred,
    )
