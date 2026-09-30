# Agent Stream Protocol Specifications

## Overview

This directory defines shared compact-display semantics and the public AG-UI observation boundary distributed as `a13n-stream-protocol`. Native capture projects stable blocks and scoped execution summaries; Python and TypeScript applicators apply atomic typed operations. The package also converts public Harness stream items to standard AG-UI events or namespaced `CUSTOM` fallback events.

One process-local observer binds to one Harness Run, can apply an optional Host processor, accumulates the resulting events in order, and can atomically reconstruct a fresh instance from a finite Host-supplied public source history. It is not a scheduler, lifecycle authority, Session store, durable event log, durable replay system, transport, browser SDK, or second Agent runtime.

## Document Catalog

| Document                         | Owning contract                                                                                                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md) | Compact-display identities and operations, native capture, dependency direction, observer reconstruction, AG-UI conversion, Host processing and lifecycle ownership |

## Reading Paths

### Observe Harness Runs

Read `00`, then [Harness Events and Usage](../a13n-harness/12-events-observability-and-usage.md) for the source event and lifecycle contract. Agent Stream Protocol translates that public stream and does not invent missing Harness observations.

### Build Harness UI Surfaces

Read `00`, then [Harness UI Runtime Subagents and Surfaces](../a13n-harness-ui/05-runtime-subagents-and-surfaces.md). Compact snapshots and typed deltas share one semantic projector; Harness UI owns persistence, replay, fan-out, and transport. AG-UI conversion remains available to embedding adapters.

### Service observation

The Service uses compact snapshots for durable display and shared typed deltas for its thread stream, owned by [facts and delivery](../a13n-service/07-facts-and-delivery.md). Producer coverage is independent of its optional Redis seek hint.

## Authority Rules

- Pydantic AI and the Harness own run execution, public source events, lifecycle observations, results, and `HarnessState`.
- Agent Stream Protocol owns native-to-display projection, compact values, revision-checked atomic operations and matching applicators, as well as standard AG-UI conversion, `CUSTOM` fallback and explicit finite-history observation.
- A Host owns input acceptance, visibility policy, source-history retention and selection, cursors, gaps, replay-to-live cutover, persistence, event identities, fan-out, cancellation, and transport lifecycle.
- A renderer owns ephemeral view state only.
- AG-UI event delivery is observation. It never commits execution, proves external side effects, grants tool authority, or becomes continuation state.

## Specification Conventions

- Standard AG-UI names and payloads retain their upstream meaning.
- Project-specific fallback events use the `a13n.*` namespace and carry public Harness correlation and event data.
- “Accumulation” means the observer's ordered in-memory sequence.
- “Observer resumption” means folding one finite Host-supplied public Harness source prefix into a fresh observer; durable retention, cursor and gap semantics, history selection, and replay-to-live cutover remain Host concepts.
