"""Skill read scheduling never becomes catalog or prompt ordering authority."""

from __future__ import annotations

import asyncio
from pathlib import Path

import anyio
import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_harness import DefinitionError, HarnessBuilder, RunBindings
from a13n_harness.capabilities import FileSkillSource, SkillManager, SkillsCapability, skills
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import FunctionModel

from .environment_helpers import DirectLocalFilePolicy
from .test_skills import _binding

pytestmark = pytest.mark.anyio


def _write_skill(root: Path, directory: str, name: str, description: str = "A skill.") -> None:
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")


def _files(root: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        execution_id="skills",
        generation="generation-1",
    )


@pytest.mark.parametrize("count", [0, 1, 25])
async def test_ordered_reads_bound_workers_and_overlap_io(count: int) -> None:
    active = peak = task_peak = 0
    completed: list[int] = []

    async def read(index):
        nonlocal active, peak, task_peak
        active += 1
        peak = max(peak, active)
        task_peak = max(task_peak, sum(task.get_name() == "skill-catalog-read" for task in asyncio.all_tasks()))
        try:
            await asyncio.sleep(0)
            completed.append(index)
            return index * 2
        finally:
            active -= 1

    assert await skills._map_ordered(tuple(range(count)), read) == [index * 2 for index in range(count)]
    assert peak == task_peak == min(8, count)
    assert sorted(completed) == list(range(count))
    assert active == 0


@pytest.mark.parametrize("stage", ["discovery", "validation"])
async def test_catalog_overlaps_reads_but_preserves_sorted_results(tmp_path: Path, monkeypatch, stage: str) -> None:
    for index in range(20):
        _write_skill(tmp_path, f"skills/directory-{index:02}", f"skill-{19 - index:02}")
    files = _files(tmp_path)
    source = FileSkillSource("files", ("/skills",))
    manager = SkillManager((source,))
    baseline = await manager.scan(files=files)
    if stage == "validation":

        async def catalog(*, files):
            return baseline

        monkeypatch.setattr(source, "catalog", catalog)
    method = "read_text" if stage == "discovery" else "stat"
    original = files.read_text if stage == "discovery" else files.stat
    active = peak = 0

    async def read(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            return await original(*args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(files, method, read)
    assert await manager.scan(files=files) == baseline
    assert 1 < peak <= 8
    assert active == 0


async def test_completion_order_does_not_change_full_skill_instructions(tmp_path: Path, monkeypatch) -> None:
    for root in ("global", "project"):
        _write_skill(tmp_path, root, "root-skill", f"{root} root")
        _write_skill(tmp_path, f"{root}/a", "same", f"{root} earlier")
        _write_skill(tmp_path, f"{root}/z", "same", f"{root} later")
        _write_skill(tmp_path, f"{root}/m", "another")
    manager = SkillManager((FileSkillSource("files", ("/workspace/global", "/workspace/project")),))
    instructions: list[str] = []

    async def stream(messages, info):
        instructions.append(str(info.instructions))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(manager),),
    )
    monkeypatch.setattr(skills, "_SKILL_READ_CONCURRENCY", 1)
    await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))
    monkeypatch.setattr(skills, "_SKILL_READ_CONCURRENCY", 8)
    original = LocalFileOperator.read_text
    later_done = {root: asyncio.Event() for root in ("global", "project")}
    completed: list[str] = []

    async def reordered(self, path, **kwargs):
        parts = Path(path).parts
        root = next((root for root in later_done if root in parts), None)
        if root is not None and path.endswith("/a/SKILL.md"):
            await later_done[root].wait()
        result = await original(self, path, **kwargs)
        completed.append(path)
        if root is not None and path.endswith("/z/SKILL.md"):
            later_done[root].set()
        return result

    monkeypatch.setattr(LocalFileOperator, "read_text", reordered)
    async with asyncio.timeout(3):
        await executable.run("Use a skill", bindings=RunBindings.embedded(environment=_binding(tmp_path)))
    assert instructions[0] == instructions[1]
    assert "project later" in instructions[1]
    assert "project earlier" not in instructions[1]
    for root in later_done:
        selected = [path for path in completed if root in Path(path).parts]
        assert next(i for i, path in enumerate(selected) if path.endswith("/z/SKILL.md")) < next(
            i for i, path in enumerate(selected) if path.endswith("/a/SKILL.md")
        )


@pytest.mark.parametrize("skip_invalid", [False, True])
async def test_invalid_entries_and_diagnostics_follow_candidate_order(
    tmp_path: Path, monkeypatch, caplog, skip_invalid
) -> None:
    for name in ("a", "b", "valid"):
        _write_skill(tmp_path, f"skills/{name}", name)
    for name in ("a", "b"):
        (tmp_path / "skills" / name / "SKILL.md").write_text("invalid")
    files = _files(tmp_path)
    original = files.read_text
    later_done = asyncio.Event()

    async def reordered(path, **kwargs):
        if path.endswith("/a/SKILL.md"):
            await later_done.wait()
        result = await original(path, **kwargs)
        if path.endswith("/b/SKILL.md"):
            later_done.set()
        return result

    monkeypatch.setattr(files, "read_text", reordered)
    source = FileSkillSource("files", ("/skills",), skip_invalid=skip_invalid)
    async with asyncio.timeout(3):
        if skip_invalid:
            assert [item.name for item in await source.catalog(files=files)] == ["valid"]
            records = [record for record in caplog.records if record.msg == "skill_catalog_entry_skipped"]
            assert [record.path for record in records] == ["/skills/a", "/skills/b"]
        else:
            with pytest.raises(DefinitionError) as failure:
                await source.catalog(files=files)
            assert failure.value.code == "skill_catalog_invalid"
            assert failure.value.details["path"] == "/skills/a/SKILL.md"


@pytest.mark.parametrize("cancel_mode", ["task", "scope", "reader"])
async def test_cancelled_reads_join_all_workers(cancel_mode: str) -> None:
    ready = asyncio.Event()
    cancel_reader = asyncio.Event()
    active = 0

    async def read(index):
        nonlocal active
        active += 1
        if active == 8:
            ready.set()
        try:
            if cancel_mode == "reader" and index == 0:
                await cancel_reader.wait()
                raise asyncio.CancelledError
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            active -= 1

    async with asyncio.timeout(3):
        if cancel_mode == "scope":
            async with anyio.create_task_group() as group:
                group.start_soon(skills._map_ordered, tuple(range(30)), read)
                await ready.wait()
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(skills._map_ordered(tuple(range(30)), read))
            await ready.wait()
            if cancel_mode == "task":
                task.cancel()
            else:
                cancel_reader.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert active == 0
    assert not any(task.get_name() == "skill-catalog-read" for task in asyncio.all_tasks())


async def test_existing_size_limits_remain_errors(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write_skill(tmp_path, f"skills/{name}", name)
    files = _files(tmp_path)
    with pytest.raises(DefinitionError) as failure:
        await FileSkillSource("files", ("/skills",), max_entries_per_root=1).catalog(files=files)
    assert failure.value.code == "skill_catalog_too_large"
    manager = SkillManager((FileSkillSource("files", ("/skills",)),), policy=skills.SkillsPolicy(max_skills=1))
    with pytest.raises(DefinitionError) as failure:
        await manager.scan(files=files)
    assert failure.value.code == "skill_catalog_too_large"
