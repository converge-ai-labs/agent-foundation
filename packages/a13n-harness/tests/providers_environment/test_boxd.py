"""boxd execution attaches to a recorded running machine and bounds SDK errors."""

import asyncio
from types import SimpleNamespace

import boxd
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.boxd.execution import BoxdExecution
from a13n_harness.providers.environment.boxd.shared import BoxdConnectionConfiguration, BoxdEnvironmentConfiguration
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from pydantic import ValidationError

MACHINE = "0f1e2d3c-0000-4000-8000-000000000001"


def execution(machine, *, exec_result=None, exec_error=None, labels=None, seen=None, read_stream=None, timeout=120):
    config = BoxdEnvironmentConfiguration(request_timeout_seconds=timeout)
    state = EnvironmentState(
        provider_key="boxd",
        state_version="1",
        state={"target_id": MACHINE, "environment_id": "env-test", "configuration_fingerprint": config.fingerprint},
    )
    runtime = NativeRuntime(BoxdConnectionConfiguration(region="fra"), TokenCredential(api_key="fixture"))
    target = BoxdExecution(config, "env-test", state, runtime)
    owned = {"a13n_environment": "env-test", "a13n_configuration": config.fingerprint}

    async def get(machine_id):
        assert machine_id == MACHINE
        if machine is None:
            raise boxd.NotFoundError("machine not found")
        return SimpleNamespace(id=MACHINE, status=machine, labels=labels or owned)

    async def run(machine_id, argv, *, timeout):
        if seen is not None:
            seen["timeout"] = timeout
        if exec_error:
            raise exec_error
        return exec_result

    async def close():
        return None

    target._client = SimpleNamespace(
        machines=SimpleNamespace(get=get, exec=run, fs=SimpleNamespace(read_stream=read_stream)), close=close
    )
    return target


def code(awaitable):
    with pytest.raises(EnvironmentProviderError) as error:
        asyncio.run(awaitable)
    return error.value


def test_definition_defaults_and_account_require_an_explicit_region():
    definition = ProviderCatalog(select_builtin_environment_providers(("boxd",))).require("boxd")
    config = definition.validate_environment({})
    assert (config.root, config.size, config.auto_suspend_timeout_seconds) == ("/home/boxd", None, 0)
    assert config.auto_hibernate_timeout_seconds == 0
    assert definition.display_name == "boxd"
    with pytest.raises(ValidationError):
        BoxdConnectionConfiguration()
    with pytest.raises(ValidationError):
        BoxdConnectionConfiguration(region="ams")
    assert "boxd" not in BoxdConnectionConfiguration(region="nyc").model_dump_json()


def test_client_uses_fixed_endpoint_explicit_region_and_no_retries(monkeypatch):
    seen = {}
    monkeypatch.setattr(boxd, "AsyncBoxd", lambda **options: seen.update(options) or SimpleNamespace())
    target = execution("running")
    target._client = None
    target.client()
    assert seen == {
        "api_key": "fixture",
        "base_url": "https://boxd.sh:9443",
        "region": "fra",
        "timeout": 130,
        "max_retries": 0,
    }


@pytest.mark.parametrize("status", ["running", "suspended"])
def test_running_and_standby_machines_open(status):
    target = execution(status)
    asyncio.run(target.open(execution_id="exec-1"))
    assert target.availability.status == "available"
    assert target.descriptor.backing_identity == MACHINE


@pytest.mark.parametrize("status", ["hibernated", "stopped"])
def test_hibernated_or_stopped_machines_are_not_woken_by_execution(status):
    assert code(execution(status).open(execution_id="exec-1")).code == "provider_target_stopped"


def test_missing_and_foreign_machines_fail_without_replacement():
    assert code(execution(None).open(execution_id="exec-1")).code == "provider_target_missing"
    foreign = execution("running", labels={"a13n_environment": "other", "a13n_configuration": "x"})
    assert code(foreign.open(execution_id="exec-1")).code == "provider_target_conflict"


def test_exit_codes_and_sdk_failures_map_to_dispatch_certainty():
    ok = execution("running", exec_result=SimpleNamespace(stdout="{}", stderr="", exit_code=0))
    assert asyncio.run(ok.execute(["true"], 5)) == "{}"
    failed = code(execution("running", exec_result=SimpleNamespace(stdout="", stderr="", exit_code=1)).execute([], 5))
    assert (failed.code, failed.certainty) == ("provider_command_failed", "known")
    timed_out = code(execution("running", exec_error=boxd.BoxdError("exec timed out")).execute([], 5))
    assert (timed_out.code, timed_out.certainty) == ("provider_unknown_outcome", "unknown")
    lost = code(execution("running", exec_result=SimpleNamespace(stdout="", stderr="", exit_code=-1)).execute([], 5))
    assert (lost.code, lost.certainty) == ("provider_unknown_outcome", "unknown")
    invalid = code(execution("running", exec_error=boxd.APIStatusError("bad", code="invalid_argument")).execute([], 5))
    assert (invalid.code, invalid.certainty) == ("provider_spec_invalid", "known")
    denied = code(execution("running", exec_error=boxd.AuthenticationError("bad key")).execute([], 5))
    assert denied.code == "provider_denied"
    assert "bad key" not in str(denied)


def test_exec_deadline_outlasts_the_guest_helper_bound():
    seen = {}
    ok = execution("running", exec_result=SimpleNamespace(stdout="{}", stderr="", exit_code=0), seen=seen)
    asyncio.run(ok.execute(["true"], 130))
    # The helper ends the command at the request timeout and then cleans up for up to 5 s more.
    assert seen["timeout"] >= 130 + 30


def test_download_chunks_pass_through_and_a_stalled_download_ends_at_the_request_timeout():
    seen = {}

    async def content(machine_id, path):
        yield b"one"
        yield b"two"

    async def stalled(machine_id, path):
        try:
            yield b"one"
            # A download that stops sending content.
            await asyncio.Event().wait()
        finally:
            seen["closed"] = True

    async def read(target):
        return [chunk async for chunk in target.read_stream("/file")]

    assert asyncio.run(read(execution("running", read_stream=content))) == [b"one", b"two"]
    target = execution("running", read_stream=stalled, timeout=0.2)
    error = code(asyncio.wait_for(read(target), timeout=10))
    assert (error.code, error.certainty) == ("provider_unavailable", "known")
    assert seen == {"closed": True}


def test_file_path_errors_are_not_reported_as_a_missing_target():
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.providers.environment.boxd.shared import sdk_errors

    with pytest.raises(EnvironmentError) as error, sdk_errors(mutation=False):
        raise boxd.MachineFileNotFoundError("/missing: No such file or directory")
    assert error.value.code == "environment_provider_failure"
    # A file request the machine cannot serve yet is a transfer failure, not an ownership conflict.
    with pytest.raises(EnvironmentError) as outdated, sdk_errors(mutation=False):
        raise boxd.MachineAgentOutdatedError("agent outdated")
    assert outdated.value.code == "environment_provider_failure"
