"""Native HTTP contracts exercised through catalog construction and actual guest helpers."""

import asyncio
import json
import shlex
import sys
from datetime import UTC, datetime, timedelta

import a13n_environment.native.environment as native_environment
import httpx2
import pytest
from a13n_environment.builtins import select_builtin_environment_providers
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.errors import EnvironmentProviderError, observed_environment_state
from a13n_environment.models import EnvironmentState
from a13n_environment.native.http import NativeHTTP
from a13n_environment.retention import EnvironmentOutputPolicy

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")

BACKENDS = {
    "daytona": {"organization_id": "org-fixture"},
    "vercel": {"team_id": "team-fixture", "project_id": "project-fixture"},
    "runloop": {"organization": "org-fixture"},
}


class NativeCloud:
    def __init__(self, key):
        self.key = key
        self.target = None
        self.count = 0
        self.session = 0
        self.calls = []
        self.error = None
        self.lose_create_response = False
        self.cancel_create = False
        self.delete_pending = 0
        self.deleting = False
        self.delete_error = None

    def view(self):
        if self.key == "vercel":
            return {
                "sandbox": self.target,
                "session": {
                    "id": f"session-{self.session}",
                    "status": self.target["status"],
                    "timeout": 3600000,
                    "startedAt": 1789700000000,
                },
            }
        return self.target

    async def request(self, request):
        self.calls.append((request.method, request.url.path))
        if (
            self.key == "vercel"
            and request.method == "POST"
            and request.headers.get("content-type") != "application/json"
        ):
            return httpx2.Response(415)
        if self.error:
            return httpx2.Response(self.error)
        if self.deleting and request.method == "GET":
            if self.delete_error:
                return httpx2.Response(self.delete_error)
            if self.delete_pending == 0:
                self.target = None
            else:
                self.delete_pending -= 1
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/process/execute") or path.endswith("/execute_sync") or path.endswith("/cmd"):
            argv = [body["command"], *body["args"]] if self.key == "vercel" else shlex.split(body["command"])
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if self.key == "vercel":
                data = [
                    {"command": {"exitCode": None}},
                    {"stream": "stdout", "data": stdout.decode()},
                    {"command": {"exitCode": process.returncode}},
                ]
                return httpx2.Response(
                    200,
                    content="\n".join(json.dumps(item) for item in data),
                    headers={"content-type": "application/x-ndjson"},
                )
            if self.key == "runloop":
                return httpx2.Response(
                    200, json={"exit_status": process.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}
                )
            return httpx2.Response(200, json={"exitCode": process.returncode, "result": stdout.decode()})
        if path == "/v1/devboxes" and request.method == "GET":
            return httpx2.Response(200, json={"devboxes": [self.target] if self.target else [], "has_more": False})
        if request.method == "POST" and path in {"/api/sandbox", "/v1/devboxes", "/v2/sandboxes"}:
            assert self.target is None
            self.count += 1
            self.session += 1
            self.target = {
                **body,
                "id": f"target-{self.count}",
                "status": "running",
                "state": "started",
                "organizationId": "org-fixture",
                "toolboxProxyUrl": "https://toolbox.fixture/",
                "createdAt": self.count,
            }
            if self.cancel_create:
                self.cancel_create = False
                raise asyncio.CancelledError
            if self.lose_create_response:
                self.lose_create_response = False
                raise httpx2.ReadTimeout("fixture lost response")
            return httpx2.Response(201, json=self.view())
        if self.target is None:
            return httpx2.Response(404)
        if path.endswith("/stop") or path.endswith("/suspend"):
            self.target.update(status="suspended" if self.key == "runloop" else "stopped", state="stopped")
        elif path.endswith("/start") or path.endswith("/resume") or request.url.params.get("resume") == "true":
            self.target.update(status="running", state="started")
            self.session += 1
        elif request.method == "DELETE" or path.endswith("/shutdown"):
            if self.delete_pending or self.delete_error:
                self.deleting = True
                self.target.update(state="destroying", status="shutting_down")
            else:
                self.target = None
            return httpx2.Response(204)
        return httpx2.Response(200, json=self.view())


@pytest.mark.parametrize("key", BACKENDS)
def test_native_http_lifecycle_and_operations(key, tmp_path, monkeypatch):
    async def scenario(key):
        cloud = NativeCloud(key)

        def transport_init(self, provider_key, url, token, timeout, **kwargs):
            self.key = provider_key
            self.client = httpx2.AsyncClient(
                base_url=url, transport=httpx2.MockTransport(cloud.request), params=kwargs.get("params")
            )

        monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
        monkeypatch.setattr(native_environment, "_DELETE_POLL_SECONDS", 0.01)
        definition = select_builtin_environment_providers([key])[0]
        config = definition.validate_environment({"root": str(tmp_path), "python": sys.executable})
        provider = await definition.open_provider(
            configuration=BACKENDS[key], credential={"api_key": "fixture-private-token"}
        )

        async def manage(operation, state=None, **kwargs):
            return await getattr(provider, operation)(
                config, environment_id="env-fixture", state=state, operation_id="op-test", **kwargs
            )

        def connector(state):
            return provider.execution_connector(config, environment_id="env-fixture", state=state)

        state = await manage("create")
        assert "fixture-private-token" not in state.model_dump_json()
        state = EnvironmentState.model_validate_json(state.model_dump_json())
        before = list(cloud.calls)
        input = connector(state)
        assert cloud.calls == before
        first = await input.open()
        assert all(method == "GET" for method, _ in cloud.calls[len(before) :])
        await first.operations.files.write_text("/data", f"{key} files", mode="upsert")
        result = await first.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="cat data; printf stderr >&2"),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate"
                ),
            )
        )
        assert result.output.stdout.inline == f"{key} files".encode()
        assert result.output.stderr.inline == b"stderr"
        assert result.status.exit_code == 0
        failed = await first.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="exit 7"),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate"
                ),
            )
        )
        assert failed.status.exit_code == 7
        if key == "daytona":
            cloud.target["autoDeleteInterval"] = 0
            with pytest.raises(EnvironmentProviderError) as exc:
                await manage("stop", state)
            assert exc.value.code == "provider_stop_retention_unsupported"
            assert cloud.target["state"] == "started"
            cloud.target["autoDeleteInterval"] = -1
        if key == "runloop":
            cloud.target["launch_parameters"]["after_idle"]["idle_time_seconds"] = 600
            with pytest.raises(EnvironmentProviderError) as exc:
                await manage("keepalive", state, deadline=datetime.now(UTC) + timedelta(seconds=900))
            assert exc.value.code == "provider_keepalive_limit"
            deadline = datetime.now(UTC) + timedelta(seconds=300)
            assert await manage("keepalive", state, deadline=deadline) >= deadline
        old_identity = first.descriptor.backing_identity
        await first.close()
        state = await manage("stop", state)
        assert (await provider.inspect(config, environment_id="env-fixture", state=state)).status == "stopped"
        before = list(cloud.calls)
        with pytest.raises(EnvironmentProviderError):
            await connector(state).open()
        assert all(method == "GET" for method, _ in cloud.calls[len(before) :])
        state = await manage("start", state)
        second = await connector(state).open()
        assert second.execution_id != first.execution_id
        assert (await second.operations.files.read_text("/data")).text == f"{key} files"
        assert second.descriptor.backing_identity == old_identity
        assert cloud.count == 1
        await second.close()
        cloud.error = 503
        with pytest.raises(EnvironmentProviderError):
            await connector(state).open()
        assert cloud.count == 1
        cloud.error = None
        cloud.target = None
        before = list(cloud.calls)
        with pytest.raises(EnvironmentProviderError) as exc:
            await connector(state).open()
        assert exc.value.code == "provider_target_missing"
        for operation in ("create", "start"):
            with pytest.raises(EnvironmentProviderError) as exc:
                await manage(operation, state)
            assert exc.value.code == "provider_target_missing"
        assert all(method == "GET" for method, _ in cloud.calls[len(before) :])
        assert cloud.count == 1
        state = await manage("create")
        cloud.delete_pending = 1
        await manage("destroy", state)
        assert cloud.target is None
        cloud.deleting = False
        cloud.lose_create_response = True
        with pytest.raises(EnvironmentProviderError) as exc:
            await manage("create")
        assert exc.value.code == "provider_unknown_outcome"
        recovered = await provider.inspect(config, environment_id="env-fixture", state=None)
        assert recovered.status == "running"
        assert recovered.state is not None
        await manage("destroy", recovered.state)
        cloud.cancel_create = True
        with pytest.raises(asyncio.CancelledError):
            await manage("create")
        recovered = await provider.inspect(config, environment_id="env-fixture", state=None)
        assert recovered.status == "running"
        assert cloud.count == 4
        await manage("destroy", recovered.state)
        state = await manage("create")
        input = connector(state)
        await provider.close()
        async with await input.open() as execution:
            assert execution.state == state

    asyncio.run(scenario(key))


@pytest.mark.parametrize("key", BACKENDS)
@pytest.mark.parametrize("poll_failure", ["timeout", "unavailable"])
def test_native_delete_keeps_state_until_terminal_evidence(key, poll_failure, tmp_path, monkeypatch):
    async def scenario():
        cloud = NativeCloud(key)

        def transport_init(self, provider_key, url, token, timeout, **kwargs):
            self.key = provider_key
            self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(cloud.request))

        monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
        definition = select_builtin_environment_providers([key])[0]
        config = definition.validate_environment(
            {"root": str(tmp_path), "python": sys.executable, "request_timeout_seconds": 0.2}
        )
        async with await definition.open_provider(
            configuration=BACKENDS[key], credential={"api_key": "fixture"}
        ) as provider:
            state = await provider.create(config, environment_id="env-fixture", operation_id="op-create")
            cloud.delete_pending = 100
            cloud.delete_error = 503 if poll_failure == "unavailable" else None
            with pytest.raises(EnvironmentProviderError) as exc:
                await provider.destroy(config, environment_id="env-fixture", state=state, operation_id="op-delete")
            assert exc.value.certainty.value == "unknown"
            assert exc.value.code == "provider_delete_unconfirmed"
            assert observed_environment_state(exc.value, None) == state
            mutations = [call for call in cloud.calls if call[0] == "DELETE" or call[1].endswith("/shutdown")]
            assert len(mutations) == 1

    asyncio.run(scenario())
