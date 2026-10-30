---
title: 模型与身份验证
description: 连接提供方、选择已保存 Model，并区分请求、连接和上下文设置。
---

**Model** 是可复用的提供方连接及请求设置。**Agent** 通过 ID 选择 Model，并添加指令和工具。Model 文件位于[选中的根配置](configuration.md#locate-and-validate-your-files)旁的 `models/` 中；凭据独立存储。

## 按任务查找

- **添加连接：** 按下文的创建流程操作。
- **编写 YAML：** 使用[完整 API 密钥用法](configuration-recipes.md#change-the-model-reasoning-or-context-budget)或 [Codex 示例](#codex-model-example)。
- **修改可用连接：** 查阅[字段参考](#model-file-reference)、[请求设置](#native-request-settings-and-connection-wiring)或[上下文策略](#context-and-modality-policy)。
- **管理凭据和额度：** 见[登录](#subscription-login-and-api-keys)和[账户](#account-store-locations)。

## 创建与管理 Model

先安装 Harness UI，并准备一种支持的连接所需的凭据。第一个 Agent 使用[设置指南](setup.md)；此流程用于添加另一个 Model。

1. 在 WebUI 打开 **Settings → Models**，或运行：

   ```console
   a13n-harness-ui add model
   ```

2. 选择连接：API 提供方/协议，或 ChatGPT、Codex、Grok、GitHub Copilot 订阅。订阅连接使用各自的登录和传输。

3. 连接账户。API 访问可选择已存密钥、在隐藏字段保存新密钥，或指定服务器环境变量名。Codex/Grok 选择账户组。登录和密钥保存独立于 Model 草稿生效。

4. 选择建议模型，或输入区分大小写的提供方模型 ID。建议是发行版维护的起点；使用账户可访问的 ID。发现失败时仍可手工输入。

5. 检查 **Model defaults**：推理、服务层级和工作上下文预算。**Custom budget** 接受数值；**Restore recommended defaults** 会主动替换已编写的设置。普通连接修改保留显式设置。

6. 保存。已保存 Model 出现在对话选择器中。为新 Run 选择它，并发送简短提示。TUI 中用 `/model`，再用 `/status` 检查选择。

保存/验证 Model 证明配置有效；发送提示验证凭据和推理访问。保存不改变活动 Run。要作为 Agent 的永久选择，编辑该 Agent 的 `model` 字段，或使用其 Model 选择器。原生工具在 **Agent** 上选择；修改 Model 不改写工具。

`setup` 和 `add agent` 的新 Model 路径使用相同连接准备流程。可在登录前保存订阅配置，但首次请求前必须认证。取消草稿不会撤销已保存密钥、登录或独立保存的内联 Model。

## 在 TUI 中切换 Agent 或 Model

| 命令                               | 效果                                  |
| ---------------------------------- | ------------------------------------- |
| `/agent` 或 `/agent agent-primary` | 选择完整 Agent 配置                   |
| `/model` 或 `/model model-primary` | 只覆盖 Model；为当前 Project 记住选择 |
| `/model default`                   | 清除 Model 覆盖和记住的 Project 选择  |
| `/thinking`                        | 显示所选 Model 支持的推理控件         |
| `/thinking default`                | 使用 Model 已保存的推理设置           |
| `/status`                          | 检查选择和根级观测用量                |

使用自己的已保存 ID。选择 Agent/Model 会清除临时推理、Fast 和 Pro 控制。记住的 Project Model 在对话导航、Agent 选择和 TUI 重启后保留；显式启动 `--agent`、无界面运行和 API 调用方不继承。命令保留历史和 Environment 选择，不编辑 YAML。

活动操作保留捕获值。Markdown 子级继承父级实际 Model；独立配置的子级和辅助 Model 保留自己的选择。观测费用是估算，不是订阅账单。见[选择与恢复](everyday-use.md#everyday-interaction)和[用量控制](everyday-use.md#tasks-usage-and-terminal-feedback)。

TUI 和 WebUI 都会自动向每个 Agent 提供当前 Thread ID 和提供方模型名。Agent 无需查询配置或询问你，就能确认所用 Model 或为工作署名。这里使用当前 Run 实际解析的 Model，不使用本地资源 ID 或下一轮选择。子 Agent 收到各自的模型名。

## Codex Model 示例

此例向已有配置添加一个 Codex 订阅 Model。

1. 运行 `a13n-harness-ui login codex` 并完成授权。

2. 运行 `a13n-harness-ui config path`，找到 `configuration_path`。以该根 YAML 的父目录为起点，将以下完整文件保存为 `models/codex.yaml`。账户使用其他受支持模型时，替换路由中的模型 ID。

   ```yaml title="models/codex.yaml — complete file"
   schema_version: "1"
   kind: model
   id: model-codex
   name: Codex
   route: openai-codex:gpt-6.1-sol
   authentication:
     kind: codex_subscription
   settings:
     openai_reasoning_effort: high
     openai_reasoning_summary: detailed
     openai_store: false
   model_characteristics:
     context_window_tokens: 350000
     proactive_context_management_threshold: 0.65
     compact_threshold: 0.90
   ```

3. 运行 `a13n-harness-ui config validate`。自定义配置树应在每个子命令前使用相同的 `--config PATH`，包括登录。

4. 在 TUI 选择 `/model model-codex`，发送简短提示，再检查 `/status`。作为 Agent 默认值时，设置其 `model: model-codex` 并清除临时 Model 覆盖。

示例使用默认 Codex 账户组。[账户组](#account-store-locations)允许选择其他组，无须将 token 放入 YAML。

## Model 文件参考

每个文件需要 `schema_version: "1"`、`kind: model`、以 `model-` 开头的唯一 ID，以及易读 `name`。

| 字段                    | 默认值 | 负责内容                                              |
| ----------------------- | ------ | ----------------------------------------------------- |
| `route`                 | 必需   | 提供方及其模型 ID，例如 `openai-responses:gpt-5`      |
| `authentication`        | 必需   | 与路由匹配的凭据来源                                  |
| `settings`              | `{}`   | 推理、输出限制等请求参数                              |
| `model_configuration`   | `{}`   | 支持的连接设置：`base_url`、`session_affinity_header` |
| `model_characteristics` | `null` | 本地上下文预算和输入策略                              |

这些映射属于 **Model 文件**，不是根 YAML。API 认证恰好需要一个来源：

```yaml title="Model file — read a process environment variable"
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

```yaml title="Model file — use a saved key"
authentication:
  kind: api_key
  credential_ref: key-primary
```

先用 `a13n-harness-ui auth key set key-primary` 保存 `key-primary`。环境引用填写变量名，不是密钥；使用时该变量必须存在于 Harness UI 进程中。凭据缺失会使请求失败，不会选择其他来源。

订阅认证使用 `chatgpt_subscription`、`codex_subscription`、`grok_subscription` 或 `copilot_subscription`，对应 `openai-chatgpt:`、`openai-codex:`、`grok:` 或 `github-copilot:` 路由。Codex/Grok 可选 `group`。订阅端点不能覆盖。

### 上下文与模态策略

提供 `model_characteristics` 对象时，内部字段默认如下：

| 字段                                     | 默认值     | 含义                                                                                                          |
| ---------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------- |
| `context_window_tokens`                  | `null`     | 正数本地工作预算；省略保留原生/目录行为                                                                       |
| `proactive_context_management_threshold` | `0.65`     | 提醒比例，0–1；`null` 关闭派生阈值                                                                            |
| `compact_threshold`                      | `0.90`     | 压缩比例，大于 0 且不超过 1                                                                                   |
| `capabilities`                           | `[]`       | 二进制输入声明：`image_understanding`、`audio_understanding`、`video_understanding`、`document_understanding` |
| `url_input.video`                        | `[]`       | 原生 URL 子类型，当前为 `youtube`；与二进制视频分开                                                           |
| `video_input.max_video_bytes`            | `10485760` | Base64 编码后的视频预算，限制单个视频及请求总量                                                               |

预算控制本地行为，不授予提供方权限。Agent handoff/compaction Capability 的显式 token 阈值仍优先。Harness UI 接受旧 `context_window`；新保存使用 `context_window_tokens`，同时提供时两者必须一致。核心 Harness spec 要求规范名称。读取旧文件或捕获不会改写它们。

### 图片输入预处理

将此配置合并到 Model，可自定义图像预处理。省略 `image_input` 使用默认值，设为 `null` 关闭预处理：

```yaml title="Model file — image preparation"
model_characteristics:
  image_input:
    support_gif: false
    max_images: 20
    image_retention_ratio: 0.6
    max_image_bytes: 5242880
```

超限时保留最新 12 张图像，再允许增长到 20 张。比例表示保留量；`1` 只移除超限部分。字节按**每张图像**的 Base64 编码大小计算，不是原始文件或请求总大小。GIF 支持是声明策略，不是探测。未指定字段保留原生默认值；尺寸和切分见[完整图像策略](../a13n-harness/models.md#image-input-policy)。

根、独立子级和图像理解 Model 各自使用自身策略。捕获的 Run 在后续文件修改后仍保留完整策略。

## 原生请求设置与连接设置

JSON 兼容请求参数放在 `settings`。嵌套对象、显式 `null` 和有意义的字符串空白会保留。已安装适配器/提供方在使用时验证含义；加载配置不发出推理请求。

| 设置                                                                                  | 用途                              |
| ------------------------------------------------------------------------------------- | --------------------------------- |
| `max_tokens`、`temperature`、`stop_sequences`                                         | 生成控制                          |
| `thinking`、`openai_reasoning_effort`、`anthropic_thinking`、`google_thinking_config` | 通用或原生推理控制                |
| `service_tier`、`openai_service_tier`                                                 | 处理层级；原生 OpenAI 字段优先    |
| `extra_headers`、`extra_body`                                                         | 原生请求扩展                      |
| `openai_prompt_cache_key`、`openai_store`                                             | OpenAI 选项，受订阅适配器行为约束 |

设置原样持久化，包括放在其中的秘密。使用专用凭据引用，分享前检查诊断内容。

Host 连接构造使用 `model_configuration`。支持的 HTTP/API 密钥路由接受不含嵌入凭据、查询或片段的 HTTP(S) `base_url`；支持本地 HTTP。未知构造字段验证失败。订阅和原生 `xai:` gRPC 要求空连接配置。

使用 API 密钥 **Google Cloud 网关**时，按下例修改完整 Model 的连接字段。这与向导的 `google:` Gemini Developer API 连接不同：

```yaml title="Model file — Google Cloud gateway connection fields"
route: google-cloud:your-gateway-model-id
authentication:
  kind: api_key
  credential_ref: key-gateway
model_configuration:
  base_url: https://gateway.example.com
```

保存 `key-gateway`，填写网关模型 ID 和端点，验证后测试请求。SDK 添加版本/资源路径；追加 `/v1beta` 或 `/v1beta1` 不会切换传输。`google-gla:`、`google-vertex:` 和 `gemini:` 是 Cloud 别名。该路由提供 API 密钥认证，不配置 Cloud IAM/服务账户。

## 网关会话亲和性

网关已有读取会话 header 的路由规则时，可使用亲和性。在 Add Model 选择 **Gateway session affinity**，或填写 **Session affinity header**。保存的 Model 包含 header 名称：

```yaml title="Model file — gateway wiring"
model_configuration:
  base_url: https://gateway.example.com/v1
  session_affinity_header: x-litellm-session-id
```

替换端点和 header 为网关设置。字段放在 `model_configuration`，不放 `settings.extra_headers`。Harness 根据当前 Thread ID 派生稳定 UUID v5 作为值；不要自行填写值。省略/null 字段关闭。

| 选择器预设            | 保存的 header          |
| --------------------- | ---------------------- |
| LiteLLM               | `x-litellm-session-id` |
| Conversation ID       | `x-conversation-id`    |
| Bifrost               | `x-bf-session-id`      |
| X-Session-ID / 自定义 | `x-session-id`         |

选择器配置客户端，不配置网关。通过网关日志验证实际路由；模型响应成功不证明亲和性或缓存复用。Bifrost 预设针对 API 密钥亲和性，不固定加权目标。原生 xAI gRPC 和订阅不提供此设置。

同一 Thread 的轮次/重试保留值；子级、fork 和独立 Thread 使用各自 ID。header 名称按 Run 捕获。见[共享派生规则](../a13n-harness/models.md#model-request-affinity)。

**升级旧文件：** 省略字段现在关闭旧版隐式 `x-session-id`；显式添加该名称可为后续 Run 保留行为。旧进程级开关不覆盖 Model 配置。自动网关/缓存/原生 Codex 默认值也由原始 Thread ID 改为派生 UUID，已有 Thread 可能一次性失去上游亲和性。本地历史和 ID 不变，文件不改写。

## 订阅登录与 API 密钥

Codex、Grok 或 Copilot 可运行一条登录命令，在浏览器完成打印的设备授权流程：

```console
a13n-harness-ui login codex
```

其他连接将 `codex` 替换为 `grok` 或 `copilot`。设备授权不需要 Host 回调，十五分钟内到期。浏览器能访问 Host 回环回调时，Codex/Grok 可使用 `--browser`；Codex 使用 1455 端口。没有自动方式回退；Copilot 没有浏览器回调模式。[ChatGPT](#chatgpt-subscription) 使用另一种回调流程。

用 `a13n-harness-ui auth status` 检查登录状态。替换所选账户的其他身份需要 CLI `--allow-account-switch`。退出或替换前先检查选中的账户。

API 访问可通过隐藏输入保存命名密钥：

```console
a13n-harness-ui auth key set key-primary
a13n-harness-ui auth key list
```

然后在 Model 中使用 `credential_ref: key-primary`。向导也接受 `key:key-primary` 或 `env:OPENAI_API_KEY`。新输入密钥立即以新引用保存。不要将密钥放入对话。`auth key delete key-primary` 删除该密钥；先修改受影响的 Model 引用。

密钥以明文保存在私有权限的 `<data-root>/auth.json`。捕获保存引用，不保存密钥字节。保护 Host 和备份。取消 Model 创建后，已完成密钥保存/登录仍保留。

### 账户存储位置

使用 **Settings → Accounts & API keys** 添加、登录、重命名、暂停和分组 Codex/Grok 账户。**Ordered consumption** 按成员顺序使用；**Weighted balance** 按正整数权重分配 Run。在 **Settings → Models** 选择生成的组。

使用文件配置时，从账户页面复制账户 ID 并保存：

```yaml title="subscription_groups/work.yaml — complete file"
schema_version: "1"
kind: subscription_group
id: codex-work
name: Work accounts
provider: codex
strategy: weighted
allow_credits: false
accounts:
  - account: acct-work
    weight: 3
  - account: acct-personal
    weight: 1
```

将组选择合并到订阅 Model，验证后用于新 Run：

```yaml title="Model file — select the account group"
authentication:
  kind: codex_subscription
  group: codex-work
```

Run 捕获成员/策略，每次请求前检查账户暂停、删除、退出和凭据。优先使用套餐内额度；充值余额需要 **Allow rechargeable credits**。快照可能落后于并发消耗，因此不是严格支出上限。认证/额度失败可在响应交付前换号；不重放工具或可见流。**MAIN** 显示实际根账户和原因，辅助调用不替换它。Live 在连接时固定一个可用账户。

凭据位于数据根下私有权限的明文 `oauth/accounts/<account-id>.json`。`oauth/accounts.json` 是非敏感目录；`oauth/state/` 保存用量快照。升级后首次启动可从 `CODEX_HOME` 或 `GROK_AUTH_PATH`/`GROK_HOME` 一次性导入支持的凭据。原文件不变，后续操作使用原生文件，不再导入。复制的刷新授权共享：任一客户端轮换可使另一方失效，需分别重新登录。不支持的导入显示在 **Migration from existing CLI logins**。

省略组选择 `codex-default` 或 `grok-default`，初始包含迁移账户。删除默认组会被尊重；恢复该组或选择其他组。

**刷新额度。** Codex 响应立即更新报告的窗口。请求前刷新至少 60 秒前的用量 API 观测；空闲账户每十分钟刷新。重置日期不安排轮询。**Refresh** 或 `/usage accounts` 立即刷新，`/usage subscription` 查看缓存摘要。失败保留最后观测及时间/错误。共享数据根的 TUI 最终共享这些观测。

卡片显示提供方各自额度：Codex 时长窗口、模型/月度限制、兑换券和充值余额；Grok 计费额度及美元购买额度；Copilot 返回的额度类别；ChatGPT 的官方用量设置链接，而非数值额度。缺失/失败数据表示不可用，不是零。Copilot 在进入时及每十分钟刷新，也提供 **Refresh allowance**。兑换券详情失败保留已知数量。**Reset voucher…** 或 `/usage reset` 需要账户、兑换券及明确确认；自动刷新/换号不兑换。

## ChatGPT 订阅

1. 在 Add Model 选择 **ChatGPT subscription**，或运行 `a13n-harness-ui login chatgpt`。
2. 打开登录链接并授权。CLI 中将**完整回调 URL**粘贴到隐藏输入，即使浏览器回环页面无法加载也可以。WebUI 先自动尝试回调；远程 Host 可展开 **Callback cannot reach this server?** 粘贴。
3. 使用发现的账户模型 slug 保存 Model，再选中它并测试提示。

```yaml title="models/chatgpt.yaml — complete file; replace the slug"
schema_version: "1"
kind: model
id: model-chatgpt
name: ChatGPT Subscription
route: openai-chatgpt:YOUR_ACCOUNT_MODEL_SLUG
authentication:
  kind: chatgpt_subscription
settings: {}
model_configuration: {}
```

回调含一次性授权码，不要放进对话或共享诊断。URL 被校验并用于交换，不作为 URL 抓取。无效 URL 可修正，交换失败需重新登录。这不是设备码流程。

原生传输通过 Responses 发送完整历史/developer 指令，使用 `store: false` 和 `stream: true`，普通调用也收集必需流。其 profile 省略通用 temperature、Top P 和输出 token 限制。不支持静态 API 密钥或端点覆盖。账户目录可见后仍需测试推理。

凭据保存在 Host `auth.json` 独立命名空间，不用 Codex 存储。`auth logout chatgpt` 删除本地 token，保留注册；远程授权在账户设置管理。活动 Model 可保留缓存 token。到期/401 刷新保存完整凭据集，无跨进程刷新互斥。替换 Host 账户影响其所有用户。

## GitHub Copilot 订阅

选择 **GitHub Copilot subscription**，通过设备登录授权，再选择 **Fetch account models** 或手工填 ID。发现列出已认证的 Chat Completions 模型，不进行推理。原生登录不需要安装 Copilot CLI、注册应用或 client secret。请检查授权页请求的 `read:user`、`read:org`、`repo` 和 `gist` 权限。

```yaml title="models/copilot.yaml — complete file; replace the model ID"
schema_version: "1"
kind: model
id: model-copilot
name: Copilot
route: github-copilot:YOUR_MODEL_ID
authentication:
  kind: copilot_subscription
settings: {}
model_configuration: {}
```

选中已保存 Model 并发送提示来验证实际访问。Copilot 使用原生 profile，不采用 Codex Fast/上下文/工具预设或自动选择的审查器。设置、登录、保存及空对话不发出推理。

原生凭据及选中账户/来源位于私有权限的明文 `<data-root>/oauth/copilot.json`。**Choose account source** 可改为复用官方 GitHub.com CLI 文件，但要求 `$COPILOT_HOME/settings.json` 显式开启 `storeTokenPlaintext: true`；默认目录 `~/.copilot`。从 `config.json` 读取支持的旧字符串/对象 token 格式及 snake_case 别名，不复制凭据。兼容测试使用合成 CLI 1.0.88 Linux 文件；OS keychain 存储不回退到旧文件。优先原生登录，不要降低 CLI 存储保护。

```console
a13n-harness-ui auth sources copilot
a13n-harness-ui auth select copilot --source native --account YOUR_LOGIN
a13n-harness-ui auth status copilot --format json
```

用 `--source copilot_cli_file` 显式选择文件来源。凭据缺失/过期不会自动选择其他账户。

> [!WARNING]
> `a13n-harness-ui auth logout copilot` 删除选中的凭据。来源为 CLI 文件时，也会影响官方 CLI 和使用该文件的其他 Host。其他账户保留；需恢复选中凭据或选择其他来源。退出不撤销 GitHub 授权，保留文件无关数据，但将注释/格式规范化为 JSON。

## API 上下文默认值与名称

新 API 连接采用 **350,000 token** 工作预算，或更小的已知目录窗口。未知 ID 也以 350,000 作为本地默认值。可输入 `128000` 或 `128k` 等正数，并保持在端点实际容量内。

保存的提醒和压缩比例为 65% 和 90%：350k 时为 227,500 和 315,000 token。依据最近根请求占用，不是累计用量，使用标准 Harness 摘要提示。复用 Model 保留其策略。

生成名称描述连接和模型；Agent 建议添加 **- Coding**。add 命令避免名称冲突，允许自定义 Unicode 名称。名称是标签，引用仍用 ID，不重命名已有资源。

## Codex 推理与上下文

高级设置提供本地预算 **standard: 272,000**、**balanced: 350,000**（默认）和 **extended: 872,000**。选择端点/账户支持的预算。它控制提醒/压缩时机，不提高提供方限制。

`/thinking` 显示适配器支持的强度、token 预算或 Off。`/thinking default` 恢复 Model 配置。推理强度与显示细节独立。状态行报告请求设置；只展示提供方公开的推理内容。

Codex 移除 `max_tokens` 等不支持的通用设置，强制 `openai_store: false`。其他显式 `openai_*` 字段遵循原生验证。API 输出预算预设不用于 Codex 订阅创建。

## Pro 推理模式

支持的 Responses/Codex Model 中，`/pro on` 请求 Pro，`/pro off` 请求 Standard，**不是关闭推理**。`/pro reset` 恢复 Model 配置模式，该模式可能也是 Pro。未设置表示 Provider default。WebUI 在 **Agent & Model settings** 提供同样独立控件。

永久选择可合并到选中 Model：

```yaml title="Model file — reasoning mode"
settings:
  openai_reasoning_mode: pro
  thinking: high
  openai_reasoning_summary: detailed
```

使用 `standard` 选择 Standard，移除模式字段采用提供方默认。模式、强度、摘要偏好和 Fast 互相独立。临时模式在 `/new` 和进程内 `/resume` 后保留，选择 Agent/Model 后清除，新 TUI 不从历史恢复。不支持的连接或冲突 `extra_body.reasoning` 会拒绝显式选择。适配器支持不等于账户权限；捕获的 Run 保留原设置。

## Fast 模式与服务层级

新 Codex 设置默认 **Fast**，保存 `openai_service_tier: priority`；**Standard** 保存 `default`。复用已有 Model 保留层级。

### 临时设置：使用 `/fast`

| TUI 命令          | 请求层级                         |
| ----------------- | -------------------------------- |
| `/fast`           | 切换实际优先处理                 |
| `/fast on`        | `priority`                       |
| `/fast off`       | `default`                        |
| `/fast ultrafast` | Codex GPT-6 Astra 的 `ultrafast` |
| `/fast reset`     | 继承 Model 文件                  |

文件指定 priority 时，Off 与 reset 不同。覆盖跨 `/new` 和 `/resume` 保留，直到退出或选择 Agent/Model。推理不变，继承的 Markdown 子级受影响，独立/辅助 Model 不变。YAML 和已保存 Thread 配置不改动。

Fast 是请求设置，不是速度保证。状态显示请求层级，提供方支持、额度和费用决定实际服务。不支持层级按原生行为处理，不静默换层级重试。

### Codex GPT-6 Astra 的 Ultrafast

路由 `openai-codex:gpt-6-astra` 可使用 `/fast ultrafast`，或 WebUI Agent & Model 设置中 **Fast** 旁的 **Ultrafast**。按钮互斥，点击已选按钮请求 Standard，**Use default** 恢复文件值。HTTP 操作使用 `fast: "ultrafast"`；布尔/null 保留原含义。

Harness UI 将此控件限制到支持的连接，不检查账户资格。选择前向提供方确认访问和用量条款。请求不改变推理强度或模式。永久设置可在该 Model 保存 `settings.openai_service_tier: ultrafast`。

### 永久设置：编辑 Model

将层级合并到选中 Model，保留其他设置：

```yaml title="Model file — processing tier"
settings:
  openai_service_tier: priority
```

验证，用 `/fast reset` 清除临时覆盖，开始新 Run。`default` 请求 Standard，移除层级字段使用提供方默认。通用 `service_tier` 仍是别名，同时存在时 OpenAI 专用字段优先。只保留一个字段。其他提供方使用原生/通用字段；`auto`/`flex` 依赖适配器支持。

## 原生媒体输入

新 Model 创建从内置目录保存已知输入声明。未知 ID 不猜测媒体能力。确切已知 ID 在自定义端点后也可能获得同样默认值，因此需验证端点支持。已有文件不自动补齐。

为已有 Model 开启受支持图像输入，可合并：

```yaml title="Model file — image input declaration"
model_characteristics:
  capabilities: [image_understanding]
```

保留列表中其他所需声明。验证、开始新 Run，请 Agent 检查一张小图。这些是 **Model 输入**能力，与 Agent 工具/Capability 和输出模态不同。

已知原生 Google 连接还可声明音频/视频及 YouTube 输入。端点支持时：

```yaml title="Model file — native video and audio"
model_characteristics:
  capabilities: [image_understanding, audio_understanding, video_understanding]
  url_input:
    video: [youtube]
  video_input:
    max_video_bytes: 10485760
```

`view` 和 `read_media_url` 优先使用原生输入，再使用[配置的理解 Model](#media-understanding-defaults)。直接媒体 URL 下载有界二进制输入；粘贴的链接仍是文本。YouTube 保持原生 URL，不支持时无下载回退。URL 声明不需要单独 Agent Capability/reader。

原生视频默认编码后单个/总量预算为 10 MiB，覆盖本地 `view` 和直接提供视频。不会自动压缩、切分、提取帧或上传。提供方 413/明确 payload 大小错误可移除内联图像/视频并重试一次；认证、权限、MIME、限流和下载错误保留各自处理。请求过滤/恢复保留原历史。

## 媒体理解默认值

1. 创建声明所需图像/视频/音频输入能力的已保存 Model。
2. 打开 **Settings → Models → Media understanding**，选择每种回退并保存。**Set as…** 菜单或 TUI `/model defaults` 编辑同一全局选择。
3. 使用没有对应原生输入能力的主 Model，开始新 Run，请它检查媒体文件。

这些引用放在根文件，与前面的 Model 示例不同：

```yaml title="a13n-harness-ui.yaml — merge global fallbacks"
media_understanding:
  image: model-vision
  video: model-video
  audio: null
```

替换为已有 ID。每个 Model 必须声明匹配输入能力；删除 Model 或声明前先移除引用。选择与主 Model/记住的 Project Model 分开。

原生支持的主 Model 直接收到媒体，不初始化辅助 Model。否则所选 Model 使用自己的捕获设置、凭据、输入策略和 Thread 亲和性，返回文本，不继承临时主 Model 推理/Fast 覆盖。省略/null 使用 [Harness 环境变量回退](../a13n-harness/multimedia-understanding.md)；清除选择不关闭该回退。已配置 Model 失败会报告，不静默替换。

保存检查引用/声明，不请求提供方。仅需使用时加载凭据，可能产生费用。捕获/恢复的 Run 和子级保留解析的配置。它控制文件理解，不转换不支持的输入框附件，也不强制原生输入经过代理。

## 入门模型选择

已安装发行版维护 setup 和 Add Model 的建议。选择提供的路由，或输入提供方的区分大小写 ID。建议不查询账户权限；API 和订阅访问可能不同。通过第一条提示检查实际连接，不把目录条目当作测试成功。

单独创建的 **Codex shell review** 或 **Grok shell review** Model 负责审查，无需独立 Agent。设置保留用户编辑的审查资源。API 设置仅在选择审查器时开启快捷配置。见 [shell 审查](configuration-recipes.md#configure-shell-review)。

## API 提供方与设置预设

有原生连接时，选择提供方原生连接。专用 `deepseek:`、`zai:` 和 `moonshotai:` 路由在工具续接和后续轮次保留提供方推理内容；通用 Chat Completions 可能失去该行为。xAI 提供 `grok:` Chat Completions 和带原生工具的 `xai:` gRPC。两种 API 密钥连接均独立于 Grok 订阅登录。

其他兼容 HTTP 服务选择 **OpenAI-compatible · Chat Completions**，再填写 URL 和模型 ID。Cloud IAM 和订阅需要各自传输。Cloudflare AI Gateway 使用以 `/ai` 结尾的账户 API URL、具有 Workers AI Read 权限的 token，以及带厂商前缀的模型 ID。可选 `settings.extra_headers.cf-aig-gateway-id` 选择网关，否则第三方模型使用默认别名。在网关配置 BYOK 或统一计费。该连接提供 Chat Completions，不提供独立 Workers AI 目录/原生服务端工具。

预设在**创建时**写入普通设置。选择器在保存前显示推理和输出上限。Provider defaults 不添加预设输出上限，但原生默认仍可限输出。已有文件和显式设置映射（含空映射）保留。

- **OpenAI Responses：** 已识别推理 profile 提供强度和返回摘要控制；Chat Completions 省略 Responses 专用摘要字段。
- **Anthropic：** 原生 profile 选择自适应、扩展或交错推理。未知 ID 不获得猜测的推理控制。
- **Google：** 强度映射到支持的级别/预算，并请求可用摘要。
- **OpenRouter：** 推理预设用 `exclude: false` 请求返回推理。
- **DeepSeek/GLM/Kimi：** 已识别模型提供 **Thinking · preserved**；GLM 还写入 `zai_clear_thinking: false`。不会为始终推理或采用其他控制的模型假设通用 Off。

返回推理是提供方公开的内容或摘要。原生设置和端点接受情况仍由适配器/提供方负责。

### Anthropic 缓存默认值

Anthropic 创建预设保存显式分块缓存开关，自定义 ID/端点也适用：

```yaml title="Anthropic Model — cache fields saved by presets"
settings:
  anthropic_cache_instructions: true
  anthropic_cache_tool_definitions: true
  anthropic_cache_messages: true
```

该连接预设使用这些字段而非统一缓存控制。显式 false、时长或已编写空设置映射仍优先。其他协议的 Claude 不自动获得这些字段。已有文件需主动编辑或应用预设。

已识别自适应 profile 保存 `anthropic_thinking: {type: adaptive, display: summarized}` 和 high effort。自适应预设省略 interleaved-thinking、context-management 和 1M-context beta header。网关需要 beta 时，显式配置 `settings.anthropic_betas`。始终推理的模型不提供 Off。

### 配套输出预算

部分经审查 API 模型 ID 将推理预设与 `settings.max_tokens` 配对。选择器显示确切值；保存前按端点和预期响应检查。这是**每请求输出上限**，不是 Run 总限制或目标长度。推理可能消耗其中一部分。上下文提醒/压缩独立，不调整上限。

例如，经审查 OpenAI 强度预设对 low/medium/high-or-xhigh 使用 16,384/32,768/65,536；经审查 Claude 自适应预设使用 32,768，显式 8,192 token 扩展推理保留 16,384 输出。Gemini 2.5 high 在 32,768 输出上限内使用 24,576 思考预算。适用范围依确切经审查模型及原生 profile，而非名称前缀；未经审查 Claude 自适应保留 16,384 基线。其他未经审查路由不增加上限。订阅创建不使用 API 预设。

之后可独立编辑 `settings.max_tokens`。`/thinking` 不重算它，加载/复用 Model 也不重新应用预设。自定义端点可能有其他限制。原生序列化决定线上字段；已安装 DeepSeek 适配器发送 `max_completion_tokens`，本地模拟响应测试未建立其真实接受情况。依赖该上限前需验证实际端点行为。
