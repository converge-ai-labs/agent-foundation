# Specification Structure and Contract Template

Use this reference for placement, splits, navigation, and section selection. Adapt the structure to the contract; it is not a required heading checklist.

## Information Hierarchy

| Layer                        | Owns                                                                                          |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| `spec/README.md`             | Platform definition, components, dependency direction, global authority, top-level navigation |
| `spec/<subsystem>/README.md` | Subsystem catalog, reading paths, authority rules, terminology conventions                    |
| `00-overview.md`             | Subsystem architecture, boundaries, major components, end-to-end flow, stable principles      |
| Numbered detail document     | One cohesive domain, API, lifecycle, protocol, security, compatibility, or packaging contract |

Update an existing owner when the change refines its contract. Add or split a detail document when it has distinct authority/lifecycle or independently meaningful content that would otherwise mix responsibilities. Length alone is not a reason to split. A reader should be able to navigate from platform to subsystem to owning detail.

## Adaptive Detail-Document Shape

A detailed contract explains its design position and boundaries, using existing headings where appropriate. Select further sections according to the subject:

```markdown
# <Contract Name>

## Design Position

<What the contract is, why the boundary exists, and which layer is authoritative.>

## Boundaries

<Ownership, scope, and links to adjacent contracts.>

## <Core Model or Contract>

<Typed conceptual schema, field meanings, relationships, and authority.>

## <Flow or Lifecycle>

<Observable interactions, legal transitions, and independent completion boundaries.>

## Failure Semantics

<Observable outcomes, cancellation, retries, reconciliation, and unknown effects.>

## Compatibility

<Version axes, additive/breaking behavior, and migration owner.>

## Trade-offs

<Costs of the accepted design and the owner that absorbs them.>

## Invariants

<Testable statements that implementations and reviews can verify.>
```

Failure, compatibility, security, trade-off, and lifecycle sections are conditional. Avoid overlapping sections that repeat the same contract.

## Choose a Representation

- Use an ownership table when several layers participate in a flow. Separate observations from the authority that commits durable completion.
- Use typed conceptual schemas when relationships are clearer than prose; label conceptual versus wire format and explain field authority.
- Use state diagrams for public/durable states, with transition preconditions and terminal/retry meaning.
- Use sequence diagrams for cross-boundary interactions. Identify who accepts or commits each fact; distinguish external delivery, telemetry, usage settlement, and execution completion where independent.
- Use failure tables to distinguish failure before dispatch from unknown outcome after possible side effects. Useful columns are failure, observable outcome, retry/reconciliation, and authority.

## Navigation and Terminology

Link a foreign concept to its owner on first material use, using a relative Markdown link and a section anchor where helpful. Summaries may state a conclusion but must not duplicate a schema, state machine, or field table.

Update incoming links, catalogs, and reading paths when ownership or filenames change. A complete catalog lists every owned document; label a selective entry list as selective.

Preserve exact spelling/capitalization of named contracts. Define overloaded terms before use and use `must` for a genuine invariant or compatibility requirement. Separate identity from authority, routing from authorization, observation from commitment, and acceptance from delivery.

Apply the consistency and validation checks in [SKILL.md](../SKILL.md) after restructuring.
