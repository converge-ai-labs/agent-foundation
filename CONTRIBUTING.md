# Contributing

Contributions to Agent Foundation are welcome. The project uses GitHub Issues for discussion and progress tracking, and pull requests for every reviewed change to specifications, documentation, code, tests, and automation. Repository-wide code quality principles and component engineering requirements are defined in [DEVELOPMENT.md](DEVELOPMENT.md).

## Repository Language

Write repository code and documentation in English. UI translation resources contain their target-language text.

## Before You Start

Search the existing [issues](https://github.com/converge-ai-labs/agent-foundation/issues) before opening new work.

Open an issue before implementing a change with unresolved product, architecture, security, compatibility, or scope questions. Use the issue to describe the problem, constraints, alternatives, and progress. Trivial corrections may go directly to a pull request when no material discussion is needed.

Do not add proposals, RFC drafts, discussion logs, or progress tracking to `spec/`. Once an issue reaches an accepted conclusion, update the specification directly in the same pull request as the implementation or as a focused specification pull request.

Before changing a surface, read the relevant sections of this guide, [DEVELOPMENT.md](DEVELOPMENT.md), and the directly owning specification. Service, persistence, migration, streaming, worker, logging, and container changes require their applicable engineering rules. Reuse sections already read unless they changed.

## Local Setup

Requirements:

- Git
- Python 3.13
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Make
- Node.js 24 with npm (standalone TypeScript SDK) and pnpm (frontend workspace)
- Go 1.25 or newer
- A stable Rust toolchain with `rustfmt` and Clippy
- Docker when generating PostgreSQL migrations, running container-backed integration tests, or validating images

Clone your fork and install the locked development environment and Git hooks:

```bash
git clone git@github.com:YOUR_NAME/agent-foundation.git
cd agent-foundation
make install
```

The frontend pnpm workspace lives under `frontend/`: applications in `frontend/apps/` and shared UI source in `frontend/packages/`. `frontend/package.json` pins pnpm; install that version before running `make install`. The standalone TypeScript SDK retains its independent npm project and lockfile.

Local Service development uses the explicit, public test configuration in `dev/service/local.toml`. `make dev` prepares local PostgreSQL, Redis and Langfuse, applies migrations, and launches Service and Console; no `.env` or manual trace credentials are required. See [the local Service guide](dev/service/README.md) for startup, storage ownership, reset baselines, and fictional login credentials. Service does not automatically load `.env`; environment variables remain available as explicit deployment overrides. `dev/harness/.env.example` and `dev/harness-ui/.env.example` provide separate Langfuse-first development profiles with commented Logfire alternatives. Copy each to its sibling ignored `.env` without overwriting an existing private file, then use `make harness-dev`, `make cli`, or `make harness-ui-smoke`. See the [Harness](dev/harness/README.md) and [Harness UI](dev/harness-ui/README.md) development guides. `.env.harness.example` remains optional reference material for other embedded Harness workflows. Existing examples and live-test targets load private environment files only at their explicit launcher boundaries.

The repository selects Python 3.13 through `.python-version`. Python packages are uv workspace members under `packages/`; Rust crates under `crates/` are validated by the same top-level merge gate.

## Engineering Standards

Apply [Code Quality and Design](DEVELOPMENT.md#code-quality-and-design) when implementing or reviewing features, bug fixes, and refactoring across repository languages and components. Read additional engineering rules for the boundaries affected by the change. For deployable services, these include:

- service I/O is async-first;
- database access uses one canonical engine/session factory and short transaction scopes;
- database sessions never span streams, agent runs, external calls, waits, or background-task boundaries;
- streaming FastAPI routes complete database-backed authentication and initial reads before constructing the response;
- logging, process lifespan, role selection, image construction, and graceful shutdown use shared service infrastructure;
- `a13n-service` uses one artifact for all-in-one, control, worker, and connector deployment roles.

Keep transport handling, application orchestration, domain behavior, and infrastructure adapters separated. Update the accepted design in `spec/` when a change alters ownership, lifecycle, compatibility, security, or deployment semantics; do not use the development guide to introduce product architecture implicitly.

## Local Validation

Use the Makefile as the stable development interface:

| Command                       | Purpose                                                            |
| ----------------------------- | ------------------------------------------------------------------ |
| `make help`                   | List available commands                                            |
| `make install`                | Synchronize locked workspace, application, and SDK dependencies    |
| `make setup`                  | Prepare local PostgreSQL, Redis, Langfuse and Service schema       |
| `make dev`                    | Upgrade the schema and run a13n Service and Console                |
| `make service-dev`            | Run only local Service and the scripted development model          |
| `make dev-reset STATE=empty`  | Rebuild owned Service storage with no business data                |
| `make dev-reset STATE=seeded` | Rebuild owned Service storage with fictional resources and history |
| `make dev-state-check`        | Validate state tools with disposable local infrastructure          |
| `make dev-down`               | Stop local Service and Langfuse infrastructure; preserve data      |
| `make cli`                    | Run Harness UI with `dev/harness-ui/.env`                          |
| `make harness-dev`            | Run SDK observation scenarios with `dev/harness/.env`              |
| `make harness-ui-smoke`       | Run a scripted real-App observation smoke test                     |
| `make langfuse-up`            | Start the isolated local Langfuse trace backend                    |
| `make langfuse-down`          | Stop local Langfuse while preserving its data                      |
| `make langfuse-reset`         | Stop local Langfuse and remove its data volumes                    |
| `make format`                 | Apply repository formatting hooks                                  |
| `make lint`                   | Run non-mutating repository lint checks                            |
| `make deps-check`             | Check each Python package's dependency declarations with deptry    |
| `make typecheck`              | Type-check Python package sources with Pyright                     |
| `make docs-serve`             | Start the local MkDocs development server                          |
| `make docs-build`             | Build the documentation site in strict mode                        |
| `make test`                   | Run Python workspace tests                                         |
| `make examples-check`         | Lint and type-check the independent examples                       |
| `make examples-check-all`     | Build and run the complete independent examples gate               |
| `make eip-check`              | Verify generated EIP artifacts and shared Python/Rust wire models  |
| `make rust-check`             | Format-check and lint the root Rust workspace                      |
| `make sdk-generate`           | Regenerate all Native SDK bindings from Service OpenAPI            |
| `make sdk-generated-check`    | Check shared OpenAPI and generated bindings without modifying them |
| `make sdk-check`              | Lint and type-check the standalone SDKs                            |
| `make a13n-service-cli-check` | Format-check and lint the standalone a13n Service CLI              |
| `make frontend-sync`          | Install the locked frontend workspace dependencies                 |
| `make frontend-check-all`     | Check and build all frontend applications                          |
| `make build`                  | Build all workspace packages, applications, and standalone SDKs    |
| `make images`                 | Build the a13n-service and sandbox images                          |
| `make image-check`            | Build and smoke-check both container images                        |
| `make check`                  | Apply formatting, then run fast checks with four parallel workers  |
| `make check-all`              | Run the complete component gates, including tests and builds       |

This section owns validation policy; agent guides and skills refer here rather than adding separate gates. Select checks from changes since the last successful validation and their dependency impact. Without prior results, cover the complete intended change. For merge or rebase updates, include incoming changes and interactions between both branches, not just textual conflicts.

The SDK pre-commit hook regenerates on potential Service contract changes and leaves changed files unstaged for review. Commit `sdk/openapi.json` and generated outputs together. `make sdk-generated-check` regenerates in temporary directories and fails on stale or removed output; CI runs it independently of the language checks. See [Native SDK generation](sdk/codegen/README.md) for pinned tools and owned outputs.

Start with the fastest relevant Make targets and add meaningful tests for behavior changes. Use `make check` for repository-wide fast validation. Run `make check-all` before handoff when the affected scope is broad, such as changes spanning multiple component boundaries or shared build tooling, or cannot be bounded confidently. Complete applicable [migration checks](#database-changes), [image checks](DEVELOPMENT.md#container-image), and `make docs-build` for changes to `docs/`, navigation, or site configuration. Instruction-only changes need formatting, link checks, and structural validation of changed skills; they do not require unrelated application suites.

Reuse successful results whose relevant source, dependency, configuration, and environment inputs remain unchanged. A commit or PR update alone does not invalidate them. After a fix, rerun affected checks; expand only for new changes, failures, or unresolved risk. Do not repeat covered checks merely to run both `make check` and `make check-all`. Report exact commands and outcomes, including failures and unavailable checks; a partial gate is not a passing full gate. Required CI checks remain unchanged.

Use `make format` for formatting alone; `make check` applies the same formatters before its fast checks, while `make check-all` does not apply them. Installed pre-commit hooks format supported changed files automatically. Review formatter edits and, if a commit hook rewrites a file, stage the intended result before committing again. Never bypass hooks.

`a13n-service` integration tests use fixture-owned Testcontainers. Application `A13N_SERVICE_*` variables never select test infrastructure. Loopback SSE and fixture-owned S3 clients bypass ambient proxies.

Testcontainers is pinned to 4.13.1 because 4.15.0 can read Ryuk port mappings before Docker publishes them; upgrades must verify mapped-port startup with Ryuk enabled. Unreturned SQL connections, unhandled thread exceptions, and unraisable exceptions fail the test gate.

a13n Service CI runs service tests on a dedicated larger runner, with logging tests, type checks, and builds on a standard runner. The `a13n Service Python` check requires both jobs to pass. See [the workflow](.github/workflows/ci-a13n-service.yml) for worker counts, timing output, and timeout settings. Local `make test` uses seven workers for a13n Service, matching CI, and two workers for other Python suites. Tests are grouped by file unless explicitly marked with `xdist_group`; each worker owns its containers. SQLite fixtures give each test an independent copy of a schema template. Process tests use a template built through real migrations; migration tests still run upgrades and downgrades directly.

UI Tests runs the Linux suite with two file-grouped workers on pull requests and `main`. Independent PTY scenarios use separate `xdist_group` marks because each owns its process and temporary home. Python tests, Console/shared frontend checks, and WebUI distribution verification run in parallel; the `UI (Linux)` check requires all three jobs to pass. Distribution tooling tests run with packaging, outside the application test path. The separate [WebUI workflow](.github/workflows/ci-a13n-harness-ui-webui.yml) owns browser unit tests, API snapshot checks, and Python startup/HTTP integration tests; the Linux Python test job excludes those tests rather than running them twice. WebUI test CI does not build or start Docker images. Pull requests run a focused Windows native integration suite; the full Windows UI suite runs weekly and through `workflow_dispatch`, not on every merge. Manual runs also retain the Linux gate. The [workflow](.github/workflows/ci-a13n-harness-ui.yml) owns the native test selection and schedule. Windows smoke coverage does not replace full platform coverage: less common storage, plugin, and interaction regressions may only be detected by the full Windows run.

a13n-envd CI runs protocol verification and the native daemon platform matrix in parallel after path classification. The `a13n-envd checks` job requires every selected protocol, client, and daemon check to succeed; unselected checks may be skipped.

Select directories, files, or pytest node IDs with `PYTHON_TEST_DIRS`. Paths in the same package run in one pytest process; packages run separately in first-selected order, stopping on failure. Without a selection, all workspace suites run. Use `PYTHON_TEST_WORKERS` to override concurrency, including `0` for a small serial reproduction:

```bash
make test PYTHON_TEST_DIRS='packages/a13n-service/tests/storage/test_sql.py packages/a13n-service/tests/storage/test_s3_object_store.py' PYTHON_TEST_WORKERS=2
```

## Releases

Create a stable release with canonical version `X.Y.Z` or a release candidate with `X.Y.Z-rc.N`, where `N` is a positive integer without leading zeroes. The release tag must point to a commit whose required CI checks have passed. Keep source project versions at `0.0.0` and workspace dependencies unversioned. Do not commit release-only version bumps: each release workflow injects the tag version and owning `[tool.a13n.release-dependencies]` requirements into its known manifests and lock files in the ephemeral checkout, validates the resulting source, and then builds and publishes it. Release workflows do not repeat the full CI test or lint suites. Harness and Harness UI release workflows only prepare versions, build distributions, publish them, and create GitHub Releases; they do not run artifact checks, dependency compatibility matrices, or application/native-runtime smoke checks. Their validation belongs to local development and CI. The canonical cross-ecosystem RC identity is `X.Y.Z-rc.N`; Python package metadata and artifact names use its standard PEP 440 normalization, `X.Y.ZrcN`.

Release workflows reuse the existing GitHub Environments and their secrets; environment names are infrastructure identifiers, not package names. Keep these environment names when renaming components, and update their deployment tag policies to match the owning workflow's canonical release tag pattern instead of creating replacement environments. Preserve other environment protection settings.

When bootstrapping an empty registry namespace, publish dependency owners before their consumers:

1. publish the a13n-envd release so `a13n-envd` and `a13n-envd-client` exist;
2. publish the independent a13n Logging release so `a13n-logging` exists; this can run independently or in parallel with the a13n-envd release;
3. publish the Harness release group after compatible `a13n-envd-client` and `a13n-logging` releases are available;
4. publish Harness UI after compatible releases of its dependencies are available from PyPI;
5. publish a13n Service after its compatible library dependencies, including `a13n-logging`, are available.

The language SDKs can publish independently of that chain. A new TypeScript SDK npm name is the exception to ordinary tag-only release preparation: publish one reviewed RC locally under the npm `rc` tag, configure Trusted Publishing for the resulting package, and then publish the stable version through the release workflow. The SDK README owns the exact bootstrap and trust commands.

Harness releases use `release/a13n-harness-v<version>`. The workflow assigns exactly the same version to `a13n-environment`, `a13n-harness`, and `a13n-stream-protocol`, pins the published Harness dependency to that exact Environment version and the published Protocol dependency to that exact Harness version, builds all three wheels and source distributions, publishes them through the `harness-pypi` environment, and attaches all six artifacts to one GitHub Release.

Cross-group dependencies use the consuming manifest's `[tool.a13n.release-dependencies]` mapping, following the [dependency compatibility lines](spec/repository-model.md#dependency-compatibility-lines). Review these bounds when consuming newer APIs or crossing a breaking compatibility line; do not bump them for every dependency patch. Keep the three UI Harness-group requirements identical. Release preparation preserves exact same-version dependencies within the Harness group.

Harness UI releases use `release/a13n-harness-ui-v<version>`. The UI version advances independently. The workflow injects its bounded dependency requirements, builds the private WebUI into the sdist and wheel alongside the Python CLI and reusable App, publishes through `agent-ui-pypi`, and attaches the two artifacts to one GitHub Release. After Python publication, the workflow builds the GHCR image from that same UI wheel with its declared internal dependency bounds and locked third-party constraints. It does not rebuild browser assets, acquire a native runtime, or run application smoke tests in the image job. The GitHub Release follows both package and image publication. The private frontend has no independent npm publication.

Local EIP derives its canonical native version from installed `a13n-envd-client` metadata, not a packaged version file or a latest-release lookup. Automatic acquisition requires public access to the co-versioned daemon's archives; a private repository does not provide anonymous download access. This runtime prerequisite does not gate publication. Source client version `0.0.0`, missing metadata, or invalid metadata blocks only managed acquisition; source development can use an explicit validated executable override. Full Control does not require managed acquisition.

a13n Logging releases use `release/a13n-logging-v<version>` and advance independently of Service, Harness, and Harness UI. The workflow versions only `a13n-logging` and its root lock entry, builds its wheel and source distribution, publishes through the existing `foundation-pypi` environment using `PYPI_TOKEN`, and attaches both artifacts to one GitHub Release. The environment's deployment tag policy must allow `release/a13n-logging-v*` alongside `release/a13n-service-v*`; the token must permit publishing `a13n-logging`. No new environment is required.

a13n Service releases use `release/a13n-service-v<version>`. The workflow versions the repository root and `a13n-service`; it publishes only the `a13n-service` Python distribution through the `foundation-pypi` environment and publishes the native `linux/amd64` a13n-service image. It excludes and does not republish `a13n-logging`, Harness, Harness UI, or `a13n-envd-client`, and selects compatible published Harness libraries through its own dependency management.

a13n-envd releases use `release/a13n-envd-v<version>`. The workflow versions the Cargo workspace, `a13n-envd-client`, and their lock files with one canonical release identity. It builds the Python wheel and source distribution concurrently with Linux GNU and macOS tar archives plus Windows x64 and ARM64 ZIP archives. After every distribution and the crate package validate, the workflow creates the GitHub Release with the Python distributions, native archives, and `SHA256SUMS`, before publishing `a13n-envd-client` to PyPI with `PYPI_TOKEN` from the `agent-envd-client-pypi` environment. Crate publication uses `CARGO_REGISTRY_TOKEN` from `agent-envd-crates-io` and is independent of client publication. The sandbox image publishes only after both registry jobs succeed. Release retries use the published version to skip existing releases; they do not compare rebuilt native or crate bytes. Standalone installers own archive SHA verification using the published `SHA256SUMS` file. A visible GitHub Release proves native assets are available, not that registry or image publication completed. Successful completion of the overall workflow is the all-channel completion signal. Linux binaries target the current GitHub-hosted Ubuntu/glibc baseline; use the sandbox image when a fixed userspace is required.

SDK languages version and release independently from the standalone `sdk/` directory. Push `release/a13n/<language>/<version>`; the workflow versions that language's package metadata before building. Python publishes through `sdk-python-pypi`, Rust through `sdk-rust-crates-io`, and TypeScript through npm Trusted Publishing bound to `release-a13n-typescript.yml` and `sdk-typescript-npm`. A TypeScript RC publishes under the npm `rc` dist-tag rather than `latest`. Go has no embedded package version; its workflow validates the release version and creates the canonical `sdk/go/v<version>` module tag through `sdk-go-github`.

a13n Service CLI releases use `release/a13n-service-cli-v<version>`. The workflow injects the version into the independent `sdk/rust/a13n-service-cli` manifest and lock file, then builds Linux x86_64 and ARM64, macOS x86_64 and ARM64, and Windows x86_64 and ARM64 archives. Each archive contains the `a13n-service-cli` executable and `LICENSE`; the GitHub Release also includes `SHA256SUMS`. This channel publishes no crate, uses no registry credentials or GitHub Environment, and defines no mutable `latest` selector for stable or RC releases.

An RC publishes the same registry and downloadable artifact set as its corresponding stable channel and creates a GitHub prerelease. A stable release creates a normal GitHub Release. A release may add reviewed, human-written notes at `.github/release-notes/<component>/<version>.md`; see [the release-notes guide](.github/release-notes/README.md). The file is optional. Its content is prepended to generated notes for later releases and replaces the default initial sentence for the first release in a channel. The first RC for a target compares with the preceding release, later RCs compare with the preceding RC, and the final stable release compares with the preceding stable release so its notes cover the complete stable change set. Pull requests are categorized by the labels configured in `.github/release.yml`; use `breaking-change`, `enhancement`, `bug`, or `documentation`, and use `chore` or `skip-changelog` to omit a pull request. Direct commits remain visible through the generated Full Changelog comparison link but are not listed as categorized pull requests.

A push to `main` that changes a development image input builds and publishes only the affected images through [Development Images](.github/workflows/images.yml). Harness UI, Service, and sandbox use a single mutable `dev` tag, without per-build `sha-*` tags. Manual development-image publication is restricted to `main`. The OCI revision label retains the source commit; use an image digest when an exact deployment reference is needed. Development publication does not repeat image smoke tests. Harness UI image startup/restart checks run locally on demand. Overwriting `dev` does not guarantee removal of older GHCR package versions; no automatic registry deletion or retention job is configured. An RC image publishes only its exact `X.Y.Z-rc.N` tag and never modifies `latest`. A stable image publishes its exact `X.Y.Z` tag and advances `latest`. Development image jobs also never modify `latest`. Release image builds check `BUILD_VERSION` against the installed Python distribution or compiled daemon version, not only OCI labels. Service and Harness UI read their actual installed Python metadata at runtime; Service's optional deployment/Worker build label does not replace its OpenAPI or CLI package version. The sandbox daemon embeds Cargo's package version. Replace each placeholder registry secret in its scoped GitHub Environment before the corresponding release.

## Database Changes

Database changes follow the migration contract in [DEVELOPMENT.md](DEVELOPMENT.md#migrations).

Do not create Alembic revision files manually or autogenerate against an existing developer or shared database. Generate every `a13n-service` revision through the stable repository target:

```bash
make db-migrate msg="describe the schema change"
```

The target starts the local PostgreSQL service when needed, rebuilds schema history in a disposable database, autogenerates and formats the revision, and removes the temporary database. Review the generated migration rather than treating a clean model diff as proof of safety. The complete model-import and verification flow is documented in [packages/a13n-service/README.md](packages/a13n-service/README.md#add-an-orm-model).

A schema-change pull request must explain lock duration, scans or rewrites, rolling old/new compatibility, index strategy, bounded backfill, interruption and rerun behavior, and rollback or forward repair. Prefer additive expand-and-contract changes. The shared image auto-migrates `all` and `control` replicas under advisory locking; deployments with a dedicated migration job disable replica auto migration. Worker-only processes never migrate.

Run migration graph, clean-upgrade, schema-parity, and relevant PostgreSQL lock/concurrency tests. Record any required timeout override and its rationale in the pull request.

## Documentation Changes

- Keep each continuous Markdown paragraph on one source line; preserve semantic line breaks, separate list items, tables, and code blocks. The shared `.mdformat.toml` rule applies through the existing formatting hooks and `make lint`.
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

## Writing Issues and Pull Requests

Agents must follow the writing guidance in this section when drafting or updating Issue and PR bodies. Human contributors may adapt it at their discretion; this section adds no mandatory writing format for humans. Existing contribution and validation requirements still apply.

Start the main description with a concise explanation that a reader unfamiliar with the discussion can understand: the triggering scenario, the problem and its impact, and the desired or resulting behavior. Use the existing Issue problem field or PR Summary rather than adding a duplicate overview.

- In Issues, distinguish observed behavior, desired outcomes, and proposed solutions or open decisions. Label uncertain causes and proposed flows explicitly.
- In PRs, explain the final before/after behavior and material boundaries or compatibility effects. Keep the explanation and any visuals aligned with the final diff as scope changes. Avoid work logs and file inventories as substitutes for explaining the change.
- Choose the smallest view that clarifies the point: Mermaid for interactions, flows, or states; a short `diff` for a local change; pseudocode for logic; a shallow text tree for calls or file responsibilities; or a table for comparisons. Use a visual when it makes relationships or changes easier to understand; a short paragraph is enough for a simple correction.
- Place each visual beside the brief text it supports. Keep only the participants, steps, files, or states needed to understand the issue or change. Use concrete names and keep facts separate from proposals in diagrams as well as prose.
- Use GitHub-native Markdown and fenced code blocks, including `mermaid` for diagrams. Do not rely on HTML artifacts or external interactive pages for the explanation. GitHub supports Mermaid in [Issues and pull requests](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams).

Keep reproduction steps, constraints, implementation details, and validation in the relevant sections after the opening explanation. Scale detail to the change instead of filling every available visual format.

## Pull Requests

A pull request should:

1. Link the relevant issue when one exists.
2. Explain the motivation and material changes. For significant design changes, explain the current need for added mechanisms and the effect on understanding and maintenance cost, using the [code quality principles](DEVELOPMENT.md#code-quality-and-design). Routine changes need only a proportionate explanation.
3. Update affected specifications, docs, tests, and automation.
4. Report the exact validation commands and outcomes.
5. Request reviewers according to `MAINTAINERS.md`.

Keep commit messages in English. Do not add an agent as a co-author. If assistant attribution is required, use only:

```text
Assisted-by: NAME <email>
```
