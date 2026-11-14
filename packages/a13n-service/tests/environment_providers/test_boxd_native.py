"""Real public operations over a fixture-owned boxd SDK client that runs guest helpers locally."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import boxd
import pytest
from a13n_harness.environment.commands import CommandLimits, CommandRequest, ShellCommand
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
        self.hidden: set[str] = set()
        self.exec_timeouts: list[float] = []
        self.partial_create = False
        self.get_errors: list[Exception] = []
        self.stalled_readiness = False

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

            async def get(self, reference):
                fake.calls.append(("get", reference))
                if fake.get_errors:
                    raise fake.get_errors.pop(0)
                for item in fake.machines.values():
                    if reference in {item.id, item.name}:
                        return item
                raise boxd.NotFoundError("machine not found")

            async def list(self, *, org=None, labels=None):
                fake.calls.append(("list", org, dict(labels or {})))
                return [
                    item
                    for item in fake.machines.values()
                    if item.id not in fake.hidden
                    and all(item.labels.get(key) == value for key, value in (labels or {}).items())
                ]

            async def create(self, name, **options):
                fake.calls.append(("create", name))
                if any(item.name == name for item in fake.machines.values()):
                    raise boxd.ConflictError("name exists")
                fake.created.append({"name": name, **options})
                machine_id = f"0f1e2d3c-0000-4000-8000-{len(fake.created):012d}"
                fake.machines[machine_id] = SimpleNamespace(
                    id=machine_id,
                    name=name,
                    status="running",
                    labels=dict(options["labels"]),
                    idle=SimpleNamespace(hibernate_after=14400),
                )
                if fake.partial_create:
                    # A create result that carries the machine ID without its labels.
                    return SimpleNamespace(
                        id=machine_id, name=name, status="pending", labels={}, idle=SimpleNamespace(hibernate_after=0)
                    )
                return fake.machines[machine_id]

            async def wait_until_ready(self, machine_id, *, timeout):
                fake.calls.append(("ready", machine_id))
                if fake.stalled_readiness:
                    # A readiness wait that never returns by itself.
                    await asyncio.Event().wait()
                return fake.machines[machine_id]

            async def set_auto_hibernate_timeout(self, machine_id, seconds):
                fake.calls.append(("auto_hibernate", machine_id, seconds))
                fake.machines[machine_id].idle.hibernate_after = seconds

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
                fake.exec_timeouts.append(timeout)
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
        # A snapshot restore takes no creation environment.
        assert created["env"] is None
        assert created["org"] == "fixture-org" and created["region"] == "nyc"
        assert created["auto_suspend_timeout"] == 0
        assert ("auto_hibernate", next(iter(fake.machines)), 0) in fake.calls
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
        fake.machines["copy"] = SimpleNamespace(
            id="copy", name="copy", status="running", labels=dict(machine.labels), idle=machine.idle
        )
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


def test_boxd_create_adopts_its_own_name_when_labels_miss_it_and_omits_size_by_default(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        first = await provider.create(config, environment_id="env-name", operation_id="op-create")
        [machine_id] = fake.machines
        assert (fake.created[0]["vcpu"], fake.created[0]["memory"]) == (None, None)
        assert fake.created[0]["env"] is None
        # The label listing has not caught up: the retried create meets its own name and adopts that machine.
        fake.hidden.add(machine_id)
        again = await provider.create(config, environment_id="env-name", operation_id="op-create")
        assert again.state["target_id"] == first.state["target_id"] == machine_id
        assert [call[0] for call in fake.calls].count("create") == 2 and len(fake.machines) == 1
        # A machine with that name but other owners is never adopted.
        fake.machines[machine_id].labels["a13n_environment"] = "someone-else"
        with pytest.raises(EnvironmentProviderError) as foreign:
            await provider.create(config, environment_id="env-name", operation_id="op-create")
        assert foreign.value.code == "provider_target_conflict"
        await provider.close()

    asyncio.run(scenario())


def test_boxd_idle_hibernation_policy_and_stop_reconciliation(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "auto_hibernate_timeout_seconds": 3600}
        )
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-idle", operation_id="op-create")
        [machine_id] = fake.machines
        assert ("auto_hibernate", machine_id, 3600) in fake.calls
        state = await provider.stop(config, environment_id="env-idle", state=state, operation_id="op-stop")
        hibernations = fake.calls.count(("hibernate", machine_id))
        # Repeated stop reconciles the hibernated machine without another call or a wake.
        state = await provider.stop(config, environment_id="env-idle", state=state, operation_id="op-stop")
        assert fake.calls.count(("hibernate", machine_id)) == hibernations == 1
        assert ("wake", machine_id) not in fake.calls
        fake.machines[machine_id].status = "failed"
        with pytest.raises(EnvironmentProviderError) as failed:
            await provider.start(config, environment_id="env-idle", state=state, operation_id="op-start")
        assert failed.value.code == "provider_unavailable"
        await provider.close()

    asyncio.run(scenario())


def test_boxd_command_longer_than_the_request_timeout_ends_as_timed_out(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "request_timeout_seconds": 1}
        )
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-long", operation_id="op-create")
        execution = await definition.execution.execution_connector(
            config, configuration=ACCOUNT, credential=CREDENTIAL, environment_id="env-long", state=state
        ).open()
        # The command asks for more wall time than the request timeout allows; the helper caps it.
        result = await execution.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="sleep 30"),
                limits=CommandLimits(wall_time_seconds=30),
                output_policy=OUTPUT,
            )
        )
        assert (result.status.phase, result.receipt.outcome) == ("timed_out", "timed_out")
        # The exec call outlives the helper's bound plus its process group cleanup.
        assert min(fake.exec_timeouts) > config.request_timeout_seconds + 10 + 5
        await execution.close()
        await provider.close()

    asyncio.run(scenario())


def test_boxd_create_passes_recipe_environment_defaults_to_the_new_machine(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "environment": {"APP_MODE": "agent"}}
        )
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        await provider.create(config, environment_id="env-defaults", operation_id="op-create")
        assert fake.created[0]["env"] == {"APP_MODE": "agent"}
        # A snapshot restore rejects a creation environment; commands still receive the defaults.
        restored = definition.validate_environment(
            {
                "root": str(tmp_path),
                "python": sys.executable,
                "environment": {"APP_MODE": "agent"},
                "from_snapshot": "agent-base",
            }
        )
        await provider.create(restored, environment_id="env-restored", operation_id="op-create")
        assert fake.created[1]["from_snapshot"] == "agent-base" and fake.created[1]["env"] is None
        await provider.close()

    asyncio.run(scenario())


def test_boxd_create_result_without_labels_keeps_the_machine_and_stays_retryable(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        fake.partial_create = True
        # The create is acknowledged, then the next read of the machine fails too.
        fake.get_errors.append(boxd.APIConnectionError("unavailable"))
        with pytest.raises(EnvironmentProviderError) as interrupted:
            await provider.create(config, environment_id="env-partial", operation_id="op-create")
        [machine_id] = fake.machines
        # The allocation is recorded and the failure is a retryable read, not a permanent ownership conflict.
        assert (interrupted.value.code, interrupted.value.category) == ("provider_unavailable", "unavailable")
        assert interrupted.value.state.state["target_id"] == machine_id
        state = await provider.create(
            config, environment_id="env-partial", operation_id="op-create", state=interrupted.value.state
        )
        assert state.state["target_id"] == machine_id
        assert len(fake.created) == 1 and ("ready", machine_id) in fake.calls
        await provider.close()

    asyncio.run(scenario())


def test_boxd_create_result_without_labels_still_rejects_a_foreign_machine(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        fake.partial_create = True
        state = await provider.create(config, environment_id="env-owned", operation_id="op-create")
        [machine_id] = fake.machines
        assert state.state["target_id"] == machine_id
        # Ownership is validated on every read of the recorded machine.
        fake.machines[machine_id].labels["a13n_environment"] = "someone-else"
        with pytest.raises(EnvironmentProviderError) as foreign:
            await provider.start(config, environment_id="env-owned", state=state, operation_id="op-start")
        assert foreign.value.code == "provider_target_conflict"
        await provider.close()

    asyncio.run(scenario())


def test_boxd_stalled_readiness_ends_at_the_request_timeout_and_keeps_the_machine(tmp_path, fake):
    async def scenario():
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["boxd"]
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "request_timeout_seconds": 0.2}
        )
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        fake.stalled_readiness = True
        with pytest.raises(EnvironmentProviderError) as stalled:
            # The provider ends the wait itself, well before this outer bound.
            await asyncio.wait_for(
                provider.create(config, environment_id="env-stalled", operation_id="op-create"), timeout=10
            )
        [machine_id] = fake.machines
        assert stalled.value.code == "provider_unavailable"
        assert stalled.value.state.state["target_id"] == machine_id
        await provider.close()

    asyncio.run(scenario())
