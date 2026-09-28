"""Create the first tenant and administrator atomically, once."""

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, advisory_lock, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.authorize import Scope
from a13n_service.tenancy.credentials import hash_password
from a13n_service.tenancy.schemas import Email, NewPassword
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow
from a13n_service.tenancy.workspaces import WorkspaceCreated, insert_workspace


class BootstrapInput(BaseModel):
    email: Email
    password: NewPassword


class AlreadyBootstrapped(Exception):
    """The Service already has an organization; bootstrap changes nothing."""


async def initialized(storage: Storage) -> bool:
    async with short_session(storage) as session:
        return await session.scalar(select(OrganizationRow.id).limit(1)) is not None


@dataclass(frozen=True)
class Bootstrapped:
    organization_id: str
    workspace_id: str
    principal_id: str


async def bootstrap(
    storage: Storage, request: BootstrapInput, *, on_created: WorkspaceCreated | None = None
) -> Bootstrapped:
    password_hash = await hash_password(request.password.get_secret_value())
    result = Bootstrapped(new_object_id("org"), new_object_id("ws"), new_object_id("usr"))
    async with transaction(storage) as session:
        await advisory_lock(session, "bootstrap")
        if await session.scalar(select(OrganizationRow.id).limit(1)):
            raise AlreadyBootstrapped()
        session.add(OrganizationRow(id=result.organization_id, name="Default organization"))
        await session.flush()
        await insert_workspace(
            session,
            workspace_id=result.workspace_id,
            organization_id=result.organization_id,
            name="Default workspace",
            on_created=on_created,
        )
        session.add(PrincipalRow(id=result.principal_id, kind="user", name=request.email, email=request.email))
        await session.flush()
        session.add(PasswordRow(principal_id=result.principal_id, hash=password_hash))
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=result.organization_id,
                principal_id=result.principal_id,
                role="admin",
                created_by_id=result.principal_id,
            )
        )
        record(
            session,
            Scope(result.organization_id, result.workspace_id),
            actor_id=result.principal_id,
            action="organization.bootstrap",
            target_kind="organization",
            target_id=result.organization_id,
        )
    return result
