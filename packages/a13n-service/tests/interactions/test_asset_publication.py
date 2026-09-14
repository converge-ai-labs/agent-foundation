"""Real Attempt, storage, Environment, and Harness publication boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext, AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.models import EnvironmentAction, EnvironmentPermissionSet
from a13n_harness.environment.providers import EnvironmentRuntimeMount
from a13n_harness.environment.sources import _EnvironmentAdapterBinding
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.domain import AssetRef
from a13n_service.assets.errors import AssetError
from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.publication import AssetPublisher
from a13n_service.assets.retention import AssetRetention
from a13n_service.assets.runtime import AssetCapability, AssetRuntime, PublicationSelection
from a13n_service.assets.staging import AssetStaging
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord, UserRecord
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete, select

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, AGENT_REVISION_ID, NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_harness_runtime import _environment
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@dataclass
class PublicationFixture:
    runtime: AssetRuntime
    catalog: AssetCatalog
    selection: PublicationSelection
    authority: object
    actor: AuthenticatedActor
    sessions: object

    async def publish(self, environment, invocation_id="invoke-first", **kwargs):
        return await self.runtime.publish(
            current_context=lambda: self.authority,
            selection=self.selection,
            invocation_id=invocation_id,
            environment=environment,
            path=kwargs.pop("path", "/environment/workspace/report.txt"),
            filename=kwargs.pop("filename", None),
            media_type=kwargs.pop("media_type", None),
            **kwargs,
        )


async def _fixture(sessions, objects, tmp_path):
    _, run, _ = await _accept_root(sessions, objects)
    async with transaction(sessions) as session:
        revision = await session.get(AgentRevisionRecord, AGENT_REVISION_ID)
        toolsets = dict(revision.config["toolsets"])
        toolsets["assets"] = {**toolsets["assets"], "enabled": True}
        revision.config = {**revision.config, "toolsets": toolsets}
        session.add(
            UserRecord(
                id=USER_ID,
                email="publisher@example.com",
                normalized_email="publisher@example.com",
                name="Publisher",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        for kind, identifier, role in (
            ("organization", ORGANIZATION_ID, "member"),
            ("workspace", WORKSPACE_ID, "builder"),
        ):
            session.add(
                RoleBindingRecord(
                    id=f"rb_publish_{kind}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID if kind == "workspace" else None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type=kind,
                    resource_id=identifier,
                    role_key=role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    await prepare_permissions(sessions, run, authority)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    preparation = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=preparation, harness_run_id="asset-harness")
    staging = await AssetStaging.create(tmp_path)
    asset_objects = AssetObjectStore(objects, staging)
    publisher = AssetPublisher(asset_objects, staging, max_size_bytes=64, clock=lambda: NOW)
    return PublicationFixture(
        AssetRuntime(sessions, publisher, asset_objects, clock=lambda: NOW),
        AssetCatalog(sessions, asset_objects, clock=lambda: NOW),
        PublicationSelection(WORKSPACE_ID, AGENT_ID, AGENT_REVISION_ID, run.effective_agent_config_digest),
        authority,
        AuthenticatedActor(
            principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
            auth_method="internal",
            credential_id="test",
            boundary_workspace_id=WORKSPACE_ID,
        ),
        sessions,
    )


@pytest.fixture
async def publication(interaction_sessions, interaction_object_store, tmp_path):
    return await _fixture(interaction_sessions, interaction_object_store, tmp_path)


def _binding(root):
    environment = _environment(root, "asset-environment")
    return create_environment_runtime(
        mounts={
            "workspace": EnvironmentRuntimeMount(
                binding=_EnvironmentAdapterBinding(environment),
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="workspace",
    )


@asynccontextmanager
async def _files(root):
    binding = _binding(root)
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="test"), agent_instance_id="asset-test"
    )
    async with binding.bind(
        thread_id="asset-test", run_id="asset-harness", instance=instance, host_refs={}
    ) as environment:
        await binding._activate()
        await environment.files.write_text("/environment/workspace/report.txt", "published bytes", mode="create")
        yield environment


async def test_publication_replay_conflict_and_safe_source_query(publication, tmp_path):
    async with _files(tmp_path / "env") as environment:
        first = await publication.publish(environment)
        assert await publication.publish(environment) == first
        distinct = await publication.publish(environment, "invoke-second")
        assert distinct.asset_id != first.asset_id
        await environment.files.write_text("/environment/workspace/report.txt", "changed bytes", mode="upsert")
        with pytest.raises(AssetError, match="different Asset"):
            await publication.publish(environment)
    page = await publication.catalog.list(
        actor=publication.actor,
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        source_kind=None,
        source_run_id=publication.authority.run_id,
    )
    assert {item.id for item in page.items} == {first.asset_id, distinct.asset_id}
    assert all(item.source.run_id == publication.authority.run_id for item in page.items)
    assert not any(name in first.model_dump() for name in ("path", "object_key", "invocation_id", "run_attempt_id"))
    await publication.catalog.delete(actor=publication.actor, asset_id=first.asset_id)
    async with short_session(publication.sessions) as session:
        row = await session.get(AssetRecord, first.asset_id)
        assert row.deleted_at is not None
        assert row.source_invocation_id == "invoke-first"


async def test_live_attempt_pins_tombstone_but_expired_attempt_does_not(publication, tmp_path, monkeypatch):
    async with _files(tmp_path / "env") as environment:
        reference = await publication.publish(environment)
    await publication.catalog.delete(actor=publication.actor, asset_id=reference.asset_id)
    async with transaction(publication.sessions) as session:
        # Simulate completed cleanup and elapsed audit retention independently of the Attempt.
        await session.execute(delete(OutboxRecord).where(OutboxRecord.source_id == reference.asset_id))
        await session.execute(delete(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == reference.asset_id))
    monkeypatch.setattr("a13n_service.assets.retention.utc_now", lambda: NOW + timedelta(seconds=1))
    retention = AssetRetention(publication.sessions, minimum_age=timedelta(0), batch_limit=10)
    assert (await retention.scan()).deferred == 1
    async with transaction(publication.sessions) as session:
        attempt = await session.get(RunAttemptRecord, publication.authority.run_attempt_id)
        attempt.lease_expires_at = NOW
    await retention.scan()  # Finish the current cursor pass before revisiting the tombstone.
    assert (await retention.scan()).completed == 1
    async with short_session(publication.sessions) as session:
        assert await session.get(AssetRecord, reference.asset_id) is None
        assert await session.get(RunAttemptRecord, publication.authority.run_attempt_id) is not None


@pytest.mark.parametrize("change", ["fence", "lease", "selection"])
async def test_stale_or_unselected_publication_creates_no_asset(publication, tmp_path, change):
    if change == "fence":
        publication.authority = replace(publication.authority, attempt_number=publication.authority.attempt_number + 1)
    elif change == "lease":
        async with transaction(publication.sessions) as session:
            attempt = await session.get(RunAttemptRecord, publication.authority.run_attempt_id)
            attempt.lease_expires_at = NOW
    else:
        async with transaction(publication.sessions) as session:
            revision = await session.get(AgentRevisionRecord, AGENT_REVISION_ID)
            toolsets = dict(revision.config["toolsets"])
            toolsets["assets"] = {**toolsets["assets"], "enabled": False}
            revision.config = {**revision.config, "toolsets": toolsets}
    async with _files(tmp_path / "env") as environment:
        with pytest.raises((AttemptAuthorityError, AssetError)):
            await publication.publish(environment)
    async with short_session(publication.sessions) as session:
        assert await session.scalar(select(AssetRecord)) is None


@pytest.mark.parametrize("path", ["/environment/workspace", "/environment/workspace/link.txt", "../outside.txt"])
async def test_only_confined_regular_files_can_be_published(publication, tmp_path, path):
    from a13n_harness.environment.models import EnvironmentError

    async with _files(tmp_path / "env") as environment:
        (tmp_path / "outside.txt").write_text("private host file")
        (tmp_path / "env" / "link.txt").symlink_to(tmp_path / "outside.txt")
        with pytest.raises((AssetError, EnvironmentError)):
            await publication.publish(environment, path=path)


async def test_postgresql_concurrent_invocation_commits_one_asset(
    postgres_interaction_sessions, interaction_object_store, tmp_path
):
    publication = await _fixture(postgres_interaction_sessions, interaction_object_store, tmp_path)
    async with _files(tmp_path / "env") as environment:
        first, second = await asyncio.gather(publication.publish(environment), publication.publish(environment))
    assert first == second
    async with short_session(publication.sessions) as session:
        assert len((await session.scalars(select(AssetRecord))).all()) == 1


async def test_harness_supplies_trusted_invocation_and_returns_only_asset_ref(publication, tmp_path):
    seen = []

    async def model(messages, info):
        seen.extend(
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        if seen:
            yield "done"
            return
        assert "publish_asset" in {tool.name for tool in info.function_tools}
        schema = next(tool for tool in info.function_tools if tool.name == "publish_asset").parameters_json_schema
        assert set(schema["properties"]) == {"path", "filename", "media_type"}
        yield {
            0: DeltaToolCall(
                name="publish_asset",
                json_args=json.dumps({"path": "/environment/workspace/report.txt"}),
                tool_call_id="model-controlled-id",
            )
        }

    binding = _binding(tmp_path / "harness-env")
    (tmp_path / "harness-env" / "report.txt").write_text("tool publication")
    capability = AssetCapability(publication.runtime, lambda: publication.authority, publication.selection)
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(capability, ToolPermissionsCapability(ToolPermissions(default="allow"))),
    )
    result = await executable.run("Publish the report", bindings=RunBindings.embedded(environment=binding))
    assert result.output_or_raise() == "done"
    reference = AssetRef.model_validate(seen[0].content)
    async with short_session(publication.sessions) as session:
        row = await session.get(AssetRecord, reference.asset_id)
        assert row.source_invocation_id != "model-controlled-id"
        assert row.source_run_attempt_id == publication.authority.run_attempt_id
    assert "asset-staging" not in json.dumps(reference.model_dump())


async def test_authority_loss_after_object_publication_cannot_commit(publication, tmp_path, monkeypatch):
    publish = publication.runtime._publisher.publish

    async def expire_after_publish(candidate):
        await publish(candidate)
        async with transaction(publication.sessions) as session:
            attempt = await session.get(RunAttemptRecord, publication.authority.run_attempt_id)
            attempt.lease_expires_at = NOW

    monkeypatch.setattr(publication.runtime._publisher, "publish", expire_after_publish)
    async with _files(tmp_path / "env") as environment:
        with pytest.raises(AttemptAuthorityError):
            await publication.publish(environment)
    async with short_session(publication.sessions) as session:
        assert await session.scalar(select(AssetRecord)) is None


async def test_unreadable_source_is_concealed_without_hiding_asset(publication, tmp_path, monkeypatch):
    from a13n_service.assets import provenance
    from a13n_service.iam import AuthorizationError

    async with _files(tmp_path / "env") as environment:
        reference = await publication.publish(environment)

    async def deny_source(*args, **kwargs):
        raise AuthorizationError("permission_denied", concealed=True)

    monkeypatch.setattr(provenance, "authorize_agent", deny_source)
    asset = await publication.catalog.get(actor=publication.actor, asset_id=reference.asset_id)
    assert asset.source.model_dump(mode="json") == {"kind": "run_output", "run_id": None}
    with pytest.raises(AssetError, match="not found"):
        await publication.catalog.list(
            actor=publication.actor,
            workspace_id=WORKSPACE_ID,
            limit=10,
            cursor=None,
            source_kind=None,
            source_run_id=publication.authority.run_id,
        )


async def test_unicode_agent_name_preserves_key_on_postgresql(postgres_interaction_sessions):
    from a13n_service.agents.domain import normalize_agent_name
    from a13n_service.agents.models import AgentRecord

    name = normalize_agent_name("ΐ" * 128)
    async with transaction(postgres_interaction_sessions) as session:
        agent = await session.get(AgentRecord, AGENT_ID)
        key = agent.key
        agent.name = name
    async with short_session(postgres_interaction_sessions) as session:
        agent = await session.get(AgentRecord, AGENT_ID)
        assert agent.name == name
        assert agent.key == key


async def test_skill_materialization_guard_uses_the_live_attempt(publication):
    from a13n_service.skills.attempts import CurrentSkillAttempt
    from a13n_service.skills.materialization import SkillMaterializationStale

    fence = CurrentSkillAttempt(publication.sessions, lambda: publication.authority, clock=lambda: NOW)
    await fence.require_current()
    publication.authority = replace(publication.authority, attempt_number=publication.authority.attempt_number + 1)
    with pytest.raises(SkillMaterializationStale):
        await fence.require_current()


@pytest.mark.parametrize(
    ("root_enabled", "child_enabled"), [(False, False), (True, False), (False, True), (True, True)]
)
async def test_publication_capability_is_selected_independently_for_inline_children(
    publication, root_enabled, child_enabled
):
    from contextlib import AsyncExitStack
    from types import SimpleNamespace

    from a13n_service.agents.domain import ChildAgentExecution, ResolvedSubagentEdge
    from a13n_service.interactions.agent_resources import prepare_agent_resources
    from a13n_service.interactions.models import RunRecord
    from a13n_service.skills.runtime import PreparedSkillRuntime

    from .conftest import effective_agent_config

    child_id = "ap_child123456789012"
    child_revision_id = "apr_child123456789012"
    child = effective_agent_config()
    child = child.model_copy(
        update={
            "toolsets": {
                **child.toolsets,
                "assets": child.toolsets["assets"].model_copy(update={"enabled": child_enabled}),
            }
        }
    )
    edge = ResolvedSubagentEdge(
        name="helper", child_agent_id=child_id, child_agent_revision_id=child_revision_id, context={}, environment={}
    )
    config = effective_agent_config().model_copy(
        update={
            "toolsets": {
                **effective_agent_config().toolsets,
                "assets": effective_agent_config().toolsets["assets"].model_copy(update={"enabled": root_enabled}),
            },
            "subagent_mode": "inline",
            "resolved_subagents": (edge,),
            "child_configs": {
                child_revision_id: ChildAgentExecution(
                    agent_id=child_id, revision_content_digest="a" * 64, effective_config=child
                )
            },
        }
    )
    async with short_session(publication.sessions) as session:
        run = (await session.get(RunRecord, publication.authority.run_id)).to_resource()

    @asynccontextmanager
    async def no_external_tools(*args, **kwargs):
        yield ()

    skills = {revision: PreparedSkillRuntime(None, None, None) for revision in (AGENT_REVISION_ID, child_revision_id)}
    async with AsyncExitStack() as stack:
        resources = await prepare_agent_resources(
            run=run,
            workspace_id=WORKSPACE_ID,
            config=config,
            current_context=lambda: publication.authority,
            skills=skills,
            asset_publication=publication.runtime,
            external_tools=SimpleNamespace(capabilities=no_external_tools, child_capabilities=no_external_tools),
            stack=stack,
        )
        selected = {
            revision
            for revision, capabilities in resources.capabilities.items()
            if any(isinstance(capability, AssetCapability) for capability in capabilities)
        }
    assert selected == ({AGENT_REVISION_ID} if root_enabled else set()) | (
        {child_revision_id} if child_enabled else set()
    )


@pytest.mark.parametrize("failure", ["oversize", "media", "revoked"])
async def test_stream_failure_never_commits_and_revocation_is_audited(publication, tmp_path, monkeypatch, failure):
    from a13n_service.iam import AuthorizationError

    async def contents(environment, route):
        yield b"%PDF-1.7" if failure == "media" else b"first"
        if failure == "revoked":
            async with transaction(publication.sessions) as session:
                from a13n_service.iam.models import RoleBindingRecord

                from .test_attempt_authorization import direct_runner

                session.add(direct_runner())
                binding = await session.get(RoleBindingRecord, "rb_publish_workspace")
                binding.role_key = "viewer"
            for _ in range(11):
                await publication.authority.authorization.admit_model_request()
        yield b"x" * 65 if failure == "oversize" else b"tail"

    monkeypatch.setattr("a13n_service.assets.runtime._file_contents", contents)
    async with _files(tmp_path / "env") as environment:
        with pytest.raises(AuthorizationError if failure == "revoked" else AssetError):
            await publication.publish(environment, media_type="image/png" if failure == "media" else None)
    async with short_session(publication.sessions) as session:
        assert await session.scalar(select(AssetRecord)) is None
        if failure == "revoked":
            audit = await session.scalar(
                select(SecurityAuditRecord).where(SecurityAuditRecord.action == "asset.create")
            )
            assert audit.outcome == "failure"
            assert audit.details == {
                "source_kind": "run_output",
                "run_id": publication.authority.run_id,
                "run_attempt_id": publication.authority.run_attempt_id,
            }


async def test_deleted_publication_replays_reference_and_retains_bounded_audit(publication, tmp_path):
    async with _files(tmp_path / "env") as environment:
        reference = await publication.publish(environment)
        await publication.catalog.delete(actor=publication.actor, asset_id=reference.asset_id)
        assert await publication.publish(environment) == reference
    with pytest.raises(AssetError):
        await publication.catalog.get(actor=publication.actor, asset_id=reference.asset_id)
    async with short_session(publication.sessions) as session:
        audit = await session.scalar(
            select(SecurityAuditRecord).where(
                SecurityAuditRecord.action == "asset.create", SecurityAuditRecord.resource_id == reference.asset_id
            )
        )
        assert audit.outcome == "success"
        assert audit.details == {
            "source_kind": "run_output",
            "run_id": publication.authority.run_id,
            "run_attempt_id": publication.authority.run_attempt_id,
        }


async def test_publication_cannot_change_workspace(publication, tmp_path):
    publication.selection = replace(publication.selection, workspace_id="ws_other1234567890")
    async with _files(tmp_path / "env") as environment:
        with pytest.raises(AssetError):
            await publication.publish(environment)
    async with short_session(publication.sessions) as session:
        assert await session.scalar(select(AssetRecord)) is None
