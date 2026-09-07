.DEFAULT_GOAL := help

A13N_SERVICE_IMAGE ?= a13n-service:local
SANDBOX_IMAGE ?= a13n-sandbox:local
EXAMPLE_DIRS := examples/agent-app examples/environment-provider examples/plugins
PYTHON_TEST_DIRS ?=
PYTHON_TEST_WORKERS ?=
LANGFUSE_COMPOSE := docker compose $(if $(wildcard .env),--env-file .env,) -f dev/langfuse.compose.yaml
CHECK_JOBS ?= 4
CHECK_TARGETS := \
	lint \
	typecheck \
	examples-check \
	a13n-harness-ui-webui-check \
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
	@command -v go >/dev/null || { echo "Go is required: https://go.dev/"; exit 1; }
	@command -v cargo >/dev/null || { echo "Rust is required: https://rustup.rs/"; exit 1; }
	@echo "Synchronizing the Python workspace"
	@uv sync --locked --all-packages
	@echo "Synchronizing the standalone Python SDK"
	@uv sync --project sdk/python --locked
	@echo "Installing Harness UI WebUI dependencies"
	@npm --prefix apps/a13n-harness-ui ci
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
		(cd examples/agent-app && uv run --locked agent-app-example --state "$$state_dir/state.json" "first turn" "second turn"); \
		(cd examples/agent-app && uv run --locked agent-app-example --state "$$state_dir/state.json" "turn after restart")

.PHONY: examples-build
examples-build: examples-sync ## Build every example distribution
	@for directory in $(EXAMPLE_DIRS); do (cd "$$directory" && rm -rf dist && uv build) || exit $$?; done

.PHONY: examples-check
examples-check: examples-lock-check examples-format-check examples-typecheck ## Run example lint and type checks

.PHONY: examples-check-all
examples-check-all: examples-check examples-test examples-smoke examples-build ## Run the complete examples gate

.PHONY: setup
setup: sync ## Start local PostgreSQL and Redis
	@docker compose -f dev/compose.yaml up -d --wait

.PHONY: dev
dev: setup ## Upgrade the schema and run a13n Service
	@uv run --locked a13n-service db upgrade
	@bash scripts/dev.sh

.PHONY: dev-down
dev-down: ## Stop local infrastructure and remove its data volumes
	@docker compose -f dev/compose.yaml down --volumes --remove-orphans

.PHONY: langfuse-up
langfuse-up: ## Start the local Langfuse trace backend
	@set -e; \
	$(LANGFUSE_COMPOSE) up -d --wait; \
	web_address="$$( $(LANGFUSE_COMPOSE) port langfuse-web 3000 )"; \
	worker_address="$$( $(LANGFUSE_COMPOSE) port langfuse-worker 3030 )"; \
	web_port="$${web_address##*:}"; \
	worker_port="$${worker_address##*:}"; \
	deadline=$$(( $$(date +%s) + 120 )); \
	check_url() { \
		remaining=$$(( deadline - $$(date +%s) )); \
		[ "$$remaining" -gt 0 ] || return 1; \
		max_time=$$remaining; \
		[ "$$max_time" -le 5 ] || max_time=5; \
		curl --fail --silent --show-error --connect-timeout 2 --max-time "$$max_time" "$$1" >/dev/null 2>&1; \
	}; \
	until check_url "http://127.0.0.1:$$web_port/api/public/health?failIfDatabaseUnavailable=true" \
		&& check_url "http://127.0.0.1:$$web_port/api/public/ready" \
		&& check_url "http://127.0.0.1:$$worker_port/api/health"; do \
		if [ "$$(date +%s)" -ge "$$deadline" ]; then \
			echo "Langfuse web and worker did not become ready within 120 seconds." >&2; \
			$(LANGFUSE_COMPOSE) ps >&2; \
			$(LANGFUSE_COMPOSE) logs --tail=100 langfuse-web langfuse-worker >&2; \
			exit 1; \
		fi; \
		sleep 2; \
	done; \
	echo "Langfuse: http://127.0.0.1:$$web_port"

.PHONY: langfuse-down
langfuse-down: ## Stop local Langfuse while preserving its data
	@$(LANGFUSE_COMPOSE) down --remove-orphans

.PHONY: langfuse-test
langfuse-test: langfuse-up ## Verify Service OTLP write and Trace Query against local Langfuse v4
	@set -e; \
	web_container="$$( $(LANGFUSE_COMPOSE) ps -q langfuse-web )"; \
	public_key="$$( docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$$web_container" | sed -n 's/^LANGFUSE_INIT_PROJECT_PUBLIC_KEY=//p' )"; \
	secret_key="$$( docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$$web_container" | sed -n 's/^LANGFUSE_INIT_PROJECT_SECRET_KEY=//p' )"; \
	web_address="$$( $(LANGFUSE_COMPOSE) port langfuse-web 3000 )"; \
	A13N_TEST_LANGFUSE_BASE_URL="http://$$web_address" \
	A13N_TEST_LANGFUSE_PUBLIC_KEY="$$public_key" \
	A13N_TEST_LANGFUSE_SECRET_KEY="$$secret_key" \
	uv run --locked python -m pytest packages/a13n-service/tests/trace_query/test_langfuse_integration.py

.PHONY: langfuse-reset
langfuse-reset: ## Stop local Langfuse and remove all local Langfuse data
	@$(LANGFUSE_COMPOSE) down --volumes --remove-orphans

.PHONY: a13n-harness-ui
a13n-harness-ui: sync ## Run the interactive Harness UI without release update checks
	@uv run --locked a13n-harness-ui --no-update-check

.PHONY: a13n-harness-ui-db-migrate
a13n-harness-ui-db-migrate: sync ## Generate a Harness UI SQLite migration against a disposable database
	@test -n "$(msg)" || { echo 'msg is required: make a13n-harness-ui-db-migrate msg="description"'; exit 2; }
	@uv run --locked python -m a13n_harness_ui.storage.migrations.generate "$(msg)"

.PHONY: format
format: sync a13n-harness-ui-webui-sync sdk-python-sync sdk-typescript-sync ## Format repository and standalone SDK sources
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
	@npm --prefix apps/a13n-harness-ui run format
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
test: sync ## Run Python workspace tests
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
a13n-harness-release-build: a13n-harness-python-build ## Build and verify the prepared Harness release group
	@uv run --no-project python scripts/check-a13n-distributions.py dist --require-exact-internal-version

.PHONY: a13n-harness-ui-build
a13n-harness-ui-build: sync a13n-harness-ui-assets ## Build Harness UI for repository development
	@rm -rf dist
	@uv build --package a13n-harness-ui --out-dir dist
	@uv run --locked python scripts/check-a13n-harness-ui-distribution.py dist --rebuild-wheel

.PHONY: a13n-harness-ui-release-build
a13n-harness-ui-release-build: ## Build and verify Harness UI from prepared assets and release metadata
	@rm -rf dist
	@uv build --package a13n-harness-ui --out-dir dist
	@uv run --no-project python scripts/check-a13n-harness-ui-distribution.py dist --rebuild-wheel --require-exact-internal-version

.PHONY: a13n-service-python-build
a13n-service-python-build: sync ## Build only Service release-group Python distributions
	@rm -rf dist
	@for package in a13n-logging a13n-service; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

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

apps/a13n-harness-ui/node_modules/.package-lock.json: apps/a13n-harness-ui/package.json apps/a13n-harness-ui/package-lock.json
	@npm --prefix apps/a13n-harness-ui ci

.PHONY: a13n-harness-ui-webui-sync
a13n-harness-ui-webui-sync: apps/a13n-harness-ui/node_modules/.package-lock.json ## Install locked Harness UI WebUI dependencies

.PHONY: a13n-harness-ui-webui-format
a13n-harness-ui-webui-format: a13n-harness-ui-webui-sync ## Format Harness UI WebUI sources
	@npm --prefix apps/a13n-harness-ui run format

.PHONY: a13n-harness-ui-webui-build
a13n-harness-ui-webui-build: a13n-harness-ui-webui-sync ## Build Harness UI WebUI production assets
	@npm --prefix apps/a13n-harness-ui run build

.PHONY: a13n-harness-ui-webui-check
a13n-harness-ui-webui-check: a13n-harness-ui-webui-sync ## Run Harness UI WebUI formatting and type checks
	@npm --prefix apps/a13n-harness-ui run check

.PHONY: a13n-harness-ui-webui-check-all
a13n-harness-ui-webui-check-all: a13n-harness-ui-webui-check a13n-harness-ui-webui-build ## Run the complete Harness UI WebUI gate

.PHONY: a13n-harness-ui-assets
a13n-harness-ui-assets: sync a13n-harness-ui-webui-build ## Prepare generated Harness UI WebUI files for Python packaging
	@uv run --locked python scripts/prepare-a13n-harness-ui-assets.py

sdk/typescript/node_modules/.package-lock.json: sdk/typescript/package.json sdk/typescript/package-lock.json
	@npm --prefix sdk/typescript ci

.PHONY: sdk-typescript-sync
sdk-typescript-sync: sdk/typescript/node_modules/.package-lock.json ## Install locked TypeScript SDK dependencies

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
sdk-check-all: sdk-python-check-all sdk-go-check-all sdk-rust-check-all sdk-typescript-check-all ## Run all complete standalone SDK gates

.PHONY: build
build: python-build rust-build sdk-build a13n-service-cli-build ## Build all workspace, application, SDK, and CLI artifacts

.PHONY: db-migrate
db-migrate: sync ## Generate a migration (usage: make db-migrate msg="description")
	@bash dev/db-migrate.sh "$(msg)"

.PHONY: db-upgrade
db-upgrade: sync ## Upgrade the local a13n-service database to all heads
	@uv run --locked a13n-service db upgrade

.PHONY: db-downgrade
db-downgrade: sync ## Downgrade the local database by one reviewed revision
	@uv run --locked a13n-service db downgrade

.PHONY: db-current
db-current: sync ## Show the current a13n-service database revision
	@uv run --locked a13n-service db current

.PHONY: db-check
db-check: sync ## Fail unless the a13n-service database is at all heads
	@uv run --locked a13n-service db current --check-heads

.PHONY: db-history
db-history: sync ## Show a13n-service migration history
	@uv run --locked a13n-service db history

.PHONY: release-check
release-check: ## Validate a component version (component=a13n-harness|a13n-harness-ui|a13n-service|a13n-envd|a13n-service-cli|a13n-<language> version=X.Y.Z or X.Y.Z-rc.N)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-a13n-service
image-a13n-service: ## Build the local a13n-service container image
	@docker build -f deploy/containers/a13n-service/Dockerfile -t "$(A13N_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with a13n-envd
	@docker build -f deploy/containers/sandbox/Dockerfile -t "$(SANDBOX_IMAGE)" .

.PHONY: images
images: image-a13n-service image-sandbox ## Build all local container images

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
	@$(MAKE) --no-print-directory image-check-a13n-service image-check-sandbox

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
check-all: eip-check examples-check-all a13n-harness-ui-webui-check-all python-check-all rust-check-all sdk-check-all a13n-service-cli-check-all ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist examples/plugins/dist site target sdk/python/dist sdk/rust/target sdk/rust/a13n-service-cli/target packages/a13n-harness-ui/a13n_harness_ui/static
	@npm --prefix apps/a13n-harness-ui run clean
	@npm --prefix sdk/typescript run clean

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
