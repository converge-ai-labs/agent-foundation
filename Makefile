.DEFAULT_GOAL := help

FOUNDATION_SERVICE_IMAGE ?= agent-foundation-service:local
SANDBOX_IMAGE ?= agent-foundation-sandbox:local

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
	@echo "Installing TypeScript SDK dependencies"
	@npm --prefix sdk/typescript ci
	@echo "Installing pre-commit hooks"
	@uv run --locked pre-commit install --install-hooks

.PHONY: sync
sync: ## Synchronize the locked Python workspace
	@uv sync --locked --all-packages

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

.PHONY: format
format: sync foundation-web-sync sdk-python-sync sdk-typescript-sync ## Format repository and standalone SDK sources
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files || true
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files
	@files="$$(find sdk/go -type f -name '*.go')"; gofmt -w $$files
	@cargo fmt --all
	@(cd sdk/rust && cargo fmt)
	@npm --prefix apps/foundation-web run format
	@npm --prefix sdk/typescript run format

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/agent-envd-client && uv run --locked deptry converge_agent_envd_client)
	@(cd packages/agent-harness && uv run --locked deptry converge_agent_harness)
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
	@AGENT_ENVD_TEST_BINARY="$(CURDIR)/target/debug/agent-envd" uv run --locked python -m pytest scripts/tests/test_eip_codegen.py packages/agent-envd-client/tests/eip
	@uv run --locked pyright packages/agent-envd-client/converge_agent_envd_client
	@cargo test --locked --package converge-agent-envd

.PHONY: eip-check
eip-check: eip-verify eip-test ## Run the complete EIP protocol gate

.PHONY: python-build
python-build: sync ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: foundation-python-build
foundation-python-build: sync ## Build only Foundation release-group Python distributions
	@rm -rf dist
	@for package in converge-agent-harness converge-logging converge-foundation-service; do \
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
rust-check: rust-format-check rust-lint rust-test ## Run the fast Rust workspace gate

.PHONY: rust-check-all
rust-check-all: rust-check rust-build rust-package ## Run the complete Rust workspace gate

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
sdk-python-check: sdk-python-isolation-check sdk-python-format-check sdk-python-typecheck sdk-python-test ## Run the fast Python SDK gate

.PHONY: sdk-python-check-all
sdk-python-check-all: sdk-python-check sdk-python-build ## Run the complete Python SDK gate

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
sdk-go-check: sdk-go-format-check sdk-go-vet sdk-go-test ## Run the fast Go SDK gate

.PHONY: sdk-go-check-all
sdk-go-check-all: sdk-go-check sdk-go-build ## Run the complete Go SDK gate

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
sdk-rust-check: sdk-rust-isolation-check sdk-rust-format-check sdk-rust-lint sdk-rust-test ## Run the fast Rust SDK gate

.PHONY: sdk-rust-check-all
sdk-rust-check-all: sdk-rust-check sdk-rust-build sdk-rust-package ## Run the complete Rust SDK gate

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
foundation-web-check: foundation-web-sync ## Run the fast Foundation Web gate
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
sdk-typescript-check: sdk-typescript-sync ## Run the fast TypeScript SDK gate
	@npm --prefix sdk/typescript run check

.PHONY: sdk-typescript-check-all
sdk-typescript-check-all: sdk-typescript-sync ## Run the complete TypeScript SDK gate
	@npm --prefix sdk/typescript run check:all

.PHONY: sdk-build
sdk-build: sdk-python-build sdk-go-build sdk-rust-build sdk-typescript-build ## Build all standalone SDKs

.PHONY: sdk-check
sdk-check: sdk-python-check sdk-go-check sdk-rust-check sdk-typescript-check ## Run all fast standalone SDK gates

.PHONY: sdk-check-all
sdk-check-all: sdk-python-check-all sdk-go-check-all sdk-rust-check-all sdk-typescript-check-all ## Run all complete standalone SDK gates

.PHONY: build
build: python-build rust-build foundation-web-build sdk-build ## Build all workspace, application, and SDK artifacts

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
release-check: ## Validate a component version (component=foundation|agent-envd|sdk-<language> version=X.Y.Z)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-foundation-service
image-foundation-service: ## Build the local foundation-service container image
	@docker build -f Dockerfile -t "$(FOUNDATION_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with agent-envd
	@docker build -f Dockerfile.sandbox -t "$(SANDBOX_IMAGE)" .

.PHONY: images
images: image-foundation-service image-sandbox ## Build all local container images

.PHONY: image-check
image-check: images ## Build and smoke-check all container images
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(FOUNDATION_SERVICE_IMAGE)")" = "app"
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(SANDBOX_IMAGE)")" = "sandbox"
	@docker run --rm --entrypoint sh "$(FOUNDATION_SERVICE_IMAGE)" -c 'test -r /app/web/index.html && ! command -v node'
	@docker run --rm --entrypoint python "$(FOUNDATION_SERVICE_IMAGE)" -c 'from converge_foundation_service.asgi import app; assert str(app.state.settings.web_dist_dir) == "/app/web"'
	@docker run --rm \
		--env AGENT_ENVD_ENVIRONMENT_ID=image-check \
		--env AGENT_ENVD_EXECUTION_ISOLATION=disabled \
		--entrypoint agent-envd "$(SANDBOX_IMAGE)"

.PHONY: python-check
python-check: lint typecheck test ## Run the fast Python workspace gate

.PHONY: python-check-all
python-check-all: python-check python-build docs-build ## Run the complete Python and documentation gate

.PHONY: check
check: eip-check foundation-web-check python-check rust-check sdk-check ## Run the fast repository gate

.PHONY: check-all
check-all: eip-check foundation-web-check-all python-check-all rust-check-all sdk-check-all ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist site target sdk/python/dist sdk/rust/target
	@npm --prefix apps/foundation-web run clean
	@npm --prefix sdk/typescript run clean

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
