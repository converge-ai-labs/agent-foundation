---
name: audit-repository-consistency
description: Audit a repository or revision range for spec-code gaps, inconsistent shared concepts, semantic duplication, and removable code. Use for repository consistency or architecture-conformance reviews; report evidence-backed findings and remediate only when requested.
---

# Repository Consistency Audit

Determine whether accepted contracts match actual behavior, shared concepts have consistent owners, and retained implementation paths still serve a purpose. Prefer consequential, proven findings over search matches.

## Scope and Evidence

Follow the repository's `AGENTS.md` and contribution rules. Read the engineering standards and owning specification sections for the surfaces under review; use `spec/README.md` to locate owners. Reuse documents already read.

Keep audits read-only unless remediation is requested. Accepted specifications define intended behavior; code and tests establish actual behavior. Do not copy implementation drift into a specification to make the mismatch disappear. Private implementation details need a specification only when they affect observable behavior, ownership, security, or compatibility.

## Resolve the Audit Basis

Identify the target snapshot, optional baseline, and requested surfaces before analysis:

- Default to the current worktree, including staged, unstaged, and relevant untracked changes. Record the full `HEAD` ID and whether local changes are included. An explicit revision or `HEAD` request excludes local changes.
- A whole-project or `full` request has no baseline. A request limited to a feature, directory, or file stays within that scope, including directly affected owners and consumers.
- For a branch or change review without an explicit base, inspect Git status, the repository default/integration branch, merge base, and diff summary. Use the meaningful base found there and state the choice; a tracking ref that merely mirrors the feature branch is not a useful base.
- Resolve supplied revisions to full commit IDs. Interpret “against a branch/ref” as its merge base with the target unless an exact snapshot comparison is requested. Use an explicit ancestor commit directly.
- Ask a focused question only if unresolved topology, missing refs, or multiple plausible targets/bases would materially change coverage. For a non-ancestor explicit commit without comparison semantics, clarify exact comparison versus merge base.

State the resolved basis and proceed when the request and Git evidence establish it. Do not require confirmation just because a default was used, and do not infer a previous audit or maintain checkpoints.

## Trace the Relevant System

Inventory with `rg --files` or `git ls-files`; locate owning specs, public entry points, schemas, persistence, generators, tests, and automation. Mark generated, vendored, fixture, cache, and migration-history paths so they are assessed through their owners.

For incremental reviews, read the aggregate diff with renames/deletions, then relevant commits when intent is unclear. Extract changed concepts and trace affected consumers beyond changed lines. For a full audit, start with specification indexes, public and durable boundaries, and shared infrastructure.

Follow each material concept through applicable stages:

```text
specification -> definition/generation -> validation/persistence
              -> transport/SDK/UI -> observability/tests
```

Batch related searches and deep-read plausible owning paths. Use existing non-mutating checks to test candidates; do not install audit-only dependencies without authorization.

## Audit Passes

### Specification and implementation

Trace both directions: accepted entities, operations, defaults, states, authority, compatibility, and failure rules into code; public or durable behavior back to its owning contract. Compare semantics, including ordering, cancellation, retries, completion, and unknown outcomes.

Classify gaps as stale specification, implementation drift, incomplete implementation, missing accepted contract, intentional private detail, or unresolved design. Establish which authority should change before recommending remediation.

### Consistency and duplication

Look for competing policies or representations of IDs, pagination, time, versions, errors, state transitions, configuration, schemas, authorization, transactions, logging, and redaction.

Recommend a canonical owner when definitions encode the same policy and must evolve together. Preserve deliberate isolation across packages, releases, languages, runtimes, and security boundaries. Boundary adapters, compatibility layers, and defense-in-depth checks may legitimately repeat logic; a few similar lines do not justify an abstraction. Prefer generation over runtime coupling for shared cross-language wire contracts.

### Invalid and removable code

Check unreachable or ineffective paths, orphaned registrations/assets/jobs, unused dependencies, expired flags, superseded helpers, and completed cutovers.

Before calling anything removable, verify:

1. Static references, aliases, exports, and string keys.
2. Registries, decorators, reflection, dynamic imports, plugin discovery, and import side effects.
3. CLI, environment, deployment, scheduled, serialization, persistence, and external entry points.
4. Generators, manifests, packaging, builds, and release automation.
5. Deprecation promises, old clients, compatibility layers, and migration history.

Classify candidates as confirmed removable, redundant and consolidatable, obsolete but compatibility-bound, or unproven. Age, missing tests, TODOs, and zero-result searches are not removal proof.

## Validate and Report

For each candidate, read its complete owning contract and implementation path, trace alternate and cross-language consumers, and inspect relevant tests. Try to falsify it by looking for intentional isolation or compatibility requirements. Run the narrowest useful non-mutating checks; for review-only work, avoid formatting gates that rewrite the worktree.

Report actionable findings by severity, with a short statement of target/full commit ID, baseline/full commit ID or full audit, and worktree scope. Give each finding:

- A stable ID, category, severity, and confidence.
- Exact specification and implementation evidence with file paths and lines.
- Actual versus required behavior and a concrete consequence.
- The responsible owner and smallest coherent fix.
- Removal proof, compatibility constraints, or unresolved decisions where relevant.

Include commands/results and coverage limitations. Keep unproven concerns separate from defects; never claim full coverage from sampling. If no actionable findings remain, say what was examined.

When fixes are requested, implement supported remediations and relevant tests. Use the repository's `spec-writing` skill for accepted specification changes. An unresolved design decision blocks only the affected remediation; continue independent authorized work. GitHub posting and other external mutations remain subject to the user's authorization.
