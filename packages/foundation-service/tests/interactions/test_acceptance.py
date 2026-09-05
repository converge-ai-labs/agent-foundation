from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks import InlineHookSubscriptionInput, InlineHookValidator, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceService
from a13n_service.interactions.domain import (
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunInputKind,
    RunLineageKind,
    RunPayloadObjectRef,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_retry_state,
    initialize_start_state,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunPayloadStore, RunStateStore
from a13n_service.interactions.state import RunPayloadEnvelope
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import ObjectStore, short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import seed_hook_actor_access

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    SESSION_ID,
    TENANT_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)

pytestmark = pytest.mark.anyio


def _inline_hooks() -> InlineHookValidator:
    return InlineHookValidator(EndpointPolicy())


def _accepted_run(
    *,
    run_id: str,
    thread_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    config: EffectiveAgentConfig | None = None,
) -> Run:
    config = config or effective_agent_config()
    return Run(
        id=run_id,
        version=1,
        tenant_id=TENANT_ID,
        authority_principal=PrincipalRef(
            principal_type=PrincipalType.user,
            principal_id=USER_ID,
        ),
        session_id=SESSION_ID,
        thread_id=thread_id,
        lineage_kind=RunLineageKind.root,
        trigger_type="user_input",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config_digest=config.content_digest,
        runtime_lock_digest=config.runtime_lock_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        connector_connection_selections=({"connector_connection_id": "cconn_1234567890abcdef"},),
        priority=0,
        queue_name="default",
        available_at=NOW,
        next_attempt_fence=1,
        recovery_budget=RecoveryBudget(
            policy_version="1",
            max_recovery_attempts=3,
            max_handoffs=2,
        ),
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input={"schema_version": "1", "content": "hello"},
        created_at=NOW,
        updated_at=NOW,
    )


def _with_input_object(run: Run, reference: RunPayloadObjectRef) -> Run:
    payload = run.model_dump(
        mode="python",
        exclude={"input", "input_object", "output", "output_object"},
    )
    payload["input_object"] = reference
    return Run.model_validate(payload)


async def test_accepts_prepared_root_state_and_round_trips_the_run(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    seed = RunStateSeed(
        run_id="run_1111111111111111",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=state.thread_id,
        idempotency_key="start-1",
        request_fingerprint="1" * 64,
    )
    session = Session(
        id=SESSION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    thread = Thread(
        id=state.thread_id,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )

    invalid_config = state.effective_agent_config.model_copy(update={"content_digest": "f" * 64})
    invalid_state = state.model_copy(update={"effective_agent_config": invalid_config})
    invalid_run = run.model_copy(update={"effective_agent_config_digest": "f" * 64})
    with pytest.raises(ValueError, match="configuration digest is invalid"):
        await service.accept_new_thread(
            session=session,
            thread=thread,
            run=invalid_run,
            state=invalid_state,
        )

    receipt = await service.accept_new_thread(session=session, thread=thread, run=run, state=state)

    assert receipt.thread_version == 1
    assert await states.read(TENANT_ID, run.id, expected_thread_id=thread.id)
    async with short_session(interaction_sessions) as database:
        record = await database.get(RunRecord, run.id)
        assert record is not None
        assert record.to_resource() == run
        assert record.environment_id is None

    async with transaction(interaction_sessions) as database:
        record = await database.get(RunRecord, run.id)
        assert record is not None
        record.version += 1
    replay = await service.accept_new_thread(session=session, thread=thread, run=run, state=state)

    assert replay == receipt
    assert (replay.run_version, replay.status) == (1, "accepted")


async def test_acceptance_atomically_creates_inline_hook_and_accepted_delivery(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await seed_hook_actor_access(interaction_sessions)
    secret_id = "sec_9191919191919191"
    async with transaction(interaction_sessions) as database:
        database.add(
            SecretRecord(
                id=secret_id,
                organization_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="inline-hook-signing",
                version=1,
                ciphertext=b"ciphertext",
                nonce=b"1" * 12,
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    service = RunAcceptanceService(
        interaction_sessions,
        RunStateStore(interaction_object_store),
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    seed = RunStateSeed(
        run_id="run_9191919191919191",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=THREAD_ID,
        idempotency_key="inline-hook",
        request_fingerprint="9" * 64,
    )
    session = Session(
        id=SESSION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    thread = Thread(
        id=THREAD_ID,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )
    hook = InlineHookSubscriptionInput(
        hook_names=("run.accepted", "run.completed"),
        webhook=WebhookDestinationConfig(
            endpoint_url="https://8.8.8.8/foundation",
            signing_secret_id=secret_id,
        ),
    )

    with pytest.raises(RunAcceptanceError) as invalid_endpoint:
        await service.accept_new_thread(
            session=session,
            thread=thread,
            run=run,
            state=state,
            hook_subscription=hook.model_copy(
                update={"webhook": hook.webhook.model_copy(update={"endpoint_url": "https://127.0.0.1/hook"})}
            ),
        )
    assert invalid_endpoint.value.code == "invalid_webhook_endpoint"

    receipt = await service.accept_new_thread(
        session=session,
        thread=thread,
        run=run,
        state=state,
        hook_subscription=hook,
    )
    replay = await service.accept_new_thread(
        session=session,
        thread=thread,
        run=run,
        state=state,
        hook_subscription=hook,
    )

    assert receipt.hook_subscription_id is not None
    assert replay == receipt
    with pytest.raises(RunAcceptanceError, match="different inline Hook"):
        await service.accept_new_thread(
            session=session,
            thread=thread,
            run=run,
            state=state,
            hook_subscription=hook.model_copy(
                update={"webhook": hook.webhook.model_copy(update={"endpoint_url": "https://other.example.com/hook"})}
            ),
        )
    async with short_session(interaction_sessions) as database:
        head = await database.get(HookSubscriptionRecord, receipt.hook_subscription_id)
        assert head is not None
        revision = await database.get(HookSubscriptionRevisionRecord, head.current_revision_id)
        assert revision is not None
        assert (revision.session_id, revision.thread_id, revision.run_id) == (SESSION_ID, THREAD_ID, run.id)
        deliveries = (await database.scalars(select(OutboxRecord))).all()
        assert len(deliveries) == 1
        assert deliveries[0].destination_ref == revision.id


async def test_acceptance_rejects_input_payload_owned_by_another_run(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    seed = RunStateSeed(
        run_id="run_aaaaaaaaaaaaaaaa",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    inline = _accepted_run(
        run_id=seed.run_id,
        thread_id=state.thread_id,
        idempotency_key="wrong-input-owner",
        request_fingerprint="a" * 64,
    )
    run = _with_input_object(
        inline,
        RunPayloadObjectRef(
            object_key=(f"tenants/{TENANT_ID}/runs/run_bbbbbbbbbbbbbbbb/payloads/input/{'b' * 64}.json"),
            digest_sha256="b" * 64,
            size_bytes=123,
            content_type="application/vnd.converge.run-payload+json",
            schema_version="1",
        ),
    )

    with pytest.raises(RunObjectIntegrityError, match="owned by the selected Run"):
        await service.accept_new_thread(
            session=Session(
                id=SESSION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                created_at=NOW,
                updated_at=NOW,
            ),
            thread=Thread(
                id=state.thread_id,
                version=1,
                queue_version=0,
                tenant_id=TENANT_ID,
                session_id=SESSION_ID,
                role=ThreadRole.root,
                origin_kind=ThreadOriginKind.new,
                current_run_id=run.id,
                created_at=NOW,
                updated_at=NOW,
            ),
            run=run,
            state=state,
        )


async def test_root_retry_is_atomic_exact_and_idempotent(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    payloads = RunPayloadStore(interaction_object_store)
    service = RunAcceptanceService(
        interaction_sessions,
        states,
        payloads,
        _inline_hooks(),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    config = effective_agent_config()
    first_seed = RunStateSeed(
        run_id="run_2222222222222222",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    first_state = initialize_start_state(first_seed, thread_id=THREAD_ID)
    first_inline = _accepted_run(
        run_id=first_seed.run_id,
        thread_id=first_state.thread_id,
        idempotency_key="start-2",
        request_fingerprint="2" * 64,
        config=config,
    ).model_copy(
        update={
            "native_tool_contexts": (
                {
                    "kind": "account",
                    "account_id": "acct_retry",
                    "provider_key": "slack",
                    "execution_principal_ref": {"principal_type": "user", "principal_id": USER_ID},
                    "allowed_actions": ["slack.send_message"],
                    "target_scope": {"channel_ids": ["C1"]},
                },
            )
        }
    )
    first = _with_input_object(
        first_inline,
        await payloads.create(
            TENANT_ID,
            RunPayloadEnvelope(
                run_id=first_inline.id,
                payload_kind="input",
                payload_schema_version="1",
                payload={"schema_version": "1", "content": "hello"},
            ),
        ),
    )
    await service.accept_new_thread(
        session=Session(
            id=SESSION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=first_state.thread_id,
            version=1,
            queue_version=0,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=first.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=first,
        state=first_state,
    )
    async with transaction(interaction_sessions) as database:
        first_record = await database.get(RunRecord, first.id)
        thread_record = await database.get(ThreadRecord, first.thread_id)
        assert first_record is not None and thread_record is not None
        first_record.status = RunStatus.failed.value
        first_record.failure_json = {
            "code": "preparation_failed",
            "message": "Preparation failed.",
            "details": {},
            "retry_hint": "none",
        }
        first_record.sealed_at = NOW + timedelta(seconds=1)
        thread_record.version = 2
        thread_record.updated_at = NOW + timedelta(seconds=1)

    second_seed = RunStateSeed(
        run_id="run_3333333333333333",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    second_state = initialize_retry_state(
        second_seed,
        thread_id=first.thread_id,
        source_lineage_kind=RunLineageKind.root,
        source_input_kind=RunInputKind.agent_input,
        parent=None,
    )
    second_inline = _accepted_run(
        run_id=second_seed.run_id,
        thread_id=first.thread_id,
        idempotency_key="continue-after-failure",
        request_fingerprint="3" * 64,
        config=config,
    )
    second = _with_input_object(
        second_inline,
        await payloads.create(
            TENANT_ID,
            RunPayloadEnvelope(
                run_id=second_inline.id,
                payload_kind="input",
                payload_schema_version="1",
                payload={"schema_version": "1", "content": "hello"},
            ),
        ),
    ).model_copy(update={"retry_of_run_id": first.id, "native_tool_contexts": first.native_tool_contexts})
    await states.create(TENANT_ID, second_state)

    with pytest.raises(RunAcceptanceError, match="preserve its source"):
        await service.advance_thread(
            run=second.model_copy(update={"native_tool_contexts": ()}),
            state=second_state,
            expected_thread_version=2,
            expected_current_run_id=first.id,
            expected_head_run_id=None,
            next_head_run_id=None,
        )
    receipt = await service.advance_thread(
        run=second,
        state=second_state,
        expected_thread_version=2,
        expected_current_run_id=first.id,
        expected_head_run_id=None,
        next_head_run_id=None,
    )
    replay = await service.advance_thread(
        run=second,
        state=second_state,
        expected_thread_version=2,
        expected_current_run_id=first.id,
        expected_head_run_id=None,
        next_head_run_id=None,
    )

    assert receipt == replay
    assert receipt.thread_version == 3
    async with short_session(interaction_sessions) as database:
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
        assert thread is not None
        assert (thread.version, thread.current_run_id, thread.head_run_id) == (3, second.id, None)

    conflicting = second.model_copy(
        update={
            "id": "run_4444444444444444",
            "request_fingerprint": "4" * 64,
        }
    )
    conflicting_state = second_state.model_copy(update={"run_id": conflicting.id})
    with pytest.raises(RunAcceptanceError, match="different Run intent"):
        await service.advance_thread(
            run=conflicting,
            state=conflicting_state,
            expected_thread_version=3,
            expected_current_run_id=second.id,
            expected_head_run_id=None,
            next_head_run_id=None,
        )


async def test_new_session_cannot_begin_with_a_child_thread(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    seed = RunStateSeed(
        run_id="run_6666666666666666",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=state.thread_id,
        idempotency_key="invalid-child-session",
        request_fingerprint="6" * 64,
    )
    thread = Thread(
        id=state.thread_id,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.child,
        origin_kind=ThreadOriginKind.child,
        origin_thread_id="thread-11111111111111111111111111111111",
        origin_run_id="run_7777777777777777",
        current_run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ValueError, match="new Session must begin with its root Thread"):
        await service.accept_new_thread(
            session=Session(
                id=SESSION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                created_at=NOW,
                updated_at=NOW,
            ),
            thread=thread,
            run=run,
            state=state,
        )


async def test_existing_session_cannot_accept_another_root_thread(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    seed = RunStateSeed(
        run_id="run_8888888888888888",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=state.thread_id,
        idempotency_key="invalid-second-root",
        request_fingerprint="8" * 64,
    )
    thread = Thread(
        id=state.thread_id,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ValueError, match="existing Session can accept only a child Thread"):
        await service.accept_new_thread(session=None, thread=thread, run=run, state=state)
