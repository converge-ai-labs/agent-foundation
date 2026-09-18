"""Debug classification survives durable acceptance and never changes existing Sessions."""

import pytest
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.interactions.command_values import ContinueRunCommand
from a13n_service.interactions.domain import SessionPurpose
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import SessionRecord
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore

from tests.gateway.test_commands import (
    _actor,
    _commands,
    _complete_run,
    _Freezing,
    _frozen,
    _Preparation,
    _request,
)
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import WORKSPACE_ID

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("purpose", list(SessionPurpose))
async def test_session_purpose_is_durable_across_replay_and_continuation(
    lifecycle_interaction_sessions, tmp_path, purpose
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    request = _request().model_copy(update={"session_purpose": purpose})
    first = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="debug", request=request
    )
    assert (
        await commands.runs.start(actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="debug", request=request)
        == first
    )
    queries = NativeInteractionQueries(sessions, RunDisplayStore(objects))
    thread = await queries.get_thread(actor=_actor(), thread_id=first.thread_id)
    assert thread.session_purpose == purpose
    page = await queries.list_sessions(actor=_actor(), workspace_id=WORKSPACE_ID, limit=100, cursor=None)
    assert next(item for item in page.items if item.id == first.session_id).purpose == purpose
    await _complete_run(sessions, objects, run_id=first.run_id)
    thread = await queries.get_thread(actor=_actor(), thread_id=first.thread_id)
    second = await commands.runs.continue_from(
        actor=_actor(),
        source_run_id=first.run_id,
        idempotency_key="next",
        request=ContinueRunCommand(expected_thread_version=thread.version, input=_request("next").input),
    )
    assert second.session_id == first.session_id
    assert (await queries.get_thread(actor=_actor(), thread_id=second.thread_id)).session_purpose == purpose
    with pytest.raises(InteractionCommandError) as error:
        await commands.runs.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="change-purpose",
            request=_request().model_copy(
                update={"session_id": first.session_id, "session_purpose": SessionPurpose.execution}
            ),
        )
    assert error.value.code == "session_purpose_immutable"
    async with short_session(sessions) as database:
        stored = await database.get(SessionRecord, first.session_id)
        assert stored.to_resource().purpose == purpose
