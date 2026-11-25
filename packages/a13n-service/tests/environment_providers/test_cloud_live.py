"""Explicit opt-in cloud checks; every test owns its target and attempts cleanup."""

import json
import os
import secrets

import pytest
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS

pytestmark = pytest.mark.anyio
KEYS = ("daytona", "modal", "vercel", "sprites", "runloop", "boxd")


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
    definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)[key]
    try:
        backend = definition.configuration_model.model_validate_json(backend_json)
        credential = definition.credential_model.model_validate_json(credential_json)
    except ValueError:
        pytest.fail("Test backend or credential JSON does not match the provider schema", pytrace=False)
    configuration = definition.validate_environment(json.loads(os.environ.get(prefix + "RECIPE_JSON", "{}")))
    environment_id = "env-live-" + secrets.token_hex(12)
    async with await definition.open_provider(configuration=backend, credential=credential) as provider:
        state = None

        def connector():
            return definition.execution.execution_connector(
                configuration, configuration=backend, credential=credential, environment_id=environment_id, state=state
            )

        filename = ("/workspace/" if key in {"daytona", "modal"} else "/") + "a13n-test-" + secrets.token_hex(8)
        try:
            state = await provider.create(configuration, environment_id=environment_id, operation_id="op-create")
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
        except EnvironmentProviderError as error:
            # An allocation may exist even when create or readiness failed.
            if error.state is not None:
                state = error.state
            raise
        finally:
            await provider.destroy(configuration, environment_id=environment_id, state=state, operation_id="op-delete")


async def test_daytona_volume_survives_sandbox_deletion():
    volume = os.getenv("A13N_TEST_DAYTONA_VOLUME_ID")
    snapshot = os.getenv("A13N_TEST_DAYTONA_SNAPSHOT")
    credential = os.getenv("A13N_TEST_DAYTONA_CREDENTIAL_JSON")
    if not (volume and snapshot and credential):
        pytest.skip("Requires a ready Daytona test volume, Envd snapshot and test credentials")
    definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["daytona"]
    backend = definition.configuration_model.model_validate_json(os.getenv("A13N_TEST_DAYTONA_BACKEND_JSON", "{}"))
    key = definition.credential_model.model_validate_json(credential)
    prefix = "a13n-test-" + secrets.token_hex(10)
    recipe = definition.validate_environment(
        {
            "snapshot": snapshot,
            "volumes": [{"volume_id": volume, "mount_path": "/data", "subpath": prefix}],
        }
    )
    await check_volume_reuse(definition, backend, key, prefix, recipe)


async def test_modal_volume_survives_sandbox_deletion():
    name = os.getenv("A13N_TEST_MODAL_VOLUME_NAME")
    config = os.getenv("A13N_TEST_MODAL_RECIPE_JSON")
    credential = os.getenv("A13N_TEST_MODAL_CREDENTIAL_JSON")
    if not (name and config and credential):
        pytest.skip("Requires an existing Modal test volume, runnable Envd image recipe and test credentials")
    definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["modal"]
    backend = definition.configuration_model.model_validate_json(os.getenv("A13N_TEST_MODAL_BACKEND_JSON", "{}"))
    key = definition.credential_model.model_validate_json(credential)
    prefix = "a13n-test-" + secrets.token_hex(10)
    recipe = definition.validate_environment(
        json.loads(config)
        | {
            "volumes": [{"volume_name": name, "mount_path": "/data", "sub_path": "/" + prefix}],
        }
    )
    await check_volume_reuse(definition, backend, key, prefix, recipe)


async def test_boxd_disk_survives_machine_deletion():
    disk = os.getenv("A13N_TEST_BOXD_DISK_ID")
    config = os.getenv("A13N_TEST_BOXD_BACKEND_JSON")
    credential = os.getenv("A13N_TEST_BOXD_CREDENTIAL_JSON")
    if not (disk and config and credential):
        pytest.skip("Requires an available boxd test disk, its region and test credentials")
    definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
    backend = definition.configuration_model.model_validate_json(config)
    key = definition.credential_model.model_validate_json(credential)
    prefix = "a13n-test-" + secrets.token_hex(10)
    recipe = definition.validate_environment({"volumes": [{"disk_id": disk, "mount_path": "/home/boxd/data"}]})
    await check_volume_reuse(definition, backend, key, prefix, recipe)


async def check_volume_reuse(definition, backend, key, prefix, recipe):
    path = f"/data/{prefix}.txt"
    async with await definition.open_provider(configuration=backend, credential=key) as provider:
        for phase in ("write", "read"):
            env_id = f"env-{prefix}-{phase}"
            state = None
            try:
                state = await provider.create(recipe, environment_id=env_id, operation_id="op-create")
                connector = definition.execution.execution_connector(
                    definition.execution_environment(recipe),
                    configuration=backend,
                    credential=key,
                    environment_id=env_id,
                    state=state,
                )
                async with await connector.open() as env:
                    files = env.operations.files
                    assert files is not None
                    if phase == "write":
                        await files.write_text(path, prefix, mode="create")
                    else:
                        assert (await files.read_text(path)).text == prefix
                        await files.remove(path)
            except EnvironmentProviderError as error:
                state = error.state or state
                raise
            finally:
                await provider.destroy(recipe, environment_id=env_id, state=state, operation_id="op-delete")
