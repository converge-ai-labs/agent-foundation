"""Application adapter for one CLI session; HarnessUiApp owns execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from pathlib import Path

import yaml
from a13n_harness.input import RunInputValue
from a13n_stream_protocol import CustomEventAssembler

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.configuration import (
    ExternalSubagentImportPreview,
    LoadedHarnessUiConfiguration,
    ResourceMutationRequest,
)
from a13n_harness_ui.environment_profiles import (
    environment_profile_id_for_mode,
    local_sandbox_supported,
    require_supported_local_profile,
)
from a13n_harness_ui.errors import HarnessUiError, ThreadError
from a13n_harness_ui.live import LiveEvent, root_context_samples, root_model_usage
from a13n_harness_ui.model_adapters import service_tier_setting
from a13n_harness_ui.storage import (
    AgentResourceSource,
    ThreadConfiguration,
    ThreadConfigurationMutation,
    ThreadConfigurationPatch,
)
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    RunModelOverrides,
    SkillCatalogView,
    SkillReference,
    ThreadDeferredResponse,
    ThreadPage,
    ThreadSummary,
    TranscriptPage,
)
from a13n_harness_ui.thread_files import ComposerInput

from .decisions import DecisionInteraction
from .rendering import Status, StreamRenderer
from .selection import Choice
from .subagents import subagent_status


class SessionBackend:
    def __init__(self, app: HarnessUiApp, request: CliRequest, directory: Path, status: Status) -> None:
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
        self.agent_id = request.agent_id
        self.overrides = RunModelOverrides()
        self.environment = request.environment_profile_id or (
            environment_profile_id_for_mode(request.environment_mode) if request.environment_mode else None
        )
        self.receipt_id: str | None = None
        self.cancel_requested = False
        self.import_preview: ExternalSubagentImportPreview | None = None
        self.resumed_transcript: TranscriptPage | None = None
        self._child_page_thread: str | None = None
        self._child_page_cursor: str | None = None

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
            return self.status.model != "not configured"
        return await self.refresh()

    async def refresh(self, *, thread: ThreadSummary | None = None) -> bool:
        configuration = await self.app.current_configuration()
        if configuration is None:
            return False
        if self.thread_id is not None:
            thread = thread or (await self.app.get_thread(self.thread_id)).thread
        draft = None
        if thread is None:
            try:
                draft = await self._draft_configuration()
            except ThreadError as exc:
                if exc.code != "thread_agent_missing":
                    raise
        return self._refresh_status(configuration, thread, draft=draft)

    async def _draft_configuration(self) -> ThreadConfiguration:
        projects = await self.app.cwd_project_ids(self.directory)
        # No exact match means ensure_session will create a new, empty-default Project.
        # Ambiguity remains an explicit error at creation rather than selecting a Project.
        return await self.app.preview_thread_configuration(
            defaults=NewThreadDefaults(
                project_id=next(iter(projects)) if len(projects) == 1 else None,
                agent_id=self.agent_id,
                environment_profile_id=self.environment,
            )
        )

    def _refresh_status(
        self,
        configuration: LoadedHarnessUiConfiguration,
        thread: ThreadSummary | None,
        *,
        draft: ThreadConfiguration | None = None,
    ) -> bool:
        agent_id = None if draft is None else draft.agent_source.id
        if thread is not None:
            agent_id = thread.configuration.agent_source.id
            self.status.session_id = self.thread_id
            self.environment = thread.configuration.environment_profile_id
        agent = configuration.agents.get(agent_id or "")
        self.status.agent = "not configured" if agent is None else agent.name
        model_id = self.overrides.model_id or (None if agent is None else agent.model)
        model = configuration.models.get(model_id or "")
        self.status.environment = (
            draft.environment_profile_id
            if draft is not None
            else self.environment or configuration.document.defaults.environment_profile or "environment-native"
        )
        if model is None:
            self.status.model = "not configured"
            self.status.thinking = "default"
            self.status.service_tier = None
            self.status.context_window = None
            return False
        self.status.model = model.route
        tier = (
            self.overrides.service_tier
            or model.settings.get(service_tier_setting(model.route))
            or model.settings.get("service_tier")
        )
        self.status.service_tier = tier if isinstance(tier, str) else None
        self.status.thinking = str(
            self.overrides.thinking
            if self.overrides.thinking is not None
            else model.settings.get("thinking", "default")
        )
        self.status.context_window = (
            None if model.model_characteristics is None else model.model_characteristics.context_window
        )
        return True

    async def skill_catalog(self) -> SkillCatalogView | None:
        if self.thread_id is not None:
            return await self.app.skill_catalog(thread_id=self.thread_id)
        projects = await self.app.cwd_project_ids(self.directory)
        if len(projects) != 1:
            return None
        return await self.app.skill_catalog(
            defaults=NewThreadDefaults(
                project_id=next(iter(projects)),
                agent_id=self.agent_id,
                environment_profile_id=self.environment,
            )
        )

    async def agents(self, selected: str | None = None) -> str:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Run a13n-harness-ui setup first.")
        if selected is None:
            return (
                "\n".join(f"{item.id}: {item.name}" for item in configuration.agents.values())
                or "No agents yet. Run a13n-harness-ui add agent."
            )
        if selected == "default":
            selected = configuration.document.defaults.agent
        agent = configuration.agents.get(selected or "")
        if agent is None:
            raise ValueError("Unknown agent. Use /agent to see available choices.")
        if agent.model is None:
            raise ValueError("This agent has no model. Configure it before switching.")
        if self.thread_id is not None:
            detail = await self.app.get_thread(self.thread_id)
            await self.app.update_thread_configuration(
                thread_id=self.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=detail.thread.configuration.version,
                    patch=ThreadConfigurationPatch(agent_source=AgentResourceSource(id=agent.id)),
                ),
            )
        self.agent_id = agent.id
        self.overrides = RunModelOverrides(model_id=self.overrides.model_id)
        self.status.context_tokens = None
        await self.refresh()
        return f"Agent · {agent.name} · {self.status.model}"

    async def models(self, selected: str) -> str:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Run a13n-harness-ui setup first.")
        model_id = None if selected == "default" else selected
        if model_id is not None and model_id not in configuration.models:
            raise ValueError("Unknown model. Use /model to see available choices.")
        # Switching models drops model-specific reasoning, not the selected Agent.
        self.overrides = RunModelOverrides(model_id=model_id)
        self.status.context_tokens = None
        await self.refresh()
        return f"Model · {self.status.model} · session only"

    async def thinking(self, selected: str | None) -> str:
        if selected is not None:
            self.overrides = RunModelOverrides.model_validate(
                {**self.overrides.model_dump(), "thinking": None if selected == "default" else selected}
            )
            await self.refresh()
        return f"Reasoning · {self.status.thinking}"

    async def fast(self, selected: str | None) -> str:
        if not await self.refresh():
            raise ValueError("Configure a model before selecting its service tier.")
        action = selected or ("off" if self.status.service_tier == "priority" else "on")
        tiers = {"on": "priority", "off": "default", "reset": None}
        if action not in tiers:
            raise ValueError("Usage: /fast [on|off|reset]")
        self.overrides = RunModelOverrides.model_validate(
            {**self.overrides.model_dump(), "service_tier": tiers[action]}
        )
        await self.refresh()
        message = f"Service tier · {self.status.service_tier_text} · " + (
            "Model configuration restored." if action == "reset" else "session only; configuration unchanged."
        )
        if self.status.service_tier == "priority":
            message += (
                " Priority requested; may use more quota or cost more. Provider support and speed are not guaranteed."
            )
        return message

    async def set_environment(self, selected: str | None) -> str:
        if selected is not None:
            profile = environment_profile_id_for_mode(selected)
            require_supported_local_profile(profile)
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
        return f"Environment · {self.status.environment} · next turn"

    async def ensure_session(self) -> str:
        await self.refresh()
        require_supported_local_profile(self.status.environment)
        if self.thread_id is None:
            if not await self.refresh():
                raise ValueError("No model is configured. Use `a13n-harness-ui setup` before sending a prompt.")
            project_id = await self.app.ensure_cwd_project(self.directory)
            thread = await self.app.create_thread(
                defaults=NewThreadDefaults(
                    project_id=project_id,
                    agent_id=self.agent_id,
                    environment_profile_id=self.environment,
                ),
                title=self.request.title,
            )
            self.thread_id = thread.thread_id
            self.status.session_id = thread.thread_id
            await self.refresh(thread=thread)
        return self.thread_id

    async def new(self) -> str:
        self.thread_id = None
        self.status.session_id = None
        self.status.context_tokens = None
        self.status.reset_usage()
        await self.refresh()
        return "New session."

    async def resume_sessions(
        self, *, query: str = "", all_directories: bool = False, cursor: str | None = None
    ) -> ThreadPage:
        projects = None if all_directories else tuple(sorted(await self.app.cwd_project_ids(self.directory)))
        return await self.app.list_threads(
            query=query or None, project_ids=projects, sort="activity", cursor=cursor, limit=20
        )

    async def resume(self, selected: str) -> str:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Use `a13n-harness-ui setup` first.")
        matches = await self.app.cwd_project_ids(self.directory)
        detail = await self.app.get_thread(selected)
        if detail.thread.parent_thread_id is not None or detail.thread.archived:
            raise ValueError("Only non-archived root sessions can be resumed.")
        if await self.app.active_root_operation(selected) is not None:
            raise ValueError("This session is already running.")
        # Fetch one recent page before switching; never replay every saved message.
        page = await self.app.get_thread_transcript(
            thread_id=selected, expected_continuation_id=detail.continuation_id, limit=50
        )
        totals = await self.app.thread_usage(thread_id=selected)
        usage = await self.app.context_usage(selected)
        thread = detail.thread
        if thread.configuration.project_id not in matches:
            project_id = await self.app.ensure_cwd_project(self.directory)
            thread = await self.app.update_thread_configuration(
                thread_id=selected,
                mutation=ThreadConfigurationMutation(
                    expected_version=thread.configuration.version,
                    patch=ThreadConfigurationPatch(project_id=project_id),
                ),
            )
        # All fallible I/O precedes the local selection change.
        self.thread_id = selected
        self.status.restore_usage(totals.root)
        self.status.context_tokens = usage.latest_request_tokens
        agent = configuration.agents.get(thread.configuration.agent_source.id)
        self.agent_id = thread.configuration.agent_source.id
        if self.overrides.model_id is None:
            # Historical usage never restores a temporary model selection.
            self.overrides = RunModelOverrides.model_validate(
                {
                    "thinking": usage.thinking if agent is not None and usage.model_id == agent.model else None,
                    "service_tier": self.overrides.service_tier,
                }
            )
        self._refresh_status(configuration, thread)
        self.resumed_transcript = page
        return f"Resumed {selected}."

    async def interaction(self) -> DecisionInteraction | None:
        if self.thread_id is None:
            return None
        batch = await self.app.thread_decisions(thread_id=self.thread_id)
        if batch is None:
            return None
        interaction = DecisionInteraction(batch)
        configuration = await self.app.current_configuration()
        if configuration is not None:
            interaction.timeout_seconds = configuration.document.tools.ask_user_question_timeout_seconds
        return interaction

    async def pending(self) -> str:
        if self.thread_id is None:
            return ""
        detail = await self.app.get_thread(self.thread_id)
        if not detail.deferred_requests:
            return ""
        # The typed selector owns arguments and response instructions. Do not
        # duplicate its preview or advertise commands blocked by that selector.
        lines = ["Pending decisions (nothing is approved automatically):"]
        lines.extend(
            f"{item.request_id}: {item.kind} {item.tool_name}"
            for item in detail.deferred_requests
            if item.tool_name != "ask_user_question"
        )
        if len(lines) == 1:
            return ""
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
        lines = [f"{review.title} · {review.lifecycle}"]
        lines.extend(item for item in (review.summary, review.content, review.unavailable_reason) if item)
        if review.value is not None:
            lines.append(json.dumps(review.value, ensure_ascii=False, indent=2))
        if review.truncated or review.omitted:
            lines.append("Review preview is incomplete; omitted content is not approval evidence.")
        return "\n".join(lines)

    async def subagents(self, execution_id: str | None = None) -> str:
        thread_id = self.thread_id
        if thread_id is None:
            return "No subagent executions."
        if execution_id is not None and execution_id != "next":
            review = await self.app.child_review(parent_thread_id=thread_id, execution_id=execution_id)
            if self.thread_id != thread_id:
                return "Conversation changed; subagent inspection discarded. /subagents retries."
            state: str = review.lifecycle
            local_active = False
            if isinstance(review.value, dict):
                saved_state = review.value.get("persisted_status")
                if isinstance(saved_state, str):
                    state = saved_state
                local_active = review.value.get("local_status") == "active"
            state = subagent_status(state, local_active=local_active)
            lines = [f"{review.title} · {state}"]
            lines.extend(item for item in (review.summary, review.content, review.unavailable_reason) if item)
            if review.value is not None:
                lines.append(json.dumps(review.value, ensure_ascii=False, indent=2))
            if review.truncated or review.omitted:
                lines.append("Subagent preview incomplete.")
            return "\n".join(lines)
        cursor = None
        if execution_id == "next":
            if self._child_page_thread != thread_id or self._child_page_cursor is None:
                return "No next page. /subagents to refresh."
            cursor = self._child_page_cursor
        page = await self.app.query_child_executions(parent_thread_id=thread_id, cursor=cursor, limit=20)
        if self.thread_id != thread_id:
            return "Conversation changed; subagent inspection discarded. /subagents retries."
        self._child_page_thread = thread_id
        self._child_page_cursor = page.next_cursor
        lines = [f"Subagents · {len(page.executions)} shown · {page.total} executions"]
        for item in page.executions:
            state = subagent_status(item.persisted_status, local_active=item.local_status == "active")
            lines.append(f"{item.execution_id} · {item.subagent_name} · {state}")
        if not page.executions:
            lines.append("No subagent executions.")
        if page.next_cursor is not None:
            lines.append("More · /subagents next")
        lines.append("/subagents <execution-id> · details")
        return "\n".join(lines)

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
            agent_id = (
                (await self._draft_configuration()).agent_source.id
                if self.thread_id is None
                else (await self.app.get_thread(self.thread_id)).thread.configuration.agent_source.id
            )
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
                    content=yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                ),
            )
            return f"Imported and enabled {len(candidates)} subagent(s) · {agent_id} · next turn"
        except Exception as exc:
            raise ValueError(
                f"Import/enrollment incomplete: {exc}. Definitions already published or reused: {', '.join(published) or 'none'}. No rollback claimed; /import previews the current state before retry."
            ) from exc

    async def choices(self, kind: str) -> tuple[Choice, ...]:
        if kind == "model":
            configuration = await self.app.current_configuration()
            if configuration is None:
                return ()
            return (
                Choice("default", "Agent default", "Clear the temporary model override"),
                *(Choice(item.id, item.name, item.route) for item in configuration.models.values()),
            )
        if kind == "agent":
            configuration = await self.app.current_configuration()
            return (
                tuple(
                    Choice(
                        item.id,
                        item.name,
                        configuration.models[item.model].route
                        if item.model in configuration.models
                        else "No model configured",
                    )
                    for item in configuration.agents.values()
                )
                if configuration
                else ()
            )
        if kind == "thinking":
            return tuple(Choice(value, value) for value in ("default", "low", "medium", "high", "xhigh"))
        if kind == "environment":
            return (
                Choice("full-control", "Full Control", "Native host shell and files; no sandbox"),
                *(
                    (Choice("sandbox", "Sandbox", "Isolation; unavailable configurations fail without fallback"),)
                    if local_sandbox_supported()
                    else ()
                ),
            )
        raise ValueError("Unknown selector.")

    def execute(
        self,
        renderer: StreamRenderer,
        *,
        prompt: RunInputValue | ComposerInput | None = None,
        response: ThreadDeferredResponse | None = None,
        flush: Callable[[], Awaitable[None]] | None = None,
        admitted: Callable[[], None] | None = None,
        skill_references: tuple[SkillReference, ...] = (),
    ) -> Coroutine[object, object, str]:
        # Reset at scheduling, not first coroutine execution: Enter and Ctrl+C
        # may arrive in the same terminal input batch before this task starts.
        self.cancel_requested = False
        return self._execute(
            renderer,
            prompt=prompt,
            response=response,
            flush=flush,
            admitted=admitted,
            skill_references=skill_references,
        )

    async def _execute(
        self,
        renderer: StreamRenderer,
        *,
        prompt: RunInputValue | ComposerInput | None,
        response: ThreadDeferredResponse | None,
        flush: Callable[[], Awaitable[None]] | None,
        admitted: Callable[[], None] | None,
        skill_references: tuple[SkillReference, ...],
    ) -> str:
        if self.cancel_requested:
            return "Cancelled before admission. No operation was submitted."
        await self.refresh()
        thread_id = await self.ensure_session()
        last_ordinal = -1
        custom_events = CustomEventAssembler()
        # Snapshot before admission, then add only this operation's live records.
        # Never combine an in-flight ledger snapshot with the same live delta.
        totals = await self.app.thread_usage(thread_id=thread_id)
        self.status.restore_usage(totals.root)
        async with self.app.live_events(root_thread_id=thread_id) as subscription:

            def ingest(event: LiveEvent) -> bool:
                nonlocal last_ordinal
                if event.event_type == "CUSTOM" and event.payload is not None:
                    payload = custom_events.accept(event.payload)
                    renderer.gap |= custom_events.gap
                    if payload is None:
                        return False
                    event = event.model_copy(update={"payload": payload})
                renderer.ingest(
                    event.event_type,
                    event.payload,
                    child=event.run_kind == "child",
                    run_id=event.run_id,
                    execution_id=event.execution_id,
                )
                records = root_model_usage(event)
                for record in records:
                    self.status.record_usage(record)
                for sample in root_context_samples(event):
                    if sample.response_ordinal > last_ordinal:
                        self.status.context_tokens = sample.tokens
                        last_ordinal = sample.response_ordinal
                return bool(records)

            async def consume() -> None:
                try:
                    async for event in subscription:
                        usage_changed = ingest(event)
                        if flush is not None and (usage_changed or renderer.should_flush):
                            await flush()
                except HarnessUiError:
                    renderer.gap = True

            pump = asyncio.create_task(consume())
            try:
                if self.cancel_requested:
                    return "Cancelled before admission. No operation was submitted."
                receipt = (
                    await self.app.submit_thread(
                        thread_id=thread_id,
                        prompt=prompt or "",
                        model_overrides=self.overrides,
                        skill_references=skill_references,
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
                        usage_changed = ingest(event)
                        if flush is not None and (usage_changed or renderer.should_flush):
                            await flush()
                except HarnessUiError:
                    renderer.gap = True
                finally:
                    # Children can outlive this root subscription. Without live
                    # observation, a running snapshot is no longer current.
                    renderer.end_process_observations()
                renderer.finish()
                # The ledger includes terminal/failed/cancelled observations and
                # repairs live gaps. Replace, rather than add, after draining.
                totals = await self.app.thread_usage(thread_id=thread_id)
                self.status.restore_usage(totals.root)
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
                f"\nRetry: {failure.retry_hint}"
                if failure.retry_hint and failure.code != "model_recovery_exhausted"
                else ""
            )
        if operation.status == RootOperationStatus.suspended:
            return await self.pending()
        return f"Run {operation.status.value}."

    async def steer(
        self, message: str, *, receipt_id: str | None = None, skill_references: tuple[SkillReference, ...] = ()
    ) -> str:
        # The Enter handler supplies its captured target; never substitute a newer receipt.
        receipt_id = self.receipt_id if receipt_id is None else receipt_id
        if receipt_id is None:
            raise ValueError("No running receipt accepts steering. Your guidance was not sent.")
        result = await self.app.steer_root_operation(
            receipt_id=receipt_id, message=message, skill_references=skill_references
        )
        if not result.accepted:
            raise ValueError("This receipt is preparing or no longer running. Your guidance was not accepted.")
        return "Guidance sent."

    async def cancel(self) -> None:
        self.cancel_requested = True
        if self.receipt_id is not None:
            await self.app.cancel_root_operation(self.receipt_id)
