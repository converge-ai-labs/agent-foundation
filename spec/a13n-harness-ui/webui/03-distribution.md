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

The installed window uses existing auth, navigation, and observation paths against the running Python server. An opted-in notification-only Service Worker (`/sw.js`, public static delivery with revalidation, containing no credentials, fetch interception, offline cache, or queued mutations) enables [closed-page Web Push](04-workbench-interaction.md#task-notifications). Browser installation and worker updates leave drafts, Run persistence, and open pages unchanged.

## Compatibility and Boundaries

Authenticated server status reports the installed `a13n-harness-ui` version (displayed by the browser) independently of the API schema version.

Operators may run the Python distribution in their own containers; containerization leaves Agent Environment configuration, computer-sharing enablement, and authentication explicit, with persistent mounts preserving only stored data (not live processes or execution authority).

## Invariants

1. Installed WebUI operation and wheel rebuild from sdist require no Node.js runtime.
2. Bundled frontend assets have no independent npm publication or application backend.
3. Harness UI release automation publishes Python distributions, not a server image.
4. Agent execution uses independently configured Environment Providers and their execution images.
