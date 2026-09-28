.DEFAULT_GOAL := help

A13N_SERVICE_IMAGE ?= a13n-service:local
SANDBOX_IMAGE ?= a13n-sandbox:local
A13N_HARNESS_UI_IMAGE ?= a13n-harness-ui:local
EXAMPLE_DIRS := examples/agent-app examples/environment-provider examples/plugins examples/provider-plugin examples/mcp-apps
PYTHON_TEST_DIRS ?=
PYTHON_TEST_WORKERS ?=
SERVICE_CONFIG ?= var/dev/service.toml
HARNESS_ENV ?= dev/harness/.env
HARNESS_UI_ENV ?= dev/harness-ui/.env
STATE ?=
TRACES ?= auto
SERVICE_DEV = python3 -m dev.service --traces "$(TRACES)"
CHECK_JOBS ?= 4
CHECK_TARGETS := \
	lint \
	typecheck \
	examples-check \
	frontend-check \
	rust-check \
	service-contract-check

.PHONY: install
install: ## Install locked dependencies and Git hooks
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	@command -v node >/dev/null || { echo "Node.js is required: https://nodejs.org/"; exit 1; }
	@command -v pnpm >/dev/null || { echo "pnpm is required: https://pnpm.io/installation"; exit 1; }
	@command -v cargo >/dev/null || { echo "Rust is required: https://rustup.rs/"; exit 1; }
	@echo "Synchronizing the Python workspace"
	@uv sync --locked --all-packages
	@echo "Installing frontend workspace dependencies"
	@pnpm --dir frontend install --frozen-lockfile
	@echo "Installing pre-commit hooks"
	@uv run --locked pre-commit install --install-hooks

.PHONY: sync
sync: ## Synchronize the locked Python workspace
	@uv sync --quiet --locked --all-packages

.PHONY: examples-sync
examples-sync: mcp-apps-example-assets ## Synchronize every independent example project
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
	@(cd examples/plugins && uv run --locked python -m a13n_plugin_examples.demo_web)
	@workspace_dir=$$(mktemp -d); trap 'rm -rf "$$workspace_dir"' EXIT; \
		(cd examples/environment-provider && uv run --locked environment-provider-example direct_local --workspace "$$workspace_dir")
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
setup: ## Prepare this checkout's stores, schema and local administrator without starting applications
	@$(SERVICE_DEV) setup

.PHONY: k8s-up k8s-check k8s-smoke
k8s-up: ## Build and start local kind Kubernetes, preserving credentials and creating the first administrator
	@python3 scripts/k8s_local.py up

k8s-check: ## Test the chart and local Kubernetes launcher without building images or changing a cluster
	@uv run --locked python -m pytest scripts/tests/test_k8s_local.py
	@helm lint deploy/kubernetes/helm/a13n-service --strict
	@helm lint deploy/kubernetes/helm/a13n-service -f deploy/kubernetes/helm/values-local.yaml --strict

k8s-smoke: ## Check the running local kind deployment: Console, administrator sign-in and credential storage
	@python3 scripts/deploy_smoke.py kind

.PHONY: compose-smoke
compose-smoke: ## Exercise single-host and quickstart Compose startup and persistence, then remove their disposable stacks
	@python3 scripts/deploy_smoke.py compose
	@python3 scripts/deploy_smoke.py quickstart

.PHONY: dev
dev: ## Prepare and start this checkout's scripted model, Service and Console in the background (TRACES=auto|langfuse|none)
	@$(SERVICE_DEV) dev

.PHONY: dev-foreground dev-stop
dev-foreground: ## Prepare and run the scripted model, Service and Console attached to this terminal
	@$(SERVICE_DEV) dev --foreground

dev-stop: ## Stop this checkout's running applications
	@$(SERVICE_DEV) stop

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

.PHONY: mcp-apps-example-assets mcp-apps-demo
mcp-apps-example-assets: ## Bundle the standalone MCP App example without CDN dependencies
	@npm --prefix examples/mcp-apps ci --no-audit --no-fund
	@npm --prefix examples/mcp-apps run build

mcp-apps-demo: a13n-harness-ui-assets mcp-apps-example-assets ## Run a disposable real MCP Apps demo (WEBUI_ARGS forwards options)
	@uv run --locked python -m dev.harness-ui.mcp_apps $(WEBUI_ARGS)

.PHONY: cli-landing webui-landing
cli-landing: ## Try CLI first-run setup with disposable home/config/data; clean up on exit
	@uv run --locked python -m dev.harness-ui.landing cli

webui-landing: a13n-harness-ui-assets ## Try WebUI first-run setup with disposable state (WEBUI_ARGS forwards options); Ctrl+C cleans up
	@uv run --locked python -m dev.harness-ui.landing webui $(WEBUI_ARGS)

harness-dev: harness-env ## Run SDK observation scenarios; initialize .env if missing (HARNESS_ARGS selects a scenario)
	@uv run --locked --env-file "$(HARNESS_ENV)" opentelemetry-instrument python dev/observation-demo/agent.py $(HARNESS_ARGS)

harness-ui-smoke: harness-ui-env ## Exercise HarnessUiApp with a scripted model; initialize .env if missing
	@uv run --locked --env-file "$(HARNESS_UI_ENV)" python -m dev.harness-ui.smoke

.PHONY: dev-down dev-status dev-env-list
dev-down: ## Stop this checkout's PostgreSQL and Redis, preserving their data
	@$(SERVICE_DEV) down

dev-status: ## Print this checkout's instance, URLs and listeners as JSON without changing anything
	@$(SERVICE_DEV) status

dev-env-list: ## List this machine's checkouts and their local instances
	@python3 -m dev.service.envs list

.PHONY: service-e2e
service-e2e: sync ## Run Service E2E scenarios: Control and two Workers over HTTPS with disposable stores (Docker)
	@uv run --locked python -m e2e.service $(SERVICE_E2E_ARGS)

.PHONY: langfuse-up langfuse-down langfuse-reset
langfuse-up: ## Start and authenticate machine-shared local Langfuse
	@uv run --locked python -m dev.observability.langfuse up

langfuse-down: ## Stop machine-shared local Langfuse while preserving its data
	@uv run --locked python -m dev.observability.langfuse down

langfuse-reset: ## Stop shared local Langfuse and remove all shared local trace data
	@uv run --locked python -m dev.observability.langfuse reset

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
format: sync frontend-sync ## Format repository sources
	@run_formatters() { \
		formatter_status=0; \
		for hook in end-of-file-fixer trailing-whitespace mdformat ruff-format; do \
			git ls-files --cached --others --exclude-standard -z | \
				xargs -0 uv run --locked pre-commit run "$$hook" --files || formatter_status=$$?; \
		done; \
		return "$$formatter_status"; \
	}; \
	run_formatters || run_formatters
	@cargo fmt --all
	@pnpm --dir frontend run format

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/a13n-envd-client && uv run --locked deptry a13n_envd_client)
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
	@git ls-files --cached --others --exclude-standard -z -- '*.md' | \
		python3 -c 'import os,sys; sys.stdout.buffer.write(b"\0".join(p for p in sys.stdin.buffer.read().split(b"\0") if p and os.path.isfile(p)))' | \
		xargs -0 uv run --locked mdformat --check --number
	@uv run --locked ruff check --no-fix packages scripts
	@uv run --locked ruff format --check packages scripts

.PHONY: typecheck
typecheck: sync service-boundaries ## Type-check Python package sources
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

.PHONY: verify
verify: ## Run only the checks and tests that local changes can affect (VERIFY_ARGS=--full for every gate)
	@uv run --locked python -m scripts.verify $(VERIFY_ARGS)

.PHONY: impact-record
impact-record: ## Record test impact maps for make verify (IMPACT_PACKAGES="a13n-service" limits the packages)
	@uv run --locked python -m scripts.impact record $(IMPACT_PACKAGES)

.PHONY: impact-status
impact-status: ## Show which recorded impact map each package would use
	@uv run --locked python -m scripts.impact status

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
eip-integration-test: sync ## Run EIP generation, runtime, cross-language, wire-model and Service external-target integration tests
	@cargo build --locked --package a13n-envd
	@test -x "$(CURDIR)/target/debug/a13n-envd"
	@A13N_ENVD_TEST_BINARY="$(CURDIR)/target/debug/a13n-envd" A13N_ENVD_EXECUTABLE="$(CURDIR)/target/debug/a13n-envd" uv run --locked python -m pytest $(if $(EIP_TEST_REPORT_DIR),--junitxml=$(EIP_TEST_REPORT_DIR)/eip.xml) scripts/tests/test_eip_codegen.py packages/a13n-envd-client/tests/eip packages/a13n-harness/tests/providers_environment/test_local_envd.py packages/a13n-harness/tests/providers_environment/test_local_envd_e2e.py packages/a13n-harness/tests/providers_environment/test_remote_envd.py packages/a13n-harness/tests/providers_environment/test_remote_envd_e2e.py
	@A13N_ENVD_TEST_BINARY="$(CURDIR)/target/debug/a13n-envd" uv run --project examples/environment-provider --locked python -m pytest $(if $(EIP_TEST_REPORT_DIR),--junitxml=$(EIP_TEST_REPORT_DIR)/eip-example.xml) examples/environment-provider/tests
	@A13N_ENVD_TEST_BINARY="$(CURDIR)/target/debug/a13n-envd" uv run --locked python -m pytest $(if $(EIP_TEST_REPORT_DIR),--junitxml=$(EIP_TEST_REPORT_DIR)/eip-service.xml) packages/a13n-service/tests/test_environments_envd.py
	@uv run --locked pyright packages/a13n-envd-client/a13n_envd_client packages/a13n-harness/a13n_harness/providers/environment

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
	A13N_ENVD_TEST_BINARY="$$A13N_ENVD_EXECUTABLE" uv run --locked python -m pytest packages/a13n-harness/tests/providers_environment/test_local_envd.py packages/a13n-harness/tests/providers_environment/test_local_envd_e2e.py

.PHONY: e2b-provider-test
e2b-provider-test: sync ## Run native E2B unit and opt-in live integration tests
	@uv run --locked pytest -q packages/a13n-harness/tests/providers_environment/test_e2b*.py packages/a13n-harness/tests/test_e2b_environment_live.py

.PHONY: docker-provider-test
docker-provider-test: sync ## Run Docker Provider tests
	@uv run --locked python -m pytest packages/a13n-harness/tests/providers_environment/test_docker.py

.PHONY: eip-check
eip-check: eip-verify eip-test ## Run the complete EIP protocol gate

.PHONY: python-build
python-build: sync a13n-harness-ui-assets a13n-service-assets ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: a13n-harness-python-build
a13n-harness-python-build: ## Build the prepared Harness release-group distributions
	@rm -rf dist
	@for package in a13n-harness a13n-stream-protocol; do \
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
a13n-service-python-build: sync a13n-service-assets ## Build only the Service Python distributions
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

.PHONY: service-contract-generate
service-contract-generate: sync frontend-sync ## Export the Service contract and regenerate Console types
	@uv run --locked python scripts/export-a13n-service-openapi.py
	@pnpm --dir frontend --filter a13n-console run generate

.PHONY: service-contract-check
service-contract-check: sync frontend-sync ## Check Service contract and Console type drift without changing files
	@uv run --locked python scripts/export-a13n-service-openapi.py --check
	@pnpm --dir frontend --filter a13n-console run generate:check

.PHONY: frontend-sync
frontend-sync: ## Install locked frontend workspace dependencies
	@pnpm --dir frontend install --frozen-lockfile

.PHONY: frontend-check
frontend-check: frontend-sync ## Check frontend formatting, types, and API contract
	@pnpm --dir frontend run check

.PHONY: frontend-test
frontend-test: frontend-sync ## Run frontend unit and interaction tests
	@pnpm --dir frontend run test

.PHONY: frontend-build
frontend-build: a13n-ui-build a13n-console-build a13n-harness-ui-webui-build ## Build frontend applications and the UI showcase

.PHONY: frontend-check-all
frontend-check-all: frontend-check frontend-test frontend-build ## Run the complete frontend gate

.PHONY: a13n-ui-build
a13n-ui-build: frontend-sync ## Build the shared UI showcase
	@pnpm --dir frontend --filter a13n-ui run build

.PHONY: a13n-console-build
a13n-console-build: frontend-sync ## Build Console production assets
	@pnpm --dir frontend --filter a13n-console run build

.PHONY: a13n-harness-ui-webui-build
a13n-harness-ui-webui-build: frontend-sync ## Build Harness UI WebUI production assets
	@pnpm --dir frontend --filter a13n-harness-ui-webui run build

.PHONY: a13n-service-assets
a13n-service-assets: a13n-console-build ## Place the Console production build in the a13n-service package it serves from
	@rm -rf packages/a13n-service/a13n_service/static
	@cp -R frontend/apps/a13n-console/dist packages/a13n-service/a13n_service/static

.PHONY: a13n-harness-ui-assets
a13n-harness-ui-assets: a13n-harness-ui-skills a13n-harness-ui-webui-build ## Prepare generated Harness UI WebUI files for Python packaging
	@uv run --locked python scripts/prepare-a13n-harness-ui-assets.py

.PHONY: build
build: frontend-build python-build rust-build ## Build all workspace and application artifacts

.PHONY: db-migrate
db-migrate: sync ## Generate a migration (usage: make db-migrate msg="description")
	@bash dev/service/db-migrate.sh "$(msg)"

.PHONY: db-upgrade
db-upgrade: service-config-check sync ## Upgrade this checkout's a13n-service database to all heads
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" migrate

.PHONY: db-check
db-check: service-config-check sync ## Fail unless this checkout's a13n-service database is at all heads
	@uv run --locked a13n-service --config "$(SERVICE_CONFIG)" migrate --check

.PHONY: release-check
release-check: ## Validate a component version (component=a13n-harness|a13n-harness-ui|a13n-logging|a13n-service|a13n-envd version=X.Y.Z or X.Y.Z-rc.N)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-a13n-service
image-a13n-service: ## Build the local a13n-service container image, which serves the Console
	@docker build -f deploy/docker/images/a13n-service/Dockerfile -t "$(A13N_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with a13n-envd
	@docker build -f deploy/docker/images/sandbox/Dockerfile -t "$(SANDBOX_IMAGE)" .

.PHONY: a13n-harness-ui-image-context
a13n-harness-ui-image-context: a13n-harness-ui-assets ## Stage source wheels and locked constraints for the UI image
	@uv run --locked python scripts/prepare_harness_ui_image.py

.PHONY: image-a13n-harness-ui
image-a13n-harness-ui: a13n-harness-ui-image-context ## Build the local packaged Harness UI image
	@docker build -f deploy/docker/images/a13n-harness-ui/Dockerfile -t "$(A13N_HARNESS_UI_IMAGE)" dist/a13n-harness-ui-image

.PHONY: images
images: image-a13n-service image-sandbox image-docker-environment image-a13n-harness-ui ## Build all local container images

.PHONY: image-check-a13n-harness-ui
image-check-a13n-harness-ui: ## Check an existing UI image locally; not a CI or release prerequisite
	@uv run --locked python scripts/check_harness_ui_image.py "$(A13N_HARNESS_UI_IMAGE)"

.PHONY: image-check-a13n-service
image-check-a13n-service: ## Smoke-check the existing a13n-service container image
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(A13N_SERVICE_IMAGE)")" = "app"
	@docker run --rm --entrypoint sh "$(A13N_SERVICE_IMAGE)" -c '! command -v node'
	@docker run --rm "$(A13N_SERVICE_IMAGE)" a13n-service --config /app/service.toml run --help >/dev/null
	@docker run --rm --entrypoint python "$(A13N_SERVICE_IMAGE)" -c 'from a13n_service.app import CONSOLE; assert (CONSOLE / "index.html").is_file()'
	@test "$$(docker run --rm --user root "$(A13N_SERVICE_IMAGE)" sh -c 'id -u; grep ^CapEff /proc/self/status')" = "$$(printf '10001\nCapEff:\t0000000000000000')"

.PHONY: image-check-sandbox
image-check-sandbox: ## Smoke-check sandbox defaults, development account, sudo and daemon startup
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(SANDBOX_IMAGE)")" = "root"
	@docker run --rm --user sandbox --entrypoint sh "$(SANDBOX_IMAGE)" -ec '\
		test "$$(id -u):$$(id -g)" = "1000:1000"; \
		test "$$A13N_ENVD_EXECUTION_UID:$$A13N_ENVD_EXECUTION_GID" = "1000:1000"; \
		test "$$A13N_ENVD_FULL_CONTROL" = "true"; \
		test "$${A13N_ENVD_EGRESS_MODE:-inherit}" = "inherit"; \
		test -w /workspace && test -w /home/sandbox; \
		test "$$(sudo -n id -u)" = "0"'
	@docker run --rm "$(SANDBOX_IMAGE)"

.PHONY: image-check
image-check: images ## Build and smoke-check all container images
	@$(MAKE) --no-print-directory image-check-a13n-service image-check-sandbox image-check-a13n-harness-ui image-check-docker-environment

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
check-all: dev-state-check eip-check examples-check-all frontend-check-all python-check-all rust-check-all service-contract-check ## Run the complete repository gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist examples/plugins/dist site target packages/a13n-harness-ui/a13n_harness_ui/static
	@pnpm --dir frontend run clean

.PHONY: help
help: ## Show available commands
	@printf 'Usage: make <target> [VARIABLE=value]\n\n'
	@printf 'Common workflows:\n'
	@printf '  make cli                           Start Harness UI; create its .env if missing\n'
	@printf '  make webui                         Build and start WebUI; print a generated login link\n'
	@printf '  make webui WEBUI_ARGS="--port 9000" Forward WebUI server options\n'
	@printf '  make cli CLI_ARGS="--help"         Forward options or subcommands to Harness UI\n'
	@printf '  make env-init                      Prepare both development .env files only\n'
	@printf '  make dev                           Start the local Service and Console in the background\n'
	@printf '  make dev-status                    Show the local URLs, ports and listeners\n'
	@printf '  make dev-stop                      Stop the applications; make dev-down also stops the stores\n'
	@printf '  make test PYTHON_TEST_DIRS=scripts/tests PYTHON_TEST_WORKERS=0\n'
	@printf '  make check CHECK_JOBS=4             Format, then run fast checks\n\n'
	@printf 'Environment overrides: HARNESS_UI_ENV=path, HARNESS_ENV=path, SERVICE_CONFIG=path\n'
	@printf 'Existing .env files are preserved; missing files need a sibling .env.example.\n\n'
	@printf 'All targets:\n'
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-38s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: service-dev dev-reset dev-state-check
service-dev: ## Prepare and run only this checkout's scripted model and Service in the foreground
	@$(SERVICE_DEV) service-dev

dev-reset: ## Delete this checkout's state and rebuild it (STATE=empty or STATE=seeded)
	@$(SERVICE_DEV) reset "$(STATE)"

dev-state-check: sync ## Check the local development tools, including seeding a disposable Service
	@uv run --locked ruff check --no-fix dev/service
	@uv run --locked ruff format --check dev/service
	@uv run --locked pyright dev/service
	@uv run --locked python -m pytest dev/service/tests -q --tb=short

DOCKER_ENVIRONMENT_IMAGE ?= a13n-docker-environment:local
.PHONY: image-docker-environment image-check-docker-environment
image-docker-environment: ## Build the native Docker execution image without Envd
	@docker build -f deploy/docker/images/docker-environment/Dockerfile -t "$(DOCKER_ENVIRONMENT_IMAGE)" deploy/docker/images/docker-environment

image-check-docker-environment: ## Validate native Docker image prerequisites
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(DOCKER_ENVIRONMENT_IMAGE)")" = "sandbox"
	@docker run --rm --entrypoint sh "$(DOCKER_ENVIRONMENT_IMAGE)" -c 'python3 --version && git --version && bash --version && node --version && npm --version && test -w /workspace && test -w /tmp/a13n && ! command -v a13n-envd'

.PHONY: service-e2e-docker
service-e2e-docker: sync image-docker-environment ## Run the environment journey on the native Docker provider
	@DOCKER_ENVIRONMENT_IMAGE="$(DOCKER_ENVIRONMENT_IMAGE)" uv run --locked python -m e2e.service -k docker --require-all

.PHONY: service-boundaries
service-boundaries: sync ## Verify Service import direction
	@uv run --locked lint-imports --config packages/a13n-service/.importlinter

.PHONY: service-e2e-check
service-e2e-check: sync ## Check Service E2E code, fixtures and scenario selection without Docker
	@uv run --locked ruff check --no-fix e2e
	@uv run --locked ruff format --check e2e
	@uv run --locked pyright e2e
	@uv run --locked python -m pytest scripts/tests/test_service_e2e_tooling.py -q

.PHONY: service-config-check
service-config-check:
	@test -f "$(SERVICE_CONFIG)" || { echo "Missing Service settings: $(SERVICE_CONFIG). Run make setup first or set SERVICE_CONFIG to an existing file." >&2; exit 2; }

.PHONY: console-review
console-review: sync frontend-sync ## Launch Console with disposable real Service stores and model fixture
	@uv run --locked python -m dev.service.console_review --directory "$(CONSOLE_REVIEW_DIR)"
