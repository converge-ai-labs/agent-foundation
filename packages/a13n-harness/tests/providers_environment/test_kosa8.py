"""kosa8 execution attaches to a recorded running sandbox and keeps argv within the API's bounds."""

import asyncio
import json
import sys

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.kosa8.execution import Kosa8Execution, bounded_argv
from a13n_harness.providers.environment.kosa8.shared import (
    API_URL,
    Kosa8ConnectionConfiguration,
    Kosa8EnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.http import NativeHTTP
from pydantic import ValidationError

SANDBOX = "sbx-0001"


def execution(monkeypatch, state_name, *, labels=None):
    config = Kosa8EnvironmentConfiguration()
    state = EnvironmentState(
        provider_key="kosa8",
        state_version="1",
        state={"target_id": SANDBOX, "environment_id": "env-test", "configuration_fingerprint": config.fingerprint},
    )
    owned = {"a13n_environment": "env-test", "a13n_configuration": config.fingerprint}
    seen = []

    async def handle(request):
        seen.append((request.method, request.url.path))
        if state_name is None:
            return httpx2.Response(404, json={"error": "not_found"})
        sandbox = {"id": SANDBOX, "state": state_name, "name": "a13n-x", "labels": labels or owned}
        return httpx2.Response(200, json={"sandbox": sandbox})

    def transport_init(self, provider_key, url, token, timeout, **kwargs):
        seen.append(("client", url))
        self.key = provider_key
        self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(handle))

    monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
    runtime = NativeRuntime(Kosa8ConnectionConfiguration(region="us"), TokenCredential(api_key="fixture"))
    return Kosa8Execution(config, "env-test", state, runtime), seen


def test_definition_and_account_schema():
    [definition] = select_builtin_environment_providers(["kosa8"])
    assert definition.display_name == "kosa8"
    assert Kosa8ConnectionConfiguration().region is None
    with pytest.raises(ValidationError):
        Kosa8ConnectionConfiguration(region="ap")
    with pytest.raises(ValidationError):
        Kosa8ConnectionConfiguration(api_url="https://10.0.0.1")
    assert Kosa8EnvironmentConfiguration().root == "/workspace"
    assert API_URL == "https://api.kosa8.com"


@pytest.mark.parametrize(
    ("state_name", "code"),
    [
        ("running", None),
        ("sleeping", "provider_target_stopped"),
        ("creating", "provider_unavailable"),
        ("error", "provider_unavailable"),
        (None, "provider_target_missing"),
    ],
)
def test_open_attaches_only_to_a_running_sandbox(monkeypatch, state_name, code):
    async def scenario():
        target, seen = execution(monkeypatch, state_name)
        try:
            if code is None:
                await target.open(execution_id="exec-1")
                assert target.descriptor.backing_identity == SANDBOX
            else:
                with pytest.raises(EnvironmentProviderError) as error:
                    await target.open(execution_id="exec-1")
                assert error.value.code == code
            # Attaching never wakes, starts or changes the sandbox.
            assert {method for method, _ in seen if method != "client"} == {"GET"}
            assert ("client", "https://api.kosa8.com") in seen
        finally:
            await target.close()

    asyncio.run(scenario())


def test_open_rejects_a_sandbox_owned_by_another_environment(monkeypatch):
    async def scenario():
        target, _ = execution(monkeypatch, "running", labels={"a13n_environment": "env-other"})
        with pytest.raises(EnvironmentProviderError) as error:
            await target.open(execution_id="exec-1")
        assert error.value.code == "provider_target_conflict"
        await target.close()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="Rejoins argv with the Host's Bash")
@pytest.mark.parametrize(
    "argv",
    [
        ["printf", "%s"],
        [sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", "a" * 9000, "", "b\n\n", "é" * 5000],
    ],
    ids=["unchanged", "long-entries"],
)
def test_bounded_argv_rejoins_to_the_original_command(argv):
    bounded = bounded_argv(argv)
    assert len(bounded) <= 256 and all(len(entry.encode("utf-16-le")) // 2 <= 8192 for entry in bounded)
    if argv[0] == "printf":
        assert bounded == argv
        return

    async def run():
        process = await asyncio.create_subprocess_exec(*bounded, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        return stdout

    assert json.loads(asyncio.run(run())) == argv[3:]


@pytest.mark.parametrize("argv", [["x" * 4096 * 252], ["x"] * 257], ids=["long", "many"])
def test_bounded_argv_refuses_a_command_past_the_argument_count(argv):
    with pytest.raises(EnvironmentProviderError) as error:
        bounded_argv(argv)
    assert error.value.code == "provider_spec_invalid"
