"""Real public operations over a fixture-owned boxd SDK client that runs guest helpers locally."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import boxd
import pytest
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")
ACCOUNT = {"region": "nyc", "organization": "fixture-org"}
CREDENTIAL = {"api_key": "fixture"}
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate")


class Fake:
    """One account's machines; every call is recorded and commands run on the Host."""

    def __init__(self):
        self.machines: dict[str, SimpleNamespace] = {}
        self.calls: list[tuple] = []
        self.clients: list[dict] = []
        self.created: list[dict] = []

    def client(self, **options):
        self.clients.append(options)
        fake = self

        class Fs:
            async def read_stream(self, machine_id, path):
                fake.calls.append(("read", machine_id))
                yield Path(path).read_bytes()

            async def write(self, machine_id, path, data, *, mode=None):
                fake.calls.append(("write", machine_id))
                content = b"".join([chunk async for chunk in data])
                Path(path).write_bytes(content)
                Path(path).chmod(mode)

        class Machines:
            fs = Fs()

            async def get(self, machine_id):
                fake.calls.append(("get", machine_id))
                if machine_id not in fake.machines:
                    raise boxd.NotFoundError("machine not found")
                return fake.machines[machine_id]

            async def list(self, *, org=None, labels=None):
                fake.calls.append(("list", org, dict(labels or {})))
                return [
                    item
                    for item in fake.machines.values()
                    if all(item.labels.get(key) == value for key, value in (labels or {}).items())
                ]

            async def create(self, name, **options):
                fake.calls.append(("create", name))
                fake.created.append({"name": name, **options})
                machine_id = f"0f1e2d3c-0000-4000-8000-{len(fake.created):012d}"
                fake.machines[machine_id] = SimpleNamespace(
                    id=machine_id, name=name, status="running", labels=dict(options["labels"])
                )
                return fake.machines[machine_id]

            async def wait_until_ready(self, machine_id, *, timeout):
                fake.calls.append(("ready", machine_id))
                return fake.machines[machine_id]

            async def hibernate(self, machine_id):
                fake.calls.append(("hibernate", machine_id))
                fake.machines[machine_id].status = "hibernated"

            async def wake(self, machine_id):
                fake.calls.append(("wake", machine_id))
                fake.machines[machine_id].status = "running"

            async def resume(self, machine_id):
                fake.calls.append(("resume", machine_id))
                fake.machines[machine_id].status = "running"

            async def start(self, machine_id):
                fake.calls.append(("start", machine_id))
                fake.machines[machine_id].status = "running"

            async def delete(self, machine_id):
                fake.calls.append(("delete", machine_id))
                fake.machines.pop(machine_id)

            async def exec(self, machine_id, argv, *, timeout):
                fake.calls.append(("exec", machine_id))
                process = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
                return SimpleNamespace(stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode)

        class Client:
            machines = Machines()

            async def close(self):
                fake.calls.append(("close",))

        return Client()


@pytest.fixture
def fake(monkeypatch):
    value = Fake()
    monkeypatch.setattr(boxd, "AsyncBoxd", value.client)
    return value


def test_boxd_roundtrip_lifecycle_and_reconstruction(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        assert definition.supports_stop and definition.supports_destroy and not definition.requires_keepalive
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "size": "4x16", "from_snapshot": "agent-base"}
        )
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-test", operation_id="op-create")
        [created] = fake.created
        assert created["isolated"] is True
        assert (created["vcpu"], created["memory"]) == (4, "16G")
        assert created["from_snapshot"] == "agent-base"
        assert created["org"] == "fixture-org" and created["region"] == "nyc"
        assert created["auto_suspend_timeout"] == 0
        assert created["labels"]["a13n_environment"] == "env-test"
        assert created["labels"]["a13n_configuration"] == config.fingerprint
        assert all(options["base_url"] == "https://boxd.sh:9443" for options in fake.clients)
        assert all(options["region"] == "nyc" and options["max_retries"] == 0 for options in fake.clients)
        [machine_id] = fake.machines
        assert state.state["target_id"] == machine_id
        assert ("ready", machine_id) in fake.calls

        def connector():
            return definition.execution.execution_connector(
                config, configuration=ACCOUNT, credential=CREDENTIAL, environment_id="env-test", state=state
            )

        first = await connector().open()
        await first.operations.files.write_text("/hello", "persisted", mode="upsert")
        result = await first.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="cat hello; printf error >&2; exit 3"),
                output_policy=OUTPUT,
            )
        )
        assert result.output.stdout.inline == b"persisted"
        assert result.output.stderr.inline == b"error"
        assert result.status.exit_code == 3
        identity = first.descriptor.backing_identity
        assert identity == machine_id
        await first.close()

        state = await provider.stop(config, environment_id="env-test", state=state, operation_id="op-stop")
        assert ("hibernate", machine_id) in fake.calls
        assert (await provider.inspect(config, environment_id="env-test", state=state)).status == "stopped"
        with pytest.raises(EnvironmentProviderError) as stopped:
            await connector().open()
        assert stopped.value.code == "provider_target_stopped"
        assert ("wake", machine_id) not in fake.calls

        state = await provider.start(config, environment_id="env-test", state=state, operation_id="op-start")
        assert ("wake", machine_id) in fake.calls
        state = EnvironmentState.model_validate_json(state.model_dump_json())
        second = await connector().open()
        assert second.descriptor.backing_identity == identity
        assert (await second.operations.files.read_text("/hello")).text == "persisted"
        await second.close()

        await provider.destroy(config, environment_id="env-test", state=state, operation_id="op-delete")
        assert ("delete", machine_id) in fake.calls
        assert not fake.machines
        await provider.close()

    asyncio.run(scenario())


def test_boxd_interrupted_create_recovers_by_labels_and_standby_reads_running(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        first = await provider.create(config, environment_id="env-recover", operation_id="op-create")
        [machine_id] = fake.machines
        fake.machines[machine_id].status = "suspended"
        # An interrupted allocation has no saved state; ownership labels find the same machine.
        again = await provider.create(config, environment_id="env-recover", operation_id="op-create")
        assert again.state["target_id"] == first.state["target_id"] == machine_id
        assert len(fake.created) == 1
        assert ("list", "fixture-org", {"a13n_environment": "env-recover"}) in fake.calls
        assert ("resume", machine_id) in fake.calls
        fake.machines[machine_id].status = "suspended"
        assert (await provider.inspect(config, environment_id="env-recover", state=again)).status == "running"
        await provider.close()

    asyncio.run(scenario())


def test_boxd_stopped_machine_starts_and_missing_target_is_never_replaced(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-stopped", operation_id="op-create")
        [machine_id] = fake.machines
        fake.machines[machine_id].status = "stopped"
        state = await provider.start(config, environment_id="env-stopped", state=state, operation_id="op-start")
        assert ("start", machine_id) in fake.calls
        fake.machines.clear()
        assert (await provider.inspect(config, environment_id="env-stopped", state=state)).status == "absent"
        with pytest.raises(EnvironmentProviderError) as missing:
            await provider.start(config, environment_id="env-stopped", state=state, operation_id="op-again")
        assert missing.value.code == "provider_target_missing"
        assert len(fake.created) == 1
        await provider.close()

    asyncio.run(scenario())


def test_boxd_foreign_or_duplicate_machines_are_conflicts(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        await provider.create(config, environment_id="env-dup", operation_id="op-create")
        [machine] = fake.machines.values()
        fake.machines["copy"] = SimpleNamespace(id="copy", name="copy", status="running", labels=dict(machine.labels))
        with pytest.raises(EnvironmentProviderError) as duplicate:
            await provider.create(config, environment_id="env-dup", operation_id="op-create")
        assert duplicate.value.code == "provider_target_conflict"
        fake.machines.pop("copy")
        machine.labels["a13n_configuration"] = "0" * 64
        with pytest.raises(EnvironmentProviderError) as foreign:
            await provider.create(config, environment_id="env-dup", operation_id="op-create")
        assert foreign.value.code == "provider_target_conflict"
        assert len(fake.created) == 1
        await provider.close()

    asyncio.run(scenario())
