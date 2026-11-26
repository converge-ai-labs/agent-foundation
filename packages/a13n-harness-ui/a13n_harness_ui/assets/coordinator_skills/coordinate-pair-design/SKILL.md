---
name: coordinate-pair-design
description: Pressure-test a design with one worker before implementation. Resolve material trade-offs and record the decision, open questions, and acceptance criteria.
---

# Coordinate Pair Design

Use this workflow when the user needs a design decision that benefits from technical challenge before implementation. The Coordinator owns the decision brief and user discussion; one continuing worker tests the proposal against evidence.

## Frame the Decision

Give the worker a short brief:

- The user's goal, what success looks like, and what stays out of scope.
- The checkout and revision to inspect, the contracts to preserve, and what the user has already decided.
- The leading option, if any, and the few questions whose answers could change it. Label assumptions as assumptions.

Choose a worker that can read the relevant checkout, contracts, and tests. Ask the user first only when the missing answer prevents useful investigation. Keep this phase read-only: inspect existing evidence without editing repository files or running checks that would write to the workspace.

## Try to Disprove the Leading Option

Ask the worker to trace the actual execution path and return:

- Verified facts with source or test references, separated from assumptions and unverified behavior.
- The strongest viable alternative, including keeping the current behavior if it meets the goal. Explain what it does better and what it costs.
- A concrete case where the leading option could fail, and the evidence or acceptance check that would change the recommendation.

## Resolve the Delta

Spot-check the facts that drive the decision. In the next round, tell the same worker what changed, which assumption it affects, and what question remains. Ask for a targeted answer, not another full design review.

Bring the user a recommendation and the consequences of choices they own. Discuss open-ended questions in chat; use `ask_user_question` only for a blocking decision with meaningful options. Track the current recommendation, decisions the user has made, and any remaining blocker in one place.

## Close or Hand Off

A design is ready when its critical assumptions have evidence, the decisions needed to proceed are settled, and another executor can tell what to build and how to verify it. Record unresolved dissent as a risk if it does not block the design; call it a blocker if it could change the outcome.

Close with:

- The chosen design and why it beats the strongest alternative.
- Scope, invariants, compatibility boundaries, and acceptance checks.
- Remaining risks or decisions, who owns them, and what would reopen the design.

Keep the decision in saved chat or an authorized durable destination. Pass the decision and revision inline or by a reference the receiving worker can read; `thread-files/tmp` is private to the originating Thread.

Begin implementation when existing authorization covers it and its stated conditions are met. Otherwise stop at the design. Design agreement alone grants no write authority.
