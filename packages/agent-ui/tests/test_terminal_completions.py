from __future__ import annotations

from a13n_ui.surfaces import SkillCatalogItemView, SkillCatalogView, SkillReference
from a13n_ui.tui.events import CompletionApplied, CompletionLoaded, DraftChanged, DraftSubmitted
from a13n_ui.tui.models import CompletionState, DraftState, TerminalState
from a13n_ui.tui.reducer import reduce_terminal
from a13n_ui.tui.widgets.composer import completion_request


def test_completion_request_tracks_the_full_token_around_cursor() -> None:
    request = completion_request("inspect @root:src/main.py next", cursor=17, key="thread-1")

    assert request is not None
    assert request.kind == "path"
    assert request.query == "root:src"
    assert request.token_start == 8
    assert request.token_end == 25


def test_exact_skill_completion_updates_text_and_identity_atomically() -> None:
    catalog = SkillCatalogView(
        catalog_id="a" * 64,
        context_kind="idle",
        thread_id="thread-1",
        items=(
            SkillCatalogItemView(
                item_id="b" * 64,
                name="code-review",
                description="Review a focused change",
                source_id="project",
                logical_path="skills/code-review/SKILL.md",
            ),
        ),
    )
    state = TerminalState(drafts=(DraftState(key="thread-1", text="use $code", cursor=9),))
    completion = CompletionState(
        request_version=1,
        key="thread-1",
        kind="skill",
        query="code",
        token_start=4,
        token_end=9,
        skills=catalog,
    )
    state = reduce_terminal(state, CompletionLoaded(completion)).state
    reference = SkillReference(catalog_id="a" * 64, item_id="b" * 64, name="code-review")

    state = reduce_terminal(
        state,
        CompletionApplied(
            key="thread-1",
            token_start=4,
            token_end=9,
            replacement="$code-review",
            skill_reference=reference,
        ),
    ).state

    draft = state.draft("thread-1")
    assert draft is not None
    assert draft.text == "use $code-review"
    assert draft.skill_references == (reference,)
    assert state.completion is None


def test_accepted_input_clears_references_and_deleted_markers_drop_identity() -> None:
    reference = SkillReference(catalog_id="a" * 64, item_id="b" * 64, name="code-review")
    state = TerminalState(
        drafts=(
            DraftState(
                key="thread-1",
                text="use $code-review @root:src/main.py",
                cursor=38,
                skill_references=(reference,),
                project_paths=("root:src/main.py",),
            ),
        )
    )

    edited = reduce_terminal(
        state,
        DraftChanged(
            key="thread-1",
            text="ordinary prompt",
            cursor=15,
            skill_references=(reference,),
            project_paths=("root:src/main.py",),
        ),
    ).state
    edited_draft = edited.draft("thread-1")
    assert edited_draft is not None
    assert edited_draft.skill_references == ()
    assert edited_draft.project_paths == ()

    cleared = reduce_terminal(state, DraftSubmitted("thread-1")).state
    cleared_draft = cleared.draft("thread-1")
    assert cleared_draft is not None
    assert cleared_draft.text == ""
    assert cleared_draft.skill_references == ()
    assert cleared_draft.project_paths == ()


def test_stale_completion_result_is_rejected_after_draft_changes() -> None:
    state = TerminalState(drafts=(DraftState(key="new", text="@src", cursor=4),))
    state = reduce_terminal(
        state,
        DraftChanged(key="new", text="ordinary text", cursor=13),
    ).state
    completion = CompletionState(
        request_version=1,
        key="new",
        kind="path",
        query="src",
        token_start=0,
        token_end=4,
    )

    reduction = reduce_terminal(state, CompletionLoaded(completion))

    assert reduction.state is state
    assert reduction.state.completion is None
