# Local Service development

This directory owns the local Service workflow. The root Makefile is its stable interface; people and agents use the same commands.

## Quick start

```sh
make dev
```

`make dev` synchronizes locked Python and frontend dependencies, starts this checkout's PostgreSQL and Redis, applies migrations, creates the administrator `admin@example.com` / `local-public-password-123` in an empty database, and starts three applications in the background: the local scripted model (`dev/fixtures/model.py`), the Service (`--role all`) and the Console dev server. It returns once all three accept connections and prints the Console URL. The applications run in their own OS session with no terminal input, so closing the terminal or ending an agent turn does not stop them. Their output goes to `var/dev/logs/`.

Sign in through the printed Console URL, `http://<instance>.localhost:<port>`. Every checkout gets its own host name, because browsers keep one cookie jar per host whatever the port: two checkouts' Consoles hold separate sessions in one browser. Browsers resolve every `*.localhost` name to loopback. The Service accepts browser changes only from that origin, so signing in through `http://127.0.0.1:<port>` fails.

A fresh database is empty apart from the administrator. For representative content, run `make dev-reset STATE=seeded` once.

| Command                       | Effect                                                                                      |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `make dev`                    | Prepare, then run the model, Service and Console in the background                          |
| `make dev-foreground`         | The same, attached; Ctrl+C stops everything, a second Ctrl+C forces it                      |
| `make service-dev`            | Prepare, then run only the model and Service in the foreground                              |
| `make setup`                  | Prepare stores, schema and administrator without starting applications                      |
| `make dev-stop`               | Stop the running applications and wait until they exit                                      |
| `make dev-status`             | Print this checkout's instance, URLs, ports, listeners and lifecycle owner as JSON          |
| `make dev-down`               | Stop PostgreSQL and Redis, keeping their data                                               |
| `make dev-reset STATE=empty`  | Delete this checkout's data and rebuild the schema and administrator                        |
| `make dev-reset STATE=seeded` | The same, then seed and verify representative content                                       |
| `make dev-env-list`           | List this machine's checkouts, their instances, stores and running applications             |
| `make db-upgrade`, `db-check` | Migrate, or check the migration state of, this checkout's database (`var/dev/service.toml`) |

Do not assume ports: `make dev-status` reports them. It changes nothing and works in a fresh checkout, where it reports `configured: false`.

## Instance and settings

The first command that needs an instance reserves a block of loopback ports for the checkout (Service, Console, model, PostgreSQL, Redis) and records it in `var/dev/instance.json`. A machine lock serializes reservations, and a machine registry (`~/.local/state/agent-foundation/dev/checkouts.json`) keeps blocks disjoint across checkouts. Assignments never move, because seeded model providers store the model's address. When an assigned port is taken, the command fails and names it; stop the process holding it.

One resolver, `checkout.py`, derives everything local from the instance and writes the Service's ordinary settings file, `var/dev/service.toml`: loopback listener, the Console origin as `server.public_url`, the database and Redis URLs, the object store under `var/dev/objects`, an encryption key generated once per checkout (`var/dev/encryption.key`), outbound access to loopback over plain HTTP for the scripted model, development-only `local` environments, and telemetry. Nothing in `packages/` knows about checkouts. Applications start without inherited `A13N_*` and `OTEL_*` variables, so a deployment shell can neither redirect nor break the local instance.

Each checkout owns the Compose project `a13n-service-dev-<instance>` with its own PostgreSQL and Redis volumes. `make dev-down` stops them; `make dev-reset` deletes and recreates them. A lifecycle lock (`var/dev/lifecycle.lock`) admits one setup, reset, down or application run at a time, so a reset or down is refused while applications run; stop them first.

## Seeded state

`make dev-reset STATE=seeded` rebuilds the database, starts the model and Service temporarily, and creates a fictional organization through the public API (`seed.py` and its `seed_*.py` modules), with real execution against the scripted model:

- **Identity**: the organization, workspace and administrator renamed, with images; the members `builder@example.com`, `runner@example.com` and `viewer@example.com`, one per built-in role, who sign in with the administrator's password; a pending invitation (the expiry sweep deletes revoked ones); active, expiring and revoked API keys; an active and a disabled service account; an empty second workspace and an archived one.
- **Providers**, all in the default workspace: a fictional account of every offered model, web, connector and environment provider type, with placeholder credentials no backend accepts; each fictional model provider serves one disabled model, keyed `fictional-<type>`, that carries a models.dev catalog reference where its type has one. The scripted models `local-scripted` and `local-scripted-media` are enabled and carry fictional prices, so usage shows cost; the media model declares every understanding capability and is the workspace's media-understanding default.
- **Resources**: labeled skills with several revisions, one whose newest revision is not the default, and an archived one; more agents than one Console page, including revised, duplicated and archived ones and Agent Composer; a template per environment account, none of them reserved, so the seed calls no hosted provider, and a disabled one; a webhook subscription that the scripted model process accepts; example files (text, image, audio, PDF, archive, binary) stored as assets.
- **Environments**: `local` environments are directories under `var/dev/environments`, a development-only type the checkout's settings allow. Seeded conversations mount the ready instance of the Local workspace template, so runs show the writer's skill, file edits, commands and a published asset; another instance is stopped, and an agent whose default template is Local workspace reserves one per thread.
- **Memory**: a team handbook with an always-loaded README and preferences, both PostgreSQL file memories; team facts, a record memory in a fake mem0 server that the scripted model process serves under `/mem0` (`dev/fixtures/mem0.py`) and keeps in `var/dev/mem0.json` across restarts; and a team assistant that mounts the preferences and facts for writing and the handbook for reading by default. One conversation edits a preference through the memory tools, a person then edits the handbook, and the next run receives both changes, so each file memory shows history, diffs and revisions to restore. Another conversation recalls the facts and records one more through the record tools.
- **Connections**: MCP connections to the scripted model process's `/mcp` route (ready, pending authorization, which any token makes ready, and disabled) and a Composio connection pending authorization. `make dev` already serves that route, so no other fixture process runs.
- **Execution**: a multi-turn conversation with an attachment and a fork; a conversation showing each way a file reaches the model: an image, a recording and a PDF natively to the media model, short text inline, and an archive, a binary file and long text placed into the mounted environment; a steered run; an interrupted run whose thread keeps a queued message; runs waiting for an approval, a client tool and a user's answer, and runs resumed after an approval and a client tool result; failed runs (a model error, malformed structured output); structured output; a sub-agent delegation; another member's conversation; runs on an agent's first revision after it changed, one pinned to it.

It reads the state back through the API and writes the seeded IDs and the verification to `var/dev/seed-report.md`; any failed check fails the reset. The scripted model's prompt markers, such as `[client]`, `[workspace]` and `[fail]`, select its behavior (see `dev/fixtures/model.py`).

## Private development resources

To use real providers in a seeded checkout, copy [dev-resources.example.toml](dev-resources.example.toml) to `~/.a13n/dev-resources.toml`, set its mode to `0600`, and fill in credentials. The file lives outside every checkout and is shared by all of them. It is read only as a regular file owned by the current user and not readable by others; errors name the offending location, never a value.

In a seeded checkout, the seeded reset and every application start (`make dev`, `dev-foreground`, `service-dev`) apply the file through the public API, signed in as the local administrator, to the default workspace: model providers and their models, web providers, environment providers and their templates, and connector providers. Names identify providers and templates; keys identify models; `configuration` becomes the provider's `config` (a template's recipe). An entry with a blank credential is skipped, together with its models or templates. Removing an entry deletes nothing. Each checkout records a digest per applied provider in `var/dev/dev-resources.json`, so unchanged providers are not resent. A failure is reported and leaves the seeded state and the running applications usable; fix the file and run `make dev` again. Applying the file creates no environment or connection and makes no model call.

## Tracing

With `TRACES=auto` (the default), `make dev`, `make service-dev` and resets export traces to the machine-shared Langfuse (`make langfuse-up`, `http://127.0.0.1:3000`) when it is already running and its local project keys authenticate; otherwise tracing is off. Starting the shared stack takes minutes, so it never starts implicitly: run `make langfuse-up` and restart, or pass `TRACES=langfuse` to start it first. `TRACES=none` turns tracing off. Spans carry the deployment environment `local-<instance>`, which separates checkouts in Langfuse; the Console's trace views query the same backend. The public Langfuse account is `dev@agent-foundation.local` / `agent-foundation-local`.

## Other checkouts

`make dev-env-list` (or `python3 -m dev.service.envs list --json`) lists this repository's worktrees and every registered checkout with its instance, ports, store state and running applications, plus Compose projects of this workflow whose checkout is no longer registered.

```sh
python3 -m dev.service.envs rm ID1 ID2 --dry-run
python3 -m dev.service.envs rm ID1 ID2 --yes
```

Removal stops the checkout's applications, deletes its Compose containers and volumes and its `var/dev`, and releases its port reservation. It keeps the worktree itself and the shared Langfuse. Unregistered projects are listed for investigation only.

## Recovery and checks

- A migration failure because the database's migration history does not match this checkout: `make dev-reset STATE=empty` or `STATE=seeded` rebuilds this checkout's database.
- An application that exits: its log is in `var/dev/logs/`; the others are stopped with it.
- Docker unavailable: start it and retry; nothing is switched or started implicitly.

`make dev-state-check` lints and type-checks this directory and runs its tests, including one that seeds a disposable instance with its own Compose project (Docker required). `make db-migrate msg="..."` generates migrations against a separate disposable database (`db-migrate.sh`). `make service-e2e` and `make console-review` use their own stores and never touch this checkout's instance.
