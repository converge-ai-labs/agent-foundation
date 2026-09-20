"""Run selection survives concurrent edits without adopting another configuration."""

import pytest
from a13n_service.agents.domain import CreateAgentRequest, CreateAgentRevisionRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation_resolution.skills import validate_retained_skills
from a13n_service.agents.models import AgentRecord
from a13n_service.etags import resource_etag
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.command_preparation import CommandInput
from a13n_service.interactions.command_values import ContinueRunCommand
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.models.models import ModelRecord
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import delete, event

from tests.gateway.test_commands import _commands, _complete_run, _request
from tests.skills.test_runtime import DEPLOY_REVISION_ID, DEPLOY_SKILL_ID, _add_skill, _package

from .conftest import MODEL_ID, WORKSPACE_ID, actor, agent_config

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("operation", ["start", "continue"])
async def test_command_keeps_selected_config_and_authority_across_input_io(
    agent_management, agent_invocation_resolver, agent_sessions, tmp_path, monkeypatch, operation
):
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-snapshot",
        request=CreateAgentRequest(name="Snapshot", config=agent_config()),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="second-revision",
        request=CreateAgentRevisionRequest(config=agent_config(instructions="Next revision.")),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )
    async with transaction(agent_sessions) as session:
        agent = await session.get(AgentRecord, created.agent.id)
        agent.default_revision_id = created.revision.id
    objects = await LocalObjectStore.create(tmp_path / "objects")
    resolver = agent_invocation_resolver
    commands = _commands(agent_sessions, objects, resolver.preparation, resolver.freezing)
    request = _request().model_copy(update={"agent_id": created.agent.id})
    source = None
    if operation == "continue":
        source = await commands.runs.start(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=request
        )
        await _complete_run(agent_sessions, objects, run_id=source.run_id)
    original = CommandInput.accept
    selected = []

    async def change_during_input(self, **kwargs):
        selected.append(kwargs["frozen"])
        async with transaction(agent_sessions) as session:
            model = await session.get(ModelRecord, MODEL_ID)
            model.upstream_model = "edited-after-selection"
            agent = await session.get(AgentRecord, created.agent.id)
            agent.default_revision_id = second.revision.id
            await session.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "workspace"))
        return await original(self, **kwargs)

    monkeypatch.setattr(CommandInput, "accept", change_during_input)
    statements = []
    async with short_session(agent_sessions) as session:
        engine = session.get_bind()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        if source is None:
            accepted = await commands.runs.start(
                actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="accept", request=request
            )
        else:
            accepted = await commands.runs.continue_from(
                actor=actor(),
                source_run_id=source.run_id,
                idempotency_key="accept",
                request=ContinueRunCommand(expected_thread_version=2, input=request.input),
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(selected) == 1
    async with short_session(agent_sessions) as session:
        run = await session.get(RunRecord, accepted.run_id)
        assert run.agent_revision_id == created.revision.id
    stored = await RunStateStore(objects).read(created.agent.organization_id, accepted.run_id)
    assert stored.envelope.effective_agent_config == selected[0].effective_config
    assert selected[0].effective_config.resolved_model.execution.upstream_model != "edited-after-selection"
    assert sum("FROM role_bindings" in sql for sql in statements if sql.startswith("SELECT")) == 1
    # Only preparation reads Model + Provider; the mutation above reads Model alone.
    assert sum("JOIN model_providers" in sql for sql in statements) == 1
    assert not any("FOR SHARE" in sql and "model_providers" in sql for sql in statements)
    with pytest.raises((AgentError, InteractionCommandError)):
        await commands.runs.start(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="next-operation", request=request
        )


@pytest.mark.parametrize("child", [False, True])
async def test_skill_publication_after_selection_does_not_refresh_root_or_child(
    agent_management, agent_invocation_resolver, agent_sessions, child
):
    package = _package("deploy", "Original package.", ())
    async with transaction(agent_sessions) as session:
        _add_skill(session, DEPLOY_SKILL_ID, DEPLOY_REVISION_ID, "Deploy", package)
    selected_agent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="skill-agent",
        request=CreateAgentRequest(name="Skill Agent", config=agent_config(skills=[{"skill_key": "deploy"}])),
    )
    if child:
        selected_agent = await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="parent-agent",
            request=CreateAgentRequest(
                name="Parent", config=agent_config(subagents={"helper": {"agent_id": selected_agent.agent.id}})
            ),
        )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=selected_agent.agent.id)
    async with transaction(agent_sessions) as session:
        skill = await session.get(SkillRecord, DEPLOY_SKILL_ID)
        first = await session.get(SkillRevisionRecord, DEPLOY_REVISION_ID)
        session.add(
            SkillRevisionRecord(
                id="skr_2222222222222222",
                organization_id=first.organization_id,
                workspace_id=first.workspace_id,
                skill_id=first.skill_id,
                version=2,
                content_digest=first.content_digest,
                manifest=first.manifest,
                imported_from=first.imported_from,
                created_by_type=first.created_by_type,
                created_by_id=first.created_by_id,
                created_at=first.created_at,
            )
        )
        skill.version = 2
        skill.default_revision_id = "skr_2222222222222222"
    frozen = agent_invocation_resolver.freezing.freeze_selected(prepared=prepared)
    config = (
        next(iter(frozen.effective_config.child_configs.values())).effective_config
        if child
        else frozen.effective_config
    )
    assert config.skills[0].skill_revision_id == DEPLOY_REVISION_ID
    assert config.skills[0].version == 1
    # A source-preserving successor checks the exact old package without following
    # or locking the newly published default.
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    async with short_session(agent_sessions) as session:
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            await validate_retained_skills(
                session, organization_id=prepared.organization_id, workspace_id=WORKSPACE_ID, locks=config.skills
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(statements) == 2
        assert all("FOR UPDATE" not in sql and "FOR SHARE" not in sql for sql in statements)
        with pytest.raises(AgentError):
            await validate_retained_skills(
                session,
                organization_id=prepared.organization_id,
                workspace_id=WORKSPACE_ID,
                locks=(config.skills[0].model_copy(update={"content_digest": "f" * 64}),),
            )
    fresh = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=selected_agent.agent.id)
    frozen = agent_invocation_resolver.freezing.freeze_selected(prepared=fresh)
    config = (
        next(iter(frozen.effective_config.child_configs.values())).effective_config
        if child
        else frozen.effective_config
    )
    assert config.skills[0].version == 2


@pytest.mark.parametrize("conflict", [False, True])
async def test_media_capture_rejects_conflicting_model_snapshots_before_acceptance(
    agent_management, agent_invocation_resolver, conflict
):
    from dataclasses import replace

    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="media-agent",
        request=CreateAgentRequest(name="Media", config=agent_config()),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    media = replace(prepared.model, settings_layers=())
    if conflict:
        media = replace(media, resource=media.resource.model_copy(update={"upstream_model": "concurrently-edited"}))
    prepared = replace(prepared, media_models={"image": media})
    if conflict:
        with pytest.raises(AgentError) as failure:
            agent_invocation_resolver.freezing.freeze_selected(prepared=prepared)
        assert failure.value.details == {"reason": "model_configuration_changed"}
    else:
        frozen = agent_invocation_resolver.freezing.freeze_selected(prepared=prepared)
        assert (
            frozen.effective_config.media_understanding["image"].execution
            == frozen.effective_config.resolved_model.execution
        )


@pytest.mark.parametrize("operation", ["continue", "retry"])
async def test_media_defaults_follow_fresh_selection_and_retained_retry(
    agent_management, agent_invocation_resolver, agent_sessions, tmp_path, operation
):
    from a13n_service.interactions.command_values import RetryRunCommand
    from a13n_service.interactions.control_domain import InterruptRequest
    from a13n_service.models.models import MediaUnderstandingDefaultsRecord

    async with transaction(agent_sessions) as session:
        session.add(MediaUnderstandingDefaultsRecord(workspace_id=WORKSPACE_ID, version=1, image_model_id=MODEL_ID))
        (await session.get(ModelRecord, MODEL_ID)).settings = {"temperature": 0.7}
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="captured-media",
        request=CreateAgentRequest(name="Media", config=agent_config()),
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    resolver = agent_invocation_resolver
    commands = _commands(agent_sessions, objects, resolver.preparation, resolver.freezing)
    request = _request().model_copy(update={"agent_id": created.agent.id})
    source = await commands.runs.start(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=request
    )
    if operation == "continue":
        await _complete_run(agent_sessions, objects, run_id=source.run_id)
    else:
        await commands.active.interrupt(
            actor=actor(),
            run_id=source.run_id,
            idempotency_key="cancel",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
        )
    stored = await RunStateStore(objects).read(created.agent.organization_id, source.run_id)
    media = stored.envelope.effective_agent_config.media_understanding["image"]
    assert media.settings == {"temperature": 0.7}
    async with transaction(agent_sessions) as session:
        defaults = await session.get(MediaUnderstandingDefaultsRecord, WORKSPACE_ID)
        defaults.image_model_id = None
        defaults.version += 1
        (await session.get(ModelRecord, MODEL_ID)).settings = {"temperature": 0.1}
    if operation == "continue":
        successor = await commands.runs.continue_from(
            actor=actor(),
            source_run_id=source.run_id,
            idempotency_key="continue",
            request=ContinueRunCommand(expected_thread_version=2, input=request.input),
        )
    else:
        successor = await commands.continuations.retry(
            actor=actor(),
            run_id=source.run_id,
            idempotency_key="retry",
            request=RetryRunCommand(expected_thread_version=2),
        )
    continued = await RunStateStore(objects).read(created.agent.organization_id, successor.run_id)
    assert continued.envelope.effective_agent_config.media_understanding == (
        {"image": media} if operation == "retry" else {}
    )
    fresh = await commands.runs.start(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="fresh", request=request
    )
    latest = await RunStateStore(objects).read(created.agent.organization_id, fresh.run_id)
    assert latest.envelope.effective_agent_config.media_understanding == {}
