"""Workspace-scoped evidence and explicit object damage in the opt-in fault Host."""

import hashlib
import json
from typing import Literal

import rfc8785
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord
from a13n_service.interactions.objects import RunStateStore, run_state_key
from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import ObjectNotFound, short_session
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select


class StateDamage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["missing", "invalid_json", "schema", "harness_schema", "digest"]


def evidence_router(config, authenticate):
    router = APIRouter(prefix="/__live__/faults")
    authentication = Depends(authenticate)

    async def owned_run(request, actor, run_id):
        if actor.workspace_id != config["workspace_id"]:
            raise HTTPException(403, "Fault evidence belongs to the primary test Workspace")
        runtime = get_process_runtime(request)
        assert runtime is not None
        storage = runtime.shared.storage
        async with short_session(storage.sessions) as database:
            row = await database.scalar(
                select(RunRecord)
                .join(SessionRecord, RunRecord.session_id == SessionRecord.id)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.organization_id == config["organization_id"],
                    SessionRecord.workspace_id == config["workspace_id"],
                )
            )
            if row is None:
                raise HTTPException(404, "Test Run not found")
            run = row.to_resource()
        return storage, run

    @router.get("/runs/{run_id}/execution")
    async def execution(run_id: str, request: Request, actor=authentication):
        storage, run = await owned_run(request, actor, run_id)
        async with short_session(storage.sessions) as database:
            rows = list(
                await database.scalars(
                    select(RunAttemptRecord)
                    .where(RunAttemptRecord.run_id == run_id, RunAttemptRecord.organization_id == run.organization_id)
                    .order_by(RunAttemptRecord.attempt_number)
                )
            )
            attempts = [
                {
                    "id": row.id,
                    "lease_expires_at": row.lease_expires_at,
                    "usage": row.usage_json,
                    "status": row.status,
                }
                for row in rows
            ]
        return {
            "current_run_attempt_id": run.current_run_attempt_id,
            "authority_principal": run.authority_principal.model_dump(mode="json"),
            "execution_budget": run.execution_budget.model_dump(mode="json"),
            "available_at": run.available_at,
            "attempts_charged": run.attempts_charged,
            "handoffs_completed": run.handoffs_completed,
            "usage_charged": run.usage_charged.model_dump(mode="json"),
            "attempts": attempts,
        }

    @router.get("/runs/{run_id}/state")
    async def state(run_id: str, request: Request, actor=authentication):
        storage, run = await owned_run(request, actor, run_id)
        try:
            state = await RunStateStore(storage.objects).read_run(run)
        except ObjectNotFound:
            return {"missing": True}
        envelope = state.envelope
        return {
            "kind": envelope.checkpoint_kind,
            "seq": envelope.checkpoint_seq,
            "fence": state.writer_fence,
            "digest": state.digest_sha256,
            "version": state.info.version,
            "receipts": [receipt.model_dump() for receipt in envelope.host.inbox_receipts],
            "messages": len(envelope.harness.message_history),
        }

    @router.post("/runs/{run_id}/damage")
    async def damage(run_id: str, body: StateDamage, request: Request, actor=authentication):
        storage, run = await owned_run(request, actor, run_id)
        if run.status not in {"accepted", "running"}:
            raise HTTPException(409, "Only an owned active test Run may be damaged")
        key = run_state_key(run.organization_id, run_id)
        info = await storage.objects.stat(key)
        if body.kind == "missing":
            await storage.objects.delete(key, if_match=info.version)
        else:
            async with storage.objects.open(key) as reader:
                content = b"".join([chunk async for chunk in reader])
            metadata = dict(info.metadata)
            if body.kind == "invalid_json":
                content = b"{broken-test-state"
            elif body.kind == "schema":
                value = json.loads(content)
                value["schema_version"] = "999"
                content = rfc8785.dumps(value)
                metadata["schema-version"] = "999"
            elif body.kind == "harness_schema":
                value = json.loads(content)
                value["harness_schema_version"] = value["harness"]["schema_version"] = "999"
                content = rfc8785.dumps(value)
            metadata["digest-sha256"] = "0" * 64 if body.kind == "digest" else hashlib.sha256(content).hexdigest()
            await storage.objects.put(
                key, content, content_type=info.content_type, metadata=metadata, if_match=info.version
            )
        return {"run_id": run_id, "damage": body.kind, "previous_version": info.version}

    return router
