"""Environment-bounded document conversion through a Host-selected provider."""

from __future__ import annotations

from dataclasses import dataclass

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
_DOCUMENT_TOOLSET_ID = "a13n-document-tools"


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
        provider = ctx.deps.document_converter
        if provider is None:
            raise DefinitionError(
                "DocumentsCapability requires RunBindings.document_converter.", code="documents_binding_missing"
            )
        return provider


__all__ = [
    "DocumentAsset",
    "DocumentConversionError",
    "DocumentConversionRequest",
    "DocumentConversionResult",
    "DocumentConverter",
    "DocumentKind",
    "DocumentsCapability",
    "DocumentsConfiguration",
]
