---
title: 发现并使用技能（Skills）
sidebarTitle: 技能（Skills）
description: 封装可复用的 SKILL.md 流程包，扫描工作区与组织级技能目录，并按运行精细控制技能暴露范围。
---

**Skill** 将可复用的操作流程与配套资料封装在 `.agents/skills/<name>/SKILL.md` 目录中。`SkillsCapability()` 向模型指令注入可用技能的名称与简介目录，配合 `DynamicEnvironmentCapability` 的文件读取工具（`view`），模型可按需加载所需流程正文。

## 添加并运行技能

<span id="add-a-skill"></span>

在 `.agents/skills/<name>/SKILL.md` 顶部编写 YAML 前置元数据（`name`、`description`）及流程正文：

```markdown title=".agents/skills/code-review/SKILL.md"
---
name: code-review
description: Review a code change for correctness and maintainability.
---

# Code review

Report findings with file paths.
```

构建智能体时挂载 `SkillsCapability()` 与 `DynamicEnvironmentCapability(...)`，并可通过 `RunBindings.skill_selection` 按运行限制可见技能：

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
    environment=workspace_source,  # 见运行环境：PreparedSource
    bindings=RunBindings.embedded(skill_selection=frozenset({"code-review"})),
)
```

| `RunBindings.skill_selection` | 对模型暴露的目录范围                                         |
| ----------------------------- | ------------------------------------------------------------ |
| `None`（默认）                | 展示去重后的全部已发现技能                                   |
| 非空 `frozenset`              | 仅暴露指定名称的技能（缺失时抛出 `skill_selection_unknown`） |
| 空集合 `frozenset()`          | 不注入任何技能说明或路径                                     |

## 添加组织级来源与前置物化器

通过 `SkillManager` 追加共享目录（`FileSkillSource`）或扫描前物化钩子（`SkillMaterializer`）：

```python
from a13n_harness.capabilities import FileSkillSource, SkillManager, SkillsCapability

manager = SkillManager.default(
    additional_sources=(
        FileSkillSource("organization", ("/environment/shared/skills",), required=True),
    )
)
capability = SkillsCapability(manager)
```

同名技能默认以后声明的来源为准（传入 `SkillsPolicy(conflict="error")` 可改为遇到重名报错）。

## 在宿主代码中直接扫描技能目录

- **直接扫描 `FileOperator`：** 调用 `await manager.scan(files=files)` 返回 `tuple[SkillCatalogItem, ...]`。
- **扫描已进入的 `BoundEnvironment`：** 调用 `catalog = await manager.scan_environment(environment=env)` 返回 `BoundSkillCatalog`；跨越异步等待再次消费路径前请调用 `catalog.require_current(env)` 校验新鲜度。
