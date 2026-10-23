---
title: Discover and use Skills
sidebarTitle: Skills
description: Discover and materialize Skills from files or Environments, and make them available to a Run.
---

Add project procedures to an Agent with `SkillsCapability()`. Put Skills under `.agents/skills/` in the workspace. Each Run gives the model names, descriptions, and paths; the model reads the content through file tools when needed.

## Skill Package Layout

Create `.agents/skills/code-review/SKILL.md`:

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Follow the repository review workflow.
```

Keep supporting files such as `checklist.md` in the same directory. A configured source root and each immediate child directory can be a Skill package; discovery stops at that level. `name` identifies the Skill for conflict resolution and Run selection.

Treat Skill content as untrusted model context. The Host retains control of tools, credentials, file access, and package or plugin loading.

## Use Skills in a Harness Run

Add `SkillsCapability()` and file tools to the Agent, then supply the workspace source. This function accepts a [prepared Environment source](environments.md#supply-a-source) and a configured Model:

```python
from pydantic_ai.models import Model

from a13n_harness import AgentSpec, EnvironmentSource, HarnessBuilder, RunBindings
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration


async def review_workspace(source: EnvironmentSource, model: Model) -> str:
    agent = HarnessBuilder().build(
        AgentSpec(instructions="Read the code-review Skill before reviewing code."),
        output_type=str,
        model=model,
        capabilities=(
            SkillsCapability(),
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
        ),
    )
    result = await agent.run(
        "Use code-review to review the workspace changes.",
        environment=source,
        bindings=RunBindings.embedded(skill_selection=frozenset({"code-review"})),
    )
    return result.output_or_raise()
```

The default manager scans `/workspace/.agents/skills`; a missing directory gives an empty catalog. The example explicitly selects `code-review`, so create that Skill before running it.

Selection is exact and fresh for each root, resumed, or child Run:

- leave `RunBindings.skill_selection=None` to expose the complete conflict-resolved catalog;
- provide a non-empty set to expose only those names;
- provide an empty set to inject no Skill instructions or paths;
- an unknown name fails Run preparation with `skill_selection_unknown`.

Selection is not portable `HarnessState` and grants no source or file authority. If a relevant Environment route changes, the current Run fails with `skill_catalog_stale`; it does not silently rescan or retarget frozen instructions.

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

Pass `SkillsCapability(manager)` in the Agent's capabilities to use these sources. Constructing `SkillManager(...)` directly replaces the default sources. Set `SkillsPolicy(conflict="error")` to reject duplicate names instead of applying source precedence.

With `required=False`, each missing, unroutable, or unsupported root is skipped independently. By default, `skip_invalid=True` skips individual entries whose bounded catalog read or frontmatter validation fails and logs a warning. Valid siblings remain available, and a Run can continue even when all entries are skipped. Set `skip_invalid=False` for strict validation. Root access failures, malformed paths, catalog overflow, and mount-incarnation checks remain errors.

## Design

`FileSkillSource` discovers metadata under explicit roots. `SkillManager` materializes, validates, and resolves conflicts. `SkillsCapability` exposes the catalog to a Run. The standalone scan APIs also serve CLI and import workflows without building an Agent:

| Situation                          | API                                 | Result and consistency                                                              |
| ---------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------- |
| Caller controls the file namespace | `scan(files=...)`                   | `tuple[SkillCatalogItem, ...]`; caller keeps the namespace stable                   |
| Host uses an entered Environment   | `scan_environment(environment=...)` | `BoundSkillCatalog`; pins mount incarnations and checks freshness before path reuse |

Both APIs share parsing, limits, and conflict policy. Scans read only configured roots. Environment scans stay on mount-incarnation-pinned file scopes; a relevant route change fails with `skill_catalog_stale` instead of rescanning or retargeting.

## Scan a Direct FileOperator

For direct scanning, use canonical absolute roots in the FileOperator's namespace. A local operator rooted at the project uses `/.agents/skills`, not `/workspace/.agents/skills`:

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

Keep the operator's namespace stable until you finish using the returned paths. Use Environment-aware scanning when mounts can change.

`SkillManager.default()` uses `/workspace/.agents/skills` in Environment-backed Runs. For explicit mount roots or direct FileOperators, provide a manager with roots in that namespace.

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

`scan_environment()` pins configured routes while reading and returns exact paths. It checks that those routes remain current before returning.

A `BoundSkillCatalogItem` contains:

- `name`, `description`, `path`, and `source_id`;
- `directory`, the exact resolved Skill directory;
- `document`, the exact resolved `SKILL.md` path;
- `directory.mount_id` and `document.mount_id`, the opaque mount incarnation used during scanning;
- `observed_generation`, the provider generation held during scanning.

Call `catalog.require_current(environment)` before reusing catalog paths. A changed relevant mount, generation, or route fails with `skill_catalog_stale`; unrelated mounts do not invalidate it.

`EnvironmentPath` is valid only in the entered Environment. Import packages into Host-owned immutable storage for durable use.

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

Set `target_root` to one configured source root and materialize beneath it. The FileOperator or Environment supplies access permissions.

## Host Responsibilities

The Host manages source authorization, package validation, and durable storage.

To import a package:

1. Scan through `scan_environment()` and select an item.
2. Check the catalog remains current; validate content through the same Environment.
3. Copy to Host-owned immutable storage and validate the copy before publication.

## Read Concurrency and Limits

Built-in scans use up to eight read workers while preserving source precedence and name-sorted results.

`max_entries_per_root` defaults to 256; `SkillsPolicy.max_skills` defaults to 512 per source and final catalog. Oversized catalogs fail instead of truncating.
