---
name: docs-writing
description: Author, rewrite, translate, or reorganize user documentation under docs/ with audience-specific structure, anti-defensive prose, idiomatic Simplified Chinese, and SEO metadata. Use when creating or updating documentation pages, navigation, or docs site metadata.
---

# Documentation Authoring

Maintain `docs/` as user-centric, technically accurate bilingual documentation. Follow `AGENTS.md`, [Documentation Changes](../../../CONTRIBUTING.md#documentation-changes), and [Repository Model](../../../spec/repository-model.md#documentation-system).

## 1. Ground in Code and Accepted Contracts

Read the owning specification under `spec/`, the live implementation or Pydantic schema, and existing pages in the target section before drafting or refactoring:

- **Verify every symbol and key against code:** Never invent or guess Python imports, method signatures, CLI flags, or YAML/TOML/JSON keys. Check Python imports against installed workspace modules (`a13n_harness`, `a13n_service`, `a13n_envd_client`, `a13n_stream_protocol`), Harness UI YAML blocks against `a13n_harness_ui.configuration` (`HarnessUiDocument`, `ModelResource`, `AgentResource`, `McpServerResource`, `ProjectResource`), and Service TOML blocks against `a13n_service.settings.Settings`.
- **Preserve incoming anchors and reference tables:** Grep `docs/` for incoming `#anchor` links before renaming headings (or preserve legacy targets via empty `<span id="..."></span>` outside `docs/a13n-harness-ui/`, which must stay plain `.md`). Do not hand-edit generated reference pages (`configuration-reference.md`) or strip exhaustive reference tables (`command-reference.md`, `python-client.md`, `a13n-logging/index.md`).
- **Check regression suites:** Inspect assertions in `scripts/tests/test_docs.py`, `packages/a13n-harness-ui/tests/test_builtin_skills.py`, and `packages/a13n-service/tests/test_documentation.py`.

## 2. Write for Users, Not as an Implementation Spec

Documentation exists to help users understand concepts and accomplish tasks—not to mirror internal state machines or edge-case invariants from `spec/`.

Apply four structural rules to every narrative page:

1. **Shallow to deep, big to small:**
   - **Lead with the mental model and fast path:** Open with what the feature does in 1–3 sentences, followed immediately by the shortest working example or numbered procedure.
   - **Expand core workflows next:** Cover the 80% tasks users actually perform using concise tables, runnable snippets, or a clean Mermaid diagram where relationships matter.
   - **Push advanced options and edge cases to the bottom:** Put specialized modes (custom transports, recovery retries, low-level tuning) and troubleshooting at the end, never before the main workflow.
2. **Cut implementation details that do not change user action:**
   - Omit internal storage layouts, background garbage-collection mechanics, internal queue plumbing, and repetitive negative disclaimers unless the user must configure or act on them.
   - Keep paragraphs concise (2–4 sentences). Prefer a compact table or bulleted list over a dense wall of prose.
3. **Separate task guides from under-the-hood deep dives:**
   - **Task guides** stay concise and workflow-focused while keeping runnable code/CLI/YAML/TOML examples, essential parameter/status tables, and copy-pasteable recipes—never compress them into hollow stubs, and never clutter them with internal plumbing.
   - **Architecture deep dives (`architecture.md`)** explain how the system works beneath the surface (state machines, wire framing, hybrid storage, lease fencing, prompt-cache preservation, and multi-layer crash recovery) using Mermaid diagrams and concise invariants grounded in `spec/`. Link to them for curious engineers rather than repeating internals across task pages.
4. **Target the audience for each surface** (defined in [Documentation Changes](../../../CONTRIBUTING.md#documentation-changes)):

| Surface                                                       | Target audience                             | Primary focus                                                                                                                                   |
| ------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs/a13n-harness/`, `docs/a13n-stream-protocol/`            | Python developers                           | Core mental model (`AgentSpec` / `RunBindings` / `HarnessState`), runnable Python examples, tools, subagents, CodeAct, and event streams        |
| `docs/a13n-harness-ui/`                                       | Terminal/WebUI users & DIY builders         | Quick setup, TUI/WebUI workflows, Live voice, Host Browser, Bot, Coordinator, YAML recipes, Markdown subagents, `SKILL.md`, and Content Plugins |
| `docs/a13n-service/`                                          | Platform engineers, team admins & app teams | Deployment, RBAC & credentials, Agents, Environments, Memory, MCP & tools, Schedules, SDK/HTTP integration, and day-2 operations                |
| `docs/environments/`, `docs/a13n-envd/`, `docs/a13n-logging/` | Infrastructure & sandbox integrators        | Native vs. Envd execution, OS sandboxing, egress policies, computer use, and provider configuration                                             |

## 3. Write Direct, Anti-Defensive Prose

State what the system does, how to use it, and how to verify the result. Do not negotiate with an imagined critic or litter introductions and procedures with negative disclaimers.

| Avoid (defensive or negative framing)                                                          | Prefer (direct, user-centric guidance)                                                                                        |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| "Saving configuration alone does not test connectivity or prove model entitlement."            | "After saving the configuration, send a short test prompt to verify end-to-end model connectivity."                           |
| "A cancellation receipt is not proof that cleanup has finished."                               | "Query the current execution status after requesting cancellation to confirm cleanup completed."                              |
| "A Skill read records access; it does not establish that the Agent followed the instructions." | "The trace span records when `SKILL.md` is read; inspect subsequent tool calls to review how the Agent applied the workflow." |
| "Configure requests without confusing connection settings and context budgets."                | "Configure provider connection settings and local context budgets for each Model."                                            |

## 4. Author English First, Then Idiomatic Chinese

1. **Finalize the English page (`.md` or `.mdx`):**
   - Craft search-intent-driven `title` and `description` front matter (plus optional `sidebarTitle`). Quote any YAML value containing `: `. Do not add a `#` (`h1`) heading in the body.
   - Keep `docs/a13n-harness-ui/` strictly plain `.md` (never `.mdx`), as pages are bundled verbatim into the built-in configuration Skill.
2. **Synchronize Simplified Chinese (`.zh-CN.md` / `.zh-CN.mdx` and `meta.zh-CN.json`):**
   - Translate into natural, idiomatic technical Chinese written for native engineers; avoid stiff clause-by-clause calques or stacked passive constructions.
   - Preserve exact heading count, order, and depth (`##`, `###`, `####`)—plus any `<span id="..."></span>` anchors—so `remark-translation-headings` assigns canonical English heading IDs to every Chinese section.
   - Keep all relative links in `.zh-CN.md` pointing to the canonical English file path and English anchor slug (for example, `[模型配置](models.md#model-selection)`).

## 5. Validate and Update Bundled Consumers

Run the checks proportional to the documentation surfaces changed:

```bash
# Format changed Markdown files:
uv run --locked pre-commit run mdformat --files <changed-md-files>

# If docs/a13n-harness-ui/** changed, refresh and test the bundled configuration Skill:
make a13n-harness-ui-skills
uv run --locked pytest packages/a13n-harness-ui/tests/test_builtin_skills.py

# If docs/a13n-service/** changed, refresh the bundled search index and test it:
uv sync --locked --all-packages
uv run --locked pytest packages/a13n-service/tests/test_documentation.py

# If Service settings or Environment provider models changed, refresh generated references:
uv run --locked python scripts/docs/references.py

# Run documentation source & schema regression tests:
uv run --locked pytest scripts/tests/test_docs.py

# Build the bilingual documentation site and verify 0 broken links/anchors and passing export/search tests:
make docs-build
```
