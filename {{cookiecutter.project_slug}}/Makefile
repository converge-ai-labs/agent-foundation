SHELL := /bin/bash
.DEFAULT_GOAL := help
PYTHON := .venv/bin/python
PORT ?= 8080
RELOAD ?= 1
INSTANCE := app-$(shell printf '%s' "$(CURDIR)" | cksum | cut -d ' ' -f 1)
COMPOSE := docker compose -p $(INSTANCE) -f compose.dev.yaml
WHEEL = $(shell "$(PYTHON)" scripts/wheel_path.py)
WATCH := $(if $(filter 0,$(RELOAD)),,--watch src)
{% if cookiecutter.surface != 'backend-only' %}
FRONTEND := --frontend frontend --assets .a13n-dev/assets
{% endif %}
.PHONY: help setup frontend console-sdk typecheck build check verify test-wheel dev dev-down install
help: ## Show local development commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install the pinned Service and this App editable
	uv sync
{%- if cookiecutter.surface != 'backend-only' %}
	pnpm --dir frontend install --frozen-lockfile
	$(MAKE) console-sdk
{%- endif %}

{% if cookiecutter.surface != 'backend-only' -%}
console-sdk: ## Refresh editor types from the installed Service
	$(PYTHON) -m a13n_service.console_sdk frontend/.console-sdk/index.d.ts

typecheck: console-sdk ## Check the App against the installed Console bridge
	pnpm --dir frontend typecheck

frontend: typecheck ## Build the App's production frontend
	pnpm --dir frontend build

{% endif %}
build:{% if cookiecutter.surface != 'backend-only' %} frontend{% endif %} ## Build a wheel and source distribution
	uv build --wheel --sdist

check: ## Check Python formatting and lint
	uv run --no-sync ruff check src tests scripts
	uv run --no-sync ruff format --check src tests scripts

verify: setup ## Build and test the wheel in a separate environment
	$(MAKE) build check
	$(MAKE) test-wheel

test-wheel: ## Test a built wheel without changing the editable environment (WHEEL=...)
	@set -e; env_dir=$$(mktemp -d); trap 'rm -rf "$$env_dir"' EXIT; \
	uv venv --python 3.13 "$$env_dir"; \
	uv pip install --python "$$env_dir/bin/python" "$(WHEEL)" 'pytest>=8,<10'; \
	"$$env_dir/bin/python" -m pytest tests -q

dev: setup ## Run Service and watch this App's sources (PORT=8080 RELOAD=1)
	@for name in $${!A13N_@}; do unset "$$name"; done; \
	.venv/bin/a13n-service apps --help | grep -qE '^  dev[[:space:]]' || { echo 'Upgrade the pinned a13n-service to a release that provides apps dev.'; exit 1; }
	@mkdir -p .a13n-dev
{%- if cookiecutter.surface != 'backend-only' %}
	pnpm --dir frontend build --mode app-dev
{%- endif %}
	$(COMPOSE) up -d --wait --wait-timeout 60
	@set -e; \
	pg=$$($(COMPOSE) port postgres 5432); \
	redis=$$($(COMPOSE) port redis 6379); \
	s3=$$($(COMPOSE) port s3 9000); \
	for name in $${!A13N_@}; do unset "$$name"; done; \
	A13N_SERVER__HOST=127.0.0.1 A13N_SERVER__PORT=$(PORT) \
	A13N_SERVER__PUBLIC_URL=http://$(INSTANCE).localhost:$(PORT) \
	A13N_SERVER__CONTROL_URL=http://127.0.0.1:$(PORT) \
	A13N_DATABASE__URL=postgresql+psycopg://app_dev:app_dev@$$pg/app_dev \
	A13N_REDIS__URL=redis://$$redis/0 A13N_OBJECTS__ENDPOINT_URL=http://$$s3 \
	exec .venv/bin/a13n-service --config service.dev.toml apps dev {{ cookiecutter.app_id }} $(WATCH) $(FRONTEND)

dev-down: ## Stop development containers without deleting their data
	$(COMPOSE) down

install: build ## Install the wheel into an existing compatible host (HOST_PYTHON=/path/to/python)
	@test -n "$(HOST_PYTHON)" || { echo 'Set HOST_PYTHON to the Service environment Python'; exit 1; }
	uv pip install --python "$(HOST_PYTHON)" --no-deps --reinstall "$(WHEEL)"
