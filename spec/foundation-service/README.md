# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional hosted control and execution service that embeds `agent-harness`.

Foundation owns durable Agent authoring schemas, typed Presets, immutable definition revisions and dependency locks, process-local reconstruction adapters, Environment provider registry integration and desired topology, durable root and asynchronous child Execution lifecycles, worker Attempts, scheduling, continuation selection, client-tool delivery, service APIs, durable events, and usage records.

It does not redefine the code-first Harness `AgentDefinition`, plugin lifecycle, Pydantic Agent loop, Harness result/state semantics, or provider-native Environment state. Platform-owned data and service APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Authority Rules

- The control plane owns source acceptance, typed Presets, model-integration revisions, immutable definition revisions, dependency locks, and durable Executions.
- Foundation definition records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, Model, Toolset, Capability, callable, client, or credential.
- The worker verifies Host locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition`.
- Every logical run receives fresh `RunBindings`, including an Environment aggregate materialized from current desired topology and an explicit `ModelRunBinding` when hosted model aliases must fail closed rather than delegate to native inference.
- The current worker retains the Environment controller only while its Harness run is entered; dynamic desired acceptance and effective topology publication are separately fenced facts.
- One Foundation Attempt may contain several internal Harness model attempts; those inner attempts are not durable Attempt generations.
- A stale worker cannot commit a checkpoint, lifecycle event, client feedback, child result, usage record, or terminal outcome.
- Native deferred external calls and approvals remain distinct; Foundation owns durable pending state and authenticated feedback, while the external client owns its side effects.
- Process-local Harness completion, durable Host completion, event delivery, external delivery, usage recording, billing, and payment are independent facts.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- A definition revision is immutable; changing materialized content or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local Python objects are reconstructed and never become durable payloads.
