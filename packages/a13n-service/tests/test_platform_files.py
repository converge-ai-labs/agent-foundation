"""Builtin files use ordinary Environment routing and the complete Skills lifecycle."""

import json
import re
from contextlib import asynccontextmanager
from functools import partial
from tempfile import TemporaryDirectory

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessExtensionEvent, RunBindings
from a13n_harness.capabilities import SkillManager, SkillsCapability
from a13n_harness.environment import DynamicEnvironmentCapability
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from a13n_service.app import _registry
from a13n_service.distribution import OSS
from a13n_service.resources.agents.composer import configuration
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.toolsets import environment_configuration
from a13n_service.runs.host import _environment
from a13n_service.runs.platform_files import INSTANCE_ROOT, ROOT, SOURCE, _mount, open_platform_files
from a13n_service.settings import Settings
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def startup(settings=None):
    settings = settings or Settings()
    async with open_platform_files(_registry(OSS, settings), settings) as files:
        yield files.mounts


async def test_local_mounts_schema_references_and_permissions():
    bindings = RunBindings.embedded()
    async with startup() as mounts:
        environment = create_environment_runtime(mounts=mounts)
        async with environment.bind(
            thread_id="thread-test", run_id="run-test", instance=bindings.instance, host_refs={}
        ) as bound:
            assert bound.snapshot.default_mount is None
            catalog = await SkillManager((SOURCE,)).scan_environment(environment=bound)
            assert [item.name for item in catalog.items] == ["platform-operations"]
            files = bound.files
            schema = await files.read_text(f"{ROOT}/schemas/agent-config.json", line_limit=10000)
            assert json.loads(schema.text) == AgentConfig.model_json_schema()
            document = await files.read_text(f"{ROOT}/SKILL.md")
            for reference in re.findall(r"\]\(([^)]+)\)", document.text):
                path = reference if reference.startswith("/") else f"{ROOT}/{reference}"
                assert (await files.stat(path)).kind == "file"
            first = await files.read_text(f"{ROOT}/SKILL.md", line_limit=1)
            assert first.text == "---\n" and first.has_more
            second = await files.read_text(f"{ROOT}/SKILL.md", line_offset=1, line_limit=1)
            assert second.text == "name: platform-operations\n"
            assert (await files.query(FileQueryRequest(root=ROOT, pattern="**/*.md", max_results=100))).entries
            assert (await files.search_text(FileTextSearchRequest(root=ROOT, pattern="ETag", max_matches=1))).matches
            for path in (f"{ROOT}/SKILL.md", f"{INSTANCE_ROOT}/catalog.json"):
                with pytest.raises(EnvironmentError, match="denied"):
                    await files.read_bytes(path)
                with pytest.raises(EnvironmentError, match="denied"):
                    await files.write_text(path, "changed", mode="replace")
            for path in ("/etc/passwd", f"{ROOT}/../../secret", "/platform-skills-other/file"):
                with pytest.raises(EnvironmentError):
                    await bound.resolve_files(path)
        with pytest.raises(EnvironmentError):
            await files.read_text(f"{ROOT}/SKILL.md")


async def test_builtin_skill_without_user_environment_emits_standard_events():
    results = []
    instructions = []

    async def model(messages, info):
        instructions.append(str(info.instructions))
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            assert {tool.name for tool in info.function_tools} == {
                "view",
                "ls",
                "glob",
                "grep",
            }
            yield {
                0: DeltaToolCall(
                    name="view", json_args=json.dumps({"file_path": f"{ROOT}/SKILL.md"}), tool_call_id="guide"
                )
            }
        else:
            results.append(returns[-1].content)
            yield "done"

    async with startup() as mounts:
        executable = HarnessBuilder(configured_plugins_enabled=False).build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                SkillsCapability(SkillManager((SOURCE,))),
                DynamicEnvironmentCapability(environment_configuration(configuration("test").toolsets)),
            ),
        )
        events = []
        async with executable.stream("read", environments=mounts, bindings=RunBindings.embedded()) as run:
            async for event in run:
                if isinstance(event, HarnessEvent) and isinstance(event.event, HarnessExtensionEvent):
                    events.append(event.event.payload)
        assert run.result.output == "done"
    assert results[0]["ok"] and "# Platform operations" in results[0]["content"]
    assert instructions[0].count("<available-skills>") == 1
    assert any(
        event.get("type") == "skills_catalog_resolved" and event["skills"] == ["platform-operations"]
        for event in events
    )
    assert any(event.get("type") == "skill_accessed" and event["source_id"] == SOURCE.source_id for event in events)


async def test_process_reuses_references_across_runs_and_removes_them_on_shutdown(tmp_path, monkeypatch):
    from a13n_service.runs import platform_files

    monkeypatch.setattr(platform_files, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path))
    settings = Settings()
    settings = settings.model_copy(
        update={"plugins": settings.plugins.model_copy(update={"keys": ("deployment-plugin",)})}
    )
    async with startup(settings) as mounts:
        (directory,) = tuple(tmp_path.iterdir())
        assert {path.name for path in directory.iterdir()} == {"catalog.json"}
        catalog = directory / "catalog.json"
        original = catalog.read_bytes()
        created_at = catalog.stat().st_mtime_ns
        assert json.loads(original)["plugins"] == ["deployment-plugin"]

        def unexpected_generation(*args, **kwargs):
            raise AssertionError("Run preparation must only read existing reference files")

        monkeypatch.setattr(platform_files, "TemporaryDirectory", unexpected_generation)
        monkeypatch.setattr(AgentConfig, "model_json_schema", unexpected_generation)
        for number in range(2):
            environment = create_environment_runtime(mounts=mounts)
            async with environment.bind(
                thread_id="thread-test", run_id=f"run-{number}", instance=RunBindings.embedded().instance, host_refs={}
            ) as bound:
                await SkillManager((SOURCE,)).scan_environment(environment=bound)
                assert (await bound.files.read_text(f"{ROOT}/schemas/agent-config.json", line_limit=1)).text
                tail = await bound.files.read_text(
                    f"{INSTANCE_ROOT}/catalog.json", line_offset=original.count(b"\n") - 5, line_limit=10
                )
                assert '"deployment-plugin"' in tail.text
            assert catalog.read_bytes() == original and catalog.stat().st_mtime_ns == created_at
        assert tuple(tmp_path.iterdir()) == (directory,)
    assert not directory.exists()


@pytest.mark.parametrize("name", ["workspace", "user-files"])
async def test_host_preserves_user_default_with_builtin_mounts(tmp_path, name):
    (tmp_path / "note.txt").write_text("user file")
    async with startup() as mounts:
        environment = _environment({name: _mount(tmp_path, "/user-files")}, mounts)
        async with environment.bind(
            thread_id="thread-test", run_id="run-test", instance=RunBindings.embedded().instance, host_refs={}
        ) as bound:
            assert bound.snapshot.default_mount == name
            assert (await bound.files.read_text("note.txt")).text == "user file"
            assert (await bound.files.read_text(f"{ROOT}/SKILL.md")).text.startswith("---")
            with pytest.raises(EnvironmentError):
                await bound.files.write_text(f"{ROOT}/SKILL.md", "changed", mode="replace")


async def test_composer_does_not_offer_mutations_with_writable_user_environment(tmp_path):
    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == {"view", "ls", "glob", "grep"}
        yield "done"

    from dataclasses import replace

    user = replace(
        _mount(tmp_path, "/user"), permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))
    )
    async with startup() as mounts:
        executable = HarnessBuilder(configured_plugins_enabled=False).build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                SkillsCapability(SkillManager((SOURCE,))),
                DynamicEnvironmentCapability(environment_configuration(configuration("test").toolsets)),
            ),
        )
        result = await executable.run(
            "read",
            environments={"workspace": user, **mounts},
            default_environment="workspace",
            bindings=RunBindings.embedded(),
        )
        assert result.output == "done"
