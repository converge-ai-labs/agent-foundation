import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness import AgentContext, HarnessBuilder, RunBindings
from a13n_harness.tools.invocation import current_invocation_scope
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_service.agents.domain import SecretRequirement
from a13n_service.digests import digest_request
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import UserRecord
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.input import AgentInput
from a13n_service.interactions.models import RunRecord
from a13n_service.secrets.agent_inputs import AgentSecretError, require_secret
from a13n_service.secrets.agent_runtime import AgentSecretRuntime
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.secrets.domain import AgentSecretBinding
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from tests.agents.test_reconstruction import (
    CHILD_REVISION_ID,
    _config,
    _edge,
    _effective,
    _reconstruct,
    _revision,
    _with_children,
)
from tests.gateway.test_commands import _commands, _Freezing, _frozen, _Preparation, _request
from tests.hooks.support import hook_actor, seed_hook_actor_access

from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .worker_helpers import accepted_running_attempt

pytestmark = pytest.mark.anyio
SECRET_ID = "sec_1234567890abcdef"
TEST_VALUE = "credential-only-in-the-tool-task"


def _binding(*, key="storage", source="workspace_secret"):
    credential = {"source": source}
    credential.update({"secret_id": SECRET_ID} if source == "workspace_secret" else {"secret_key": "storage"})
    return AgentSecretBinding.model_validate({"key": key, "credential": credential})


async def _secret(sessions, *, owner="workspace", value=TEST_VALUE):
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    async with transaction(sessions) as database:
        row = await database.get(SecretRecord, SECRET_ID)
        version = 1 if row is None else row.version + 1
        owner_id = WORKSPACE_ID if owner == "workspace" else USER_ID
        protected = protector.encrypt(
            value,
            secret_id=SECRET_ID,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            owner_type=owner,
            owner_id=owner_id,
            key="storage",
            version=version,
        )
        if row is None:
            database.add(
                SecretRecord(
                    id=SECRET_ID,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    owner_type=owner,
                    owner_id=owner_id,
                    key="storage",
                    version=version,
                    ciphertext=protected.ciphertext,
                    nonce=protected.nonce,
                    encryption_key_id=protected.encryption_key_id,
                    created_at=NOW,
                    value_updated_at=NOW,
                    deleted_at=None,
                )
            )
        else:
            row.version = version
            row.ciphertext = protected.ciphertext
            row.nonce = protected.nonce
    return protector


class _SecretTool(AbstractCapability[AgentContext]):
    id = "test.secret-consumer"

    def __init__(self, consume, *, audience="storage"):
        self.tool = HarnessTool(
            consume,
            name="use_credential",
            harness_metadata=HarnessToolMetadata(
                tool_id="test.secret-consumer",
                effects=frozenset({"read"}),
                credential_audiences=(audience,),
                idempotency="read_only",
                output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
            ),
        )

    def get_toolset(self):
        return FunctionToolset([self.tool], id="test.secret-consumer")


@pytest.mark.parametrize("owner", ["workspace", "user"])
async def test_managed_tool_resolves_current_secret_and_keeps_values_out_of_harness_state(
    interaction_sessions, interaction_object_store, owner
):
    run, attempt = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    protector = await _secret(interaction_sessions, owner=owner)
    config = _effective(_config(secret_requirements=[{"key": "storage"}]))
    binding = _binding(source="workspace_secret" if owner == "workspace" else "invoking_user_secret")
    bound = AgentSecretRuntime(interaction_sessions, protector, clock=lambda: NOW).bind(
        run=run,
        workspace_id=WORKSPACE_ID,
        config=config,
        bindings=(binding,),
        current_attempt=lambda: attempt,
    )
    await bound.validate()
    consumed = []
    leases = []
    original_acquire = bound.acquire

    async def capture(*args, **kwargs):
        lease = await original_acquire(*args, **kwargs)
        leases.append(lease)
        return lease

    bound.acquire = capture

    def consume():
        scope = current_invocation_scope()
        consumed.append(scope.credentials["storage"])
        return {"authenticated": True}

    calls = 0

    async def model(messages, info):
        nonlocal calls
        assert TEST_VALUE not in repr(messages)
        assert "rotated-credential" not in repr(messages)
        if calls < 2:
            if calls == 1:
                await _secret(interaction_sessions, owner=owner, value="rotated-credential")
            calls += 1
            yield {0: DeltaToolCall(name="use_credential", json_args="{}", tool_call_id=f"tool-{calls}")}
        else:
            yield "authenticated"

    async def resolve(ctx, model_id):
        return FunctionModel(stream_function=model)

    definition = _reconstruct(config, capability_provider=lambda _: (_SecretTool(consume),))
    result = (
        await HarnessBuilder()
        .build(definition)
        .run(
            "Use the configured credential.",
            bindings=RunBindings.embedded(model_resolver=resolve, capabilities=(bound.capability(),)),
        )
    )
    assert result.output_or_raise() == "authenticated"
    assert consumed == [TEST_VALUE, "rotated-credential"]
    assert len(leases) == 2 and all(lease.value is None for lease in leases)
    assert TEST_VALUE not in repr(result.state)
    assert "rotated-credential" not in repr(result.state)
    with pytest.raises(RuntimeError, match="No managed tool invocation"):
        current_invocation_scope()


async def test_run_acceptance_freezes_only_declared_secret_references(
    interaction_sessions, interaction_object_store, tmp_path
):
    await seed_hook_actor_access(interaction_sessions)
    await _secret(interaction_sessions)
    frozen = _frozen()
    config = frozen.effective_config.model_copy(update={"secret_requirements": (SecretRequirement(key="storage"),)})
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    commands = _commands(
        interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([replace(frozen, effective_config=config)]),
    )
    request = _request().model_copy(
        update={
            "input": AgentInput(schema_version="2", content=_request().input.content, secret_bindings=(_binding(),))
        }
    )
    receipt = await commands.runs.start(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, idempotency_key="secret-bound", request=request
    )
    async with short_session(interaction_sessions) as database:
        run = (await database.get(RunRecord, receipt.run_id)).to_resource()
    from a13n_service.interactions.objects import RunStateStore

    state = await RunStateStore(interaction_object_store).read_run(run)
    assert state.envelope.secret_bindings == (_binding(),)
    assert run.input["secret_bindings"] == [_binding().model_dump(mode="json")]
    assert TEST_VALUE not in json.dumps(run.model_dump(mode="json"))
    assert TEST_VALUE not in state.envelope.model_dump_json()


@pytest.mark.parametrize(
    ("bindings", "code"),
    [
        ((), "input_secret_required"),
        ((_binding(key="undeclared"),), "input_secret_undeclared"),
        ((_binding(), _binding()), "input_secret_undeclared"),
    ],
)
async def test_missing_or_undeclared_bindings_fail_before_run_acceptance(
    interaction_sessions, interaction_object_store, bindings, code
):
    await seed_hook_actor_access(interaction_sessions)
    frozen = _frozen()
    config = frozen.effective_config.model_copy(update={"secret_requirements": (SecretRequirement(key="storage"),)})
    commands = _commands(
        interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([replace(frozen, effective_config=config)]),
    )
    request = _request().model_copy(update={"input": _request().input.model_copy(update={"secret_bindings": bindings})})
    with pytest.raises(AgentSecretError) as error:
        await commands.runs.start(
            actor=hook_actor(), workspace_id=WORKSPACE_ID, idempotency_key="invalid-secrets", request=request
        )
    assert error.value.code == code


async def test_workspace_reference_cannot_resolve_a_user_owned_secret(interaction_sessions):
    await seed_hook_actor_access(interaction_sessions)
    await _secret(interaction_sessions, owner="user")
    async with short_session(interaction_sessions) as database:
        with pytest.raises(AgentSecretError) as error:
            await require_secret(database, actor=hook_actor(), binding=_binding(), accepting=True)
    assert error.value.code == "input_secret_unavailable"


@pytest.mark.parametrize("failure", ["deleted", "other-user", "revoked", "lost-fence", "undeclared"])
async def test_secret_use_rechecks_current_owner_authority_and_node_declaration(
    interaction_sessions, interaction_object_store, failure
):
    run, attempt = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    protector = await _secret(interaction_sessions, owner="user")
    config = _effective(_config(secret_requirements=[{"key": "storage"}]))
    bound = AgentSecretRuntime(interaction_sessions, protector, clock=lambda: NOW).bind(
        run=run,
        workspace_id=WORKSPACE_ID,
        config=config,
        bindings=(_binding(source="invoking_user_secret"),),
        current_attempt=lambda: attempt,
    )
    await bound.validate()
    async with transaction(interaction_sessions) as database:
        if failure == "deleted":
            row = await database.get(SecretRecord, SECRET_ID)
            row.deleted_at = NOW
            row.ciphertext = row.nonce = row.encryption_key_id = None
        elif failure == "other-user":
            row = await database.get(SecretRecord, SECRET_ID)
            row.owner_id = "usr_somebodyelse"
        elif failure == "revoked":
            row = await database.get(UserRecord, USER_ID)
            row.status = "disabled"
        elif failure == "lost-fence":
            row = await database.get(RunRecord, run.id)
            row.current_run_attempt_id = None
    context = SimpleNamespace(instance=SimpleNamespace(parent_agent_instance_id=None))
    with pytest.raises((AgentSecretError, AuthorizationError, AttemptAuthorityError)):
        await bound.acquire("undeclared" if failure == "undeclared" else "storage", None, context=context)


async def test_optional_secret_can_be_absent_and_old_input_serialization_is_stable(
    interaction_sessions, interaction_object_store
):
    run, attempt = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    config = _effective(_config(secret_requirements=[{"key": "storage", "required": False}]))
    bound = AgentSecretRuntime(
        interaction_sessions, SecretProtector(key=b"k" * 32, encryption_key_id="test"), clock=lambda: NOW
    ).bind(run=run, workspace_id=WORKSPACE_ID, config=config, bindings=(), current_attempt=lambda: attempt)
    await bound.validate()
    assert "secret_bindings" not in AgentInput(schema_version="1").model_dump()


@pytest.mark.parametrize("child_declares", [True, False])
async def test_inline_child_can_use_only_its_own_declared_secret(
    interaction_sessions, interaction_object_store, child_declares
):
    run, attempt = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    protector = await _secret(interaction_sessions)
    child = _revision(
        agent_id=run.agent_id, config=_config(secret_requirements=[{"key": "storage"}] if child_declares else [])
    )
    config = _with_children(
        _effective(
            _config(secret_requirements=[{"key": "storage"}]),
            subagents=(_edge("helper", child_agent_id=run.agent_id).model_copy(update={"usage_limits": None}),),
        ),
        {CHILD_REVISION_ID: child},
    )
    bound = AgentSecretRuntime(interaction_sessions, protector, clock=lambda: NOW).bind(
        run=run,
        workspace_id=WORKSPACE_ID,
        config=config,
        bindings=(_binding(),),
        current_attempt=lambda: attempt,
    )
    consumed = []

    def consume():
        consumed.append(current_invocation_scope().credentials["storage"])
        return "authenticated"

    requests = {"root": 0, "child": 0}

    def model_for(role):
        async def model(messages, info):
            requests[role] += 1
            if requests[role] == 1:
                yield {
                    0: DeltaToolCall(
                        name="delegate" if role == "root" else "use_credential",
                        json_args=json.dumps({"subagent": "helper", "prompt": "Use your credential."})
                        if role == "root"
                        else "{}",
                        tool_call_id=role,
                    )
                }
            else:
                yield role + " completed"

        return FunctionModel(stream_function=model)

    async def resolve(context, model_id):
        return model_for("child" if context.deps.instance.parent_agent_instance_id else "root")

    definition = _reconstruct(config, capability_provider=lambda node: () if node.is_root else (_SecretTool(consume),))
    result = (
        await HarnessBuilder()
        .build(definition)
        .run(
            "Delegate the work.",
            bindings=RunBindings.embedded(model_resolver=resolve, capabilities=(bound.capability(),)),
        )
    )
    assert result.output_or_raise() == "root completed"
    assert requests["child"] == 2
    assert consumed == ([TEST_VALUE] if child_declares else [])
    assert TEST_VALUE not in repr(result.state)
