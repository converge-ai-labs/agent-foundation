---
title: Discover and use Skills
sidebarTitle: Skills
description: Package reusable SKILL.md procedures, scan workspace and organization skill directories, and control per-run skill exposure in Harness.
---

A **Skill** packages reusable instructions and supporting assets in `.agents/skills/<name>/SKILL.md`. `SkillsCapability()` injects the catalog of available skill names and descriptions into model instructions, and `DynamicEnvironmentCapability` lets the Model read `SKILL.md` on demand via `view`.

## Add a Skill and run it

<span id="add-a-skill"></span>

Place YAML front matter (`name`, `description`) above the procedure body in `.agents/skills/<name>/SKILL.md`:

```markdown title=".agents/skills/code-review/SKILL.md"
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Report findings with file paths.
```

Attach `SkillsCapability()` and `DynamicEnvironmentCapability(...)` when building the Agent, and optionally restrict which discovered skills are exposed on a Run via `RunBindings.skill_selection`:

```python
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration

agent = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
    capabilities=(
        SkillsCapability(),
        DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
    ),
)

result = await agent.run(
    "Review the latest diff.",
    environment=workspace_source,  # see Environments: PreparedSource
    bindings=RunBindings.embedded(skill_selection=frozenset({"code-review"})),
)
```

| `RunBindings.skill_selection` | Catalog exposed to the Model                                 |
| ----------------------------- | ------------------------------------------------------------ |
| `None` (default)              | All discovered skills after duplicate resolution             |
| Non-empty `frozenset`         | Only the named skills (`skill_selection_unknown` if missing) |
| `frozenset()`                 | No skill instructions or paths                               |

## Add organization sources and materializers

Append shared skill roots (`FileSkillSource`) or pre-discovery `SkillMaterializer` hooks via `SkillManager`:

```python
from a13n_harness.capabilities import FileSkillSource, SkillManager, SkillsCapability

manager = SkillManager.default(
    additional_sources=(
        FileSkillSource("organization", ("/environment/shared/skills",), required=True),
    )
)
capability = SkillsCapability(manager)
```

Later sources win duplicate skill names by default (pass `SkillsPolicy(conflict="error")` to reject duplicates).

## Scan catalogs from Host code

- **Direct `FileOperator`:** `await manager.scan(files=files)` returns `tuple[SkillCatalogItem, ...]`.
- **Entered `BoundEnvironment`:** `catalog = await manager.scan_environment(environment=env)` returns a `BoundSkillCatalog`; call `catalog.require_current(env)` before reusing resolved paths after async work.
