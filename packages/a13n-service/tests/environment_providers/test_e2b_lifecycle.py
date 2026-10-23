"""Lifecycle tests deliberately keep sandbox operation preparation outside the fake API."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import a13n_harness.providers.environment.e2b.execution as provider_module
import e2b
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentAvailability, EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.e2b.configuration import E2BConnectionConfiguration, E2BCredential
from a13n_harness.providers.environment.e2b.execution import E2BExecution
from a13n_harness.providers.environment.e2b.provider import E2BProviderRuntime
from a13n_harness.providers.environment.e2b.shared import descriptor
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.providers.environments.e2b.definition import E2B
from a13n_service.providers.environments.e2b.management import E2BManagement
from e2b.api.client.models import SandboxState
from e2b.exceptions import AuthenticationException, SandboxNotFoundException
from e2b.sandbox.sandbox_api import SandboxInfo
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class API:
    def __init__(self):
        self.targets: dict[str, SandboxInfo] = {}
        self.calls: list[str] = []
        self.fail: Exception | None = None
        self.created_then_lost = False
        self.options = []

    async def create(self, *, metadata, **kwargs):
        self.calls.append("create")
        self.options.append(kwargs)
        if self.fail:
            raise self.fail
        identifier = f"sandbox-{len(self.targets) + 1}"
        now = datetime.now(UTC)
        info = SandboxInfo(
            sandbox_id=identifier,
            sandbox_domain=None,
            template_id="template-1",
            name="base",
            metadata=metadata,
            started_at=now,
            end_at=now + timedelta(seconds=300),
            state=SandboxState.RUNNING,
            cpu_count=2,
            memory_mb=512,
            envd_version="0.6.2",
        )
        self.targets[identifier] = info
        if self.created_then_lost:
            raise TimeoutError("private provider response")
        return self.sandbox(identifier)

    def sandbox(self, identifier):
        return SimpleNamespace(
            sandbox_id=identifier,
            get_info=AsyncMock(return_value=self.targets[identifier]),
            _envd_api=SimpleNamespace(aclose=AsyncMock()),
        )

    async def get_info(self, sandbox_id, **kwargs):
        self.calls.append("inspect")
        if self.fail:
            raise self.fail
        if sandbox_id not in self.targets:
            raise SandboxNotFoundException("private sandbox identity")
        return self.targets[sandbox_id]

    def list(self, *, query, **kwargs):
        api = self

        class Pages:
            has_next = False

            async def next_items(self):
                api.calls.append("list")
                if api.fail:
                    raise api.fail
                return [
                    item
                    for item in api.targets.values()
                    if all(item.metadata.get(k) == v for k, v in query.metadata.items())
                ]

        return Pages()

    async def connect(self, sandbox_id, **kwargs):
        self.calls.append("connect")
        self.targets[sandbox_id] = replace(self.targets[sandbox_id], state=SandboxState.RUNNING)
        return self.sandbox(sandbox_id)

    async def pause(self, sandbox_id, **kwargs):
        self.calls.append("pause")
        self.targets[sandbox_id] = replace(self.targets[sandbox_id], state=SandboxState.PAUSED)
        return True

    async def kill(self, sandbox_id, **kwargs):
        self.calls.append("kill")
        return self.targets.pop(sandbox_id, None) is not None

    async def set_timeout(self, sandbox_id, timeout, **kwargs):
        self.calls.append("renew")
        self.targets[sandbox_id] = replace(
            self.targets[sandbox_id], end_at=datetime.now(UTC) + timedelta(seconds=timeout)
        )


@pytest.fixture
def api(monkeypatch):
    api = API()
    monkeypatch.setattr(e2b, "AsyncSandbox", api)

    async def opened(self, sandbox, execution_id):
        self._descriptor = descriptor(self._configuration, "generation-1", sandbox.sandbox_id)
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self._descriptor.operation_families
        )

    async def attach(sandbox_id, options, validate):
        api.calls.append("attach")
        info = await api.get_info(sandbox_id)
        validate(info)
        if info.state is not SandboxState.RUNNING:
            raise EnvironmentError("Stopped", code="environment_unavailable")
        return api.sandbox(sandbox_id)

    monkeypatch.setattr(provider_module, "open_sandbox", attach)
    monkeypatch.setattr(E2BExecution, "check_ready", AsyncMock())
    monkeypatch.setattr(E2BExecution, "_open_operations", opened)
    return api


def environment(state=None, *, configuration=None, identity="env-test"):
    return E2BManagement(
        configuration or E2BRecipe(),
        environment_id=identity,
        state=state,
        runtime=E2BProviderRuntime(SecretStr("secret-api-key")),
        operation_id="op-test",
    )


async def test_inert_catalog_and_provider_construction(api):
    provider = E2B
    assert provider is E2B
    async with await provider.open_provider(credential={"api_key": "secret-api-key"}):
        assert api.calls == []


async def test_create_resume_and_exact_destruction(api):
    config = E2BRecipe(environment={"APP_MODE": "agent-default"})
    env = environment(configuration=config)
    await env.create()
    assert api.calls.count("create") == 1
    assert api.options[0]["envs"] == config.environment
    state = env.state
    assert state is not None and "secret" not in state.model_dump_json()
    await env.close()
    assert "kill" not in api.calls and "pause" not in api.calls
    control = environment(state, configuration=config)
    await control.stop()
    assert await control.inspect() == "stopped"
    paused_calls = list(api.calls)
    with pytest.raises(EnvironmentProviderError, match="E2B"):
        await control.keepalive(deadline=datetime.now(UTC) + timedelta(seconds=30), operation_id="renew-1")
    assert api.calls.count("connect") == paused_calls.count("connect")
    resumed = environment(state, configuration=config)
    await resumed.start()
    assert api.calls.count("create") == 1 and api.calls.count("connect") == 1
    destroy = environment(state, configuration=config)
    await destroy.destroy()
    assert await environment(state, configuration=config).inspect() == "absent"
    await environment(state, configuration=config).destroy()
    assert api.calls.count("kill") == 1


async def test_failed_execution_open_keeps_the_selected_target_and_releases_scope(api, monkeypatch):
    async with await E2B.open_provider(credential={"api_key": "secret-api-key"}) as provider:
        state = await provider.create({}, environment_id="env-test", operation_id="op-create")
        connector = E2B.execution.execution_connector(
            {}, credential={"api_key": "secret-api-key"}, environment_id="env-test", state=state
        )

    async def fail(*args):
        raise RuntimeError("readiness")

    monkeypatch.setattr(E2BExecution, "_open_operations", fail)
    with pytest.raises(RuntimeError, match="readiness"):
        await connector.open()
    assert connector.state == state
    assert api.calls.count("create") == 1
    assert "connect" not in api.calls and "renew" not in api.calls


async def test_unknown_create_recovers_by_metadata_without_duplicate(api):
    api.created_then_lost = True
    env = environment()
    with pytest.raises(EnvironmentProviderError) as failure:
        await env.create()
    assert failure.value.certainty.value == "unknown"
    assert env.state is None
    recovered = environment()
    assert await recovered.inspect() == "running"
    assert recovered.state is not None
    await recovered.create()
    assert api.calls.count("create") == 1


@pytest.mark.parametrize("failure", [AuthenticationException("secret-api-key"), TimeoutError("secret-api-key")])
async def test_unknown_or_denied_inspection_never_creates(api, failure):
    api.fail = failure
    with pytest.raises(EnvironmentProviderError) as error:
        await environment().create()
    assert "create" not in api.calls
    assert "secret-api-key" not in str(error.value)
    assert "secret-api-key" not in error.value.safe_projection().model_dump_json()


async def test_metadata_conflict_blocks_resume_stop_and_destroy(api):
    env = environment()
    await env.create()
    state = env.state
    item = next(iter(api.targets.values()))
    api.targets[item.sandbox_id] = replace(item, metadata={})
    for action in ("start", "stop", "destroy"):
        with pytest.raises(EnvironmentProviderError) as error:
            await getattr(environment(state), action)()
        assert error.value.code == "provider_target_conflict"
    assert "kill" not in api.calls and "pause" not in api.calls and "connect" not in api.calls


async def test_saved_target_absence_never_allocates_a_replacement(api):
    env = environment()
    await env.create()
    state = env.state
    api.targets.clear()
    for operation in ("start", "create"):
        with pytest.raises(EnvironmentProviderError) as missing:
            await getattr(environment(state), operation)()
        assert missing.value.category == "missing"
    assert api.calls.count("create") == 1


async def test_configuration_and_state_validation_are_inert(api):
    provider = E2B
    for value in (
        {"api_key": "secret"},
        {"root": "../other"},
        {"max_observation_bytes": 0},
        {"max_active_observations": 0},
        {"max_retained_output_bytes": 0},
        {"max_observations": 16},
        {"shell": "/bin/sh"},
        {"max_wall_time_seconds": 1},
        {"max_processes": 1},
        {"max_output_bytes": 1},
    ):
        with pytest.raises(EnvironmentProviderError):
            provider.validate_environment(value)
    with pytest.raises(EnvironmentProviderError):
        environment(EnvironmentState(provider_key="test_wrong", state_version="1", state={}))
    assert api.calls == []


async def test_keepalive_reports_observed_expiry_and_never_shortens(api):
    env = environment()
    await env.create()
    item = next(iter(api.targets.values()))
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    assert await environment(env.state).keepalive(deadline=deadline, operation_id="renew-1") == item.end_at
    assert "renew" not in api.calls
    api.targets[item.sandbox_id] = replace(item, end_at=datetime.now(UTC))
    assert await environment(env.state).keepalive(deadline=deadline, operation_id="renew-2") >= deadline
    assert api.calls.count("renew") == 1
    with pytest.raises(EnvironmentProviderError):
        await environment(env.state).keepalive(deadline=datetime.now(UTC) + timedelta(days=1), operation_id="renew-3")
    assert api.calls.count("renew") == 1


@pytest.mark.parametrize("borrowed", [False, True])
async def test_host_runtime_uses_explicit_credentials_and_correlation(api, borrowed):
    runtime = E2BProviderRuntime(SecretStr("secret-api-key"))
    assert "secret-api-key" not in repr(runtime)
    inputs = {"runtime": runtime} if borrowed else {"credential": {"api_key": "secret-api-key"}}
    async with await E2B.open_provider(**inputs) as provider:
        await provider.create({}, environment_id="env-test", operation_id="operation-1")
    assert next(iter(api.targets.values())).metadata["a13n_operation"] == "operation-1"
    assert api.options[0]["lifecycle"] == {"on_timeout": "kill", "auto_resume": False}


def test_observer_limits_do_not_change_target_fingerprint():
    original = E2BRecipe()
    assert original.timeout_seconds == 3600
    assert original.request_timeout_seconds == 30
    assert original.max_active_observations == 128
    assert original.max_observation_bytes == 1024 * 1024
    assert original.max_retained_output_bytes == 128 * 1024 * 1024
    assert (
        original.fingerprint
        == original.model_copy(
            update={"max_active_observations": 2, "max_observation_bytes": 256, "max_retained_output_bytes": 4096}
        ).fingerprint
    )
    assert original.fingerprint != original.model_copy(update={"template": "other"}).fingerprint


@pytest.mark.parametrize(
    "api_url",
    [None, "https://api.cn-beijing.e2b.fc.aliyuncs.com"],
)
async def test_custom_api_endpoint_reaches_sdk(api, monkeypatch, api_url):
    monkeypatch.setenv("E2B_API_URL", "https://ambient.invalid")
    async with await E2B.open_provider(
        configuration=E2BConnectionConfiguration(domain="sandbox.example", api_url=api_url),
        credential=E2BCredential(api_key=SecretStr("test-key")),
    ) as provider:
        await provider.create({}, environment_id="env-test", operation_id="op-test")
    assert api.options[0]["api_url"] == (api_url or "https://api.sandbox.example")
    assert api.options[0]["domain"] == "sandbox.example"


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "ftp://example.com",
        "https://user:secret@example.com",
        "https://example.com?key=secret",
        "https://example.com/#fragment",
    ],
)
def test_invalid_api_endpoint_is_rejected(url):
    with pytest.raises(ValueError):
        E2BConnectionConfiguration(api_url=url)


async def test_independent_opens_never_resume_or_renew(api):
    async with await E2B.open_provider(credential={"api_key": "secret-api-key"}) as provider:
        state = await provider.create({}, environment_id="env-test", operation_id="op-create")
        connector = E2B.execution.execution_connector(
            {}, credential={"api_key": "secret-api-key"}, environment_id="env-test", state=state
        )
    api.calls.clear()
    async with await connector.open() as first, await connector.open() as second:
        assert first.execution_id != second.execution_id
        assert first.state == second.state == state
    assert api.calls == ["attach", "inspect", "attach", "inspect"]


@pytest.mark.parametrize("exporter", [True, False])
async def test_metrics_launch_reaches_only_envd_bootstrap(api, monkeypatch, exporter):
    import json

    from a13n_service.providers.environment.metrics import MetricsLaunch

    run = AsyncMock()
    original = api.sandbox

    def sandbox(identifier):
        result = original(identifier)
        result.commands = SimpleNamespace(run=run)
        return result

    monkeypatch.setattr(api, "sandbox", sandbox)
    monkeypatch.setattr(E2BManagement, "_check_envd", AsyncMock())
    config = E2BRecipe(execution="envd_http", template="team-envd")
    values = {"OTEL_EXPORTER_OTLP_METRICS_HEADERS": "authorization=private-write-token"} if exporter else {}
    metrics = MetricsLaunch(values)
    async with await E2B.open_provider(credential={"api_key": "test-key"}) as provider:
        state = await provider.create(config, environment_id="env_metrics", operation_id="create", metrics=metrics)
        options = run.call_args_list[0].kwargs
        assert options["envs"].get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") == values.get(
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS"
        )
        assert json.loads(options["envs"]["A13N_ENVD_CONFIG_JSON"])["monitoring"] == {"sample_interval_ms": 10_000}
        if not exporter:
            assert not any(key.startswith("OTEL_") for key in options["envs"])
        assert options["envs"]["A13N_ENVD_DEVICE_ID"] == "env_metrics"
        assert state.state["configuration_fingerprint"] == config.fingerprint
        assert "private-write-token" not in config.model_dump_json() + state.model_dump_json() + str(api.options)
        await provider.create(config, environment_id="env_metrics", operation_id="create", state=state, metrics=metrics)
        await provider.start(config, environment_id="env_metrics", operation_id="start", state=state)
        assert run.await_count == 2  # One bootstrap and its bounded TCP readiness probe.
        assert api.calls.count("create") == 1


async def test_envd_creation_records_identity_before_bootstrap_failure(api, monkeypatch):
    env = environment(configuration=E2BRecipe(execution="envd_http", desktop=True, template="team-envd"))
    bootstrap = AsyncMock(side_effect=EnvironmentError("Envd unavailable", code="environment_unavailable"))
    monkeypatch.setattr(env, "_bootstrap_envd", bootstrap)
    with pytest.raises(EnvironmentError):
        await env.create()
    assert env.state is not None
    assert api.calls.count("create") == 1
    assert api.options[0]["template"] == "team-envd"
    check = AsyncMock(side_effect=EnvironmentError("Not ready", code="environment_unavailable"))
    monkeypatch.setattr(env, "_check_envd", check)
    with pytest.raises(EnvironmentError):
        await env.create()
    assert api.calls.count("create") == 1
    assert check.await_count == 1
    assert bootstrap.await_count == 1
    await env.destroy()


async def test_envd_resume_checks_without_bootstrap_or_reallocation(api, monkeypatch):
    env = environment(configuration=E2BRecipe(execution="envd_http", desktop=True, template="team-envd"))
    bootstrap, check = AsyncMock(), AsyncMock()
    monkeypatch.setattr(env, "_bootstrap_envd", bootstrap)
    monkeypatch.setattr(env, "_check_envd", check)
    await env.create()
    state = env.state
    await env.stop()
    await env.start()
    assert env.state == state
    assert check.await_count == 2
    assert bootstrap.await_count == 1
    assert api.calls.count("create") == 1
    assert api.calls.count("connect") == 1
    await env.destroy()


async def test_envd_bootstrap_exit_is_actionable_and_does_not_expose_guest_output(api, monkeypatch):
    from e2b.sandbox.commands.command_handle import CommandExitException

    env = environment(configuration=E2BRecipe(execution="envd_http", template="team-envd"))
    monkeypatch.setattr(
        env,
        "_bootstrap_envd",
        AsyncMock(
            side_effect=CommandExitException(
                stderr="private guest stderr",
                stdout="private guest stdout",
                exit_code=1,
                error=None,
            )
        ),
    )
    with pytest.raises(EnvironmentProviderError) as failure:
        await env.create()
    assert failure.value.code == "envd_bootstrap_failed"
    assert "template prerequisites" in str(failure.value)
    assert "private guest" not in str(failure.value)
    assert env.state is not None
    assert api.calls.count("create") == 1
    await env.destroy()
