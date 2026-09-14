"""Authorized built-in Toolset catalog and side-effect-free candidate checks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.storage import short_session
from a13n_service.web.domain import ScrapeSelection
from a13n_service.web.models import WebProviderRecord
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.resources import WebProviderError, require_eligible, require_operation

from .domain import AgentReviewer
from .toolsets import ToolsetCatalog, Toolsets, catalog, default_toolsets, enabled_tool, requires_reviewer


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolsetCandidate(_StrictModel):
    toolsets: Toolsets = Field(default_factory=default_toolsets)
    reviewer: AgentReviewer | None = None


class ToolSetupDestination(_StrictModel):
    kind: Literal["web_provider", "reviewer"]
    operation: Literal["search", "scrape"] | None = None


class ToolsetCandidateError(_StrictModel):
    code: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    setup_destination: ToolSetupDestination | None = None


class ToolsetCandidateResult(_StrictModel):
    valid: bool
    toolsets: Toolsets
    errors: tuple[ToolsetCandidateError, ...]


class ToolsetService:
    """Expose immutable definitions and validate only selected durable resources."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        web_providers: WebProviderRegistry,
    ) -> None:
        self._sessions = sessions
        self._web_providers = web_providers

    async def definitions(self, *, actor: AuthenticatedActor, workspace_id: str) -> ToolsetCatalog:
        async with short_session(self._sessions) as session:
            await authorize_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.agent_read,
            )
        operations = {
            operation for definition in self._web_providers.definitions() for operation in definition.operations
        }
        return catalog(supported_web_operations=frozenset(operations))

    async def validate_candidate(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        candidate: ToolsetCandidate,
    ) -> ToolsetCandidateResult:
        errors: list[ToolsetCandidateError] = []
        if requires_reviewer(candidate.toolsets) and candidate.reviewer is None:
            errors.append(
                ToolsetCandidateError(
                    code="tool_reviewer_missing",
                    path="reviewer",
                    setup_destination=ToolSetupDestination(kind="reviewer"),
                )
            )

        selections = _provider_selections(candidate.toolsets)
        async with short_session(self._sessions) as session:
            scope = await authorize_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.web_provider_read,
            )
            records = await _selected_providers(
                session,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                provider_ids=frozenset(item[2] for item in selections if item[2] is not None),
            )

        for operation, path, provider_id, scrape in selections:
            destination = ToolSetupDestination(kind="web_provider", operation=operation)
            if provider_id is None:
                errors.append(
                    ToolsetCandidateError(
                        code="web_provider_required",
                        path=path,
                        setup_destination=destination,
                    )
                )
                continue
            record = records.get(provider_id)
            if record is None:
                errors.append(
                    ToolsetCandidateError(
                        code="web_provider_not_found",
                        path=path,
                        setup_destination=destination,
                    )
                )
                continue
            try:
                require_eligible(record, self._web_providers)
                require_operation(record, operation, self._web_providers, selection=scrape)
            except WebProviderError as error:
                errors.append(
                    ToolsetCandidateError(
                        code=error.code,
                        path=path,
                        setup_destination=destination,
                    )
                )

        return ToolsetCandidateResult(
            valid=not errors,
            toolsets=candidate.toolsets,
            errors=tuple(errors),
        )


def _provider_selections(
    toolsets: Toolsets,
) -> tuple[tuple[Literal["search", "scrape"], str, str | None, ScrapeSelection | None], ...]:
    items: list[tuple[Literal["search", "scrape"], str, str | None, ScrapeSelection | None]] = []
    for operation in ("search", "scrape"):
        selection = enabled_tool(toolsets, "web", operation)
        if selection is None:
            continue
        provider_id = selection.config.get("provider_id")
        path = f"toolsets.web.tools.{operation}.config.provider_id"
        scrape = ScrapeSelection.model_validate(selection.config) if operation == "scrape" and provider_id else None
        items.append((operation, path, provider_id if isinstance(provider_id, str) else None, scrape))
    return tuple(items)


async def _selected_providers(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_ids: frozenset[str],
) -> dict[str, WebProviderRecord]:
    if not provider_ids:
        return {}
    query = select(WebProviderRecord).where(
        WebProviderRecord.id.in_(provider_ids),
        WebProviderRecord.organization_id == organization_id,
        WebProviderRecord.workspace_id.in_((None, workspace_id)),
    )
    return {record.id: record for record in (await session.scalars(query)).all()}


__all__ = [
    "ToolSetupDestination",
    "ToolsetCandidate",
    "ToolsetCandidateError",
    "ToolsetCandidateResult",
    "ToolsetService",
]
