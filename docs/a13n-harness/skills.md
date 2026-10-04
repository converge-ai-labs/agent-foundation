---
title: Discover and use Skills
sidebarTitle: Skills
description: Discover and materialize Skills from files or Environments, and make them available to a Run.
---

`a13n-harness` keeps Skill discovery reusable outside Agent execution. A Host chooses one of two explicit modes:

| Host situation                                                 | API                                              | Result                         | Consistency owner                                                                                                          |
| -------------------------------------------------------------- | ------------------------------------------------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| A CLI or embedded process directly controls one `FileOperator` | `SkillManager.scan(files=...)`                   | `tuple[SkillCatalogItem, ...]` | The caller keeps the operator's namespace stable                                                                           |
| A Host uses an entered `BoundEnvironment`                      | `SkillManager.scan_environment(environment=...)` | `BoundSkillCatalog`            | The manager pins mount incarnations; the Host checks that the catalog is still current before it uses a catalog path later |

Both modes use the same `SkillSource`, `SkillMaterializer`, frontmatter parser, limits, conflict policy, and path-containment checks. Neither mode scans a home directory, installed package, or sibling directory implicitly.

## Design

```mermaid
flowchart TB
    Host[Host configuration] --> Sources[FileSkillSource values]
    Host --> Materializers[Trusted SkillMaterializer values]
    Sources --> Manager[SkillManager]
    Materializers --> Manager

    Direct[Caller-controlled FileOperator] --> DirectScan[scan files]
    DirectScan --> Manager
    Manager --> Catalog[SkillCatalogItem catalog]

    Environment[Entered Environment] --> BoundScan[scan_environment]
    BoundScan --> Scopes[Mount-incarnation-pinned file scopes]
    Scopes --> Manager
    Manager --> BoundCatalog[BoundSkillCatalog]

    BoundCatalog --> HostImport[Host preview or import]
    BoundCatalog --> Capability[SkillsCapability]
    Capability --> Instructions[Model instructions]
    Capability --> Paths[Resolved SkillPath values]
    Capability --> Events[Catalog and access events]

    class Host,Direct app
    class Manager,Capability a13n
    class Catalog,BoundCatalog store
```

The design separates four responsibilities:

- `FileSkillSource` discovers bounded metadata below explicitly configured FileOperator roots.
- `SkillMaterializer` is trusted Host code that publishes managed packages beneath one configured root.
- `SkillManager` performs materialization, discovery, validation, conflict resolution, and optional Environment mount binding without depending on an Agent Run.
- `SkillsCapability` adapts one bound catalog into Run selection, model instructions, resolved `SkillPath` values, access observations, and model/tool-boundary stale checks.

The two APIs have exact, non-overlapping behavior:

- `scan(files=...)` uses exactly the supplied FileOperator and never constructs or probes an Environment;
- `scan_environment(environment=...)` uses only mount-incarnation-pinned scopes and never retries through the unpinned `environment.files` facade or direct scan mode;
- a source reads only its configured roots and never tries the process working directory, home directory, package locations, or alternate directories;
- a relevant route change raises `skill_catalog_stale`; it never triggers an automatic rescan or retarget;
- `FileSkillSource`, `SkillSource.roots`, and `SkillManager.roots` are the complete source/root surface.

`required=False` is explicit source policy rather than fallback discovery. It can omit that exact missing, unroutable, or unsupported root, but it never substitutes another path.

## Read Concurrency and Limits

Built-in file discovery and final document validation use at most eight read workers per operation. Materializers, sources, and roots remain sequential; parallel reads do not change source precedence, skipped-entry diagnostic order, or the name-sorted model catalog. With unchanged source configuration and content, read completion order does not change the Skill instruction prefix. Cancellation joins the workers before releasing their file scopes.

Size limits are independent of concurrency: `FileSkillSource.max_entries_per_root` defaults to 256 listed directory entries, and `SkillsPolicy.max_skills` defaults to 512 entries per source and in the final resolved catalog. Oversized catalogs fail explicitly rather than silently selecting the first entries. Concurrency is internal; it needs no Host configuration or cross-Run cache.

## Skill Package Layout

A configured root can itself be a Skill package, and each immediate child directory can be one Skill package. Discovery does not recurse beyond that level.

```text
.agents/skills/
├── SKILL.md
├── code-review/
│   ├── SKILL.md
│   └── checklist.md
└── release/
    └── SKILL.md
```

Each `SKILL.md` starts with bounded YAML frontmatter:

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Follow the repository review workflow.
```

`name` is the conflict and Run-selection identity. Skill content is untrusted model context; it grants no tools, credentials, filesystem access, plugin loading, or package authority.

## Scan a Direct FileOperator

Use direct scanning when the Host already owns a non-virtual FileOperator and controls its lifetime and retargeting. Roots are canonical absolute paths in that operator's namespace: repeated separators, traversal segments, and a trailing slash other than `/` are rejected. For example, a root-confined local operator whose `/` is the project directory uses `/.agents/skills`, not `/workspace/.agents/skills`:

```python
from a13n_harness.capabilities import (
    FileSkillSource,
    SkillCatalogItem,
    SkillManager,
)
from a13n_harness.environment import FileOperator


async def scan_cli_skills(
    files: FileOperator,
) -> tuple[SkillCatalogItem, ...]:
    manager = SkillManager(
        (
            FileSkillSource(
                "project",
                ("/.agents/skills",),
                required=False,
            ),
        )
    )
    return await manager.scan(files=files)
```

This mode operates directly in the supplied FileOperator namespace and has no Environment mount-routing semantics. Keep that backing namespace stable until every path derived from the returned catalog has been consumed. If another process can replace the backing directory concurrently, provide an operator with the snapshot or locking behavior your Host requires, or use an entered Environment instead.

`SkillManager.default()` is designed for Environment-backed Runs and contains the canonical `/workspace/.agents/skills` source. That path resolves only when the default mount has no explicit `mount_path`; a Host with explicit aggregate roots supplies an explicit manager. A direct FileOperator Host normally constructs an explicit manager with roots in its own namespace.

## Scan an Entered Environment

Use Environment-aware scanning when paths can route through `/workspace` or `/environment/{name}` and mounts can change while the Host is active:

```python
from a13n_harness.capabilities import (
    BoundSkillCatalog,
    SkillManager,
)
from a13n_harness.environment.advanced import BoundEnvironment


async def scan_environment_skills(
    environment: BoundEnvironment,
) -> BoundSkillCatalog:
    manager = SkillManager.default()
    catalog = await manager.scan_environment(environment=environment)

    # Call again immediately before consuming catalog paths after any await.
    catalog.require_current(environment)
    return catalog
```

`scan_environment()`:

1. captures every configured root with `BoundEnvironment.select_files()` before awaiting provider I/O;
2. opens mount-incarnation-pinned file scopes for those roots;
3. runs materialization, listing, frontmatter reads, and final `SKILL.md` validation through the pinned scopes;
4. resolves every final item to exact directory and document `EnvironmentPath` values;
5. verifies that every configured scan route, including empty and conflict-overridden roots, is still current before returning.

A `BoundSkillCatalogItem` contains:

- `name`, `description`, `path`, and `source_id`;
- `directory`, the exact resolved Skill directory;
- `document`, the exact resolved `SKILL.md` path;
- `mount_id`, the opaque Harness mount incarnation held during scanning;
- `observed_generation`, the provider generation held during scanning.

`BoundSkillCatalog.require_current(environment)` reselects only paths represented by catalog items. Adding or replacing an unrelated Environment mount does not invalidate the catalog. Changing a relevant mount selection, opaque mount ID, provider generation, default route, or resolved provider path raises `DefinitionError` with code `skill_catalog_stale`.

Do not persist `EnvironmentPath` values as durable authority. They describe one entered Environment and are useful only while that Environment remains active. A Host that imports Skill packages should copy and validate package content into its own immutable revision format.

## Add Explicit Sources

Retain the canonical `/workspace/.agents/skills` source and append Host roots with normal later-source precedence:

```python
from a13n_harness.capabilities import (
    FileSkillSource,
    SkillManager,
)

manager = SkillManager.default(
    additional_sources=(
        FileSkillSource(
            "organization",
            ("/environment/shared/skills",),
            required=True,
        ),
    )
)
```

Pass an explicit `SkillManager(...)` when the Host wants to replace the default composition completely. Source order is deterministic. Configure `SkillsPolicy(conflict="error")` when duplicate final names must fail rather than use precedence.

With `required=False`, each missing, unroutable, or unsupported root is skipped independently. Permission denial, malformed paths or frontmatter, provider failures, and catalog overflow remain errors.

## Materialize Managed Skills

A trusted Host adapter can implement `SkillMaterializer` to populate one configured source root before scanning:

```python
from a13n_harness.environment import FileOperator


class ManagedSkillMaterializer:
    materializer_id = "managed-snapshot"
    target_root = "/workspace/.agents/skills"

    async def materialize(self, *, files: FileOperator) -> None:
        # Verify the Host-owned package manifest and digest first.
        # Then publish only beneath target_root through `files`.
        ...
```

`target_root` must equal one configured source root. It is a composition and provenance contract, not a sandbox wrapper. The materializer is trusted Host code and must stay beneath that root; the supplied FileOperator or Environment remains the actual authority boundary.

## Use Skills in a Harness Run

Pass the manager to the definition-selected `SkillsCapability`. The Capability uses Environment-aware scanning, publishes exact `SkillPath` values, injects bounded routing instructions, observes ordinary `SKILL.md` reads, and fences the selected catalog at model and tool boundaries.

```python
from a13n_harness import RunBindings
from a13n_harness.capabilities import (
    SkillsCapability,
)

skills = SkillsCapability(manager)

bindings = RunBindings.embedded(
    environment=environment_binding,
    skill_selection=frozenset({"code-review", "release"}),
)
```

Selection is exact and fresh for each root, resumed, or child Run:

- leave `RunBindings.skill_selection=None` to expose the complete conflict-resolved catalog;
- provide a non-empty set to expose only those names;
- provide an empty set to inject no Skill instructions or paths;
- an unknown name fails Run preparation with `skill_selection_unknown`.

Selection is not portable `HarnessState` and grants no source or file authority. If a relevant Environment route changes, the current Run fails with `skill_catalog_stale`; it does not silently rescan or retarget frozen instructions.

## Host Responsibilities

The Harness scanner deliberately does not own:

- source CRUD or enablement;
- native path authorization;
- package manifests, digests, signatures, or immutable revisions;
- symlink and traversal policy for imported package copies;
- persistence or refresh history;
- durable execution or retry;
- credentials, plugins, Capabilities, or tools.

An importing Host should:

1. Scan through `scan_environment()`.
2. Select one exact item.
3. Verify that the catalog remains current.
4. Enumerate and validate the package through the same entered Environment.
5. Copy the package into Host-owned immutable storage.
6. Validate the copied package again before publication.
