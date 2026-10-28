---
title: 发现和使用 Skill
sidebarTitle: Skill
description: 为 Agent 提供可发现的操作流程，通过文件工具读取，并选择每次 Run 可见的 Skill。
---

Skill 将重复任务所需的指令和支持文件放在一个包中。`SkillsCapability()` 告诉模型有哪些 Skill，文件工具让模型按需读取具体流程。

## Skill 包结构

将每个流程放在 `.agents/skills/<name>/SKILL.md`。例如，`.agents/skills/code-review/SKILL.md` 的内容如下：

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Report findings with file paths.
```

Frontmatter 提供目录条目，正文则在模型读取文件后提供具体步骤。将 `checklist.md`、模板等支持文件放在旁边。扫描会检查配置的根目录及其直接子目录；更深层的材料放在已发现的包内。`name` 字段用于选择 Skill 和处理同名冲突。

## 在 Harness Run 中使用 Skill

使用[入门](getting-started.md)中准备的 Python 环境，将以下程序保存为 `skills_demo.py`。程序在临时工作区创建上面的包，选择 `code-review`，再实际调用 `view` 工具。这里使用确定性的 Model，无需提供方凭据。

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

在该环境中运行：

```bash
uv run --no-sync python skills_demo.py
```

输出为 `Skill loaded: report findings with file paths.` 第一次模型请求检查目录，第二次检查文件工具返回的正文。`SkillsCapability` 提供元数据，`DynamicEnvironmentCapability` 提供文件工具，`PreparedSource` 接入临时目录。Run 清理后文件仍在；示例退出时由 `TemporaryDirectory` 删除。

接入实际应用时，为工作区提供[已准备的 source](environments.md#supply-a-source)，并将 `FunctionModel` 替换为已配置的 Model。先创建包，再启动 Run。默认 manager 扫描 `/workspace/.agents/skills`；这个可选目录不存在时，得到空目录列表。

通过 `RunBindings.skill_selection` 选择模型可见的内容：

| 值                 | 模型可见的目录                               |
| ------------------ | -------------------------------------------- |
| `None`             | 处理同名冲突后的全部 Skill                   |
| 非空 `frozenset`   | 只包含集合中的准确名称                       |
| 空 `frozenset()`   | 不注入 Skill 指令或路径                      |
| 目录中不存在的名称 | 准备失败，错误码为 `skill_selection_unknown` |

每个根 Run、恢复 Run 和子 Run 都要重新提供选择；可移植 `HarnessState` 不保存它。选择在发现之后缩小可见范围，来源验证和文件权限仍然生效。Skill 文本是不可信上下文，读取它不会授予工具、凭据或插件加载权限。

## 添加明确的来源

要增加组织共享库，先将其挂载到 Environment，再把根目录追加到默认 manager：

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

将示例中的 `SkillsCapability()` 替换为 `SkillsCapability(manager)`。Environment 必须能将 `/environment/shared/skills` 路由到可读目录。这个来源为必需项，声明的每个根目录都必须可用。默认由后面的来源覆盖同名 Skill；要拒绝同名冲突，可通过 manager 的 `policy` 参数传入 `SkillsPolicy(conflict="error")`。

需要替换默认来源列表时，直接构建 `SkillManager(...)`。例如，Host 使用显式的聚合挂载路径时，应提供这些路径，而不是依赖 `/workspace` 兼容路由。

按来源选择失败处理方式：

- `required=False` 分别跳过缺失、无法路由或不支持的根目录。
- 默认的 `skip_invalid=True` 对有界读取或 frontmatter 验证失败的条目发出警告并跳过。其他有效条目仍然可用，最终目录也可以为空。
- `skip_invalid=False` 使这类条目错误导致扫描失败。

根目录访问失败、路径格式错误、目录超限和挂载实例错误始终会报错。这些设置控制发现行为，不改变访问权限。

## 设计

Agent 和 Host 界面共用同一套扫描流程：

1. `FileSkillSource` 指定要检查的根目录。
2. `SkillManager` 执行内容准备、读取元数据、验证条目并处理同名冲突。
3. `SkillsCapability` 选择当前 Run 的目录，发布描述和路径。

为 CLI 列出目录或实现导入界面时，可以直接调用 manager，无需构建 Agent：

| 调用方                                 | API                                 | 返回结果                           |
| -------------------------------------- | ----------------------------------- | ---------------------------------- |
| 管理稳定的文件命名空间                 | `scan(files=...)`                   | `tuple[SkillCatalogItem, ...]`     |
| 使用已进入、支持挂载路由的 Environment | `scan_environment(environment=...)` | 带有效性检查的 `BoundSkillCatalog` |

两种 API 共用解析、限制和冲突策略，都只读取明确配置的根目录。

## 直接扫描 FileOperator

使用传入 operator 命名空间中的绝对路径。以项目目录为根的 operator，其 Skill 根路径为 `/.agents/skills`。下面的函数接收已有 `FileOperator`，返回扫描目录：

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

使用返回路径期间，保持该命名空间稳定。`/workspace` 前缀属于 Environment 路由，直接扫描不会添加这个前缀。Host 可能更换挂载时，使用下一节的 API。

## 扫描已进入的 Environment

在 Host 的 `BoundEnvironment` 已进入期间调用以下函数：

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

扫描期间，配置的路由固定到各自的挂载实例，并在返回前再次检查。每个条目包含 `name`、`description`、逻辑 `path`、`source_id`，以及解析后的 `directory` 和 `document` 路径。路径记录 `mount_id`，`observed_generation` 记录扫描使用的 provider generation。

经过其他异步操作后，复用路径前立即调用 `catalog.require_current(environment)`。相关挂载、generation 或路由发生变化时，抛出 `skill_catalog_stale`；无关挂载变化不影响目录。在 Run 中，`SkillsCapability` 会在模型请求和工具调用前后执行这些检查。目录失效会导致失败，不会悄悄将模型指向新的位置。

解析后的 `EnvironmentPath` 只在已进入的 Environment 内使用。需要长期保存包时，将副本导入 Host 管理的存储。

## 准备托管 Skill

Host 可以在发现前为选定来源准备内容。实现 `SkillMaterializer.materialize`，验证并发布托管包。下面是适配器骨架，请用应用自己的导入逻辑替换方法体：

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

将 `target_root` 设为一个明确配置的来源根目录，并将写入限制在该目录下。实际写入权限由传入的 FileOperator 或 Environment 决定。实现方法后，将这个 `capability` 传给 builder。

## Host 的职责

导入包的流程如下：

1. 用 `scan_environment()` 扫描并选择条目。
2. 检查目录仍然有效，然后通过同一 Environment 验证内容。
3. 复制到 Host 管理的不可变存储，发布前验证副本。

Host 负责来源授权、包验证、版本和持久存储。发现的路径可用于在当前 Environment 中读取；经过验证并保存的副本才是供以后使用的产物。

## 读取并发与限制

内置扫描最多使用八个读取 worker。无论读取按什么顺序完成，来源优先级和按名称排序的输出都保持确定。

`FileSkillSource.max_entries_per_root` 默认为 256。`SkillsPolicy.max_skills` 默认限制每个来源及最终目录最多 512 个 Skill。超限会失败，不会返回截断的选择结果。
