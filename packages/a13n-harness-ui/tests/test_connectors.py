from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import ClassVar

import pytest
from a13n_harness_ui.app import HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorInput, ConnectorRule, StateChange
from a13n_harness_ui.connectors import Controller, Declaration, Definition, EventDeclaration, Status
from a13n_harness_ui.connectors.catalog import Catalog
from a13n_harness_ui.connectors.forms import safe_values, validate_form
from a13n_harness_ui.connectors.github import GITHUB_ENDPOINT
from a13n_harness_ui.connectors.models import Capture, SetupCommand
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_adapters import resolve_mcp_values
from anyio import Event, create_task_group, get_current_task
from pydantic import BaseModel, ConfigDict, ValidationError

from .test_app import _settings, _write_configuration
from .test_bot import ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


class SampleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "example"


class SampleConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str


class SampleDefinition(Definition):
    configuration_model = SampleSettings
    condition_models: ClassVar[dict[str, type[BaseModel] | None]] = {"changed": SampleConditions, "tick": None}

    def __init__(self):
        self.started = []
        self.stopped = []
        self.controllers = []

    def declaration(self):
        return Declaration(
            key="sample",
            name="Sample",
            description="A third-party producer",
            configuration_schema=SampleSettings.model_json_schema(),
            bot_delivery=True,
            events=(
                EventDeclaration(key="changed", name="Changed", conditions_schema=SampleConditions.model_json_schema()),
                EventDeclaration(key="tick", name="Tick"),
            ),
        )

    @asynccontextmanager
    async def open(self, context):
        controller = SampleController(context, self)
        self.controllers.append(controller)
        yield controller


class SampleController(Controller):
    def __init__(self, context, definition):
        super().__init__(context)
        self.definition = definition

    async def status(self):
        return Status(state="ready", summary="Ready")

    async def run(self, configuration):
        self.definition.started.append(configuration.version)
        try:
            await Event().wait()
        finally:
            self.definition.stopped.append(configuration.version)


async def create_github(app, *, connector_id="connector-test"):
    return await app.configure_connector(
        ConnectorConfiguration(connector_id=connector_id, kind="github", group_name=connector_id), expected_version=None
    )


async def save_github(app, connector_id="connector-test"):
    await create_github(app, connector_id=connector_id)
    setup = await app.begin_connector_setup(connector_id)
    values = {"token": "test-private-token"}
    result = await app.act_connector_setup(
        setup.session_id, SetupCommand(operation_id="save", version=setup.version, action="save", values=values)
    )
    assert result.step.state == "completed", result.step.error
    assert result.step.actions == ()
    return result


@pytest.mark.parametrize("group_name", ["github", "work-github-2", "a" * 32])
def test_connector_groups_are_canonical_tool_search_names(group_name):
    from a13n_harness.tools.tool_search import validate_group

    configuration = ConnectorConfiguration(connector_id="account", kind="github", group_name=group_name)
    validate_group(configuration.group_name, "Account tools")
    capture = Capture(
        connector_id="account",
        connector_key="github",
        connector_name="Personal GitHub",
        group_name=group_name,
        configuration_version=1,
        implementation="example:GitHub",
    )
    assert Capture.model_validate_json(capture.model_dump_json()) == capture


@pytest.mark.parametrize(
    "group_name", ["", "GitHub", "a__b", "two words", "-github", "1github", "a--b", "a-", "a" * 33]
)
def test_connector_group_rejects_noncanonical_values(group_name):
    with pytest.raises(ValidationError):
        ConnectorConfiguration(connector_id="account", kind="github", group_name=group_name)


async def test_group_reservations_are_atomic_editable_and_released_on_delete(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        repository = app._store.connectors
        successes, failures = [], []

        async def create(connector_id):
            try:
                successes.append(
                    await repository.configure(
                        ConnectorConfiguration(
                            connector_id=connector_id,
                            kind="github",
                            group_name="github",
                            enabled=False,
                            credentials={"token": "private-fixture"},
                        )
                    )
                )
            except HarnessUiError as exc:
                failures.append(exc.code)

        async with create_task_group() as group:
            group.start_soon(create, "account-one")
            group.start_soon(create, "account-two")
        assert len(successes) == 1 and failures == ["connector_group_conflict"]
        current = successes[0]
        saved = await repository.configure(current, expected_version=current.version)
        assert saved.group_name == "github"  # Saving oneself retains the reservation.
        other = await repository.configure(
            ConnectorConfiguration(
                connector_id="account-other",
                kind="other-service",
                group_name="other",
            )
        )
        with pytest.raises(HarnessUiError) as conflict:
            await repository.configure(
                other.model_copy(update={"group_name": "github"}), expected_version=other.version
            )
        assert conflict.value.code == "connector_group_conflict"
        assert (await repository.configuration(other.connector_id)).group_name == "other"
        with pytest.raises(HarnessUiError) as invalid:
            await repository.configure(
                other.model_copy(update={"group_name": "INVALID"}), expected_version=other.version
            )
        assert invalid.value.code == "connector_group_invalid"
        renamed = await repository.configure(
            saved.model_copy(update={"name": "New display name", "group_name": "personal"}),
            expected_version=saved.version,
        )
        assert renamed.version == saved.version + 1
        assert renamed.credentials == {"token": "private-fixture"}
        with pytest.raises(HarnessUiError):
            await repository.configure(saved, expected_version=saved.version)
        moved = await repository.configure(
            other.model_copy(update={"group_name": "github"}), expected_version=other.version
        )
        await repository.delete(moved.connector_id, expected_version=moved.version)
        replacement = await repository.configure(
            ConnectorConfiguration(connector_id="replacement", kind="github", group_name="github")
        )
        assert replacement.group_name == "github"
        with pytest.raises(HarnessUiError) as retired:
            await repository.configure(moved.model_copy(update={"group_name": "unused"}))
        assert retired.value.code == "connector_identity_retired"


async def test_remote_mcp_setup_uses_private_references_and_native_capture(tmp_path):
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        setup = await save_github(app)
        instance = await app.connectors().instance("connector-test")
        assert instance.status.state == "ready"
        assert instance.settings == {}
        assert "test-private-token" not in instance.model_dump_json() + setup.model_dump_json()
        captures = await app.connectors().capture()
        assert len(captures) == 1
        assert "test-private-token" not in json.dumps([capture.model_dump(mode="json") for capture in captures])
        source = app.connectors().capabilities(captures)["connector-test"].wrapped
        assert source._recipe.transport.url == GITHUB_ENDPOINT
        values = await resolve_mcp_values(source._recipe.transport.headers, None, private_values=source._private_values)
        assert values == {"Authorization": "Bearer test-private-token"}
        current = await app._store.connectors.configuration("connector-test")
        await app._store.connectors.save_credentials(
            "connector-test",
            expected_version=current.credential_version,
            credentials={"token": "Bearer rotated"},
        )
        # Private rotation does not invalidate a human form or captured identity.
        assert (await app.connectors().instance("connector-test")).version == instance.version
        assert await resolve_mcp_values(
            source._recipe.transport.headers, None, private_values=source._private_values
        ) == {"Authorization": "Bearer rotated"}
        await app.configure_connector(current.model_copy(update={"enabled": False}), expected_version=current.version)
        with pytest.raises(HarnessUiError, match="disabled"):
            await source._operation_guard()
        assert await app.connectors().capture() == ()


async def test_remote_setup_exact_retry_stale_step_and_omitted_secret(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await ready(app)
        await save_github(app)
        setup = await app.begin_connector_setup("connector-test")
        assert "token" not in setup.step.values
        command = SetupCommand(operation_id="second", version=setup.version, action="save", values={})
        saved = await app.act_connector_setup(setup.session_id, command)
        assert await app.act_connector_setup(setup.session_id, command) == saved
        with pytest.raises(HarnessUiError, match="different content"):
            await app.act_connector_setup(setup.session_id, command.model_copy(update={"values": {"token": "changed"}}))
        private = await app._store.connectors.configuration("connector-test")
        assert private.credentials["token"] == "Bearer test-private-token"
        with pytest.raises(HarnessUiError) as exc:
            await app.act_connector_setup(setup.session_id, command.model_copy(update={"operation_id": "another"}))
        assert exc.value.code == "connector_setup_conflict"
        assert (await app.connectors().instance("connector-test")).version == 3


async def test_setup_reopen_does_not_create_another_session_and_conflicts_retain_state(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        setup = await app.begin_connector_setup("connector-test")
        assert (await app.begin_connector_setup("connector-test")).session_id == setup.session_id
        current = await app._store.connectors.configuration("connector-test")
        await app.configure_connector(current.model_copy(update={"name": "Renamed"}), expected_version=current.version)
        with pytest.raises(HarnessUiError) as failure:
            await app.act_connector_setup(
                setup.session_id,
                SetupCommand(
                    operation_id="stale", version=setup.version, action="save", values={"token": "test-private-token"}
                ),
            )
        assert failure.value.code == "connector_setup_conflict"
        result = await app.connectors().setup(setup.session_id)
        assert result.step.state == "failed"
        assert "changed" in result.step.error
        assert not (await app._store.connectors.configuration("connector-test")).credentials


async def test_shared_owner_keeps_bot_and_connector_authority_in_one_app(tmp_path):
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, host_mode="webui") as first:
        await ready(first)
        await create_github(first)
        async with open_harness_ui_app(settings, host_mode="webui") as second:
            assert not (await second.bot().snapshot()).available
            assert (await second.connectors().instance("connector-test")).status.state == "runtime_unavailable"
            with pytest.raises(HarnessUiError) as exc:
                await second.begin_connector_setup("connector-test")
            assert exc.value.code == "connector_runtime_unavailable"
        assert first.bot()._owned
    async with open_harness_ui_app(settings, host_mode="local") as terminal:
        with pytest.raises(HarnessUiError):
            terminal.connectors()


async def test_atomic_state_and_input_bounds_dedup_rules_and_generation(tmp_path):
    definition = SampleDefinition()
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), host_mode="webui", integrations=HarnessUiIntegrations(connectors=(definition,))
    ) as app:
        await ready(app)
        await app.configure_connector(
            ConnectorConfiguration(
                connector_id="connector-source",
                group_name="sample-source",
                kind="sample",
                deliver_to_bot=True,
                rules=(ConnectorRule(id="rule", event="changed", conditions={"label": "selected"}),),
            ),
            expected_version=None,
        )
        repository = app._store.connectors
        context = definition.controllers[0].context
        event = ConnectorInput(
            connector_id=context.connector_id,
            event_type="plugin-decided",
            source_event_id="event-1",
            text="External content",
        )
        retained = await context.commit(
            configuration_version=1,
            state_changes=(StateChange(key="cursor", value={"codec": 1, "position": 1}),),
            bot_inputs=(event,),
        )
        assert retained == (event.input_id,)
        assert (
            await context.commit(configuration_version=1, bot_inputs=(event.model_copy(update={"input_id": "new-id"}),))
            == retained
        )
        state = await context.state("cursor")
        assert state.value["position"] == 1
        with pytest.raises(HarnessUiError):
            await context.commit(
                configuration_version=1,
                state_changes=(StateChange(key="cursor", value=2),),
                bot_inputs=(event.model_copy(update={"source_event_id": "event-2"}),),
            )
        assert len(await app._store.bot_inbox.pending()) == 1
        current = await repository.configuration(context.connector_id)
        await app.configure_connector(current.model_copy(update={"rules": ()}), expected_version=1)
        # Rule edits never remove already-retained matching decisions.
        assert len(await app._store.bot_inbox.pending()) == 1
        with pytest.raises(HarnessUiError):
            await context.commit(
                configuration_version=1,
                state_changes=(StateChange(key="cursor", expected_version=state.version, value=2),),
            )
        current = await repository.configuration(context.connector_id)
        await app.configure_connector(current.model_copy(update={"deliver_to_bot": False}), expected_version=2)
        with pytest.raises(HarnessUiError) as exc:
            await context.commit(
                configuration_version=3,
                state_changes=(StateChange(key="cursor", expected_version=state.version, value=3),),
                bot_inputs=(event,),
            )
        assert exc.value.code == "connector_delivery_disabled"
        assert (await context.state("cursor")) == state
        assert await app._store.bot_inbox.pending() == ()
        # Ignored events explicitly commit state without an input.
        await context.commit(
            configuration_version=3,
            state_changes=(StateChange(key="cursor", expected_version=state.version, value=None),),
        )
        stored_null = await context.state("cursor")
        assert stored_null is not None and stored_null.value is None
        await context.commit(
            configuration_version=3,
            state_changes=(StateChange(key="cursor", expected_version=stored_null.version, delete=True),),
        )
        assert await context.state("cursor") is None
        await context.commit(configuration_version=3, state_changes=(StateChange(key="cursor", value="recreated"),))
        with pytest.raises(HarnessUiError):
            await context.commit(
                configuration_version=3,
                state_changes=(StateChange(key="cursor", expected_version=state.version, value="stale"),),
            )
        with pytest.raises(HarnessUiError) as exc:
            await context.commit(
                configuration_version=3, state_changes=(StateChange(key="too-large", value="雪" * 22000),)
            )
        assert exc.value.code == "connector_state_too_large"
        assert definition.started == [1, 2, 3]
        assert definition.stopped == [1, 2]


async def test_form_profile_keeps_secret_bytes_out_of_nested_safe_values():
    from pydantic import Field

    class SecretRow(BaseModel):
        name: str
        value: str = Field(json_schema_extra={"writeOnly": True})

    class Form(BaseModel):
        headers: list[SecretRow]

    schema = Form.model_json_schema()
    validate_form(schema)
    safe = safe_values(
        schema, {"url": "https://example.test", "headers": [{"name": "Authorization", "value": "secret"}]}
    )
    assert safe["headers"] == [{"name": "Authorization"}]
    assert "secret" not in json.dumps(safe)
    with pytest.raises(ValueError, match="local"):
        validate_form({"$ref": "https://evil.test/schema"})


async def test_catalog_collision_is_unavailable_without_hiding_other_types():
    sample = SampleDefinition()
    catalog = Catalog((sample, SampleDefinition()), installed=())
    assert not catalog.items["sample"].available
    assert "remote-mcp" not in catalog.items
    assert catalog.items["github"].available
    assert catalog.items["github"].declaration.events == ()
    assert not catalog.items["github"].declaration.bot_delivery


async def test_controller_context_has_one_owning_task_through_root_drain(tmp_path):
    class ResourceDefinition(SampleDefinition):
        @asynccontextmanager
        async def open(self, context):
            task_id = get_current_task().id
            async with create_task_group():
                controller = SampleController(context, self)
                self.controllers.append(controller)
                try:
                    yield controller
                finally:
                    assert get_current_task().id == task_id
                    self.closed = True

    definition = ResourceDefinition()
    definition.closed = False
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), host_mode="webui", integrations=HarnessUiIntegrations(connectors=(definition,))
    ) as app:
        # Opening from an API-operation task must not enter a task group's scope
        # on that request's stack or exit it from App cleanup's task.
        async with create_task_group() as requests:

            async def configure():
                await app.configure_connector(
                    ConnectorConfiguration(
                        connector_id="connector-resource", kind="sample", group_name="sample-resource"
                    ),
                    expected_version=None,
                )

            requests.start_soon(configure)
        await app.connectors().stop()
        assert not definition.closed
        await app.connectors().close()
        assert definition.closed


@pytest.mark.parametrize("explicit_cancel", [False, True])
async def test_setup_shutdown_or_explicit_cancel_interrupts_action_before_closing_flow(tmp_path, explicit_cancel):
    from a13n_harness_ui.connectors import SetupAction, SetupFlow, Step

    entered, closed = Event(), Event()
    acting = False

    class Flow(SetupFlow):
        async def read(self):
            return Step(key="confirm", title="Confirm", actions=(SetupAction(id="confirm", label="Confirm"),))

        async def act(self, action, values):
            nonlocal acting
            acting = True
            entered.set()
            try:
                await Event().wait()
            finally:
                acting = False
            return await self.read()

        async def close(self):
            assert not acting
            closed.set()

    class SetupController(SampleController):
        async def begin_setup(self, intent):
            return Flow()

    class SetupDefinition(SampleDefinition):
        @asynccontextmanager
        async def open(self, context):
            yield SetupController(context, self)

    async with open_harness_ui_app(
        _settings(tmp_path / "data"),
        host_mode="webui",
        integrations=HarnessUiIntegrations(connectors=(SetupDefinition(),)),
    ) as app:
        await app.configure_connector(
            ConnectorConfiguration(connector_id="connector-flow", kind="sample", group_name="sample-flow"),
            expected_version=None,
        )
        snapshot = await app.begin_connector_setup("connector-flow")
        results = []
        command = SetupCommand(operation_id="confirm", action="confirm", version=snapshot.version)
        async with create_task_group() as requests:

            async def act():
                results.append(await app.act_connector_setup(snapshot.session_id, command))

            requests.start_soon(act)
            await entered.wait()
            if explicit_cancel:
                from anyio import fail_after

                cancel = SetupCommand(operation_id="cancel", action="cancel", version=snapshot.version)
                with fail_after(2):
                    cancelled = await app.act_connector_setup(snapshot.session_id, cancel)
                assert cancelled.step.state == "cancelled"
                assert await app.act_connector_setup(snapshot.session_id, cancel) == cancelled
            else:
                await app.connectors().stop()
        await closed.wait()
        assert results[0].step.state == "failed"
        assert (await app.connectors().setup(snapshot.session_id)).step.state == (
            "cancelled" if explicit_cancel else "failed"
        )


async def test_form_profile_rejects_cycles_unsupported_unions_and_secret_defaults():
    for schema in (
        {"$ref": "#/$defs/Node", "$defs": {"Node": {"properties": {"child": {"$ref": "#/$defs/Node"}}}}},
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        {"type": "string", "writeOnly": True, "default": "private-value"},
    ):
        with pytest.raises(ValueError):
            validate_form(schema)


@pytest.mark.parametrize("token", ["bad\r\ntoken", "\nfake-token", "fake-token\x7f"])
async def test_github_setup_rejects_token_controls(tmp_path, token):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        setup = await app.begin_connector_setup("connector-test")
        result = await app.act_connector_setup(
            setup.session_id,
            SetupCommand(operation_id="invalid", version=setup.version, action="save", values={"token": token}),
        )
        assert result.step.error
        assert not (await app._store.connectors.configuration("connector-test")).credentials


async def test_deleted_identity_never_rebinds_old_mcp_capture(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await save_github(app)
        runtime = app.connectors()
        captures = await runtime.capture()
        source = runtime.capabilities(captures)["connector-test"].wrapped
        current = await app._store.connectors.configuration("connector-test")
        await app.delete_connector("connector-test", expected_version=current.version)
        assert await runtime.instances() == ()
        with pytest.raises(HarnessUiError) as exc:
            await create_github(app)
        assert exc.value.code == "connector_identity_retired"
        with pytest.raises(HarnessUiError):
            await source._operation_guard()
        with pytest.raises(HarnessUiError):
            await resolve_mcp_values(source._recipe.transport.headers, None, private_values=source._private_values)


async def test_reopen_setup_does_not_hold_configuration_lock_while_action_saves(tmp_path):
    from anyio import fail_after, wait_all_tasks_blocked

    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        runtime = app.connectors()
        setup = await app.begin_connector_setup("connector-test")
        completed = []
        with fail_after(3):
            async with create_task_group() as requests:

                async def act():
                    completed.append(
                        await app.act_connector_setup(
                            setup.session_id,
                            SetupCommand(
                                operation_id="save",
                                version=setup.version,
                                action="save",
                                values={"token": "test-private-token"},
                            ),
                        )
                    )

                async def reopen():
                    await app.begin_connector_setup("connector-test")

                async with runtime._lock:
                    requests.start_soon(act)
                    await wait_all_tasks_blocked()
                    requests.start_soon(reopen)
                    await wait_all_tasks_blocked()
        assert completed[0].step.state == "completed"
        assert (await runtime.instance("connector-test")).status.state == "ready"


@pytest.mark.parametrize(
    "codeact,change", [(False, None), (True, None), (True, "disable"), (True, "rename"), (True, "group")]
)
async def test_real_native_mcp_scoped_tools_and_durable_setup_offer(tmp_path, monkeypatch, codeact, change):
    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import McpRemoteTransport
    from a13n_harness_ui.connectors.remote_mcp import RemoteMcp, RemoteMcpService
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from fastmcp import FastMCP
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_tool_search_integration import _http_mcp

    root = _write_configuration(tmp_path)
    root.write_text(
        root.read_text() + f"tools: {{enable_codeact: {str(codeact).lower()}, execution: {{mode: inline}}}}\n"
    )
    server = FastMCP("connector-native")
    calls = []

    @server.tool()
    async def lookup(query: str) -> str:
        calls.append(query)
        if change is not None:
            current = await app._store.connectors.configuration("connector-first")
            update = {
                "disable": {"enabled": False},
                "rename": {"name": "Renamed account"},
                "group": {"group_name": "renamed-documents"},
            }[change]
            await app.configure_connector(current.model_copy(update=update), expected_version=current.version)
        return "Native result: " + query

    group = "work-documents"
    empty_group = "empty-documents"
    source = f"""directory = await list_connectors()
assert directory['groups'][0]['group'] == {group!r}
assert 'Work documents' in directory['groups'][0]['description']
found = await discover_connector_tools(group={group!r}, query='lookup', detail='full')
assert found['tools'][0]['codeact_eligible']
definition = await describe_connector_tool(group={group!r}, tool='lookup')
assert definition['tool']['parameters_json_schema']['properties']['query']['type'] == 'string'
try:
    await call_connector_tool(group={empty_group!r}, tool='lookup', arguments={{'query': 'denied'}})
except Exception:
    pass
else:
    raise ValueError('Empty account unexpectedly exposed a tool')
await call_connector_tool(group={group!r}, tool='lookup', arguments={{'query': 'direct'}})"""
    if change is not None:
        source += f"\nawait call_connector_tool(group={group!r}, tool='lookup', arguments={{'query': 'after-change'}})"
    commands = [
        ("discover_connector_tools", {"group": group, "query": "", "detail": "full"}),
        ("discover_connector_tools", {"group": empty_group, "query": ""}),
        ("run_code", {"code": source})
        if codeact
        else ("call_connector_tool", {"group": group, "tool": "lookup", "arguments": {"query": "direct"}}),
        ("offer_connector_setup", {"connector_key": "github", "text": "Connect your GitHub account"}),
    ]
    seen = []

    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert "offer_connector_setup" in names
        assert "call_connector_tool" in names
        assert "lookup" not in names
        if codeact:
            assert "async def call_connector_tool" in info.instructions
            assert "async def describe_connector_tool" in info.instructions
            assert "async def list_connector_tools" in info.instructions
            assert "async def list_connectors" in info.instructions
            assert "async def lookup" not in info.instructions
            assert f"{group}__lookup" not in info.instructions
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returns:
            seen.append(returns[-1])
        index = len(returns)
        if index < len(commands):
            name, args = commands[index]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"connector-call-{index}")}
        else:
            yield "done"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    # A developer supplies the service endpoint and native tool policy in code.
    # Neither is a built-in Connector configuration field or catalog type.
    tools_by_account = {"connector-first": None, "connector-empty": ()}

    class DocumentTools(RemoteMcp):
        def connection(self, configuration):
            return ResolvedMcpRecipe(
                server_id=configuration.connector_id,
                transport=McpRemoteTransport(url=url),
                tools=tools_by_account[configuration.connector_id],
            )

    class DocumentSettings(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class DocumentService(Definition):
        configuration_model = DocumentSettings

        def declaration(self):
            return Declaration(
                key="documents",
                name="Documents",
                description="Find documents",
                configuration_schema=DocumentSettings.model_json_schema(),
            )

        @asynccontextmanager
        async def open(self, context):
            yield DocumentTools(context)

    async with _http_mcp(server) as url:
        async with open_harness_ui_app(
            _settings(tmp_path / "data"),
            configuration_path=root,
            host_mode="webui",
            integrations=HarnessUiIntegrations(connectors=(DocumentService(),)),
        ) as app:
            assert "remote-mcp" not in app.connectors().catalog.items
            for connector_id in tools_by_account:
                await app.configure_connector(
                    ConnectorConfiguration(
                        connector_id=connector_id,
                        kind="documents",
                        name="Work documents",
                        group_name=group if connector_id == "connector-first" else empty_group,
                    ),
                    expected_version=None,
                )
            captured = await app.connectors().capture()
            assert captured[0].connector_name == "Work documents"
            context = await app.connectors().model_context(captured)
            assert next(item for item in context["instances"] if item["id"] == "connector-first")["group_name"] == group
            thread = await app.create_thread(title="Native Connector")
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Find a document")
            settled = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
            if change is not None:
                assert settled.status == "failed", settled
                assert calls == ["direct"]  # The second callback rechecks the captured account guard.
                if change == "rename":
                    fresh = await app.connectors().capture()
                    first = next(item for item in fresh if item.connector_id == "connector-first")
                    assert first.group_name == group
                    assert first.connector_name == "Renamed account"
                    assert captured[0].connector_name == "Work documents"
                if change == "group":
                    fresh = await app.connectors().capture()
                    first = next(item for item in fresh if item.connector_id == "connector-first")
                    assert first.group_name == "renamed-documents"
                    assert next(item for item in captured if item.connector_id == "connector-first").group_name == group
                assert await app._store.connectors.offers(thread.thread_id) == ()
                return
            assert settled.status == "completed", settled
            assert calls == ["direct"]
            discovery = next(part for part in seen if part.tool_call_id == "connector-call-0")
            assert discovery.content["tools"][0]["codeact_eligible"] is True
            empty_result = next(part for part in seen if part.tool_call_id == "connector-call-1")
            assert "lookup" not in str(empty_result.content)
            offers = await app._store.connectors.offers(thread.thread_id)
            assert len(offers) == 1 and offers[0].tool_call_id == "connector-call-3"
            assert offers[0].connector_key == "github"
            assert len(await app.connectors().instances()) == 2  # Offering creates no connection.

            service = app.connectors().service("connector-first", "mcp", api_version=1, expected_type=RemoteMcpService)
            assert [tool.name for tool in await service.list_tools()] == ["lookup"]
            await service.call_tool("lookup", {"query": "service"})
            assert calls == ["direct", "service"]
            empty = app.connectors().service("connector-empty", "mcp", api_version=1, expected_type=RemoteMcpService)
            assert await empty.list_tools() == []
            with pytest.raises(HarnessUiError) as error:
                await empty.call_tool("lookup", {"query": "denied"})
            assert error.value.code == "connector_tool_unavailable"
            current = await app._store.connectors.configuration("connector-first")
            await app.configure_connector(
                current.model_copy(update={"enabled": False}), expected_version=current.version
            )
            with pytest.raises(HarnessUiError):
                await service.call_tool("lookup", {"query": "disabled"})
            assert calls == ["direct", "service"]


async def test_connector_http_setup_cas_private_projection_and_guest_exclusion(tmp_path):
    from .test_sharing_webui import create_link, shared_server

    owner = {"Authorization": "Bearer owner-test-key"}
    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread(title="Shared offer")
        prefix, guest = await create_link(client, "thread", thread.thread_id)
        for path in ("/api/connectors", "/api/connectors/catalog", "/api/connector-setups/setup-missing"):
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers=guest)).status_code == 401
            assert (await client.get(prefix + path.removeprefix("/api"), headers=guest)).status_code == 404
        catalog = (await client.get("/api/connectors/catalog", headers=owner)).json()
        assert "remote-mcp" not in {item["key"] for item in catalog}
        rejected = await client.put(
            "/api/connectors/connector-generic", headers=owner, json={"kind": "remote-mcp", "group_name": "generic"}
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "connector_plugin_unavailable"
        assert await app.connectors().instances() == ()
        response = await client.put(
            "/api/connectors/connector-http", headers=owner, json={"kind": "github", "group_name": "github"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 1
        assert response.json()["group_name"] == "github"
        duplicate = await client.put(
            "/api/connectors/connector-duplicate", headers=owner, json={"kind": "github", "group_name": "github"}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "connector_group_conflict"
        for invalid in ["GitHub", "has space", "a--b", "a" * 33]:
            rejected_group = await client.put(
                "/api/connectors/connector-invalid", headers=owner, json={"kind": "github", "group_name": invalid}
            )
            assert rejected_group.status_code == 400
        renamed = await client.put(
            "/api/connectors/connector-http",
            headers=owner,
            json={"kind": "github", "name": "Personal", "group_name": "personal", "expected_version": 1},
        )
        assert renamed.status_code == 200
        assert renamed.json()["group_name"] == "personal"
        assert renamed.json()["version"] == 2
        assert (
            await client.put(
                "/api/connectors/connector-http", headers=owner, json={"kind": "github", "group_name": "github"}
            )
        ).status_code == 409
        setup = (await client.post("/api/connectors/connector-http/setup", headers=owner, json={})).json()
        path = f"/api/connector-setups/{setup['session_id']}/actions"
        command = {
            "operation_id": "save-http",
            "version": setup["version"],
            "action": "save",
            "values": {"token": "fixture-private-token"},
        }
        assert (await client.post(path, headers=guest, json=command)).status_code == 401
        saved = await client.post(path, headers=owner, json=command)
        assert saved.status_code == 200, saved.text
        assert saved.json()["step"]["state"] == "completed"
        assert saved.json()["step"]["actions"] == []
        assert (await client.post(path, headers=owner, json=command)).json() == saved.json()
        instances = await client.get("/api/connectors", headers=owner)
        assert "fixture-private-token" not in saved.text + instances.text
        assert instances.json()[0]["configured_secrets"] == ["token"]
        version = instances.json()[0]["version"]
        assert (
            await client.request(
                "DELETE", "/api/connectors/connector-http", headers=owner, json={"expected_version": version}
            )
        ).status_code == 204
        assert (await client.get("/api/connectors/connector-http", headers=owner)).status_code == 404


@pytest.mark.parametrize("error_type", [ValueError, HarnessUiError])
async def test_setup_corrected_submission_uses_returned_error_version_without_get(tmp_path, error_type):
    from a13n_harness_ui.connectors import SetupAction, SetupFlow, Step

    calls = []

    class Flow(SetupFlow):
        async def read(self):
            return Step(key="configure", title="Configure", actions=(SetupAction(id="save", label="Save"),))

        async def act(self, action, values):
            calls.append(values)
            if not values.get("valid"):
                if error_type is HarnessUiError:
                    raise HarnessUiError("Correct the input.", code="connector_input_invalid")
                raise ValueError("Unsafe private input must not escape")
            return Step(key="done", title="Done", state="completed")

    class SetupController(SampleController):
        async def begin_setup(self, intent):
            return Flow()

    class SetupDefinition(SampleDefinition):
        @asynccontextmanager
        async def open(self, context):
            yield SetupController(context, self)

    async with open_harness_ui_app(
        _settings(tmp_path / "data"),
        host_mode="webui",
        integrations=HarnessUiIntegrations(connectors=(SetupDefinition(),)),
    ) as app:
        await app.configure_connector(
            ConnectorConfiguration(connector_id="connector-error", kind="sample", group_name="sample-error"),
            expected_version=None,
        )
        setup = await app.begin_connector_setup("connector-error")
        invalid = SetupCommand(operation_id="invalid", version=setup.version, action="save")
        failed = await app.act_connector_setup(setup.session_id, invalid)
        assert failed.step.error
        assert "Unsafe private" not in failed.model_dump_json()
        assert await app.act_connector_setup(setup.session_id, invalid) == failed
        corrected = await app.act_connector_setup(
            setup.session_id,
            SetupCommand(operation_id="corrected", version=failed.version, action="save", values={"valid": True}),
        )
        assert corrected.step.state == "completed"
        assert len(calls) == 2


async def test_shutdown_rejects_setup_queued_before_sealing_and_keeps_session_iteration_stable(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from anyio import wait_all_tasks_blocked

    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        runtime = app.connectors()
        setup = await app.begin_connector_setup("connector-test")
        original = runtime._sessions[setup.session_id]
        # Fill the bounded registry with expired sessions: a queued admission
        # would evict these while shutdown awaits the first flow's cleanup.
        from dataclasses import replace

        for index in range(127):
            session = replace(
                original, session_id=f"expired-{index}", expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
            runtime._sessions[session.session_id] = session
        stopping, release = Event(), Event()
        finish = runtime._finish_session
        first = True

        async def blocked_finish(session, state):
            nonlocal first
            if first:
                first = False
                stopping.set()
                await release.wait()
            await finish(session, state)

        monkeypatch.setattr(runtime, "_finish_session", blocked_finish)
        failures = []

        async def queued_setup():
            try:
                await runtime.begin_setup("connector-test")
            except HarnessUiError as exc:
                failures.append(exc.code)

        async with create_task_group() as group:
            async with runtime._setup_lock:
                group.start_soon(queued_setup)
                await wait_all_tasks_blocked()
                group.start_soon(runtime.stop)
                await stopping.wait()
            await wait_all_tasks_blocked()
            release.set()
        assert failures == ["connector_runtime_unavailable"]
        assert len(runtime._sessions) == 128
        assert not runtime._workers


async def test_github_save_completes_and_test_remains_an_explicit_separate_flow(tmp_path, monkeypatch):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        initial = await app.begin_connector_setup("connector-test")
        from unittest.mock import AsyncMock

        list_tools = AsyncMock(return_value=[])
        monkeypatch.setattr(
            app.connectors()._sessions[initial.session_id].flow.controller.mcp, "list_tools", list_tools
        )
        assert set(initial.step.form["properties"]) == {"token"}
        assert "test" not in {action.id for action in initial.step.actions}
        assert "clear_token" not in initial.step.form["properties"]
        saved = await app.act_connector_setup(
            initial.session_id,
            SetupCommand(operation_id="save", version=initial.version, action="save", values={"token": "fake-token"}),
        )
        assert saved.step.state == "completed", saved.step.error
        assert saved.step.actions == ()
        list_tools.assert_not_awaited()
        test = await app.begin_connector_setup("connector-test", intent="test")
        assert {action.id for action in test.step.actions} == {"edit", "test", "done"}
        list_tools.assert_not_awaited()
        tested = await app.act_connector_setup(
            test.session_id, SetupCommand(operation_id="test", version=test.version, action="test")
        )
        assert tested.step.description == "Connection test succeeded."
        list_tools.assert_awaited_once()
        edited = await app.act_connector_setup(
            test.session_id, SetupCommand(operation_id="edit", version=tested.version, action="edit")
        )
        assert edited.step.form["x-a13n-saved"] == ["/token"]
        assert "clear_token" in edited.step.form["properties"]
        current = await app._store.connectors.configuration("connector-test")
        for settings in (
            {"url": "https://other.example/mcp"},
            {"headers": {"X-Custom": "value"}},
            {"protocol": "2025-03-26"},
            {"init_timeout_seconds": 60},
            {"tools": ["search"]},
        ):
            with pytest.raises(HarnessUiError) as failure:
                await app.configure_connector(
                    current.model_copy(update={"settings": settings}), expected_version=current.version
                )
            assert failure.value.code == "connector_configuration_invalid"
        assert (await app._store.connectors.configuration("connector-test")).settings == {}


@pytest.mark.parametrize("change", [{"name": "Renamed"}, {"enabled": False}])
async def test_github_reopen_replaces_setup_after_account_settings_change(tmp_path, change):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        setup = await app.begin_connector_setup("connector-test")
        current = await app._store.connectors.configuration("connector-test")
        await app.configure_connector(current.model_copy(update=change), expected_version=current.version)
        reopened = await app.begin_connector_setup("connector-test")
        assert reopened.session_id != setup.session_id
        saved = await app.act_connector_setup(
            reopened.session_id,
            SetupCommand(operation_id="save", version=reopened.version, action="save", values={"token": "fake-token"}),
        )
        assert saved.step.state == "completed", saved.step.error
        current = await app._store.connectors.configuration("connector-test")
        assert all(getattr(current, key) == value for key, value in change.items())
        assert current.credentials["token"] == "Bearer fake-token"


async def test_github_parallel_test_setup_reopens_after_token_replacement(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await save_github(app)
        test = await app.begin_connector_setup("connector-test", intent="test")
        configure = await app.begin_connector_setup("connector-test")
        await app.act_connector_setup(
            configure.session_id,
            SetupCommand(
                operation_id="replace", version=configure.version, action="save", values={"token": "replacement"}
            ),
        )
        reopened = await app.begin_connector_setup("connector-test", intent="test")
        assert reopened.session_id != test.session_id
        assert {action.id for action in reopened.step.actions} == {"edit", "test", "done"}
        done = await app.act_connector_setup(
            reopened.session_id, SetupCommand(operation_id="done", version=reopened.version, action="done")
        )
        assert done.step.state == "completed"


@pytest.mark.parametrize("token", ["", "   "])
async def test_github_first_save_requires_token_and_corrected_submission_can_save(tmp_path, token):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await create_github(app)
        setup = await app.begin_connector_setup("connector-test")
        invalid = await app.act_connector_setup(
            setup.session_id,
            SetupCommand(operation_id="blank", version=setup.version, action="save", values={"token": token}),
        )
        assert invalid.step.key == "configure"
        assert invalid.step.error == "Enter a GitHub personal access token."
        current = await app._store.connectors.configuration("connector-test")
        assert not current.credentials
        assert current.version == 1
        saved = await app.act_connector_setup(
            setup.session_id,
            SetupCommand(
                operation_id="corrected", version=invalid.version, action="save", values={"token": "  fake-token  "}
            ),
        )
        assert saved.step.state == "completed", saved.step.error
        assert (await app._store.connectors.configuration("connector-test")).credentials == {
            "token": "Bearer fake-token"
        }


async def test_github_blank_keeps_saved_token_and_removal_completes(tmp_path):
    async with open_harness_ui_app(_settings(tmp_path / "data"), host_mode="webui") as app:
        await save_github(app)
        setup = await app.begin_connector_setup("connector-test")
        kept = await app.act_connector_setup(
            setup.session_id,
            SetupCommand(operation_id="keep", version=setup.version, action="save", values={"token": "   "}),
        )
        assert kept.step.state == "completed", kept.step.error
        assert (await app._store.connectors.configuration("connector-test")).credentials == {
            "token": "Bearer test-private-token"
        }
        edited = await app.begin_connector_setup("connector-test")
        assert edited.session_id != setup.session_id
        invalid = await app.act_connector_setup(
            edited.session_id,
            SetupCommand(
                operation_id="ambiguous",
                version=edited.version,
                action="save",
                values={"clear_token": True, "token": "replacement"},
            ),
        )
        assert invalid.step.error == "Choose either a replacement token or removal, not both."
        removed = await app.act_connector_setup(
            edited.session_id,
            SetupCommand(operation_id="remove", version=invalid.version, action="save", values={"clear_token": True}),
        )
        assert removed.step.state == "completed", removed.step.error
        assert removed.step.actions == ()
        assert not (await app._store.connectors.configuration("connector-test")).credentials
        assert (await app.connectors().instance("connector-test")).status.state == "unconfigured"
