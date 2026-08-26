.DEFAULT_GOAL := help

FOUNDATION_SERVICE_IMAGE ?= agent-foundation-service:local
SANDBOX_IMAGE ?= agent-foundation-sandbox:local
EXAMPLE_DIRS := examples/general-agent examples/plugins examples/hosting examples/local-agent

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
	@echo "Installing Foundation Web dependencies"
	@npm --prefix apps/foundation-web ci
	@echo "Installing Harness UI dependencies"
	@npm --prefix apps/harness-ui ci
	@echo "Installing TypeScript SDK dependencies"
	@npm --prefix sdk/typescript ci
	@echo "Installing pre-commit hooks"
	@uv run --locked pre-commit install --install-hooks

.PHONY: sync
sync: ## Synchronize the locked Python workspace
	@uv sync --locked --all-packages

.PHONY: examples-sync
examples-sync: ## Synchronize every independent example project
	@for directory in $(EXAMPLE_DIRS); do uv sync --project "$$directory" --locked || exit $$?; done

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
	@(cd examples/plugins && uv run --locked plugin-example-environment-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-environment-code)
	@(cd examples/plugins && uv run --locked plugin-example-environment-extension-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-environment-extension-code)
	@(cd examples/plugins && uv run --locked plugin-example-harness-entrypoint)
	@(cd examples/plugins && uv run --locked plugin-example-harness-code)
	@(cd examples/general-agent && uv run --locked general-agent-example)
	@(cd examples/hosting && uv run --locked host-persistence-example)
	@(cd examples/local-agent && uv run --locked local-agent-example)

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
dev: setup foundation-web-sync ## Upgrade the schema and run Foundation Service with Foundation Web
	@uv run --locked foundation-service db upgrade
	@bash scripts/dev.sh

.PHONY: dev-down
dev-down: ## Stop local infrastructure and remove its data volumes
	@docker compose -f dev/compose.yaml down --volumes --remove-orphans

.PHONY: agent-ui tui
agent-ui: sync ## Run Agent UI (default WebUI; append `tui` for terminal UI)
	@uv run --locked converge-agent-ui $(filter-out agent-ui,$(MAKECMDGOALS))

tui: agent-ui
	@:

.PHONY: agent-ui-db-migrate
agent-ui-db-migrate: sync ## Generate an Agent UI SQLite migration against a disposable database
	@test -n "$(msg)" || { echo 'msg is required: make agent-ui-db-migrate msg="description"'; exit 2; }
	@uv run --locked python -m converge_agent_ui.storage.migrations.generate "$(msg)"

.PHONY: format
format: sync foundation-web-sync harness-ui-sync sdk-python-sync sdk-typescript-sync ## Format repository and standalone SDK sources
	@for hook in end-of-file-fixer trailing-whitespace mdformat ruff-format; do \
		git ls-files --cached --others --exclude-standard -z | \
			xargs -0 uv run --locked pre-commit run "$$hook" --files || true; \
	done
	@for hook in end-of-file-fixer trailing-whitespace mdformat ruff-format; do \
		git ls-files --cached --others --exclude-standard -z | \
			xargs -0 uv run --locked pre-commit run "$$hook" --files || exit $$?; \
	done
	@files="$$(find sdk/go -type f -name '*.go')"; gofmt -w $$files
	@cargo fmt --all
	@(cd sdk/rust && cargo fmt)
	@(cd sdk/rust/agent-foundation-cli && cargo fmt)
	@npm --prefix apps/foundation-web run format
	@npm --prefix apps/harness-ui run format
	@npm --prefix sdk/typescript run format

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/agent-envd-client && uv run --locked deptry converge_agent_envd_client)
	@(cd packages/agent-environment-provider && uv run --locked deptry converge_agent_environment_provider)
	@(cd packages/agent-harness && uv run --locked deptry converge_agent_harness)
	@(cd packages/agent-stream-protocol && uv run --locked deptry converge_agent_stream_protocol)
	@(cd packages/agent-ui && uv run --locked deptry converge_agent_ui)
	@(cd packages/logging && uv run --locked deptry converge_logging)
	@(cd packages/foundation-service && uv run --locked deptry converge_foundation_service)

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
	@uv run --locked python -m pytest

.PHONY: eip-generate
eip-generate: sync ## Generate checked EIP descriptor, Python surface, and inspection artifacts
	@uv run --locked python -m scripts.eip_codegen generate

.PHONY: eip-verify
eip-verify: sync ## Verify checked EIP artifacts without modifying the repository
	@uv run --locked python -m scripts.eip_codegen verify

.PHONY: eip-test
eip-test: sync ## Run EIP generation, runtime, cross-language, and wire-model tests
	@cargo build --locked --package converge-agent-envd
	@AGENT_ENVD_TEST_BINARY="$(CURDIR)/target/debug/agent-envd" uv run --locked python -m pytest scripts/tests/test_eip_codegen.py packages/agent-envd-client/tests/eip packages/agent-harness/tests/test_environment_eip_e2e.py
	@uv run --locked pyright packages/agent-envd-client/converge_agent_envd_client packages/agent-environment-provider/converge_agent_environment_provider
	@cargo test --locked --package converge-agent-envd

.PHONY: eip-check
eip-check: eip-verify eip-test ## Run the complete EIP protocol gate

.PHONY: python-build
python-build: sync agent-ui-assets ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: harness-python-build
harness-python-build: ## Build the prepared Harness release-group distributions
	@rm -rf dist
	@for package in converge-agent-environment-provider converge-agent-harness converge-agent-stream-protocol; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

.PHONY: harness-dist-check
harness-dist-check: ## Verify isolated same-version Harness wheels
	@uv run --no-project python scripts/check-agent-distributions.py dist

.PHONY: harness-release-build
harness-release-build: harness-python-build ## Build and verify the prepared Harness release group
	@uv run --no-project python scripts/check-agent-distributions.py dist --require-exact-internal-version

.PHONY: agent-ui-build
agent-ui-build: sync agent-ui-assets ## Build Agent UI for repository development
	@rm -rf dist
	@uv build --package converge-agent-ui --out-dir dist
	@uv run --locked python scripts/check-agent-ui-distribution.py dist --rebuild-wheel

.PHONY: agent-ui-release-build
agent-ui-release-build: ## Build and verify Agent UI from prepared assets and release metadata
	@rm -rf dist
	@uv build --package converge-agent-ui --out-dir dist
	@uv run --no-project python scripts/check-agent-ui-distribution.py dist --rebuild-wheel --require-exact-internal-version

.PHONY: foundation-python-build
foundation-python-build: sync ## Build only Foundation release-group Python distributions
	@rm -rf dist
	@for package in converge-logging converge-foundation-service; do \
		uv build --package "$$package" --out-dir dist || exit $$?; \
	done

.PHONY: agent-envd-client-build
agent-envd-client-build: sync ## Build the agent-envd client Python distributions
	@rm -rf dist
	@uv build --package converge-agent-envd-client --out-dir dist

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
	@cargo package --locked --allow-dirty --package converge-agent-envd

.PHONY: rust-check
rust-check: rust-format-check rust-lint ## Run Rust workspace formatting and lint checks

.PHONY: rust-check-all
rust-check-all: rust-check rust-test rust-build rust-package ## Run the complete Rust workspace gate

.PHONY: sdk-python-isolation-check
sdk-python-isolation-check: ## Verify the Python SDK is excluded from the root workspace
	@python3 -c 'import tomllib; from pathlib import Path; data = tomllib.loads(Path("pyproject.toml").read_text()); workspace = data["tool"]["uv"]["workspace"]; members = {path.resolve() for pattern in workspace["members"] for path in Path().glob(pattern)}; excluded = {path.resolve() for pattern in workspace.get("exclude", []) for path in Path().glob(pattern)}; assert Path("sdk/python").resolve() not in members - excluded, "sdk/python must remain outside the root uv workspace"'

.PHONY: sdk-python-sync
sdk-python-sync: ## Synchronize the standalone Python SDK
	@uv sync --project sdk/python --locked

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

.PHONY: harness-ui-sync
harness-ui-sync: ## Install locked Harness UI dependencies
	@npm --prefix apps/harness-ui ci

.PHONY: harness-ui-format
harness-ui-format: harness-ui-sync ## Format Harness UI sources
	@npm --prefix apps/harness-ui run format

.PHONY: harness-ui-build
harness-ui-build: harness-ui-sync ## Build Harness UI production assets
	@npm --prefix apps/harness-ui run build

.PHONY: harness-ui-check
harness-ui-check: harness-ui-sync ## Run Harness UI formatting and type checks
	@npm --prefix apps/harness-ui run check

.PHONY: harness-ui-check-all
harness-ui-check-all: harness-ui-sync ## Run the complete Harness UI gate
	@npm --prefix apps/harness-ui run check:all

.PHONY: agent-ui-assets
agent-ui-assets: sync harness-ui-build ## Prepare generated Harness UI files for Python packaging
	@uv run --locked python scripts/prepare-agent-ui-assets.py

.PHONY: foundation-web-sync
foundation-web-sync: ## Install locked Foundation Web dependencies
	@npm --prefix apps/foundation-web ci

.PHONY: foundation-web-format
foundation-web-format: foundation-web-sync ## Format Foundation Web sources
	@npm --prefix apps/foundation-web run format

.PHONY: foundation-web-build
foundation-web-build: foundation-web-sync ## Build Foundation Web production assets
	@npm --prefix apps/foundation-web run build

.PHONY: foundation-web-check
foundation-web-check: foundation-web-sync ## Run Foundation Web formatting and type checks
	@npm --prefix apps/foundation-web run check

.PHONY: foundation-web-check-all
foundation-web-check-all: foundation-web-sync ## Run the complete Foundation Web gate
	@npm --prefix apps/foundation-web run check:all

.PHONY: sdk-typescript-sync
sdk-typescript-sync: ## Install locked TypeScript SDK dependencies
	@npm --prefix sdk/typescript ci

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
build: python-build rust-build foundation-web-build sdk-build foundation-cli-build ## Build all workspace, application, SDK, and CLI artifacts

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
	@docker run --rm --entrypoint sh "$(FOUNDATION_SERVICE_IMAGE)" -c 'test -r /app/web/index.html && ! command -v node'
	@docker run --rm --entrypoint python "$(FOUNDATION_SERVICE_IMAGE)" -c 'from converge_foundation_service.asgi import app; assert str(app.state.settings.web_dist_dir) == "/app/web"'

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
check: ## Apply formatting, then check lint and types
	@printf '\n==> Format repository sources with pre-commit hooks\n'
	@$(MAKE) --no-print-directory format
	@printf '\n==> [1/11] Lint repository and verify Python/Markdown formatting\n'
	@$(MAKE) --no-print-directory lint
	@printf '\n==> [2/11] Type-check Python workspace with Pyright\n'
	@$(MAKE) --no-print-directory typecheck
	@printf '\n==> [3/11] Check examples with Ruff and Pyright\n'
	@$(MAKE) --no-print-directory examples-check
	@printf '\n==> [4/11] Check Foundation Web with Prettier and TypeScript\n'
	@$(MAKE) --no-print-directory foundation-web-check
	@printf '\n==> [5/11] Check Harness UI with Prettier and TypeScript\n'
	@$(MAKE) --no-print-directory harness-ui-check
	@printf '\n==> [6/11] Check Rust workspace with rustfmt and Clippy\n'
	@$(MAKE) --no-print-directory rust-check
	@printf '\n==> [7/11] Check Python SDK with Ruff and Pyright\n'
	@$(MAKE) --no-print-directory sdk-python-check
	@printf '\n==> [8/11] Check Go SDK with gofmt and vet\n'
	@$(MAKE) --no-print-directory sdk-go-check
	@printf '\n==> [9/11] Check Rust SDK with rustfmt and Clippy\n'
	@$(MAKE) --no-print-directory sdk-rust-check
	@printf '\n==> [10/11] Check TypeScript SDK with Prettier and TypeScript\n'
	@$(MAKE) --no-print-directory sdk-typescript-check
	@printf '\n==> [11/11] Check Foundation CLI with rustfmt and Clippy\n'
	@$(MAKE) --no-print-directory foundation-cli-check
	@printf '\n==> Formatting and checks completed\n'

.PHONY: check-all
check-all: eip-check examples-check-all foundation-web-check-all harness-ui-check-all python-check-all rust-check-all sdk-check-all foundation-cli-check-all ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist examples/plugins/dist site target sdk/python/dist sdk/rust/target sdk/rust/agent-foundation-cli/target packages/agent-ui/converge_agent_ui/static
	@npm --prefix apps/foundation-web run clean
	@npm --prefix apps/harness-ui run clean
	@npm --prefix sdk/typescript run clean

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
