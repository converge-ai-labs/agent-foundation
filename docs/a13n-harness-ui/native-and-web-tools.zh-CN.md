---
title: 原生工具与 Web 提供方
description: 配置提供方原生搜索、代码执行和图像生成，以及 Host Web 提供方。
---

原生工具通过所选 Model 的提供方执行；Host 工具通过 Harness UI 和其 Environment 执行。两者相互独立：原生搜索不是本地爬虫，提供方代码执行不是你的 Environment shell，提供方文件搜索也不会索引 Thread 文件。

在 Agent 上配置提供方原生工具。可用选项取决于提供方、Model 和账户。原生图像生成将完成的结果保存到当前 Thread 的 `tmp/`，并返回路径，不会切换模型或提供方。

| 所选原生工具    | Host Web 行为                         |
| --------------- | ------------------------------------- |
| 原生 Web Search | 关闭 Host 搜索；仍提供 Host 获取/下载 |
| 原生 Web Fetch  | 关闭 Host 抓取；仍提供 Host 获取/下载 |
| 两者均未选      | Host 搜索和抓取使用配置的提供方       |

这些是创建时的设置选择；[Host Web 配置](#host-web-capability-every-built-in-provider)可以单独编辑。

## 设置向导

1. 运行 `a13n-harness-ui setup`，或按照首次启动欢迎页操作。
2. 选择订阅或 API 提供方。
3. 完成身份验证，然后选择模型。
4. 查看 **Choose native Agent tools**（连接没有原生候选时跳过）。上/下方向键移动，Space 勾选，Enter 确认。也可输入逗号分隔的编号或名称，或 `none`。推荐项默认勾选；全部取消表示不选原生工具。Host Web 仍开启，包括获取/下载、无密钥搜索和 HTML 转 Markdown 抓取。原生 Web Search 只关闭 Host 搜索；原生 Web Fetch 只关闭 Host 抓取。内置提供方无须额外 Web 凭据设置。
5. 选择 File Search 时，需要输入已有的提供方存储 ID。Remote MCP 需要提供方可访问的 URL 和服务器标签。Advisor 需要其提供方模型 ID。设置不会创建这些远程资源或检查账户权限。
6. 检查最终选择。Esc 返回；改变连接或 Model 会重置工具建议和资源输入。
7. 设置会保存普通 Agent `capabilities`。之后可编辑 YAML，再运行 `a13n-harness-ui config validate`。验证检查配置，不检查提供方权限。

**Add Agent** 使用同样的工具选择器，复用已有 Model 时也是如此。**Add Model** 只保存连接，再提供为该 Model 创建 Agent 并配置工具的选项。只保留 Model，或取消后续 Agent 设置，都不会删除已保存 Model。不会静默改变已有 Agent 和默认值。`model_characteristics.capabilities` 中的模型输入能力与 Agent 工具是独立配置。

### 推荐选项与连接差异

下面每种连接都包含 Host Web 获取/下载，即使表中只列出原生工具。搜索和抓取仅在未选择对应原生工具时保持开启。

| 连接                                                | 默认勾选的工具                                                     | 向导提供的其他原生选项                                       |
| --------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| Codex 订阅                                          | 实时原生 Web Search、Host Web Fetch、可保存的原生 Image Generation | 仅提供这些经审查的订阅工具                                   |
| Grok 订阅                                           | 原生 Web Search、Host Web Fetch                                    | 不宣称此订阅传输支持 X Search 或图像生成                     |
| OpenAI Responses、经审查的 GPT 模型                 | 原生 Web Search、可保存的原生 Image Generation、Host Web Fetch     | Code Execution、File Search、Remote MCP                      |
| Anthropic、经审查的 Claude 模型                     | 原生 Web Search 和 Web Fetch                                       | Code Execution、Remote MCP，以及上游模型配置允许时的 Advisor |
| Gemini 3 编程模型                                   | 原生 Web Search 和 Web Fetch                                       | Code Execution；已有存储且不使用其他原生工具时的 File Search |
| OpenRouter                                          | 经审查模型 ID 的原生 Web Search、Host Web Fetch                    | Advisor                                                      |
| xAI 原生 SDK（`xai:`）                              | 原生 Web Search、Host Web Fetch                                    | X Search、Code Execution、File Search、Remote MCP            |
| Chat Completions、较旧 Gemini 组合、其他 API 提供方 | Host Web Search + Fetch                                            | 不会仅凭 OpenAI 兼容 API 标签推断不受支持的原生工具          |
| 未知模型 ID                                         | Host Web Search + Fetch                                            | 可显式选择官方适配器候选；不推断模型权限                     |
| 自定义 HTTP 端点                                    | Host Web Search + Fetch                                            | 适配器候选仍可选择；默认不勾选任何项。需验证端点支持         |

API 密钥连接与订阅使用同一选择器。选择 **OpenAI Responses** 时，即使是自定义模型 ID 或网关，也会提供原生 Web Search 和可保存 Image Generation；非默认 URL 不能证明这些工具不受支持。未经审查的模型 ID 和自定义端点默认不勾选原生工具，启用是显式选择，不代表保证网关兼容。OpenAI Chat Completions 不公开这些原生工具对象；仅当端点实现 Responses 时才选择该协议。

选择器从已安装上游适配器和相关模型配置获取原生候选，不是独立的运行时兼容性引擎。模型不在简短建议菜单中，也可提供适配器候选：例如 OpenAI 除新模型外，还声明 `gpt-4.1` 和 `gpt-4o` 支持图像生成工具。实际要求见 [OpenAI 图像生成模型支持](https://developers.openai.com/api/docs/guides/tools-image-generation#supported-models)和 [Web 搜索支持](https://developers.openai.com/api/docs/guides/tools-web-search)。提供方限制、账户访问和计费仍适用。推荐项是创建时默认值，不是迁移规则，也不会在切换 Model 时自动重新计算。

Codex 和 Grok 订阅连接目前使用 Responses 适配器，**没有独立的 `WebFetchTool`**。始终开启的 **Host Web** 提供 HTTP 获取、HTML 抓取和下载。xAI 搜索本身可能浏览网页，但不等于实现独立 WebFetch 工具。

需要 xAI 完整原生工具集时，选择 **xAI · Native SDK (gRPC)**，而不是 **xAI · Chat Completions** 或 Grok 订阅。它使用 `XAI_API_KEY` 或选中的已存密钥，以及上游 SDK 默认端点；向导不会询问 HTTP 基础 URL。Model 示例：

```yaml
schema_version: "1"
kind: model
id: model-xai
name: xAI native
route: xai:grok-4.6
authentication:
  kind: api_key
  env: XAI_API_KEY
```

仅支持图像的 Gemini 模型无法运行编程入门 Agent 的函数工具。应单独添加该 Model，并编写具有兼容 Capability 的专用 Agent，不要在编程入门模板上启用。较早的 Gemini 模型也不能将原生工具与入门模板函数工具组合。见 [Google 工具组合](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#google-tool-combinations)。

## 原生工具配置

在 Agent 的 `capabilities` 下添加各项选择。重复的 `NativeTool` 条目可以组合不同原生工具。平铺 `configuration: {kind: web_search}` 和显式 `configuration: {tool: {kind: web_search}}` 都使用上游反序列化。使用 `native_image_generation` 时不要再添加原始 `ImageGenerationTool`。

### Web Search

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_search
      search_context_size: medium
  - capability: web
    configuration:
      search: {mode: off}
```

第二个条目保留 Host 获取/抓取/下载，不会重复添加搜索。原生搜索使用 Model 身份验证，不使用独立搜索提供方 API 密钥。提供方专用选项包括 `allowed_domains`、`blocked_domains`、`max_uses`、`user_location` 和 `external_web_access`；并非每个提供方都实现全部选项。Codex 推荐设置为 `external_web_access: true`，使原生搜索默认读取实时网页内容。只有明确需要缓存搜索时才设为 `false`。OpenRouter 使用服务器侧 Web 搜索工具和提供方管理的搜索计费。添加过滤前查阅[上游参数支持表](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#web-search-tool)。

### Web Fetch

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_fetch
      max_uses: 5
      enable_citations: true
      max_content_tokens: 12000
  - capability: web
    configuration:
      search: {mode: host}
      scrape: {mode: off}
```

此配置示例适用于 Anthropic。Google 将 `WebFetchTool` 映射为 URL context，不实现这些 Anthropic 参数；Google 仅使用 `kind: web_fetch`。Fetch 将 URL 内容带入模型上下文，不会递归爬取网站、运行浏览器、保存本地文件，也不提供 Host 的 HTML 转 Markdown API。参考：[Anthropic Web Fetch](https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-fetch-tool)、[Google URL context](https://ai.google.dev/gemini-api/docs/url-context)。

### 图像生成

```yaml
capabilities:
  - capability: native_image_generation
    configuration:
      output_format: png
      quality: auto
```

Harness UI 注入图像保存器；YAML 中不填写保存路径或导入。此 Capability 指示模型用 `![brief description](<saved path or URL>)` 在回复中展示已保存图像，保留保存器给出的位置，不虚构 URL。完成的图像保存到**当前 Thread 的 `tmp/`**，子级 Run 也一样。回复、对话记录和恢复历史包含保存路径，不含原始图像字节。预览帧不保存为最终图像。保存失败会使 Run 失败，不会报告不可用图像。Agent 之后可 `view` 文件，但保存路径不会自动把像素重新发送给其他 Model。

Thread 临时文件会在普通 Run 完成和重启后保留，但可能被清理。重要图像应复制到 Project 中保留的位置。生成图像不是用户附件。自定义 Host 向 [Harness 原生图像 Capability](../a13n-harness/capabilities.md)提供自己的异步保存器。原生选项和支持的图像模型仍由[上游](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#image-generation-tool)定义。

### X Search 与 Code Execution

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: x_search
      allowed_x_handles: [pydantic]
  - capability: NativeTool
    configuration:
      kind: code_execution
```

X Search 要求 xAI 原生 SDK 路由。原生工具还支持日期限制和图像/视频理解选项；见 [X Search](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#x-search-tool)。

Code Execution 通过兼容 Responses、Anthropic、Google 和 xAI 适配器提供。它在提供方环境中执行，不在所选本地/沙箱 Environment 中，也不受 Host shell 审查控制。可选 `files` 在支持的适配器上引用提供方已上传文件。设置不会上传 Project 文件，也不会将提供方生成的代码产物复制到 Thread tmp。图像保存器不是通用的提供方产物下载器。见 [Code Execution](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#code-execution-tool)。

### File Search

先创建提供方资源，上传/导入文件并等待索引完成。然后选择工具并输入存储标识：

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: file_search
      file_store_ids: [vs_your_store_id]
```

| 提供方            | `file_store_ids` 中的值                   | 资源设置                                                                        |
| ----------------- | ----------------------------------------- | ------------------------------------------------------------------------------- |
| OpenAI Responses  | `vs_...` 等向量存储 ID                    | [OpenAI File Search](https://platform.openai.com/docs/guides/tools-file-search) |
| Google Gemini API | `fileSearchStores/...` 等文件搜索存储名称 | [Gemini File Search](https://ai.google.dev/gemini-api/docs/file-search)         |
| xAI 原生 SDK      | Collection ID                             | [xAI Collections Search](https://docs.x.ai/developers/tools/collections-search) |

这些不是文件名或 Thread 路径。使用与 Model 连接相同的提供方账户/项目。此选择器中 Google File Search 不得与其他原生工具组合；Host Web 可以保留。Google 临时上传文件对象和导入搜索存储数据的生命周期不同：不要假定搜索存储随临时上传过期。xAI 还映射 `max_num_results`、`instructions` 和 `retrieval_mode`；不要假定这些字段在其他适配器中效果相同。资源访问、保留和费用由提供方负责。

### Remote MCP

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: mcp_server
      id: public-docs
      url: https://your-public-mcp.example/mcp
      allowed_tools: [search_docs]
```

支持的适配器为 Responses、Anthropic 和 xAI。连接远程服务器的是**提供方**；它无法通过这种方式访问你的本地 stdio 服务器或 localhost URL。向导只配置公共服务器。检查服务器和工具权限：这些是提供方执行的工具，不是 Host 工具调用，Responses 适配器会发送 `require_approval: never`。

上游 `authorization_token` 和 `headers` 是普通字符串；原生工具声明中的 `${TOKEN}` 或 `{env: TOKEN}` **不会** 由 Harness UI 解析。不要把密钥放入 Agent Capability，因为 Capability 会被 Run 组合捕获。经身份验证的原生 MCP，需要可信 Host Capability 在运行时加载凭据并构造上游工具。也可使用 Harness UI 的 [Host MCP 集成](mcp.md)，它已负责 environment/header 凭据解析。该集成使用 Agent `mcp_servers`，不同于提供方托管的 `MCPServerTool`。

### Advisor

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: advisor
      model: claude-opus-4-6
      max_tokens: 4096
```

使用 Anthropic advisor 模型 ID，或在 OpenRouter 路由上使用 `anthropic/claude-opus-4.6` 等模型 slug。这不是 Harness UI Model 资源 ID、子 Agent 或第二个 Host 连接。执行模型的提供方负责咨询和计费。Anthropic 执行模型/advisor 配对限制仍适用。OpenRouter 将 `max_tokens` 映射到 `max_completion_tokens`，固定 `forward_transcript` 为 false，忽略 Anthropic 专用 `max_uses` 和 `caching`。见 [Advisor](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#advisor-tool)。

### 原生 memory 工具：需要 Host 集成

`NativeTool` 可反序列化 `kind: memory`，但**仅这样配置并未实现功能**。Anthropic 要求一个名称恰好为 `memory`、实现其记忆命令的可执行函数工具。Harness UI 普通状态/任务/历史功能不是该处理器。因此介绍了原生 memory 工具，但标准向导不提供选择。可信 Host 集成必须提供存储和处理器，再将 `NativeTool(MemoryTool())` 与其组合。遵循[上游 Memory 示例与命令契约](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#memory-tool)；这里不引入另一套记忆子系统。

## 原生搜索与图像生成

使用兼容 Model 时，将以下条目添加到 Agent 的 `capabilities` 列表：

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_search
      external_web_access: true
  - capability: native_image_generation
    configuration:
      quality: auto
      output_format: png
  - capability: web
    configuration:
      search:
        mode: off
```

`NativeTool` 接受上游原生工具声明，包括显式 `tool: {kind: web_search}` 形式。多个条目可选择不同工具。示例开启实时搜索，与 Codex 设置默认值一致；在兼容提供方上显式请求缓存搜索时，设置 `external_web_access: false`。选项和可用性取决于实际 Model/提供方，不仅是品牌或 API 兼容标签。

生成图像保存在 Thread 临时存储中。重要结果应复制到 Project；见[图像保存与保留](#image-generation)。

上面的 `web` 条目保留获取、抓取和下载，不注册第二个搜索工具。设置 `search.mode: host` 可公开独立的无密钥 DuckDuckGo 搜索实现，支持与原生搜索并用。原生搜索使用所选 Model 提供方的账户和计费；Host 搜索使用 Harness UI 的 Web 传输。仅安装 Capability 不会自动启用任何一种搜索。

设置会将经审查的入门选择写入**新 Agent 资源**：Codex 使用实时原生搜索和原生图像生成；Grok 订阅使用原生搜索。兼容 API 模板在经审查处使用原生工具，其他情况使用 Host 搜索。不会假定自定义端点和较旧的不兼容工具组合支持原生工具。已有资源和 Agent 不迁移，改变 Model 也不改写工具选择。要修改，请编辑 Agent YAML 并运行 `a13n-harness-ui config validate`。

## Host Web Capability：所有内置提供方

新 Agent 默认包含 `web`。每次 Run 获得下列 Host 提供方的新实例；原生搜索/获取选择控制暴露哪些 Host 功能。

| 组件       | UI 实现                               | 凭据 / 配置                                                                                                                              |
| ---------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 搜索       | `DuckDuckGoSearchProvider`            | 无密钥 DuckDuckGo HTML 搜索；选择 `search.mode: host`。后端 ID 为 `default`。受公共端点可用性和速率限制影响。                            |
| 获取与下载 | `HttpxWebClient` 配合 `HttpWebPolicy` | 有界 HTTP(S) 请求，使用 Run 的允许主机策略。未设置策略时允许私有目标。不使用浏览器登录或独立 API 密钥。                                  |
| 抓取       | `HtmlScrapeProvider`                  | 使用同一客户端获取 HTML/文本并转为 Markdown。选择 `scrape.mode: host`；后端 ID 为 `default`。不渲染 JavaScript、不绕过登录、不递归爬取。 |

完整显式 Host 配置：

```yaml
capabilities:
  - capability: web
    configuration:
      search:
        mode: host
        backend: default
      scrape:
        mode: host
        backend: default
      deadline_seconds: 60
      max_redirects: 8
      max_search_results: 10
      max_text_bytes: 262144
      max_scrape_bytes: 524288
      max_download_bytes: 268435456
      download_concurrency: 4
```

Fetch 支持有上限的内联读取，以及通过当前 Environment 文件权限写入下载。Model/提供方账户不会给这些 Host 工具增加文件系统或网络权限。

### 搜索模式与后端选择

- `off`：此 Capability 不提供搜索；保留获取、抓取和下载。与独立的原生搜索 Capability 配合可避免重复搜索。
- `host`：使用已绑定 Host 搜索提供方。标准 UI 只绑定 `default`（DuckDuckGo）。
- `native`：注册上游 `WebSearchTool`，不公开 Host 搜索。与显式 `NativeTool` 条目不同，此便捷方式仅提供 `search_context_size`（`low`、`medium`、`high`）。
- `auto`：注册原生搜索，同时提供已绑定 Host 搜索。它不是提供方兼容性探测，也不会在原生工具被拒后自动重试。已知连接应使用显式选择。

抓取模式为 `host` 或 `off`。关闭抓取不会关闭普通获取/下载。设置分别写入两种模式：

| 所选原生工具 | `search.mode` | `scrape.mode` | Host 获取/下载 |
| ------------ | ------------- | ------------- | -------------- |
| 两者均未选   | `host`        | `host`        | 开启           |
| Web Search   | `off`         | `host`        | 开启           |
| Web Fetch    | `host`        | `off`         | 开启           |
| 两者均选择   | `off`         | `off`         | 开启           |

这是创建时配置，不是运行时回退。不会改写已有 YAML；显式移除整个 `web` Capability 会移除 Host Web 工具集。

`backend` 选择一个确切的已绑定后端 ID。`backend_priority` 列出已绑定后端间的偏好，不能与 `backend` 同时使用。这些值选择已有实现，不是待安装的供应商名称。自定义嵌入 Host 可通过 `WebBinding(search_backends=..., scrape_backends=...)` 提供异步 `WebSearchProvider` / `WebScrapeProvider` 实现；该 Host 负责 API 凭据、客户端生命周期、策略和错误转换。提供方端口和执行契约见 [Harness Capability](../a13n-harness/capabilities.md)。标准 Harness UI 不支持只靠 YAML 安装第三方后端。内置提供方不需要密钥，因此 `WebConfiguration` 没有 `api_key` 字段。Model 凭据不配置 Host 搜索/抓取。需要密钥的提供方必须通过真实 Host 集成解析自己的凭据引用（如环境变量）并绑定；写 `backend: tavily` 或未使用的密钥字段不能启用服务。不要将字面密钥放入 Agent Capability 配置。

嵌入代码构造 `WebCapability()` 而不显式配置时，`WebConfiguration.from_environment()` 识别 `A13N_HARNESS_WEB_SEARCH_MODE`、`A13N_HARNESS_WEB_SEARCH_BACKEND`、`A13N_HARNESS_WEB_SEARCH_BACKEND_PRIORITY`、`A13N_HARNESS_WEB_SEARCH_CONTEXT_SIZE`，及对应的 `A13N_HARNESS_WEB_SCRAPE_MODE`、`..._BACKEND`、`..._BACKEND_PRIORITY`。Harness UI 以显式 Agent YAML 为准；不要期待这些进程默认值覆盖已保存配置。

## 验证与故障排查

- 配置已接受，但提供方拒绝工具：检查实际适配器、模型 ID、账户和工具组合。“OpenAI 兼容”不代表支持 Responses 原生工具。
- Codex/Grok 缺少原生 Fetch：这是预期行为；默认包含 Host Web。不要为订阅 Agent 添加不支持的原始 `web_fetch` 条目。
- 搜索可用但没有下载文件：原生搜索/获取返回提供方上下文，不返回本地文件。使用内置 Host `web` 工具并指定下载目的地。
- 原生 memory 因缺少 `memory` 工具失败：提供 Host 处理器；仅启用原生 schema 不够。
- File Search 无结果：检查提供方存储 ID、索引状态和账户/项目访问，不要检查本地路径。
- MCP 无法连接：排查 URL 或凭据前，先确定发起连接的是 Host 还是提供方。
- 已保存图像后来消失：Thread `tmp/` 是临时存储。重要输出应保留在其他位置。

实现已针对锁定模型适配器和模拟提供方响应进行检查。设置本身不发出原生提供方请求。当前限制和账户权限应查阅链接的上游/提供方文档，不要把推荐项视为对提供方支持的实时检查。
