# Service end-to-end tests

`make service-e2e` runs the Service as operators deploy it and drives it as clients use it. It needs Docker.

Ordinary journeys share one Control and two Worker processes per pytest session, started with `a13n-service run` over HTTPS, with a 3-second lease and sub-second scans. Each journey creates a fresh Workspace with its own providers and models, scripted model process, HTTP clients and temporary directory. Each Service stack logs in once; clients copy its cookies and CSRF token without sharing an async client across event loops, and name the journey's workspace in `X-Workspace-ID`, as a login session does for business routes. An API key's client names none: the key acts in its own workspace.

Journeys that stop Service processes use `@pytest.mark.isolated_service` and get a dedicated stack. Fault journeys use `start_workers=1` to let the first worker claim the run, then `await stack.start_worker(successor)` before injecting the fault. This avoids suspending a live worker while it may hold database locks. Both workers remain owned by stack teardown, including a deferred worker whose startup fails. Journeys needing custom worker capacity can use `@pytest.mark.isolated_service(worker_slots=1)`; holding one run then directs the next to the other worker without suspending active heartbeats. Every stack owns a database cloned from a template migrated and bootstrapped through the installed CLI, and an object directory. PostgreSQL and Redis containers belong to the session; no journey clears shared Redis. Read-only database assertions select their own workspace or resource.

Before reusing the shared Service, teardown waits for the journey's active runs, attempts, pending inbox entries and outbox work to settle. If they do not settle within the normal wait budget, the suite stops with a failure rather than running subsequent journeys against leftover work. Test data is discarded with the owned databases at session end, without a table-by-table reset.

The scripted model (`dev/fixtures/scripted_model.py`) answers each OpenAI-compatible request with the turn the journey scripted for it, can hold a request until a gate opens, and records every request with what became of it.

Journeys use only the public HTTP API as the bootstrapped administrator or an API key. They signal processes and delete Redis keys to inject faults. Where the API does not show an invariant, such as which worker held an attempt or when its heartbeat ran, they read the database; they never write it. Package tests in `packages/a13n-service/tests` own business rules in-process; these journeys cover what only separate processes show.

| File                   | Journeys                                                                                                                                                                                                                                                                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_execution.py`    | CLI migrate and bootstrap, login, provider/model/agent setup, `Idempotency-Key` replay and conflict, concurrent retries, completion and continuation; the thread SSE stream, and reconnection after Redis loses it                                                                                                |
| `test_recovery.py`     | Heartbeats through a model slower than the lease; SIGTERM drain with handoff to the other worker (SIGKILL recovery is covered by the memory journey)                                                                                                                                                              |
| `test_control.py`      | Interrupt of a running run, authority revocation by deleting the grant, approval wait and resume with replay and stale-resume conflict, a steer joining a running run, messages queued behind a client-tool wait                                                                                                  |
| `test_subagents.py`    | An async subagent's child run on its own thread and its result delivered to the parent thread                                                                                                                                                                                                                     |
| `test_contention.py`   | Concurrent submissions to one thread and to many threads across both workers: one active run per thread, FIFO consumption, every input consumed once                                                                                                                                                              |
| `test_memory.py`       | Two conversations on different workers editing one memory file (a stale-version edit is refused without a revision, then applies after rereading; obsolete edit anchors fail) and the changes the next run receives; recovery from committed memory cursors after a worker SIGKILL; a memory deleted during a run |
| `test_environments.py` | A shell tool writing in the run's environment, then stop, restart with the files intact, unmount and delete, for `local`, native `docker` and each hosted type (`e2b`, `daytona`, `modal`, `vercel`, `sprites`, `runloop`); an E2B sandbox renewed past its timeout                                               |
| `test_egress.py`       | Controlled HTTPS credential injection through HTTP and reverse WebSocket; rotation during a tracked background command after its Run ends, idle synchronization on reuse, and revocation                                                                                                                          |

```sh
make service-e2e                                # default journeys (excludes hosted and Kubernetes)
make service-e2e SERVICE_E2E_ARGS="-k recovery"   # a selection; any pytest arguments
make service-e2e-docker                # build the native Docker image, then run its environment journey
make service-e2e-check                          # code, fixture, configuration and selection checks without Docker
```

## Slow reverse-link file import

`test_client_transfer_backpressure.py` runs a native reverse-WebSocket Envd with one isolated Control/Worker stack and real PostgreSQL/Redis. A transparent TCP relay limits actual TLS carrier bytes to 20,000 bytes/s upstream and 200,000 bytes/s downstream, with a 35 ms initial delay in each direction. A Run imports a random 1 MiB file with `asset_ref` while three sibling Runs read a control file on independent Sessions over the same connection. The assertions cover completed sibling Runs before the upload drains, object publication, size, SHA-256, exact stored bytes, and no carrier reconnect. Both default chunks and an 8192-byte frame diagnostic run.

Build this checkout's daemon before selecting the journey:

```sh
cargo build --locked --package a13n-envd --no-default-features
A13N_ENVD_TEST_BINARY="$PWD/target/debug/a13n-envd" \
  make service-e2e SERVICE_E2E_ARGS="--require-all -k client_transfer_backpressure"
```

The ordinary CI `other` shard builds and supplies this binary. Without it, local collection still works and execution skips; `--require-all` fails instead. The journey owns its daemon, relay, workspace and credentials, and the shared fixture owns its disposable stores and Service processes. Its observation deadlines allow the approximately 53 seconds of payload transmission plus protocol/control overhead; product heartbeat, Session cleanup, transfer and lease budgets remain unchanged. The WebSocket data-delivery fence trades one Ping/Pong round trip per bounded data batch for a bounded bulk backlog; this test establishes liveness at the stated rates, not at arbitrary slower links.

The environment journey also reads recipe defaults and Agent/Thread/Run overrides inside Agent commands and their child processes, verifies that clearing the Thread map restores Agent defaults, exercises explicit and empty command overrides without leaking them to later commands, and checks that editing a template does not change a retained instance after stop/resume. The same assertions run for every selected provider.

The `docker` environment journey needs the native execution image (`make image-sandbox`, or `SANDBOX_IMAGE`) and uses the Engine the Docker CLI uses. A hosted journey needs its vendor account in the environment and creates billable sandboxes, which it deletes, after a failure too. Hosted journeys are marked `hosted` with their type and run only when asked for: with `--hosted`, a `-m` expression naming `hosted`, or a `-k` expression naming their type, such as `-k e2b`; any other selection, `-k environments` say, leaves them out.

The credential journeys are included in the `docker` selection and require the current standard sandbox image. They grant `SYS_ADMIN` and `NET_ADMIN` with unconfined seccomp/AppArmor to their disposable Linux container so Envd can create real controlled namespaces. An HTTPS receiver runs on a public-address alias on that container's loopback interface; no credential request reaches a public service. Its temporary CA is trusted by Envd, while commands use Envd's separate Session CA. The receiver records only header hashes and never echoes authentication. Each journey owns a Service stack with HTTPS bound on `0.0.0.0` so the reverse daemon can reach it through the Docker host gateway. A guest TCP relay preserves the Control URL's loopback certificate identity; TLS verification stays enabled. Container and Service processes are removed on success and failure.

| Type      | Variables                                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `e2b`     | `E2B_API_KEY`                                                                                                                                           |
| `daytona` | `DAYTONA_API_KEY`, `DAYTONA_ENVD_SNAPSHOT` (a compatible snapshot with Envd); `DAYTONA_ORGANIZATION_ID`, read from the key when unset                   |
| `modal`   | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_ENVD_IMAGE`; optional `MODAL_WORKSPACE`. The journey deploys an empty App for its sandboxes and stops it |
| `vercel`  | `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `VERCEL_PROJECT_ID`                                                                                                   |
| `sprites` | `SPRITES_TOKEN`, `SPRITES_ORGANIZATION`; the token must permit creation with ownership labels (restricted tokens reject labels)                         |
| `runloop` | `RUNLOOP_API_KEY`; optional `RUNLOOP_ORGANIZATION`                                                                                                      |

```sh
set -a; . ~/.a13n/.env; set +a; make service-e2e SERVICE_E2E_ARGS="-k e2b"      # the E2B journeys
set -a; . ~/.a13n/.env; set +a; make service-e2e SERVICE_E2E_ARGS="--hosted"   # include hosted journeys
```

A journey whose dependency is missing is skipped with the reason in the report; `--require-all`, which CI passes, turns that skip into a failure, including a selected hosted journey without its account. Process logs of a failed journey are printed with its report.

[Service E2E CI](../../.github/workflows/ci-a13n-service-e2e.yml) runs memory, recovery and other non-Docker journeys in parallel on three runners. The Docker journey runs against the checkout with the standard Linux amd64 sandbox. CI resolves the published development image to its platform digest and compares its recorded source revision with the checkout for sandbox build inputs. A missing image, unverifiable revision or changed input selects one same-run build. Docker and Kubernetes load the same digest or the same checkout artifact. Collection checks require these four selections to be disjoint and cover the default suite exactly. All journeys run with `--require-all`. The SIGKILL memory journey also owns lease-expiry recovery, frozen execution options, inbox consumption and nonduplicated history checks. Image inputs, including the Rust sources and lockfile, select this workflow. Package test-only changes stay in their owning package gate. The [manual Console review](../../dev/service/console-review.md) is launched separately with `make console-review`.

## Kubernetes journeys

Kubernetes is explicitly selected with `-m kubernetes`. Set `E2E_KUBERNETES_PROFILE` to an absolute path to a trusted `ClusterProfile` JSON file with an absolute kubeconfig path, context, namespace allowlist, ownership ID, and `allow_port_forward` / `allow_plaintext_envd` enabled. The namespace and sandbox ServiceAccount must exist. Set `SANDBOX_IMAGE` to a compatible image already loaded into the cluster.

```sh
make service-e2e SERVICE_E2E_ARGS="-m kubernetes --require-all --junitxml=test-results/kubernetes-e2e.xml"
```

[Deployment CI](../../.github/workflows/ci-deploy.yml) runs both paths in parallel, each on its own pinned kind cluster. Pull requests select it for deployment, sandbox, cluster provider and cluster test inputs. Integration changes on main and manual Service E2E runs include the full Kubernetes suite. The shared build workflow caches exact source binaries and Cargo dependencies; Container CI also shares one binary per architecture across its three image variants.

- Helm installation and upgrade smoke run Control, Worker and sandbox Pods in-cluster, including Envd execution over the direct Pod IP.
- All eight Kubernetes pytest journeys use host-owned Service processes and real sandbox Pods over port-forward: the generic execution lifecycle, combined PVC deletion and retention/reuse in one Pod, four preparation failures, Worker SIGKILL plus Control restart, and finalizer-blocked deletion. A retained-claim metadata edit also verifies that retirement still cleans up the bootstrap Secret. Four disjoint shards cover storage, preparation failures, process recovery and the remaining lifecycle journeys. CI requires all eight reported cases with no skips or failures and retains JUnit reports and failure logs.

Package-level fake API tests complement these journeys with deterministic lost responses, conflicts, replacement UIDs and ownership changes. Real kind tests verify container execution and storage behavior that fake API responses cannot establish. The single-node kind gate exercises its default storage class; it does not establish multi-node or production CSI behavior.

These suites run separately from `make test`, which owns in-process package and tooling tests. CI reports `Service E2E support`, three `Service E2E scenarios` shards and `Service E2E Docker`; the aggregate `Service E2E` check requires all of them to succeed.
