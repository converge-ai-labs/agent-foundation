from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from converge_agent_harness import (
    BoundEnvironment,
    BoundProcessHandle,
    CompactionCapability,
    CompactionPolicy,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    DocumentConversionRequest,
    DocumentConversionResult,
    DocumentsCapability,
    DocumentsRunCapability,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentSkillSource,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    FileContextCapability,
    FileContextConfiguration,
    HandoffCapability,
    HarnessBuilder,
    HarnessRunResultEvent,
    MediaCapability,
    MediaReadRequest,
    MediaResource,
    MediaRunCapability,
    MonitoredProcessCapability,
    MonitoredProcessNotification,
    MonitoredProcessRunCapability,
    RunBindings,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
    SkillManager,
    SkillsCapability,
    UserInteractionCapability,
    WebCapability,
    WebRequest,
    WebResponse,
    WebRunCapability,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
    WorkingStateCapability,
    create_environment_run_binding,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio

_EXPECTED_TOOLS = {
    "ask_user_question",
    "download",
    "edit",
    "environment_port_inspect",
    "environment_port_wait",
    "environment_process_close_stdin",
    "environment_process_inspect",
    "environment_process_kill",
    "environment_process_monitor",
    "environment_process_read_output",
    "environment_process_release",
    "environment_process_signal",
    "environment_process_start",
    "environment_process_status",
    "environment_process_wait",
    "environment_process_write_stdin",
    "environment_shell_exec",
    "fetch",
    "glob",
    "grep",
    "ls",
    "multi_edit",
    "note",
    "note_get",
    "office_to_markdown",
    "pdf_convert",
    "read_media",
    "scrape",
    "search",
    "summarize",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "view",
    "write",
}


class _MediaReader:
    async def read(self, request: MediaReadRequest) -> MediaResource:
        raise AssertionError(f"unexpected media request: {request.url}")


class _DocumentConverter:
    async def convert(self, request: DocumentConversionRequest) -> DocumentConversionResult:
        raise AssertionError(f"unexpected document request: {request.source_name}")


class _WebPolicy:
    async def authorize(self, url: str, *, purpose: Any) -> None:
        del url, purpose


class _WebClient:
    async def request(self, request: WebRequest, *, policy: Any) -> WebResponse:
        del policy
        raise AssertionError(f"unexpected Web request: {request.url}")


class _WebSearchProvider:
    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        raise AssertionError(f"unexpected Web search: {request.query}")


class _WebScrapeProvider:
    async def scrape(self, request: WebScrapeRequest, *, policy: Any) -> WebScrapeResult:
        del policy
        raise AssertionError(f"unexpected Web scrape: {request.url}")


class _Monitor:
    def __init__(self) -> None:
        self.closed = False

    async def register(
        self,
        *,
        process: BoundProcessHandle,
        reference: str,
        environment: BoundEnvironment,
    ) -> None:
        del process, reference, environment
        raise AssertionError("unexpected monitored process registration")

    async def pending(self) -> Sequence[MonitoredProcessNotification]:
        return ()

    async def acknowledge(self, notification: MonitoredProcessNotification) -> None:
        del notification

    async def close(self) -> None:
        self.closed = True


def _environment(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="capability-integration",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
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
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _bindings(root: Path, monitor: _Monitor) -> RunBindings:
    return RunBindings.local(
        environment=_environment(root),
        metadata={"tenant": "integration-test"},
        capabilities=(
            MonitoredProcessRunCapability(monitor=monitor),
            MediaRunCapability(reader=_MediaReader()),
            DocumentsRunCapability(converter=_DocumentConverter()),
            WebRunCapability(
                client=_WebClient(),
                policy=_WebPolicy(),
                search_provider=_WebSearchProvider(),
                scrape_provider=_WebScrapeProvider(),
            ),
        ),
    )


def _definition_capabilities():
    skill_manager = SkillManager((EnvironmentSkillSource("workspace", ("/workspace/.agents/skills",)),))
    return (
        DynamicEnvironmentCapability(
            DynamicEnvironmentConfiguration(
                file_tools=True,
                shell_tools=True,
                process_tools=True,
                port_tools=True,
                max_reference_entries=64,
            )
        ),
        RuntimeContextCapability(RuntimeContextConfiguration(metadata_keys=("tenant",))),
        FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",), required=True)),
        HandoffCapability(),
        CompactionCapability(
            CompactionPolicy(trigger_tokens=1_000_000, target_tokens=500_000, preserve_recent_user_turns=1)
        ),
        SkillsCapability(skill_manager),
        WorkingStateCapability(),
        UserInteractionCapability(),
        MonitoredProcessCapability(),
        MediaCapability(),
        DocumentsCapability(),
        WebCapability(),
    )


def _request_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )


async def test_core_capabilities_compose_through_run_and_stream(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Canonical file context.", encoding="utf-8")
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review the current implementation.\n---\n\n# Review\n",
        encoding="utf-8",
    )

    observed_tools: list[set[str]] = []
    observed_request_text: list[str] = []
    observed_instructions: list[str] = []

    async def model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        observed_tools.append({tool.name for tool in info.function_tools})
        observed_request_text.append(_request_text(messages))
        observed_instructions.append(str(info.instructions))
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", name="capability-integration"),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=_definition_capabilities(),
    )

    run_monitor = _Monitor()
    run_result = await executable.run("Compose capabilities", bindings=_bindings(tmp_path, run_monitor))
    assert run_result.output_or_raise() == "done"
    assert run_monitor.closed is True

    stream_monitor = _Monitor()
    async with executable.stream("Compose capabilities", bindings=_bindings(tmp_path, stream_monitor)) as stream:
        events = [event async for event in stream]
    assert isinstance(events[-1], HarnessRunResultEvent)
    assert events[-1].result.output_or_raise() == "done"
    assert stream.result is events[-1].result
    assert stream_monitor.closed is True

    assert observed_tools == [_EXPECTED_TOOLS, _EXPECTED_TOOLS]
    assert all("<runtime-context" in text for text in observed_request_text)
    assert all("<file-context" in text and "Canonical file context." in text for text in observed_request_text)
    assert all("<available-skills>" in instructions for instructions in observed_instructions)
    assert all("Review the current implementation." in instructions for instructions in observed_instructions)
