# Agent UI Specifications

## Overview

This directory defines `agent-ui`, the local single-user Host distributed as `converge-agent-ui`. It embeds `agent-harness`, persists local Agent profiles and Threads, manages foreground Turns and process-local background subagents, and exposes the same application behavior through a bundled browser application and a terminal UI.

Agent UI is not a reduced Foundation Service. It owns local Host lifecycle and presentation, while the Harness remains the process-local Agent runtime and the shared [Agent Stream Protocol projection](../agent-stream-protocol/README.md) remains the presentation protocol boundary.

## Document Catalog

| Document                                                                     | Owning contract                                                                                                                |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                             | Local Host architecture, application-service boundary, surface selection, packaging, and completion boundaries                 |
| [01-agent-profiles-and-composition.md](01-agent-profiles-and-composition.md) | Agent profiles, resolved composition, Thread pinning, plugins, Capabilities, and child definitions                             |
| [02-local-threads-and-state.md](02-local-threads-and-state.md)               | Local Thread/Turn/Item identity, checkpoints, display replay, read-only Thread Capability, concurrency, fork, and recovery     |
| [03-runtime-subagents-and-surfaces.md](03-runtime-subagents-and-surfaces.md) | Shared foreground orchestration, process-local background children, dynamic envd attachments, WebUI, TUI, and AG-UI inspection |

## Reading Paths

### Understand the Local Host

Read `00`, then `02` for local authority and recovery. Read the [Harness catalog](../agent-harness/README.md) for the embedded execution contract.

### Configure Agents and Subagents

Read `01`, then [Harness Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md) and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Implement a Presentation Surface

Read `03`, then the [Agent Stream Protocol specification](../agent-stream-protocol/README.md). A surface consumes the shared application service and AG-UI stream rather than interpreting Harness events independently.

## Authority Rules

- Agent UI owns local Agent-profile documents, resolved profile snapshots, Thread/Turn/Item records, local checkpoint selection, process-local background jobs, optional process-local envd attachment routing, and surface lifecycle.
- `HarnessState` is the canonical process-local continuation value. A transcript, AG-UI replay log, rendered terminal state, or browser cache never replaces it.
- The Harness and Pydantic AI own Agent construction, Agent loops, inline delegation, run events, results, and continuation semantics.
- Agent Stream Protocol owns the reusable Harness-to-AG-UI projection contract. Agent UI owns local transport, replay retention, and presentation policy around that projection.
- WebUI and TUI are presentation peers over one application service. Neither owns a separate Thread/Turn/Item model, orchestration loop, or rendering truth.
- Local files, attachment status, and compact references identify records but grant no Agent, Environment, model, plugin, or child authority. Fresh run bindings remain required.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly identified as serialized local documents.
- A Thread is a local Host record that implements the shared Thread/Turn/Item semantics; it is not a browser connection, Pydantic run, or model-provider session.
- A Turn is one user request and all foreground Agent work against the selected Thread checkpoint; internal Harness model attempts remain process-local details.
- An Item is one ordered semantic message, action, or result within the Turn; AG-UI deltas and replay envelopes remain presentation data.
- Background child jobs are process-local Host work. They are not Harness inline delegation state or durable Foundation child Threads.
