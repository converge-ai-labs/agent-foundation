"""Completion admission, worker recovery, current policy and native publication."""

from contextlib import asynccontextmanager
from hashlib import sha256
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.providers.memory.builtins import FILESYSTEM
from a13n_harness.providers.memory.contracts import MemoryScope
from a13n_harness.providers.memory.documents import DocumentInput
from a13n_harness.providers.memory.filesystem.commit import EnvironmentMemoryFileCoordinator
from a13n_harness.providers.memory.filesystem.store import FilesystemMemoryStore
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.digests import digest_request
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.state import CompletedOutcomeCandidate
from a13n_service.memory.domain import MemoryEntries, MemoryEntrySelection
from a13n_service.memory.models import MemoryOrganizationRecord, MemoryStorageRecord, RunMemoryStorageRecord
from a13n_service.memory.organization import MemoryOrganizer, admit_organization
from a13n_service.memory.scopes import MemoryAuthorizer, memory_subject
from a13n_service.memory.service import MemoryService
from a13n_service.storage import short_session, transaction
from pydantic_ai.models.test import TestModel
from sqlalchemy import select
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID, effective_agent_config
from tests.interactions.test_attempt_execution import _accept_root, _authority, _completed_state, _worker
from tests.models.conftest import protector

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("disable", [False, True, "deleted"])
async def test_completed_work_is_admitted_once_and_rechecks_organization_policy(
    interaction_sessions,
    interaction_object_store,
    tmp_path,
    disable,
):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    entry = MemoryEntrySelection(
        name="project",
        mode="documents",
        description="Decisions",
        backend={"type": "filesystem"},
        auto_organize=True,
        scope="thread",
    )
    selection = MemoryEntries(entries=(entry,))
    config = effective_agent_config().model_copy(update={"memory": selection})
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    states, run, state = await _accept_root(sessions, interaction_object_store, config=config)
    catalog = ProviderCatalog((FILESYSTEM,))
    memory = MemoryService(catalog, protector(), MemoryAuthorizer(sessions, catalog))
    store_id = "mstore_1234567890abcdef"
    subject = memory_subject(ORGANIZATION_ID, WORKSPACE_ID, "filesystem", MemoryScope.THREAD, run.thread_id).value
    files = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        mount_id="test",
        generation="one",
    )

    async def allow(_):
        pass

    store = FilesystemMemoryStore(
        files=files,
        root="/memory",
        scope=subject,
        store_id=store_id,
        principal=run.authority_principal.principal_id,
        authorize=allow,
        authorize_sources=allow,
    )
    store.coordinator = EnvironmentMemoryFileCoordinator(
        files, root=store.subject_root, store_id=store_id, scope=subject
    )
    await store.initialize()

    class ExistingFiles:
        @asynccontextmanager
        async def open(self, **kwargs):
            await kwargs["authorize"]()
            yield files
            await kwargs["authorize"]()

    memory.files = ExistingFiles()
    async with transaction(sessions) as session:
        agent = await session.get(AgentRecord, run.agent_id)
        revision = await session.get(AgentRevisionRecord, agent.default_revision_id)
        revision.config = {**revision.config, "memory": selection.model_dump(mode="json")}
        session.add(
            MemoryStorageRecord(
                id=store_id,
                target_digest="a" * 64,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                provider_identity="filesystem",
                subject=subject,
                scope_kind="thread",
                subject_id=run.thread_id,
                environment_id="env_1234567890abcdef",
                root="/memory",
                backing_identity="test:1",
                initialized=True,
            )
        )
        await session.flush()
        session.add(
            RunMemoryStorageRecord(
                run_id=run.id,
                selection_digest="b" * 64,
                storage_id=store_id,
                organization_policy={
                    "agent_id": run.agent_id,
                    "entry": entry.model_dump(mode="json"),
                    "erasure_digest": sha256(b"").hexdigest(),
                },
            )
        )
    lifecycle = LifecycleWriter((admit_organization,))
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=lifecycle).claim(run.id, _worker())
    context = _authority(claim)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW, lifecycle=lifecycle)
    preparation = await execution.commit_preparation_success(context)
    await execution.enter_harness(context, preparation=preparation, harness_run_id="test")
    candidate = _completed_state(
        state,
        claim.attempt.id,
        claim.attempt.attempt_number,
        outcome=CompletedOutcomeCandidate(
            output="We decided to use Python 3.13.", output_text="We decided to use Python 3.13."
        ),
    )
    stored = await execution.publish_checkpoint(context, states, await states.read(ORGANIZATION_ID, run.id), candidate)
    await RunOutcomeService(
        sessions, RunPayloadStore(interaction_object_store), clock=lambda: NOW, lifecycle=lifecycle
    ).commit_state_outcome(context, stored)
    async with transaction(sessions) as session:
        await admit_organization(session, Mock(event_type="run.completed", run_id=run.id))
        work = (await session.scalars(select(MemoryOrganizationRecord))).one()
        work_id = work.id
        if disable is True:
            revision = await session.get(AgentRevisionRecord, run.agent_revision_id)
            revision.config = {**revision.config, "memory": None}
    if disable == "deleted":
        forgotten = await store.create_document(
            DocumentInput(
                kind="semantic",
                title="Runtime",
                description="Decision",
                text="Use Python 3.13.",
                path="semantic/runtime.md",
            ),
            request_key="forgotten",
        )
        await store.delete(forgotten.document.id)
    models = Mock()
    models.build = AsyncMock(
        return_value=TestModel(
            custom_output_args={
                "candidates": [
                    {
                        "decision": "create",
                        "reason": "Explicit decision",
                        "confidence": 0.99,
                        "document": {
                            "kind": "semantic",
                            "title": "Runtime",
                            "description": "Project runtime",
                            "text": "Use Python 3.13.",
                            "path": "semantic/runtime.md",
                        },
                    }
                ]
            }
        )
    )
    providers = Mock(resolve=AsyncMock(return_value=Mock()))
    worker = MemoryOrganizer(memory, interaction_object_store, models, providers)
    if not disable:
        organize = worker.organize

        async def checked(*args):
            try:
                return await organize(*args)
            except Exception as error:
                pytest.fail(f"Unexpected organization failure: {error!r}")

        worker.organize = checked
    await worker.run_once()
    await worker.run_once()
    async with short_session(sessions) as session:
        work = await session.get(MemoryOrganizationRecord, work_id)
        assert work.status == ("cancelled" if disable else "completed")
        assert work.attempts == 1
    assert len((await store.list_documents())[0]) == (0 if disable else 1)
    assert models.build.await_count == (0 if disable else 1)
