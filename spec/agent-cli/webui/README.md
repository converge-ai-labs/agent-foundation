# Agent UI WebUI Specifications

## Overview

The Agent UI WebUI is the bundled browser workstation distributed inside `a13n-ui`. It is a static React application served by the local Web adapter and connected to the same process-local `AgentUiApp` used by the CLI. Its ordinary shell keeps one conversation primary: a transient picker provides bounded Thread search and Project/all-Project scope, while the header provides New, Setup, and Settings; the selected root Thread occupies the main region; and one optional contextual panel presents current Environment information or selected activity detail.

Settings is the complete browser management surface for Projects, Agents, canonical subagents, Models and compatible accounts, Environment profiles, Plugins, Environment Run Extensions, MCP servers, defaults, catalogs, and configuration diagnostics. Guided forms are the primary experience, while exact source remains an advanced recovery and interoperability surface over the same desired-resource authority.

The WebUI does not own another Agent loop, Thread model, desired-resource store, durable queue, authorization system, or recovery mechanism. Browser state improves interaction latency only; detached App projections, selected continuations, accepted configuration generations, exact receipts, and compare-and-select preconditions remain authoritative.

## Document Catalog

| Document                                                                                       | Owning contract                                                                                                                                              |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                                               | Technology profile, browser boundary, conversation-first shell, Settings information architecture, routes, startup flow, and packaging                       |
| [01-client-runtime-and-data.md](01-client-runtime-and-data.md)                                 | API-key bootstrap, generated HTTP client, state ownership, query cache, focused and summary streams, reconciliation, retries, and browser storage            |
| [02-thread-interaction.md](02-thread-interaction.md)                                           | Project and recent-Thread navigation, conversation, composer, configuration patching, control, decisions, child work, and contextual Environment information |
| [03-configuration-and-management.md](03-configuration-and-management.md)                       | Settings navigation, guided resource management, advanced source drafts, expected-digest mutation, accounts, catalogs, diagnostics, and conflict handling    |
| [04-debugging-and-diagnostics.md](04-debugging-and-diagnostics.md)                             | Inline activity disclosure, the on-demand activity log and detail panel, App diagnostics, exact correlations, and process-local limits                       |
| [05-design-system-accessibility-and-quality.md](05-design-system-accessibility-and-quality.md) | Radix and local CSS design system, conversation and Settings responsive behavior, accessibility, safe content rendering, performance, testing, and browsers  |

## Reading Paths

### Understand the Browser Workstation

Read [Agent UI Overview](../00-overview.md), [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md), then `00` and `01` in this directory.

### Implement Thread Interaction

Read `00`, `01`, `02`, and `04`, then [Projects, Threads, and Environments](../04-projects-threads-and-environments.md), [Local Storage and Recovery](../03-local-storage-and-recovery.md), and [Agent Stream Protocol](../../agent-stream-protocol/README.md).

### Implement Settings and Management

Read `00`, `01`, and `03`, then [Configuration and Resource Catalog](../01-configuration-and-resource-catalog.md), [Extension and Capability Discovery](../01a-extension-discovery-and-management.md), [Agent, MCP, and Run Composition](../02-agent-composition-and-snapshots.md), and [Model Authentication](../02a-model-authentication-and-account-stores.md).

### Implement Visual Components and Validation

Read `00`, the feature document being rendered, and `05`.

## Authority Rules

01. `AgentUiApp` remains the only application, execution, desired-resource mutation, persistence, and live-presentation authority.
02. The browser consumes only authenticated HTTP commands, detached queries, exact receipts, source digests, versions, and bounded stream frames. It never opens local storage or receives native Harness values.
03. TanStack Query owns cached server projections. Route state, temporary form state, and focused live reduction remain separate and do not create a second durable model.
04. One open root Thread owns one focused stream and provisional presentation layer shared by its conversation, activity log, and contextual detail. Selected continuations and refreshed App projections provide retained truth.
05. Summary events are invalidation hints. They trigger bounded refetch and are never folded into domain state directly.
06. Mutating commands are never retried automatically. A stale source digest, metadata version, configuration version, continuation ID, receipt, or execution ID fails explicitly.
07. The browser API key is process-local access material. It is scrubbed from the URL fragment before routing, retained only for the browser session, and never placed in application URLs, logs, error reports, or persisted query data.
08. Resource files remain the only desired-resource authority. Guided Settings controls and advanced source editing operate on one exact source draft and save only through expected-digest mutation.
09. Available actions come from App projections. Button visibility, an open dialog, or prior live output never grants control authority.
10. The conversation is the only ordinary execution surface and the only surface with a composer. Activity and diagnostics remain read-only disclosures of the same App facts.
11. Project is the only local-root and root-Thread organization concept. Recent Threads is a derived cross-Project index, not another grouping resource.
12. Settings provides complete desired-resource management without making raw configuration files part of the ordinary workflow.
13. Responsive layout changes placement and visibility only. It does not change Thread, Run, configuration, continuation, or authorization semantics.
14. Browser rendering treats model, tool, source, and diagnostic content as untrusted text or structured data. It does not execute returned HTML, scripts, or arbitrary component descriptions.
15. The built application is a static asset tree included in the `a13n-ui` wheel and sdist. It has no Node.js runtime, server rendering, service worker, independent npm release, or remote asset dependency.

## Conventions

- A **server projection** is a detached `AgentUiApp` value returned by an HTTP query or command.
- A **focused session** is the browser runtime for one selected root Thread: one subscribe-before-query snapshot, one detailed stream cursor, and one ephemeral reducer.
- A **retained timeline** comes from the selected continuation and saved child checkpoints.
- A **live layer** is provisional current-process activity folded from detailed stream events after the snapshot cutover.
- An **activity selection** is a route-owned view or exact correlation inside the current root Thread. It is not another Thread or event-history authority.
- A **context panel** is the optional right-side presentation of current Environment context or one selected activity detail. It is closed by default and never owns execution.
- A **source draft** is browser-local text plus the exact source digest from which it began. It has no authority until a mutation succeeds.
- An **invalidation** means a summary projection may be stale and is refetched. It does not describe the replacement value.
