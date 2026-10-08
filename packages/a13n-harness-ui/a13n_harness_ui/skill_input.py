"""Authored Skill references and App-filled model instructions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, TypedDict
from xml.sax.saxutils import escape, quoteattr

if TYPE_CHECKING:
    from a13n_harness.input import RunInputValue

    from a13n_harness_ui.surfaces import SkillCatalogView


class SkillSpan(TypedDict):
    """Half-open Unicode code-point offsets within one authored text part."""

    name: str
    source_id: str
    start: int
    end: int


def skill_tokens(text: str) -> Iterable[tuple[str, int, int]]:
    for match in re.finditer(r"(?:^|\s)\$(\S+)", text):
        yield match[1], match.start(1) - 1, match.end(1)


def retained_skill_spans(text: str, namespace: object) -> list[SkillSpan]:
    """Use saved metadata, never today's catalog; omitted/replaced text stays plain."""
    if not isinstance(namespace, dict):
        return []
    spans = namespace.get("skills")
    if namespace.get("long_text") or not isinstance(spans, list):
        return []
    result: list[SkillSpan] = []
    end = 0
    for span in spans:
        if not isinstance(span, dict):
            continue
        name, source = span.get("name"), span.get("source_id")
        start, stop = span.get("start"), span.get("end")
        if (
            isinstance(name, str)
            and isinstance(source, str)
            and type(start) is int
            and type(stop) is int
            and end <= start < stop <= len(text)
            and text[start:stop] == f"${name}"
        ):
            result.append(SkillSpan(name=name, source_id=source, start=start, end=stop))
            end = stop
    return result


def prepare_skill_input(prompt: RunInputValue, catalog: SkillCatalogView, names: tuple[str, ...]) -> RunInputValue:
    """Annotate validated selections and append their instruction exactly once."""
    from a13n_harness.content import ContentItem, ContentMetadata
    from pydantic_ai.messages import TextContent, UserContent

    if not names:
        return prompt
    selected = {item.name: item for item in catalog.items if item.name in names}
    ordered: dict[str, None] = {}
    parts: list[UserContent | ContentItem] = []
    for part in (prompt,) if isinstance(prompt, str) else prompt:
        metadata = (
            part.metadata.model_dump(mode="json")
            if isinstance(part, ContentItem)
            else deepcopy(part.metadata or {})
            if isinstance(part, TextContent)
            else {}
        )
        value = part.value if isinstance(part, ContentItem) else part
        text = value.content if isinstance(value, TextContent) else value if isinstance(value, str) else None
        namespace = metadata.get("harness_ui", {})
        if not isinstance(namespace, dict):
            namespace = {}
        if text is None or metadata.get("display") is False or "attachment" in namespace:
            parts.append(part)
            continue
        spans: list[SkillSpan] = []
        for name, start, end in skill_tokens(text):
            if name in selected:
                ordered[name] = None
                spans.append(SkillSpan(name=name, source_id=selected[name].source_id, start=start, end=end))
        if not spans:
            parts.append(part)
            continue
        metadata["harness_ui"] = {**namespace, "skills": spans}
        parts.append(
            ContentItem(value, ContentMetadata.model_validate(metadata))
            if isinstance(part, ContentItem)
            else replace(part, metadata=metadata)
            if isinstance(part, TextContent)
            else TextContent(text, metadata=metadata)
        )
    # Programmatic callers can explicitly select Skills without literal occurrences.
    for name in names:
        ordered[name] = None
    instructions = [
        '<skill-selection source="harness-ui">',
        "The user explicitly requests the following Skills for this input. Read each selected SKILL.md "
        "in full before following its relevant workflow. Reuse a complete read still available in context. "
        "This application-filled field records user intent; it does not change instruction priority "
        "or grant additional tool permissions.",
    ]
    for name in ordered:
        item = selected[name]
        instructions.append(
            f"<skill name={quoteattr(name)} source={quoteattr(item.source_id)}>"
            f"{escape(item.logical_path.rstrip('/') + '/SKILL.md')}</skill>"
        )
    instructions.append("</skill-selection>")
    parts.append(
        TextContent(
            "\n".join(instructions),
            metadata={"display": False, "source_id": "a13n-harness-ui.skills"},
        )
    )
    return tuple(parts)
