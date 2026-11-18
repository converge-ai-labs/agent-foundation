# {{ cookiecutter.project_name }}

An independently packaged Service App: a workspace-scoped Python API and a Console page, shipped together in one wheel. The frontend uses the host's `@a13n/console` bridge for React, query caching, authentication and workspace scope.

## Prerequisites and compatibility

Use Python 3.13, uv, Git, Make, Node.js 24 and pnpm 10.30.3 on Linux or macOS. The App Plugins API is currently a prerelease source contract. `.service-ref` pins the compatible Agent Foundation commit; `make setup` checks out that source under `.host/` and installs Service and its workspace dependencies into **this project's `.venv`**. A bare install of the current PyPI `a13n-service` is not a substitute for that pinned contract. Review and update the pin when adopting a newer Service release.

## Build and test

```bash
make setup
make verify
```

`make setup` installs the plugin editable after building its frontend. To reuse a compatible source checkout, run `make setup HOST_SOURCE=/absolute/path/to/agent-foundation`. The checkout must match `.service-ref`; its own virtual environment is left unchanged. `make verify` builds the frontend, wheel and sdist, checks Python code, installs the wheel and tests real entry-point discovery and packaged assets. Run `make setup` again to return to editable development after `make verify`.

| Command                                                   | Purpose                                                                 |
| --------------------------------------------------------- | ----------------------------------------------------------------------- |
| `make frontend`                                           | Build frontend assets into `src/{{ cookiecutter.module_name }}/static/` |
| `make dev`                                                | Watch frontend source and rebuild the host-mounted bundle               |
| `make build`                                              | Create wheel and sdist in `dist/`                                       |
| `make check`                                              | Check Python formatting and lint                                        |
| `make test`                                               | Test the installed plugin                                               |
| `make install HOST_PYTHON=/path/to/host/.venv/bin/python` | Build and install into a compatible Service environment                 |

The GitHub Actions workflow runs these build/test steps on pushes and pull requests and uploads wheel/sdist artifacts. It also rebuilds a wheel from the sdist using its prebuilt frontend. The separate tag-triggered release workflow follows the same build-artifact → publish-job pattern as Service. Configure your GitHub `pypi` environment with a `PYPI_TOKEN` secret and any required approvals, choose a unique distribution name, then update `pyproject.toml` and push a matching `vX.Y.Z` tag to publish. Release tags must match the current project version. The publish job only downloads verified artifacts and runs `uv publish`; it does not rebuild. Ordinary pushes and pull requests never publish. Deployment remains owned by your project.

## Open the App in Console

1. Prepare and start a Service checkout at `.service-ref` using its [local development guide](https://github.com/converge-ai-labs/agent-foundation/blob/{{ cookiecutter.service_ref }}/dev/service/README.md). Use `make dev-status` in that checkout to discover its URL.
2. Install this plugin into that checkout's Service environment with `make install HOST_PYTHON=/absolute/path/to/host/.venv/bin/python`.
3. Restart Service using that prepared environment. For the source development launcher, run `PATH="$PWD/.venv/bin:$PATH" make dev TRACES=none` from the host checkout so dependency synchronization does not remove the additional plugin.
4. In Console, open **Settings → Organization → App plugins**, enable this App, and then open it under **Apps** in a workspace. Its route is `/workspace/{workspaceId}/apps/{{ cookiecutter.app_id }}/overview`.

For live frontend development, install this project editable into the prepared host environment with `uv pip install --python /path/to/host/.venv/bin/python --no-deps -e .`, run `make dev` here, and reload the App page after each build. `make dev` emits the real host-mounted bundle; it does not run a separate React application. Restart Service after Python changes, installation or upgrades. Increment the version in `pyproject.toml` when changing shipped frontend bytes: Service versions its frontend URLs by the installed distribution version. The Makefile selects the wheel using the current manifest version even when older artifacts remain in `dist/`.

## Where to make changes

- `src/{{ cookiecutter.module_name }}/app.py`: App definition and `GET /summary`. The host owns authentication, organization activation and initial workspace admission. Add explicit workspace write authorization before any business mutation.
- `frontend/src/index.tsx`: the overview page. `useApp().request()` scopes requests to `/api/v1/apps/{{ cookiecutter.app_id }}/`; `queryKey()` scopes caches to the App and current organization/workspace.
- `frontend/src/app.css`: styles scoped to `.{{ cookiecutter.project_slug }}-overview`. Use host design tokens; App CSS shares the document.
- `pyproject.toml`: entry point and asset packaging. Both wheel and sdist include the built frontend; rebuilding a wheel from the sdist needs no Node.js.
- `tests/test_installed_app.py`: installed distribution discovery, assets and the example handler. Host authentication and activation are Service's integration-test responsibility.

The App is trusted installed code. It uses the host React tree, not its own root or providers, and does not override workspace home. See the \[App authoring guide\](https://github.com/converge-ai-labs/agent-foundation/blob/{{ cookiecutter.service_ref }}/docs/a13n-service/app-plugins.md) for settings, business tools and lifecycle APIs.

## Add persistence when needed

The generated project has no database declaration, models or migration scaffold. Add them only when your App needs durable business data. First update `.service-ref` to a compatible Service revision that includes `a13n_service.app_database.AppDatabase` and `apps db` commands, then run `make setup HOST_SOURCE=/absolute/path/to/checkout-at-new-ref` using a checkout whose HEAD matches the new pin. Updating `.service-ref` alone does not update an existing `.host/agent-foundation` checkout. The default template pin predates this optional SDK. See the [App persistence guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/docs/a13n-service/app-plugins.md#persist-business-data) for the complete model, packaging and test workflow.

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
