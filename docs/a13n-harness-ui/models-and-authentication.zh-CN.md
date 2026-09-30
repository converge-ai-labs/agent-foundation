---
title: 模型与身份验证
description: 创建 Model，通过订阅或 API 密钥登录，并设置上下文预算。
---

Model 资源定义提供方连接、请求设置和上下文预算。将其保存到**所选根配置旁的 `models/<name>.yaml`**，Agent 通过 `id` 引用。凭据独立存储：Model 身份验证只持有引用，不存密钥或 token。

## 按任务查找

| 任务                       | 从这里开始                                                                        |
| -------------------------- | --------------------------------------------------------------------------------- |
| 交互式创建连接             | `a13n-harness-ui add model`                                                       |
| 编写完整的 API 密钥 Model  | [配置用法](configuration-recipes.md#change-the-model-reasoning-or-context-budget) |
| 连接兼容端点               | [自定义端点用法](configuration-recipes.md#connect-an-openai-compatible-endpoint)  |
| 查阅所有 Model 字段        | [Model 文件参考](#model-file-reference)                                           |
| 修改请求参数               | [原生请求设置](#native-request-settings)                                          |
| 调整上下文或开启图像输入   | [上下文与模态策略](#context-and-modality-policy)                                  |
| 使用订阅账户               | [登录](#subscription-login-and-api-keys)、[Codex 示例](#codex-model-example)      |
| 只为当前终端会话切换 Model | [临时选择](#change-agents-during-a-conversation)                                  |

`settings` 控制模型请求。`model_configuration` 控制 `base_url` 等连接设置。`model_characteristics` 控制本地上下文/输入策略。三者不能互换，也都不属于 `a13n-harness-ui.yaml` 根级设置。

## 创建与管理 Model

在浏览器打开 **Settings → Models**，添加、编辑或克隆已保存的 Model。首次设置和 Agent 的 **Add model** 使用相同流程。保存 Model 不改变任何活动 Run。

1. 选择 **Codex subscription**、**Grok subscription** 或 API 连接。订阅保留原生账户存储和传输，不是 API 密钥预设。
2. 连接共享订阅账户、选择已保存 API 密钥、保存新密钥，或指定服务器环境变量名。凭据写入独立进行；取消 Model 草稿不会撤销已完成登录或密钥保存。
3. 选择建议项或输入区分大小写的模型 ID。建议描述已知路由，不代表账户权限；目录获取失败时仍可手动填写 ID。
4. 选择 **Use this model**，调整提供的推理、速度和工作上下文选项。对已有连接，**Apply connection & defaults** 会有意替换预设设置。
5. 保存 Model YAML。只有已保存 Model 出现在对话选择器中；仍可高级编辑 YAML。

选择已保存 Model 后，在 **Agent** 上选择原生工具。改变 Model 不改写 Agent 工具。提供方资源要求和 YAML 示例见[原生工具](native-and-web-tools.md)。

终端 `setup`、`add model` 和 `add agent` 的新 Model 分支采用相同后端选项和准备规则。API 连接可复用已存密钥元数据或环境变量引用，不暴露密钥字节。订阅账户不可用时，设置提供直接设备/浏览器登录，或显式稍后配置的选项。

## 网关会话亲和性

在 **Add Model** 或首次设置中选择 **Gateway session affinity** 预设，或在 **Session affinity header** 中填写其他名称。CLI `add model` 提供同样预设和自定义输入。保存结果是普通 header 名称，不是预设引用：

```yaml
model_configuration:
  base_url: https://gateway.example.com/v1
  session_affinity_header: x-litellm-session-id
```

在 Model 文件中与 `base_url` 同级设置，**不要** 放在 `settings.extra_headers` 或根进程配置中。无需提供会话值：系统按[共享 Harness 派生规则](../a13n-harness/models.md#automatic-model-request-affinity)，自动从当前 Thread ID 生成稳定 UUID v5。派生值不存入 Model 配置或 Thread 状态。省略或设为 `null` 可关闭。替换预设只需改变名称，例如 `x-company-session`。

| 预设                          | Header 名称            | 网关前置条件 / 边界                                 |
| ----------------------------- | ---------------------- | --------------------------------------------------- |
| LiteLLM                       | `x-litellm-session-id` | 在网关开启会话亲和性。                              |
| Conversation ID               | `x-conversation-id`    | 配置优先读取此 header 的路由规则。                  |
| Bifrost（API 密钥亲和性）     | `x-bf-session-id`      | 仅提供 API 密钥亲和性，不保证加权提供方或目标固定。 |
| X-Session-ID（旧版 / 自定义） | `x-session-id`         | 网关明确配置识别此 header 时使用。                  |

预设不会配置或检测网关。发送 header 只是请求亲和性，不能证明目标固定。需要验证时检查网关路由日志或目标标识。连接测试不能证明持久路由或缓存复用。

同一 Thread 的各轮次和重试使用稳定值；独立 Thread、子 Thread 和 fork 使用各自 ID。名称在不可变 Run 配置中捕获。修改 Model 影响新组合，不影响已捕获 Run。这与 OpenAI 提示缓存和 Codex 订阅协议 headers 不同。原生 xAI gRPC 和订阅连接不提供该网关设置。

**亲和性值升级：** 自动网关 headers、提示缓存键和绑定 Codex 原生会话默认值现在使用派生 UUID，不再直接使用 Thread ID。已有 Thread 可能一次性失去上游缓存或路由亲和性；本地 ID 和历史不变，无需迁移。

**升级说明：** 旧版本隐式发送 `x-session-id`。现在省略字段会关闭网关亲和性，已有文件和捕获也如此。要保留行为，添加 `session_affinity_header: x-session-id` 并启动新组合。旧版进程级环境开关不覆盖 Harness UI Model 配置。配置文件绝不会自动改写。

## 订阅登录与 API 密钥

```console
a13n-harness-ui auth status
a13n-harness-ui login codex
a13n-harness-ui login grok
a13n-harness-ui login copilot
a13n-harness-ui login codex --browser
a13n-harness-ui auth key list
a13n-harness-ui auth key set key-primary
a13n-harness-ui auth key delete key-primary
```

默认使用设备授权，无须宿主机回调。自行打开打印的 URL。只有浏览器能访问宿主机回环回调时，才使用 `--browser`；Codex 使用 `http://localhost:1455/auth/callback`。不会自动回退到其他登录方式。授权十五分钟内到期。替换另一个共享账户需通过 CLI 使用 `--allow-account-switch`。

使用 API 时，在首次设置、`a13n-harness-ui add model`，或 `a13n-harness-ui add agent` 的 **Create a new model** 分支选择 **API key** 。选择提供方/协议，确认或修改基础 URL，在不回显凭据框输入密钥，选择提供方模型建议（或手动输入区分大小写的 ID），选择设置预设，并检查工作上下文预算。也可用 `key:key-primary` 引用已存密钥，或 `env:OPENAI_API_KEY` 引用 Harness UI 进程可用的环境变量。新密钥立即以新引用保存在本地密钥存储中，与配置发布独立。不要将 API 密钥粘贴到普通输入框。

已存 API 密钥以明文保存于数据根的独立 `auth.json`，文件权限为私有。保护宿主机和备份。配置和 Run 快照只含引用，不含密钥字节。凭据保存/登录与设置发布独立，取消设置不会撤销。

## GitHub Copilot 订阅

在初始引导、Add Model 或 Add Agent 新 Model 分支中选择 **GitHub Copilot subscription**。可选择已有 Model，不必修改或重新登录。身份验证尚未就绪也可保存配置，但首次请求前需要凭据。取消 Agent 创建不会删除独立保存的内联 Model。

原生登录使用官方 Copilot CLI 公共 App ID 的设备授权，无须应用注册、client secret、已安装 CLI 或 Copilot SDK Agent 循环。历史 CLI 权限基线请求 `read:user`、`read:org`、`repo` 和 `gist`；请在 GitHub 授权页检查较广的仓库权限。这里不宣称最小权限。Copilot 不支持浏览器回调登录。

同级 `settings.json` 显式设置 `storeTokenPlaintext: true` 时，Harness UI 可复用 `$COPILOT_HOME/config.json`（默认 `~/.copilot/config.json`）中选中的官方 CLI GitHub.com 账户。支持经审查的旧 token 字符串、`{token: ...}` 文件形式及 snake_case 别名，不会复制 token 到 Harness UI。兼容性用合成 CLI 1.0.88 Linux 文件检查。当前 OS keychain 存储不受支持；Harness UI 不会回退到可能过时的文件。建议使用原生登录，不要仅为此集成降低 CLI 存储策略。

原生凭据以明文保存于 `<data-root>/oauth/copilot.json`，以私有文件权限保护，且与 API 密钥 `auth.json` 分开。文件还记住选中账户和来源。凭据缺失或过期不会自动选择其他已存账户。**Choose account source** 显式改变绑定。终端提供同样操作：

```console
a13n-harness-ui auth sources copilot
a13n-harness-ui auth select copilot --source copilot_cli_file --account YOUR_LOGIN
a13n-harness-ui auth select copilot --source native --account YOUR_LOGIN
a13n-harness-ui auth status copilot --format json
a13n-harness-ui auth logout copilot
```

> [!WARNING]
> **退出登录会删除选中的凭据。** 来源为 CLI 文件时，这也影响官方 CLI 和使用该账户文件的其他 Host。其他账户保留；记住的选择会保持缺失，直到在原位置恢复凭据或显式选择其他来源。它不会撤销 GitHub 授权。CLI 文件退出保留无关数据，但会将 JSON 注释/格式规范化为 JSON。

连接后手动填写模型 ID，或显式选择 **Fetch account models**。这调用经身份验证的目录，不进行推理，只列出声明支持 Chat Completions 的模型，与公共 API 模型目录分开。设置、登录、保存 Model 和打开空对话不发出推理请求。目录存在和授权成功不能证明访问：订阅层级、组织策略、端点支持和可用性仍适用。此集成未通过真实订阅权限/推理验证。

Copilot 不继承 Codex Fast 模式、上下文预算、原生工具预设，也不会猜测更便宜的审查模型。以上游原生模型配置为准。最小手工配置为：

```yaml
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

## 原生媒体输入

设置、`add model` 和使用新连接的 `add agent` 自动保存已知模型输入能力。最终摘要显示选中的原生媒体类型，无须单独询问。默认值来自随包提供、经审查的模型目录，不是网络探测；描述模型能力，不代表账户权限或模型持续可用。

例如已知支持图像的 OpenAI、Claude 或 Grok Model 包含：

```yaml
model_characteristics:
  capabilities: [image_understanding]
  context_window: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

原生 Google API 的已知 Gemini Model 还可包含 `audio_understanding` 和 `video_understanding`。默认值遵循所选协议：兼容路由支持图像，不代表仅凭上游模型在其他 API 支持音频/视频就自动获得这些输入能力。

能力与 Agent 工具和模型输出模态分开。尤其是文件 `view` 工具用这些声明直接向活动模型附加媒体。没有匹配能力时，需要显式配置[媒体理解回退](../a13n-harness/multimedia-understanding.md)，否则报告不可用。

未知模型 ID 标为未知，不自动开启媒体。确切已知 ID 在自定义基础 URL 后仍获得入门默认值；需验证端点支持声明的输入。可在 Model 文件中编辑 `model_characteristics.capabilities`。直接设置 API 调用方可显式提供能力（含 `[]`）覆盖默认值，不改变上下文策略。

绝不会自动为已有 Model 文件补齐字段。如果旧设置为支持图像的模型生成了 `capabilities: []`，检查所选模型和端点后，仅将此字段改为 `[image_understanding]`。后续 Run 使用已接受配置；活动 Run 和历史捕获不变。添加复用 Model 的 Agent 时，完整保留该 Model。

## 媒体理解默认值

在 **Settings → Models → Media understanding** 选择已保存 Model 用于 Image、Video 或 Audio，再保存。Model 的 **Set as…** 菜单设置或清除相同默认值。TUI 中用 `/model defaults`（也可从 `/model` 进入），选择用途和 Model，再确认 **Save** 。这些是全局设置，与对话主 Model 和 TUI 记住的 Project Model 分开。

```yaml
# Root configuration, including when selected with --config
media_understanding:
  image: model-vision
  video: model-video
  audio: null
```

每个引用必须存在，并声明对应 `image_understanding`、`video_understanding` 或 `audio_understanding` 能力。不支持的 Model 不能选作该用途。删除 Model 或移除必需能力前，先删除引用。

文件 `view` 工具仍优先使用原生能力：

1. 活动 Model 声明媒体能力时，Harness 直接附加媒体，不初始化辅助 Model 或凭据。
2. 否则，该媒体类型配置的 Model 描述或转写文件，返回文本。使用其自己的请求设置、连接配置、凭据引用和 Thread 亲和性；不继承主 Model 设置或临时 `/thinking`、`/fast` 覆盖。
3. 省略或 `null` 引用使用该类型现有的 Harness 环境回退。**Environment** 表示对应模型环境变量已设置；**Not configured** 表示未设置。清除默认值不关闭环境回退。变量名和设置见 [Harness 多媒体配置](../a13n-harness/multimedia-understanding.md)。

已配置 Model 失败会报告，不静默替换为环境 Model。保存验证引用和能力声明，不发出提供方请求，也不证明账户访问。只有需要辅助推理时才加载凭据。可能产生提供方费用。

新 Run 捕获所选 Model 完整配置，不含密钥字节。编辑影响未来捕获，不影响已捕获 Run 或恢复续接。子级 Run 使用相同配置版本和捕获规则。此设置控制文件 `view` 回退，不转换不受支持的输入框附件，也不会强制支持原生媒体的 Model 使用代理。

## 入门模型选择

首次设置和新 Model 创建提供以下显式订阅路由：

| 提供方 | 模型                       | 适用场景                        |
| ------ | -------------------------- | ------------------------------- |
| Codex  | `gpt-6.1-sol`              | 默认；擅长编程和推理            |
| Codex  | `gpt-6-astra`              | 要求最高的端到端推理和编程      |
| Codex  | `gpt-5.6-terra`            | 模型成本较低的日常工作          |
| Codex  | `gpt-6-sol`                | 上一代；编程和推理              |
| Codex  | `gpt-5.6-sol`              | 更早一代；编程和推理            |
| Grok   | `grok-4.7`                 | 默认；当前编程与 agent 任务模型 |
| Grok   | `grok-4.5`                 | 上一代，推理可配置              |
| Grok   | `grok-4.20-0309-reasoning` | 更早的长上下文推理模型          |

Codex 入门选择由发行版维护。Grok 选择在 2026 年 9 月 7 日依据官方 [xAI 发行说明](https://docs.x.ai/developers/release-notes)和 [Grok 4.20 模型页](https://docs.x.ai/developers/models/grok-4.20-beta-0309-reasoning)审查。Grok 4.7 默认值在 2026 年 9 月 22 日依据官方 [Grok 4.7 指南](https://docs.x.ai/developers/grok-4-7)审查。这些选项不查询权限，也不保证所有订阅账户能访问每个模型。Grok 选项代表模型代际，不是三个经过验证的订阅价格层级。API 密钥设置提供提供方建议和自定义 ID。列表按终端可用空间展开，随聚焦选项滚动。建议是随包提供的入门选择，不是实时可用性检查。

Codex 和 OpenAI API 建议使用同样的首选顺序：GPT-6.1 Sol、GPT-6 Astra、GPT-5.6 Terra、GPT-6 Sol，再到 GPT-5.6 Sol。GPT-6.1 Sol 预设写入原生 `openai_reasoning_effort`，因为内置运行时配置尚未识别该 ID。其控件提供 low、medium、high、xhigh 和 max，不提供 Off，与[官方模型参考](https://developers.openai.com/api/docs/models/gpt-6.1-sol)一致。agent 工具调用应使用 OpenAI Responses；该模型的 Chat Completions 端点不支持工具调用。GPT-6 Sol 继续使用内置推理配置。内置模型目录尚未声明这些 ID 的媒体能力；需要时应显式配置支持的媒体能力。已有 Model 不迁移。

辅助 Model 命名为 **Codex shell review** 或 **Grok shell review** ，不能作为根 Agent 选择。Codex 审查使用低推理的 Luna；Grok 审查使用低推理的 4.7。用户编辑过的审查资源保留。

## API 上下文默认值与名称

API 设置推荐 **350,000 token**；内置目录的模型上下文窗口更小时采用该值。未知模型也默认 350,000；提示明确说明这是本地工作默认值，不是经验证的提供方上限。输入 `128000` 或 `128k` 等正 token 数可覆盖。端点或账户要求更低时应降低预算。

新 API Model 使用与 Codex 相同的原生默认值：**65%** 时提醒生成摘要， **90%** 时自动压缩，使用标准 Harness 摘要提示。350k 时阈值为 **227,500** 和 **315,000** token。设置保存在 `model_characteristics`，在 Run 捕获/重建后保留；复用已有 Model 不改变它们。目录随包提供，设置不发出模型发现请求。

生成名称用普通文字标识连接，例如 **OpenAI - GPT-5.6 Sol**、**Z.AI - GLM 5.3** 或 **Moonshot AI - Kimi K2.6** 。默认 Agent 名称追加 **- Coding**。建议和已保存名称使用同一规则，不受终端标签、颜色或状态指示影响。add 命令建议不冲突的名称，也允许覆盖；自定义 Agent 名不会移除其新 Model 的描述性连接名。配置保持 UTF-8，自定义名可含 Unicode。不会重命名已有资源。

## Codex 推理与上下文

默认值是发行版建议，不代表每个账户支持全部模型或上下文大小。

| 高级设置选项 | 工作上下文预算 | 何时选择                                         |
| ------------ | -------------: | ------------------------------------------------ |
| standard     |        272,000 | 保守本地预算，与当前 Codex 目录默认值一致        |
| balanced     |        350,000 | 仓库工作的默认选择                               |
| extended     |        872,000 | 账户支持目录最大值的大型任务；预计延迟和用量更高 |

**工作预算** 控制本地提醒和压缩，不提高提供方上限或授予访问。默认提醒阈值 65%，自动压缩从 90% 开始，依据最近报告的根请求占用，不依据累计 token。350k 时分别为 227,500 和 315,000 token。

用 `/thinking` 查看所选 Model 和已安装适配器支持的选项。菜单可能提供强度级别、显式 token 预算预设或 Off，不是通用固定列表。`/thinking default` 返回所选 Model 的配置，包括提供方原生推理字段。状态行描述请求设置，不是测量的提供方结果。高推理与详细显示独立，可以高推理、简洁输出。只显示提供方公开的推理内容，部分提供方不返回这些内容。

Codex 订阅请求**不会** 使用 API 设置预设的输出 token 上限。Codex 适配器移除 `max_tokens` 等不支持的通用设置；强制 `openai_store` 为 false。其他显式 `openai_*` 设置遵循上游验证，不经过另一套 Harness 过滤。

## Pro 推理模式

在支持的 OpenAI Responses 和 Codex Model 上，`/pro` 为后续 Run 切换请求的推理模式。`/pro on` 选择 Pro；`/pro off` 选择 Standard，**不是** 关闭推理；`/pro reset` 继承所选 Model 配置。Model 已配置 Pro 时，重置回到 Pro。未配置显示为 Provider default，不是 Standard。

模式与 `/thinking` 强度、`/fast` 处理和 `openai_reasoning_summary`（提供方公开摘要偏好）独立。覆盖在 `/new` 和进程内 `/resume` 后保留，但改变 Agent 或 Model 会清除，新 TUI 进程不会从历史还原。不支持连接或冲突的 `extra_body.reasoning` 控件会拒绝显式选择，不静默忽略。可用性来自已安装 SDK 配置，不是账户访问检查。

要设置永久默认值，编辑选中 Model：

```yaml
settings:
  openai_reasoning_mode: pro  # or standard; remove for provider default
  thinking: high
  openai_reasoning_summary: detailed
```

WebUI 在 Agent & Model 设置中提供同样独立的 Reasoning mode 控件，显式 Default 选项显示继承目标。Model 资源编辑器保存永久 Standard/Pro 选择；Provider default 移除原生字段。修改不会重新标记已捕获 Run。Pro 访问、用量和延迟取决于提供方；选择 Pro 不保证有权限。

## Fast 模式与服务层级

Codex 引导在选择模型后增加 **Fast / Standard**，默认 **Fast** 。Fast 保存 `settings.openai_service_tier: priority`；Standard 保存 `settings.openai_service_tier: default`。使用 `add model` 或 `add agent` 创建新 Codex Model 时也提供该选项。复用 Model 或加载已有配置不改变层级。

### 临时设置：使用 `/fast`

空闲 TUI 中，以下命令影响后续 Run，不改写 YAML 或已保存 Thread 配置：

| 命令              | 请求设置                                       |
| ----------------- | ---------------------------------------------- |
| `/fast`           | 开启/关闭实际优先处理                          |
| `/fast on`        | `service_tier: priority`                       |
| `/fast off`       | `service_tier: default`                        |
| `/fast ultrafast` | Codex GPT-6 Astra 的 `service_tier: ultrafast` |
| `/fast reset`     | 移除覆盖，继承 Model 配置                      |

**Off 不等于重置：** Model 配置为 priority 时，`/fast off` 请求标准服务，`/fast reset` 则回到 Fast。覆盖在当前 TUI 进程内持续，包括 `/new` 和 `/resume`。选择 Model 或 Agent 会清除；重启不从历史还原层级覆盖。`/thinking` 独立修改推理，保留层级。

这是通用 Model 设置，不只适用于 Codex。影响根 Model 和继承它的 Markdown 子级，不影响显式配置的子级或辅助 Model。状态栏 **Fast** 和 `/status` 请求层级描述实际请求，不证明提供方兑现了优先服务。支持因提供方/模型/账户而异；priority 可能消耗更多配额或费用，也不保证速度。不支持设置保留原生集成行为；Harness UI 不会静默换层级重试。

### Codex GPT-6 Astra 的 Ultrafast

选择路由为 `openai-codex:gpt-6-astra` 的 Model。在 WebUI 打开 **Agent & Model settings**，选择 **Fast** 旁的 **Ultrafast** 。两按钮互斥；点击已选按钮请求标准处理。**Use default** 恢复 Model 配置层级。HTTP 操作字段为 `fast: "ultrafast"`；已有 `true`、`false` 和 null 含义不变。TUI 对应 `/fast ultrafast`。

OpenAI 当前要求 Pro $500 或符合条件的 Enterprise/Edu 工作区才能使用 Codex Ultrafast。在其他个人套餐购买额外额度不会解锁。GPT-6 Astra Ultrafast 对套餐内配额按 Standard 的 8 倍消耗，对购买额度按 6 倍；这些是用量倍数，不是端到端速度保证。当前可用性和工作区限制见 [Codex 速度与资格](https://developers.openai.com/codex/agent-configuration/speed)。Harness UI 本地检查 Model 连接，不检查账户权限；访问由 OpenAI 授予。其他模型（包括 Sol）不会通过此控件获得 Ultrafast 覆盖。

Fast、Off 和 Ultrafast 替换实际请求层级，不改变推理强度或模式。状态显示单独记录请求的 **Ultrafast**，与模型默认值和实际服务结果分开。要作为 Astra Model 默认值，在其 YAML 设置 `settings.openai_service_tier: ultrafast`。

### 永久设置：编辑 Model

用 `/config` 定位选中配置目录。OpenAI/Codex 在 `models/<name>.yaml` 中添加或修改此字段，保留其他设置：

```yaml
settings:
  openai_service_tier: priority
```

编写 OpenAI/Codex 配置使用 `openai_service_tier`。通用 `service_tier` 仍支持为兼容别名；两字段都有时，`openai_service_tier` 优先。只保留一项以免值冲突。其他提供方保留原生或通用服务层级设置。

设置为 `default` 可永久采用标准服务，移除已配置层级字段则交由提供方选择。原生集成在支持时也接受通用 `auto` 和 `flex`；Fast 特指 `priority`，不是其他模型或推理级别。用 `a13n-harness-ui config validate` 验证（自定义配置传入同一 `--config`）。`/fast reset` 移除临时覆盖；未来 Run 加载 Model 当前文件设置。

## Codex Model 示例

保存为根配置旁的 `models/codex.yaml`。

`models/codex.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex - GPT-6.1 Sol
route: openai-codex:gpt-6.1-sol
authentication:
  kind: codex_subscription
settings:
  openai_reasoning_effort: high
  openai_reasoning_summary: detailed
  openai_store: false
model_characteristics:
  context_window: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

## Model 文件参考

每个文件使用 `schema_version: "1"`、`kind: model`、唯一的 `model-` `id` 和易读 `name`。

| 字段                    | 默认值 | 含义                                                                             |
| ----------------------- | ------ | -------------------------------------------------------------------------------- |
| `route`                 | 必需   | 支持的提供方/模型路由，如 `openai-responses:gpt-5` 或 `openai-codex:gpt-6.1-sol` |
| `authentication`        | 必需   | 下列显式身份验证形式之一                                                         |
| `settings`              | `{}`   | 传入 Harness 和模型适配器的原生请求设置                                          |
| `model_configuration`   | `{}`   | 支持的 HTTP/API 密钥提供方可选 `base_url`；订阅为空                              |
| `model_characteristics` | `null` | 可选原生 Harness 上下文/能力策略                                                 |

身份验证恰好接受一种形式：

```yaml
# Environment variable, read at native use:
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

```yaml
# Previously saved with `a13n-harness-ui auth key set key-primary`:
authentication:
  kind: api_key
  credential_ref: key-primary
```

```yaml
# Compatible account store, no literal token:
authentication:
  kind: codex_subscription
```

Grok 使用 `kind: grok_subscription` 和兼容 `grok:` 路由。Copilot 使用 `kind: copilot_subscription` 和 `github-copilot:`。API 密钥身份验证要求 `env` 与 `credential_ref` 恰好有一项。订阅类型必须与路由匹配，不会静默回退到其他提供方凭据。

### 上下文与模态策略

在 `model_characteristics` 中：

| 字段                                     | 提供对象时的默认值 | 含义                                                                                                                                   |
| ---------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `capabilities`                           | `[]`               | 可选原生策略：`image_understanding`、`video_understanding`、`audio_understanding`；接受 `document_understanding`，但 Harness UI 不使用 |
| `context_window_tokens`                  | `null`             | 正数工作上下文预算；省略保留原生/目录行为                                                                                              |
| `proactive_context_management_threshold` | `0.65`             | 0–1 比例，或 `null` 关闭派生的主动阈值                                                                                                 |
| `compact_threshold`                      | `0.90`             | 大于 0 且不超过 1 的比例                                                                                                               |

Harness UI 在配置和已保存快照中接受旧名称 `context_window`。新序列化和编辑器保存使用 `context_window_tokens`；两者都提供时值必须一致。读取已有文件或对象不改写它们。核心 Harness Agent spec 要求规范名称。

这些值指导 Harness 行为，不能让模型获得其本身缺少的模态或 token 权限。Agent 级显式上下文能力阈值仍优先。改变通用示例前，检查所选提供方支持的设置。

### 账户存储位置

Codex 在 `CODEX_HOME`（默认 `~/.codex`）下共享受支持的文件存储。Harness UI 遵循上游凭据存储策略，对不支持存储报告错误，不替换。Grok 优先使用 `GROK_AUTH_PATH`，再用 `GROK_HOME` 或默认文件；内联 `GROK_AUTH` 不是共享可写登录模式。账户检查不登录或刷新凭据。Codex 模型请求使用显式共享存储凭据来源。provider 在自身生命周期内缓存凭据，并在刷新前重读存储，不是每次请求都读。新 Run 或账户操作获得新 provider。刷新凭据无法保存时，请求失败，但 provider 在内存保留轮换凭据；应解决存储冲突并启动新 Run，不要假定轮换已持久化。Grok 保留由 Harness 负责的刷新生命周期。

官方 Codex provider 没有等效功能时，Harness 保留设备登录、Thread 亲和性、路由提示和每 Run 的轮次状态。浏览器 PKCE 和回调使用官方流程；小型登录交换适配器保留原生 Codex `auth.json` 要求的真实 ID token。新登录和账户切换写入该 token，同账户刷新保留它。不会创建第二个 Codex 订阅存储。

```console
a13n-harness-ui auth status codex --format json
a13n-harness-ui auth logout codex
a13n-harness-ui login codex --allow-account-switch
```

退出和账户替换是显式凭据修改；先确认正在改变哪个共享账户/存储。不要将账户文件、token 或已存密钥文件放入诊断报告。

## 在对话中切换 Agent

`/agent` 列出已配置 Agent 和模型路由。`/agent agent-primary` 为下一操作切换完整 Agent 配置，并重置会话推理。`/model` 列出已配置 Model；`/model <model-id>` 只覆盖模型，并按 Project 记住选择，不改变 Agent。`/model default` 清除覆盖和 Project 偏好。该偏好在对话导航、Agent 选择和 TUI 重启后保留。显式启动 `--agent` 跳过偏好；无界面运行和 API 调用方不继承。恢复与重置见[日常模型选择](everyday-use.md#everyday-interaction)。历史和 Environment 选择保留，不改写 YAML。用 `a13n-harness-ui add agent` 添加其他使用模型的 Agent，再切换。

`/thinking low` 不编辑文件即可修改推理；`/thinking default` 返回实际 Model 配置。正在执行的操作保留捕获值。继承的 Markdown 子级获得父级实际配置；独立引用的 Agent 保留自己的 Model。

`/status` 显示根级观测用量，Codex 还提供只读订阅限制。`/usage reset` 独立打开需明确确认的额度兑换。本地观测费用是估计，不是订阅账单。见[用量与额度确认](everyday-use.md#tasks-usage-and-terminal-feedback)。

## API 提供方与设置预设

引导式 HTTP/API 密钥目录包含 OpenAI Responses、OpenAI 兼容 Chat Completions、Anthropic、Google Gemini API、OpenRouter、DeepSeek、Z.AI / GLM、Moonshot AI / Kimi、Groq、Mistral、Together AI 和 Fireworks AI 集成。xAI 有两种独立 API 密钥选项：`grok:` 使用 Chat Completions，`xai:` 使用原生 SDK 默认 gRPC 端点，提供原生 X Search 和其他 xAI 工具。原生 SDK 跳过 HTTP 基础 URL 步骤。两者都不同于 Grok 订阅身份验证。其他 OpenAI 兼容服务，选择 **OpenAI-compatible · Chat Completions**，填写服务 URL 和模型 ID。Cloud IAM 和订阅传输不是通用 URL/密钥连接。

预设选择器在推理旁显示输出上限；最后的 Environment 或命名问题在保存前显示组装的连接和设置。首次欢迎页、`add model` 和新 Model 的 `add agent` 共用这些创建时预设。预设写入普通、可编辑 YAML：

- **OpenAI Responses：** 上游配置识别为支持推理的模型使用 high 推理、`openai_reasoning_summary: detailed` 和 `openai_store: false`。还提供 low、medium、extra-high（`xhigh`）和提供方默认选项。未知或非推理模型（如 GPT-4.1）默认中性设置，无推理摘要参数。Chat Completions 不接收 Responses 专用摘要字段。
- **Anthropic：** 较新支持的模型配置使用自适应推理、返回摘要和 high effort；否则使用交错扩展推理，8192 token 预算和 16,384 token 输出上限。交错预设显式开启 `interleaved-thinking-2025-05-14`；自适应推理自动交错。上游配置拒绝预算推理的模型，只提供自适应和提供方默认预设。
- **Google Gemini：** 原生推理预设请求可用的思考摘要，并将强度映射到所选模型支持的级别或预算。
- **OpenRouter：** 推理预设用 `exclude: false` 请求返回推理内容。
- **DeepSeek、GLM 和 Kimi：** 专用原生集成保留返回的 `reasoning_content`，在工具续接和后续轮次发送回模型。已识别推理模型提供 **Thinking · preserved**（`thinking: true`）；GLM 还保存 `zai_clear_thinking: false` 并使用原生 Z.AI 请求转换。选择 `deepseek:`、`zai:` 或 `moonshotai:` 而非通用 OpenAI 兼容路由，以保留提供方专用行为。不提供通用“off”选项：部分模型始终推理，Kimi 原生关闭控制也不等同于统一 `thinking: false`。

DeepSeek Reasoner/V4 Pro、GLM 4.7/5.3 和 Kimi K2.5/K2 Thinking 的流解析、工具结果续接和序列化下一轮重放，已用模拟原生 SDK HTTP 响应进行回归测试。测试验证本地请求准确性，不验证真实账户访问或提供方可用性。

模型不支持建议推理设置时，选择 **Provider defaults**。它不插入 `max_tokens`；原生适配器/提供方保留默认值，仍可能限制输出。预设不授予权限或提高 token 上限。“Returned thinking”指提供方公开内容或摘要，不是私有内部推理。已有资源文件绝不迁移到新预设默认值。

### 配套输出预算

对下面确切、经过审查的模型 ID，选择推理预设也保存 `settings.max_tokens`。这些是**每请求输出建议值**，不是 Run 总限制、模型最大值或强制输出量。推理可能按提供方原生计算消耗输出额度。工作上下文独立控制提醒和压缩；选择更小上下文不会按比例缩小这些值。

| 连接与经审查的模型 ID                                                                                                             | 推理选择                     |      保存的 `max_tokens` |
| --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | -----------------------: |
| OpenAI Responses / Chat Completions：`gpt-5`、`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.5`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-6-astra` | low / medium / high 或 xhigh | 16,384 / 32,768 / 65,536 |
| Anthropic：`claude-sonnet-4-6`、`claude-opus-4-6`、`claude-haiku-4-5`、`claude-sonnet-4-5`                                        | adaptive / interleaved       |          32,768 / 16,384 |
| Google：`gemini-3.1-pro-preview`、`gemini-3.5-flash`、`gemini-2.5-pro`、`gemini-2.5-flash`                                        | low / medium 或 high         |          16,384 / 32,768 |
| DeepSeek：`deepseek-v4-pro`、`deepseek-v4-flash`、`deepseek-reasoner`                                                             | Thinking · preserved         |                   32,768 |
| Z.AI：`glm-5.3`、`glm-5.2`、`glm-4.7`、`glm-4.5`                                                                                  | Thinking · preserved         |                   32,768 |
| Moonshot AI：`kimi-k2.6`、`kimi-k2.5`、`kimi-k2-thinking`                                                                         | Thinking · preserved         |                   32,768 |
| OpenRouter：`openai/gpt-5.4`                                                                                                      | low / medium / high          | 16,384 / 32,768 / 65,536 |
| OpenRouter：`anthropic/claude-sonnet-4.6`、`google/gemini-2.5-pro`                                                                | low / medium 或 high         |          16,384 / 32,768 |

所选原生模型配置仍决定可提供哪些推理预设。Gemini 2.5 原生 high 推理使用 24,576 token 思考预算，32,768 token 输出上限为回答留空间。Claude 显式 8192 token 交错思考预算保留已有 16,384 token 上限，自定义模型 ID 也如此。未经审查的 Claude 自适应预设也保留 16,384 token 基线。其他未经审查模型和路由不加输出上限；增加建议项或匹配模型名前缀不会自动设置预算。确切经审查 ID 在自定义端点后获得同样可编辑建议，但端点限制可能不同。Codex、Grok 和 Copilot 订阅创建不使用 API 预设。

根据端点、上下文大小、推理需要、延迟和费用检查保存值。之后可独立编辑 `settings.max_tokens`。改变 `/thinking` **不会** 重新计算。复用 Model、加载已有文件或向设置 API 显式传入设置，都不应用预设。没有运行时自动预算策略、上下文溢出保证、Harness 预设依赖，也不在设置时进行网络查询。

发行版建议在 2026 年 9 月 10 日依据提供方参考检查：[OpenAI 模型限制](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[Claude 模型限制](https://platform.claude.com/docs/en/models/sonnet-4-6/overview)、[Gemini 输出限制](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro)、[DeepSeek 模型详情](https://api-docs.deepseek.com/quick_start/pricing)、[GLM 模型详情](https://docs.z.ai/guides/llm/glm-5.3)、[Moonshot 模型卡](https://huggingface.co/moonshotai/Kimi-K2.5)和 [OpenRouter 模型元数据](https://openrouter.ai/api/v1/models)。来源描述模型能力和示例，不保证自定义端点或账户接受每项设置。原生 SDK 请求序列化用模拟 HTTP 测试，不是真实提供方测试。特别是已安装原生 DeepSeek 适配器发送 `max_completion_tokens`，而 DeepSeek API 参考记录 `max_tokens`；该别名的接受和执行尚未在真实服务验证。Harness UI 不覆盖适配器字段映射。

首次设置后，`add model` 只保存可复用 Model，再提供独立 Add Agent 流程。`add agent` 先让你选择已有 Model 或创建新 Model，再保存新 Agent。复用直接引用 Model，不复制或修改设置。两命令为重复名称分配独立身份，不修改现有 Agent、Model、默认值或对话。首次欢迎流程同时创建初始 Model 和 Agent，不询问已有 Model。设置和 Add Agent 初始化缺失的根 `security.shell_review`，保留现有根选择。订阅可使用独立审查 Model；API 密钥设置复用所连接 Model。快捷配置对各 Agent 使用 `risk_threshold: extra_high`，标记 shell 调用需审批，非超时审查失败默认不额外限制。审查超时始终拒绝。Add Model 绝不修改此根策略。现有 Agent 文件不迁移。见 [shell 审查配置](configuration-recipes.md#configure-tool-review)；风险阈值与模型推理强度独立。

## 原生请求设置

通过 API 密钥网关使用 **Google Cloud** 时，应显式编写 Model 路由；设置目录的 `google:` 连接使用 **Gemini Developer API** ，是不同传输：

```yaml
route: google-cloud:your-gateway-model-id
authentication:
  kind: api_key
  credential_ref: key-gateway
model_configuration:
  base_url: https://gateway.example.com
```

保留网关模型 ID 和端点。原生 SDK 添加版本和资源路径；不要仅为切换传输追加 `/v1beta` 或 `/v1beta1`。已有 `google-gla:`、`google-vertex:` 和 `gemini:` 别名使用相同 Cloud 传输；`google-cloud:` 明确表示该选择。此 API 密钥路由不配置 Cloud IAM 或服务账户凭据。验证配置后测试真实请求：仅接受配置不证明网关或模型访问。

`settings` 是传入 Harness 和模型适配器的 JSON 兼容对象，不是 Harness UI 参数允许列表。原生提供方专用及未来选项、嵌套对象、显式 `null` 和字符串空白，在保存组合和新 Agent 构造中都保留。已安装原生 Model 和 provider 负责参数含义、优先级、支持值及使用时错误。加载配置或创建 Project 不验证提供方请求参数，也不发出模型请求。

使用已安装模型适配器和提供方支持的设置，例如：

| 设置                                                                                  | 用途                                                       |
| ------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `max_tokens`、`temperature`、`stop_sequences`                                         | 原生生成控制；停止序列中的空白有意义                       |
| `thinking`、`openai_reasoning_effort`、`anthropic_thinking`、`google_thinking_config` | 通用或提供方专用推理；原生优先级适用                       |
| `service_tier`、`openai_service_tier`                                                 | 通用或 OpenAI 服务层级；已配置 OpenAI 专用值按原生规则优先 |
| `extra_headers`、`extra_body`                                                         | 原生请求扩展，包括嵌套 JSON 值                             |
| `openai_prompt_cache_key`、`openai_store`                                             | OpenAI 请求选项；Codex 仍采用原生订阅行为                  |

例如已有 `settings.service_tier: priority` 不重命名也继续工作；新编写的 OpenAI/Codex 配置使用 `settings.openai_service_tier: priority`。终端显示配置的原生层级；显式 `/fast on` 或 `/fast off` 仅为后续 Run 覆盖适用层级，`/fast reset` 恢复文件选择。不改写源文件。

不透明设置原样保留，不通过猜测字段名清除密钥。密钥应使用专用身份验证和 MCP 凭据来源；除非希望值被持久化到本地配置捕获，否则不要将凭据放入 settings 或扩展配置。分享前检查诊断和设置显示。

`model_configuration` 是独立 Host 连接设置，不是请求设置：对设置向导提供的 HTTP/API 密钥提供方、旧 `openai` 别名，以及原生 `google-cloud` 路由和别名，接受可选 `base_url`。使用不含嵌入凭据、查询参数或片段的 HTTP(S) URL。支持本地 HTTP 端点。订阅端点不能覆盖。`xai:` 原生 SDK 路由也要求空 `model_configuration`；它使用上游默认 gRPC 端点，不接受 HTTP `base_url`。未知 Host 构造字段和不支持路由仍失败，不静默忽略。
