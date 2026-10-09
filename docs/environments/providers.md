---
title: Providers and runtime configuration
sidebarTitle: Providers and runtime
description: Register Provider plugins and configure the runtime, lifecycle, and limits of each built-in Provider.
---

The recipe describes the target; the runtime holds its clients and credentials.

[Choose a backend](index.md#choose-a-backend) for the short comparison. This page covers catalogs, extension registration, and built-in runtime requirements.

## Provider catalog and plugins

`ProviderCatalog` is an immutable explicit allowlist, shared by all five Provider domains: Model, Web, Connector, Memory, and Environment. Built-in Environment definitions are selected by exact type, and installed plugins contribute their own definitions:

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
catalog = ProviderCatalog(
    (
        *select_builtin_environment_providers(("direct_local", "docker")),
        *(item for plugin in plugins for item in plugin.manifest.environment),
    )
)
definition = catalog.require("acme_sandbox")
```

`load_provider_plugins()` imports only the entry-point names you list; an empty selection scans no installed metadata. It rejects a duplicate, malformed, missing, or ambiguous name, and reports the distribution name, version, and import target of each loaded plugin as provenance. The catalog then rejects a duplicate Provider type, so a plugin cannot shadow a built-in.

Select plugins explicitly and restart the Host after changing their code.

An installed distribution publishes one manifest under the shared entry-point group:

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_agent_environment:manifest"
```

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=2, environment=(ACME_SANDBOX,))
```

An `EnvironmentProviderDefinition` should:

1. declare a stable `type`, display name, and optional HTTPS setup link;
2. declare separate models for account configuration, credentials, and target recipes;
3. return an account-scoped `EnvironmentProvider` from `provider_factory` for explicit management;
4. return a fixed-target `EnvironmentConnector` from the pure `connector_factory`;
5. return a fresh ready `EnvironmentExecution` from every `open()`, with its own `execution_id` and operations;
6. validate references and preserve known state on management failure or cancellation; execution does not change it;
7. declare management capabilities truthfully. Opening, checking, and closing execution never creates, starts, replaces, renews, or destroys targets.

The runnable [Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) shows one installed manifest with the same catalog, validation, construction, and Harness path. [Plugins and extensions](../a13n-harness/plugins.md#provider-plugins) covers the shared authoring contract.

For complete Host-side built-in lifecycles, including Docker state re-entry and explicit destruction, follow the [Built-in Provider Examples](examples.md).

## Built-in Providers

There are two operation routes: **Native** uses the Host OS or vendor APIs directly; **Envd** uses one shared EIP operation implementation over different deployment and connection arrangements.

| Route  | Provider         | Use it for                          | Operation and ownership boundary                            |
| ------ | ---------------- | ----------------------------------- | ----------------------------------------------------------- |
| Native | `direct_local`   | Trusted local automation            | Host OS operations; existing directory, no sandbox claim    |
| Native | `e2b`            | Native managed cloud sandbox        | E2B SDK; sandbox create/pause/resume/renew/destroy          |
| Native | `daytona`        | Cloud sandbox                       | Native stop/start and preserved files                       |
| Native | `modal`          | Cloud sandbox                       | Snapshot-backed stop/resume; fixed running lifetime         |
| Native | `vercel`         | Cloud sandbox                       | Named persistent sandbox with native sessions               |
| Native | `sprites`        | Cloud sandbox                       | Persistent disk and automatic sleep/wake                    |
| Native | `runloop`        | Cloud sandbox                       | Devbox suspend/resume and idle keepalive                    |
| Envd   | `local_envd`     | CLI and local Agents                | Shared Host Device; adapter close ends its Session          |
| Native | `docker`         | Single-host services                | Docker Engine lifecycle and exec; close preserves container |
| Envd   | `http_envd`      | Network-reachable external daemons  | HTTP(S) EIP; connect-only                                   |
| Envd   | `websocket_envd` | Daemons that connect back to a Host | Reverse WebSocket EIP; Host-integrated SDK, connect-only    |

Direct Local shares the Host account. Docker uses native Engine operations; all six cloud Providers use native vendor transports. None requires Envd. Local and remote Envd Providers use EIP for Agent operations.

For separately deployed Envd, see the [sandbox validation record](../a13n-envd/egress.md#cloud-platform-validation-record) for E2B, Runloop, Vercel, Daytona and both Modal runtimes. It records the tested binary, launch identities, egress coverage and platform limits; it does not imply that these native Providers bootstrap Envd or configure its egress policy.

Multi-tenant authorization and container allocation remain Host responsibilities. One Device supports concurrent independent Sessions; Sessions are not tenant partitions. Hosts share the Device connection and select a fixed cwd for each adapter, closing the shared runtime only at Host shutdown.

Start with the [built-in examples](examples.md) or [run both remote transports locally](remote-envd.md). HTTP/WebSocket Providers require a Host-supplied `EnvironmentState` that names the Device, declare `supports_managed=False`, and do not provision or destroy infrastructure. The WebSocket SDK receives authenticated connections from your Host; it never opens a listener.

## Local Envd runtime

The Host selects one compatible `a13n-envd` executable and private-runtime allocator:

```python
from a13n_environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

`resolve_a13n_envd_executable()` checks an explicit argument, then `A13N_ENVD_EXECUTABLE`, then `a13n-envd` or `a13n-envd.exe` on `PATH`. The library does not load `.env`, install a native binary, or silently fall back to Direct Local. First Device acquisition validates exact daemon/client compatibility. The runtime launches one shared Device lazily; every adapter opens its own Session. Close adapters after use and call `await runtime.close()` at Host shutdown. There is no native-isolation probe. Envd enforces the launch Sandbox and egress mode you select; the Host's account, container, or VM sets the outer boundary.

Run the real Provider path with `make local-envd-test`, or follow the [Local Envd example](examples.md#local-envd).

## Docker runtime

Docker uses an Engine connection from the Host process. No bootstrap store, guest daemon or published control port is needed:

```python
import docker
from a13n_environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

The native Provider overrides the image entrypoint, enables Docker init support and keeps the container alive between Runs. Its private working directory is `/workspace`. Optional bind mounts expose existing directories on the Docker Engine's machine at explicit container targets; deletion preserves external data. Named-volume configuration is not supported. Registry authentication, credential helpers, mirrors and proxies remain Docker client configuration.

`close()` disconnects local observations without stopping the container or its background processes. A fresh managed adapter reuses the saved container; confirmed absence creates a replacement with an empty private `/workspace`. Transport failures do not prove absence. Docker file paths are native container paths, while Harness adds its aggregate mount prefix; relative tool paths start in `/workspace`.

Docker recipes accept `pull_policy="if_missing"` (the default) or `"never"`. `never` refuses missing images with `environment_image_missing` and never contacts a registry. Use `make image-sandbox` to build the image, and follow the [Docker lifecycle example](examples.md#docker) for direct Provider use. The Service manages Docker environments from templates; see [Service environments](../a13n-service/environments.md).

## Cloud Providers

E2B (`e2b`), Daytona (`daytona`), Modal (`modal`), Vercel Sandbox (`vercel`), Fly.io Sprites (`sprites`), and Runloop (`runloop`) provide cloud execution without installing Envd. All support files and shell commands; E2B additionally supports process observations, stdin, retained SDK text output, and loopback ports. The Service offers all six as environment provider types; its E2B accounts always use the E2B cloud ([Service environments](../a13n-service/environments.md#providers)).

| Provider       | Backend settings                                                               | Credential fields               | Common recipe settings                                                         |
| -------------- | ------------------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------ |
| E2B            | `domain`, optional `api_url`                                                   | `api_key`                       | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`         |
| Daytona        | `organization_id`, `target` (default `us`)                                     | `api_key`                       | `snapshot`, which sets the sandbox's resources                                 |
| Modal          | `workspace`, existing deployed `app_name`, `environment_name` (default `main`) | `token_id`, `token_secret`      | `image` (default `python:3.13-slim`), `cpu`, `memory` (MiB), `timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                                        | `api_key` (Vercel access token) | `runtime` (default `python3.13`), `vcpus`, `timeout_seconds`                   |
| Fly.io Sprites | `organization`                                                                 | `api_key` (Sprites token)       | `region`                                                                       |
| Runloop        | `organization`                                                                 | `api_key`                       | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                        |

Use the organization, team, or Modal `workspace` that owns the supplied credentials; these fields describe backend namespaces, not permission grants. Credentials are never part of a recipe or reconnect state.

Daytona, Modal, Vercel, Sprites, and Runloop recipes accept `root`, `python`, `shell`, and bounded request/file/output settings. `python` names an executable on the guest PATH or an absolute guest path; it never discovers a Host executable. Custom images and snapshots must include Linux, Python 3 with the standard library, and the selected shell. The root maps file paths such as `/notes.txt` into that guest directory; shell execution retains the authority of the native guest user. The default Modal image uses `/usr/local/bin/python3`; Vercel defaults to `/vercel/sandbox` as its root. Those five Providers do not advertise process handles, ports, retained output, interactive stdin, per-command network denial, or resource limits other than wall time. Unsupported requests fail before command execution.

### Reconnection and lifecycle

The Host saves the latest `EnvironmentState` and supplies it to a fixed-target connector. Execution close releases only owned resources. A lost saved target is never automatically rebuilt; management must explicitly decide what follows. Timeouts, permission failures, and unknown responses do not count as absence.

| Provider       | Explicit stop/resume                                                            | Memory                    | Expiry and retention                                                                                                                                                                                                          |
| -------------- | ------------------------------------------------------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E2B            | Native pause/resume preserves files                                             | Preserved by native pause | Renewable whole-sandbox TTL; keepalive reports observed expiry without resuming a paused sandbox.                                                                                                                             |
| Daytona        | Native stop/start, including archived sandboxes                                 | Not promised              | Requests disabled automatic stop/delete and no hard TTL. Organization-enforced hard TTL is rejected during readiness.                                                                                                         |
| Modal          | Filesystem snapshot, then terminate; resume creates a sandbox from the snapshot | Not preserved             | Running sandbox has a fixed maximum of 24 hours. Keepalive reports the conservative known deadline and refuses to promise an extension. Stop snapshots have no expiry and are deleted after successful resume or destruction. |
| Vercel Sandbox | Named native sandbox stop/resume with filesystem preservation                   | Not preserved             | Running sessions have a bounded timeout. Renewal verifies the returned deadline and respects the configured total limit. Native stop snapshots are configured without expiry.                                                 |
| Fly.io Sprites | No explicit stop API; idle Sprites sleep and wake on exec                       | Not promised              | Durable filesystem survives native automatic sleep. The Provider does not support `stop()`, so do not schedule an explicit stop; destruction is separate.                                                                     |
| Runloop        | Native suspend/resume preserves disk                                            | Not preserved             | Idle policy suspends the Devbox. Keepalive acknowledges a new idle interval; it cannot start a suspended target.                                                                                                              |

Modal stop first saves a filesystem snapshot. Explicit `start()` restores from that snapshot and returns the resulting reference; `open()` performs neither restoration nor snapshot cleanup. Snapshot deletion requires matching native ownership tags. External registration supplies a connector only; destruction authority belongs to management and cannot be inferred from a state reference.

An interrupted create is reconciled through the native name or ownership metadata. Unknown dispatched commands are not replayed. For Daytona, Modal, Vercel, Sprites, and Runloop, each foreground command has a bounded guest-side deadline; losing or cancelling its transport does not prove the command stopped immediately. Cancellation closes local transport resources; the guest command runner bounds the foreground command and kills its process group. This is not sandbox-wide process containment: descendants that deliberately detach into a new session require native target lifecycle cleanup. Timeout output is partial and has no invented producer total. Modal's SDK retries native commands with a stable exec ID and filesystem snapshots with a stable snapshot request ID.

### E2B runtime

E2B executes commands directly through its native asynchronous SDK. Bounded Python helpers implement files and port checks only. The default `base` template works without installing `a13n-envd`, uploading an executable, or building a custom template. Custom templates need Linux, Python 3.11+, Bash and the configured account/root; Git-ignore queries also need Git.

```python
import os

from a13n_environment.builtins import select_builtin_environment_providers

(E2B,) = select_builtin_environment_providers(("e2b",))
recipe = {"template": "base", "timeout_seconds": 300}
async with await E2B.open_provider(
    configuration={"domain": "e2b.dev"}, credential={"api_key": os.environ["E2B_API_KEY"]}
) as provider:
    state = await provider.create(recipe, environment_id="env-example", operation_id="op-create")
    await state_store.publish(environment_key, state)
    connector = provider.execution_connector(recipe, environment_id="env-example", state=state)
result = await executable.run("Inspect the sandbox", environment=connector)
```

`close()` preserves the sandbox and files, disconnecting this execution's observations without killing commands. Management explicitly calls `stop()`, `start()`, `keepalive()`, and `destroy()`. Opening uses a read-only lookup of a running target without renewal or resume, and rejects E2B targets configured for automatic resume. The library never reads `.env`; the Host supplies credentials.

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

E2B uses native login Bash, so direct argv, explicit non-login mode and environment unsets are unsupported. Native stdin has a per-request size bound, not a cross-Run quota. Native kill is supported; interrupt/terminate, process-tree verification and per-command hard deadlines are not. A per-command wall-time limit (`limits.wall_time_seconds`, or the shell tool's `execution_timeout_seconds`) is rejected before command launch. SDK connection/request waits, Harness tool waits and sandbox expiry are different limits. Sandbox TTL still applies while commands run without an observer.

The isolation boundary is the E2B sandbox. File-root mapping does not confine allowed shell commands. Per-command CPU, memory and process-count limits are unsupported. Per-command network denial requires sandbox-wide `allow_internet_access=False`; it cannot be added to an internet-enabled sandbox. Port inspection supports loopback TCP only. Append and patch do not provide concurrent compare-and-swap guarantees.

Run the example with `E2B_API_KEY` set in the Host process environment:

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

The example explicitly destroys its sandbox in `finally`. Live integration tests are opt-in and also destroy their targets:

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### Daytona

Default file root: `/home/daytona`. The standard sandbox supplies Python; commands select `python3` on the guest PATH. Custom snapshots must supply the selected root and executables. Stop requires confirmed disabled auto-delete; delete waits for terminal destruction rather than accepting the initial acknowledgement. See [Daytona's sandbox example](https://www.daytona.io/docs/en/guides/openai/openai-agents-sdk-with-sandboxes/) and [delete semantics](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/).

### Modal

Default image: `python:3.13-slim`, with `/usr/local/bin/python3`; root `/` is writable by that image's root user. Use an existing deployed App. Managed stop snapshots files before termination; resume replaces the native sandbox, and successful readiness permits snapshot cleanup. Running sandbox TTL cannot be extended beyond its known deadline.

### Vercel Sandbox

Default runtime: `python3.13`; root: `/vercel/sandbox`. Named persistent sandboxes use native stop/resume. Target identity includes native creation time; a new running session alone does not replace the persistent filesystem identity. Delete also requests native asynchronous cleanup of orphan snapshots.

### Fly.io Sprites

Default root: `/home/sprite`; Python: guest-PATH `python3`. [Sprites' environment guide](https://docs.sprites.dev/working-with-sprites/) documents the writable home and preinstalled tools. Native automatic sleep preserves disk; explicit stop is unsupported. Destruction waits for confirmed absence.

### Runloop

Default root: `/home/user`; Python: guest-PATH `python3`. Runloop documents its [default unprivileged user](https://docs.runloop.ai/docs/devboxes/configuration/user-parameters) and [preinstalled Python stack](https://docs.runloop.ai/docs/devboxes/overview). Keepalive uses the returned target idle interval. Shutdown completion is checked before clearing state.

### Embedded configuration

Use the same catalog and typed runtime construction as Service:

```python
from a13n_environment.builtins import select_builtin_environment_providers

(DAYTONA,) = select_builtin_environment_providers(("daytona",))
connector = DAYTONA.execution_connector(
    {}, configuration={"organization_id": "your-organization"},
    credential={"api_key": api_key}, environment_id="env-workspace", state=saved_state,
)
async with await connector.open() as execution:
    assert execution.operations.files is not None
    listing = await execution.operations.files.list("/", max_results=20)
```

`execution_connector()` validates inputs without acquiring clients. `open()` acquires clients for each execution. The Host closes borrowed runtimes; management and execution clients have independent lifetimes.

### Cloud validation

Deterministic tests run the adapters through catalog construction, native HTTP responses, Sprites WebSocket frames, and Modal's real async SDK against local gRPC fixtures. They execute the actual file and shell helpers. Cloud tests are separate and opt-in:

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-environment/tests/test_cloud_live.py
```

Provide `A13N_TEST_DAYTONA_BACKEND_JSON` and `A13N_TEST_DAYTONA_CREDENTIAL_JSON` through your private test environment; the corresponding uppercase Provider prefixes work for the other four. Optional `A13N_TEST_<PROVIDER>_RECIPE_JSON` overrides the recipe. The fixture allocates billable targets and attempts deletion in cleanup, including after failures. Never commit credential JSON. Missing opt-in or credentials produces a skip, not live validation.

Native API references: [E2B](https://e2b.dev/docs), [Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/), [Modal snapshots](https://modal.com/docs/guide/sandbox-snapshots), [Vercel Sandbox SDK](https://github.com/vercel/sandbox), [Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/), and [Runloop async Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html).
