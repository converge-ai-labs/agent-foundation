# WebUI Build and Distribution

## Design Position

The browser is bundled with Harness UI and runs against the same Python App as the CLI. A ready-to-use Docker image supplies a shared development machine; it is not an Agent Environment Provider or a separate hosted service product.

## Browser Assets

`frontend/apps/a13n-harness-ui` is private frontend build input with npm package name `a13n-harness-ui-webui`. TypeScript, React, and Vite produce static assets. The shared `frontend/pnpm-lock.yaml` owns dependency versions. There is no independent npm publication, browser-owned backend, separate Node.js runtime service, or mandatory remote asset dependency.

Compiled assets and their hash manifest are included in the `a13n-harness-ui` wheel and sdist under the [repository packaging boundary](../../repository-model.md#repository-surfaces). Repository/release asset preparation requires Node.js; installing the wheel, running the server, and rebuilding a wheel from the sdist do not. Generated assets are not committed.

The browser's API use follows the Python adapter's public contract. A checked-in OpenAPI snapshot detects contract drift; it is not a separately authored server schema. Browser navigation paths support direct load and refresh. Unknown API routes and missing assets remain failures rather than browser-HTML fallbacks, under [HTTP delivery](../05-runtime-subagents-and-surfaces.md#http-adapter-contract).

## Docker Development Image

The image belongs to Harness UI's distribution boundary and contains the installed application and compiled browser assets. It starts the foreground WebUI server, includes a shell, Git, and development tooling, and runs as a non-root OS user. It does not require a13n Service, Redis, a separate collaboration service, privileged mode, or a mounted Docker socket.

Inside the image, native computer sharing is enabled and the server listens on `0.0.0.0`. Authentication remains enabled by default, using the same key selection and startup output as the native executable. Container defaults do not imply the dangerous authentication bypass.

The normal published-port example binds to host loopback. Exposing the instance to a team is deliberate and follows the listener's trusted-network/TLS boundary. A container listener on all interfaces is not proof that its host port is safely restricted.

Working directories, configuration, and App data support explicit persistent mounts. Replacing a container preserves only data stored in those mounts; the container writable layer is not promised durable storage. Host Files and PTY operate inside the container and on its mounts. Mounting an external Host directory deliberately includes that directory in the shared computer's authority. The image does not mount an entire host filesystem implicitly.

The foreground process receives termination signals and closes owned native PTY resources and child processes on orderly shutdown. Browser disconnect is not container shutdown. Container replacement or process loss does not recover prior live terminal sessions or Run receipts merely because conversation data was persisted.

## Compatibility and Boundaries

The authenticated server status reports the actually installed `a13n-harness-ui` distribution version, independently of the API schema version. The bundled browser displays that server-reported running version rather than its private npm manifest version or an asset build timestamp. Release images contain the same UI wheel as the Python publication: image tag `X.Y.Z` corresponds to Python version `X.Y.Z`, and image tag `X.Y.Z-rc.N` corresponds to Python version `X.Y.ZrcN`. Development images retain the source package version and report their build revision separately. The image and bundled browser identify the Harness UI version they contain. They do not create another frontend release line or change the Harness group's dependency ownership. Existing wheel/sdist and bounded cross-group dependency rules remain in force. Image build definitions and deployment assets belong under `deploy/`; credentials are runtime inputs, not image layers or committed resources. RC artifacts do not advance a stable `latest` channel.

This development image is distinct from selecting a Docker or E2B Environment for an Agent. Agent Environment support remains governed by Provider and Host-adapter contracts and is not inferred from the server being containerized.

## Invariants

1. Installed WebUI operation and wheel rebuild from sdist require no Node.js runtime.
2. Bundled frontend assets have no independent npm publication or application backend.
3. Docker enables native computer sharing inside the container without disabling authentication.
4. Persistent mounts preserve selected data, not live processes or execution authority.
5. Container shutdown and browser disconnect have distinct lifecycle effects.
6. Running the server image does not silently configure an Agent Environment or grant Docker-daemon access.
