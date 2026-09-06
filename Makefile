.DEFAULT_GOAL := help

FOUNDATION_SERVICE_IMAGE ?= agent-foundation-service:local
SANDBOX_IMAGE ?= agent-foundation-sandbox:local
EXAMPLE_DIRS := examples/agent-app examples/environment-provider examples/plugins
PYTHON_TEST_DIRS := $(sort $(wildcard packages/*/tests) scripts/tests)
LANGFUSE_COMPOSE := docker compose $(if $(wildcard .env),--env-file .env,) -f dev/langfuse.compose.yaml
CHECK_JOBS ?= 4
CHECK_TARGETS := \
	lint \
	typecheck \
	examples-check \
	rust-check \
	sdk-python-check \
	sdk-go-check \
	sdk-rust-check \
	sdk-typescript-check \
	foundation-cli-check

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
	@echo "Installing Harness UI dependencies"
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
dev: setup ## Upgrade the schema and run Foundation Service
	@uv run --locked foundation-service db upgrade
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
langfuse-test: langfuse-up ## Verify Foundation OTLP write and Trace Query against local Langfuse v4
	@set -e; \
	web_container="$$( $(LANGFUSE_COMPOSE) ps -q langfuse-web )"; \
	public_key="$$( docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$$web_container" | sed -n 's/^LANGFUSE_INIT_PROJECT_PUBLIC_KEY=//p' )"; \
	secret_key="$$( docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$$web_container" | sed -n 's/^LANGFUSE_INIT_PROJECT_SECRET_KEY=//p' )"; \
	web_address="$$( $(LANGFUSE_COMPOSE) port langfuse-web 3000 )"; \
	A13N_TEST_LANGFUSE_BASE_URL="http://$$web_address" \
	A13N_TEST_LANGFUSE_PUBLIC_KEY="$$public_key" \
	A13N_TEST_LANGFUSE_SECRET_KEY="$$secret_key" \
	uv run --locked python -m pytest packages/foundation-service/tests/trace_query/test_langfuse_integration.py

.PHONY: langfuse-reset
langfuse-reset: ## Stop local Langfuse and remove all local Langfuse data
	@$(LANGFUSE_COMPOSE) down --volumes --remove-orphans

.PHONY: a13n-ui
a13n-ui: sync ## Run the Agent UI interactive TUI
	@uv run --locked a13n-ui

.PHONY: agent-ui-db-migrate
agent-ui-db-migrate: sync ## Generate an Agent UI SQLite migration against a disposable database
	@test -n "$(msg)" || { echo 'msg is required: make agent-ui-db-migrate msg="description"'; exit 2; }
	@uv run --locked python -m a13n_ui.storage.migrations.generate "$(msg)"

.PHONY: format
format: sync sdk-python-sync sdk-typescript-sync ## Format repository and standalone SDK sources
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
	@(cd sdk/rust/agent-foundation-cli && cargo fmt)
	@npm --prefix sdk/typescript run format

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/agent-envd-client && uv run --locked deptry a13n_envd_client)
	@(cd packages/agent-environment-provider && uv run --locked deptry a13n_environment_provider)
	@(cd packages/agent-harness && uv run --locked deptry a13n_harness)
	@(cd packages/agent-stream-protocol && uv run --locked deptry a13n_stream_protocol)
	@(cd packages/agent-ui && uv run --locked deptry a13n_ui)
	@(cd packages/logging && uv run --locked deptry a13n_logging)
	@(cd packages/foundation-service && uv run --locked deptry a13n_service)

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
	@for directory in $(PYTHON_TEST_DIRS); do \
		uv run --locked python -m pytest -n 2 --dist loadgroup "$$directory" || exit $$?; \
	done

.PHONY: eip-generate
eip-generate: sync ## Generate checked EIP descriptor, Python surface, and inspection artifacts
	@uv run --locked python -m scripts.eip_codegen generate

.PHONY: eip-verify
eip-verify: sync ## Verify checked EIP artifacts without modifying the repository
	@uv run --locked python -m scripts.eip_codegen verify

.PHONY: eip-integration-test
eip-integration-test: sync ## Run EIP generation, runtime, cross-language, and wire-model integration tests
	@cargo build --locked --package agent-envd
	@AGENT_ENVD_TEST_BINARY="$(CURDIR)/target/debug/agent-envd" A13N_AGENT_ENVD_EXECUTABLE="$(CURDIR)/target/debug/agent-envd" uv run --locked python -m pytest scripts/tests/test_eip_codegen.py packages/agent-envd-client/tests/eip packages/agent-environment-provider/tests/test_local_envd.py packages/agent-environment-provider/tests/test_remote_envd.py packages/agent-environment-provider/tests/test_remote_envd_e2e.py
	@AGENT_ENVD_TEST_BINARY="$(CURDIR)/target/debug/agent-envd" uv run --project examples/environment-provider --locked python -m pytest examples/environment-provider/tests/test_remote.py
	@uv run --locked pyright packages/agent-envd-client/a13n_envd_client packages/agent-environment-provider/a13n_environment_provider

.PHONY: eip-test
eip-test: eip-integration-test ## Run complete EIP integration and daemon tests
	@cargo test --locked --package agent-envd

.PHONY: local-envd-test
local-envd-test: sync ## Build agent-envd and run Local Envd provider tests
	@cargo build --locked --package agent-envd
	@set -a; \
	if [ -f "$(CURDIR)/.env" ]; then . "$(CURDIR)/.env"; fi; \
	set +a; \
	A13N_AGENT_ENVD_EXECUTABLE="$${A13N_AGENT_ENVD_EXECUTABLE:-$(CURDIR)/target/debug/agent-envd}"; \
	export A13N_AGENT_ENVD_EXECUTABLE; \
	uv run --locked python -m pytest packages/agent-environment-provider/tests/test_local_envd.py

.PHONY: e2b-provider-test
e2b-provider-test: sync ## Run native E2B unit and opt-in live integration tests
	@uv run --locked pytest -q packages/agent-environment-provider/tests/test_e2b*.py packages/agent-harness/tests/test_e2b_environment_live.py

.PHONY: docker-provider-test
docker-provider-test: sync ## Run Docker Provider tests
	@uv run --locked python -m pytest packages/agent-environment-provider/tests/test_docker.py

.PHONY: eip-check
eip-check: eip-verify eip-test ## Run the complete EIP protocol gate

.PHONY: python-build
python-build: sync ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: harness-python-build
harness-python-build: ## Build the prepared Harness release-group distributions
	@rm -rf dist
	@for package in a13n-environment-provider a13n-harness a13n-stream-protocol; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

.PHONY: harness-dist-check
harness-dist-check: ## Verify isolated same-version Harness wheels
	@uv run --no-project python scripts/check-agent-distributions.py dist

.PHONY: harness-release-build
harness-release-build: harness-python-build ## Build and verify the prepared Harness release group
	@uv run --no-project python scripts/check-agent-distributions.py dist --require-exact-internal-version

.PHONY: agent-ui-build
agent-ui-build: sync ## Build Agent UI for repository development
	@rm -rf dist
	@uv build --package a13n-ui --out-dir dist
	@uv run --locked python scripts/check-agent-ui-distribution.py dist --rebuild-wheel

.PHONY: agent-ui-release-build
agent-ui-release-build: ## Build and verify Agent UI from prepared release metadata
	@rm -rf dist
	@uv build --package a13n-ui --out-dir dist
	@uv run --no-project python scripts/check-agent-ui-distribution.py dist --rebuild-wheel --require-exact-internal-version

.PHONY: foundation-python-build
foundation-python-build: sync ## Build only Foundation release-group Python distributions
	@rm -rf dist
	@for package in a13n-logging a13n-service; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

.PHONY: agent-envd-client-build
agent-envd-client-build: sync ## Build the agent-envd client Python distributions
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
rust-package: ## Verify the agent-envd crates.io package
	@cargo package --locked --allow-dirty --package agent-envd

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

.PHONY: foundation-cli-isolation-check
foundation-cli-isolation-check: ## Verify the Foundation CLI remains an independent Cargo project
	@cargo metadata --locked --no-deps --format-version 1 | python3 -c 'import json, sys; from pathlib import Path; cli = Path("sdk/rust/agent-foundation-cli/Cargo.toml").resolve(); manifests = {Path(item["manifest_path"]).resolve() for item in json.load(sys.stdin)["packages"]}; assert cli not in manifests, "Foundation CLI must remain outside the root Cargo workspace"'
	@cargo metadata --locked --no-deps --manifest-path sdk/rust/agent-foundation-cli/Cargo.toml --format-version 1 | python3 -c 'import json, sys; from pathlib import Path; cli = Path("sdk/rust/agent-foundation-cli/Cargo.toml").resolve(); data = json.load(sys.stdin); packages = data["packages"]; manifests = {Path(item["manifest_path"]).resolve() for item in packages}; member_ids = set(data["workspace_members"]); package_ids = {item["id"] for item in packages}; assert Path(data["workspace_root"]).resolve() == cli.parent, "Foundation CLI must own its Cargo workspace"; assert manifests == {cli} and member_ids == package_ids, "Foundation CLI workspace must contain only the CLI package"'
	@cargo package --locked --allow-dirty --manifest-path sdk/rust/Cargo.toml --list | python3 -c 'import sys; paths = sys.stdin.read().splitlines(); assert not any(path == "agent-foundation-cli" or path.startswith("agent-foundation-cli/") for path in paths), "Rust SDK source package must exclude the Foundation CLI"'

.PHONY: foundation-cli-format-check
foundation-cli-format-check: ## Check Foundation CLI formatting
	@(cd sdk/rust/agent-foundation-cli && cargo fmt -- --check)

.PHONY: foundation-cli-lint
foundation-cli-lint: ## Run Foundation CLI Clippy with warnings denied
	@(cd sdk/rust/agent-foundation-cli && cargo clippy --all-targets --all-features --locked -- -D warnings)

.PHONY: foundation-cli-test
foundation-cli-test: ## Run Foundation CLI tests
	@(cd sdk/rust/agent-foundation-cli && cargo test --all-features --locked)

.PHONY: foundation-cli-build
foundation-cli-build: ## Build the Foundation CLI
	@(cd sdk/rust/agent-foundation-cli && cargo build --all-features --locked)

.PHONY: foundation-cli-check
foundation-cli-check: foundation-cli-isolation-check foundation-cli-format-check foundation-cli-lint ## Run Foundation CLI formatting and lint checks

.PHONY: foundation-cli-check-all
foundation-cli-check-all: foundation-cli-check foundation-cli-test foundation-cli-build ## Run the complete Foundation CLI gate



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
build: python-build rust-build sdk-build foundation-cli-build ## Build all workspace, application, SDK, and CLI artifacts

.PHONY: db-migrate
db-migrate: sync ## Generate a migration (usage: make db-migrate msg="description")
	@bash dev/db-migrate.sh "$(msg)"

.PHONY: db-upgrade
db-upgrade: sync ## Upgrade the local foundation-service database to all heads
	@uv run --locked foundation-service db upgrade

.PHONY: db-downgrade
db-downgrade: sync ## Downgrade the local database by one reviewed revision
	@uv run --locked foundation-service db downgrade

.PHONY: db-current
db-current: sync ## Show the current foundation-service database revision
	@uv run --locked foundation-service db current

.PHONY: db-check
db-check: sync ## Fail unless the foundation-service database is at all heads
	@uv run --locked foundation-service db current --check-heads

.PHONY: db-history
db-history: sync ## Show foundation-service migration history
	@uv run --locked foundation-service db history

.PHONY: release-check
release-check: ## Validate a component version (component=harness|agent-ui|foundation|agent-envd|foundation-cli|sdk-<language> version=X.Y.Z or X.Y.Z-rc.N)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-foundation-service
image-foundation-service: ## Build the local foundation-service container image
	@docker build -f deploy/containers/foundation-service/Dockerfile -t "$(FOUNDATION_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with agent-envd
	@docker build -f deploy/containers/sandbox/Dockerfile -t "$(SANDBOX_IMAGE)" .

.PHONY: images
images: image-foundation-service image-sandbox ## Build all local container images

.PHONY: image-check-foundation-service
image-check-foundation-service: ## Smoke-check the existing foundation-service container image
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(FOUNDATION_SERVICE_IMAGE)")" = "app"
	@docker run --rm --entrypoint sh "$(FOUNDATION_SERVICE_IMAGE)" -c '! command -v node'

.PHONY: image-check-sandbox
image-check-sandbox: ## Smoke-check the existing sandbox container image
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(SANDBOX_IMAGE)")" = "sandbox"
	@docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(SANDBOX_IMAGE)" | grep -qx 'AGENT_ENVD_EXECUTION_ISOLATION=disabled'
	@docker run --rm \
		--env AGENT_ENVD_ENVIRONMENT_ID=image-check \
		--entrypoint agent-envd "$(SANDBOX_IMAGE)"

.PHONY: image-check
image-check: images ## Build and smoke-check all container images
	@$(MAKE) --no-print-directory image-check-foundation-service image-check-sandbox

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
check-all: eip-check examples-check-all python-check-all rust-check-all sdk-check-all foundation-cli-check-all ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist examples/plugins/dist site target sdk/python/dist sdk/rust/target sdk/rust/agent-foundation-cli/target packages/agent-ui/a13n_ui/static
	@npm --prefix sdk/typescript run clean

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
