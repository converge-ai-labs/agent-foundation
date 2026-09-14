"""Test credentials for real managed principals; encrypted Secret fixture setup."""

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.request_runtime import get_process_runtime
from a13n_service.secrets import SecretProtector
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from fastapi import APIRouter, Depends, HTTPException, Request


def install_management_identity():
    """Keep native IAM management alongside this Host's private authenticator."""
    from a13n_service.iam.runtime import build_identity_runtime
    from a13n_service.process import lifecycle

    original = lifecycle.build_control_runtime

    async def build(settings, components, shared, *args):
        control, background = await original(settings, components, shared, *args)
        assert control.identity is None
        identity = await build_identity_runtime(shared.storage.sessions, settings.identity_configuration())
        return replace(control, identity=identity), background

    lifecycle.build_control_runtime = build


def fault_authenticator(config, fallback):
    """Authenticate fixture tokens without replacing persisted execution authorization."""
    path = anyio.Path(Path(config["workspace_root"]).parent / "fault-principals.json")

    async def authenticate(request: Request):
        headers = request.headers.getlist("authorization")
        if len(headers) == 1 and await path.is_file():
            for identity in json.loads(await path.read_text()):
                if secrets.compare_digest(headers[0], "Bearer " + identity["token"]):
                    return AuthenticatedActor(
                        principal=PrincipalRef(principal_type="service_account", principal_id=identity["id"]),
                        auth_method="session",
                        credential_id="ses_live_fault",
                        boundary_workspace_id=config["workspace_id"],
                    )
        return await fallback(request)

    return authenticate


def secret_router(config, authenticate):
    # Generic Secret CRUD has no public router. Seed only fixture-owned secrets;
    # binding, authorization, decryption and recovery still use production code.
    router = APIRouter(prefix="/__live__/fault-secrets")
    authentication = Depends(authenticate)
    owned = set()

    def storage(request, actor):
        if actor.workspace_id != config["workspace_id"] or actor.principal.principal_id != config["user_id"]:
            raise HTTPException(403, "Secret fixture setup requires the lab owner")
        runtime = get_process_runtime(request)
        assert runtime is not None
        return runtime.shared.storage

    @router.post("", status_code=201)
    async def create(request: Request, actor=authentication):
        resources = storage(request, actor)
        secret_id = new_object_id("sec")
        protector = SecretProtector.from_base64(encoded_key=config["encryption_key"], encryption_key_id="live-test-1")
        fields = {
            "secret_id": secret_id,
            "organization_id": config["organization_id"],
            "workspace_id": config["workspace_id"],
            "owner_type": "workspace",
            "owner_id": config["workspace_id"],
            "key": "fault-secret-" + secret_id,
            "version": 1,
        }
        encrypted = protector.encrypt(**fields, value="fixture-secret-value")
        now = datetime.now(UTC)
        async with transaction(resources.sessions) as database:
            database.add(
                SecretRecord(
                    id=secret_id,
                    **{key: value for key, value in fields.items() if key != "secret_id"},
                    ciphertext=encrypted.ciphertext,
                    nonce=encrypted.nonce,
                    encryption_key_id=encrypted.encryption_key_id,
                    created_at=now,
                    value_updated_at=now,
                    deleted_at=None,
                )
            )
        owned.add(secret_id)
        return {"id": secret_id}

    @router.delete("/{secret_id}", status_code=204)
    async def delete(secret_id: str, request: Request, actor=authentication):
        resources = storage(request, actor)
        if secret_id not in owned:
            raise HTTPException(404, "Secret is not owned by this fixture")
        async with transaction(resources.sessions) as database:
            row = await database.get(SecretRecord, secret_id, with_for_update=True)
            assert row is not None and row.workspace_id == config["workspace_id"]
            row.deleted_at = datetime.now(UTC)
            row.ciphertext = row.nonce = row.encryption_key_id = None
            row.version += 1

    return router
