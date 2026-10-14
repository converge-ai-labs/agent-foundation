"""Shared publication, mode selection and isolation on real local files."""

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness.environment.archives import FileArchive
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_service.resources.agents.schemas import (
    AgentConfig,
    AgentOverride,
    IsolatedSkillSync,
    SharedSkillSync,
    apply_override,
    freeze_override,
)
from a13n_service.resources.skills.package import pack
from a13n_service.runs import skills
from a13n_service.runs.skill_cache import Cache
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def sync_root(tmp_path):
    files = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="test",
        generation="test",
    )
    try:
        yield files, tmp_path / "workspace" / ".agents" / "skills"
    finally:
        files.close()


def materializer(monkeypatch, version="new", *, overwrite=True, cleanup=False, empty=False):
    async def load(storage, objects, workspace, revisions):
        return [
            FileArchive(
                pack({"SKILL.md": revision.encode(), revision: b"content"}),
                (("SKILL.md", len(revision.encode())), (revision, 7)),
            )
            for revision in revisions
        ]

    async def prove():
        pass

    monkeypatch.setattr(skills, "load_packages", load)
    return skills._PinnedSkills(
        () if empty else (skills.PinnedSkill(version, version, "review", version),),
        SimpleNamespace(storage=None, objects=None),
        "workspace",
        prove,
        cache=Cache(),
        sync=SharedSkillSync(overwrite=overwrite, cleanup=cleanup),
    )


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("cleanup", [False, True])
async def test_sync_policy_matrix(sync_root, monkeypatch, overwrite, cleanup):
    files, root = sync_root
    (root / "review").mkdir(parents=True)
    (root / "review" / "SKILL.md").write_text("old")
    (root / "review" / "obsolete").write_text("old")
    (root / "manual").mkdir()
    (root / "manual" / "SKILL.md").write_text("manual")
    (root / ".hidden").write_text("manual")
    cache = root.parent / "skill-cache" / "digest" / "review"
    cache.mkdir(parents=True)
    (cache / "SKILL.md").write_text("isolated")
    source = materializer(monkeypatch, overwrite=overwrite, cleanup=cleanup)
    await source.materialize(files=files)
    assert (root / "review" / "SKILL.md").read_text() == ("new" if overwrite else "old")
    assert (root / "review" / "obsolete").exists() is (not overwrite)
    assert (root / "manual").exists() is (not cleanup)
    assert (root / ".hidden").exists() is (not cleanup)
    assert (cache / "SKILL.md").read_text() == "isolated"
    assert not list(root.parent.glob(".skill-stage-*"))
    assert not source.cache.uses
    assert (await source.catalog(files=files))[0].path == "/workspace/.agents/skills/review"


async def test_default_republishes_and_last_run_wins(sync_root, monkeypatch):
    files, root = sync_root
    await materializer(monkeypatch, "first").materialize(files=files)
    await materializer(monkeypatch, "second").materialize(files=files)
    assert {item.name for item in (root / "review").iterdir()} == {"SKILL.md", "second"}
    assert (root / "review" / "SKILL.md").read_text() == "second"


async def test_empty_selection_can_cleanup(sync_root, monkeypatch):
    files, root = sync_root
    root.mkdir(parents=True)
    (root / "unselected").write_text("old")
    await materializer(monkeypatch, empty=True, cleanup=True).materialize(files=files)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("overwrite", [False, True])
async def test_concurrent_publication_preserves_complete_winner(sync_root, monkeypatch, overwrite):
    files, root = sync_root
    first = materializer(monkeypatch, "first", overwrite=overwrite)
    second = materializer(monkeypatch, "second")
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()

    async def delayed(source, destination, *, replace=False):
        if destination.endswith("/skills/review") and await files.read_bytes(source + "/SKILL.md") == b"first":
            ready.set()
            await release.wait()
        return await move(source, destination, replace=replace)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(first.materialize(files=files))
    await asyncio.wait_for(ready.wait(), 5)
    await second.materialize(files=files)
    release.set()
    await task
    winner = "first" if overwrite else "second"
    assert (root / "review" / "SKILL.md").read_text() == winner
    assert {item.name for item in (root / "review").iterdir()} == {"SKILL.md", winner}
    assert not list(root.parent.glob(".skill-stage-*"))


@pytest.mark.parametrize("overwrite", [False, True])
async def test_cancelled_publication_keeps_outstanding_candidate(sync_root, monkeypatch, overwrite):
    files, root = sync_root
    if overwrite:
        await materializer(monkeypatch, "old").materialize(files=files)
    source = materializer(monkeypatch, overwrite=overwrite)
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()
    outstanding = []

    async def delayed(candidate, destination, *, replace=False):
        async def publish():
            ready.set()
            await release.wait()
            return await move(candidate, destination, replace=replace)

        operation = asyncio.create_task(publish())
        outstanding.append(operation)
        return await asyncio.shield(operation)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(source.materialize(files=files))
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(list(root.parent.glob(".skill-stage-*"))) == 1
    release.set()
    await asyncio.gather(*outstanding)
    assert (root / "review" / "SKILL.md").read_text() == "new"


def test_modes_defaults_and_whole_block_override():
    config = AgentConfig(model="test")
    assert config.skill_sync == SharedSkillSync(overwrite=True, cleanup=False)
    isolated = apply_override(config, AgentOverride(skill_sync=IsolatedSkillSync(cache_count=7)))
    assert isolated.skill_sync == IsolatedSkillSync(cache_count=7)
    assert apply_override(isolated, AgentOverride()).skill_sync == isolated.skill_sync
    assert apply_override(isolated, AgentOverride(skill_sync=None)).skill_sync == isolated.skill_sync
    shared = apply_override(isolated, AgentOverride(skill_sync=SharedSkillSync(overwrite=False)))
    assert shared.skill_sync == SharedSkillSync(overwrite=False, cleanup=False)
    override = AgentOverride(skill_sync=IsolatedSkillSync(cache_count=7))
    frozen = freeze_override(override, isolated)
    assert frozen.skill_sync == isolated.skill_sync
    assert AgentConfig.model_validate(isolated.model_dump()).skill_sync == isolated.skill_sync


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "shared", "cache_count": 1},
        {"mode": "isolated", "cleanup": True},
        {"mode": "isolated", "overwrite": False},
        {"mode": "isolated", "cache_count": -1},
        {"mode": "unknown"},
    ],
)
def test_mode_specific_fields_are_rejected(value):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"model": "test", "skill_sync": value})


async def test_cleanup_requires_primary_even_without_selected_skills():
    from a13n_service.infra.errors import ServiceError

    with pytest.raises(ServiceError):
        await skills.resolve_skills(
            None, SimpleNamespace(environment_mounts=[]), (), sync=SharedSkillSync(cleanup=True)
        )


@pytest.mark.parametrize("failure", ["stage", "environment_unsupported", "environment_conflict"])
async def test_failed_overwrite_preserves_old_directory(sync_root, monkeypatch, failure):
    from a13n_harness.environment.models import EnvironmentError
    from a13n_service.infra.errors import ServiceError

    files, root = sync_root
    await materializer(monkeypatch, "old").materialize(files=files)
    source = materializer(monkeypatch)

    async def fail(*args, **kwargs):
        raise EnvironmentError("Publication failed", code="environment_unsupported" if failure == "stage" else failure)

    if failure == "stage":
        monkeypatch.setattr(files, "write_archive", fail)
    else:
        monkeypatch.setattr(files, "move", fail)
    with pytest.raises(ServiceError):
        await source.materialize(files=files)
    assert (root / "review" / "SKILL.md").read_text() == "old"
    assert {item.name for item in (root / "review").iterdir()} == {"SKILL.md", "old"}
    # Only an unconfirmed move keeps its stage; it must never race the provider's rename.
    stages = [item for item in root.parent.iterdir() if item.name.startswith(".skill-stage-")]
    assert len(stages) == (failure == "environment_unsupported")


async def test_overwrite_keeps_old_directory_until_publication(sync_root, monkeypatch):
    files, root = sync_root
    await materializer(monkeypatch, "old").materialize(files=files)
    source = materializer(monkeypatch)
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()

    async def delayed(candidate, destination, *, replace=False):
        assert replace is True
        assert (root / "review" / "SKILL.md").read_text() == "old"
        assert await files.read_bytes(candidate + "/SKILL.md") == b"new"
        ready.set()
        await release.wait()
        return await move(candidate, destination, replace=replace)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(source.materialize(files=files))
    await asyncio.wait_for(ready.wait(), 5)
    assert (root / "review" / "SKILL.md").read_text() == "old"
    release.set()
    await task
    assert (root / "review" / "SKILL.md").read_text() == "new"


async def test_failed_preparation_leaves_no_shared_candidates(sync_root, monkeypatch):
    from a13n_harness.environment.models import EnvironmentError
    from a13n_service.infra.errors import ServiceError

    files, root = sync_root
    source = materializer(monkeypatch)
    source.skills = tuple(skills.PinnedSkill(name, name, name, name) for name in ("one", "two", "three"))
    write = files.write_archive

    async def fail_one(path, archive):
        await write(path, archive)
        if path.endswith("/two"):
            raise EnvironmentError("Disconnected", code="environment_unavailable")

    monkeypatch.setattr(files, "write_archive", fail_one)
    with pytest.raises(ServiceError):
        await source.materialize(files=files)
    assert [item.name for item in root.parent.iterdir()] == ["skills"]
    assert not list(root.iterdir())
