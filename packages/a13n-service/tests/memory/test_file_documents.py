"""Management and runtime share exact file identities, version checks, and authority."""

from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.document_memory import DocumentInput, Replace
from a13n_harness.filesystem_memory import FilesystemMemoryStore
from a13n_harness.memory import MemoryScope
from a13n_harness.memory_file_commit import EnvironmentMemoryFileCoordinator
from a13n_harness.memory_plugins import FilesystemMemoryBackendPlugin, MemoryBackendCatalog
from a13n_service.application_errors import ApplicationError
from a13n_service.iam import AuthorizationError
from a13n_service.memory import document_router
from a13n_service.memory.documents import FileDocuments
from a13n_service.memory.models import MemoryStorageRecord
from a13n_service.memory.scopes import MemoryAuthorizer, memory_subject
from a13n_service.memory.service import MemoryService
from a13n_service.storage import transaction
from fastapi import Response
from tests.models.conftest import ORG_ID, WORKSPACE_ID, actor, protector

pytestmark = pytest.mark.anyio


async def test_manage_retained_scope_and_reject_another_user(memory_sessions, tmp_path, monkeypatch):
    catalog = MemoryBackendCatalog((FilesystemMemoryBackendPlugin(),))
    service = MemoryService(catalog, protector(), MemoryAuthorizer(memory_sessions, catalog))
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        mount_id="memory",
        generation="one",
    )
    store_id = "mstore_1234567890abcdef"
    principal = actor()
    subject = memory_subject(
        ORG_ID, WORKSPACE_ID, "a13n.filesystem", MemoryScope.USER, principal.principal.principal_id
    ).value

    async def allow(_):
        pass

    store = FilesystemMemoryStore(
        files=files,
        root="/memory",
        scope=subject,
        store_id=store_id,
        principal=principal.principal.principal_id,
        authorize=allow,
        authorize_sources=allow,
    )
    store.coordinator = EnvironmentMemoryFileCoordinator(
        files, root=store.subject_root, store_id=store_id, scope=subject
    )
    await store.initialize()

    class ExistingFiles:
        @asynccontextmanager
        async def open(self, *, actor, environment_id, backing_identity, write, authorize):
            assert environment_id == "env_1234567890abcdef" and backing_identity == "env_1234567890abcdef:1"
            await authorize()
            yield files
            await authorize()

    service.files = ExistingFiles()
    async with transaction(memory_sessions) as session:
        session.add(
            MemoryStorageRecord(
                id=store_id,
                target_digest="f" * 64,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                provider_identity="a13n.filesystem",
                subject=subject,
                scope_kind="user",
                subject_id=principal.principal.principal_id,
                environment_id="env_1234567890abcdef",
                root="/memory",
                backing_identity="env_1234567890abcdef:1",
                initialized=True,
            )
        )
    documents = FileDocuments(service)
    assert [item.id for item in (await documents.scopes(principal, WORKSPACE_ID)).items] == [store_id]
    async with documents.open(principal, WORKSPACE_ID, store_id, write=True) as current:
        saved = await current.create_document(
            DocumentInput(
                kind="semantic", title="Runtime", description="Decision", text="Python", path="semantic/runtime.md"
            ),
            request_key="management",
        )
    assert (await store.document(saved.document.id)).text == "Python"
    async with documents.open(principal, WORKSPACE_ID, store_id, write=True) as current:
        result = await current.revise(
            saved.document.id,
            expected_version=1,
            change=Replace(type="replace", text="Python 3.13"),
            request_key="edit",
        )
        assert result.document.version == 2
    assert len(await store.history(saved.document.id)) == 2
    monkeypatch.setattr(document_router, "documents", lambda _: documents)
    response = Response()
    public = await document_router.read(Mock(), response, principal, WORKSPACE_ID, store_id, saved.document.id)
    assert public.version == 2
    assert not {"scope", "store_id", "principal"}.intersection(public.model_dump())
    edit = document_router.ReviseDocument(expected_version=2, change=Replace(type="replace", text="Python 3.14"))
    for _ in range(2):
        replay = await document_router.revise(
            Mock(), principal, WORKSPACE_ID, store_id, saved.document.id, edit, "console-edit", response.headers["ETag"]
        )
        assert replay.document.version == 3
    with pytest.raises(ApplicationError) as stale:
        await document_router.revise(
            Mock(),
            principal,
            WORKSPACE_ID,
            store_id,
            saved.document.id,
            edit,
            "different-edit",
            response.headers["ETag"],
        )
    assert stale.value.category.value == "stale_version"
    async with transaction(memory_sessions) as session:
        binding = await session.get(MemoryStorageRecord, store_id)
        binding.subject_id = "usr_another_user123456"
    with pytest.raises(AuthorizationError):
        async with documents.open(principal, WORKSPACE_ID, store_id):
            pytest.fail("Another user's store must not be opened")
