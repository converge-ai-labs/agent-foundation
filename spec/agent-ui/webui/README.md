# Agent UI WebUI Specifications

## Overview

The Agent UI WebUI is the bundled browser workstation distributed inside `a13n-ui`. It is a static React application served by the local Web adapter and connected to the same process-local `AgentUiApp` used by the CLI and TUI. It separates three jobs: the Threads area provides Project-filtered attention-first supervision and focused interaction; Configure provides complete Project and other desired-resource management; and Debug provides detailed read-only inspection and application diagnostics.

The WebUI is a presentation and editing surface. It does not own another Agent loop, Thread model, desired-resource store, durable queue, authorization system, or recovery mechanism. Browser state improves interaction latency only; detached App projections, selected continuations, accepted configuration generations, exact receipts, and compare-and-select preconditions remain authoritative.

## Document Catalog

| Document                                                                                       | Owning contract                                                                                                                                                  |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                               | Technology profile, browser boundary, Threads/Configure/Debug information architecture, routes, startup flow, and packaging                                      |
| [01-client-runtime-and-data.md](01-client-runtime-and-data.md)                                 | API-key bootstrap, generated HTTP client, state ownership, query cache, focused and summary streams, reconciliation, retries, and browser storage                |
| [02-thread-interaction.md](02-thread-interaction.md)                                           | Project-filtered Workbench and Focus interaction, timeline, composer, configuration patching, control, decisions, and child work                                 |
| [03-configuration-and-management.md](03-configuration-and-management.md)                       | Resource, Project, default, extension, account, import, and configuration-diagnostic experiences; source drafts; expected-digest mutation; and conflict handling |
| [04-debugging-and-diagnostics.md](04-debugging-and-diagnostics.md)                             | Separation from ordinary Thread interaction, Thread and Run inspection, diagnostic views, exact correlations, process-local limits, and App health               |
| [05-design-system-accessibility-and-quality.md](05-design-system-accessibility-and-quality.md) | Radix and Tailwind design system, area-specific responsive behavior, accessibility, safe content rendering, performance, testing, and browser compatibility      |

## Reading Paths

### Understand the Browser Workstation

Read [Agent UI Overview](../00-overview.md), [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md), then `00` and `01` in this directory.

### Implement Thread Interaction

Read `00`, `01`, and `02`, then [Projects, Threads, and Environments](../04-projects-threads-and-environments.md), [Local Storage and Recovery](../03-local-storage-and-recovery.md), and [Agent Stream Protocol](../../agent-stream-protocol/README.md).

### Implement Configuration and Management

Read `00`, `01`, and `03`, then [Configuration and Resource Catalog](../01-configuration-and-resource-catalog.md), [Extension and Capability Discovery](../01a-extension-discovery-and-management.md), [Agent, MCP, and Run Composition](../02-agent-composition-and-snapshots.md), and [Model Authentication](../02a-model-authentication-and-account-stores.md).

### Implement Debugging and Diagnostics

Read `00`, `01`, and `04`, then [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md) and [Local Storage and Recovery](../03-local-storage-and-recovery.md).

### Implement Visual Components and Validation

Read `00`, the feature document being rendered, and `05`.

## Authority Rules

01. `AgentUiApp` remains the only application, execution, desired-resource mutation, persistence, and live-presentation authority.
02. The browser consumes only authenticated HTTP commands, detached queries, exact receipts, source digests, versions, and bounded stream frames. It never opens local storage or receives native Harness values.
03. TanStack Query owns cached server projections. Route state, temporary form state, and focused live reduction remain separate and do not create a second durable model.
04. A focused stream is provisional presentation layered over a high-water-bound snapshot. Selected continuations and refreshed App projections provide retained truth.
05. Summary events are invalidation hints. They trigger bounded refetch and are never folded into domain state directly.
06. Mutating commands are never retried automatically. A stale source digest, metadata version, configuration version, continuation ID, receipt, or execution ID fails explicitly.
07. The browser API key is process-local access material. It is scrubbed from the URL fragment before routing, retained only for the browser session, and never placed in application URLs, logs, error reports, or persisted query data.
08. Resource files remain the only desired-resource authority. Guided controls and source editing operate on one exact source draft and save only through expected-digest mutation.
09. Available actions come from App projections. Button visibility, an open dialog, or prior live output never grants control authority.
10. The Threads area owns ordinary Project-filtered supervision, conversation, and control. Project remains the only local-root and root-Thread organization concept. Debug owns detailed read-only inspection and never places a second composer beside diagnostic state.
11. Responsive layout changes placement and visibility only. It does not change Thread, Run, configuration, continuation, or authorization semantics.
12. Browser rendering treats model, tool, source, and diagnostic content as untrusted text or structured data. It does not execute returned HTML, scripts, or arbitrary component descriptions.
13. The built application is a static asset tree included in the `a13n-ui` wheel and sdist. It has no Node.js runtime, server rendering, service worker, independent npm release, or remote asset dependency.

## Conventions

- A **Project filter** is one Project ID or its omission for All Projects. It changes root-Thread query membership without changing Thread authority.
- A **server projection** is a detached `AgentUiApp` value returned by an HTTP query or command.
- A **focused session** is the browser runtime for one selected root Thread: one high-water-bound snapshot, one detailed stream cursor, and one ephemeral reducer.
- A **retained timeline** comes from the selected continuation and saved child checkpoints.
- A **live layer** is provisional current-process activity folded from detailed stream events after the snapshot cutover.
- A **source draft** is browser-local text plus the exact source digest from which it began. It has no authority until a mutation succeeds.
- An **invalidation** means a summary projection may be stale and is refetched. It does not describe the replacement value.
- An **attention state** is a derived presentation category such as pending decision, failed operation, or active work. It is not a persisted lifecycle state.
