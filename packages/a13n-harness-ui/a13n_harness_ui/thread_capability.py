"""Opt-in embedding collaboration tools over detached Harness UI commands and queries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.input import RunInputValue
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_logging import get_logger
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import TextContent
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness_ui.composition import CompositionAcceptanceService, ResolvedRunComposition
from a13n_harness_ui.configuration.discovery import ResourceKind, resource_page
from a13n_harness_ui.configuration_inspection import ThreadConfigurationInspection
from a13n_harness_ui.errors import ConfigurationError, HarnessUiError, ThreadError
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage.repositories import ThreadRepository
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationView, RunModelOverrides, ThreadSummary
from a13n_harness_ui.thread_projection import ThreadProjectionService

_THREAD_CAPABILITY_ID = "a13n.harness-ui.thread-collaboration"
_MAX_OUTPUT_CHARS = 64 * 1024


class ThreadCreator(Protocol):
    async def __call__(
        self,
        *,
        defaults: NewThreadDefaults,
        title: str | None = None,
        coordinator_thread_id: str | None = None,
    ) -> ThreadSummary: ...


class ThreadToolController:
    """Narrow detached command/query boundary consumed by the root Capability."""

    def __init__(
        self,
        *,
        projections: ThreadProjectionService,
        threads: ThreadRepository,
        root_runs: RootRunCoordinator,
        create_thread: ThreadCreator,
        configurations: CompositionAcceptanceService,
        inspect_configuration: Callable[[str], Awaitable[ThreadConfigurationInspection]] | None = None,
    ) -> None:
        self._projections = projections
        self._threads = threads
        self._root_runs = root_runs
        self._create_thread = create_thread
        self._configurations = configurations
        self._inspect_configuration = inspect_configuration

    async def resources(
        self, *, kind: ResourceKind, query: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        source = await self._configurations.current()
        if source is None:
            raise ConfigurationError("No configuration has been accepted.", code="configuration_missing")
        return resource_page(source, kind=kind, query=query, cursor=cursor, limit=limit)

    async def get_project(self, project_id: str) -> dict[str, Any]:
        source = await self._configurations.current()
        project = None if source is None else source.projects.get(project_id)
        if source is None or project is None:
            raise ThreadError("The selected Project is unavailable.", code="project_missing")
        return {
            "generation_digest": source.source_digest,
            "project": {
                "id": project.id,
                "name": project.name,
                "roots": [root.path for root in project.roots],
                "defaults": project.defaults.model_dump(mode="json", include=set(type(project.defaults).model_fields)),
            },
        }

    async def list_threads(
        self,
        *,
        source_thread_id: str,
        query: str | None,
        cursor: str | None,
        limit: int,
        project_id: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        source = await self._projections.get_thread(source_thread_id)
        owner = source.coordinator_thread_id
        coordinator_id = source_thread_id if source.role == "coordinator" else None
        page = await self._projections.list_threads(
            query=query,
            cursor=cursor,
            limit=limit,
            project_id=project_id,
            include_archived=include_archived,
            coordinator_thread_id=coordinator_id,
            independent_only=source.role == "ordinary",
            visible_thread_ids=(source_thread_id, owner) if owner is not None else None,
        )
        return page.model_dump(mode="json")

    async def get_thread(
        self,
        *,
        source_thread_id: str,
        thread_id: str,
        history_cursor: str | None,
        history_limit: int,
    ) -> dict[str, Any]:
        await self._require_target(source_thread_id, thread_id)
        detail = await self._projections.detail(thread_id)
        transcript = await self._projections.transcript(
            thread_id=thread_id,
            cursor=history_cursor,
            limit=history_limit,
        )
        result = {
            "thread": detail.model_dump(mode="json"),
            "transcript": transcript.model_dump(mode="json"),
        }
        if self._inspect_configuration is not None:
            inspection = await self._inspect_configuration(thread_id)
            result["configuration"] = inspection.model_dump(mode="json")
        return result

    async def run_thread(
        self, *, source_thread_id: str, thread_id: str, prompt: str, model_id: str | None = None
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ThreadError("A non-empty prompt is required.", code="thread_prompt_empty")
        await self._require_target(source_thread_id, thread_id, control=True)
        source = await self._projections.detail(source_thread_id)
        receipt = await self._root_runs.submit_prompt(
            thread_id=thread_id,
            prompt=_thread_input(source.thread, prompt),
            model_overrides=RunModelOverrides(model_id=model_id) if model_id is not None else None,
            touch=True,
        )
        return receipt.model_dump(mode="json")

    async def create_thread(
        self,
        *,
        source_thread_id: str,
        prompt: str,
        title: str | None,
        agent_id: str | None,
        project_id: str | None = "current",
        model_id: str | None = None,
        source_composition: ResolvedRunComposition | None = None,
    ) -> dict[str, Any]:
        model_overrides = RunModelOverrides(model_id=model_id) if model_id is not None else None
        source = await self._projections.detail(source_thread_id)
        configuration = source.thread.configuration
        is_coordinator = await self._threads.is_coordinator(source_thread_id)
        if source.thread.coordinator_thread_id is not None:
            raise ThreadError(
                "Ask your Coordinator to create another worker; use subagents for bounded work within your task.",
                code="coordinator_worker_creation_scoped",
            )
        if is_coordinator and project_id not in ("current", configuration.project_id):
            raise ThreadError(
                "A Coordinator creates workers only in its own Project.", code="coordinator_worker_project_locked"
            )
        sidekick = source_composition.webui_sidekick if source_composition is not None else None
        if sidekick is not None and agent_id is None:
            assert source_composition is not None
            agent_id = sidekick.agent
            if agent_id is None and (
                project_id != "current" or configuration.agent_source.id != source_composition.root.source_id
            ):
                # Inherit the calling Run's Agent, not a concurrently edited Thread selection.
                agent_id = source_composition.root.source_id
        default_model_id = None if sidekick is None else sidekick.model
        if project_id == "current" and agent_id is None:
            defaults = NewThreadDefaults(
                project_id=configuration.project_id,
                agent_id=configuration.agent_source.id,
                default_model_id=(default_model_id if sidekick is not None else configuration.default_model_id),
                local_roots=configuration.local_roots,
                environment_profile_id=configuration.environment_profile_id,
                environment_bindings=configuration.environment_bindings,
                default_environment=configuration.default_environment,
                harness_plugin_ids=configuration.harness_plugin_ids,
                environment_run_extension_ids=configuration.environment_run_extension_ids,
                mcp_server_ids=configuration.mcp_server_ids,
            )
        else:
            # Explicit selections use normal Project/Agent defaults, not the source's MCP or tool selection.
            defaults = NewThreadDefaults(
                project_id=configuration.project_id if project_id == "current" else project_id,
                agent_id=agent_id,
                default_model_id=default_model_id,
            )
        created = await self._create_thread(
            defaults=defaults, title=title, coordinator_thread_id=source_thread_id if is_coordinator else None
        )
        requester_project = (
            source_composition.project_id if source_composition is not None else configuration.project_id
        )
        context = (
            f"Task from Thread {source_thread_id}. Requesting Project: {requester_project or 'No Project'}.\n"
            "This is an independent root Thread, not a delegated child. "
            f"For clarification, decisions or blockers, use send_thread_message(thread_id={source_thread_id!r}, "
            "message=...) to ask the requester. The requester can reply with the same tool targeting your Thread. "
            "When finished, use that tool to report your findings, changes, validation and remaining issues to the requester. "
            "Sending a message does not wait for an answer; do not invent a reply. "
            "Do not send acknowledgement-only replies or delegate the same task back to its requester."
        )
        if is_coordinator:
            context += (
                " The requester is the Coordinator coordinating this work. "
                "Use ordinary send_thread_message messages for coordination questions, not ask_user_question. "
                "If its answer is required, explain the blocker and finish this turn; its reply can start another Run. "
                "Existing tool approvals still apply; ordinary messages cannot grant a denied approval."
            )
        try:
            receipt = await self._root_runs.submit_prompt(
                thread_id=created.thread_id,
                prompt=_thread_input(source.thread, prompt, context=context),
                model_overrides=model_overrides,
                touch=True,
            )
        except HarnessUiError as exc:
            # Creation and run admission are separate durable effects. Never hide the created identity.
            return {**_failure(exc, "thread_run_failed"), "thread_id": created.thread_id}
        return {"ok": True, "thread_id": created.thread_id, "receipt": receipt.model_dump(mode="json")}

    async def notify_coordinator(self, project_id: str | None, operation: RootOperationView) -> None:
        """Notify only the explicit owner of a managed worker, without retries or a queue."""
        owner_id = (await self._threads.worker_owners((operation.receipt.thread_id,))).get(operation.receipt.thread_id)
        if owner_id is None:
            return
        coordinator = (await self._threads.coordinators((owner_id,))).get(owner_id)
        if coordinator is None or not coordinator.auto_followup:
            return
        message = (
            f"Host notification: Thread {operation.receipt.thread_id} ended a root operation "
            f"in Project {project_id}. Receipt: {operation.receipt.receipt_id}. "
            f"Run: {operation.run_id or 'not started'}. Status: {operation.status.value}. "
            "Use get_thread to inspect its saved results and reconcile your tasks and notes. "
            "This lifecycle notice is separate from any worker report; an ended operation does not prove "
            "the task succeeded. Do not send an acknowledgement or repeat an already integrated report."
        )
        result = await self._run_or_steer(
            thread_id=owner_id,
            prompt=[TextContent(message, metadata={"display": False})],
        )
        if not result["ok"]:
            get_logger(__name__).info(
                "Coordinator did not accept terminal notification: %s", operation.receipt.receipt_id
            )

    async def _require_target(self, source_thread_id: str, thread_id: str, *, control: bool = False) -> None:
        if source_thread_id == thread_id:
            return
        target = await self._threads.get(thread_id)
        if target is None or target.parent_thread_id is not None:
            raise ThreadError("This Thread is not accessible from your conversation.", code="thread_access_denied")
        owners = await self._threads.worker_owners((source_thread_id, thread_id))
        source_owner, target_owner = owners.get(source_thread_id), owners.get(thread_id)
        if source_owner is not None:
            allowed = thread_id == source_owner and not control
        elif await self._threads.is_coordinator(source_thread_id):
            allowed = target_owner == source_thread_id
        else:
            allowed = target_owner is None
        if not allowed:
            raise ThreadError("This Thread is not accessible from your conversation.", code="thread_access_denied")

    async def send_thread_message(self, *, source_thread_id: str, thread_id: str, message: str) -> dict[str, Any]:
        await self._require_target(source_thread_id, thread_id)
        source = await self._projections.detail(source_thread_id)
        return await self._run_or_steer(thread_id=thread_id, prompt=_thread_input(source.thread, message))

    async def _run_or_steer(self, *, thread_id: str, prompt: RunInputValue) -> dict[str, Any]:
        detail = await self._projections.detail(thread_id)
        if detail.thread.parent_thread_id is not None:
            raise ThreadError("Child Threads use parent-scoped delegation controls.", code="child_thread_scoped")
        if detail.thread.archived:
            raise ThreadError("An archived Thread cannot receive messages.", code="thread_archived")
        operation = await self._root_runs.active(thread_id)
        if operation is not None:
            # Resolve once. A rejected steer never falls through into a different operation.
            result = await self._root_runs.steer(receipt_id=operation.receipt.receipt_id, message=prompt)
            return {"ok": result.accepted, "mode": "steer", **result.model_dump(mode="json")}
        if detail.deferred_requests:
            raise ThreadError(
                "Resolve this Thread's pending decisions before sending a message.", code="thread_deferred_pending"
            )
        # Admission serializes concurrent sends. If another operation wins, report its conflict without retrying.
        receipt = await self._root_runs.submit_prompt(thread_id=thread_id, prompt=prompt)
        return {"ok": True, "mode": "run", "receipt": receipt.model_dump(mode="json")}

    async def steer_thread(self, *, source_thread_id: str, thread_id: str, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ThreadError("A non-empty message is required.", code="thread_message_empty")
        await self._require_target(source_thread_id, thread_id, control=True)
        operation = await self._root_runs.active(thread_id)
        if operation is None:
            return {"accepted": False, "receipt_id": None, "enqueue_id": None}
        source = await self._projections.detail(source_thread_id)
        result = await self._root_runs.steer(
            receipt_id=operation.receipt.receipt_id,
            message=_thread_input(source.thread, message),
        )
        return result.model_dump(mode="json")


@dataclass(kw_only=True, slots=True)
class ThreadCollaborationCapability(AbstractCapability[AgentContext]):
    """Expose bounded cross-Thread operations only to one root invocation."""

    controller: ThreadToolController
    source_thread_id: str
    composition: ResolvedRunComposition | None = None
    id: str | None = _THREAD_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != _THREAD_CAPABILITY_ID:
            raise ValueError(f"ThreadCollaborationCapability.id must be {_THREAD_CAPABILITY_ID!r}")
        if not self.source_thread_id:
            raise ValueError("source_thread_id must not be blank")

    def get_instructions(self) -> str:
        identity = f"Your current Thread: {self.source_thread_id}. "
        if self.composition is not None:
            identity += (
                f"Your captured Project: {self.composition.project_id or 'No Project'}. "
                f"Captured local roots: {list(self.composition.project_roots)!r}. "
                "These are this Run's captured selections; no discovery call is needed to identify yourself.\n"
            )
        instructions = identity + (
            "Use get_thread() to inspect saved history and status; current_run describes the captured "
            "configuration, while thread.configuration describes next-Run selections. "
            "The configuration inspection exposes the target's next_model_id and captured Model separately. "
            "list_projects, list_agents and list_models discover accepted resources, not proven model connectivity. "
            "Cross-Thread work creates independent root conversations, not delegated child executions. "
            "create_thread and run_thread return admission receipts, not completed work. Inspect progress with get_thread. "
            "Use send_thread_message(thread_id=..., message=...) to ask another root a question, reply to it, "
            "or report results: it steers an active operation or starts an idle Thread. Incoming tasks and messages "
            "identify the source Thread; target that ID to answer. Sending does not wait for a reply. "
            "A positive result means acceptance only, not processing or saved delivery. A rejected or uncertain send "
            "must be reconciled, not blindly retried. Do not create acknowledgement loops or delegate a task back to its requester."
        )
        is_coordinator = self.composition is not None and self.composition.role == "coordinator"
        if is_coordinator:
            instructions += (
                "\nYou are a Coordinator in this Project: an ordinary root Thread that helps the user plan work, "
                "coordinate your explicitly owned worker Threads, and integrate verified results. "
                "Create independent worker Threads for bounded authorized work when useful. "
                "Project membership is not worker ownership. Your Thread tools are scoped to yourself and your "
                "workers, including while automatic coordination is disabled. Old conversations and ordinary "
                "Sidekicks remain independent; never adopt, steer, or continue them. "
                "create_thread atomically creates an owned root in your Project; do not supply another Project. "
                "Workers remain directly accessible to the user. Reconcile any user changes before assigning more work. "
                "Keep the user's objective and authorization in view. At the start of each Run, recover relevant "
                "open work from the continuation summary and projected tasks and notes; use note_get for omitted "
                "notes when available. Before planning new work or reporting progress, use get_thread(thread_id=...) "
                "to check the known workers' current status and saved results. Use list_threads to rediscover "
                "your durable worker set; it cannot list external conversations. Reuse existing workers rather "
                "than creating duplicates. An inactive worker "
                "is not necessarily successful: inspect its outcome, blockers, and validation before marking work done. "
                "When notes tools are available, maintain a compact coordination note with each delegated objective, "
                "worker Thread ID, last verified status, blocker, and next action. Treat it as an index, not live truth; "
                "refresh stale observations with get_thread. Use task tools when available to track meaningful "
                "deliverables and reconcile their status after verification, without copying whole worker transcripts. "
                "Before summarize, reconcile tasks and notes. Use the summary for the user's objective, decisions, "
                "verified outcomes, unresolved work, and immediate next step; do not duplicate the separately "
                "projected notes and tasks. Never assume an unsaved plan will survive a handoff. "
                "Answer workers through send_thread_message, using their source Thread IDs. "
                "Ask ordinary coordination questions in text, not ask_user_question. When waiting for an answer, "
                "state what is blocked and finish the turn; an incoming message can start another Run. "
                "While auto-follow-up is enabled, the Host attempts to run or steer this Thread "
                "when one of your owned workers ends an operation, including failure, cancellation "
                "or suspension. These lifecycle notices are separate from worker reports. On receipt, inspect "
                "the worker's saved results and reconcile tasks and notes; do not acknowledge the notice or "
                "repeat an already integrated report. There is no background polling, durable notification queue, "
                "retry or guaranteed delivery. Check progress at meaningful points while executing. "
                "If only waiting remains, record the pending work and end the turn. A Host notification, "
                "worker report or new user message may start another Run; do not promise a guaranteed wake-up. "
                "Do not claim a worker finished from an admission receipt. "
                "Existing tool approvals and pending-decision restrictions still apply; messages do not override denial. "
                "Stopping this Thread does not stop other Threads."
            )
        if self.composition is not None and self.composition.coordinator_thread_id is not None:
            instructions += (
                f"\nYou are a managed worker of Coordinator {self.composition.coordinator_thread_id}. "
                "This durable ownership is distinct from subagent execution: you remain an independent root "
                "conversation with your own history, configuration and user interaction. Work within the assigned "
                "objective and authorization; direct user messages do not change your owner. "
                f"Use send_thread_message(thread_id={self.composition.coordinator_thread_id!r}, message=...) "
                "for coordination questions, blockers and verified results. Use get_thread() to inspect your "
                "own saved outcome; coordinator_thread_id remains your return address after a restart or handoff. "
                "Your Thread tools can read only yourself and your Coordinator. Communicate with your Coordinator "
                "using send_thread_message; you cannot run or steer it directly, or access sibling workers. "
                "For ordinary coordination, do not use ask_user_question: ask the Coordinator and finish the turn "
                "if its answer is required. Never pretend a message was processed merely because accepted. "
                "Existing human approvals still apply and cannot be granted by a Coordinator message. "
                "Do not create another root or pass the same task back. Ask the Coordinator to split independent work; "
                "use declared subagents for bounded work you integrate yourself. "
                "Report substantive findings, changes, validation and remaining issues once; do not send "
                "acknowledgement-only replies. The Host separately attempts an end-of-operation notice to "
                "your Coordinator when coordination is enabled. That notice is not your report and neither path "
                "guarantees processing or a wake-up. Do not poll or repeatedly send the same report."
            )
        if (
            self.composition is not None
            and self.composition.coordinator_thread_id is None
            and (sidekick := self.composition.webui_sidekick) is not None
        ):
            agent_id = sidekick.agent or self.composition.root.source_id
            model_selection = f", model_id={sidekick.model!r}" if sidekick.model is not None else ""
            instructions += (
                "\nSidekick is enabled. Create independent worker Threads for bounded parts of the user's "
                "authorized Project work when useful. Use subagents for short, scoped work you will integrate "
                "in this Run. For independent work, use "
                if is_coordinator
                else "\nSidekick is enabled. Use subagents for parallel research, exploration, and other bounded tasks "
                "whose results you will integrate into the current conversation. If no suitable subagent is available, "
                "keep that work in the current Thread rather than creating a Sidekick as a fallback. "
                "Create a separate Thread only for coordination work that needs human attention, decisions, or "
                "follow-up in its own conversation. For that work, use "
            )
            instructions += (
                "create_thread(prompt=...). The Host applies the captured Sidekick defaults "
                f"(Agent {agent_id!r}{model_selection}) when arguments are omitted. "
                "Its configured Model becomes the new Thread's default for later turns. "
                "Explicit agent_id selects another Agent; explicit model_id overrides only the first Run. "
                "Give a bounded task and necessary context; inspect "
                "results before integrating them. Other configured Agents and Models remain selectable. "
                "The created task identifies your Thread and Project and tells the worker how to ask you questions "
                "and report back through send_thread_message. Answer its questions through that same tool. "
                "If you are already executing another Thread's task, ask for clarification as needed, complete it and report to the requester "
                "rather than creating another Sidekick for the same task. Do not create work merely because enabled."
            )
        return instructions

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                _tool(self.list_threads, name="list_threads", effects={"read"}),
                _tool(self.get_thread, name="get_thread", effects={"read"}),
                _tool(self.list_projects, name="list_projects", effects={"read"}),
                _tool(self.get_project, name="get_project", effects={"read"}),
                _tool(self.list_agents, name="list_agents", effects={"read"}),
                _tool(self.list_models, name="list_models", effects={"read"}),
                _tool(
                    self.send_thread_message,
                    name="send_thread_message",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
                _tool(
                    self.create_thread,
                    name="create_thread",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
                _tool(
                    self.run_thread,
                    name="run_thread",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
                _tool(
                    self.steer_thread,
                    name="steer_thread",
                    effects={"write"},
                    idempotency="none",
                ),
            ],
            id="a13n-a13n-harness-ui-thread-tools",
        )

    async def list_threads(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
        project_id: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List root conversations; for a Coordinator, list only its owned workers.

        Optional Project/search/archive filters narrow this scope, never expand it. Older
        independent Threads are not workers. Use get_thread() without an ID to inspect yourself.
        """
        self._require_context(ctx)
        try:
            return {
                "ok": True,
                **await self.controller.list_threads(
                    source_thread_id=self.source_thread_id,
                    query=query,
                    cursor=cursor,
                    limit=limit,
                    project_id=project_id,
                    include_archived=include_archived,
                ),
            }
        except (HarnessUiError, ValueError) as exc:
            return _failure(exc, "thread_list_failed")

    async def get_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str | None = None,
        history_cursor: str | None = None,
        history_limit: int = Field(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        """Inspect saved history and current status; omit thread_id to inspect yourself.

        Coordinators can inspect only themselves and their owned workers. Workers can inspect themselves and their Coordinator; unrelated roots cannot inspect workers. The returned
        coordinator_thread_id identifies worker ownership, not subagent execution lineage.
        Inactive status is not proof that a task succeeded; read the saved outcome.
        """
        self._require_context(ctx)
        thread_id = thread_id or self.source_thread_id
        try:
            result = {
                "ok": True,
                **await self.controller.get_thread(
                    source_thread_id=self.source_thread_id,
                    thread_id=thread_id,
                    history_cursor=history_cursor,
                    history_limit=history_limit,
                ),
            }
            if thread_id == self.source_thread_id and self.composition is not None:
                result["current_run"] = {
                    "project_id": self.composition.project_id,
                    "project_roots": list(self.composition.project_roots),
                    "agent_id": self.composition.root.source_id,
                    "model_id": self.composition.root.model.model_id,
                    "role": self.composition.role,
                    "coordinator_thread_id": self.composition.coordinator_thread_id,
                    "generation_digest": self.composition.generation_digest,
                }
            return result
        except (HarnessUiError, ValueError) as exc:
            return _failure(exc, "thread_get_failed")

    async def run_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        prompt: str,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Start another idle Thread; optionally override its Model for this Run without changing its Agent."""
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code(
                "thread_recursive_run",
                "A Thread cannot recursively run itself from its active root invocation.",
            )
        try:
            receipt = await self.controller.run_thread(
                source_thread_id=self.source_thread_id, thread_id=thread_id, prompt=prompt, model_id=model_id
            )
            return {"ok": True, "receipt": receipt}
        except (HarnessUiError, ValueError) as exc:
            return _failure(exc, "thread_run_failed")

    async def create_thread(
        self,
        ctx: RunContext[AgentContext],
        prompt: str,
        title: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = "current",
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Create independent work with a return address and return immediately after run admission.

        project_id='current' keeps this Project; null selects no Project; an ID selects another Project.
        Enabled Sidekick defaults are applied by the Host: omitted agent_id uses its Agent (or inherits
        the caller); its configured Model initializes the new Thread's persistent default_model_id.
        Explicit agent_id wins. model_id overrides the first Run only, never the saved default.
        Without Sidekick, unchanged Project/Agent inherits this Thread's selections; explicit selections
        use normal Project/Agent defaults. Discover IDs using list_projects, list_agents and list_models.

        For a Coordinator, creation atomically assigns the new root to that Coordinator and must
        stay in its Project. Ordinary Sidekicks remain independent and receive no automatic
        lifecycle notification. Managed workers cannot create more roots: ask their Coordinator
        for another worker or use a declared subagent for bounded work.

        The returned receipt is not completion. Use get_thread to inspect progress.
        If admission fails after creation, the returned thread_id remains valid; do not create a duplicate.
        """
        self._require_context(ctx)
        if not prompt.strip():
            return _failure_code("thread_prompt_empty", "A non-empty initial prompt is required.")
        try:
            return await self.controller.create_thread(
                source_thread_id=self.source_thread_id,
                prompt=prompt,
                title=title,
                agent_id=agent_id,
                project_id=project_id,
                model_id=model_id,
                source_composition=self.composition,
            )
        except (HarnessUiError, ValueError) as exc:
            return _failure(exc, "thread_create_failed")

    async def steer_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        message: str,
    ) -> dict[str, Any]:
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code(
                "thread_recursive_steer",
                "A Thread cannot steer itself from its active root invocation.",
            )
        try:
            result = await self.controller.steer_thread(
                source_thread_id=self.source_thread_id, thread_id=thread_id, message=message
            )
            return {"ok": result["accepted"], **result}
        except HarnessUiError as exc:
            return _failure(exc, "thread_steer_failed")

    async def list_projects(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Discover configured Projects by name or ID. Use get_project for roots and creation defaults."""
        self._require_context(ctx)
        return await self._resources(kind="projects", query=query, cursor=cursor, limit=limit)

    async def get_project(self, ctx: RunContext[AgentContext], project_id: str) -> dict[str, Any]:
        """Inspect a configured Project's roots and creation defaults, without changing your Environment."""
        self._require_context(ctx)
        try:
            return {"ok": True, **await self.controller.get_project(project_id)}
        except HarnessUiError as exc:
            return _failure(exc, "project_get_failed")

    async def list_agents(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Discover configured root Agents, their Model IDs and selected Capability IDs, not child roster names."""
        self._require_context(ctx)
        return await self._resources(kind="agents", query=query, cursor=cursor, limit=limit)

    async def list_models(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Discover configured Model IDs, names and routes, without credentials or a connectivity check."""
        self._require_context(ctx)
        return await self._resources(kind="models", query=query, cursor=cursor, limit=limit)

    async def _resources(
        self, *, kind: ResourceKind, query: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        try:
            return {"ok": True, **await self.controller.resources(kind=kind, query=query, cursor=cursor, limit=limit)}
        except HarnessUiError as exc:
            return _failure(exc, "resource_list_failed")

    async def send_thread_message(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Report to another root with source attribution; steer if active or start a new turn if idle.

        Returns acceptance, not processing or saved delivery. No offline queue, automatic retry or
        fallback to a replacement operation. Inspect a rejected/uncertain send before submitting again.
        """
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code("thread_recursive_message", "A Thread cannot send itself a message during its Run.")
        if not message.strip():
            return _failure_code("thread_message_empty", "A non-empty message is required.")
        try:
            return await self.controller.send_thread_message(
                source_thread_id=self.source_thread_id, thread_id=thread_id, message=message
            )
        except HarnessUiError as exc:
            return _failure(exc, "thread_message_failed")

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps.thread_id != self.source_thread_id:
            raise DefinitionError(
                "Harness UI Thread tools cannot cross root Run scope.",
                code="capability_scope_invalid",
            )


def _thread_input(source: ThreadSummary, text: str, *, context: str | None = None) -> tuple[TextContent, TextContent]:
    """Keep model context separate from the attributed, surface-visible message."""
    return (
        TextContent(context or f"Message from Thread {source.thread_id}:", metadata={"display": False}),
        TextContent(
            text,
            metadata={
                "harness_ui": {
                    "thread_message": {
                        "source_thread_id": source.thread_id,
                        "source_thread_title": source.title or source.excerpt.first_input or None,
                    }
                }
            },
        ),
    )


def _tool(
    function: Any,
    *,
    name: str,
    effects: set[str],
    idempotency: Literal["read_only", "none"] = "read_only",
) -> HarnessTool:
    return HarnessTool(
        function,
        harness_metadata=HarnessToolMetadata(
            tool_id=f"a13n-harness-ui.thread.{name}",
            effects=cast(Any, frozenset(effects)),
            credential_audiences=(),
            idempotency=idempotency,
            output_policy=ToolOutputPolicy(
                max_inline_bytes=_MAX_OUTPUT_CHARS,
                max_output_bytes=_MAX_OUTPUT_CHARS,
                overflow="truncate",
                redact=True,
            ),
        ),
        name=name,
    )


def _bounded(value: str) -> str:
    if len(value) <= _MAX_OUTPUT_CHARS:
        return value
    return value[: _MAX_OUTPUT_CHARS - 23] + "\n...[output truncated]"


def _failure(exc: Exception, fallback_code: str) -> dict[str, Any]:
    if isinstance(exc, HarnessUiError):
        return _failure_code(exc.code, str(exc))
    return _failure_code(fallback_code, str(exc))


def _failure_code(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": _bounded(message)}}


__all__ = ["ThreadCollaborationCapability", "ThreadToolController"]
