import pytest
from a13n_service.connectivity.accounts.reception import InputOverride, Reception
from a13n_service.connectivity.accounts.targets import ReplaceTargetRequest, TargetConfig
from a13n_service.connectivity.errors import NativeError
from pydantic import ValidationError

from .conftest import ACCOUNT_ID, AGENT_ID, actor


def target_request(**changes):
    return TargetConfig(target_kind="conversation", external_target_id="support", **changes)


async def test_exact_target_identity_idempotency_and_uniqueness(target_service):
    request = target_request()
    first = await target_service.create(actor=actor(), account_id=ACCOUNT_ID, idempotency_key="target", request=request)
    replay = await target_service.create(
        actor=actor(), account_id=ACCOUNT_ID, idempotency_key="target", request=request
    )
    assert replay == first
    assert first.agent_id is None
    with pytest.raises(NativeError, match="already configured"):
        await target_service.create(actor=actor(), account_id=ACCOUNT_ID, idempotency_key="another", request=request)
    with pytest.raises(NativeError) as error:
        await target_service.create(
            actor=actor(), account_id=ACCOUNT_ID, idempotency_key="target", request=target_request(agent_id=AGENT_ID)
        )
    assert error.value.code == "idempotency_conflict"


async def test_full_replacement_resets_inheritance_and_checks_version(target_service):
    first = await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="target",
        request=target_request(agent_id=AGENT_ID, config_override=InputOverride(skills=())),
    )
    assert first.config_override.skills == ()
    updated = await target_service.replace(
        actor=actor(),
        account_id=ACCOUNT_ID,
        target_id=first.id,
        request=ReplaceTargetRequest(expected_version=1, target_kind="conversation", external_target_id="support"),
    )
    assert updated.version == 2
    assert updated.agent_id is None and updated.config_override is None
    with pytest.raises(NativeError) as error:
        await target_service.delete(actor=actor(), account_id=ACCOUNT_ID, target_id=first.id, expected_version=1)
    assert error.value.code == "version_conflict"
    await target_service.delete(actor=actor(), account_id=ACCOUNT_ID, target_id=first.id, expected_version=2)
    assert (await target_service.list(actor=actor(), account_id=ACCOUNT_ID, limit=50, cursor=None)).items == ()


@pytest.mark.parametrize("field", ["instructions", "plugins", "subagents", "client_tools", "output_spec", "retries"])
def test_only_four_override_categories_are_accepted(field):
    with pytest.raises(ValidationError):
        InputOverride.model_validate({field: []})


def test_override_distinguishes_inheritance_and_empty_selection():
    assert InputOverride().invocation_override().model_dump(exclude_unset=True) == {}
    assert InputOverride(skills=()).invocation_override().model_dump(exclude_unset=True) == {"skills": ()}
    assert Reception().default_agent_id is None
    with pytest.raises(ValidationError):
        Reception(receive_enabled=True)


@pytest.mark.parametrize("field", ["skills", "connection_tools", "connection_tools"])
def test_null_override_categories_are_rejected_at_configuration(field):
    with pytest.raises(ValidationError):
        InputOverride.model_validate({field: None})
