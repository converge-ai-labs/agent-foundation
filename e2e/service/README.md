# Service end-to-end tests

`make service-e2e` runs the Service as operators deploy it and drives it as clients use it. It needs Docker.

Ordinary journeys share one Control and two Worker processes per pytest session, started with `a13n-service run` over HTTPS, with a 3-second lease and sub-second scans. Each journey creates a fresh Workspace and workspace-scoped providers, with its own scripted model process, HTTP clients and temporary directory. Each Service stack logs in once; clients copy its cookies and CSRF token without sharing an async client across event loops.

Journeys that stop or suspend Service processes use `@pytest.mark.isolated_service` and get a dedicated stack. Journeys needing custom worker capacity can use `@pytest.mark.isolated_service(worker_slots=1)`; holding one run then directs the next to the other worker without suspending active heartbeats. Every stack owns a database cloned from a template migrated and bootstrapped through the installed CLI, and an object directory. PostgreSQL and Redis containers belong to the session; no journey clears shared Redis. Read-only database assertions select their own workspace or resource.

Before reusing the shared Service, teardown waits for the journey's active runs, attempts, pending inbox entries and outbox work to settle. If they do not settle within the normal wait budget, the suite stops with a failure rather than running subsequent journeys against leftover work. Test data is discarded with the owned databases at session end, without a table-by-table reset.

The scripted model (`dev/fixtures/scripted_model.py`) answers each OpenAI-compatible request with the turn the journey scripted for it, can hold a request until a gate opens, and records every request with what became of it.

Journeys use only the public HTTP API as the bootstrapped administrator or an API key. They signal processes and delete Redis keys to inject faults. Where the API does not show an invariant, such as which worker held an attempt or when its heartbeat ran, they read the database; they never write it. Package tests in `packages/a13n-service/tests` own business rules in-process; these journeys cover what only separate processes show.

| File                   | Journeys                                                                                                                                                                                                                                                               |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_execution.py`    | CLI migrate and bootstrap, login, provider/model/agent setup, `Idempotency-Key` replay and conflict, concurrent retries, completion and continuation; the thread SSE stream, and reconnection after Redis loses it                                                     |
| `test_recovery.py`     | SIGKILL of the worker holding a run (recovery after lease expiry without repeating committed work), heartbeats through a model slower than the lease, SIGTERM drain with handoff to the other worker                                                                   |
| `test_control.py`      | Interrupt of a running run, authority revocation by deleting the grant, approval wait and resume with replay and stale-resume conflict, a steer joining a running run, messages queued behind a client-tool wait                                                       |
| `test_subagents.py`    | An async subagent's child run on its own thread and its result delivered to the parent thread                                                                                                                                                                          |
| `test_contention.py`   | Concurrent submissions to one thread and to many threads across both workers: one active run per thread, FIFO consumption, every input consumed once                                                                                                                   |
| `test_memory.py`       | Two conversations on different workers editing one memory file (disjoint edits apply, a stale edit fails with the current content) and the changes the next run receives; recovery from committed memory cursors after a worker SIGKILL; a memory deleted during a run |
| `test_environments.py` | A shell tool writing in the run's environment, then stop, restart with the files intact, unmount and delete, for `local`, native `docker` and each hosted type (`e2b`, `daytona`, `modal`, `vercel`, `sprites`, `runloop`); an E2B sandbox renewed past its timeout    |

```sh
make service-e2e                                # every journey except the hosted ones
make service-e2e SERVICE_E2E_ARGS="-k recovery"   # a selection; any pytest arguments
make service-e2e-docker                # build the native Docker image, then run its environment journey
make service-e2e-check                          # code, fixture, configuration and selection checks without Docker
```

The `docker` environment journey needs the native execution image (`make image-docker-environment`, or `DOCKER_ENVIRONMENT_IMAGE`) and uses the Engine the Docker CLI uses. A hosted journey needs its vendor account in the environment and creates billable sandboxes, which it deletes, after a failure too. Hosted journeys are marked `hosted` with their type and run only when asked for: with `--hosted`, a `-m` expression naming `hosted`, or a `-k` expression naming their type, such as `-k e2b`; any other selection, `-k environments` say, leaves them out.

| Type      | Variables                                                                                                                           |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `e2b`     | `E2B_API_KEY`                                                                                                                       |
| `daytona` | `DAYTONA_API_KEY`; `DAYTONA_ORGANIZATION_ID`, read from the key when unset                                                          |
| `modal`   | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`; optional `MODAL_WORKSPACE`. The journey deploys an empty App for its sandboxes and stops it |
| `vercel`  | `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `VERCEL_PROJECT_ID`                                                                               |
| `sprites` | `SPRITES_TOKEN`, `SPRITES_ORGANIZATION`                                                                                             |
| `runloop` | `RUNLOOP_API_KEY`; optional `RUNLOOP_ORGANIZATION`                                                                                  |

```sh
set -a; . ~/.a13n/.env; set +a; make service-e2e SERVICE_E2E_ARGS="-k e2b"      # the E2B journeys
set -a; . ~/.a13n/.env; set +a; make service-e2e SERVICE_E2E_ARGS="--hosted"   # every journey
```

A journey whose dependency is missing is skipped with the reason in the report; `--require-all`, which CI passes, turns that skip into a failure, including a selected hosted journey without its account. Process logs of a failed journey are printed with its report.

[Service E2E CI](../../.github/workflows/ci-a13n-service-e2e.yml) builds the image, runs every journey except the hosted ones with `--require-all` and reports the collected, executed and skipped counts. The [manual Console review](../../dev/service/console-review.md) is launched separately with `make console-review`.

These suites run separately from `make test`, which owns in-process package and tooling tests. CI reports `Service E2E support` and `Service E2E scenarios`; the aggregate `Service E2E` check requires both to succeed.
