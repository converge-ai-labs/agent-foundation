"""Render release-owned guidance with optional file-backed operational details."""

REFERENCE_MARKER = "<!-- reference -->"


def render_guidance(content: str, reference_path: str | None = None) -> str:
    """Keep essential rules inline and optionally reference the remaining Markdown."""
    essentials, marker, details = content.partition(REFERENCE_MARKER)
    if reference_path is not None and marker:
        return (
            essentials.strip()
            + f"\n\n<reference>\nWorkflows and troubleshooting: `{reference_path}`. Read it with view before a multi-step task or after an error.\n</reference>"
        )
    return (essentials + details).strip()


_GUIDANCE_PAGE_ATTRIBUTE = "_a13n_guidance_page"


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
    """Find the boundary-owned marker through native and proxy exception wrapping."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        page = getattr(current, _GUIDANCE_PAGE_ATTRIBUTE, None)
        if isinstance(page, str):
            return page
        part = getattr(current, "tool_retry", None)
        content = getattr(part, "content", None)
        if isinstance(content, _GuidanceRetryText):
            return content.page
        current = current.__cause__
    return None


def guidance_hint(reference_root: str, page: str) -> str:
    return f"Guidance: `{reference_root}/harness/{page}.md`. Read with view before deciding whether to retry."
