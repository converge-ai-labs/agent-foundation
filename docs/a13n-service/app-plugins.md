---
title: Extend Service with App plugins
description: Install trusted application packages, enable them per organization, and keep accepted run behavior stable.
---

App plugins add business APIs, organization-scoped background work, Harness factories and packaged Console assets to Service. Operators install trusted Python packages on every serving and worker process. Organization administrators enable each App. Newly installed Apps start disabled.

App plugins run inside Service with its process permissions. Review packages before installation. [MCP Apps](mcp-apps.md) are a separate protocol for interactive tool results.

## Start an independent App

The [App plugin template](https://github.com/converge-ai-labs/agent-foundation/tree/template/app-plugin) creates a standalone project with a Python backend, a host-mounted Console frontend, a Makefile, and GitHub Actions checks. It does not include a license; choose licensing terms before distributing your project. The existing [Reports example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin) remains a separate full-stack integration example.

Install Git, uv, Make, Python 3.13, Node.js 24, and pnpm 10.30.3. Generate a project and answer the prompts:

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin
```

Cookiecutter creates local files in the selected project directory. In that directory, run:

```bash
make setup
make verify
make dev
```

`make setup` prepares an isolated environment and installs the Service source pinned by `.service-ref`. `make verify` builds the frontend, wheel and source distribution, then tests the installed wheel. Run `make setup` again to restore editable development after verification. `make dev` watches and rebuilds the frontend. For live changes, install the App editable into a compatible Service host, enable it, and reload the App page after a rebuild. Start Service and Console separately; the generated README covers host setup and installation.

The current template pins a prerelease Service source commit for the App plugin API. Use a compatible host at that revision until a release supports the API. Project generation does not create a remote repository. If you want one, initialize and commit the generated project, then create a repository with GitHub CLI or on GitHub; the generated README includes the steps. The optional release workflow builds and verifies artifacts for a matching `vX.Y.Z` tag, then publishes through a `pypi` environment configured with `PYPI_TOKEN`.

## Register a package

Declare one zero-argument definition function in your wheel:

```toml
[project.entry-points."a13n_service.app_plugins"]
"acme.reports" = "acme_reports.app:definition"
```

Use the host router so policy is checked before your dependencies and handler:

```python
from a13n_service.app_definitions import AppDefinition
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = app_router("acme.reports")


@router.get("/summary", name="get_acme_report_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(actor: Actor, workspace_id: WorkspaceId) -> dict[str, str]:
    # Read admission is complete. Check additional business permissions here when needed.
    return {"workspace_id": workspace_id, "requested_by": actor.id}


def definition() -> AppDefinition:
    return AppDefinition(app_id="acme.reports", name="Reports", routers=(router,))
```

The entry-point name must match `AppDefinition.app_id`. Service loads every installed definition during application assembly, including disabled Apps. Duplicate App IDs, factory ownership, settings or HTTP operations fail startup. Install the wheel on every control and worker process, then restart the deployment. Use the same package version across the deployment.

`AppDefinition.factory_keys` declares owned Harness factory keys. Register each factory under `a13n_harness.plugins`, as shown in [Plugins and extensions](../a13n-harness/plugins.md#publish-a-plugin-factory), and add its key to the definition, for example `factory_keys=frozenset({"acme.audit"})`. Configure named plugin instances on the agent as usual. Service loads owned factories at startup and gates their instances using the accepted run snapshot. Standalone factories are selected through `plugins.keys`.

`OrganizationSweep` callbacks receive the existing Runtime and one enabled organization ID. Service selects organizations and closes SQL sessions before callbacks. Callbacks own bounded business work and replica-safe claims. Each organization gets the declared timeout; a failed callback leaves the rest of the batch running and marks the pass failed. Optional `startup(runtime, stack)` opens process resources and registers cleanup on the existing exit stack. Startup runs for each installed App, regardless of organization enablement. Apps can declare an optional database with independent migrations, as described below. Service validates installed App database heads before running any App startup callback.

## Persist business data

Use `AppDatabase` when an App needs tables in the host PostgreSQL database. Each App owns an independent migration history in `public`. App rows use ordinary SQLAlchemy models and the existing Runtime Storage, so queries can join native Service rows without a second database pool. Prefixes separate migration ownership; App code still enforces tenant scoping and authorization.

Declare the database and models in a module such as `models.py`, then import that module from `app.py` and pass the same database to `AppDefinition(database=database)`. Resolve deployment configuration before importing models:

```python
import os

from a13n_service.app_database import AppDatabase
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Optional deployment override. Leave unset for a stable App-ID-derived prefix.
database = AppDatabase(
    "acme.reports",
    migrations="acme_reports:migrations",
    prefix=os.environ.get("ACME_REPORTS_TABLE_PREFIX") or None,
)
database.reference(WorkspaceRow.__table__)  # Read-only foreign-key target, never App-owned DDL.


class Base(DeclarativeBase):
    metadata = database.metadata


class Note(Base):
    __tablename__ = database.name("notes")

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    body: Mapped[str]
```

The default physical table is `app__acme_report_8b627b7edb3d__notes`. Its label combines a readable slug with a stable digest of the full, exact App ID, so `acme.reports` and `acme_reports` get different namespaces regardless of installation order. Long App IDs work without configuration. An optional explicit label must be lowercase snake case, start with a letter and fit 24 bytes; for example `ACME_REPORTS_TABLE_PREFIX=reports_1` selects `app__reports_1__notes`. Use `database.name(...)` rather than hardcoding physical names. A deployment uses the same setting in migration, control and worker processes; authoring can use the default because generated identifiers are portable. Duplicate App IDs and conflicting explicit prefixes are rejected. After a database is initialized, its retained App ID/prefix binding prevents accidental retargeting. Changing the setting does not move existing data; moving a deployed namespace requires an explicit reviewed migration.

1. Install your App editable in a Service-compatible development environment. Run these authoring commands from the App project root. `--app` is optional when one current-project App is unambiguous.

   ```bash
   a13n-service apps db init --app acme.reports
   a13n-service apps db revision --app acme.reports -m "create notes" \
     --scratch-url "$APP_TEST_POSTGRES_URL"
   a13n-service apps db check --app acme.reports \
     --scratch-url "$APP_TEST_POSTGRES_URL"
   ```

   `init` creates only `acme_reports/migrations/versions/`; the template does not initialize persistence. `revision` and `check` each create a disposable database, replay Core and this App history, then remove it. Their PostgreSQL role needs `CREATE DATABASE` permission. Use a local/CI server. Without `--scratch-url`, these commands use the server from Service database settings, connecting to its `postgres` maintenance database. They never upgrade the configured business database.

2. Review the generated revision. Table, key, index and foreign-key identifiers use `migration_name("notes")` and related local suffixes, so one published history works with a deployment-selected prefix. Defaults and data literals retain their values. Review renames, drops, backfills, locks and rolling compatibility. Autogenerate cannot infer every schema object: write raw SQL, expression text, functions, triggers and other manual changes explicitly, using `migration_name` for owned SQL names. Core shared functions are already available; do not recreate them in an App base revision.

3. Include `migrations/versions/*.py` in the App wheel and source distribution. Verify the installed artifact with `apps db check`, then install the same package on every Service process. By default, `all` and `control` startup upgrade Core, then every installed App database. For a dedicated migration job, run:

   ```bash
   a13n-service migrate
   a13n-service apps db status --app acme.reports
   ```

   `migrate` upgrades Core first, then all installed App histories; `migrate --no-apps` prepares Core only. To apply one App after Core is current, use `a13n-service apps db upgrade --app acme.reports`. App histories remain independent of each other's installation order. Set `A13N_DATABASE__AUTO_MIGRATE=false` on serving replicas when a dedicated job owns migrations. To automate Core but manage App upgrades separately, keep the master switch enabled and set `A13N_DATABASE__AUTO_MIGRATE_APPS=false`. These switches affect startup only; explicit CLI commands still migrate. `migrate --check` checks live Core/App parity without DDL or `CREATE DATABASE` permission; `apps db check` is the disposable authoring/CI check. Startup and readiness require every installed App at its packaged head, including organization-disabled Apps. Workers, readiness and organization enablement never execute DDL. Uninstalling the package retains its tables, version table and ownership binding.

Use `transaction(runtime.storage)` or `short_session(runtime.storage)` from `a13n_service.infra.db` for business queries. Keep the session around one short database operation. A native join uses the same ORM session:

```python
from a13n_service.infra.db import short_session
from sqlalchemy import select

async with short_session(runtime.storage) as session:
    rows = (
        await session.execute(
            select(Note.body, WorkspaceRow.name)
            .join(WorkspaceRow, Note.workspace_id == WorkspaceRow.id)
            .where(WorkspaceRow.id == authorized_workspace_id)
        )
    ).all()
```

For integration tests, reuse `a13n_service.migrations.scratch.scratch_database(development_database_settings)`. Inside that scope, call `runner.upgrade(scratch, OSS)` and `apps.upgrade(scratch, OSS, database)` from `a13n_service.migrations`, then open ordinary `Storage(scratch.url.get_secret_value())`. Close Storage before leaving the scope. This exercises the packaged history and drops the database after the test. Add `apps db check` to CI; `status` checks deployment revision state, while `check` proves migration/model parity on a fresh replay.

## Report custom metrics

App plugins use the same process-wide metrics provider and private `/metrics` listener as Service. Use the standard `opentelemetry-api` package; declare `opentelemetry-api>=1.39,<2` as a direct dependency when your App imports it. No `AppDefinition` registration or separate exporter is needed. Operators enable `telemetry.metrics_port` and scrape every control and worker process as described in [monitoring](monitoring.md#metrics).

Create instruments under your App's own prefix. A meter scope alone does not namespace Prometheus series: the Service exporter omits scope labels. This example extends the `/preview` handler from the [Reports example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin) to count completed previews and measure their successful handler duration:

```python
from time import perf_counter

from opentelemetry import metrics

meter = metrics.get_meter("acme.reports")
previews = meter.create_counter("acme.reports.previews", description="Report previews completed")
preview_duration = meter.create_histogram(
    "acme.reports.preview.duration",
    unit="s",
    description="Successful report preview handler duration",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5),
)


@router.post("/preview", name="preview_acme_report")
async def preview(body: PreviewRequest, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> Preview:
    started = perf_counter()
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
    result = Preview(workspace_id=workspace_id, title=body.title)
    # Host admission and the action's write check both succeeded.
    previews.add(1, {"result": "completed"})
    preview_duration.record(perf_counter() - started, {"result": "completed"})
    return result
```

The existing example supplies the route, request/response types and authorization imports; replace its handler rather than registering a second `/preview` route. Prometheus exports `acme_reports_previews_total` and the `acme_reports_preview_duration_seconds` histogram. The counter measures successful handler calls, not distinct reports or delivery to the browser. Core HTTP metrics already cover refused/failed requests. Organization sweeps already receive host pass/failure/freshness metrics under `sweep="app:acme.reports:<name>"`; add only business measurements that those signals do not answer. Use your own Grafana panels and alerts for custom names; the bundled dashboards cover Service instruments.

Follow these rules:

- Service owns the meter provider, listener and process shutdown. Do not call `set_meter_provider()`, `serve_metrics()` or `start_http_server()` from an App. With metrics disabled, the standard API performs no export. Embedded hosts that call `build_app()` directly own their telemetry initialization.
- Creating meters/instruments during import is supported. Record observations from startup or admitted business work, after executable initialization; observations made before provider initialization are discarded. Startup runs even when all organizations disable the App, so a startup metric is process state, not tenant enablement.
- Use fixed instrument names and bounded attribute values defined in code. Never label metrics with organization/workspace/user/run IDs, model names, report titles, raw URLs, credentials or payloads. The host does not automatically sanitize custom App attributes.
- Use seconds for durations and choose histogram buckets for the operation. Count a durable business fact only after its transaction commits, using the existing `after_commit(session, callback)` boundary. An operation-attempt metric can instead count each attempt, including retries; name it accordingly.
- Observable gauge callbacks run during scrapes. Read a cached in-memory snapshot only; refresh external/SQL/Redis state in bounded admitted background work and publish a freshness timestamp alongside inventory. Disabling an App stops future organization callbacks, not previously exported counter values or cached samples; expire stale samples in dashboard queries.

## Enable an organization

Read the installed inventory and its organization ETag:

```http
GET /api/v1/organizations/org_example/app-plugins
```

Copy the `ETag` response header into `If-Match` to enable an installed App (the value below is illustrative):

```http
PUT /api/v1/organizations/org_example/app-plugins/acme.reports
If-Match: "org_example:1"
Content-Type: application/json

{"enabled": true}
```

Organization readers can inspect inventory. Organization admins can change policy. Browser changes use the normal session, CSRF and Origin rules. The ETag is the organization's version: another organization edit can make it stale. Read again after a 412 response. Real policy changes are audited; repeating the current value changes neither version nor audit history. After uninstalling a package, an administrator can clear its retained policy ID with the same PUT and `{"enabled": false}`.

Business endpoints live under `/api/v1/apps/acme.reports/`. Each request uses existing workspace selection and authorization. A disabled App returns `disabled` before its request dependencies execute. Previously admitted work can finish and clean up.

## Keep run behavior stable

Each accepted run freezes organization policy, package versions and factory ownership. Later turns and worker recovery use that snapshot. Inline children share its availability ceiling while keeping their own instance declarations. A new durable child or resume successor captures current policy.

A worker with a different enabled package version or ownership fails explicitly before plugin construction. The check covers every enabled App in the snapshot, including Apps the run's agent does not select. Roll out packages consistently and drain old work when upgrading. If a resume successor restores an unfinished tool now disabled, it fails with `deferred_surface_mismatch` before executing that tool. Disabling preserves historical results and saved plugin state.

## Add a Console page

Export an `AppContribution` as the default export of the frontend entry. Console loads enabled Apps in the current organization and renders their contributions inside its existing providers. This example displays the `/summary` endpoint defined above:

```tsx
import { React, useApp, useQuery } from "@a13n/console";
import type { AppContribution } from "@a13n/console";

type Summary = { workspace_id: string; requested_by: string };

function ReportsPage() {
  const app = useApp();
  const summary = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: async ({ signal }) => {
      const response = await app.request("/summary", { signal });
      return (await response.json()) as Summary;
    },
  });

  if (summary.isPending) return <p>Loading reports…</p>;
  if (summary.isError) return <p>Could not load reports.</p>;
  return <p>Workspace: {summary.data.workspace_id}</p>;
}

const contribution: AppContribution = {
  bridge: 1,
  pages: [{ id: "reports", label: { en: "Reports", "zh-CN": "报告" }, component: ReportsPage }],
};

export default contribution;
```

`useApp()` supplies `appId`, `organizationId`, `workspaceId`, a scoped `queryKey(...)`, and `request(path, init)`. A request path starts with `/` and remains within `/api/v1/apps/{appId}/` after normalization. The host adds the selected workspace header and uses its existing authentication, CSRF handling and transport. Business requests require a selected workspace.

In organization settings, the host selects the remembered accessible workspace, or the first active workspace when none is remembered. If only archived workspaces are available, the host selects the first one. `workspaceId` is undefined when no workspace is available. The settings scope determines where the section appears; App business HTTP requests remain workspace-scoped.

Pages appear under `/workspace/{workspaceId}/apps/{appId}/{pageId}/*`. Page IDs use lowercase letters, digits and hyphens, starting with a letter. Optional `description` and `permission` fields describe a page and control its Console visibility and route access. Business endpoints still enforce their own authorization. Labels and descriptions accept a string or an object with `en` and optional `zh-CN`. A `settings` entry uses the same fields and adds `scope: "workspace"` or `"organization"`; its host settings key is `app.{appId}.{sectionId}`.

An App can also provide `workspaceHome: Component` to replace the workspace index. Core agent routes remain available. If multiple enabled Apps claim the index, Console shows an error with a link to agents. Organization administrators can enable or disable an App in settings; successful changes update the active registry. Refreshing Console fetches the current catalog.

### Build the entry

Bundle the frontend as ESM and externalize `@a13n/console`. Console's import map supplies the shared React, query, router, translation, UI and host context exports. Import these through `@a13n/console`, use classic JSX with its exported `React`, and let Console mount the component.

The [independent App example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin) contains a Vite build, its own App definition and an installable wheel. From the repository root, build its frontend with:

```bash
pnpm --dir examples/service-app-plugin/frontend install --frozen-lockfile
pnpm --dir examples/service-app-plugin/frontend build
```

Follow the example's build and install instructions to package the generated assets, start Service and enable the App. The example implements its own `/summary` response and uses the same App ID as the snippets on this page.

The bridge also exports hooks such as `useAuth`, `useAccess`, `useWorkspace`, `useAppearance`, `useTranslation`, `useMutation`, and `useQueryClient`, plus selected UI components including `Page`, `Empty`, `Loading`, and `ErrorPage`. Console owns the React root and providers.

## Package browser assets

Add a `Frontend` declaration to the definition when the package includes a prebuilt ESM asset tree:

```python
from a13n_service.app_definitions import AppDefinition
from a13n_service.app_plugins import Frontend


def definition() -> AppDefinition:
    return AppDefinition(
        app_id="acme.reports",
        name="Reports",
        routers=(router,),  # router from the registration example above
        frontend=Frontend(
            package="acme_reports", directory="static",
            entry="index.js", styles=("index.css",),
        ),
    )
```

Include the entry, styles, relative chunks and fonts in the wheel under `acme_reports/static/`. If the generated directory is ignored by Git, configure the build backend to include it explicitly in both the wheel and source distribution, as the example does with Hatch `force-include`. The entry and declared styles are checked at startup; a missing referenced asset returns 404. Service serves assets from same-origin versioned URLs and keeps extracted resources available until shutdown. The declaration uses bridge version `1` by default. A refresh reads current catalog metadata without caching it; asset bytes are immutable for their package version.

Packaged code is public like Console assets. Business API authorization remains independent. Change the package version whenever asset bytes change.
