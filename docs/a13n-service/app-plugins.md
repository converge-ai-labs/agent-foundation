---
title: Extend Service with App plugins
description: Install trusted application packages, enable them per organization, and keep accepted run behavior stable.
---

App plugins add business APIs, organization-scoped background work, Harness factories and packaged Console assets to Service. Operators install trusted Python packages on every serving and worker process. Organization administrators enable each App. Newly installed Apps start disabled.

App plugins run inside Service with its process permissions. Review packages before installation. [MCP Apps](mcp-apps.md) are a separate protocol for interactive tool results.

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

Service discovers definitions once during application assembly. Duplicate App IDs, factory ownership, settings or HTTP operations fail startup. Install or upgrade the wheel, then restart the deployment. Keep package versions consistent across control and workers.

`AppDefinition.factory_keys` declares owned Harness entry-point keys. Agents still select named instances through ordinary agent configuration. Service loads owned factories at startup and uses the accepted run snapshot to gate instance construction. Standalone factories continue to use `plugins.keys`.

`OrganizationSweep` callbacks receive the existing Runtime and one enabled organization ID. Service selects organizations and closes SQL sessions before callbacks. Callbacks own bounded business work and replica-safe claims. Optional `startup(runtime, stack)` opens process resources and registers cleanup on the existing exit stack. App definitions expose no tables or migrations; prepare business databases with your application's operator workflow.

## Enable an organization

Read the installed inventory and its organization ETag:

```http
GET /api/v1/organizations/org_example/app-plugins
```

Enable one installed App with that ETag:

```http
PUT /api/v1/organizations/org_example/app-plugins/acme.reports
If-Match: "org_example:1"
Content-Type: application/json

{"enabled": true}
```

Organization readers can inspect inventory. Organization admins can change policy. Browser changes use the normal session, CSRF and Origin rules. The ETag is the organization's version: another organization edit can make it stale. Read again after a 412 response. Real policy changes are audited; repeating the current value changes neither version nor audit history.

Business endpoints live under `/api/v1/apps/acme.reports/`. Each request uses existing workspace selection and authorization. A disabled App returns `disabled` before its request dependencies execute. Previously admitted work can finish and clean up.

## Keep run behavior stable

Each accepted run freezes organization policy, package versions and factory ownership. Later turns and worker recovery use that snapshot. Inline children share its availability ceiling while keeping their own instance declarations. A new durable child or resume successor captures current policy.

A worker with a different enabled package version or ownership fails explicitly before plugin construction. Roll out packages consistently and drain old work when upgrading. If a resume successor restores an unfinished tool now disabled, it fails with `deferred_surface_mismatch` before executing that tool. Disabling preserves historical results and saved plugin state.

## Package browser assets

`Frontend(package="acme_reports", directory="static", entry="index.js", styles=("index.css",))` declares a prebuilt ESM asset tree and bridge version `1`. Include every relative chunk, style and font in the wheel. Service serves the tree from same-origin versioned URLs and keeps extracted package resources alive until shutdown. Missing files return 404. A refresh reads uncached current catalog metadata; asset bytes are immutable for their package version.

Packaged code is public like Console assets. Business API authorization remains independent. Change the package version whenever asset bytes change.
