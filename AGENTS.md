# Repository Guide

Agent Foundation is a Python-first open-source cloud foundation for building agents and multi-agent systems. Its core surfaces are the embeddable Agent Harness, hosted agent services, and built-in observability. The repository is currently in its architecture and specification phase.

## Sources of Truth

- `spec/` contains only the current accepted product and architecture design.
- `docs/` contains Markdown user documentation published with MkDocs Material.
- GitHub Issues are the primary venue for proposals, discussion, open questions, coordination, and progress tracking.
- Pull requests are the reviewed mechanism for changing specifications, documentation, code, tests, and automation.
- `CONTRIBUTING.md` defines the contribution workflow, local setup, and validation.
- `DEVELOPMENT.md` defines repository-wide engineering standards for deployable services.
- `MAINTAINERS.md` defines semantic reviewer routing.

Read [spec/repository-model.md](spec/repository-model.md) before changing repository structure or workflow. **Before starting any change, read [CONTRIBUTING.md](CONTRIBUTING.md); before implementation, also read [DEVELOPMENT.md](DEVELOPMENT.md).** The contribution workflow and applicable engineering standards are mandatory.

## Workflow

- Start material product, architecture, security, compatibility, or scope discussions in a GitHub Issue.
- Do not add RFCs, discussion logs, issue summaries, roadmaps, or progress tracking to `spec/`.
- When an issue reaches a conclusion, update the accepted design directly through a pull request.
- Keep changes focused and update affected specs, implementation, tests, docs, and automation together.
- Use the semantic areas in `MAINTAINERS.md` when requesting review.

## Documentation

- Keep documentation source files under `docs/` as Markdown.
- Configure site behavior and navigation in the root `mkdocs.yml`.
- Run `make docs-build` after changing docs content, navigation, or site configuration.
- Use `make docs-serve` for local preview.

## Development

Do not work from this summary alone. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for the Issue-to-PR workflow and [DEVELOPMENT.md](DEVELOPMENT.md) for implementation standards throughout design, development, and review.

The Python 3.13 environment and `packages/*` workspace are managed with `uv`. Workspace directories omit the project prefix, Python distribution names use the `a13n-` prefix, and import packages normalize it as `a13n_` (for example, `packages/agent-stream-protocol`, `a13n-stream-protocol`, and `a13n_stream_protocol`). Rust crates live under `crates/`. Foundation Service SDKs live independently under `sdk/{python,go,rust,typescript}` and do not join the root Python or Rust workspaces. The independent `sdk/rust/agent-foundation-cli` companion uses the Rust SDK for every network operation; it does not own another HTTP client or service-process behavior.

`a13n-harness` and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag assigns and publishes exactly the same version for both, and published Stream Protocol metadata pins that exact Harness version. `a13n-ui` releases independently through `release/agent-ui-v<version>`; reviewed source metadata selects one Harness release, and published UI metadata pins both Harness and Protocol to that version. Source manifests keep workspace dependencies unversioned for local development. `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`; Python metadata normalizes an RC to `X.Y.ZrcN`. `apps/harness-ui` is private build input to `a13n-ui`; its compiled files are not committed or released independently, but both the Agent UI sdist and wheel must contain them and an sdist-to-wheel build must not require Node.js. RC releases never advance Docker or npm `latest`.

Follow these service invariants; the complete contract and rationale live in [DEVELOPMENT.md](DEVELOPMENT.md):

- Use async I/O on service paths and keep blocking work off the event loop.
- Use the canonical engine, session, and short-transaction helpers rather than constructing local variants. Use `a13n-logging`; libraries obtain namespaced loggers, while executables configure output once at the process boundary.
- Never hold a database session or transaction across agent execution, external I/O, sleeps, background work, or a streaming response.
- SSE, WebSocket, and other streaming routes must not receive a yielded database session through their FastAPI dependency graph. Finish authorization and initial reads in a closed short session; open fresh short sessions inside the stream only when needed.
- Generate migration revisions through the repository Make target against disposable PostgreSQL, then review the generated operations and rollout safety. Never create a revision file from scratch.
- Worker-only processes never migrate. The shared image lets compatible control or all-in-one replicas auto-migrate under bounded PostgreSQL advisory locking; deployments with a dedicated migration job disable replica auto migration.
- Build one non-root service image for all-in-one, control, and worker roles; select the role at runtime.
- Keep identifiers that may reach model context or user-facing traces concise and kind-prefixed (for example, `process-1` or a short kind-prefixed hash). Do not shorten identifiers whose unpredictability or entropy is part of their security or protocol contract.

```bash
make install
make setup
make dev
make agent-ui
make agent-ui tui
make db-migrate msg="description"
make format
make lint
make deps-check
make typecheck
make test
make examples-check
make rust-check
make build
make image-foundation-service
make image-sandbox
make image-check
make check
make check-all
```

Use `make check` for fast feedback while iterating, and run `make check-all` before finalizing a broad change. Add implementation-specific checks behind the existing Make targets as packages are introduced.
