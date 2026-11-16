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

The entry-point name must match `AppDefinition.app_id`. Service loads every installed definition during application assembly, including disabled Apps. Duplicate App IDs, factory ownership, settings or HTTP operations fail startup. Install the wheel on every control and worker process, then restart the deployment. Use the same package version across the deployment.

`AppDefinition.factory_keys` declares owned Harness factory keys. Register each factory under `a13n_harness.plugins`, as shown in [Plugins and extensions](../a13n-harness/plugins.md#publish-a-plugin-factory), and add its key to the definition, for example `factory_keys=frozenset({"acme.audit"})`. Configure named plugin instances on the agent as usual. Service loads owned factories at startup and gates their instances using the accepted run snapshot. Standalone factories are selected through `plugins.keys`.

`OrganizationSweep` callbacks receive the existing Runtime and one enabled organization ID. Service selects organizations and closes SQL sessions before callbacks. Callbacks own bounded business work and replica-safe claims. Each organization gets the declared timeout; a failed callback leaves the rest of the batch running and marks the pass failed. Optional `startup(runtime, stack)` opens process resources and registers cleanup on the existing exit stack. Startup runs for each installed App, regardless of organization enablement. App definitions expose no tables or migrations; prepare business databases with your application's operator workflow.

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

`useApp()` supplies `appId`, `organizationId`, `workspaceId`, a scoped `queryKey(...)`, and `request(path, init)`. The request path starts with `/` and resolves under `/api/v1/apps/{appId}`. The host adds the selected workspace header and uses its existing authentication, CSRF handling and transport. Business requests require a selected workspace.

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
