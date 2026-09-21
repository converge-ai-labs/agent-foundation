"""Storage identity is durable and runtime access uses current Attempt authority."""

import os
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities.memory import MemoryCapability
from a13n_harness.errors import RunError
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.memory.builtins import FILESYSTEM
from a13n_harness.providers.memory.documents import DocumentInput
from a13n_service.memory.domain import MemoryEntrySelection
from a13n_service.memory.file_runtime import filesystem_store
from a13n_service.memory.models import MemoryStorageRecord, RunMemoryStorageRecord
from a13n_service.memory.scopes import MemoryAuthorizer
from a13n_service.memory.service import MemoryService
from a13n_service.storage import short_session
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select
from tests.interactions.conftest import (
    NOW,
    WORKSPACE_ID,
)
from tests.interactions.worker_helpers import accepted_running_attempt
from tests.models.conftest import protector

pytestmark = pytest.mark.anyio


async def test_file_binding_reconnect_and_marker_loss(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    executable = os.environ.get("A13N_ENVD_TEST_BINARY")
    if not executable:
        pytest.skip("Set A13N_ENVD_TEST_BINARY for native memory execution")
    run, attempt = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    # Environment acquisition has its own authorization tests. This component
    # receives a Host-prepared adapter and the accepted logical selection.
    run = run.model_copy(update={"environment_id": "env_1234567890abcdef"})
    monkeypatch.setattr("a13n_service.memory.file_runtime.utc_now", lambda: NOW)
    catalog = ProviderCatalog((FILESYSTEM,))
    service = MemoryService(catalog, protector(), MemoryAuthorizer(interaction_sessions, catalog))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = LOCAL_ENVD
    configuration = provider.validate_environment({"working_directory": str(workspace)})
    entry = MemoryEntrySelection(
        name="project",
        mode="documents",
        description="Project decisions",
        backend={
            "type": "filesystem",
            "configuration": {"storage": {"root": str(workspace / "memory")}},
        },
    )
    captured = []

    async def factory(context):
        store = await filesystem_store(
            context,
            service,
            run=run,
            workspace_id=WORKSPACE_ID,
            agent_id=run.agent_id,
            entry=entry,
            current_context=lambda: attempt,
        )
        captured.append(store)
        if len(captured) == 1:
            await store.create_document(
                DocumentInput(
                    kind="semantic",
                    title="Decision",
                    description="Runtime",
                    text="Use Python",
                    path="semantic/runtime.md",
                ),
                request_key="seed",
            )
        return store

    async def model(messages, info):
        assert "Decision" in repr(messages)
        assert "memory_revise" in {tool.name for tool in info.function_tools}
        yield "done"

    async def execute():
        async with LocalEnvdProviderRuntime(
            executable=Path(executable),
            allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        ) as runtime:
            environment = provider.construct(
                operation_id="op-test",
                allow_create=True,
                environment_id=run.environment_id,
                configuration=configuration,
                state=None,
                runtime=runtime,
            )
            harness = HarnessBuilder().build(
                AgentSpec(),
                model=FunctionModel(stream_function=model),
                output_type=str,
                capabilities=(MemoryCapability(document_factory=factory, recall_required=True),),
            )
            return await harness.run("Recall project", environment=environment)

    assert (await execute()).output_or_raise() == "done"
    assert (await execute()).output_or_raise() == "done"
    assert captured[0].store_id == captured[1].store_id
    async with short_session(interaction_sessions) as session:
        bindings = (
            await session.scalars(select(RunMemoryStorageRecord).where(RunMemoryStorageRecord.run_id == run.id))
        ).all()
        assert len(bindings) == 1
        storage = await session.get(MemoryStorageRecord, bindings[0].storage_id)
        assert storage.initialized
    marker = next(workspace.rglob("store.json"))
    marker.unlink()
    with pytest.raises(RunError, match="Required memory index is unavailable"):
        await execute()
    assert not marker.exists()
