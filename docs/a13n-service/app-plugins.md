---
title: Extend Service with App plugins
description: Package and install trusted Python App plugins to add custom APIs, Console pages, database tables, Harness plugins, and metrics to Service.
---

App plugins extend Service with domain APIs (`/api/v1/apps/<app_id>/...`), organization background sweeps, Harness plugin factories, PostgreSQL tables, and Console UI pages while reusing Service's authentication, workspaces, and agent runtime.

> [!IMPORTANT]
> App plugins run in-process with Service permissions. Install identical package versions across all `control` and `worker` processes (or `all`), and enable each App per organization.

## Scaffold and run an App locally

Generate a standalone project from the [App plugin template](https://github.com/converge-ai-labs/agent-foundation/tree/template/app-plugin) and start the local stack:

```bash
uvx cookiecutter gh:converge-ai-labs/agent-foundation --checkout template/app-plugin
cd my-service-app
make dev
```

`make dev` starts PostgreSQL, Redis, and S3, runs migrations, and launches Service with hot reload. Sign in to Console, open **Settings → Organization → App plugins**, and enable your App. See [`examples/service-app-plugin`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/service-app-plugin) for a complete reference implementation.

## Register an App definition

Expose a zero-argument factory under `a13n_service.app_plugins` matching `AppDefinition.app_id`:

```toml title="pyproject.toml"
[project.entry-points."a13n_service.app_plugins"]
"acme.reports" = "acme_reports.app:definition"
```

```python title="acme_reports/app.py"
from a13n_service.app_definitions import AppDefinition
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = app_router("acme.reports")


@router.get("/summary", name="get_acme_report_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(actor: Actor, workspace_id: WorkspaceId) -> dict[str, str]:
    return {"workspace_id": workspace_id, "requested_by": actor.id}


def definition() -> AppDefinition:
    return AppDefinition(app_id="acme.reports", name="Reports", routers=(router,))
```

- **`factory_keys`:** Claim owned Harness plugin factories (registered under `a13n_harness.plugins`; see [Plugins and extensions](../a13n-harness/plugins.md#publish-a-plugin-factory)), e.g. `factory_keys=frozenset({"acme.audit"})`.
- **`OrganizationSweep` & `startup`:** Use `startup(runtime, stack)` for per-process resources and `OrganizationSweep` for replica-safe periodic background work on enabled organizations.

## Use the in-process App SDK and Run seals

Import `a13n_service.app_sdk` to manage native Threads, Schedules, and App-owned egress credentials atomically alongside your business tables:

- **Threads & submissions:** Call `threads.prepare_admission(session, runtime, workspace_id, principal_id, app_id=...)` before locking App rows, then call `threads.create_thread_in_transaction` or `threads.submit_message_in_transaction` using a workspace Service Account with `run` permission. Use `threads.update_thread_resources` to update a root Thread and its shared-Environment descendants.
- **App-owned egress credentials:** Manage tokens with `egress.create_app_credential`, `rotate_app_credential`, and `revoke_app_credential` (or their `*_in_transaction` variants to commit with App state; active Sessions are notified after commit).
- **Thread egress bindings:** Call `threads.update_app_egress_secrets` with `set_secrets` (`egress.SecretReference`) or `remove_envs` on an idle shared Thread subtree while preserving existing destinations and other owners' secrets. When initializing an unset binding, pass `initial_destinations` (such as `AllowlistDestinations(mode="allowlist", hosts=("api.example.com",))` from `a13n_envd_client.eip.v1`).
- **Run seals:** Subscribe to terminal or `waiting` Run completions via `RunSealSubscription(callback, timeout=5)` on `AppDefinition(run_seals=(...,))`. Treat seal hints as at-least-once/best-effort wakeups and reconcile durable state in an `OrganizationSweep`.

## Persist business data

Use `AppDatabase` to manage App tables in PostgreSQL (`public` schema with an isolated table/migration prefix) and join native Service rows on the same pool:

```python title="acme_reports/models.py"
from a13n_service.app_database import AppDatabase
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

database = AppDatabase("acme.reports", migrations="acme_reports:migrations")
database.reference(WorkspaceRow.__table__)


class Base(DeclarativeBase):
    metadata = database.metadata


class Note(Base):
    __tablename__ = database.name("notes")
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    body: Mapped[str]
```

Pass `database=database` to `AppDefinition`, then author and verify migrations against a scratch PostgreSQL URL:

```bash
a13n-service apps db init --app acme.reports
a13n-service apps db revision --app acme.reports -m "create notes" --scratch-url "$APP_TEST_POSTGRES_URL"
a13n-service apps db check --app acme.reports --scratch-url "$APP_TEST_POSTGRES_URL"
```

Use `short_session(runtime.storage)` or `transaction(runtime.storage)` from `a13n_service.infra.db` for short async queries. `control`, `all`, or `a13n-service migrate` upgrades Core and installed App schemas before `worker` replicas start.

## Add a Console page

Bundle a frontend ESM entry with `@a13n/console` externalized, export an `AppContribution` as the default export, and attach `Frontend` to `AppDefinition`:

```tsx
import { React, useApp, useQuery } from "@a13n/console";
import type { AppContribution } from "@a13n/console";

function ReportsPage() {
  const app = useApp();
  const summary = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: async ({ signal }) => (await app.request("/summary", { signal })).json(),
  });
  return <div>{summary.data?.workspace_id}</div>;
}

export default {
  pages: [{ path: "reports", label: { en: "Reports", "zh-CN": "报告" }, component: ReportsPage }],
} satisfies AppContribution;
```

```python
from a13n_service.app_plugins import Frontend

frontend = Frontend(
    package="acme_reports", directory="static", entry="index.js", styles=("index.css",)
)
```

Export TypeScript declarations for editor completion and `make typecheck` with:

```bash
python -m a13n_service.console_sdk frontend/.console-sdk/index.d.ts
```

## Report custom metrics

Emit custom business metrics with `opentelemetry-api` under your App's prefix; Service exports them on its `/metrics` endpoint alongside host metrics:

```python
from opentelemetry import metrics

meter = metrics.get_meter("acme.reports")
previews = meter.create_counter("acme.reports.previews", description="Report previews completed")
```

Keep attribute values bounded—never attach workspace, user, or run IDs, raw URLs, or secrets as metric labels.

## Deploy and enable Apps

1. Install the exact same App wheel versions into every `control` and `worker` environment (and run `a13n-service migrate` if auto-migrate is disabled).
2. Optionally enable Apps on first bootstrap via `service.toml`:

```toml
[plugins]
bootstrap_enabled_apps = ["acme.reports"]
```

3. Enable or disable an installed App for an organization in **Settings → Organization → App plugins** or via API (`GET` the organization ETag, then `PUT /api/v1/organizations/{org_id}/app-plugins/acme.reports` with `If-Match` and `{"enabled": true}`). Accepted runs retain their enabled-App snapshot until completion.
