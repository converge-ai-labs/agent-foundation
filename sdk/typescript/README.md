# @converge.ai/a13n

TypeScript SDK package for a13n Service.

## Installation

```bash
npm install @converge.ai/a13n
```

This SDK targets the Native `/api/v1` contract and notification subprotocol `a13n.service.notifications.v1`. HTTP requests and responses are generated from the Service application; every Native operation is available through the typed `http` client.

```typescript
import { createClient, data } from "@converge.ai/a13n";

const client = createClient({
  baseUrl: "https://agents.example.com",
  auth: { type: "bearer", token: process.env.A13N_API_KEY! },
});
try {
  const http = await client.workspaceHttp();
  const agent = data(
    await http.GET("/agents/{agent}", {
      params: { path: { agent: "code-reviewer" } },
    }),
  );
  console.log(agent.name);
} finally {
  client.close();
}
```

`workspaceHttp()` reads `/api/v1/auth/context` and binds the generated workspace operations to the API key’s Workspace. Callers supply only child resource references, using an ID or key for Agents. The bound client shares authentication, retries, and shutdown with the parent. Create a new binding after switching credentials to another Workspace. The full `http` surface remains available for explicit resource paths and organization or personal operations.

Browser clients use `{ type: "session" }` on the same origin as Service. Restore the CSRF token from `/api/v1/auth/csrf` (or the login response) with `setCsrfToken` before mutations. Tokens stay in memory. Callers pass `If-Match`, `Idempotency-Key`, Workspace headers, pagination cursors, and `AbortSignal` explicitly through typed operation options. Responses expose headers for ETags and request IDs. `ApiError` carries status, code, safe details, request ID, and retry guidance.

GET and HEAD retry at most twice by default, honoring bounded `Retry-After`. Mutations are never replayed automatically. Reconcile a lost command acknowledgement using the original idempotency key and the owning command contract. Omitted object fields and explicit `null` remain distinct.

Binary operations accept `Blob` or `ReadableStream<Uint8Array>` and an explicit content type. Downloads support openapi-fetch's `parseAs: "stream"`. The SDK does not buffer binary bodies; Node streaming request bodies require the runtime's `duplex: "half"` request option.

`streamRun(runId, { after, workspaceId, signal })` returns an async iterator of `{ cursor, event }`. Apply an event before requesting the next one. A `ReplayGapError` requires current Run, Items, and pending-action reconciliation. Closing an iterator or client only stops local delivery.

`notifications({ subscriptions, onNotification, onState, onError })` opens a best-effort attachment with up to three reconnects. The `gap` state requires durable Workspace event and current resource reconciliation. Close the returned handle to change subscriptions. Browser notifications use session cookies; bearer clients supply a `socketFactory` capable of attaching authorization headers. Credentials never travel in WebSocket URLs or subprotocols.

## Development

```bash
make sdk-typescript-generate  # refresh the Service contract and TypeScript models
make sdk-typescript-check-all
```

Generated contract drift is checked against the live Service schema in the repository gate. The standalone SDK retains its own npm lockfile and build boundary.

## Publishing

The first public version must be published locally as an RC to establish the npm package while preserving the stable version for the release workflow. For the initial A13N release:

```bash
python3 scripts/prepare-release-version.py a13n-typescript 0.0.3-rc.1
cd sdk/typescript
npm ci
npm run check:all
npm publish --access public --tag rc
```

Run this from a clean checkout of the reviewed release commit and discard the injected local version changes afterward. The publishing account must be able to create the public `@converge.ai/a13n` package in the `converge.ai` organization and either complete 2FA or use a temporary granular access token that can bypass 2FA. Revoke the bootstrap token immediately after trusted publishing is configured.

After bootstrap, inspect `npm dist-tag ls @converge.ai/a13n`. If npm initializes `latest` to the bootstrap RC, remove only that tag with `npm dist-tag rm @converge.ai/a13n latest`; retain the published version and the `rc` tag. Never remove a `latest` tag that points to a stable release. If the bootstrap token cannot manage dist-tags, complete this step after interactive account login before considering bootstrap complete.

The GitHub Environment `sdk-typescript-npm` must allow deployment tags matching `release/a13n/typescript/*`. A rule for the former `release/sdk/typescript/*` channel does not allow the renamed release tags.

Configure the newly created package to trust the exact GitHub workflow and Environment. `npm trust` requires npm 11.15 or newer and interactive account authentication with 2FA; a bypass-2FA granular token cannot configure trust:

```bash
npx -y npm@11.19.0 trust github @converge.ai/a13n \
  --repo converge-ai-labs/agent-foundation \
  --file release-a13n-typescript.yml \
  --environment sdk-typescript-npm \
  --allow-publish \
  --yes

npx -y npm@11.19.0 trust list @converge.ai/a13n
```

After the bootstrap RC and trust configuration succeed, publish stable `0.0.3` from `.github/workflows/release-a13n-typescript.yml`. Subsequent versions also use this Trusted Publishing workflow. Push `release/a13n/typescript/<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`; the workflow injects that version into `package.json` and `package-lock.json` in its ephemeral checkout. RCs publish under the npm `rc` dist-tag and never advance `latest`.

## License

Licensed under the Apache License 2.0.
