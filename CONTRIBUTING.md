# Contributing

Contributions to Agent Foundation are welcome. The project uses GitHub Issues for discussion and progress tracking, and pull requests for every reviewed change to specifications, documentation, code, tests, and automation. Repository-wide service engineering requirements are defined in [DEVELOPMENT.md](DEVELOPMENT.md).

## Before You Start

Search the existing [issues](https://github.com/converge-ai-labs/agent-foundation/issues) before opening new work.

Open an issue before implementing a change with unresolved product, architecture, security, compatibility, or scope questions. Use the issue to describe the problem, constraints, alternatives, and progress. Trivial corrections may go directly to a pull request when no material discussion is needed.

Do not add proposals, RFC drafts, discussion logs, or progress tracking to `spec/`. Once an issue reaches an accepted conclusion, update the specification directly in the same pull request as the implementation or as a focused specification pull request.

Before changing service code, persistence, migrations, streaming endpoints, workers, logging, or container behavior, read [DEVELOPMENT.md](DEVELOPMENT.md) and the directly owning specification.

## Local Setup

Requirements:

- Git
- Python 3.13
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Make
- Node.js 24 with npm
- Go
- A stable Rust toolchain with `rustfmt` and Clippy
- Docker when generating PostgreSQL migrations, running container-backed integration tests, or validating images

Clone your fork and install the locked development environment and Git hooks:

```bash
git clone git@github.com:YOUR_NAME/agent-foundation.git
cd agent-foundation
make install
```

The repository selects Python 3.13 through `.python-version`. Python packages are uv workspace members under `packages/`; Rust crates under `crates/` are validated by the same top-level merge gate.

## Engineering Standards

[DEVELOPMENT.md](DEVELOPMENT.md) is the normative implementation guide for deployable services. In particular:

- service I/O is async-first;
- database access uses one canonical engine/session factory and short transaction scopes;
- database sessions never span streams, agent runs, external calls, waits, or background-task boundaries;
- streaming FastAPI routes complete database-backed authentication and initial reads before constructing the response;
- logging, process lifespan, role selection, image construction, and graceful shutdown use shared service infrastructure;
- `foundation-service` uses one artifact for all-in-one, control-plane, and execution-plane deployment roles.

Keep transport handling, application orchestration, domain behavior, and infrastructure adapters separated. Update the accepted design in `spec/` when a change alters ownership, lifecycle, compatibility, security, or deployment semantics; do not use the development guide to introduce product architecture implicitly.

## Local Validation

Use the Makefile as the stable development interface:

| Command                     | Purpose                                                           |
| --------------------------- | ----------------------------------------------------------------- |
| `make help`                 | List available commands                                           |
| `make install`              | Synchronize locked workspace, application, and SDK dependencies   |
| `make setup`                | Start local PostgreSQL and Redis                                  |
| `make dev`                  | Upgrade the schema and run Foundation Service with Foundation Web |
| `make dev-down`             | Stop local infrastructure and remove its data volumes             |
| `make format`               | Apply repository formatting hooks                                 |
| `make lint`                 | Run non-mutating repository lint checks                           |
| `make deps-check`           | Check each Python package's dependency declarations with deptry   |
| `make typecheck`            | Type-check Python package sources with Pyright                    |
| `make docs-serve`           | Start the local MkDocs development server                         |
| `make docs-build`           | Build the documentation site in strict mode                       |
| `make test`                 | Run Python workspace tests                                        |
| `make examples-check`       | Lint and type-check the independent examples                      |
| `make examples-check-all`   | Build and run the complete independent examples gate              |
| `make eip-check`            | Verify generated EIP artifacts and shared Python/Rust wire models |
| `make rust-check`           | Format-check and lint the root Rust workspace                     |
| `make sdk-check`            | Lint and type-check the standalone SDKs                           |
| `make foundation-cli-check` | Format-check and lint the standalone Foundation CLI               |
| `make foundation-web-check` | Format-check and type-check Foundation Web                        |
| `make foundation-web-build` | Build Foundation Web production assets                            |
| `make build`                | Build all workspace packages, applications, and standalone SDKs   |
| `make images`               | Build the foundation-service and sandbox images                   |
| `make image-check`          | Build and smoke-check both container images                       |
| `make check`                | Apply formatting, then check lint and types                       |
| `make check-all`            | Run the complete component gates, including tests and builds      |

Use `make format` when you want to apply formatting changes alone. `make check` applies the same formatters before running the fast validation gate. Installed pre-commit hooks also format supported changed files automatically; if a hook rewrites a file during commit, review and stage that result before committing again. Run the full local gate before opening or updating a broad pull request:

```bash
make check-all
```

`foundation-service` integration tests use fixture-owned Testcontainers. Application `FOUNDATION_*` variables never select test infrastructure.

## Releases

Create a stable release with canonical version `X.Y.Z` or a release candidate with `X.Y.Z-rc.N`, where `N` is a positive integer without leading zeroes. The release tag must point to a commit whose required CI checks have passed. Do not commit release-only version bumps: each release workflow injects the tag version into its known manifests and lock files in the ephemeral checkout, validates the resulting source, and then builds and publishes it. Release workflows do not repeat CI tests or lint checks. The canonical cross-ecosystem RC identity is `X.Y.Z-rc.N`; Python package metadata and artifact names use its standard PEP 440 normalization, `X.Y.ZrcN`.

Harness releases use `release/harness-v<version>`. The workflow assigns exactly the same version to `converge-agent-harness` and `converge-agent-stream-protocol`, pins the published Protocol dependency to that exact Harness version, builds both wheels and source distributions, publishes them through the `harness-pypi` environment, and attaches all four artifacts to one GitHub Release.

Agent UI releases use `release/agent-ui-v<version>`. The UI version advances independently. Before tagging, maintainers set `[tool.converge.agent-ui-release].harness-version` in `packages/agent-ui/pyproject.toml` to a published Harness release; the `0.0.0` placeholder blocks a real release. The workflow pins both Harness and Protocol to that exact version in the publishable sdist and wheel. It builds the private Harness UI into both artifacts, verifies a wheel rebuild from the sdist without Node.js, and installs the wheel in a clean environment from PyPI to prove the selected libraries are available before publishing through `agent-ui-pypi`. It attaches the two artifacts to one GitHub Release. The private frontend has no npm publication or independent release.

Foundation releases use `release/foundation-v<version>`. The workflow versions the repository root, `converge-logging`, and `converge-foundation-service`; it publishes the latter two Python distributions through the `foundation-pypi` environment and publishes the native `linux/amd64` foundation-service image. It excludes and does not republish Harness, Agent UI, or `converge-agent-envd-client`, and selects compatible published Harness libraries through its own dependency management.

agent-envd releases use `release/agent-envd-v<version>`. The workflow versions the Cargo workspace, `converge-agent-envd-client`, and their lock files with one canonical release identity. It builds the Python wheel and source distribution concurrently with Linux GNU and macOS tar archives plus Windows x64 and ARM64 ZIP archives. After every distribution and the crate package validate, independent jobs publish `converge-agent-envd` to crates.io with `CARGO_REGISTRY_TOKEN` from the `agent-envd-crates-io` environment and publish `converge-agent-envd-client` to PyPI with `PYPI_TOKEN` from the `agent-envd-client-pypi` environment. The sandbox image publishes only after both registry jobs succeed. A retried publish skips an existing artifact only when the registry check confirms the built artifact matches; a mismatch fails closed. The GitHub Release waits for all publish paths, generates checksums, and attaches the Python distributions and binary archives. Linux binaries target the current GitHub-hosted Ubuntu/glibc baseline; use the sandbox image when a fixed userspace is required.

SDK languages version and release independently from the standalone `sdk/` directory. Push `release/sdk/<language>/<version>`; the workflow versions that language's package metadata before building. Python publishes through `sdk-python-pypi`, Rust through `sdk-rust-crates-io`, and TypeScript through npm Trusted Publishing bound to `release-sdk-typescript.yml` and `sdk-typescript-npm`. A TypeScript RC publishes under the npm `rc` dist-tag rather than `latest`. Go has no embedded package version; its workflow validates the release version and creates the canonical `sdk/go/v<version>` module tag through `sdk-go-github`.

Foundation CLI releases use `release/foundation-cli-v<version>`. The workflow injects the version into the independent `sdk/rust/agent-foundation-cli` manifest and lock file, then builds Linux x86_64 and ARM64, macOS x86_64 and ARM64, and Windows x86_64 and ARM64 archives. Each archive contains the `agent-foundation` executable and `LICENSE`; the GitHub Release also includes `SHA256SUMS`. This channel publishes no crate, uses no registry credentials or GitHub Environment, and defines no mutable `latest` selector for stable or RC releases.

An RC publishes the same registry and downloadable artifact set as its corresponding stable channel and creates a GitHub prerelease. A stable release creates a normal GitHub Release. A release may add reviewed, human-written notes at `.github/release-notes/<component>/<version>.md`; see [the release-notes guide](.github/release-notes/README.md). The file is optional. Its content is prepended to generated notes for later releases and replaces the default initial sentence for the first release in a channel. The first RC for a target compares with the preceding release, later RCs compare with the preceding RC, and the final stable release compares with the preceding stable release so its notes cover the complete stable change set. Pull requests are categorized by the labels configured in `.github/release.yml`; use `breaking-change`, `enhancement`, `bug`, or `documentation`, and use `chore` or `skip-changelog` to omit a pull request. Direct commits remain visible through the generated Full Changelog comparison link but are not listed as categorized pull requests.

A push to `main` that changes a development image input builds and smoke-checks both images before publishing the mutable `dev` tag and an immutable `sha-*` tag. An RC image publishes only its exact `X.Y.Z-rc.N` tag and never modifies `latest`. A stable image publishes its exact `X.Y.Z` tag and advances `latest`. Development image jobs also never modify `latest`. Replace each placeholder registry secret in its scoped GitHub Environment before the corresponding release.

## Database Changes

Database changes follow the migration contract in [DEVELOPMENT.md](DEVELOPMENT.md#schema-migrations).

Do not create Alembic revision files manually or autogenerate against an existing developer or shared database. Generate every `foundation-service` revision through the stable repository target:

```bash
make db-migrate msg="describe the schema change"
```

The target starts the local PostgreSQL service when needed, rebuilds schema history in a disposable database, autogenerates and formats the revision, and removes the temporary database. Review the generated migration rather than treating a clean model diff as proof of safety. The complete model-import and verification flow is documented in [packages/foundation-service/README.md](packages/foundation-service/README.md#add-an-orm-model).

A schema-change pull request must explain lock duration, scans or rewrites, rolling old/new compatibility, index strategy, bounded backfill, interruption and rerun behavior, and rollback or forward repair. Prefer additive expand-and-contract changes. The shared image auto-migrates `all` and `control` replicas under advisory locking; deployments with a dedicated migration job disable replica auto migration. Execution-only processes never migrate.

Run migration graph, clean-upgrade, schema-parity, and relevant PostgreSQL lock/concurrency tests. Record any required timeout override and its rationale in the pull request.

## Documentation Changes

- Keep user-facing documentation in `docs/`.
- Every source file under `docs/` must be Markdown.
- Update `mkdocs.yml` when adding, removing, or moving a page.
- Run `make docs-build` after documentation or site configuration changes.
- The canonical public site is [agent-foundation-docs.converge.ai](https://agent-foundation-docs.converge.ai/).
- The `Docs` GitHub Actions workflow publishes build artifacts for pull requests and deploys `main` to the `agent-foundation-docs` Cloudflare Pages project.

## Specification Changes

- Keep accepted product and architecture design in `spec/`.
- State the resulting design directly and consistently.
- Keep alternatives under debate, open questions, meeting notes, progress, and issue history in GitHub Issues.
- Link the issue that established the motivation and agreement from the pull request rather than copying its discussion into the specification.

See [spec/repository-model.md](spec/repository-model.md) for the normative repository boundaries.

## Pull Requests

A pull request should:

1. Link the relevant issue when one exists.
2. Explain the motivation and material changes.
3. Update affected specifications, docs, tests, and automation.
4. Report the exact validation commands and outcomes.
5. Request reviewers according to `MAINTAINERS.md`.

Keep commit messages in English. Do not add an agent as a co-author. If assistant attribution is required, use only:

```text
Assisted-by: NAME <email>
```
