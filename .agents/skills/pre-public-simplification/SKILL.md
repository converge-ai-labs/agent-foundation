---
name: pre-public-simplification
description: Redesign a scoped part of Agent Foundation for simplicity before going public. Use only on explicit invocation or a request for this pre-public redesign mode, not ordinary cleanup or review.
---

# Pre-public Simplification

Choose the simplest clear design that meets real capability, reliability, and performance requirements and remains easy for people and agents to understand, maintain, and extend.

Treat elegance as an observable developer experience: concepts fit the problem, interfaces make behavior predictable, and code structure makes execution and change easy to follow. Judge it through concrete use and maintenance tasks rather than brevity, cleverness, or fixed counts of lines, files, or layers.

## Activation and Scope

This optional mode requires explicit user invocation or opt-in for a defined scope, and current task context establishing intensive pre-public development with breaking changes acceptable for that surface. Reuse authorization from the ongoing task. The skill's presence or a development version alone does not establish eligibility; clarify missing eligibility before dependent breaking changes while continuing independent authorized work.

The mode expires when the project goes public, even if this file remains installed. External consumers, shared deployments, compatibility commitments, or data-preservation requirements on an affected surface invalidate its disposable-development premise. Subsequent breaking redesign follows the normal contribution workflow. Other workflows must not depend on this skill, so it can be retired or removed.

Discussion and review remain read-only unless implementation is requested. Follow [AGENTS.md](../../../AGENTS.md) and [CONTRIBUTING.md](../../../CONTRIBUTING.md), with the migration exception below. This mode does not authorize database resets, commits, pushes, publication, or deployment on its own.

## Design from First Principles

Start with user journeys, required outcomes, failure guarantees, and relevant scale. Read the owning contracts and trace actual behavior. Existing mechanisms and specifications are evidence to examine, not proof that the design must survive.

Every repository surface in scope may undergo breaking changes, including specifications, code, tests, APIs, protocols, data models, package boundaries, directory structure, naming, documentation, CI, and development workflows. Delete, replace, move, or completely rewrite them when justified. A full rewrite is available, not mandatory; choose it when it yields a simpler coherent result than local changes. Resolve material design decisions before replacing accepted contracts, and update their consumers together.

Preserve valuable capabilities and required guarantees while questioning their implementation. Any proposed reduction must be an explicit decision, not an incidental way to shorten code.

## Pre-public Database Migrations

The user has confirmed that this phase has no existing database data to preserve. Treat that as settled while the mode remains applicable; do not invent legacy-data obligations or repeatedly reconfirm the premise.

Migration files and history, including initial schema revisions, may be edited directly, rewritten, consolidated, deleted, or replaced within the agreed implementation scope. Build the intended schema cleanly from an empty database. Do not preserve obsolete columns, backfills, compensating revisions, or upgrade paths solely for superseded development schemas. This explicitly permits rewriting existing revisions and bypasses expand-and-contract requirements for this phase.

Generate new or replacement revisions through the owning disposable-database workflow and review them. Direct edits to existing revisions need no extra revision recording the edit. Validate the migration graph, expected heads, clean-database upgrade, and parity with ORM metadata. The exception ends when this mode expires or requirements to preserve data or deployed schemas emerge.

## Choose the Least Overall Complexity

- Reduce the concepts, rules, states, branches, and dependencies a maintainer must understand, and the places that must change together.
- Give shared rules and facts clear owners. Consolidate semantic duplication while preserving real differences in ownership, lifecycle, and external protocols. Use domain terms consistently across public APIs, implementation, and documentation; make each core concept's responsibility and relationships easy to explain.
- Prefer direct flows and small shared functions. Make state ownership, side effects, resource lifetimes, and failure handling traceable from the entry point. Extra abstractions, frameworks, options, and execution paths need concrete current justification. Extensibility should make a realistic change local and understandable.
- Make common tasks straightforward through coherent interfaces and useful defaults. Similar operations should follow consistent parameter, result, and error conventions. Keep timeout, cancellation, retry, and partial-success behavior understandable, with errors and diagnostics that help developers determine what happened and how to respond.
- Include runtime, storage, network, recovery, operational, and contributor costs. Remove obvious waste; support performance trade-offs with measurements or an explicit capacity model. Separate assumptions from evidence and account for known bottlenecks.

## Design for Open-source Developers

Evaluate the affected surface from first use through contribution:

- A small, complete example accomplishes a useful task with little prerequisite knowledge.
- Following that example into the public entry point and core implementation reveals the same concepts and a traceable execution path. Readers can learn the common flow before needing specialized machinery.
- A realistic contribution has a discoverable change location, clear contracts and impact, and a practical way to validate it.

Use a concrete task within scope to demonstrate the relevant improvements in the before-and-after explanation and final report. A polished API alone is insufficient if its implementation remains hard to understand or modify. Preserve required guarantees and performance; make necessary complexity and its boundaries understandable.

## Explain Before and After

Before dependent implementation, explain the preferred design in everyday language using the same concrete action or failure scenario before and after. Show how it works today, why it became complex, what disappears, how the simpler flow works, and what changes in capability, reliability, performance, or compatibility. Explain why the result is sufficient.

Prefer a small worked example, with short code or labeled pseudocode where it clarifies the difference. A directory tree or dependency sketch may better explain structural changes. Avoid large excerpts and unexplained jargon.

Investigate special cases behind the existing complexity. Distinguish real requirements and demonstrated failures from anticipated scenarios; say when the original rationale is unknown. Explain what triggers each material case, what the old mechanism protects, what happens without it, and the cost of supporting it. Rarity alone does not make a case unnecessary.

When simplification drops or narrows such a case, give a recommendation and consequences, then ask whether the user still needs it supported. Reuse confirmed decisions; ask about material capability and guarantee choices rather than routine implementation details. Keep dependent changes pending until those choices are resolved, while continuing independent authorized work.

## Complete the Replacement

Update affected contracts, implementation, consumers, tests, and documentation as one coherent change. Cleanup is a completion requirement: remove related legacy paths, aliases, fallbacks, obsolete configuration, unused dependencies, abandoned files, and stale supporting artifacts. Keep tests for required behavior exercising the surviving implementation. Leave no parallel old path, commented-out implementation, or deferred-cleanup TODO merely because the new path works.

Trace callers and search old names, entry points, and configuration keys; check relevant exports, dynamic registrations, generated consumers, examples, documentation, and build/CI references. Regenerate affected outputs through their owning tools. Preserve unrelated work; report a concrete reason for any retained related legacy.

Run proportionate repository checks through real integration boundaries, covering relevant concurrency, retries, partial failure, and recovery. Measure performance where the decision depends on it and reuse still-valid checks. Existing tests passing alone does not prove correctness or reduced complexity.

Report the resulting design, concrete reduction in understanding and maintenance cost, validation, and remaining limitations. Local conclusions do not become permanent project-wide rules without an explicit decision.
