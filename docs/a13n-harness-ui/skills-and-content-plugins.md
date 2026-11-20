---
title: Skills and Content Plugins
description: Extend your agent with reusable procedural instructions (Skills) and distribute teams of subagents and workflows via Git-backed Content Plugins.
---

A **Skill** is a specialized procedure packaged as a directory containing a `SKILL.md` instruction file. Rather than inflating your agent's initial prompt with endless procedural rules, Skills are discovered just-in-time: the model reviews their short descriptions and loads their full instructions only when a matching task arises.

A **Content Plugin** takes this further, allowing you to package multiple Skills and specialized subagent roles into a version-controlled Git repository that any team member can install with a single command.

## Enable Skills

Ensure your active Agent has the `skills` capability registered in its YAML configuration (located in your `agents/` directory):

```yaml title="agents/default.yaml"
capabilities:
  - capability: dynamic_environment
  - capability: skills
    configuration:
      roots: []  # Automatically discover from project and user directories
```

Validate your configuration:

```bash
a13n-harness-ui config validate
```

Start the TUI (`a13n-harness-ui`). In the composer, type `$` to open the interactive Skill picker, or invoke a skill directly by name:

```text
Use $harness-ui-configuration to review how Model configuration files are structured.
```

When execution begins, the agent inspects the selected skill catalog, reads the full `SKILL.md` file using its file tools, and follows the structured guidelines.

### Write your first Skill

Creating a custom skill requires only a folder and a Markdown file. Create `~/.agents/skills/change-summary/SKILL.md` on your host machine:

```markdown title="~/.agents/skills/change-summary/SKILL.md"
---
name: change-summary
description: Summarize code changes and git commits cleanly for pull request reviews.
---

When asked to summarize code changes, structure your output into three distinct sections:

1. **Architectural Intent**: High-level summary of what changed and why.
2. **Key Modifications**: Grouped bullet points covering altered modules and interfaces.
3. **Validation & Risks**: List of tests executed, edge cases considered, and potential regressions.

Ask clarifying questions if git diff context is incomplete rather than assuming behavior.
```

Start a new run in the TUI, type `$change-summary`, and provide a git diff. The agent immediately references your custom guidelines to structure its response.

To scope a skill to a specific repository, place it inside `<project-root>/.agents/skills/change-summary/SKILL.md`.

## Install a Content Plugin

Content Plugins package collections of Skills and Markdown-defined subagents inside a Git repository.

1. Locate a Git repository containing a valid plugin manifest.
2. Install the plugin using the CLI:

```bash
a13n-harness-ui plugin install https://github.com/example-org/team-agent-plugins.git
```

To install from a local directory during plugin development, pass the local filesystem path instead:

```bash
a13n-harness-ui plugin install /path/to/my-plugin-repo
```

Verify your installation:

```bash
a13n-harness-ui plugin list --format json
```

The plugin's skills immediately become available in the TUI/WebUI `$` autocomplete menu.

## Use contributed subagents

Content Plugins can also contribute specialized Markdown subagents (such as security auditors, documentation writers, or code reviewers). To enroll a contributed subagent into your primary agent, reference its ID in your agent's YAML:

```yaml title="agents/default.yaml"
subagents:
  - markdown: subagent-security-reviewer
```

Validate with `a13n-harness-ui config validate`. Your primary agent can now delegate specialized tasks to that subagent role.

## Remove a Content Plugin

To uninstall a plugin and remove its installed files from your local environment:

```bash
a13n-harness-ui plugin uninstall plugin-review
```

Verify that the plugin no longer appears in `a13n-harness-ui plugin list`.

## Author a Content Plugin repository

Creating a distributable Content Plugin repository requires three simple files:

1. Create `.agents/plugins/marketplace.yaml` at the root of your Git repository:

```yaml title=".agents/plugins/marketplace.yaml"
schema_version: "1"
name: Team Workflows
plugins:
  - path: ./plugins/engineering
```

2. Create the plugin manifest at `plugins/engineering/.a13n-plugin/plugin.yaml`:

```yaml title="plugins/engineering/.a13n-plugin/plugin.yaml"
schema_version: "1"
kind: content_plugin
id: plugin-engineering-tools
name: Engineering Workflows
version: "1.0.0"
description: Standardized engineering review skills and specialized auditor subagents.
skills: ./skills
subagents: ./subagents
```

3. Place your skill directories inside `plugins/engineering/skills/` (e.g., `skills/change-summary/SKILL.md`) and subagent Markdown files inside `plugins/engineering/subagents/`.

Commit these files to Git and distribute your repository URL to your team.

### Marketplace fields

| Field            | Description                                        | Requirement                       |
| :--------------- | :------------------------------------------------- | :-------------------------------- |
| `schema_version` | Manifest format version                            | Must be `"1"`                     |
| `name`           | Human-readable marketplace collection name         | 1–256 characters                  |
| `plugins`        | Array of relative paths pointing to member plugins | At least one plugin path required |

### Manifest fields

| Field            | Description                                              | Requirement                                        |
| :--------------- | :------------------------------------------------------- | :------------------------------------------------- |
| `schema_version` | Plugin manifest format version                           | Must be `"1"`                                      |
| `kind`           | Type of plugin                                           | Must be `content_plugin`                           |
| `id`             | Unique alphanumeric identifier with hyphens              | Lowercase string, e.g., `plugin-engineering-tools` |
| `name`           | Display title shown in UI menus                          | 1–256 characters                                   |
| `version`        | Semantic version string                                  | e.g., `"1.0.0"`                                    |
| `description`    | Summary of contributed capabilities                      | 1–4096 characters                                  |
| `skills`         | Relative directory containing skill subdirectories       | Optional if subagents provided                     |
| `subagents`      | Relative directory containing `.md` subagent definitions | Optional if skills provided                        |

## Automatic sources and precedence

When multiple skills share the same name, Harness UI applies a deterministic priority cascade:

1. Explicit paths defined in `capabilities.skills.configuration.roots` (highest priority).
2. Project working directories: `<project-root>/.agents/skills/`.
3. Installed Content Plugins in order of registration.
4. User-global skills: `~/.agents/skills/`.
5. Built-in system configuration skills (lowest priority).

This hierarchy ensures that repository-specific guidelines seamlessly override user-level or system defaults when working within a project.

## Explicit Skill roots

To mount a skills directory located outside the standard conventions, list its absolute path in your agent's capability configuration:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /shared/enterprise/team-skills
```

Harness UI indexes the directory at runtime and merges its skills into the catalog.

## Built-in configuration Skill

Harness UI ships with a built-in skill named `harness-ui-configuration`. It provides offline documentation and configuration indexes directly to the agent without requiring external web access.

Whenever an agent needs guidance on configuring a model provider, editing YAML parameters, or tuning execution policies, typing `$harness-ui-configuration` allows the agent to consult release-accurate operational manuals.

## User and plugin file mounts

When Skills are enabled, Harness UI automatically exposes `~/.agents/skills` to the agent via a dedicated, read-only file mount. This provides safe read access to your instructions without granting arbitrary access to your user home directory. Installed Content Plugins are similarly mapped into isolated, read-only mounts.

## Troubleshoot discovery

If a custom skill does not appear in your autocomplete menu:

1. **Verify capability:** Ensure `capability: skills` is declared in your active agent's YAML file.
2. **Validate YAML:** Run `a13n-harness-ui config validate` to surface any syntax errors.
3. **Verify directory structure:** Ensure `SKILL.md` is capitalized and placed inside a named folder (e.g., `my-skill/SKILL.md`).
4. **Inspect precedence:** If a different skill with the same name is chosen, review the precedence rules above to verify which directory takes priority.
