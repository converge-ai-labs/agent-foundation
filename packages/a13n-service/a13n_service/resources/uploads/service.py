"""Upload staging: the bytes land under a new upload's own key, then its row binds them to one workspace.

The row is written only after the store acknowledged the bytes, so an upload ID always names finished bytes and
resolves only in its own workspace; a handle can never name an arbitrary object key or another workspace's bytes.
The row is also the evidence of the uploader's request key, arbitrated by its unique constraint.
"""

import hashlib

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session, transaction, violated_constraint
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, conflict, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.resources.uploads.schemas import ContentType, FileName, Upload
from a13n_service.resources.uploads.tables import UploadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

_FILE = TypeAdapter(tuple[FileName, ContentType])


def object_key(organization_id: str, upload_id: str) -> str:
    return f"orgs/{organization_id}/uploads/{upload_id}"


def _view(row: UploadRow) -> Upload:
    return Upload(
        upload_id=row.id, filename=row.filename, content_type=row.content_type, size=row.size, digest=row.digest
    )


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
    return await store(
        storage,
        objects,
        scope,
        actor.id,
        request_key=request_key,
        filename=filename,
        content_type=content_type,
        content=content,
    )


async def store(
    storage: Storage,
    objects: ObjectStore,
    scope: WorkspaceScope,
    principal_id: str,
    *,
    request_key: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> Upload:
    """Stage bytes for an already authorized writer; the same request key and bytes return the same upload."""
    try:
        filename, content_type = _FILE.validate_python((filename, content_type))
    except ValidationError:
        raise invalid("file", "filename or content type is invalid") from None
    digest = hashlib.sha256(content).hexdigest()
    request = (filename, content_type, len(content), digest)
    known = select(UploadRow).where(
        UploadRow.workspace_id == scope.workspace_id,
        UploadRow.created_by_id == principal_id,
        UploadRow.request_key == request_key,
    )
    async with short_session(storage) as session:
        found = await session.scalar(known)
    if found is None:
        upload_id = new_object_id("upl")
        await objects.put(object_key(scope.organization_id, upload_id), content, content_type=content_type)
        try:
            async with transaction(storage) as session:
                row = UploadRow(
                    id=upload_id,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    created_by_id=principal_id,
                    request_key=request_key,
                    filename=filename,
                    content_type=content_type,
                    size=len(content),
                    digest=digest,
                )
                session.add(row)
                await session.flush()
                return _view(row)
        except IntegrityError as error:
            if violated_constraint(error) != "uq_uploads_workspace_id_created_by_id_request_key":
                raise
        # A concurrent request with the same key committed first; the bytes written here stay unreferenced.
        async with short_session(storage) as session:
            found = (await session.scalars(known)).one()
    if (found.filename, found.content_type, found.size, found.digest) != request:
        raise conflict("upload", found.id, IDEMPOTENCY_KEY_REUSED)
    return _view(found)


async def find(session: AsyncSession, workspace_id: str, upload_id: str) -> UploadRow:
    """A finished upload staged in this workspace."""
    row = await session.get(UploadRow, upload_id)
    if row is None or row.workspace_id != workspace_id:
        raise not_found("upload", upload_id)
    return row


async def load(storage: Storage, objects: ObjectStore, workspace_id: str, upload_id: str) -> bytes:
    """The verified bytes of an upload staged in this workspace."""
    async with short_session(storage) as session:
        row = await find(session, workspace_id, upload_id)
        reference = ObjectRef(object_key(row.organization_id, row.id), row.digest, row.size, row.content_type)
    return await read(objects, reference)
