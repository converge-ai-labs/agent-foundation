"""Cloudflare provider tests use fake HTTP responses; never call a cloud account."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.cloudflare.provider import CLOUDFLARE, HttpReference
from a13n_harness.providers.environment.cloudflare.shared import (
    CloudflareConnectionConfiguration,
    CloudflareEnvironmentConfiguration,
    CloudflareState,
    bridge_token,
    deployment,
)
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_service.providers.environments.cloudflare import CLOUDFLARE as MANAGEMENT
from a13n_service.providers.environments.cloudflare.bootstrap import manifest, prepare
from a13n_service.providers.environments.cloudflare.management import CloudflareManagement
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.anyio

ACCOUNT = "a" * 32
TOKEN = "fixture-cloudflare-token"
CONFIG = CloudflareEnvironmentConfiguration()
RUNTIME = NativeRuntime(
    CloudflareConnectionConfiguration(account_id=ACCOUNT), TokenCredential(api_key=SecretStr(TOKEN))
)


def state(**changes):
    script = deployment(ACCOUNT, TOKEN)
    values = dict(
        target_id="a13n-target",
        account_id=ACCOUNT,
        deployment=script,
        endpoint=f"https://{script}.fixture.workers.dev",
        environment_id="env_fixture",
        configuration_fingerprint=CONFIG.fingerprint,
    )
    values.update(changes)
    return EnvironmentState(provider_key="cloudflare", state_version="1", state=CloudflareState(**values).model_dump())


def transport(monkeypatch, handler):
    original = NativeHTTP.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.client._transport = httpx2.MockTransport(handler)

    monkeypatch.setattr(NativeHTTP, "__init__", initialize)


def test_registration_and_no_computer_or_manual_bridge_inputs():
    assert MANAGEMENT.execution is CLOUDFLARE
    assert MANAGEMENT.supports_stop and MANAGEMENT.requires_keepalive
    assert "computer" not in CLOUDFLARE.describe_environment(CONFIG).operation_families
    assert "endpoint" not in CloudflareConnectionConfiguration.model_fields
    with pytest.raises(ValidationError):
        CloudflareEnvironmentConfiguration(desktop=True)
    with pytest.raises(ValidationError):
        CloudflareConnectionConfiguration(account_id="../../other")


def test_saved_endpoint_and_credential_are_bound_to_the_deployment():
    foreign = state(endpoint="https://a13n-env-" + "b" * 32 + "-v1.fixture.workers.dev")
    with pytest.raises(EnvironmentProviderError):
        HttpReference(CONFIG, "env_fixture", foreign, RUNTIME)
    changed = NativeRuntime(RUNTIME.configuration, TokenCredential(api_key=SecretStr("different-token")))
    with pytest.raises(EnvironmentProviderError):
        HttpReference(CONFIG, "env_fixture", state(), changed)
    assert TOKEN not in state().model_dump_json()
    assert bridge_token(deployment(ACCOUNT, TOKEN), TOKEN) not in state().model_dump_json()


async def test_bootstrap_preserves_foreign_worker(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.method)
        result = {"subdomain": "fixture"} if request.url.path.endswith("/subdomain") else {"tags": ["someone-else"]}
        return httpx2.Response(200, json={"success": True, "result": result})

    transport(monkeypatch, handler)
    reference = CloudflareManagement(CONFIG, "env_fixture", None, RUNTIME, "op-create")
    with pytest.raises(EnvironmentProviderError) as error:
        await prepare(reference)
    assert error.value.code == "provider_deployment_conflict"
    assert calls == ["GET", "GET"]


async def test_bootstrap_automatically_uploads_and_reconciles(monkeypatch):
    script = deployment(ACCOUNT, TOKEN)
    calls = []
    setup = 0

    def handler(request):
        nonlocal setup
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/settings"):
            return httpx2.Response(404)
        if request.url.host == "api.cloudflare.com":
            assert request.headers["Authorization"] == "Bearer " + TOKEN
            if request.method == "PUT" and request.url.path.endswith(script):
                assert b"new_sqlite_classes" in request.content
                assert b"Environment" in request.content
                assert TOKEN.encode() not in request.content
                assert manifest().encode() in request.content
                return httpx2.Response(200, json={"success": True, "result": {"id": script}})
            return httpx2.Response(200, json={"success": True, "result": {"subdomain": "fixture"}})
        assert request.url.host == f"{script}.fixture.workers.dev"
        assert request.headers["X-A13N-Cloudflare-Token"] == TOKEN
        setup += 1
        return httpx2.Response(200, json={"phase": "submitted" if setup == 1 else "ready"})

    transport(monkeypatch, handler)
    reference = CloudflareManagement(CONFIG, "env_fixture", None, RUNTIME, "op-create")
    assert await prepare(reference) == f"https://{script}.fixture.workers.dev"
    assert setup == 2
    assert ("PUT", f"/client/v4/accounts/{ACCOUNT}/workers/scripts/{script}") in calls


async def test_inspection_and_attachment_do_not_provision(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.url.host == "api.cloudflare.com":
            return httpx2.Response(200, json={"success": True, "result": {"subdomain": "fixture"}})
        return httpx2.Response(
            200, json={"environment_id": "env_fixture", "fingerprint": CONFIG.fingerprint, "status": "stopped"}
        )

    transport(monkeypatch, handler)
    reference = HttpReference(CONFIG, "env_fixture", state(), RUNTIME)
    try:
        assert await reference.status() == "stopped"
    finally:
        await reference.close()
    assert calls == ["GET", "GET"]


async def test_management_carries_operation_and_version_without_account_token(monkeypatch):
    def handler(request):
        if request.url.host == "api.cloudflare.com":
            return httpx2.Response(200, json={"success": True, "result": {"subdomain": "fixture"}})
        assert request.headers["Authorization"] == "Bearer " + bridge_token(deployment(ACCOUNT, TOKEN), TOKEN)
        body = json.loads(request.content)
        assert body["operation_id"] == "op-stop" and body["version"] == 0
        assert TOKEN not in request.content.decode()
        return httpx2.Response(
            200,
            json={
                "environment_id": "env_fixture",
                "fingerprint": CONFIG.fingerprint,
                "status": "stopped",
                "version": 1,
            },
        )

    transport(monkeypatch, handler)
    reference = CloudflareManagement(CONFIG, "env_fixture", state(), RUNTIME, "op-stop")
    try:
        await reference.stop()
        assert reference.target.lifecycle_version == 1
    finally:
        await reference.close()


async def test_delete_before_allocation_does_not_create_cloud_resources(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network")

    monkeypatch.setattr(NativeHTTP, "__init__", forbidden)
    reference = CloudflareManagement(CONFIG, "env_fixture", None, RUNTIME, "op-delete")
    await reference.destroy()


def test_worker_lifecycle_and_proxy_faults():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the Cloudflare Worker contract tests"
    subprocess.run(
        [node, "--test", str(Path(__file__).with_name("cloudflare_worker.test.mjs"))], check=True, timeout=60
    )


async def test_saved_endpoint_cannot_exfiltrate_bridge_credentials(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        assert request.url.host == "api.cloudflare.com"
        return httpx2.Response(200, json={"success": True, "result": {"subdomain": "fixture"}})

    transport(monkeypatch, handler)
    forged = state(endpoint=f"https://{deployment(ACCOUNT, TOKEN)}.attacker.workers.dev")
    reference = HttpReference(CONFIG, "env_fixture", forged, RUNTIME)
    with pytest.raises(EnvironmentProviderError) as error:
        await reference.lookup()
    assert error.value.code == "provider_endpoint_conflict"
    assert calls == ["api.cloudflare.com"]
