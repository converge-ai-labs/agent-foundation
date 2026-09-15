"""Skill-only refresh must preserve all other invocation meaning and remain bounded."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_service.agents.domain import ChildAgentExecution
from a13n_service.interactions.command_preparation import (
    CommandInput,
    PreparedCommandInput,
    SkillPublicationChanged,
    validate_invocation,
)
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.skills.domain import SkillRevisionLock

from .test_commands import _Freezing, _frozen

pytestmark = pytest.mark.anyio


def selection(version, *, child=False, instructions=None):
    base = _frozen()
    lock = SkillRevisionLock(
        skill_id="sk_1234567890abcdef",
        skill_revision_id=f"skr_{version:016x}",
        skill_key="deploy",
        version=version,
        content_digest=f"{version:064x}",
    )
    config = base.effective_config.model_copy(update={"skills": (lock,), "content_digest": f"{version:064x}"})
    if child:
        config = base.effective_config.model_copy(
            update={
                "content_digest": f"{version:064x}",
                "child_configs": {
                    base.agent_revision_id: ChildAgentExecution(
                        agent_id=base.agent_id,
                        revision_content_digest="a" * 64,
                        effective_config=config,
                    )
                },
            }
        )
    if instructions is not None:
        config = config.model_copy(update={"instructions": instructions})
    return replace(base, effective_config=config)


@pytest.mark.parametrize("child", [False, True])
async def test_skill_refresh_retains_preparation_and_input(lifecycle_interaction_sessions, child):
    old, latest = selection(1, child=child), selection(2, child=child)
    invocation, accepted = object(), object()
    prepared = PreparedCommandInput(invocation, old, accepted)
    inputs = CommandInput(lifecycle_interaction_sessions, Mock(), Mock())
    resolver = SimpleNamespace(freezing=_Freezing([latest]))
    seen = []

    async def accept(value):
        assert value.invocation is invocation and value.input is accepted
        seen.append(value.frozen)
        await validate_invocation(None, resolver, prepared=invocation, frozen=value.frozen)
        return value.frozen

    assert await inputs.accept_with_skill_refresh(resolver, prepared, accept) == latest
    assert seen == [old, latest]


async def test_continuous_publication_stops_after_three_acceptance_attempts(lifecycle_interaction_sessions):
    inputs = CommandInput(lifecycle_interaction_sessions, Mock(), Mock())
    prepared = PreparedCommandInput(object(), selection(1), object())
    resolver = SimpleNamespace(
        freezing=_Freezing([selection(2), selection(2), selection(3), selection(3), selection(4)])
    )
    seen = []

    async def accept(value):
        seen.append(value.frozen)
        await validate_invocation(None, resolver, prepared=value.invocation, frozen=value.frozen)

    with pytest.raises(SkillPublicationChanged) as captured:
        await inputs.accept_with_skill_refresh(resolver, prepared, accept)
    assert captured.value.code == "run_invocation_changed"
    assert len(seen) == 3


@pytest.mark.parametrize("when", ["final-validation", "refresh"])
async def test_skill_refresh_does_not_adopt_unrelated_config_changes(lifecycle_interaction_sessions, when):
    inputs = CommandInput(lifecycle_interaction_sessions, Mock(), Mock())
    prepared = PreparedCommandInput(object(), selection(1), object())
    changed = selection(2, instructions="Different instructions")
    values = [changed] if when == "final-validation" else [selection(2), changed]
    resolver = SimpleNamespace(freezing=_Freezing(values))
    seen = []

    async def accept(value):
        seen.append(value)
        await validate_invocation(None, resolver, prepared=value.invocation, frozen=value.frozen)

    with pytest.raises(InteractionCommandError) as captured:
        await inputs.accept_with_skill_refresh(resolver, prepared, accept)
    assert type(captured.value) is InteractionCommandError
    assert captured.value.code == "run_invocation_changed"
    assert len(seen) == 1
