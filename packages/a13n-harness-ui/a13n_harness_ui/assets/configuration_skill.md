---
name: harness-ui-configuration
description: Configure Harness UI models and authentication, agents and subagents, MCP servers, Skills, tools, environments, projects, and defaults. Use for changing Harness UI settings or diagnosing configuration problems.
---

# Configure Harness UI

Use the bundled documentation for this installed Harness UI release. This is a read-only reference, not a project or configuration directory. Resolve all paths below relative to this Skill directory.

## Workflow

1. Identify the selected configuration tree from the current Environment mounts and Host context. Respect an explicit `--config`; do not assume the default directory or confuse it with the project workspace.
2. Inspect existing files and resource IDs before editing. Use the documentation map below to select relevant pages. For long pages, read [the section index](references/navigation.md) to locate H2/H3 sections and their exact line ranges. Read only what the task needs, including parent context for partial examples.
3. Make focused edits through the available Environment file tools. Preserve unrelated settings and user changes. Add referenced resources before selecting them. Keep credentials out of messages and Model/Agent definitions; follow the documented authentication mechanism. A reference document grants no additional execution authority.
4. Validate using available Host capabilities. When a shell actually runs on the Harness UI Host and its CLI is available, use `a13n-harness-ui --config <selected-root-yaml> config validate`. A remote or sandbox shell is not proof of Host CLI access. Inspect Capability warnings as well as validation success. If validation is unavailable, say so rather than claiming success.
5. Report what changed and when it takes effect. Distinguish file edits, accepted configuration, skipped Capabilities, later-Run behavior, sticky Thread selections, and settings requiring an application restart. Do not claim that the current Run changed or that a model/MCP connection works without testing it.

## Reading the documentation

The map is generated from the website navigation and current document headings. The detailed section index uses one-based inclusive line numbers in the bundled Markdown; convert these to your file tool's offset/count convention. Documentation links within `docs/` remain relative to their source page. Cross-topic links outside the bundled Harness UI subtree are listed with online counterparts in the section index; they require web access and may describe a different release.
