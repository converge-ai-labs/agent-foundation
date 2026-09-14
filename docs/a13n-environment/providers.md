# Providers and runtime configuration

Choose a Provider for the target you actually operate. Desired configuration is credential-free data; runtime collaborators contain clients, credentials, and Host-owned bootstrap resources. Writing a Provider key does not install or authorize it.

[Choose a backend](../environments/index.md#choose-a-backend) for the short comparison. This page covers catalogs, extension registration, and built-in runtime requirements.

## Provider catalog and plugins

`EnvironmentProviderCatalog` is an immutable explicit allowlist. Built-ins and installed extension entry points are selected by exact key, while trusted embedded code can supply Provider objects directly:

```python
from a13n_environment import (
    build_environment_provider_catalog,
    discover_environment_provider_references,
)

available = discover_environment_provider_references()
catalog = build_environment_provider_catalog(
    builtin_keys=("a13n.direct-local", "a13n.docker"),
    extension_keys=("acme.sandbox",),
    explicit_providers=(development_provider,),
)
provider = catalog.require("acme.sandbox")
```

Discovery returns sorted entry-point metadata without importing target modules. Catalog construction imports only `extension_keys`; an empty or explicit-only build does not scan installed metadata. It rejects malformed, missing, duplicate, and colliding keys before loading selected extension targets. `catalog.registrations` records the concrete class and built-in or distribution provenance in built-in, extension, then explicit order.

Package presence is availability, not authorization. No catalog accepts arbitrary serialized import targets, performs ambient activation, mutates a process-global registry, or reloads changed modules. Use a fresh Host process to load changed Provider code.

Register one concrete no-argument Provider class through the entry-point group:

```toml
[project.entry-points."a13n_environment.providers"]
"acme.sandbox" = "acme_agent_environment:AcmeSandboxProvider"
```

A Provider implementation should:

1. expose one stable namespaced `key` and `configuration_models`, mapping each supported version to its Pydantic recipe model; the base class derives `configuration_versions` and validation;
2. validate configuration into a frozen package-owned Pydantic model;
3. accept credentials, SDK clients, transport factories, and bootstrap stores only through a fresh process-local runtime collaborator;
4. return one fresh inert `Environment` from `create_environment()` and describe its configured capabilities without target I/O;
5. validate supplied state before mutation and update cached state at every target-identity transition;
6. expose provider-neutral `EnvironmentOperations` after entry;
7. keep `close()` non-destructive and implement target removal only in explicit `destroy()`.

The entry-point name and constructed `provider.key` must match. Preconstructed Provider objects are supported only through `explicit_providers`, which is intended for embedded applications, tests, and source development.

The runnable [Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) demonstrates both installed entry-point and explicit-object composition with the same immutable catalog, validation, construction, and Harness path.

For complete Host-side built-in lifecycles, including Docker state re-entry and explicit destruction, follow the [Built-in Provider Examples](examples.md).

## Built-in Providers

There are two operation routes: **Native** uses the host OS or vendor APIs directly; **Envd** uses one shared EIP operation implementation over different deployment and connection arrangements.

| Route  | Provider                    | Use it for                               | Operation and ownership boundary                          |
| ------ | --------------------------- | ---------------------------------------- | --------------------------------------------------------- |
| Native | `a13n.direct-local`         | Trusted local automation                 | Host OS operations; existing directory, no sandbox claim  |
| Native | `a13n.e2b`                  | Native managed cloud sandbox             | E2B SDK; sandbox create/pause/resume/renew/destroy        |
| Envd   | `a13n.local-envd`           | CLI and local Agents                     | Private stdio daemon; close preserves workspace           |
| Envd   | `a13n.docker` (Docker Envd) | Small single-node self-hosted services   | Docker lifecycle plus HTTP EIP; close preserves container |
| Envd   | `a13n.http-envd`            | Network-reachable external environments  | HTTP(S) EIP; connect-only                                 |
| Envd   | `a13n.websocket-envd`       | Environments that connect back to a Host | Reverse WebSocket EIP; Host-integrated SDK, connect-only  |

Direct Local shares the Host account; E2B uses the native sandbox API and needs no envd installation. Envd-backed Providers use EIP for Agent operations, not Docker exec/copy/logs. Docker Envd retains its existing `a13n.docker` key.

Multi-tenant authorization and container allocation remain Host responsibilities. One daemon admits one active Session; Sessions are not tenant partitions. To work with multiple remote environments, register separate daemon identities and coordinate their use.

Start with the [built-in examples](examples.md) or [run both remote transports locally](remote-envd.md). HTTP/WebSocket Providers require external state, declare `supports_managed=False`, and do not provision or destroy infrastructure. The WebSocket SDK receives authenticated connections from your Host; it never opens a listener.

## Local Envd runtime

The Host selects one compatible `a13n-envd` executable and private-runtime allocator:

```python
from a13n_environment import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

`resolve_a13n_envd_executable()` checks an explicit argument, then `A13N_ENVD_EXECUTABLE`, then `a13n-envd` or `a13n-envd.exe` on `PATH`. The library does not load `.env`, install a native binary, or silently fall back to Direct Local. Entry validates exact daemon/client compatibility and the required native-isolation probe.

Run the real provider path with `make local-envd-test`, or follow the [Local Envd example](examples.md#local-envd).

## Docker runtime

Docker requires an async engine adapter and a durable bootstrap store:

```python
from pathlib import Path

from a13n_environment import (
    DirectoryDockerBootstrapStore,
    DockerProviderRuntime,
    DockerSDKEngine,
)

runtime = DockerProviderRuntime(
    engine=DockerSDKEngine.from_env(),
    bootstrap_store=DirectoryDockerBootstrapStore(
        Path("/var/lib/my-host/docker-bootstrap")
    ),
)
```

The built-in directory store keeps its POSIX Host root private with mode `0700`. Files inside an allocation remain readable by the fixed non-root container user through the bind mount; unrelated Host users cannot traverse the private parent. A Host needing different ownership, persistence, or sharing supplies another `DockerBootstrapStore`.

Docker publishes EIP only on a Docker-assigned `127.0.0.1` Host port. It can use existing Host bind directories and external named volumes, but it never creates or deletes those sources. Registry authentication, credential helpers, mirrors, and proxies remain Docker client configuration.

Run the real image and Harness lifecycle check with `make docker-provider-test`, or follow the [Docker state lifecycle example](examples.md#docker).

## E2B runtime

E2B executes commands directly through its native asynchronous SDK. Bounded Python helpers implement files and port checks only. The default `base` template works without installing `a13n-envd`, uploading an executable, or building a custom template. Custom templates need Linux, Python 3.11+, Bash and the configured account/root; Git-ignore queries also need Git.

```python
import os
from pydantic import SecretStr
from a13n_environment import E2BEnvironment, E2BProviderConfiguration, E2BProviderRuntime

configuration = E2BProviderConfiguration(template="base", timeout_seconds=300)
runtime = E2BProviderRuntime(api_key=SecretStr(os.environ["E2B_API_KEY"]))
environment = E2BEnvironment(
    configuration, environment_id="environment-example", state=None, runtime=runtime,
)
```

Pass this Environment to Harness as usual. Persist `environment.dump_state()` on the Host and give it to a fresh adapter for re-entry. `close()` preserves the sandbox and user files; it disconnects this adapter's output observations without killing commands. Use fresh adapters for `stop()` (pause), `prepare()` (resume) and `destroy()` (kill). Keepalive reports actual expiry and never resumes a paused sandbox. The library never reads `.env`; a13n Service receives the domain through Provider Backend configuration and `api_key` through its credential reference.

A fresh adapter can use native process discovery to find commands still running in the same sandbox. This is best-effort: the sandbox ID is not proof that a particular process survived, and missing commands are never restarted automatically. Native listing is not paginated by the SDK; returned projections are bounded, but the upstream inventory is not.

Output is SDK-decoded text, not lossless original bytes. The defaults separate command concurrency from history retention:

| Setting                     | Default      | Scope                                                                                                                   |
| --------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `max_active_observations`   | 128          | Active native attachments, including pending starts/connects; completed or disconnected observations release their slot |
| `max_observation_bytes`     | 1 MiB        | Cumulative combined stdout/stderr for one observation, including output subsequently evicted                            |
| `max_retained_output_bytes` | 128 MiB      | Adapter-wide retained output; evicts the oldest closed logs first                                                       |
| `timeout_seconds`           | 3600 seconds | Whole-sandbox TTL, not a command deadline                                                                               |
| `request_timeout_seconds`   | 30 seconds   | Native request timeout, not tool waiting time                                                                           |

Discovery alone allocates no output buffer or active slot. A separate 100,000-reference metadata guard requires explicit release before admitting more references; it does not silently invalidate completed handles. Closed output may be evicted under memory pressure, but its reference, known status and cumulative offsets survive. Reads explicitly report `observation_evicted`, partial coverage and the remaining available range. If active buffers exhaust the aggregate budget, or one command reaches its cumulative budget, the adapter disconnects that observation without killing the command. Status queries and repeated waits cannot reset budgets. A transient reconnect before the cap appends text to the same log with partial coverage; eviction does not replenish its allowance, while a fresh Run starts a new observation. Direct application logs to files when complete durable output matters.

E2B uses native login Bash, so direct argv, explicit non-login mode and environment unsets are unsupported. Native stdin has a per-request size bound, not a cross-Run quota. Native kill is supported; interrupt/terminate, process-tree verification and per-command hard deadlines are not. `execution_timeout_seconds` is rejected before command launch. SDK connection/request waits, Harness tool waits and sandbox expiry are different limits. Sandbox TTL still applies while commands run without an observer.

The isolation boundary is the E2B sandbox. File-root mapping does not confine allowed shell commands. Per-command CPU, memory and process-count limits are unsupported. Per-command network denial requires sandbox-wide `allow_internet_access=False`; it cannot be added to an internet-enabled sandbox. Port inspection supports loopback TCP only. Append and patch do not provide concurrent compare-and-swap guarantees.

Run the example with `E2B_API_KEY` set in the Host environment:

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

The example explicitly destroys its sandbox in `finally`. Live integration tests are opt-in and also destroy their targets:

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```
