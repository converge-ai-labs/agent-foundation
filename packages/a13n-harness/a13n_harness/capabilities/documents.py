"""Environment-bounded document conversion through a Host-selected provider."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.toolsets.documents import (
    DocumentAsset,
    DocumentConversionError,
    DocumentConversionRequest,
    DocumentConversionResult,
    DocumentConverter,
    DocumentKind,
    DocumentsConfiguration,
    DocumentsToolset,
)

DOCUMENTS_CAPABILITY_ID = "a13n.documents"
DOCUMENTS_RUN_CAPABILITY_ID = "a13n.documents.run"
_DOCUMENT_TOOLSET_ID = "a13n-document-tools"


@dataclass(kw_only=True)
class DocumentsRunCapability(AbstractCapability[AgentContext]):
    """Fresh run attachment carrying document conversion authority."""

    id: str | None = DOCUMENTS_RUN_CAPABILITY_ID
    converter: DocumentConverter = field()

    def __post_init__(self) -> None:
        if self.id != DOCUMENTS_RUN_CAPABILITY_ID:
            raise ValueError(f"DocumentsRunCapability.id must be {DOCUMENTS_RUN_CAPABILITY_ID!r}")
        if not isinstance(self.converter, DocumentConverter):
            raise TypeError("converter must implement DocumentConverter")


@dataclass(init=False)
class DocumentsCapability(AbstractCapability[AgentContext]):
    """Publish PDF and Office conversion tools without owning converter dependencies."""

    id = DOCUMENTS_CAPABILITY_ID

    def __init__(self, configuration: DocumentsConfiguration | None = None) -> None:
        self.configuration = (configuration or DocumentsConfiguration()).model_copy(deep=True)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(DOCUMENTS_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _DocumentsActiveCapability):
                raise DefinitionError("Documents has an incompatible run replacement.", code="capability_type_mismatch")
            return existing
        if DOCUMENTS_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "DocumentsCapability must originate from the Agent definition.", code="capability_scope_invalid"
            )
        replacement = _DocumentsActiveCapability(self.configuration, context=ctx.deps)
        ctx.deps._record_run_capability(DOCUMENTS_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _DocumentsActiveCapability(DocumentsCapability):
    def __init__(self, configuration: DocumentsConfiguration, *, context: AgentContext) -> None:
        super().__init__(configuration)
        self._context = context
        self._converter: DocumentConverter | None = None

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Documents run replacement cannot cross logical runs.", code="capability_scope_invalid"
            )
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id=_DOCUMENT_TOOLSET_ID)

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        environment = ctx.deps.environment
        return DocumentsToolset(
            self._bind(ctx),
            self.configuration,
            files=environment.files,
            file_scopes=environment,
        ).get_toolset()

    def _bind(self, ctx: RunContext[AgentContext]) -> DocumentConverter:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Documents run replacement cannot cross logical runs.", code="capability_scope_invalid"
            )
        owner = ctx.capabilities.get(DOCUMENTS_CAPABILITY_ID)
        if type(owner) is not _DocumentsActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized Documents owner has an incompatible identity.", code="capability_scope_invalid"
            )
        attachment = ctx.capabilities.get(DOCUMENTS_RUN_CAPABILITY_ID)
        if type(attachment) is not DocumentsRunCapability:
            raise DefinitionError(
                "DocumentsCapability requires one fresh DocumentsRunCapability.", code="documents_binding_missing"
            )
        if DOCUMENTS_RUN_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
            raise DefinitionError(
                "DocumentsRunCapability must originate from RunBindings.", code="capability_scope_invalid"
            )
        if self._converter is None:
            self._converter = attachment.converter
        elif self._converter is not attachment.converter:
            raise DefinitionError(
                "Document converter identity changed within one run.", code="capability_scope_invalid"
            )
        return self._converter


__all__ = [
    "DocumentAsset",
    "DocumentConversionError",
    "DocumentConversionRequest",
    "DocumentConversionResult",
    "DocumentConverter",
    "DocumentKind",
    "DocumentsCapability",
    "DocumentsConfiguration",
    "DocumentsRunCapability",
]
