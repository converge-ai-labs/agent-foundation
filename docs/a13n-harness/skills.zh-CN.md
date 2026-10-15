---
title: 发现和使用 Skill
sidebarTitle: Skill
description: 从文件或 Environment 中发现并准备 Skill，供 Run 使用。
---

用 `SkillsCapability()` 为 Agent 提供项目工作流程。将 Skill 放在工作区的 `.agents/skills/` 下；Run 会向模型提供名称、描述和路径，模型再通过文件工具按需读取内容。

## Skill 包结构

创建 `.agents/skills/code-review/SKILL.md`：

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Follow the repository review workflow.
```

将 `checklist.md` 等支持文件放在同一目录。配置的来源根目录本身及其直接子目录都可以是 Skill 包；扫描止于这一层。`name` 用于冲突处理和 Run 选择。

将 Skill 内容视为不可信的模型上下文。工具、凭据、文件访问及包或插件加载仍由 Host 控制。

## 在 Harness Run 中使用 Skill

为 Agent 添加 `SkillsCapability()` 和文件工具，再传入工作区 source。以下函数接收[已准备的 Environment source](environments.md#supply-a-source) 和已配置的 Model：

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

默认 manager 扫描 `/workspace/.agents/skills`；缺少该目录时，目录列表为空。上例明确选择 `code-review`，因此运行前需要创建这个 Skill。

每个根 Run、恢复 Run 或子 Run，都使用准确且重新提供的选择：

- `RunBindings.skill_selection=None` 暴露完整的冲突处理后目录；
- 非空集合只暴露指定名称；
- 空集合不注入任何 Skill 指令或路径；
- 未知名称使 Run 准备失败，错误码为 `skill_selection_unknown`。

选择不属于可移植 `HarnessState`，也不授予来源或文件权限。相关 Environment 路由变化时，当前 Run 会以 `skill_catalog_stale` 失败，不会静默重新扫描，也不会将冻结的指令改指向其他目标。

## 添加明确的来源

保留规范的 `/workspace/.agents/skills` 来源，追加 Host 根目录，遵循普通的后来源优先规则：

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

在 Agent 的 capabilities 中传入 `SkillsCapability(manager)` 即可使用这些来源。直接构建 `SkillManager(...)` 会替换默认来源。设置 `SkillsPolicy(conflict="error")` 可拒绝重复名称，而非按来源优先级选择。

`required=False` 会分别跳过每个缺失、无法路由或不支持的根目录。权限拒绝、路径或 frontmatter 格式错误、provider 失败和目录溢出，仍然报错。

## 设计

`FileSkillSource` 从明确配置的根目录发现元数据，`SkillManager` 负责准备包内容、验证和冲突处理，`SkillsCapability` 将目录接入 Run。独立的扫描 API 也可供 CLI 或导入流程使用，无需构建 Agent：

| 场景                          | API                                 | 结果与一致性                                                |
| ----------------------------- | ----------------------------------- | ----------------------------------------------------------- |
| 调用方管理文件命名空间        | `scan(files=...)`                   | `tuple[SkillCatalogItem, ...]`；调用方保持命名空间稳定      |
| Host 使用已进入的 Environment | `scan_environment(environment=...)` | `BoundSkillCatalog`；固定挂载实例，复用路径前检查是否仍有效 |

两个 API 共用解析、限制和冲突策略。扫描只读取配置的根目录；Environment 扫描始终使用固定到挂载实例的文件作用域。相关路由变化时以 `skill_catalog_stale` 失败，不会自动重扫或改换路径。

## 直接扫描 FileOperator

直接扫描使用 FileOperator 命名空间中的规范绝对根路径。以项目目录为根的本地 operator 使用 `/.agents/skills`，而非 `/workspace/.agents/skills`：

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

使用返回路径期间，保持 operator 命名空间稳定。挂载可能变化时使用 Environment 扫描。

`SkillManager.default()` 在 Environment Run 中使用 `/workspace/.agents/skills`。显式挂载根或直接 FileOperator 需要按自身命名空间提供 manager。

## 扫描已进入的 Environment

路径可能通过 `/workspace` 或 `/environment/{name}` 路由，且 Host 活跃期间挂载可能变化时，应使用 Environment 感知的扫描：

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

`scan_environment()` 读取时固定配置路由，返回准确路径；返回前检查路由仍为最新。

`BoundSkillCatalogItem` 包含：

- `name`、`description`、`path` 和 `source_id`；
- `directory`：准确解析后的 Skill 目录；
- `document`：准确解析后的 `SKILL.md` 路径；
- `directory.mount_id` 和 `document.mount_id`：扫描期间使用的不透明挂载实例标识；
- `observed_generation`：扫描期间持有的 provider generation。

复用目录路径前调用 `catalog.require_current(environment)`。相关挂载、generation 或路由变化时，以 `skill_catalog_stale` 失败；无关挂载不影响目录。

`EnvironmentPath` 仅在已进入的 Environment 中有效。持久使用需将包导入 Host 管理的不可变存储。

## 准备托管 Skill

可信 Host 适配器可以实现 `SkillMaterializer`，在扫描前为一个配置的来源根目录准备内容：

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

将 `target_root` 设为一个已配置的来源根目录，在其下物化。访问权限由 FileOperator 或 Environment 提供。

## Host 的职责

Host 管理来源授权、包验证和持久存储。

导入包时：

1. 通过 `scan_environment()` 扫描并选择条目。
2. 检查目录仍为最新，通过同一 Environment 验证内容。
3. 复制到 Host 管理的不可变存储，发布前验证副本。

## 读取并发与限制

内置扫描最多使用八个读取 worker，保留来源优先级与按名称排序的结果。

`max_entries_per_root` 默认 256，`SkillsPolicy.max_skills` 默认每个来源和最终目录最多 512。超限时失败，不截断。
