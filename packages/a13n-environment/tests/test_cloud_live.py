"""Explicit opt-in cloud checks; every test owns its target and attempts cleanup."""

import json
import os
import secrets

import pytest
from a13n_environment.builtins import select_builtin_environment_providers
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.models import EnvironmentState
from a13n_environment.retention import EnvironmentOutputPolicy

pytestmark = pytest.mark.anyio
KEYS = ("daytona", "modal", "vercel", "sprites", "runloop")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("key", KEYS)
async def test_cloud_files_execution_and_resume(key, tmp_path):
    if key not in os.environ.get("A13N_TEST_CLOUD_PROVIDERS", "").split(","):
        pytest.skip("Opt in with A13N_TEST_CLOUD_PROVIDERS and provider-specific test credentials")
    prefix = f"A13N_TEST_{key.upper()}_"
    backend_json = os.environ.get(prefix + "BACKEND_JSON")
    credential_json = os.environ.get(prefix + "CREDENTIAL_JSON")
    if not backend_json or not credential_json:
        pytest.skip("Provider-specific test backend/credential JSON is not configured")
    definition = select_builtin_environment_providers([key])[0]
    try:
        backend = definition.configuration_model.model_validate_json(backend_json)
        credential = definition.credential_model.model_validate_json(credential_json)
    except ValueError:
        pytest.fail("Test backend or credential JSON does not match the provider schema", pytrace=False)
    configuration = definition.validate_environment(json.loads(os.environ.get(prefix + "RECIPE_JSON", "{}")))
    environment_id = "env-live-" + secrets.token_hex(12)
    async with await definition.open_provider(configuration=backend, credential=credential) as provider:
        state = await provider.create(configuration, environment_id=environment_id, operation_id="op-create")

        def connector():
            return provider.execution_connector(configuration, environment_id=environment_id, state=state)

        filename = "/a13n-test-" + secrets.token_hex(8)
        try:
            async with await connector().open() as env:
                await env.operations.files.write_text(filename, "native persistence", mode="create")
                result = await env.operations.shell.exec(
                    CommandRequest(
                        command=ShellCommand(profile_id="default", script="printf a13n-shell"),
                        output_policy=EnvironmentOutputPolicy(
                            max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate"
                        ),
                    )
                )
                assert result.output.stdout.inline == b"a13n-shell"
                state = EnvironmentState.model_validate_json(env.state.model_dump_json())
                identity = env.descriptor.backing_identity
            async with await connector().open() as env:
                assert env.descriptor.backing_identity == identity
                assert (await env.operations.files.read_text(filename)).text == "native persistence"
            if definition.supports_stop:
                state = await provider.stop(
                    configuration, environment_id=environment_id, state=state, operation_id="op-stop"
                )
                assert (
                    await provider.inspect(configuration, environment_id=environment_id, state=state)
                ).status == "stopped"
                state = await provider.start(
                    configuration, environment_id=environment_id, state=state, operation_id="op-start"
                )
                async with await connector().open() as env:
                    assert (await env.operations.files.read_text(filename)).text == "native persistence"
        finally:
            await provider.destroy(configuration, environment_id=environment_id, state=state, operation_id="op-delete")
