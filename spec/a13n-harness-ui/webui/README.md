# Harness UI WebUI

## Current Browser Contract

The bundled WebUI renders only a **Hello World** heading. It is a static React page, not an Agent conversation or management application. It has no conversation composer, Thread navigation, setup, Settings, diagnostics, or execution controls.

Loading the page makes no application API requests, opens no SSE subscriptions, and starts no Agent operation. The page does not consume the startup URL's API-key fragment, authenticate, retain access keys, or store application state in browser storage. It does not implement client-side application routes. Server-recognized navigation paths can serve the same placeholder without providing the former browser features.

## Server and Application Boundary

The [HTTP adapter contract](../05-runtime-subagents-and-surfaces.md#http-adapter-contract) remains independent of the placeholder. `a13n-harness-ui webui` starts one foreground HTTP/SSE server owning one WebUI-mode `HarnessUiApp`; the page does not own that App or its lifetime. Browser disconnect does not stop the server or cancel App execution.

[HTTP startup and access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access) owns listener binding, terminal key output, API authentication, Host/Origin checks, and static-asset security. The existing finite API, authenticated OpenAPI document, summary stream, focused stream, cursor/reset behavior, and App mutation semantics remain server contracts. The absence of a browser application neither removes these APIs nor relaxes their access requirements.

## Build and Distribution

`frontend/apps/a13n-harness-ui` remains private frontend build input with npm package name `a13n-harness-ui-webui`. TypeScript, React, and Vite produce the local static asset tree. The shared `frontend/pnpm-lock.yaml` owns dependency versions. There is no independently published npm artifact, browser-owned backend, Node.js runtime, or remote asset dependency.

The compiled assets and their hash manifest remain bundled in the `a13n-harness-ui` wheel and sdist under the [repository packaging boundary](../../repository-model.md#repository-surfaces). Repository and release asset preparation requires Node.js; installed runtime and wheel rebuilds from the sdist do not.

The frontend retains an OpenAPI snapshot derived from the Python adapter for contract drift checks. That snapshot is not imported into the page. There is no generated browser API client or runtime validator bundle.

## Verifiable Invariants

1. The page renders a level-one Hello World heading without contacting the App API.
2. Rendering the page cannot admit, steer, cancel, or resume an Agent operation, or mutate configuration.
3. Browser rendering tests cover the placeholder; backend tests continue to own HTTP, authentication, OpenAPI, SSE, and App behavior.
4. Production asset preparation and wheel/sdist inclusion remain required even though the page has no application features.
