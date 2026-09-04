# Content Plugin Repositories

## Design Position

Agent UI installs declarative **Content Plugins** from Git repositories. A Content Plugin contributes ordinary Harness Skills and canonical Markdown subagents without importing Python, registering runtime hooks, or executing installation code. It is distinct from a [Harness Plugin](01a-extension-discovery-and-management.md), which is trusted Python runtime behavior selected through an Agent or Thread configuration.

Installation is an explicit user-scope CLI operation. The installed catalog lives under the selected Agent UI data root and remains available without network access. The configuration loader combines the catalog with the human-editable configuration tree; installation alone does not select the `skills` Capability or add a subagent to an Agent roster.

## Repository Format

A repository exposes one marketplace index at a fixed path:

```text
<repository>/
  .agents/
    plugins/
      marketplace.yaml
  plugins/
    <plugin>/
      .a13n-plugin/
        plugin.yaml
      skills/
        <skill>/
          SKILL.md
          references/
          assets/
          scripts/
      subagents/
        <subagent>.md
```

The marketplace index is:

```yaml
schema_version: "1"
name: example-marketplace
plugins:
  - path: ./plugins/reviewer
```

Each entry points to one plugin root in the same repository. The plugin manifest is:

```yaml
schema_version: "1"
kind: content_plugin
id: plugin-reviewer
name: Reviewer Toolkit
version: 1.0.0
description: Review skills and focused review subagents.
skills: ./skills
subagents: ./subagents
```

`skills` and `subagents` are independently optional, and at least one is present. A Skill source contains immediate child directories with `SKILL.md`; the Harness Skills contract owns each Skill document and its optional supporting files. A subagent source contains immediate non-README lower-case `.md` files using the canonical format in [Canonical Markdown Subagents](02-agent-composition-and-snapshots.md#canonical-markdown-subagents).

Marketplace paths and manifest content paths use canonical `./`-prefixed POSIX-relative syntax. They cannot be absolute, contain `~`, contain `.` or `..` traversal segments, escape their owning repository or plugin root, or resolve through a symlink. Selected trees contain only ordinary directories and regular files. Unknown fields, duplicate YAML keys, aliases, anchors, merge keys, custom tags, invalid UTF-8, unsupported schema versions, malformed manifests, special files, and exceeded file or byte bounds reject installation.

Version 1 supports only Skills and Markdown subagents. MCP servers, hooks, install scripts, Python modules, executables, and arbitrary runtime configuration are not plugin contribution types. Files under a Skill's `scripts/` directory remain inert Skill content: Agent UI never executes them during install, load, list, or uninstall.

## Installation and On-Disk State

The selected data root owns:

```text
<data-root>/
  content-plugins/
    installed/
      plugin-reviewer.json
    objects/
      <content-digest>/
        .a13n-plugin/plugin.yaml
        skills/...
        subagents/...
    staging/...
```

`a13n-ui plugin install <repository>` clones the repository into private staging, optionally checks out an explicit Git ref, resolves the exact commit, validates the complete marketplace and selected plugin, copies that plugin into a content-addressed object directory, and atomically creates its per-plugin registration file. The command accepts `--plugin <plugin-id>` to choose among repositories with multiple entries; omission is valid only when the repository contains exactly one plugin. Git credentials remain owned by the user's Git configuration and are not persisted by Agent UI.

One plugin ID has at most one installed registration. Installing an already registered ID fails rather than updating it. The installed record captures the repository locator, exact commit, manifest identity and version, content digest, and absolute object path. Object directories are immutable from the manager's perspective and never depend on the clone or network after publication.

`a13n-ui plugin list` reads and validates installed records and prints each plugin's ID, version, exact Git commit, and installation directory. The installation directory is the complete inspection surface; Agent UI does not provide a parallel command for browsing plugin files.

`a13n-ui plugin uninstall <plugin-id>` atomically removes the registration. It does not delete the content-addressed object. Retaining an unreferenced object ensures a Run that already captured that exact path does not change beneath execution. A later App generation excludes the unregistered plugin, and retained unreferenced objects are not rediscovered as installed content.

Install, list, and uninstall are the complete Content Plugin management surface. Agent UI defines no WebUI management, background updates, implicit upgrades, dependency solver, registry service, or automatic Git fetch.

## Configuration Integration

At load time Agent UI validates the installed records and their immutable object trees, then combines them with the selected configuration tree into one accepted generation. The generation digest covers installed plugin identity, exact commit, content digest, and contributed canonical Markdown. An invalid registered plugin rejects the candidate generation while preserving the prior accepted generation.

Installed Markdown subagents use their canonical frontmatter IDs. Immediate local `subagents/*.md` files override plugin subagents with the same ID. Two installed plugins contributing the same subagent ID reject the candidate generation even when a local file would otherwise override it; plugin-to-plugin ownership never depends on directory enumeration order. Agent resources still select every Markdown subagent explicitly by ID.

The accepted generation captures each installed plugin's exact immutable path and provenance. A resolved Run composition carries that catalog snapshot. Uninstalling a plugin therefore affects later accepted generations and later Run admission, but it cannot retarget or remove the files used by an already admitted Run.

## Skill Integration

When an Agent selects the `skills` Capability, every installed plugin with a Skill source contributes one read-only Environment mount. Plugin Skills participate in the source order defined by [Run Source Set](02b-environment-skill-sources.md#run-source-set). Capability omission creates no plugin mounts and no plugin Skill catalog.

Plugin source IDs are derived from plugin IDs. Plugins are ordered lexicographically by plugin ID; a lexicographically later plugin wins a duplicate Skill name among plugins under the Harness `prefer_later` policy. Project and explicit roots retain higher precedence, and the user Skill directory retains lower precedence. The selected catalog item preserves the winning plugin source ID and Environment path.

Full Control, Sandbox, and other Host-path-preserving adapters expose the immutable canonical plugin Skill directory at the same absolute path. Virtual-layout adapters expose one deterministic `/environment/content-plugin-<position>` route. Every plugin mount uses the Direct Local Provider with read-only file operations and grants no shell, process, port, output, or arbitrary Host-path authority.

## Failure Semantics

| Failure                                                                      | Outcome                                                                        |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Git clone, checkout, or commit resolution fails                              | Installation fails without creating a registration                             |
| Marketplace, manifest, path, tree, Skill root, or subagent source is invalid | Installation fails before publication                                          |
| Repository contains several plugins and no selector is supplied              | Installation fails and reports the selectable IDs                              |
| Selected plugin ID is absent or already installed                            | Installation fails without changing existing state                             |
| Object publication races with identical content                              | The existing identical object is reused                                        |
| Registration publication races                                               | Exactly one registration wins; the other install fails                         |
| Installed record or object is missing or invalid at App load                 | Candidate generation is rejected; the prior accepted generation remains active |
| Plugin subagent IDs conflict                                                 | Candidate generation is rejected                                               |
| Uninstall target is not registered                                           | Uninstall fails without changing state                                         |

## Invariants

1. A Content Plugin is declarative file content and never a Harness Plugin or executable extension.
2. Every installed registration identifies one exact Git commit and one content-addressed immutable object path.
3. Installation and loading never follow symlinks or allow a declared path to escape its owner root.
4. Installation alone grants no Capability selection, Agent roster membership, shell execution, credential, or network authority.
5. Local canonical subagents override plugin subagents; plugin-to-plugin subagent conflicts fail deterministically.
6. Project Skills override plugin Skills, and plugin Skills override user-global Skills.
7. Uninstall removes availability for later generations without invalidating paths already captured by admitted Runs.
8. Plugin management consists only of explicit CLI install, list, and uninstall operations.
