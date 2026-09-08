# Environment Skill Sources

## Design Position

Harness UI exposes Harness file Skills as an Agent-selected Capability without creating a second managed Skill resource. A selected `skills` Capability discovers ordinary `SKILL.md` directories through the current Run's entered Environment. Project-local, user-local, and installed [Content Plugin](01b-content-plugin-repositories.md) Skill files are directly editable through their owning Environment mounts. Skills do not become Harness UI desired resources or SQLite rows.

The [Harness Skills contract](../a13n-harness/09-context-and-memory.md#skills) owns Skill document parsing, catalog freezing, selection, model-facing routing, and read observation. Harness UI owns only the Host source set, Environment routing, user Skill root exposure, and immutable source configuration captured for a Run.

## Boundaries

| Concern                                                                  | Owner                       | Harness UI relationship                                                                               |
| ------------------------------------------------------------------------ | --------------------------- | ----------------------------------------------------------------------------------------------------- |
| Skill format, catalog limits, conflicts, and run-frozen model projection | Harness `SkillsCapability`  | Supplies an explicit ordered `SkillManager` for each reconstructed Agent node                         |
| Project Skill files                                                      | Project root owner          | Exposes each captured root through its existing Environment mount                                     |
| User Skill files                                                         | User at `~/.agents/skills`  | Exposes that directory through an exact Project mount or a dedicated read-write Environment mount     |
| Content Plugin Skill files                                               | Installed plugin catalog    | Uses the declared Skill subdirectory of a read-write plugin-directory mount                           |
| Additional Skill roots                                                   | Agent resource              | Stores Environment-logical paths in `skills` Capability configuration                                 |
| Environment path layout and authorization                                | Harness UI Host and Harness | Uses Host-path-preserving or virtual roots, then resolves each source through the entered mount table |
| Direct Skill authoring                                                   | User or Project owner       | Remains ordinary file editing                                                                         |
| Content Plugin acquisition                                               | Harness UI CLI              | Installs Git-derived editable local bundles through explicit commands                                 |

A Skill is not a Harness UI configured resource. Harness UI surfaces can report the selected Capability configuration and bounded Run diagnostics. The CLI installs or uninstalls whole Content Plugins rather than individual Skills; users edit installed files directly, and an Agent with Environment file tools can edit them through the plugin's file mount.

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

The conceptual Harness UI configuration is:

```python
class SkillsConfiguration(BaseModel):
    roots: tuple[EnvironmentLogicalPath, ...] = ()
```

`roots` contains ordered unique canonical absolute paths in the Harness aggregate Environment namespace. These are additional required sources and not replacements for automatic discovery. Full Control, Sandbox, and any other Host-path-preserving adapter use the canonical Host filesystem path of a mounted Project or user Skill root. A virtual-layout adapter uses a route such as `/workspace/team-skills` or `/environment/workspace-2/product-skills`; that route is never a Provider-internal path. Roots cannot name `~`, a relative path, or a path outside the current Run's routes. Each path must resolve when the catalog is prepared, so switching between Host-preserving and virtual layouts can require a corresponding explicit-root configuration change.

Capability omission disables Skill discovery and omits any dedicated user Skill mount for that Agent Run. An empty `roots` list keeps automatic sources enabled.

## Run Source Set

For a Run whose root Agent selects `skills`, Harness UI constructs sources from the captured initial mount set in this precedence order, from highest to lowest:

1. additional explicit roots, with a later configured root winning over an earlier root;
2. the first Project root's `/.agents/skills` directory, addressed through mount alias `workspace`;
3. later Project roots' `/.agents/skills` directories in Project order;
4. installed Content Plugin Skill roots, with lexicographically later plugin IDs winning over earlier IDs;
5. the dedicated user Skill mount backed by `~/.agents/skills`.

A Thread without a Project omits both Project source tiers. User, Content Plugin, and explicit Skill sources remain selected under the same rules; the configuration file mount does not automatically contribute Project Skills.

Harness conflict policy is `prefer_later`; Harness UI supplies sources in the reverse order needed to realize that precedence. Plugin source IDs use `a13n-harness-ui:content-plugin:<plugin-id>` and do not depend on installation order or directory enumeration. Source IDs are deterministic from source kind and captured mount alias or explicit-list position. The same Skill `name` therefore resolves predictably while retained catalog items preserve their winning source ID and Environment path.

For Full Control, Sandbox, and other Host-path-preserving adapters, automatic Project roots preserve the captured canonical Host paths:

```text
<first-project-root>/.agents/skills
<second-project-root>/.agents/skills
<third-project-root>/.agents/skills
...
```

For virtual-layout adapters, they retain the compatibility routes:

```text
/workspace/.agents/skills
/environment/workspace-2/.agents/skills
/environment/workspace-3/.agents/skills
...
```

The deterministic source IDs continue to use `workspace`, `workspace-2`, and later mount aliases in either layout; changing presentation paths does not change Skill precedence or provenance identity. A missing automatic directory contributes no Skills. An unavailable, unroutable, or unreadable explicit root fails catalog preparation. Harness per-root and total catalog bounds apply independently of the number of mounted Project roots; exceeding a bound fails rather than truncating an ambiguous catalog.

Plugin files use the whole-directory mount defined by [Skill and File Access](01b-content-plugin-repositories.md#skill-and-file-access), including editable subagent files. Skill sources resolve the manifest-declared subdirectory beneath that mount. Mount availability does not depend on selecting `skills`; catalog construction does. Plugin Skill sources are optional and skip invalid individual entries with diagnostics without hiding valid siblings.

The catalog is prepared after initial Environment entry and frozen for the logical Harness Run. A mount added, replaced, or removed after preparation does not silently change it. Harness mount-incarnation checks reject a selected Skill whose route changes while it is being prepared or read. A later root or child Run reconstructs and rescans its own current source set.

## Interactive Skill References

`HarnessUiApp` exposes a bounded, credential-free Skill catalog projection for interactive completion. A draft or idle Thread preview uses its effective next-Run Agent, Project roots, and Environment selection and invokes the same ordered Environment-routed source composition used for Run preparation. Preview does not create a Thread, persist a catalog, mutate a source, or claim that its observed provider generation will remain current. An active root operation instead projects only the catalog already frozen for that Run.

The App accepts optional exact Skill references from embedding adapters and validates each against the applicable fresh or active catalog before admitting a prompt or steering action. A missing, ambiguous, stale, or newly unavailable item rejects that input. The full-terminal CLI currently sends ordinary prompt text; mentioning a Skill asks the Agent to use the native Skills capability, but dollar-prefixed text alone is not an exact catalog binding.

A validated reference is an explicit request to use the named Skill for the current input. It does not replace or narrow the run-frozen catalog, suppress implicit routing to another available Skill, capture Skill bytes, mutate Capability configuration, or grant Environment authority. The root Agent receives the exact request through the Skills Capability's ordinary model-facing routing and reads `SKILL.md` through the existing Environment file path only when applying that Skill. Raw dollar-prefixed text that has no resolved reference remains ordinary prompt text.

## Dedicated User Skill Mount

When the root Agent for an independent Run selects `skills`, Harness UI resolves `~/.agents/skills` and creates that exact directory when absent. It ordinarily binds the directory as the non-default `user-skills` mount. A Host-path-preserving adapter assigns that mount's canonical resolved Host path as its aggregate root, so `<resolved-user-skills-root>/<name>/SKILL.md` uses the same address inside and outside the Agent. A virtual-layout adapter retains `/environment/user-skills/<name>/SKILL.md` as the aggregate route.

If a Host-path-preserving Project root is exactly the resolved user Skill root, Harness UI reuses that Project mount and does not create an equal `user-skills` route. The deterministic `a13n-harness-ui:user-skills` source remains present and targets the same aggregate root, while the Project source keeps its own identity and targets that root's `.agents/skills` child. This exception avoids ambiguous equal routes without weakening Project authority deliberately selected by the user.

This Skill binding adds only the exact `~/.agents/skills` directory, not `~`, `~/.agents`, or sibling user files. Separate Host bindings expose installed content, the selected configuration directory, and Thread files under their owning contracts.

The dedicated mount uses the release-owned Direct Local Provider with file operations only. Its Provider permissions and Harness permission ceiling allow read and write file operations, as explicitly selected for this user-owned Skill directory, but no shell, process, port, output, or arbitrary Host-path operation. It is fresh and stateless for each independent Run, is not the default working mount, and does not participate in Project Environment-state publication.

The `user-skills` mount is ordinarily present for every selected Project Environment profile because it is a separate Host-owned local mount. Exact equality with a Host-path-preserving Project root is the only omission. Selecting Sandbox, a remote profile, or another isolated Project profile does not copy the user Skill directory into that Provider; access remains routed through the dedicated Direct Local file-only mount, and Project commands cannot execute through it.

## Composition and Child Behavior

The immutable Run composition captures:

- the exact `skills` Capability configuration for every resolved Agent node;
- the captured Project roots and their stable mount order;
- the installed Content Plugin IDs, directory paths, and declared Skill paths; and
- the selected Capability implementation provenance.

It does not capture Skill document bytes or a discovered catalog. Fresh Agent reconstruction derives one deterministic aggregate-path source set from the captured Project, plugin, and user roots plus the selected Environment profile, and Harness freezes the observed catalog after Environment entry. Plugin file edits remain on the installed local path and are observed when a later Run reconstructs and rescans that source.

A root Run injects the user Skill mount only when its root Agent selects `skills`. A delegated child receives a separately prepared Environment and applies the same rule to the child Run's root Agent. A nested roster entry selecting `skills` does not broaden the parent's Environment before that child is independently admitted.

## Failure Semantics

| Failure                                                                | Outcome                                                                   |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| User Skill directory cannot be created or opened                       | Run preparation fails before model dispatch                               |
| Explicit root is outside current Environment routing                   | Skill catalog preparation fails explicitly                                |
| Automatic `.agents/skills` directory is absent                         | That source contributes no catalog items                                  |
| Captured plugin directory is missing or an individual Skill is invalid | Missing optional sources and invalid entries are skipped with diagnostics |
| Skill root exceeds a Harness scan bound                                | Catalog preparation fails without truncation                              |
| Duplicate Skill name                                                   | The deterministic source precedence selects one winner                    |
| Selected mount incarnation changes during preparation or use           | Harness reports a stale Skill catalog                                     |
| Skill document changes after the catalog is frozen                     | The active Run retains its frozen catalog provenance; a later Run rescans |
| Interactive Skill reference is stale or unavailable                    | Input admission rejects it and preserves the user's complete draft        |

## Compatibility

`skills` is a release-owned Harness UI Capability key backed by the public Harness `SkillsCapability`. Adding a new automatic source or changing source precedence changes observable Skill resolution and therefore requires a Harness UI specification and compatibility review. Harness Skill format and catalog validation remain owned by the Harness release selected by Harness UI.

## Invariants

1. Skills are Environment-routed files, not Harness UI managed resources; whole Content Plugins are managed catalog bundles.
2. Every explicit Skill root must resolve inside the current Run Environment.
3. All captured Project mounts contribute their conventional `.agents/skills` directory.
4. Every Content Plugin Skill source is a subdirectory of its plugin's read-write file mount with deterministic provenance and ordering.
5. `~/.agents/skills` is exposed through an exact Host-path-preserving Project mount when one already owns that root, otherwise through a dedicated read-write file mount: by its canonical Host path for Full Control, Sandbox, and other Host-preserving adapters, or by `/environment/user-skills` for virtual-layout adapters, never through the ambient Host home.
6. Capability omission creates no Skill catalog or dedicated user Skill mount; plugin-directory file access remains available.
7. One logical Run observes one frozen, deterministically ordered Skill catalog.
8. Root and child Runs prepare independent Skill sources and mount incarnations.
9. An interactive Skill reference is an exact current-input request over the applicable catalog, not a source mutation, catalog filter, content attachment, or authority grant.
