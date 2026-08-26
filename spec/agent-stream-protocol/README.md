# Agent Stream Protocol Specifications

## Overview

This directory defines the shared Agent User Interaction Protocol observation boundary distributed as `converge-agent-stream-protocol`. The package converts every public Harness stream item to standard AG-UI events where a direct mapping exists and to a namespaced `CUSTOM` event otherwise.

One process-local observer binds to one Harness Run, can apply an optional Host processor, and accumulates the resulting events in order. It is not a scheduler, lifecycle authority, Session store, durable event log, replay system, transport, browser SDK, or second Agent runtime.

## Document Catalog

| Document                         | Owning contract                                                                                                                                |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md) | Dependency direction, observer state, event conversion, custom fallback, Host processing, accumulation, lifecycle ownership, and compatibility |

## Reading Paths

### Observe Harness Runs

Read `00`, then [Harness Events, Observability, and Usage](../agent-harness/12-events-observability-and-usage.md) for the source event and lifecycle contract. Agent Stream Protocol translates that public stream and does not invent missing Harness observations.

### Build Agent UI Surfaces

Read `00`, then [Agent UI Runtime Subagents and Surfaces](../agent-ui/03-runtime-subagents-and-surfaces.md). Both local surfaces consume the same post-processor AG-UI events, while Agent UI owns persistence, replay, fan-out, and transport.

### Add Hosted AG-UI Delivery

Read `00`, then [Foundation Service](../foundation-service/README.md) for hosted durability boundaries. A hosted adapter can use the same observer, but Foundation owns its lifecycle records, event identities, retention, and delivery.

## Authority Rules

- Pydantic AI and the Harness own run execution, public source events, lifecycle observations, results, and `HarnessState`.
- Agent Stream Protocol owns standard AG-UI conversion, generic `CUSTOM` fallback, optional Host processing, and process-local accumulation.
- A Host owns input acceptance, visibility policy, persistence, event identities, replay, fan-out, cancellation, and transport lifecycle.
- A renderer owns ephemeral view state only.
- AG-UI event delivery is observation. It never commits execution, proves external side effects, grants tool authority, or becomes continuation state.

## Specification Conventions

- Standard AG-UI names and payloads retain their upstream meaning.
- Project-specific fallback events use the `converge.*` namespace and carry public Harness correlation and event data.
- “Accumulation” means the observer's ordered in-memory sequence. Durable retention and replay are Host concepts.
