"""Bounded provider-neutral citations for assistant text."""

import json
from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_ai import messages

MAX_CITATIONS = 64
MAX_CITATION_BYTES = 24 * 1024
_MAX_SOURCES = 4
_MAX_EXCERPTS = 2
_MAX_EXCERPT_CHARS = 512
_MAX_TITLE_CHARS = 256
_MAX_LOCATOR_CHARS = 2048
TEXT_CITATIONS_EVENT = "a13n.text_citations"
UNRESOLVED_CITATION_PATTERN = r"\ue200(?:cite|filecite)\ue202[^\ue200\ue201]+\ue201"


class CitationAnchor(BaseModel):
    """Half-open Unicode code-point range in the item's text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["content", "marker"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Citation anchor must satisfy 0 <= start < end")
        return self


class WebCitationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["web"] = "web"
    url: str = Field(min_length=1, max_length=_MAX_LOCATOR_CHARS)
    title: str | None = Field(default=None, max_length=_MAX_TITLE_CHARS)
    excerpts: tuple[Annotated[str, Field(max_length=_MAX_EXCERPT_CHARS)], ...] = Field(
        default=(), max_length=_MAX_EXCERPTS
    )


class DocumentCitationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["document"] = "document"
    document_id: str | None = Field(default=None, max_length=_MAX_LOCATOR_CHARS)
    title: str | None = Field(default=None, max_length=_MAX_TITLE_CHARS)
    excerpts: tuple[Annotated[str, Field(max_length=_MAX_EXCERPT_CHARS)], ...] = Field(
        default=(), max_length=_MAX_EXCERPTS
    )


type CitationSource = Annotated[WebCitationSource | DocumentCitationSource, Field(discriminator="kind")]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sources: tuple[CitationSource, ...] = Field(min_length=1, max_length=_MAX_SOURCES)
    anchor: CitationAnchor | None = None


def _from_provider(raw: messages.Citation) -> Citation | None:
    sources: list[CitationSource] = []
    for source in raw.sources[:_MAX_SOURCES]:
        title = source.title[:_MAX_TITLE_CHARS] if source.title else None
        excerpts = tuple(excerpt[:_MAX_EXCERPT_CHARS] for excerpt in source.excerpts[:_MAX_EXCERPTS])
        if isinstance(source, messages.WebCitationSource):
            if 0 < len(source.url) <= _MAX_LOCATOR_CHARS:
                sources.append(WebCitationSource(url=source.url, title=title, excerpts=excerpts))
        elif isinstance(source, messages.DocumentCitationSource):
            locator = source.document_id
            document_id = locator if locator and len(locator) <= _MAX_LOCATOR_CHARS else None
            if document_id or title or excerpts:
                sources.append(DocumentCitationSource(document_id=document_id, title=title, excerpts=excerpts))
    if not sources:
        return None
    anchor = raw.anchor
    return Citation(
        sources=tuple(sources),
        anchor=CitationAnchor(kind=anchor.kind, start=anchor.start, end=anchor.end)
        if anchor is not None and 0 <= anchor.start < anchor.end
        else None,
    )


def project_citations(raw: Iterable[object] | None, *, text_length: int | None = None) -> tuple[Citation, ...]:
    """Keep bounded, distinct citations in encounter order; retain sources beyond the visible prefix."""
    citations: dict[Citation, None] = {}
    size = 2  # JSON array delimiters
    for value in raw or ():
        if isinstance(value, messages.Citation):
            citation = _from_provider(value)
            if citation is None:
                continue
        else:
            try:
                citation = Citation.model_validate(value)
            except ValidationError:
                continue
        if text_length is not None and citation.anchor is not None and citation.anchor.end > text_length:
            citation = citation.model_copy(update={"anchor": None})
        if citation in citations:
            continue
        payload = json.dumps(citation.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        size += len(payload.encode("utf-8")) + bool(citations)
        if size > MAX_CITATION_BYTES:
            break
        citations[citation] = None
        if len(citations) == MAX_CITATIONS:
            break
    return tuple(citations)
