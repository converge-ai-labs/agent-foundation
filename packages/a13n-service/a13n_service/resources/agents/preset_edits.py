"""Managed preset editing and instruction composition, before ordinary revision validation."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.resources import revisions
from a13n_service.resources.agents.schemas import AgentConfig, PresetEdit, PresetSnapshot
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow

EDITABLE_KINDS = frozenset({"composer", "finding"})
MODEL_SETTINGS = frozenset({"thinking", "max_tokens", "temperature", "top_p"})


def require_editable(head: AgentRow, if_match: str | None) -> None:
    """Only preset revisions/defaults have this exception; head metadata remains deployment-owned."""
    if head.builtin and head.preset_kind in EDITABLE_KINDS:
        require_match(if_match, head.id, head.version)
        if head.archived_at is not None:
            raise conflict(head.KIND, head.id, "archived")
    else:
        revisions.require_open(head, if_match)


def definition_snapshot(config: AgentConfig) -> PresetSnapshot:
    return PresetSnapshot(
        official_instructions=config.instructions,
        definition_digest=revisions.config_digest(config.model_dump(mode="json", exclude={"model"})),
    )


def snapshot(revision: AgentRevisionRow) -> PresetSnapshot:
    # Older revisions contain only official instructions. Never parse their text for a user suffix.
    return (
        PresetSnapshot.model_validate(revision.preset)
        if revision.preset is not None
        else definition_snapshot(AgentConfig.model_validate(revision.config))
    )


def compose(config: AgentConfig, preset: PresetSnapshot) -> AgentConfig:
    instructions = preset.official_instructions
    if preset.instructions:
        instructions += "\n\nWorkspace instructions:\n" + preset.instructions
    if len(instructions) > 256 * 1024:
        raise invalid("preset.instructions", "combined instructions exceed 262144 characters")
    return config.model_copy(update={"instructions": instructions})


async def prepare_edit(
    session: AsyncSession, head: AgentRow | None, config: AgentConfig, edit: PresetEdit | None
) -> tuple[AgentConfig, PresetSnapshot | None]:
    if head is None or not head.builtin:
        if edit is not None:
            raise invalid("preset", "only supported for managed presets")
        return config, None
    if head.preset_kind not in EDITABLE_KINDS:
        raise conflict(head.KIND, head.id, "builtin")
    if edit is None:
        raise invalid("preset", "base_revision_id and supplemental instructions are required")
    base = await revisions.find_revision(session, AgentRevisionRow, head.id, edit.base_revision_id)
    previous = AgentConfig.model_validate(base.config)
    for name in sorted(AgentConfig.model_fields.keys() - {"model", "model_settings"}):
        if getattr(config, name) != getattr(previous, name):
            raise invalid(
                name, "managed preset field is read-only; edit preset.instructions for supplemental instructions"
            )
    for name in sorted((config.model_settings.keys() | previous.model_settings.keys()) - MODEL_SETTINGS):
        if (name in config.model_settings) != (name in previous.model_settings) or config.model_settings.get(
            name
        ) != previous.model_settings.get(name):
            raise invalid(f"model_settings.{name}", "managed preset setting is read-only")
    preset = snapshot(base).model_copy(update={"instructions": edit.instructions})
    return compose(config, preset), preset
