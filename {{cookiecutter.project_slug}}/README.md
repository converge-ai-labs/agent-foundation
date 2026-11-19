# {{ cookiecutter.project_name }}

An independently packaged Service App (`{{ cookiecutter.surface }}`).

## Develop locally

Use Python 3.13, uv, Make and Docker Compose v2 on Linux or macOS.{% if cookiecutter.surface != 'backend-only' %} Install Node.js 24 and pnpm 10.30.3 for the frontend.{% endif %}

```bash
make dev
```

The command installs the Service version in `pyproject.toml` and this App's editable Python package in `.venv`. Compose starts PostgreSQL, Redis and an S3-compatible store; the Service wheel runs on the host and serves its bundled Console. On first start, the launcher migrates the database and creates a local organization, workspace and administrator.

Open the printed **Console** URL and sign in with the local credentials printed in the terminal. Then open the **App plugins** URL and enable **{{ cookiecutter.project_name }}**.
{% if cookiecutter.surface != 'backend-only' %}Open the App under **Apps** in the workspace sidebar.{% else %}This backend-only App has no Console page; its workspace-scoped endpoint is `GET /api/v1/apps/{{ cookiecutter.app_id }}/summary`.{% endif %}
{% if cookiecutter.surface == 'backend-only' %}
Create a workspace API key with **read** access, then use the URL printed by `make dev` to call the App:

```bash
export SERVICE_URL=http://127.0.0.1:8080  # Replace with the URL printed by make dev.
export A13N_API_KEY=your-workspace-api-key
curl --fail-with-body \
  -H "Authorization: Bearer $A13N_API_KEY" \
  "$SERVICE_URL/api/v1/apps/{{ cookiecutter.app_id }}/summary"
```

The API key selects its workspace, so no `X-Workspace-ID` header is needed.
{%- endif %}

- **Python:** Edit `src/`; the launcher restarts Service on save. To attach your IDE without restarts, run `make dev RELOAD=0`. Run `make dev` again after dependency or migration changes.
{% if cookiecutter.surface != 'backend-only' -%}
- **TSX/CSS:** Edit `frontend/src/`, wait for the rebuild, and reload the App page. Browser DevTools can use the development sourcemaps. Production builds use separate output.
{% endif -%}
- **Lifecycle:** Ctrl+C stops Service and the watch process. `make dev-down` also stops the infrastructure without deleting volumes. Set `PORT=8081` when another checkout uses the default Console port 8080. Reuse the printed hostname so browser sessions stay separate across checkouts.
- **Upgrade Service:** Edit the pinned `a13n-service` dependency in `pyproject.toml` and rerun `make dev`. Commit the resulting `uv.lock` with your project.

## Build and distribute

`make verify` lints the source, builds wheel and sdist and tests the wheel in a temporary environment. Your editable install remains in place. `make install HOST_PYTHON=/path/to/host/.venv/bin/python` installs the wheel into a compatible Service environment; restart the host afterward. {% if cookiecutter.surface != 'backend-only' %}Bump the App package version when changing shipped frontend bytes, because production asset URLs are immutable within a package version.{% endif %}

GitHub Actions builds and verifies artifacts on pushes and pull requests. A separate `vX.Y.Z` tag workflow publishes verified distributions when configured with a `PYPI_TOKEN` in the `pypi` environment. The tag must match the project version.

## Where to make changes

- `src/{{ cookiecutter.module_name }}/app.py`: App definition{% if cookiecutter.surface != 'frontend-only' %} and the workspace-scoped `GET /summary` route{% endif %}. Service owns authentication, App activation and initial workspace admission; the App owns further business permissions.
{% if cookiecutter.surface != 'backend-only' -%}
- `frontend/src/index.tsx` and `app.css`: Console page and scoped styles. `@a13n/console` provides the host React tree and workspace-aware API. The wheel and sdist carry prebuilt assets; a wheel rebuilt from the sdist does not require Node.js.
{% endif -%}
- `tests/test_installed_app.py`: installed entry-point discovery and the selected App surface.

See the [App authoring guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/docs/a13n-service/app-plugins.md) for settings, business tools and lifecycle APIs.

## Add persistence when needed

The generated project has no database declaration, models or migration scaffold. Add them only when your App needs durable business data. See the [App persistence guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/docs/a13n-service/app-plugins.md#persist-business-data) for the complete model, packaging and test workflow.

Declare an `AppDatabase` and ordinary SQLAlchemy models in `models.py`, use its `metadata` on your declarative base, then import the module from `app.py` and pass the same database to `AppDefinition(database=database)`. Resolve deployment configuration before model imports:

```python
import os

from a13n_service.app_database import AppDatabase
from sqlalchemy.orm import DeclarativeBase

database = AppDatabase(
    "{{ cookiecutter.app_id }}",
    migrations="{{ cookiecutter.module_name }}:migrations",
    prefix=os.environ.get("{{ cookiecutter.module_name | upper }}_TABLE_PREFIX") or None,
)


class Base(DeclarativeBase):
    metadata = database.metadata

# On each owned model: __tablename__ = database.name("notes")
```

The optional prefix label can be selected at deployment before model imports. Leave it unset for a deterministic readable slug plus digest of the full exact App ID. IDs differing only in separators get different defaults; long IDs work without an override. Use `database.name(...)` rather than hardcoding names. A label is lowercase snake case, starts with a letter and fits 24 bytes; the physical namespace is `app__{label}__` in PostgreSQL `public`. Configure the same value in migration, control and worker processes for a deployment. Authoring may use the default because generated identifiers are portable. A retained App ID/prefix binding prevents changing an initialized deployment's identity: a different setting does not rename or move existing data.

From this project root, after adding the declaration and models:

```bash
uv run --no-sync a13n-service apps db init --app {{ cookiecutter.app_id }}
uv run --no-sync a13n-service apps db revision --app {{ cookiecutter.app_id }} -m "create business tables" \
  --scratch-url "$APP_TEST_POSTGRES_URL"
uv run --no-sync a13n-service apps db check --app {{ cookiecutter.app_id }} \
  --scratch-url "$APP_TEST_POSTGRES_URL"
```

Use a local/CI PostgreSQL server whose role can create and drop temporary databases. Generation and checks replay Core plus this App in a new disposable database and leave the configured business database unchanged. Review the generated Alembic draft for locks, data changes, downgrade and rolling compatibility. Owned generated identifiers use `migration_name(...)` so one packaged history works with deployment-selected prefixes; raw SQL and expression text need explicit portable names and review. This template's existing `src` package configuration already includes Python migration resources. Verify the built wheel with `make verify` followed by `apps db check`. For persistence CI, add a PostgreSQL service with `CREATE DATABASE` permission and run that parity check; the generated build workflow does not provision PostgreSQL.

App rows use the host's `transaction(runtime.storage)` and `short_session(runtime.storage)` scopes. Native ORM rows can participate in the same session and joins. Use `database.reference(NativeRow.__table__)` for read-only native foreign-key targets, and keep App migrations confined to their own prefix. Reuse `a13n_service.migrations.scratch.scratch_database` for tests that replay Core and the App's packaged history; close Storage before the temporary database scope ends.

For deployment, install the App wheel consistently. By default, `all` and `control` startup migrate Core and every installed App database, even for organization-disabled Apps. Set `A13N_DATABASE__AUTO_MIGRATE_APPS=false` to manage App upgrades separately, or `A13N_DATABASE__AUTO_MIGRATE=false` to disable all startup migration and run `a13n-service migrate` in a dedicated job. Plain `migrate` includes installed Apps; `--no-apps` selects Core only. `migrate --check` checks live schema parity without creating a database. Use `a13n-service apps db status --app {{ cookiecutter.app_id }}` to inspect applied and packaged heads, or `apps db upgrade --app {{ cookiecutter.app_id }}` to apply only this App after Core is current. Startup validates every installed App database before callbacks. Workers, readiness and organization enablement never run DDL. Uninstalling the package retains its namespace and data.

## Create your GitHub repository

Generation creates local files. After reviewing them:

```bash
git init -b main
git add .
git commit -m "feat(app): initialize Service App"
gh repo create {{ cookiecutter.project_slug }} --private --source=. --remote=origin --push
```

Choose `--public` instead if appropriate. No license file or project license metadata is generated; choose your project's licensing terms before public distribution. Dependencies retain their own licenses.
