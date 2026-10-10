# Service contract exports

`make service-contract-generate` exports the current Service without opening process resources:

- `openapi.json`: the HTTP API, including binary responses and idempotent replay results.
- `thread-stream.schema.json`: the data schema for each Thread SSE frame (`delta`, `boundary`, `changed`, `reset`, `gap`).

Console HTTP types are regenerated from OpenAPI. The [HTTP API contract](../../spec/a13n-service/10-api.md) owns the exports; [Runs](../../spec/a13n-service/05-runs.md) and [Facts and delivery](../../spec/a13n-service/07-facts-and-delivery.md) own submission and stream semantics.

The documentation site also consumes OpenAPI. When adding or changing an API `summary` or `description`, update its Simplified Chinese translation in [`frontend/apps/a13n-docs/lib/locales/openapi.zh-CN.json`](../../frontend/apps/a13n-docs/lib/locales/openapi.zh-CN.json), including summaries generated from route function names. Run `make docs-build` to check translation coverage and the generated reference. Translate human-facing prose only; keep identifiers, examples and machine-readable exports unchanged. See [Documentation Changes](../../CONTRIBUTING.md#documentation-changes).

`make service-contract-check` checks drift without writes. Independent SDK and remote CLI repositories consume these exports separately; this repository does not build or release their clients. Each SDK pins committed inputs to a complete Service SHA rather than importing a mutable working tree.

The `notify-service-contract.yml` workflow notifies the four SDK repositories when either export, `spec/api-conventions.md`, or the three owning chapters above changes on `main`. It sends the immutable source SHA; each SDK owns synchronization, generation, validation and release. No SDK checkout is required by this repository's gates.
