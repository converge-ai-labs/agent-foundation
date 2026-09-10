---
name: uiux-polish
description: Build and refine frontend pages and interactions with strong visual taste, proactive polish, and lightweight iteration. Use for UI implementation and visual or interaction improvements.
---

# UI/UX Polish

**Exercise visual taste and own the finished result.** Working functionality and assembled components are not enough. Notice awkward proportions, weak hierarchy, inconsistent spacing, poor alignment, distracting colors, and unclear interactions. Resolve those problems yourself within the affected page or flow; do not wait for the user to identify them. Deliver a coherent, refined interface. Polish means purposeful choices, not more decoration.

**Be proactive in judgment and lightweight in execution.** Look and adjust as you build. Avoid lengthy plans, separate acceptance exercises, and repeated polish cycles with no meaningful improvement. Ask only when a real product tradeoff or visual direction needs the user's decision.

- Follow the [design system](../../../spec/frontend/design-system.md) and the closest accepted product patterns. Reuse shared components, but judge their composition in the actual page rather than assuming reuse guarantees quality.
- Consider the complete affected interaction, including its natural entry points and important states. Do not implement only the spot highlighted in a screenshot or expand into unrelated redesigns.
- Open the actual page, assess its overall hierarchy and details, and fix obvious rough edges before handing it back. Check additional states or viewport sizes when they could reveal a relevant problem; do not turn every small change into an exhaustive audit.
- Keep validation proportional under [CONTRIBUTING.md](../../../CONTRIBUTING.md#local-validation). For visual changes, prioritize the rendered result; for behavior changes, verify the affected behavior. Reuse applicable successful checks. Do not default to repeated full-repository tests or add tests that merely mirror styling.
- Put reusable visual decisions in the design system and recurring implementations in shared components. Keep local exceptions local; do not accumulate a new permanent rule for every adjustment.

Work directly toward a polished result and a prompt handoff. State what changed and any material validation limitation briefly.
