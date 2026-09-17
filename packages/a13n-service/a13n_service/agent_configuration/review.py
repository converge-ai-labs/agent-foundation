"""Read the exact source, merge base and current target alongside a candidate."""

from __future__ import annotations

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.storage import short_session

from .context import StrictModel
from .domain import ConfigurationApplicationReceipt, ConfigurationDraft
from .models import ConfigurationApplicationRecord
from .persistence import load_owned_draft, not_found


class ConfigurationRevisionView(StrictModel):
    agent_id: str
    revision_id: str
    version: int
    config: AgentConfig


class ConfigurationDifference(StrictModel):
    path: tuple[str, ...]
    before: JsonValue
    after: JsonValue
    before_present: bool
    after_present: bool


class ConfigurationDraftReview(ConfigurationDraft):
    latest_application_receipt: ConfigurationApplicationReceipt | None
    source: ConfigurationRevisionView | None
    base: ConfigurationRevisionView | None
    current_target: ConfigurationRevisionView | None
    current_target_etag: str | None
    source_to_candidate: tuple[ConfigurationDifference, ...]
    base_to_candidate: tuple[ConfigurationDifference, ...]
    current_target_to_candidate: tuple[ConfigurationDifference, ...]
    base_to_current_target: tuple[ConfigurationDifference, ...]
    target_conflict: bool


class ConfigurationReviews:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, *, actor: AuthenticatedActor, draft_id: str) -> ConfigurationDraftReview:
        async with short_session(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=False)
            draft = record.to_resource()
            source = await revision_view(session, draft.source_agent_revision_id)
            base = await revision_view(session, draft.base_agent_revision_id)
            target = None if draft.target_agent_id is None else await session.get(AgentRecord, draft.target_agent_id)
            current = None if target is None else await revision_view(session, target.default_revision_id)
            application = await session.scalar(
                select(ConfigurationApplicationRecord)
                .where(ConfigurationApplicationRecord.draft_id == draft_id)
                .order_by(ConfigurationApplicationRecord.reviewed_version.desc())
                .limit(1)
            )
            return ConfigurationDraftReview(
                **draft.model_dump(),
                latest_application_receipt=None if application is None else application.to_resource(),
                source=source,
                base=base,
                current_target=current,
                current_target_etag=None if target is None else resource_etag(target.id, target.updated_at),
                source_to_candidate=config_diff(None if source is None else source.config, draft.config),
                current_target_to_candidate=config_diff(None if current is None else current.config, draft.config),
                base_to_candidate=config_diff(None if base is None else base.config, draft.config),
                base_to_current_target=config_diff(
                    None if base is None else base.config, None if current is None else current.config
                ),
                target_conflict=target is not None
                and resource_etag(target.id, target.updated_at) != draft.base_agent_etag,
            )


async def revision_view(session: AsyncSession, revision_id: str | None) -> ConfigurationRevisionView | None:
    if revision_id is None:
        return None
    revision = await session.get(AgentRevisionRecord, revision_id)
    if revision is None:
        raise not_found()
    return ConfigurationRevisionView(
        agent_id=revision.agent_id,
        revision_id=revision.id,
        version=revision.version,
        config=AgentConfig.model_validate(revision.config),
    )


def config_diff(before: AgentConfig | None, after: AgentConfig | None) -> tuple[ConfigurationDifference, ...]:
    first = None if before is None else before.model_dump(mode="json", by_alias=True)
    second = None if after is None else after.model_dump(mode="json", by_alias=True)
    result = []

    def compare(path: tuple[str, ...], left, right, left_present=True, right_present=True) -> None:
        if left_present == right_present and left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict) and len(path) < 8:
            for key in sorted(left.keys() | right.keys()):
                compare((*path, key), left.get(key), right.get(key), key in left, key in right)
        else:
            result.append(
                ConfigurationDifference(
                    path=path, before=left, after=right, before_present=left_present, after_present=right_present
                )
            )

    compare((), first, second, before is not None, after is not None)
    return tuple(result)
