"""Pure validation of prepared Run, Thread, and checkpoint relationships."""

from a13n_service.digests import digest_request

from .domain import Run, RunInputKind, RunLineageKind, RunStatus, Session, Thread, ThreadOriginKind, ThreadRole
from .input import AcceptedAgentInput
from .state import RunCheckpoint, RunPayloadEnvelope


def validate_prepared_run(run: Run, state: RunCheckpoint) -> None:
    if run.status is not RunStatus.accepted or run.version != 1:
        raise ValueError("prepared acceptance requires a version-one accepted Run")
    if state.checkpoint_kind != "initial" or state.checkpoint_seq != 0:
        raise ValueError("prepared acceptance requires initial Run state")
    validate_run_state_selection(run, state)


def validate_run_state_selection(run: Run, state: RunCheckpoint) -> None:
    """Validate immutable Run selection facts against any retained checkpoint."""

    if (state.run_id, state.thread_id) != (run.id, run.thread_id):
        raise ValueError("Run and state identities do not match")
    if (state.agent_id, state.agent_revision_id) != (run.agent_id, run.agent_revision_id):
        raise ValueError("Run and state Agent selection do not match")
    effective = state.effective_agent_config
    effective_payload = effective.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    if digest_request(effective_payload) != effective.content_digest:
        raise ValueError("Run effective configuration digest is invalid")
    if effective.content_digest != run.effective_agent_config_digest:
        raise ValueError("Run effective configuration digest does not match state")
    if effective.resolved_model.execution.observation() != run.model_execution_observation:
        raise ValueError("Run model observation does not match state")


def validate_new_thread(thread: Thread, run: Run, session: Session | None) -> None:
    _validate_new_thread_identity(thread, run)
    _validate_new_thread_session(thread, run, session)
    _validate_new_thread_origin(thread, run)


def _validate_new_thread_identity(thread: Thread, run: Run) -> None:
    if thread.version != 1 or thread.queue_version != 0 or thread.head_run_id is not None:
        raise ValueError("new Thread must start at version one with an empty head and queue")
    if thread.current_run_id != run.id:
        raise ValueError("new Thread must select its first Run")
    if (thread.organization_id, thread.session_id, thread.id) != (run.organization_id, run.session_id, run.thread_id):
        raise ValueError("new Thread and first Run scope do not match")
    if run.retry_of_run_id is not None:
        raise ValueError("the first Run of a new Thread cannot retry another Run")


def _validate_new_thread_session(thread: Thread, run: Run, session: Session | None) -> None:
    if session is not None and (session.id, session.organization_id) != (thread.session_id, thread.organization_id):
        raise ValueError("new Session and root Thread scope do not match")
    if session is not None and thread.role is not ThreadRole.root:
        raise ValueError("a new Session must begin with its root Thread")
    if session is None and thread.role is not ThreadRole.child:
        raise ValueError("an existing Session can accept only a child Thread")


def _validate_new_thread_origin(thread: Thread, run: Run) -> None:
    if thread.origin_kind is ThreadOriginKind.fork and run.lineage_kind is not RunLineageKind.fork:
        raise ValueError("fork Thread requires fork Run lineage")
    if thread.origin_kind is ThreadOriginKind.child and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("independent child Thread requires root Run lineage")
    if thread.origin_kind is ThreadOriginKind.new and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("new root Thread requires root Run lineage")


def validate_queued_run_input(
    run: Run,
    payload: RunPayloadEnvelope | None,
    accepted_input: AcceptedAgentInput,
) -> None:
    if run.input_kind is not RunInputKind.agent_input or run.retry_of_run_id is not None:
        raise ValueError("queue consumption requires ordinary non-retry Agent input")
    expected = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    actual = run.input if payload is None else payload.payload
    if actual != expected:
        raise ValueError("prepared queued Run input does not match its accepted submission")
