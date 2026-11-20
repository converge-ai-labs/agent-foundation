---
title: Stream Protocol
sidebarTitle: Overview
description: High-fidelity streaming event translation protocol converting Harness runtime observations into typed AG-UI specifications for modern frontends.
---

**Stream Protocol** translates Harness's internal execution observations into standardized, typed events specified by the AG-UI protocol. It bridges the gap between backend agent execution loops and rich frontend user interfaces, providing token-by-token streaming, reasoning deltas, tool invocation timelines, and structured completion payloads.

## Start here

If you are building an interactive chat interface, streaming dashboard, or custom desktop client, start with the core integration workflow:

1. [Stream Protocol Quickstart](getting-started.md): Connect an active Harness run to an `AgUiStreamObserver` and consume translated AG-UI events.
2. [Event Reference](events.md): Explore the complete taxonomy of streamed event types (text chunks, reasoning thoughts, tool calls, status transitions).
3. [Replay and Resilience](replay.md): Replay historical event sequences from checkpoints to catch up reconnected browser clients.
4. [API Reference](api-reference.md): Consult exact Pydantic event models, serializer signatures, and schema constraints.

| Milestone | What you will build | Verification |
| :--- | :--- | :--- |
| **Basic streaming** | Single-turn agent stream | Verify incoming `RUN_START`, `TEXT_DELTA`, and `RUN_END` events |
| **Tool timeline** | Function calling stream | Observe `TOOL_CALL_START`, argument deltas, and `TOOL_CALL_END` |
| **State replay** | Client reconnection | Resume event streaming from an arbitrary event sequence number |

## Install

Stream Protocol is published as `a13n-stream-protocol` alongside Harness:

```bash
uv add a13n-stream-protocol
```

Stream Protocol requires the exact matching release version of `a13n-harness` to ensure 100% binary schema alignment.

## One observer, one Run

In Stream Protocol, observer instances are strictly scoped: create a fresh observer for each logical run. An observer attaches to the run lifecycle, converts internal events deterministically in monotonic sequence order, and finishes cleanly upon terminal run outcome.

## Ownership Summary

Stream Protocol maintains a clear architectural boundary between execution and serialization:

| Architectural layer | Responsibility |
| :--- | :--- |
| **Harness Engine** | Executes agent logic, coordinates models, and manages tool executions |
| **Stream Protocol Observer** | Intercepts internal observations and transforms them into standard AG-UI events |
| **Host Transport Layer** | Serializes events over Server-Sent Events (SSE), WebSockets, or gRPC |
| **Frontend Client** | Parses incoming event frames and renders typography, thought blocks, and diffs |

## Next Steps

- Explore [Getting started](getting-started.md) to implement your first streaming observer.
- Review [Event schemas](events.md) to build custom web components that render tool progress bars.

## Upgrade to AG-UI 1.0

If upgrading from earlier protocol drafts, review key AG-UI 1.0 improvements:

- **Unified delta streaming:** Text content, internal reasoning tokens, and tool argument payloads share consistent chunk-streaming representations.
- **Strict monotonic sequencing:** Every emitted event carries an incrementing `seq` counter, allowing client-side reconciliation.
- **Auditable run attribution:** Child subagent runs emit explicit parent execution references, simplifying nested thread visualization.

## Reference topics

- [API Specification](api-reference.md)
- [Event Sequence Lifecycle](events.md)
- [Reconnection & Replay Protocol](replay.md)
