"""Real public operations over a fixture-owned kosa8 REST API that runs guest helpers locally."""

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx2
import pytest
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.kosa8 import execution as kosa8_execution
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_service.providers.environments.kosa8 import management as kosa8_management

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")
ACCOUNT = {"region": "eu"}
CREDENTIAL = {"api_key": "fixture"}
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate")


class Fake:
    """One organization's sandboxes; every call is recorded and commands run on the Host."""

    def __init__(self):
        self.sandboxes: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.argv: list[list[str]] = []
        self.clients: list[tuple[str, str]] = []
        self.boot_polls = 1
        self.starting = 0
        self.lose_create_response = False
        self.exec_frames: list[dict] | None = None
        self.count = 0

    def view(self, sandbox: dict) -> dict:
        return {key: sandbox[key] for key in ("id", "state", "name", "labels")}

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        assert request.headers["authorization"] == "Bearer fixture"
        if path == "/v1/sandboxes" and request.method == "GET":
            wanted = request.url.params.get_list("label")
            found = [
                self.view(item)
                for item in self.sandboxes.values()
                if all(item["labels"].get(f.split("=", 1)[0]) == f.split("=", 1)[1] for f in wanted)
            ]
            return httpx2.Response(200, json={"sandboxes": found, "next_cursor": None})
        if path == "/v1/sandboxes" and request.method == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            self.count += 1
            sandbox_id = f"sbx-{self.count}"
            self.sandboxes[sandbox_id] = {
                "id": sandbox_id,
                "state": "creating",
                "name": body.get("name"),
                "labels": body.get("labels", {}),
                "polls": self.boot_polls,
            }
            if self.lose_create_response:
                self.lose_create_response = False
                raise httpx2.ReadTimeout("fixture lost response")
            return httpx2.Response(202, json={"sandbox": self.view(self.sandboxes[sandbox_id])})
        parts = path.split("/")
        sandbox = self.sandboxes.get(parts[3]) if len(parts) > 3 else None
        if sandbox is None:
            return httpx2.Response(404, json={"error": "not_found", "message": "no such sandbox"})
        action = "/".join(parts[4:])
        if request.method == "GET" and not action:
            if sandbox["state"] == "creating":
                sandbox["polls"] -= 1
                if sandbox["polls"] < 0:
                    sandbox["state"] = "running"
            return httpx2.Response(200, json={"sandbox": self.view(sandbox)})
        if request.method == "DELETE" and not action:
            self.sandboxes.pop(sandbox["id"])
            return httpx2.Response(200, json={"ok": True, "id": sandbox["id"]})
        if action == "sleep":
            sandbox["state"] = "sleeping"
            return httpx2.Response(200, json={"sandbox": self.view(sandbox)})
        if action == "wake":
            sandbox["state"] = "running"
            return httpx2.Response(200, json={"sandbox": self.view(sandbox), "wake_ms": 80})
        if action == "files/raw":
            target = Path(request.url.params["path"])
            if request.method == "PUT":
                target.write_bytes(await request.aread())
                return httpx2.Response(200, json={"file": {"path": str(target)}})
            return httpx2.Response(200, content=target.read_bytes())
        if action == "exec":
            if sandbox["state"] != "running":
                return httpx2.Response(409, json={"error": "not_running", "message": sandbox["state"]})
            body = json.loads(request.content)
            argv = body["cmd"]
            assert 0 < len(argv) <= 256 and all(len(entry) <= 8192 for entry in argv)
            if not body.get("stream"):
                if self.starting:
                    self.starting -= 1
                    return httpx2.Response(409, json={"error": "starting", "message": "starting"})
                return httpx2.Response(200, json={"exit_code": 0, "stdout": "", "stderr": ""})
            self.argv.append(argv)
            if self.exec_frames is not None:
                frames = self.exec_frames
            else:
                process = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                frames = [
                    {"stream": "stdout", "data": "", "heartbeat": True},
                    {"stream": "stdout", "data": base64.b64encode(stdout).decode()},
                    {"stream": "stderr", "data": base64.b64encode(stderr).decode()},
                    {"done": True, "exit": process.returncode},
                ]
            return httpx2.Response(
                200,
                content="\n".join(json.dumps(frame) for frame in frames) + "\n",
                headers={"content-type": "application/x-ndjson"},
            )
        raise AssertionError((request.method, path))


@pytest.fixture
def fake(monkeypatch):
    value = Fake()

    def transport_init(self, provider_key, url, token, timeout, **kwargs):
        value.clients.append((provider_key, url))
        self.key = provider_key
        self.client = httpx2.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx2.MockTransport(value.handle),
        )

    monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
    monkeypatch.setattr(kosa8_management, "_POLL_SECONDS", 0.01)
    return value


def setup(tmp_path, **recipe):
    definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["kosa8"]
    config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable, **recipe})
    return definition, config


def test_kosa8_roundtrip_lifecycle_and_reconstruction(tmp_path, fake):
    async def scenario():
        definition, config = setup(tmp_path, size="4x8", disk_gb=64, environment={"APP_MODE": "agent"})
        assert definition.supports_stop and definition.supports_destroy and not definition.requires_keepalive
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-test", operation_id="op-create")
        [created] = fake.created
        assert created["size"] == "4x8" and created["disk_gb"] == 64 and created["region"] == "eu"
        assert created["persistent"] is True and created["sleep_after_s"] == 0
        assert "image" not in created and "docker" not in created and "ttl_s" not in created
        # Recipe defaults reach commands through the guest helper, never the create call.
        assert "env" not in created
        assert created["labels"] == {"a13n_environment": "env-test", "a13n_configuration": config.fingerprint}
        assert created["name"].startswith("a13n-")
        assert all(url == "https://api.kosa8.com" for _, url in fake.clients)
        [sandbox_id] = fake.sandboxes
        assert state.state["target_id"] == sandbox_id
        # Readiness covers a first command, not only the running state.
        assert ("POST", f"/v1/sandboxes/{sandbox_id}/exec") in fake.calls

        def connector():
            return definition.execution.execution_connector(
                config, configuration=ACCOUNT, credential=CREDENTIAL, environment_id="env-test", state=state
            )

        first = await connector().open()
        await first.operations.files.write_text("/hello", "persisted", mode="upsert")
        assert ("PUT", f"/v1/sandboxes/{sandbox_id}/files/raw") in fake.calls
        result = await first.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(
                    profile_id="default", script='cat hello; printf "$APP_MODE"; printf error >&2; exit 3'
                ),
                output_policy=OUTPUT,
            )
        )
        assert result.output.stdout.inline == b"persistedagent"
        assert result.output.stderr.inline == b"error"
        assert result.status.exit_code == 3
        identity = first.descriptor.backing_identity
        assert identity == sandbox_id
        await first.close()

        state = await provider.stop(config, environment_id="env-test", state=state, operation_id="op-stop")
        assert ("POST", f"/v1/sandboxes/{sandbox_id}/sleep") in fake.calls
        assert (await provider.inspect(config, environment_id="env-test", state=state)).status == "stopped"
        with pytest.raises(EnvironmentProviderError) as stopped:
            await connector().open()
        assert stopped.value.code == "provider_target_stopped"
        assert ("POST", f"/v1/sandboxes/{sandbox_id}/wake") not in fake.calls

        state = await provider.start(config, environment_id="env-test", state=state, operation_id="op-start")
        assert ("POST", f"/v1/sandboxes/{sandbox_id}/wake") in fake.calls
        state = EnvironmentState.model_validate_json(state.model_dump_json())
        second = await connector().open()
        assert second.descriptor.backing_identity == identity
        assert (await second.operations.files.read_text("/hello")).text == "persisted"
        await second.close()

        await provider.destroy(config, environment_id="env-test", state=state, operation_id="op-delete")
        assert ("DELETE", f"/v1/sandboxes/{sandbox_id}") in fake.calls
        assert not fake.sandboxes
        assert (await provider.inspect(config, environment_id="env-test", state=state)).status == "absent"
        await provider.close()

    asyncio.run(scenario())


def test_kosa8_recipe_options_reach_the_create_call(tmp_path, fake):
    async def scenario():
        definition, config = setup(tmp_path, image="kosa8-template/universal-24.04", docker=True, ttl_seconds=3600)
        provider = await definition.open_provider(configuration={}, credential=CREDENTIAL)
        await provider.create(config, environment_id="env-options", operation_id="op-create")
        [created] = fake.created
        assert created["size"] == "2x4"
        assert created["image"] == "kosa8-template/universal-24.04"
        assert created["docker"] is True and created["ttl_s"] == 3600
        assert "region" not in created and "disk_gb" not in created
        await provider.close()
        for invalid in ({"size": "1x2"}, {"disk_gb": 4}, {"ttl_seconds": 30}):
            with pytest.raises(EnvironmentProviderError):
                definition.validate_environment({"root": str(tmp_path), **invalid})
        assert definition.validate_environment({}).root == "/workspace"

    asyncio.run(scenario())


def test_kosa8_interrupted_create_recovers_by_labels(tmp_path, fake):
    async def scenario():
        definition, config = setup(tmp_path)
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        fake.lose_create_response = True
        with pytest.raises(EnvironmentProviderError) as lost:
            await provider.create(config, environment_id="env-recover", operation_id="op-create")
        assert lost.value.code == "provider_unknown_outcome"
        [sandbox_id] = fake.sandboxes
        # An interrupted allocation has no saved state; ownership labels find the same sandbox.
        state = await provider.create(config, environment_id="env-recover", operation_id="op-create")
        assert state.state["target_id"] == sandbox_id
        assert len(fake.created) == 1
        recovered = await provider.inspect(config, environment_id="env-recover", state=None)
        assert recovered.status == "running" and recovered.state is not None
        await provider.destroy(config, environment_id="env-recover", state=state, operation_id="op-delete")
        assert not fake.sandboxes
        await provider.close()

    asyncio.run(scenario())


def test_kosa8_foreign_duplicate_and_missing_sandboxes(tmp_path, fake):
    async def scenario():
        definition, config = setup(tmp_path)
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-dup", operation_id="op-create")
        [sandbox] = fake.sandboxes.values()
        fake.sandboxes["copy"] = {**sandbox, "id": "copy", "labels": dict(sandbox["labels"])}
        with pytest.raises(EnvironmentProviderError) as duplicate:
            await provider.create(config, environment_id="env-dup", operation_id="op-create")
        assert duplicate.value.code == "provider_target_conflict"
        fake.sandboxes.pop("copy")
        sandbox["labels"]["a13n_configuration"] = "0" * 64
        with pytest.raises(EnvironmentProviderError) as foreign:
            await provider.start(config, environment_id="env-dup", state=state, operation_id="op-start")
        assert foreign.value.code == "provider_target_conflict"
        fake.sandboxes.clear()
        with pytest.raises(EnvironmentProviderError) as missing:
            await provider.start(config, environment_id="env-dup", state=state, operation_id="op-start")
        assert missing.value.code == "provider_target_missing"
        assert len(fake.created) == 1
        await provider.close()

    asyncio.run(scenario())


def test_kosa8_readiness_waits_for_boot_and_a_first_command(tmp_path, fake):
    async def scenario():
        fake.boot_polls = 3
        fake.starting = 2
        definition, config = setup(tmp_path)
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        await provider.create(config, environment_id="env-boot", operation_id="op-create")
        [sandbox_id] = fake.sandboxes
        probes = [call for call in fake.calls if call == ("POST", f"/v1/sandboxes/{sandbox_id}/exec")]
        assert len(probes) == 3 and fake.starting == 0
        await provider.close()

    asyncio.run(scenario())


def test_kosa8_long_helper_arguments_are_split_and_rejoined(tmp_path, fake):
    async def scenario():
        definition, config = setup(tmp_path)
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-argv", operation_id="op-create")
        execution = await definition.execution.execution_connector(
            config, configuration=ACCOUNT, credential=CREDENTIAL, environment_id="env-argv", state=state
        ).open()
        # The file helper's source alone is past one kosa8 argument.
        text = "x" * 20_000 + "\n"
        await execution.operations.files.write_text("/long", text, mode="upsert")
        counted = await execution.operations.shell.exec(
            CommandRequest(command=ShellCommand(profile_id="default", script="wc -c < long"), output_policy=OUTPUT)
        )
        assert counted.output.stdout.inline.strip() == b"20001"
        assert any(argv[:2] == ["/bin/bash", "-c"] and len(argv) > 6 for argv in fake.argv)
        script = "printf %s " + "y" * 9000 + " | wc -c"
        result = await execution.operations.shell.exec(
            CommandRequest(command=ShellCommand(profile_id="default", script=script), output_policy=OUTPUT)
        )
        assert result.output.stdout.inline.strip() == b"9000"
        await execution.close()
        await provider.close()
        assert kosa8_execution.bounded_argv(["true"]) == ["true"]
        with pytest.raises(EnvironmentProviderError) as too_long:
            kosa8_execution.bounded_argv(["x" * 4096 * 260])
        assert too_long.value.code == "provider_spec_invalid"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "frames",
    [
        [{"stream": "stdout", "data": base64.b64encode(b"{}").decode()}],
        [{"done": True, "error": "the command ran longer than its timeout_s", "code": "exec_timeout"}],
        [{"stream": "stdout", "data": "not base64!"}, {"done": True, "exit": 0}],
    ],
    ids=["no-exit", "timeout", "invalid-frame"],
)
def test_kosa8_stream_without_an_exit_status_is_an_unknown_outcome(tmp_path, fake, frames):
    async def scenario():
        definition, config = setup(tmp_path)
        provider = await definition.open_provider(configuration=ACCOUNT, credential=CREDENTIAL)
        state = await provider.create(config, environment_id="env-lost", operation_id="op-create")
        execution = await definition.execution.execution_connector(
            config, configuration=ACCOUNT, credential=CREDENTIAL, environment_id="env-lost", state=state
        ).open()
        fake.exec_frames = frames
        dispatched = len(fake.argv)
        with pytest.raises(EnvironmentProviderError) as lost:
            await execution.operations.shell.exec(
                CommandRequest(command=ShellCommand(profile_id="default", script="true"), output_policy=OUTPUT)
            )
        # The command may or may not have run: the outcome is reconciled, never assumed.
        assert lost.value.certainty.value == "unknown"
        # A dispatched command is never resent.
        assert len(fake.argv) == dispatched + 1
        await execution.close()
        await provider.close()

    asyncio.run(scenario())
