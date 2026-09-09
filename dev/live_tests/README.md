# Local live tests

These opt-in tests send real HTTP requests to separate local Control and Worker
processes. Control accepts Native API requests; Worker readiness is checked over
HTTP, and execution is dispatched through the real queue. The tests never call
Worker execution internals or use an in-process ASGI transport for live journeys.

| File                           | Journey                                                                                                        |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `test_01_basic_run.py`         | Acceptance, execution, result, attempts, usage and retained items                                              |
| `test_02_continuation.py`      | Successor Run reconstructs conversation context                                                                |
| `test_03_tools_environment.py` | Real local shell writes and reads a file                                                                       |
| `test_04_stream_reconnect.py`  | Disconnect during execution and resume with Last-Event-ID                                                      |
| `test_05_idempotency.py`       | Concurrent duplicate submission and changed-intent conflict                                                    |
| `test_06_steer.py`             | Idempotent mid-tool input has one durable inbox row and one occurrence in the actual model request; no new Run |
| `test_07_interrupt.py`         | Interrupt model I/O and tool execution; verify teardown                                                        |
| `test_08_approval.py`          | Approve/reject pending work through a successor Run                                                            |

The first round has ten live cases because interrupt and approval each have two variants.
The deterministic OpenAI-compatible model fixture controls timing and expected
answers. This tests Foundation orchestration and real tool execution, not external
model quality or provider compatibility. The approval tool comes from a trusted
plugin loaded into the Worker at startup.

## Local state

`.state/` and `providers.local.toml` are local-only paths excluded by the root
`.gitignore`. They contain generated test state and optional credentials, and must
remain untracked. Existing local files stay on disk for test reuse and diagnosis.
Never force-add these paths. Store custom Provider
configuration selected with `LIVE_TEST_PROVIDERS_CONFIG` under `.state/` or outside
the repository.

Before submitting changes, `git ls-files -- dev/live_tests/.state dev/live_tests/providers.local.toml` must return no paths. Only the blank example
configuration belongs in Git; each developer creates their own local copy.

## Installed test plugins

The explicit live-test Worker host builds one immutable factory catalog from
`approval_plugin.py` and `resilience_plugin.py` before serving. It supplies this
catalog through the Service's trusted `Components.plugin_factory_catalog`
composition boundary. Control does not import these factories. This uses plugin
code already present in the checkout; setup never builds, uploads, or installs
code through HTTP. Restart the Worker after changing a fixture plugin.

Agent configuration selects `live.approval` or `live.resilience` using
`instance_name`, `plugin_key`, and `config`. Control stores the authored selection;
the Worker validates and normalizes it before execution. A missing factory or
invalid configuration fails the Run before model or tool effects. See the
[installed plugin contract](../../spec/a13n-service/36-installed-harness-plugins.md).

## Optional real Provider configuration

Create `dev/live_tests/providers.local.toml` from the committed blank example:

```sh
cp -n dev/live_tests/providers.example.toml dev/live_tests/providers.local.toml
chmod 600 dev/live_tests/providers.local.toml
```

The local file is gitignored. Every section is optional and independent. A missing
default file or an entirely blank section leaves the current deterministic tests
unchanged and skips that section's additional integration journey. Fill only the
sections you want to exercise. A partially filled, invalid or unsupported section
fails instead of silently falling back. `LIVE_TEST_PROVIDERS_CONFIG` can select a
different TOML file; an explicitly selected missing file is an error. Keep custom
files outside Git too. Keys are never taken implicitly from application `.env`
settings, existing Provider records or previous test state.

If a trusted local proxy resolves a configured model hostname to a private or
reserved address, explicitly set `LIVE_TEST_MODEL_PRIVATE_ENDPOINT_DOMAINS` to a
JSON array of those operator-approved domains, such as `["openrouter.ai"]`.
This uses the normal Service endpoint allowlist only in disposable lab processes;
the default is empty and HTTPS validation remains enabled. Real Provider journeys
allow 90 seconds per Control HTTP request for cloud catalog discovery; local
deterministic journeys retain their shorter timeout.

| Parameter                   | Meaning when enabled                                                   | Empty/default behavior                                                                                             |
| --------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `environment.type`          | `a13n.e2b`, the native E2B Environment implementation                  | No additional cloud Environment test; existing direct-local and explicit Docker cases keep their current providers |
| `environment.api_key`       | E2B account API key; required with `type`                              | No credentials required by the existing local cases                                                                |
| `environment.template`      | Optional E2B template ID or alias                                      | `base` when E2B is enabled                                                                                         |
| `connector.provider`        | `composio` or `openconnector`                                          | No additional external Connector test; case 27 keeps its local TLS Composio fixture and local MCP server           |
| `connector.api_key`         | Composio project API key; required with `provider`                     | Existing connectivity fixtures use a generated lab-only credential                                                 |
| `connector.toolkits`        | Optional nonempty list of Composio toolkit keys to discover            | `["github"]` when Composio is enabled; leave the example line commented when the section is disabled               |
| `connector.project_api_key` | OOMOL Project API key; required for `openconnector`                    | No default; create under Console → Projects → your project → API Keys                                              |
| `connector.catalog_api_key` | OOMOL personal API key for catalog reads; required for `openconnector` | No default; create at <https://console.oomol.com/api-key>                                                          |
| `connector.services`        | Optional nonempty list of OpenConnector service keys                   | `["slack"]` when OpenConnector is enabled                                                                          |
| `model.provider`            | `openrouter` or `openai_compatible`                                    | No additional external Model test; existing cases keep their scripted local model                                  |
| `model.api_key`             | Model provider API key; required with `provider`                       | Existing scripted model uses a generated lab-only credential                                                       |
| `model.model`               | Required upstream model ID supporting Chat Completions                 | No implicit model selection; choose a model available to your account                                              |
| `model.base_url`            | Required HTTPS API base URL for `openai_compatible`                    | Leave blank for `openrouter`, which uses the service's built-in endpoint                                           |

OpenRouter uses the native `openrouter` Provider and `openrouter.chat_completions`
API. Its endpoint is `https://openrouter.ai/api/v1`, as described in the
[OpenRouter quickstart](https://openrouter.ai/docs/quickstart). A custom compatible
endpoint uses `openai_compatible` and `openai.chat_completions`; URLs must not
contain credentials, query strings or fragments.

```sh
# Run only the three additional Provider journeys (blank sections skip):
make live-test-providers
# Run management cases plus the configured Provider journeys:
make live-test-management
# Select just one configured Provider:
make live-test-providers LIVE_TEST_ARGS='-k configured_model'
```

`test_31_real_providers.py` is also collected by `make live-test` and
`make live-test-round-two`. `make live-test-local` runs the first-round files only;
run `make live-test-providers` alongside it for external integration coverage.
Existing timing, fault injection and management assertions always retain their
deterministic dependencies, even when all three sections are configured.
`make live-test-check` never reads this private file or contacts these providers.

Each enabled section starts its own disposable PostgreSQL, Redis, object-storage
bucket, Control and Worker lab. Initialization creates the Provider and associated
resources through Control HTTP, persisting credentials encrypted in that lab's
database. No external credentials are copied into retained lab configuration.
The Environment journey executes a real Shell write and file read in E2B, using
the scripted model to control tool selection. The Connector journey performs real
authentication and catalog discovery. An API key alone does not authorize a user's
OAuth accounts: this journey creates no account connection and executes no business
tools unless the separate Slack OAuth journey is explicitly enabled below.
The Model journey executes a real Run using the configured upstream model,
with a short prompt and a 128-token output limit. External usage can consume credits.

On success, failure, partial provisioning or normal cancellation, cleanup interrupts
owned Runs and deletes owned remote Environments while the Worker is still running.
Cleanup failures fail the test and identify the Environment ID. E2B sandboxes also
have a five-minute timeout as a bound if the test process is forcibly killed.
The lab then removes its containers and database, deleting all Provider rows,
encrypted keys, templates, models and Agents created there. It never deletes or
rotates existing account credentials or touches an existing installation's data.
The local TOML remains available for later runs; ignored private process logs and
test evidence remain under `.state/management/<random-id>/`.

### OpenConnector and interactive Slack authorization

The built-in `openconnector` adapter uses OOMOL's hosted Project API at
`https://connector.oomol.com`; no OpenConnector deployment is needed. In
[OOMOL Console](https://console.oomol.com), create a test Project and a Slack
Provider config using OAuth2 and **System Client**. Create a Project API key in
that Project and obtain a personal API key from the separate
[API Keys page](https://console.oomol.com/api-key). `catalog_api_key` is our local
field name for that personal key, not a separate OOMOL key type. See the
[OOMOL SaaS setup guide](https://oomol.com/en/docs/connector-saas/).

Use the following `[connector]` section in a private TOML file. Omit the Composio
`api_key` and `toolkits` fields when selecting OpenConnector:

```toml
[connector]
provider = "openconnector"
project_api_key = "<OOMOL Project API key>"
catalog_api_key = "<OOMOL personal API key>"
services = ["slack"]
```

For example, save it as `dev/live_tests/.state/openconnector.toml` with mode `0600`
to preserve an existing Composio configuration. Then run:

```sh
# Noninteractive: verify catalog credentials and discover configured services.
LIVE_TEST_PROVIDERS_CONFIG=dev/live_tests/.state/openconnector.toml \
  make live-test-providers LIVE_TEST_ARGS='-k configured_connector'

# Interactive: authorize a new Slack account binding and execute a read-only tool.
LIVE_TEST_PROVIDERS_CONFIG=dev/live_tests/.state/openconnector.toml \
  make live-test-providers LIVE_TEST_ARGS='--live-slack -k openconnector_slack'
```

The interactive journey creates a fresh Workspace ConnectorConnection through
Control. Its log identifies a mode-`0600` `slack-authorization.json` file inside the
private lab directory. Open its `redirect_url` in a browser and authorize the test
Slack workspace within ten minutes. Control polls OOMOL and verifies the exact
account before publishing readiness; this flow needs no public Service callback
origin. The temporary authorization file is removed when waiting ends.
Review the actual Slack consent screen: OOMOL's System Client can request read
and write permissions even though this test executes only a read-only action.
Use a dedicated test workspace with permission to install the app.

The scripted model then calls only `slack.list_channels` with `limit=1`, through a
real Agent Run and Worker. Assertions check readiness, tool selection, a successful
provider outcome, and the returned channel schema. Logs report connection/Run IDs
and the channel count, without printing keys or channel contents. A completed Run
with a failed or unknown tool outcome does not pass. The catalog-only check proves
only `catalog_read`; the Project key is exercised by this OAuth/execution journey.

Each interactive run uses a fresh isolated Workspace and therefore requires a new
authorization. Ordinary live-test targets never start Slack OAuth without
`--live-slack`. OOMOL's published Project API has no account-revoke operation:
local lab teardown does not remove remote test accounts. Manage those in the
test Project's **Connected accounts** page after testing.

## Disposable local setup

With Docker running, execute the first round without preparing `.env` or starting
service processes manually:

```sh
make live-test-local
# Run cases 3 and 4, in file order:
make live-test-local LIVE_TEST_ARGS='-k "environment_tool or stream_disconnect"'
```

This entry point creates fresh PostgreSQL, Redis and RustFS containers, applies
committed migrations, provisions the first-round resources through Control HTTP,
and starts separate Control and Worker processes on new loopback ports. It does
not use an existing installation's database, credentials or service listeners.
Test subprocesses explicitly bypass proxies for loopback, including macOS system
proxies, so interrupt checks observe the Worker's actual model connection.
RustFS uses the digest pinned in `local_storage.py`; the first invocation may need
to download images. Startup waits for the authenticated S3 API, then runs the
Service's unchanged conditional-write/delete and concurrent-write probes.

The Environment uses the `default` Shell profile expected by Harness, with
`/bin/sh` and no extra `-c` argument. Only first-round resources are provisioned;
fault relays and the second identity are omitted. On completion, failure or
interruption, the runner cleans up its own services and containers. Private
configuration, process/test logs, file evidence and `results.json` remain under
ignored `.state/core/<random-id>/`. These configuration files describe disposable
resources; rerun the command to create a new installation instead of reusing them.

## Manual setup with existing dependencies

Run commands from the repository root after the normal development setup. Configure
`.env` with the actual PostgreSQL and Redis endpoints and apply committed migrations
using `make db-upgrade`. Check Docker's published PostgreSQL port if the configured
port is not reachable.

Separate processes require the same compatible S3 bucket. Configure
`A13N_SERVICE_OBJECT_BACKEND=s3`, `A13N_SERVICE_OBJECT_BUCKET`,
`A13N_SERVICE_OBJECT_ENDPOINT_URL`, `A13N_SERVICE_OBJECT_REGION`, and, if required,
`A13N_SERVICE_OBJECT_FORCE_PATH_STYLE=true`, plus the backend's AWS credentials.
The endpoint must pass the service's conditional-write/delete compatibility probe.
The default local object backend is not supported for these separate processes.

```sh
make live-test-init
# Terminal 1
make live-test-control
# Terminal 2
make live-test-worker
# Terminal 3, after both roles are ready
make live-test-setup
make live-test
```

The initializer creates a dedicated Organization, Workspace, User and admin role
bindings in the configured database. Re-running it retains the same identity.
Setup creates the Model Provider, Model, Environment Provider, Environment and
Agents through Control HTTP. It records each successful creation for reuse.
The approval Agent selects the Worker's installed `live.approval` factory.

The explicit test host installs a private bearer authenticator and the fixture
routes on Control. It binds to loopback and does not modify production startup.
Default origins are `http://127.0.0.1:18000` and `http://127.0.0.1:18001`;
`LIVE_TEST_CONTROL_URL` and `LIVE_TEST_WORKER_URL` override them. Set overrides
consistently for both processes, setup and tests before provisioning resources.
Control and Worker must run on this machine because the direct-local Environment
and fixture evidence use the same absolute workspace path.

Credentials, resource IDs and test files live in ignored `.state/` under this
directory. The configuration file is private (mode 0600). Keep it to reuse setup;
do not publish it. Tests retain settled Runs and evidence for diagnosis and
interrupt only their own active Runs during cleanup. Stop the two test processes
with Ctrl-C when finished. No automatic database/resource deletion is performed.

## Selection and validation

```sh
make live-test LIVE_TEST_ARGS='-k basic'
make live-test LIVE_TEST_ARGS='-k interrupt'
make live-test-check
```

Use `LIVE_TEST_ARGS='-k interrupt'` to select interrupt cases. Without `--live`,
the ten live cases skip and do not contact services. `live-test-check` checks
formatting, lint and offline support tests; it does not prove the live journeys pass.

To authenticate an existing local Control installation, stop its current Control
process and run `make live-test-auth-control`. This uses the ordinary `.env`
settings and the configured Control port, adding the private test bearer
identity and `/__live__` fixture routes. It does not change the Worker's queue, storage or encryption settings,
and requires the model endpoint loopback allowlist below. The regular service executable
remains unchanged. Missing, incorrect or duplicate bearer credentials are rejected.
Plugin journeys require the explicit live-test Worker host described above.

Waiting Run responses expose `sealed_state_digest_sha256`; case 8 passes this
public value to Native feedback without reading private state from the database.

For the local model fixture, set `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS='["127.0.0.1/32"]'` in `.env` and restart both roles so Worker model calls can reach the loopback fixture.

## Second round: persistence, concurrency and faults

| File                             | Variants and acceptance evidence                                                                                                                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_09_failures.py`            | Model authentication failure, model read timeout, and exhausted tool retries: bounded failed Run, diagnostic failure, no tool effect, released capacity                                          |
| `test_10_worker_recovery.py`     | SIGKILL recovery and suspended stale owner: same Run/input/revision, increasing Attempt number, replacement start reason, checkpointed effect retained once, immutable replaced Attempt and seal |
| `test_11_graceful_shutdown.py`   | SIGTERM: clean process exit, no new claims, ordinary completion or planned handoff, replacement Worker drains pending work                                                                       |
| `test_12_worker_competition.py`  | Two one-slot Workers and four slow Runs: bounded admission, one successful Attempt per Run, eventual completion                                                                                  |
| `test_13_queue_retry_fork.py`    | Busy-Thread FIFO consumption, explicit Retry after repairing the upstream, Fork retaining context in a separate Thread; original history remains immutable                                       |
| `test_14_async_subagents.py`     | Two durable children; independently gated arrival at accepted/running/completed/waiting (approval and client tool)/failed/cancelled parents; durable consumption or suppression                  |
| `test_15_dependency_outages.py`  | Worker-only PostgreSQL, Redis and object-store connection cuts: prove the cut was exercised, restore and restart, validate terminal state and continued service availability                     |
| `test_16_workspace_isolation.py` | Another User in the same Organization and a different Workspace: valid own access, denied reads/stream/control, no unauthorized state changes                                                    |

Case 14 fixes the destination status before releasing either child's model response.
The accepted destination is an explicit continuation of the completed spawning Run;
draining child Workers finish their existing work without claiming that continuation.
Waiting cases retain pending actions and child inbox entries until explicit feedback,
then check that the successor's first model request contains no child results.
Failed and cancelled spawning Runs suppress results while their independent children
finish normally. Assertions read real HTTP model observations and an authenticated,
read-only test inbox endpoint backed by the actual PostgreSQL records. The test never
writes database state to manufacture a Run status. These cases cover all six Run
statuses; they do not enumerate every selected-head, queue, crash, or child-outcome
combination in the async-subagent contract.

These are 21 additional live variants in eight files. They use the real Control,
Worker and embedded Harness, with HTTP model responses and an installed test tool
plugin controlling errors and checkpoint timing. The plugin records non-idempotent
file effects so duplicate execution is visible. It does not install Environment
tools or replace the core round's Environment integration checks. The recovery
assertion concerns a **completed checkpoint**; effects that happened before a
crash without a completed checkpoint can be replayed and require tool-owned
idempotency in applications.

Run this round separately:

```sh
make live-test-round-two
make live-test-round-two LIVE_TEST_ARGS='-k worker_replacement'
make live-test-round-two LIVE_TEST_ARGS='-k workspace'
```

Only `--live-round-two` enables these tests. `make live-test` continues to run the
first round. Neither default collection nor `make live-test-check` starts
Foundation, containers, or dependency faults; support tests may open short-lived
loopback echo sockets to validate the fault relay itself.

Docker is required. Without `A13N_SERVICE_OBJECT_ENDPOINT_URL`, each lab starts
the same pinned, compatible RustFS container used by `live-test-local`. To use an
existing loopback S3 server, configure that endpoint, `A13N_SERVICE_OBJECT_REGION`
and its AWS credentials in `.env`. The endpoint must pass the Service's storage
compatibility probe; an arbitrary MinIO version is not sufficient. The test
creates and deletes its own random bucket, never the bucket named by
`A13N_SERVICE_OBJECT_BUCKET`. An explicitly configured but incompatible endpoint
fails setup rather than silently switching storage or skipping enabled cases.
Run only one selection at a time; these journeys do not use pytest-xdist.

Each test owns new PostgreSQL and Redis containers, fresh migrated schemas,
identities, loopback ports and service subprocesses. It never discovers or kills
an existing Worker PID, never resets a developer database and never stops a
shared dependency. Network faults close only connections through a fixture-owned
TCP listener used by that test's Workers; Control keeps its independent healthy
connection for observation. Cleanup restores proxies and terminates only owned
process groups before removing owned containers and the owned bucket. Child
cancellation is tested against the currently exposed default `independent`
policy; this suite does not fabricate a private cancellation-policy selection.

Private configurations, process logs and model/tool evidence are retained under
`.state/round-two/<random-id>/`; their database and bucket are disposable. Logs
identify accepted Run/Thread IDs and failed assertions retain API observations.
An explicit `LIVE_TEST_CONFIG` path lets child processes share their lab's
private configuration without changing the existing first-round installation.

Static/support checks establish fixture correctness and collection only. Report
live results separately; neither collected cases nor skipped cases prove that
Foundation passes the corresponding contract.

## Management integration: Service configuration to Harness execution

This round adds 36 live variants in 11 independently selectable files. Cases 19,
28, 29 and 30 are intentionally excluded. All management resources are created
through public HTTP APIs, and each enabled test uses its own isolated lab with
the same automatic RustFS setup and optional loopback S3 override as round two.

| File                                 | Acceptance evidence                                                                                                                                                                                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_17_agent_revisions.py`         | Update after Run acceptance but before Worker claim; current and explicit old revisions reach the model; per-Run overrides do not alter stored revisions; stale writes are rejected                                                                          |
| `test_18_plugin_execution.py`        | Installed factory selections produce a real file effect; an unbound Agent has no plugin tool; missing factories and invalid configuration fail Worker preparation before model/tool execution                                                                |
| `test_20_environment_templates.py`   | Explicit old/current recipes read different files; `on_run` versus `on_use` is observed before tool execution; simultaneous first reads use one logical target/generation; unused lazy selection stays unprepared; explicit null overrides the Agent default |
| `test_21_environment_lifecycle.py`   | Stop/reopen preserves backing generation; delete/rebuild advances it; stale process handles fail; Continue, Retry, Fork and Feedback inherit correctly; changing the Thread default does not change a historical Fork source                                 |
| `test_22_environment_access.py`      | No environment, read-only, read-write and full access have the expected file/Shell surface; actual read/write/Shell bytes agree; forged unadvertised writes produce no file                                                                                  |
| `test_23_model_updates.py`           | Accepted Model settings/upstream remain frozen; a new Run receives new settings; the next request of a running Agent uses rotated Provider credentials/new endpoint or is denied after disable                                                               |
| `test_24_skill_execution.py`         | Real ZIP document and attachment uploads; accepted latest version freezes before update; later current and explicit pinned bindings materialize/read correct files; cross-Workspace package reads and absent Environment are denied                          |
| `test_25_asset_execution.py`         | File bytes materialize into the Environment and PNG bytes enter the model request unchanged; deletion before Worker claim blocks delivery; explicit publication produces an immutable downloadable Asset linked to its Run, with cross-Workspace denial      |
| `test_26_output_and_client_tools.py` | Structured output advertises the authored schema and enforces JSON Schema validation and native retry bounds; external client tool waits and resumes with actual supplied data; malformed, duplicate and stale feedback cannot create extra successors       |
| `test_27_connectivity_execution.py`  | Production MCP client and Composio adapter connect to local HTTP peers; selected tools, arguments and credential hashes agree; disabling/revoking the connection prevents later dispatch                                                                     |
| `test_31_observability.py`           | Actual OTLP/HTTP exports correlate Service Attempt, Harness, model and tool spans; durable Items, SSE and model usage agree; rejecting trace exports with HTTP 503 does not change the result or repeat the effect                                           |

```sh
make image-sandbox
make live-test-management
make live-test-management LIVE_TEST_ARGS='-k revision'
make live-test-management LIVE_TEST_ARGS='-k asset'
make live-test-check
```

The two backing-lifecycle cases in case 21 use the Docker Provider and require
the locally built `a13n-sandbox:local` image. Set `LIVE_TEST_SANDBOX_IMAGE` to use
another locally built image; these cases never pull an implicit remote image.
Their workspace is a fixture-owned bind directory, so its proof file survives
backing-container deletion. This does not assert recovery of deleted ephemeral
container files. Cleanup stops the lab's Workers and removes only containers
labelled with that test's exact Environment ID. Other management cases use
Direct Local, which does not support backing stop or deletion.

Use `--live-management` only for this round. The other entry points retain
their own opt-ins. The lab also retains private model request observations,
Composio/MCP dispatch evidence and OTLP exports beneath
`.state/management/<random-id>/`. Ambient OpenTelemetry destinations are removed
from lab subprocess environments; case 31 enables only its local receiver.
Composio requires HTTPS, so the lab starts an owned TLS peer and appends its
one-day certificate to a private CA bundle used only by lab subprocesses. It does
not install a system trust root or disable certificate verification.

The deterministic model chooses tools and returns **observed tool results**.
Expected Skill/file bytes never substitute for a missing tool result. MCP and
Composio peers replace external services, while the Service adapters, management,
authorization, queue, storage, Worker and Harness remain real. Support checks
exercise the Composio peer through the production adapter and validate MCP and
OTLP wire bodies without starting Service processes.

Missing product integration is an assertion failure when live tests are enabled,
not an automatic skip or a fixture-installed capability. Environment/Skill
file-tool and output-publication journeys exercise the Service's reconstructed
`DynamicEnvironmentCapability` and `publish_asset` tool. Their availability in
source does not establish a passing end-to-end result; run the enabled journeys
against the configured lab to validate the complete path.

These tests do not exercise external model inference quality, hosted OAuth user
interaction, Runner activation, plugin rollout compatibility or instance isolation, Secret
bindings or IAM grant revocation. The client-tool stale-feedback check concerns
a superseded sealed state, not a fabricated wall-clock expiry. Direct Local
lifecycle checks concern its retained directory and process resources, not a
remote VM provider. Trace checks use received OTLP evidence; they do not claim
coverage of a hosted trace-query backend or UI.

## Five-backend Environment matrix

`test_28_environment_backends.py` adds 15 separately selected journeys for
`a13n.local-envd`, `a13n.docker`, `a13n.e2b`, `a13n.http-envd`, and
`a13n.websocket-envd`. Each backend runs tools/access, template/preparation,
and lifecycle/continuity journeys. The access journey checks read-only,
read-write, and full ceilings, actual file/Shell results, and forged tool calls
whose prohibited filesystem effects must remain absent. The existing case 22
continues to cover explicit no-environment selection.

| Backend        | Template and preparation                                               | Lifecycle                                                                                                          |
| -------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Local Envd     | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Fresh per-Run daemon scopes preserve the caller-owned workspace; stop/delete are rejected                          |
| Docker         | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Stop/resume retains generation; delete/rebuild advances it and preserves the fixture's bind directory              |
| E2B            | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Stop/resume preserves files; delete/rebuild advances generation and removes ephemeral files                        |
| HTTP Envd      | Managed templates are rejected; explicit external registration is used | Fresh sessions preserve files; Service stop/delete are rejected without terminating the external daemon            |
| WebSocket Envd | Managed templates are rejected; explicit external registration is used | Fresh reverse attachments preserve files; Service stop/delete are rejected without terminating the external daemon |

```sh
cargo build --locked -p a13n-envd
make image-sandbox
make live-test-management LIVE_TEST_ARGS='--live-environments -k test_environment_backend'
# Select one backend, or supply a different exact-version native executable.
A13N_ENVD_TEST_BINARY=/absolute/path/to/a13n-envd \
  make live-test-management LIVE_TEST_ARGS='--live-environments -k "test_environment_backend and http-envd"'
```

The matrix requires the explicit `--live-environments` opt-in. Default checks
do not start its processes or read private Provider configuration. E2B uses the
existing optional `environment` section and skips when absent. The other
backends require no external account. Envd defaults to the source-built
`target/debug/a13n-envd`; Docker uses the locally built sandbox image above.

Every backend owns a disposable Service lab. Managed Docker/E2B targets are
deleted before teardown. The HTTP and reverse WebSocket daemons are real native
processes over fixture-owned workspaces. Their external-operator configuration
disables native isolation; the separate Local Envd journey retains mandatory
native isolation. Reverse WebSocket uses an explicit, authenticated test Host
listener and a connection SDK injected into its single Worker. This verifies
Service/Harness execution through the production Provider without claiming
cross-Worker connection routing or a production WebSocket ingress deployment.
