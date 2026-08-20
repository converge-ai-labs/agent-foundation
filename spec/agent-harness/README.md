# Harness Specifications

## Overview

This directory defines the current design of `agent-harness`, the Pydantic AI-based harness used by embedded applications and hosted execution workers.

The Harness is a process-local execution boundary. It owns the canonical materialized `AgentDefinition`, code-first materialization, resolved-plan validation and construction, first-class plugin construction and input-to-result middleware, Pydantic Capability composition, typed execution context, model and tool loop integration, dynamic multi-Environment access, context management, continuation state, delegation, normalized events, and native Pydantic usage exposure. Hosted definition sources, typed Presets, immutable revisions, durable acceptance, product policy, deployment coordination, and business billing remain Host concerns.

## Document Catalog

| Document                                                                                 | Owning contract                                                                                                                                 |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                         | Scope, system architecture, authority, execution flow, completion boundaries, and design principles                                             |
| [01-pydantic-ai-foundation.md](01-pydantic-ai-foundation.md)                             | Pydantic AI primitive mapping, latest-version policy, native ModelProfile boundary, and harness-owned extensions                                |
| [02-domain-model.md](02-domain-model.md)                                                 | Core entities, identities, references, version axes, and relationship invariants                                                                |
| [03-agent-definition-and-build.md](03-agent-definition-and-build.md)                     | Canonical materialized Agent definition, process-local resolved build plan, validation, and final Agent construction                            |
| [04-capability-model.md](04-capability-model.md)                                         | Native Capability composition, AgentContext, namespaced state, interaction, and checkpoint integration                                          |
| [05-plugin-system.md](05-plugin-system.md)                                               | First-class plugin specs, exports, catalog, construction, ordering, run binding, middleware, Capability contribution, context, state, and trust |
| [06-execution-context-and-lifecycle.md](06-execution-context-and-lifecycle.md)           | Typed execution context, run-stream lifecycle, control input, and completion                                                                    |
| [07-tool-execution.md](07-tool-execution.md)                                             | Native and managed function tools, client-side external tools, authorization, approval, credentials, invocation, and deferred results           |
| [08-environment-integration.md](08-environment-integration.md)                           | Multi-binding Bound Environment, dynamic topology, model-facing routing, recoverable state, and provider enforcement                            |
| [09-context-and-memory.md](09-context-and-memory.md)                                     | History, context assembly, guidance, compaction, memory boundary, and token budgets                                                             |
| [10-snapshot-and-resume.md](10-snapshot-and-resume.md)                                   | `message_history` plus namespaced Agent Context state, semantic checkpoints, restore, resume, fork, and host state boundary                     |
| [11-delegation-and-subagents.md](11-delegation-and-subagents.md)                         | Complete child declarations, immutable built collection, State-backed inline delegation, shared tasks, and Host async boundary                  |
| [12-events-observability-and-usage.md](12-events-observability-and-usage.md)             | Canonical event stream, terminal result, telemetry, native accumulation and limits, custom pricing, and per-response usage observation          |
| [13-hosting-contract.md](13-hosting-contract.md)                                         | Embedded and hosted execution integration contracts                                                                                             |
| [14-public-api-and-packaging.md](14-public-api-and-packaging.md)                         | Stable durable and code-first API, package layers, optional capabilities, and compatibility policy                                              |
| [15-security-compatibility-and-tradeoffs.md](15-security-compatibility-and-tradeoffs.md) | Trust boundaries, security model, compatibility strategy, and design trade-offs                                                                 |
| [16-input-model-and-output.md](16-input-model-and-output.md)                             | Native and hosted input, content resolution, ModelSettings/ModelProfile/Capability layering, model resolution, streaming, and output            |
| [17-core-capability-catalog.md](17-core-capability-catalog.md)                           | First-party Capability catalog entries, dependencies, state, and security boundaries                                                            |

## Reading Paths

### Understand the harness

Read `00`, `01`, and `02`, then follow the owning document for the concern being changed.

### Build an Agent or Capability

Read `03`, `04`, `05`, and `14`. For hosted Preset materialization and immutable definition revisions, first read [Foundation Service Agent Definitions and Presets](../foundation-service/01-agent-definitions-and-presets.md).

### Integrate tools or Environments

Read `02`, `06`, `07`, `08`, and `15`. Direct local file and shell integration is owned by `08`; for Docker, E2B, remote, optional local-daemon, transport, protocol, output-retention, or native command-isolation integration, follow the [agent-envd specification catalog](../agent-envd/README.md). For durable client-side tool delivery and Foundation Client feedback, read [Foundation Service Client-Side Tools](../foundation-service/02-client-side-tools.md).

### Implement persistence or hosted execution

Read `06`, `09`, `10`, `11`, `12`, and `13`.

### Review security or compatibility

Read the relevant owning contract and finish with `15`.

## Authority Rules

- This directory owns process-local harness semantics.
- Pydantic AI owns its Agent loop and public Capability, Toolset, model, output, and inner run contracts; the Harness owns the outer plugin lifecycle and terminal validation.
- The host owns trusted execution identity issuance, durable acceptance, durable storage, provider installation, and product policy.
- The Harness owns provider-neutral Environment semantics, virtual routing, and the direct EIP adapter over `converge-agent-envd-client`; the client owns generated wire and transport/session behavior, while EIP and `agent-envd` own daemon-backed methods, processes, handles, cursors, retained output, generation, and native enforcement. Direct `LocalFileOperator` and `LocalShell` enforce configured local access without envd.
- A telemetry backend owns exported observations but is never an execution-state authority.
- Each host owns its durable execution, queue, lease, recovery, and lifecycle facts.

No document in this directory may redefine a fact owned by another layer. It may define the reference or port through which the harness consumes that fact.

## Specification Conventions

- Python-like schemas are conceptual typed contracts unless a document explicitly declares a serialized wire format.
- Identifiers ending in `_id` identify an entity within the namespace defined by the owning contract; an identifier alone never grants authority.
- `Ref` types are references; `Receipt` types record an observed outcome without becoming its authority.
- Provider-neutral contracts define behavior and failure semantics without selecting one vendor, transport, database, or deployment topology.
- Cross-document references point to the owning document instead of copying its state machine or field table.
- The documents use descriptive design language. Requirements, delivery status, migration plans, and acceptance criteria remain outside the specification.
