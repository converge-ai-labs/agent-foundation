"""Per-submission provenance and append-on-change response-surface guidance."""

from typing import Literal

from a13n_harness.content import request_input_content
from a13n_harness.input import RunInputValue
from a13n_harness.model_context import ModelContextBlock, ModelContextPlacement
from pydantic_ai.messages import ModelMessage, ModelRequest, TextContent

_SURFACE_HINTS = {
    "webui": (
        "Current response surface: Harness UI WebUI.\n"
        "For responses on this surface, the interface supports Markdown and renders fenced mermaid blocks "
        "as static diagrams. Use them when helpful. Mermaid configuration directives, image assets, and "
        "interactive callbacks are not supported. LaTeX math renders with KaTeX: use $...$ for inline math, "
        "$$ on separate lines for display math, or fenced math blocks. Escape literal dollar signs as "
        "\\$ when needed. Follow the user's explicit output-format preferences.\n"
        "When a Host file should be directly openable by the user, link it as "
        "`[label](/threads/{root_thread_id}?native=files&native_path={URL-encoded absolute Host path})`, "
        "using the current root Thread ID supplied in your instructions. The WebUI opens that same-instance "
        "link in its Files drawer. Do not use a bare Host path or an `/api/host/files/...` URL as the link target."
    ),
    "tui": (
        "Current response surface: Harness UI TUI.\n"
        "For responses on this surface, the interface supports terminal Markdown but displays Mermaid blocks "
        "as source text rather than diagrams. Prefer prose or simple ASCII diagrams for inline explanations "
        "unless the user requests Mermaid. This does not restrict formats written to files."
    ),
}


def append_surface_provenance(prompt: RunInputValue, surface: Literal["tui", "webui"] | None) -> RunInputValue:
    """Retain the origin of each accepted submission, including an unspecified origin."""
    parts = (prompt,) if isinstance(prompt, str) else tuple(prompt)
    label = {"webui": "Harness UI WebUI", "tui": "Harness UI TUI", None: "an unspecified interface"}[surface]
    return (
        *parts,
        TextContent(
            f"This input was submitted through {label}.",
            metadata={
                "display": False,
                "source_id": "a13n-harness-ui.input-surface",
                "context_group": "runtime_state",
                "input_surface": surface,
            },
        ),
    )


def surface_context(messages: list[ModelMessage]) -> tuple[ModelContextBlock, ...]:
    """Derive current guidance from retained semantic inputs, not a per-Thread watermark."""
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        for item in reversed(request_input_content(message)):
            if item.metadata.source_id != "a13n-harness-ui.input-surface":
                continue
            surface = (item.metadata.model_extra or {}).get("input_surface")
            content = _SURFACE_HINTS.get(surface) if isinstance(surface, str) else None
            return (
                ModelContextBlock(
                    source_id="a13n-harness-ui.surface",
                    context_group="runtime_state",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content='<surface-context source="a13n-harness-ui">\n<![CDATA[\n'
                    + (
                        content
                        or "The current input has no declared response surface. Do not assume earlier interface capabilities."
                    )
                    + "\n]]>\n</surface-context>",
                ),
            )
    return ()
