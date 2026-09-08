# Local live tests

These opt-in tests send real HTTP requests to separate local Control and Worker
processes. Control accepts Native API requests; Worker readiness is checked over
HTTP, and execution is dispatched through the real queue. The tests never call
Worker execution internals or use an in-process ASGI transport for live journeys.

| File                           | Journey                                                           |
| ------------------------------ | ----------------------------------------------------------------- |
| `test_01_basic_run.py`         | Acceptance, execution, result, attempts, usage and retained items |
| `test_02_continuation.py`      | Successor Run reconstructs conversation context                   |
| `test_03_tools_environment.py` | Real local shell writes and reads a file                          |
| `test_04_stream_reconnect.py`  | Disconnect during execution and resume with Last-Event-ID         |
| `test_05_idempotency.py`       | Concurrent duplicate submission and changed-intent conflict       |
| `test_06_steer.py`             | Input submitted mid-tool is durably consumed by the same Run      |
| `test_07_interrupt.py`         | Interrupt model I/O and tool execution; verify teardown           |
| `test_08_approval.py`          | Approve/reject pending work through a successor Run               |

The first round has ten live cases because interrupt and approval each have two variants.
The deterministic OpenAI-compatible model fixture controls timing and expected
answers. This tests Foundation orchestration and real tool execution, not external
model quality or provider compatibility. The approval tool is uploaded as a real
plugin wheel through the API.

## Setup

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
Setup creates the Model Provider, Model, Environment Provider, Environment, plugin
and Agents through Control HTTP. It records each successful creation for reuse.

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
| `test_14_async_subagents.py`     | Two durable children, correlated results, stable publication; parent cancellation leaves default independent children running and suppresses automatic result successors                         |
| `test_15_dependency_outages.py`  | Worker-only PostgreSQL, Redis and object-store connection cuts: prove the cut was exercised, restore and restart, validate terminal state and continued service availability                     |
| `test_16_workspace_isolation.py` | Another User in the same Organization and a different Workspace: valid own access, denied reads/stream/control, no unauthorized state changes                                                    |

These are 16 additional live variants in eight files. They use the real Control,
Worker and embedded Harness, with HTTP model responses and an uploaded test tool
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

Prerequisites are Docker and a **compatible loopback HTTP S3 server**, configured
using `A13N_SERVICE_OBJECT_ENDPOINT_URL`, `A13N_SERVICE_OBJECT_REGION`, and the
server's AWS credentials in `.env`. The server must support the service's
conditional-write/delete probe; an arbitrary MinIO version is not sufficient.
The test creates and deletes its own random bucket, and does not use the bucket
named by `A13N_SERVICE_OBJECT_BUCKET`. Missing or incompatible S3 fails setup
explicitly, rather than skipping enabled live cases. Run only one selection at a
time; these journeys do not use pytest-xdist.

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

This round adds 30 live variants in 11 independently selectable files. Cases 19,
28, 29 and 30 are intentionally excluded. All management resources are created
through public HTTP APIs, and each enabled test uses its own isolated lab with
the same Docker and compatible loopback S3 prerequisites as round two.

| File                                 | Acceptance evidence                                                                                                                                                                                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_17_agent_revisions.py`         | Update after Run acceptance but before Worker claim; current and explicit old revisions reach the model; per-Run overrides do not alter stored revisions; stale writes are rejected                                                                          |
| `test_18_plugin_execution.py`        | Uploaded wheel and exact version/configuration lock produce a real file effect; an unbound Agent has no plugin tool; invalid factory configuration is rejected                                                                                               |
| `test_20_environment_templates.py`   | Explicit old/current recipes read different files; `on_run` versus `on_use` is observed before tool execution; simultaneous first reads use one logical target/generation; unused lazy selection stays unprepared; explicit null overrides the Agent default |
| `test_21_environment_lifecycle.py`   | Stop/reopen preserves backing generation; delete/rebuild advances it; stale process handles fail; Continue, Retry, Fork and Feedback inherit correctly; changing the Thread default does not change a historical Fork source                                 |
| `test_22_environment_access.py`      | No environment, read-only, read-write and full access have the expected file/Shell surface; actual read/write/Shell bytes agree; forged unadvertised writes produce no file                                                                                  |
| `test_23_model_updates.py`           | Accepted Model settings/upstream remain frozen; a new Run receives new settings; the next request of a running Agent uses rotated Provider credentials/new endpoint or is denied after disable                                                               |
| `test_24_skill_execution.py`         | Real ZIP document and attachment uploads; accepted latest version freezes before update; later current and explicit pinned bindings materialize/read correct files; cross-Workspace package reads and absent Environment are denied                          |
| `test_25_asset_execution.py`         | File bytes materialize into the Environment and PNG bytes enter the model request unchanged; deletion before Worker claim blocks delivery; explicit publication produces an immutable downloadable Asset linked to its Run, with cross-Workspace denial      |
| `test_26_output_and_client_tools.py` | Output schema rejects invalid results and enforces the retry bound; external client tool waits and resumes with actual supplied data; malformed, duplicate and stale feedback cannot create extra successors                                                 |
| `test_27_connectivity_execution.py`  | Production MCP client and Composio adapter connect to local HTTP peers; selected tools, arguments and credential hashes agree; disabling/revoking the connection prevents later dispatch                                                                     |
| `test_31_observability.py`           | Actual OTLP/HTTP exports correlate Service Attempt, Harness, model and tool spans; durable Items, SSE and model usage agree; rejecting trace exports with HTTP 503 does not change the result or repeat the effect                                           |

```sh
make live-test-management
make live-test-management LIVE_TEST_ARGS='-k revision'
make live-test-management LIVE_TEST_ARGS='-k asset'
make live-test-check
```

Use `--live-management` only for this round. The existing two entry points retain
their own opt-ins. The lab also retains private model request observations,
Composio/MCP dispatch evidence and OTLP exports beneath
`.state/round-two/<random-id>/`. Ambient OpenTelemetry destinations are removed
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
interaction, Runner activation, plugin version/instance isolation, Secret
bindings or IAM grant revocation. The client-tool stale-feedback check concerns
a superseded sealed state, not a fabricated wall-clock expiry. Direct Local
lifecycle checks concern its retained directory and process resources, not a
remote VM provider. Trace checks use received OTLP evidence; they do not claim
coverage of a hosted trace-query backend or UI.
