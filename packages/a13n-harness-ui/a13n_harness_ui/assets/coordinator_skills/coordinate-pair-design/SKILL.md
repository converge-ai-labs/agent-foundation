---
name: coordinate-pair-design
description: Co-design a technical or product solution with a dedicated managed worker Thread. The Coordinator leads requirements clarification, user interaction, and trade-off alignment while the worker inspects code, specs, and implementation feasibility.
---

# Coordinate Pair Design

Use this workflow when a task needs collaborative design before writing code. Keep product alignment and user interaction with the Coordinator, and delegate deep codebase investigation and technical challenge to one continuing worker Thread.

## Role Split

- **Coordinator owns:** framing the problem, clarifying user intent and UX/product boundaries, asking focused user questions, synthesizing trade-offs, and deciding when the design is ready.
- **Design worker owns:** reading the codebase, specs, and tests in depth; verifying real execution paths and constraints; identifying hidden edge cases; proposing concrete architecture options; and pushing back on weak assumptions.

## Workflow

1. **Frame the design brief.** Summarize the user's goal, known constraints, non-goals, and specific open questions. If obvious product ambiguities block even initial exploration, ask the user first before spinning up work.
2. **Start or reuse one design worker.** Create one worker with `create_worker` (or reuse an existing design worker with `send_thread_message`). Prefer a strong reasoning model when allowed. Instruct the worker explicitly:
   - Keep this phase **read-only**: inspect code, specs, and tests, but do not edit repository files, create commits, or open PRs.
   - Return grounded findings with exact file references, feasible design options, material risks, and concrete questions or pushback.
3. **Iterate between Coordinator and worker.** Inspect the worker's saved reply with `get_thread`. Challenge gaps, simplify over-engineered proposals, and send follow-up rounds to the same worker via `send_thread_message` rather than spawning a new worker for each question.
4. **Align with the user on material decisions.** Bring only genuine product, contract, schema, security, or scope trade-offs to the user—with a clear recommendation and concise reasoning. Avoid dumping raw worker exploration logs into the user chat.
5. **Converge before execution.** Once the Coordinator, worker, and user agree on the design, summarize the final decisions, boundaries, and verification plan clearly. Do not start implementation until the user authorizes it; when authorized, you may transition the same worker (or hand off the agreed plan to an implementation worker) cleanly.
