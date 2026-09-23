"""Detached bounded projections used by interactive Harness UI surfaces."""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from a13n_harness.capabilities import AskUserQuestionRequest
from anyio import to_thread
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.tools import DeferredToolRequests

from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, BUILTIN_SKILLS_ROOT, BUILTIN_SKILLS_SOURCE_ID
from a13n_harness_ui.environment_profiles import built_in_environment_profile
from a13n_harness_ui.errors import AppStateError, ThreadError
from a13n_harness_ui.media_understanding import environment_media_kinds
from a13n_harness_ui.model_controls import describe_model_controls
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import LocalStore
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import (
    ActivitySummary,
    AgentSummary,
    ApprovalRequestView,
    ChildStatusCounts,
    DecisionBatchView,
    EnvironmentProfileSummary,
    ExternalRequestView,
    LaunchProjectAmbiguous,
    LaunchProjectResolution,
    LaunchProjectSelected,
    LaunchProjectUnmatched,
    ModelSummary,
    NewThreadDefaults,
    NotePage,
    PendingDecisionSummary,
    ProjectPathCompletion,
    ProjectPathCompletionPage,
    ProjectSummary,
    QuestionOptionView,
    QuestionView,
    ReviewView,
    RootActivityState,
    RootOperationView,
    SelectableResourceSummary,
    SkillCatalogItemView,
    SkillCatalogView,
    SkillReference,
    StructuredQuestionRequestView,
    TaskPage,
    ThreadActivityPage,
    ThreadActivityView,
    ThreadSelectorCatalog,
    ThreadSummary,
)
from a13n_harness_ui.thread_projection import ThreadProjectionService
from a13n_harness_ui.thread_service import RootThreadDefaults, resolve_thread_configuration

_MAX_PATH_SCAN = 10_000
_MAX_SKILLS = 512
_MAX_SKILL_ENTRIES_PER_ROOT = 256
_JSON_ADAPTER = TypeAdapter(JsonValue)


class TerminalProjectionService:
    """Build cohesive terminal views without exposing App internals."""

    def __init__(
        self,
        *,
        store: LocalStore,
        configurations: CompositionAcceptanceService,
        threads: ThreadProjectionService,
        root_runs: RootRunCoordinator,
        children: HarnessUiSubagentOperator,
        configuration_path: Path | None,
    ) -> None:
        self._store = store
        self._configurations = configurations
        self._threads = threads
        self._root_runs = root_runs
        self._children = children
        self._configuration_path = configuration_path
        self._active_skill_catalogs: OrderedDict[str, SkillCatalogView] = OrderedDict()

    async def resolve_launch_project(
        self,
        directory: Path,
        *,
        project_id: str | None = None,
    ) -> LaunchProjectResolution:
        source = await self._required_configuration()
        normalized = await to_thread.run_sync(_normalize_directory, directory)
        projects = await self._threads.projects()
        by_id = {item.project_id: item for item in projects}
        configuration_path = None if self._configuration_path is None else os.fspath(self._configuration_path)
        if project_id is not None:
            selected = by_id.get(project_id)
            if selected is None:
                raise ThreadError("The selected launch Project is unavailable.", code="project_missing")
            return LaunchProjectSelected(
                directory=os.fspath(normalized),
                project=selected,
                configuration_path=configuration_path,
            )

        matches: list[tuple[int, ProjectSummary]] = []
        for project in source.projects.values():
            if not project.roots:
                continue
            first_root = Path(project.roots[0].path)
            if _is_relative_to(normalized, first_root):
                matches.append((len(first_root.parts), by_id[project.id]))
        if not matches:
            return LaunchProjectUnmatched(
                directory=os.fspath(normalized),
                configuration_path=configuration_path,
            )
        specificity = max(item[0] for item in matches)
        selected = tuple(
            sorted(
                (project for length, project in matches if length == specificity),
                key=lambda item: (item.position, item.project_id),
            )
        )
        if len(selected) == 1:
            return LaunchProjectSelected(
                directory=os.fspath(normalized),
                project=selected[0],
                configuration_path=configuration_path,
            )
        return LaunchProjectAmbiguous(
            directory=os.fspath(normalized),
            projects=selected,
            configuration_path=configuration_path,
        )

    async def thread_activity(
        self,
        *,
        project_id: str | None,
        project_scope: Literal["all", "projectless", "unavailable"] = "all",
        query: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        include_active: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadActivityPage:
        source = await self._required_configuration()
        if project_id is not None and project_scope != "all":
            raise ThreadError("Choose a Project or a Project scope.", code="thread_page_invalid")
        unavailable = None
        if project_scope == "unavailable":
            recency = await self._store.threads.project_recency(include_archived=include_archived or archived_only)
            unavailable = tuple(sorted(set(recency) - source.projects.keys()))
        active_ids = await self._root_runs.active_thread_ids() if include_active else ()
        active_threads = []
        if include_active and active_ids and cursor is None:
            active_cursor = None
            while True:
                active_page = await self._threads.list_threads(
                    query=query,
                    project_id=project_id,
                    projectless=project_scope == "projectless",
                    project_ids=unavailable,
                    include_archived=include_archived,
                    archived_only=archived_only,
                    sort="touched",
                    active_only=True,
                    active_thread_ids=active_ids,
                    cursor=active_cursor,
                    limit=100,
                )
                active_threads.extend(active_page.threads)
                active_cursor = active_page.next_cursor
                if active_cursor is None:
                    break
        page = await self._threads.list_threads(
            query=query,
            project_id=project_id,
            projectless=project_scope == "projectless",
            project_ids=unavailable,
            include_archived=include_archived,
            archived_only=archived_only,
            sort="touched",
            active_only=False if include_active else None,
            active_thread_ids=active_ids,
            cursor=cursor,
            limit=limit,
        )
        active_total = len(active_threads)
        if include_active and active_ids and cursor is not None:
            # Preserve the scoped total on later pages without projecting active rows.
            # The repository returns only bounded metadata alongside its filtered count.
            _, active_total = await self._store.threads.list(
                query=query,
                project_id=project_id,
                projectless=project_scope == "projectless",
                project_ids=unavailable,
                include_archived=include_archived,
                archived_only=archived_only,
                thread_ids=active_ids,
                limit=1,
            )
        rows = await self._activity_rows((*active_threads, *page.threads), source)
        return ThreadActivityPage(
            project_id=project_id,
            active_rows=rows[: len(active_threads)],
            rows=rows[len(active_threads) :],
            total=page.total + active_total,
            next_cursor=page.next_cursor,
        )

    async def lookup_thread_activity(self, thread_ids: tuple[str, ...]) -> tuple[ThreadActivityView, ...]:
        source = await self._required_configuration()
        page = await self._threads.lookup_threads(thread_ids)
        return await self._activity_rows(page.threads, source)

    async def _activity_rows(
        self, threads: tuple[ThreadSummary, ...], source: LoadedHarnessUiConfiguration
    ) -> tuple[ThreadActivityView, ...]:
        thread_ids = tuple(item.thread_id for item in threads)
        latest = await self._root_runs.latest_many(thread_ids)
        counts, running = await self._store.child_executions.status_counts_for_roots(thread_ids)
        active_child_ids = await self._children.active_execution_ids()
        read_models = await self._store.threads.read_models(thread_ids)
        pending: dict[str, PendingDecisionSummary] = {}
        retained_activity: dict[str, ActivitySummary] = {}
        for thread_id, read_model in read_models.items():
            summary = _pending_summary(read_model.deferred_requests)
            if summary is not None:
                pending[thread_id] = summary
            activity = read_model.latest_activity
            if activity is not None:
                retained_activity[thread_id] = ActivitySummary(
                    kind=activity.kind, text=activity.text, occurred_at=activity.occurred_at
                )

        rows: list[ThreadActivityView] = []
        for thread in threads:
            project = (
                source.projects.get(thread.configuration.project_id)
                if thread.configuration.project_id is not None
                else None
            )
            agent = source.agents.get(thread.configuration.agent_source.id)
            profile = built_in_environment_profile(thread.configuration.environment_profile_id)
            custom_profile = source.environment_profiles.get(thread.configuration.environment_profile_id)
            environment_name = (
                profile.name
                if profile is not None
                else custom_profile.name
                if custom_profile is not None
                else thread.configuration.environment_profile_id
            )
            status_counts = counts.get(thread.thread_id, {})
            running_ids = running.get(thread.thread_id, ())
            active = sum(1 for execution_id in running_ids if execution_id in active_child_ids)
            child_counts = ChildStatusCounts(
                running=status_counts.get("running", 0),
                succeeded=status_counts.get("succeeded", 0),
                failed=status_counts.get("failed", 0),
                cancelled=status_counts.get("cancelled", 0),
                lost=status_counts.get("lost", 0),
                active=active,
                unavailable=len(running_ids) - active,
            )
            operation = latest.get(thread.thread_id)
            decision = pending.get(thread.thread_id)
            actions: list[Literal["open", "archive", "respond", "wait", "steer", "cancel"]] = ["open"]
            if thread.root_activity.state is RootActivityState.inactive:
                if decision is not None:
                    actions.append("respond")
                if not thread.archived:
                    actions.append("archive")
            else:
                actions.extend(thread.root_activity.available_actions)
            rows.append(
                ThreadActivityView(
                    thread=thread,
                    project_name=project.name
                    if project is not None
                    else thread.configuration.project_id or "No project",
                    agent_name=agent.name if agent is not None else thread.configuration.agent_source.id,
                    environment_name=environment_name,
                    pending_decision=decision,
                    latest_operation=operation,
                    children=child_counts,
                    latest_activity=_latest_activity(
                        thread,
                        decision,
                        operation,
                        child_counts,
                        retained_activity.get(thread.thread_id),
                    ),
                    available_actions=tuple(dict.fromkeys(actions)),
                )
            )
        return tuple(rows)

    async def note_page(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
    ) -> NotePage:
        """Full note values, bounded by count and UTF-8 bytes, from the selected checkpoint."""
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        continuation_id = thread.continuation.logical_digest if thread.continuation is not None else None
        if expected_continuation_id is not None and continuation_id != expected_continuation_id:
            raise ThreadError("The selected continuation changed.", code="thread_continuation_conflict")
        if thread.continuation is None:
            return NotePage()
        return (await self._threads.inspection(thread)).notes

    async def task_page(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        limit: int = 100,
    ) -> TaskPage:
        if not 1 <= limit <= 256:
            raise ThreadError("Task page is outside supported bounds.", code="task_page_invalid")
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        continuation_id = thread.continuation.logical_digest if thread.continuation is not None else None
        if expected_continuation_id is not None and continuation_id != expected_continuation_id:
            raise ThreadError("The selected continuation changed.", code="thread_continuation_conflict")
        if thread.continuation is None:
            return TaskPage(continuation_id=None)
        page = (await self._threads.inspection(thread)).tasks
        return page.model_copy(update={"tasks": page.tasks[:limit], "omitted": max(0, page.total - limit)})

    async def decisions(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
    ) -> DecisionBatchView | None:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        continuation_id = thread.continuation.logical_digest if thread.continuation is not None else None
        if expected_continuation_id is not None and continuation_id != expected_continuation_id:
            raise ThreadError("The selected continuation changed.", code="thread_continuation_conflict")
        if thread.continuation is None or continuation_id is None:
            return None
        if thread.read_model is None:
            raise ThreadError("Decision query data is not available yet.", code="thread_read_model_unavailable")
        requests = thread.read_model.deferred_requests
        if requests is None or (not requests.calls and not requests.approvals):
            return None
        projected = []
        for request in requests.calls:
            raw_metadata = requests.metadata.get(request.tool_call_id)
            metadata = _json_mapping(raw_metadata)
            metadata_omitted = raw_metadata is not None and metadata is None
            if request.tool_name == "ask_user_question":
                try:
                    questions = AskUserQuestionRequest.model_validate(request.args_as_dict())
                except ValidationError as exc:
                    raise ThreadError(
                        "A structured user question is invalid.",
                        code="thread_deferred_request_invalid",
                    ) from exc
                projected.append(
                    StructuredQuestionRequestView(
                        request_id=request.tool_call_id,
                        tool_name=request.tool_name,
                        questions=tuple(
                            QuestionView(
                                question=item.question,
                                header=item.header,
                                options=tuple(
                                    QuestionOptionView(label=option.label, description=option.description)
                                    for option in item.options
                                ),
                                multi_select=item.multi_select,
                            )
                            for item in questions.questions
                        ),
                        metadata=metadata,
                        metadata_omitted=metadata_omitted,
                    )
                )
            else:
                value, omitted = _bounded_json(request.args)
                projected.append(
                    ExternalRequestView(
                        request_id=request.tool_call_id,
                        tool_name=request.tool_name,
                        arguments=value,
                        arguments_omitted=omitted,
                        metadata=metadata,
                        metadata_omitted=metadata_omitted,
                    )
                )
        for request in requests.approvals:
            value, omitted = _bounded_json(request.args)
            raw_metadata = requests.metadata.get(request.tool_call_id)
            metadata = _json_mapping(raw_metadata)
            # Bound Harness approvals authorize the captured arguments, not edits.
            # Read the original evidence even when its display projection is omitted.
            bound = isinstance(raw_metadata, dict) and "a13n.harness.tool-approval" in raw_metadata
            projected.append(
                ApprovalRequestView(
                    request_id=request.tool_call_id,
                    tool_name=request.tool_name,
                    arguments=value,
                    arguments_omitted=omitted,
                    metadata=metadata,
                    metadata_omitted=raw_metadata is not None and metadata is None,
                    override_allowed=not bound and not omitted,
                )
            )
        expires_at = await self._root_runs.interaction_expiry(thread_id, continuation_id)
        return DecisionBatchView(
            continuation_id=continuation_id,
            requests=tuple(projected),
            expires_at=expires_at,
            server_time=datetime.now(UTC) if expires_at is not None else None,
        )

    async def selectors(self, environments: tuple[EnvironmentProfileSummary, ...]) -> ThreadSelectorCatalog:
        source = await self._required_configuration()
        paths = {
            item.resource_id: item.relative_path
            for item in await self._store.configurations.resources(source.source_digest)
        }
        return ThreadSelectorCatalog(
            media_understanding=source.document.media_understanding.selections(),
            media_understanding_environment=environment_media_kinds(),
            agents=tuple(
                AgentSummary(
                    agent_id=item.id,
                    name=item.name,
                    model_id=item.model,
                    source_path=paths.get(item.id, "a13n-harness-ui.yaml"),
                )
                for item in sorted(source.agents.values(), key=lambda item: (item.name.casefold(), item.id))
            ),
            models=tuple(
                ModelSummary(
                    model_id=item.id,
                    name=item.name,
                    route=item.route,
                    **describe_model_controls(item.route, item.settings).model_dump(),
                    media_capabilities=item.media_capabilities(),
                )
                for item in sorted(source.models.values(), key=lambda item: (item.name.casefold(), item.id))
            ),
            environments=environments,
            harness_plugins=tuple(
                SelectableResourceSummary(
                    resource_id=item.id,
                    name=item.name,
                    kind="harness_plugin",
                    implementation_key=item.plugin_key,
                    source_path=paths.get(item.id, "a13n-harness-ui.yaml"),
                )
                for item in sorted(source.harness_plugins.values(), key=lambda item: (item.name.casefold(), item.id))
            ),
            environment_run_extensions=tuple(
                SelectableResourceSummary(
                    resource_id=item.id,
                    name=item.name,
                    kind="environment_run_extension",
                    implementation_key=item.extension_key,
                    source_path=paths.get(item.id, "a13n-harness-ui.yaml"),
                )
                for item in sorted(
                    source.environment_run_extensions.values(),
                    key=lambda item: (item.name.casefold(), item.id),
                )
            ),
            mcp_servers=tuple(
                SelectableResourceSummary(
                    resource_id=item.id,
                    name=item.name,
                    kind="mcp_server",
                    source_path=paths.get(item.id, "a13n-harness-ui.yaml"),
                )
                for item in sorted(source.mcp_servers.values(), key=lambda item: (item.name.casefold(), item.id))
            ),
        )

    async def complete_project_paths(
        self,
        *,
        project_id: str,
        query: str = "",
        limit: int = 50,
    ) -> ProjectPathCompletionPage:
        if len(query) > 512 or not 1 <= limit <= 100:
            raise ThreadError("Project path completion request is invalid.", code="project_path_query_invalid")
        source = await self._required_configuration()
        project = source.projects.get(project_id)
        if project is None:
            raise ThreadError("The selected Project is unavailable.", code="project_missing")
        items, truncated = await to_thread.run_sync(
            _scan_project_paths,
            project_id,
            tuple(Path(item.path) for item in project.roots),
            query,
            limit,
        )
        return ProjectPathCompletionPage(
            project_id=project_id,
            query=query,
            items=items,
            truncated=truncated,
        )

    async def skill_catalog(
        self,
        *,
        thread_id: str | None = None,
        defaults: NewThreadDefaults | None = None,
        local_roots_override: tuple[str, ...] | None = None,
    ) -> SkillCatalogView:
        receipt_id: str | None = None
        context_kind: Literal["draft", "idle", "active"] = "draft"
        thread = None
        if thread_id is not None:
            thread = await self._store.threads.get(thread_id)
            if thread is None:
                raise ThreadError("Thread does not exist.", code="thread_missing")
            operation = await self._root_runs.active(thread_id)
            if operation is None:
                context_kind = "idle"
            else:
                context_kind = "active"
                receipt_id = operation.receipt.receipt_id
                cached = self._active_skill_catalogs.get(receipt_id)
                if cached is not None:
                    self._active_skill_catalogs.move_to_end(receipt_id)
                    return cached.model_copy(deep=True)
        # Active catalogs belong to the admitted Run. Current configuration may
        # have changed (or become unreadable) without changing that live Run.
        source = await self._required_configuration()
        if thread is None:
            selected = defaults or NewThreadDefaults()
            resolved = resolve_thread_configuration(
                source, RootThreadDefaults(**selected.model_dump(exclude_unset=True))
            )
            project_id = resolved.project_id
            agent_id = resolved.agent_source.id
            local_roots = resolved.local_roots
        else:
            project_id = thread.configuration.project_id
            agent_id = thread.configuration.agent_source.id
            local_roots = thread.configuration.local_roots
        if project_id is not None and project_id not in source.projects:
            raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
        if agent_id is None or agent_id not in source.agents:
            raise ThreadError("A Skill catalog requires an available Agent.", code="thread_agent_missing")
        catalog = await to_thread.run_sync(
            _scan_skill_catalog,
            source,
            local_roots if local_roots_override is None else local_roots_override,
            agent_id,
            context_kind,
            thread_id,
            receipt_id,
        )
        if receipt_id is not None:
            self._active_skill_catalogs[receipt_id] = catalog
            self._active_skill_catalogs.move_to_end(receipt_id)
            while len(self._active_skill_catalogs) > 128:
                self._active_skill_catalogs.popitem(last=False)
        return catalog

    async def validate_skill_references(
        self,
        references: tuple[SkillReference, ...],
        *,
        thread_id: str | None = None,
        defaults: NewThreadDefaults | None = None,
    ) -> tuple[str, ...]:
        if not references:
            return ()
        catalog = await self.skill_catalog(thread_id=thread_id, defaults=defaults)
        return self.validate_references_against(catalog, references)

    def validate_references_against(
        self,
        catalog: SkillCatalogView,
        references: tuple[SkillReference, ...],
    ) -> tuple[str, ...]:
        if not references:
            return ()
        names = tuple(item.name for item in references)
        if len(names) != len(set(names)):
            raise ThreadError("Skill references must be unique.", code="skill_reference_invalid")
        for reference in references:
            matches = tuple(item for item in catalog.items if item.name == reference.name)
            if len(matches) != 1:
                raise ThreadError("A selected Skill is unavailable.", code="skill_reference_unavailable")
            # Completion references are previews, not version locks. Resolve an old
            # catalog's name against the fresh (or active Run's pinned) catalog.
            if reference.catalog_id == catalog.catalog_id and reference.item_id != matches[0].item_id:
                raise ThreadError("A selected Skill is unavailable.", code="skill_reference_unavailable")
        return names

    def pin_active_skill_catalog(
        self,
        *,
        receipt_id: str,
        thread_id: str,
        catalog: SkillCatalogView,
    ) -> None:
        pinned = catalog.model_copy(
            update={
                "context_kind": "active",
                "thread_id": thread_id,
                "receipt_id": receipt_id,
            },
            deep=True,
        )
        self._active_skill_catalogs[receipt_id] = pinned
        self._active_skill_catalogs.move_to_end(receipt_id)
        while len(self._active_skill_catalogs) > 128:
            self._active_skill_catalogs.popitem(last=False)

    async def retained_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        position: int,
        tool_call_id: str,
    ) -> ReviewView:
        try:
            entry = await self._threads.transcript_entry(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                position=position,
            )
        except ThreadError as exc:
            if exc.code != "thread_history_position_invalid":
                raise
            return ReviewView(
                lifecycle="unavailable",
                kind="generic",
                title="Tool detail",
                unavailable_reason="The selected transcript entry is unavailable.",
            )
        part = next((item for item in entry.parts if item.tool_call_id == tool_call_id), None)
        if part is None:
            return ReviewView(
                lifecycle="unavailable",
                kind="generic",
                title="Tool detail",
                unavailable_reason="The selected tool call is unavailable.",
            )
        kind: Literal["json", "shell", "task", "child", "diff", "generic"] = "json"
        tool_name = part.tool_name or "Tool"
        lowered = tool_name.casefold()
        if "shell" in lowered:
            kind = "shell"
        elif "task" in lowered:
            kind = "task"
        elif "subagent" in lowered or "child" in lowered:
            kind = "child"
        elif any(token in lowered for token in ("edit", "write", "patch", "diff")):
            kind = "diff"
        return ReviewView(
            lifecycle="closed",
            kind=kind,
            title=tool_name,
            summary=part.text,
            value=part.value,
            truncated=part.text_truncated,
            omitted=part.value_omitted,
        )

    async def deferred_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        request_id: str,
    ) -> ReviewView:
        decisions = await self.decisions(
            thread_id=thread_id,
            expected_continuation_id=expected_continuation_id,
        )
        request = (
            None
            if decisions is None
            else next(
                (item for item in decisions.requests if item.request_id == request_id),
                None,
            )
        )
        if request is None:
            return ReviewView(
                lifecycle="unavailable",
                kind="generic",
                title="Decision detail",
                unavailable_reason="The selected pending request is unavailable.",
            )
        arguments_omitted = (
            request.arguments_omitted if isinstance(request, ApprovalRequestView | ExternalRequestView) else False
        )
        return ReviewView(
            lifecycle="pending",
            kind="json",
            title=request.tool_name,
            value=request.model_dump(mode="json"),
            omitted=arguments_omitted,
        )

    async def task_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        task_id: str,
    ) -> ReviewView:
        page = await self.task_page(
            thread_id=thread_id,
            expected_continuation_id=expected_continuation_id,
            limit=256,
        )
        task = next((item for item in page.tasks if item.task_id == task_id), None)
        if task is None:
            return ReviewView(
                lifecycle="unavailable",
                kind="task",
                title=task_id,
                unavailable_reason="The selected task is unavailable or omitted.",
                omitted=page.omitted > 0,
            )
        return ReviewView(
            lifecycle="closed",
            kind="task",
            title=task.subject,
            summary=task.active_form,
            value=task.model_dump(mode="json"),
        )

    async def child_review(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
    ) -> ReviewView:
        page = await self._children.query_child_executions(
            parent_thread_id=parent_thread_id,
            execution_id=execution_id,
            limit=1,
        )
        if not page.executions:
            return ReviewView(
                lifecycle="unavailable",
                kind="child",
                title=execution_id,
                unavailable_reason="The selected child execution is unavailable.",
            )
        execution = page.executions[0]
        assert execution.activity is not None
        lifecycle: Literal["pending", "running", "closed", "unavailable"] = (
            "running" if execution.persisted_status == "running" else "closed"
        )
        return ReviewView(
            lifecycle=lifecycle,
            kind="child",
            title=execution.subagent_name,
            summary=execution.activity.output_preview or execution.persisted_status,
            value=execution.model_dump(mode="json"),
            truncated=execution.activity.output_truncated,
        )

    async def _required_configuration(self) -> LoadedHarnessUiConfiguration:
        source = await self._configurations.current()
        if source is None:
            raise AppStateError(
                "No accepted Harness UI configuration is available.",
                code="configuration_unavailable",
            )
        return source


def _normalize_directory(value: Path) -> Path:
    path = value.expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("launch directory must be a directory")
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _pending_summary(value: DeferredToolRequests | None) -> PendingDecisionSummary | None:
    if value is None:
        return None
    kinds: set[Literal["question", "approval", "external"]] = set()
    for request in value.calls:
        kinds.add("question" if request.tool_name == "ask_user_question" else "external")
    if value.approvals:
        kinds.add("approval")
    count = len(value.calls) + len(value.approvals)
    if not count:
        return None
    kind: Literal["question", "approval", "external", "mixed"] = next(iter(kinds)) if len(kinds) == 1 else "mixed"
    return PendingDecisionSummary(kind=kind, count=count)


def _latest_activity(
    thread: ThreadSummary,
    decision: PendingDecisionSummary | None,
    operation: RootOperationView | None,
    children: ChildStatusCounts,
    retained: ActivitySummary | None,
) -> ActivitySummary | None:
    if decision is not None:
        return ActivitySummary(kind="decision", text=f"{decision.count} {decision.kind} request(s) awaiting response")
    root_activity = thread.root_activity
    if root_activity.state is not RootActivityState.inactive:
        return ActivitySummary(kind="notice", text=f"Root operation {root_activity.state.value}")
    if operation is not None:
        if operation.failure is not None:
            return ActivitySummary(
                kind="failure",
                text=operation.failure.message[:2048],
                occurred_at=operation.completed_at,
            )
        return ActivitySummary(
            kind="assistant",
            text=f"Root operation {operation.status.value}",
            occurred_at=operation.completed_at,
        )
    if children.active:
        return ActivitySummary(kind="child", text=f"{children.active} child execution(s) active")
    return retained


def _scan_project_paths(
    project_id: str,
    roots: tuple[Path, ...],
    query: str,
    limit: int,
) -> tuple[tuple[ProjectPathCompletion, ...], bool]:
    normalized = query.strip().casefold()
    candidates: list[tuple[tuple[int, int, str], ProjectPathCompletion]] = []
    scanned = 0
    truncated = False
    for index, root in enumerate(roots, start=1):
        mount = "workspace" if index == 1 else f"workspace-{index}"
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(item for item in directories if not (Path(current) / item).is_symlink())
            entries: list[tuple[str, Literal["file", "directory"]]] = [(item, "directory") for item in directories]
            entries.extend((item, "file") for item in sorted(files))
            for name, kind in entries:
                scanned += 1
                if scanned > _MAX_PATH_SCAN:
                    truncated = True
                    break
                path = Path(current) / name
                if path.is_symlink():
                    continue
                relative = path.relative_to(root).as_posix()
                haystack = f"{mount}:{relative}".casefold()
                position = haystack.find(normalized) if normalized else 0
                if position < 0:
                    continue
                display = f"{mount}:{relative}"
                candidates.append(
                    (
                        (position, len(relative), display.casefold()),
                        ProjectPathCompletion(
                            project_id=project_id,
                            mount=mount,
                            relative_path=relative,
                            kind=kind,
                            display=display,
                        ),
                    )
                )
            if truncated:
                break
        if truncated:
            break
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > limit:
        truncated = True
    return tuple(item[1] for item in candidates[:limit]), truncated


def _scan_skill_catalog(
    source: LoadedHarnessUiConfiguration,
    local_roots: tuple[str, ...],
    agent_id: str,
    context_kind: Literal["draft", "idle", "active"],
    thread_id: str | None,
    receipt_id: str | None,
) -> SkillCatalogView:
    agent = source.agents[agent_id]
    capability = next((item for item in agent.capabilities if item.capability == "skills"), None)
    sources: list[tuple[str, Path, bool]] = []
    if capability is not None:
        sources.append((BUILTIN_SKILLS_SOURCE_ID, BUILTIN_SKILLS_ROOT, True))
        user_root = (Path.home() / ".agents" / "skills").resolve(strict=False)
        sources.append(("a13n-harness-ui:user-skills", user_root, False))
        roots = tuple(Path(path) for path in local_roots)
        for index in range(len(roots), 1, -1):
            sources.append(
                (f"a13n-harness-ui:project:workspace-{index}", roots[index - 1] / ".agents" / "skills", False)
            )
        if roots:
            sources.append(("a13n-harness-ui:project:workspace", roots[0] / ".agents" / "skills", False))
        raw_roots = capability.configuration.get("roots", [])
        if isinstance(raw_roots, list):
            for index, value in enumerate(raw_roots, start=1):
                if isinstance(value, str):
                    sources.append(
                        (f"a13n-harness-ui:explicit:{index}", _logical_to_host(value, roots, user_root), True)
                    )

    selected: dict[str, SkillCatalogItemView] = {}
    fingerprints: list[str] = [source.source_digest, *local_roots, agent_id]
    for source_id, root, required in sources:
        if not root.exists():
            if required:
                raise ThreadError("A required Skill root is unavailable.", code="skill_source_unavailable")
            continue
        if not root.is_dir():
            raise ThreadError("A selected Skill root is not a directory.", code="skill_source_unavailable")
        entries = sorted(root.iterdir(), key=lambda item: item.name)
        if len(entries) > _MAX_SKILL_ENTRIES_PER_ROOT:
            raise ThreadError("A Skill root is too large.", code="skill_catalog_too_large")
        candidates = [root] if (root / "SKILL.md").is_file() else []
        candidates.extend(
            item for item in entries if item.is_dir() and not item.is_symlink() and (item / "SKILL.md").is_file()
        )
        for directory in candidates:
            document = directory / "SKILL.md"
            name, description = _read_skill_frontmatter(document)
            stat = document.stat()
            identity = _digest(source_id, os.fspath(directory), name, str(stat.st_mtime_ns), str(stat.st_size))
            fingerprints.append(identity)
            selected[name] = SkillCatalogItemView(
                item_id=identity,
                name=name,
                description=description,
                source_id=source_id,
                logical_path=(
                    f"{BUILTIN_SKILLS_PATH}/{directory.relative_to(BUILTIN_SKILLS_ROOT).as_posix()}"
                    if directory.is_relative_to(BUILTIN_SKILLS_ROOT)
                    else os.fspath(directory)
                ),
            )
    if len(selected) > _MAX_SKILLS:
        raise ThreadError("The selected Skill catalog is too large.", code="skill_catalog_too_large")
    items = tuple(selected[name] for name in sorted(selected, key=str.casefold))
    catalog_id = _digest(*fingerprints, *(item.item_id for item in items))
    return SkillCatalogView(
        catalog_id=catalog_id,
        context_kind=context_kind,
        thread_id=thread_id,
        receipt_id=receipt_id,
        items=items,
    )


def _logical_to_host(value: str, roots: tuple[Path, ...], user_root: Path) -> Path:
    if value == BUILTIN_SKILLS_PATH or value.startswith(f"{BUILTIN_SKILLS_PATH}/"):
        return BUILTIN_SKILLS_ROOT / value.removeprefix(BUILTIN_SKILLS_PATH).lstrip("/")
    if value == "/environment/user-skills" or value.startswith("/environment/user-skills/"):
        return user_root / value.removeprefix("/environment/user-skills").lstrip("/")
    if value == "/workspace" or value.startswith("/workspace/"):
        if not roots:
            raise ThreadError("An explicit Skill root requires a Project.", code="skill_source_unavailable")
        return roots[0] / value.removeprefix("/workspace").lstrip("/")
    for index, root in enumerate(roots[1:], start=2):
        prefix = f"/environment/workspace-{index}"
        if value == prefix or value.startswith(f"{prefix}/"):
            return root / value.removeprefix(prefix).lstrip("/")
    return Path(value).expanduser().resolve(strict=False)


def _read_skill_frontmatter(path: Path) -> tuple[str, str]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            lines = []
            for index, line in enumerate(stream):
                if index >= 256:
                    break
                lines.append(line)
                if index > 0 and line.strip() == "---":
                    break
    except (OSError, UnicodeError) as exc:
        raise ThreadError("A Skill document cannot be read.", code="skill_catalog_invalid") from exc
    if not lines or lines[0].strip() != "---" or len(lines) < 3 or lines[-1].strip() != "---":
        raise ThreadError("A Skill document has invalid frontmatter.", code="skill_catalog_invalid")
    try:
        value = yaml.safe_load("".join(lines[1:-1]))
    except yaml.YAMLError as exc:
        raise ThreadError("A Skill document has invalid frontmatter.", code="skill_catalog_invalid") from exc
    if not isinstance(value, dict):
        raise ThreadError("A Skill document has invalid frontmatter.", code="skill_catalog_invalid")
    name = value.get("name")
    description = value.get("description")
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str) or not description.strip():
        raise ThreadError("A Skill document has invalid frontmatter.", code="skill_catalog_invalid")
    return name.strip()[:256], description.strip()[: 16 * 1024]


def _json_mapping(value: object | None) -> dict[str, JsonValue] | None:
    projected, omitted = _bounded_json(value)
    return projected if not omitted and isinstance(projected, dict) else None


def _bounded_json(value: object) -> tuple[JsonValue | None, bool]:
    try:
        projected = _JSON_ADAPTER.validate_python(value)
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 64 * 1024:
            return None, True
        return projected, False
    except (TypeError, ValueError, ValidationError):
        return None, True


def _digest(*values: str) -> str:
    value = hashlib.sha256()
    for item in values:
        encoded = item.encode("utf-8")
        value.update(len(encoded).to_bytes(8, "big"))
        value.update(encoded)
    return value.hexdigest()


__all__ = ["TerminalProjectionService"]
