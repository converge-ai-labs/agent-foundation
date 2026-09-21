"""Layered media selections remain per-Agent and freeze at Run acceptance."""

import pytest
from a13n_service.agents.domain import AgentConfig, AgentRunOverride, CreateAgentRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.models.domain import MediaUnderstandingSelection
from a13n_service.models.models import MediaUnderstandingDefaultsRecord, ModelRecord
from a13n_service.storage import transaction

from .conftest import MODEL_ID, MODEL_KEY, NOW, WORKSPACE_ID, actor, agent_config


@pytest.mark.parametrize("override", [None, {}, {"image": None}, {"image": "run-image"}])
def test_media_override_merges_non_null_kinds_and_roundtrips(override):
    base = agent_config().model_copy(
        update={"media_understanding": MediaUnderstandingSelection(image="agent-image", audio="agent-audio")}
    )
    patch = AgentRunOverride.model_validate({"media_understanding": override})
    patch = AgentRunOverride.model_validate_json(patch.model_dump_json(exclude_unset=True))
    merged = merge_agent_run_override(base, patch)
    assert merged.media_understanding.selections() == {
        "image": "run-image" if override == {"image": "run-image"} else "agent-image",
        "audio": "agent-audio",
    }
    assert AgentConfig.model_validate_json(base.model_dump_json()).media_understanding == base.media_understanding


def test_empty_media_selection_preserves_legacy_agent_serialization():
    assert "media_understanding" not in agent_config().model_dump(mode="json")
    assert "media_understanding" not in merge_agent_run_override(agent_config(), None).model_dump(mode="json")


async def add_media_models(sessions):
    async with transaction(sessions) as session:
        base = await session.get(ModelRecord, MODEL_ID)
        for index, key in enumerate(("workspace-media", "agent-media", "run-media"), start=1):
            session.add(
                ModelRecord(
                    id=f"mdl_media{index:016d}",
                    organization_id=base.organization_id,
                    workspace_id=WORKSPACE_ID,
                    key=key,
                    normalized_key=key,
                    name=key,
                    provider_id=base.provider_id,
                    upstream_model=key,
                    model_api=base.model_api,
                    settings={"temperature": index / 10},
                    declarations={
                        "capabilities": ["image_understanding", "video_understanding", "audio_understanding"]
                    },
                    enabled=True,
                    created_by_type=base.created_by_type,
                    created_by_id=base.created_by_id,
                    updated_by_type=base.updated_by_type,
                    updated_by_id=base.updated_by_id,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()
        session.add(
            MediaUnderstandingDefaultsRecord(
                workspace_id=WORKSPACE_ID,
                version=1,
                image_model_id="mdl_media0000000000000001",
                video_model_id="mdl_media0000000000000001",
                audio_model_id="mdl_media0000000000000001",
            )
        )


@pytest.mark.anyio
async def test_media_precedence_child_isolation_and_frozen_settings(
    agent_management, agent_invocation_resolver, agent_sessions
):
    await add_media_models(agent_sessions)
    child = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="media-child",
        request=CreateAgentRequest(
            name="Child",
            config=agent_config().model_copy(
                update={
                    "media_understanding": MediaUnderstandingSelection(image="agent-media"),
                }
            ),
        ),
    )
    root = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="media-root",
        request=CreateAgentRequest(
            name="Root",
            config=agent_config(subagents={"child": {"agent_id": child.agent.id}}).model_copy(
                update={
                    "media_understanding": MediaUnderstandingSelection(image="agent-media", audio="agent-media"),
                }
            ),
        ),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=root.agent.id,
        config_override=AgentRunOverride(media_understanding=MediaUnderstandingSelection(image="run-media")),
    )
    async with transaction(agent_sessions) as session:
        (await session.get(MediaUnderstandingDefaultsRecord, WORKSPACE_ID)).video_model_id = None
        (await session.get(ModelRecord, "mdl_media0000000000000003")).settings = {"temperature": 0.9}
    frozen = agent_invocation_resolver.freezing.freeze_selected(prepared=prepared).effective_config
    assert {kind: model.execution.model_key for kind, model in frozen.media_understanding.items()} == {
        "image": "run-media",
        "video": "workspace-media",
        "audio": "agent-media",
    }
    assert frozen.media_understanding["image"].settings == {"temperature": 0.3}
    nested = next(iter(frozen.child_configs.values())).effective_config
    assert {kind: model.execution.model_key for kind, model in nested.media_understanding.items()} == {
        "image": "agent-media",
        "video": "workspace-media",
        "audio": "workspace-media",
    }


@pytest.mark.anyio
async def test_overridden_unavailable_workspace_model_is_not_resolved(
    agent_management, agent_invocation_resolver, agent_sessions
):
    await add_media_models(agent_sessions)
    async with transaction(agent_sessions) as session:
        defaults = await session.get(MediaUnderstandingDefaultsRecord, WORKSPACE_ID)
        defaults.video_model_id = defaults.audio_model_id = None
        (await session.get(ModelRecord, "mdl_media0000000000000001")).enabled = False
    root = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="overridden-default",
        request=CreateAgentRequest(name="Root", config=agent_config()),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=root.agent.id,
        config_override=AgentRunOverride(media_understanding=MediaUnderstandingSelection(image="run-media")),
    )
    assert prepared.media_models["image"].resource.key == "run-media"
    with pytest.raises(AgentError):
        await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=root.agent.id)


@pytest.mark.anyio
@pytest.mark.parametrize("selection", [{"image": "missing-model"}, {"audio": MODEL_KEY}])
async def test_agent_and_run_reject_invalid_media_models(agent_management, agent_invocation_resolver, selection):
    config = agent_config().model_copy(
        update={"media_understanding": MediaUnderstandingSelection.model_validate(selection)}
    )
    with pytest.raises(AgentError):
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="invalid-media",
            request=CreateAgentRequest(name="Invalid", config=config),
        )
    root = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="valid-media",
        request=CreateAgentRequest(name="Root", config=agent_config()),
    )
    with pytest.raises(AgentError):
        await agent_invocation_resolver.preparation.prepare(
            actor=actor(),
            agent_id=root.agent.id,
            config_override=AgentRunOverride.model_validate({"media_understanding": selection}),
        )


@pytest.mark.anyio
async def test_native_http_override_captures_and_retry_retains_media(
    agent_management, agent_invocation_resolver, agent_sessions, process_runtime_factory, tmp_path
):
    from types import SimpleNamespace

    import httpx2
    from a13n_service.api import install_api_conventions
    from a13n_service.gateway.router import router
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.storage.object_store import LocalObjectStore
    from fastapi import FastAPI

    from tests.gateway.test_commands import _commands

    await add_media_models(agent_sessions)
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="http-media-agent",
        request=CreateAgentRequest(name="Media", config=agent_config()),
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    resolver = agent_invocation_resolver
    commands = _commands(agent_sessions, objects, resolver.preparation, resolver.freezing)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)

    async def authenticate(_request):
        return actor()

    app.state.runtime = process_runtime_factory(
        request_authenticator=authenticate,
        sessions=agent_sessions,
        gateway=SimpleNamespace(commands=commands),
    )
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/runs",
            headers={"Idempotency-Key": "http-media-run"},
            json={
                "agent_id": created.agent.id,
                "input": {"schema_version": "2", "content": [{"type": "text", "text": "hello"}]},
                "config_override": {"media_understanding": {"image": "run-media"}},
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        stored = await RunStateStore(objects).read(created.agent.organization_id, run_id)
        media = stored.envelope.effective_agent_config.media_understanding
        assert media["image"].execution.model_key == "run-media"
        assert media["video"].execution.model_key == "workspace-media"
        interrupted = await client.post(
            f"/api/v1/runs/{run_id}/interrupt",
            headers={"Idempotency-Key": "stop-media"},
            json={"expected_run_version": 1, "expected_thread_version": 1},
        )
        assert interrupted.status_code in (200, 202), interrupted.text
        async with transaction(agent_sessions) as session:
            (await session.get(ModelRecord, "mdl_media0000000000000003")).settings = {"temperature": 0.9}
        retried = await client.post(
            f"/api/v1/runs/{run_id}/retry",
            headers={"Idempotency-Key": "retry-media"},
            json={"expected_thread_version": 2},
        )
        assert retried.status_code == 202, retried.text
        retained = await RunStateStore(objects).read(created.agent.organization_id, retried.json()["run_id"])
        assert retained.envelope.effective_agent_config.media_understanding == media
