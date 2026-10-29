# Frontend Agent Guide

Before changing UI, read the [shared design system](../spec/frontend/design-system.md) and the affected product guide: [Console](../spec/frontend/console-design-guidelines.md) or [Harness WebUI](../spec/a13n-harness-ui/webui/06-design-guidelines.md). Read relevant product behavior sections for interaction changes. For shared components, consider affected consumers. Reuse unchanged sections already read; product-specific rules do not transfer automatically between applications.

**Apply the design rules; scale the process to the change.** Follow [Local Validation](../CONTRIBUTING.md#local-validation) and [Frontend Tests](../DEVELOPMENT.md#frontend-tests):

- Small copy, spacing, color or icon changes usually need only a quick rendered check of the affected state.
- Interaction or responsive changes need relevant state and viewport checks. Shared changes need relevant consumer checks.
- Plans, screenshot collections, exhaustive state matrices and full suites are not routine prerequisites. Reuse valid evidence; expand checks when risk or findings warrant it, and stop once the affected risks are covered.
- For an explicitly requested broad audit, track coverage and omissions. Use spacing inventories or alignment measurements when they help locate systemic problems.

Inspect the rendered result for visual changes; code checks alone do not prove visual quality. Choose relevant hover, focus, expanded, long-content, error, theme and narrow states. Report any material inspection gap honestly.

Fix recurring problems at the owning token or component. Preserve input, results, recovery and accessibility when simplifying UI. Correct adjacent issues within scope without waiting for the user to find them.

Keep accepted visual rules in their shared or product guide, behavior in the product contract, and API usage in component READMEs. Remove superseded rules and repair links; do not duplicate dimensions or turn one-off adjustments into global requirements.
