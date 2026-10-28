---
title: Skills and Content Plugins
description: Enable a Skill, write a reusable workflow, and share Skills and child roles through a Content Plugin.
---

A Skill is a directory containing `SKILL.md`: instructions the Agent reads when a task needs them. Start by enabling Skills and trying the bundled configuration guide. Then add your own workflow locally or distribute it through a Git-backed Content Plugin.

## Enable Skills

Use an Agent that already produces a response and can read files, such as the [minimal file-based Agent](agents-and-subagents.md#create-an-agent-from-files).

1. Open that Agent's YAML under `agents/` beside your selected root YAML. Add `skills` to its existing `capabilities` list; keep `dynamic_environment` and your other entries. This is a merge fragment, not a complete Agent file:

   ```yaml
   capabilities:
     - capability: dynamic_environment
     - capability: skills
       configuration:
         roots: []
   ```

2. Validate the same configuration tree:

   ```console
   a13n-harness-ui config validate
   ```

   For a custom tree, put `--config /absolute/path/a13n-harness-ui.yaml` before `config`. Resolve reported errors or Capability warnings.

3. Select the Agent and start a new Run. In the TUI or WebUI composer, type `$` and choose `harness-ui-configuration`, or enter its complete name:

   ```text
   Use $harness-ui-configuration to explain where my Model files belong. Do not change files.
   ```

4. Check the tool activity: the Agent should read the selected `SKILL.md` before using its instructions. A reply alone is not evidence that the file was read.

`roots: []` enables automatic sources. Removing the `skills` Capability disables discovery for that Agent. A Skill supplies instructions; file access, tool permissions, and Model authentication still come from the Agent and Environment.

### Write your first Skill

Create `~/.agents/skills/change-summary/SKILL.md` on the Harness UI Host. This complete file defines a workflow for summarizing a change supplied in the prompt:

```markdown title="~/.agents/skills/change-summary/SKILL.md"
---
name: change-summary
description: Summarize a supplied code change for a reviewer.
---

Read the supplied change and explain:

1. What behavior changed.
2. Which checks ran and what they established.
3. What the reviewer still needs to verify.

Ask for missing change details instead of inventing them.
```

Start a new Run with the same Agent. Type `$change-summary` followed by a small change description and its test results. Check that the Agent reads your file and follows the three-part structure.

For a repository-owned Skill, save the same directory under `<project-root>/.agents/skills/` instead. Use a local root actually selected by the Thread; the configuration directory is not an automatic Project Skill source.

## Install a Content Plugin

A Content Plugin shares Skills and Markdown child roles as editable files. It does not install an executable [Harness Plugin](extensions-and-mcp.md#harness-plugin-and-run-extension-files).

1. Obtain a trusted Git repository containing `.agents/plugins/marketplace.yaml` and a plugin manifest. A directory containing only `SKILL.md` is a local Skill, not a plugin repository.

2. Install from its local Git path. Replace the path below with the actual repository:

   ```console
   a13n-harness-ui plugin install /absolute/path/to/plugin-repository
   a13n-harness-ui plugin list --format json
   ```

   If the repository offers multiple valid plugins, add `--plugin plugin-review` with the desired ID. For a remote Git repository, pass its URL instead of the local path. Add `--ref` to select an existing tag or commit.

3. Check the listed plugin ID, installed path, and commit. Installation copies the selected content to `<data-root>/content-plugins/<plugin-id>` and records its origin.

4. Start a new Run with Skills enabled. Choose a contributed Skill with `$` and check that the Agent reads its installed file.

Use the same `--config` and `--data-root` selection as the application when running these commands. Existing plugin IDs are never overwritten. Installed files can be edited locally; they are not continuously synchronized with Git, and there is no `plugin update` command.

## Use contributed subagents

Plugin installation makes child roles available for selection. To enroll one, merge its ID into the parent Agent's `subagents` list:

```yaml
subagents:
  - markdown: subagent-investigator
```

Use an ID actually supplied by your plugin. Run `config validate`, select the parent Agent, and start a new Run. Check the child roster before asking the Agent to delegate. See [Agents and subagents](agents-and-subagents.md) for Model inheritance and tool filtering.

Local `subagents/*.md` overrides a plugin child with the same ID. Plugin-to-plugin collisions use deterministic ordering and emit diagnostics. Built-in child IDs are reserved. A missing selected child fails composition rather than becoming a different role.

## Remove a Content Plugin

Back up local edits and remove any Agent references to contributed children first. **Uninstall deletes the installed directory, including local edits, without another confirmation.** Then run:

```console
a13n-harness-ui plugin uninstall plugin-review
a13n-harness-ui plugin list --format json
```

Replace `plugin-review` with the installed ID. Check that it is absent from the list. Uninstall also works when the installed manifest is broken. Reinstalling copies repository content; it does not restore deleted edits, and resuming an older Run does not recreate the directory.

## Author a Content Plugin repository

Use a Git repository to package the `change-summary` Skill above. All paths in these steps are relative to that repository root.

1. Create this complete marketplace file:

   ```yaml title=".agents/plugins/marketplace.yaml"
   schema_version: "1"
   name: Team content
   plugins:
     - path: ./plugins/review
   ```

2. Create this complete plugin manifest:

   ```yaml title="plugins/review/.a13n-plugin/plugin.yaml"
   schema_version: "1"
   kind: content_plugin
   id: plugin-review
   name: Review workflows
   version: "1.0.0"
   description: Shared review Skills.
   skills: ./skills
   ```

3. Save the earlier Skill file at `plugins/review/skills/change-summary/SKILL.md`. The manifest's `./skills` is relative to `plugins/review`, not the repository root.

4. Commit these files to the source repository. Installation clones Git content, so uncommitted working-tree edits are not the installation source.

5. Follow [Install a Content Plugin](#install-a-content-plugin) with that repository path. Confirm the installed commit and test `$change-summary` in a new Run.

To include child roles too, add `subagents: ./subagents` to the manifest and place [canonical Markdown roles](agents-and-subagents.md#write-a-markdown-subagent) in `plugins/review/subagents/`.

### Marketplace fields

| Field            | Requirement                                                       |
| ---------------- | ----------------------------------------------------------------- |
| `schema_version` | Required `"1"`                                                    |
| `name`           | Required, 1–256 characters                                        |
| `plugins`        | Required, 1–256 entries with unique paths                         |
| `plugins[].path` | Canonical repository-relative plugin directory, 3–4096 characters |

### Manifest fields

| Field            | Requirement / default                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------ |
| `schema_version` | Required `"1"`                                                                                         |
| `kind`           | Required `content_plugin`                                                                              |
| `id`             | Required `plugin-` ID; lowercase ASCII letters/digits with single separating hyphens; 8–128 characters |
| `name`           | Required, 1–256 characters                                                                             |
| `version`        | Required non-empty label, up to 128 characters                                                         |
| `description`    | Required, 1–4096 characters                                                                            |
| `skills`         | Optional relative Skill directory                                                                      |
| `subagents`      | Optional relative Markdown-role directory                                                              |

At least one of `skills` or `subagents` is required. Paths use `./...` and stay inside their owner without traversal or symlinks. Unknown fields are rejected. Installation applies size, file-type, Git-operation, and YAML-parser limits. `.a13n-plugin/origin.json` records repository/commit provenance separately from the editable manifest; the version label does not trigger updates.

## Automatic sources and precedence

When Skills share a name, the highest-priority available source wins:

1. Explicit `roots`, with later entries winning.
2. Selected Device working directories' `.agents/skills`, with later bindings winning.
3. The first local root's `.agents/skills`.
4. Later local roots' `.agents/skills` in selection order.
5. Installed Content Plugin Skill roots, with lexicographically later plugin IDs winning.
6. User Skills from `~/.agents/skills`.
7. Release-owned Skills from the built-in mount.

Without local roots, their tiers disappear. Device sources remain available through `/environment/<alias><device-cwd>/.agents/skills`.

Each Run prepares its own catalog. Start a new Run to discover new or renamed Skills; an active Run keeps its catalog. Missing optional directories contribute no Skills. Unavailable explicit roots fail preparation. Files are read when used, so a captured path does not pin file contents.

Complete recognized `$name` text and completion selection have the same effect. New submissions resolve against the current catalog; steering uses the active Run's catalog. Unknown names remain ordinary text. The draft keeps editable `$name` text. Programmatic Skill references have separate validation; a catalog preview does not reserve a later Run's catalog.

## Explicit Skill roots

Use an explicit root for a directory outside the automatic conventions but already accessible through the selected Environment. Merge this entry into the Agent's Capability list, replacing the path:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /absolute/mounted/project/team-skills
```

`roots` accepts up to 128 ordered, unique, canonical absolute Environment paths. These supplement automatic sources. Use the mounted Host path in Host-path-preserving layouts, or a route such as `/workspace/team-skills` in a virtual layout. Relative paths, `~`, and Provider-internal paths are invalid. Listing a directory does not mount it; every explicit root must resolve when the catalog is prepared.

## Built-in configuration Skill

`harness-ui-configuration` includes release-matched documentation and a page/section index. It works offline from the read-only mount at:

```text
/environment/builtin-skills/harness-ui-configuration
```

The file-only, non-default mount supports inspection, reading, search, and copy-source access. It remains available with Skills enabled, including without a Project or with a remote or sandboxed Project. It grants no configuration-write privilege and copies no files into user directories. Higher-priority Skills can override its name. Online references in the index can describe another release.

For source development, regenerate bundled copies from `docs/a13n-harness-ui/` after changing documentation or navigation:

```console
make a13n-harness-ui-skills
```

`make a13n-harness-ui` includes this step. Edit the source documentation, not the generated Skill tree. Published wheels and sdists already contain the Skill; rebuilding a wheel from its sdist requires neither the repository docs tree nor Node.js.

## User and plugin file mounts

With Skills selected, Harness UI normally exposes exactly `~/.agents/skills` through a writable, file-only user mount and creates the directory if absent. It grants no shell or access to the whole home directory. An exactly matching Host-path-preserving Project root can be reused instead of adding a duplicate mount.

Installed plugin directories have separate editable file mounts. Mount availability lets the Agent inspect content; the selected `skills` Capability controls discovery, and the parent Agent's roster controls child enrollment.

## Troubleshoot discovery

Check these in order:

1. **No Skills in completion:** confirm the selected Agent includes `skills`, validate the configuration, and start a new Run.
2. **Your Skill is missing:** check the actual Thread roots, the `SKILL.md` location and front matter, and the Environment path. Read `<data-root>/logs/terminal.log` for skipped-entry diagnostics.
3. **The wrong Skill wins:** compare its source against the precedence list above.
4. **A plugin Skill or child is missing:** run `plugin list --format json`; inspect the installed manifest's paths and the Agent's selected child IDs before reinstalling.
5. **The Skill is listed but cannot be used:** check file-tool visibility and Environment permissions. Skill selection does not grant either.
