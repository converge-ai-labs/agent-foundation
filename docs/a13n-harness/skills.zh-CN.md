---
title: 发现和使用 Skill
sidebarTitle: Skill
description: 从文件或 Environment 中发现并准备 Skill，供 Run 使用。
---

通过 FileOperator 或已进入的 Environment 扫描 Skills：

| Host 场景                                   | API                                              | 结果                           | 谁负责一致性                                                        |
| ------------------------------------------- | ------------------------------------------------ | ------------------------------ | ------------------------------------------------------------------- |
| CLI 或嵌入式进程直接管理一个 `FileOperator` | `SkillManager.scan(files=...)`                   | `tuple[SkillCatalogItem, ...]` | 调用方保持 operator 命名空间稳定                                    |
| Host 使用已进入的 `BoundEnvironment`        | `SkillManager.scan_environment(environment=...)` | `BoundSkillCatalog`            | manager 固定挂载实例；Host 之后使用目录路径前，检查目录是否仍为最新 |

两种模式共用 `SkillSource`、`SkillMaterializer`、frontmatter 解析器、限制、冲突策略和路径范围检查。都不会隐式扫描 home 目录、已安装软件包或相邻目录。

## 设计

```mermaid
flowchart TB
    Host[Host 配置] --> Sources[FileSkillSource]
    Host --> Materializers[可信 SkillMaterializer]
    Sources --> Manager[SkillManager]
    Materializers --> Manager

    Direct[调用方管理的 FileOperator] --> DirectScan[scan files]
    DirectScan --> Manager
    Manager --> Catalog[SkillCatalogItem 目录]

    Environment[已进入的 Environment] --> BoundScan[scan_environment]
    BoundScan --> Scopes[固定到挂载实例的文件作用域]
    Scopes --> Manager
    Manager --> BoundCatalog[BoundSkillCatalog]

    BoundCatalog --> HostImport[Host 预览或导入]
    BoundCatalog --> Capability[SkillsCapability]
    Capability --> Instructions[模型指令]
    Capability --> Paths[解析后的 SkillPath]
    Capability --> Events[目录与访问事件]

    class Host,Direct app
    class Manager,Capability a13n
    class Catalog,BoundCatalog store
```

设计将四项职责分开：

- `FileSkillSource` 在明确配置的 FileOperator 根目录下发现大小受限的元数据。
- `SkillMaterializer` 是可信 Host 代码，在一个配置的根目录下发布托管软件包。
- `SkillManager` 负责准备包内容、发现、验证、冲突处理，以及可选的 Environment 挂载绑定，不依赖 Agent Run。
- `SkillsCapability` 将一个绑定目录接入 Run 选择、模型指令、解析后的 `SkillPath`、访问观测，以及模型和工具边界的失效检查。

两个 API 的行为明确且互不混用：

- `scan(files=...)` 只使用传入的 FileOperator，不会创建或探测 Environment；
- `scan_environment(environment=...)` 只使用固定到挂载实例的作用域，不会通过未固定的 `environment.files` 门面或直接扫描模式重试；
- 来源只读取配置的根目录，不尝试进程工作目录、home、软件包目录或其他目录；
- 相关路由变化会触发 `skill_catalog_stale`，绝不会自动重新扫描或改换目标；
- `FileSkillSource`、`SkillSource.roots` 和 `SkillManager.roots` 构成完整的来源与根目录接口。

`required=False` 是明确的来源策略，不是回退发现机制。它可以跳过对应的缺失、无法路由或不支持的根目录，但绝不会用其他路径替代。

## 读取并发与限制

内置扫描最多使用八个读取 worker，保留来源优先级与按名称排序的结果。

`max_entries_per_root` 默认 256，`SkillsPolicy.max_skills` 默认每个来源和最终目录最多 512。超限时失败，不截断。

## Skill 包结构

配置的根目录本身可以是一个 Skill 包，它的每个直接子目录也可以是一个 Skill 包。发现不会继续递归。

```text
.agents/skills/
├── SKILL.md
├── code-review/
│   ├── SKILL.md
│   └── checklist.md
└── release/
    └── SKILL.md
```

每个 `SKILL.md` 都以大小受限的 YAML frontmatter 开始：

```markdown
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Follow the repository review workflow.
```

`name` 是冲突处理和 Run 选择使用的标识。Skill 内容是不可信的模型上下文，不授予工具、凭据、文件系统访问、插件加载或软件包权限。

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

显式 manager 替换默认来源。设置 `SkillsPolicy(conflict="error")` 可拒绝重复名称，而非按来源优先级选择。

`required=False` 会分别跳过每个缺失、无法路由或不支持的根目录。权限拒绝、路径或 frontmatter 格式错误、provider 失败和目录溢出，仍然报错。

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

## 在 Harness Run 中使用 Skill

将 manager 传给 `SkillsCapability`，向 Agent 提供目录及路径：

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

每个根 Run、恢复 Run 或子 Run，都使用准确且重新提供的选择：

- `RunBindings.skill_selection=None` 暴露完整的冲突处理后目录；
- 非空集合只暴露指定名称；
- 空集合不注入任何 Skill 指令或路径；
- 未知名称使 Run 准备失败，错误码为 `skill_selection_unknown`。

选择不属于可移植 `HarnessState`，也不授予来源或文件权限。相关 Environment 路由变化时，当前 Run 会以 `skill_catalog_stale` 失败，不会静默重新扫描，也不会将冻结的指令改指向其他目标。

## Host 的职责

Host 管理来源授权、包验证和持久存储。

导入包时：

1. 通过 `scan_environment()` 扫描并选择条目。
2. 检查目录仍为最新，通过同一 Environment 验证内容。
3. 复制到 Host 管理的不可变存储，发布前验证副本。
