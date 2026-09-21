"""Explicit opt-in cloud checks; every test owns its target and attempts cleanup."""

import json
import os
import secrets

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

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
    provider = ProviderCatalog(select_builtin_environment_providers([key])).require(key)
    try:
        backend = provider.configuration_model.model_validate_json(backend_json)
        credential = provider.credential_model.model_validate_json(credential_json)
    except ValueError:
        pytest.fail("Test backend or credential JSON does not match the provider schema", pytrace=False)
    configuration = provider.validate_environment(json.loads(os.environ.get(prefix + "RECIPE_JSON", "{}")))
    environment_id = "env-live-" + secrets.token_hex(12)
    runtime = await provider.runtime_factory(
        configuration=backend,
        credential=credential,
    )

    def fresh(state=None):
        return provider.construct(
            operation_id="operation-" + secrets.token_hex(12),
            allow_create=True,
            configuration=configuration,
            environment_id=environment_id,
            state=state,
            runtime=runtime,
        )

    env = fresh()
    filename = "/a13n-test-" + secrets.token_hex(8)
    try:
        await env.prepare()
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
        state = EnvironmentState.model_validate_json(env.dump_state().model_dump_json())
        identity = env.descriptor.backing_identity
        await env.close()
        env = fresh(state)
        await env.prepare()
        assert env.descriptor.backing_identity == identity
        assert (await env.operations.files.read_text(filename)).text == "native persistence"
        if provider.supports_stop:
            state = env.dump_state()
            await env.close()
            env = fresh(state)
            await env.stop()
            assert await env.reconcile() == "stopped"
            state = EnvironmentState.model_validate_json(env.dump_state().model_dump_json())
            await env.close()
            env = fresh(state)
            await env.prepare()
            assert (await env.operations.files.read_text(filename)).text == "native persistence"
    finally:
        state = env.dump_state()
        await env.close()
        cleanup = fresh(state)
        try:
            await cleanup.destroy()
        finally:
            await cleanup.close()
