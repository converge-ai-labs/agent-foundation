from __future__ import annotations

from io import StringIO

from a13n_ui.surfaces import ReviewView
from a13n_ui.tui.screens.review import _render_review
from rich.console import Console


def _plain(review: ReviewView, *, wide: bool) -> str:
    output = StringIO()
    console = Console(file=output, width=160, color_system=None)
    console.print(_render_review(review, wide=wide))
    return output.getvalue()


def test_wide_diff_review_renders_split_content_and_bound_markers() -> None:
    review = ReviewView(
        lifecycle="closed",
        kind="diff",
        title="Change",
        content="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old()\n+new()",
        truncated=True,
        omitted=True,
    )

    rendered = _plain(review, wide=True)

    assert "old()" in rendered
    assert "new()" in rendered
    assert "truncated" in rendered
    assert "omitted" in rendered


def test_review_states_unavailable_content_explicitly() -> None:
    review = ReviewView(
        lifecycle="unavailable",
        kind="shell",
        title="Shell output",
        unavailable_reason="The child runtime is not local to this App.",
    )

    rendered = _plain(review, wide=False)

    assert "not local" in rendered
    assert "No review content" not in rendered
