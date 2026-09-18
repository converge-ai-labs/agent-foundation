"""Native HTTP contracts exercised through catalog construction and actual guest helpers."""

import asyncio
import json
import shlex
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.management import ProviderRuntimeContext
from a13n_environment.models import EnvironmentState
from a13n_environment.native.http import NativeHTTP
from a13n_environment.retention import EnvironmentOutputPolicy

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
        provider = build_environment_provider_catalog(builtin_keys=[key]).require(key)
        config = provider.validate_configuration(
            schema_version="1", value={"root": str(tmp_path), "python": sys.executable}
        )
        backend = provider.provider_configuration_model.model_validate(BACKENDS[key])
        credential = provider.credential_model(api_key="fixture-private-token")

        async def create(state=None, managed=True):
            runtime = await provider.create_runtime(
                configuration=backend,
                credential=credential,
                context=ProviderRuntimeContext("env-fixture", "op-fixture", Path(tmp_path), managed),
            )
            return provider.create_environment(
                configuration=config, environment_id="env-fixture", state=state, runtime=runtime
            )

        first = await create()
        await first.enter(
            thread_id="session-one", run_id="run-one", agent_instance_id="agent-one", mount_id="mount-one"
        )
        await first.prepare()
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
        with pytest.raises(EnvironmentProviderError) as exc:
            await first.execute([sys.executable, "-c", "raise SystemExit(7)"], 5)
        assert exc.value.certainty.value == "known"
        assert exc.value.code == "provider_command_failed"
        if key == "daytona":
            cloud.target["autoDeleteInterval"] = 0
            with pytest.raises(EnvironmentProviderError) as exc:
                await first.stop()
            assert exc.value.code == "provider_stop_retention_unsupported"
            assert cloud.target["state"] == "started"
            cloud.target["autoDeleteInterval"] = -1
        if key == "runloop":
            cloud.target["launch_parameters"]["after_idle"]["idle_time_seconds"] = 600
            with pytest.raises(EnvironmentProviderError) as exc:
                await first.keepalive(deadline=datetime.now(UTC) + timedelta(seconds=900), operation_id="op-long")
            assert exc.value.code == "provider_keepalive_limit"
            deadline = datetime.now(UTC) + timedelta(seconds=300)
            assert await first.keepalive(deadline=deadline, operation_id="op-short") >= deadline
        state_json = first.dump_state().model_dump_json()
        assert "fixture-private-token" not in state_json
        state = EnvironmentState.model_validate_json(state_json)
        old_identity = first.descriptor.backing_identity
        await first.close()
        stopping = await create(state)
        await stopping.stop()
        assert await stopping.reconcile() == "stopped"
        await stopping.close()
        second = await create(state)
        await second.enter(
            thread_id="session-two", run_id="run-two", agent_instance_id="agent-two", mount_id="mount-two"
        )
        await second.prepare()
        assert (await second.operations.files.read_text("/data")).text == f"{key} files"
        assert second.descriptor.backing_identity == old_identity
        assert cloud.count == 1
        await second.close()
        cloud.error = 503
        unavailable = await create(state)
        with pytest.raises(EnvironmentProviderError):
            await unavailable.prepare()
        assert cloud.count == 1
        assert unavailable.dump_state() == state
        await unavailable.close()
        cloud.error = None
        cloud.target = None
        external = await create(state, managed=False)
        with pytest.raises(EnvironmentProviderError) as exc:
            await external.prepare()
        assert exc.value.code == "provider_target_missing"
        assert cloud.count == 1
        await external.close()
        replacement = await create(state)
        await replacement.prepare()
        assert replacement.descriptor.backing_identity != old_identity
        new_state = replacement.dump_state()
        await replacement.close()
        cleanup = await create(new_state)
        cloud.delete_pending = 1
        await cleanup.destroy()
        assert cleanup.dump_state() is None
        assert cloud.target is None
        await cleanup.close()
        cloud.deleting = False
        cloud.lose_create_response = True
        ambiguous = await create()
        with pytest.raises(EnvironmentProviderError) as exc:
            await ambiguous.prepare()
        assert exc.value.code == "provider_unknown_outcome"
        await ambiguous.close()
        recovering = await create()
        assert await recovering.reconcile() == "running"
        assert recovering.dump_state() is not None
        await recovering.close()
        cleanup = await create(recovering.dump_state())
        await cleanup.destroy()
        await cleanup.close()
        cloud.cancel_create = True
        cancelled = await create()
        with pytest.raises(asyncio.CancelledError):
            await cancelled.prepare()
        await cancelled.close()
        reconciler = await create()
        assert await reconciler.reconcile() == "running"
        assert cloud.count == 4
        await reconciler.close()
        cleanup = await create(reconciler.dump_state())
        await cleanup.destroy()
        await cleanup.close()

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
        provider = build_environment_provider_catalog(builtin_keys=[key]).require(key)
        config = provider.validate_configuration(
            schema_version="1",
            value={
                "root": str(tmp_path),
                "python": sys.executable,
                "request_timeout_seconds": 0.2,
            },
        )
        runtime = await provider.create_runtime(
            configuration=provider.provider_configuration_model.model_validate(BACKENDS[key]),
            credential=provider.credential_model(api_key="fixture"),
            context=ProviderRuntimeContext("env-fixture", "op-fixture", tmp_path),
        )
        env = provider.create_environment(
            configuration=config, environment_id="env-fixture", state=None, runtime=runtime
        )
        try:
            await env.prepare()
            state = env.dump_state()
            cloud.delete_pending = 100
            cloud.delete_error = 503 if poll_failure == "unavailable" else None
            with pytest.raises(EnvironmentProviderError) as exc:
                await env.destroy()
            assert exc.value.certainty.value == "unknown"
            assert exc.value.code == "provider_delete_unconfirmed"
            assert env.dump_state() == state
            mutations = [call for call in cloud.calls if call[0] == "DELETE" or call[1].endswith("/shutdown")]
            assert len(mutations) == 1
        finally:
            await env.close()

    asyncio.run(scenario())
