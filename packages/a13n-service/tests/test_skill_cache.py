"""Package-level publication races on a real filesystem, independent of the object-store transport."""

import asyncio
import hashlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import IsolatedSkillSync
from a13n_service.resources.skills.package import Package, pack, read_package, validate_name
from a13n_service.runs import skills
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.skills import PinnedSkill, _PinnedSkills

pytestmark = pytest.mark.anyio


@pytest.fixture
def files(tmp_path: Path) -> Iterator[LocalFileOperator]:
    operator = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="cache",
        generation="test",
    )
    try:
        yield operator
    finally:
        operator.close()


async def prove() -> None:
    pass


async def published(files: Any, skill: PinnedSkill) -> bool:
    return (await skills._published(files, [skill]))[0]


async def evict(files: Any, digest: str) -> bool:
    await files.mkdir(f"{skills._ROOT}/.cache/evict", parents=True, exist_ok=True)
    return await Cache()._evict(files, digest)


def materializer(monkeypatch: pytest.MonkeyPatch, content: bytes = b"first") -> tuple[_PinnedSkills, PinnedSkill]:
    document = b"---\nname: review\ndescription: Review changes\n---\n" + content
    package = Package("review", "Review changes", "", {"SKILL.md": document, "scripts/check.py": content})
    digest = hashlib.sha256(pack(package.files)).hexdigest()
    pinned = PinnedSkill(f"skr_{digest}", digest, package.name, package.description)

    async def load(*args: Any) -> tuple[FileArchive, ...]:
        return (FileArchive(pack(package.files), tuple((name, len(data)) for name, data in package.files.items())),)

    monkeypatch.setattr(skills, "load_packages", load)
    runtime: Any = SimpleNamespace(storage=None, objects=None)
    return _PinnedSkills((pinned,), runtime, "ws_test", prove, cache=Cache(), sync=IsolatedSkillSync()), pinned


async def test_identical_publishers_race_without_rewriting_winner(files, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    move = files.move
    arrived = 0
    ready = asyncio.Event()

    async def race(candidate: str, destination: str, *, replace: bool = False) -> Any:
        nonlocal arrived
        assert not replace
        assert await files.read_bytes(f"{candidate}/review/scripts/check.py") == b"first"
        arrived += 1
        if arrived == 2:
            ready.set()
        await ready.wait()
        return await move(candidate, destination, replace=replace)

    monkeypatch.setattr(files, "move", race)
    await asyncio.wait_for(asyncio.gather(source.materialize(files=files), source.materialize(files=files)), 10)
    assert arrived == 2
    assert await files.read_bytes(f"{pinned.cached_directory}/scripts/check.py") == b"first"
    assert not (await files.list("/workspace/.agents/skill-cache/.staging", max_results=10)).entries
    assert len((await files.list("/workspace/.agents/skill-cache", max_results=10)).entries) == 1

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A warm cache neither downloads nor republishes")

    monkeypatch.setattr(skills, "load_packages", forbidden)
    monkeypatch.setattr(files, "move", forbidden)
    await source.materialize(files=files)


async def test_same_name_versions_coexist_with_separate_catalogs(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first, one = materializer(monkeypatch, b"one")
    await first.materialize(files=files)
    second, two = materializer(monkeypatch, b"two")
    await second.materialize(files=files)
    assert one.digest != two.digest
    assert await files.read_bytes(f"{one.cached_directory}/scripts/check.py") == b"one"
    assert await files.read_bytes(f"{two.cached_directory}/scripts/check.py") == b"two"
    assert [item.path for item in await first.catalog(files=files)] == [one.cached_directory]
    assert [item.path for item in await second.catalog(files=files)] == [two.cached_directory]
    assert Path(one.cached_directory).name == Path(two.cached_directory).name == "review"


async def test_partial_write_is_not_published_and_retry_starts_clean(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    write = files.write_bytes_stream
    written = 0

    async def fail(target: str, stream: Any, *, mode: Any) -> Any:
        nonlocal written
        result = await write(target, stream, mode=mode)
        if "/.staging/" not in target:
            return result
        written += 1
        if written == 1:
            raise EnvironmentError("Disconnected", code="environment_unavailable")

    monkeypatch.setattr(files, "write_bytes_stream", fail)
    with pytest.raises(ServiceError) as error:
        await source.materialize(files=files)
    assert error.value.code == "unavailable"
    with pytest.raises(EnvironmentError):
        await files.stat(pinned.package_directory)
    assert not (await files.list("/workspace/.agents/skill-cache/.staging", max_results=10)).entries
    monkeypatch.setattr(files, "write_bytes_stream", write)
    await source.materialize(files=files)
    assert await files.read_bytes(f"{pinned.cached_directory}/scripts/check.py") == b"first"


async def test_lease_loss_before_publish_cleans_only_own_candidate(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    proofs = 0

    async def lost() -> None:
        nonlocal proofs
        proofs += 1
        if proofs == 2:
            raise LeaseLost()

    source.prove_lease = lost
    await files.mkdir("/workspace/.agents/skill-cache/.staging/another-publisher", parents=True)
    with pytest.raises(LeaseLost):
        await source.materialize(files=files)
    assert [
        Path(item.path).name
        for item in (await files.list("/workspace/.agents/skill-cache/.staging", max_results=10)).entries
    ] == ["another-publisher"]
    with pytest.raises(EnvironmentError):
        await files.stat(pinned.package_directory)


async def test_cancelled_publisher_leaves_no_partial_package(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    write = files.write_bytes_stream
    started = asyncio.Event()

    async def pause(target: str, stream: Any, *, mode: Any) -> Any:
        result = await write(target, stream, mode=mode)
        if "/.staging/" not in target:
            return result
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(files, "write_bytes_stream", pause)
    task = asyncio.create_task(source.materialize(files=files))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(EnvironmentError):
        await files.stat(pinned.package_directory)
    assert not (await files.list("/workspace/.agents/skill-cache/.staging", max_results=10)).entries


@pytest.mark.parametrize(
    "name",
    ["../escape", "nested/skill", "nested\\skill", ".", "..", "CON", "nul.txt", "skill.", "name:stream", "x" * 256],
)
def test_unsafe_skill_names_are_rejected_before_placement(name: str) -> None:
    with pytest.raises(ServiceError) as error:
        read_package(pack({"SKILL.md": f"---\nname: '{name}'\ndescription: Test\n---\n".encode()}))
    assert error.value.code == "invalid_argument"


@pytest.mark.parametrize("name", ["code-review", "Code Review", "中文技能"])
def test_portable_skill_names_are_accepted(name: str) -> None:
    validate_name(name)


async def test_cancelled_move_does_not_remove_candidate_still_being_published(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    move = files.move
    started = asyncio.Event()
    finish = asyncio.Event()
    pending: list[asyncio.Task[Any]] = []

    async def delayed(candidate: str, destination: str, *, replace: bool = False) -> Any:
        async def complete() -> Any:
            started.set()
            await finish.wait()
            return await move(candidate, destination, replace=replace)

        operation = asyncio.create_task(complete())
        pending.append(operation)
        # Model a provider operation that survives cancellation of its caller.
        return await asyncio.shield(operation)

    monkeypatch.setattr(files, "move", delayed)
    task = asyncio.create_task(source.materialize(files=files))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len((await files.list("/workspace/.agents/skill-cache/.staging", max_results=10)).entries) == 1
    finish.set()
    await asyncio.wait_for(asyncio.gather(*pending), 10)
    assert await published(files, pinned)
    assert await files.read_bytes(f"{pinned.cached_directory}/scripts/check.py") == b"first"
    await source.materialize(files=files)


@pytest.mark.parametrize("name", [".complete", ".COMPLETE", ".complete-package"])
async def test_hidden_skill_names_need_no_completion_marker(files, monkeypatch, name: str) -> None:  # type: ignore[no-untyped-def]
    package = read_package(pack({"SKILL.md": f"---\nname: '{name}'\ndescription: Test\n---\n".encode()}))
    digest = hashlib.sha256(pack(package.files)).hexdigest()
    pinned = PinnedSkill("skr_hidden", digest, name, "Test")

    async def load(*args: Any) -> tuple[FileArchive, ...]:
        return (FileArchive(pack(package.files), tuple((name, len(data)) for name, data in package.files.items())),)

    monkeypatch.setattr(skills, "load_packages", load)
    runtime: Any = SimpleNamespace(storage=None, objects=None)
    source = _PinnedSkills((pinned,), runtime, "ws_test", prove, cache=Cache(), sync=IsolatedSkillSync())
    await source.materialize(files=files)
    assert await published(files, pinned)
    assert await files.read_bytes(f"{pinned.cached_directory}/SKILL.md") == package.files["SKILL.md"]


async def test_soft_budget_evicts_least_recent_inactive_package(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first, one = materializer(monkeypatch, b"one")
    await first.materialize(files=files)
    await first.cache.release(files)
    second, two = materializer(monkeypatch, b"two")
    await second.materialize(files=files)
    await second.cache.release(files)
    # A warm acquisition makes the first package newer without downloading it.
    await first.materialize(files=files)
    await first.cache.release(files)
    third, three = materializer(monkeypatch, b"new")
    third.sync = IsolatedSkillSync(cache_count=2)
    await third.materialize(files=files)
    assert await published(files, one)
    assert not await published(files, two)
    assert await published(files, three)
    assert not (await files.list(f"{skills._ROOT}/.cache/evict", max_results=10)).entries


async def test_active_packages_can_exceed_capacity(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first, one = materializer(monkeypatch, b"one")
    first.sync = IsolatedSkillSync(cache_count=1)
    await first.materialize(files=files)
    second, two = materializer(monkeypatch, b"two")
    second.sync = IsolatedSkillSync(cache_count=1)
    await second.materialize(files=files)
    assert await published(files, one)
    assert await published(files, two)
    await first.cache.release(files)
    await second.cache.trim(files, capacity=1)
    assert not await published(files, one)
    assert await published(files, two)
    # Reacquiring an evicted pinned revision restores the exact same package path.
    restored, again = materializer(monkeypatch, b"one")
    restored.sync = IsolatedSkillSync(cache_count=1)
    await restored.materialize(files=files)
    assert again.cached_directory == one.cached_directory
    assert await published(files, again)


async def test_reader_arriving_after_eviction_check_waits_for_completion(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    await source.cache.release(files)
    remove = files.remove
    deleting, finish = asyncio.Event(), asyncio.Event()

    async def pause(path: str, *, recursive: bool = False) -> Any:
        if path == pinned.package_directory:
            deleting.set()
            await finish.wait()
        return await remove(path, recursive=recursive)

    monkeypatch.setattr(files, "remove", pause)
    eviction = asyncio.create_task(evict(files, pinned.digest))
    await asyncio.wait_for(deleting.wait(), 10)
    reader = asyncio.create_task(source.materialize(files=files))
    # Observe the reader's durable pin rather than sleeping for an assumed scheduling interval.
    while not (await files.list(f"{skills._ROOT}/.cache/pins/{pinned.digest}", max_results=1)).entries:
        await asyncio.sleep(0)
    assert not reader.done()
    finish.set()
    await asyncio.wait_for(asyncio.gather(eviction, reader), 10)
    assert await published(files, pinned)
    assert await files.read_bytes(f"{pinned.cached_directory}/scripts/check.py") == b"first"


async def test_pin_published_before_collector_check_prevents_eviction(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    await source.cache.release(files)
    listing = files.list
    checking, proceed = asyncio.Event(), asyncio.Event()
    pins = f"{skills._ROOT}/.cache/pins/{pinned.digest}"

    async def pause(path: str, **kwargs: Any) -> Any:
        if path == pins:
            checking.set()
            await proceed.wait()
        return await listing(path, **kwargs)

    monkeypatch.setattr(files, "list", pause)
    eviction = asyncio.create_task(evict(files, pinned.digest))
    await asyncio.wait_for(checking.wait(), 10)
    reader = asyncio.create_task(source.materialize(files=files))
    while not (await listing(pins, max_results=1)).entries:
        await asyncio.sleep(0)
    proceed.set()
    await asyncio.wait_for(asyncio.gather(eviction, reader), 10)
    assert await published(files, pinned)


async def test_unknown_eviction_keeps_gate_but_other_packages_still_load(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    await source.cache.release(files)
    remove = files.remove
    calls = 0

    async def uncertain(path: str, *, recursive: bool = False) -> Any:
        nonlocal calls
        if path == pinned.package_directory:
            calls += 1
            raise EnvironmentError("Outcome unknown", code="environment_unavailable")
        return await remove(path, recursive=recursive)

    monkeypatch.setattr(files, "remove", uncertain)
    with pytest.raises(EnvironmentError):
        await evict(files, pinned.digest)
    assert not await evict(files, pinned.digest)
    assert calls == 1
    assert await files.stat(f"{skills._ROOT}/.cache/evict/{pinned.digest}")
    source.cache.wait_seconds = 0.01
    with pytest.raises(ServiceError):
        await source.materialize(files=files)
    other, new = materializer(monkeypatch, b"new")
    other.sync = IsolatedSkillSync(cache_count=1)
    await other.materialize(files=files)
    assert await published(files, new)


async def test_uncertain_publication_pin_survives_execution_release(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)

    async def uncertain(*args: Any, **kwargs: Any) -> Any:
        raise EnvironmentError("Outcome unknown", code="environment_unavailable")

    monkeypatch.setattr(files, "move", uncertain)
    with pytest.raises(ServiceError):
        await source.materialize(files=files)
    await source.cache.release(files)
    assert len((await files.list(f"{skills._ROOT}/.cache/pins/{pinned.digest}", max_results=10)).entries) == 1


async def test_independent_clients_share_pins_and_collectors_do_not_redelete(files, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    other = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="other-worker",
        generation="other",
    )
    try:
        assert not await evict(other, pinned.digest)
        assert await published(files, pinned)
        await source.cache.release(files)
        await asyncio.gather(evict(other, pinned.digest), evict(files, pinned.digest))
        assert not await published(files, pinned)
        # A collector using an earlier scan must not strand a new gate on an already removed package.
        assert not await evict(other, pinned.digest)
        assert not (await files.list(f"{skills._ROOT}/.cache/evict", max_results=10)).entries
    finally:
        other.close()


async def test_same_archive_reuses_cache_across_revision_ids(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    other = _PinnedSkills(
        (replace(pinned, revision_id="skr_another"),),
        source.runtime,
        "ws_test",
        prove,
        cache=Cache(),
        sync=IsolatedSkillSync(),
    )

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Archive identity, not revision identity, owns cached bytes")

    monkeypatch.setattr(skills, "load_packages", forbidden)
    await other.materialize(files=files)
    assert [item.path for item in await other.catalog(files=files)] == [pinned.cached_directory]


async def test_zero_capacity_disables_automatic_eviction(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    await source.cache.release(files)
    await source.cache.trim(files, capacity=0)
    assert await published(files, pinned)


async def test_large_cache_counts_and_considers_entries_beyond_first_4096(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    count = 5000
    seen: set[str] = set()
    evicted: list[str] = []

    async def listing(path: str, *, offset: int, max_results: int, **kwargs: Any) -> Any:
        stop = min(count, offset + max_results)
        return SimpleNamespace(
            entries=[SimpleNamespace(path=f"{path}/{i:064x}") for i in range(offset, stop)], has_more=stop < count
        )

    async def read(path: str, **kwargs: Any) -> bytes:
        seen.add(path)
        return b"1"

    async def mkdir(*args: Any, **kwargs: Any) -> None:
        pass

    async def evict(files: Any, digest: str) -> bool:
        if int(digest, 16) < 4096:  # The prefix is pinned, but the tail is reclaimable.
            return False
        evicted.append(digest)
        return True

    cache = Cache()
    monkeypatch.setattr(cache, "_evict", evict)
    files: Any = SimpleNamespace(list=listing, read_bytes=read, mkdir=mkdir)
    await cache.trim(files, capacity=4096)
    assert len(seen) == count
    assert len(evicted) == 32


async def test_large_healthy_release_drains_every_pin_in_bounded_batches() -> None:
    from a13n_service.runs.skill_cache import ROOT, Use

    cache = Cache()
    cache.uses = [Use(f"{ROOT}/.cache/pins/{i}/unique") for i in range(512)]
    removed: set[str] = set()
    sizes = []

    async def batch(operations):
        sizes.append(len(operations))
        removed.update(item.path for item in operations)
        return (None,) * len(operations)

    files: Any = SimpleNamespace(batch=batch)
    await cache.release(files)
    assert len(removed) == 512
    assert sizes == [32] * 16


async def test_uncertain_pin_batch_retains_all_dispatched_pins(files, monkeypatch):
    cache = Cache()
    original = files.batch

    async def fail(operations):
        await original(operations[:2])
        raise EnvironmentError("Unconfirmed batch", code="environment_unknown_outcome")

    monkeypatch.setattr(files, "batch", fail)
    with pytest.raises(EnvironmentError):
        await cache.acquire_many(files, ["a" * 64, "b" * 64])
    assert cache.uses == []
    pins = f"{skills._ROOT}/.cache/pins/{'a' * 64}"
    created = (await files.list(pins, include_hidden=True, max_results=10)).entries
    monkeypatch.setattr(files, "batch", original)
    await cache.release(files)
    assert (await files.list(pins, include_hidden=True, max_results=10)).entries == created


async def test_within_capacity_never_reads_entry_metadata(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    source.sync = IsolatedSkillSync(cache_count=1)

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A cache within capacity only lists entries")

    monkeypatch.setattr(files, "read_bytes", forbidden)
    await source.cache.trim(files, capacity=1)
    await source.materialize(files=files)
    assert await published(files, pinned)


async def test_published_package_contains_only_the_named_skill(files, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source, pinned = materializer(monkeypatch)
    await source.materialize(files=files)
    entries = (await files.list(pinned.package_directory, include_hidden=True, max_results=10)).entries
    assert [Path(entry.path).name for entry in entries] == [pinned.name]


async def test_parallel_packages_settle_before_failure_and_retry_only_missing(files, monkeypatch):
    archives = {}
    for name in ("one", "two"):
        content = f"---\nname: {name}\ndescription: Test\n---\n".encode()
        archives[f"skr_{name}"] = FileArchive(pack({"SKILL.md": content}), (("SKILL.md", len(content)),))
    pins = tuple(
        PinnedSkill(revision, hashlib.sha256(archive.content).hexdigest(), revision.removeprefix("skr_"), "Test")
        for revision, archive in archives.items()
    )
    loads = []

    async def load(storage, objects, workspace, revisions):
        loads.append(list(revisions))
        return [archives[revision] for revision in revisions]

    monkeypatch.setattr(skills, "load_packages", load)
    source = _PinnedSkills(
        pins, SimpleNamespace(storage=None, objects=None), "ws", prove, cache=Cache(), sync=IsolatedSkillSync()
    )
    write = files.write_archive
    both_started, release = asyncio.Event(), asyncio.Event()
    started = 0

    async def overlap(path, archive):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        if path.endswith("/one"):
            raise EnvironmentError("Disconnected", code="environment_unavailable")
        await release.wait()
        return await write(path, archive)

    monkeypatch.setattr(files, "write_archive", overlap)
    preparing = asyncio.create_task(source.materialize(files=files))
    await asyncio.wait_for(both_started.wait(), 2)
    await asyncio.sleep(0)
    assert not preparing.done()
    release.set()
    with pytest.raises(ServiceError):
        await preparing
    assert not await published(files, pins[0])
    assert await published(files, pins[1])
    monkeypatch.setattr(files, "write_archive", write)
    await source.materialize(files=files)
    assert await published(files, pins[0])
    assert loads == [["skr_one", "skr_two"], ["skr_one"]]
    assert not (await files.list(f"{skills._ROOT}/.staging", max_results=10)).entries
