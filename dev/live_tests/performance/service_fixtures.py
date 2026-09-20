"""Native Service transitions against real PostgreSQL and S3, without a model loop."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

from a13n_service.agents.domain import PreparedAgentPlugins
from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.attempts import AttemptContext, AttemptExecutionService, AttemptLease
from a13n_service.interactions.control_domain import ThreadRunSubmissionIntent
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
from a13n_service.interactions.inbox_delivery import AdaptedThreadInboxEntry
from a13n_service.interactions.input import AcceptedAgentInput
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    HostContinuationState,
    InboxReceipt,
    RunCheckpoint,
)
from a13n_service.storage import short_session
from a13n_service.storage.relational import create_session_factory
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from ..infrastructure.client import agent_input
from .connection_preparation import warm_http_connections

logger = logging.getLogger(__name__)


async def prepare_many(create, count):
    """Prepare every caller concurrently; drain failures before closing clients."""
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(create()) for _ in range(count)]
    return [task.result() for task in tasks]


class NoWebhook:
    async def validate(self, endpoint, *, resolve_dns=True):
        raise AssertionError("The benchmark configures no webhook")


async def no_materialization(*args):
    raise AssertionError("Receipt confirmation must not materialize or execute input")


@dataclass
class PreparedRun:
    run: object
    thread: object
    authority: AttemptContext
    state: object
    queued_ids: tuple[str, ...] = ()


class ServiceFixture:
    def __init__(self, lab, engine, objects):
        sessions = create_session_factory(engine)
        self.lab, self.sessions = lab, sessions
        self.engine = engine
        self.preparations = []
        self.http_connections = 0
        self.organization = lab.config["organization_id"]
        self.states = RunStateStore(objects)
        lifecycle = LifecycleWriter((append_matching_a2a_push_outbox,))
        self.scheduler = AttemptScheduler(sessions, lifecycle=lifecycle)
        self.execution = AttemptExecutionService(sessions, lifecycle=lifecycle)
        self.outcomes = RunOutcomeService(sessions, RunPayloadStore(objects), lifecycle=lifecycle)
        self.inbox = ThreadInboxStore(sessions)  # This boundary ends at PG commit; no optional Redis wakeup.
        self.reconciler = DatabaseThreadInboxReconciler(sessions, no_materialization)
        self.queue = QueuedSubmissionStore(sessions, InlineHookValidator(NoWebhook()))

    async def trace_http(self, name, info):
        if name == "connection.connect_tcp.complete":
            self.http_connections += 1

    async def prepare_runs(self, count, *, running=False):
        evidence = {"requested": count, "kind": "running" if running else "accepted", "status": "incomplete"}
        self.preparations.append(evidence)
        started = perf_counter()
        before = self.http_connections
        created = 0

        async def create():
            nonlocal created
            result = await (self.running() if running else self.accepted())
            created += 1
            return result

        try:
            evidence["http_warmed_connections"] = await warm_http_connections(self.lab.client.http, count)
            prepared = await prepare_many(create, count)
            evidence["status"] = "verified"
            return prepared
        except BaseException:
            evidence["status"] = "failed"
            raise
        finally:
            evidence.update(
                created=created,
                http_new_connections=self.http_connections - before,
                elapsed_ms=(perf_counter() - started) * 1000,
            )
            logger.info("service_fixture_preparation %s", evidence)

    async def accepted(self):
        receipt = await self.lab.client.request(
            "POST",
            f"/api/v1/workspaces/{self.lab.config['workspace_id']}/runs",
            expected=202,
            headers={"Idempotency-Key": uuid4().hex},
            json={"agent_id": self.lab.config["child_agent_id"], "input": agent_input("operation fixture")},
            extensions={"trace": self.trace_http},
        )
        self.lab.client.track(receipt)
        return receipt["run_id"]

    def worker(self):
        return WorkerClaim(
            organization_id=self.organization,
            worker_id="operation-benchmark",
            worker_build_id="live-test",
            lease_duration=timedelta(seconds=300),
            handoff_preference_window=timedelta(seconds=30),
        )

    async def running(self):
        run_id = await self.accepted()
        claim = await self.scheduler.claim(run_id, self.worker())
        assert isinstance(claim, ClaimedAttempt)
        lease = timedelta(seconds=300)
        authority = AttemptContext(
            organization_id=self.organization,
            thread_id=claim.thread_id,
            run_id=run_id,
            run_attempt_id=claim.attempt.id,
            attempt_number=claim.attempt.attempt_number,
            lease_token=claim.lease_token,
            worker_id=claim.attempt.worker_id,
            worker_build_id=claim.attempt.worker_build_id,
            lease_duration=lease,
            lease=AttemptLease(claim.attempt.lease_expires_at),
            renewal_interval=lease / 3,
            renewal_timeout=lease / 6,
            reconciliation_timeout=timedelta(seconds=30),
            cleanup_timeout=timedelta(seconds=5),
        )
        async with short_session(self.sessions) as database:
            run = (await database.get(RunRecord, run_id)).to_resource()
        await authority.authorization.initialize(
            self.sessions,
            principal=run.authority_principal,
            organization_id=self.organization,
            workspace_id=self.lab.config["workspace_id"],
            root_agent_id=run.agent_id,
            agent_ids=frozenset(),
            run_id=run.id,
            run_attempt_id=authority.run_attempt_id,
            environment_id=run.environment_id,
        )
        preparation = await self.execution.commit_preparation_success(authority)
        await self.execution.enter_harness(authority, preparation=preparation, harness_run_id="operation-benchmark")
        state = await self.states.claim_writer(
            await self.states.read(self.organization, run_id), attempt_number=authority.attempt_number
        )
        state = await self.states.prepare_plugins(
            state, PreparedAgentPlugins(plugins=()), attempt_number=authority.attempt_number
        )
        progress = state.envelope.model_dump(mode="python", by_alias=True)
        progress.update(
            checkpoint_seq=1,
            checkpoint_kind="progress",
            last_checkpoint_run_attempt_id=authority.run_attempt_id,
            last_checkpoint_fence=authority.attempt_number,
            harness=state.envelope.harness.model_copy(
                update={"message_history": [ModelRequest(parts=[UserPromptPart("operation fixture")])]}
            ),
        )
        state = await self.execution.publish_checkpoint(
            authority, self.states, state, RunCheckpoint.model_validate(progress)
        )
        async with short_session(self.sessions) as database:
            run = (await database.get(RunRecord, run_id)).to_resource()
            thread = (await database.get(ThreadRecord, run.thread_id)).to_resource()
        assert run.status.value == "running"
        return PreparedRun(run, thread, authority, state)

    def checkpoint(self, prepared, *, size=1024, receipt=None):
        state = prepared.state.envelope
        values = state.model_dump(mode="python", by_alias=True)
        history = list(state.harness.message_history)
        if receipt is None:
            history.extend(
                [
                    ModelRequest(parts=[UserPromptPart("x" * size)]),
                    ModelResponse(parts=[TextPart("done")]),
                ]
            )
        else:
            delivery = AdaptedThreadInboxEntry(
                receipt.delivery_sequence,
                InboxReceipt(inbox_entry_id=receipt.steer_id, kind="steer"),
                "s" * 1024,
            )
            history.append(ModelRequest(parts=[UserPromptPart(delivery.tagged_input(prepared.run.id))]))
        values.update(
            checkpoint_seq=state.checkpoint_seq + 1,
            checkpoint_kind="completed" if receipt is None else "progress",
            last_checkpoint_run_attempt_id=prepared.authority.run_attempt_id,
            last_checkpoint_fence=prepared.authority.attempt_number,
            harness=state.harness.model_copy(update={"message_history": history}),
            host=HostContinuationState(
                inbox_receipts=() if receipt is None else (InboxReceipt(inbox_entry_id=receipt.steer_id, kind="steer"),)
            ),
            outcome_candidate=CompletedOutcomeCandidate(output="done", output_text="done") if receipt is None else None,
        )
        return RunCheckpoint.model_validate(values)

    async def append(self, prepared):
        return await self.inbox.append_steer(
            organization_id=self.organization,
            run_id=prepared.run.id,
            input=AcceptedAgentInput.model_validate(agent_input("s" * 1024)),
        )

    async def enqueue(self, prepared, *, store=None):
        return await (store or self.queue).enqueue(
            organization_id=self.organization,
            thread_id=prepared.thread.id,
            expected_thread_version=prepared.thread.version,
            authority_principal=prepared.run.authority_principal,
            submission=ThreadRunSubmissionIntent.model_validate({"input": agent_input("q" * 1024)}),
        )
