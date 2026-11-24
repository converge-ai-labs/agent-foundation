---
title: 使用终端界面 (TUI)
description: 在终端中高效浏览代码库、实时引导智能体思考、添加附件、审批工具调用并恢复历史会话。
---

完成[初始化设置](setup.md)后，在任意代码仓库目录启动 Harness UI 终端界面（TUI）：

```bash
a13n-harness-ui
```

输入 `/status` 确认当前智能体、模型与工作目录，提交任务后可随时按 **Ctrl+O** 展开或折叠完整工具参数、执行输出与代码 Diff。

## 输入框状态、快捷键与常用指令

**Enter** 键的行为会随当前运行状态自动切换：

| 当前状态         | 按下 Enter 的动作                  | 常用编辑与视图快捷键                                                                                                      |
| :--------------- | :--------------------------------- | :------------------------------------------------------------------------------------------------------------------------ |
| **空闲（Idle）** | 提交草稿开启新轮次                 | **Ctrl+J** / **Alt+Enter**：换行；**Ctrl+G**：在 `$VISUAL`/`$EDITOR` 中编辑长草稿；**Tab**：自动补全；`$`：唤起技能选择器 |
| **运行中**       | 向当前运行实时注入引导（Steering） | **Ctrl+O**：展开/收起工具详情；**F2**：切换任务面板；**Ctrl+C**：取消运行；**Ctrl+End**：跳回实时输出底部                 |
| **等待决策**     | 确认当前审批或提问卡片上的选择     | **Ctrl+T** / `/history`：完整对话记录；**Alt+E**：查看折叠的长粘贴块（`> 1,000` 字符）；**Ctrl+D** / `/exit`：退出应用    |

对话过程中可随时切换模型、推理档位或查看运行状态（完整语法见[命令参考](command-reference.md)）：

| 操作                 | 命令                                                                         |
| :------------------- | :--------------------------------------------------------------------------- |
| **切换智能体或模型** | `/agent <agent-id>`、`/model <model-id>`、`/model default`                   |
| **推理强度与速度档** | `/thinking`、`/pro on\|off\|reset`、`/fast on\|off\|reset`                   |
| **查看运行态与用量** | `/status`、`/environment`、`/subagents`、`/ps`、`/notes`、`/usage [details]` |
| **显示与草稿恢复**   | `/theme auto\|dark\|light`、`/mouse on\|off`、`/recover`                     |

## 审批与结构化提问

当敏感工具调用或 `ask_user_question` 挂起执行时，界面会弹出交互式决策卡片：

- **工具审批：** 核对工具名、参数与风险摘要，使用方向键选择 **Approve once**（批准一次）、**Deny**（拒绝）或 **Deny with reason**（附理由拒绝，引导模型调整方案）。
- **结构化提问：** 使用 **Up/Down** 选择选项（多选题按 **Space** 勾选）并按 **Enter** 确认；或按 **Tab** 切入自由文本输入框填写自定义回答。

可在根配置或项目 YAML 中调整内置交互开关，并用 `a13n-harness-ui config validate` 校验：

```yaml
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
```

## 附件、历史会话恢复、Goal 模式与本地命令

- **添加文件与剪贴板截图：** 输入 `/attach "path/to/file"`，或按 **Ctrl+V** / 输入 `/paste-image` 粘贴截图。每个会话的工作文件分别存放在 `attachments/`（跨轮次保留）、`tmp/`（跨轮次复用）与 `tmp/tool-results/`（运行结束后清理）。
- **浏览与恢复历史会话：** 输入 `/resume` 打开会话选择器（**Up/Down** 与 **PageUp/PageDown** 翻阅、**Ctrl+A** 切换跨项目显示、**F2** 重命名、**Enter** 加载续聊）。
- **多轮自主 Goal 模式：** 对需要多轮验证的复杂目标使用 `/goal`，让智能体持续迭代执行并自检完成标准：
  ```text
  /goal Refactor the database connection pool, run the test suite, and resolve any regressions.
  ```
- **直接执行宿主机命令：** 以感叹号 `!` 开头（如 `!git status`、`!pytest -q`）即可在本地目录直接执行 Shell 命令，不消耗模型 Token。
