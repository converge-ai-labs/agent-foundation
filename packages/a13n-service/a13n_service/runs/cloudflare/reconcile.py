"""Reconcile one durable stage with a bounded, fenced claim."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

import httpx2
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import SecretStr
from sqlalchemy import or_, select

from a13n_service.infra.crypto import Envelope
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.sweeps import Sweep
from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.resources.providers.schemas import ProviderCreate
from a13n_service.resources.providers.service import insert_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.cloudflare_deployments import DeploymentRow

from .remote import Cloud, CloudFailure
from .service import ACTIVE, CLEANUP, LEASE, audit, location, remove_provider


@dataclass(slots=True)
class Claim:
    row: DeploymentRow
    fence: str


async def claim(runtime: Runtime) -> Claim | None:
    async with transaction(runtime.storage) as session:
        current = await now(session)
        row = await session.scalar(
            select(DeploymentRow)
            .where(
                DeploymentRow.status.in_(ACTIVE),
                DeploymentRow.due_at <= current,
                or_(DeploymentRow.claim.is_(None), DeploymentRow.claim_until <= current),
            )
            .order_by(DeploymentRow.due_at, DeploymentRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        fence = new_object_id("op")
        row.claim, row.claim_until = fence, current + LEASE
        return Claim(row, fence)


def reveal(runtime: Runtime, row: DeploymentRow, column: str) -> str:
    value = row.deployment_token if column == "deployment_token" else row.gateway_token
    return runtime.keys.reveal(Envelope.model_validate(value), location(row, column)).decode()


type SmokeFactory = Callable[[str, str, SecretStr, EndpointPolicy], ManagementBackend]


def smoke(runtime: Runtime, row: DeploymentRow, factory: SmokeFactory) -> ManagementBackend:
    return factory(
        row.resources["endpoint"],
        row.resources["smoke_id"],
        SecretStr(reveal(runtime, row, "gateway_token")),
        runtime.endpoint_policy,
    )


async def remove_smoke(runtime: Runtime, row: DeploymentRow, factory: SmokeFactory) -> None:
    management = smoke(runtime, row, factory)
    await management.destroy()
    if await management.inspect() != "absent":
        raise CloudFailure("smoke_removal_unconfirmed")


async def provision(runtime: Runtime, row: DeploymentRow, cloud: Cloud, resources: dict, factory: SmokeFactory) -> str:
    match row.stage:
        case "preflight":
            resources["endpoint"] = await cloud.endpoint()
            if await cloud.worker_exists(owned=False) or await cloud.namespace() is not None:
                raise CloudFailure("worker_name_in_use", terminal=True)
            if await cloud.image_digest(await cloud.registry_password()) is not None:
                raise CloudFailure("image_name_in_use", terminal=True)
            resources["image_attempted"] = True
            return "image"
        case "image":
            resources["image_digest"] = await cloud.mirror()
            resources["worker_attempted"] = True
            return "worker"
        case "worker":
            namespace = await cloud.namespace() if await cloud.worker_exists() else None
            application = await cloud.application(namespace) if namespace else None
            route, _ = (
                await cloud.request("GET", cloud.script + "/subdomain", optional=True) if application else (None, {})
            )
            if not application or not isinstance(route, dict) or route.get("enabled") is not True:
                await cloud.upload(reveal(runtime, row, "gateway_token"), row.service_origin, resources["image_digest"])
                if not await cloud.worker_exists():
                    raise CloudFailure("worker_creation_unconfirmed")
                namespace = await cloud.namespace()
                application = await cloud.application(namespace) if namespace else None
            if not namespace or not application:
                raise CloudFailure("application_creation_unconfirmed")
            resources.update(namespace_id=namespace, application_id=application, smoke_id=new_object_id("env"))
            return "smoke"
        case "smoke":
            management = smoke(runtime, row, factory)
            await management.create()  # Stage the normal Provider's fixed Environment state.
            try:
                if await management.create() is not None:
                    raise CloudFailure("smoke_waiting")
            except EnvironmentProviderError as error:
                if error.code != "environment_lost" or await management.inspect() != "absent":
                    raise
                # A lost Environment ID is terminal. Confirm and tombstone it before
                # trying a new scratch ID; cap replacements so a bad image stays bounded.
                if resources.get("smoke_replacements", 0) >= 2:
                    raise CloudFailure("smoke_container_lost", terminal=True) from None
                await remove_smoke(runtime, row, factory)
                resources["smoke_replacements"] = resources.get("smoke_replacements", 0) + 1
                resources["smoke_id"] = new_object_id("env")
                return "smoke"
            return "remove_smoke"
        case "remove_smoke":
            await remove_smoke(runtime, row, factory)
            resources["removed_smoke"] = True
            return "publish"
        case _:
            raise RuntimeError("Unknown deployment stage")


async def cleanup(runtime: Runtime, row: DeploymentRow, cloud: Cloud, resources: dict, factory: SmokeFactory) -> str:
    stage = row.stage
    receipt = f"removed_{stage}"
    if not resources.get(receipt):
        match stage:
            case "smoke":
                if resources.get("smoke_id"):
                    try:
                        if not await cloud.worker_exists():
                            raise CloudFailure("worker_missing")
                        await remove_smoke(runtime, row, factory)
                    except EnvironmentProviderError:
                        resources["smoke_waiting_for_namespace"] = True
                    except CloudFailure as error:
                        if error.code != "worker_missing":
                            raise
                        resources["smoke_waiting_for_namespace"] = True
            case "application" | "namespace" | "worker":
                if not resources.get("worker_attempted"):
                    resources[receipt] = "not_created"
                else:
                    owned = await cloud.worker_exists()
                    recorded = resources.get("namespace_id")
                    namespace = await cloud.namespace(recorded)
                    if namespace and not (recorded or owned):
                        raise CloudFailure("namespace_ownership_mismatch", terminal=True)
                    target = namespace or recorded
                    if stage == "application" and target:
                        application = await cloud.application(target, resources.get("application_id"))
                        if application:
                            await cloud.request("DELETE", f"/containers/applications/{application}", optional=True)
                        if await cloud.application(target):
                            raise CloudFailure("application_removal_unconfirmed")
                        resources["namespace_id"] = target
                    elif stage == "namespace":
                        if namespace:
                            if await cloud.application(namespace):
                                raise CloudFailure("application_removal_unconfirmed")
                            await cloud.remove_namespace()
                        if await cloud.namespace(recorded):
                            raise CloudFailure("namespace_removal_unconfirmed")
                        if resources.pop("smoke_waiting_for_namespace", False):
                            resources["removed_smoke"] = True
                    elif stage == "worker":
                        if namespace:
                            raise CloudFailure("namespace_removal_unconfirmed")
                        if owned:
                            await cloud.request("DELETE", cloud.script, optional=True)
                        if await cloud.worker_exists():
                            raise CloudFailure("worker_removal_unconfirmed")
            case "image":
                if resources.get("image_attempted"):
                    await cloud.remove_image(resources.get("image_digest"))
                else:
                    resources[receipt] = "not_created"
            case _:
                raise RuntimeError("Unknown cleanup stage")
        if stage == "smoke" and not resources.get("smoke_id"):
            resources[receipt] = "not_created"
        elif not resources.get("smoke_waiting_for_namespace") or stage != "smoke":
            resources.setdefault(receipt, True)
    index = CLEANUP.index(stage) + 1
    return CLEANUP[index] if index < len(CLEANUP) else "finish"


async def publish(runtime: Runtime, session, row: DeploymentRow) -> None:
    provider = await insert_provider(
        session,
        None,
        EnvironmentProviderRow,
        WorkspaceScope(row.organization_id, row.workspace_id),
        ProviderCreate(
            type="cloudflare",
            name=row.name,
            config={"endpoint": row.resources["endpoint"]},
            credential={"api_key": reveal(runtime, row, "gateway_token")},
        ),
        registry=runtime.registry,
        keys=runtime.keys,
    )
    row.provider_id, row.status, row.stage = provider.id, "ready", "complete"
    audit(session, row, "ready")


async def reconcile(runtime: Runtime, factory: SmokeFactory) -> None:
    if not runtime.settings.provisioning.cloudflare.enabled or (job := await claim(runtime)) is None:
        return
    row = job.row
    resources = dict(row.resources)
    next_stage = row.stage
    failure = None
    try:
        if row.stage not in {"publish", "finish"}:
            async with asyncio.timeout(390), httpx2.AsyncClient(timeout=15, trust_env=False) as client:
                cloud = Cloud(
                    client,
                    runtime.settings.provisioning.cloudflare,
                    row.account_id,
                    row.worker_name,
                    row.id,
                    row.source_image,
                    reveal(runtime, row, "deployment_token"),
                )
                step = provision if row.operation == "deploy" else cleanup
                next_stage = await step(runtime, row, cloud, resources, factory)
    except CloudFailure as error:
        failure = error
    except EnvironmentProviderError:
        failure = CloudFailure("smoke_gateway_unavailable")
    except ServiceError:
        failure = CloudFailure("deployment_encryption_unavailable", terminal=True)
    except TimeoutError:
        failure = CloudFailure("deployment_step_timed_out")

    async with transaction(runtime.storage) as session:
        current = await now(session)
        active = await session.scalar(
            select(DeploymentRow)
            .where(
                DeploymentRow.id == row.id,
                DeploymentRow.claim == job.fence,
                DeploymentRow.claim_until > current,
            )
            .with_for_update()
        )
        if active is None:
            return
        if failure is None:
            try:
                async with session.begin_nested():
                    if row.stage == "publish":
                        await publish(runtime, session, active)
                    elif row.stage == "finish":
                        await remove_provider(session, active)
                        active.status, active.stage = "retired", "complete"
                        active.deployment_token = active.gateway_token = None
                        audit(session, active, "retired")
                    else:
                        active.resources, active.stage = resources, next_stage
            except ServiceError:
                await session.refresh(active)
                failure = CloudFailure("provider_finalization_failed", terminal=True)
        if failure is not None:
            active.attempts += 1
            active.error = failure.code
            if failure.terminal or active.attempts >= (24 if row.stage == "smoke" else 6):
                active.status = "needs_attention"
                audit(session, active, "needs_attention")
            active.due_at = current + timedelta(seconds=min(60, 2**active.attempts))
        else:
            active.attempts, active.error, active.due_at = 0, None, current
        active.claim = active.claim_until = None


def sweep(runtime: Runtime, *, factory: SmokeFactory) -> Sweep:
    return Sweep("cloudflare-deployments", every=2, run=partial(reconcile, runtime, factory), timeout=405)
