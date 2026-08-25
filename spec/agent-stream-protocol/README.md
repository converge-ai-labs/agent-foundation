# Agent Stream Protocol Specifications

## Overview

This directory defines the shared Agent User Interaction Protocol projection boundary, distributed as `converge-agent-stream-protocol`. The package adapts validated Harness execution observations and retained Host display data into standard AG-UI events that presentation surfaces can consume without importing Host session or lifecycle types.

The projection is reusable by local Agent UI and optional hosted transports. It is not a scheduler, session store, web server, browser SDK, durable event log, or second Agent runtime.

## Document Catalog

| Document                         | Owning contract                                                                                                                     |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md) | Dependency direction, protocol profiles, Harness event projection, ordering, replay, ingress validation, failure, and compatibility |

## Reading Paths

### Project Harness Runs

Read `00`, then [Harness Events, Observability, and Usage](../agent-harness/12-events-observability-and-usage.md) for source observations and [Harness Snapshot and Resume](../agent-harness/10-snapshot-and-resume.md) for continuation authority.

### Build Agent UI Surfaces

Read `00`, then [Agent UI Runtime Subagents and Surfaces](../agent-ui/03-runtime-subagents-and-surfaces.md). Both local surfaces consume the same validated projection.

### Add Hosted AG-UI Delivery

Read `00`, then [Foundation Service](../foundation-service/README.md) for hosted durability boundaries. Foundation durable events and replay cursors remain authoritative even when AG-UI is the live presentation codec.

## Authority Rules

- The shared [interaction model](../interaction-model.md) owns Session, Thread, Turn, and Item meaning.
- Pydantic AI and the Harness own run execution, messages, tool semantics, final results, and `HarnessState`.
- A Host owns input acceptance, typed Host scope, Session, Thread, Turn, authorization, checkpoint selection, retention, and transport lifecycle.
- Agent Stream Protocol owns deterministic projection, protocol validation, ordering checks, replay-safe display envelopes, and namespaced extension policy.
- A renderer owns ephemeral view state only.
- AG-UI event delivery is observation. It never commits execution, proves external side effects, grants tool authority, or becomes continuation state.

## Specification Conventions

- AG-UI names and payloads refer to the upstream protocol version selected by the Host.
- Project-specific events use an explicitly versioned `converge.*` namespace and never redefine a standard event type.
- “Replay” means replay of retained presentation envelopes. It does not mean replaying an Agent run or restoring `HarnessState` from UI data.
