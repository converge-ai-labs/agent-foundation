"""Post-commit conversation authority, independent of expired execution Attempts."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.interactions.models import RunRecord
from a13n_service.memory.models import MemoryStorageRecord
from a13n_service.memory.service import failure

from .bindings import require_binding
from .domain import ScopeSettings
from .models import ScopeRecord
from .settings import read_settings


async def authorize_organization(
    session: AsyncSession,
    run_id: str,
    storage: MemoryStorageRecord,
    policy: dict[str, object],
) -> None:
    run = await session.get(RunRecord, run_id)
    scope = await session.get(ScopeRecord, storage.subject_id)
    if run is None or scope is None:
        raise failure("memory_scope_unavailable", "Conversation memory is unavailable.")
    binding = await require_binding(session, run_id)
    account = await session.get(AccountRecord, scope.account_id)
    settings = (await read_settings(session, scope.account_id)).memory
    group = ScopeSettings.model_validate(scope.settings_json)
    if (
        run.status != "completed"
        or account is None
        or account.status != "active"
        or account.deleted_at is not None
        or settings is None
        or not settings.auto_organize
        or not settings.use_memory
        or not settings.save_on_request
        or settings.provider_id != binding.provider_id
        or not group.enabled
        or not group.auto_organize
        or not group.use_memory
        or not group.save_on_request
        or scope.audience == "unknown"
        or not binding.auto_organize
        or binding.scope_id != scope.id
        or binding.provider_id != scope.provider_id
        or binding.scope_version is None
        or binding.scope_version < scope.binding_floor
        or storage.provider_identity != scope.provider_id
        or storage.workspace_id != scope.workspace_id
        or storage.organization_id != run.organization_id
    ):
        raise failure("memory_organization_revoked", "Conversation organization is disabled.")
    actor = AuthenticatedActor(
        principal=run.to_resource().authority_principal,
        auth_method="internal",
        credential_id="memory-organization",
        boundary_workspace_id=scope.workspace_id,
    )
    for action in (WorkspaceAction.memory_read, WorkspaceAction.memory_write):
        await authorize_workspace(session, actor=actor, workspace_id=scope.workspace_id, action=action)
