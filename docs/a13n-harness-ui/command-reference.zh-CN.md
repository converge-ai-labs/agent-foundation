---
title: 命令参考
description: 查找 shell 或 TUI 命令、所需参数，以及命令会修改的设置。
---

Shell 命令用于启动和管理 Harness UI。斜杠命令用于在 TUI 内控制对话。先按任务找到入口，再查阅具体参数。

| 我想……             | 命令                                              | 操作指南                                     |
| ------------------ | ------------------------------------------------- | -------------------------------------------- |
| 配置第一个 Agent   | `a13n-harness-ui setup`                           | [首次配置](setup.md)                         |
| 在终端聊天         | `a13n-harness-ui`                                 | [TUI 使用](everyday-use.md)                  |
| 在浏览器聊天       | `a13n-harness-ui webui`                           | [WebUI](webui.md)                            |
| 从脚本运行一次提示 | `a13n-harness-ui run "PROMPT" --format json`      | [自动化](automation-and-troubleshooting.md)  |
| 查找或验证配置文件 | `a13n-harness-ui config path` / `config validate` | [配置](configuration.md)                     |
| 查看本地执行环境   | `a13n-harness-ui environment status`              | [Environment](environments-and-projects.md)  |
| 查看身份验证状态   | `a13n-harness-ui auth status`                     | [Model 与账户](models-and-authentication.md) |

## Shell 调用

```console
a13n-harness-ui [GLOBAL OPTIONS] [COMMAND] [COMMAND OPTIONS]
```

全局选项放在命令前。例如，验证另一套配置树：

```bash
a13n-harness-ui --config ./a13n-harness-ui.yaml config validate
```

省略 `COMMAND` 会打开 TUI。每层都支持 `-h` 或 `--help`，例如 `a13n-harness-ui run --help`。帮助和 `--version` 在初始化 App、数据库、Model 或 Environment 之前退出。

大写参数表示需要替换的值。语法说明中的方括号表示可选参数。表格区分了解析器默认值与从[配置](configuration.md)加载的设置。

### 全局选项

| 选项                    | 值                        | 指定后的效果                                                         |
| ----------------------- | ------------------------- | -------------------------------------------------------------------- |
| `--config`              | YAML 路径                 | 选择配置树；默认根文件为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`。 |
| `--data-root`           | 目录路径                  | 覆盖本地数据根目录。                                                 |
| `--resume`              | Thread ID                 | 打开已保存的对话。                                                   |
| `--agent`               | Agent ID                  | 为新对话选择 Agent。                                                 |
| `--environment-mode`    | `full-control`、`sandbox` | 为新对话选择内置模式。                                               |
| `--environment-profile` | Profile ID                | 为新对话选择自定义 profile。                                         |
| `--display`             | `concise`、`detailed`     | 覆盖 `display.mode`；配置默认值为 `concise`。                        |
| `--no-update-check`     | 标志                      | 本次启动跳过更新检测。                                               |
| `--version`             | 标志                      | 打印已安装版本并退出。                                               |

这些选项默认未设置；标志默认 false。`--environment-mode` 与 `--environment-profile` 互斥。`--resume` 不能与新对话的 Agent、Environment 或标题覆盖同用。先恢复，再在 TUI 中修改选择。

## 命令

下面每个标题都是接在 `a13n-harness-ui` 后的子命令。凡支持 `--format text|json` 的命令，默认使用 `text`。身份验证和插件命令可能修改本地存储；`config` 查询也会打开应用状态。

### `webui`

启动前台浏览器服务器。向其他机器开放前，先完成 [WebUI 访问配置](webui.md)。

| 选项                                                              | 默认值      | 用途                                                       |
| ----------------------------------------------------------------- | ----------- | ---------------------------------------------------------- |
| `--host ADDRESS`                                                  | `127.0.0.1` | IPv4 或 IPv6 监听地址。                                    |
| `--port PORT`                                                     | `8765`      | TCP 端口，范围 1 到 65535。                                |
| `--apikey KEY`、`--api-key KEY`                                   | 未设置      | 覆盖 `A13N_HARNESS_UI_API_KEY`；值会出现在 shell 参数中。  |
| `--dangerous-skip-permissions`、`--dangerously-bypass-permission` | 关闭        | 关闭 Web 身份验证，保留 Agent 权限设置。                   |
| `--share-computer` / `--no-share-computer`                        | 启用        | 以服务器 OS 账户共享 Host Files、Git Changes 和 Terminal。 |
| `--browser-no-sandbox` / `--browser-sandbox`                      | 沙箱启用    | 覆盖 `A13N_HARNESS_UI_BROWSER_NO_SANDBOX`。                |

`--no-share-computer` 也会关闭 Host Browser 旁栏、程序探测和浏览器自动化。Host 共享与 Agent 权限独立。

以非 root 用户运行浏览器 Host，并保持 Chromium 沙箱启用。只有单独隔离的 Host 才应明确选择关闭沙箱；未作此选择时，root 会被拒绝。即使环境变量关闭了沙箱，`--browser-sandbox` 也会恢复保护。[Browser 可执行文件覆盖](configuration.md#host-browser)需要单独配置。

### `envd update`

更新原生 Envd 安装，不打开 App。先停止使用该安装的守护进程。未指定可执行文件覆盖时，命令准备 Python 客户端选定的托管版本。

| 选项                | 默认值 | 用途                                           |
| ------------------- | ------ | ---------------------------------------------- |
| `--executable PATH` | 未设置 | 更新独立可执行文件，而不是配置的运行时。       |
| `--version VERSION` | 未设置 | 选择精确原生版本；托管安装仍固定为客户端版本。 |
| `--check`           | 关闭   | 查看更新，不安装。                             |
| `--force`           | 关闭   | 重新安装所选版本；不能与 `--check` 同用。      |

更新流程见 [Envd 维护](automation-and-troubleshooting.md#logs-updates-and-exit)。

### `update`

立即升级当前 uv tool 安装。没有额外参数，也不进行第二次确认。不启动 TUI 或首次配置流程。

### `setup`

交互式配置 Model、上下文预算和执行权限。添加 `--advanced` 可明确选择上下文、推理、工具审查、subagent 和指令。

### `add agent`

创建另一个 Agent，保留已有 Agent 和默认值。添加 `--advanced` 可自定义推理、工具审查和指令。

### `add model`

创建可复用的 Model，不创建或修改 Agent。添加 `--advanced` 可自定义订阅上下文和推理设置。

### `run`

提交一次提示，在对应操作结束后退出。脚本需要 Thread ID、状态或结构化结果时，使用 `--format json`。

```bash
a13n-harness-ui run "Reply with one short sentence." --format json
```

| 参数或选项                         | 默认值 | 用途                                           |
| ---------------------------------- | ------ | ---------------------------------------------- |
| `PROMPT`                           | 必需   | 提示文本；包含空格时加引号。                   |
| `--resume THREAD_ID`               | 未设置 | 继续已保存的 Thread。                          |
| `--agent AGENT_ID`                 | 未设置 | 为新 Thread 选择 Agent。                       |
| `--environment-mode MODE`          | 未设置 | 为新 Thread 选择 `full-control` 或 `sandbox`。 |
| `--environment-profile PROFILE_ID` | 未设置 | 为新 Thread 选择自定义 profile。               |
| `--title TITLE`                    | 未设置 | 为新 Thread 命名。                             |
| `--format text\|json`              | `text` | 选择输出格式。                                 |

恢复限制在这里同样适用。操作失败或挂起时，退出码非零。因决策而挂起时，用打印出的 Thread ID 以交互方式重新打开；单次模式没有审批选择器。见[完整脚本流程](automation-and-troubleshooting.md)。

### `config path`

打印 `configuration_path` 和 `data_root`。用前者定位根 YAML 及其同级资源目录。支持 `--format text|json`。

### `config validate`

验证所选源配置树，报告配置错误或 Capability 警告。支持 `--format text|json`。它检查配置，不测试 provider 或 MCP 连接。

### `config show`

查看已接受的配置。支持 `--format text|json`。文件修改未通过验证时，已接受配置代可能与磁盘文件不同。

### `config subagents`

列出包提供的 subagent，以及各自是否启用。支持 `--format text|json`。

### `import subagents`

预览外部 subagent 定义的转换。检查候选后，添加 `--apply` 再次运行，写入就绪的导入项。

```bash
a13n-harness-ui import subagents --product codex --scope project --project-root .
```

| 选项                  | 默认值     | 用途                                 |
| --------------------- | ---------- | ------------------------------------ |
| `--product PRODUCT`   | 必需       | `claude-code`、`cursor` 或 `codex`。 |
| `--scope SCOPE`       | 必需       | `user` 或 `project`。                |
| `--project-root PATH` | 未设置     | 源目录；project 范围必需。           |
| `--user-home PATH`    | 你的主目录 | user 范围使用的源主目录。            |
| `--apply`             | 关闭       | 写入所有就绪候选。                   |
| `--format text\|json` | `text`     | 选择输出格式。                       |

导入会创建文件，不会自动将导入项加入每个 Agent。按 [Agent 配置](agents-and-subagents.md)选择需要的项。

### `plugin install`

从 Git 安装 Content Plugin：`plugin install REPOSITORY`。仓库包含多个插件时，用 `--plugin ID` 选择；用 `--ref REF` 指定分支、标签或提交。两个选项默认未设置。支持 `--format text|json`。

### `plugin list`

列出已安装的 Content Plugin 及其可编辑目录。支持 `--format text|json`。

### `plugin uninstall`

用 `plugin uninstall PLUGIN_ID` 删除插件。命令会永久删除安装目录及其中的本地修改，不再确认。运行前备份编辑内容。支持 `--format text|json`。

### `environment list`

列出可用的 Environment 模式和 profile。支持 `--format text|json`。

### `environment status`

查看本地 Envd 安装和运行时状态。不下载、不启动守护进程，也不请求桌面权限。支持 `--format text|json`。

### `environment check`

准备内置 profile 并检查就绪状态，不修改已保存的默认值。准备过程可能下载匹配版本的 Envd。

```bash
a13n-harness-ui environment check --profile environment-envd
```

`--profile` 支持 `environment-native`、`environment-envd`、`environment-desktop` 或 `environment-sandbox`，默认 `environment-envd`。也支持 `--format text|json`。退出码 `0` 表示就绪，`1` 表示准备失败。

检查 `environment-desktop` 前，先在 Runtime 设置中启用 Computer Use 并重启 Harness UI。检查可能在 Harness Host 上请求 OS 权限。见[本地 Envd 配置](environments-and-projects.md#manage-local-envd)。

### `environment configure`

准备 profile，再将其保存为新对话默认值。`--profile` 必需，选项与 `environment check` 相同。也支持 `--format text|json`。

成功退出 `0`；准备失败退出 `1`，保留旧默认值。已有 Thread 保留原选择。桌面准备的前提条件与 `environment check` 相同。

### `doctor`

查看 App 和扩展诊断。支持 `--format text|json`。按报告的现象，在[故障排查](automation-and-troubleshooting.md)中选择下一步检查。

### `auth status`

查看 Model 身份验证状态：`auth status [PROVIDER]`。可选 provider 为 `chatgpt`、`codex`、`grok` 或 `copilot`。支持 `--format text|json`。

### `auth key list`

列出已保存的 API 密钥引用，不返回密钥值。支持 `--format text|json`。

### `auth key set`

用 `auth key set REFERENCE` 保存密钥，例如 `auth key set key-primary`。在不回显的提示中输入密钥；命令行只包含引用名。

### `auth key delete`

用 `auth key delete REFERENCE` 删除密钥。除非添加 `--yes`，否则命令会要求确认。引用该密钥的 Model 需要替换密钥后才能完成身份验证。

### `auth sources`

用 `auth sources PROVIDER` 列出账户来源。解析器接受 `chatgpt`、`codex`、`grok` 和 `copilot`；Copilot 支持来源/账户选择，ChatGPT 支持原生注册记录选择。支持 `--format text|json`，不公开凭据。

### `auth select`

替换当前数据根目录中的账户/来源绑定。先运行 `auth sources copilot`，再选择列出的登录名：

```bash
a13n-harness-ui auth select copilot --source native --account YOUR_GITHUB_LOGIN
```

`PROVIDER`、`--source` 和 `--account` 均必需。`--source` 支持 `native` 或 `copilot_cli_file`；文件路径由 Host 解析。Provider 语法与 `auth sources` 相同。Copilot 支持两种来源；ChatGPT 使用 `native`，账户值取自 `auth sources chatgpt` 返回的注册记录 key。支持 `--format text|json`。

### `auth logout`

用 `auth logout PROVIDER` 移除选中的本地凭据，provider 为 `chatgpt`、`codex`、`grok` 或 `copilot`。支持 `--format text|json`。

来源是共享 Copilot CLI 文件时，登出也会删除该文件中选中账户受支持的 token 字段，影响 Copilot CLI。其他账户保留；Host 不选择备用来源。

### `login`

用 `login PROVIDER` 完成身份验证，provider 为 `chatgpt`、`codex`、`grok` 或 `copilot`。

| 选项                          | 默认值          | 用途                           |
| ----------------------------- | --------------- | ------------------------------ |
| `--allow-account-switch`      | 关闭            | 允许替换另一个已保存账户。     |
| `--device-code` / `--browser` | `--device-code` | 选择设备授权或本地浏览器回调。 |
| `--format text\|json`         | `text`          | 选择输出格式。                 |

ChatGPT 的默认流程使用粘贴回调 URL。本地浏览器回调支持 Codex 和 Grok。Copilot 仅支持设备授权，默认使用公共 Copilot App 身份，订阅用户无需注册 OAuth App。权限和兼容性见 [Copilot 配置](models-and-authentication.md#github-copilot-subscription)。

## 斜杠命令

在 TUI 输入框中输入。Tab 补全参数，`/help` 或 `/?` 显示帮助，`/help COMMAND` 查看单个命令。首次配置和登录在 shell 中运行；待处理决策使用 TUI 对应类型的选择器，而不是 `/approve`、`/deny` 或 `/result`。

下表按任务分组。“忙碌时可用”表示解析器允许在执行期间使用；目标资源仍须可用。忙碌时按 Enter，会将草稿文本和附件一起作为 steering 发送。

### 对话与输入

| 命令                     | 忙碌时可用 | 用途                                       |
| ------------------------ | ---------- | ------------------------------------------ |
| `/new`                   | 否         | 新建对话，保留已保存历史。                 |
| `/resume [thread-id]`    | 否         | 搜索、预览、命名或重新打开已保存的对话。   |
| `/history`               | 否         | 浏览保留的消息，也可用 Ctrl+T。            |
| `/notes`                 | 否         | 在显示预算内读取已保存笔记。               |
| `/goal task description` | 否         | 开始目标驱动工作，包含审核和有上限的续接。 |
| `/attach path`           | 是         | 向草稿添加文件或图像。                     |
| `/paste-image`           | 是         | 从 TUI Host 的剪贴板读取图像。             |
| `/recover`               | 否         | 恢复未发送的提示。                         |
| `/review request-id`     | 否         | 针对所选续接查看待处理请求。               |
| `/steer message`         | 是         | 向可接受引导的根操作发送指导。             |
| `/cancel`                | 是         | 停止活动 Run。                             |
| `/quit`、`/exit`         | 是         | 退出 TUI，保留已保存的对话状态。           |

`/steer` 保留尾随消息，包括引号和换行。Steering 遵循全局 `input.steering_mode`：默认即时中断或协作移交；选择 `queued` 则等待下一个安全边界。`/cancel` 还会禁止完成事件自动唤醒，直到收到新输入；它不能替代单独的工具取消操作。

### Model 与 Environment 选择

| 命令                                   | 忙碌时可用 | 用途                                       |
| -------------------------------------- | ---------- | ------------------------------------------ |
| `/agent [agent-id]`                    | 否         | 选择 Agent 及其 Model、指令和工具。        |
| `/model [model-id\|default\|defaults]` | 否         | 覆盖 Model、恢复默认值或配置全局媒体理解。 |
| `/fast [on\|off\|ultrafast\|reset]`    | 否         | 选择当前终端会话的 Fast/Ultrafast 设置。   |
| `/pro [on\|off\|reset]`                | 否         | 选择 Pro/Standard 推理或继承 Model。       |
| `/thinking [level]`                    | 否         | 选择 Model 支持的推理强度。                |
| `/environment [mode]`                  | 否         | 修改后续轮次的本地执行方式。               |
| `/import`                              | 否         | 预览并可选启用继承父级的外部 subagent。    |
| `/config`                              | 否         | 定位配置文件。                             |

`/model` 为 Project 记住覆盖选择；`default` 清除当前终端会话的覆盖和记住的选择。`defaults` 打开全局媒体理解编辑器，可 Save/Cancel。Fast 选择仅在当前终端会话内有效。

`/thinking` 及补全使用所选 Model 支持的选项。`default` 继承配置。按适配器不同，明确的级别可能包括 `off`、`minimal`、`low`、`medium`、`high`、`xhigh` 和 `max`。未知模型只提供 `default` 并解释原因；不支持的级别会被拒绝。

`/environment` 在支持时接受 `full-control`、`envd`、`desktop` 或 `sandbox`。`envd` 和 `desktop` 先准备运行时，再修改 Thread 下一轮的 profile；失败保留原选择。它修改当前 Thread，不修改新对话默认值。

### 状态与显示

| 命令                                                                    | 忙碌时可用 | 用途                                                        |
| ----------------------------------------------------------------------- | ---------- | ----------------------------------------------------------- |
| `/help [command]`、`/?`                                                 | 是         | 显示帮助和快捷键。                                          |
| `/mode [concise\|detailed]`                                             | 是         | 修改输出详细程度，不改变执行。                              |
| `/theme [auto\|dark\|light]`                                            | 是         | 修改 TUI 主题。                                             |
| `/mouse [on\|off]`                                                      | 是         | 切换滚轮捕获；`off` 允许原生文本选择/复制。                 |
| `/status`                                                               | 是         | 查看 Model、上下文、Environment 和订阅用量。                |
| `/ps`                                                                   | 是         | 查看观测到的进程及最后报告状态。                            |
| `/executions [offset\|cancel execution-id]`                             | 是         | 分页查看保留的工具执行或请求取消。                          |
| `/subagents [execution-id\|next]`                                       | 是         | 查看子级执行和保留输出。                                    |
| `/usage [all\|root\|subagents\|details\|subscription\|accounts\|reset]` | 是         | 查看 Thread 用量、订阅限额、账户或需确认的 Codex 重置额度。 |

`/usage details` 补充 Run 归属；`all`、`root` 和 `subagents` 选择用量范围。订阅限额与 Thread 的 token 和费用总量分开。`/executions` 读取进程内状态，不消耗结果；`cancel execution-id` 请求取消对应的保留执行。见[工具执行控制](webui.md#tool-executions-and-immediate-steering)。

## 重要操作与限制

| 操作                                 | 使用前需确认                                       |
| ------------------------------------ | -------------------------------------------------- |
| `plugin uninstall`                   | 先备份插件的本地修改；删除立即执行。               |
| `auth key delete --yes`              | 确认引用名；该标志跳过确认提示。                   |
| `update`、`envd update`              | 会直接安装，不再次确认。仅检测到更新不会触发安装。 |
| `import subagents --apply`           | 先检查预览；这会写入导入文件。                     |
| `run`                                | 因决策挂起时，需要以交互方式重新打开。             |
| `webui --dangerous-skip-permissions` | 它移除 HTTP 身份验证，不关闭 Agent 权限检查。      |

## 按任务查阅指南

- [首次配置](setup.md)：连接 Model，发送第一条提示。
- [配置](configuration.md)：定位文件、编辑资源、验证修改。
- [Model 与身份验证](models-and-authentication.md)：选择路由和管理凭据。
- [MCP](mcp.md) 与 [Skill/Content Plugin](skills-and-content-plugins.md)：添加集成。
- [TUI 使用](everyday-use.md)：编写输入、查看历史、处理决策。
- [自动化与故障排查](automation-and-troubleshooting.md)：检查结果、诊断失败、恢复已保存的工作。
