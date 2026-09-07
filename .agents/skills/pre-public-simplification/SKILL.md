---
name: pre-public-simplification
description: Rethink a scoped part of Agent Foundation from first principles during intensive development before going public. Use only when the user explicitly invokes this skill or requests this pre-public redesign mode; never select it for ordinary cleanup, implementation, or review.
---

# Pre-public Simplification

Choose the simplest clear design that meets real capability, reliability, and performance requirements, so people and agents can understand, maintain, and extend the project with confidence. This is an optional, temporary redesign mode, not the repository's default development workflow.

## Activation and Expiry

Apply this mode only when both conditions hold:

- The user explicitly invokes this skill or opts into pre-public redesign for a defined scope.
- Current task context establishes that the project is still in intensive development before going public, with breaking changes acceptable for the affected surface.

Reuse explicit authorization already established in the ongoing task. A request to simplify code, fix a bug, review a PR, or improve performance alone does not activate this mode. The skill's presence, an old conversation, a development version number, or a private checkout does not establish current eligibility. If eligibility is unclear, clarify it before dependent breaking changes; continue useful read-only analysis or independently authorized work.

Once the project goes public, this mode expires even if this file remains installed. The skill can then be retired or removed; other workflows must not depend on it. External consumers, shared deployments, or compatibility commitments on an affected surface also invalidate the assumption that it is disposable, even before the overall project goes public. Subsequent breaking redesign requires a separately scoped decision under the normal contribution workflow; this skill supplies no standing exception.

Activation authorizes the reasoning mode, not every possible action. A discussion or review stays read-only unless implementation is requested. Follow [repository authorization and workflow rules](../../../AGENTS.md) and [contribution requirements](../../../CONTRIBUTING.md), with the explicit pre-public migration exception below. Editing repository files does not itself authorize database resets, commits, pushes, publication, or deployment.

## Design from Requirements

Start with the actual user journey, required outcomes, failure guarantees, and relevant scale. Read the owning contracts and trace the real execution path. Treat code, tests, specifications, and existing mechanisms as evidence to examine, not proof that their design must survive.

Within the requested scope, reconsider concepts, ownership, state, configuration, naming, protocols, execution and recovery flows together where they interact. Preserve required guarantees while questioning the mechanisms that implement them. For example, first establish what must remain true during worker takeover, then evaluate whether the existing lease, fence, heartbeat, and persistence arrangement is the clearest way to ensure it.

Every repository surface within that scope is open to breaking redesign: specifications, source code, tests, APIs, protocols, data models, package boundaries, directory structure, naming, documentation, and development workflows. They may be deleted, replaced, renamed, moved, or rewritten completely when that produces the best coherent design. Neither an accepted specification nor the current layout is an immutable constraint; update affected contracts and consumers together after resolving the design. Do not preserve obsolete interfaces, structures, or compatibility paths solely because they already exist. This freedom does not expand the task's scope or waive the action and data boundaries above.

Do not remove useful capabilities merely to shorten an implementation. Make capability reductions and changed guarantees explicit decisions. Do not silently rewrite accepted contracts to match a preferred implementation; resolve material design choices through the owning workflow.

## Pre-public Database Migrations

The user has established the premise for this development phase: there is no existing database data to preserve. Treat this as a settled project decision while this mode remains applicable; do not invent legacy-data obligations or repeatedly ask whether existing data must survive.

When implementation includes schema changes, migration files and migration history may be edited directly, rewritten, consolidated, deleted, or replaced within the agreed scope, including initial schema revisions. Prefer a clean migration history that builds the intended schema from an empty database. Do not append compensating revisions, retain old columns or tables, add backfills, or maintain upgrade paths from superseded development schemas solely to preserve that history. This is an explicit exception to treating existing revisions as immutable or requiring expand-and-contract changes for this phase.

Keep the migration graph coherent and validate clean-database upgrade, expected heads, and parity with the current ORM metadata. Generate new or replacement revisions through the owning disposable-database workflow and review the result; direct edits to existing files do not require an extra revision merely to record the edit. This exception ends with the mode's expiry or a newly established requirement to preserve data or support deployed schemas.

## Choose the Least Overall Complexity

- Evaluate how many concepts, rules, states, branches, and dependency relationships a maintainer must understand. Count the places that must change together for one behavior. Fewer lines, smaller files, or more layers do not establish improvement.
- Give each shared rule and durable fact a clear owner. Consolidate genuine semantic duplication; preserve real differences in lifecycle, ownership, and external protocols. Choose names by meaning rather than mechanical uniformity.
- Prefer direct flows and small shared functions. Add an abstraction, framework, configuration option, or execution path only when a concrete current need justifies its ongoing cost. Extensibility should make a realistic change local and understandable.
- Include runtime work, storage and network costs, recovery, operations, and contributor effort in the comparison. Remove obvious waste. Use relevant measurements or an explicit capacity model for performance trade-offs; distinguish evidence from assumptions. Do not add mechanisms for imagined scale or ignore a known bottleneck to claim simplicity.
- Apply the same reasoning to product behavior, APIs, code, naming, documentation, CI, instructions, and collaboration processes when they are in scope. Avoid replacing implementation complexity with a heavy redesign procedure.

A full rewrite is available, not mandatory. Prefer it when it produces a simpler coherent result than local changes. Each retained or added mechanism should have an explainable purpose; do not freeze existing mechanisms before considering the whole problem.

## Explain the Comparison and Resolve Special Cases

Once a preferred simple design emerges, explain the before-and-after comparison to the user before dependent implementation. Use everyday language and a concrete user action or failure scenario, not just architectural labels or a list of renamed classes:

- **Before:** What happens today, where the flow becomes hard to follow, and why the extra machinery exists.
- **After:** How the same scenario works in the proposed design, which concepts or steps disappear, and what remains necessary.
- **Trade-off:** Which capabilities, guarantees, performance characteristics, or compatibility expectations change. Explain why the simpler design is sufficient under the stated requirements.

Prefer a small worked example. When implementation details help, show short code or pseudocode snippets for the same operation before and after; label pseudocode and keep it focused on the meaningful difference. For structure changes, a small directory tree or dependency sketch may explain more than code. Do not make the user reconstruct the comparison from large excerpts or unexplained jargon.

Investigate whether existing complexity serves an anticipated special case. Separate observed requirements and demonstrated failures from documented assumptions and speculative future scenarios; if the original rationale is unknown, say so. Explain each material case concretely: what triggers it, what the existing mechanism protects, what happens without it, and what supporting it costs. For example, describe two workers both trying to save progress after a delayed request rather than merely saying that a fence is necessary.

If simplification depends on dropping or narrowing such a case, ask the user whether it still needs to be supported, alongside a clear recommendation and consequence. Do not silently discard the case, assume it is unnecessary because it is rare, or require the user to choose low-level implementation details. Reuse already confirmed decisions. Keep dependent changes pending while a material capability or guarantee decision remains unresolved, and continue independent authorized work.

## Implement and Validate

When implementation is authorized, replace the agreed design coherently across affected contracts, code, consumers, tests, and documentation. Preserve unrelated work and any actual consumer obligations; follow the owning engineering rules for persistence changes with the pre-public migration exception above.

Cleanup is part of completing the simplification. Trace the replaced design through its callers and supporting artifacts, and remove related legacy implementations, compatibility aliases and fallbacks, duplicate execution paths, obsolete configuration and flags, unused dependencies, and abandoned files or directories. Update or remove stale tests, fixtures, specifications, examples, documentation, exports, registrations, and build or CI references. Keep tests for required behavior and make them exercise the surviving implementation. Do not leave a parallel old path, commented-out implementation, or cleanup TODO for later merely because the new path works.

Search for the old names, entry points, and configuration keys, and inspect relevant dynamic registration and generated consumers before declaring cleanup complete. Regenerate affected outputs through their owning tools. Check that no stale references or orphaned artifacts remain and that the affected surfaces describe and use one coherent design. Delete only material made obsolete by this change; any retained related legacy must have a concrete reason reported to the user.

Validate meaningful behavior through the real integration boundaries, including relevant concurrency, retries, partial failure, and recovery. Measure performance where the decision depends on it. Use proportionate repository checks and reuse still-valid results. Passing existing tests alone does not prove either correctness or reduced complexity.

Report the resulting design, the concrete reduction in understanding and maintenance cost, the guarantees checked, and remaining limitations. Do not turn a local redesign conclusion into a permanent project-wide rule without an explicit decision.
