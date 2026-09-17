"""Accepted additions join Attempt IAM without putting storage in dispatch paths."""

import pytest
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import event

from .test_attempt_authorization import admit, direct_runner
from .test_mount_observations import mounted_attempt as mounted_attempt
from .test_websocket_use_authorization import client_environment as client_environment
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mounted_authorization(interaction_sessions, mounted_attempt):
    attempt, environment = mounted_attempt
    async with short_session(interaction_sessions) as session:
        run = (await session.get(RunRecord, attempt.run_id)).to_resource()
    await prepare_permissions(interaction_sessions, run, attempt)
    return attempt.authorization, environment


async def test_addition_requires_explicit_admission_then_dispatch_checks_are_local(
    interaction_sessions, mounted_authorization
):
    authorization, environment = mounted_authorization
    with pytest.raises(AuthorizationError, match="environment_not_found"):
        authorization.require_environment(environment.id)
    await authorization.admit_environment(name="computer", environment_id=environment.id)
    engine = interaction_sessions.kw["bind"].sync_engine
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(20):
            authorization.require_environment(environment.id)
        assert statements == []
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("name,target", [("missing", None), ("computer", "env_unaccepted71717")])
async def test_admission_checks_exact_association_even_for_an_already_known_target(mounted_authorization, name, target):
    authorization, environment = mounted_authorization
    await authorization.admit_environment(name="computer", environment_id=environment.id)
    with pytest.raises(AuthorizationError, match="environment_not_found"):
        await authorization.admit_environment(name=name, environment_id=target or environment.id)
    authorization.require_environment(environment.id)


async def test_addition_provider_uses_existing_iam_refresh_cadence(interaction_sessions, mounted_authorization):
    authorization, environment = mounted_authorization
    await authorization.admit_environment(name="computer", environment_id=environment.id)
    async with transaction(interaction_sessions) as session:
        (await session.get(EnvironmentProviderRecord, environment.provider_id)).enabled = False
    await admit(authorization, 10)
    authorization.require_environment(environment.id)
    await authorization.admit_model_request()
    with pytest.raises(AuthorizationError, match="environment_provider_unavailable"):
        authorization.require_environment(environment.id)
    # A failed addition does not poison execution of the model itself.
    with pytest.raises(AuthorizationError, match="environment_provider_unavailable"):
        await authorization.admit_environment(name="computer", environment_id=environment.id)
    await authorization.admit_model_request()
    async with transaction(interaction_sessions) as session:
        (await session.get(EnvironmentProviderRecord, environment.provider_id)).enabled = True
    await authorization.admit_environment(name="computer", environment_id=environment.id)
    authorization.require_environment(environment.id)


async def test_new_mount_reads_current_run_principal_permissions(interaction_sessions, mounted_authorization):
    authorization, environment = mounted_authorization
    async with transaction(interaction_sessions) as session:
        session.add(direct_runner())
        (await session.get(RoleBindingRecord, "rb_hookws717171717")).role_key = "viewer"
    with pytest.raises(AuthorizationError, match="permission_denied"):
        await authorization.admit_environment(name="computer", environment_id=environment.id)
    with pytest.raises(AuthorizationError, match="environment_not_found"):
        authorization.require_environment(environment.id)
    await authorization.admit_model_request()
