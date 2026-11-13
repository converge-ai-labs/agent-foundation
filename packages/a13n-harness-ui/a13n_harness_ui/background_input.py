"""Source-marked background input shared by Host delivery and presentation."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from a13n_harness.content import ContentItem

ACTIVITY_SOURCES = frozenset({"async_subagent", "background_process", "envd_session", "parent_agent", "tool_execution"})


def background_input(
    prompt: str, execution_ids: tuple[str, ...], *, source: Literal["async_subagent", "tool_execution"]
) -> tuple["ContentItem", ...]:
    from a13n_harness.content import ContentItem, ContentMetadata

    return (
        ContentItem(
            prompt,
            ContentMetadata.model_validate(
                {
                    "source_id": "tool_execution.completion" if source == "tool_execution" else "a13n.async_subagent",
                    "execution_ids": list(execution_ids),
                    "a13n.steering-source": source,
                }
            ),
        ),
    )
