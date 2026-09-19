from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import a13n_harness.environment.dynamic as dynamic_environment_module
import a13n_harness.execution as execution_module
import a13n_harness.toolsets.file_media as file_media_module
import a13n_harness.toolsets.files as file_toolset_module
import pytest
from a13n_environment import (
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness import (
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessModelCharacteristics,
    ModelCapability,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPath,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    create_empty_environment_runtime,
    create_environment_runtime,
)
from a13n_harness.environment.dynamic import _DynamicEnvironmentRunCapability
from a13n_harness.environment.files import (
    FileEntriesResult,
    FileMetadata,
    FileTextMatch,
    FileTextSearchResult,
    FileWriteResult,
)
from a13n_harness.environment.models import EnvironmentOperationReceipt
from a13n_harness.environment.providers import (
    EnvironmentRuntimeMount,
    FileScopeSelection,
)
from a13n_harness.environment.virtual_files import VirtualFileOperator, _PreparedFile
from a13n_harness.model_context import user_prompt_content
from a13n_harness.plugins import (
    AbstractHarnessPlugin,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
)
from a13n_harness.result import HarnessRunResult
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.toolsets import (
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
)
from a13n_harness.toolsets._scoped_files import ScopedFileAccess
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.output import (
    disclose_sequence_field,
)
from a13n_harness.toolsets.process_manager import _fit_stream_prefixes
from a13n_harness.toolsets.shell import ShellToolset
from a13n_harness.usage import ProviderUsageRecord
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

from .environment_helpers import (
    DirectLocalEnvironmentProviderBinding,
    DirectLocalFilePolicy,
)

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
        **updates,
    )


async def test_dynamic_environment_creates_run_owned_replacements_and_registers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacements: list[Any] = []

    class Replacement:
        async def _close_processes(self) -> None:
            return None

    def capture_run_capability(
        configuration: DynamicEnvironmentConfiguration,
        *,
        run_id: str,
        environment: Any,
    ) -> Replacement:
        del configuration, run_id, environment
        replacement = Replacement()
        replacements.append(replacement)
        return replacement

    monkeypatch.setattr(dynamic_environment_module, "_DynamicEnvironmentRunCapability", capture_run_capability)

    class Deps:
        def __init__(self) -> None:
            self.run_id = "run-1"
            self.environment = object()
            self.recorded: object | None = None
            self.cleanup: tuple[str, Any] | None = None

        def _run_capability(self, capability_id: str) -> None:
            del capability_id
            return None

        def _record_run_capability(self, capability_id: str, value: object) -> None:
            del capability_id
            self.recorded = value

        def _register_run_cleanup(self, owner_id: str, cleanup: Any) -> None:
            self.cleanup = (owner_id, cleanup)

    capability = DynamicEnvironmentCapability(_configuration())
    first = Deps()
    second = Deps()

    await capability.for_run(cast(Any, SimpleNamespace(deps=first)))
    await capability.for_run(cast(Any, SimpleNamespace(deps=second)))

    assert [first.recorded, second.recorded] == replacements
    assert first.cleanup == (
        "a13n.dynamic-environment.shell-processes",
        replacements[0]._close_processes,
    )
    assert second.cleanup == (
        "a13n.dynamic-environment.shell-processes",
        replacements[1]._close_processes,
    )


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _policy() -> InvocationPolicyCapability:
    return InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0)


def _local_mount(
    root: Path,
    *,
    environment_id: str = "dynamic-environment-test",
    operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
    process_output: bool = False,
    mount_path: str | None = None,
) -> EnvironmentRuntimeMount:
    return EnvironmentRuntimeMount(
        binding=DirectLocalEnvironmentProviderBinding(
            DirectLocalProviderConfiguration(
                root=DirectLocalRootConfiguration(path=root),
                shell_profiles=(
                    (DirectLocalShellProfile(profile_id="default", executable=Path("/bin/sh")),)
                    if process_output and sys.platform != "win32"
                    else ()
                ),
                allowed_executables=(frozenset({_PROCESS_EXECUTABLE}) if process_output else frozenset()),
            ),
            environment_id=environment_id,
        ),
        permission_ceiling=EnvironmentPermissionSet(operations=operations),
        working_directory="/",
        mount_path=mount_path,
    )


def _local_binding(
    root: Path,
    *,
    operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
    process_output: bool = False,
    mount_path: str | None = None,
):
    return create_environment_runtime(
        mounts={
            "local": _local_mount(
                root,
                operations=operations,
                process_output=process_output,
                mount_path=mount_path,
            )
        },
        default_mount="local",
    )


def _two_local_bindings(
    first_root: Path,
    second_root: Path,
    *,
    first_operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
    second_operations: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
    second_process_output: bool = False,
):
    return create_environment_runtime(
        mounts={
            "local": _local_mount(
                first_root,
                environment_id="dynamic-environment-1",
                operations=first_operations,
            ),
            "shared": _local_mount(
                second_root,
                environment_id="dynamic-environment-2",
                operations=second_operations,
                process_output=second_process_output,
            ),
        },
        default_mount="local",
    )


async def test_dynamic_mount_change_emits_an_independent_harness_context_event(tmp_path: Path) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await finish.wait()
        yield "done"

    aggregate = create_empty_environment_runtime()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    async with executable.stream("wait", bindings=RunBindings.embedded(environment=aggregate)) as run:
        pending = asyncio.create_task(run.__anext__())
        await started.wait()
        await aggregate.mount("local", _local_mount(tmp_path), make_default=True)
        item = await asyncio.wait_for(pending, timeout=2)
        while not (
            isinstance(item, HarnessEvent)
            and isinstance(item.event, HarnessExtensionEvent)
            and item.event.kind == "context"
            and item.event.payload.get("type") == "environment_changed"
        ):
            item = await asyncio.wait_for(run.__anext__(), timeout=2)
        assert item.event.payload == {
            "type": "environment_changed",
            "sequence": 1,
            "kind": "mounted",
            "name": "local",
            "previous_default": None,
            "current_default": "local",
        }
        finish.set()
        terminal = [event async for event in run][-1]
        assert terminal.result.output_or_raise() == "done"


@requires_posix_process_groups
async def test_mount_changes_refresh_the_environment_tool_surface_between_model_steps(tmp_path: Path) -> None:
    aggregate = create_empty_environment_runtime()
    observed_environment_tools: list[set[str]] = []

    def advance() -> str:
        return "advanced"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        completed_advances = sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "advance"
        )
        observed_environment_tools.append({tool.name for tool in info.function_tools if tool.name != "advance"})
        if completed_advances == 0:
            await aggregate.mount(
                "local",
                _local_mount(tmp_path, process_output=True),
                make_default=True,
            )
        elif completed_advances == 1:
            await aggregate.unmount("local")
        else:
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name="advance",
                json_args="{}",
                tool_call_id=f"advance-{completed_advances + 1}",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(_configuration()),
            Capability(tools=[advance], id="test-advance"),
        ),
    )
    result = await executable.run(
        "inspect changing mounts",
        bindings=RunBindings.embedded(environment=aggregate),
    )

    assert result.output_or_raise() == "done"
    assert observed_environment_tools[0] == set()
    assert {"view", "write", "shell_exec"} <= observed_environment_tools[1]
    assert observed_environment_tools[2] == set()


@pytest.mark.parametrize(
    ("operations", "expected_tools"),
    (
        (FILE_READ_ACTIONS, {"view", "ls", "glob", "grep"}),
        (
            FILE_ACTIONS,
            {"view", "write", "edit", "multi_edit", "mkdir", "move", "copy", "delete", "ls", "glob", "grep"},
        ),
        (
            frozenset(EnvironmentAction),
            {
                "view",
                "write",
                "edit",
                "multi_edit",
                "mkdir",
                "ls",
                "glob",
                "grep",
                "shell_exec",
                "shell_info",
                "shell_wait",
                "shell_input",
                "shell_signal",
            },
        ),
    ),
)
async def test_permission_ceiling_projects_the_corresponding_tool_surface(
    tmp_path: Path,
    operations: frozenset[EnvironmentAction],
    expected_tools: set[str],
) -> None:
    observed_tools: set[str] = set()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_tools.update(tool.name for tool in info.function_tools)
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
            environment=_local_binding(
                tmp_path,
                operations=operations,
                process_output=True,
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    assert observed_tools == expected_tools


@requires_posix_process_groups
async def test_environment_tool_filters_remove_disabled_tools_before_surface_assembly(tmp_path: Path) -> None:
    observed_tools: set[str] = set()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_tools.update(tool.name for tool in info.function_tools)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                _configuration(
                    file_tools=frozenset({"view"}),
                    shell_tools=frozenset({"shell_exec"}),
                )
            ),
        ),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path, process_output=True)),
    )

    assert result.output_or_raise() == "done"
    assert observed_tools == {"view", "shell_exec"}


async def test_mixed_mount_shell_does_not_hide_file_mutations_on_another_mount(tmp_path: Path) -> None:
    observed_tools: set[str] = set()
    (tmp_path / "workspace").mkdir()
    (tmp_path / "shared").mkdir()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_tools.update(tool.name for tool in info.function_tools)
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
            environment=_two_local_bindings(
                tmp_path / "workspace",
                tmp_path / "shared",
                first_operations=FILE_ACTIONS,
                second_operations=frozenset(EnvironmentAction),
                second_process_output=True,
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    assert {"shell_exec", "move", "copy", "delete"} <= observed_tools


async def test_capability_projects_stable_tools_and_one_bounded_fresh_mount_snapshot(tmp_path: Path) -> None:
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
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(
            environment=_local_binding(
                tmp_path,
                process_output=True,
                mount_path=tmp_path.as_posix(),
            )
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(calls) == 1
    messages, info = calls[0]
    names = {tool.name for tool in info.function_tools}
    assert {"view", "write", "edit", "multi_edit", "ls", "glob", "grep"} <= names
    assert "environment_read_text" not in names
    assert "shell_exec" in names
    assert {"shell_wait", "shell_input", "shell_signal"} <= names
    metadata = {
        tool.name: tool.metadata[HARNESS_TOOL_METADATA_KEY]
        for tool in info.function_tools
        if tool.metadata is not None and HARNESS_TOOL_METADATA_KEY in tool.metadata
    }
    assert "mkdir" in names
    assert {"move", "copy", "delete"}.isdisjoint(names)
    assert metadata["edit"].effects == frozenset({"read", "write"})
    assert metadata["shell_exec"].effects == frozenset({"read", "write", "delete", "execute", "external_communication"})
    shell_tool = next(tool for tool in info.function_tools if tool.name == "shell_exec")
    assert "background" not in shell_tool.parameters_json_schema["properties"]
    grep_tool = next(tool for tool in info.function_tools if tool.name == "grep")
    ignored_description = grep_tool.parameters_json_schema["properties"]["include_ignored"]["description"]
    assert "do not interpret repository ignore files" in ignored_description
    assert info.instructions is not None
    assert "Agent-wide tool timeout" in info.instructions
    assert '<tool-instruction name="view">' in info.instructions
    assert '<tool-instruction name="environment-shell">' in info.instructions
    assert '<tool-instruction name="copy">' not in info.instructions
    assert '<tool-instruction name="delete">' not in info.instructions
    mount_parts = [
        item.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
        if "Current Environment mounts" in item.content
    ]
    assert len(mount_parts) == 1
    assert '"default_mount":"local"' in mount_parts[0]
    assert '"name":"local"' in mount_parts[0]
    assert f'"root":"{tmp_path.as_posix()}"' in mount_parts[0]
    assert "/workspace" not in mount_parts[0]
    assert len(mount_parts[0].encode()) < 64 * 1024
    assert executable.definition.agent.tool_timeout is None


@requires_posix_process_groups
async def test_fresh_process_binding_adds_run_owned_process_tools(tmp_path: Path) -> None:
    calls: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        calls.append(info)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path, process_output=True)),
    )

    assert result.output_or_raise() == "done"
    names = {tool.name for tool in calls[0].function_tools}
    assert {"shell_exec", "shell_info", "shell_wait", "shell_input", "shell_signal"} <= names
    assert {"shell_status", "shell_kill"}.isdisjoint(names)
    shell_tool = next(tool for tool in calls[0].function_tools if tool.name == "shell_exec")
    assert "background" not in shell_tool.parameters_json_schema["properties"]
    assert "yield_time_seconds" in shell_tool.parameters_json_schema["properties"]


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
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    result: HarnessRunResult[Any] | None = None
    async with executable.stream(
        "mutate files",
        bindings=RunBindings.embedded(
            environment=_local_binding(
                tmp_path,
                operations=FILE_ACTIONS,
            ),
            capabilities=(_policy(),),
        ),
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
    from a13n_harness.toolsets.events import FileEditAppliedEvent

    applied = []
    tool_events: list[HarnessExtensionEvent] = []
    async with executable.stream(
        "edit file",
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    ) as run:
        async for item in run:
            if isinstance(item, HarnessEvent) and isinstance(item.event, FileEditAppliedEvent):
                applied.append(item.event)
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

    assert len(applied) == 1
    assert (applied[0].before, applied[0].after, applied[0].tool_call_id) == ("value", "changed", "multi-edit-one")


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
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    tool_events: list[HarnessExtensionEvent] = []
    async with executable.stream(
        "delete files",
        bindings=RunBindings.embedded(
            environment=_local_binding(
                tmp_path,
                operations=FILE_ACTIONS,
            ),
            capabilities=(_policy(),),
        ),
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


@pytest.mark.parametrize("invalid_path", ["/environment/missing/rejected", "/workspace/../rejected"])
async def test_mixed_invalid_file_batch_fails_before_any_mutation(tmp_path: Path, invalid_path: str) -> None:
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
                                "/workspace/must-not-exist",
                                invalid_path,
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
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "mutate files",
        bindings=RunBindings.embedded(
            environment=_local_binding(
                tmp_path,
                operations=FILE_ACTIONS,
            ),
            capabilities=(_policy(),),
        ),
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
        assert names == {"copy"}
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
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
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
            model_characteristics=HarnessModelCharacteristics(
                capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING})
            ),
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
    assert binaries[0].vendor_metadata == {"display": False}


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
            capabilities=(_policy(),),
            file_media_understanding=UnderstandingProvider(),
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(requests) == 1
    assert requests[0].kind == "image"
    assert requests[0].media_type == "image/png"
    assert requests[0].source_bytes == b"\x89PNG"
    assert requests[0].source.path == "/image.png"
    assert requests[0].source.mount_id.startswith("mount-")
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
            "message": "Media understanding could not complete. Check the configured media model and provider.",
            "details": {},
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
            "message": "Media understanding could not complete. Check the configured media model and provider.",
            "details": {},
            "retry_hint": "dependency_change",
        },
    }


async def test_media_understanding_releases_mount_scope_before_model_execution() -> None:
    scope_open = False
    scope_released = asyncio.Event()
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    class BindingVersionFiles:
        async def stat(self, path: str) -> FileMetadata:
            assert scope_open
            return FileMetadata(path=path, kind="file", size=4, writable=False)

        async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
            del path, offset, length
            assert scope_open
            return b"\x89PNG"

    files = BindingVersionFiles()

    class Scopes:
        async def resolve_files(self, path: str) -> FileScopeSelection:
            return self.select_files(path)

        def select_files(self, path: str) -> FileScopeSelection:
            return FileScopeSelection(
                logical_path=path,
                resolved_path=EnvironmentPath(
                    mount_id="mount-1",
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
                mount_id="mount-1",
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
                model_characteristics=None,
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
    assert observed[0]["error"]["details"] == {
        "edit_index": 2,
        "hint": "Read the current target and copy an exact old_string, including whitespace, before retrying the edit.",
    }
    assert observed[1]["ok"] is True
    assert target.read_text() == "alpha\ngamma\ngamma\n"


@pytest.mark.parametrize("file_root", [False, True])
async def test_grep_returns_requested_context_at_file_boundaries(tmp_path: Path, file_root: bool) -> None:
    (tmp_path / "context.txt").write_bytes(
        b"needle0 top\nbefore middle\nneedle1 middle\nafter middle\nneedle2 bottom\n"
    )
    observed: list[dict[str, Any]] = []
    requests = (
        {"pattern": "needle0", "context_lines": 0},
        {"pattern": "needle1", "context_lines": 1},
        {"pattern": "needle2", "context_lines": 2},
    )
    if file_root:
        requests = tuple({**request, "root": str(tmp_path / "context.txt")} for request in requests)

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
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path, mount_path=str(tmp_path)), capabilities=(_policy(),)
        ),
    )

    assert result.output_or_raise() == "done"
    assert all(item["ok"] for item in observed), observed
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


async def test_grep_schema_options_literal_search_and_actionable_errors(tmp_path: Path) -> None:
    (tmp_path / "context.py").write_text("a.b\naxb\nHELLO\n")
    observed: list[dict[str, Any]] = []
    requests = (
        {"pattern": "a.b", "regex": False, "include": "*.{py,rs}"},
        {"pattern": "a.b"},
        {"pattern": "hello", "case_sensitive": False},
        {"pattern": "absent"},
        {"pattern": "(", "regex": True},
        {"pattern": "a", "include": "{broken}"},
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        schema = next(tool for tool in info.function_tools if tool.name == "grep").parameters_json_schema
        assert schema["properties"]["regex"]["default"] is True
        assert schema["properties"]["case_sensitive"]["default"] is True
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
    assert [len(item["matches"]) for item in observed[:4]] == [1, 2, 1, 0]
    assert all(item["ok"] for item in observed[:4])
    for item, field, reason in zip(
        observed[4:], ("pattern", "include"), ("invalid_regex", "invalid_glob"), strict=True
    ):
        assert item["ok"] is False
        assert item["error"]["code"] == "environment_request_invalid"
        assert item["error"]["details"]["field"] == field
        assert item["error"]["details"]["reason"] == reason
        assert item["error"]["details"]["hint"]


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
            raise ConnectionResetError("model stream disconnected")
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


@pytest.mark.parametrize("change", ["same_root", "replacement", "default", "denied"])
@pytest.mark.parametrize("tool", ["view", pytest.param("shell_exec", marks=requires_posix_process_groups)])
async def test_managed_dispatch_selects_current_route_after_policy_wait(tmp_path: Path, change: str, tool: str) -> None:
    (tmp_path / "value.txt").write_text("value")
    aggregate = _local_binding(tmp_path, process_output=True)
    other = tmp_path / "other"
    other.mkdir()
    (other / "value.txt").write_text("new value")
    replacement = _local_mount(
        tmp_path if change == "same_root" else other,
        environment_id="dynamic-environment-test-replacement",
        process_output=True,
        operations=frozenset() if change == "denied" else frozenset(EnvironmentAction),
    )

    class RefreshOnAuthorize:
        applied = False

        async def __call__(self, invocation, metadata, *, context):
            del invocation, metadata, context
            if not self.applied:
                self.applied = True
                if change == "default":
                    await aggregate.mount("other", replacement)
                    await aggregate.set_default("other")
                else:
                    await aggregate.replace("local", replacement)
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
                    name=tool,
                    json_args=json.dumps(
                        {"file_path": "/workspace/value.txt"} if tool == "view" else {"command": "cat value.txt"}
                    ),
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
    if change == "denied":
        assert observed["ok"] is False
        assert observed["error"]["code"] == "environment_denied"
    else:
        content = observed["content"] if tool == "view" else observed["stdout"]["text"]
        assert content == ("value" if change == "same_root" else "new value")


@requires_posix_process_groups
async def test_managed_resources_follow_direct_mount_aliases_and_absolute_path_flavors(tmp_path: Path) -> None:
    roots = {
        "posix": tmp_path / "posix",
        "drive": tmp_path / "drive",
        "unc": tmp_path / "unc",
    }
    for root in roots.values():
        root.mkdir()
    runtime = create_environment_runtime(
        mounts={
            "posix": _local_mount(
                roots["posix"],
                process_output=True,
                environment_id="managed-resource-posix",
                mount_path="/native/project",
            ),
            "drive": _local_mount(
                roots["drive"],
                process_output=True,
                environment_id="managed-resource-drive",
                mount_path="C:/Users/Example/Project",
            ),
            "unc": _local_mount(
                roots["unc"],
                process_output=True,
                environment_id="managed-resource-unc",
                mount_path="//server/share/project",
            ),
        },
        default_mount="posix",
    )
    run_bindings = RunBindings.embedded(environment=runtime)

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=run_bindings.instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        capability = _DynamicEnvironmentRunCapability(
            _configuration(),
            run_id="run-1",
            environment=environment,
        )
        context = cast(Any, SimpleNamespace(environment=environment))
        shell_tool = capability._shell_toolset.get_toolset().tools["shell_exec"]
        shell_resources = shell_tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        drive_mount_id = environment.resolve_path("C:/Users/Example/Project").mount_id
        unc_mount_id = environment.resolve_path("//server/share/project").mount_id

        drive_binding = await shell_resources({"alias": "drive"}, context=context)
        drive_relative = await shell_resources(
            {"alias": "drive", "cwd": "./src//"},
            context=context,
        )
        drive_absolute = await shell_resources(
            {"alias": "drive", "cwd": "c:/users/example/project/./src/"},
            context=context,
        )
        unc_absolute = await shell_resources(
            {"alias": "unc", "cwd": "//SERVER/SHARE/project//src/"},
            context=context,
        )

        assert drive_binding[0].kind == "mount"
        assert drive_binding[0].identifier.startswith(f"{drive_mount_id}:")
        assert drive_relative[0].identifier.startswith(f"{drive_mount_id}:")
        assert drive_relative[0].identifier.endswith(":/src")
        assert drive_absolute == drive_relative
        assert unc_absolute[0].identifier.startswith(f"{unc_mount_id}:")
        assert unc_absolute[0].identifier.endswith(":/src")
        mismatch = await shell_resources(
            {"alias": "drive", "cwd": "//server/share/project/src"},
            context=context,
        )
        assert mismatch == ()
        with pytest.raises(EnvironmentError) as mismatch_error:
            environment.resolve_path("//server/share/project/src", alias="drive")
        assert mismatch_error.value.code == "environment_selection_invalid"


@pytest.mark.parametrize("tool", ["ls", pytest.param("shell_exec", marks=requires_posix_process_groups)])
@pytest.mark.parametrize(
    "path", ["/native/project/tmp/", "/native/project/./tmp//", "./tmp/", "/native/project/../tmp"]
)
async def test_managed_tools_accept_directory_paths_and_explain_invalid_paths(
    tmp_path: Path, path: str, tool: str
) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "value.txt").write_text("value", encoding="utf-8")
    observed: list[Any] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            arguments = {"path": path} if tool == "ls" else {"command": "pwd", "cwd": path, "yield_time_seconds": 10}
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(arguments), tool_call_id="tool-1")}
        else:
            observed.append(returns[-1].content)
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    runtime = create_environment_runtime(
        mounts={"project": _local_mount(tmp_path, mount_path="/native/project", process_output=tool == "shell_exec")},
        default_mount="project",
    )
    result = await executable.run("inspect directory", bindings=RunBindings.embedded(environment=runtime))
    assert result.output_or_raise() == "done"
    if ".." in path:
        assert observed[0]["ok"] is False
        assert observed[0]["error"]["code"] == "environment_request_invalid"
        assert observed[0]["error"]["details"]["reason"] == "invalid_path"
        assert "Parent traversal" in observed[0]["error"]["details"]["hint"]
    else:
        assert observed[0]["ok"] is True
        if tool == "ls":
            assert observed[0]["entries"][0]["path"] == "/native/project/tmp/value.txt"
        else:
            assert observed[0]["status"]["exit_code"] == 0
            assert Path(observed[0]["stdout"]["text"].strip()).resolve() == (tmp_path / "tmp").resolve()


@pytest.mark.parametrize("tool", ["write", "edit", "multi_edit"])
@pytest.mark.parametrize("suffix", ["/", "/.", "//./"])
@pytest.mark.parametrize("root", ["/native/project", "C:/Project", "//server/share/project"])
@pytest.mark.parametrize("nested", [True, False])
async def test_file_creation_derives_parent_from_normalized_path(
    tmp_path: Path, tool: str, suffix: str, root: str, nested: bool
) -> None:
    observed: list[Any] = []
    relative_path = "sub/value.txt" if nested else "value.txt"
    arguments: dict[str, Any] = {"file_path": f"{root}/./{relative_path}{suffix}"}
    if tool == "write":
        arguments["content"] = "created"
    elif tool == "edit":
        arguments.update(old_string="", new_string="created")
    else:
        arguments["edits"] = [{"old_string": "", "new_string": "created"}]

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(arguments), tool_call_id="create-1")}
        else:
            observed.append(returns[-1].content)
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    operations = (
        frozenset({EnvironmentAction.FILE_WRITE_TEXT})
        if tool == "write" and not nested
        else frozenset(EnvironmentAction)
    )
    runtime = create_environment_runtime(
        mounts={"project": _local_mount(tmp_path, mount_path=root, operations=operations)}, default_mount="project"
    )
    result = await executable.run("create file", bindings=RunBindings.embedded(environment=runtime))
    assert result.output_or_raise() == "done"
    assert observed[0]["ok"] is True
    assert (tmp_path / relative_path).is_file()
    assert (tmp_path / relative_path).read_text(encoding="utf-8") == "created"


async def test_resource_metadata_does_not_bind_execution_to_mount_incarnation(tmp_path: Path) -> None:
    aggregate = _local_binding(tmp_path)
    run_bindings = RunBindings.embedded(environment=aggregate)
    replacement = _local_mount(
        tmp_path,
        environment_id="dynamic-environment-test-replacement",
    )

    async with aggregate.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=run_bindings.instance,
        host_refs={},
    ) as environment:
        await aggregate._activate()
        capability = _DynamicEnvironmentRunCapability(
            _configuration(),
            run_id="run-1",
            environment=environment,
        )
        toolset = capability._file_toolset
        resolver = toolset.get_toolset().tools["write"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        resources = await resolver(
            {"file_path": "/workspace/relative.txt"},
            context=cast(Any, SimpleNamespace(environment=environment)),
        )
        assert len(resources) == 1
        await aggregate.replace("local", replacement)

        result = await toolset.write(cast(Any, None), "/workspace/relative.txt", "current operation")
        assert result["ok"] is True
        assert (tmp_path / "relative.txt").read_text() == "current operation"


@pytest.mark.parametrize("custom_directory", [False, True])
async def test_managed_large_json_result_spills_for_the_run_and_is_cleaned(
    tmp_path: Path, custom_directory: bool
) -> None:
    directory = f"{tmp_path.as_posix()}/tmp/tool-results" if custom_directory else None
    expected_directory = directory or f"{tmp_path.as_posix()}/.a13n/tmp/tool-results"

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
        assert observed_path.startswith(f"{expected_directory}/run-")
        assert json.loads(Path(observed_path).read_text(encoding="utf-8")) == produce()
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
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path, mount_path=tmp_path.as_posix()),
            tool_result_directory=directory,
            capabilities=(_policy(),),
        ),
    )

    assert result.output_or_raise() == "done"
    if custom_directory:
        assert not (tmp_path / ".a13n").exists()
    assert observed_path is not None
    assert not Path(observed_path).exists()


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
        bindings=RunBindings.embedded(environment=_local_binding(tmp_path, process_output=True)),
    )

    assert result.output_or_raise() == "done"


async def test_model_error_projection_omits_internal_environment_details() -> None:
    async def fail() -> None:
        raise EnvironmentError(
            "internal provider detail",
            code="environment_unavailable",
            details={
                "mount_id": "mount-secret",
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
    assert result["error"]["details"] == {
        "timeout_seconds": 3,
        "missing": ["files"],
        "hint": "Check Environment readiness with the Host. Reconcile any previously dispatched work before retrying.",
    }
    assert result["error"]["message"] == "The selected Environment is unavailable."


async def test_empty_environment_omits_environment_tools() -> None:
    observed_names: set[str] = set()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_names.update(tool.name for tool in info.function_tools)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert observed_names == set()


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
    assert observed["disclosure"]["content_complete"] is False
    assert "one-based" in observed["disclosure"]["hint"]
    assert "max_line_length" in observed["disclosure"]["hint"]
    assert "next_line_offset" not in observed
    assert isinstance(observed["content"], str)
    assert len(observed["content"]) == 2_000


@pytest.mark.parametrize("line_count", [160, 520])
@pytest.mark.parametrize("line_text", ["x" * 90, "中文𐐀" * 30, '\\"' * 45])
async def test_text_view_output_budget_continues_without_skipping_lines(
    tmp_path: Path, line_count: int, line_text: str
) -> None:
    content = "".join(f"line {index}: {line_text}\n" for index in range(line_count)) + "tail"
    (tmp_path / "notes.md").write_bytes(content.encode("utf-8"))
    pages: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        offset = 0
        if returns:
            page = returns[-1]
            assert isinstance(page, dict)
            pages.append(page)
            assert page["ok"] is True
            assert page["truncated_lines"] == []
            assert page["lines_read"] == len(page["content"].splitlines())
            assert content.startswith("".join(item["content"] for item in pages))
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 12_000
            if not page["has_more"]:
                assert "".join(item["content"] for item in pages) == content
                assert "next_line_offset" not in page
                yield "done"
                return
            offset = page["next_line_offset"]
            assert offset == page["line_offset"] + page["lines_read"]
            assert offset > page["line_offset"]
            assert page["content"].endswith("\n")
            assert page["disclosure"]["content_complete"] is False
            assert page["disclosure"]["output_file_path"] is None
            assert "next_line_offset" in page["disclosure"]["hint"]
        yield {
            0: DeltaToolCall(
                name="view",
                json_args=json.dumps({"file_path": "/workspace/notes.md", "line_offset": offset, "line_limit": 1000}),
                tool_call_id=f"read-page-{len(pages)}",
            )
        }

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
    assert len(pages) >= 2


@pytest.mark.parametrize("provider_budget", [9, 1000])
@pytest.mark.parametrize("line", ["abcd\n", "中文\n", "𐐀\r\n"])
async def test_text_view_batches_obey_actual_page_budget(tmp_path: Path, provider_budget: int, line: str) -> None:
    content = line * 7 + "tail"
    (tmp_path / "text").write_text(content, encoding="utf-8", newline="")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=provider_budget),
        mount_id="workspace",
        generation="test",
    )
    toolset = FileToolset(files)
    pages = []
    offset = 0
    for _ in range(10):
        page = await toolset._read_text_page(
            files, "/text", line_offset=offset, line_limit=1000, max_line_length=20_000, page_bytes=19
        )
        assert len(page.text.encode("utf-8")) <= 19
        assert page.lines_read == len(page.text.splitlines()) > 0
        assert not page.truncated_lines
        pages.append(page.text)
        offset += page.lines_read
        if not page.has_more:
            break
    else:
        pytest.fail("Text pagination made no bounded progress")
    assert "".join(pages) == content
    assert offset == 8


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
async def test_cross_mount_copy_uses_plain_stream_completion(source_fails: bool) -> None:
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
                    mount_id="mount-destination-1",
                    observed_generation="generation-destination",
                    operation_id="operation-1",
                    stage="completed",
                    outcome="succeeded",
                ),
            )

    source_backend = SourceBackend()
    destination_backend = DestinationBackend()
    current_incarnation = 1
    prepared_mount_ids: list[str] = []

    def resolve(path: str) -> EnvironmentPath:
        source = path == "source"
        kind = "source" if source else "destination"
        return EnvironmentPath(
            mount_id=f"mount-{kind}-{current_incarnation}",
            path=f"/{path}",
        )

    @asynccontextmanager
    async def prepare(selected: EnvironmentPath, action: EnvironmentAction) -> AsyncGenerator[Any]:
        nonlocal current_incarnation
        source = action is EnvironmentAction.FILE_COPY_SOURCE
        prepared_mount_ids.append(selected.mount_id)
        if source:
            current_incarnation = 2
        yield _PreparedFile(
            selected=selected,
            backend=source_backend if source else destination_backend,
            validate_result=lambda value: None,
            virtualize_path=lambda path: path,
        )

    files = VirtualFileOperator(resolve, prepare)
    if source_fails:
        with pytest.raises(RuntimeError, match="source failed"):
            await files.copy("source", "destination")
        assert destination_backend.data is None
    else:
        result = await files.copy("source", "destination")
        assert result.bytes_copied == 4
        assert destination_backend.data == b"data"
    assert prepared_mount_ids == ["mount-source-1", "mount-destination-1"]


class _MountAfterResultPlugin(AbstractHarnessPlugin):
    def __init__(self, runtime: Any, mount: EnvironmentRuntimeMount) -> None:
        self._runtime = runtime
        self._mount = mount
        self.error_code: str | None = None

    @property
    def plugin_id(self) -> str:
        return "mount-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    try:
                        await self._runtime.mount("local", self._mount, make_default=True)
                    except EnvironmentError as exc:
                        self.error_code = exc.code
                yield item

        return PluginRunResponse(iterate())


def _environment_change_events(items: list[Any]) -> list[HarnessEvent]:
    return [
        item
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.payload.get("type") == "environment_changed"
    ]


async def test_environment_change_event_adapter_survives_model_recovery_boundary(tmp_path: Path) -> None:
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
            raise ConnectionResetError("recoverable failure")
        yield "done"

    aggregate = create_empty_environment_runtime()
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
        await aggregate.mount("local", _local_mount(tmp_path), make_default=True)
        observed = [await asyncio.wait_for(pending, timeout=2)]
        while not _environment_change_events(observed):
            observed.append(await asyncio.wait_for(run.__anext__(), timeout=2))
        change_event = _environment_change_events(observed)[0]
        assert _environment_change_events([change_event]) == [change_event]
        release_prompt.set()
        remaining = [item async for item in run]

    assert calls == 2
    assert remaining[-1].result.output_or_raise() == "done"


async def test_mount_from_result_middleware_drains_before_terminal_result(tmp_path: Path) -> None:
    aggregate = create_empty_environment_runtime()
    plugin = _MountAfterResultPlugin(aggregate, _local_mount(tmp_path))

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

    change_events = _environment_change_events(items)
    assert plugin.error_code is None
    assert len(change_events) == 1
    assert change_events[0].event.payload["kind"] == "mounted"
    assert items.index(change_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"


class _TransformEnvironmentChangeEventsPlugin(AbstractHarnessPlugin):
    def __init__(self) -> None:
        self.seen = 0
        self.order: list[str] = []

    @property
    def plugin_id(self) -> str:
        return "transform-environment-change-events"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if _environment_change_events([item]):
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


async def test_environment_change_events_pass_through_plugin_middleware(tmp_path: Path) -> None:
    aggregate = create_empty_environment_runtime()
    transform_plugin = _TransformEnvironmentChangeEventsPlugin()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        await aggregate.mount("local", _local_mount(tmp_path), make_default=True)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(transform_plugin,),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        items = [item async for item in run]

    change_events = _environment_change_events(items)
    assert transform_plugin.seen == 1
    assert transform_plugin.order == ["event", "result"]
    assert len(change_events) == 1
    assert change_events[0].event.payload["observed_by_plugin"] is True
    assert items[-1].result.output_or_raise() == "done"


async def test_terminal_drains_mount_change_burst_larger_than_emitter_capacity(tmp_path: Path) -> None:
    change_count = 65
    aggregate = create_empty_environment_runtime()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        for index in range(change_count):
            if index % 2 == 0:
                await aggregate.mount(
                    "local",
                    _local_mount(tmp_path, environment_id=f"environment-burst-{index}"),
                    make_default=True,
                )
            else:
                await aggregate.unmount("local")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
        items = [item async for item in run]

    assert len(_environment_change_events(items)) == change_count
    assert items[-1].result.output_or_raise() == "done"


async def test_terminal_waits_for_delayed_environment_change_adapter_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_started = asyncio.Event()
    release_adapter = asyncio.Event()
    mount_applied = asyncio.Event()
    original_adapter = execution_module._emit_environment_change_events

    async def delayed_adapter(context: Any, drain: Any) -> None:
        adapter_started.set()
        await release_adapter.wait()
        await original_adapter(context, drain)

    monkeypatch.setattr(execution_module, "_emit_environment_change_events", delayed_adapter)
    aggregate = create_empty_environment_runtime()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        await aggregate.mount("local", _local_mount(tmp_path), make_default=True)
        mount_applied.set()
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    async def collect() -> list[Any]:
        async with executable.stream("start", bindings=RunBindings.embedded(environment=aggregate)) as run:
            return [item async for item in run]

    collect_task = asyncio.create_task(collect())
    await adapter_started.wait()
    await mount_applied.wait()
    await asyncio.sleep(0)
    assert not collect_task.done()
    release_adapter.set()
    items = await collect_task

    change_events = _environment_change_events(items)
    assert len(change_events) == 1
    assert items.index(change_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"


async def test_direct_local_move_preserves_nonempty_directory_on_rejected_replace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "new.txt").write_text("new")
    (destination / "old.txt").write_text("old")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
        generation="generation-1",
    )

    with pytest.raises(EnvironmentError):
        await files.move("/source", "/destination", replace=True)

    assert (source / "new.txt").read_text() == "new"
    assert (destination / "old.txt").read_text() == "old"
    assert not (destination / "new.txt").exists()
    assert not tuple(tmp_path.glob(".destination.a13n-replaced-*"))


async def test_dynamic_file_operations_accept_non_virtual_file_operator(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_bytes(b"provider-neutral\n")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
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


async def test_shell_toolset_is_foreground_only_without_process_actions(tmp_path: Path) -> None:
    runtime = _local_binding(
        tmp_path,
        operations=frozenset({EnvironmentAction.SHELL_EXEC}),
        process_output=True,
    )
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=bindings.instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        toolset = ShellToolset(environment)
        tools = toolset.get_toolset().tools

        assert set(tools) == {"shell_exec"}
        properties = tools["shell_exec"].function_schema.json_schema["properties"]
        assert "background" not in properties
        assert "yield_time_seconds" not in properties
        assert "not a command or process label" in properties["alias"]["description"]


async def test_shell_toolset_exposes_exact_run_owned_process_surface(tmp_path: Path) -> None:
    runtime = _local_binding(tmp_path, process_output=True)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=bindings.instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        toolset = ShellToolset(environment)
        tools = toolset.get_toolset().tools

        assert set(tools) == {"shell_exec", "shell_info", "shell_wait", "shell_input", "shell_signal"}
        properties = tools["shell_exec"].function_schema.json_schema["properties"]
        assert "background" not in properties
        assert "yield_time_seconds" in properties
        assert "not a command or process label" in properties["alias"]["description"]
        info_schema = tools["shell_info"].function_schema.json_schema
        assert "process_id" in info_schema["required"]
        assert "limit" not in info_schema["properties"]
        assert "not a command or process label" in info_schema["properties"]["alias"]["description"]
        signal_metadata = tools["shell_signal"].metadata[HARNESS_TOOL_METADATA_KEY]
        assert signal_metadata.effects == frozenset({"delete", "execute"})
        await toolset.close()


async def test_file_toolset_creates_nested_parents_and_returns_stable_missing_error(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
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


async def test_file_failures_distinguish_unmounted_existing_path_from_missing_mounted_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "sibling-worktree"
    sibling.mkdir()
    (sibling / "README.md").write_text("keep in place")
    runtime = _local_binding(root, mount_path=root.as_posix())
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}
    ) as environment:
        await runtime._activate()
        toolset = FileToolset(environment.files, file_scopes=environment)
        ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=environment), capabilities={}))
        outside = await toolset.ls(ctx, sibling.as_posix())
        missing = await toolset.view(ctx, "spec/harness-ui/README.md")
        assert outside["error"]["code"] == "environment_selection_invalid"
        assert outside["error"]["details"]["reason"] == "path_outside_mounts"
        assert "existence was not checked" in outside["error"]["details"]["hint"]
        assert "Do not move files or worktrees" in outside["error"]["details"]["hint"]
        assert missing["error"]["code"] == "environment_not_found"
        assert missing["error"]["details"]["reason"] == "path_not_found"
        assert "ls or glob" in missing["error"]["details"]["hint"]
        assert "not an outside-mount" in missing["error"]["details"]["hint"]
        with pytest.raises(EnvironmentError) as invalid_alias:
            environment.resolve_path("README.md", alias="invented")
        assert invalid_alias.value.details["reason"] == "mount_selection_unavailable"
    assert not tuple(root.iterdir())
    assert (sibling / "README.md").read_text() == "keep in place"


def test_file_failure_hints_preserve_specific_diagnostics_without_raw_provider_details() -> None:
    from a13n_harness.toolsets.files import _environment_error_result

    result = _environment_error_result(
        EnvironmentError(
            "private OS exception text",
            code="environment_not_found",
            details={"hint": "Check the selected source.", "reason": "specific_lookup", "private": "secret"},
        )
    )
    assert result == {
        "ok": False,
        "error": {
            "code": "environment_not_found",
            "message": "The selected resource was not found or is not visible.",
            "details": {"hint": "Check the selected source.", "reason": "specific_lookup"},
        },
    }


async def test_file_toolset_rechecks_authorization_between_compound_operations(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
        generation="generation-1",
    )
    mount_current = True
    writes = 0

    class RefreshingFiles:
        async def mkdir(self, path: str, *, parents: bool, exist_ok: bool):
            nonlocal mount_current
            result = await files.mkdir(path, parents=parents, exist_ok=exist_ok)
            mount_current = False
            return result

        async def write_text(self, path: str, text: str, *, mode: str):
            nonlocal writes
            writes += 1
            return await files.write_text(path, text, mode=cast(Any, mode))

    def guard() -> None:
        if not mount_current:
            raise EnvironmentError("Mount changed.", code="environment_stale_mount")

    toolset = FileToolset(cast(Any, RefreshingFiles()), execution_guard=guard)
    ctx = cast(Any, SimpleNamespace())

    result = await toolset.write(ctx, "/nested/value.txt", "must-not-write")

    assert result["ok"] is False
    assert result["error"]["code"] == "environment_stale_mount"
    assert writes == 0
    assert not (tmp_path / "nested" / "value.txt").exists()


async def test_file_toolset_pins_one_mount_incarnation_across_compound_write() -> None:
    writes: list[tuple[str, str]] = []
    current_mount_id = "mount-1"

    class MountFiles:
        def __init__(self, mount_id: str) -> None:
            self.mount_id = mount_id

        async def mkdir(self, path: str, *, parents: bool, exist_ok: bool):
            nonlocal current_mount_id
            del path, parents, exist_ok
            if self.mount_id == "mount-1":
                current_mount_id = "mount-2"
            return SimpleNamespace()

        async def write_text(self, path: str, text: str, *, mode: str):
            del mode
            writes.append((self.mount_id, path))
            return FileWriteResult(
                path=path,
                bytes_written=len(text.encode()),
                receipt=EnvironmentOperationReceipt(
                    mount_id=self.mount_id,
                    observed_generation=f"generation-{self.mount_id}",
                    operation_id=f"operation-{len(writes)}",
                    stage="completed",
                    outcome="succeeded",
                ),
            )

    mounts = {"mount-1": MountFiles("mount-1"), "mount-2": MountFiles("mount-2")}

    class Scopes:
        async def resolve_files(self, path: str) -> FileScopeSelection:
            return self.select_files(path)

        def select_files(self, path: str) -> FileScopeSelection:
            return FileScopeSelection(
                logical_path=path,
                resolved_path=EnvironmentPath(
                    mount_id=current_mount_id,
                    path=path,
                ),
                observed_generation=f"generation-{current_mount_id}",
            )

        @asynccontextmanager
        async def open_files(self, selection: FileScopeSelection) -> AsyncGenerator[Any]:
            yield mounts[selection.resolved_path.mount_id]

    toolset = FileToolset(cast(Any, mounts["mount-1"]), file_scopes=Scopes())
    ctx = cast(Any, SimpleNamespace())

    first = await toolset.write(ctx, "/nested/first.txt", "first")
    second = await toolset.write(ctx, "/nested/second.txt", "second")

    assert first["ok"] is True
    assert second["ok"] is True
    assert writes == [("mount-1", "/nested/first.txt"), ("mount-2", "/nested/second.txt")]


async def test_file_toolset_serializes_concurrent_exact_edits(tmp_path: Path) -> None:
    target = tmp_path / "value.txt"
    target.write_text("first\nsecond\n")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
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
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
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
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        mount_id="mount-1",
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


@requires_posix_process_groups
async def test_direct_mount_path_translates_shell_working_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    working_directory = project / "nested"
    working_directory.mkdir(parents=True)
    runtime = _local_binding(
        project,
        process_output=True,
        mount_path=project.as_posix(),
    )
    bindings = RunBindings.embedded(environment=runtime)

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=bindings.instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        toolset = ShellToolset(environment)
        ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(), emit=AsyncMock()))

        result = await toolset.shell_exec_foreground(
            ctx,
            "pwd",
            cwd=working_directory.as_posix(),
        )

        assert result["ok"] is True
        assert result["stdout"]["text"].strip() == working_directory.resolve().as_posix()
        ctx.emit.assert_awaited_once()
        assert ctx.emit.call_args.args[0].phase == "exited"


@requires_posix_process_groups
async def test_mixed_shell_mounts_dispatch_foreground_and_process_paths_per_alias(tmp_path: Path) -> None:
    (tmp_path / "local").mkdir()
    (tmp_path / "shared").mkdir()
    runtime = create_environment_runtime(
        mounts={
            "local": _local_mount(
                tmp_path / "local",
                environment_id="mixed-shell-local",
                operations=frozenset(
                    {
                        EnvironmentAction.SHELL_EXEC,
                        EnvironmentAction.OUTPUT_READ,
                        EnvironmentAction.OUTPUT_RELEASE,
                    }
                ),
                process_output=True,
            ),
            "shared": _local_mount(
                tmp_path / "shared",
                environment_id="mixed-shell-shared",
                process_output=True,
            ),
        },
        default_mount="local",
    )
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=bindings.instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        toolset = ShellToolset(environment)
        ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(), emit=AsyncMock()))

        foreground = await toolset.shell_exec(
            ctx,
            "printf foreground",
            cwd="/workspace",
            yield_time_seconds=0,
        )
        live = await toolset.shell_exec(
            ctx,
            "printf process; sleep 30",
            cwd="/environment/shared",
            yield_time_seconds=0,
        )

        assert foreground["ok"] is True
        assert foreground["stdout"]["text"] == "foreground"
        ctx.emit.assert_awaited_once()
        assert ctx.emit.call_args.args[0].exit_code == 0
        assert "process_id" not in foreground
        assert live["ok"] is True
        assert "process_id" in live
        await toolset.close()


async def test_spill_tracks_default_changes_and_owns_only_unique_leaves(tmp_path: Path) -> None:
    from a13n_harness.context import _ToolResultSpillStore

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    runtime = _two_local_bindings(first, second)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        context = cast(Any, SimpleNamespace(run_id="run-1", environment=env, tool_result_directory=None))
        store = _ToolResultSpillStore(context)
        other = _ToolResultSpillStore(context)
        first_path = await store.write(b"first", suffix=".txt")
        other_path = await other.write(b"other", suffix=".txt")
        assert first_path and other_path and first_path != other_path
        assert first_path.startswith("/environment/local/")
        relative = first_path.removeprefix("/environment/local/")
        sentinel = second / relative
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"not-owned")
        await runtime.set_default("shared")
        second_path = await store.write(b"second", suffix=".json")
        assert second_path and second_path.startswith("/environment/shared/")
        assert await env.files.read_bytes(first_path) == b"first"
        assert await env.files.read_bytes(second_path) == b"second"
        await store.close()
        assert not (first / relative).exists()
        assert sentinel.read_bytes() == b"not-owned"
        assert await env.files.read_bytes(other_path) == b"other"
        await other.close()
        assert await store.write(b"closed", suffix=".txt") is None


async def test_spill_cleanup_does_not_follow_replaced_mount(tmp_path: Path) -> None:
    from a13n_harness.context import _ToolResultSpillStore

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    runtime = _local_binding(first)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        store = _ToolResultSpillStore(
            cast(Any, SimpleNamespace(run_id="run-1", environment=env, tool_result_directory=None))
        )
        path = await store.write(b"original", suffix=".txt")
        assert path
        relative = path.removeprefix("/environment/local/")
        sentinel = second / relative
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"replacement")
        await runtime.replace("local", _local_mount(second))
        await store.close()
        assert (first / relative).read_bytes() == b"original"
        assert sentinel.read_bytes() == b"replacement"


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        ({EnvironmentAction.FILE_WRITE_TEXT}, {"write", "edit", "multi_edit"}),
        ({EnvironmentAction.FILE_LIST}, {"ls"}),
        ({EnvironmentAction.FILE_QUERY}, {"glob"}),
        ({EnvironmentAction.FILE_SEARCH_TEXT}, {"grep"}),
        ({EnvironmentAction.FILE_READ_BYTES}, set()),
        ({EnvironmentAction.FILE_STAT}, set()),
        ({EnvironmentAction.FILE_READ_TEXT}, {"view"}),
        ({EnvironmentAction.FILE_STAT, EnvironmentAction.FILE_READ_BYTES}, {"view"}),
        ({EnvironmentAction.FILE_PATCH_TEXT}, set()),
    ],
)
async def test_file_tool_surface_requires_one_valid_branch(actions, expected):
    assert FileToolset.available_names([frozenset(actions)]) == expected


async def test_file_surface_keeps_media_on_one_mount_but_allows_cross_mount_copy():
    assert (
        FileToolset.available_names(
            [frozenset({EnvironmentAction.FILE_STAT}), frozenset({EnvironmentAction.FILE_READ_BYTES})]
        )
        == frozenset()
    )
    assert FileToolset.available_names(
        [frozenset({EnvironmentAction.FILE_COPY_SOURCE}), frozenset({EnvironmentAction.FILE_COPY_DESTINATION})]
    ) == {"copy"}


@pytest.mark.parametrize("tool", ["write", "edit", "multi_edit"])
@pytest.mark.parametrize("nested", [False, True])
async def test_write_only_tools_allow_create_but_require_parent_action(tmp_path: Path, tool: str, nested: bool):
    from .test_content_capabilities import _one_tool_model, _tool_contents

    path = "nested/new.txt" if nested else "new.txt"
    arguments: dict[str, object] = {"file_path": path}
    if tool == "write":
        arguments["content"] = "new"
    elif tool == "edit":
        arguments.update(old_string="", new_string="new")
    else:
        arguments["edits"] = [{"old_string": "", "new_string": "new"}]
    # Even an existing parent requires mkdir when the tool actually calls it.
    (tmp_path / "nested").mkdir()
    seen = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(tool, arguments, seen=seen),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "create",
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path, operations=frozenset({EnvironmentAction.FILE_WRITE_TEXT})),
            capabilities=(_policy(),),
        ),
    )
    assert result.output_or_raise() == "done"
    content = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert content["ok"] is (not nested)
    if nested:
        assert content["error"]["code"] == "environment_denied"
        assert not (tmp_path / path).exists()
    else:
        assert (tmp_path / path).read_text() == "new"


@pytest.mark.parametrize("can_read", [False, True])
async def test_existing_edit_requires_byte_read_not_patch_action(tmp_path: Path, can_read: bool):
    from .test_content_capabilities import _one_tool_model, _tool_contents

    (tmp_path / "existing.txt").write_text("before")
    actions = {EnvironmentAction.FILE_WRITE_TEXT}
    if can_read:
        actions.add(EnvironmentAction.FILE_READ_BYTES)
    seen = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "edit", {"file_path": "existing.txt", "old_string": "before", "new_string": "after"}, seen=seen
        ),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "edit",
        bindings=RunBindings.embedded(
            environment=_local_binding(tmp_path, operations=frozenset(actions)),
            capabilities=(_policy(),),
        ),
    )
    assert result.output_or_raise() == "done"
    content = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert content["ok"] is can_read
    assert (tmp_path / "existing.txt").read_text() == ("after" if can_read else "before")


@pytest.mark.parametrize("failure", ["write", "cancel", "cleanup"])
async def test_spill_failure_keeps_owned_leaf_for_best_effort_cleanup(tmp_path: Path, monkeypatch, failure: str):
    from a13n_harness.context import _ToolResultSpillStore

    runtime = _local_binding(tmp_path, mount_path="/mounted")
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        store = _ToolResultSpillStore(
            cast(Any, SimpleNamespace(run_id="run-1", environment=env, tool_result_directory=None))
        )

        async def fail(*args, **kwargs):
            if failure == "cancel":
                raise asyncio.CancelledError
            raise RuntimeError("unavailable")

        if failure != "cleanup":
            monkeypatch.setattr(VirtualFileOperator, "write_bytes_stream", fail)
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await store.write(b"value", suffix=".txt")
        else:
            path = await store.write(b"value", suffix=".txt")
            if failure == "cleanup":
                assert path and path.startswith("/mounted/")
                monkeypatch.setattr(VirtualFileOperator, "remove", fail)
            else:
                assert path is None
        assert list((tmp_path / ".a13n/tmp/tool-results").iterdir())
        await store.close()
        assert bool(list((tmp_path / ".a13n/tmp/tool-results").iterdir())) is (failure == "cleanup")


@pytest.mark.parametrize("tool", ["write", "edit", "multi_edit"])
@pytest.mark.parametrize(
    ("mount_path", "file_path"),
    [
        ("/data", "/data/new.txt"),
        ("C:/", "C:/new.txt"),
        ("//server/share/", "//server/share/new.txt"),
        ("C:/Work", "c:/work/new.txt"),
    ],
)
async def test_explicit_write_only_mount_without_default_has_usable_tools(
    tmp_path: Path,
    tool: str,
    mount_path: str,
    file_path: str,
):
    from .test_content_capabilities import _one_tool_model, _tool_contents

    runtime = create_environment_runtime(
        mounts={
            "sink": _local_mount(
                tmp_path, mount_path=mount_path, operations=frozenset({EnvironmentAction.FILE_WRITE_TEXT})
            )
        },
        default_mount=None,
    )
    arguments: dict[str, object] = {"file_path": file_path}
    if tool == "write":
        arguments["content"] = "new"
    elif tool == "edit":
        arguments.update(old_string="", new_string="new")
    else:
        arguments["edits"] = [{"old_string": "", "new_string": "new"}]
    seen = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(tool, arguments, seen=seen),
        capabilities=(DynamicEnvironmentCapability(_configuration()),),
    )
    result = await executable.run(
        "create", bindings=RunBindings.embedded(environment=runtime, capabilities=(_policy(),))
    )
    assert result.output_or_raise() == "done"
    assert next(item for item in _tool_contents(seen) if isinstance(item, dict))["ok"] is True
    assert (tmp_path / "new.txt").read_text() == "new"


@pytest.mark.parametrize(
    ("name", "arguments", "paths"),
    [
        ("view", {"file_path": "input.txt"}, ("/input.txt",)),
        ("write", {"file_path": "output.txt"}, ("/output.txt",)),
        ("edit", {"file_path": "output.txt"}, ("/output.txt",)),
        ("multi_edit", {"file_path": "output.txt"}, ("/output.txt",)),
        ("ls", {"path": "folder"}, ("/folder",)),
        ("glob", {}, ("/",)),
        ("grep", {"root": "folder"}, ("/folder",)),
        ("mkdir", {"paths": ["one", "two", "one"]}, ("/one", "/two")),
        ("delete", {"paths": ["one", "two"]}, ("/one", "/two")),
        ("move", {"pairs": [{"src": "one", "dst": "two"}]}, ("/one", "/two")),
        ("copy", {"pairs": [{"src": "one", "dst": "two"}, {"src": "one", "dst": "three"}]}, ("/one", "/two", "/three")),
    ],
)
async def test_file_toolset_owns_resource_metadata_without_dynamic_capability(
    tmp_path: Path, name: str, arguments: dict[str, Any], paths: tuple[str, ...]
) -> None:
    runtime = _local_binding(tmp_path)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        tool = FileToolset(env.files, file_scopes=env).get_toolset().tools[name]
        resolver = tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        resources = await resolver(arguments, context=cast(Any, None))
        assert tuple(resource.approval_revision for resource in resources) == paths
        assert all(resource.namespace == "environment" and resource.kind == "file" for resource in resources)


@pytest.mark.parametrize("name", ["mkdir", "delete", "move", "copy"])
async def test_file_batch_resources_do_not_swallow_unresolved_endpoints(tmp_path: Path, name: str) -> None:
    runtime = _local_binding(tmp_path)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        tool = FileToolset(env.files, file_scopes=env).get_toolset().tools[name]
        resolver = tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        arguments = (
            {"paths": ["valid", "/environment/missing/path"]}
            if name in {"mkdir", "delete"}
            else {"pairs": [{"src": "valid", "dst": "/environment/missing/path"}]}
        )
        with pytest.raises(EnvironmentError) as error:
            await resolver(arguments, context=cast(Any, None))
        assert error.value.code == "environment_selection_invalid"


async def test_file_batch_resources_are_observations_without_execution_authority(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("source", "target")}
    for root in roots.values():
        root.mkdir()
    runtime = create_environment_runtime(
        mounts={name: _local_mount(root, environment_id=name) for name, root in roots.items()}, default_mount="source"
    )
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        toolset = FileToolset(env.files, file_scopes=env)
        tools = toolset.get_toolset().tools
        copy_resolver = tools["copy"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        view_resolver = tools["view"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        authorized = asyncio.Event()
        replaced = asyncio.Event()

        async def authorize_copy() -> None:
            resources = await copy_resolver(
                {"pairs": [{"src": "/environment/source/one", "dst": "/environment/target/two"}]},
                context=cast(Any, None),
            )
            assert len(resources) == 2
            authorized.set()
            await replaced.wait()
            toolset._guard_execution()
            current = await copy_resolver(
                {"pairs": [{"src": "/environment/source/one", "dst": "/environment/target/two"}]},
                context=cast(Any, None),
            )
            assert resources[0] == current[0]
            assert resources[1].identifier != current[1].identifier
            assert resources[1].approval_revision == current[1].approval_revision

        async def authorize_unaffected_read() -> None:
            await authorized.wait()
            await view_resolver({"file_path": "/environment/source/one"}, context=cast(Any, None))
            await runtime.replace("target", _local_mount(roots["target"], environment_id="new-target"))
            toolset._guard_execution()
            replaced.set()

        await asyncio.gather(authorize_copy(), authorize_unaffected_read())


@pytest.mark.parametrize("add_missing", [False, True])
async def test_unresolved_resource_metadata_does_not_retain_a_publication_fence(
    tmp_path: Path, add_missing: bool
) -> None:
    runtime = _local_binding(tmp_path)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        toolset = FileToolset(env.files, file_scopes=env)
        resolver = toolset.get_toolset().tools["write"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        assert await resolver({"file_path": "/environment/missing/one"}, context=cast(Any, None)) == ()
        if add_missing:
            await runtime.mount("missing", _local_mount(tmp_path, environment_id="new-mount"))
        else:
            await runtime.replace("local", _local_mount(tmp_path, environment_id="replacement"))
        result = await toolset.write(cast(Any, None), "/environment/missing/one", "current operation")
        if add_missing:
            assert result["ok"] is True
            assert (tmp_path / "one").read_text() == "current operation"
        else:
            assert result["ok"] is False
            assert result["error"]["code"] == "environment_selection_invalid"


async def test_file_resource_defaults_preserve_direct_scopes_and_explicit_overrides(tmp_path: Path) -> None:
    async def custom_resolver(arguments, *, context):
        return ()

    runtime = _local_binding(tmp_path)
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        for scopes in (
            None,
            SimpleNamespace(select_files=env.select_files, resolve_files=env.resolve_files, open_files=env.open_files),
        ):
            toolset = FileToolset(env.files, file_scopes=cast(Any, scopes))
            assert all(
                tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver is None
                for tool in toolset.get_toolset().tools.values()
            )

        calls: list[str] = []
        toolset = FileToolset(env.files, file_scopes=env, resource_resolver=lambda tool_id: custom_resolver)
        assert all(
            tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver is custom_resolver
            for tool in toolset.get_toolset().tools.values()
        )
        toolset._guard_execution()
        toolset = FileToolset(env.files, file_scopes=env, execution_guard=lambda: calls.append("guard"))
        resolver = toolset.get_toolset().tools["view"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        await resolver({"file_path": "one"}, context=cast(Any, None))
        await runtime.replace("local", _local_mount(tmp_path, environment_id="replacement"))
        toolset._guard_execution()
        assert calls == ["guard"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_file_scope_metadata_is_execution_local_and_restored_on_exit(tmp_path: Path, cancelled: bool) -> None:
    roots = {name: tmp_path / name for name in ("first", "second")}
    for root in roots.values():
        root.mkdir()
    runtime = create_environment_runtime(
        mounts={name: _local_mount(root, environment_id=name) for name, root in roots.items()},
        default_mount="first",
    )
    bindings = RunBindings.embedded(environment=runtime)
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        access = ScopedFileAccess(env.files, env)
        resolver = access.resource_resolver("file_path")
        assert resolver is not None
        await resolver({"file_path": "file.txt"}, context=cast(Any, None))
        assert not access.has_mount_root_parent("file.txt")
        first = access.resolved_path("file.txt")
        try:
            async with access.scope("file.txt"):
                assert access.has_mount_root_parent("file.txt")
                await runtime.set_default("second")
                assert access.resolved_path("file.txt") == first
                async with access.scope("file.txt"):
                    assert access.resolved_path("file.txt") != first
                assert access.resolved_path("file.txt") == first
                if cancelled:
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            assert cancelled
        assert not access.has_mount_root_parent("file.txt")
        assert access.resolved_path("file.txt") != first
        assert not access.has_mount_root_parent("file.txt")
