"""Owner-bound profile images using the canonical object publication lifecycle."""

import warnings
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

from anyio import to_thread
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.ids import new_object_id
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.storage import ObjectNotFound, ObjectStore, short_session
from a13n_service.temporal import utc_now

from ..auth.sessions import require_user
from ..authorization import WorkspaceAction, authorize_organization_admin, authorize_workspace
from ..domain import AuthenticatedActor
from ..models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from ..schemas import Organization, User, Workspace
from ..service_common import (
    audit,
    identity_error,
    identity_transaction,
    not_found,
    require_etag,
    singleton_organization,
)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


class ImageOwner(StrEnum):
    user = "user"
    organization = "organization"
    workspace = "workspace"


@dataclass(frozen=True, slots=True)
class ImageTarget:
    kind: ImageOwner
    id: str
    organization_id: str

    def key(self, image_id: str) -> str:
        if self.kind is ImageOwner.user:
            return f"users/{self.id}/profile/avatar/{image_id}/content.webp"
        prefix = f"organizations/{self.organization_id}"
        if self.kind is ImageOwner.workspace:
            prefix += f"/workspaces/{self.id}"
        return f"{prefix}/profile/icon/{image_id}/content.webp"


def normalize_image(content: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Unsupported image")
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGBA")
                image.thumbnail((512, 512))
                output = BytesIO()
                image.save(output, format="WEBP", quality=90)
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise identity_error(
            "invalid_profile_image", "Use a PNG, JPEG or WebP image up to 16 megapixels.", ErrorCategory.invalid_request
        ) from error


class ImageService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def replace(
        self,
        actor: AuthenticatedActor,
        kind: ImageOwner,
        owner_id: str,
        content: bytes | None,
        if_match: str,
        objects: ObjectStore,
    ) -> User | Organization | Workspace:
        if content is not None and (not content or len(content) > MAX_IMAGE_BYTES):
            raise identity_error(
                "invalid_profile_image", "Images must contain between 1 byte and 5 MiB.", ErrorCategory.invalid_request
            )
        async with short_session(self._sessions) as session:
            target, row = await self._owner(session, actor, kind, owner_id, write=True)
            require_etag(row, if_match)
        image_id = None
        if content is not None:
            normalized = await to_thread.run_sync(normalize_image, content)
            image_id = new_object_id("img")
            await objects.put(target.key(image_id), normalized, content_type="image/webp", if_none_match=True)
        async with identity_transaction(self._sessions, target.organization_id) as session:
            _, row = await self._owner(session, actor, kind, owner_id, write=True)
            require_etag(row, if_match)
            if image_id is not None:
                await require_object_publications(session, [target.key(image_id)])
            row.image_id, row.updated_at = image_id, utc_now()
            audit(
                session,
                actor=actor,
                action=f"{kind.value}.image_update",
                resource_type=kind.value,
                resource_id=owner_id,
                organization_id=None if kind is ImageOwner.user else target.organization_id,
                workspace_id=owner_id if kind is ImageOwner.workspace else None,
            )
            if isinstance(row, UserRecord):
                return User.model_validate(row)
            if isinstance(row, WorkspaceRecord):
                return Workspace.model_validate(row)
            return Organization.model_validate(row)

    async def read(
        self, actor: AuthenticatedActor, kind: ImageOwner, owner_id: str, image_id: str, objects: ObjectStore
    ) -> bytes:
        async with short_session(self._sessions) as session:
            target, row = await self._owner(session, actor, kind, owner_id, write=False)
            if row.image_id != image_id:
                raise not_found()
        try:
            async with objects.open(target.key(image_id)) as reader:
                data = bytearray()
                async for chunk in reader:
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise identity_error(
                            "profile_image_unavailable", "The image is unavailable.", ErrorCategory.unavailable
                        )
                return bytes(data)
        except ObjectNotFound as error:
            raise not_found() from error

    @staticmethod
    async def _owner(
        session: AsyncSession, actor: AuthenticatedActor, kind: ImageOwner, owner_id: str, *, write: bool
    ) -> tuple[ImageTarget, UserRecord | OrganizationRecord | WorkspaceRecord]:
        user = await require_user(session, actor, browser=True)
        organization = await singleton_organization(session)
        if kind is ImageOwner.workspace:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=owner_id,
                action=WorkspaceAction.role_binding_manage if write else WorkspaceAction.agent_read,
            )
            workspace = await session.get(WorkspaceRecord, owner_id)
            if workspace is None or workspace.deleted_at is not None:
                raise not_found()
            return ImageTarget(kind, owner_id, workspace.organization_id), workspace
        if actor.boundary_workspace_id is not None:
            raise not_found()
        if kind is ImageOwner.organization:
            if organization.id != owner_id:
                raise not_found()
            if write:
                await authorize_organization_admin(session, actor=actor)
            else:
                await ImageService._member(session, organization.id, user.id)
            return ImageTarget(kind, owner_id, organization.id), organization
        if owner_id != user.id:
            if write:
                raise not_found()
            await ImageService._member(session, organization.id, user.id)
            await ImageService._member(session, organization.id, owner_id)
        owner = await session.get(UserRecord, owner_id)
        if owner is None or owner.status != "active":
            raise not_found()
        return ImageTarget(kind, owner_id, organization.id), owner

    @staticmethod
    async def _member(session: AsyncSession, organization_id: str, user_id: str) -> None:
        if (
            await session.scalar(
                select(RoleBindingRecord.id).where(
                    RoleBindingRecord.organization_id == organization_id,
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.principal_id == user_id,
                )
            )
            is None
        ):
            raise not_found()
