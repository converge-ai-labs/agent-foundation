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
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
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

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

An `EnvironmentProviderDefinition` should:

1. declare a stable `type`, display name, and optional HTTPS setup link;
2. declare separate models for account configuration, credentials, and target recipes;
3. expose execution metadata only; Service composes a separate `EnvironmentManagementDefinition` for management;
4. return a fixed-target `EnvironmentConnector` from the pure `connector_factory`;
5. return a fresh ready `EnvironmentExecution` from every `open()`, with its own `execution_id` and operations;
6. validate fixed target references without changing authoritative state;
7. leave target management to Service or the embedding Host. Opening, checking, and closing execution never creates, starts, replaces, renews, or destroys targets.

The runnable [Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) shows one installed manifest with the same catalog, validation, construction, and Harness path. [Plugins and extensions](../a13n-harness/plugins.md#provider-plugins) covers the shared authoring contract.

For complete Host-side built-in lifecycles, including Docker state re-entry and explicit destruction, follow the [Built-in Provider Examples](examples.md).

## Built-in Providers

Execution uses a Native basic path or the general Envd path; resource management retains a separate implementation for each vendor. See the [execution model](index.md#choose-a-backend).

| Execution path | Provider         | Execution scope                                               | Resource management             |
| -------------- | ---------------- | ------------------------------------------------------------- | ------------------------------- |
| Native         | `direct_local`   | Files & Shell on the local Host                               | Host-owned directory            |
| Native / Envd  | `docker`         | Native Files & Shell; explicit HTTP EIP for general execution | Docker provider                 |
| Native         | `e2b`            | Files & Shell in a remote sandbox                             | E2B provider                    |
| Envd           | `local_envd`     | Local daemon over stdio                                       | Host-owned daemon               |
| Envd           | `daytona`        | EIP to a compatible sandbox daemon                            | Daytona provider                |
| Envd           | `modal`          | EIP to a compatible sandbox daemon                            | Modal provider                  |
| Envd           | `vercel`         | EIP to a compatible sandbox daemon                            | Vercel provider                 |
| Envd           | `sprites`        | EIP to a compatible sandbox daemon                            | Sprites provider                |
| Envd           | `runloop`        | EIP to a compatible sandbox daemon                            | Runloop provider                |
| Envd           | `http_envd`      | HTTP(S) EIP                                                   | Connect-only                    |
| Envd           | `websocket_envd` | Reverse WebSocket EIP                                         | Connect-only; Host owns ingress |

Native Local, Docker, and E2B provide Files & Shell directly. General execution on Daytona, Modal, Vercel, Sprites, and Runloop uses Envd/EIP, while their resource management providers continue to use vendor APIs. Docker retains explicit Native and Envd paths.

Before deploying Envd, check the [sandbox validation record](../a13n-envd/egress.md#cloud-platform-validation-record) for launch identities, egress coverage, and platform limits. Available execution capabilities follow daemon negotiation and Host policy.

Multi-tenant authorization and container allocation remain Host responsibilities. One Device supports concurrent independent Sessions; Sessions are not tenant partitions. Hosts share the Device connection and select a fixed cwd for each adapter, closing the shared runtime only at Host shutdown.

Start with the [built-in examples](examples.md) or [run both remote transports locally](remote-envd.md). HTTP/WebSocket Providers require a Host-supplied `EnvironmentState` that names the Device, expose execution definitions only, and do not provision or destroy infrastructure. The WebSocket SDK receives authenticated connections from your Host; it never opens a listener.

## Local Envd runtime

The Host selects one compatible `a13n-envd` executable and private-runtime allocator:

```python
from a13n_harness.providers.environment.local_envd.runtime import (
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

Docker uses an Engine connection from the Host process. The default native route executes through Docker exec; the HTTP EIP route also connects to the image-managed Envd daemon. A native runtime can borrow the Engine client:

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

The native Provider overrides the image entrypoint, enables Docker init support and keeps the container alive between Runs. Its private working directory is `/workspace`. Optional bind mounts expose existing directories on the Docker Engine's machine at explicit container targets; deletion preserves external data. Named-volume configuration is not supported. Registry authentication, credential helpers, mirrors and proxies remain Docker client configuration.

`close()` disconnects local observations without stopping the container or its background processes. A fresh managed adapter reuses the saved container; confirmed absence fails without silently creating a replacement. Transport failures do not prove absence. Docker file paths are native container paths, while Harness adds its aggregate mount prefix; relative tool paths start in `/workspace`.

Docker recipes accept `pull_policy="if_missing"` (the default) or `"never"`. `never` refuses missing images with `environment_image_missing` and never contacts a registry. Use `make image-sandbox` to build the image, and follow the [Docker lifecycle example](examples.md#docker) for direct Provider use. The Service manages Docker environments from templates; see [Service environments](../a13n-service/environments.md).

With `execution="envd_http"`, the connector opens HTTP EIP Sessions against the retained container. Closing a Session preserves the desktop; stopping and restarting the container preserves files but recreates GUI state. Configure the separate HTTP route and compatible image as described in [Docker configuration](configuration.md#docker).

## Cloud Providers

E2B, Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop each retain a resource management provider. E2B supplies Native Files & Shell; the other five use the general Envd execution path. Advanced capabilities on an E2B target also use a compatible Envd connection. Service E2B accounts select the E2B cloud or a compatible service by domain; see [Service environments](../a13n-service/environments.md#providers).

| Provider       | Backend settings                                                               | Credential fields               | Common recipe settings                                                         |
| -------------- | ------------------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------ |
| E2B            | `domain`, optional `api_url`                                                   | `api_key`                       | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`         |
| Daytona        | `organization_id`, `target` (default `us`)                                     | `api_key`                       | `snapshot`, which sets the sandbox's resources                                 |
| Modal          | `workspace`, existing deployed `app_name`, `environment_name` (default `main`) | `token_id`, `token_secret`      | `image` (default `python:3.13-slim`), `cpu`, `memory` (MiB), `timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                                        | `api_key` (Vercel access token) | `runtime` (default `python3.13`), `vcpus`, `timeout_seconds`                   |
| Fly.io Sprites | `organization`                                                                 | `api_key` (Sprites token)       | `region`                                                                       |
| Runloop        | `organization`                                                                 | `api_key`                       | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                        |

Use the organization, team, or Modal `workspace` that owns the supplied credentials; these fields describe backend namespaces, not permission grants. Credentials are never part of a recipe or reconnect state.

Daytona, Modal, Vercel, Sprites, and Runloop management providers prepare the target, compatible Envd runtime, and authorized connection. Images and snapshots may preinstall the daemon. EIP supplies file-path, command, process, and output semantics; OS sandbox and Egress enforce Host-selected policy. Capabilities follow actual negotiation. Migrate existing target references and configuration explicitly rather than silently interpreting legacy native execution settings as Envd settings.

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

Management providers reconcile interrupted creation through native names or ownership metadata. Unknown command dispatch is not replayed. Envd execution uses the shared EIP deadline, cancellation, output, and Session cleanup semantics; each vendor management provider retains target stop and destruction.

### E2B runtime

E2B executes commands directly through its native asynchronous SDK. Bounded Python helpers implement file operations. The default `base` template works without installing `a13n-envd`, uploading an executable, or building a custom template. Custom templates need Linux, Python 3.11+, Bash and the configured account/root; Git-ignore queries also need Git.

Use the Host-owned `PreparedSource` implementation from the [Harness environment guide](../a13n-harness/environments.md#supply-a-source). It returns the prepared connector on first use.

```python
import os

from a13n_service.providers.environments.e2b.definition import E2B
recipe = {"template": "base", "timeout_seconds": 300}
async with await E2B.open_provider(
    configuration={"domain": "e2b.dev"}, credential={"api_key": os.environ["E2B_API_KEY"]}
) as provider:
    state = await provider.create(recipe, environment_id="env-example", operation_id="op-create")
    await state_store.publish(environment_key, state)
    connector = E2B.execution.execution_connector(
        E2B.execution_environment(recipe), credential={"api_key": os.environ["E2B_API_KEY"]},
        environment_id="env-example", state=state
    )
result = await executable.run("Inspect the sandbox", environment=PreparedSource(connector))
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

The isolation boundary is the E2B sandbox. File-root mapping does not confine allowed shell commands. Per-command CPU, memory and process-count limits are unsupported. Per-command network denial requires sandbox-wide `allow_internet_access=False`; it cannot be added to an internet-enabled sandbox. Append and patch do not provide concurrent compare-and-swap guarantees.

Run the example with `E2B_API_KEY` set in the Host process environment:

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

The example explicitly destroys its sandbox in `finally`. Live integration tests are opt-in and also destroy their targets:

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### E2B desktop preparation

The E2B provider creates and resumes desktop resources. Native Execution remains Files & Shell; Agent Computer use runs through the general Envd path.

Select a template containing compatible Envd and desktop runtimes, establish an explicit [Envd connection](remote-envd.md), then configure the Agent's Computer tools and mount permissions separately. `desktop=true` manages desktop resources; EIP method negotiation determines screenshot and input capabilities. The template and management provider prepare fonts, desktop initialization, and human-viewer endpoints. See [Envd Computer use](../a13n-envd/computer-use.md) for input behavior.

Fonts belong to the sandbox template. E2B's default `desktop` template may lack Chinese glyphs even when input reaches the application correctly. Build a compatible template with `fonts-noto-cjk` using the [desktop template example](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/desktop_template.py):

```bash
# Set E2B_API_KEY securely in the environment first.
cd examples/environment-provider
uv sync --locked
uv run python desktop_template.py my-desktop-cjk
```

Select the custom desktop template in Console and create a new environment; existing sandboxes stay unchanged. This template example prepares fonts; configure Envd installation, startup, and connection separately. The operator owns the template, and fonts are installed at build time.

### Daytona

Default file root: `/home/daytona`. The standard sandbox supplies Python; commands select `python3` on the guest PATH. Custom snapshots must supply the selected root and executables. Stop requires confirmed disabled auto-delete; delete waits for terminal destruction rather than accepting the initial acknowledgement. See [Daytona's sandbox example](https://www.daytona.io/docs/en/guides/openai/openai-agents-sdk-with-sandboxes/) and [delete semantics](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/).

The Daytona management provider prepares a snapshot with Envd and desktop dependencies and checks the selected target during creation or start. Agent Computer operations use EIP. Attaching to an existing target checks readiness; tools are enabled from negotiated Envd capabilities.

Configure the Agent Computer toolset and mount permissions separately. Preinstall CJK fonts in the snapshot and reconcile interrupted input before continuing. Console viewing and human takeover use a separate desktop-access endpoint.

For Console viewing, Agent-requested assistance and user takeover, build a custom snapshot with the existing desktop access supervisor and CJK fonts:

```bash
# Supply DAYTONA_API_KEY privately with permission to create snapshots.
uv run --locked python examples/environment-provider/daytona_desktop_snapshot.py my-desktop-access --region us
```

Then use `{"snapshot": "my-desktop-access", "desktop": true, "desktop_assistance": true}` in the Service environment recipe. The snapshot starts the supervisor at boot; attaching or granting access does not install or start it. Snapshot creation needs additional permission beyond a sandbox-management-only Key; ordinary Service runtime does not build snapshots.

Daytona's signed preview host opens only port 6081. The supervisor separately enforces read-only viewing, a single controller, 60-second renewable grants and forced disconnect on return. Revoking a native signed preview URL alone is insufficient to close an established input connection. The Console reuses its existing viewing, acquire-control and return-control interactions. Agent execution must reach a confirmed safe boundary before control is granted; uncertain input or unmanaged background work blocks takeover.

New Daytona organizations must choose a default region in the Daytona dashboard before creating sandboxes. The connection's region does not replace that account setup requirement.

The existing `test_daytona_desktop_live.py` covers the legacy vendor-native Computer adapter and needs Envd execution coverage during migration. `test_daytona_access_live.py` checks the human-viewer endpoint for read-only enforcement, connection revocation, and lease expiry. These billable-target tests are separate from full Console/Agent handoff acceptance.

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
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

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
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

Provide `A13N_TEST_DAYTONA_BACKEND_JSON` and `A13N_TEST_DAYTONA_CREDENTIAL_JSON` through your private test environment; the corresponding uppercase Provider prefixes work for the other four. Optional `A13N_TEST_<PROVIDER>_RECIPE_JSON` overrides the recipe. The fixture allocates billable targets and attempts deletion in cleanup, including after failures. Never commit credential JSON. Missing opt-in or credentials produces a skip, not live validation.

Native API references: [E2B](https://e2b.dev/docs), [Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/), [Modal snapshots](https://modal.com/docs/guide/sandbox-snapshots), [Vercel Sandbox SDK](https://github.com/vercel/sandbox), [Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/), and [Runloop async Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html).

Docker templates can select `execution: envd_http` and require a desktop with `desktop: true`. Docker lifecycle still uses `docker_host`; HTTP EIP requires a separately configured reachable host and port-publication policy. See [the desktop setup](../a13n-envd/sandbox.md#managed-docker-desktop-in-service) for startup, permissions and restart behavior.
