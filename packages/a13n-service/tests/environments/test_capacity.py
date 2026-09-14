"""Target observations and pending preparations account for each managed slot once."""

import pytest
from a13n_environment import EnvironmentError
from a13n_service.environments.capacity import CapacityLimits
from a13n_service.environments.domain import CreateManagedEnvironmentRequest
from a13n_service.environments.models import EnvironmentRecord, EnvironmentTemplateRevisionRecord
from a13n_service.storage import short_session, transaction

from .conftest import WORKSPACE_ID, actor
from .test_lifecycle import fixture_environment

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("status", "action", "ownership", "limit", "admitted"),
    [
        ("unprepared", None, "managed", 1, True),
        ("deleted", None, "managed", 1, True),
        ("unprepared", "prepare", "managed", 1, False),
        ("deleted", "prepare", "managed", 1, False),
        ("unprepared", "stop", "managed", 1, True),
        ("deleted", "delete", "managed", 1, True),
        ("running", None, "managed", 1, False),
        ("stopped", None, "managed", 1, False),
        ("unavailable", None, "managed", 1, False),
        ("running", "prepare", "managed", 2, True),
        ("unprepared", "prepare", "external", 1, True),
        ("running", None, "external", 1, True),
    ],
)
async def test_target_capacity_counts_observations_and_pending_preparations_once(
    environment_service, environment_sessions, status, action, ownership, limit, admitted
):
    first = await fixture_environment(environment_service)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, first.id)
        revision = await session.get(EnvironmentTemplateRevisionRecord, first.template_revision_id)
        template_id = revision.template_id
        if ownership == "external":
            row.template_revision_id = None
        row.status, row.ownership = status, ownership
        row.operation_id = "envop-pending" if action else None
        row.operation_action = action
    second = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateManagedEnvironmentRequest(template_id=template_id),
        idempotency_key="second-target",
    )
    capacity = CapacityLimits(max_targets=limit)

    async def admit():
        async with transaction(environment_sessions) as session:
            await capacity.lock_workspace(session, second.id)
            row = await session.get(EnvironmentRecord, second.id, with_for_update=True)
            await capacity.admit(session, row)

    if admitted:
        await admit()
    else:
        with pytest.raises(EnvironmentError) as error:
            await admit()
        assert error.value.code == "environment_capacity_exceeded"
        assert error.value.details == {"capacity": "targets", "limit": limit}
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, second.id)
        assert row.status == "unprepared" and row.operation_id is None
