# Repository Guide

Agent Foundation is a Python-first open-source cloud foundation for building agents and multi-agent systems, with an embeddable Agent Harness, hosted agent services, and built-in observability.

## Sources of Truth

- [CONTRIBUTING.md](CONTRIBUTING.md) owns contribution workflow, setup, and validation. Read the sections relevant to the requested change and handoff.
- [DEVELOPMENT.md](DEVELOPMENT.md) owns code quality principles and component engineering standards. Apply [Code Quality and Design](DEVELOPMENT.md#code-quality-and-design) to features, bug fixes, refactoring, and reviews; read the component rules and owning specifications relevant to the change.
- [spec/repository-model.md](spec/repository-model.md) owns repository structure and workflow boundaries. Read it before changing either.
- [spec/README.md](spec/README.md) leads to the accepted product and architecture contracts. Keep proposals, discussion, and progress in GitHub Issues; changes are reviewed through pull requests.
- `docs/` contains Markdown user documentation published with MkDocs Material; `mkdocs.yml` owns site configuration and navigation.
- [MAINTAINERS.md](MAINTAINERS.md) owns semantic reviewer routing.

Read the relevant contribution and engineering sections before changing that surface. Reuse sections already read unless they changed. This guide and skills summarize operational rules; they do not replace the owning contracts. Do not turn personal preferences or tool-specific defaults into repository requirements without an explicit project decision.

Write repository content in English, as required by [CONTRIBUTING.md](CONTRIBUTING.md#repository-language).

## Scope and Authorization

- Carry requested changes through implementation and relevant validation. Resolve routine choices from the request and repository evidence; ask only when missing information materially affects correctness, scope, or authorization. Existing authorization carries across follow-ups.
- An audit or review is read-only unless fixes are requested. Local editing does not itself authorize committing, pushing, GitHub writes, merging, deploying, or releasing. Each action must be covered by the request or established authorization; loading a skill grants none of these permissions.
- Preserve unrelated work and secrets. History rewrites, destructive cleanup, and changes to shared or deployed state require authorization covering the concrete operation and target.
- Unresolved product, architecture, security, compatibility, or scope decisions follow the Issue-to-PR workflow. Complete independent, authorized work while those decisions remain open. Routine corrections do not require a new Issue, and the workflow does not authorize posting one on the user's behalf.
- Explicit user instructions take precedence over skill guidelines, subject to system and developer instructions. Resolve apparent conflicts using the request and existing authorization. If work remains blocked by an applicable skill instruction, link its `SKILL.md`, quote the requirement, and explain the missing decision or authority while continuing independent authorized work.

Keep diffs focused and update affected contracts, implementation, tests, docs, and automation together. Report the outcome, changed files, validation, and material limitations concisely.

## Package and Release Boundaries

Python 3.13 and `packages/*` use `uv`; Rust crates live under `crates/`. Component source directories and distributions use canonical `a13n-` names, while Python imports replace hyphens with underscores (for example, `packages/a13n-stream-protocol`, `a13n-stream-protocol`, and `a13n_stream_protocol`). SDKs under `sdk/{python,go,rust,typescript}` and the companion `sdk/rust/a13n-service-cli` stay outside the root workspaces. The CLI uses the Rust SDK for every network operation and owns no service-process behavior or parallel HTTP client.

For packaging and release changes, read [repository boundaries](spec/repository-model.md#repository-surfaces), [release rules](CONTRIBUTING.md#releases), and the owning workflow. The Harness group includes Environment, Harness, and Stream Protocol at one exact release version; Harness UI releases independently against one reviewed Harness version. Source workspace dependencies remain unversioned. `apps/a13n-harness-ui` is private build input included in the UI wheel and sdist, with no independent release or committed build output; rebuilding the wheel from its sdist requires no Node.js. RC releases never advance Docker or npm `latest`.

## High-Risk Engineering Rules

Retain these constraints and read [DEVELOPMENT.md](DEVELOPMENT.md) for the full service engineering contract:

- Keep service I/O async and use canonical storage helpers. Never hold a database session or transaction across agent execution, external I/O, waits, background work, or streams. Streaming routes must not receive yielded database sessions, including through authentication dependencies.
- Generate migrations with the owning Make target against a disposable database, then review rollout safety; never write revisions from scratch. Worker and connectivity roles never migrate. The `all` and `control` roles auto-migrate under bounded PostgreSQL advisory locking; a dedicated migration job disables replica auto migration.
- Build one non-root service image with runtime role selection. Libraries use namespaced `a13n-logging` loggers; executables configure logging once.
- Keep model-visible and user-trace identifiers concise and kind-prefixed. Preserve entropy where unpredictability is part of a security or protocol contract.

## Validation

Follow [CONTRIBUTING.md](CONTRIBUTING.md#local-validation) for validation scope, required gates, and Make targets. Reuse successful checks whose relevant inputs remain unchanged. Report commands, outcomes, and unavailable checks accurately; do not claim a gate passed when it did not run.
