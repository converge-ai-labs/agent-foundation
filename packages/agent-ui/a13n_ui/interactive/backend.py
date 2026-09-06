"""Application adapter for one CLI session; AgentUiApp owns execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

import yaml
from a13n_harness.input import RunInputValue

from a13n_ui.app import AgentUiApp
from a13n_ui.cli import CliRequest
from a13n_ui.configuration import ExternalSubagentImportPreview, ResourceMutationRequest
from a13n_ui.configuration.setup import SetupPreview, SetupSelection
from a13n_ui.environment_profiles import environment_profile_id_for_mode
from a13n_ui.errors import AgentUiError, ConfigurationError
from a13n_ui.live import LiveEvent, root_context_samples
from a13n_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_ui.surfaces import (
    ApprovalDecision,
    ExternalToolResult,
    NewThreadDefaults,
    RootOperationStatus,
    RunModelOverrides,
    ThreadDeferredResponse,
)

from .decisions import DecisionInteraction
from .rendering import Status, StreamRenderer
from .selection import Choice
from .setup import SetupWizard


class SessionBackend:
    def __init__(self, app: AgentUiApp, request: CliRequest, directory: Path, status: Status) -> None:
        if request.thread_id is not None and any(
            value is not None
            for value in (
                request.agent_id,
                request.environment_mode,
                request.environment_profile_id,
                request.title,
            )
        ):
            raise ValueError("Resume cannot be combined with Agent, Environment, or title overrides.")
        self.app = app
        self.request = request
        self.directory = directory
        self.status = status
        self.thread_id = request.thread_id
        self.overrides = RunModelOverrides()
        self.environment = request.environment_profile_id or (
            environment_profile_id_for_mode(request.environment_mode) if request.environment_mode else None
        )
        self.receipt_id: str | None = None
        self.cancel_requested = False
        self._decisions: dict[str, ApprovalDecision | ExternalToolResult] = {}
        self._decision_continuation: str | None = None
        self.preview: SetupPreview | None = None
        self.import_preview: ExternalSubagentImportPreview | None = None

    async def initialize(self) -> bool:
        configuration = await self.app.current_configuration()
        if configuration is not None:
            display = configuration.document.display
            if not self.status.theme_explicit:
                self.status.theme = display.theme
            if not self.status.mode_explicit:
                self.status.mode = display.mode
            self.status.show_status = display.show_status
            self.status.max_tool_result_lines = display.max_tool_result_lines
            self.status.max_tool_argument_chars = display.max_tool_argument_chars
        if self.thread_id is not None:
            await self.resume(self.thread_id)
        return await self.refresh()

    async def refresh(self) -> bool:
        configuration = await self.app.current_configuration()
        if configuration is None:
            return False
        agent_id = self.request.agent_id or configuration.document.defaults.agent
        if self.thread_id is not None:
            thread = (await self.app.get_thread(self.thread_id)).thread
            agent_id = thread.configuration.agent_source.id
            self.status.session_id = self.thread_id
            self.environment = thread.configuration.environment_profile_id
        agent = configuration.agents.get(agent_id or "")
        model_id = self.overrides.model_id or (None if agent is None else agent.model)
        model = configuration.models.get(model_id or "")
        self.status.environment = (
            self.environment or configuration.document.defaults.environment_profile or "environment-native"
        )
        if model is None:
            self.status.model = "not configured"
            self.status.thinking = "default"
            self.status.context_window = None
            return False
        self.status.model = model.route
        self.status.thinking = str(
            self.overrides.thinking
            if self.overrides.thinking is not None
            else model.settings.get("thinking", "default")
        )
        self.status.context_window = (
            None if model.model_characteristics is None else model.model_characteristics.context_window
        )
        return True

    async def models(self, selected: str | None = None) -> str:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Use /setup first.")
        if selected is None:
            return (
                "\n".join(f"{item.id}: {item.route}" for item in configuration.models.values())
                or "No models. Use /setup."
            )
        if selected == "default":
            selected = None
        elif selected not in configuration.models:
            raise ValueError("Unknown model ID. Use /model to list configured choices.")
        self.overrides = RunModelOverrides(model_id=selected, thinking=None)
        self.status.context_tokens = None
        await self.refresh()
        return f"Model: {self.status.model}. Reasoning reset to this model's configured default; applies next turn."

    async def thinking(self, selected: str | None) -> str:
        if selected is not None:
            self.overrides = RunModelOverrides.model_validate(
                {"model_id": self.overrides.model_id, "thinking": None if selected == "default" else selected}
            )
            await self.refresh()
        return f"Reasoning: {self.status.thinking}. Display detail is controlled separately by /mode."

    async def set_environment(self, selected: str | None) -> str:
        if selected is not None:
            profile = environment_profile_id_for_mode(selected)
            if self.thread_id is not None:
                detail = await self.app.get_thread(self.thread_id)
                await self.app.update_thread_configuration(
                    thread_id=self.thread_id,
                    mutation=ThreadConfigurationMutation(
                        expected_version=detail.thread.configuration.version,
                        patch=ThreadConfigurationPatch(environment_profile_id=profile),
                    ),
                )
            self.environment = profile
            await self.refresh()
        return f"Environment: {self.status.environment}. Changes apply on the next turn; no fallback."

    async def ensure_session(self) -> str:
        if self.thread_id is None:
            if not await self.refresh():
                raise ValueError("No model is configured. Use /setup before sending a prompt.")
            workspace = await self.app.ensure_cwd_workspace(self.directory)
            thread = await self.app.create_thread(
                defaults=NewThreadDefaults(
                    project_id=workspace.project_id,
                    agent_id=self.request.agent_id,
                    environment_profile_id=self.environment,
                ),
                title=self.request.title,
            )
            self.thread_id = thread.thread_id
            self.status.session_id = thread.thread_id
        return self.thread_id

    async def new(self) -> str:
        self.thread_id = None
        self.status.session_id = None
        self.status.context_tokens = None
        self._decisions.clear()
        self._decision_continuation = None
        await self.refresh()
        return "New session. Existing history is saved; no files were deleted."

    async def resume(self, selected: str | None = None) -> str:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Use /setup first.")
        matches = {
            project.id
            for project in configuration.projects.values()
            if len(project.roots) == 1 and project.roots[0].path == str(self.directory)
        }
        if selected is None:
            sessions = []
            for project_id in sorted(matches):
                page = await self.app.list_threads(project_id=project_id, limit=20)
                sessions.extend(page.threads)
            sessions.sort(key=lambda item: item.updated_at, reverse=True)
            return (
                "\n".join(
                    f"{item.thread_id}  {item.updated_at:%Y-%m-%d %H:%M}  {item.title or '(untitled)'}"
                    for item in sessions[:20]
                )
                or "No saved sessions in this directory."
            )
        detail = await self.app.get_thread(selected)
        if detail.thread.parent_thread_id is not None or detail.thread.archived:
            raise ValueError("Only non-archived root sessions can be resumed.")
        if detail.thread.configuration.project_id not in matches:
            raise ValueError("This session belongs to another workspace. Launch a13n-cli from its original directory.")
        if await self.app.active_root_operation(selected) is not None:
            raise ValueError("This session is already running.")
        self.thread_id = selected
        self.overrides = RunModelOverrides()
        self._decisions.clear()
        self._decision_continuation = None
        usage = await self.app.context_usage(selected)
        self.status.context_tokens = usage.latest_request_tokens
        await self.refresh()
        if usage.model_id is not None:
            # Restore the effective last-turn selection for reproducible continuation.
            self.overrides = RunModelOverrides.model_validate({"model_id": usage.model_id, "thinking": usage.thinking})
            await self.refresh()
        return f"Resumed {selected}. Use /history to print retained messages.\n{await self.pending()}"

    async def history(self, cursor: str | None = None) -> str:
        if self.thread_id is None:
            return "No messages yet."
        # Explicitly bounded page; old output is never rerendered automatically.
        page = await self.app.get_thread_transcript(thread_id=self.thread_id, cursor=cursor, limit=50)
        lines = []
        for entry in page.entries:
            for part in entry.parts:
                if self.status.mode == "concise" and part.kind not in {"user", "assistant", "retry"}:
                    continue
                value = part.text or json.dumps(part.value, ensure_ascii=False)
                lines.append(f"[{part.kind}] {part.tool_name or ''}\n{value}")
        if page.next_cursor is not None:
            lines.append(f"[More retained messages: /history {page.next_cursor}]")
        return "\n".join(lines) or "No retained messages."

    async def interaction(self) -> DecisionInteraction | None:
        if self.thread_id is None:
            return None
        batch = await self.app.thread_decisions(thread_id=self.thread_id)
        return DecisionInteraction(batch) if batch is not None else None

    async def pending(self) -> str:
        if self.thread_id is None:
            return ""
        detail = await self.app.get_thread(self.thread_id)
        if not detail.deferred_requests:
            return ""
        lines = ["Pending decisions (nothing is approved automatically):"]
        for item in detail.deferred_requests:
            arguments = json.dumps(item.arguments, ensure_ascii=False)
            lines.append(
                f"{item.request_id}: {item.kind} {item.tool_name}\n{arguments[: self.status.max_tool_argument_chars]}"
            )
            if item.arguments_omitted or len(arguments) > self.status.max_tool_argument_chars:
                lines.append(f"[Arguments omitted/truncated; /review {item.request_id} inspects the retained request]")
            lines.append(
                f"  /approve {item.request_id} or /deny {item.request_id}"
                if item.kind == "approval"
                else f"  /result {item.request_id} 'JSON' or /deny {item.request_id}"
            )
        return "\n".join(lines)

    async def review(self, request_id: str) -> str:
        if self.thread_id is None:
            raise ValueError("No active session.")
        detail = await self.app.get_thread(self.thread_id)
        if detail.continuation_id is None:
            raise ValueError("No selected continuation to review.")
        review = await self.app.deferred_review(
            thread_id=self.thread_id, expected_continuation_id=detail.continuation_id, request_id=request_id
        )
        return review.model_dump_json(indent=2)

    async def decide(self, command: str, request_id: str, value: str | None = None) -> ThreadDeferredResponse | None:
        if self.thread_id is None:
            raise ValueError("No active session.")
        detail = await self.app.get_thread(self.thread_id)
        item = next((item for item in detail.deferred_requests if item.request_id == request_id), None)
        if item is None or detail.continuation_id is None:
            raise ValueError("This request is no longer pending. Use /status to refresh.")
        if self._decision_continuation != detail.continuation_id:
            self._decisions.clear()
            self._decision_continuation = detail.continuation_id
        if item.kind == "approval":
            if command == "result":
                raise ValueError("Use /approve or /deny for an approval request.")
            self._decisions[request_id] = ApprovalDecision(request_id=request_id, approved=command == "approve")
        else:
            if command == "approve":
                raise ValueError("This tool needs a result, not approval. Use /result or /deny.")
            self._decisions[request_id] = ExternalToolResult(
                request_id=request_id,
                result=json.loads(value) if value else None,
                denied=command == "deny",
                denial_message="Denied in terminal" if command == "deny" else None,
            )
        if len(self._decisions) != len(detail.deferred_requests):
            return None
        response = ThreadDeferredResponse(
            expected_continuation_id=detail.continuation_id, responses=tuple(self._decisions.values())
        )
        self._decisions.clear()
        return response

    async def import_choices(self, product: str, scope: str) -> tuple[tuple[Choice, ...], str]:
        self.import_preview = await self.app.preview_subagent_import(
            product=product, scope=scope, project_root=self.directory, inherit_runtime=True
        )
        choices = []
        lines = [
            f"Import preview: {self.import_preview.source_root}",
            "Selected definitions inherit the parent model, capabilities, and tools; imported instructions replace parent instructions.",
        ]
        for candidate in self.import_preview.candidates:
            lines.append(f"{candidate.name}: {candidate.status} -> {candidate.target_relative_path}")
            lines.extend(f"  {item.severity}: {item.message}" for item in candidate.diagnostics)
            if candidate.status in {"ready", "unchanged"}:
                choices.append(Choice(candidate.name, candidate.name, candidate.status))
                lines.append(candidate.canonical_content or "")
        return tuple(choices), "\n".join(lines)

    async def import_and_enroll(self, names: tuple[str, ...]) -> str:
        if self.import_preview is None:
            raise ValueError("Preview definitions first.")
        candidates = tuple(item for item in self.import_preview.candidates if item.name in names)
        if len(candidates) != len(names):
            raise ValueError("The import selection changed. Preview again.")
        published = []
        try:
            for candidate in candidates:
                await self.app.apply_subagent_import(candidate)
                published.append(candidate.target_relative_path)
            configuration = await self.app.current_configuration()
            if configuration is None:
                raise ValueError("No accepted configuration.")
            agent_id = self.request.agent_id or configuration.document.defaults.agent
            if self.thread_id is not None:
                agent_id = (await self.app.get_thread(self.thread_id)).thread.configuration.agent_source.id
            source = next((item for item in configuration.sources if item.resource_id == agent_id), None)
            if source is None:
                raise ValueError("The selected Agent has no editable source.")
            document = yaml.safe_load(source.content)
            roster = document.setdefault("subagents", [])
            for candidate in candidates:
                edge = {"markdown": f"subagent-{candidate.name}"}
                if edge not in roster:
                    roster.append(edge)
            await self.app.mutate_configuration(
                relative_path=source.relative_path,
                request=ResourceMutationRequest(
                    expected_source_digest=source.source_digest,
                    content=yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                ),
            )
            return f"Imported and enabled {len(candidates)} subagent(s) on {agent_id}. Model and tools inherit at the next composition capture."
        except Exception as exc:
            raise ValueError(
                f"Import/enrollment incomplete: {exc}. Definitions already published or reused: {', '.join(published) or 'none'}. No rollback claimed; /import previews the current state before retry."
            ) from exc

    async def choices(self, kind: str) -> tuple[Choice, ...]:
        if kind == "model":
            configuration = await self.app.current_configuration()
            return (
                (
                    Choice("default", "Agent default"),
                    *(Choice(item.id, item.id, item.route) for item in configuration.models.values()),
                )
                if configuration
                else ()
            )
        if kind == "thinking":
            return tuple(Choice(value, value) for value in ("default", "low", "medium", "high", "xhigh"))
        if kind == "environment":
            return (
                Choice("full-control", "Full Control", "Native host shell and files; no sandbox"),
                Choice("sandbox", "Sandbox", "Isolation; unavailable configurations fail without fallback"),
            )
        if kind == "resume":
            configuration = await self.app.current_configuration()
            if configuration is None:
                return ()
            sessions = []
            for project in configuration.projects.values():
                if len(project.roots) == 1 and project.roots[0].path == str(self.directory):
                    page = await self.app.list_threads(project_id=project.id, limit=20)
                    sessions.extend(
                        item for item in page.threads if item.parent_thread_id is None and not item.archived
                    )
            sessions.sort(key=lambda item: item.updated_at, reverse=True)
            return tuple(
                Choice(item.thread_id, item.title or "Untitled", f"{item.updated_at:%Y-%m-%d %H:%M} · {item.thread_id}")
                for item in sessions[:20]
            )
        raise ValueError("Unknown selector.")

    async def preview_setup(self, wizard: SetupWizard) -> str:
        selection = SetupSelection.model_validate(wizard.selection(str(self.directory)))
        self.preview = await self.app.preview_setup(selection)
        wizard.preview_generation = self.preview.generation
        lines = ["Files to create (existing resources are never overwritten):", *self.preview.files.keys()]
        if self.preview.preserved_paths:
            lines.append("Preserved: " + ", ".join(self.preview.preserved_paths))
        if "models/codex.yaml" in self.preview.files:
            lines += ["Explicit model configuration:", self.preview.files["models/codex.yaml"]]
        return "\n".join(lines)

    async def publish_setup(self, wizard: SetupWizard) -> str:
        if wizard.preview_generation is None:
            raise ValueError("Preview setup before publication.")
        publication = await self.app.apply_setup(
            SetupSelection.model_validate(wizard.selection(str(self.directory))),
            expected_generation=wizard.preview_generation,
        )
        if not publication.completed:
            raise ConfigurationError(
                publication.error_message or "Setup publication failed", code=publication.error_code or "setup_failed"
            )
        await self.refresh()
        if wizard.values.get("provider") == "api":
            return "Configuration saved. Set the referenced API-key environment variable or stored key before sending a prompt."
        return "Configuration saved. Use /login to authenticate a subscription, or send a prompt if already signed in."

    async def execute(
        self,
        renderer: StreamRenderer,
        *,
        prompt: RunInputValue | None = None,
        response: ThreadDeferredResponse | None = None,
        flush: Callable[[], Awaitable[None]] | None = None,
        admitted: Callable[[], None] | None = None,
    ) -> str:
        self.cancel_requested = False
        await self.refresh()
        thread_id = await self.ensure_session()
        last_ordinal = -1
        async with self.app.live_events(root_thread_id=thread_id) as subscription:

            def ingest(event: LiveEvent) -> None:
                nonlocal last_ordinal
                renderer.ingest(event.event_type, event.payload, child=event.run_kind == "child", run_id=event.run_id)
                for sample in root_context_samples(event):
                    if sample.response_ordinal > last_ordinal:
                        self.status.context_tokens = sample.tokens
                        last_ordinal = sample.response_ordinal

            async def consume() -> None:
                try:
                    async for event in subscription:
                        ingest(event)
                        if flush is not None and renderer.should_flush:
                            await flush()
                except AgentUiError:
                    renderer.gap = True

            pump = asyncio.create_task(consume())
            try:
                receipt = (
                    await self.app.submit_thread(
                        thread_id=thread_id, prompt=prompt or "", model_overrides=self.overrides
                    )
                    if response is None
                    else await self.app.respond_thread(
                        thread_id=thread_id, response=response, model_overrides=self.overrides
                    )
                )
                self.receipt_id = receipt.receipt_id
                if admitted is not None:
                    admitted()
                if self.cancel_requested:
                    await self.app.cancel_root_operation(receipt.receipt_id)
                operation = await self.app.wait_root_operation(receipt.receipt_id)
            finally:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
                self.receipt_id = None
                try:
                    for event in subscription.drain_pending():
                        ingest(event)
                        if flush is not None and renderer.should_flush:
                            await flush()
                except AgentUiError:
                    renderer.gap = True
                renderer.finish()
        usage = await self.app.context_usage(thread_id)
        self.status.context_tokens = usage.latest_request_tokens
        outcome = operation.outcome
        if outcome is not None and operation.status == RootOperationStatus.completed:
            if not renderer.assistant_seen or renderer.gap:
                prefix = "[Recovered final answer after incomplete live output]\n" if renderer.gap else ""
                if outcome.execution.output_omitted:
                    renderer.append(
                        prefix
                        + "[Final output exceeds the bounded result projection; use /history to inspect retained messages.]\n"
                    )
                else:
                    renderer.append(prefix + str(outcome.execution.output) + "\n")
            if outcome.environment.cleanup_failures:
                renderer.append(
                    f"Warning: {len(outcome.environment.cleanup_failures)} environment cleanup failure(s).\n"
                )
            return ""
        failure = operation.failure or (None if outcome is None else outcome.execution.failure)
        if failure is not None:
            return f"Error [{failure.code}]: {failure.message}" + (
                f"\nRetry: {failure.retry_hint}" if failure.retry_hint else ""
            )
        if operation.status == RootOperationStatus.suspended:
            return await self.pending()
        return f"Run {operation.status.value}."

    async def cancel(self) -> None:
        self.cancel_requested = True
        if self.receipt_id is not None:
            await self.app.cancel_root_operation(self.receipt_id)
