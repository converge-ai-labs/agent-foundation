"""Protected source authority participates in the shared Attempt refresh cadence."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.agent_configuration.execution import ConfigurationExecution
from a13n_service.agent_configuration.inputs import ConfigurationInputRequest
from a13n_service.agents.models import AgentRecord
from a13n_service.iam import AuthorizationError
from a13n_service.iam.attempts import AttemptAuthorization, AttemptAuthorizationError
from a13n_service.iam.authorization import PrincipalPermissions, WorkspaceAction
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session, transaction

from ..agents.conftest import ORG_ID, WORKSPACE_ID, actor
from .test_drafts import new_draft, services
from .test_inputs import inputs_service

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("revocation", ["binding", "assistant", "permission"])
async def test_protected_root_refresh_does_not_grant_invoke_to_children(
    agent_sessions, tmp_path, monkeypatch, revocation
):
    conversations, drafts, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    thread_id = (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
    inputs, objects = await inputs_service(agent_sessions, tmp_path)
    receipt = await inputs.submit(
        actor=actor(),
        thread_id=thread_id,
        idempotency_key="protected-refresh",
        request=ConfigurationInputRequest.model_validate(
            {
                "expected_thread_version": 1,
                "input": {"schema_version": "2", "content": [{"type": "text", "text": "Create an agent"}]},
            }
        ),
    )
    async with short_session(agent_sessions) as session:
        run = (await session.get(RunRecord, receipt.run_id)).to_resource()
    # Supply a valid create-only permission snapshot. The real protected policy
    # must check the persisted binding without synthesizing agent.invoke.
    snapshot = PrincipalPermissions(
        principal=actor().principal,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        workspace_actions=frozenset({WorkspaceAction.agent_create}),
        agent_actions=(),
    )
    read_permissions = AsyncMock(return_value=snapshot)
    monkeypatch.setattr("a13n_service.iam.attempts.read_principal_permissions", read_permissions)
    source = ConfigurationExecution(agent_sessions, run, WORKSPACE_ID, Mock(), drafts, RunDisplayStore(objects))
    authorization = AttemptAuthorization()
    await authorization.initialize(
        agent_sessions,
        principal=run.authority_principal,
        organization_id=run.organization_id,
        workspace_id=WORKSPACE_ID,
        root_agent_id=run.agent_id,
        agent_ids=frozenset({"agt_child717171717"}),
        run_id=run.id,
        run_attempt_id="ratt_protected717171",
        root_policy=source.root_policy,
    )
    with pytest.raises(AuthorizationError, match="permission_denied"):
        await authorization.admit_model_request(agent_id="agt_child717171717")
    with pytest.raises(AuthorizationError, match="permission_denied"):
        await authorization.admit_model_request(agent_id="agt_unknown7171717")
    async with transaction(agent_sessions) as session:
        if revocation == "binding":
            conversation = await session.get(SessionRecord, run.session_id)
            conversation.configuration_owner_user_id = None
            conversation.configuration_draft_id = None
        elif revocation == "assistant":
            (await session.get(AgentRecord, run.agent_id)).enabled = False
        else:
            read_permissions.return_value = replace(snapshot, workspace_actions=frozenset())
    for _ in range(10):
        await authorization.admit_model_request()
    assert read_permissions.await_count == 1
    assert WorkspaceAction.agent_invoke not in authorization.snapshot.for_agent(run.agent_id)
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await authorization.admit_model_request()
    assert read_permissions.await_count == 2
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        _ = authorization.snapshot
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await authorization.admit_model_request()
