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

There are ten live cases because interrupt and approval each have two variants.
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
`FOUNDATION_OBJECT_BACKEND=s3`, `FOUNDATION_OBJECT_BUCKET`,
`FOUNDATION_OBJECT_ENDPOINT_URL`, `FOUNDATION_OBJECT_REGION`, and, if required,
`FOUNDATION_OBJECT_FORCE_PATH_STYLE=true`, plus the backend's AWS credentials.
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

For the local model fixture, set `FOUNDATION_MODEL_PRIVATE_ENDPOINT_CIDRS='["127.0.0.1/32"]'` in `.env` and restart both roles so Worker model calls can reach the loopback fixture.
