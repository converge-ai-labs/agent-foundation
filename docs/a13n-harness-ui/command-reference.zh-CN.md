---
title: 命令参考
description: 全部 shell 命令、选项和聊天斜杠命令。
---

用 shell 命令启动或管理 Harness UI；在交互式对话中使用斜杠命令。需要工作流说明而非语法速查时，从[设置](setup.md)或[终端使用](everyday-use.md)开始。

## Shell 调用

```console
a13n-harness-ui [GLOBAL OPTIONS] [COMMAND] [COMMAND OPTIONS]
```

省略 `COMMAND` 会打开交互式终端。全局选项放在命令前；每层都可用 `-h` 或 `--help`。`--version` 是优先处理的信息选项。帮助和版本不初始化 App、数据库、Model 或 Environment。

这些表展示命令行默认值；[配置](configuration.md)说明实际默认值。`--resume` 不能与新会话 Agent、Environment 或标题覆盖同用。

### 全局选项

| 参数                    | 类型 / 选项           | 解析器默认值 | 含义                                                                          |
| ----------------------- | --------------------- | ------------ | ----------------------------------------------------------------------------- |
| `--config`              | 路径                  | 未设置       | 显式 Harness UI 配置 YAML（默认 `~/.a13n-harness-ui/a13n-harness-ui.yaml`）。 |
| `--data-root`           | 路径                  | 未设置       | 覆盖本地 Harness UI 数据根。                                                  |
| `--resume`              | 文本                  | 未设置       | 按 ID 恢复已保存会话。                                                        |
| `--agent`               | 文本                  | 未设置       | 为当前会话选择已配置 Agent。                                                  |
| `--environment-mode`    | full-control, sandbox | 未设置       | 覆盖当前会话的内置 Environment 模式。                                         |
| `--environment-profile` | 文本                  | 未设置       | 覆盖当前会话的自定义 Environment 配置。                                       |
| `--display`             | concise, detailed     | 未设置       | 只影响显示；覆盖 `display.mode`（默认 concise）。`/mode` 实时切换。           |
| `--no-update-check`     | 布尔值                | `false`      | 本次启动跳过更新检测。                                                        |
| `--version`             | 布尔值                | `false`      | 显示版本并退出。                                                              |

## 命令

全局选项放在这些子命令前。身份验证和插件命令修改本地存储；`config` 检查也会打开应用状态。

### `webui`

运行一个前台 WebUI 服务器，使用内置浏览器资源。

| 参数                                                            | 类型 / 选项   | 解析器默认值  | 含义                                                                     |
| --------------------------------------------------------------- | ------------- | ------------- | ------------------------------------------------------------------------ |
| `--host`                                                        | 文本          | `"127.0.0.1"` | 监听 IPv4 或 IPv6 地址。                                                 |
| `--port`                                                        | 整数 1..65535 | `8765`        |                                                                          |
| `--apikey, --api-key`                                           | 文本          | 未设置        | 监听器 API 密钥；覆盖 `A13N_HARNESS_UI_API_KEY`（在 shell 参数中可见）。 |
| `--dangerous-skip-permissions, --dangerously-bypass-permission` | 布尔值        | `false`       | 只关闭 Web 身份验证，不改变 Agent 权限。                                 |
| `--share-computer` / `--no-share-computer`                      | 布尔值        | `true`        | 以服务器 OS 账户共享原生 Host 文件，与 Agent 权限独立。                  |

### `update`

立即更新当前 uv tool 安装，不启动聊天或设置。

没有命令专用参数。

### `setup`

交互式配置模型、上下文预算和执行权限。

| 参数         | 类型 / 选项 | 解析器默认值 | 含义                                              |
| ------------ | ----------- | ------------ | ------------------------------------------------- |
| `--advanced` | 布尔值      | `false`      | 还可选择上下文、推理、工具审查、subagent 和指令。 |

### `add agent`

创建其他 agent，不改变已有 agent 或默认值。

| 参数         | 类型 / 选项 | 解析器默认值 | 含义                             |
| ------------ | ----------- | ------------ | -------------------------------- |
| `--advanced` | 布尔值      | `false`      | 还可自定义推理、工具审查和指令。 |

### `add model`

创建可复用模型，不创建或修改 agent。

| 参数         | 类型 / 选项 | 解析器默认值 | 含义                             |
| ------------ | ----------- | ------------ | -------------------------------- |
| `--advanced` | 布尔值      | `false`      | 还可自定义订阅上下文和推理设置。 |

### `run`

执行一次提示，不打开交互式终端。

| 参数                    | 类型 / 选项           | 解析器默认值 | 含义                                  |
| ----------------------- | --------------------- | ------------ | ------------------------------------- |
| `PROMPT`                | 文本                  | 必需         | 必需位置参数                          |
| `--resume`              | 文本                  | 未设置       | 继续已保存会话。                      |
| `--agent`               | 文本                  | 未设置       | 新会话使用的 Agent。                  |
| `--environment-mode`    | full-control, sandbox | 未设置       | 新会话使用的内置执行模式。            |
| `--environment-profile` | 文本                  | 未设置       | 新会话使用的自定义 Environment 配置。 |
| `--title`               | 文本                  | 未设置       | 新会话标题。                          |
| `--format`              | text, json            | `"text"`     |                                       |

### `config path`

显示选中的配置和数据路径。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `config validate`

验证选中的源配置树。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `config show`

显示已接受配置。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `config subagents`

列出包提供的 subagent 和当前选择。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `import subagents`

预览或应用外部 subagent 导入。

| 参数             | 类型 / 选项                | 解析器默认值 | 含义                             |
| ---------------- | -------------------------- | ------------ | -------------------------------- |
| `--product`      | claude-code, cursor, codex | 必需         |                                  |
| `--scope`        | user, project              | 必需         |                                  |
| `--project-root` | 路径                       | 未设置       |                                  |
| `--user-home`    | 路径                       | 未设置       |                                  |
| `--apply`        | 布尔值                     | `false`      | 应用所有就绪候选；省略则只预览。 |
| `--format`       | text, json                 | `"text"`     |                                  |

### `plugin install`

从 Git 仓库安装一个 Content Plugin。

| 参数         | 类型 / 选项 | 解析器默认值 | 含义                              |
| ------------ | ----------- | ------------ | --------------------------------- |
| `REPOSITORY` | 文本        | 必需         | 必需位置参数                      |
| `--plugin`   | 文本        | 未设置       | 仓库包含多个插件时，指定插件 ID。 |
| `--ref`      | 文本        | 未设置       | 要安装的 Git 分支、标签或提交。   |
| `--format`   | text, json  | `"text"`     |                                   |

### `plugin list`

列出已安装 Content Plugin 及其目录。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `plugin uninstall`

永久删除已安装 Content Plugin 目录，包括本地修改，不再确认。先备份编辑内容；重新安装不会恢复。

| 参数        | 类型 / 选项 | 解析器默认值 | 含义         |
| ----------- | ----------- | ------------ | ------------ |
| `PLUGIN_ID` | 文本        | 必需         | 必需位置参数 |
| `--format`  | text, json  | `"text"`     |              |

### `environment list`

列出 Environment 模式和配置。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `doctor`

检查 App 和扩展健康状态。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `auth status`

显示模型身份验证状态。

| 参数       | 类型 / 选项          | 解析器默认值 | 含义         |
| ---------- | -------------------- | ------------ | ------------ |
| `PROVIDER` | codex, grok, copilot | 未设置       | 可选位置参数 |
| `--format` | text, json           | `"text"`     |              |

### `auth key list`

列出已保存密钥引用，不返回密钥字节。

| 参数       | 类型 / 选项 | 解析器默认值 | 含义 |
| ---------- | ----------- | ------------ | ---- |
| `--format` | text, json  | `"text"`     |      |

### `auth key set`

通过不回显提示添加或替换密钥，绝不通过命令行传入密钥。

| 参数        | 类型 / 选项 | 解析器默认值 | 含义         |
| ----------- | ----------- | ------------ | ------------ |
| `REFERENCE` | 文本        | 必需         | 必需位置参数 |

### `auth key delete`

确认后删除已保存密钥。后续使用该密钥解析 Model 可能失败。

| 参数        | 类型 / 选项 | 解析器默认值 | 含义                       |
| ----------- | ----------- | ------------ | -------------------------- |
| `REFERENCE` | 文本        | 必需         | 必需位置参数               |
| `--yes`     | 布尔值      | `false`      | 不显示提示，直接确认操作。 |

### `auth sources`

列出受支持的已存账户来源，不公开凭据。目前只有 Copilot 支持来源选择。

| 参数       | 类型 / 选项          | 解析器默认值 | 含义         |
| ---------- | -------------------- | ------------ | ------------ |
| `PROVIDER` | codex, grok, copilot | 必需         | 必需位置参数 |
| `--format` | text, json           | `"text"`     | 输出格式     |

### `auth select`

显式替换当前 Host 的账户和凭据来源绑定。使用 `auth sources copilot` 列出的登录；来源路径由 Host 解析，不在命令行提供。

| 参数        | 类型 / 选项              | 解析器默认值 | 含义                                  |
| ----------- | ------------------------ | ------------ | ------------------------------------- |
| `PROVIDER`  | codex, grok, copilot     | 必需         | 必需位置参数；目前仅 Copilot 支持选择 |
| `--source`  | native, copilot_cli_file | 必需         | 凭据来源类型                          |
| `--account` | 文本                     | 必需         | 选中的 GitHub 登录名                  |
| `--format`  | text, json               | `"text"`     | 输出格式                              |

### `auth logout`

移除选中的本地模型凭据。来源是共享 Copilot CLI 文件时，也会删除该文件中选中账户受支持的 token 字段，影响 Copilot CLI。其他账户保留；Host 不回退到其他来源。

| 参数       | 类型 / 选项          | 解析器默认值 | 含义         |
| ---------- | -------------------- | ------------ | ------------ |
| `PROVIDER` | codex, grok, copilot | 必需         | 必需位置参数 |
| `--format` | text, json           | `"text"`     |              |

### `login`

为兼容模型提供方完成身份验证。Copilot 只支持设备授权，默认公共 Copilot App 身份；订阅用户无需注册 OAuth App。权限和兼容性限制见[模型与身份验证](models-and-authentication.md#github-copilot-subscription)。

| 参数                       | 类型 / 选项          | 解析器默认值 | 含义                               |
| -------------------------- | -------------------- | ------------ | ---------------------------------- |
| `PROVIDER`                 | codex, grok, copilot | 必需         | 必需位置参数                       |
| `--allow-account-switch`   | 布尔值               | `false`      |                                    |
| `--device-code, --browser` | 布尔值               | `true`       | 设备授权（默认）或本地浏览器回调。 |
| `--format`                 | text, json           | `"text"`     |                                    |

## 聊天命令

在终端聊天输入框而非 shell 中输入。Tab 补全支持语法；`/help` 和 `/?` 显示原生帮助。没有 `/setup`、`/login`、`/approve`、`/deny` 或 `/result` 命令。决策使用对应类型的选择器。

“忙碌时可用”表示解析器允许在执行期间使用，不允许绕过待处理交互或操作不可用资源。`/steer` 要求根操作当前可接受指导。附件命令修改草稿；活动 Run 中 Enter 将文本和附件一同发送。见[使用终端](everyday-use.md)。

| 命令语法                                | 别名    | 忙碌时可用 | 用途                                                                              |
| --------------------------------------- | ------- | ---------- | --------------------------------------------------------------------------------- |
| `/help [command]`                       | `/?`    | 是         | 显示命令帮助和键盘快捷键。                                                        |
| `/mode [concise\|detailed]`             | —       | 是         | 切换输出详细程度，不改变执行。                                                    |
| `/theme [auto\|dark\|light]`            | —       | 是         | 选择终端主题。                                                                    |
| `/mouse [on\|off]`                      | —       | 是         | 切换滚轮捕获；off 保留原生选择/复制。                                             |
| `/attach path`                          | —       | 是         | 为当前草稿附加文件或图像。                                                        |
| `/paste-image`                          | —       | 是         | 显式读取剪贴板图像。                                                              |
| `/recover`                              | —       | 否         | 恢复未发送提示。                                                                  |
| `/status`                               | —       | 是         | 显示模型、上下文、环境和订阅用量。                                                |
| `/ps`                                   | —       | 是         | 查看观测的后台进程及其最后报告状态。                                              |
| `/subagents [execution-id\|next]`       | —       | 是         | 查看本对话子级执行和保留输出。                                                    |
| `/usage [details\|subscription\|reset]` | —       | 是         | 显示已记录 Thread 用量；subscription/reset 查看 Codex 限制。                      |
| `/goal task description`                | —       | 否         | 开始 Goal，针对编写目标进行有上限的自查。                                         |
| `/steer message`                        | —       | 是         | agent 工作期间补充指导。                                                          |
| `/import`                               | —       | 否         | 预览并可选启用继承父级的外部 subagent。                                           |
| `/agent [agent-id]`                     | —       | 否         | 切换 agent，包括模型、指令和工具。                                                |
| `/model [model-id\|default\|defaults]`  | —       | 否         | 选择 Project Model；default 清除偏好；defaults 配置全局媒体理解，可 Save/Cancel。 |
| `/fast [on\|off\|ultrafast\|reset]`     | —       | 否         | 切换支持的 Fast 处理，不保存配置。                                                |
| `/pro [on\|off\|reset]`                 | —       | 否         | 选择 Pro 或 Standard 推理模式；reset 继承 Model 配置。                            |
| `/thinking [level]`                     | —       | 否         | 查看或改变后续轮次推理强度。                                                      |
| `/environment [mode]`                   | —       | 否         | 查看或选择后续轮次执行权限。                                                      |
| `/new`                                  | —       | 否         | 开始新会话，保留全部已存历史。                                                    |
| `/resume [session-id]`                  | —       | 否         | 搜索、预览、命名已保存会话，或按 ID 恢复。                                        |
| `/history`                              | —       | 否         | 浏览保留消息（Ctrl+T）。                                                          |
| `/notes`                                | —       | 否         | 在显示预算内展示完整已保存笔记。                                                  |
| `/config`                               | —       | 否         | 查找配置文件。                                                                    |
| `/review request-id`                    | —       | 否         | 针对所选续接查看待处理请求。                                                      |
| `/cancel`                               | —       | 是         | 停止当前任务。                                                                    |
| `/quit`                                 | `/exit` | 是         | 结束当前会话。                                                                    |

`/thinking` 打开所选 Model 支持的选项；命令补全使用相同列表。`default` 继承 Model 配置。按模型和适配器，显式选择可能包括 `off`、`minimal`、`low`、`medium`、`high`、`xhigh` 或 `max`。不支持的选项会被拒绝；未知模型仅提供 default 并解释原因。`/environment` 在支持时选择 `full-control` 或 `sandbox`。原生 `/steer` 保留整个尾随消息，不拆成 shell 词语。

## 重要操作与限制

- `auth key delete REFERENCE --yes` 显式跳过删除确认，不使删除变得可逆。
- `plugin uninstall ID` 立即删除安装文件和修改，不只是关闭来源。
- `update` 请求安装，不再次确认。启动更新检查仍需在安装前确认。
- `import subagents` 未提供 `--apply` 时只预览；导入文件不会自动加入每个 Agent。
- 单次 `run` 不打开交互审批选择器；挂起/失败操作以非零状态退出。需要决策时以交互方式恢复。
- `webui --dangerous-skip-permissions` 只改变 HTTP 身份验证，不改变 Agent 执行权限。暴露监听器前阅读[浏览器服务器](webui.md)。

## 按任务查阅指南

- [设置连接](setup.md)与[管理账户](models-and-authentication.md)。
- [配置资源](configuration.md)、[MCP 服务器](mcp.md)和 [Skill/Content Plugin](skills-and-content-plugins.md)。
- [使用终端](everyday-use.md)介绍忙碌输入、历史、选择器和键盘快捷键。
- [自动化与诊断](automation-and-troubleshooting.md)介绍退出/恢复、日志和中断任务。
