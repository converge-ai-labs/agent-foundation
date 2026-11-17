# An independently packaged Service App

This example installs `acme.reports` as a Python wheel with its own prebuilt Console frontend. It adds a reporting page, workspace and organization settings, and a workspace-home replacement. The report uses fixed, explicitly labeled demo data. Its preview action exercises session authentication, CSRF and workspace write authorization without creating a persistent business record.

The frontend builds in its own Node project. Its only runtime import is `@a13n/console`, supplied by the host bridge. React hooks, query caching, router navigation, identity, appearance and translations come from the Console that mounts it. The example owns no React root or providers.

## Build and install

Start from a Service development environment with the App Plugins beta interfaces. From the repository root:

```bash
pnpm --dir examples/service-app-plugin/frontend install --frozen-lockfile
pnpm --dir examples/service-app-plugin/frontend build
uv build --wheel --sdist --out-dir examples/service-app-plugin/dist examples/service-app-plugin
uv pip install --python .venv/bin/python --no-deps examples/service-app-plugin/dist/acme_reports_app_example-0.1.0-py3-none-any.whl
uv run --no-sync pytest examples/service-app-plugin/tests -q
```

The frontend output goes into `src/acme_reports/static/`. Hatch explicitly includes that ignored build directory in both the wheel and sdist. Rebuilding the wheel from the sdist uses those prebuilt files and requires no Node.js. `SpaceGrotesk-OFL.txt` accompanies the example's font.

Install the wheel into every Service process environment and restart Service. In a source checkout, use the prepared environment when starting the local workflow so dependency synchronization does not remove the extra installed example:

```bash
PATH="$PWD/.venv/bin:$PATH" make dev TRACES=none
```

Use the Console URL reported by `make dev-status`. Open **Settings → Organization → App plugins**, enable **Acme reports**, and return to the workspace. The installed page becomes workspace home and appears under **Apps → Reports**. No Console rebuild follows installation or activation.

The installed-wheel test verifies real entry-point discovery and the packaged entry, stylesheet, relative JavaScript chunk, separate font and license. It runs against the installed distribution, rather than importing the example source directory.

## Try the integration

- Open **Reports** and click **Preview report**. The POST uses the host session, CSRF token and selected workspace; Service checks workspace `write` in the App handler.
- Click **Build details** to load a separate relative JavaScript chunk. The heading uses the font in the same versioned asset tree.
- Open **Report settings** from both Workspace and Organization settings. The host owns navigation metadata and renders the App component in its existing settings layout.
- Change Console language or appearance in Personal settings. The App reads those same providers. Switching workspaces gives it a separate query key and workspace header.
- Open **App recovery demo**. Its deliberate render error stays within the App surface; **Agents** and the rest of Console remain available.
- Disable the App in Organization settings. Its pages, settings and home replacement disappear; direct business requests are refused centrally. Existing Runs retain their accepted activation under the Service contract.

App imports and startup are trusted installed code. Keep CSS scoped to `.acme-reports`; the example's stylesheet follows that convention. Package installation and upgrades require a Service restart. Change the distribution version whenever frontend bytes change.

## Source map

| File                       | Owns                                                                      |
| -------------------------- | ------------------------------------------------------------------------- |
| `src/acme_reports/app.py`  | App definition, host-gated API routes and additional action authorization |
| `frontend/src/index.tsx`   | Page, settings and workspace-home contributions using host providers      |
| `frontend/src/details.tsx` | Separately loaded frontend chunk                                          |
| `frontend/vite.config.mjs` | Independent ESM build with the host bridge externalized                   |
| `pyproject.toml`           | Entry-point discovery and wheel/sdist asset inclusion                     |

The [App plugin guide](../../docs/a13n-service/app-plugins.md) describes the authoring API and organization policy. Business storage and its migrations belong to your application.
