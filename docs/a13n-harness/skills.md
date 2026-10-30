---
title: Discover and use Skills
sidebarTitle: Skills
description: Give an Agent a discoverable procedure, read it through file tools, and choose the Skills exposed to each Run.
---

A Skill packages instructions and supporting files for a recurring task. `SkillsCapability()` tells the model which Skills are available; file tools let it read the procedure it needs.

## Skill Package Layout

Place each procedure in `.agents/skills/<name>/SKILL.md`. For example, `.agents/skills/code-review/SKILL.md` contains:

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Report findings with file paths.
```

The frontmatter supplies the catalog entry. The body supplies the procedure after the model reads the file. Keep `checklist.md`, templates, and other supporting files beside it. Discovery checks each configured root and its immediate child directories; put deeper material inside a discovered package. The `name` field identifies the Skill for selection and duplicate resolution.

## Use Skills in a Harness Run

Use the Python environment from [Getting started](getting-started.md). Save the following as `skills_demo.py`. It creates the package above in a temporary workspace, selects `code-review`, and makes a real `view` tool call. The deterministic Model needs no provider credentials.

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL


@dataclass(frozen=True)
class PreparedSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


async def respond(messages, info):
    assert 'skill name="code-review"' in str(info.instructions)
    returns = [
        part
        for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        yield {0: DeltaToolCall(
            name="view",
            json_args='{"file_path":"/workspace/.agents/skills/code-review/SKILL.md"}',
        )}
    else:
        assert "Report findings with file paths" in str(returns[-1].content)
        yield "Skill loaded: report findings with file paths."


async def main():
    agent = HarnessBuilder().build(
        AgentSpec(instructions="Read the code-review Skill before reviewing code."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(
            SkillsCapability(),
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
        ),
    )
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        skill = root / ".agents/skills/code-review/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: code-review\n"
            "description: Review a code change for correctness and maintainability.\n"
            "---\n\n# Code review\n\nReport findings with file paths.\n",
            encoding="utf-8",
        )
        connector = DIRECT_LOCAL.execution_connector(
            {"root": {"path": str(root)}}, environment_id="env-skills"
        )
        result = await agent.run(
            "Read code-review and state its reporting rule.",
            environment=PreparedSource(connector),
            bindings=RunBindings.embedded(skill_selection=frozenset({"code-review"})),
        )
        print(result.output_or_raise())
        assert skill.is_file()


asyncio.run(main())
```

Run it from that environment:

```bash
uv run --no-sync python skills_demo.py
```

The output is `Skill loaded: report findings with file paths.` The first model request checks the catalog; the second checks the file's returned content. `SkillsCapability` supplies metadata, `DynamicEnvironmentCapability` supplies file tools, and `PreparedSource` connects the temporary directory. Run cleanup leaves the file intact; `TemporaryDirectory` removes it when the example exits.

For your application, use a [prepared source](environments.md#supply-a-source) for the actual workspace and replace `FunctionModel` with your configured Model. Create the package before starting the Run. The default manager scans `/workspace/.agents/skills`; a missing optional directory yields an empty catalog.

Choose exposure with `RunBindings.skill_selection`:

| Value                          | Model-facing catalog                             |
| ------------------------------ | ------------------------------------------------ |
| `None`                         | All discovered Skills after duplicate resolution |
| Non-empty `frozenset`          | Only the exact names supplied                    |
| Empty `frozenset()`            | No Skill instructions or paths                   |
| A name absent from the catalog | Preparation fails with `skill_selection_unknown` |

Supply the selection afresh for each root, resumed, or child Run. Selection is not stored in portable `HarnessState`. It narrows exposure after discovery; source validation and file permissions still apply. Skill text is untrusted context, and reading it grants no tools, credentials, or plugin-loading authority.

## Add Explicit Sources

To add an organization library, mount it in the Environment and append its root to the default manager:

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

Replace `SkillsCapability()` in the example with `SkillsCapability(manager)`. The Environment must route `/environment/shared/skills` to a readable directory. This source is required, so every declared root must be available. Later sources win duplicate names by default; use `SkillsPolicy(conflict="error")` through the manager's `policy` argument to reject duplicates.

Use `SkillManager(...)` directly when you want to replace the default source list. For example, a Host with explicit aggregate mount paths supplies those paths rather than relying on the `/workspace` compatibility route.

Choose failure behavior per source:

- `required=False` skips each missing, unroutable, or unsupported root independently.
- `skip_invalid=True`, the default, warns and skips entries with invalid bounded reads or frontmatter. Valid siblings remain available, even if the final catalog is empty.
- `skip_invalid=False` makes those entry errors fail the scan.

Root access failures, malformed paths, catalog overflow, and mount-incarnation errors always fail. These settings control discovery behavior, not access permissions.

## Design

The same scanning pipeline supports both Agents and Host interfaces:

1. `FileSkillSource` names the roots to inspect.
2. `SkillManager` runs materializers, reads metadata, validates entries, and resolves duplicates.
3. `SkillsCapability` selects the Run's catalog and publishes its descriptions and paths.

For a CLI catalog or an import screen, call the manager without building an Agent:

| Caller                                         | API                                 | Result                                    |
| ---------------------------------------------- | ----------------------------------- | ----------------------------------------- |
| Owns a stable file namespace                   | `scan(files=...)`                   | `tuple[SkillCatalogItem, ...]`            |
| Uses an entered Environment with routed mounts | `scan_environment(environment=...)` | `BoundSkillCatalog` with freshness checks |

Both APIs share parsing, limits, and conflict policy. Each reads only explicitly configured roots.

## Scan a Direct FileOperator

Use absolute paths in the supplied operator's namespace. For an operator rooted at your project, the Skill root is `/.agents/skills`. This helper takes an existing `FileOperator` and returns its catalog:

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

Keep that namespace stable while consuming the returned paths. The `/workspace` prefix belongs to Environment routing; direct scanning adds no such prefix. Choose the next API when the Host can change mounts.

## Scan an Entered Environment

Call this helper while the Host's `BoundEnvironment` is entered:

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

The scan holds the configured routes on their exact mount incarnations and checks them again before returning. Each item includes `name`, `description`, logical `path`, and `source_id`, plus resolved `directory` and `document` paths. Those paths record `mount_id`; `observed_generation` records the provider generation used by the scan.

Call `catalog.require_current(environment)` immediately before reusing its paths after other asynchronous work. A relevant mount, generation, or route change raises `skill_catalog_stale`; unrelated mount changes remain valid. In a Run, `SkillsCapability` performs these checks around model requests and tool calls. A stale catalog fails rather than silently sending the model to a new location.

Use resolved `EnvironmentPath` values only within the entered Environment. For durable packages, import copies into Host-owned storage.

## Materialize Managed Skills

A Host can populate a selected source before discovery. Implement `SkillMaterializer.materialize` to validate and publish your managed package. This is an adapter skeleton; replace the method body with your application's import logic:

```python
from a13n_harness.capabilities import SkillManager, SkillsCapability
from a13n_harness.environment import FileOperator


class ManagedSkillMaterializer:
    materializer_id = "managed-snapshot"
    target_root = "/workspace/.agents/skills"

    async def materialize(self, *, files: FileOperator) -> None:
        # Application code: validate the package manifest and digest,
        # then publish its files beneath target_root using files.
        ...


manager = SkillManager.default(materializers=(ManagedSkillMaterializer(),))
capability = SkillsCapability(manager)
```

Set `target_root` to an explicitly configured source root and keep writes beneath it. The supplied FileOperator or Environment determines actual write access. Pass this `capability` to the builder after implementing the method.

## Host Responsibilities

For an import workflow:

1. Scan with `scan_environment()` and select an item.
2. Check freshness, then validate its content through that same Environment.
3. Copy it into Host-owned immutable storage and validate the copy before publication.

The Host owns source authorization, package validation, revisions, and durable storage. A discovered path is sufficient to read within its active Environment; a validated stored copy is the artifact to retain for later use.

## Read Concurrency and Limits

Built-in scans use at most eight read workers. Source precedence and name-sorted output stay deterministic regardless of read completion order.

`FileSkillSource.max_entries_per_root` defaults to 256. `SkillsPolicy.max_skills` defaults to 512 for each source and the final catalog. Oversized catalogs fail rather than returning a truncated selection.
