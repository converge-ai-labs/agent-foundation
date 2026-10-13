"""Shared-name publication policies over real local files, with controlled concurrent publishers."""

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override, freeze_override
from a13n_service.resources.skills.package import Package, validate_name
from a13n_service.runs import skills

pytestmark = pytest.mark.anyio


@pytest.fixture
def sync_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace" / ".agents" / "skills"
    files = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="test",
        generation="test",
    )
    return files, root


def materializer(monkeypatch, version="new", *, overwrite=True, cleanup=False, empty=False):
    async def load(storage, objects, workspace, revisions):
        return [
            Package("review", version, "", {"SKILL.md": revision.encode(), revision: b"content"})
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
        force_overwrite=overwrite,
        force_cleanup=cleanup,
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
    sibling = root.parent / "unrelated"
    sibling.write_text("preserve")
    await materializer(monkeypatch, overwrite=overwrite, cleanup=cleanup).materialize(files=files)
    assert (root / "review" / "SKILL.md").read_text() == ("new" if overwrite else "old")
    assert (root / "review" / "obsolete").exists() is (not overwrite)
    assert (root / "manual").exists() is (not cleanup)
    assert (root / ".hidden").exists() is (not cleanup)
    assert sibling.read_text() == "preserve"
    assert not list(root.parent.glob(".skill-stage-*"))


async def test_empty_selection_can_force_cleanup(sync_root, monkeypatch):
    files, root = sync_root
    root.mkdir(parents=True)
    (root / "unselected").write_text("old")
    await materializer(monkeypatch, empty=True, cleanup=True).materialize(files=files)
    assert list(root.iterdir()) == []


async def test_concurrent_overwriters_publish_whole_packages(sync_root, monkeypatch):
    files, root = sync_root
    first = materializer(monkeypatch, "first")
    second = materializer(monkeypatch, "second")
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()

    async def delayed(source, destination, *, replace=False):
        if (await files.read_bytes(source + "/SKILL.md")) == b"first":
            ready.set()
            await release.wait()
        return await move(source, destination, replace=replace)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(first.materialize(files=files))
    await ready.wait()
    await second.materialize(files=files)
    assert (root / "review" / "SKILL.md").read_text() == "second"
    release.set()
    await task
    assert (root / "review" / "SKILL.md").read_text() == "first"
    assert {entry.name for entry in (root / "review").iterdir()} == {"SKILL.md", "first"}


async def test_additive_publication_preserves_a_concurrent_winner(sync_root, monkeypatch):
    files, root = sync_root
    first = materializer(monkeypatch, "first", overwrite=False)
    second = materializer(monkeypatch, "second")
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()

    async def delayed(source, destination, *, replace=False):
        if not replace:
            ready.set()
            await release.wait()
        return await move(source, destination, replace=replace)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(first.materialize(files=files))
    await ready.wait()
    await second.materialize(files=files)
    release.set()
    await task
    assert (root / "review" / "SKILL.md").read_text() == "second"
    assert not list(root.parent.glob(".skill-stage-*"))


async def test_cancelled_publication_keeps_candidate_for_outstanding_provider(sync_root, monkeypatch):
    files, root = sync_root
    move = files.move
    ready, release = asyncio.Event(), asyncio.Event()
    outstanding = []

    async def delayed(source, destination, *, replace=False):
        async def publish():
            ready.set()
            await release.wait()
            return await move(source, destination, replace=replace)

        task = asyncio.create_task(publish())
        outstanding.append(task)
        return await asyncio.shield(task)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(materializer(monkeypatch).materialize(files=files))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (root / "review").exists()
    assert len(list(root.parent.glob(".skill-stage-*"))) == 1
    release.set()
    await asyncio.gather(*outstanding)
    assert (root / "review" / "SKILL.md").read_text() == "new"


async def test_unknown_publication_preserves_candidate(sync_root, monkeypatch):
    files, root = sync_root

    async def unknown(*args, **kwargs):
        raise EnvironmentError("unknown", code="environment_unknown_outcome")

    monkeypatch.setattr(files, "move", unknown)
    with pytest.raises(EnvironmentError):
        await materializer(monkeypatch).materialize(files=files)
    assert len(list(root.parent.glob(".skill-stage-*"))) == 1


@pytest.mark.parametrize("field", ["skill_force_overwrite", "skill_force_cleanup"])
@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("choice", [None, False, True])
def test_agent_and_run_sync_policy_round_trip(field, baseline, choice):
    config = AgentConfig(model="test", **{field: baseline})
    override = AgentOverride(**{field: choice})
    applied = apply_override(config, override)
    assert applied.model_dump()[field] is (baseline if choice is None else choice)
    frozen = freeze_override(override, applied)
    restored = AgentOverride.model_validate_json(frozen.model_dump_json())
    assert apply_override(config, restored) == applied


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "with/slash",
        "with\\slash",
        ".",
        "..",
        "con",
        "nul",
        "Upper",
        "-leading",
        "trailing-",
        "double--dash",
        "a" * 65,
    ],
)
def test_skill_names_are_safe_conventional_directory_names(name):
    with pytest.raises(ServiceError):
        validate_name(name)
