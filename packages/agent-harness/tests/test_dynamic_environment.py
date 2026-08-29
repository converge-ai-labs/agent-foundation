from __future__ import annotations

import asyncio
import json
import shlex
import sys
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import a13n_harness.environment.dynamic as dynamic_environment_module
import a13n_harness.execution as execution_module
import a13n_harness.toolsets.file_media as file_media_module
import a13n_harness.toolsets.files as file_toolset_module
import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPath,
    EnvironmentPermissionSet,
    FileMediaUnderstandingRunCapability,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    ModelCapability,
    ModelConfiguration,
    ModelRecoveryPolicy,
    ProviderUsageRecord,
    RunBindings,
)
from a13n_harness.environment.advanced import (
    EnvironmentBindingRequest,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    create_environment_run_binding,
    create_noop_environment_run_binding,
)
from a13n_harness.environment.dynamic import _DynamicEnvironmentRunCapability
from a13n_harness.environment.files import (
    FileEntriesResult,
    FileMetadata,
    FileTextMatch,
    FileTextSearchResult,
    FileWriteResult,
)
from a13n_harness.environment.local.binding import (
    DirectLocalEnvironmentProviderBinding,
    _DirectLocalFilePolicy,
)
from a13n_harness.environment.local.files import LocalFileOperator
from a13n_harness.environment.models import EnvironmentOperationReceipt
from a13n_harness.environment.providers import FileScopeSelection
from a13n_harness.environment.virtual_files import VirtualFileOperator, _PreparedFile
from a13n_harness.plugins import (
    AbstractHarnessPlugin,
    PluginOrdering,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
)
from a13n_harness.result import HarnessRunResult
from a13n_harness.state import AgentContextState
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    disclose_sequence_field,
    tool_output_size,
)
from a13n_harness.toolsets.process_manager import _fit_stream_prefixes
from a13n_harness.toolsets.shell import ShellToolset
from pydantic_ai import BinaryContent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio
requires_posix_process_groups = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Direct Local process groups require POSIX",
)
_PROCESS_EXECUTABLE = Path(sys.executable).resolve()


async def test_incomplete_oversized_sequence_spills_before_preserving_provider_cursor() -> None:
    spilled: list[bytes] = []

    class Context:
        async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
            assert suffix == ".json"
            spilled.append(data)
            return "/workspace/page.json"

    value = {
        "ok": True,
        "entries": [{"path": f"/entry-{index}-{'x' * 80}"} for index in range(200)],
        "has_more": True,
        "next_offset": 200,
    }
    bounded, showing = await disclose_sequence_field(
        cast(Any, Context()),
        cast(Any, value),
        field="entries",
        content_complete=False,
        noun="test page",
        continuation_hint="Continue from next_offset.",
    )

    assert showing < len(value["entries"])
    assert bounded["next_offset"] == 200
    assert bounded["disclosure"]["output_file_path"] == "/workspace/page.json"
    assert b"entry-199" in spilled[0]


async def test_file_list_restarts_page_when_secondary_spill_is_unavailable() -> None:
    class PagingFiles:
        async def list(self, path: str, *, offset: int, max_results: int, include_hidden: bool):
            del path, include_hidden
            entries = tuple(
                FileMetadata(
                    path=f"/entry-{index}-{'x' * 80}",
                    kind="file",
                    size=1,
                    writable=False,
                )
                for index in range(offset, min(200, offset + max_results))
            )
            return FileEntriesResult(
                entries=entries,
                offset=offset,
                has_more=offset + len(entries) < 200,
            )

    class Context:
        async def _spill_tool_result(self, data: bytes, *, suffix: str) -> None:
            del data, suffix
            return None

    toolset = FileToolset(cast(Any, PagingFiles()))
    ctx = cast(Any, SimpleNamespace(deps=Context()))

    oversized = await toolset.ls(ctx, "/", max_results=200)
    retried = await toolset.ls(ctx, "/", offset=oversized["next_offset"], max_results=10)

    assert oversized["showing"] < 200
    assert oversized["next_offset"] == 0
    assert oversized["has_more"] is True
    assert oversized["disclosure"]["output_file_path"] is None
    assert "smaller max_results" in oversized["disclosure"]["hint"]
    assert retried["entries"] == [
        {"path": f"/entry-{index}-{'x' * 80}", "kind": "file", "size": 1, "writable": False} for index in range(10)
    ]
    assert retried["next_offset"] == 10


def test_stream_prefixes_do_not_split_valid_utf8_characters() -> None:
    output = ("界" * 10).encode()

    stdout, stderr = _fit_stream_prefixes(output, b"", 10)

    assert stderr == b""
    assert stdout.decode() == "界" * 3
    assert stdout + output[len(stdout) :] == output


def _configuration(**updates: Any) -> DynamicEnvironmentConfiguration:
    return DynamicEnvironmentConfiguration(
        max_reference_entries=64,
        **updates,
    )


class _ShellRunContext:
    def __init__(self) -> None:
        self.deps = SimpleNamespace(
            state=AgentContextState(),
            thread_id="thread-test",
            run_id="run-test",
            instance=SimpleNamespace(agent_instance_id="agent-instance-test"),
        )
        self.enqueued: list[str] = []

    def enqueue(self, value: str, *, priority: str) -> str:
        del priority
        self.enqueued.append(value)
        return f"enqueue-{len(self.enqueued)}"


async def test_dynamic_environment_passes_process_event_hooks_to_each_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hook(event: Any) -> None:
        del event

    propagated: list[Any] = []

    def capture_run_capability(
        configuration: DynamicEnvironmentConfiguration,
        *,
        run_id: str,
        environment: Any,
        process_event_hooks: Any,
    ) -> object:
        del configuration, run_id, environment
        propagated.append(process_event_hooks)
        return object()

    monkeypatch.setattr(
        dynamic_environment_module,
        "_DynamicEnvironmentRunCapability",
        capture_run_capability,
    )

    class Deps:
        def __init__(self, parent_agent_instance_id: str | None) -> None:
            self.run_id = "run-1"
            self.environment = object()
            self.instance = SimpleNamespace(parent_agent_instance_id=parent_agent_instance_id)
            self.recorded: object | None = None

        def _run_capability(self, capability_id: str) -> None:
            del capability_id
            return None

        def _record_run_capability(self, capability_id: str, value: object) -> None:
            del capability_id
            self.recorded = value

    capability = DynamicEnvironmentCapability(_configuration(), process_event_hooks=(hook,))
    root_deps = Deps(None)
    child_deps = Deps("parent-1")

    await capability.for_run(cast(Any, SimpleNamespace(deps=root_deps)))
    await capability.for_run(cast(Any, SimpleNamespace(deps=child_deps)))

    assert propagated == [(hook,), (hook,)]
    assert root_deps.recorded is not None
    assert child_deps.recorded is not None


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _policy() -> InvocationPolicyCapability:
    return InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0)


def _local_binding(root: Path, *, process_output: bool = False):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="dynamic-environment-test",
            root=DirectLocalRootConfiguration(path=root),
            shell_profiles=(
                (DirectLocalShellProfile(profile_id="default", executable=Path("/bin/sh")),)
                if process_output and sys.platform != "win32"
                else ()
            ),
            allowed_executables=(frozenset({_PROCESS_EXECUTABLE}) if process_output else frozenset()),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _two_local_bindings(
    first_root: Path,
    second_root: Path,
    *,
    first_operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
    second_operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
):
    bindings = tuple(
        EnvironmentBindingRequest(
            binding_id=f"binding-{index}",
            binding_revision=1,
            alias=alias,
            permission_ceiling=EnvironmentPermissionSet(
                operations=first_operations if index == 1 else second_operations
            ),
            default_working_directory="/",
            provider_binding=DirectLocalEnvironmentProviderBinding(
                DirectLocalProviderConfiguration(
                    environment_id=f"dynamic-environment-{index}",
                    root=DirectLocalRootConfiguration(path=root),
                )
            ),
        )
        for index, (alias, root) in enumerate(
            (("local", first_root), ("shared", second_root)),
            start=1,
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=bindings,
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_dynamic_topology_emits_an_independent_harness_context_event(tmp_path: Path) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await finish.wait()
        yield "done"

    aggregate = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(topology_version=0, bindings=(), default_binding_id=None),
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="dynamic-environment-test",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )

    async with executable.stream("wait", bindings=RunBindings.embedded(environment=aggregate)) as run:
        pending = asyncio.create_task(run.__anext__())
        await started.wait()
        await aggregate.controller.apply(request)
        item = await asyncio.wait_for(pending, timeout=2)
        while not (
            isinstance(item, HarnessEvent)
            and isinstance(item.event, HarnessExtensionEvent)
            and item.event.kind == "context"
            and item.event.payload.get("type") == "environment_topology_changed"
        ):
            item = await asyncio.wait_for(run.__anext__(), timeout=2)
        assert item.event.payload["current_version"] == 1
        finish.set()
        terminal = [event async for event in run][-1]
        assert terminal.result.output_or_raise() == "done"


async def test_capability_projects_stable_tools_and_one_bounded_fresh_topology_snapshot() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append((messages, info))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert len(calls) == 1
    messages, info = calls[0]
    names = {tool.name for tool in info.function_tools}
    assert {"view", "write", "edit", "multi_edit", "ls", "glob", "grep"} <= names
    assert "environment_read_text" not in names
    assert {"shell_exec", "shell_wait", "shell_status", "shell_input", "shell_signal", "shell_kill"} <= names
    metadata = {
        tool.name: tool.metadata[HARNESS_TOOL_METADATA_KEY]
        for tool in info.function_tools
        if tool.metadata is not None and HARNESS_TOOL_METADATA_KEY in tool.metadata
    }
    assert "mkdir" in names
    assert {"move", "copy", "delete"}.isdisjoint(names)
    assert metadata["edit"].effects == frozenset({"read", "write"})
    assert metadata["shell_exec"].effects == frozenset({"read", "write", "delete", "execute", "external_communication"})
    assert metadata["shell_wait"].effects == frozenset({"read"})
    grep_tool = next(tool for tool in info.function_tools if tool.name == "grep")
    ignored_description = grep_tool.parameters_json_schema["properties"]["include_ignored"]["description"]
    assert "do not interpret repository ignore files" in ignored_description
    assert info.instructions is not None
    assert "Agent-wide tool timeout" in info.instructions
    assert '<tool-instruction name="view">' in info.instructions
    assert '<tool-instruction name="environment-shell">' in info.instructions
    assert '<tool-instruction name="copy">' not in info.instructions
    assert '<tool-instruction name="delete">' not in info.instructions
    topology_parts = [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str) and "topology_version" in part.content
    ]
    assert len(topology_parts) == 1
    assert '"bindings":[]' in topology_parts[0]
    assert len(topology_parts[0].encode()) < 64 * 1024
    assert executable.definition.agent.tool_timeout is None


async def test_file_tools_omit_file_revisions_and_use_native_managed_policy(tmp_path: Path) -> None:
    model_calls = 0
    tool_results: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal model_calls
        del info
        model_calls += 1
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        tool_results[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps({"file_path": "note.txt", "content": "one", "mode": "w"}),
                    tool_call_id="write-1",
                )
            }
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/note.txt",
                            "content": "two",
                            "mode": "w",
                        }
                    ),
                    tool_call_id="write-2",
                )
            }
        elif len(returns) == 2:
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/note.txt",
                            "content": "",
                            "mode": "a",
                        }
                    ),
                    tool_call_id="write-noop",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 2}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    result: HarnessRunResult[Any] | None = None
    async with executable.stream(
        "write",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    ) as run:
        async for item in run:
            if isinstance(item, HarnessEvent):
                if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "tool":
                    tool_events.append(item.event)
            else:
                result = item.result

    assert result is not None
    assert result.output_or_raise() == "done"
    assert [event.payload["tool_call_id"] for event in tool_events] == ["write-1", "write-2"]
    assert [event.payload["tool_id"] for event in tool_events] == ["filesystem.write", "filesystem.write"]
    assert [event.payload["value"]["changes"] for event in tool_events] == [
        [{"path": "note.txt", "action": "written", "destination": None}],
        [{"path": "/workspace/note.txt", "action": "written", "destination": None}],
    ]
    assert model_calls == 4
    assert (tmp_path / "note.txt").read_text() == "two"
    assert tool_results[0]["ok"] is True
    assert tool_results[0]["bytes_written"] == 3
    assert tool_results[1]["bytes_written"] == 3
    assert tool_results[2]["bytes_written"] == 0
    assert "revision" not in tool_results[0]
    assert "revision" not in tool_results[1]
    assert "revision" not in tool_results[2]


async def test_file_mutation_tools_execute_without_shell(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("value", encoding="utf-8")
    observed_results: list[dict[str, Any]] = []
    calls = (
        ("mkdir", {"paths": ["folder"], "parents": False}),
        (
            "move",
            {"pairs": [{"src": "source.txt", "dst": "folder/moved.txt"}], "overwrite": False},
        ),
        (
            "copy",
            {"pairs": [{"src": "folder/moved.txt", "dst": "copied.txt"}], "overwrite": False},
        ),
        ("delete", {"paths": ["folder"], "recursive": True, "force": False}),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed_results[:] = [cast(dict[str, Any], part.content) for part in returns]
        assert {"mkdir", "move", "copy", "delete"} <= {tool.name for tool in info.function_tools}
        assert info.instructions is not None
        assert '<tool-instruction name="move">' in info.instructions
        assert '<tool-instruction name="copy">' in info.instructions
        assert '<tool-instruction name="delete">' in info.instructions
        assert '<tool-instruction name="environment-shell">' not in info.instructions
        if len(returns) < len(calls):
            name, arguments = calls[len(returns)]
            yield {
                0: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"{name}-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration(shell_tools=False)),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    result: HarnessRunResult[Any] | None = None
    async with executable.stream(
        "mutate files",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    ) as run:
        async for item in run:
            if isinstance(item, HarnessEvent):
                if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "tool":
                    tool_events.append(item.event)
            else:
                result = item.result

    assert result is not None
    assert result.output_or_raise() == "done"
    assert [event.payload["tool_id"] for event in tool_events] == [
        "filesystem.mkdir",
        "filesystem.move",
        "filesystem.copy",
        "filesystem.remove",
    ]
    assert [event.payload["tool_call_id"] for event in tool_events] == [
        "mkdir-1",
        "move-1",
        "copy-1",
        "delete-1",
    ]
    assert [event.payload["value"]["changes"] for event in tool_events] == [
        [{"path": "folder", "action": "created", "destination": None}],
        [{"path": "source.txt", "action": "moved", "destination": "folder/moved.txt"}],
        [{"path": "folder/moved.txt", "action": "copied", "destination": "copied.txt"}],
        [{"path": "folder", "action": "deleted", "destination": None}],
    ]
    assert len(observed_results) == len(calls)
    assert all(item["ok"] is True for item in observed_results)
    assert not (tmp_path / "source.txt").exists()
    assert not (tmp_path / "folder").exists()
    assert (tmp_path / "copied.txt").read_text(encoding="utf-8") == "value"


async def test_file_edit_events_keep_called_tool_id_and_skip_content_noop(tmp_path: Path) -> None:
    (tmp_path / "edit.txt").write_text("value", encoding="utf-8")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        if not returns:
            name = "multi_edit"
            arguments = {
                "file_path": "edit.txt",
                "edits": [{"old_string": "value", "new_string": "changed"}],
            }
            tool_call_id = "multi-edit-one"
        elif len(returns) == 1:
            name = "edit"
            arguments = {
                "file_path": "edit.txt",
                "old_string": "changed",
                "new_string": "changed",
            }
            tool_call_id = "edit-noop"
        else:
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name=name,
                json_args=json.dumps(arguments),
                tool_call_id=tool_call_id,
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    async with executable.stream(
        "edit file",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    ) as run:
        async for item in run:
            if (
                isinstance(item, HarnessEvent)
                and isinstance(item.event, HarnessExtensionEvent)
                and item.event.kind == "tool"
            ):
                tool_events.append(item.event)

    assert (tmp_path / "edit.txt").read_text(encoding="utf-8") == "changed"
    assert len(tool_events) == 1
    assert tool_events[0].payload["tool_call_id"] == "multi-edit-one"
    assert tool_events[0].payload["tool_name"] == "multi_edit"
    assert tool_events[0].payload["tool_id"] == "filesystem.multi_edit"
    assert tool_events[0].payload["value"]["changes"] == [
        {"path": "edit.txt", "action": "modified", "destination": None},
    ]


async def test_file_change_event_keeps_only_confirmed_partial_batch_items(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("value", encoding="utf-8")
    tool_results: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        tool_results[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="delete",
                    json_args=json.dumps(
                        {
                            "paths": ["existing.txt", "missing.txt"],
                            "recursive": False,
                            "force": False,
                        }
                    ),
                    tool_call_id="delete-partial",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration(shell_tools=False)),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    async with executable.stream(
        "delete files",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    ) as run:
        async for item in run:
            if (
                isinstance(item, HarnessEvent)
                and isinstance(item.event, HarnessExtensionEvent)
                and item.event.kind == "tool"
            ):
                tool_events.append(item.event)

    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is False
    assert [item["ok"] for item in tool_results[0]["results"]] == [True, False]
    assert len(tool_events) == 1
    assert tool_events[0].payload["value"]["changes"] == [
        {"path": "existing.txt", "action": "deleted", "destination": None},
    ]


async def test_mixed_invalid_file_batch_fails_before_any_mutation(tmp_path: Path) -> None:
    model_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        if model_calls == 1:
            yield {
                0: DeltaToolCall(
                    name="mkdir",
                    json_args=json.dumps(
                        {
                            "paths": [
                                "/environment/missing/rejected",
                                "/workspace/must-not-exist",
                            ],
                            "parents": False,
                        }
                    ),
                    tool_call_id="mkdir-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 1}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration(shell_tools=False)),),
    )
    result = await executable.run(
        "mutate files",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert model_calls == 2
    assert not (tmp_path / "must-not-exist").exists()


async def test_copy_streams_across_bindings_when_shell_is_disabled(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "source.txt").write_text("cross-binding", encoding="utf-8")

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "copy"
        ]
        names = {tool.name for tool in info.function_tools}
        assert {"mkdir", "move", "copy", "delete"} <= names
        assert info.instructions is not None
        assert '<tool-instruction name="copy">' in info.instructions
        assert '<tool-instruction name="environment-shell">' not in info.instructions
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="copy",
                    json_args=json.dumps(
                        {
                            "pairs": [
                                {
                                    "src": "/workspace/source.txt",
                                    "dst": "/environment/shared/copied.txt",
                                }
                            ],
                            "overwrite": False,
                        }
                    ),
                    tool_call_id="copy-1",
                )
            }
        else:
            assert returns[0].content["ok"] is True
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration(shell_tools=False)),),
    )
    result = await executable.run(
        "copy",
        bindings=RunBindings.embedded(
            environment=_two_local_bindings(
                first_root,
                second_root,
                first_operations=frozenset({EnvironmentAction.FILE_COPY_SOURCE}),
                second_operations=frozenset({EnvironmentAction.FILE_COPY_DESTINATION}),
            ),
            capabilities=(_policy(),),
        ),
    )

    assert result.output_or_raise() == "done"
    assert (second_root / "copied.txt").read_text(encoding="utf-8") == "cross-binding"


async def test_view_attaches_common_environment_media_natively(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        seen.append(messages)
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/image.png",
                            "instructions": "Read visible text.",
                        }
                    ),
                    tool_call_id="view-image-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        HarnessAgentSpec(
            model_config=ModelConfiguration(capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING})),
        ),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "view",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    binaries = [
        item
        for call in seen
        for message in call
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
        for item in part.content
        if isinstance(item, BinaryContent)
    ]
    assert len(binaries) == 1
    assert binaries[0].data == b"\x89PNG"
    assert binaries[0].media_type == "image/png"


async def test_view_uses_run_scoped_understanding_when_active_model_lacks_native_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    requests: list[MediaUnderstandingRequest] = []
    tool_returns: list[ToolReturnPart] = []

    class UnderstandingProvider:
        async def understand(self, request: MediaUnderstandingRequest) -> MediaUnderstandingResult:
            requests.append(request)
            return MediaUnderstandingResult(text="Detected text: hello")

    monkeypatch.setenv("A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL", "test:must-not-be-resolved")

    def unexpected_inference(model: str):
        raise AssertionError(f"run provider must take precedence over environment model {model!r}")

    monkeypatch.setattr(file_media_module, "infer_model", unexpected_inference)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        tool_returns[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/image.png",
                            "instructions": "Read visible text.",
                        }
                    ),
                    tool_call_id="view-image-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "view",
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path),
            capabilities=(
                _policy(),
                FileMediaUnderstandingRunCapability(provider=UnderstandingProvider()),
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(requests) == 1
    assert requests[0].kind == "image"
    assert requests[0].media_type == "image/png"
    assert requests[0].source_bytes == b"\x89PNG"
    assert requests[0].source == EnvironmentPath(
        binding_id="binding-1",
        binding_revision=1,
        path="/image.png",
    )
    assert requests[0].source_name == "/workspace/image.png"
    assert requests[0].instructions == "Read visible text."
    assert len(tool_returns) == 1
    assert tool_returns[0].content == "Detected text: hello"


async def test_view_uses_environment_configured_default_understanding_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    understanding_calls: list[list[ModelMessage]] = []
    tool_returns: list[ToolReturnPart] = []

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        understanding_calls.append(messages)
        assert info.model_settings is not None
        assert info.model_settings["temperature"] == 0.2
        return ModelResponse(
            parts=[TextPart("Environment agent detected: hello")],
            model_name="vision-model",
            provider_name="function",
            usage=RequestUsage(input_tokens=5, output_tokens=3),
        )

    understanding_model = FunctionModel(understand)
    selected_models: list[str] = []

    def infer_understanding_model(model: str):
        selected_models.append(model)
        return understanding_model

    monkeypatch.setenv("A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL", "test:vision-model")
    monkeypatch.setenv(
        "A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS",
        '{"temperature": 0.2}',
    )
    monkeypatch.setattr(file_media_module, "infer_model", infer_understanding_model)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        tool_returns[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/image.png",
                            "instructions": "Read visible text.",
                        }
                    ),
                    tool_call_id="view-image-default-agent",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        HarnessAgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "view",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert selected_models == ["test:vision-model"]
    assert len(understanding_calls) == 1
    binary = next(
        item
        for message in understanding_calls[0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
        for item in part.content
        if isinstance(item, BinaryContent)
    )
    assert binary.data == b"\x89PNG"
    assert binary.media_type == "image/png"
    assert tool_returns[0].content == "Environment agent detected: hello"
    provider_record = next(record for record in result.usage_records if isinstance(record, ProviderUsageRecord))
    assert provider_record.source == "files.media_understanding"
    assert provider_record.tool_id == "filesystem.view"
    assert {measure.unit: measure.quantity for measure in provider_record.usage.measures} == {
        "requests": 1,
        "input_tokens": 5,
        "output_tokens": 3,
    }


async def test_view_records_nested_usage_when_understanding_output_retries_exhaust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    tool_returns: list[ToolReturnPart] = []

    def understand(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(
            parts=[TextPart("   ")],
            model_name="vision-model",
            provider_name="function",
            usage=RequestUsage(input_tokens=2, output_tokens=1),
        )

    monkeypatch.setenv("A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL", "test:vision-model")
    monkeypatch.setattr(file_media_module, "infer_model", lambda model: FunctionModel(understand))

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        tool_returns[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/image.png"}),
                    tool_call_id="view-image-invalid-output",
                )
            }
        else:
            yield "handled invalid media output"

    executable = HarnessBuilder().build(
        HarnessAgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "view",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "handled invalid media output"
    assert tool_returns[0].content == {
        "ok": False,
        "error": {
            "code": "media_understanding_response_invalid",
            "retry_hint": "dependency_change",
        },
    }
    provider_record = next(record for record in result.usage_records if isinstance(record, ProviderUsageRecord))
    assert provider_record.source == "files.media_understanding"
    assert provider_record.tool_id == "filesystem.view"
    assert provider_record.tool_call_id == "view-image-invalid-output"
    assert {measure.unit: measure.quantity for measure in provider_record.usage.measures} == {
        "requests": 3,
        "input_tokens": 6,
        "output_tokens": 3,
    }


async def test_view_reports_unavailable_understanding_as_an_ordinary_tool_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    for variable in (
        "A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL",
        "A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL",
        "A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL",
    ):
        monkeypatch.delenv(variable, raising=False)
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(messages)
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/image.png"}),
                    tool_call_id="view-image-unavailable",
                )
            }
        else:
            yield "handled unavailable media"

    executable = HarnessBuilder().build(
        HarnessAgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "view",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "handled unavailable media"
    assert len(calls) == 2
    second_request_parts = [part for message in calls[1] if isinstance(message, ModelRequest) for part in message.parts]
    assert not any(isinstance(part, RetryPromptPart) for part in second_request_parts)
    tool_result = next(part for part in second_request_parts if isinstance(part, ToolReturnPart))
    assert tool_result.content == {
        "ok": False,
        "error": {
            "code": "media_understanding_unavailable",
            "retry_hint": "dependency_change",
        },
    }


async def test_media_understanding_releases_revision_scope_before_model_execution() -> None:
    scope_open = False
    scope_released = asyncio.Event()
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    class RevisionFiles:
        async def stat(self, path: str) -> FileMetadata:
            assert scope_open
            return FileMetadata(path=path, kind="file", size=4, writable=False)

        async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
            del path, offset, length
            assert scope_open
            return b"\x89PNG"

    files = RevisionFiles()

    class Scopes:
        def select_files(self, path: str) -> FileScopeSelection:
            return FileScopeSelection(
                logical_path=path,
                resolved_path=EnvironmentPath(
                    binding_id="binding-1",
                    binding_revision=1,
                    path="/image.png",
                ),
                observed_generation="generation-1",
            )

        @asynccontextmanager
        async def open_files(self, selection: FileScopeSelection) -> AsyncGenerator[Any]:
            nonlocal scope_open
            del selection
            scope_open = True
            try:
                yield files
            finally:
                scope_open = False
                scope_released.set()

    class BlockingProvider:
        async def understand(self, request: MediaUnderstandingRequest) -> MediaUnderstandingResult:
            assert request.source == EnvironmentPath(
                binding_id="binding-1",
                binding_revision=1,
                path="/image.png",
            )
            assert not scope_open
            provider_started.set()
            await release_provider.wait()
            return MediaUnderstandingResult(text="detached analysis")

    async def record_provider_usage(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    context = cast(
        Any,
        SimpleNamespace(
            deps=SimpleNamespace(
                model_configuration=None,
                record_provider_usage=record_provider_usage,
            ),
            tool_call_id="view-detached-media",
        ),
    )
    toolset = FileToolset(
        cast(Any, files),
        file_scopes=Scopes(),
        media_understanding=BlockingProvider(),
    )

    task = asyncio.create_task(toolset.view(context, "/workspace/image.png"))
    await provider_started.wait()

    assert scope_released.is_set()
    assert not scope_open

    release_provider.set()
    result = await task
    assert result.return_value == "detached analysis"


async def test_exact_edits_are_agent_friendly_and_failed_batch_is_not_published(tmp_path: Path) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("alpha\nbeta\nbeta\n")
    observed: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="multi_edit",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/edit.txt",
                            "edits": [
                                {"old_string": "alpha", "new_string": "changed"},
                                {"old_string": "missing", "new_string": "never-written"},
                            ],
                        }
                    ),
                    tool_call_id="multi-edit-1",
                )
            }
        elif len(returns) == 1:
            assert target.read_text() == "alpha\nbeta\nbeta\n"
            yield {
                0: DeltaToolCall(
                    name="edit",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/edit.txt",
                            "old_string": "beta",
                            "new_string": "gamma",
                            "replace_all": True,
                        }
                    ),
                    tool_call_id="edit-2",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "edit",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert observed[0]["error"]["code"] == "environment_edit_not_found"
    assert observed[0]["error"]["details"] == {"edit_index": 2}
    assert observed[1]["ok"] is True
    assert target.read_text() == "alpha\ngamma\ngamma\n"


async def test_grep_returns_requested_context_at_file_boundaries(tmp_path: Path) -> None:
    (tmp_path / "context.txt").write_bytes(
        b"needle0 top\nbefore middle\nneedle1 middle\nafter middle\nneedle2 bottom\n"
    )
    observed: list[dict[str, Any]] = []
    requests = (
        {"pattern": "needle0", "context_lines": 0},
        {"pattern": "needle1", "context_lines": 1},
        {"pattern": "needle2", "context_lines": 2},
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        if not returns:
            yield {
                index: DeltaToolCall(
                    name="grep",
                    json_args=json.dumps(request),
                    tool_call_id=f"grep-context-{index}",
                )
                for index, request in enumerate(requests)
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "grep",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    matches = {match["matching_line"]: match for item in observed for match in item["matches"].values()}
    assert matches["needle0 top"]["context_start_line"] == 1
    assert matches["needle0 top"]["context"].splitlines() == ["needle0 top"]
    assert matches["needle1 middle"]["context_start_line"] == 2
    assert matches["needle1 middle"]["context"].splitlines() == [
        "before middle",
        "needle1 middle",
        "after middle",
    ]
    assert matches["needle2 bottom"]["context_start_line"] == 3
    assert matches["needle2 bottom"]["context"].splitlines() == [
        "needle1 middle",
        "after middle",
        "needle2 bottom",
    ]


async def test_explicit_file_offsets_survive_inner_model_recovery_attempts(tmp_path: Path) -> None:
    (tmp_path / "recovered.txt").write_text("one\ntwo\n")
    failed_once = False

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal failed_once
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/recovered.txt", "line_offset": 0, "line_limit": 1}),
                    tool_call_id="recover-read-1",
                )
            }
        elif len(returns) == 1 and not failed_once:
            failed_once = True
            raise RuntimeError("model stream disconnected")
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/recovered.txt",
                            "line_offset": returns[0]["line_offset"] + returns[0]["lines_read"],
                            "line_limit": 1,
                        }
                    ),
                    tool_call_id="recover-read-2",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )
    result = await executable.run(
        "write",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert (tmp_path / "recovered.txt").read_text() == "one\ntwo\n"
    assert result.usage.requests == 4


async def test_managed_dispatch_fails_stale_when_policy_wait_refreshes_binding(tmp_path: Path) -> None:
    (tmp_path / "value.txt").write_text("value")
    aggregate = _local_binding(tmp_path)
    replacement = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="dynamic-environment-test",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )
    refresh = EnvironmentTopologyRequest(
        topology_version=2,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=2,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=replacement,
            ),
        ),
        default_binding_id="binding-1",
    )

    class RefreshOnAuthorize:
        applied = False

        async def __call__(self, invocation, metadata, *, context):
            del invocation, metadata, context
            if not self.applied:
                self.applied = True
                await aggregate.controller.apply(refresh)
            return InvocationPolicyDecision.allow()

    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/value.txt"}),
                    tool_call_id="stat-refresh-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(
            environment=aggregate,
            capabilities=(InvocationPolicyCapability(evaluator=RefreshOnAuthorize()),),
        ),
    )

    assert result.output_or_raise() == "done"
    assert observed["ok"] is False
    assert observed["error"]["code"] == "environment_stale_binding"


async def test_managed_authorization_is_fenced_by_binding_revision(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path)
    run_bindings = RunBindings.embedded(environment=aggregate)
    replacement = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="dynamic-environment-test",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )
    refresh = EnvironmentTopologyRequest(
        topology_version=2,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=2,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=replacement,
            ),
        ),
        default_binding_id="binding-1",
    )

    async with aggregate.bind(run_id="run-1", instance=run_bindings.instance) as environment:
        await environment.activate()
        capability = _DynamicEnvironmentRunCapability(
            _configuration(),
            run_id="run-1",
            environment=environment,
            process_event_hooks=(),
        )
        resolver = capability._resource_resolver("filesystem.view")
        resources = await resolver(
            {"file_path": "/workspace/relative.txt"},
            context=cast(Any, SimpleNamespace(environment=environment)),
        )
        assert len(resources) == 1
        await aggregate.controller.apply(refresh)

        with pytest.raises(Exception) as stale_authorization:
            capability._assert_authorized_fence()
        assert getattr(stale_authorization.value, "code", None) == "environment_stale_binding"


async def test_managed_large_json_result_spills_for_the_run_and_is_cleaned(tmp_path: Path) -> None:
    def produce() -> dict[str, str]:
        return {"content": "x" * 4_000, "hint": "keep-this-field"}

    tool = HarnessTool(
        produce,
        harness_metadata=HarnessToolMetadata(
            tool_id="test.large-result",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=512,
                max_output_bytes=8 * 1024,
                overflow="spill",
                redact=True,
            ),
        ),
    )
    observed_path: str | None = None

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal observed_path
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {0: DeltaToolCall(name="produce", json_args="{}", tool_call_id="large-result-1")}
            return
        content = returns[-1]
        assert isinstance(content, dict)
        assert content["truncated"] is True
        assert content["output_bytes"] > 4_000
        result = content["result"]
        assert isinstance(result, dict)
        assert result["hint"] == "keep-this-field"
        observed_path = cast(str, content["output_file_path"])
        relative = observed_path.removeprefix("/workspace/")
        spilled = tmp_path / relative
        assert json.loads(spilled.read_text(encoding="utf-8")) == produce()
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(tools=[tool], id="large-result-tools"),
            DynamicEnvironmentCapability(_configuration()),
        ),
    )
    result = await executable.run(
        "produce",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert observed_path is not None
    assert not (tmp_path / observed_path.removeprefix("/workspace/")).exists()


async def test_unmanaged_large_json_result_uses_default_truncation(tmp_path: Path) -> None:
    def produce() -> dict[str, str]:
        return {"content": "x" * (300 * 1024), "hint": "native-tool"}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {0: DeltaToolCall(name="produce", json_args="{}", tool_call_id="native-large-result-1")}
            return
        content = returns[-1]
        assert isinstance(content, dict)
        assert content["truncated"] is True
        assert content["output_bytes"] > 300 * 1024
        assert content["output_file_path"] is None
        result = content["result"]
        assert isinstance(result, dict)
        assert result["hint"] == "native-tool"
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[produce], id="native-large-result-tools"),),
    )
    result = await executable.run(
        "produce",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path)),
    )

    assert result.output_or_raise() == "done"


async def test_model_error_projection_omits_internal_environment_details() -> None:
    async def fail() -> None:
        raise EnvironmentError(
            "internal provider detail",
            code="environment_unavailable",
            details={
                "binding_id": "binding-secret",
                "generation": "generation-secret",
                "reason_code": "provider-secret",
                "timeout_seconds": 3,
                "missing": ["files"],
            },
        )

    result = await FileToolset(cast(Any, SimpleNamespace()))._execute(
        "/failure",
        lambda files: fail(),
        lambda value: {},
    )
    assert result["ok"] is False
    assert result["error"]["details"] == {"timeout_seconds": 3, "missing": ["files"]}


async def test_empty_topology_tool_returns_typed_unavailable_result_after_policy_allow() -> None:
    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/missing"}),
                    tool_call_id="stat-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded(capabilities=(_policy(),)))

    assert result.output_or_raise() == "done"
    assert observed["ok"] is False
    assert observed["error"]["code"] == "environment_selection_invalid"


async def test_large_environment_result_is_bounded_without_retry_shaped_failure(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("\\" * (256 * 1024))
    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/large.txt", "line_limit": 1}),
                    tool_call_id="large-read-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "read",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert observed["ok"] is True
    assert observed["has_more"] is False
    assert observed["truncated_lines"] == [1]
    assert isinstance(observed["content"], str)
    assert len(observed["content"]) == 2_000


async def test_agent_spec_tool_retries_exhaust_once_without_environment_retry_loop() -> None:
    model_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        yield {
            0: DeltaToolCall(
                name="view",
                json_args="{}",
                tool_call_id=f"invalid-{model_calls}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 2}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded(capabilities=(_policy(),)))

    assert result.status == "failed"
    assert model_calls == 3
    assert result.usage.requests == 3
    retry_parts = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]
    assert len(retry_parts) == 2


@pytest.mark.parametrize("source_fails", [False, True])
async def test_cross_binding_copy_uses_plain_stream_completion(source_fails: bool) -> None:
    class SourceBackend:
        async def read_bytes_stream(self, path: str, *, chunk_size: int = 65_536):
            del path, chunk_size
            yield b"data"
            if source_fails:
                raise RuntimeError("source failed")

    class DestinationBackend:
        def __init__(self) -> None:
            self.data: bytes | None = None

        async def write_bytes_stream(self, path: str, stream, *, mode: str) -> FileWriteResult:
            del mode
            staged = bytearray()
            async for chunk in stream:
                staged.extend(chunk)
            self.data = bytes(staged)
            return FileWriteResult(
                path=path,
                bytes_written=len(staged),
                receipt=EnvironmentOperationReceipt(
                    binding_id="binding-destination",
                    binding_revision=1,
                    observed_generation="generation-destination",
                    operation_id="operation-1",
                    stage="completed",
                    outcome="succeeded",
                ),
            )

    source_backend = SourceBackend()
    destination_backend = DestinationBackend()
    topology_revision = 1
    prepared_revisions: list[int] = []

    def resolve(path: str) -> EnvironmentPath:
        source = path == "source"
        return EnvironmentPath(
            binding_id="binding-source" if source else "binding-destination",
            binding_revision=topology_revision,
            path=f"/{path}",
        )

    @asynccontextmanager
    async def prepare(selected: EnvironmentPath, action: EnvironmentAction) -> AsyncGenerator[Any]:
        nonlocal topology_revision
        source = action is EnvironmentAction.FILE_COPY_SOURCE
        prepared_revisions.append(selected.binding_revision)
        if source:
            topology_revision = 2
        yield _PreparedFile(
            selected=selected,
            observed_generation="generation-source" if source else "generation-destination",
            backend=source_backend if source else destination_backend,
            validate_result=lambda value: None,
        )

    files = VirtualFileOperator(resolve, prepare, lambda selected, path: path)
    if source_fails:
        with pytest.raises(RuntimeError, match="source failed"):
            await files.copy("source", "destination")
        assert destination_backend.data is None
    else:
        result = await files.copy("source", "destination")
        assert result.bytes_copied == 4
        assert destination_backend.data == b"data"
    assert prepared_revisions == [1, 1]


class _ApplyTopologyAfterResultPlugin(AbstractHarnessPlugin):
    def __init__(
        self,
        controller: Any,
        request: EnvironmentTopologyRequest,
        *,
        applied: asyncio.Event | None = None,
    ) -> None:
        self._controller = controller
        self._request = request
        self._applied = applied

    @property
    def plugin_id(self) -> str:
        return "apply-topology-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    await self._controller.apply(self._request)
                    if self._applied is not None:
                        self._applied.set()
                yield item

        return PluginRunResponse(iterate())


def _dynamic_local_request(root: Path) -> EnvironmentTopologyRequest:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="environment-event-test",
            root=DirectLocalRootConfiguration(path=root),
        )
    )
    return EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )


def _topology_context_events(items: list[Any]) -> list[HarnessEvent]:
    return [
        item
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.payload.get("type") == "environment_topology_changed"
    ]


async def test_topology_event_adapter_survives_model_recovery_boundary(tmp_path: Path) -> None:
    prompt_started = asyncio.Event()
    release_prompt = asyncio.Event()
    calls = 0

    async def prompt_factory(error: BaseException, attempt: int, messages: Any) -> str:
        del error, attempt, messages
        prompt_started.set()
        await release_prompt.wait()
        return "continue"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            raise RuntimeError("recoverable failure")
        yield "done"

    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            prompt_factory=prompt_factory,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        pending = asyncio.create_task(run.__anext__())
        await prompt_started.wait()
        await aggregate.controller.apply(_dynamic_local_request(tmp_path))
        observed = [await asyncio.wait_for(pending, timeout=2)]
        while not _topology_context_events(observed):
            observed.append(await asyncio.wait_for(run.__anext__(), timeout=2))
        topology_event = _topology_context_events(observed)[0]
        assert _topology_context_events([topology_event]) == [topology_event]
        release_prompt.set()
        remaining = [item async for item in run]

    assert calls == 2
    assert remaining[-1].result.output_or_raise() == "done"


async def test_topology_event_from_result_middleware_precedes_terminal_result(tmp_path: Path) -> None:
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        items = [item async for item in run]

    topology_events = _topology_context_events(items)
    assert len(topology_events) == 1
    assert items.index(topology_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"


class _TransformTopologyEventsPlugin(AbstractHarnessPlugin):
    def __init__(self) -> None:
        self.seen = 0
        self.order: list[str] = []

    @property
    def plugin_id(self) -> str:
        return "transform-topology-events"

    def get_ordering(self) -> PluginOrdering:
        return PluginOrdering(wraps=("apply-topology-after-result",))

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if _topology_context_events([item]):
                    assert isinstance(item, HarnessEvent)
                    assert isinstance(item.event, HarnessExtensionEvent)
                    self.seen += 1
                    self.order.append("event")
                    item = HarnessEvent(
                        thread_id=item.thread_id,
                        run_id=item.run_id,
                        sequence=item.sequence,
                        occurred_at=item.occurred_at,
                        event=HarnessExtensionEvent(
                            kind=item.event.kind,
                            payload={**item.event.payload, "observed_by_plugin": True},
                        ),
                    )
                elif isinstance(item, HarnessRunResult):
                    self.order.append("result")
                yield item

        return PluginRunResponse(iterate())


async def test_emitter_topology_events_pass_through_plugin_middleware(tmp_path: Path) -> None:
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    apply_plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
    )
    transform_plugin = _TransformTopologyEventsPlugin()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(apply_plugin, transform_plugin),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        items = [item async for item in run]

    topology_events = _topology_context_events(items)
    assert transform_plugin.seen == 1
    assert transform_plugin.order == ["event", "result"]
    assert len(topology_events) == 1
    assert topology_events[0].event.payload["observed_by_plugin"] is True
    assert items[-1].result.output_or_raise() == "done"


class _ApplyTopologyBurstAfterResultPlugin(AbstractHarnessPlugin):
    def __init__(self, controller: Any, count: int) -> None:
        self._controller = controller
        self._count = count

    @property
    def plugin_id(self) -> str:
        return "apply-topology-burst-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    for version in range(1, self._count + 1):
                        await self._controller.apply(
                            EnvironmentTopologyRequest(
                                topology_version=version,
                                bindings=(),
                                default_binding_id=None,
                            )
                        )
                yield item

        return PluginRunResponse(iterate())


async def test_terminal_drains_topology_burst_larger_than_emitter_capacity() -> None:
    change_count = 65
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=1, max_committed_changes=change_count)
    )
    plugin = _ApplyTopologyBurstAfterResultPlugin(aggregate.controller, change_count)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        items = [item async for item in run]

    assert len(_topology_context_events(items)) == change_count
    assert items[-1].result.output_or_raise() == "done"


async def test_terminal_waits_for_delayed_topology_adapter_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_started = asyncio.Event()
    release_adapter = asyncio.Event()
    topology_applied = asyncio.Event()
    original_adapter = execution_module._emit_environment_topology_events

    async def delayed_adapter(context: Any, drain: Any) -> None:
        adapter_started.set()
        await release_adapter.wait()
        await original_adapter(context, drain)

    monkeypatch.setattr(execution_module, "_emit_environment_topology_events", delayed_adapter)
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
        applied=topology_applied,
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )

    async def collect() -> list[Any]:
        async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
            return [item async for item in run]

    collect_task = asyncio.create_task(collect())
    await adapter_started.wait()
    await topology_applied.wait()
    await asyncio.sleep(0)
    assert not collect_task.done()
    release_adapter.set()
    items = await collect_task

    topology_events = _topology_context_events(items)
    assert len(topology_events) == 1
    assert items.index(topology_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"


async def test_direct_local_move_replaces_a_nonempty_directory_portably(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "new.txt").write_text("new")
    (destination / "old.txt").write_text("old")
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )

    await files.move("/source", "/destination", replace=True)

    assert not source.exists()
    assert (destination / "new.txt").read_text() == "new"
    assert not (destination / "old.txt").exists()
    assert not tuple(tmp_path.glob(".destination.a13n-replaced-*"))


async def test_dynamic_file_operations_accept_non_virtual_file_operator(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_bytes(b"provider-neutral\n")
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )
    environment = SimpleNamespace(files=files)
    toolset = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=environment), capabilities={}))

    result = await toolset.view(ctx, "/sample.txt")

    assert result == {
        "ok": True,
        "file_path": "/sample.txt",
        "content": "provider-neutral\n",
        "line_offset": 0,
        "lines_read": 1,
        "has_more": False,
        "truncated_lines": [],
    }


async def test_file_toolset_grep_delegates_all_filters_and_context_without_followup_reads() -> None:
    requests = []

    class SearchFiles:
        async def search_text(self, request):
            requests.append(request)
            return FileTextSearchResult(
                matches=(
                    FileTextMatch(
                        path="/src/value.py",
                        line=2,
                        text="needle",
                        context="before\nneedle\nafter\n",
                        context_start_line=1,
                    ),
                ),
                offset=request.offset,
                has_more=False,
            )

        async def read_text(self, *args, **kwargs):
            del args, kwargs
            pytest.fail("grep context must be returned by search_text")

    toolset = FileToolset(cast(Any, SearchFiles()))
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace()))

    result = await toolset.grep(
        ctx,
        "needle",
        root="/",
        include="**/*.py",
        include_ignored=False,
        include_hidden=True,
        context_lines=1,
        max_results=7,
        max_matches_per_file=3,
        max_files=4,
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.include == "**/*.py"
    assert request.ignore_mode == "git"
    assert request.include_hidden is True
    assert request.context_lines == 1
    assert request.max_matches == 7
    assert request.max_matches_per_file == 3
    assert request.max_files == 4
    assert result["matches"]["/src/value.py:2"]["context"] == "before\nneedle\nafter\n"


async def test_file_toolset_list_continues_after_a_fully_filtered_raw_page() -> None:
    class PagingFiles:
        async def list(self, path: str, *, offset: int, max_results: int, include_hidden: bool):
            del path, max_results, include_hidden
            if offset == 0:
                entries = tuple(
                    FileMetadata(
                        path=f"/ignored-{index}.tmp",
                        kind="file",
                        size=1,
                        writable=False,
                    )
                    for index in range(1_000)
                )
                return FileEntriesResult(entries=entries, offset=0, has_more=True)
            assert offset == 1_000
            return FileEntriesResult(
                entries=(FileMetadata(path="/visible.txt", kind="file", size=1, writable=False),),
                offset=offset,
                has_more=False,
            )

    toolset = FileToolset(cast(Any, PagingFiles()))
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace()))

    first = await toolset.ls(ctx, "/", ignore=("*.tmp",), max_results=-1)
    second = await toolset.ls(ctx, "/", ignore=("*.tmp",), offset=first["next_offset"], max_results=-1)

    assert first["entries"] == []
    assert first["has_more"] is True
    assert first["next_offset"] == 1_000
    assert first["disclosure"]["content_complete"] is False
    assert "next_offset" in first["disclosure"]["hint"]
    assert second["entries"] == [{"path": "/visible.txt", "kind": "file", "size": 1, "writable": False}]
    assert second["next_offset"] is None
    assert "disclosure" not in second


@requires_posix_process_groups
async def test_background_process_output_is_drained_once_and_auto_released(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path, process_output=True)
    run_bindings = RunBindings.embedded(environment=aggregate)

    async with aggregate.bind(run_id="run-drain", instance=run_bindings.instance) as environment:
        toolset = ShellToolset(
            shell=environment.shell,
            processes=environment.processes,
            outputs=environment.outputs,
        )
        ctx = cast(Any, _ShellRunContext())

        async def exercise() -> None:
            command = shlex.join(
                [
                    str(_PROCESS_EXECUTABLE),
                    "-c",
                    "import sys; sys.stdout.write('once-out'); sys.stderr.write('once-err')",
                ]
            )
            started = await toolset.shell_exec(ctx, command, background=True)
            assert started["ok"] is True
            assert started["background"] is True
            process_id = cast(str, started["process_id"])

            waited = await toolset.shell_wait(ctx, process_id, timeout_seconds=5)

            assert started["stdout"]["text"] + waited["stdout"]["text"] == "once-out"
            assert started["stderr"]["text"] + waited["stderr"]["text"] == "once-err"
            assert "output" not in started["stdout"]
            assert "output" not in waited["stdout"]

            released = await toolset.shell_wait(ctx, process_id, timeout_seconds=0)
            assert released["ok"] is False
            assert released["error"]["code"] == "environment_reference_invalid"

        await toolset.wrap_run(ctx, handler=exercise)
        await toolset.close()


@requires_posix_process_groups
async def test_background_process_output_continues_across_bounded_pages(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path, process_output=True)
    async with aggregate.bind(run_id="run-pages", instance=RunBindings.embedded().instance) as environment:
        toolset = ShellToolset(
            shell=environment.shell,
            processes=environment.processes,
            outputs=environment.outputs,
        )
        ctx = cast(Any, _ShellRunContext())

        async def exercise() -> None:
            expected = "x" * 200_000
            command = shlex.join([str(_PROCESS_EXECUTABLE), "-c", "import sys; sys.stdout.write('x' * 200000)"])
            started = await toolset.shell_exec(ctx, command, background=True)
            assert started["ok"] is True
            process_id = cast(str, started["process_id"])
            chunks = [started["stdout"]["text"]]

            while True:
                page = await toolset.shell_wait(ctx, process_id, timeout_seconds=5 if len(chunks) == 1 else 0)
                if page["ok"] is False:
                    assert page["error"]["code"] == "environment_reference_invalid"
                    break
                assert tool_output_size(cast(Any, page)) <= DEFAULT_TOOL_OUTPUT_CHARS
                chunks.append(page["stdout"]["text"])

            assert "".join(chunks) == expected
            assert len(chunks) > 2

        await toolset.wrap_run(ctx, handler=exercise)
        await toolset.close()


@requires_posix_process_groups
async def test_background_process_input_status_signal_and_kill(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path, process_output=True)
    async with aggregate.bind(run_id="run-controls", instance=RunBindings.embedded().instance) as environment:
        toolset = ShellToolset(
            shell=environment.shell,
            processes=environment.processes,
            outputs=environment.outputs,
        )
        ctx = cast(Any, _ShellRunContext())

        async def exercise() -> None:
            echo_command = shlex.join(
                [
                    str(_PROCESS_EXECUTABLE),
                    "-c",
                    "import sys; sys.stdout.write(sys.stdin.read())",
                ]
            )
            started = await toolset.shell_exec(ctx, echo_command, background=True)
            process_id = cast(str, started["process_id"])

            status = await toolset.shell_status(ctx)
            assert status["ok"] is True
            assert status["processes"][0]["process_id"] == process_id
            assert "stdout" not in status["processes"][0]
            assert "stderr" not in status["processes"][0]
            accepted = await toolset.shell_input(ctx, process_id, "hello", close_stdin=True)
            assert accepted == {"ok": True, "accepted_bytes": 5, "stdin_open": False}
            waited = await toolset.shell_wait(ctx, process_id, timeout_seconds=5)
            assert waited["stdout"]["text"] == "hello"

            sleep_command = shlex.join([str(_PROCESS_EXECUTABLE), "-c", "import time; time.sleep(30)"])
            signaled = await toolset.shell_exec(ctx, sleep_command, background=True)
            signal_id = cast(str, signaled["process_id"])
            signal_result = await toolset.shell_signal(ctx, signal_id, "interrupt")
            assert signal_result["ok"] is True
            await toolset.shell_wait(ctx, signal_id, timeout_seconds=5)

            killed = await toolset.shell_exec(ctx, sleep_command, background=True)
            kill_id = cast(str, killed["process_id"])
            kill_result = await toolset.shell_kill(ctx, kill_id)
            assert kill_result["ok"] is True
            assert kill_result["status"]["phase"] in {"signaled", "cancelled", "exited"}

        await toolset.wrap_run(ctx, handler=exercise)
        await toolset.close()


async def test_shell_toolset_composes_exactly_six_tools_over_bound_provider_ports(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path, process_output=True)
    run_bindings = RunBindings.embedded(environment=aggregate)

    async with aggregate.bind(run_id="run-1", instance=run_bindings.instance) as environment:
        toolset = ShellToolset(
            shell=environment.shell,
            processes=environment.processes,
            outputs=environment.outputs,
        )
        names = set(toolset.get_toolset().tools)

    assert names == {"shell_exec", "shell_wait", "shell_status", "shell_input", "shell_signal", "shell_kill"}


async def test_file_toolset_creates_nested_parents_and_returns_stable_missing_error(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )
    toolset = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=SimpleNamespace(files=files)), capabilities={}))

    written = await toolset.write(ctx, "/one/two/value.txt", "written")
    created = await toolset.edit(ctx, "/three/four/value.txt", "", "created")
    missing = await toolset.view(ctx, "/missing/ancestor/value.txt")

    assert written["ok"] is True
    assert created["ok"] is True
    assert (tmp_path / "one" / "two" / "value.txt").read_text() == "written"
    assert (tmp_path / "three" / "four" / "value.txt").read_text() == "created"
    assert missing["ok"] is False
    assert missing["error"]["code"] == "environment_not_found"


async def test_file_toolset_rechecks_authorization_between_compound_operations(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )
    revision = 1
    writes = 0

    class RefreshingFiles:
        async def mkdir(self, path: str, *, parents: bool, exist_ok: bool):
            nonlocal revision
            result = await files.mkdir(path, parents=parents, exist_ok=exist_ok)
            revision = 2
            return result

        async def write_text(self, path: str, text: str, *, mode: str):
            nonlocal writes
            writes += 1
            return await files.write_text(path, text, mode=cast(Any, mode))

    def guard() -> None:
        if revision != 1:
            raise EnvironmentError("Binding changed.", code="environment_stale_binding")

    toolset = FileToolset(cast(Any, RefreshingFiles()), execution_guard=guard)
    ctx = cast(Any, SimpleNamespace())

    result = await toolset.write(ctx, "/nested/value.txt", "must-not-write")

    assert result["ok"] is False
    assert result["error"]["code"] == "environment_stale_binding"
    assert writes == 0
    assert not (tmp_path / "nested" / "value.txt").exists()


async def test_file_toolset_pins_one_revision_across_compound_write() -> None:
    writes: list[tuple[int, str]] = []
    current_revision = 1

    class RevisionFiles:
        def __init__(self, revision: int) -> None:
            self.revision = revision

        async def mkdir(self, path: str, *, parents: bool, exist_ok: bool):
            nonlocal current_revision
            del path, parents, exist_ok
            if self.revision == 1:
                current_revision = 2
            return SimpleNamespace()

        async def write_text(self, path: str, text: str, *, mode: str):
            del mode
            writes.append((self.revision, path))
            return FileWriteResult(
                path=path,
                bytes_written=len(text.encode()),
                receipt=EnvironmentOperationReceipt(
                    binding_id="binding-1",
                    binding_revision=self.revision,
                    observed_generation=f"generation-{self.revision}",
                    operation_id=f"operation-{len(writes)}",
                    stage="completed",
                    outcome="succeeded",
                ),
            )

    revisions = {1: RevisionFiles(1), 2: RevisionFiles(2)}

    class Scopes:
        def select_files(self, path: str) -> FileScopeSelection:
            return FileScopeSelection(
                logical_path=path,
                resolved_path=EnvironmentPath(
                    binding_id="binding-1",
                    binding_revision=current_revision,
                    path=path,
                ),
                observed_generation=f"generation-{current_revision}",
            )

        @asynccontextmanager
        async def open_files(self, selection: FileScopeSelection) -> AsyncGenerator[Any]:
            yield revisions[selection.resolved_path.binding_revision]

    toolset = FileToolset(cast(Any, revisions[1]), file_scopes=Scopes())
    ctx = cast(Any, SimpleNamespace())

    first = await toolset.write(ctx, "/nested/first.txt", "first")
    second = await toolset.write(ctx, "/nested/second.txt", "second")

    assert first["ok"] is True
    assert second["ok"] is True
    assert writes == [(1, "/nested/first.txt"), (2, "/nested/second.txt")]


async def test_file_toolset_serializes_concurrent_exact_edits(tmp_path: Path) -> None:
    target = tmp_path / "value.txt"
    target.write_text("first\nsecond\n")
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )
    toolset = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=SimpleNamespace(files=files)), capabilities={}))

    first, second = await asyncio.gather(
        toolset.edit(ctx, "/value.txt", "first", "FIRST"),
        toolset.edit(ctx, "/value.txt", "second", "SECOND"),
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert target.read_text() == "FIRST\nSECOND\n"


async def test_direct_local_create_is_exclusive_under_concurrency(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )

    async def create(content: str) -> FileWriteResult | EnvironmentError:
        try:
            return await files.write_text("/value.txt", content, mode="create")
        except EnvironmentError as exc:
            return exc

    results = await asyncio.gather(create("first"), create("second"))

    assert sum(isinstance(result, FileWriteResult) for result in results) == 1
    errors = [result for result in results if isinstance(result, EnvironmentError)]
    assert len(errors) == 1
    assert errors[0].code == "environment_conflict"
    assert (tmp_path / "value.txt").read_text() in {"first", "second"}


async def test_large_exact_edit_transformation_runs_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "value.txt"
    target.write_text("before\n")
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )
    toolset = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=SimpleNamespace(files=files)), capabilities={}))
    started = threading.Event()
    original = file_toolset_module._apply_text_edits

    def slow_transform(content, edits, start_index=1):
        started.set()
        time.sleep(0.1)
        return original(content, edits, start_index)

    monkeypatch.setattr(file_toolset_module, "_apply_text_edits", slow_transform)
    edit_task = asyncio.create_task(toolset.edit(ctx, "/value.txt", "before", "after"))
    for _ in range(200):
        if started.is_set():
            break
        await asyncio.sleep(0.01)

    assert started.is_set()
    await asyncio.sleep(0.01)
    assert not edit_task.done()
    result = await edit_task
    assert result["ok"] is True
