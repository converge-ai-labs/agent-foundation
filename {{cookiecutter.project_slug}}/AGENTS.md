# {{ cookiecutter.project_name }} development guide

Read `README.md`, the existing backend, frontend and tests before changing this project. The App is trusted installed Service code, packaged independently from its host. Keep canonical code and documentation in English.

## Boundaries

- Keep the App ID `{{ cookiecutter.app_id }}` consistent across the entry point, definition, router and frontend contribution.
- Use the host App router for authentication, organization policy and initial workspace admission. Business mutations still require the appropriate workspace/resource write authorization.
- Use the host `@a13n/console` bridge and React tree. Do not create another React root or bundle a second runtime. Scope CSS to this App.
- `.service-ref` owns the compatible host source revision. Inspect it before using a newer Service API; update it deliberately when adopting a compatible revision. Then run `make setup HOST_SOURCE=/absolute/path/to/checkout-at-new-ref` against a checkout matching the new pin; an existing `.host/agent-foundation` checkout is not updated automatically.

## Optional persistence

This template deliberately initializes no database, models or migration history. Add persistence only for a concrete business requirement, following the README and the host's App persistence guide. The default host pin predates the persistence SDK; adopt a revision containing `AppDatabase` and `apps db` before using it.

- Declare one `AppDatabase` with the App's ID before declaring ordinary SQLAlchemy models. Use its independent `metadata`, `name(...)` for owned tables and named objects, and `AppDefinition(database=database)` for registration.
- An optional prefix override may come from deployment configuration. Resolve it before model imports and use the same value for CLI, control and worker processes. A changed prefix does not move existing data; preserve initialized App ID/prefix bindings.
- Keep App tables in PostgreSQL `public`, with the `app__{label}__` namespace obtained through `database.name(...)` and `migration_name(...)`; never hardcode the resolved prefix. Keep their Alembic history independent from Core and other Apps. Native tables are read-only DDL references; use `database.reference(...)` for foreign-key resolution and ordinary native ORM models for joins.
- Generate revisions with `a13n-service apps db revision` against a disposable development/CI PostgreSQL server, do not start revisions from scratch or generate against existing business state. Edit the generated draft for changes autogenerate cannot infer. Review the draft's locks, destructive changes, data migration, interruption behavior and rolling compatibility.
- Generated identifiers resolve through `migration_name(...)`. Raw SQL, expression text, functions and triggers need explicit portable naming and review. Do not recreate Core shared functions in an App base revision.
- The existing `src` packaging includes Python migration resources; verify the built wheel and run `apps db check` in CI with a PostgreSQL service whose role has `CREATE DATABASE`. Reuse `scratch_database` to replay real history in integration tests rather than substituting `metadata.create_all` for migration coverage.
- Reuse `transaction(runtime.storage)` and `short_session(runtime.storage)` from the host. Keep SQL sessions around one short database operation, outside model/tool execution, external I/O, waits and streams. Do not create a second runtime connection pool.
- Host `all`/`control` auto migration includes all installed App databases by default, independently of organization enablement. Respect the deployment's `auto_migrate_apps` opt-out and `auto_migrate` master switch. Startup callbacks, worker execution, readiness and organization enablement must not prepare schemas themselves. Uninstalling an App retains its data and migration identity.

## Validation and delivery

Use the Make targets in `README.md`: `make check`, `make test`, and `make verify` for the installed wheel and source distribution. Add business tests for authorization, tenant isolation and persistence as those capabilities are introduced. When persistence is present, also check packaged migration/model parity on a fresh database.

Preserve unrelated work and keep changes focused. Publishing packages, creating remote repositories, pushing, deploying and merging require user authorization; build/test instructions alone grant none of those actions.
