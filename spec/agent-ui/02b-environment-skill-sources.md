# Environment Skill Sources

## Design Position

Agent UI exposes Harness file Skills as an Agent-selected Capability without creating a second managed Skill resource. A selected `skills` Capability discovers ordinary `SKILL.md` directories through the current Run's entered Environment. Project-local and user-local Skill files remain directly editable files; they do not enter the Agent UI desired-resource tree, SQLite, or a package-management catalog.

The [Harness Skills contract](../agent-harness/09-context-and-memory.md#skills) owns Skill document parsing, catalog freezing, selection, model-facing routing, and read observation. Agent UI owns only the Host source set, Environment routing, user Skill root exposure, and immutable source configuration captured for a Run.

## Boundaries

| Concern                                                                  | Owner                            | Agent UI relationship                                                                                           |
| ------------------------------------------------------------------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Skill format, catalog limits, conflicts, and run-frozen model projection | Harness `SkillsCapability`       | Supplies an explicit ordered `SkillManager` for each reconstructed Agent node                                   |
| Project Skill files                                                      | Project root owner               | Exposes each captured root through its existing Environment mount                                               |
| User Skill files                                                         | User at `~/.agents/skills`       | Exposes that directory through an exact Project mount or a dedicated read-write Environment mount               |
| Additional Skill roots                                                   | Agent resource                   | Stores Environment-logical paths in `skills` Capability configuration                                           |
| Environment path layout and authorization                                | Agent UI Host and Harness        | Uses Native direct roots or non-Native virtual roots, then resolves each source through the entered mount table |
| Skill authoring and package acquisition                                  | Direct files or an external tool | Not an Agent UI CLI, TUI, WebUI, database, or extension-package operation                                       |

A Skill is not an Agent UI configured resource. Agent UI surfaces can report the selected Capability configuration and bounded Run diagnostics, but they do not import, install, edit, delete, or upgrade Skills.

## Agent Configuration

The release-owned Capability catalog contains the `skills` key. An Agent selects it like any other Capability:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /work/agent-foundation/team-skills
        - /work/design-notes/product-skills
```

The conceptual Agent UI configuration is:

```python
class SkillsConfiguration(BaseModel):
    roots: tuple[EnvironmentLogicalPath, ...] = ()
```

`roots` contains ordered unique canonical absolute paths in the Harness aggregate Environment namespace. These are additional required sources and not replacements for automatic discovery. Under Native, an aggregate path is the canonical Host filesystem path of a mounted Project or user Skill root. Under a non-Native profile, it is a virtual route such as `/workspace/team-skills` or `/environment/workspace-2/product-skills`; it is never a Provider-internal path. Roots cannot name `~`, a relative path, or a path outside the current Run's routes. Each path must resolve when the catalog is prepared, so profile-specific explicit roots can require a corresponding Agent configuration change when switching between direct and virtual layouts.

Capability omission disables Skill discovery and omits any dedicated user Skill mount for that Agent Run. An empty `roots` list keeps automatic sources enabled.

## Run Source Set

For a Run whose root Agent selects `skills`, Agent UI constructs sources from the captured initial mount set in this precedence order, from highest to lowest:

1. additional explicit roots, with a later configured root winning over an earlier root;
2. the first Project root's `/.agents/skills` directory, addressed through mount alias `workspace`;
3. later Project roots' `/.agents/skills` directories in Project order;
4. the dedicated user Skill mount backed by `~/.agents/skills`.

Harness conflict policy is `prefer_later`; Agent UI supplies sources in the reverse order needed to realize that precedence. Source IDs are deterministic from source kind and captured mount alias or explicit-list position. The same Skill `name` therefore resolves predictably while retained catalog items preserve their winning source ID and Environment path.

For Native, automatic Project roots preserve the captured canonical Host paths:

```text
<first-project-root>/.agents/skills
<second-project-root>/.agents/skills
<third-project-root>/.agents/skills
...
```

For non-Native profiles, they retain the compatibility routes:

```text
/workspace/.agents/skills
/environment/workspace-2/.agents/skills
/environment/workspace-3/.agents/skills
...
```

The deterministic source IDs continue to use `workspace`, `workspace-2`, and later mount aliases in either layout; changing presentation paths does not change Skill precedence or provenance identity. A missing automatic directory contributes no Skills. An unavailable, unroutable, or unreadable explicit root fails catalog preparation. Harness per-root and total catalog bounds apply independently of the number of mounted Project roots; exceeding a bound fails rather than truncating an ambiguous catalog.

The catalog is prepared after initial Environment entry and frozen for the logical Harness Run. A mount added, replaced, or removed after preparation does not silently change it. Harness mount-incarnation checks reject a selected Skill whose route changes while it is being prepared or read. A later root or child Run reconstructs and rescans its own current source set.

## Dedicated User Skill Mount

When the root Agent for an independent Run selects `skills`, Agent UI resolves `~/.agents/skills` and creates that exact directory when absent. It ordinarily binds the directory as the non-default `user-skills` mount. Native assigns that mount's canonical resolved Host path as its aggregate root, so `<resolved-user-skills-root>/<name>/SKILL.md` uses the same address inside and outside the Agent. Non-Native profiles retain `/environment/user-skills/<name>/SKILL.md` as the virtual aggregate route.

If a Native Project root is exactly the resolved user Skill root, Agent UI reuses that Project mount and does not create an equal `user-skills` route. The deterministic `agent-ui:user-skills` source remains present and targets the same aggregate root, while the Project source keeps its own identity and targets that root's `.agents/skills` child. This exception avoids ambiguous equal routes without weakening Project authority deliberately selected by the user.

In either layout, Agent UI exposes no user path beyond the selected Project roots and the exact `~/.agents/skills` directory. It does not implicitly mount `~`, `~/.agents`, or sibling user files.

The dedicated mount uses the release-owned Direct Local Provider with file operations only. Its Provider permissions and Harness permission ceiling allow read and write file operations, as explicitly selected for this user-owned Skill directory, but no shell, process, port, output, or arbitrary Host-path operation. It is fresh and stateless for each independent Run, is not the default working mount, and does not participate in Project Environment-state publication.

The `user-skills` mount is present for Local EIP and other selected Project Environment profiles and is ordinarily present for Native because it is a separate Host-owned local mount. The exact Native Project-root equality above is the only omission. Selecting a remote or isolated Project profile therefore does not imply that the user Skill directory is copied into that Provider; access remains routed through the dedicated mount.

## Composition and Child Behavior

The immutable Run composition captures:

- the exact `skills` Capability configuration for every resolved Agent node;
- the captured Project roots and their stable mount order; and
- the selected Capability implementation provenance.

It does not capture Skill document bytes or a discovered catalog. Fresh Agent reconstruction derives one deterministic aggregate-path source set from the captured roots and selected Environment profile, and Harness freezes the observed catalog after Environment entry.

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
4. `~/.agents/skills` is exposed through an exact Native Project mount when one already owns that root, otherwise through a dedicated read-write file mount: by its canonical Host path under Native and by `/environment/user-skills` under non-Native profiles, never through the ambient Host home.
5. Capability omission creates no Skill catalog and no dedicated user Skill mount.
6. One logical Run observes one frozen, deterministically ordered Skill catalog.
7. Root and child Runs prepare independent Skill sources and mount incarnations.
