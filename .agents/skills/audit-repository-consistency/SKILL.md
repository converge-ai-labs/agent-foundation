---
name: audit-repository-consistency
description: Audit a repository or revision range for alignment between accepted specifications and actual implementation, semantic duplication and inconsistent conventions, and dead, invalid, obsolete, or removable code. Use for whole-project or feature-branch reviews involving spec-code gaps, architecture conformance, DRY opportunities, inconsistent IDs, pagination, errors, configuration, states, schemas, or helpers, and cleanup or deprecation candidates. Resolve and confirm the audit target and baseline before analysis, then trace affected concepts across the repository and report evidence-backed findings without modifying code unless explicitly requested.
---

# Repository Consistency Audit

Audit a repository as a coherent system. Determine whether its accepted specification describes its actual behavior, whether shared concepts have one consistent owner and representation, and whether every retained implementation path still serves a purpose.

Prefer a few proven, consequential findings over a long list of search matches.

## Operating Rules

1. Keep the audit read-only unless the user explicitly requests remediation.
2. Read and follow the repository's agent instructions, contribution workflow, engineering standards, and specification index before auditing. In this repository, read `AGENTS.md`, `CONTRIBUTING.md`, `DEVELOPMENT.md`, `spec/repository-model.md`, and `spec/README.md` first.
3. Treat accepted specifications as normative design and implementation as evidence of actual behavior. When they disagree, determine which side should change; do not automatically legitimize implementation drift by copying it into the specification.
4. Distinguish public or durable contracts from replaceable implementation details. Do not require specifications to document private file layouts, classes, or algorithms that do not affect behavior, ownership, security, or compatibility.
5. Treat tool output and text matches as candidates, not findings. Verify every finding through its owning contract and complete implementation path.
6. Respect package, release, language, runtime, and security boundaries. Do not recommend a shared abstraction when duplication is deliberate isolation.

## Resolve the Audit Basis

Model every audit as:

```text
audit(target, optional baseline) -> findings
```

- The **target** is the repository snapshot to audit. Default to the current worktree; when it is clean, identify it by the full `HEAD` commit ID.
- The **baseline** is the comparison starting point for an incremental audit. Accept a branch, tag, ref, or full commit ID.
- A **full audit** has no baseline and examines the complete target snapshot.

Do not maintain checkpoints or infer a previous audit from repository state.

### When the user specifies the basis

Resolve every Git revision to a full commit ID and verify that it exists before analysis.

- Interpret "against `<branch-or-ref>`" as the merge base of that ref and the target unless the user requests an exact snapshot comparison.
- Use an explicit commit directly when it is an ancestor of the target.
- If an explicit commit is not an ancestor, explain the topology and confirm whether to use the exact commit or the merge base.
- Honor `full` as an explicit request to skip baseline selection.
- If the worktree has staged, unstaged, or untracked changes, confirm whether the target includes them or only `HEAD`.

### When the user does not specify the basis

Inspect the current branch, worktree status, upstream, repository default branch, merge base, commit count, and diff summary. Recommend the most likely useful basis, then ask the user to confirm before starting substantive analysis.

Prefer the repository's default or integration branch as the proposed baseline for a feature branch. Do not use a remote-tracking upstream merely because it mirrors the same feature branch. Recommend a full audit when no meaningful baseline exists or when the user asks about the repository as a whole rather than a change.

Ask a concrete question such as:

> The current target is clean `feature/example` at `<full-target-id>`. Its merge base with `origin/main` is `<full-base-id>`, covering 8 commits and 27 changed files. I recommend auditing that range. Confirm this baseline, provide another branch/ref/tag/commit, or say `full`.

Do not ask the user to discover information that Git can provide. Do not begin the audit until an unspecified or ambiguous basis is confirmed.

Record the resolved basis in the final report. If uncommitted changes are included, identify the target as `HEAD <full-id> + worktree changes`.

## Audit Efficiently

Use a narrowing funnel rather than reading every file in directory order.

### 1. Build a repository map

- Inventory tracked files with `git ls-files` and search with `rg` or `rg --files`.
- Identify subsystem indexes, manifests, public exports, routers, schemas, protocol definitions, generators, persistence models, configuration, entry points, tests, and automation.
- Map each relevant domain to its specification owner, implementation entry points, contracts or generators, tests, and public boundary.
- Identify generated, vendored, cached, build, fixture, and migration-history paths. Trace generated output back to its source rather than treating it as an independent definition.

### 2. Understand the change before expanding it

For an incremental audit:

- Read the commits from baseline to target in chronological order to understand intent.
- Review the aggregate diff, including renames and deletions, to understand the final state.
- Include confirmed worktree changes when they are part of the target.
- Extract affected concepts, contracts, and owners from the change.

For a full audit, derive the initial concepts from specification indexes, public boundaries, durable schemas, and shared infrastructure.

### 3. Trace one concept across the system

Follow each material concept through its complete lifecycle:

```text
specification -> definition -> generation -> validation -> persistence
              -> transport -> SDK or UI -> observability -> tests
```

Search all relevant roots for one concept at a time. Batch related symbol, field, state, endpoint, constant, and configuration searches. Deep-read only the owning documents and plausible implementation paths found by the search.

Start with public and durable boundaries because they have the largest blast radius. Expand beyond changed lines whenever a changed definition can affect unchanged consumers.

Use existing linters, type checkers, dependency checks, compilers, test discovery, and clone detection as filters. Do not introduce an audit-only dependency without approval.

## Run Three Audit Passes

### 1. Specification and implementation truth

Audit in both directions:

- **Specification to code:** Trace material entities, fields, operations, states, transitions, defaults, invariants, ownership, security boundaries, compatibility rules, and failure semantics into implementation and tests.
- **Code to specification:** Trace public APIs, exported schemas, protocols, durable state, configuration, process behavior, and security-sensitive behavior back to an accepted owner.

Compare semantics rather than names. Verify defaults, ordering, terminal behavior, cancellation, retries, authority, versioning, and failure outcomes.

Classify each gap as one of:

- specification stale;
- implementation drift;
- implementation incomplete;
- missing accepted contract;
- intentional private detail;
- unresolved design question.

Use tests as evidence of intent and current behavior, not as normative authority.

### 2. Consistency and DRY

Look for one concept represented or governed in multiple ways, including:

- identifier generation, prefixes, validation, serialization, storage, and display;
- pagination model, parameter names, defaults, limits, ordering, and cursor semantics;
- timestamps, durations, versions, states, terminal predicates, errors, and retries;
- configuration names, parsing, precedence, defaults, and role ownership;
- DTO, ORM, protocol, SDK, and UI schemas;
- authorization, tenant scoping, transaction helpers, logging fields, and redaction;
- duplicated validators, converters, constants, helpers, state machines, and policy decisions.

Recommend one canonical owner only when the implementations encode the same policy and must evolve together. Prefer generation over runtime coupling for cross-language contracts. Keep boundary adapters, compatibility layers, independently released packages, and defense-in-depth enforcement separate when their duplication is intentional.

Do not create an abstraction merely to remove a few obvious lines.

### 3. Invalid and removable code

Look for:

- unreachable branches, impossible conditions, unused declarations, imports, exports, and dependencies;
- orphaned modules, routes, plugins, jobs, tests, scripts, assets, configuration, and build steps;
- expired feature flags, superseded helpers, abandoned adapters, redundant fallbacks, and completed migration paths;
- shadowed configuration, duplicate registrations, ineffective handlers, and code that cannot influence behavior;
- old and new implementations retained after a completed cutover.

Before calling code removable, check:

1. static references, aliases, exports, and string keys;
2. registries, decorators, reflection, dynamic imports, plugin discovery, and import side effects;
3. CLI, environment, deployment, scheduled, serialization, persistence, and external entry points;
4. generators, manifests, packaging, build, and release automation;
5. deprecation, backward compatibility, historical migrations, and supported old clients.

Classify cleanup candidates as `confirmed removable`, `redundant and consolidatable`, `obsolete but compatibility-bound`, or `unproven`.

Do not treat age, missing tests, a TODO, or a zero-result symbol search as removal proof.

## Validate Findings

For every candidate:

1. Read the complete owning specification section and implementation path, not only matching lines.
2. Trace definitions through callers, adapters, persistence, serialization, and observable output.
3. Search alternate names, aliases, generated references, registries, and cross-language consumers.
4. Inspect relevant tests and run the narrowest useful non-mutating repository checks.
5. Try to falsify the candidate by finding an intentional boundary, compatibility requirement, or distinct semantic need.
6. Discard claims without a concrete consequence and actionable recommendation. Present unresolved cases as questions, not defects.

Prioritize findings by consequence and confidence. Security, authority, data integrity, durable state, and public protocol gaps come before localized cleanup.

## Report the Audit

Lead with findings ordered by severity. Use stable IDs such as `SPEC-001`, `CONSISTENCY-001`, `DRY-001`, and `CLEANUP-001`.

Begin with:

- target and full target commit ID, plus worktree state when applicable;
- baseline and full baseline commit ID, or `full audit`;
- covered and excluded surfaces;
- validation commands and outcomes.

For each finding, include:

- category, severity, and confidence;
- exact specification and code evidence with file paths and line numbers;
- current behavior and expected or canonical behavior;
- consequence;
- recommended owner and smallest coherent remediation;
- removal proof, compatibility constraint, or unresolved question when applicable.

End with coverage limitations and the minimal remediation order. If no actionable findings remain, say so and state what was examined. Never claim complete coverage after sampling or failed validation.

## Remediate Only on Request

When the user requests fixes, resolve the intended authority before editing. Update implementation and tests when code violates an accepted contract. Update the owning specification and affected implementation together when the accepted design changes; use `$spec-writing` for specification edits in this repository.

Centralize under the narrowest correct owner, remove code only after completing removal proof, and follow the repository's Issue-to-PR and validation workflow.
