---
name: spec-writing
description: Author and maintain this repository's accepted technical design under spec/. Use whenever creating, updating, reviewing, splitting, indexing, or reorganizing a specification; turning a concluded design discussion into normative architecture; or changing a domain, lifecycle, API, protocol, security, compatibility, or ownership contract that must remain aligned with spec. Enforces broad-to-deep organization, one owner per fact, cross-document links, and separation from proposals, status, user docs, and implementation standards.
---

# Specification Authoring

Maintain `spec/` as the internally consistent, current technical design of Agent Foundation. Write the accepted design directly; keep the discussion that produced it elsewhere.

## Scope

Use specifications for technical contracts such as:

- system and subsystem boundaries;
- domain concepts, identity, authority, and ownership;
- public APIs, protocols, schemas, and extension points;
- state machines, execution flows, failure semantics, and completion facts;
- persistence, security, compatibility, versioning, and deployment semantics;
- the consequences and costs of the selected design.

Product behavior belongs in `spec/` only when it establishes a technical contract. Market positioning, user guidance, coding conventions, delivery planning, user stories, and independent acceptance-criteria backlogs belong elsewhere. The specification is still normative: implementation and review must conform to its accepted technical contracts.

## Workflow

### 1. Establish authority and scope

1. Read `AGENTS.md`, `CONTRIBUTING.md`, `spec/repository-model.md`, and `spec/README.md`.
2. Read the relevant subsystem `README.md`, its reading path, and every document that owns a changed concept.
3. Search all of `spec/` for the affected names, states, schemas, and claims before editing.
4. Follow the Issue-to-PR flow in `CONTRIBUTING.md` and identify the accepted outcome supplied by the user or concluded discussion. If material alternatives remain unresolved, stop and keep the discussion in the GitHub Issue rather than encoding options in `spec/`.

Do not infer a new product or architecture decision merely from current implementation behavior.

### 2. Choose placement and ownership

Select the shallowest document that can own the fact without becoming a detail dump:

- `spec/README.md` owns platform-wide definition, components, dependency direction, and global authority boundaries.
- `spec/<subsystem>/README.md` owns the subsystem catalog, reading paths, authority rules, and local conventions.
- `00-overview.md` owns subsystem architecture, scope, major components, end-to-end flow, and stable principles.
- Numbered detail documents each own one cohesive contract or cross-cutting concern.

Do not let an overview become the sole owner of growing subsystem details. Once a stable fact needs its own schema, lifecycle, failure rules, or repeated references, assign it a detail owner and leave only a linked summary in the overview.

Update an existing owning document when possible. Create a new detail document only when the contract is independently meaningful, has enough depth to justify an owner, and would otherwise blur an existing document's responsibility. Keep numeric prefixes as a deliberate broad-to-deep reading order; do not renumber stable documents casually.

Use one owner per durable fact. Overviews may summarize a fact briefly, but schemas, state machines, failure rules, and field semantics live only in their owning document.

Read [references/structure-and-template.md](references/structure-and-template.md) when adding a document, restructuring a subsystem, or deciding which sections a contract needs.

### 3. Draft the accepted design

Write in present tense with decisive, testable language. Describe what the system is and how it behaves, not what a team plans to implement.

Before drafting schemas or APIs, establish the domain model:

1. Walk through representative end-to-end use flows before choosing resources, schemas, or tables. If a common operation cannot be expressed naturally, the model is not ready.
2. Derive the core concepts, identities, owners, relationships, and lifecycles from those flows.
3. Separate values that can change independently; keep values together when they have no independent lifecycle or meaning.
4. Give each concept one owning specification, one canonical model, and one canonical term.
5. Add a separate model only when it has independent identity, lifecycle, authority, compatibility, or query value.
6. Normalize different entry paths such as managed references, revisions, overrides, inline definitions, triggers, and children into the same core concepts.
7. Use names that state what a concept is without repeating its project or module namespace.

Read [references/domain-modeling-and-naming.md](references/domain-modeling-and-naming.md) when adding or restructuring a domain model, introducing several related schemas, changing a core term, or reviewing model and field names.

For each material contract, make clear:

- its design position and purpose;
- what it owns and what it explicitly does not own;
- its authority and trust boundary;
- the typed data, lifecycle, or interaction it exposes;
- the main success flow and independent completion boundaries;
- failure, cancellation, retry, and unknown-outcome semantics where applicable;
- security, compatibility, and versioning consequences;
- invariants that implementations and reviews can verify.

Use upstream public primitives directly when they already own the semantics. Add project abstractions only where they establish a stable cross-host or cross-provider contract. Mark Python-like schemas as conceptual unless the document intentionally defines a serialized wire format.

Before documenting an internal mechanism, ask whether it could be replaced without changing observable behavior, authority, security, or compatibility. If yes, omit it from the technical contract.

Use Mermaid for architecture, lifecycle, and interaction diagrams. Prefer tables for ownership, field meaning, failure outcomes, and boundary comparisons. Keep prose focused on semantics that diagrams and schemas cannot express alone.

### 4. Link instead of duplicating

Use relative Markdown links to the owning document when another contract is needed. Update the subsystem catalog and relevant reading paths whenever a document is added, renamed, removed, or changes ownership.

Maintain this navigation shape:

```text
platform overview
  -> subsystem index
      -> subsystem overview
          -> owning detail contract
              -> related owning contracts
```

A detail document may link laterally to another owner. It must not copy that owner's schema or state machine and then evolve a competing version.

### 5. Review consistency

Before finalizing:

1. Search every occurrence of changed terminology and update stale summaries or links.
2. Verify that each fact has one authority and one owning document.
3. Check identity, version, state, lifecycle, completion, and failure terms across diagrams, tables, schemas, and prose.
4. Separate process-local observations from durable facts and transport delivery from execution authority.
5. Confirm security and compatibility boundaries fail explicitly rather than relying on implied behavior.
6. Remove accidental duplication, truncated or residual text, orphan sections, discussion history, and implementation-status language.
7. Ensure the design can be understood from `spec/` without reading an Issue, meeting note, code diff, or roadmap.

### 6. Validate the change

For a spec-only change, run:

```bash
uv run --locked mdformat --number <changed-spec-files>
make lint
git diff --check -- spec
```

Run the broader repository checks required by `AGENTS.md` when the specification changes with implementation or shared tooling. Review the final diff as a technical contract, not only as formatted Markdown.

## Editing Existing Specifications

Change the owning detail document first, then update broader summaries and dependent contracts. Delete or rewrite stale statements rather than appending corrections, historical notes, or “superseded” sections. The final set describes one current design and does not require chronological reconstruction.

Preserve established terminology and document ownership unless the accepted change intentionally replaces them. When ownership moves, update both catalogs and every incoming reference in the same change.

## Guardrails

Do not put the following in `spec/`:

- proposals, RFC drafts, unresolved alternatives, or open-question logs;
- issue summaries, meeting notes, decision chronology, or review transcripts;
- implementation checklists, acceptance criteria, roadmaps, milestones, or status matrices;
- statements such as “not implemented yet,” “phase two,” or “future work”;
- contributor commands, coding conventions, migration procedures, or package setup;
- user tutorials, operational runbooks, release notes, or marketing language;
- implementation-private classes, file layouts, node hooks, thread-marshalling details, or algorithms unless they are intentionally part of the observable technical contract.

Accepted trade-offs are valid when they explain the cost of the selected design. Do not preserve rejected alternatives or the debate that preceded it.

Do not create generic templates, empty subsystem folders, or placeholder documents inside `spec/`. Add only accepted, substantive technical design.
