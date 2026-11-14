---
title: Cloud Provider setup and limits
sidebarTitle: Cloud Providers
description: Prepare cloud accounts and runtimes, choose native or Envd execution, and manage backend-specific lifetimes.
---

Choose a backend in the [Environment overview](index.md#choose-a-backend). This guide owns cloud-specific preparation and differences; use [configuration](configuration.md) for input ownership and [lifecycle](lifecycle.md) for state publication.

## Cloud Providers

Prepare the selected account and recipe before creating a billable target. Daytona and Modal use Envd. E2B, Vercel, and Sprites offer Native Files & Shell and an explicit Envd path. Runloop and boxd provide Native Files & Shell; Envd, Computer, and human desktop access are not integrated.

| Provider       | Backend settings                                                                                 | Credential fields               | Common recipe settings                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| E2B            | `domain`, optional `api_url`                                                                     | `api_key`                       | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`                                                        |
| Daytona        | `organization_id`, `target` (default `us`)                                                       | `api_key`                       | `snapshot`, which sets the sandbox's resources                                                                                |
| Modal          | optional `workspace`, `app_name` (default `a13n-sandboxes`), `environment_name` (default `main`) | `token_id`, `token_secret`      | Service automatic desktop `image="auto"` or custom Envd-compatible image, `desktop`, `cpu`, `memory` (MiB), `timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                                                          | `api_key` (Vercel access token) | `execution`, `snapshot`, `desktop`, `runtime`, `vcpus`, `timeout_seconds`                                                     |
| Fly.io Sprites | `organization`                                                                                   | `api_key` (Sprites token)       | `region`                                                                                                                      |
| Runloop        | `organization`                                                                                   | `api_key`                       | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                                                                       |
| boxd           | `region` (`fra` or `nyc`), optional `organization`                                               | `api_key` (boxd API key)        | `size`, `from_snapshot`, `auto_suspend_timeout_seconds`, `auto_hibernate_timeout_seconds`                                     |

Store credentials outside recipes and reconnect state. Organization/team fields name backend namespaces, not permission grants. Modal credentials select the workspace; its optional `workspace` field is a label. Service E2B connections select E2B cloud or a compatible backend by domain; see [Service providers](../a13n-service/environments.md#providers).

Headless Envd preparation uses POSIX utilities without installing guest Python, jq or desktop packages. Native Files & Shell helpers retain their Python requirements. Custom preinstalled images provide compatible Envd, the selected users/workspace and boot command; a plain image is not an installation request.

Management prepares the target and, for the Envd path, its daemon and authorized connection. EIP supplies execution semantics; Sandbox and egress settings enforce the chosen execution policy. Migrate existing Native targets explicitly when adopting Envd.

### Reconnection and lifecycle

Use this sequence around every managed target:

1. Create or explicitly start the target through management.
2. Save the latest returned `EnvironmentState` before exposing it to execution.
3. Build a fixed-target connector and close each execution after use.
4. Stop, renew, or destroy through management according to the backend's supported lifecycle.

| Provider       | Explicit stop/resume                                                            | Memory                    | Expiry and retention                                                                                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E2B            | Native pause/resume preserves files                                             | Preserved by native pause | Renewable whole-sandbox TTL; keepalive reports observed expiry without resuming a paused sandbox.                                                                                                                                                 |
| Daytona        | Native stop/start, including archived sandboxes                                 | Not promised              | Requests disabled automatic stop/delete and no hard TTL. Organization-enforced hard TTL is rejected during readiness.                                                                                                                             |
| Modal          | Filesystem snapshot, then terminate; resume creates a sandbox from the snapshot | Not preserved             | Running sandbox has a fixed maximum of 24 hours. Keepalive reports the conservative known deadline and refuses to promise an extension. Stop snapshots have no expiry and are deleted after successful resume or destruction.                     |
| Vercel Sandbox | Named native sandbox stop/resume with filesystem preservation                   | Not preserved             | Running sessions have a bounded timeout. Renewal verifies the returned deadline and respects the configured total limit. Native stop snapshots are configured without expiry.                                                                     |
| Fly.io Sprites | No explicit stop API; idle Sprites sleep and wake on exec                       | Not promised              | Durable filesystem survives native automatic sleep. The Provider does not support `stop()`, so do not schedule an explicit stop; destruction is separate.                                                                                         |
| Runloop        | Native suspend/resume preserves disk                                            | Not preserved             | Idle policy suspends the Devbox. Keepalive acknowledges a new idle interval; it cannot start a suspended target.                                                                                                                                  |
| boxd           | Native hibernate/wake preserves disk and memory                                 | Preserved by hibernation  | Machines have no expiry and need no keepalive. Idle hibernation is off by default; when `auto_hibernate_timeout_seconds` is set, an idle machine hibernates and stays stopped until `start()`. Optional idle standby resumes on the next command. |

For Modal, `start()` restores the saved filesystem snapshot and returns the new reference. `open()` neither restores nor cleans up snapshots. Snapshot deletion requires matching native ownership tags. An externally registered state supplies connection information, not destruction authority.

On interrupted creation, reconcile native names or ownership metadata. Treat timeouts, permission errors, and unknown responses as unresolved outcomes, not absence. Never rebuild a lost target or replay an uncertain command implicitly. Managed Envd launchers replace a crashed daemon up to three times with 1/2/4-second backoff; desktop launchers retain the display/access processes. Sprites uses its native service, and Kubernetes uses the Pod's restart policy. Crash recovery creates a new daemon Session, so reconnect and inspect before further actions. Live hangs and interrupted commands are not replayed. Envd uses shared deadline, cancellation, output, and Session-cleanup behavior; vendor management owns stop and destruction.

### E2B runtime

For a first Native sandbox, use the default `base` template. It needs no Envd installation or custom build. Custom templates require Linux, Python 3.11+, Bash, and the configured account/root; Git-ignore queries also require Git.

The Native defaults are root `/home/user`, user `user`, Python `/usr/bin/python3`, and internet access enabled. Supply the E2B domain in account settings and the API key separately.

To integrate creation with Harness, prepare `E2B_API_KEY`, an application state store and `environment_key`, your `executable`, and the [`PreparedSource` helper](../a13n-harness/environments.md#supply-a-source). This fragment creates a billable target and publishes its state; your management code must later stop or destroy it:

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

Opening uses a read-only lookup of a running target, without renewal or resume, and rejects targets configured for automatic resume. Closing preserves the sandbox, files, and commands while disconnecting execution observations. Supply credentials from the Host; the library does not read `.env`.

A new execution can discover still-running native processes in the same sandbox. Inspect the results: sandbox identity does not prove process survival, and missing commands are not restarted. Native discovery has no SDK pagination; returned projections are bounded, but upstream inventory is not.

Tune output separately from sandbox lifetime. Output is SDK-decoded text, not lossless bytes:

| Setting                     | Default      | Scope                                                                                                                   |
| --------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `max_active_observations`   | 128          | Active native attachments, including pending starts/connects; completed or disconnected observations release their slot |
| `max_observation_bytes`     | 1 MiB        | Cumulative combined stdout/stderr for one observation, including output subsequently evicted                            |
| `max_retained_output_bytes` | 128 MiB      | Adapter-wide retained output; evicts the oldest closed logs first                                                       |
| `timeout_seconds`           | 3600 seconds | Whole-sandbox TTL, not a command deadline                                                                               |
| `request_timeout_seconds`   | 30 seconds   | Native request timeout, not tool waiting time                                                                           |

For long-running commands, write durable application logs to files. Observation limits work as follows:

- Discovery allocates neither an output buffer nor an active slot. At 100,000 references, release references before admitting more; completed handles are not silently invalidated.
- Memory pressure can evict closed output while retaining references, known status, and offsets. Reads report `observation_evicted`, partial coverage, and the available range.
- Reaching a command's cumulative budget or exhausting active aggregate buffers disconnects observation without killing the command. Repeated waits and status queries do not reset budgets.
- Reconnection before the cap appends to the same log with partial coverage. Eviction does not replenish the allowance; a fresh Run creates a new observation.

Use E2B's login Bash and native kill. Direct argv, explicit non-login mode, environment unsets, interrupt/terminate, process-tree verification, and per-command hard deadlines are unsupported. `limits.wall_time_seconds` and shell `execution_timeout_seconds` are rejected before launch. Native stdin is bounded per request. Request waits, tool waits, and sandbox TTL are distinct; TTL still applies without an observer.

The sandbox is the isolation boundary. File-root mapping does not confine shell commands. CPU, memory, and process-count limits are not configurable per command. Network denial requires sandbox-wide `allow_internet_access=False`; it cannot be added to an internet-enabled target. Append and patch provide no concurrent compare-and-swap guarantee.

For a complete example with cleanup, install [the example project](examples.md#install), set `E2B_API_KEY` in the Host environment, and run from the repository root:

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

Expect a file read, the Provider name, and either a recovered native command or an explicit report that it is no longer discoverable. The example destroys its sandbox in `finally`. From the repository root, opt into the live integration test with:

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### E2B headless preparation

Select Envd HTTP without Desktop, then **Install headless Envd**. Service installs on the selected sandbox and preserves E2B's SDK daemon. Choose a GNU/Linux x86_64 template with glibc 2.39 or newer, apt-get and util-linux, such as a base-only Debian 13 or Ubuntu 24.04+ template. The stock `base` may use older Bookworm and fail with template guidance; Service does not upgrade libc. Custom templates default to **Preinstalled Envd** unless you opt into installation.

```json
{"template":"your-compatible-base","execution":"envd_http","preparation":"headless","timeout_seconds":1200}
```

With `preparation="headless"`, `timeout_seconds` must be at least 960 seconds to cover the bounded installation and readiness budget. This conditional requirement does not raise the general 30-second minimum for Native or preinstalled recipes; the selected E2B backend can impose a higher lifetime minimum.

Creation records the target and installer revision before the bounded guest job. Reentry follows the same target and terminal result; a confirmed failed attempt needs explicit retry. Attachment only checks readiness. Native pause/resume retains memory and files.

### E2B desktop preparation

In Console, choose the E2B connection and enable **Desktop environment**. **Automatic desktop** prepares a private template on first creation and reuses it later. The key must permit both template builds and sandboxes. First creation takes longer; the stock E2B `desktop` template alone lacks Envd.

For explicit recipes, choose `execution="envd_http"` and `desktop=true`. Automatic desktop enables `desktop_assistance=true`; enable it yourself for a custom template when human viewing/control is needed. Configure Agent Computer tools and mount permissions separately.

For Service automatic preparation, this complete recipe enables the desktop and human access:

```json
{"template":"auto","execution":"envd_http","desktop":true,"desktop_assistance":true}
```

Automatic builds pin the base image, Envd release, and startup checksums. Reuse is scoped to workspace, connection, backend, credential version, and manifest. Credential/manifest changes prepare new templates for new instances; existing instances keep their selection. Failed builds retain diagnostic identity and require ownership checks before cleanup. Successful templates remain reusable account assets.

To maintain your own template, choose **Custom template**. Prepare `E2B_API_KEY` and a compatible Linux amd64 Envd binary, then run the [template builder](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/desktop_template.py) from the repository root:

```bash
cd examples/environment-provider
uv sync --locked
uv run python desktop_template.py my-envd-desktop --binary /absolute/path/to/linux-amd64/a13n-envd
```

Enter the resulting template name or ID in Console. The builder includes the shared desktop launcher, Noto CJK fonts, Chromium, and access supervisor. On creation, Service starts the components, generates a private per-sandbox credential, and connects through E2B's HTTPS port route. File, shell, and Computer operations use EIP at `/workspace`; Native account/root/helper settings apply only to Native execution. Opening an existing target checks readiness without repairing or restarting components.

The supervisor enforces human grants and revocation; Service coordinates safe pause/resume. Envd owns screenshots, coordinates, and input. Linux X11 supports physical keys but rejects literal Unicode text; it has no E2B Desktop SDK fallback. See [Envd Computer use](../a13n-envd/computer-use.md).

### Daytona

For automatic headless or desktop Envd in Service / Console:

1. Set the organization's default region in the Daytona dashboard. A connection's region does not replace that account setup.
2. Add a key with Sandbox operations and Snapshot read/write/delete permissions. `organization_id` is an optional constraint; the default target region is `us`.
3. Keep `snapshot="auto"` and Desktop off for headless Envd, or enable Desktop for Computer and human access. Create an environment; saving a template alone starts no build.
4. Follow the displayed build stages. You can leave the page and return to Environment instances; after a confirmed failure, fix its cause before retrying.

First creation builds a private snapshot for the selected headless or desktop profile; later creations reuse its recorded ID. Headless snapshots omit Python, X11 and browser dependencies. Automatic snapshots use 2 CPUs, 4 GiB memory, and 10 GiB disk. Reuse is scoped to workspace, Provider, backend, credential version, and manifest, with ownership/identity/content revalidation. Uncertain submissions are reconciled before another build. Validated failed builds are cleaned up; ready snapshots survive instance stop/deletion.

For custom preinstalled snapshots or runtime-only keys, prepare a Linux amd64 binary and a private `DAYTONA_API_KEY` with snapshot authority. Run from the repository root:

```bash
uv run --with daytona==0.225.0 python examples/environment-provider/daytona_desktop_snapshot.py my-envd --region us --binary /absolute/path/to/linux-amd64/a13n-envd
```

Use `{"snapshot": "my-envd"}` for Files & Shell, or add `"desktop": true, "desktop_assistance": true` for Computer and human access. Custom-snapshot runtime keys need no build permission. Snapshot boot starts Envd and the enabled desktop/access components; Service verifies EIP readiness after create/start. Attachment and access grants perform no installation or repair.

Agent operations use EIP on private port 8888 at `/workspace`. Daytona proxy authentication and guest Envd credentials remain separate and in memory; the Host validates the discovered endpoint. There is no Native fallback. Enable Computer tools and mount permissions separately. CJK fonts are included, but X11 accepts physical keys rather than literal Chinese or multiline text.

Human access uses a short-lived signed preview route on port 6081. The supervisor enforces read-only viewing, one controller, renewable grants, and disconnect on return. Revoking a signed URL alone does not prove that existing input connections have ended. Service waits for a safe execution boundary before handoff and requires re-observation after return.

Stop/start preserves files but recreates desktop processes and invalidates old observations. Stop requires confirmed disabled auto-delete; deletion waits for terminal destruction. `test_daytona_desktop_live.py` covers EIP/desktop/stop-start, and `test_daytona_access_live.py` covers viewing, revocation, expiry, and restart fencing. These billable-target tests are separate from full Console/Agent handoff acceptance.

### Modal

For automatic Envd preparation in Service:

1. Supply the Modal Token ID and Token Secret. Credentials choose the account; `workspace` is an optional label.
2. Leave `image` unset or use `auto`. Keep `desktop=false` for headless Files & Shell, or enable Desktop for Computer tools. Empty connection settings choose App `a13n-sandboxes` in environment `main`, creating the App on first preparation if needed.
3. Create the environment to start preparation. Follow build stages and elapsed time; leaving Console does not stop the build.
4. Use the ready environment, or correct a confirmed failure before retrying. Desktop profiles also provide a preview.

The first creation builds from pinned public artifacts; later creations reuse the immutable image ID for the selected profile. Headless images omit desktop, browser and guest Python dependencies. Automatic desktop defaults are 2 CPUs, 4096 MiB, 3600 seconds, and human assistance enabled, with explicit overrides retained. Build records are scoped to workspace, Provider, credential version, and manifest. Reuse checks current image access; interrupted waits rejoin the same content-addressed build without temporary sandboxes. Images and the App remain account assets.

Modal and Daytona automatic preparation optionally accept `base_image`, a digest-pinned GNU/Linux x86_64 apt-based registry image with glibc >= 2.39, a root build user and unused `sandbox` UID/GID 1000. The digest is part of artifact reuse; incompatible userspace fails before package mutation. Managed builds replace its entrypoint. Explicit custom preinstalled image/snapshot selectors do not accept `base_image`.

Custom images require an explicit compatible `image` and existing App. Authenticate with `modal token new`, prepare a Linux amd64 Envd binary, and run the optional builder from the repository root:

```sh
uv run --locked python examples/environment-provider/modal_desktop_image.py \
  --app a13n-sandbox-dev --binary /path/to/linux-amd64/a13n-envd
```

The builder excludes runtime credentials. Put the resulting image ID in your recipe:

```json
{
  "image": "im-yourImageId",
  "envd_management": "preinstalled",
  "startup_command": ["a13n-sandbox-entrypoint", "a13n-sandbox-desktop", "a13n-envd", "supervise"],
  "desktop": true,
  "desktop_assistance": true,
  "cpu": 2,
  "memory": 4096
}
```

Envd runs at `/workspace` as UID/GID 1000. `desktop` requires an observable, input-ready desktop on create/resume; Service's `desktop_assistance` also requires the access supervisor. Separate TLS tunnels carry EIP on 8888 and human viewing/control on 6081. Browsers receive neither Modal nor Envd credentials. Configure Agent Computer tools and mount permissions separately.

The process-local client cache holds at most 32 entries, expires idle entries after 120 seconds, and retires all entries after 10 minutes. Every request still checks current ownership, liveness, desktop generation/epoch, credential version, and endpoint policy. Success releases the exclusive SDK borrow; Envd releases it before Agent execution. Errors, cancellation, lifecycle changes, and shutdown retire affected transports without replaying commands. Separate control/worker processes warm independent caches.

Managed stop snapshots files before terminating. Explicit start restores files and starts fresh desktop processes; successful readiness allows snapshot cleanup. Old observations and human grants become invalid. Running TTL stays within the known deadline, at most 24 hours; attachment neither resumes nor renews.

For live validation, set `A13N_TEST_MODAL_DESKTOP=1`, `A13N_TEST_MODAL_IMAGE`, `A13N_TEST_MODAL_BACKEND_JSON`, and `A13N_TEST_MODAL_CREDENTIAL_JSON`. `test_modal_desktop_live.py` covers EIP/desktop/resume; `test_modal_access_live.py` covers authorization/revocation/expiry. `test_modal_takeover_live.py` uses a scripted model and seeded ready lifecycle receipt to check Service HTTP admission, safe pause, real RFB control, revocation, and successor observation. Console browser UI needs separate acceptance. These tests allocate billable targets and attempt cleanup.

### Vercel Sandbox

Keep `execution="native"` for Native Files & Shell. Select Envd HTTP with an omitted snapshot or `snapshot="auto"` to install directly on the final persistent Ubuntu target. Desktop off selects headless Envd; Desktop on adds Computer and human access. Automatic lifetime defaults to 2700 seconds. Console reports durable stages while creation continues after navigation.

```json
{"execution":"envd_http","snapshot":"auto","desktop":false,"timeout_seconds":2700}
```

Installation rejoins the recorded target and frozen job after interruption; it creates no temporary builder or preparation snapshot. Stop/resume retains files and recreates processes. A custom snapshot remains supported as a preinstalled contract. To build one yourself:

Securely configure `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, and `VERCEL_PROJECT_ID`. From the repository root, build with a compatible Linux amd64 Envd binary:

```sh
uv run --locked python examples/environment-provider/vercel_desktop_snapshot.py --binary /path/to/linux-amd64/a13n-envd
```

On slow upload links, replace `--binary` with an official compatible `--archive-url` and its verified `--archive-sha256` from `SHA256SUMS`. The sandbox verifies the download and extracts only Envd.

The builder allocates a temporary billable sandbox, installs desktop dependencies and Chrome, and uploads the shared launchers. It retains a snapshot without live daemons or guest credentials, then deletes the build sandbox. Use the returned snapshot ID:

```json
{
  "execution": "envd_http",
  "snapshot": "snap_yourSnapshot",
  "envd_management": "preinstalled",
  "startup_command": ["sh", "-c", "umask 077; install -d -m 0700 /var/lib/a13n-envd; cd /workspace; rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /run/a13n-desktop-access/control.sock; nohup a13n-sandbox-entrypoint a13n-sandbox-desktop a13n-envd supervise >> /var/lib/a13n-envd/startup.log 2>&1 < /dev/null &"],
  "desktop": true,
  "desktop_assistance": true,
  "vcpus": 2,
  "timeout_seconds": 1800
}
```

Envd uses `/workspace` as UID/GID 1001; Native execution uses `/vercel/sandbox`. For preinstalled snapshots, `startup_command` is required: it starts the runtime in the background and returns. Creation and native session resume execute this operator-authored argv as root with the instance bootstrap environment through a frozen job per native session, then verify EIP/Computer readiness. Lost replies rejoin that job instead of replaying startup; a confirmed failed job needs an explicit new lifecycle operation to retry. Snapshots preserve files, not processes. Attachment and access do not install, repair, or resume. Port 8888 carries authenticated EIP; 6081 carries the revocable viewer. Browsers receive short-lived supervisor credentials, not Vercel or guest tokens. Service checks ownership, native identity, current session, and endpoint policy. Stop preserves files; resume recreates desktop processes and invalidates old observations/grants.

### Fly.io Sprites

Native execution uses `/home/sprite` and guest-PATH `python3` for helpers. Explicit `execution="envd_http"` with Desktop off installs headless Envd on the final Sprite without Python or display dependencies. The Sprites native service owns daemon restart, and each fresh EIP attachment checks the recorded runtime. The [Sprites environment guide](https://docs.sprites.dev/working-with-sprites/) describes its writable home and tools. Native sleep preserves disk and execution wakes the Sprite. Schedule no explicit stop; destruction waits for confirmed absence.

For Computer and human control, enable Desktop and let Service [install a desktop on each new Sprite](../a13n-service/computer-use.md#automatic-sprites-desktops), then attach with `execution="envd_http"`. Native execution remains available.

### Runloop

Use `/home/user` as the default root and guest-PATH `python3`. See Runloop's [default unprivileged user](https://docs.runloop.ai/docs/devboxes/configuration/user-parameters) and [Python environment](https://docs.runloop.ai/docs/devboxes/overview). Keepalive uses the returned idle interval; shutdown completion is checked before state is cleared. Use this Provider for Native Files & Shell; historical standalone Envd probes do not establish Provider or Console integration.

### boxd

Use `/home/boxd` as the default root and guest-PATH `python3`. Each Environment gets one [boxd](https://boxd.sh?ref=agent-foundation) machine. The machine is created isolated: it never joins the organization's private network. `size` selects vCPU and memory (`1x4`, `2x8`, `4x16`, or `8x32`); omit it to use the organization default or the snapshot's size. `from_snapshot` starts the machine from a prepared boxd snapshot. `stop()` hibernates the machine with its memory and processes; `start()` wakes it. Destruction deletes the exact recorded machine and waits for confirmed absence. Install the SDK with the `boxd` extra, `a13n-harness[boxd]`. Create an API key with `boxd auth keys create <name>`; a key acts in one organization.

### Embedded configuration

To attach from your own Host, first obtain `saved_state` from management and `api_key` from your credential store. This asynchronous integration fragment selects the built-in Daytona definition and reads a bounded directory listing:

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(DAYTONA,) = select_builtin_environment_providers(("daytona",))
connector = DAYTONA.execution_connector(
    {"snapshot": "my-envd"}, configuration={"organization_id": "your-organization"},
    credential={"api_key": api_key}, environment_id="env-workspace", state=saved_state,
)
async with await connector.open() as execution:
    assert execution.operations.files is not None
    listing = await execution.operations.files.list("/", max_results=20)
```

`execution_connector()` validates inputs without acquiring clients. `open()` acquires execution resources. Close borrowed runtimes at Host shutdown; management and execution clients have separate lifetimes.

### Cloud validation

Start with deterministic adapter tests for catalog construction, native HTTP responses, Sprites WebSocket frames, and Modal's async SDK against local gRPC fixtures. These exercise file/shell helpers without qualifying a deployed cloud account.

For a live Daytona check, securely supply `A13N_TEST_DAYTONA_BACKEND_JSON` and `A13N_TEST_DAYTONA_CREDENTIAL_JSON`, then run from the repository root:

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

Modal, Vercel, Sprites, Runloop, and boxd use their corresponding uppercase prefixes. Override the recipe with `A13N_TEST_<PROVIDER>_RECIPE_JSON` when needed. The fixture creates billable targets and attempts deletion even after failure. Keep credential JSON private. Missing opt-in or credentials causes a skip, not a live pass.

Native API references: [E2B](https://e2b.dev/docs), [Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/), [Modal snapshots](https://modal.com/docs/guide/sandbox-snapshots), [Vercel Sandbox SDK](https://github.com/vercel/sandbox), [Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/), [Runloop async Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html), and [boxd Python SDK](https://docs.boxd.sh/reference/python-sdk?ref=agent-foundation).
