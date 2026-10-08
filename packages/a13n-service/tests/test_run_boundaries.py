"""Producer display cuts and pre-effect acknowledgement are independent of delivery."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, RunBindings
from a13n_service.runs.boundaries import Boundaries, Staged
from a13n_service.runs.display import DisplayFold, Snapshot, Tail, open_tool_calls
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fail_commit", [False, True])
async def test_checkpoint_control_is_serial_and_independent_of_public_consumption(fail_commit: bool) -> None:
    fold = DisplayFold("run", Tail(), attempt=1, page_items=2, page_bytes=65536)
    boundaries = Boundaries(lambda: {})
    effects: list[int] = []
    calls = 0
    tools_staged = asyncio.Event()
    tool_commit = asyncio.Event()
    release_tools = asyncio.Event()
    cuts: list[Staged] = []

    def freeze(open_calls: frozenset[str]) -> Snapshot:
        if open_calls:
            tools_staged.set()
        return fold.snapshot()

    boundaries.freeze_display = freeze

    def capture(item: HarnessEvent) -> None:
        fold.fold(fold.events(item), item)

    async def commit(staged: Staged) -> None:
        cuts.append(staged)
        items = [entry for page in staged.display.pages for entry in page.items] + staged.display.tail.items
        if len(cuts) == 1:
            assert any(entry.content.get("text") == "Input" for entry in items)
            assert not any(entry.kind == "tool_call" for entry in items)
            # Checkpoint I/O overlaps the model. Its old display cut cannot move ahead.
            await tools_staged.wait()
            assert effects == []
            assert any(entry.kind == "tool_call" for entry in fold.items.values())
            assert not any(entry.kind == "tool_call" for entry in staged.display.tail.items)
        if staged.at == "tool":
            assert len(cuts) == 2
            assert cuts[0].completed.done()
            assert effects == []
            assert open_tool_calls(staged.state.message_history) == {"call-0", "call-1"}
            assert {
                entry.content["toolCallId"]
                for entry in items
                if entry.kind == "tool_call" and entry.state == "in_progress"
            } == {"call-0", "call-1"}
            tool_commit.set()
            await release_tools.wait()
            if fail_commit:
                raise RuntimeError("checkpoint failed")
        fold.committed(fold.pending(staged.display))

    async def effect(value: int) -> str:
        assert cuts[1].completed.done() and not cuts[1].completed.cancelled()
        effects.append(value)
        return str(value)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                index: DeltaToolCall(name="effect", json_args=f'{{"value":{index}}}', tool_call_id=f"call-{index}")
                for index in (0, 1)
            }
        else:
            assert sorted(effects) == [0, 1]
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(boundaries, Capability(tools=[Tool(effect)])),
    )

    async def run() -> None:
        async with executable.stream("Input", bindings=RunBindings.embedded(producer_observer=capture)) as stream:
            async with boundaries.process(commit):
                await stream.__anext__()  # Pause the public consumer after run_started.
                await tool_commit.wait()
                assert effects == []
                release_tools.set()
                async for _ in stream:
                    pass
                await boundaries.drain()
            assert stream.result is not None and stream.result.output_or_raise() == "done"

    async with asyncio.timeout(3):
        if fail_commit:
            with pytest.raises(RuntimeError, match="checkpoint failed"):
                await run()
            assert effects == []
            assert cuts[1].completed.cancelled()
        else:
            await run()
            assert len(cuts) == 3
            assert sorted(effects) == [0, 1]
            assert all(cut.completed.done() for cut in cuts)


async def test_handoff_keeps_model_cut_when_tool_checkpoint_is_already_queued(
    service, scripted_model, runs_kit, monkeypatch
) -> None:
    from a13n_service.infra.db import short_session
    from a13n_service.runs.checkpoints import StatePointer, load_state
    from a13n_service.runs.execute import _Attempt
    from a13n_service.runs.tables import RunRow

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"enabled": True}})
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_handoff")
    submitted = await runs_kit.start_thread(service, agent, "look around")
    run_id = submitted["run"]["id"]
    queued = asyncio.Event()
    stage, commit = Boundaries._stage, _Attempt._commit
    committed = []

    async def staged(self, ctx, messages, at):
        completed = await stage(self, ctx, messages, at)
        if at == "tool":
            queued.set()
        return completed

    async def delayed(self, state, **kwargs):
        await queued.wait()
        result = await commit(self, state, **kwargs)
        committed.append(state)
        return result

    monkeypatch.setattr(Boundaries, "_stage", staged)
    monkeypatch.setattr(_Attempt, "_commit", delayed)
    async with asyncio.timeout(10):
        await (await runs_kit.attempt(service, handoff=True))

    assert len(committed) == 1
    assert open_tool_calls(committed[0].message_history) == frozenset()
    assert (await runs_kit.get_run(service, run_id))["status"] == "accepted"
    attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
    assert [attempt["status"] for attempt in attempts] == ["yielded"]
    async with short_session(service.runtime.storage) as session:
        pointer = StatePointer.model_validate((await session.get_one(RunRow, run_id)).checkpoint)
    saved = await load_state(service.runtime.objects, pointer)
    assert saved is not None and open_tool_calls(saved.harness.message_history) == frozenset()
    assert scripted_model.requests.qsize() == 1
