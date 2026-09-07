---
name: pre-public-simplification
description: Redesign a scoped part of Agent Foundation for simplicity before going public. Use only on explicit invocation or a request for this pre-public redesign mode, not ordinary cleanup or review.
---

# Pre-public Simplification

Choose the simplest clear design that meets real capability, reliability, and performance requirements and remains easy for people and agents to understand, maintain, and extend.

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

## Choose a Clear, Coherent Design

- Reduce the concepts, states, branches, dependencies, and coordinated edits a maintainer must understand. Judge improvement through realistic use and maintenance tasks, not line counts or layer counts.
- Give shared rules clear owners and use domain terms consistently across interfaces, implementation, and documentation. Preserve real lifecycle, protocol, and security differences when consolidating duplication.
- Prefer direct flows and cohesive functions and modules. Keep interfaces predictable and state, side effects, resource lifetimes, and failure handling traceable. Abstractions and options need current justification and should make a realistic change easier.
- Explain necessary concepts and prerequisites so readers can follow common workflows before unrelated mechanisms or exceptional cases. Necessary complexity should have understandable boundaries.
- Include runtime, storage, network, recovery, operational, and maintenance costs. Support performance trade-offs with measurements or an explicit capacity model; distinguish assumptions from evidence and account for known bottlenecks.

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
