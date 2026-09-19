"""Exercise actual file publication across independent native daemon processes."""

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.memory.documents import Append, DocumentInput, MemoryDocumentError
from a13n_harness.providers.memory.filesystem.commit import EnvironmentMemoryFileCoordinator
from a13n_harness.providers.memory.filesystem.store import FilesystemMemoryStore

pytestmark = pytest.mark.anyio


async def allow(_):
    pass


@pytest.fixture(params=["direct", "envd"])
async def stores(tmp_path, request):
    executable = os.environ.get("A13N_ENVD_TEST_BINARY") or os.environ.get("A13N_ENVD_EXECUTABLE")
    if request.param == "envd" and not executable:
        pytest.skip("Set A13N_ENVD_TEST_BINARY to exercise native conditional publication")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = LOCAL_ENVD
    configuration = provider.validate_environment({"workspace": {"path": str(workspace)}})
    async with AsyncExitStack() as stack:
        stores = []
        for i in range(2):
            if request.param == "direct":
                from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
                from a13n_harness.providers.environment.direct_local.provider import _DirectLocalFilePolicy

                files = LocalFileOperator(
                    root=workspace,
                    read_only=False,
                    policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
                    mount_id="memory",
                    generation="one",
                )
            else:
                environment = provider.construct(
                    environment_id=f"memory-test-{i}",
                    configuration=configuration,
                    state=None,
                    runtime=LocalEnvdProviderRuntime(
                        executable=Path(executable),
                        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
                    ),
                )
                stack.push_async_callback(environment.close)
                await environment.enter(mount_id="memory")
                await environment.prepare()
                files = environment.operations.files
            assert files is not None
            store = FilesystemMemoryStore(
                files=files,
                root="/memory",
                scope="thread:test",
                store_id="mstore_test",
                principal="user_test",
                authorize=allow,
                authorize_sources=allow,
            )
            store.coordinator = EnvironmentMemoryFileCoordinator(
                files, root=store.subject_root, store_id=store.store_id, scope=store.scope
            )
            stores.append(store)
        await stores[0].initialize()
        yield stores


async def test_cross_process_revision_replay_and_erasure(stores):
    first, second = stores
    value = DocumentInput(
        kind="semantic", title="Runtime", description="Requirements", text="Python", path="semantic/runtime.md"
    )
    initial = await first.create_document(value, request_key="create")
    assert await second.document(initial.document.id) == initial.document
    outcomes = await asyncio.gather(
        *[
            store.revise(
                initial.document.id,
                expected_version=1,
                change=Append(type="append", text=f"-{i}"),
                request_key=f"revise-{i}",
            )
            for i, store in enumerate(stores)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    loser = next(item for item in outcomes if isinstance(item, MemoryDocumentError))
    assert loser.code == "memory_conflict"
    winner = next(item for item in outcomes if not isinstance(item, BaseException))
    assert (await second.document(initial.document.id)).version == 2
    assert (await first.create_document(value, request_key="create")).document == initial.document
    assert len(await second.history(initial.document.id)) == 2
    await first.delete(initial.document.id)
    with pytest.raises(MemoryDocumentError, match="memory_not_found"):
        await second.document(initial.document.id)
    with pytest.raises(MemoryDocumentError, match="memory_not_found"):
        await second.revise(
            initial.document.id,
            expected_version=winner.document.version,
            change=Append(type="append", text="resurrect"),
            request_key="late",
        )


async def test_missing_marker_is_not_recreated(stores):
    first, second = stores
    await first.files.remove(first.subject_root, recursive=True)
    with pytest.raises(MemoryDocumentError, match="memory_storage_unavailable"):
        await second.index()


async def test_native_commit_cannot_follow_link_outside_corpus(stores, tmp_path):
    from a13n_harness.providers.environment.files import FileCommitCondition, FileCommitRequest, FileCommitWrite
    from a13n_harness.providers.environment.models import EnvironmentError

    store = stores[0]
    workspace = tmp_path / "workspace"
    outside = workspace / "outside"
    outside.mkdir()
    corpus = workspace / store.subject_root.removeprefix("/")
    (corpus / "link").symlink_to(outside, target_is_directory=True)
    target = store.subject_root + "/link/head"
    with pytest.raises(EnvironmentError) as rejected:
        await store.files.commit(
            FileCommitRequest(
                root=store.subject_root,
                conditions=(FileCommitCondition(path=target),),
                writes=(FileCommitWrite(path=target, text="private"),),
            )
        )
    assert rejected.value.code == "environment_denied"
    assert not (outside / "head").exists()
