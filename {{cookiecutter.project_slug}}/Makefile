SHELL := /bin/bash
.DEFAULT_GOAL := help
HOST_SOURCE ?= $(CURDIR)/.host/agent-foundation
PYTHON := $(CURDIR)/.venv/bin/python
WHEEL = $(shell "$(PYTHON)" scripts/wheel_path.py)

.PHONY: help host setup frontend build check test verify dev install
help: ## Show local development commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

host: ## Check out the compatible Service source (override HOST_SOURCE to reuse a checkout)
	@if [ ! -d "$(HOST_SOURCE)" ]; then \
		mkdir -p "$$(dirname "$(HOST_SOURCE)")"; \
		git clone --filter=blob:none --no-checkout https://github.com/converge-ai-labs/agent-foundation.git "$(HOST_SOURCE)"; \
		git -C "$(HOST_SOURCE)" checkout --detach "$$(cat .service-ref)"; \
	fi
	@test "$$(git -C "$(HOST_SOURCE)" rev-parse HEAD)" = "$$(cat .service-ref)" || { echo 'HOST_SOURCE must match .service-ref'; exit 1; }

setup: host ## Prepare an isolated Python environment and locked frontend dependencies
	UV_PROJECT_ENVIRONMENT="$(CURDIR)/.venv" uv sync --project "$(HOST_SOURCE)" --package a13n-service --no-dev --locked --python 3.13
	uv pip install --python "$(PYTHON)" 'pytest>=8,<10' 'ruff>=0.11,<1'
	pnpm --dir frontend install --frozen-lockfile
	$(MAKE) frontend
	uv pip install --python "$(PYTHON)" --no-deps -e .

frontend: ## Build the host-mounted ESM frontend into the Python package
	pnpm --dir frontend build

build: frontend ## Build wheel and source distribution with prebuilt frontend assets
	uv build --python 3.13 --wheel --sdist

check: ## Check Python formatting and lint
	"$(PYTHON)" -m ruff check src tests scripts
	"$(PYTHON)" -m ruff format --check src tests scripts

test: ## Test the installed plugin, discovery, API and packaged assets
	"$(PYTHON)" -m pytest tests -q

verify: build check ## Test the wheel installed in the development environment
	uv pip install --python "$(PYTHON)" --no-deps --reinstall "$(WHEEL)"
	$(MAKE) test

dev: ## Rebuild frontend on changes; reload the App page after each build
	pnpm --dir frontend dev

install: build ## Install wheel into an existing compatible host (HOST_PYTHON=/path/to/python)
	@test -n "$(HOST_PYTHON)" || { echo 'Set HOST_PYTHON to the Service environment Python'; exit 1; }
	uv pip install --python "$(HOST_PYTHON)" --no-deps --reinstall "$(WHEEL)"
