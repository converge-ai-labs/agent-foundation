---
name: spec-writing
description: Author, polish, review, or reorganize accepted technical contracts under spec/, including domain models, lifecycles, APIs, protocols, ownership, security, and compatibility. Use when a task changes, streamlines, or evaluates those contracts; ordinary implementation details do not require a spec rewrite.
---

# Specification Authoring

Maintain `spec/` as the current, internally consistent accepted design. Follow `AGENTS.md` and `CONTRIBUTING.md`; [Repository Model](../../../spec/repository-model.md) defines what belongs in specifications.

## Establish Authority

Read `spec/README.md`, the relevant subsystem index/reading path, and the documents that own changed concepts. Search affected terms, states, schemas, and claims across `spec/`. Reuse context already read.

Use the accepted outcome supplied by the user or concluded discussion. Do not infer a new product or architecture decision from implementation behavior alone. If material alternatives remain unresolved, identify the decision and continue independent authorized work; keep the unsettled design out of `spec/`. The Issue-to-PR workflow does not itself authorize posting to GitHub.

## Choose the Owner

Each durable fact has one owning contract. Update that owner first, then affected summaries, catalogs, reading paths, and incoming links. Preserve established terms and ownership unless the accepted change replaces them.

Read [structure-and-template.md](references/structure-and-template.md) when adding or splitting documents, changing ownership/navigation, or choosing a contract's structure. Ordinary edits can retain the existing structure.

Keep platform definition in `spec/README.md`, subsystem navigation in its `README.md`, architecture in `00-overview.md`, and detailed contracts in cohesive numbered documents. Overviews link to detailed schemas and state machines rather than becoming competing owners. Do not renumber stable files casually or create empty templates/placeholders in `spec/`.

## Write the Contract

Write the resulting design in present tense with testable statements. Explain purpose, ownership, authority, and observable behavior. Include applicable data types, lifecycle/completion boundaries, failure/cancellation/retry/unknown-outcome semantics, security, compatibility, and verifiable invariants.

Read [domain-modeling-and-naming.md](references/domain-modeling-and-naming.md) when adding or reshaping concepts, schemas, identities, lifecycles, or shared terms. Derive models from representative flows before drafting fields or APIs.

Use upstream public primitives when they already own the semantics. Introduce project abstractions only for a stable cross-host or cross-provider contract. Label conceptual Python-like schemas explicitly; distinguish them from serialized wire formats.

Omit private classes, layouts, hooks, and algorithms that can change without affecting observable behavior, authority, security, or compatibility. Keep accepted trade-offs when they explain the cost of the selected design. Proposals, rejected alternatives, discussion history, progress, tutorials, runbooks, and contributor procedures belong in the surfaces assigned by Repository Model.

Use a diagram or table when it clarifies the contract. Prefer Mermaid for architecture, state, or interaction diagrams, and keep diagrams, schemas, tables, and prose semantically aligned.

## Write Direct, Anti-Defensive Prose

Keep every material technical detail, bound, error code, and invariant while cutting defensive boilerplate, PR-diff residue, and walls of prose:

- **Preserve normative exclusions; cut defensive filler.** Retain hard security boundaries, authority limits, state preconditions, and compatibility constraints (such as *"The function-tool wrapper is an Agent invocation boundary, not Python isolation."* or *"Plaintext appears once and is never saved in browser persistence."*). Remove obvious non-effects (such as *"Opening the dialog does not create credentials"*) and PR-scope disclaimers (such as *"No ordinary setup question or automatic migration is added"*).
- **Prefer direct positive statements over habitual contrast.** Avoid routine `"X, not Y"`, `"X rather than Y"`, and `"instead of X"` when a direct rule states the exact contract. Reserve contrastive phrasing for real confusion traps between adjacent fields, authorities, or failure modes.
- **State authority and verification directly instead of `"X alone does not prove Y"` boilerplate.** State what operation X commits and which separate state, event, or query establishes Y.
- **Reserve `never` and `must not` for real invariants.** Use them for strict security, authority, or compatibility rules rather than rhetorical emphasis.
- **Refactor on append; avoid walls of prose.** Keep each paragraph focused on one concern (typically 2–4 sentences). When extending a specification, break dense sections into subsections, short bullet lists, or compact tables instead of appending unrelated sentences onto an existing paragraph.
- **Factor shared rules once and avoid restating foreign owners.** When one rule covers many controls or endpoints (such as localization coverage or conflict recovery), state the general rule plus explicit exceptions once. When linking to another owning specification, state only the binding or local delta instead of repeating its wire format, headers, or state machine.

| Avoid (defensive filler or PR-diff residue)                                               | Prefer (direct contract prose)                                                                                             |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| "Cancellation acceptance does not establish completion or remote rollback."               | "Cancellation acceptance records stop intent; completion is established only when the execution reaches a terminal state." |
| "Saving a value confirms its credential version in Service, not application to Sessions." | "Saving commits a new credential version in Service; each Session reports its applied version independently."              |
| "Opening the dialog, reading docs, and copying instructions do not create credentials."   | *(Omit—state only the actions that do create credentials.)*                                                                |
| "No ordinary setup question, automatic migration, or default opt-in is added."            | *(Omit PR-scope phrasing; state the steady-state default directly if it is part of the contract.)*                         |
| "Use the resource's canonical `path_style` rather than the requester's operating system." | "Path conversion uses the Device or Session descriptor's `path_style`."                                                    |

## Check Consistency and Validate

- Search changed terminology and update stale summaries, incoming links, and dependent contracts.
- Verify one owner for each fact, consistent identity/version/state terms, and explicit independent completion boundaries.
- Express separations between process-local observations and durable facts, or transport delivery and execution authority, through ownership tables, state models, and direct rules rather than repeating negative disclaimers in every paragraph.
- Make security and compatibility failures explicit. Remove stale text, orphan sections, duplication, and editing residue so the result stands alone without issue history.

For spec-only changes, format the affected files and run:

```bash
uv run --locked mdformat --number <changed-spec-files>
make verify
git diff --check -- spec
```

For accompanying implementation or shared tooling, complete the applicable repository/component gates. Reuse valid results and rerun checks when their inputs change. Report validation and unresolved decisions without claiming that formatting proves semantic correctness.
