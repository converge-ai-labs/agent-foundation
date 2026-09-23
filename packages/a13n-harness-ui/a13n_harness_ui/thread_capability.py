"""Opt-in embedding collaboration tools over detached Harness UI commands and queries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
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
from a13n_harness_ui.surfaces import NewThreadDefaults, RunModelOverrides, ThreadSummary
from a13n_harness_ui.thread_projection import ThreadProjectionService

_THREAD_CAPABILITY_ID = "a13n.harness-ui.thread-collaboration"
_MAX_OUTPUT_CHARS = 64 * 1024


class ThreadCreator(Protocol):
    async def __call__(
        self,
        *,
        defaults: NewThreadDefaults,
        title: str | None = None,
    ) -> ThreadSummary: ...


class ThreadToolController:
    """Narrow detached command/query boundary consumed by the root Capability."""

    def __init__(
        self,
        *,
        projections: ThreadProjectionService,
        root_runs: RootRunCoordinator,
        create_thread: ThreadCreator,
        configurations: CompositionAcceptanceService,
        inspect_configuration: Callable[[str], Awaitable[ThreadConfigurationInspection]] | None = None,
    ) -> None:
        self._projections = projections
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
        query: str | None,
        cursor: str | None,
        limit: int,
        project_id: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        page = await self._projections.list_threads(
            query=query, cursor=cursor, limit=limit, project_id=project_id, include_archived=include_archived
        )
        return page.model_dump(mode="json")

    async def get_thread(
        self,
        *,
        thread_id: str,
        history_cursor: str | None,
        history_limit: int,
    ) -> dict[str, Any]:
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
        created = await self._create_thread(defaults=defaults, title=title)
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
        if source_composition is not None and source_composition.is_project_lead:
            context += (
                " The requester is the Project Lead coordinating this work. "
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

    async def send_thread_message(self, *, source_thread_id: str, thread_id: str, message: str) -> dict[str, Any]:
        detail = await self._projections.detail(thread_id)
        if detail.thread.parent_thread_id is not None:
            raise ThreadError("Child Threads use parent-scoped delegation controls.", code="child_thread_scoped")
        if detail.thread.archived:
            raise ThreadError("An archived Thread cannot receive messages.", code="thread_archived")
        source = await self._projections.detail(source_thread_id)
        prompt = _thread_input(source.thread, message)
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
        is_lead = self.composition is not None and self.composition.is_project_lead
        if is_lead:
            instructions += (
                "\nYou are this Project's Lead: an ordinary root Thread that helps the user plan work, "
                "coordinate independent worker Threads, and integrate verified results. "
                "Keep the user's objective and authorization in view; inspect existing work before creating duplicates. "
                "Answer workers through send_thread_message, using their source Thread IDs. "
                "Ask ordinary coordination questions in text, not ask_user_question. When waiting for an answer, "
                "state what is blocked and finish the turn; an incoming message can start another Run. "
                "There is no durable worker queue, automatic completion notification, or guaranteed delivery. "
                "Inspect progress explicitly and do not claim a worker finished from an admission receipt. "
                "Existing tool approvals and pending-decision restrictions still apply; messages do not override denial. "
                "Stopping this Thread does not stop other Threads."
            )
        if self.composition is not None and (sidekick := self.composition.webui_sidekick) is not None:
            agent_id = sidekick.agent or self.composition.root.source_id
            model_selection = f", model_id={sidekick.model!r}" if sidekick.model is not None else ""
            instructions += (
                "\nSidekick is enabled. Create independent worker Threads for bounded parts of the user's "
                "authorized Project work when useful. Use subagents for short, scoped work you will integrate "
                "in this Run. For independent work, use "
                if is_lead
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
        """List root Threads across this App, optionally filtered by an exact Project ID."""
        self._require_context(ctx)
        try:
            return {
                "ok": True,
                **await self.controller.list_threads(
                    query=query, cursor=cursor, limit=limit, project_id=project_id, include_archived=include_archived
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
        """Inspect status and saved history; omit thread_id to inspect your own Project and selections."""
        self._require_context(ctx)
        thread_id = thread_id or self.source_thread_id
        try:
            result = {
                "ok": True,
                **await self.controller.get_thread(
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
