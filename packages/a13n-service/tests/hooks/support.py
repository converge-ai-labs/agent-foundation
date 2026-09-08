from __future__ import annotations

from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.control_records import inbox_counter_record
from a13n_service.interactions.domain import (
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.models import ThreadRecord
from a13n_service.interactions.records import run_record, session_record, thread_record
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    SESSION_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)

RUN_ID = "run_7171717171717171"
SECRET_ID = "sec_7171717171717171"


def hook_actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_7171717171717171",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-hook-management-test",
    )


async def seed_hook_actor_access(
    sessions: async_sessionmaker[AsyncSession],
    *,
    workspace_role: str = "builder",
) -> None:
    async with transaction(sessions) as database:
        database.add(
            UserRecord(
                id=USER_ID,
                email="hook-builder@example.com",
                normalized_email="hook-builder@example.com",
                name="Hook Builder",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        database.add_all(
            (
                RoleBindingRecord(
                    id="rb_hookorg71717171",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=ORGANIZATION_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_hookws717171717",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key=workspace_role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )


async def seed_run_and_secret(sessions: async_sessionmaker[AsyncSession]) -> None:
    config = effective_agent_config()
    async with transaction(sessions) as database:
        database.add(
            SecretRecord(
                id=SECRET_ID,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="hook-signing",
                version=1,
                ciphertext=b"ciphertext",
                nonce=b"0" * 12,
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
        database.add(
            session_record(
                Session(
                    id=SESSION_ID,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        )
        database.add(
            thread_record(
                Thread(
                    id=THREAD_ID,
                    version=1,
                    queue_version=0,
                    organization_id=ORGANIZATION_ID,
                    session_id=SESSION_ID,
                    role=ThreadRole.root,
                    origin_kind=ThreadOriginKind.new,
                    current_run_id=RUN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        )
        database.add(
            run_record(
                Run(
                    id=RUN_ID,
                    version=1,
                    organization_id=ORGANIZATION_ID,
                    authority_principal={"principal_type": "user", "principal_id": USER_ID},
                    session_id=SESSION_ID,
                    thread_id=THREAD_ID,
                    lineage_kind=RunLineageKind.root,
                    trigger_type="user_input",
                    agent_id=AGENT_ID,
                    agent_revision_id=AGENT_REVISION_ID,
                    effective_agent_config_digest=config.content_digest,
                    runtime_lock_digest=config.runtime_lock_digest,
                    model_execution_observation=config.resolved_model.execution.observation(),
                    priority=0,
                    queue_name="default",
                    available_at=NOW,
                    next_attempt_fence=1,
                    recovery_budget=RecoveryBudget(
                        policy_version="1",
                        max_recovery_attempts=1,
                        max_handoffs=1,
                    ),
                    attempts_started=0,
                    recovery_attempts_started=0,
                    handoffs_completed=0,
                    usage_charged=RecoveryUsage(),
                    request_fingerprint="f" * 64,
                    status=RunStatus.accepted,
                    input_kind=RunInputKind.agent_input,
                    input={"message": "hello"},
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        )
        await database.flush()
        persisted_thread = await database.get(ThreadRecord, THREAD_ID)
        assert persisted_thread is not None
        database.add(inbox_counter_record(persisted_thread.to_resource()))
