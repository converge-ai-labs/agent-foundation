# Service Contract Distribution and Client Boundaries

## Design Position

Service owns the public Native protocol consumed by Console, independently maintained language SDKs, and other clients. Client implementation, generation policy, language design, and releases are not part of the Service repository's completion boundary. A Service change must provide an accurate exported contract, protocol evidence, and compatibility semantics; it does not build or publish downstream SDKs.

## Authority

| Concern                                                                                      | Owner                                                                                                                        |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Native resources, commands, and receipts                                                     | [Service Management API](16-management-api.md) and owning domains                                                            |
| Authentication, errors, concurrency, and idempotency                                         | [Platform API Conventions](../api-conventions.md) and [Identity and Access Management](33-identity-and-access-management.md) |
| Run SSE, lifecycle events, and notification WebSocket                                        | [Native Streaming and Notifications](21-native-streaming-and-notifications.md)                                               |
| Exported HTTP and wire-model definitions and shared protocol evidence                        | Service, under `proto/a13n-service/`                                                                                         |
| Console client, generated types, and application behavior                                    | [Console](../frontend/console.md)                                                                                            |
| Python, Go, Rust, and TypeScript SDK implementation, specifications, generation, and release | Each independent SDK repository                                                                                              |
| Remote CLI implementation and release                                                        | Rust SDK repository                                                                                                          |

A client consumes public protocols, not Service startup, migrations, Worker control, persistence, internal operator routes, Redis, or object-store credentials. Hosted AG-UI and A2A remain independently interoperable standards; their clients require no a13n SDK.

## Exported Contract

Service exports one OpenAPI 3.1 document for its public Native HTTP operations to `proto/a13n-service/openapi.json`. Stable operation identities derive from HTTP method and path, not handler names. The export describes actual authentication choices, validation and error envelopes, response media types, binary bodies, and precondition and correlation headers. Exporting the contract does not start Service resources.

Existing Service wire models produce separate JSON Schema exports for notification client frames and Run stream events. These exports do not claim that OpenAPI describes WebSocket or SSE recovery. The owning streaming contract defines delivery, cursor, replay-gap, subscription, heartbeat, and reconciliation semantics. Shared wire fixtures are Service-owned evidence, not an alternative schema or an SDK-specific translation layer.

The exported files and their owning protocol semantics are reviewed together. A change to non-HTTP delivery behavior remains a client-relevant contract change even when the OpenAPI document is unchanged.

## Consumer Independence

The independent repositories are `converge-ai-labs/a13n-sdk-python`, `converge-ai-labs/a13n-sdk-go`, `converge-ai-labs/a13n-sdk-rust`, and `converge-ai-labs/a13n-sdk-typescript`. Each owns its supported API line, public surface, generator versions and templates, tests, and release decisions. The remote `a13n-service-cli` remains in the Rust SDK repository and consumes that SDK rather than defining another Service protocol implementation.

SDK repositories consume committed snapshots identified by the complete Service source commit SHA. Contract updates preserve that identity and enter downstream review; a moving branch name is not sufficient provenance. Snapshots include the shared evidence and owning non-HTTP semantics needed to review a protocol change. Updating a snapshot does not authorize an SDK release or imply that all clients already support the changed Service behavior.

Console owns an internal client generated and tested within the main repository. Its build, runtime, and release do not depend on an external SDK package, local SDK checkout, or published shared-client package. The main repository's build, tests, and documentation also work without a local `sdk/` directory.

## Compatibility

The Service API version, streaming subprotocol version, source commit identity, and SDK package versions are distinct. HTTP compatibility follows the shared API conventions; streaming compatibility follows the owning Native streaming contract. Clients must not collapse omitted and explicit-null inputs where the Service distinguishes them, invent mutation outcomes after lost acknowledgement, treat notifications as durable history, or turn local stream closure into a Run interrupt.

The Service contract defines those observable meanings once. Language-specific error types, method names, connection lifetimes, generated adapters, and release engineering belong to the downstream specifications, not this document.

## Invariants

1. Exported HTTP operations describe actual Service routes and can be checked without starting process resources.
2. Exported wire definitions reuse Service models; protocol evidence does not invent another schema system.
3. Non-HTTP semantic changes remain visible to downstream contract review.
4. SDK snapshots have immutable Service source provenance and independent release decisions.
5. Main repository validation requires no local or published Service SDK.
6. Console owns its client without a new shared package or SDK release dependency.
