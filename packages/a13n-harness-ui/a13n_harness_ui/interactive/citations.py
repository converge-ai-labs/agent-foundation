"""Text citations for the terminal transcript and its saved history."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from a13n_stream_protocol.citations import UNRESOLVED_CITATION_PATTERN, Citation, WebCitationSource, project_citations

if TYPE_CHECKING:
    from a13n_harness_ui.surfaces import TranscriptPart

_UNRESOLVED = re.compile(UNRESOLVED_CITATION_PATTERN)
_STANDALONE_MARKER = re.compile(
    r"\ue200[^\ue200\ue201]+\ue201|\(\[[^\n]+\]\([^\n]+\)\)|(?:\[\d+\]|\[\[\d+\]\])(?:\([^\n]+\))?"
)


def cited_text(text: str, citations: Iterable[object] | None = None) -> str:
    """Keep claims and prose links, number distinct sources, and label unresolved markers."""
    sources: dict[tuple[str, str | None], tuple[int, str]] = {}
    replacements: dict[tuple[int, int], list[int]] = {}
    for citation in project_citations(citations, text_length=len(text)):
        numbers: list[int] = []
        for source in citation.sources:
            locator = source.url if isinstance(source, WebCitationSource) else source.document_id
            label = " — ".join(dict.fromkeys(value for value in (source.title, locator) if value))
            label = label or (source.excerpts[0] if source.excerpts else "Document")
            number, _ = sources.setdefault((source.kind, locator or label), (len(sources) + 1, label))
            if number not in numbers:
                numbers.append(number)
        if (anchor := citation.anchor) is not None:
            start, end = anchor.start, anchor.end
            marker = text[start:end]
            if anchor.kind == "content":
                end = start + len(marker.rstrip("\r\n"))
                start = end
            elif not _STANDALONE_MARKER.fullmatch(marker):
                start = end
            replacements.setdefault((start, end), []).extend(numbers)
    fragments: list[str] = []
    consumed = 0
    for (start, end), numbers in sorted(replacements.items()):
        if start < consumed:
            continue
        fragments.append(text[consumed:start])
        fragments.append("".join(f"[{n}]" for n in dict.fromkeys(numbers)))
        consumed = end
    fragments.append(text[consumed:])
    body = _UNRESOLVED.sub("[source unavailable]", "".join(fragments))
    if not sources:
        return body
    return body.rstrip() + "\n\nSources\n" + "\n".join(f"[{number}] {label}" for number, label in sources.values())


def cited_reply_text(output: str, parts: Iterable[TranscriptPart]) -> str:
    """Use saved part coordinates only when they reconstruct the exact CLI output."""
    chunks: list[str] = []
    citations: list[Citation] = []
    offset = 0
    adjacent = False
    for part in parts:
        if part.kind != "assistant" or part.text is None:
            adjacent = False
            continue
        if part.text_truncated:
            return cited_text(output)
        if not adjacent and chunks:
            chunks.append("\n\n")
            offset += 2
        chunks.append(part.text)
        for citation in part.citations:
            anchor = citation.anchor
            if anchor is not None:
                anchor = anchor.model_copy(update={"start": anchor.start + offset, "end": anchor.end + offset})
            citations.append(citation.model_copy(update={"anchor": anchor}))
        offset += len(part.text)
        adjacent = True
    return cited_text(output, citations if "".join(chunks) == output else None)
