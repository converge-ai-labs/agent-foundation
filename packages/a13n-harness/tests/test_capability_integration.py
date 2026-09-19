from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import (
    HarnessBuilder,
    HarnessRunResultEvent,
    RunBindings,
)
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    DocumentConversionRequest,
    DocumentConversionResult,
    DocumentsCapability,
    FileContextCapability,
    FileContextConfiguration,
    FileSkillSource,
    HandoffCapability,
    MediaCapability,
    MediaReadRequest,
    MediaResource,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
    SkillManager,
    SkillsCapability,
    UserInteractionCapability,
    WebBinding,
    WebCapability,
    WebConfiguration,
    WebRequest,
    WebResponse,
    WebScrapeBackendBinding,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchBackendBinding,
    WebSearchConfiguration,
    WebSearchRequest,
    WebSearchResponse,
    WorkingStateCapability,
)
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    create_environment_runtime,
)
from a13n_harness.environment.providers import (
    EnvironmentRuntimeMount,
)
from a13n_harness.model_context import user_prompt_content
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .environment_helpers import DirectLocalEnvironmentProviderBinding

pytestmark = pytest.mark.anyio

_EXPECTED_TOOLS = {
    "ask_user_question",
    "download",
    "edit",
    "shell_exec",
    "shell_info",
    "shell_wait",
    "shell_input",
    "shell_signal",
    "fetch",
    "glob",
    "grep",
    "ls",
    "mkdir",
    "multi_edit",
    "note_delete",
    "note_get",
    "note_write",
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


def _environment(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
            shell_profiles=(DirectLocalShellProfile(profile_id="default", executable=Path(sys.executable).resolve()),),
            allowed_executables=frozenset({Path(sys.executable).resolve()}),
        ),
        environment_id="capability-integration",
    )
    return create_environment_runtime(
        mounts={
            "local": EnvironmentRuntimeMount(
                binding=provider,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="local",
    )


def _bindings(root: Path) -> RunBindings:
    return RunBindings.embedded(
        environment=_environment(root),
        metadata={"tenant": "integration-test"},
        media_reader=_MediaReader(),
        document_converter=_DocumentConverter(),
        web=WebBinding(
            client=_WebClient(),
            policy=_WebPolicy(),
            search_backends=(WebSearchBackendBinding("default", _WebSearchProvider()),),
            scrape_backends=(WebScrapeBackendBinding("default", _WebScrapeProvider()),),
        ),
    )


def _definition_capabilities():
    skill_manager = SkillManager((FileSkillSource("workspace", ("/workspace/.agents/skills",)),))
    return (
        DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
        RuntimeContextCapability(RuntimeContextConfiguration(metadata_keys=("tenant",))),
        FileContextCapability(FileContextConfiguration(paths=("/workspace/AGENTS.md",), required=True)),
        HandoffCapability(),
        CompactionCapability(CompactionPolicy(trigger_tokens=1_000_000)),
        SkillsCapability(skill_manager),
        WorkingStateCapability(),
        UserInteractionCapability(),
        MediaCapability(),
        DocumentsCapability(),
        WebCapability(WebConfiguration(search=WebSearchConfiguration(mode="host"))),
    )


def _request_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(
        item.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
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

    executable = HarnessBuilder().build(
        AgentSpec(name="capability-integration"),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=_definition_capabilities(),
    )

    run_result = await executable.run("Compose capabilities", bindings=_bindings(tmp_path))
    assert run_result.output_or_raise() == "done"

    async with executable.stream("Compose capabilities", bindings=_bindings(tmp_path)) as stream:
        events = [event async for event in stream]
    assert isinstance(events[-1], HarnessRunResultEvent)
    assert events[-1].result.output_or_raise() == "done"
    assert stream.result is events[-1].result

    assert observed_tools == [_EXPECTED_TOOLS, _EXPECTED_TOOLS]
    assert all("<runtime-context" in text for text in observed_request_text)
    assert all("<file-context" in text and "Canonical file context." in text for text in observed_request_text)
    assert all("<available-skills>" in instructions for instructions in observed_instructions)
    assert all("Review the current implementation." in instructions for instructions in observed_instructions)
    assert all('<tool-instruction name="view">' in instructions for instructions in observed_instructions)
    assert all('<tool-instruction name="environment-shell">' in instructions for instructions in observed_instructions)
    assert all('<tool-instruction name="copy">' not in instructions for instructions in observed_instructions)
    assert all('<tool-instruction name="delete">' not in instructions for instructions in observed_instructions)
