"""Durable, leased post-completion organization. SQL contains identities and outcomes only."""

import asyncio
import hashlib
import json
from datetime import timedelta

from a13n_harness.providers.memory.filesystem.organization import (
    ORGANIZATION_INSTRUCTIONS,
    OrganizationPlan,
    apply_organization,
    organization_input,
)
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import AgentConfig
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.background import PeriodicLoop
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .documents import FileDocuments
from .models import MemoryOrganizationRecord, MemoryStorageRecord, RunMemoryStorageRecord
from .service import MemoryService, failure
from .sources import authorize_sources


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def admit_organization(session: AsyncSession, event: LifecycleEventRecord) -> None:
    if event.event_type != "run.completed":
        return
    for binding in await session.scalars(
        select(RunMemoryStorageRecord).where(RunMemoryStorageRecord.run_id == event.run_id)
    ):
        if not binding.organization_policy or "erasure_digest" not in binding.organization_policy:
            continue
        work_id = "morg_" + digest(f"{event.run_id}:{binding.selection_digest}:1")
        if await session.get(MemoryOrganizationRecord, work_id) is None:
            session.add(
                MemoryOrganizationRecord(
                    id=work_id,
                    run_id=event.run_id,
                    storage_id=binding.storage_id,
                    policy=binding.organization_policy,
                    status="pending",
                    available_at=utc_now(),
                    created_at=utc_now(),
                )
            )


class MemoryOrganizer(PeriodicLoop):
    def __init__(
        self, memory: MemoryService, objects: ObjectStore, models: NativeModelFactory, providers: LiveProviderResolver
    ) -> None:
        super().__init__(self.run_once, interval_seconds=10)
        self.memory, self.states, self.models, self.providers = memory, RunStateStore(objects), models, providers

    async def run_once(self) -> None:
        now = utc_now()
        async with transaction(self.memory.authorizer.sessions) as session:
            row = await session.scalar(
                select(MemoryOrganizationRecord)
                .where(
                    MemoryOrganizationRecord.status.in_(("pending", "running", "retry")),
                    MemoryOrganizationRecord.available_at <= now,
                    or_(MemoryOrganizationRecord.expires_at.is_(None), MemoryOrganizationRecord.expires_at <= now),
                )
                .order_by(MemoryOrganizationRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return
            owner = new_object_id("mowner")
            row.owner, row.expires_at, row.status = owner, now + timedelta(seconds=150), "running"
            row.attempts += 1
            work_id = row.id
        try:
            async with asyncio.timeout(120):
                result = await self.organize(work_id, owner)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with transaction(self.memory.authorizer.sessions) as session:
                row = await session.get(MemoryOrganizationRecord, work_id, with_for_update=True)
                if row is not None and row.owner == owner:
                    row.status = (
                        "cancelled"
                        if getattr(error, "code", None) == "memory_organization_revoked"
                        else "attention"
                        if row.attempts >= 3
                        else "retry"
                    )
                    row.error_code = "memory_organization_unconfirmed"
                    row.owner = row.expires_at = None
                    row.available_at = utc_now() + timedelta(seconds=60)
        else:
            async with transaction(self.memory.authorizer.sessions) as session:
                row = await session.get(MemoryOrganizationRecord, work_id, with_for_update=True)
                if row is not None and row.owner == owner:
                    row.status = "deferred" if result.get("deferred") else "completed"
                    row.result, row.error_code = result, None
                    row.owner = row.expires_at = None

    async def organize(self, work_id: str, owner: str) -> dict[str, object]:
        async def check() -> None:
            async with short_session(self.memory.authorizer.sessions) as session:
                work = await session.get(MemoryOrganizationRecord, work_id)
                if (
                    work is None
                    or work.owner != owner
                    or work.status != "running"
                    or work.expires_at is None
                    or assume_utc(work.expires_at) <= utc_now()
                ):
                    raise failure("memory_organization_revoked", "Organization authority expired.")
                source = await session.get(RunRecord, work.run_id)
                storage = await session.get(MemoryStorageRecord, work.storage_id)
                if source is None or source.status != "completed" or storage is None:
                    raise failure("memory_source_unavailable", "Completed work is unavailable.")
                agent_id = str(work.policy["agent_id"])
                agent = await session.get(AgentRecord, agent_id)
                if agent is None or not agent.enabled or agent.archived_at is not None:
                    raise failure("memory_organization_revoked", "Organization is disabled.")
                actor = AuthenticatedActor(
                    principal=source.to_resource().authority_principal,
                    auth_method="internal",
                    credential_id=work_id,
                    boundary_workspace_id=storage.workspace_id,
                )
                await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=storage.workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_invoke,
                )
                if storage.scope_kind == "conversation":
                    if self.memory.authorize_organization is None:
                        raise failure("memory_organization_unavailable", "Conversation organization is unavailable.")
                    await self.memory.authorize_organization(session, source.id, storage, work.policy)
                else:
                    revision = await session.get(AgentRevisionRecord, agent.default_revision_id)
                    current = AgentConfig.model_validate(revision.config).memory if revision else None
                    entries = getattr(current, "entries", ())
                    selected = work.policy.get("entry")
                    if not isinstance(selected, dict) or not any(
                        item.auto_organize
                        and item.name == selected.get("name")
                        and item.scope == selected.get("scope")
                        and item.mode == selected.get("mode")
                        and item.backend.model_dump(mode="json") == selected.get("backend")
                        for item in entries
                    ):
                        raise failure("memory_organization_revoked", "Organization is disabled.")

            if storage.scope_kind == "conversation":
                if self.memory.verify_organization is None:
                    raise failure("memory_scope_unverified", "Conversation verification is unavailable.")
                await self.memory.verify_organization(source.id, storage.id)

        await check()
        async with short_session(self.memory.authorizer.sessions) as session:
            work = await session.get(MemoryOrganizationRecord, work_id)
            assert work is not None
            source = await session.get(RunRecord, work.run_id)
            assert source is not None
            run = source.to_resource()
            parent = await session.get(SessionRecord, run.session_id)
            assert parent is not None
            workspace_id, storage_id, plan_digest = parent.workspace_id, work.storage_id, work.plan_digest
            agent_id = str(work.policy["agent_id"])
            erasure_digest = work.policy.get("erasure_digest")
        actor = AuthenticatedActor(
            principal=run.authority_principal,
            auth_method="internal",
            credential_id=work_id,
            boundary_workspace_id=workspace_id,
        )
        state = await self.states.read_run(run)
        evidence = json.dumps({"input": run.input_text, "result": run.output_text}, ensure_ascii=False)
        if not run.output_text:
            return {"ignored": 1, "deferred": 0, "committed": []}
        source_ref = f"run://{run.id}"
        async with short_session(self.memory.authorizer.sessions) as session:
            await authorize_sources(
                session, actor=actor, storage_id=storage_id, workspace_id=workspace_id, references=(source_ref,)
            )
        documents = FileDocuments(self.memory)
        async with documents.open(actor, workspace_id, storage_id, write=True, operation_authorize=check) as store:
            saved = await store.organization_plan(work_id)
            erasure = await store.organization_token()
            if digest(erasure or "") != erasure_digest:
                raise failure("memory_organization_revoked", "Memory was deleted after this work was accepted.")
            existing = []
            if saved is None:
                if plan_digest is not None:
                    raise failure("memory_organization_unconfirmed", "The retained extraction plan is unavailable.")
                for reference in await store.search(run.output_text[:4000], limit=4):
                    document = await store.document(reference.id)
                    existing.append(
                        {
                            "id": document.id,
                            "version": document.version,
                            "kind": document.kind,
                            "title": document.title,
                            "text": document.text[:4000],
                        }
                    )
        if saved is None:
            config = state.envelope.effective_agent_config
            if agent_id != run.agent_id:
                matches = [
                    child.effective_config for child in config.child_configs.values() if child.agent_id == agent_id
                ]
                if len(matches) != 1:
                    raise failure("memory_organization_unavailable", "The accepted model is ambiguous.")
                config = matches[0]
            model_selection = config.resolved_model
            provider = await self.providers.resolve(
                organization_id=run.organization_id, workspace_id=workspace_id, snapshot=model_selection.execution
            )
            model = await self.models.build(model_selection.execution, provider)
            extractor = Agent(model, output_type=OrganizationPlan, instructions=ORGANIZATION_INSTRUCTIONS, retries=0)
            async with extractor:
                extracted = await extractor.run(
                    organization_input(evidence, existing),
                    usage_limits=UsageLimits(request_limit=2, total_tokens_limit=16000),
                )
            await check()
            saved = json.dumps(
                {"erasure": erasure, "plan": extracted.output.model_dump(mode="json")}, ensure_ascii=False
            )
            async with documents.open(actor, workspace_id, storage_id, write=True, operation_authorize=check) as store:
                store.organization_fence = (erasure.encode() if erasure is not None else None,)
                await store.organization_plan(work_id, text=saved)
            async with transaction(self.memory.authorizer.sessions) as session:
                row = await session.get(MemoryOrganizationRecord, work_id, with_for_update=True)
                if row is None or row.owner != owner or row.status != "running":
                    raise failure("memory_organization_revoked", "Organization authority changed.")
                row.plan_digest = digest(saved)
        elif plan_digest != digest(saved):
            raise failure("memory_organization_unconfirmed", "Extraction plan integrity is unavailable.")
        await check()
        retained_plan = json.loads(saved)
        token = retained_plan["erasure"]
        async with documents.open(actor, workspace_id, storage_id, write=True, operation_authorize=check) as store:
            store.organization_fence = (token.encode() if token is not None else None,)
            result = await apply_organization(
                store, OrganizationPlan.model_validate(retained_plan["plan"]), work_id=work_id, source=source_ref
            )
            return result.model_dump(mode="json")
