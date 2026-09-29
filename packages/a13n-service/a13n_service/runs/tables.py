"""Sessions, threads, queued input, runs, attempts and usage facts.

Per-row states are CHECK constraints. Rules comparing old and new values (legal transitions, frozen
selections, immutable facts) and cross-row pointer agreement are the triggers declared with each table.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, rules, trigger
from a13n_service.runs.schemas import EntryStatus, RunStatus

_TOUCH_THREADS = """
CREATE FUNCTION touch_threads() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- The thread version is the change signal for every visible inbox change; stamp_resource bumps it.
    UPDATE threads SET updated_at = clock_timestamp() WHERE id IN (SELECT DISTINCT thread_id FROM changed);
    RETURN NULL;
END $$
"""


def _thread_pointer_check(table: str) -> str:
    return (
        f"CREATE CONSTRAINT TRIGGER check_thread_pointers AFTER INSERT OR UPDATE ON {table}"
        " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_thread_pointers()"
    )


_CHECK_THREAD_POINTERS = """
CREATE FUNCTION check_thread_pointers() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t threads; target text;
BEGIN
    IF TG_TABLE_NAME = 'threads' THEN target := NEW.id; ELSE target := NEW.thread_id; END IF;
    SELECT * INTO t FROM threads WHERE id = target;
    IF t.current_run_id IS DISTINCT FROM (
            SELECT id FROM runs WHERE thread_id = target AND status IN ('accepted', 'running'))
        OR (t.head_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM runs WHERE id = t.head_run_id AND status IN ('completed', 'waiting')))
        OR (t.last_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM runs WHERE id = t.last_run_id AND sealed_at IS NOT NULL))
    THEN RAISE EXCEPTION 'thread % run pointers disagree with run status', target; END IF;
    RETURN NULL;
END $$
"""


class SessionRow(Stamped, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_workspace_updated", "workspace_id", "updated_at", "id"),
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "id", "last_run_id"],
            ["runs.workspace_id", "runs.session_id", "runs.id"],
            name="fk_sessions_last_run",
            use_alter=True,
        ),
        # A run's acceptance updates the session but not its version, so label edits keep their ETag.
        rules(unversioned=("last_run_id", "updated_at")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    labels: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    # The most recently accepted run of any of its threads: the list preview.
    last_run_id: Mapped[str | None] = mapped_column(String(72))


class ThreadRow(Stamped, Base):
    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_threads_session_created", "workspace_id", "session_id", "created_at", "id"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "session_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "session_id"], ["sessions.workspace_id", "sessions.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "origin_thread_id"],
            ["threads.workspace_id", "threads.session_id", "threads.id"],
            name="fk_threads_origin_thread",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "origin_run_id"],
            ["runs.workspace_id", "runs.session_id", "runs.id"],
            name="fk_threads_origin_run",
            use_alter=True,
        ),
        *(
            ForeignKeyConstraint(
                ["id", pointer],
                ["runs.thread_id", "runs.id"],
                name=f"fk_threads_{pointer}",
                use_alter=True,
                deferrable=True,
                initially="DEFERRED",
            )
            for pointer in ("current_run_id", "head_run_id", "last_run_id")
        ),
        CheckConstraint("origin IN ('new', 'fork', 'child')", name="origin"),
        CheckConstraint(
            "(origin = 'new' AND origin_thread_id IS NULL AND origin_run_id IS NULL AND origin_tool_call_id IS NULL)"
            " OR (origin = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL"
            " AND origin_tool_call_id IS NULL)"
            " OR (origin = 'child' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL"
            " AND origin_tool_call_id IS NOT NULL)",
            name="origin_links",
        ),
        CheckConstraint("(origin = 'child') = (subagent IS NOT NULL)", name="subagent"),
        CheckConstraint("origin = 'new' OR message_history = '[]'::jsonb", name="imported_history"),
        # A spawn is identified by its tool call, so the parent's recovery finds the same child thread.
        Index(
            "uq_threads_child_origin",
            "origin_run_id",
            "origin_tool_call_id",
            unique=True,
            postgresql_where=text("origin = 'child'"),
        ),
        # A parent's child threads in the order they were spawned, which its subagent tools page through.
        Index(
            "ix_threads_children",
            "origin_thread_id",
            "created_at",
            "id",
            postgresql_where=text("origin = 'child'"),
        ),
        # advance_threads evidence: idle threads whose automatic advancement is not paused by a failed run.
        Index(
            "ix_threads_advanceable",
            "id",
            postgresql_where=text(
                "current_run_id IS NULL AND archived_at IS NULL AND last_run_id IS NOT DISTINCT FROM head_run_id"
            ),
        ),
        rules(
            _CHECK_THREAD_POINTERS,
            _thread_pointer_check("threads"),
            # Tables whose statements change a thread's inbox or mounts share one version bump.
            _TOUCH_THREADS,
            """
            CREATE FUNCTION guard_thread_history() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.message_history IS DISTINCT FROM OLD.message_history
                THEN RAISE EXCEPTION 'thread initial history is immutable'; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("threads", "guard_thread_history"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    session_id: Mapped[str]
    origin: Mapped[str]
    origin_thread_id: Mapped[str | None]
    origin_run_id: Mapped[str | None] = mapped_column(String(72))
    origin_tool_call_id: Mapped[str | None]
    # Imported model context is immutable and distinct from checkpoints and executed facts.
    message_history: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The name of the async subagent edge that spawned a child thread, as its parent's graph declared it then.
    subagent: Mapped[str | None]
    # current: accepted or running. head: latest completed or waiting. last: most recently sealed.
    current_run_id: Mapped[str | None] = mapped_column(String(72))
    head_run_id: Mapped[str | None] = mapped_column(String(72))
    last_run_id: Mapped[str | None] = mapped_column(String(72))
    mcp_headers: Mapped[dict] = mapped_column(JSONB)
    labels: Mapped[dict] = mapped_column(JSONB)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboxEntryRow(Base):
    __tablename__ = "inbox_entries"
    __table_args__ = (
        # Reordering sets several positions in one transaction; uniqueness is checked once they are all set.
        UniqueConstraint("thread_id", "position", deferrable=True, initially="IMMEDIATE"),
        UniqueConstraint("thread_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        ForeignKeyConstraint(["agent_id", "agent_revision_id"], ["agent_revisions.agent_id", "agent_revisions.id"]),
        ForeignKeyConstraint(
            ["thread_id", "assigned_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_inbox_entries_assigned_run",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "child_run_id"],
            ["runs.workspace_id", "runs.id"],
            name="fk_inbox_entries_child_run",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "origin_run_id"],
            ["runs.workspace_id", "runs.id"],
            name="fk_inbox_entries_origin_run",
            use_alter=True,
        ),
        Index(
            "uq_inbox_entries_request",
            "workspace_id",
            "principal_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
        ),
        Index(
            "uq_inbox_entries_child_result", "child_run_id", unique=True, postgresql_where=text("kind = 'child_result'")
        ),
        Index("ix_inbox_entries_pending", "thread_id", "position", postgresql_where=text("status = 'pending'")),
        Index("ix_inbox_entries_assigned", "assigned_run_id", postgresql_where=text("status = 'assigned'")),
        CheckConstraint("kind IN ('message', 'child_result')", name="kind"),
        CheckConstraint("delivery IN ('steer', 'next_run')", name="delivery"),
        CheckConstraint("status IN ('pending', 'assigned', 'consumed', 'failed', 'withdrawn')", name="status"),
        CheckConstraint("position > 0", name="position"),
        CheckConstraint("size >= 0", name="size"),
        CheckConstraint("(kind = 'message') = (agent_id IS NOT NULL)", name="message_agent"),
        CheckConstraint(
            "(kind = 'child_result') = (child_run_id IS NOT NULL AND origin_run_id IS NOT NULL)", name="child_result"
        ),
        CheckConstraint("status NOT IN ('assigned', 'consumed') OR assigned_run_id IS NOT NULL", name="assignment"),
        CheckConstraint("status <> 'pending' OR assigned_run_id IS NULL", name="pending"),
        CheckConstraint("(status = 'consumed') = (incorporated_checkpoint_seq IS NOT NULL)", name="consumed"),
        CheckConstraint("(status IN ('pending', 'assigned')) = (finished_at IS NULL)", name="finished"),
        CheckConstraint("(status = 'failed') = (failure IS NOT NULL)", name="failure"),
        CheckConstraint("request_kind IN ('thread', 'message', 'fork')", name="request_kind"),
        CheckConstraint(
            "(request_key IS NULL) = (request_digest IS NULL)"
            " AND (request_key IS NULL) = (request_kind IS NULL)"
            " AND (request_key IS NULL) = (request_target IS NULL)",
            name="request",
        ),
        rules(
            """
            CREATE FUNCTION guard_entry() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.thread_id, NEW.kind, NEW.principal_id,
                       NEW.authority, NEW.child_run_id, NEW.origin_run_id, NEW.request_key, NEW.request_digest,
                       NEW.request_kind, NEW.request_target, NEW.created_at)
                   IS DISTINCT FROM ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.thread_id, OLD.kind,
                       OLD.principal_id, OLD.authority, OLD.child_run_id, OLD.origin_run_id, OLD.request_key,
                       OLD.request_digest, OLD.request_kind, OLD.request_target, OLD.created_at)
                THEN RAISE EXCEPTION 'entry identity and replay evidence are immutable'; END IF;
                IF OLD.status IN ('consumed', 'failed', 'withdrawn')
                THEN RAISE EXCEPTION 'settled entries are immutable'; END IF;
                IF OLD.status = 'assigned' AND (
                    (to_jsonb(NEW) - ARRAY['status', 'assigned_run_id', 'incorporated_checkpoint_seq', 'failure',
                                           'finished_at'])
                    IS DISTINCT FROM
                    (to_jsonb(OLD) - ARRAY['status', 'assigned_run_id', 'incorporated_checkpoint_seq', 'failure',
                                           'finished_at'])
                    OR (NEW.status <> 'pending' AND NEW.assigned_run_id IS DISTINCT FROM OLD.assigned_run_id)
                ) THEN RAISE EXCEPTION 'assigned entries are immutable'; END IF;
                IF NEW.status <> OLD.status AND NOT (
                    (OLD.status = 'pending' AND NEW.status IN ('assigned', 'failed', 'withdrawn'))
                    OR (OLD.status = 'assigned' AND NEW.status IN ('pending', 'consumed', 'failed', 'withdrawn'))
                ) THEN RAISE EXCEPTION 'invalid entry transition % to %', OLD.status, NEW.status; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("inbox_entries", "guard_entry"),
            "CREATE TRIGGER touch_threads_on_insert AFTER INSERT ON inbox_entries"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_update AFTER UPDATE ON inbox_entries"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    thread_id: Mapped[str]
    kind: Mapped[str]
    delivery: Mapped[str]
    position: Mapped[int] = mapped_column(BigInteger)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    # Bounded inline content; large material is an asset the payload references by ID.
    payload: Mapped[dict] = mapped_column(JSONB)
    size: Mapped[int]
    agent_id: Mapped[str | None]
    agent_revision_id: Mapped[str | None]
    options: Mapped[dict] = mapped_column(JSONB)
    child_run_id: Mapped[str | None] = mapped_column(String(72))
    origin_run_id: Mapped[str | None] = mapped_column(String(72))
    # Replay evidence: the key, the operation it named, its target and the canonical request digest.
    request_key: Mapped[str | None]
    request_digest: Mapped[str | None]
    request_kind: Mapped[str | None]
    request_target: Mapped[str | None]
    status: Mapped[EntryStatus] = mapped_column(String)
    assigned_run_id: Mapped[str | None] = mapped_column(String(72))
    incorporated_checkpoint_seq: Mapped[int | None] = mapped_column(BigInteger)
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_RUN_PROGRESS = (
    "'status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts',"
    " 'checkpoint', 'display', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at',"
    " 'sealed_at', 'version', 'updated_at'"
)


class RunRow(Stamped, Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_thread_created", "thread_id", "created_at", "id"),
        Index("ix_runs_session_created", "workspace_id", "session_id", "created_at", "id"),
        UniqueConstraint("thread_id", "id"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "session_id", "id"),
        UniqueConstraint("source_entry_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "thread_id"], ["threads.workspace_id", "threads.session_id", "threads.id"]
        ),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        ForeignKeyConstraint(["agent_id", "agent_revision_id"], ["agent_revisions.agent_id", "agent_revisions.id"]),
        ForeignKeyConstraint(
            ["thread_id", "source_entry_id"],
            ["inbox_entries.thread_id", "inbox_entries.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "parent_run_id"], ["runs.workspace_id", "runs.session_id", "runs.id"]
        ),
        ForeignKeyConstraint(
            ["id", "current_attempt_id"],
            ["run_attempts.run_id", "run_attempts.id"],
            name="fk_runs_current_attempt",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        # One active run per thread.
        Index(
            "uq_runs_active_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('accepted', 'running')"),
        ),
        Index("ix_runs_due", "available_at", "id", postgresql_where=text("status = 'accepted'")),
        Index(
            "uq_runs_resume_request",
            "workspace_id",
            "resumed_by_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
        ),
        # Active-use evidence for shared environments: which accepted or running runs mount an instance.
        Index(
            "ix_runs_active_mounts",
            "environment_mounts",
            postgresql_using="gin",
            postgresql_where=text("status IN ('accepted', 'running')"),
        ),
        CheckConstraint("revision_selection IN ('pinned', 'default', 'inherited')", name="revision_selection"),
        CheckConstraint("trigger IN ('input', 'queued', 'resume', 'child_result', 'spawned')", name="trigger"),
        CheckConstraint("lineage IN ('root', 'continue', 'fork')", name="lineage"),
        CheckConstraint(
            "status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("wait_reason IN ('approval', 'call', 'multiple')", name="wait_reason"),
        # A run starts from exactly one source: a queued entry or the answers resuming its parent.
        CheckConstraint("(source_entry_id IS NULL) <> (resume IS NULL)", name="source"),
        CheckConstraint("(resume IS NULL) = (resumed_by_id IS NULL)", name="resumed_by"),
        CheckConstraint("(request_key IS NULL) = (request_digest IS NULL)", name="request"),
        CheckConstraint("request_key IS NULL OR resume IS NOT NULL", name="request_resume"),
        CheckConstraint("(trigger = 'resume') = (resume IS NOT NULL)", name="resume_trigger"),
        CheckConstraint("(lineage = 'root') = (parent_run_id IS NULL)", name="parent"),
        CheckConstraint(
            "(status IN ('waiting', 'completed', 'failed', 'cancelled')) = (sealed_at IS NOT NULL)", name="sealed"
        ),
        CheckConstraint("(status IN ('failed', 'cancelled')) = (failure IS NOT NULL)", name="failure"),
        CheckConstraint(
            "status NOT IN ('waiting', 'completed') OR (checkpoint IS NOT NULL AND display IS NOT NULL)",
            name="outcome_state",
        ),
        CheckConstraint("(status = 'waiting') = (wait_reason IS NOT NULL AND pending IS NOT NULL)", name="waiting"),
        CheckConstraint("(status = 'running') = (current_attempt_id IS NOT NULL)", name="attempt"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts"),
        rules(
            f"""
            CREATE FUNCTION guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.status IN ('waiting', 'completed', 'failed', 'cancelled') THEN
                    -- Labels are the one editable property of a sealed run.
                    IF (to_jsonb(NEW) - ARRAY['labels', 'version', 'updated_at'])
                        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['labels', 'version', 'updated_at'])
                    THEN RAISE EXCEPTION 'sealed run facts are immutable'; END IF;
                    RETURN NEW;
                END IF;
                IF (to_jsonb(NEW) - ARRAY[{_RUN_PROGRESS}]) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[{_RUN_PROGRESS}])
                THEN RAISE EXCEPTION 'accepted run selection is immutable'; END IF;
                IF NEW.status <> OLD.status AND NOT (
                    (OLD.status = 'accepted' AND NEW.status IN ('running', 'failed', 'cancelled'))
                    OR (OLD.status = 'running' AND NEW.status IN ('accepted', 'waiting', 'completed', 'failed',
                                                                   'cancelled'))
                ) THEN RAISE EXCEPTION 'invalid run transition % to %', OLD.status, NEW.status; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("runs", "guard_run"),
            _thread_pointer_check("runs"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    session_id: Mapped[str]
    thread_id: Mapped[str]
    agent_id: Mapped[str]
    agent_revision_id: Mapped[str]
    revision_selection: Mapped[str]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    # `RunOptions` frozen at acceptance: validated, their overrides with pins resolved.
    options: Mapped[dict] = mapped_column(JSONB)
    # The digest of the options as the source message submitted them, or as the run's history inherited them:
    # a steer joins the run only when its own options have this digest.
    options_digest: Mapped[str] = mapped_column(String(64))
    # The thread's caller headers, frozen at acceptance; no view shows them.
    mcp_headers: Mapped[dict] = mapped_column(JSONB)
    environment_mounts: Mapped[list] = mapped_column(JSONB)
    # The thread's memory mounts, frozen at acceptance.
    memory_mounts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    source_entry_id: Mapped[str | None] = mapped_column(String(72))
    resume: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    resumed_by_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    request_key: Mapped[str | None]
    request_digest: Mapped[str | None]
    trigger: Mapped[str]
    lineage: Mapped[str]
    parent_run_id: Mapped[str | None] = mapped_column(String(72))
    status: Mapped[RunStatus] = mapped_column(String)
    wait_reason: Mapped[str | None]
    pending: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_attempt_id: Mapped[str | None] = mapped_column(String(72))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int]
    # Typed pointers to the latest committed state and display objects; only fenced commits move them.
    checkpoint: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    display: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Each mounted memory's change cursor its context has delivered, by memory ID, committed with `checkpoint`; a
    # null cursor gives the next run the memory's full context.
    memory_cursors: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Any JSON value, bounded by `worker.output_bytes`; large results are assets the output references.
    output: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True)
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    usage_at_seal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    labels: Mapped[dict] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_ATTEMPT_PROGRESS = (
    "'status', 'harness_run_id', 'lease_expires_at', 'heartbeat_at', 'yield_reason', 'failure', 'started_at',"
    " 'finished_at', 'updated_at'"
)

_CHECK_ATTEMPT_POINTER = """
CREATE FUNCTION check_attempt_pointer() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs; target text;
BEGIN
    IF TG_TABLE_NAME = 'runs' THEN target := NEW.id; ELSE target := NEW.run_id; END IF;
    SELECT * INTO r FROM runs WHERE id = target;
    IF r.current_attempt_id IS DISTINCT FROM (
        SELECT id FROM run_attempts WHERE run_id = target AND status IN ('leased', 'running'))
    THEN RAISE EXCEPTION 'run % attempt pointer disagrees with attempt status', target; END IF;
    RETURN NULL;
END $$
"""


class AttemptRow(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "number"),
        UniqueConstraint("run_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["run_id", "replaces_attempt_id"], ["run_attempts.run_id", "run_attempts.id"]),
        # One live attempt per run.
        Index("uq_run_attempts_live", "run_id", unique=True, postgresql_where=text("status IN ('leased', 'running')")),
        Index("ix_run_attempts_expiry", "lease_expires_at", postgresql_where=text("status IN ('leased', 'running')")),
        CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("start_reason IN ('initial', 'recovery', 'handoff')", name="start_reason"),
        CheckConstraint(
            "(status IN ('succeeded', 'yielded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)", name="finished"
        ),
        CheckConstraint("(start_reason = 'initial') = (replaces_attempt_id IS NULL)", name="replaces"),
        rules(
            f"""
            CREATE FUNCTION guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled')
                THEN RAISE EXCEPTION 'finished attempts are immutable'; END IF;
                IF (to_jsonb(NEW) - ARRAY[{_ATTEMPT_PROGRESS}])
                    IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[{_ATTEMPT_PROGRESS}])
                    OR (OLD.harness_run_id IS NOT NULL AND NEW.harness_run_id IS DISTINCT FROM OLD.harness_run_id)
                THEN RAISE EXCEPTION 'attempt identity is immutable'; END IF;
                IF OLD.status = 'running' AND NEW.status = 'leased'
                THEN RAISE EXCEPTION 'invalid attempt transition running to leased'; END IF;
                -- A lease that has expired stays expired, even before the sweep closes it.
                IF NEW.lease_expires_at > OLD.lease_expires_at AND OLD.lease_expires_at <= clock_timestamp()
                THEN RAISE EXCEPTION 'expired leases cannot be renewed'; END IF;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END $$
            """,
            trigger("run_attempts", "guard_attempt"),
            _CHECK_ATTEMPT_POINTER,
            *(
                f"CREATE CONSTRAINT TRIGGER check_attempt_pointer AFTER INSERT OR UPDATE ON {table}"
                " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_attempt_pointer()"
                for table in ("runs", "run_attempts")
            ),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    run_id: Mapped[str]
    number: Mapped[int]
    status: Mapped[str]
    start_reason: Mapped[str]
    replaces_attempt_id: Mapped[str | None]
    worker_id: Mapped[str]
    worker_build: Mapped[str]
    harness_run_id: Mapped[str | None]
    lease_token_hash: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yield_reason: Mapped[str | None]
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


_GUARD_USAGE = """
CREATE FUNCTION guard_usage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'usage cannot be deleted'; END IF;
    IF (to_jsonb(NEW) - '{record,digest}'::text[])
        IS DISTINCT FROM (to_jsonb(OLD) - '{record,digest}'::text[])
    THEN RAISE EXCEPTION 'usage attribution is immutable'; END IF;
    IF NEW.record->>'kind' IS DISTINCT FROM OLD.record->>'kind'
    THEN RAISE EXCEPTION 'usage kind is immutable'; END IF;
    IF OLD.record->>'kind' = 'snapshot' THEN
        IF (NEW.record->>'sequence')::bigint <= (OLD.record->>'sequence')::bigint
        THEN RAISE EXCEPTION 'usage snapshot sequence must advance'; END IF;
    ELSIF OLD.record->>'kind' != 'model' OR NOT EXISTS (
        SELECT 1 FROM usage_records scope
        WHERE scope.record->>'kind' = 'snapshot'
          AND scope.run_attempt_id = OLD.run_attempt_id
          AND scope.harness_run_id = OLD.harness_run_id
    ) THEN RAISE EXCEPTION 'legacy facts and provider receipts are immutable'; END IF;
    RETURN NEW;
END $$
"""


class UsageRecordRow(Base):
    """Current producer snapshots and contribution projections; attribution never changes."""

    __tablename__ = "usage_records"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["run_id", "run_attempt_id"], ["run_attempts.run_id", "run_attempts.id"]),
        Index("ix_usage_records_run", "run_id"),
        Index("ix_usage_records_workspace_ingested", "workspace_id", "ingested_at"),
        rules(_GUARD_USAGE, trigger("usage_records", "guard_usage", on="BEFORE UPDATE OR DELETE")),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    run_id: Mapped[str]
    run_attempt_id: Mapped[str]
    harness_run_id: Mapped[str]
    call_id: Mapped[str | None]
    digest: Mapped[str] = mapped_column(String(64))
    record: Mapped[dict] = mapped_column(JSONB)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("models.id"))
    price_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
