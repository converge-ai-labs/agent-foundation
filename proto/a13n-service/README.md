# Service contract inputs

This directory contains Service-owned inputs for Console and independent Service clients.

- `openapi.json`: exported Native HTTP routes and wire types, including authentication, preconditions, media types, and error envelopes.
- `notification-client.schema.json`: JSON Schema generated from the same `ClientFrame` union used by the notification WebSocket.
- `run-stream-event.schema.json`: JSON Schema generated from the Service's `RunStreamEvent` model.
- `fixtures/wire.json`: shared omitted/null/value and union serialization examples. Compatibility examples such as unknown response enum values are not necessarily valid new server inputs.

Generate with `make service-contract-generate`. Verify without modifying files with `make service-contract-check`. Console generates its private HTTP types from this directory. SDK repositories consume a pinned snapshot and own their own generator versions, templates, adapters, tests, and releases.

These files do not replace the owning semantics:

- [Platform API Conventions](../../spec/api-conventions.md)
- [Native Streaming and Notifications](../../spec/a13n-service/21-native-streaming-and-notifications.md)
- [Service SDK Design and Contract Distribution](../../spec/a13n-service/37-service-sdks-and-clients.md)

In particular, notification server delivery and reconnection behavior are defined by the streaming contract, not inferred from the notification client-frame schema. A non-HTTP semantic change must remain visible in downstream review even when `openapi.json` is unchanged.

## Downstream notifications

`notify-service-contract.yml` sends the full Service `main` SHA to the four SDK repos. Its paths include exported evidence, Service/API specs, Service runtime, Harness, Stream Protocol and dependency locks, so non-HTTP changes remain visible. Manual dispatch retries a main-line SHA; inspect all four results after partial failure.

Each SDK maintains at most one open rolling snapshot PR on `sync/service-contract` and owns adaptation, generation, CI and release. New notifications advance the proposal's immutable source pin; repeated or older notifications cannot rewind it. SDK repositories own branch recovery and maintainer-edit handling, as documented in their `CONTRIBUTING.md`. Main neither builds nor publishes SDKs. Its docs retain Service protocol/integration guidance and link to SDK repositories; language API references belong downstream.

Install receiving workflows on each SDK default branch before configuring the dedicated GitHub App. Restrict its installation to these five repos, with Contents and Pull requests read/write. Set variable `SERVICE_CONTRACT_APP_CLIENT_ID` and secret `SERVICE_CONTRACT_APP_PRIVATE_KEY`; never extract a developer's OAuth token. Each workflow narrows tokens to its source/destination. No client ID means the job is skipped, not operational.

Verify a known committed baseline through dispatch, draft, provenance, adaptation and ready-PR CI. SDK `CONTRIBUTING.md` owns the receiving/retry procedure. A snapshot PR is not compatibility acceptance or release authorization; registry credentials are separate.
