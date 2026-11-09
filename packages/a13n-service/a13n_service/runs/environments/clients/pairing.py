"""Console-approved device enrollment. Redis retains only a digest of the device's credential."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from a13n_envd_client.eip.v1.models import Identifier
from a13n_harness.environment.models import COMPUTER_ACTIONS, FILE_EXECUTION_ACTIONS, EnvironmentPermissionSet
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.redis import rate_limit
from a13n_service.runs.environments.clients import service
from a13n_service.runs.environments.clients.schemas import ConnectionRequest
from a13n_service.runs.environments.schemas import EnvironmentName, WorkingDirectory
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import require_login_session, workspace_scope
from a13n_service.tenancy.authorize import Principal

logger = get_logger(__name__)
LIFETIME_SECONDS = 600
_COMPARE_SET = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
return 1
"""


class DeviceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Identifier
    name: EnvironmentName
    default_working_directory: WorkingDirectory
    environment_id: str | None = Field(default=None, min_length=1, max_length=72)
    computer_use: bool = False
    full_control: bool = False


class Pairing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    workspace_id: str
    owner_id: str
    expires_at: datetime
    service_url: str
    host_name: str
    state: Literal["waiting", "claimed", "approving", "approved", "cancelled"] = "waiting"
    device: DeviceClaim | None = None
    verification_code: str | None = None
    environment_id: str | None = None
    reused: bool = False


class StoredPairing(Pairing):
    token_hash: str | None = None


class PairingCreated(Pairing):
    launcher_url: str


class PairingApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_code: str


class Recognition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: Identifier
    device_id: Identifier


class ClaimResult(BaseModel):
    state: Literal["pending", "approved"]
    verification_code: str
    environment_id: str | None = None
    connection_path: str | None = None


def key(identity: str) -> str:
    return f"a13n:client-pairing:{identity}"


def public(value: StoredPairing) -> Pairing:
    return Pairing.model_validate(value.model_dump(exclude={"token_hash"}))


async def authorize(runtime: Runtime, actor: Principal, workspace_id: str) -> None:
    require_login_session(actor)
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")


async def create(runtime: Runtime, actor: Principal, workspace_id: str) -> PairingCreated:
    await authorize(runtime, actor, workspace_id)
    await rate_limit(runtime.redis, f"client-pairing:{actor.id}", limit=10, window_seconds=LIFETIME_SECONDS)
    # *.localhost is a browser-only development hostname on some operating systems.
    # The native client reaches that same loopback Vite proxy through 127.0.0.1.
    origin = runtime.settings.server.public_origin
    parts = urlsplit(origin)
    if parts.scheme == "http" and parts.hostname and parts.hostname.endswith(".localhost"):
        authority = "127.0.0.1" + (f":{parts.port}" if parts.port is not None else "")
        origin = urlunsplit((parts.scheme, authority, "", "", ""))
    identity = "pair_" + secrets.token_urlsafe(24)
    host_name = "console-" + hashlib.sha256(f"{origin}\n{workspace_id}\n{actor.id}".encode()).hexdigest()[:16]
    value = StoredPairing(
        id=identity,
        workspace_id=workspace_id,
        owner_id=actor.id,
        expires_at=datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS),
        service_url=origin,
        host_name=host_name,
    )
    await runtime.redis.set(key(identity), value.model_dump_json(), ex=LIFETIME_SECONDS, nx=True)
    link = "a13n-envd://connect?" + urlencode(
        {
            "service": origin,
            "workspace": workspace_id,
            "host": host_name,
            "pairing": identity,
        }
    )
    return PairingCreated(**public(value).model_dump(), launcher_url=link)


async def load(runtime: Runtime, identity: str) -> tuple[str, StoredPairing]:
    raw = await runtime.redis.get(key(identity))
    if raw is None:
        raise not_found("client_pairing", identity)
    if isinstance(raw, bytes):
        raw = raw.decode()
    return raw, StoredPairing.model_validate_json(raw)


async def owned(runtime: Runtime, actor: Principal, workspace_id: str, identity: str) -> tuple[str, StoredPairing]:
    await authorize(runtime, actor, workspace_id)
    raw, value = await load(runtime, identity)
    if value.owner_id != actor.id or value.workspace_id != workspace_id:
        raise not_found("client_pairing", identity)
    return raw, value


async def replace(runtime: Runtime, raw: str, value: StoredPairing) -> None:
    if not await runtime.redis.eval(_COMPARE_SET, 1, key(value.id), raw, value.model_dump_json()):
        raise conflict("client_pairing", value.id, "pairing_changed_or_expired")


async def claim(runtime: Runtime, identity: str, token: str, device: DeviceClaim) -> ClaimResult:
    raw, value = await load(runtime, identity)
    digest = secret_hash(token)
    if value.state == "cancelled":
        raise conflict("client_pairing", identity, "pairing_cancelled")
    if value.token_hash is None:
        value.token_hash = digest
        value.device = device
        value.verification_code = f"{secrets.randbelow(1_000_000):06d}"
        value.state = "claimed"
        await replace(runtime, raw, value)
        logger.info("Client pairing awaiting approval", extra={"pairing_id": identity, "device_id": device.device_id})
    elif not secrets.compare_digest(value.token_hash, digest) or value.device != device:
        raise ServiceError("unauthenticated", "Pairing belongs to another device connection")
    assert value.verification_code is not None
    if value.state == "approved":
        assert value.environment_id is not None
        await service.authenticate(runtime, value.environment_id, token)
        return ClaimResult(
            state="approved",
            verification_code=value.verification_code,
            environment_id=value.environment_id,
            connection_path=f"/api/v1/client-environments/{value.environment_id}/connect",
        )
    return ClaimResult(state="pending", verification_code=value.verification_code)


async def recognize(runtime: Runtime, identity: str, token: str, device: Recognition) -> Pairing:
    """Prove an existing registration without enrolling or opening another carrier."""
    raw, value = await load(runtime, identity)
    if value.state != "waiting" and not (
        value.state == "approved" and value.reused and value.environment_id == device.environment_id
    ):
        raise conflict("client_pairing", identity, "pairing_not_waiting")
    attachment = await service.authenticate(runtime, device.environment_id, token)
    async with short_session(runtime.storage) as session:
        registration = await service.active_registration(session, device.environment_id, attachment=attachment)
        if registration is None or registration[0].workspace_id != value.workspace_id:
            raise ServiceError("unauthenticated", "Registration does not belong to this workspace")
    if attachment.device_id != device.device_id:
        raise ServiceError("unauthenticated", "Registration belongs to another device")
    value.environment_id = device.environment_id
    value.reused = True
    value.state = "approved"
    await replace(runtime, raw, value)
    logger.info("Existing client registration recognized", extra={"environment_id": device.environment_id})
    return public(value)


async def approve(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    identity: str,
    code: str,
) -> Pairing:
    raw, value = await owned(runtime, actor, workspace_id, identity)
    if value.state not in {"claimed", "approving", "approved"} or value.verification_code != code:
        raise conflict("client_pairing", identity, "pairing_not_ready")
    if value.state == "claimed":
        value.state = "approving"
        await replace(runtime, raw, value)
        raw = value.model_dump_json()
    assert value.device is not None and value.token_hash is not None
    result = await service.connect(
        runtime,
        actor,
        workspace_id,
        ConnectionRequest(
            **value.device.model_dump(exclude={"computer_use", "full_control"}),
            permission_ceiling=EnvironmentPermissionSet(
                operations=FILE_EXECUTION_ACTIONS | (COMPUTER_ACTIONS if value.device.computer_use else frozenset())
            ),
        ),
        pairing_token_hash=value.token_hash,
    )
    value.environment_id = result.client.environment.id
    value.state = "approved"
    await replace(runtime, raw, value)
    logger.info("Client pairing approved", extra={"environment_id": value.environment_id, "principal_id": actor.id})
    return public(value)


async def cancel(runtime: Runtime, actor: Principal, workspace_id: str, identity: str) -> Pairing:
    raw, value = await owned(runtime, actor, workspace_id, identity)
    if value.state in {"approving", "approved"}:
        raise conflict("client_pairing", identity, "pairing_already_approved")
    value.state = "cancelled"
    await replace(runtime, raw, value)
    return public(value)
