# Maintainers

This file is the source of truth for semantic pull-request review routing in this repository. The `commit-push-pr` skill reads the affected behavior and boundaries, matches them to the areas below, and requests the listed GitHub reviewers.

## Routing Rules

- Match a change by its intent and affected architectural boundaries. Path hints are supporting evidence, not the sole matching rule.
- A pull request may match more than one area. Request every distinct maintainer from all matching areas.
- Use the default area only when no specific area matches.
- Do not request the pull-request author as a reviewer. If every matching maintainer is the author, report that no independent reviewer is configured.
- Do not infer maintainers from organization membership, repository permissions, commit history, or other sources.
- Maintainers must be written as GitHub handles in `@user` or `@organization/team` form.

## Review Areas

| Area               | Semantic scope                                                                                                                                                                              | Path hints                                                                                                                             | Maintainers                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| Default            | Changes that do not clearly belong to another area                                                                                                                                          | Any unmatched path                                                                                                                     | `@Wh1isper`                     |
| Harness            | `agent-harness`, Pydantic AI integration, agent definitions, capabilities, execution context, tools, memory, resume, delegation, events, usage, and harness public APIs                     | `packages/agent-harness/**`, `spec/agent-harness/**`                                                                                   | `@Wh1isper`                     |
| Agent Interaction  | Agent Stream Protocol, AG-UI projection, local Agent profiles and Sessions/Threads/Turns/Items, foreground/background orchestration, WebUI, TUI, and Agent package release compatibility    | `apps/harness-ui/**`, `packages/agent-stream-protocol/**`, `packages/agent-ui/**`, `spec/agent-stream-protocol/**`, `spec/agent-ui/**` | `@Wh1isper`                     |
| Environment        | `agent-envd`, Environment Interaction Protocol, environment bindings, files, processes, ports, provider state, and environment authorization                                                | `crates/agent-envd/**`, `packages/agent-envd-client/**`, environment-related specifications                                            | `@Wh1isper`                     |
| Foundation Service | Hosted control and execution services, built-in web client, Sessions/Threads/Turns/Items, Executions/ExecutionAttempts, scheduling, workers, persistence, service APIs, and runtime plugins | `apps/foundation-web/**`, `packages/foundation-service/**`, runtime-related specifications                                             | `@hahchenchen`, `@YuxuanChen98` |
| Foundation Clients | Foundation Service language SDKs, public client contracts, and the remote `agent-foundation` CLI                                                                                            | `sdk/**`, client-related specifications                                                                                                | `@Wh1isper`                     |
| Repository         | Repository-wide architecture, governance, release engineering, CI, packaging, contribution policy, and root project metadata                                                                | `.github/**`, root-level project files, cross-component specifications                                                                 | `@Wh1isper`                     |

## Updating Ownership

Update this table in the same pull request that introduces or transfers a module boundary. Keep scopes semantically distinct and use path hints only to make common cases easier to recognize.
