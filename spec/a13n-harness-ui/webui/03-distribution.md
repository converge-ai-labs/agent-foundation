# WebUI Build and Distribution

## Design Position

The browser is bundled with Harness UI and runs against the same Python App as the CLI. Harness UI ships as a Python wheel and sdist, not an official container image. Agent execution images belong to the Environment boundary, not the workbench distribution.

## Browser Assets

`frontend/apps/a13n-harness-ui` is private frontend build input with npm package name `a13n-harness-ui-webui`. TypeScript, React, and Vite produce static assets. The shared `frontend/pnpm-lock.yaml` owns dependency versions. There is no independent npm publication, browser-owned backend, separate Node.js runtime service, or mandatory remote asset dependency.

Compiled assets and their hash manifest are included in the `a13n-harness-ui` wheel and sdist under the [repository packaging boundary](../../repository-model.md#repository-surfaces). Repository/release asset preparation requires Node.js; installing the wheel, running the server, and rebuilding a wheel from the sdist do not. Generated assets are not committed.

The browser's API use follows the Python adapter's public contract. A checked-in OpenAPI snapshot detects contract drift; it is not a separately authored server schema. Browser navigation paths support direct load and refresh. Unknown API routes and missing assets remain failures rather than browser-HTML fallbacks, under [HTTP delivery](../05-runtime-subagents-and-surfaces.md#http-adapter-contract).

## Browser Installation

The bundled WebUI is an online-only installable web app. Its same-origin manifest supplies a stable root identity and launch URL, standalone display mode, and bundled icons. Installation does not capture a Thread URL or access-key fragment. Manifest and icon requests are public static delivery, contain no instance data, and revalidate rather than using the immutable hashed-asset policy. Missing installation resources remain errors, not HTML fallbacks.

General settings offers installation when the browser supplies an install prompt and otherwise explains platform installation entry points. Prompts require an explicit user action; dismissal and failure do not cause automatic retries. An accepted prompt is not reported as completed installation without the browser's installation event. Installation availability depends on the browser and a secure origin (HTTPS or a loopback exception).

The installed window uses the existing authentication, navigation, and observation paths. Installation neither starts nor bundles the Python backend. The server must remain reachable. A separately opted-in notification-only Service Worker enables [closed-page Web Push](04-workbench-interaction.md#task-notifications). Its root-scoped `/sw.js` is public static delivery with revalidation, never an immutable hashed asset or HTML fallback. It contains no credentials, fetch interception, offline cache, or queued mutations. Installation alone does not opt into push or guarantee background execution. Browser installation and worker updates do not change draft or Run persistence and do not force page reloads.

## Compatibility and Boundaries

The authenticated server status reports the actually installed `a13n-harness-ui` distribution version, independently of the API schema version. The bundled browser displays that server-reported running version rather than its private npm manifest version or an asset build timestamp. The browser creates no separate release line; wheel/sdist and bounded cross-group dependency rules remain in force.

Operators may install the Python distribution in their own containers. Containerization does not configure an Agent Environment, enable native computer sharing, disable authentication, or grant Docker-daemon access. Native file and terminal access remains inside the server's OS authority and explicit mounts. Persistent mounts preserve selected data, not live processes or execution authority; container shutdown and browser disconnect have distinct lifecycle effects.

## Invariants

1. Installed WebUI operation and wheel rebuild from sdist require no Node.js runtime.
2. Bundled frontend assets have no independent npm publication or application backend.
3. Harness UI release automation publishes Python distributions, not a server image.
4. Agent execution uses independently configured Environment Providers and their execution images.
