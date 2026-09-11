"""Authenticated lifecycle evidence for disposable live-test Hosts."""

import json
import os

import anyio
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord
from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import short_session
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select


def lifecycle_router(config, authenticate):
    router = APIRouter(prefix="/__live__")
    authentication = Depends(authenticate)

    @router.get("/runs/{run_id}/workers")
    async def workers(run_id: str, request: Request, actor=authentication):
        if actor.workspace_id != config["workspace_id"]:
            raise HTTPException(403, "Fixture evidence belongs to the primary test Workspace")
        runtime = get_process_runtime(request)
        assert runtime is not None
        async with short_session(runtime.shared.storage.sessions) as database:
            rows = await database.execute(
                select(RunAttemptRecord.id, RunAttemptRecord.worker_id)
                .join(RunRecord, RunRecord.id == RunAttemptRecord.run_id)
                .join(SessionRecord, SessionRecord.id == RunRecord.session_id)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.organization_id == config["organization_id"],
                    SessionRecord.workspace_id == config["workspace_id"],
                )
                .order_by(RunAttemptRecord.attempt_number)
            )
            return [dict(row._mapping) for row in rows]

    @router.get("/environments/{environment_id}/lifecycle")
    async def lifecycle(environment_id: str, request: Request, actor=authentication):
        if actor.workspace_id != config["workspace_id"]:
            raise HTTPException(403, "Fixture evidence belongs to the primary test Workspace")
        runtime = get_process_runtime(request)
        assert runtime is not None
        async with short_session(runtime.shared.storage.sessions) as database:
            row = await database.scalar(
                select(EnvironmentRecord).where(
                    EnvironmentRecord.id == environment_id,
                    EnvironmentRecord.organization_id == config["organization_id"],
                    EnvironmentRecord.workspace_id == config["workspace_id"],
                )
            )
            if row is None:
                raise HTTPException(404, "Test Environment not found")
            return {
                "state": row.state,
                "generation": row.generation,
                "status": row.status,
                "expires_at": row.expires_at,
                "operation_id": row.operation_id,
                "operation_expires_at": row.operation_expires_at,
                "condition_since": row.condition_since,
                "retention_condition": row.retention_condition,
                "operation_action": row.operation_action,
                "operation_generation": row.operation_generation,
                "operation_owner": row.operation_owner,
                "next_maintenance_at": row.next_maintenance_at,
                "active_runs": list(
                    await database.scalars(
                        select(RunRecord.id).where(
                            RunRecord.environment_id == row.id,
                            RunRecord.status == "running",
                            RunRecord.environment_use_started_at.is_not(None),
                        )
                    )
                ),
            }

    return router


async def native_effect_barrier(root, environment_id, action, target_id):
    plan = anyio.Path(root / "fault.json")
    if not await plan.exists():
        return
    fault = json.loads(await plan.read_text())
    if fault["environment_id"] != environment_id or fault["action"] != action:
        return
    claimed = root / "claimed"
    try:
        descriptor = os.open(claimed, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w") as output:
        json.dump({"target_id": target_id, "environment_id": environment_id}, output)
    # The real native effect completed, but Service has not published its result.
    while not await anyio.Path(root / "release").exists():
        await anyio.sleep(0.1)
