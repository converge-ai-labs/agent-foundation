# Agent Stream Protocol Specifications

## Overview

This directory defines the shared Agent User Interaction Protocol observation boundary distributed as `a13n-stream-protocol`. The package converts every public Harness stream item to standard AG-UI events where a direct mapping exists and to a namespaced `CUSTOM` event otherwise.

One process-local observer binds to one Harness Run, can apply an optional Host processor, accumulates the resulting events in order, and can atomically reconstruct a fresh instance from a finite Host-supplied public source history. It is not a scheduler, lifecycle authority, Session store, durable event log, durable replay system, transport, browser SDK, or second Agent runtime.

## Document Catalog

| Document                         | Owning contract                                                                                                                                                   |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md) | Dependency direction, observer state and reconstruction, event conversion, custom fallback, Host processing, accumulation, lifecycle ownership, and compatibility |

## Reading Paths

### Observe Harness Runs

Read `00`, then [Harness Events and Usage](../agent-harness/12-events-observability-and-usage.md) for the source event and lifecycle contract. Agent Stream Protocol translates that public stream and does not invent missing Harness observations.

### Build Agent UI Surfaces

Read `00`, then [Agent UI Runtime Subagents and Surfaces](../agent-ui/05-runtime-subagents-and-surfaces.md). Both local surfaces consume the same post-processor AG-UI events, while Agent UI owns persistence, replay, fan-out, and transport.

### Add Hosted AG-UI Delivery

Read `00`, then [Foundation Hosted
AG-UI](../foundation-service/30-hosted-ag-ui.md) for input acceptance, durable
Run/Turn binding, event filtering, lifecycle projection, cursor, and SSE.
Foundation uses the same observer while owning lifecycle records, event
identities, retention, and delivery.

## Authority Rules

- Pydantic AI and the Harness own run execution, public source events, lifecycle observations, results, and `HarnessState`.
- Agent Stream Protocol owns standard AG-UI conversion, generic `CUSTOM` fallback, optional replay-stable Host processing, process-local accumulation, and atomic reconstruction from a supplied source history.
- A Host owns input acceptance, visibility policy, source-history retention and selection, cursors, gaps, replay-to-live cutover, persistence, event identities, fan-out, cancellation, and transport lifecycle.
- A renderer owns ephemeral view state only.
- AG-UI event delivery is observation. It never commits execution, proves external side effects, grants tool authority, or becomes continuation state.

## Specification Conventions

- Standard AG-UI names and payloads retain their upstream meaning.
- Project-specific fallback events use the `a13n.*` namespace and carry public Harness correlation and event data.
- “Accumulation” means the observer's ordered in-memory sequence.
- “Observer resumption” means folding one finite Host-supplied public Harness source prefix into a fresh observer; durable retention, cursor and gap semantics, history selection, and replay-to-live cutover remain Host concepts.
