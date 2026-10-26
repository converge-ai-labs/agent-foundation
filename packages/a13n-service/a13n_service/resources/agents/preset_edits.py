"""The bounded fields workspace users may change on managed Agent revisions."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.resources import revisions
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow

EDITABLE_KINDS = frozenset({"composer", "finding"})
MODEL_SETTINGS = frozenset({"thinking", "max_tokens", "temperature", "top_p"})


def editable(head: AgentRow) -> bool:
    return head.builtin and head.preset_kind in EDITABLE_KINDS


def require_editable(head: AgentRow, if_match: str | None) -> None:
    """Only preset revisions/defaults have this exception; metadata remains deployment-owned."""
    if editable(head):
        require_match(if_match, head.id, head.version)
        if head.archived_at is not None:
            raise conflict(head.KIND, head.id, "archived")
    else:
        revisions.require_open(head, if_match)


async def validate_edit(session: AsyncSession, head: AgentRow | None, config: AgentConfig) -> None:
    """Validate managed edits against the selected default."""
    if head is None or not head.builtin:
        return
    if not editable(head):
        raise conflict(head.KIND, head.id, "builtin")
    base = await session.get_one(AgentRevisionRow, head.default_revision_id)
    previous = AgentConfig.model_validate(base.config)
    for name in sorted(AgentConfig.model_fields.keys() - {"model", "model_settings", "instructions"}):
        if getattr(config, name) != getattr(previous, name):
            raise invalid(name, "managed preset field is read-only")
    for name in sorted((config.model_settings.keys() | previous.model_settings.keys()) - MODEL_SETTINGS):
        if (name in config.model_settings) != (name in previous.model_settings) or config.model_settings.get(
            name
        ) != previous.model_settings.get(name):
            raise invalid(f"model_settings.{name}", "managed preset setting is read-only")
