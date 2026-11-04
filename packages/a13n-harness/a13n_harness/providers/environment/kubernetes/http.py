"""Consumer-owned HTTP connection preparation; normal execution stays on EIP."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace

import anyio

from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache, managed_http_runtime

from .configuration import KEY, KubernetesEnvironmentConfiguration, KubernetesState
from .runtime import KubernetesRuntime


@asynccontextmanager
async def port_forward(runtime: KubernetesRuntime, state: KubernetesState, port: int) -> AsyncGenerator[str]:
    profile = runtime.profile
    if not profile.allow_port_forward or profile.authentication != "kubeconfig":
        raise provider_error(KEY, "kubernetes_port_forward_denied", Category.DENIED)
    await runtime.pod(state)
    args = [
        profile.kubectl,
        "--kubeconfig",
        str(profile.kubeconfig),
        "--context",
        str(profile.context),
        "--namespace",
        state.namespace,
        "port-forward",
        "--address",
        "127.0.0.1",
        f"pod/{state.pod_name}",
        f":{port}",
    ]
    # kubectl selects the free port while binding, avoiding a reserve/close/rebind race.
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    assert process.stdout is not None
    drain: asyncio.Task | None = None
    try:
        async with asyncio.timeout(profile.request_timeout_seconds):
            while line := await process.stdout.readline():
                match = re.fullmatch(rb"Forwarding from 127\.0\.0\.1:(\d+) -> \d+\s*", line)
                if match:
                    endpoint = f"http://127.0.0.1:{int(match[1])}"
                    break
            else:
                raise provider_error(KEY, "kubernetes_port_forward_failed", Category.UNAVAILABLE)
        # A name-based forwarding request must not attach a replacement Pod.
        await runtime.pod(state)

        async def discard_output() -> None:
            assert process.stdout is not None
            while await process.stdout.read(4096):
                pass

        drain = asyncio.create_task(discard_output())
        yield endpoint
    finally:
        with anyio.CancelScope(shield=True):
            if process.returncode is None:
                process.terminate()
                with anyio.move_on_after(3):
                    await process.wait()
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)


@asynccontextmanager
async def endpoint(
    runtime: KubernetesRuntime, state: KubernetesState, config: KubernetesEnvironmentConfiguration
) -> AsyncGenerator[str]:
    pod = await runtime.pod(state)
    if pod["metadata"].get("deletionTimestamp") or pod.get("status", {}).get("phase") != "Running":
        raise provider_error(KEY, "kubernetes_pod_not_running", Category.UNAVAILABLE)
    if config.connection == "port_forward":
        async with port_forward(runtime, state, config.envd_port) as address:
            yield address
    else:
        address = pod.get("status", {}).get("podIP")
        if not address:
            raise provider_error(KEY, "kubernetes_pod_ip_pending", Category.UNAVAILABLE)
        ip = ipaddress.ip_address(address)
        host = f"[{ip}]" if ip.version == 6 else str(ip)
        yield f"http://{host}:{config.envd_port}"


@asynccontextmanager
async def http_runtime(
    runtime: KubernetesRuntime,
    state: KubernetesState,
    config: KubernetesEnvironmentConfiguration,
    *,
    credential_cache: ManagedCredentialCache | None = None,
) -> AsyncGenerator[HttpEnvdProviderRuntime]:
    if not runtime.profile.allow_plaintext_envd:
        raise provider_error(KEY, "kubernetes_plaintext_envd_denied", Category.DENIED)
    async with endpoint(runtime, state, config) as address:
        # The operator explicitly trusts this provider-private route. Grant only its
        # resolved origin, retaining Run host restrictions and every other HTTPS rule.
        policy = replace(
            runtime.connection._endpoint_policy,
            allowed_http_origins=runtime.connection._endpoint_policy.allowed_http_origins | {address},
        )

        async def credential() -> HttpEnvdCredential:
            token = runtime.connection._bootstrap
            if token is None:
                raise provider_error(KEY, "envd_bootstrap_required", Category.INVALID)
            return HttpEnvdCredential(token=token)

        async with managed_http_runtime(
            provider_key=KEY,
            connection=HttpEnvdConnectionConfiguration(
                endpoint=address, allow_plaintext_private_link=True, request_timeout=config.request_timeout_seconds
            ),
            endpoint_policy=policy,
            load_credential=credential,
            credential_cache=credential_cache,
            target_id=state.pod_uid or state.pod_name,
        ) as carrier:
            yield carrier


@asynccontextmanager
async def session(
    runtime: KubernetesRuntime,
    state: KubernetesState,
    config: KubernetesEnvironmentConfiguration,
    *,
    session_recovery: EnvdSessionRecovery | None = None,
) -> AsyncGenerator[EIPSessionBinding]:
    async with http_runtime(runtime, state, config) as carrier:
        async with carrier.open_session(
            expected_device_id=state.environment_id,
            session_recovery=session_recovery,
            # The forwarding port and Pod IP are connection details, not Session identity.
            recovery_target=json.dumps([KEY, state.profile_fingerprint, state.cluster, state.namespace, state.pod_uid]),
            working_directory=config.working_directory,
            required_methods=REQUIRED_METHODS,
        ) as binding:
            yield binding


def execution(
    runtime: KubernetesRuntime,
    state: KubernetesState,
    config: KubernetesEnvironmentConfiguration,
    environment_id: str,
    *,
    session_recovery: EnvdSessionRecovery | None = None,
) -> RemoteEnvdExecution:
    return RemoteEnvdExecution(
        environment=config.environment,
        provider_key=KEY,
        environment_id=environment_id,
        state=state.envelope(),
        working_directory=config.working_directory,
        session_context=session(runtime, state, config, session_recovery=session_recovery),
    )
