import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_environment import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import EnvironmentAccess, EnvironmentMount, HarnessBuilder, RunBindings
from a13n_service.assets.domain import AssetRef, AssetSourceKind
from a13n_service.assets.errors import AssetError
from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.publication import AgentAssetPublisher, AssetCapability, AssetPublicationScope
from a13n_service.assets.service import AssetService
from a13n_service.assets.staging import AssetStaging
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import SecurityAuditRecord, UserRecord
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import func, select

from tests.agents.test_reconstruction import _config, _effective, _reconstruct
from tests.hooks.support import hook_actor

from .conftest import AGENT_ID, NOW, USER_ID, WORKSPACE_ID
from .worker_helpers import accepted_running_attempt

pytestmark = pytest.mark.anyio


async def _runtime(sessions, objects, tmp_path, *, max_size=1024):
    _, context = await accepted_running_attempt(sessions, objects)
    staging = await AssetStaging.create(tmp_path)
    asset_objects = AssetObjectStore(objects, staging)
    publisher = AgentAssetPublisher(sessions, asset_objects, staging, max_size_bytes=max_size, clock=lambda: NOW)
    service = AssetService(sessions, asset_objects, staging, max_size_bytes=max_size, clock=lambda: NOW)
    return publisher, AssetPublicationScope(lambda: context, WORKSPACE_ID, AGENT_ID), service


async def _body(value=b"published-content"):
    yield value


async def test_harness_publishes_environment_file_and_service_reads_run_output(
    interaction_sessions, interaction_object_store, tmp_path
):
    publisher, scope, service = await _runtime(interaction_sessions, interaction_object_store, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_text("published-content")
    environment = DirectLocalEnvironmentProvider().create_environment(
        configuration=DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=workspace)),
        environment_id="publication-workspace",
        state=None,
    )
    definition = _reconstruct(_effective(_config()), capability_provider=lambda _: (AssetCapability(publisher, scope),))
    published = []

    async def model(messages, info):
        assert "publish_asset" in {tool.name for tool in info.function_tools}
        results = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "publish_asset"
        ]
        if not results:
            yield {
                0: DeltaToolCall(
                    name="publish_asset",
                    json_args=json.dumps({"path": "/workspace/report.txt"}),
                    tool_call_id="model-chosen-id",
                )
            }
        else:
            published.append(AssetRef.model_validate(results[-1].content))
            yield "published"

    async def resolver(ctx, model_id):
        return FunctionModel(stream_function=model)

    result = (
        await HarnessBuilder()
        .build(definition)
        .run(
            "Publish the report.",
            environment=EnvironmentMount(environment, access=EnvironmentAccess.READ_ONLY),
            bindings=RunBindings.embedded(model_resolver=resolver),
        )
    )
    assert result.output_or_raise() == "published"
    asset = await service.get(actor=hook_actor(), asset_id=published[0].asset_id)
    assert asset.source.kind == "run_output" and asset.source.run_id == scope.current_attempt().run_id
    page = await service.list(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=1,
        cursor=None,
        source_kind=AssetSourceKind.run_output,
        source_run_id=scope.current_attempt().run_id,
    )
    assert page.items == (asset,)
    prepared = await service.prepare_content(actor=hook_actor(), asset_id=asset.id)
    try:
        assert b"".join([chunk async for chunk in prepared.content.chunks()]) == b"published-content"
    finally:
        await prepared.content.remove()
    async with short_session(interaction_sessions) as database:
        row = await database.get(AssetRecord, asset.id)
        assert row.source_run_attempt_id == scope.current_attempt().run_attempt_id
        assert row.source_invocation_id != "model-chosen-id"
        audit = await database.scalar(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == asset.id))
        assert audit.details == {
            "source_kind": "run_output",
            "run_id": scope.current_attempt().run_id,
            "run_attempt_id": scope.current_attempt().run_attempt_id,
        }


async def test_publication_reconciles_exact_invocation_and_rejects_changed_content(
    interaction_sessions, interaction_object_store, tmp_path
):
    publisher, scope, service = await _runtime(interaction_sessions, interaction_object_store, tmp_path)
    first = await publisher.publish(
        scope, invocation_id="tool-first", filename="report.txt", media_type=None, body=_body()
    )
    replay = await publisher.publish(
        scope, invocation_id="tool-first", filename="report.txt", media_type=None, body=_body()
    )
    assert replay == first
    with pytest.raises(AssetError) as conflict:
        await publisher.publish(
            scope, invocation_id="tool-first", filename="report.txt", media_type=None, body=_body(b"different")
        )
    assert conflict.value.code == "asset_idempotency_conflict"
    second = await publisher.publish(
        scope, invocation_id="tool-second", filename="report.txt", media_type=None, body=_body()
    )
    assert second.asset_id != first.asset_id
    await service.delete(actor=hook_actor(), asset_id=first.asset_id)
    tombstone_replay = await publisher.publish(
        scope, invocation_id="tool-first", filename="report.txt", media_type=None, body=_body()
    )
    assert tombstone_replay == first
    with pytest.raises(AssetError):
        await service.get(actor=hook_actor(), asset_id=first.asset_id)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("oversize", AssetError),
        ("media", AssetError),
        ("revoked", AuthorizationError),
        ("lost-fence", AttemptAuthorityError),
        ("expired", AttemptAuthorityError),
    ],
)
async def test_publication_rechecks_authority_after_streaming_and_never_commits_invalid_content(
    interaction_sessions, interaction_object_store, tmp_path, failure, error_type
):
    publisher, scope, _ = await _runtime(interaction_sessions, interaction_object_store, tmp_path, max_size=16)

    async def source():
        yield b"%PDF-1.7" if failure == "media" else b"first"
        async with transaction(interaction_sessions) as database:
            if failure == "revoked":
                user = await database.get(UserRecord, USER_ID)
                user.status = "disabled"
            if failure == "lost-fence":
                run = await database.get(RunRecord, scope.current_attempt().run_id)
                run.current_run_attempt_id = None
        yield b"x" * 17 if failure == "oversize" else b"tail"

    if failure == "expired":
        publisher._clock = lambda: NOW + timedelta(hours=1)
    with pytest.raises(error_type):
        await publisher.publish(
            scope,
            invocation_id="tool-invalid",
            filename="report.txt",
            media_type="image/png" if failure == "media" else None,
            body=source(),
        )
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(AssetRecord)) == 0


async def test_wrong_workspace_and_forged_attempt_cannot_publish(
    interaction_sessions, interaction_object_store, tmp_path
):
    publisher, scope, _ = await _runtime(interaction_sessions, interaction_object_store, tmp_path)
    wrong_scope = replace(scope, workspace_id="ws_other1234567890")
    with pytest.raises(AssetError):
        await publisher.publish(
            wrong_scope, invocation_id="tool-invalid", filename="report.txt", media_type=None, body=_body()
        )
    wrong_attempt = replace(scope.current_attempt(), attempt_number=scope.current_attempt().attempt_number + 1)
    with pytest.raises(AttemptAuthorityError):
        await publisher.publish(
            replace(scope, current_attempt=lambda: wrong_attempt),
            invocation_id="tool-invalid",
            filename="report.txt",
            media_type=None,
            body=_body(),
        )


async def test_postgresql_concurrent_publication_reconciles_one_asset(
    postgres_interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    publisher, scope, _ = await _runtime(postgres_interaction_sessions, interaction_object_store, tmp_path)
    publish = publisher._objects.publish
    arrived = 0
    ready = asyncio.Event()

    async def synchronized_publish(**kwargs):
        nonlocal arrived
        await publish(**kwargs)
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=10)

    monkeypatch.setattr(publisher._objects, "publish", synchronized_publish)
    results = await asyncio.gather(
        *(
            publisher.publish(
                scope, invocation_id="tool-concurrent", filename="report.txt", media_type=None, body=_body()
            )
            for _ in range(2)
        )
    )
    assert results[0] == results[1]
    async with short_session(postgres_interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(AssetRecord)) == 1


async def test_final_publication_transaction_rejects_lost_fence_after_object_upload(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    publisher, scope, _ = await _runtime(interaction_sessions, interaction_object_store, tmp_path)
    publish = publisher._objects.publish

    async def lose_fence(**kwargs):
        await publish(**kwargs)
        async with transaction(interaction_sessions) as database:
            run = await database.get(RunRecord, scope.current_attempt().run_id)
            run.current_run_attempt_id = None

    monkeypatch.setattr(publisher._objects, "publish", lose_fence)
    with pytest.raises(AttemptAuthorityError):
        await publisher.publish(
            scope, invocation_id="tool-lost-after-upload", filename="report.txt", media_type=None, body=_body()
        )
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(AssetRecord)) == 0
