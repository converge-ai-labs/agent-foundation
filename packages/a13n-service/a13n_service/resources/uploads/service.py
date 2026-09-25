"""Upload staging: the bytes land under the upload key, then a receipt binds them to one workspace.

An upload ID is derived from the uploader's request key and resolves only through its receipt, so a handle
can never name an arbitrary object key or another workspace's bytes. The receipt is written last, so it always
marks a finished upload and its digest and size describe the stored bytes.
"""

import hashlib
import json

from pydantic import ValidationError

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict, invalid, not_found
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.resources.uploads.schemas import Upload, UploadReceipt
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

_RECEIPT = ".receipt.json"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def object_key(organization_id: str, upload_id: str) -> str:
    return f"orgs/{organization_id}/uploads/{upload_id}"


async def stage(
    storage: Storage,
    objects: ObjectStore,
    actor: Principal,
    workspace_id: str,
    *,
    request_key: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> Upload:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
    receipt = await store(
        objects, scope, actor.id, request_key=request_key, filename=filename, content_type=content_type, content=content
    )
    return Upload(
        upload_id=receipt.id,
        filename=receipt.filename,
        content_type=receipt.content_type,
        size=receipt.size,
        digest=receipt.digest,
    )


async def store(
    objects: ObjectStore,
    scope: WorkspaceScope,
    principal_id: str,
    *,
    request_key: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> UploadReceipt:
    """Stage bytes for an already authorized writer; the same request key and bytes return the same upload."""
    identity = hashlib.sha256(
        _canonical([scope.organization_id, scope.workspace_id, principal_id, request_key])
    ).hexdigest()
    try:
        receipt = UploadReceipt(
            id=f"upl_{identity}",
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            principal_id=principal_id,
            filename=filename,
            content_type=content_type,
            size=len(content),
            digest=hashlib.sha256(content).hexdigest(),
        )
    except ValidationError:
        raise invalid("file", "filename or content type is invalid") from None
    key = object_key(scope.organization_id, receipt.id)
    try:
        # Repeating the request completes an interrupted upload; different bytes or metadata conflict.
        await objects.put(key, content, content_type=receipt.content_type)
        await objects.put(key + _RECEIPT, _canonical(receipt.model_dump(mode="json")), content_type="application/json")
    except ServiceError as error:
        if error.code == "conflict":
            raise conflict("upload", receipt.id, IDEMPOTENCY_KEY_REUSED) from None
        raise
    return receipt


async def get_receipt(objects: ObjectStore, scope: WorkspaceScope, upload_id: str) -> UploadReceipt:
    """The receipt of a finished upload staged in this workspace."""
    stored = await objects.get(object_key(scope.organization_id, upload_id) + _RECEIPT)
    if stored is None:
        raise not_found("upload", upload_id)
    try:
        receipt = UploadReceipt.model_validate_json(stored)
    except ValidationError:
        raise ServiceError("unavailable", "Upload receipt is unreadable", {"dependency": "objects"}) from None
    if receipt.workspace_id != scope.workspace_id:
        raise not_found("upload", upload_id)
    return receipt


async def load(objects: ObjectStore, scope: WorkspaceScope, upload_id: str) -> tuple[UploadReceipt, bytes]:
    """The receipt and verified bytes of an upload staged in this workspace."""
    receipt = await get_receipt(objects, scope, upload_id)
    reference = ObjectRef(
        object_key(scope.organization_id, receipt.id), receipt.digest, receipt.size, receipt.content_type
    )
    return receipt, await read(objects, reference)
