"""Render release-owned guidance with optional file-backed operational details."""

REFERENCE_MARKER = "<!-- reference -->"


def render_guidance(content: str, reference_path: str | None = None) -> str:
    """Keep essential rules inline and optionally reference the remaining Markdown."""
    essentials, marker, details = content.partition(REFERENCE_MARKER)
    if reference_path is not None and marker:
        return (
            essentials.strip()
            + f"\n\n<reference>\nBefore using this feature, read its guidance with view: `{reference_path}`.\n</reference>"
        )
    return (essentials + details).strip()
