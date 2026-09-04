from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_ui.errors import AgentUiError, LivePresentationError
from a13n_ui.surfaces import (
    AgentSourceView,
    AgentSummary,
    ApprovalDecision,
    ApprovalRequestView,
    ChildActivityView,
    ChildControlResult,
    ChildExecutionPage,
    ChildExecutionView,
    DecisionBatchView,
    DecisionResponseBatch,
    EnvironmentProfileSummary,
    ExternalRequestView,
    ExternalToolResult,
    LaunchProjectSelected,
    ProjectPathCompletion,
    ProjectPathCompletionPage,
    ProjectSummary,
    QuestionOptionView,
    QuestionResponse,
    QuestionView,
    ReviewView,
    RootActivityState,
    RootActivityView,
    RootOperationStatus,
    RootOperationView,
    RootRunReceipt,
    SelectableResourceSummary,
    SkillCatalogItemView,
    SkillCatalogView,
    SkillReference,
    StructuredQuestionRequestView,
    ThreadConfigurationMutationInput,
    ThreadConfigurationView,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSelectorCatalog,
    ThreadSummary,
    TranscriptPage,
    WorkbenchPage,
    WorkbenchThreadView,
)
from a13n_ui.tui.controller import TerminalController
from a13n_ui.tui.intents import (
    ApplyCompletion,
    ArchiveThread,
    CancelChildExecution,
    CloseCompletions,
    EditDraft,
    ExecuteCommand,
    OpenExternalEditor,
    OpenFocus,
    OpenOverlay,
    OpenReview,
    OpenWorkbench,
    RequestCompletions,
    RetryStartup,
    SearchThreadPicker,
    SelectConfigurationResource,
    SetWorkbenchFilter,
    SteerChildExecution,
    SubmitComposer,
    SubmitDecisionSession,
    SubmitThreadDraft,
    UpdateDecisionDraft,
)
from a13n_ui.tui.models import (
    DecisionAnswerDraft,
    ProjectionHints,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


class _EventStream:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        item = await self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeApp:
    def __init__(self) -> None:
        self.summary_streams: list[_EventStream] = []
        self.focus_streams: list[_EventStream] = []
        self.summary_active = 0
        self.focus_active = 0
        self.max_focus_active = 0
        self.workbench_calls = 0
        self.workbench_requests: list[dict[str, object]] = []
        self.workbench_page = WorkbenchPage(project_id="project-main", rows=(), total=0)
        self.decisions: DecisionBatchView | None = None
        self.children: tuple[ChildExecutionView, ...] = ()
        self.continuation_id = "a" * 64
        self.created: list[str] = []
        self.submitted: list[tuple[str, str]] = []
        self.responses: list[DecisionResponseBatch] = []
        self.child_steers: list[tuple[str, str]] = []
        self.child_cancels: list[str] = []
        self.configuration_mutations: list[ThreadConfigurationMutationInput] = []
        self.metadata_mutations: list[object] = []
        self.configuration_error: AgentUiError | None = None
        self.configuration_version = 1
        self.agent_id = "agent-main"
        self.submit_error: AgentUiError | None = None
        self.response_error: AgentUiError | None = None
        self.next_decisions: DecisionBatchView | None = None
        self.wait_forever = asyncio.Event()

    async def resolve_launch_project(self, directory: Path, *, project_id: str | None = None) -> Any:
        del directory, project_id
        return LaunchProjectSelected(
            directory="/workspace",
            project=ProjectSummary(
                project_id="project-main",
                name="Main",
                position=0,
                roots=("/workspace",),
            ),
        )

    async def workbench(self, **kwargs: object) -> WorkbenchPage:
        self.workbench_calls += 1
        self.workbench_requests.append(kwargs)
        project_id = kwargs.get("project_id")
        assert project_id is None or isinstance(project_id, str)
        return self.workbench_page.model_copy(update={"project_id": project_id})

    async def projects(self) -> tuple[ProjectSummary, ...]:
        return (
            ProjectSummary(
                project_id="project-main",
                name="Main",
                position=0,
                roots=("/workspace",),
            ),
        )

    async def thread_selectors(self) -> ThreadSelectorCatalog:
        return _selectors()

    async def complete_project_paths(self, **kwargs: object) -> ProjectPathCompletionPage:
        del kwargs
        return ProjectPathCompletionPage(
            project_id="project-main",
            query="src",
            items=(
                ProjectPathCompletion(
                    project_id="project-main",
                    mount="root",
                    relative_path="src/main.py",
                    kind="file",
                    display="root:src/main.py",
                ),
            ),
        )

    async def skill_catalog(self, **kwargs: object) -> SkillCatalogView:
        del kwargs
        return SkillCatalogView(
            catalog_id="c" * 64,
            context_kind="draft",
            items=(
                SkillCatalogItemView(
                    item_id="d" * 64,
                    name="review",
                    description="Review code",
                    source_id="project-main",
                    logical_path="skills/review/SKILL.md",
                ),
            ),
        )

    @asynccontextmanager
    async def summary_events(self, **kwargs: object) -> AsyncIterator[_EventStream]:
        del kwargs
        stream = _EventStream()
        self.summary_streams.append(stream)
        self.summary_active += 1
        try:
            yield stream
        finally:
            self.summary_active -= 1

    @asynccontextmanager
    async def watch_thread(self, *, root_thread_id: str, child_limit: int = 20) -> AsyncIterator[Any]:
        del child_limit
        stream = _EventStream()
        self.focus_streams.append(stream)
        self.focus_active += 1
        self.max_focus_active = max(self.max_focus_active, self.focus_active)
        try:
            yield SimpleNamespace(
                snapshot=_snapshot(
                    root_thread_id,
                    continuation_id=self.continuation_id,
                    children=self.children,
                    configuration_version=self.configuration_version,
                    agent_id=self.agent_id,
                ),
                events=stream,
            )
        finally:
            self.focus_active -= 1

    async def thread_decisions(self, **kwargs: object) -> DecisionBatchView | None:
        del kwargs
        return self.decisions

    async def get_thread_transcript(self, **kwargs: object) -> TranscriptPage:
        thread_id = kwargs["thread_id"]
        assert isinstance(thread_id, str)
        return TranscriptPage(
            continuation_id=self.continuation_id,
            entries=(),
            total=0,
        )

    async def create_thread(self, **kwargs: object) -> ThreadSummary:
        del kwargs
        self.created.append("thread-new")
        return _snapshot("thread-new").thread.thread

    async def submit_thread(self, *, thread_id: str, prompt: str, **kwargs: object) -> RootRunReceipt:
        del kwargs
        self.submitted.append((thread_id, prompt))
        if self.submit_error is not None:
            raise self.submit_error
        return RootRunReceipt(receipt_id="receipt-1", thread_id=thread_id, submitted_at=NOW)

    async def respond_decisions(
        self,
        *,
        thread_id: str,
        response: DecisionResponseBatch,
        **kwargs: object,
    ) -> RootRunReceipt:
        del kwargs
        self.responses.append(response)
        if self.response_error is not None:
            self.decisions = self.next_decisions
            if self.next_decisions is not None:
                self.continuation_id = self.next_decisions.continuation_id
            raise self.response_error
        return RootRunReceipt(receipt_id="receipt-decision", thread_id=thread_id, submitted_at=NOW)

    async def patch_thread_configuration(self, **kwargs: object) -> ThreadSummary:
        mutation = kwargs["mutation"]
        assert isinstance(mutation, ThreadConfigurationMutationInput)
        self.configuration_mutations.append(mutation)
        if self.configuration_error is not None:
            raise self.configuration_error
        if "agent_id" in mutation.patch.model_fields_set:
            assert mutation.patch.agent_id is not None
            self.agent_id = mutation.patch.agent_id
        self.configuration_version += 1
        return _snapshot(
            str(kwargs["thread_id"]),
            configuration_version=self.configuration_version,
            agent_id=self.agent_id,
        ).thread.thread

    async def update_thread_metadata(self, **kwargs: object) -> ThreadSummary:
        self.metadata_mutations.append(kwargs["mutation"])
        return _snapshot(str(kwargs["thread_id"])).thread.thread

    async def wait_root_operation(self, receipt_id: str, **kwargs: object) -> Any:
        del receipt_id, kwargs
        await self.wait_forever.wait()
        raise AssertionError("wait should be cancelled during controller cleanup")

    async def deferred_review(self, **kwargs: object) -> ReviewView:
        del kwargs
        return ReviewView(lifecycle="pending", kind="diff", title="Pending edit", content="-old\n+new")

    async def child_review(self, **kwargs: object) -> ReviewView:
        del kwargs
        return ReviewView(lifecycle="running", kind="child", title="Child detail", content="working")

    async def steer_child_execution(self, **kwargs: object) -> ChildControlResult:
        execution_id = kwargs["execution_id"]
        message = kwargs["message"]
        assert isinstance(execution_id, str)
        assert isinstance(message, str)
        self.child_steers.append((execution_id, message))
        return ChildControlResult(execution_id=execution_id, accepted=True, enqueue_id="enqueue-1")

    async def cancel_child_execution(self, **kwargs: object) -> ChildControlResult:
        execution_id = kwargs["execution_id"]
        assert isinstance(execution_id, str)
        self.child_cancels.append(execution_id)
        return ChildControlResult(execution_id=execution_id, accepted=True, persisted_status="running")


def _selectors() -> ThreadSelectorCatalog:
    return ThreadSelectorCatalog(
        agents=(
            AgentSummary(
                agent_id="agent-main",
                name="Main Agent",
                model_id="model-main",
                source_path="agents/main.yaml",
            ),
            AgentSummary(
                agent_id="agent-alt",
                name="Alternate Agent",
                model_id="model-alt",
                source_path="agents/alt.yaml",
            ),
        ),
        environments=(
            EnvironmentProfileSummary(
                profile_id="environment-native",
                name="Full Control",
                mode="full-control",
                description="Native host access",
                provider_key="native",
                release_owned=True,
                canonical_host_paths=True,
            ),
        ),
        harness_plugins=(
            SelectableResourceSummary(
                resource_id="plugin-review",
                name="Review Plugin",
                kind="harness_plugin",
                source_path="plugins/review.yaml",
            ),
        ),
        environment_run_extensions=(),
        mcp_servers=(),
    )


def _snapshot(
    thread_id: str,
    *,
    continuation_id: str = "a" * 64,
    children: tuple[ChildExecutionView, ...] = (),
    configuration_version: int = 1,
    agent_id: str = "agent-main",
) -> ThreadFocusSnapshot:
    summary = ThreadSummary(
        thread_id=thread_id,
        created_at=NOW,
        updated_at=NOW,
        metadata_version=1,
        archived=False,
        configuration=ThreadConfigurationView(
            version=configuration_version,
            project_id="project-main",
            agent_source=AgentSourceView(kind="agent", id=agent_id),
            environment_profile_id="environment-native",
        ),
        continuation_state="selected",
        root_activity=RootActivityView(state=RootActivityState.inactive),
    )
    return ThreadFocusSnapshot(
        epoch="live-1",
        cutover_sequence=0,
        thread=ThreadDetail(
            thread=summary,
            continuation_id=continuation_id,
            available_actions=("run", "archive"),
        ),
        children=ChildExecutionPage(executions=children, total=len(children)),
    )


def _factory(app: _FakeApp, exits: list[str] | None = None) -> Callable[[], Any]:
    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        try:
            yield app
        finally:
            if exits is not None:
                exits.append("closed")

    return open_app


def _renderer(states: list[TerminalState]) -> Callable[[TerminalState, ProjectionHints], Any]:
    async def render(state: TerminalState, hints: ProjectionHints) -> None:
        del hints
        states.append(state)

    return render


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_controller_installs_summary_before_startup_queries_and_owns_cleanup(tmp_path: Path) -> None:
    app = _FakeApp()
    exits: list[str] = []
    rendered: list[TerminalState] = []
    controller = TerminalController(
        app_factory=_factory(app, exits),
        render=_renderer(rendered),
        launch_directory=tmp_path,
    )

    await controller.start()

    assert controller.state.lifecycle is TerminalLifecycle.READY
    assert app.summary_active == 1
    assert app.workbench_calls == 1
    assert rendered[-1].lifecycle is TerminalLifecycle.READY

    await controller.close()

    assert app.summary_active == 0
    assert exits == ["closed"]
    assert controller.background_task_count == 0
    assert controller.state.lifecycle is TerminalLifecycle.CLOSING


@pytest.mark.anyio
async def test_controller_replaces_focus_without_overlapping_detailed_subscriptions(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()
    await _wait_until(lambda: controller.state.thread_view("thread-1") is not None)

    await controller.handle(OpenFocus("thread-2"))
    await _wait_until(lambda: controller.state.thread_view("thread-2") is not None)

    assert controller.state.focused_thread_id == "thread-2"
    assert app.focus_active == 1
    assert app.max_focus_active == 1

    await controller.close()
    assert app.focus_active == 0


@pytest.mark.anyio
async def test_summary_gap_reinstalls_subscription_before_full_refresh(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()

    await app.summary_streams[0].queue.put(LivePresentationError("summary gap", code="summary_cursor_expired"))
    await _wait_until(lambda: len(app.summary_streams) == 2 and app.workbench_calls == 2)

    assert app.summary_active == 1
    await controller.close()


@pytest.mark.anyio
async def test_startup_failure_can_retry_with_a_fresh_app_context(tmp_path: Path) -> None:
    app = _FakeApp()
    attempts = 0

    @asynccontextmanager
    async def open_app() -> AsyncIterator[Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AgentUiError("not ready", code="app_not_ready")
        yield app

    controller = TerminalController(
        app_factory=open_app,
        render=_renderer([]),
        launch_directory=tmp_path,
    )

    await controller.start()
    assert controller.state.lifecycle is TerminalLifecycle.FAILED

    await controller.handle(RetryStartup())
    assert controller.state.lifecycle is TerminalLifecycle.READY
    assert attempts == 2

    await controller.close()


@pytest.mark.anyio
async def test_first_submission_rekeys_and_restores_draft_after_admission_failure(tmp_path: Path) -> None:
    app = _FakeApp()
    app.submit_error = AgentUiError("admission rejected", code="run_admission_rejected")
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(EditDraft(key="new", text="preserve me", cursor=11))

    await controller.handle(SubmitComposer("new"))

    assert app.created == ["thread-new"]
    assert app.submitted == [("thread-new", "preserve me")]
    assert controller.state.focused_thread_id == "thread-new"
    restored = controller.state.draft("thread-new")
    assert restored is not None
    assert restored.text == "preserve me"
    assert controller.state.notices[-1].code == "run_admission_rejected"

    await controller.close()


@pytest.mark.anyio
async def test_decision_session_submits_complete_batch_and_opens_safe_review(tmp_path: Path) -> None:
    app = _FakeApp()
    app.decisions = DecisionBatchView(
        continuation_id="a" * 64,
        requests=(
            ApprovalRequestView(
                request_id="request-1",
                tool_name="edit_file",
                arguments={"path": "README.md"},
            ),
        ),
    )
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()

    def decision_ready() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.decision_session is not None

    await _wait_until(decision_ready)
    await controller.handle(
        UpdateDecisionDraft(
            thread_id="thread-1",
            draft=DecisionAnswerDraft(request_id="request-1", action="approve"),
        )
    )

    await controller.handle(SubmitDecisionSession("thread-1"))

    assert len(app.responses) == 1
    response = app.responses[0]
    assert response.expected_continuation_id == "a" * 64
    decision = response.responses[0]
    assert isinstance(decision, ApprovalDecision)
    assert decision.approved is True

    await controller.handle(
        OpenReview(
            kind="deferred",
            thread_id="thread-1",
            continuation_id="a" * 64,
            request_id="request-1",
        )
    )
    assert controller.state.review is not None
    assert controller.state.review.view.kind == "diff"
    assert controller.state.overlays[-1].kind == "review"

    await controller.close()


@pytest.mark.anyio
async def test_controller_validates_and_builds_every_decision_response_kind(tmp_path: Path) -> None:
    app = _FakeApp()
    question_text = "Choose storage"
    app.decisions = DecisionBatchView(
        continuation_id="a" * 64,
        requests=(
            StructuredQuestionRequestView(
                request_id="question-1",
                tool_name="ask_user_question",
                questions=(
                    QuestionView(
                        header="Storage",
                        question=question_text,
                        options=(
                            QuestionOptionView(label="Redis", description="Shared"),
                            QuestionOptionView(label="Memory", description="Local"),
                        ),
                    ),
                ),
            ),
            ApprovalRequestView(
                request_id="approval-1",
                tool_name="write_file",
                arguments={"path": "old.txt"},
            ),
            ExternalRequestView(
                request_id="external-1",
                tool_name="external_lookup",
            ),
        ),
    )
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()

    def decision_ready() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.decision_session is not None

    await _wait_until(decision_ready)
    await controller.handle(
        UpdateDecisionDraft(
            "thread-1",
            DecisionAnswerDraft(
                request_id="question-1",
                question_answers=((question_text, ("Redis",)),),
            ),
        )
    )
    await controller.handle(
        UpdateDecisionDraft(
            "thread-1",
            DecisionAnswerDraft(
                request_id="approval-1",
                action="override",
                payload_text='{"path":"new.txt"}',
            ),
        )
    )
    await controller.handle(
        UpdateDecisionDraft(
            "thread-1",
            DecisionAnswerDraft(
                request_id="external-1",
                action="deny",
                denial_reason="Not available in this environment",
            ),
        )
    )

    await controller.handle(SubmitDecisionSession("thread-1"))

    response = app.responses[0]
    question = response.responses[0]
    approval = response.responses[1]
    external = response.responses[2]
    assert isinstance(question, QuestionResponse)
    assert question.answers == {question_text: "Redis"}
    assert isinstance(approval, ApprovalDecision)
    assert approval.override_arguments == {"path": "new.txt"}
    assert isinstance(external, ExternalToolResult)
    assert external.denied

    await controller.close()


@pytest.mark.anyio
async def test_stale_decision_conflict_preserves_answers_and_requires_new_review(tmp_path: Path) -> None:
    app = _FakeApp()
    original = ApprovalRequestView(request_id="request-old", tool_name="write_file")
    app.decisions = DecisionBatchView(continuation_id="a" * 64, requests=(original,))
    app.next_decisions = DecisionBatchView(
        continuation_id="b" * 64,
        requests=(ApprovalRequestView(request_id="request-new", tool_name="write_file"),),
    )
    app.response_error = AgentUiError("continuation changed", code="thread_continuation_conflict")
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()

    def original_ready() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.decision_session is not None

    await _wait_until(original_ready)
    await controller.handle(
        UpdateDecisionDraft(
            "thread-1",
            DecisionAnswerDraft(request_id="request-old", action="approve"),
        )
    )
    await controller.handle(SubmitDecisionSession("thread-1"))

    def refreshed() -> bool:
        view = controller.state.thread_view("thread-1")
        return (
            view is not None and view.decision_session is not None and view.decision_session.continuation_id == "b" * 64
        )

    await _wait_until(refreshed)
    view = controller.state.thread_view("thread-1")
    assert view is not None
    assert view.stale_decision_session is not None
    assert view.stale_decision_session.answer("request-old").action == "approve"
    assert view.decision_session is not None
    assert view.decision_session.answer("request-new").action is None
    assert controller.state.notices[-1].code == "thread_continuation_conflict"

    await controller.close()


def _running_child() -> ChildExecutionView:
    return ChildExecutionView(
        execution_id="execution-1",
        root_thread_id="thread-1",
        parent_thread_id="thread-1",
        child_thread_id="thread-child",
        child_run_id="run-child",
        segment_index=0,
        composition_id="1" * 64,
        subagent_name="explorer",
        child_definition_id="child-definition",
        persisted_status="running",
        local_status="active",
        resumable=False,
        activity=ChildActivityView(sequence=1, output_preview="Inspecting files"),
        available_actions=("wait", "steer", "cancel"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_child_review_exposes_only_advertised_controls_and_refreshes_focus(tmp_path: Path) -> None:
    app = _FakeApp()
    app.children = (_running_child(),)
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        launch_thread_id="thread-1",
    )
    await controller.start()

    def child_ready() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.snapshot is not None and bool(view.snapshot.children.executions)

    await _wait_until(child_ready)
    await controller.handle(OpenReview(kind="child", thread_id="thread-1", execution_id="execution-1"))
    assert controller.state.review is not None
    assert controller.state.review.available_actions == ("wait", "steer", "cancel")

    await controller.handle(
        SteerChildExecution(
            parent_thread_id="thread-1",
            execution_id="execution-1",
            message="Check the tests too",
        )
    )
    assert app.child_steers == [("execution-1", "Check the tests too")]
    assert not controller.state.overlays

    await controller.handle(CancelChildExecution(parent_thread_id="thread-1", execution_id="missing"))
    assert app.child_cancels == []
    assert controller.state.notices[-1].code == "child_control_unavailable"

    await controller.close()


@pytest.mark.anyio
async def test_controller_loads_selectors_and_applies_new_thread_configuration(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()

    await controller.handle(OpenOverlay("configuration", key="agent"))
    assert controller.state.selectors == _selectors()
    await controller.handle(SelectConfigurationResource("agent", "agent-alt"))

    assert controller.state.draft_defaults.agent_id == "agent-alt"
    assert controller.state.draft_defaults.project_id == "project-main"
    assert not controller.state.overlays

    await controller.handle(OpenOverlay("projects"))
    assert controller.state.projects[0].project_id == "project-main"

    await controller.close()


@pytest.mark.anyio
async def test_controller_debounces_and_applies_exact_skill_completion(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(EditDraft(key="new", text="use $rev", cursor=8))
    await controller.handle(
        RequestCompletions(
            key="new",
            kind="skill",
            query="rev",
            token_start=4,
            token_end=8,
        )
    )
    await _wait_until(lambda: controller.state.completion is not None)
    completion = controller.state.completion
    assert completion is not None
    assert completion.skills is not None
    item = completion.skills.items[0]
    reference = SkillReference(
        catalog_id=completion.skills.catalog_id,
        item_id=item.item_id,
        name=item.name,
    )

    await controller.handle(
        ApplyCompletion(
            key="new",
            token_start=4,
            token_end=8,
            replacement="$review",
            skill_reference=reference,
        )
    )

    draft = controller.state.draft("new")
    assert draft is not None
    assert draft.text == "use $review"
    assert draft.skill_references == (reference,)
    assert controller.state.completion is None

    await controller.close()


@pytest.mark.anyio
async def test_configuration_conflict_refreshes_state_and_preserves_retry_intent(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(OpenFocus("thread-1"))

    def focus_loaded() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.detail is not None

    await _wait_until(focus_loaded)
    await controller.handle(OpenOverlay("configuration", key="agent"))
    app.configuration_version = 2
    app.configuration_error = AgentUiError(
        "Expected configuration version 1, found 2.",
        code="thread_configuration_conflict",
    )

    await controller.handle(SelectConfigurationResource("agent", "agent-alt"))

    conflict = controller.state.configuration_conflict
    assert conflict is not None
    assert conflict.resource_id == "agent-alt"
    assert conflict.intended_selected
    assert controller.state.overlays[-1].kind == "configuration"

    def conflict_refresh_loaded() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.detail is not None and view.detail.thread.configuration.version == 2

    await _wait_until(conflict_refresh_loaded)
    view = controller.state.thread_view("thread-1")
    assert view is not None and view.detail is not None
    assert view.detail.thread.configuration.version == 2
    assert view.detail.thread.configuration.agent_source.id == "agent-main"

    app.configuration_error = None
    await controller.handle(SelectConfigurationResource("agent", "agent-alt"))

    def retry_loaded() -> bool:
        view = controller.state.thread_view("thread-1")
        return view is not None and view.detail is not None and view.detail.thread.configuration.version == 3

    await _wait_until(retry_loaded)

    assert controller.state.configuration_conflict is None
    assert not controller.state.overlays
    view = controller.state.thread_view("thread-1")
    assert view is not None and view.detail is not None
    assert view.detail.thread.configuration.version == 3
    assert view.detail.thread.configuration.agent_source.id == "agent-alt"

    await controller.close()


@pytest.mark.anyio
async def test_external_editor_failure_preserves_complete_draft(tmp_path: Path) -> None:
    app = _FakeApp()

    async def failing_editor(_key: str, _draft: object) -> str:
        raise AgentUiError("Editor exited with status 2.", code="editor_failed")

    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        editor_callback=failing_editor,
    )
    await controller.start()
    reference = SkillReference(catalog_id="c" * 64, item_id="d" * 64, name="review")
    await controller.handle(
        EditDraft(
            key="new",
            text="@root:src/main.py inspect $review",
            cursor=33,
            project_paths=("root:src/main.py",),
            skill_references=(reference,),
        )
    )

    await controller.handle(OpenExternalEditor("new"))

    draft = controller.state.draft("new")
    assert draft is not None
    assert draft.text == "@root:src/main.py inspect $review"
    assert draft.project_paths == ("root:src/main.py",)
    assert draft.skill_references == (reference,)
    assert controller.state.notices[-1].code == "editor_failed"

    await controller.close()


@pytest.mark.anyio
async def test_archiving_focused_thread_returns_to_workbench(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(OpenFocus("thread-1"))

    await controller.handle(ArchiveThread(thread_id="thread-1", expected_version=1))

    assert len(app.metadata_mutations) == 1
    assert controller.state.mode is TerminalMode.WORKBENCH
    assert controller.state.focused_thread_id is None
    assert controller.state.previous_focused_thread_id is None
    assert app.focus_active == 0

    await controller.close()


@pytest.mark.anyio
async def test_external_editor_discards_result_when_draft_revision_changes(tmp_path: Path) -> None:
    app = _FakeApp()
    controller: TerminalController

    async def racing_editor(_key: str, draft: object) -> str:
        assert draft is not None
        await controller.handle(EditDraft(key="new", text="newer local draft", cursor=17))
        return "stale editor result"

    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        editor_callback=racing_editor,
    )
    await controller.start()
    await controller.handle(EditDraft(key="new", text="original draft", cursor=14))
    initial_workbench_calls = app.workbench_calls

    await controller.handle(OpenExternalEditor("new"))

    draft = controller.state.draft("new")
    assert draft is not None
    assert draft.text == "newer local draft"
    assert controller.state.notices[-1].code == "editor_draft_changed"
    assert app.workbench_calls == initial_workbench_calls + 1

    await controller.close()


@pytest.mark.anyio
async def test_workbench_does_not_queue_prompt_behind_preparing_operation(tmp_path: Path) -> None:
    app = _FakeApp()
    thread = _snapshot("thread-1").thread.thread
    receipt = RootRunReceipt(receipt_id="receipt-preparing", thread_id="thread-1", submitted_at=NOW)
    row = WorkbenchThreadView(
        thread=thread,
        project_name="Main",
        agent_name="Main Agent",
        environment_name="Full Control",
        latest_operation=RootOperationView(
            receipt=receipt,
            status=RootOperationStatus.preparing,
            available_actions=("wait", "cancel"),
        ),
        available_actions=("open", "wait", "cancel"),
    )
    app.workbench_page = WorkbenchPage(project_id="project-main", rows=(row,), total=1)
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(EditDraft(key="thread-1", text="queue this", cursor=10))

    await controller.handle(SubmitThreadDraft("thread-1"))

    assert app.submitted == []
    draft = controller.state.draft("thread-1")
    assert draft is not None
    assert draft.text == "queue this"
    assert controller.state.notices[-1].code == "thread_input_unavailable"

    await controller.close()


@pytest.mark.anyio
async def test_thread_picker_preserves_query_across_project_scope_change(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(OpenOverlay("threads"))
    await controller.handle(SearchThreadPicker("failure"))
    await controller.handle(OpenOverlay("projects"))

    await controller.handle(SetWorkbenchFilter(None))

    assert controller.state.overlays[-1].kind == "threads"
    assert controller.state.thread_picker_query == "failure"
    assert app.workbench_requests[-1]["project_id"] is None
    assert app.workbench_requests[-1]["query"] == "failure"

    await controller.close()


@pytest.mark.anyio
async def test_closing_pending_completion_prevents_it_from_reopening(tmp_path: Path) -> None:
    app = _FakeApp()
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
    )
    await controller.start()
    await controller.handle(EditDraft(key="new", text="use $rev", cursor=8))
    await controller.handle(
        RequestCompletions(
            key="new",
            kind="skill",
            query="rev",
            token_start=4,
            token_end=8,
        )
    )

    await controller.handle(CloseCompletions())
    await asyncio.sleep(0.2)

    assert controller.state.completion is None

    await controller.close()


@pytest.mark.anyio
async def test_workbench_path_completion_uses_selected_row_project_without_focus(tmp_path: Path) -> None:
    app = _FakeApp()
    thread = _snapshot("thread-1").thread.thread
    row = WorkbenchThreadView(
        thread=thread,
        project_name="Main",
        agent_name="Main Agent",
        environment_name="Full Control",
    )
    app.workbench_page = WorkbenchPage(project_id="project-main", rows=(row,), total=1)
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        open_workbench=True,
    )
    await controller.start()
    assert controller.state.thread_view("thread-1") is None
    await controller.handle(EditDraft(key="thread-1", text="inspect @src", cursor=12))

    await controller.handle(
        RequestCompletions(
            key="thread-1",
            kind="path",
            query="src",
            token_start=8,
            token_end=12,
        )
    )
    await _wait_until(lambda: controller.state.completion is not None)

    completion = controller.state.completion
    assert completion is not None
    assert completion.paths is not None
    assert completion.paths.project_id == "project-main"

    await controller.close()


@pytest.mark.anyio
async def test_workbench_slash_command_uses_selected_thread_context(tmp_path: Path) -> None:
    app = _FakeApp()
    selected = _snapshot("thread-selected").thread.thread
    row = WorkbenchThreadView(
        thread=selected,
        project_name="Main",
        agent_name="Main Agent",
        environment_name="Full Control",
    )
    app.workbench_page = WorkbenchPage(project_id="project-main", rows=(row,), total=1)
    edited_keys: list[str] = []

    async def editor(key: str, _draft: object) -> str:
        edited_keys.append(key)
        return "selected draft"

    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        open_workbench=True,
        editor_callback=editor,
    )
    await controller.start()
    await controller.handle(OpenFocus("thread-previous"))
    await _wait_until(lambda: controller.state.thread_view("thread-previous") is not None)
    await controller.handle(OpenWorkbench())
    await controller.handle(EditDraft(key="thread-selected", text="/editor", cursor=7))

    await controller.handle(ExecuteCommand("editor", draft_key="thread-selected"))

    assert edited_keys == ["thread-selected"]
    assert controller.state.mode is TerminalMode.WORKBENCH
    assert controller.state.previous_focused_thread_id == "thread-previous"
    draft = controller.state.draft("thread-selected")
    assert draft is not None
    assert draft.text == "selected draft"

    await controller.close()


@pytest.mark.anyio
async def test_workbench_configuration_command_mutates_selected_thread(tmp_path: Path) -> None:
    app = _FakeApp()
    selected = _snapshot("thread-selected").thread.thread
    row = WorkbenchThreadView(
        thread=selected,
        project_name="Main",
        agent_name="Main Agent",
        environment_name="Full Control",
    )
    app.workbench_page = WorkbenchPage(project_id="project-main", rows=(row,), total=1)
    controller = TerminalController(
        app_factory=_factory(app),
        render=_renderer([]),
        launch_directory=tmp_path,
        open_workbench=True,
    )
    await controller.start()

    await controller.handle(ExecuteCommand("agent", context_key="thread-selected"))
    assert controller.state.overlays[-1].context_key == "thread-selected"
    await controller.handle(SelectConfigurationResource("agent", "agent-alt"))

    assert len(app.configuration_mutations) == 1
    mutation = app.configuration_mutations[0]
    assert mutation.expected_version == 1
    assert mutation.patch.agent_id == "agent-alt"
    assert controller.state.mode is TerminalMode.WORKBENCH
    assert not controller.state.overlays

    await controller.close()
