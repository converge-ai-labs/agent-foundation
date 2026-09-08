# Content Plugin Repositories

## Design Position

Harness UI installs declarative **Content Plugins** from Git repositories. A Content Plugin contributes ordinary Harness Skills and canonical Markdown subagents without importing Python, registering runtime hooks, or executing installation code. It is distinct from a [Harness Plugin](01a-extension-discovery-and-management.md), which is trusted Python runtime behavior selected through an Agent or Thread configuration.

Installation is an explicit user-scope CLI operation. The installed catalog lives under the selected Harness UI data root, remains available without network access, and exposes its copied Skill and Markdown files for direct local editing. The configuration loader combines the catalog with the human-editable configuration tree; installation alone does not select the `skills` Capability or add a subagent to an Agent roster.

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

`skills` and `subagents` are independently optional; the manifest declares at least one. Empty or temporarily missing source directories are allowed. Skill discovery uses the Harness format. Subagents are immediate non-README `.md` files, with case-insensitive file extensions, using [Canonical Markdown Subagents](02-agent-composition-and-snapshots.md#canonical-markdown-subagents). Supporting files are ordinary content and do not need registration.

Marketplace and manifest paths are `./`-prefixed relative paths within their owner. Absolute paths, traversal, and symlink escapes are not allowed. Installation copies ordinary files and directories under bounded file-count and byte limits; it never runs plugin code or follows a copied symlink. Manifest parsing rejects ambiguous YAML and unsupported schema versions. A bad marketplace entry is diagnosed without hiding other valid entries.

## Installation and On-Disk State

Each installation is one ordinary directory named by its stable plugin ID under `<data-root>/content-plugins/`. There is no separate installed registry, content-addressed payload store, or retained-object lifecycle. For example, `plugin-reviewer` owns `<data-root>/content-plugins/plugin-reviewer/` including its manifest, `skills/`, `subagents/`, and supporting files.

The manifest is the only authority for current name, version, description, and source paths. Its ID matches the directory name. Optional `.a13n-plugin/origin.json` records only `repository` and the original Git `commit`; missing or invalid origin metadata does not disable editable content. No current-tree or installation-tree content hash participates in identity, loading, or deduplication.

`a13n-harness-ui plugin install <repository>` clones into temporary staging under the plugin root, resolves an optional Git ref to a commit, selects a valid manifest using `--plugin <plugin-id>` when needed, and copies the plugin into its final ID directory. The directory is published only after copying is complete. Existing installations are not overwritten. Staging is removed on completion or failure. Installation validates copy boundaries and manifest structure, not the correctness of every editable Markdown document. Invalid content remains available for repair.

`a13n-harness-ui plugin list` discovers plugin-ID directories, reads their current manifests and available source paths, and reports the directory plus current metadata. Invalid plugins are diagnosed individually. Listing never reads all supporting asset bytes or hashes a directory tree.

`a13n-harness-ui plugin uninstall <plugin-id>` deletes that plugin's directory, including local edits. It works even if the manifest is invalid or missing, does not follow a directory symlink to its target, and leaves unrelated directories alone. No payload is retained for admitted Runs. Reinstallation copies fresh repository content; it does not resurrect previous edits.

Installation and management never execute Skill scripts, Python modules, hooks, or arbitrary configuration. Git authentication remains owned by the user's Git installation. There are no implicit updates, dependency solver, registry service, or background Git fetches.

## Configuration Integration

Content Plugins are optional local content sources, not an all-or-nothing configuration transaction. Discovery reads each valid manifest and collects available sources. A malformed manifest skips that plugin; a missing declared directory skips that source; malformed or unreadable subagent Markdown skips only that file. Other plugins, valid sibling files, and unrelated YAML configuration remain available. The App exposes nonfatal diagnostics separately from rejected-configuration errors. Repairs are observed on subsequent configuration reloads.

Configuration captures current plugin metadata, paths, and parsed Markdown, and observes file metadata for changes without hashing supporting assets. Existing bounded stable-read retries avoid accepting mixed configuration reads. A failure in the primary YAML or local configuration tree retains its existing rejection semantics; optional plugin errors do not invalidate that tree.

Subagents use the canonical Markdown format and parser. Plugin IDs and file paths are sorted; a later plugin ID, then a later filename within one plugin, wins a duplicate subagent ID with a diagnostic. Local `subagents/*.md` overrides plugin contributions. Agent resources still explicitly select Markdown subagents by ID. Missing Markdown references and resolved roster-name conflicts fail only when resolving an Agent that uses them, not unrelated Agent configurations.

A Run composition captures normalized subagent instructions and current plugin paths, not an immutable payload snapshot. Editing a subagent affects later Run composition. Skill and other file reads use live files. Removing or editing an installed directory can make a frozen Skill catalog stale or its files unavailable; Run admission does not pin or retain disk content.

## Skill and File Access

Every installed plugin with a valid manifest contributes its entire plugin directory as a read-write, file-only Environment mount. This includes subagent-only plugins and does not require selecting the `skills` Capability. The Agent still needs ordinary Environment file tools to edit content. Installing files does not select a subagent roster, enable shell tools, or execute anything.

Host-path-preserving adapters retain the plugin directory's absolute path. Virtual adapters use `/environment/content-plugin-<position>` in ascending plugin-ID order. Skill sources use the manifest-declared path *beneath* the same mount; for example `/environment/content-plugin-1/skills`. File writes cover Skills, subagents, manifest metadata, and supporting files within that plugin, but grant no shell, process, port, output, or sibling-directory authority.

Only a selected `skills` Capability constructs a Skill catalog. Plugin sources are optional and opt into skipping invalid individual Skill entries with diagnostics. Valid entries retain the [Skill source precedence](02b-environment-skill-sources.md#run-source-set): explicit and Project roots override plugins, lexicographically later plugin IDs override earlier ones, and plugins override user-global Skills. The Harness owns the catalog format and frozen-catalog behavior.

If a captured directory was uninstalled before preparation, it is not recreated or mounted. An absent optional Skill source contributes no catalog entries. Files already removed during a Run are reported through ordinary Environment file failures.

## Compatibility

Discovery recognizes plugin-ID directories only. Legacy `installed/` registrations and `objects/` payloads are not runtime sources and are not automatically deleted or migrated; users preserve edits by relocating the desired payload into its plugin-ID directory, or install a fresh copy. Legacy layouts produce a diagnostic rather than silently appearing installed. Origin metadata is optional after relocation. Existing frozen compositions containing the removed plugin digest field require a fresh Run composition rather than being treated as immutable plugin snapshots.

## Invariants

1. A plugin is editable file content, not a runtime extension or immutable artifact.
2. Directory identity is the plugin ID; the manifest owns current metadata and Git provenance is optional.
3. Installation is staged, performs no plugin execution, and respects copy boundaries.
4. Uninstall deletes the requested directory and local edits, never a symlink target or sibling directory.
5. Invalid optional content is diagnosed at the narrowest usable source boundary.
6. All valid plugin directories are writable through file-only mounts, including subagent-only plugins.
7. Skill discovery and subagent roster selection remain separate from installation and file access.
