"""Render release-owned guidance with optional file-backed operational details."""

from functools import cache
from importlib.resources import files

from pydantic_ai.exceptions import ModelRetry, ToolRetryError

TOOL_GUIDANCE_KEY = "a13n.guidance"

REFERENCE_MARKER = "<!-- reference -->"


@cache
def load_guidance(page: str) -> str:
    """Load one release-owned page; an invalid declaration fails at its source."""
    return files("a13n_harness.toolsets.prompts").joinpath("guidance", f"{page}.md").read_text(encoding="utf-8")


def guidance_has_reference(page: str) -> bool:
    return REFERENCE_MARKER in load_guidance(page)


def guidance_reference_path(reference_root: str, page: str) -> str:
    return f"{reference_root}/harness/{page}.md"


def render_guidance(content: str, reference_path: str | None = None) -> str:
    """Keep essential rules inline and optionally reference the remaining Markdown."""
    essentials, marker, details = content.partition(REFERENCE_MARKER)
    if reference_path is not None and marker:
        return (
            essentials.strip()
            + f"\n\n<reference>\nWorkflows and troubleshooting: `{reference_path}`. Read it with view before a multi-step task or after an error.\n</reference>"
        )
    return (essentials + details).strip()


class _GuidanceRetryText(str):
    """Carry provenance through native validation wrapping without changing retry text."""

    page: str

    def __new__(cls, text: str, page: str) -> "_GuidanceRetryText":
        value = super().__new__(cls, text)
        value.page = page
        return value

    def __reduce__(self) -> tuple[type["_GuidanceRetryText"], tuple[str, str]]:
        return type(self), (str(self), self.page)


def retry_guidance_page(error: BaseException) -> str | None:
    """Recognize only boundary-owned retry text, never tool-authored hint strings."""
    content = (
        error.message
        if isinstance(error, ModelRetry)
        else error.tool_retry.content
        if isinstance(error, ToolRetryError)
        else None
    )
    return content.page if isinstance(content, _GuidanceRetryText) else None


def guidance_hint(reference_root: str, page: str) -> str:
    return (
        f"Guidance: `{guidance_reference_path(reference_root, page)}`. Read with view before deciding whether to retry."
    )


def hinted_retry_text(content: object, *, reference_root: str | None, page: object) -> str | None:
    """Append guidance once, only for reference-bearing pages in reference delivery."""
    if (
        reference_root is None
        or not isinstance(page, str)
        or not isinstance(content, str)
        or isinstance(content, _GuidanceRetryText)
        or not guidance_has_reference(page)
    ):
        return None
    return _GuidanceRetryText(content + "\n" + guidance_hint(reference_root, page), page)
