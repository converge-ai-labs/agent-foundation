.DEFAULT_GOAL := help

A13N_SERVICE_IMAGE ?= a13n-service:local
SANDBOX_IMAGE ?= a13n-sandbox:local
A13N_HARNESS_UI_IMAGE ?= a13n-harness-ui:local
EXAMPLE_DIRS := examples/agent-app examples/environment-provider examples/plugins
PYTHON_TEST_DIRS ?=
PYTHON_TEST_WORKERS ?=
SERVICE_CONFIG ?= dev/service/local.toml
HARNESS_ENV ?= dev/harness/.env
HARNESS_UI_ENV ?= dev/harness-ui/.env
STATE ?=
SERVICE_DEV = uv run --locked python -m dev.service --config "$(SERVICE_CONFIG)"
CHECK_JOBS ?= 4
CHECK_TARGETS := \
	lint \
	typecheck \
	examples-check \
	frontend-check \
	rust-check \
	sdk-python-check \
	sdk-go-check \
	sdk-rust-check \
	sdk-typescript-check \
	a13n-service-cli-check

.PHONY: install
install: ## Install locked dependencies and Git hooks
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	@command -v npm >/dev/null || { echo "Node.js and npm are required: https://nodejs.org/"; exit 1; }
	@command -v pnpm >/dev/null || { echo "pnpm is required: https://pnpm.io/installation"; exit 1; }
	@command -v go >/dev/null || { echo "Go is required: https://go.dev/"; exit 1; }
	@command -v cargo >/dev/null || { echo "Rust is required: https://rustup.rs/"; exit 1; }
	@echo "Synchronizing the Python workspace"
	@uv sync --locked --all-packages
	@echo "Synchronizing the standalone Python SDK"
	@uv sync --project sdk/python --locked
	@echo "Installing frontend workspace dependencies"
	@pnpm --dir frontend install --frozen-lockfile
	@echo "Installing TypeScript SDK dependencies"
	@npm --prefix sdk/typescript ci
	@echo "Installing pre-commit hooks"
	@uv run --locked pre-commit install --install-hooks

.PHONY: sync
sync: ## Synchronize the locked Python workspace
	@uv sync --quiet --locked --all-packages

.PHONY: examples-sync
examples-sync: ## Synchronize every independent example project
	@for directory in $(EXAMPLE_DIRS); do uv sync --quiet --project "$$directory" --locked || exit $$?; done

.PHONY: examples-lock-check
examples-lock-check: ## Verify every independent example lock file
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && uv lock --check) || exit $$?; done

.PHONY: examples-format-check
examples-format-check: examples-sync ## Check example lint and formatting
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && uv run --locked ruff check --no-fix .) || exit $$?; done
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && uv run --locked ruff format --check .) || exit $$?; done

.PHONY: examples-typecheck
examples-typecheck: examples-sync ## Type-check example sources and tests
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && uv run --locked pyright) || exit $$?; done

.PHONY: examples-test
examples-test: examples-sync ## Run focused example tests
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && uv run --locked pytest) || exit $$?; done

.PHONY: examples-smoke
examples-smoke: examples-sync ## Run every offline example path
	@(cd examples/plugins && uv run --locked plugin-example-capability-agent-spec)
	@(cd examples/plugins && uv run --locked plugin-example-capability-code)
	@(cd examples/plugins && uv run --locked plugin-example-environment-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-environment-code)
	@(cd examples/plugins && uv run --locked plugin-example-environment-extension-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-environment-extension-code)
	@(cd examples/plugins && uv run --locked plugin-example-harness-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-harness-code)
	@workspace_dir=$$(mktemp -d); trap 'rm -rf "$$workspace_dir"' EXIT; \
		(cd examples/environment-provider && uv run --locked environment-provider-example direct-local --workspace "$$workspace_dir")
	@state_dir=$$(mktemp -d); trap 'rm -rf "$$state_dir"' EXIT; \
		(cd examples/agent-app && uv run --locked agent-app-example --state "$$state_dir/state.json" "first turn" "second turn") && \
		(cd examples/agent-app && uv run --locked agent-app-example --state "$$state_dir/state.json" "turn after restart")

.PHONY: examples-build
examples-build: examples-sync ## Build every example distribution
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && rm -rf dist && uv build) || exit $$?; done

.PHONY: examples-check
examples-check: examples-lock-check examples-format-check examples-typecheck ## Run example lint and type checks

.PHONY: examples-check-all
examples-check-all: examples-check examples-test examples-smoke examples-build ## Run the complete examples gate

.PHONY: setup
setup: sync ## Prepare local PostgreSQL, Redis, Langfuse and the Service schema
	@$(SERVICE_DEV) setup

.PHONY: dev
dev: setup frontend-sync sdk-typescript-build ## Upgrade the schema and run a13n Service and Console
	@bash scripts/dev.sh "$(SERVICE_CONFIG)"

# Initialize only missing files; templates changing must never replace private settings.
# Resolve the template beside the selected file, including explicit path overrides.
define ensure-env
	@env_file="$(1)"; template="$(1).example"; \
	if [ -f "$$env_file" ]; then exit 0; fi; \
	if [ ! -f "$$template" ]; then \
		printf 'Missing environment file: %s\nCreate it or provide a sibling template: %s\n' "$$env_file" "$$template" >&2; \
		exit 2; \
	fi; \
	cp -n "$$template" "$$env_file" || exit $$?; \
	printf 'Initialized %s from %s (existing files are never overwritten).\n' "$$env_file" "$$template"; \
	printf 'Local tracing uses make langfuse-up; review this file before using a remote backend.\n'
endef

.PHONY: env-init harness-env harness-ui-env
env-init: harness-env harness-ui-env ## Initialize missing Harness and Harness UI .env files without overwriting settings

harness-env:
	$(call ensure-env,$(HARNESS_ENV))

harness-ui-env:
	$(call ensure-env,$(HARNESS_UI_ENV))

.PHONY: cli webui harness-dev harness-ui-smoke
cli: harness-ui-env ## Run Harness UI with workspace-local config/data (CLI_ARGS forwards options)
	@uv run --locked --env-file "$(HARNESS_UI_ENV)" python -m dev.harness-ui.cli $(CLI_ARGS)

webui: harness-ui-env a13n-harness-ui-assets ## Build and start WebUI with a generated login link (WEBUI_ARGS forwards options)
	@uv run --locked --env-file "$(HARNESS_UI_ENV)" python -m dev.harness-ui.cli $(CLI_ARGS) webui $(WEBUI_ARGS)

harness-dev: harness-env ## Run SDK observation scenarios; initialize .env if missing (HARNESS_ARGS selects a scenario)
	@uv run --locked --env-file "$(HARNESS_ENV)" opentelemetry-instrument python dev/observation-demo/agent.py $(HARNESS_ARGS)

harness-ui-smoke: harness-ui-env ## Exercise HarnessUiApp with a scripted model; initialize .env if missing
	@uv run --locked --env-file "$(HARNESS_UI_ENV)" python -m dev.harness-ui.smoke

.PHONY: dev-down
.PHONY: live-test-init live-test-setup live-test-control live-test-worker live-test live-test-local live-test-check live-test-auth-control live-test-round-two live-test-management
LIVE_TEST_RUN = uv run --locked $(if $(wildcard .env),--env-file .env,)

live-test-auth-control: sync ## Run ordinary local Control settings with the private test authenticator
	@$(LIVE_TEST_RUN) python -m dev.live_tests.manage authenticated-control

live-test-init: sync ## Seed an isolated local live-test identity (requires migrated database)
	@$(LIVE_TEST_RUN) python -m dev.live_tests.manage init

live-test-control: sync ## Run the local live-test Control with explicit test authentication
	@$(LIVE_TEST_RUN) python -m dev.live_tests.manage control

live-test-worker: sync ## Run the separate local live-test Worker
	@$(LIVE_TEST_RUN) python -m dev.live_tests.manage worker

live-test-setup: sync ## Create live-test Model, Environment, Plugin, and Agents through Control HTTP
	@$(LIVE_TEST_RUN) python -m dev.live_tests.manage setup

live-test: sync ## Run opt-in local HTTP journeys (LIVE_TEST_ARGS="-k basic" selects cases)
	@$(LIVE_TEST_RUN) python -m pytest dev/live_tests --live -v --tb=short -o log_cli=true -o log_cli_level=INFO $(LIVE_TEST_ARGS)

live-test-local: sync ## Run first-round HTTP journeys with owned Docker dependencies and service processes
	@uv run --locked python -m dev.live_tests.isolated $(LIVE_TEST_ARGS)

.PHONY: live-test-ci live-test-ci-environment-build
live-test-ci: sync ## Run reviewed live journeys (suite=smoke|core|functional|control|fork-queue|run-faults|environment-native|environment-service; LIVE_TEST_ARGS selects infrastructure)
	@uv run --locked python -m dev.live_tests.ci $(suite) $(LIVE_TEST_ARGS)

live-test-ci-environment-build: image-sandbox ## Build the native daemon and fixture images for the manual Environment matrices
	@cargo build --locked --package a13n-envd
	@docker build -f dev/live_tests/environment/file_resources.Dockerfile --build-arg SANDBOX_IMAGE="$(SANDBOX_IMAGE)" --target worker -t a13n-file-resources:local .
	@docker build -f dev/live_tests/environment/file_resources.Dockerfile --build-arg SANDBOX_IMAGE="$(SANDBOX_IMAGE)" --target docker-sandbox -t a13n-file-resources:docker .

live-test-round-two: sync ## Run isolated HTTP fault/recovery journeys with Docker dependencies
	@$(LIVE_TEST_RUN) python -m pytest dev/live_tests --live-round-two -v --tb=short -o log_cli=true -o log_cli_level=INFO $(LIVE_TEST_ARGS)

.PHONY: live-test-performance
live-test-performance: sync ## Measure concurrent PG/S3 calls and bounded Service operations
	@uv run --locked python -m pytest dev/live_tests/performance/test_operations.py --live-performance -n 0 -v --tb=short -o log_cli=true -o log_cli_level=INFO --log-disable=httpx2 $(LIVE_TEST_ARGS)

.PHONY: live-test-session live-test-contention live-test-s3
live-test-session: sync ## Verify real sequential history and built-in compaction without latency gates
	@uv run --locked python -m pytest dev/live_tests/harness_integration/test_36_long_session.py --live-long-session -v --tb=short -o log_cli=true -o log_cli_level=INFO --log-disable=httpx2 $(LIVE_TEST_ARGS)

live-test-contention: sync ## Exercise multiwriter Run, Attempt, inbox and queue races
	@uv run --locked python -m pytest dev/live_tests/control/test_57_admission_contention.py dev/live_tests/control/test_58_inbox_contention.py dev/live_tests/control/test_59_queue_contention.py dev/live_tests/control/test_61_attempt_control_contention.py dev/live_tests/run_recovery/test_60_attempt_contention.py --live-round-two -v --tb=short -o log_cli=true -o log_cli_level=INFO --log-disable=httpx2 $(LIVE_TEST_ARGS)

live-test-s3: sync ## Measure single S3 requests (skips without private Provider [s3] settings)
	@uv run --locked python -m pytest dev/live_tests/performance/test_s3_benchmark.py --live-performance -n 0 -v --tb=short -o log_cli=true -o log_cli_level=INFO --log-disable=httpx2 $(LIVE_TEST_ARGS)

.PHONY: live-test-report
live-test-report: ## Render selected performance artifacts as one scenario-based table
	@uv run --locked python -m dev.live_tests.performance.scenario_report $(REPORT_ARGS)

live-test-management: sync ## Run isolated Service/Harness management journeys with Docker dependencies
	@$(LIVE_TEST_RUN) python -m pytest dev/live_tests --live-management -v --tb=short -o log_cli=true -o log_cli_level=INFO $(LIVE_TEST_ARGS)

.PHONY: live-test-plugin-image
live-test-plugin-image: sync ## Build a custom plugin wheel/image and exercise the production Worker through HTTP
	@uv run --locked python -m pytest dev/live_tests/harness_integration/test_19_plugin_image.py --live-plugin-image -v --tb=short -o log_cli=true -o log_cli_level=INFO --log-disable=httpx2 $(LIVE_TEST_ARGS)

.PHONY: live-test-providers
live-test-providers: sync ## Run optional configured real Providers in disposable local labs
	@$(LIVE_TEST_RUN) python -m pytest dev/live_tests/providers/test_31_real_providers.py --live-providers -v --tb=short -o log_cli=true -o log_cli_level=INFO $(LIVE_TEST_ARGS)

live-test-check: sync ## Validate live-test support without contacting services
	@uv run --locked ruff check --no-fix dev/live_tests
	@uv run --locked ruff format --check dev/live_tests
	@uv run --locked mdformat --check --number dev/live_tests/README.md dev/live_tests/performance/REPORTING.md
	@uv run --locked python -m pytest dev/live_tests -q

dev-down: ## Stop local Service and Langfuse infrastructure, preserving all data
	@$(SERVICE_DEV) stop

.PHONY: langfuse-up langfuse-down langfuse-test langfuse-reset
langfuse-up: sync ## Start and authenticate local Langfuse using SERVICE_CONFIG
	@$(SERVICE_DEV) langfuse up

langfuse-down: sync ## Stop local Langfuse while preserving its data
	@$(SERVICE_DEV) langfuse down

langfuse-test: sync ## Verify Service OTLP write and Trace Query against local Langfuse v4
	@$(SERVICE_DEV) langfuse test

langfuse-reset: sync ## Stop local Langfuse and remove only its data
	@$(SERVICE_DEV) langfuse reset

.PHONY: a13n-harness-ui-skills
a13n-harness-ui-skills: sync ## Generate the bundled configuration Skill and documentation navigation
	@uv run --locked python packages/a13n-harness-ui/build_skills.py

.PHONY: a13n-harness-ui
a13n-harness-ui: a13n-harness-ui-skills ## Run Harness UI without dev .env or update checks (CLI_ARGS forwards options)
	@uv run --locked a13n-harness-ui --no-update-check $(CLI_ARGS)

.PHONY: a13n-harness-ui-db-migrate
a13n-harness-ui-db-migrate: sync ## Generate a Harness UI SQLite migration against a disposable database
	@test -n "$(msg)" || { echo 'msg is required: make a13n-harness-ui-db-migrate msg="description"'; exit 2; }
	@uv run --locked python -m a13n_harness_ui.storage.migrations.generate "$(msg)"

.PHONY: format
format: sync frontend-sync sdk-python-sync sdk-typescript-sync ## Format repository and standalone SDK sources
	@run_formatters() { \
		formatter_status=0; \
		for hook in end-of-file-fixer trailing-whitespace mdformat ruff-format; do \
			git ls-files --cached --others --exclude-standard -z | \
				xargs -0 uv run --locked pre-commit run "$$hook" --files || formatter_status=$$?; \
		done; \
		return "$$formatter_status"; \
	}; \
	run_formatters || run_formatters
	@files="$$(find sdk/go -type f -name '*.go')"; gofmt -w $$files
	@cargo fmt --all
	@(cd sdk/rust && cargo fmt)
	@(cd sdk/rust/a13n-service-cli && cargo fmt)
	@pnpm --dir frontend run format
	@npm --prefix sdk/typescript run format

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/a13n-envd-client && uv run --locked deptry a13n_envd_client)
	@(cd packages/a13n-environment && uv run --locked deptry a13n_environment)
	@(cd packages/a13n-harness && uv run --locked deptry a13n_harness)
	@(cd packages/a13n-stream-protocol && uv run --locked deptry a13n_stream_protocol)
	@(cd packages/a13n-harness-ui && uv run --locked deptry a13n_harness_ui)
	@(cd packages/a13n-logging && uv run --locked deptry a13n_logging)
	@(cd packages/a13n-service && uv run --locked deptry a13n_service)

.PHONY: lint
lint: sync deps-check ## Run non-mutating repository lint checks
	@uv lock --check
	@for hook in check-added-large-files check-case-conflict check-merge-conflict check-json check-toml check-yaml; do \
		git ls-files --cached --others --exclude-standard -z | \
			xargs -0 uv run --locked pre-commit run "$$hook" --files || exit $$?; \
	done
	@git ls-files --cached --others --exclude-standard -z -- '*.md' | xargs -0 uv run --locked mdformat --check --number
	@uv run --locked ruff check --no-fix packages scripts
	@uv run --locked ruff format --check packages scripts

.PHONY: typecheck
typecheck: sync ## Type-check Python package sources
	@uv run --locked pyright

.PHONY: docs-check
docs-check: ## Verify that docs contains only Markdown source files
	@files="$$(find docs -type f ! -name '*.md' -print)"; \
		test -z "$$files" || { echo "Only Markdown files are allowed under docs/:"; echo "$$files"; exit 1; }

.PHONY: docs-serve
docs-serve: sync ## Serve the documentation site locally
	@uv run --locked mkdocs serve

.PHONY: docs-build
docs-build: sync docs-check ## Build the documentation site in strict mode
	@uv run --locked mkdocs build --strict

.PHONY: test
test: a13n-harness-ui-skills ## Run Python workspace tests
	@uv run --locked python -m scripts.run_python_tests $(if $(PYTHON_TEST_WORKERS),--workers $(PYTHON_TEST_WORKERS)) $(PYTHON_TEST_DIRS)

.PHONY: eip-generate
eip-generate: sync ## Generate checked EIP descriptor, Python surface, and inspection artifacts
	@uv run --locked python -m scripts.eip_codegen generate

.PHONY: eip-verify
eip-verify: sync ## Verify checked EIP artifacts without modifying the repository
	@uv run --locked python -m scripts.eip_codegen verify

.PHONY: eip-integration-test
eip-integration-test: sync ## Run EIP generation, runtime, cross-language, and wire-model integration tests
	@cargo build --locked --package a13n-envd
	@A13N_ENVD_TEST_BINARY="$(CURDIR)/target/debug/a13n-envd" A13N_ENVD_EXECUTABLE="$(CURDIR)/target/debug/a13n-envd" uv run --locked python -m pytest scripts/tests/test_eip_codegen.py packages/a13n-envd-client/tests/eip packages/a13n-environment/tests/test_local_envd.py packages/a13n-environment/tests/test_remote_envd.py packages/a13n-environment/tests/test_remote_envd_e2e.py
	@A13N_ENVD_TEST_BINARY="$(CURDIR)/target/debug/a13n-envd" uv run --project examples/environment-provider --locked python -m pytest examples/environment-provider/tests/test_remote.py
	@uv run --locked pyright packages/a13n-envd-client/a13n_envd_client packages/a13n-environment/a13n_environment

.PHONY: eip-test
eip-test: eip-integration-test ## Run complete EIP integration and daemon tests
	@cargo test --locked --package a13n-envd

.PHONY: local-envd-test
local-envd-test: sync ## Build a13n-envd and run Local Envd provider tests
	@cargo build --locked --package a13n-envd
	@set -a; \
	if [ -f "$(CURDIR)/.env" ]; then . "$(CURDIR)/.env"; fi; \
	set +a; \
	A13N_ENVD_EXECUTABLE="$${A13N_ENVD_EXECUTABLE:-$(CURDIR)/target/debug/a13n-envd}"; \
	export A13N_ENVD_EXECUTABLE; \
	uv run --locked python -m pytest packages/a13n-environment/tests/test_local_envd.py

.PHONY: e2b-provider-test
e2b-provider-test: sync ## Run native E2B unit and opt-in live integration tests
	@uv run --locked pytest -q packages/a13n-environment/tests/test_e2b*.py packages/a13n-harness/tests/test_e2b_environment_live.py

.PHONY: docker-provider-test
docker-provider-test: sync ## Run Docker Provider tests
	@uv run --locked python -m pytest packages/a13n-environment/tests/test_docker.py

.PHONY: eip-check
eip-check: eip-verify eip-test ## Run the complete EIP protocol gate

.PHONY: python-build
python-build: sync a13n-harness-ui-assets ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: a13n-harness-python-build
a13n-harness-python-build: ## Build the prepared Harness release-group distributions
	@rm -rf dist
	@for package in a13n-environment a13n-harness a13n-stream-protocol; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

.PHONY: a13n-harness-dist-check
a13n-harness-dist-check: ## Verify isolated same-version Harness wheels
	@uv run --no-project python scripts/check-a13n-distributions.py dist

.PHONY: a13n-harness-release-build
a13n-harness-release-build: a13n-harness-python-build ## Build the prepared Harness release group

.PHONY: a13n-harness-ui-build
a13n-harness-ui-build: sync a13n-harness-ui-assets ## Build Harness UI for repository development
	@rm -rf dist
	@uv build --package a13n-harness-ui --out-dir dist
	@uv run --locked python scripts/check-a13n-harness-ui-distribution.py dist --rebuild-wheel

.PHONY: a13n-harness-ui-release-build
a13n-harness-ui-release-build: ## Build Harness UI from prepared assets and release metadata
	@rm -rf dist
	@uv build --package a13n-harness-ui --out-dir dist

.PHONY: a13n-logging-python-build
a13n-logging-python-build: ## Build only the logging Python distributions
	@rm -rf dist
	@uv build --package a13n-logging --out-dir dist

.PHONY: a13n-service-python-build
a13n-service-python-build: sync ## Build only the Service Python distributions
	@rm -rf dist
	@uv build --package a13n-service --out-dir dist

.PHONY: a13n-envd-client-build
a13n-envd-client-build: sync ## Build the a13n-envd client Python distributions
	@rm -rf dist
	@uv build --package a13n-envd-client --out-dir dist

.PHONY: rust-format-check
rust-format-check: ## Check Rust formatting
	@cargo fmt --all -- --check

.PHONY: rust-lint
rust-lint: ## Run Clippy with warnings denied
	@cargo clippy --workspace --all-targets --all-features --locked -- -D warnings

.PHONY: rust-test
rust-test: ## Run Rust workspace tests
	@cargo test --workspace --all-features --locked

.PHONY: rust-build
rust-build: ## Build the Rust workspace
	@cargo build --workspace --all-features --locked

.PHONY: rust-package
rust-package: ## Verify the a13n-envd crates.io package
	@cargo package --locked --allow-dirty --package a13n-envd

.PHONY: rust-check
rust-check: rust-format-check rust-lint ## Run Rust workspace formatting and lint checks

.PHONY: rust-check-all
rust-check-all: rust-check rust-test rust-build rust-package ## Run the complete Rust workspace gate

.PHONY: sdk-python-isolation-check
sdk-python-isolation-check: ## Verify the Python SDK is excluded from the root workspace
	@python3 -c 'import tomllib; from pathlib import Path; data = tomllib.loads(Path("pyproject.toml").read_text()); workspace = data["tool"]["uv"]["workspace"]; members = {path.resolve() for pattern in workspace["members"] for path in Path().glob(pattern)}; excluded = {path.resolve() for pattern in workspace.get("exclude", []) for path in Path().glob(pattern)}; assert Path("sdk/python").resolve() not in members - excluded, "sdk/python must remain outside the root uv workspace"'

.PHONY: sdk-python-sync
sdk-python-sync: ## Synchronize the standalone Python SDK
	@uv sync --quiet --project sdk/python --locked

.PHONY: sdk-python-format-check
sdk-python-format-check: sdk-python-sync ## Check Python SDK lint and formatting
	@(cd sdk/python && uv run --locked ruff check --no-fix .)
	@(cd sdk/python && uv run --locked ruff format --check .)

.PHONY: sdk-python-typecheck
sdk-python-typecheck: sdk-python-sync ## Type-check the Python SDK
	@(cd sdk/python && uv run --locked pyright)

.PHONY: sdk-python-test
sdk-python-test: sdk-python-sync ## Run Python SDK tests
	@(cd sdk/python && uv run --locked python -m pytest)

.PHONY: sdk-python-build
sdk-python-build: sdk-python-sync ## Build the Python SDK distributions
	@(cd sdk/python && rm -rf dist && uv build --no-build-isolation)

.PHONY: sdk-python-check
sdk-python-check: sdk-python-isolation-check sdk-python-format-check sdk-python-typecheck ## Run Python SDK lint and type checks

.PHONY: sdk-python-check-all
sdk-python-check-all: sdk-python-check sdk-python-test sdk-python-build ## Run the complete Python SDK gate

.PHONY: sdk-go-format-check
sdk-go-format-check: ## Check Go SDK formatting
	@files="$$(find sdk/go -type f -name '*.go')"; \
		unformatted="$$(gofmt -l $$files)"; \
		test -z "$$unformatted" || { echo "$$unformatted"; gofmt -d $$files; exit 1; }

.PHONY: sdk-go-vet
sdk-go-vet: ## Run Go SDK static analysis
	@(cd sdk/go && go vet ./...)

.PHONY: sdk-go-test
sdk-go-test: ## Run Go SDK tests
	@(cd sdk/go && go test ./...)

.PHONY: sdk-go-build
sdk-go-build: ## Build the Go SDK
	@(cd sdk/go && go build ./...)

.PHONY: sdk-go-check
sdk-go-check: sdk-go-format-check sdk-go-vet ## Run Go SDK formatting and static analysis

.PHONY: sdk-go-check-all
sdk-go-check-all: sdk-go-check sdk-go-test sdk-go-build ## Run the complete Go SDK gate

.PHONY: sdk-rust-isolation-check
sdk-rust-isolation-check: ## Verify the Rust SDK is excluded from the root workspace
	@cargo metadata --locked --no-deps --format-version 1 | python3 -c 'import json, sys; from pathlib import Path; sdk = Path("sdk/rust/Cargo.toml").resolve(); manifests = {Path(item["manifest_path"]).resolve() for item in json.load(sys.stdin)["packages"]}; assert sdk not in manifests, "sdk/rust must remain outside the root Cargo workspace"'

.PHONY: sdk-rust-format-check
sdk-rust-format-check: ## Check Rust SDK formatting
	@(cd sdk/rust && cargo fmt -- --check)

.PHONY: sdk-rust-lint
sdk-rust-lint: ## Run Rust SDK Clippy with warnings denied
	@(cd sdk/rust && cargo clippy --all-targets --all-features --locked -- -D warnings)

.PHONY: sdk-rust-test
sdk-rust-test: ## Run Rust SDK tests
	@(cd sdk/rust && cargo test --all-features --locked)

.PHONY: sdk-rust-build
sdk-rust-build: ## Build the Rust SDK
	@(cd sdk/rust && cargo build --all-features --locked)

.PHONY: sdk-rust-package
sdk-rust-package: ## Verify the Rust SDK crates.io package
	@(cd sdk/rust && cargo package --locked --allow-dirty)

.PHONY: sdk-rust-check
sdk-rust-check: sdk-rust-isolation-check sdk-rust-format-check sdk-rust-lint ## Run Rust SDK formatting and lint checks

.PHONY: sdk-rust-check-all
sdk-rust-check-all: sdk-rust-check sdk-rust-test sdk-rust-build sdk-rust-package ## Run the complete Rust SDK gate

.PHONY: a13n-service-cli-isolation-check
a13n-service-cli-isolation-check: ## Verify the a13n Service CLI remains an independent Cargo project
	@cargo metadata --locked --no-deps --format-version 1 | python3 -c 'import json, sys; from pathlib import Path; cli = Path("sdk/rust/a13n-service-cli/Cargo.toml").resolve(); manifests = {Path(item["manifest_path"]).resolve() for item in json.load(sys.stdin)["packages"]}; assert cli not in manifests, "a13n Service CLI must remain outside the root Cargo workspace"'
	@cargo metadata --locked --no-deps --manifest-path sdk/rust/a13n-service-cli/Cargo.toml --format-version 1 | python3 -c 'import json, sys; from pathlib import Path; cli = Path("sdk/rust/a13n-service-cli/Cargo.toml").resolve(); data = json.load(sys.stdin); packages = data["packages"]; manifests = {Path(item["manifest_path"]).resolve() for item in packages}; member_ids = set(data["workspace_members"]); package_ids = {item["id"] for item in packages}; assert Path(data["workspace_root"]).resolve() == cli.parent, "a13n Service CLI must own its Cargo workspace"; assert manifests == {cli} and member_ids == package_ids, "a13n Service CLI workspace must contain only the CLI package"'
	@cargo package --locked --allow-dirty --manifest-path sdk/rust/Cargo.toml --list | python3 -c 'import sys; paths = sys.stdin.read().splitlines(); assert not any(path == "a13n-service-cli" or path.startswith("a13n-service-cli/") for path in paths), "Rust SDK source package must exclude the a13n Service CLI"'

.PHONY: a13n-service-cli-format-check
a13n-service-cli-format-check: ## Check a13n Service CLI formatting
	@(cd sdk/rust/a13n-service-cli && cargo fmt -- --check)

.PHONY: a13n-service-cli-lint
a13n-service-cli-lint: ## Run a13n Service CLI Clippy with warnings denied
	@(cd sdk/rust/a13n-service-cli && cargo clippy --all-targets --all-features --locked -- -D warnings)

.PHONY: a13n-service-cli-test
a13n-service-cli-test: ## Run a13n Service CLI tests
	@(cd sdk/rust/a13n-service-cli && cargo test --all-features --locked)

.PHONY: a13n-service-cli-build
a13n-service-cli-build: ## Build the a13n Service CLI
	@(cd sdk/rust/a13n-service-cli && cargo build --all-features --locked)

.PHONY: a13n-service-cli-check
a13n-service-cli-check: a13n-service-cli-isolation-check a13n-service-cli-format-check a13n-service-cli-lint ## Run a13n Service CLI formatting and lint checks

.PHONY: a13n-service-cli-check-all
a13n-service-cli-check-all: a13n-service-cli-check a13n-service-cli-test a13n-service-cli-build ## Run the complete a13n Service CLI gate

.PHONY: frontend-sync
frontend-sync: ## Install locked frontend workspace dependencies
	@pnpm --dir frontend install --frozen-lockfile

.PHONY: frontend-check
frontend-check: frontend-sync sdk-typescript-build ## Check frontend formatting, types, and API contract
	@pnpm --dir frontend run check

.PHONY: frontend-test
frontend-test: frontend-sync sdk-typescript-build ## Run frontend unit and interaction tests
	@pnpm --dir frontend run test

.PHONY: frontend-build
frontend-build: a13n-ui-build a13n-console-build a13n-harness-ui-webui-build ## Build frontend applications and the UI showcase

.PHONY: frontend-check-all
frontend-check-all: frontend-check frontend-test frontend-build ## Run the complete frontend gate

.PHONY: a13n-ui-build
a13n-ui-build: frontend-sync ## Build the shared UI showcase
	@pnpm --dir frontend --filter a13n-ui run build

.PHONY: a13n-console-build
a13n-console-build: frontend-sync sdk-typescript-build ## Build Console production assets
	@pnpm --dir frontend --filter a13n-console run build

.PHONY: a13n-harness-ui-webui-build
a13n-harness-ui-webui-build: frontend-sync ## Build Harness UI WebUI production assets
	@pnpm --dir frontend --filter a13n-harness-ui-webui run build

.PHONY: a13n-harness-ui-assets
a13n-harness-ui-assets: a13n-harness-ui-skills a13n-harness-ui-webui-build ## Prepare generated Harness UI WebUI files for Python packaging
	@uv run --locked python scripts/prepare-a13n-harness-ui-assets.py

sdk/typescript/node_modules/.package-lock.json: sdk/typescript/package.json sdk/typescript/package-lock.json
	@npm --prefix sdk/typescript ci

.PHONY: sdk-typescript-sync
sdk-typescript-sync: sdk/typescript/node_modules/.package-lock.json ## Install locked TypeScript SDK dependencies

.PHONY: sdk-generate
sdk-generate: sync sdk-typescript-sync ## Regenerate all SDKs from the live Service OpenAPI contract
	@uv run --locked python sdk/codegen/generate.py

.PHONY: sdk-generated-check
sdk-generated-check: sync sdk-typescript-sync ## Verify shared OpenAPI and all generated SDK files without changing them
	@uv run --locked python sdk/codegen/generate.py --check

.PHONY: sdk-typescript-generate
sdk-typescript-generate: sdk-generate ## Regenerate shared SDK contracts (compatibility alias)

.PHONY: sdk-typescript-contract-check
sdk-typescript-contract-check: sync sdk-typescript-sync ## Check Native TypeScript contract drift
	@uv run python scripts/export-a13n-service-openapi.py --check
	@node sdk/typescript/generate.mjs --check

.PHONY: sdk-typescript-build
sdk-typescript-build: sdk-typescript-sync ## Build the TypeScript SDK
	@npm --prefix sdk/typescript run build

.PHONY: sdk-typescript-check
sdk-typescript-check: sdk-typescript-sync ## Run TypeScript SDK formatting and type checks
	@npm --prefix sdk/typescript run check

.PHONY: sdk-typescript-check-all
sdk-typescript-check-all: sdk-typescript-sync ## Run the complete TypeScript SDK gate
	@npm --prefix sdk/typescript run check:all

.PHONY: sdk-build
sdk-build: sdk-python-build sdk-go-build sdk-rust-build sdk-typescript-build ## Build all standalone SDKs

.PHONY: sdk-check
sdk-check: sdk-python-check sdk-go-check sdk-rust-check sdk-typescript-check ## Run all standalone SDK lint and type checks

.PHONY: sdk-check-all
sdk-check-all: sdk-generated-check sdk-python-check-all sdk-go-check-all sdk-rust-check-all sdk-typescript-check-all ## Run all complete standalone SDK gates

.PHONY: build
build: frontend-build python-build rust-build sdk-build a13n-service-cli-build ## Build all workspace, application, SDK, and CLI artifacts

.PHONY: db-migrate
db-migrate: sync ## Generate a migration (usage: make db-migrate msg="description")
	@bash dev/service/db-migrate.sh "$(msg)"

.PHONY: db-upgrade
db-upgrade: sync ## Upgrade the local a13n-service database to all heads
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" db upgrade

.PHONY: db-downgrade
db-downgrade: sync ## Downgrade the local database by one reviewed revision
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" db downgrade

.PHONY: db-current
db-current: sync ## Show the current a13n-service database revision
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" db current

.PHONY: db-check
db-check: sync ## Fail unless the a13n-service database is at all heads
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" db current --check-heads

.PHONY: db-history
db-history: sync ## Show a13n-service migration history
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" db history

.PHONY: release-check
release-check: ## Validate a component version (component=a13n-harness|a13n-harness-ui|a13n-logging|a13n-service|a13n-envd|a13n-service-cli|a13n-<language> version=X.Y.Z or X.Y.Z-rc.N)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-a13n-service
image-a13n-service: ## Build the local a13n-service container image
	@docker build -f deploy/containers/a13n-service/Dockerfile -t "$(A13N_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with a13n-envd
	@docker build -f deploy/containers/sandbox/Dockerfile -t "$(SANDBOX_IMAGE)" .

.PHONY: a13n-harness-ui-image-context
a13n-harness-ui-image-context: a13n-harness-ui-assets ## Stage source wheels and locked constraints for the UI image
	@uv run --locked python scripts/prepare_harness_ui_image.py

.PHONY: image-a13n-harness-ui
image-a13n-harness-ui: a13n-harness-ui-image-context ## Build the local packaged Harness UI image
	@docker build -f deploy/containers/a13n-harness-ui/Dockerfile -t "$(A13N_HARNESS_UI_IMAGE)" dist/a13n-harness-ui-image

.PHONY: images
images: image-a13n-service image-sandbox image-a13n-harness-ui ## Build all local container images

.PHONY: image-check-a13n-harness-ui
image-check-a13n-harness-ui: ## Check an existing UI image locally; not a CI or release prerequisite
	@uv run --locked python scripts/check_harness_ui_image.py "$(A13N_HARNESS_UI_IMAGE)"

.PHONY: image-check-a13n-service
image-check-a13n-service: ## Smoke-check the existing a13n-service container image
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(A13N_SERVICE_IMAGE)")" = "app"
	@docker run --rm --entrypoint sh "$(A13N_SERVICE_IMAGE)" -c '! command -v node'

.PHONY: image-check-sandbox
image-check-sandbox: ## Smoke-check the existing sandbox container image
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(SANDBOX_IMAGE)")" = "sandbox"
	@docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(SANDBOX_IMAGE)" | grep -qx 'A13N_ENVD_EXECUTION_ISOLATION=disabled'
	@docker run --rm \
		--env A13N_ENVD_ENVIRONMENT_ID=image-check \
		--entrypoint a13n-envd "$(SANDBOX_IMAGE)"

.PHONY: image-check
image-check: images ## Build and smoke-check all container images
	@$(MAKE) --no-print-directory image-check-a13n-service image-check-sandbox image-check-a13n-harness-ui

.PHONY: python-check
python-check: lint typecheck ## Run Python workspace lint and type checks

.PHONY: python-check-all
python-check-all: python-check test python-build docs-build ## Run the complete Python and documentation gate

.PHONY: check
check: ## Format, then run fast checks in parallel (override with CHECK_JOBS=N)
	@printf '\n==> Format repository sources with pre-commit hooks\n'
	@$(MAKE) --no-print-directory format
	@printf '\n==> Run $(words $(CHECK_TARGETS)) independent checks with $(CHECK_JOBS) workers\n'
	@$(MAKE) --no-print-directory -j$(CHECK_JOBS) $(CHECK_TARGETS)
	@printf '\n==> Formatting and checks completed\n'

.PHONY: check-all
check-all: dev-state-check eip-check examples-check-all frontend-check-all python-check-all rust-check-all sdk-check-all a13n-service-cli-check-all ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist examples/plugins/dist site target sdk/python/dist sdk/rust/target sdk/rust/a13n-service-cli/target packages/a13n-harness-ui/a13n_harness_ui/static
	@pnpm --dir frontend run clean
	@npm --prefix sdk/typescript run clean

.PHONY: help
help: ## Show available commands
	@printf 'Usage: make <target> [VARIABLE=value]\n\n'
	@printf 'Common workflows:\n'
	@printf '  make cli                           Start Harness UI; create its .env if missing\n'
	@printf '  make webui                         Build and start WebUI; print a generated login link\n'
	@printf '  make webui WEBUI_ARGS="--port 9000" Forward WebUI server options\n'
	@printf '  make cli CLI_ARGS="--help"         Forward options or subcommands to Harness UI\n'
	@printf '  make env-init                      Prepare both development .env files only\n'
	@printf '  make dev                           Start local Service, Console, and infrastructure\n'
	@printf '  make dev-down                      Stop infrastructure, preserving data\n'
	@printf '  make test PYTHON_TEST_DIRS=scripts/tests PYTHON_TEST_WORKERS=0\n'
	@printf '  make check CHECK_JOBS=4             Format, then run fast checks\n\n'
	@printf 'Environment overrides: HARNESS_UI_ENV=path, HARNESS_ENV=path, SERVICE_CONFIG=path\n'
	@printf 'Existing .env files are preserved; missing files need a sibling .env.example.\n\n'
	@printf 'All targets:\n'
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-38s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: service-dev dev-reset dev-state-check
service-dev: setup ## Run only local Service and its scripted model, preserving data
	@$(SERVICE_DEV) serve

dev-reset: sync ## Reset owned local Service stores (STATE=empty or STATE=seeded)
	@$(SERVICE_DEV) reset "$(STATE)"

dev-state-check: sync ## Validate local state tools and seed journeys in disposable storage
	@uv run --locked ruff check --no-fix dev/service
	@uv run --locked ruff format --check dev/service
	@uv run --locked pyright dev/service
	@uv run --locked pytest dev/service/tests -q --tb=short
