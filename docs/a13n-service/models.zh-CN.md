---
title: 模型
description: 接入 provider，为 agent 配置可用模型，再设置请求默认值、媒体理解和价格。
---

先将上游账号接入为**模型 provider**，再保存 agent 可通过它调用的**模型**。Provider 保存凭据和连接设置。每个模型保存上游 ID、调用 API、输入能力、请求默认值和可选价格。两者属于同一[工作空间](resources.md#workspaces)；agent 通过稳定的 key 选择模型。

先让一个模型成功返回一次文本回复。你需要工作空间的 `write` 权限、上游账号访问权限，以及 Service Worker 可访问的端点。目录条目用于填写表单，实际回复用于验证推理权限。完成后再调整[请求设置](#advanced-request-settings)或配置[媒体理解](#media-understanding)。

## 模型 provider

1. 打开**模型 → 添加模型**。空工作空间会直接打开连接表单。已有 provider 时，选择一个，或点击**接入新的供应商**。
2. 选择 provider 类型并填写连接信息。API key 放在凭据字段中，不放在模型设置或 agent 指令里。
3. 保存 provider，继续[添加模型](#add-a-model)。以后可在**工作区设置 → 供应商**管理该账号。

![Console 中的模型 provider 目录](../../.github/assets/console-model-providers.webp)

Provider 类型决定模型可用的调用 API。每行第一项是默认值：

| Provider 类型                                                      | 模型 API                                                                          |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `openai`, `azure_openai`                                           | `openai.responses`, `openai.chat_completions`                                     |
| `anthropic`                                                        | `anthropic.messages`                                                              |
| `google_gemini`, `google_vertex`                                   | `google.generate_content`                                                         |
| `aws_bedrock`                                                      | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` |
| `openrouter`                                                       | `openrouter.chat_completions`                                                     |
| `fireworks`, `together`, `cerebras`, `sambanova`, `vercel`, `xai`  | `openai.chat_completions`                                                         |
| `ollama`                                                           | `ollama.chat_completions`                                                         |
| `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu` | `openai.chat_completions`                                                         |
| `cloudflare_ai_gateway`                                            | `openai.chat_completions`                                                         |
| `typesafe`                                                         | `typesafe.system_one`                                                             |
| `openai_chatgpt`                                                   | `openai.responses`；按下文完成授权                                                |

API 客户端在 `/api/v1/model-providers` 创建账号。读取 `GET /api/v1/provider-types/model`，获取当前部署的配置、凭据和请求设置 schema。多数 API key 类型接受 `credential: {"api_key": "..."}` 和可选的 `config.base_url`。[Provider 操作](resources.md#providers)介绍保存、测试和轮换凭据的流程。

以下 Chat Completions provider 应保留完整的上游模型 ID；除非账号需要覆盖端点，否则使用默认地址：

| Provider 类型 | 默认端点                                | 目录渠道       |
| ------------- | --------------------------------------- | -------------- |
| `fireworks`   | `https://api.fireworks.ai/inference/v1` | `fireworks-ai` |
| `together`    | `https://api.together.xyz/v1`           | `togetherai`   |
| `cerebras`    | `https://api.cerebras.ai/v1`            | `cerebras`     |
| `sambanova`   | `https://api.sambanova.ai/v1`           | `sambanova`    |
| `vercel`      | `https://ai-gateway.vercel.sh/v1`       | `vercel`       |
| `xai`         | `https://api.x.ai/v1`                   | `xai`, `x-ai`  |

例如，Fireworks 使用 `accounts/fireworks/models/llama-v3p3-70b-instruct` 这样的 ID，Together 使用 `meta-llama/Llama-3.3-70B-Instruct-Turbo`，Vercel 使用 `anthropic/claude-sonnet-4.6` 这样的网关 ID。请选择账号可用的 ID。这些集成使用 HTTP Chat Completions，不提供 Responses API 或 xAI 原生服务端搜索工具。除 Vercel 外均支持独立连接测试；Vercel 的公开模型列表不能验证凭据，应通过模型请求验证访问权限。

使用 **Google Vertex AI** 时，让凭据类型与接入方式一致：

- **API key**：用于 API key 网关或 Vertex Express Mode。Project ID 和 Location 留空。接入网关时，Base URL 填网关 origin，不含 `/v1beta1` 或模型路径。SDK 向 `/v1beta1/publishers/google/models/...` 发送 `x-goog-api-key`。
- **Service account**：填写扁平的服务账号凭据对象，以及必填的 Project ID 和 Location。切换凭据类型会清空上一种类型的表单草稿。

使用 **Cloudflare AI Gateway** 时，将 `account_id` 设为 32 位 Cloudflare 账号 ID，并将 API token 保存为 `api_key`。Token 需要 **Account → Workers AI → Read** 权限。在 Cloudflare 配置默认别名的 BYOK 凭据或统一计费。设置 `gateway_id` 以选择网关，省略则使用默认网关的第三方模型。使用 `openai/gpt-5.4` 或 `anthropic/claude-sonnet-4.6` 等完整 ID，两者均通过 `openai.chat_completions` 调用。自定义 `base_url` 替代按账号生成的端点，必须以 `/ai` 结尾，不含 `/v1`、方法路径、查询参数或片段。请用模型请求测试：此集成不提供独立连接探测、原生服务端工具或单独的 Workers AI 目录。参见 [Cloudflare 统一 REST API](https://developers.cloudflare.com/ai-gateway/usage/rest-api/)。

网关按请求头路由或计费时，可在 provider 上添加最多 32 个**额外请求头**。值会加密，读取时只返回 `header_names`。Provider `PATCH` 中，填写值表示设置该名称，`null` 表示移除，省略表示保留。传输、认证和协议请求头名称保留给系统。密钥应放在这些加密请求头中；[请求级请求头](#advanced-request-settings)是可读取配置。

## ChatGPT 订阅 provider

使用 `openai_chatgpt` 为工作空间授权 ChatGPT 订阅额度。授权属于 provider，由工作空间共享，不保存在个人连接中。

1. 在**工作区设置 → 供应商**添加 **ChatGPT** provider。默认流程动态注册公共 OAuth client，不需要静态凭据。
2. 点击**使用 ChatGPT 继续**，打开授权链接。批准使用 ChatGPT 订阅额度；仅授予网站身份权限不能调用模型。
3. 将浏览器地址栏的完整回调 URL 复制到**完整回调 URL**，再点击**完成登录**。即使回环页面无法访问，也要复制地址。不要公开该 URL。
4. 使用 `openai.responses` 和账号模型 slug 添加模型。从账号可见选项中选择，或手动输入 ID，再通过 agent 发送测试消息。

Service 可以部署在远端：它校验并交换粘贴的回调，不请求该 URL，也不要求浏览器访问服务器的回环监听器。回调输入无效时可修改后重试。一旦开始交换授权码，登录失败就需要新的授权尝试。

API 集成按顺序使用以下操作：

| 任务         | `/api/v1/model-providers/{id}` 下的请求                                 | 需要保留或检查的结果                             |
| ------------ | ----------------------------------------------------------------------- | ------------------------------------------------ |
| 开始登录     | `POST /authorize`，请求体为 `{}`                                        | 打开 `authorization_url`，保留 `attempt_id`      |
| 完成登录     | `POST /authorization/callback`，提交 `attempt_id` 和完整 `callback_url` | 授权状态                                         |
| 检查状态     | `GET /authorization`                                                    | 不含凭据的状态和账号元数据                       |
| 列出账号模型 | `GET /models`                                                           | `slug` 和 `display_name`；可见不代表有推理权限   |
| 断开连接     | `DELETE /authorization`                                                 | 本地 token 已清除；远端访问在 ChatGPT 设置中管理 |

再次登录会复用注册信息。**使用另一个 ChatGPT 账号**，或以 `{"new_registration": true}` 调用 `POST /authorize`，会忽略保留的账号提示。未配置 client 时会创建新的动态注册；显式配置的 client 则继续保留。Codex 凭据不能代替此授权。

此 provider 使用固定端点，将 token 和待完成授权保存在加密的 provider 自有状态中。请求始终设置 `store: false` 并使用流式响应；非流式调用方收到收集完成的流。Temperature、Top P 和输出 token 上限不会发送。其他设置和工具遵循官方 ChatGPT profile，因此 API key 模型的行为不会自动沿用。

### 自托管回调

使用公共 client 和 `http://127.0.0.1:<port>/auth/callback`。Provider 编辑器中的 **OAuth client ID** 和回环回调配置覆盖部署默认值；留空则继承。先保存修改，再重新登录。

```sh
export A13N_PROVIDERS__CHATGPT_REDIRECT_URI="http://127.0.0.1:1456/auth/callback"
# Optional: reuse an issued public-client registration.
export A13N_PROVIDERS__CHATGPT_CLIENT_ID="issued-public-client"
```

从机密 client 或 HTTPS 托管回调升级时，清除旧凭据，配置公共 client 回环流程，再重新授权。不支持 client secret 或旧托管回调路由。已有公共 client token 仍可读取；升级后重新开始待完成登录。修改 client ID 后不会复用另一 client 的账号提示。

官方 provider 负责到期刷新和 401 刷新。Service 保存完整的替换凭据，并拒绝断开或替换授权后的过期保存。多个 Worker 仍可能竞争使用同一个 refresh token：并发使用订阅时关注刷新失败，必要时重新授权。本地断开后，活动 provider 可能仍持有缓存 token。推理、刷新和发现使用 Service 的模型传输限制；授权码交换使用官方固定端点客户端。[模型认证](../a13n-harness/model-authentication.md)介绍共享集成机制。

## 添加模型

**在 Console 中：**

1. 打开**模型 → 添加模型**，选择已接入的 provider。
2. 选择目录模型，或填写上游模型 ID。选择端点支持的调用 API。
3. 检查名称、输入能力、请求默认值和价格。目录选择会填入这些值，保存前可以修改。
4. 保存启用状态的模型，在 agent 上选择它，并发送简短文本请求。Agent 返回回复后，继续阅读 [agent 与运行](agents-and-runs.md)。

公开目录列出 models.dev 中的近期文本模型，每个模型一行；需要时提供多个 ID，例如 Bedrock 区域 ID。通用 `openai` provider 还提供**其他模型（兼容接入）**，用于通过 OpenAI 兼容端点调用其他厂商的模型。目录不可用时手动输入 ID。支持账号发现的 provider 应使用账号选项，不应把公开目录当作访问授权。

**通过 API**：将 `A13N_URL` 设为 Service origin，`A13N_API_KEY` 设为具有 `write` 权限的[工作空间 API key](identity.md#api-keys)。下面的例子要求工作空间中已有启用的 Ollama provider，Worker 可访问该端点，且端点提供 `llama3.3`。

将以下请求体保存为 `model.json`，把 `mprov_...` 替换为 provider ID：

```json
{
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "description": "Served by the team's Ollama host",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"context_window_tokens": 131072},
    "settings": {"max_tokens": 4096}
  }
}
```

创建模型并读回：

```sh
curl -fsS "$A13N_URL/api/v1/models" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  --data-binary @model.json
curl -fsS "$A13N_URL/api/v1/models/local-llama" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

创建成功返回 `201` 和保存后的模型。在 agent 的 `config.model` 中填写 `local-llama`，不是 `mprov_...` 或 `llama3.3`。创建模型或修改模型配置还需要 provider 的 `write` 权限，因为模型使用其凭据。

按用途选择字段：

| 字段                                    | 配置内容                                                                                                                                                                                           |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `key`                                   | 工作空间中不可变的地址，用于 agent、用量和 `/models/{key}`。省略时，Service 根据 provider 类型和上游名称生成，转为小写，并将连续的不允许字符替换为 `-`。遇到 `409 already_exists` 时选择其他 key。 |
| `config.model_name`, `config.model_api` | 精确的上游 ID，以及该 provider 类型支持的调用 API                                                                                                                                                  |
| `config.characteristics`                | 上下文窗口、上下文管理阈值和实际输入能力：`image_understanding`、`video_understanding`、`audio_understanding`，或用于 PDF 的 `document_understanding`                                              |
| `config.settings`                       | 使用此模型时的请求默认值；agent 设置可以覆盖                                                                                                                                                       |
| `pricing`                               | 此模型自身调用的价格；从目录复制或自行提供                                                                                                                                                         |
| `catalog_ref`                           | 可选的目录来源 `{provider, model}`，Console 用于显示模型身份；仅校验结构，不要求目录中存在该条目                                                                                                   |
| `enabled`                               | 默认为 `true`；设为 `false` 可保存禁用模型                                                                                                                                                         |

要用公开元数据填写模型，可按精确、区分大小写的条件查询目录：

```sh
curl -fsS --get "$A13N_URL/api/v1/model-catalog" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode "provider=openai" \
  --data-urlencode "model=gpt-5.5"
```

将所选条目的 `characteristics`、`pricing` 和 `ref` 复制到模型的 `config.characteristics`、`pricing` 和 `catalog_ref`。响应状态为 `ready`、`stale`（刷新失败后的上次目录）或 `unavailable`（尚未成功获取）。筛选条件以 AND 组合；无匹配时 `items` 为空。省略 `model` 可列出整个渠道，两者都省略则返回完整目录。刷新共用完整目录缓存。读取目录不会更新已保存的模型或价格。

编辑已保存模型时，先 `GET /api/v1/models/{key}`，保留完整配置，再将返回的 ETag 放入 `If-Match` 执行 `PATCH`。可修改 `name`、`description`、`config`、`pricing`、`catalog_ref` 和 `enabled`。`config` 是完整替换，需包含所有要保留的字段。用 `{"enabled": false}` 禁用模型；模型没有删除操作。[资源编辑](resources.md#common-conventions)展示读取、修改和检查的顺序。

### 图片输入预处理

打开模型的**高级 → 图片输入**，选择图片进入请求前的处理方式：

| 控件                    | 默认值 | 特殊值的效果                               |
| ----------------------- | ------ | ------------------------------------------ |
| 预处理图片              | 开启   | 关闭后禁用自动预处理                       |
| 支持 GIF                | 开启   | 关闭后移除二进制 GIF；不下载或识别图片 URL |
| 最多图片数              | 20     | `0` 移除全部图片                           |
| 单张图片大小上限（MiB） | 5      | `0` 取消字节数上限                         |

大小限制按**每张图片的 Base64 编码字节数**计算，不是源文件字节数；1 MiB 为 1,048,576 字节。API 使用 `config.characteristics.image_input`：省略时采用默认值，提供对象可自定义，设为 `null` 则关闭预处理。[共享图片策略](../a13n-harness/models.md#image-input-policy)列出全部七个字段。编辑其他控件时，Console 会保留未展示的尺寸、切分选项和精确字节预算。

主模型、每个子运行模型和图片理解模型各自应用自己的策略。每次 attempt 使用当次解析的模型配置。后续 attempt，包括恢复，会解析当前模型默认值；Service 不像 Harness UI 那样跨 attempt 冻结模型。

### 高级请求设置

在**高级**中调整已保存的模型。先选择**思考强度**或**最大输出 token**；OpenAI Responses 还提供**推理摘要**和**存储响应**。这些控件与**设置 JSON**编辑同一份草稿。不支持的选项留空，保存后再测试一条消息，确认修改有效。

模型默认值使用 `config.settings`，单个 agent 的覆盖值使用 `model_settings`。对于支持中等推理强度的 OpenAI Responses 模型，可使用以下设置对象：

```json
{
  "openai_reasoning_effort": "medium",
  "extra_headers": {"x-experiment": "candidate"}
}
```

设置按以下顺序应用：

1. Service 提供基础默认值：开启提示缓存，关闭 OpenAI Responses 存储。
2. 模型默认值覆盖对应的基础值。原生 `config.settings` 条目也会整体替换同名的扁平 `config` 默认值（`max_tokens`、`temperature`、`top_p`、`extra_body`、`extra_headers`）。
3. Agent 或 reviewer 设置覆盖对应的模型默认值。运行覆盖先替换 agent 的**整个** `model_settings` 对象，再以模型默认值作为底层设置。

对于 `extra_body` 和 `extra_headers`，省略表示继承模型对象，显式对象表示整体替换，`{}` 表示清空，`null` 无效。因此，运行的 `model_settings: {}` 会移除 agent 覆盖，但仍继承模型默认值。使用 `model_settings: {"extra_body": {}, "extra_headers": {}}` 可清空这两个请求对象。嵌套对象不会递归合并。

优先使用类型化参数。对于已安装 SDK 尚未识别的推理选项，`openai.responses` 和 `openai.chat_completions` 接受 `extra_body`；原始值通过浅合并覆盖生成的推理字段。它们不能修改所选模型、消息、工具、结构化输出、传输或 provider 会话状态。请使用 `openai_text_verbosity`，不要直接填写 Responses 的 `text`。Provider 类型的 `settings_schemas` 同时校验模型和 agent 设置，并排除运维超时、账号状态、替代模型选择，以及 `max_usage` 无法完整计量费用的服务端工具。

**缓存**：使用 `cache: false` 关闭库管理的缓存，或用支持的保留时长设置缓存寿命。Anthropic Messages 默认还开启 `anthropic_cache_instructions`、`anthropic_cache_tool_definitions` 和 `anthropic_cache_messages`，将标记放在内容块上以兼容网关。显式统一 `cache` 值或 provider 缓存设置（包括 `anthropic_cache`）会保留所写策略。Pydantic AI 决定 provider 专属标记的位置；是否命中以及 provider 的隐式缓存仍取决于上游。

**响应存储**：`openai.responses` 默认使用 `openai_store: false`，包括没有写入该设置的已有模型。设为 `true` 可开启，设为 `null` 则交给 provider 决定。它控制上游响应存储，不控制 Service 运行历史或其他保留策略。Agent/运行设置可以覆盖它；其他调用 API 保持自己的行为。ChatGPT 订阅 profile 仍要求关闭存储。

**请求头**：请求设置中的值可读取。密钥保存在 provider 凭据或加密额外请求头中。请求设置不能覆盖认证、协议、已有 provider 请求头名称或亲和请求头，大小写不同也不允许。模型/provider 修改在后续 attempt（包括恢复）生效，请求前会重新检查设置。

### 网关会话亲和

让线程始终路由到网关选定的后端：

1. 配置网关，使其按支持的请求头路由。
2. 打开 provider 的高级连接设置，将**会话黏性请求头**设为该名称（API 对应 `config.session_affinity_header`）。
3. 继续同一线程，在网关日志中检查线程派生 UUID 是否保持不变。再用子线程或分叉线程检查是否得到自己的值。

Service 在所有模型路径上提供该 UUID，包括 reviewer 和媒体理解。值从线程派生，不使用会话或运行 ID。清空请求头默认值不会移除它。亲和请求头留空可禁用；仅选择预设不会配置网关路由。

### 模型 API 可用性

保存 agent 或校验设置时，如果返回 `503 unavailable` 和 `{"dependency": "model_api:<api>"}`，说明部署已不提供该调用 API。将模型的 `config.model_api` 与 `GET /api/v1/provider-types/model` 对照。选择 provider 和部署均支持的 API，或请运维恢复原 API，再重新校验配置。

## 媒体理解

为 agent 配置独立模型，处理主模型无法读取的图片、视频或音频。可设置工作空间默认值供多个 agent 使用，也可在 agent 自己的 `media_understanding` 中选择模型。

1. 添加启用状态、具备对应能力的模型，例如 `image_understanding`。其 provider 也必须启用，且在工作空间中可用。
2. 以工作空间管理员身份打开**工作区设置 → 媒体理解**，为该媒体类型选择模型。
3. 向需要此后备能力的 agent 发送代表性媒体输入。检查回答，以及所选模型的用量记录。

通过 API 操作时，需要 `curl`、`jq`、前述 Service origin 和工作空间 API key，以及管理员权限。将 `IMAGE_MODEL_KEY` 设为图片模型的 key。修改前先读取当前默认值和 ETag：

```sh
curl -fsS -D media.headers -o media.json \
  "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY"
MEDIA_ETAG=$(awk 'tolower($1) == "etag:" {sub(/\r$/, "", $2); print $2}' media.headers)
jq --arg model "$IMAGE_MODEL_KEY" \
  '{image: $model, video: .video, audio: .audio}' media.json > media-update.json
```

检查 `media-update.json`，再替换默认值并读回：

```sh
curl -fsS -X PUT "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $MEDIA_ETAG" \
  --data-binary @media-update.json
curl -fsS "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

响应应显示所选图片模型，视频和音频选择保持不变。`PUT` 替换全部三类；省略或 `null` 会清除该类型。遇到 `412` 时，先重新读取当前默认值，再决定如何替换。每个所选模型都必须声明匹配的输入能力。

执行时优先使用 agent 自己的选择；该选择不可用时运行失败。不可用的工作空间默认值会被跳过并记录警告，对应类型将没有后备模型。

## 用量与价格

测试运行后，查看[用量](agents-and-runs.md#usage)，确认每次调用使用了哪个模型。Service 按所选工作空间模型归属和计价，并将当时的价格快照保存在用量记录中。这同样适用于压缩、reviewer、媒体理解和子 agent。

两个已保存模型可以使用同一个上游 ID，同时设置不同价格。Provider 返回的别名或带日期的模型名称不会改变归属。复制的目录价格中，`provider` 和 `model` 记录价格来源；该价格仍用于保存它的模型。费率变化时更新已保存的 `pricing`，不要期待目录刷新自动修改它。
