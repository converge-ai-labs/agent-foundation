---
title: Skills and Content Plugins
description: Enable Skills and install Content Plugins that share Skills and subagent roles through Git.
---

Skills provide procedural instructions and reference files. Content Plugins distribute editable Skills and Markdown subagent roles in a Git repository. Neither mechanism installs an executable Harness Plugin, grants tool permissions, or replaces Model authentication.

## Enable Skills

Add this entry to the Agent's existing `capabilities` list:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots: []
```

An empty list keeps automatic sources enabled; omitting the Capability disables Skill discovery for that Agent Run. Preserve other Capability entries when editing.

Type `$` in the TUI or WebUI composer to discover Skill names. For example, ask the Agent to use `$harness-ui-configuration` to inspect its configuration. Recognized names remain visible prompt text and carry references that the App validates before Send or steering. Older references resolve by name against the current catalog; steering uses the active Run's pinned catalog. Unknown dollar-prefixed text stays ordinary text. A reference requests that Skill without capturing its contents or granting extra authority. There is no `/skill-name` command.

## Automatic sources and precedence

If two sources use the same Skill name, the first available source in this precedence list wins:

1. Explicit `roots`, with a later entry winning over an earlier one.
2. The first local root's `.agents/skills` directory.
3. Later local roots' `.agents/skills` directories in selection order.
4. Installed Content Plugin Skill roots; a lexicographically later plugin ID wins.
5. User Skills from `~/.agents/skills`.
6. Release-owned Skills from the built-in mount.

Without local roots, only the two local-root tiers disappear. Built-in, user, plugin, and explicit sources still work. The configuration directory does not implicitly become a Project Skill source.

The catalog is selected at Run preparation. New Run captures can see source changes; the active Run does not rescan mounts. Missing optional directories are simply absent, while unavailable explicit roots fail preparation. Skill file contents are read when used, not copied into the Run.

## Explicit Skill roots

`configuration.roots` accepts at most 128 ordered unique canonical absolute **Environment paths**. Roots supplement automatic sources rather than replacing them.

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /absolute/mounted/project/team-skills
```

Use paths exposed by the selected Environment. Do not use `~`, a relative path, or a Provider-internal path. Host-path-preserving layouts use their mounted canonical Host paths; virtual layouts use routes such as `/workspace/team-skills`. Changing layout can require updating explicit roots.

Listing a Host directory here does not mount it or bypass Sandbox policy. Each required root must resolve when the catalog is prepared.

## Built-in configuration Skill

The wheel and sdist include **`harness-ui-configuration`** with release-matched Harness UI documentation and a generated page/section index. It is available offline; runtime discovery does not download a website or regenerate documentation.

The read-only `builtin-skills` mount uses the stable path:

```text
/environment/builtin-skills/harness-ui-configuration
```

It is non-default and file-only: inspection, reading, search, and copy-source operations, but no mutation, shell, processes, ports, or output operations. When the Agent selects `skills`, it remains available without a Project and with a sandboxed or remote Project Provider. No files are copied into the user's configuration directory, Project, or `~/.agents/skills`.

The Skill has lowest source priority and can be overridden by name through a higher-priority source. Its instructions still use ordinary file operations and validation; it introduces no configuration-write privilege. External references in its section index are marked as online and may describe another release.

For source development, rebuild the bundled Skill after editing the Harness UI documentation or navigation:

```console
make a13n-harness-ui-skills
```

`make a13n-harness-ui` already depends on this step. Generated copies have one source of truth in `docs/a13n-harness-ui/`; do not edit the generated Skill tree independently. A wheel can be rebuilt from its sdist without the repository docs tree or Node.js.

## User and plugin file mounts

Selecting Skills normally exposes the exact `~/.agents/skills` directory as a fresh file-only user mount, creating that directory if absent. It is writable because it is user-owned, but grants no shell or access to the user's home directory as a whole. An exactly matching Host-path-preserving Project root can be reused instead of creating an ambiguous duplicate mount.

Installed Content Plugin directories are separately mounted so their content can be inspected or edited. Their availability does not imply every contributed Skill is selected or every child role is enrolled. Removing an installed directory is a real deletion; resuming an older capture does not recreate it.

## Install a Content Plugin

A plugin source must be a Git repository with a marketplace and a selected plugin manifest, not just a directory containing `SKILL.md`:

```console
a13n-harness-ui plugin install /path/to/plugin-repository
a13n-harness-ui plugin install https://github.com/example/agent-content --plugin plugin-review --ref v1.0.0
a13n-harness-ui plugin list --format json
```

Use an existing repository/ref and plugin ID. If the marketplace has exactly one valid plugin, `--plugin` can be omitted; otherwise select one. Installation records repository/commit provenance and publishes a complete local directory under `<data-root>/content-plugins/<plugin-id>`.

Existing installed IDs are not overwritten. There is no `plugin update` command. Installed files are editable ordinary content, not a continuously synchronized checkout. Inspect and trust the source before installation; validation is not a safety guarantee for its instructions.

## Remove a Content Plugin

**Uninstall immediately deletes its installed directory, including your local edits, without another confirmation.** Back up needed changes before running:

```console
a13n-harness-ui plugin uninstall plugin-review
```

Reinstalling copies repository content; it does not restore deleted local edits. Uninstall can remove a broken installation even when its manifest is invalid. A removed source may make an explicitly selected child unavailable for later composition.

## Use contributed subagents

Select a plugin's Markdown child on the Agent like any other Markdown role:

```yaml
subagents:
  - markdown: subagent-investigator
```

Installation alone does not enroll all contributed children. Local `subagents/*.md` overrides a plugin child with the same ID. Plugin-to-plugin collisions follow deterministic ordering and produce diagnostics. Package-owned built-in child IDs are reserved. Missing selected children fail composition; invalid optional content is not silently converted into another role.

See [Agents and subagents](agents-and-subagents.md) for Model inheritance, tool filtering, and roster rules.

## Author a Content Plugin repository

Create **`.agents/plugins/marketplace.yaml`** at the repository root:

```yaml
schema_version: "1"
name: Team content
plugins:
  - path: ./plugins/review
```

Create **`plugins/review/.a13n-plugin/plugin.yaml`**:

```yaml
schema_version: "1"
kind: content_plugin
id: plugin-review
name: Review workflows
version: "1.0.0"
description: Shared review Skills and child roles.
skills: ./skills
subagents: ./subagents
```

The two declared directories are relative to that plugin root. Put each Skill's `SKILL.md` under its own child directory of `skills/`; put canonical Markdown roles under `subagents/`.

### Marketplace fields

| Field            | Requirement                                                                       |
| ---------------- | --------------------------------------------------------------------------------- |
| `schema_version` | Required `"1"`                                                                    |
| `name`           | Required 1–256 characters                                                         |
| `plugins`        | Required 1–256 entries with unique paths                                          |
| `plugins[].path` | Required canonical repository-relative path to the plugin root, 3–4096 characters |

### Manifest fields

| Field            | Requirement / default                                                                                      |
| ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `schema_version` | Required `"1"`                                                                                             |
| `kind`           | Required `content_plugin`                                                                                  |
| `id`             | Required `plugin-` ID using lowercase ASCII letters/digits and single separating hyphens, 8–128 characters |
| `name`           | Required 1–256 characters                                                                                  |
| `version`        | Required non-empty version label, up to 128 characters                                                     |
| `description`    | Required 1–4096 characters                                                                                 |
| `skills`         | Optional relative directory, default absent                                                                |
| `subagents`      | Optional relative directory, default absent                                                                |

At least one of `skills` or `subagents` is required. Declared paths use `./...`, remain inside their owner, and cannot escape through traversal or links. Unknown fields are rejected. The version label is provenance, not an automatic update resolver.

Installation enforces size, file-type, Git-operation and YAML-parser limits rather than accepting arbitrary repository content. `.a13n-plugin/origin.json` records provenance separately from the editable manifest.

## Troubleshoot discovery

Use `config validate`, `config show`, `plugin list`, and `<data-root>/logs/terminal.log` to inspect diagnostics. Check the Agent's Capability selection, effective Environment paths, source precedence, manifest paths, and selected child IDs before reinstalling anything.

A successful Skill catalog preview does not pin the catalog of a later Run or capture Skill bytes. Exact embedding-API Skill references are validated separately; plain dollar text does not provide that guarantee.
