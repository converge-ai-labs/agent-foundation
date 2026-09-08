from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import anyio
import httpx2
import pytest
from a2a.types import a2a_pb2 as a2a
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.api import install_api_conventions
from a13n_service.application_errors import ErrorCategory
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.staging import AssetStaging
from a13n_service.assets.uploads import AssetUploadService
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.a2a import A2AError, A2AService
from a13n_service.gateway.a2a_import import A2APartImporter
from a13n_service.gateway.a2a_push import (
    A2APushMaterial,
    A2APushPublisher,
    _event_status,
)
from a13n_service.gateway.a2a_router import router as a2a_router
from a13n_service.gateway.models import (
    A2AContextBindingRecord,
    A2AMessageBindingRecord,
    A2APushConfigurationRecord,
    A2ATaskBindingRecord,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.agents.conftest import agent_config
from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW

pytestmark = pytest.mark.anyio


def _protector() -> SecretProtector:
    return SecretProtector(key=b"a" * 32, encryption_key_id="test-key")


async def test_push_waiting_authentication_projects_auth_required() -> None:
    event = cast(
        Any,
        SimpleNamespace(
            id="lev_auth_waiting",
            event_type="run.waiting",
            payload={"wait_reason": "authentication", "pending": {}},
        ),
    )

    assert _event_status(event).state == a2a.TASK_STATE_AUTH_REQUIRED


def _request(
    *,
    message_id: str = "message-1",
    text: str = "hello",
    context_id: str = "",
    task_id: str = "",
) -> a2a.SendMessageRequest:
    return a2a.SendMessageRequest(
        message=a2a.Message(
            message_id=message_id,
            context_id=context_id,
            task_id=task_id,
            role=a2a.ROLE_USER,
            parts=[a2a.Part(text=text)],
        ),
        configuration=a2a.SendMessageConfiguration(return_immediately=True),
    )


async def _service(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    *,
    maximum_wait_seconds: float = 0.02,
    import_http_client: httpx2.AsyncClient | None = None,
) -> tuple[A2AService, LocalObjectStore]:
    objects = await LocalObjectStore.create(tmp_path / "a2a-objects")
    staging = await AssetStaging.create(tmp_path / "a2a-files")
    uploads = AssetUploadService(
        sessions,
        AssetObjectStore(objects, staging),
        staging,
        max_size_bytes=1024 * 1024,
        clock=lambda: NOW,
    )
    assets = AssetCatalog(sessions, AssetObjectStore(objects, staging), clock=lambda: NOW)
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]), assets=assets)
    return (
        A2AService(
            sessions,
            commands,
            _protector(),
            EndpointPolicy(require_https=True),
            A2APartImporter(
                uploads,
                import_http_client,
                EndpointPolicy(),
                max_redirects=2,
                timeout_seconds=1,
            ),
            poll_interval_seconds=0.001,
            maximum_wait_seconds=maximum_wait_seconds,
            push_drain_timeout_seconds=0.1,
            clock=lambda: NOW,
        ),
        objects,
    )


async def test_initial_message_atomically_binds_task_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()

    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert repeated == first
    assert first.status.state == a2a.TASK_STATE_SUBMITTED
    assert first.context_id.startswith("a2actx_")
    assert first.history[0].message_id == "message-1"
    async with short_session(lifecycle_interaction_sessions) as database:
        context = await database.scalar(select(A2AContextBindingRecord))
        task = await database.scalar(select(A2ATaskBindingRecord))
        message = await database.scalar(select(A2AMessageBindingRecord))
        run = await database.get(RunRecord, task.current_run_id) if task else None
    assert context is not None
    assert task is not None
    assert message is not None
    assert run is not None
    assert task.run_ids_json == [run.id]
    assert message.run_id == run.id


@pytest.mark.parametrize("operation", ["send", "push"])
async def test_protocol_tenant_selection_is_rejected_before_mutation(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    operation: str,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)

    with pytest.raises(A2AError) as captured:
        if operation == "send":
            request = _request()
            request.tenant = "external-tenant"
            await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
        else:
            await service.create_push_configuration(
                actor=_actor(),
                agent_id=AGENT_ID,
                task_id="unselected-task",
                requested=a2a.TaskPushNotificationConfig(tenant="external-tenant"),
            )

    assert captured.value.code == "tenant_not_supported"
    assert captured.value.category is ErrorCategory.invalid_request
    async with short_session(lifecycle_interaction_sessions) as database:
        for record_type in (A2ATaskBindingRecord, RunRecord, A2APushConfigurationRecord):
            assert await database.scalar(select(record_type.id)) is None


async def test_send_rejects_unavailable_accepted_output_modes_before_mutation(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()
    request.configuration.accepted_output_modes.append("application/octet-stream")

    with pytest.raises(A2AError) as captured:
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert captured.value.code == "output_mode_not_supported"
    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.scalar(select(A2ATaskBindingRecord.id)) is None
        assert await database.scalar(select(RunRecord.id)) is None


async def test_message_id_reuse_with_changed_content_conflicts(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())

    with pytest.raises(A2AError) as captured:
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request(text="different"))

    assert captured.value.code == "message_id_conflict"


async def test_send_history_length_applies_to_acceptance_and_idempotent_replay(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()
    request.configuration.history_length = 0

    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert not first.history
    assert not repeated.history


async def test_raw_part_is_atomically_imported_as_asset_and_replayed_without_republication(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()
    request.message.parts.append(
        a2a.Part(raw=b"%PDF-1.7\nA2A raw part\n", filename="brief.pdf", media_type="application/pdf")
    )

    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert repeated == first
    async with short_session(lifecycle_interaction_sessions) as database:
        assets = tuple((await database.scalars(select(AssetRecord))).all())
        task = await database.get(A2ATaskBindingRecord, first.id)
        run = await database.get(RunRecord, task.current_run_id) if task is not None else None
    assert len(assets) == 1
    assert assets[0].filename == "brief.pdf"
    assert assets[0].media_type == "application/pdf"
    assert run is not None
    assert run.input_json["content"][1] == {
        "type": "binary",
        "source": {"type": "asset", "asset_id": assets[0].id},
        "delivery": "model_content",
    }


async def test_asset_and_success_audit_rollback_with_failed_message_acceptance(
    lifecycle_interaction_sessions, tmp_path, monkeypatch
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    persist = AssetUploadService.commit_protocol_imports_in_transaction

    async def fail_after_assets(self, session, **kwargs):
        await persist(self, session, **kwargs)
        assert await session.scalar(select(AssetRecord.id)) is not None
        raise RuntimeError("message acceptance failed after Asset flush")

    monkeypatch.setattr(AssetUploadService, "commit_protocol_imports_in_transaction", fail_after_assets)
    request = _request()
    request.message.parts.append(a2a.Part(raw=b"candidate", filename="candidate.bin"))
    with pytest.raises(RuntimeError, match="after Asset flush"):
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    async with short_session(lifecycle_interaction_sessions) as session:
        assert await session.scalar(select(AssetRecord.id)) is None
        assert await session.scalar(select(RunRecord.id)) is None
        assert await session.scalar(select(A2AMessageBindingRecord.id)) is None
        assert (
            await session.scalar(select(SecurityAuditRecord.id).where(SecurityAuditRecord.action == "asset.create"))
            is None
        )


async def test_url_part_follows_bounded_redirect_and_is_not_refetched_on_replay(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"Location": "/content"})
        return httpx2.Response(
            200,
            content=b"%PDF-1.7\nA2A URL part\n",
            headers={"Content-Length": "22"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        service, _objects = await _service(
            lifecycle_interaction_sessions,
            tmp_path,
            import_http_client=client,
        )
        request = _request()
        request.message.ClearField("parts")
        request.message.parts.append(
            a2a.Part(url="https://8.8.8.8/start", filename="remote.pdf", media_type="application/pdf")
        )
        first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
        repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert repeated == first
    assert [request.url.path for request in requests] == ["/start", "/content"]
    assert all("authorization" not in request.headers and "cookie" not in request.headers for request in requests)
    async with short_session(lifecycle_interaction_sessions) as database:
        assets = tuple((await database.scalars(select(AssetRecord))).all())
    assert len(assets) == 1
    assert assets[0].filename == "remote.pdf"


async def test_failed_raw_part_acceptance_removes_unowned_candidate(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request(task_id="missing-task")
    request.message.ClearField("parts")
    request.message.parts.append(a2a.Part(raw=b"candidate", filename="candidate.bin"))

    with pytest.raises(A2AError) as captured:
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert captured.value.code == "task_not_found"
    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.scalar(select(AssetRecord.id)) is None
    assert not (await objects.list()).items


async def test_completed_task_projects_artifact_and_context_accepts_next_task(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, first.id)
    assert binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)

    completed = await service.get_task(actor=_actor(), agent_id=AGENT_ID, task_id=first.id)
    second = await service.send(
        actor=_actor(),
        agent_id=AGENT_ID,
        request=_request(message_id="message-2", text="again", context_id=first.context_id),
    )

    assert completed.status.state == a2a.TASK_STATE_COMPLETED
    assert completed.artifacts[0].parts[0].WhichOneof("content") == "data"
    assert second.id != first.id
    assert second.context_id == first.context_id
    assert second.status.state == a2a.TASK_STATE_SUBMITTED


async def test_list_tasks_pages_by_status_timestamp_and_applies_projection_options(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    async with short_session(lifecycle_interaction_sessions) as database:
        first_binding = await database.get(A2ATaskBindingRecord, first.id)
    assert first_binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=first_binding.current_run_id)
    second = await service.send(
        actor=_actor(),
        agent_id=AGENT_ID,
        request=_request(message_id="message-2", text="again", context_id=first.context_id),
    )

    first_page = await service.list_tasks(
        actor=_actor(),
        agent_id=AGENT_ID,
        context_id=first.context_id,
        status=None,
        page_size=1,
        page_token=None,
        history_length=0,
        status_timestamp_after=None,
        include_artifacts=False,
    )
    assert first_page.total_size == 2
    assert first_page.tasks[0].id == first.id
    assert not first_page.tasks[0].history
    assert not first_page.tasks[0].artifacts
    assert first_page.next_page_token is not None

    second_page = await service.list_tasks(
        actor=_actor(),
        agent_id=AGENT_ID,
        context_id=first.context_id,
        status=None,
        page_size=1,
        page_token=first_page.next_page_token,
        history_length=0,
        status_timestamp_after=None,
        include_artifacts=False,
    )
    assert second_page.total_size == 2
    assert tuple(task.id for task in second_page.tasks) == (second.id,)
    assert second_page.next_page_token is None

    submitted = await service.list_tasks(
        actor=_actor(),
        agent_id=AGENT_ID,
        context_id=None,
        status=a2a.TASK_STATE_SUBMITTED,
        page_size=50,
        page_token=None,
        history_length=1,
        status_timestamp_after=NOW - timedelta(seconds=1),
        include_artifacts=True,
    )
    assert tuple(task.id for task in submitted.tasks) == (second.id,)
    assert tuple(message.message_id for message in submitted.tasks[0].history) == ("message-2",)
    with pytest.raises(A2AError) as captured:
        await service.list_tasks(
            actor=_actor(),
            agent_id=AGENT_ID,
            context_id=first.context_id,
            status=None,
            page_size=1,
            page_token=first_page.next_page_token,
            history_length=0,
            status_timestamp_after=None,
            include_artifacts=True,
        )
    assert captured.value.code == "invalid_page_token"


async def test_stream_delivers_complete_artifact_before_terminal_status(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path, maximum_wait_seconds=1)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, task.id)
    assert binding is not None
    events: list[a2a.StreamResponse] = []

    async def collect() -> None:
        events.extend(
            [event async for event in service.stream_task(actor=_actor(), agent_id=AGENT_ID, task_id=task.id)]
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(collect)
        await anyio.sleep(0.01)
        await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)

    kinds = tuple(event.WhichOneof("payload") for event in events)
    assert kinds[0] == "task"
    assert kinds[-2:] == ("artifact_update", "status_update")
    assert events[-2].artifact_update.append is False
    assert events[-2].artifact_update.last_chunk is True
    assert events[-1].status_update.status.state == a2a.TASK_STATE_COMPLETED
    final = await service.get_task(actor=_actor(), agent_id=AGENT_ID, task_id=task.id)
    assert events[-2].artifact_update.artifact == final.artifacts[0]


async def test_list_tasks_http_binding_preserves_empty_page_token_and_a2a_query_errors(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    process_runtime_factory,
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(a2a_router)

    async def authenticate(_request: Request):
        return _actor()

    runtime = process_runtime_factory(
        request_authenticator=authenticate,
        gateway=SimpleNamespace(a2a=service),
    )
    app.state.runtime = runtime
    app.state.settings = runtime.settings
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            f"/a2a/v1/agents/{AGENT_ID}/tasks",
            headers={"A2A-Version": "1.0"},
            params={"pageSize": "1", "historyLength": "0", "includeArtifacts": "false"},
        )
        invalid = await client.get(
            f"/a2a/v1/agents/{AGENT_ID}/tasks",
            headers={"A2A-Version": "1.0"},
            params={"includeArtifacts": "TRUE"},
        )
        invalid_shape = await client.get(
            f"/a2a/v1/agents/{AGENT_ID}/tasks/{task.id}",
            headers={"A2A-Version": "1.0"},
            params={"historyLength": "12345678901"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "tasks": [
            {
                "id": task.id,
                "contextId": task.context_id,
                "status": response.json()["tasks"][0]["status"],
            }
        ],
        "pageSize": 1,
        "totalSize": 1,
        "nextPageToken": "",
    }
    assert invalid.status_code == 400
    assert invalid.headers["content-type"].startswith("application/a2a+json")
    assert invalid.json()["error"]["details"][0]["reason"] == "INVALID_QUERY_PARAMETER"
    assert invalid_shape.status_code == 400
    assert invalid_shape.headers["content-type"].startswith("application/a2a+json")
    assert invalid_shape.json()["error"]["details"][0]["reason"] == "INVALID_REQUEST"


async def test_a2a_authentication_failure_uses_a2a_error_envelope(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    process_runtime_factory,
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(a2a_router)
    runtime = process_runtime_factory(request_authenticator=None, gateway=SimpleNamespace(a2a=service))
    app.state.runtime = runtime
    app.state.settings = runtime.settings

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            f"/a2a/v1/agents/{AGENT_ID}/tasks",
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/a2a+json")
    assert response.json()["error"]["status"] == "UNAUTHENTICATED"
    assert response.json()["error"]["details"][0]["reason"] == "AUTHENTICATION_REQUIRED"


async def test_cancel_task_uses_durable_interrupt(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())

    cancelled = await service.cancel_task(actor=_actor(), agent_id=AGENT_ID, task_id=task.id)

    assert cancelled.status.state == a2a.TASK_STATE_CANCELED


async def test_public_card_projects_current_agent_protocol(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    async with transaction(lifecycle_interaction_sessions) as database:
        revision = await database.get(AgentRevisionRecord, _frozen().agent_revision_id)
        assert revision is not None
        revision.config = agent_config().model_dump(mode="json")

    card = await service.public_agent_card(agent_id=AGENT_ID, base_url="https://foundation.example")

    assert card.name
    assert card.supported_interfaces[0].url == f"https://foundation.example/a2a/v1/agents/{AGENT_ID}"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.capabilities.streaming
    assert card.capabilities.push_notifications


async def test_send_message_atomically_registers_push_configuration_and_replays_it(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()
    request.configuration.task_push_notification_config.CopyFrom(
        a2a.TaskPushNotificationConfig(
            url="https://8.8.8.8/a2a-events",
            token="opaque-client-token",
            authentication=a2a.AuthenticationInfo(scheme="Bearer", credentials="secret-credential"),
        )
    )

    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    configurations, next_page_token = await service.list_push_configurations(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        page_size=10,
        page_token=None,
    )

    assert repeated == task
    assert len(configurations) == 1
    assert configurations[0].task_id == task.id
    assert configurations[0].url == "https://8.8.8.8/a2a-events"
    assert configurations[0].authentication.scheme == "Bearer"
    assert not configurations[0].token
    assert not configurations[0].authentication.credentials
    assert next_page_token is None
    async with short_session(lifecycle_interaction_sessions) as database:
        records = tuple((await database.scalars(select(A2APushConfigurationRecord))).all())
    assert len(records) == 1
    assert records[0].task_id == task.id
    assert records[0].credential_generation == 1
    assert json.loads(records[0].credential_snapshot().decrypt(_protector())) == {
        "authentication_credentials": "secret-credential",
        "token": "opaque-client-token",
    }


async def test_send_message_rejects_push_configuration_for_existing_task(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    request = _request(message_id="feedback-1", context_id=task.context_id, task_id=task.id)
    request.configuration.task_push_notification_config.CopyFrom(
        a2a.TaskPushNotificationConfig(url="https://8.8.8.8/a2a-events")
    )

    with pytest.raises(A2AError) as captured:
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert captured.value.code == "push_configuration_not_allowed"
    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.scalar(select(A2APushConfigurationRecord.id)) is None


async def test_push_configuration_secrets_are_write_only_and_delete_is_fenced(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())

    created = await service.create_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        requested=a2a.TaskPushNotificationConfig(
            task_id=task.id,
            url="https://8.8.8.8/a2a-events",
            token="opaque-client-token",
            authentication=a2a.AuthenticationInfo(scheme="Bearer", credentials="secret-credential"),
        ),
    )
    selected = await service.get_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        config_id=created.id,
    )
    page, next_page_token = await service.list_push_configurations(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        page_size=1,
        page_token=None,
    )

    assert selected == created
    assert created.url == "https://8.8.8.8/a2a-events"
    assert created.authentication.scheme == "Bearer"
    assert not created.token
    assert not created.authentication.credentials
    assert page == (created,)
    assert next_page_token is None
    async with short_session(lifecycle_interaction_sessions) as database:
        stored = await database.get(A2APushConfigurationRecord, created.id)
    assert stored is not None
    assert stored.credential_generation == 1
    assert stored.ciphertext is not None
    assert json.loads(stored.credential_snapshot().decrypt(_protector())) == {
        "authentication_credentials": "secret-credential",
        "token": "opaque-client-token",
    }
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, task.id)
    assert binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)
    async with short_session(lifecycle_interaction_sessions) as database:
        assert (
            await database.scalar(select(OutboxRecord.id).where(OutboxRecord.destination_kind == "a2a_push"))
            is not None
        )

    await service.delete_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        config_id=created.id,
    )
    await service.delete_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        config_id=created.id,
    )

    with pytest.raises(A2AError):
        await service.get_push_configuration(
            actor=_actor(),
            agent_id=AGENT_ID,
            task_id=task.id,
            config_id=created.id,
        )
    async with short_session(lifecycle_interaction_sessions) as database:
        stored = await database.get(A2APushConfigurationRecord, created.id)
        outbox = await database.scalar(select(OutboxRecord.id).where(OutboxRecord.destination_kind == "a2a_push"))
    assert stored is not None
    assert stored.state == "disabled"
    assert stored.delivery_generation == 2
    assert stored.credential_generation == 2
    assert stored.ciphertext is None
    assert stored.nonce is None
    assert stored.encryption_key_id is None
    assert outbox is None


async def test_push_publisher_delivers_committed_terminal_task_through_outbox(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    created = await service.create_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        requested=a2a.TaskPushNotificationConfig(
            url="https://8.8.8.8/a2a-events",
            token="opaque-client-token",
            authentication=a2a.AuthenticationInfo(scheme="Bearer", credentials="secret-credential"),
        ),
    )
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, task.id)
    assert binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)
    async with short_session(lifecycle_interaction_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord).where(OutboxRecord.destination_kind == "a2a_push"))
    assert delivery is not None
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(204)

    protector = _protector()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = A2APushPublisher(
            lifecycle_interaction_sessions,
            client,
            EndpointPolicy(require_https=True),
            protector,
            poll_interval_seconds=1,
            lease_seconds=30,
            claim_limit=10,
            max_attempts=3,
            retry_base_seconds=1,
            retry_max_seconds=5,
            delivery_timeout_seconds=10,
            max_response_bytes=1024,
            max_redirects=2,
            clock=lambda: NOW + timedelta(seconds=4),
        )
        assert await publisher.publish_once() == 1
        assert await publisher.publish_once() == 1

    assert len(requests) == 2
    first_payload = json.loads(requests[0].content)
    assert first_payload["statusUpdate"]["taskId"] == task.id
    assert first_payload["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    request = requests[1]
    assert request.headers["A2A-Version"] == "1.0"
    assert request.headers["X-A2A-Notification-Token"] == "opaque-client-token"
    assert request.headers["Authorization"] == "Bearer secret-credential"
    assert request.headers["Content-Type"] == "application/a2a+json"
    payload = json.loads(request.content)
    assert payload["task"]["id"] == task.id
    assert payload["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert payload["task"]["artifacts"][0]["parts"][0]["data"] == {"answer": 42}
    async with short_session(lifecycle_interaction_sessions) as database:
        stored = await database.get(OutboxRecord, delivery.id)
    assert stored is not None
    assert stored.status == "published"
    assert created.id in stored.destination_ref


async def test_disabled_a2a_does_not_append_push_outbox(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    await service.create_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        requested=a2a.TaskPushNotificationConfig(url="https://8.8.8.8/a2a-events"),
    )
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, task.id)
    assert binding is not None

    from tests.sql_capture import capture_sql

    with capture_sql(lifecycle_interaction_sessions) as statements:
        await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id, a2a_enabled=False)
    assert not any("a2a_" in statement for statement in statements)

    async with short_session(lifecycle_interaction_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord.id).where(OutboxRecord.destination_kind == "a2a_push"))
    assert delivery is None


async def test_push_configuration_delete_waits_for_claimed_delivery(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    created = await service.create_push_configuration(
        actor=_actor(),
        agent_id=AGENT_ID,
        task_id=task.id,
        requested=a2a.TaskPushNotificationConfig(url="https://8.8.8.8/a2a-events"),
    )
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, task.id)
    assert binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)
    async with transaction(lifecycle_interaction_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord).where(OutboxRecord.destination_kind == "a2a_push"))
        assert delivery is not None
        delivery.status = "publishing"
        delivery.claim_generation = 1
        delivery.attempt_count = 1
        delivery.lease_expires_at = NOW + timedelta(seconds=30)
        delivery.updated_at = NOW

    deleted = anyio.Event()

    async def delete_configuration() -> None:
        await service.delete_push_configuration(
            actor=_actor(),
            agent_id=AGENT_ID,
            task_id=task.id,
            config_id=created.id,
        )
        deleted.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(delete_configuration)
        # Wait for the committed fence, not a scheduler-dependent delay.
        with anyio.fail_after(5):
            while True:
                async with short_session(lifecycle_interaction_sessions) as database:
                    configuration = await database.get(A2APushConfigurationRecord, created.id)
                assert configuration is not None
                if configuration.state == "disabled":
                    break
                await anyio.sleep(0.01)
        assert not deleted.is_set()
        async with short_session(lifecycle_interaction_sessions) as database:
            claimed = await database.get(OutboxRecord, delivery.id)
        assert claimed is not None
        async with transaction(lifecycle_interaction_sessions) as database:
            claimed = await database.get(OutboxRecord, delivery.id)
            assert claimed is not None
            claimed.status = "published"
            claimed.lease_expires_at = None
            claimed.published_at = NOW + timedelta(seconds=1)
            claimed.updated_at = NOW + timedelta(seconds=1)
        with anyio.fail_after(5):
            await deleted.wait()

    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.get(OutboxRecord, delivery.id) is None


async def test_push_publisher_follows_redirects_and_fences_credentials_by_origin(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if str(request.url) == "https://8.8.8.8/start":
            return httpx2.Response(307, headers={"Location": "/same-origin"})
        if str(request.url) == "https://8.8.8.8/same-origin":
            return httpx2.Response(308, headers={"Location": "https://1.1.1.1/final"})
        return httpx2.Response(204)

    protector = _protector()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = A2APushPublisher(
            lifecycle_interaction_sessions,
            client,
            EndpointPolicy(require_https=True),
            protector,
            poll_interval_seconds=1,
            lease_seconds=30,
            claim_limit=10,
            max_attempts=3,
            retry_base_seconds=1,
            retry_max_seconds=5,
            delivery_timeout_seconds=10,
            max_response_bytes=1024,
            max_redirects=2,
        )
        failure = await publisher._deliver(
            A2APushMaterial(
                endpoint_url="https://8.8.8.8/start",
                token="opaque-client-token",
                authentication_scheme="Bearer",
                authentication_credentials="secret-credential",
                payload=b"{}",
            )
        )

    assert failure is None
    assert len(requests) == 3
    assert requests[1].headers["Authorization"] == "Bearer secret-credential"
    assert requests[1].headers["X-A2A-Notification-Token"] == "opaque-client-token"
    assert "Authorization" not in requests[2].headers
    assert "X-A2A-Notification-Token" not in requests[2].headers
    assert requests[2].method == "POST"
    assert requests[2].content == b"{}"


async def test_push_publisher_rejects_redirects_beyond_bound(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(307, headers={"Location": "/again"})

    protector = _protector()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = A2APushPublisher(
            lifecycle_interaction_sessions,
            client,
            EndpointPolicy(require_https=True),
            protector,
            poll_interval_seconds=1,
            lease_seconds=30,
            claim_limit=10,
            max_attempts=3,
            retry_base_seconds=1,
            retry_max_seconds=5,
            delivery_timeout_seconds=10,
            max_response_bytes=1024,
            max_redirects=1,
        )
        failure = await publisher._deliver(
            A2APushMaterial(
                endpoint_url="https://8.8.8.8/start",
                token=None,
                authentication_scheme=None,
                authentication_credentials=None,
                payload=b"{}",
            )
        )

    assert failure is not None
    assert failure.error_code == "a2a_push_redirect_limit"
    assert failure.retryable is False
