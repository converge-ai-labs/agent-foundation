---
title: Memory
description: Share persistent knowledge across Threads in Harness using File Memory (Markdown notes) or Record Memory (similarity-recalled facts).
---

Use **Memory** when one conversation should teach another (user preferences, project conventions, or durable decisions). Use [working state](context.md#working-state) for tasks and notes scoped to a single Thread.

## Choose a memory model

<span id="what-the-model-gets"></span>

| Capability               | Storage & recall                                     | Best for                                           |
| ------------------------ | ---------------------------------------------------- | -------------------------------------------------- |
| `FileMemoryCapability`   | Human-readable Markdown files in a mounted directory | Project conventions, guidelines, curated knowledge |
| `RecordMemoryCapability` | Structured records recalled by query/similarity      | Large sets of user facts, preferences, and history |

## Configure memory in a Run

1. Enable `FileMemoryCapability` or `RecordMemoryCapability` on your `AgentSpec` or builder.
2. Supply the Host-owned store or read/write mount on `RunBindings` so the Host controls which Threads share a store and which Runs have write access.
3. Keep raw credentials and ephemeral secrets out of durable memory stores.
