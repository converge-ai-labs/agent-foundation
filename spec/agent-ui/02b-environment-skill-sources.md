# Environment Skill Sources

## Design Position

Agent UI exposes Harness file Skills as an Agent-selected Capability without creating a second managed Skill resource. A selected `skills` Capability discovers ordinary `SKILL.md` directories through the current Run's entered Environment. Project-local and user-local Skill files remain directly editable files; they do not enter the Agent UI desired-resource tree, SQLite, or a package-management catalog.

The [Harness Skills contract](../agent-harness/09-context-and-memory.md#skills) owns Skill document parsing, catalog freezing, selection, model-facing routing, and read observation. Agent UI owns only the Host source set, Environment routing, the dedicated user Skill mount, and immutable source configuration captured for a Run.

## Boundaries

| Concern                                                                  | Owner                            | Agent UI relationship                                                         |
| ------------------------------------------------------------------------ | -------------------------------- | ----------------------------------------------------------------------------- |
| Skill format, catalog limits, conflicts, and run-frozen model projection | Harness `SkillsCapability`       | Supplies an explicit ordered `SkillManager` for each reconstructed Agent node |
| Project Skill files                                                      | Project root owner               | Exposes each captured root through its existing Environment mount             |
| User Skill files                                                         | User at `~/.agents/skills`       | Exposes only that directory as a dedicated read-write Environment mount       |
| Additional Skill roots                                                   | Agent resource                   | Stores Environment-logical paths in `skills` Capability configuration         |
| Environment path authorization                                           | Harness Environment              | Resolves every source through the current entered mount table                 |
| Skill authoring and package acquisition                                  | Direct files or an external tool | Not an Agent UI CLI, TUI, WebUI, database, or extension-package operation     |

A Skill is not an Agent UI configured resource. Agent UI surfaces can report the selected Capability configuration and bounded Run diagnostics, but they do not import, install, edit, delete, or upgrade Skills.

## Agent Configuration

The release-owned Capability catalog contains the `skills` key. An Agent selects it like any other Capability:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /workspace/team-skills
        - /environment/workspace-2/product-skills
```

The conceptual Agent UI configuration is:

```python
class SkillsConfiguration(BaseModel):
    roots: tuple[EnvironmentLogicalPath, ...] = ()
```

`roots` contains ordered unique canonical absolute paths in the Harness Environment namespace. These are additional required sources, not Host filesystem paths and not replacements for automatic discovery. They cannot name `~`, a relative path, a Provider-internal path, or an arbitrary local directory. Each path must resolve through one of the current Run's Environment routes when the catalog is prepared.

Capability omission disables Skill discovery and omits the user Skill mount for that Agent Run. An empty `roots` list keeps automatic sources enabled.

## Run Source Set

For a Run whose root Agent selects `skills`, Agent UI constructs sources from the captured initial mount set in this precedence order, from highest to lowest:

1. additional explicit roots, with a later configured root winning over an earlier root;
2. the first Project root's `/.agents/skills` directory, addressed through mount ID `workspace`;
3. later Project roots' `/.agents/skills` directories in Project order;
4. the dedicated user Skill mount backed by `~/.agents/skills`.

Harness conflict policy is `prefer_later`; Agent UI supplies sources in the reverse order needed to realize that precedence. Source IDs are deterministic from source kind and captured mount alias or explicit-list position. The same Skill `name` therefore resolves predictably while retained catalog items preserve their winning source ID and Environment path.

Automatic Project roots are:

```text
/workspace/.agents/skills
/environment/workspace-2/.agents/skills
/environment/workspace-3/.agents/skills
...
```

A missing automatic directory contributes no Skills. An unavailable, unroutable, or unreadable explicit root fails catalog preparation. Harness per-root and total catalog bounds apply independently of the number of mounted Project roots; exceeding a bound fails rather than truncating an ambiguous catalog.

The catalog is prepared after initial Environment entry and frozen for the logical Harness Run. A mount added, replaced, or removed after preparation does not silently change it. Harness mount-incarnation checks reject a selected Skill whose route changes while it is being prepared or read. A later root or child Run reconstructs and rescans its own current source set.

## Dedicated User Skill Mount

When the root Agent for an independent Run selects `skills`, Agent UI resolves `~/.agents/skills`, creates that exact directory when absent, and binds it as the non-default `user-skills` mount. The logical mount root is:

```text
/environment/user-skills
```

The mount exposes the contents of `~/.agents/skills` directly, so `/environment/user-skills/<name>/SKILL.md` addresses the classic user Skill directory. Agent UI does not mount `~`, `~/.agents`, or sibling user files.

The dedicated mount uses the release-owned Direct Local Provider with file operations only. Its Provider permissions and Harness permission ceiling allow read and write file operations, as explicitly selected for this user-owned Skill directory, but no shell, process, port, output, or arbitrary Host-path operation. It is fresh and stateless for each independent Run, is not the default working mount, and does not participate in Project Environment-state publication.

The `user-skills` mount is present for Native, Local EIP, and other selected Project Environment profiles because it is a separate Host-owned local mount. Selecting a remote or isolated Project profile therefore does not imply that the user Skill directory is copied into that Provider; access remains routed through the dedicated mount.

## Composition and Child Behavior

The immutable Run composition captures:

- the exact `skills` Capability configuration for every resolved Agent node;
- the captured Project roots and their stable mount order; and
- the selected Capability implementation provenance.

It does not capture Skill document bytes or a discovered catalog. Fresh native reconstruction derives the deterministic Environment-logical source set from that composition, and Harness freezes the observed catalog after Environment entry.

A root Run injects the user Skill mount only when its root Agent selects `skills`. A delegated child receives a separately prepared Environment and applies the same rule to the child Run's root Agent. A nested roster entry selecting `skills` does not broaden the parent's Environment before that child is independently admitted.

## Failure Semantics

| Failure                                                      | Outcome                                                                   |
| ------------------------------------------------------------ | ------------------------------------------------------------------------- |
| User Skill directory cannot be created or opened             | Run preparation fails before model dispatch                               |
| Explicit root is outside current Environment routing         | Skill catalog preparation fails explicitly                                |
| Automatic `.agents/skills` directory is absent               | That source contributes no catalog items                                  |
| Skill root exceeds a Harness scan bound                      | Catalog preparation fails without truncation                              |
| Duplicate Skill name                                         | The deterministic source precedence selects one winner                    |
| Selected mount incarnation changes during preparation or use | Harness reports a stale Skill catalog                                     |
| Skill document changes after the catalog is frozen           | The active Run retains its frozen catalog provenance; a later Run rescans |

## Compatibility

`skills` is a release-owned Agent UI Capability key backed by the public Harness `SkillsCapability`. Adding a new automatic source or changing source precedence changes observable Skill resolution and therefore requires an Agent UI specification and compatibility review. Harness Skill format and catalog validation remain owned by the Harness release selected by Agent UI.

## Invariants

1. Skills are Environment-routed files, not Agent UI managed resources.
2. Every explicit Skill root must resolve inside the current Run Environment.
3. All captured Project mounts contribute their conventional `.agents/skills` directory.
4. `~/.agents/skills` is exposed only through a dedicated read-write file mount, never through the ambient Host home.
5. Capability omission creates no Skill catalog and no user Skill mount.
6. One logical Run observes one frozen, deterministically ordered Skill catalog.
7. Root and child Runs prepare independent Skill sources and mount incarnations.
