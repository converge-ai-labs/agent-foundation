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
from a13n_harness_ui.configuration.models import MEDIA_KINDS
from a13n_harness_ui.environment_profiles import (
    environment_profile_id_for_mode,
    local_sandbox_supported,
    require_supported_local_profile,
)
from a13n_harness_ui.errors import HarnessUiError, ThreadError
from a13n_harness_ui.goal import GoalMode
from a13n_harness_ui.live import LiveEvent, model_usage, root_context_samples
from a13n_harness_ui.media_understanding import environment_media_kinds
from a13n_harness_ui.model_adapters import service_tier_setting
from a13n_harness_ui.model_controls import describe_model_controls
from a13n_harness_ui.model_fast import FastControl, apply_fast, fast_state
from a13n_harness_ui.model_reasoning_mode import ReasoningModeControl, apply_reasoning_mode, reasoning_mode_state
from a13n_harness_ui.model_thinking import ThinkingControl, apply_thinking, summarize_thinking
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
        self.thinking_control: ThinkingControl | None = None
        self.fast_control: FastControl | None = None
        self.reasoning_mode_control: ReasoningModeControl | None = None
        self._model_preference_project_id: str | None = None
        self._model_from_preference = False
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
        await self._restore_project_model()
        return await self.refresh()

    async def _restore_project_model(self, project_id: str | None = None) -> None:
        # Automation and an explicit launch Agent never inherit interactive memory.
        if self.request.command is not None or self.request.agent_id is not None:
            return
        preference = await self.app.cwd_model_preference(self.directory, project_id=project_id)
        if preference is None or preference[0] == self._model_preference_project_id:
            return
        scope, model_id = preference
        configuration = await self.app.current_configuration()
        if configuration is None:
            return
        if model_id is not None and model_id not in configuration.models:
            self.status.notices.append(
                f"Remembered Model {model_id} is unavailable; using the Agent's configured model."
            )
            model_id = None
        self.overrides = RunModelOverrides(model_id=model_id)
        self._model_preference_project_id = scope
        self._model_from_preference = True

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

    async def refresh_goal(self) -> None:
        thread_id = self.thread_id
        if thread_id is None:
            self.status.goal = None
            return
        operation = await self.app.active_root_operation(thread_id)
        goal = operation.goal if operation is not None else (await self.app.get_thread(thread_id)).thread.goal
        if thread_id == self.thread_id:
            self.status.goal = goal

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
        self.status.goal = None if thread is None else thread.goal
        agent_id = None if draft is None else draft.agent_source.id
        if thread is not None:
            agent_id = thread.configuration.agent_source.id
            self.status.session_id = self.thread_id
            self.environment = thread.configuration.environment_profile_id
        agent = configuration.agents.get(agent_id or "")
        self.status.agent = "not configured" if agent is None else agent.name
        selected = thread.configuration if thread is not None else draft
        model_id = (
            self.overrides.model_id
            or (None if selected is None else selected.default_model_id)
            or (None if agent is None else agent.model)
        )
        model = configuration.models.get(model_id or "")
        self.status.environment = (
            draft.environment_profile_id
            if draft is not None
            else self.environment or configuration.document.defaults.environment_profile or "environment-native"
        )
        if model is None:
            self.status.model = "not configured"
            self.status.thinking = "default"
            self.thinking_control = None
            self.status.service_tier = None
            self.status.fast = "default"
            self.fast_control = None
            self.reasoning_mode_control = None
            self.status.reasoning_mode = "default"
            self.status.reasoning_mode_description = "Provider default"
            self.status.context_window = None
            return False
        self.status.model = model.route
        controls = describe_model_controls(model.route, model.settings)
        self.fast_control = controls.fast
        self.reasoning_mode_control = controls.reasoning_mode
        default_mode = controls.reasoning_mode.state
        default_label = "Provider default" if default_mode == "default" else default_mode.capitalize()
        try:
            mode_settings = apply_reasoning_mode(model.route, model.settings, self.overrides.reasoning_mode)
            self.status.reasoning_mode = reasoning_mode_state(model.route, mode_settings)
            self.status.reasoning_mode_description = (
                f"{default_label} (Model default)"
                if self.overrides.reasoning_mode is None
                else f"{self.overrides.reasoning_mode.capitalize()} (session override; Model default: {default_label})"
            )
        except HarnessUiError:
            self.status.reasoning_mode = "unavailable"
            self.status.reasoning_mode_description = (
                f"Unavailable selection — /pro reset (Model default: {default_label})"
            )
        try:
            fast_settings = apply_fast(model.route, model.settings, self.overrides.fast)
        except HarnessUiError:
            fast_settings = dict(model.settings)
        self.status.fast = fast_state(model.route, fast_settings)
        tier = (
            self.overrides.service_tier
            or fast_settings.get(service_tier_setting(model.route))
            or fast_settings.get("service_tier")
        )
        self.status.service_tier = tier if isinstance(tier, str) else None
        self.thinking_control = controls.thinking
        try:
            effective = apply_thinking(model.route, model.settings, self.overrides.thinking)
            self.status.thinking = summarize_thinking(model.route, effective)
            if self.overrides.thinking is None:
                self.status.thinking += " (default)"
        except HarnessUiError:
            # A configuration publication can invalidate a draft choice. Keep
            # the choice visible; capture rejects it rather than falling back.
            self.status.thinking = "Unavailable selection — /thinking to reset"
        self.status.context_window = (
            None if model.model_characteristics is None else model.model_characteristics.context_window_tokens
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
        if self.request.command is None:
            project_id = None
            if self.thread_id is not None:
                project_id = (await self.app.get_thread(self.thread_id)).thread.configuration.project_id
            preference = await self.app.cwd_model_preference(self.directory, project_id=project_id)
            if preference is None:
                raise ValueError("Multiple Projects match this directory. Resume a session before choosing a model.")
            await self.app.remember_project_model(project_id=preference[0], model_id=model_id)
            self._model_preference_project_id = preference[0]
        # Publish memory before changing the local selection; failed writes retain it.
        # Switching models drops model-specific reasoning, not the selected Agent.
        self.overrides = RunModelOverrides(model_id=model_id)
        self._model_from_preference = False
        self.status.context_tokens = None
        await self.refresh()
        scope = (
            "session only"
            if self.request.command is not None
            else ("project preference cleared" if model_id is None else "remembered for this project")
        )
        return f"Model · {self.status.model} · {scope}"

    async def media_default_choices(self, kind: str | None = None) -> tuple[Choice, ...]:
        configuration = await self.app.current_configuration()
        if configuration is None:
            raise ValueError("Run a13n-harness-ui setup first.")
        environment = environment_media_kinds()
        if kind is None:
            selected = configuration.document.media_understanding.selections()
            return tuple(
                Choice(
                    media,
                    media.capitalize(),
                    configuration.models[selected[media]].name
                    if media in selected
                    else ("Environment" if media in environment else "Not configured"),
                )
                for media in MEDIA_KINDS
            )
        if kind not in MEDIA_KINDS:
            raise ValueError("Unknown media kind.")
        return (
            Choice("default", "Environment" if kind in environment else "Not configured", "Clear configured default"),
            *(
                Choice(model.id, model.name, model.route)
                for model in configuration.models.values()
                if kind in model.media_capabilities()
            ),
        )

    async def set_media_default(self, kind: str, selected: str) -> str:
        if kind not in MEDIA_KINDS:
            raise ValueError("Unknown media kind.")
        catalog = await self.app.configuration_sources()
        root = next(source for source in catalog.sources if source.resource_kind == "root")
        source = await self.app.configuration_source(relative_path=root.relative_path)
        if not source.writable or source.content is None:
            raise ValueError("Root configuration is read only.")
        document = yaml.safe_load(source.content)
        media = document.get("media_understanding") or {}
        media[kind] = None if selected == "default" else selected
        document["media_understanding"] = media
        await self.app.mutate_configuration(
            relative_path=root.relative_path,
            request=ResourceMutationRequest(content=yaml.safe_dump(document, sort_keys=False, allow_unicode=True)),
        )
        return f"{kind.capitalize()} understanding default saved · future Runs"

    async def thinking(self, selected: str | None) -> str:
        await self.refresh()
        control = self.thinking_control
        if control is None:
            raise ValueError("Configure a model before selecting thinking.")
        if selected is not None:
            option = next((item for item in control.options if item.command == selected), None)
            if option is None:
                raise ValueError(control.reason or "Unsupported thinking option. Use /thinking for available choices.")
            if option.disabled_reason:
                raise ValueError(option.disabled_reason)
            self.overrides = self.overrides.model_copy(update={"thinking": option.value})
            await self.refresh()
        return f"Thinking · {self.status.thinking}"

    async def fast(self, selected: str | None) -> str:
        if not await self.refresh():
            raise ValueError("Configure a model before selecting its service tier.")
        action = selected or ("off" if self.status.fast == "on" else "on")
        choices = {"on": True, "off": False, "ultrafast": "ultrafast", "reset": None}
        if action not in choices:
            raise ValueError("Usage: /fast [on|off|ultrafast|reset]")
        if action != "reset" and (self.fast_control is None or not self.fast_control.supported):
            raise ValueError(self.fast_control.reason if self.fast_control else "Fast is unavailable.")
        if action == "ultrafast" and self.fast_control is not None and not self.fast_control.ultrafast_supported:
            raise ValueError(self.fast_control.ultrafast_reason or "Ultrafast is unavailable.")
        self.overrides = RunModelOverrides.model_validate(
            {**self.overrides.model_dump(), "service_tier": None, "fast": choices[action]}
        )
        await self.refresh()
        message = f"Fast · {self.status.fast.capitalize()} · " + (
            "Model configuration restored." if action == "reset" else "session only; configuration unchanged."
        )
        if self.status.fast in {"on", "ultrafast"}:
            message += (
                " Speed requested; may use more quota or cost more. Provider support and speed are not guaranteed."
            )
        if self.status.fast == "ultrafast":
            message += " Requires Pro $500 or an eligible Enterprise/Edu plan; OpenAI checks account access."
        return message

    async def pro(self, selected: str | None) -> str:
        if not await self.refresh():
            raise ValueError("Configure a model before selecting reasoning mode.")
        action = selected or ("off" if self.status.reasoning_mode == "pro" else "on")
        if action not in {"on", "off", "reset"}:
            raise ValueError("Usage: /pro [on|off|reset]")
        control = self.reasoning_mode_control
        if action != "reset" and (control is None or not control.supported):
            raise ValueError(control.reason if control else "Reasoning mode is unavailable.")
        self.overrides = RunModelOverrides.model_validate(
            {**self.overrides.model_dump(), "reasoning_mode": {"on": "pro", "off": "standard", "reset": None}[action]}
        )
        await self.refresh()
        message = f"Reasoning mode · {self.status.reasoning_mode_description} · configuration unchanged."
        if self.status.reasoning_mode == "pro":
            message += " Pro requested; access, usage and latency depend on the provider."
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
            source = await self.app.current_configuration()
            assert source is not None
            local_roots = tuple(root.path for root in source.projects[project_id].roots)
            thread = await self.app.update_thread_configuration(
                thread_id=selected,
                mutation=ThreadConfigurationMutation(
                    expected_version=thread.configuration.version,
                    patch=ThreadConfigurationPatch(
                        project_id=project_id,
                        local_roots=local_roots,
                        default_environment="workspace",
                    ),
                ),
            )
        if thread.configuration.default_model_id is None:
            await self._restore_project_model(thread.configuration.project_id)
        elif self._model_from_preference:
            # Drop restored model memory, not this terminal's explicit controls.
            self.overrides = self.overrides.model_copy(update={"model_id": None})
            self._model_from_preference = False
            self._model_preference_project_id = None
        # A saved default outranks restored Project memory, not an explicit local choice.
        # All fallible I/O precedes the local selection change.
        self.thread_id = selected
        self.status.restore_usage(totals.combined)
        self.status.context_tokens = usage.latest_request_tokens
        agent = configuration.agents.get(thread.configuration.agent_source.id)
        self.agent_id = thread.configuration.agent_source.id
        if self.overrides.model_id is None:
            default_model_id = thread.configuration.default_model_id or (None if agent is None else agent.model)
            # Historical usage never restores a temporary model selection.
            self.overrides = RunModelOverrides.model_validate(
                {
                    **self.overrides.model_dump(),
                    "thinking": usage.thinking if usage.model_id == default_model_id else None,
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
            interaction.timeout_seconds = configuration.document.tools.interaction_timeout_seconds
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

    def thinking_choices(self) -> tuple[Choice, ...]:
        control = self.thinking_control
        if control is None:
            return ()
        return tuple(
            Choice(item.command, item.label, item.disabled_reason or control.reason or item.description)
            for item in control.options
        )

    async def choices(self, kind: str) -> tuple[Choice, ...]:
        if kind == "model":
            configuration = await self.app.current_configuration()
            if configuration is None:
                return ()
            return (
                Choice("default", "Agent default", "Clear this project's remembered model"),
                Choice("defaults", "Media understanding defaults…", "Global configuration"),
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
            await self.refresh()
            return self.thinking_choices()
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
        mode: GoalMode = "normal",
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
            mode=mode,
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
        mode: GoalMode,
    ) -> str:
        if self.cancel_requested:
            return "Cancelled before admission. No operation was submitted."
        await self.refresh()
        thread_id = await self.ensure_session()
        last_ordinal = -1
        custom_events = CustomEventAssembler()
        # Snapshot before admission; live notifications refresh this committed
        # projection rather than adding potentially overlapping child deltas.
        totals = await self.app.thread_usage(thread_id=thread_id)
        self.status.restore_usage(totals.combined)
        async with self.app.live_events(root_thread_id=thread_id) as subscription:

            async def ingest(event: LiveEvent) -> bool:
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
                records = model_usage(event)
                if records:
                    # Observation commits before publication. Replace from the
                    # ledger so child replay cannot overlap the saved baseline.
                    totals = await self.app.thread_usage(thread_id=thread_id)
                    self.status.restore_usage(totals.combined)
                for sample in root_context_samples(event):
                    if sample.response_ordinal >= last_ordinal:
                        self.status.context_tokens = sample.tokens
                        last_ordinal = sample.response_ordinal
                return bool(records)

            async def consume() -> None:
                try:
                    async for event in subscription:
                        usage_changed = await ingest(event)
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
                        input_surface="tui",
                        mode=mode,
                    )
                    if response is None
                    else await self.app.respond_thread(
                        thread_id=thread_id, response=response, model_overrides=self.overrides
                    )
                )
                self.receipt_id = receipt.receipt_id
                if admitted is not None:
                    admitted()
                self.status.goal = (await self.app.get_root_operation(receipt.receipt_id)).goal
                if self.cancel_requested:
                    await self.app.cancel_root_operation(receipt.receipt_id)
                operation = await self.app.wait_root_operation(receipt.receipt_id)
                self.status.goal = operation.goal
            finally:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
                self.receipt_id = None
                try:
                    for event in subscription.drain_pending():
                        usage_changed = await ingest(event)
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
                self.status.restore_usage(totals.combined)
        usage = await self.app.context_usage(thread_id)
        self.status.context_tokens = usage.latest_request_tokens
        outcome = operation.outcome
        if operation.goal is not None and not operation.goal.active:
            goal = operation.goal
            total = goal.input_tokens + goal.output_tokens
            detail = "Agent-verified" if goal.status == "verified" else goal.status.replace("_", " ")
            warning = "" if goal.status == "verified" else " Task may be incomplete."
            renderer.append(
                f"Goal {detail} at iteration {goal.iteration}/{goal.max_iterations}; {total:,} tokens.{warning}\n",
                kind="notice",
            )
        if outcome is not None and operation.status == RootOperationStatus.completed:
            if not renderer.assistant_seen or renderer.gap:
                if renderer.gap:
                    renderer.append("[Recovered final answer after incomplete live output]\n", kind="notice")
                if outcome.execution.output_omitted:
                    renderer.append(
                        "[Final output exceeds the bounded result projection; use /history to inspect retained messages.]\n",
                        kind="notice",
                    )
                else:
                    renderer.append(str(outcome.execution.output) + "\n", markdown=True)
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
        self,
        message: str | ComposerInput,
        *,
        receipt_id: str | None = None,
        skill_references: tuple[SkillReference, ...] = (),
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
