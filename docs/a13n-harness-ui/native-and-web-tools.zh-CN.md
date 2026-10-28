---
title: 原生工具与 Web 提供方
description: 为 Agent 配置网页访问、选择提供方原生工具，并检查生成图像的保存结果。
---

需要通过 Harness UI 搜索、读取公开网页或下载文件时，使用 **Host Web**。需要所选 Model 的提供方执行搜索、图像生成等受支持操作时，使用**原生工具**。两者都配置在 Agent 上。

新建 Agent 可以从[设置向导](#guided-setup)开始；已有 Model 可以直接使用 [Host Web 配置流程](#host-web-capability-every-built-in-provider)。再根据该连接提供的选项添加原生工具。

| 任务                    | 选择                        | 执行位置                                         |
| ----------------------- | --------------------------- | ------------------------------------------------ |
| 搜索和读取公开网页      | Host `web`，或原生搜索/获取 | Harness UI，或 Model 提供方                      |
| 将 URL 保存为文件       | Host `web` 下载             | 所选 Environment 的可写存储                      |
| 生成并保存图像          | `native_image_generation`   | 提供方生成，再由 Harness UI 保存到 Thread `tmp/` |
| 对 Project 文件运行代码 | Environment shell           | 所选 Environment                                 |
| 运行提供方侧代码        | 原生 Code Execution         | 提供方环境                                       |
| 搜索提供方已索引文件    | 原生 File Search            | 已有的提供方存储                                 |

## 设置向导

1. 运行 `a13n-harness-ui setup`，或打开首次启动设置页面。
2. 选择连接，完成身份验证，再选择 Model。连接尚未配置好时，先阅读 [Model 与身份验证](models-and-authentication.md)。
3. 检查 **Choose native Agent tools**。终端中用上/下方向键移动，Space 切换勾选，Enter 确认。也可输入逗号分隔的编号或名称，或 `none`。没有原生候选的连接会跳过此步。
4. 为选中的工具提供资源：File Search 需要已有存储 ID，Remote MCP 需要公开服务器 URL 和标签，Advisor 需要提供方模型 ID。
5. 检查并保存 Agent。Esc 返回上一步；改变连接或 Model 会重置工具建议和资源输入。
6. 在对话中选择已保存的 Agent，发送一个使用单项工具的小任务。查看工具结果，不要仅凭回答判断配置是否成功。

不选原生工具时，新 Agent 仍有 Host 搜索、抓取、获取和下载。选择原生 Web Search 只关闭 Host 搜索；选择原生 Web Fetch 只关闭 Host 抓取。两种情况下都保留获取和下载。

**Add Agent** 使用同一选择器，复用已有 Model 时也是如此。**Add Model** 先保存连接，再提供创建 Agent 的选项。取消后一步仍会保留 Model。这些操作不会改变已有 Agent 及其工具选择。

### 推荐选项与连接差异

选择器根据已安装的适配器和模型配置确定候选项。默认勾选项是入门建议，不是账户权限测试。

| 连接                        | 选择器行为                                                                         |
| --------------------------- | ---------------------------------------------------------------------------------- |
| Codex 订阅                  | 默认勾选 Web Search，并启用实时网页访问。Image Generation 是可选项，默认不勾选。   |
| Grok 订阅                   | 默认勾选 Web Search。                                                              |
| 标准端点上的经审查 API 模型 | 默认勾选支持的推荐工具；也可选择其他适配器候选项。                                 |
| 未知模型 ID 或自定义端点    | 保留适配器候选项，但初始不勾选。                                                   |
| Chat Completions            | 提供 Host Web；选择器不会据此推断支持 Responses 原生工具。                         |
| Google 编程连接             | 候选项还取决于模型对函数工具与原生工具组合的支持。仅支持图像的模型需要专用 Agent。 |

Codex 和 Grok 订阅连接使用 Host Web 独立获取 URL 和下载文件；其选择器没有独立的原生 Web Fetch。需要 xAI 原生 SDK 工具时，选择 **xAI · Native SDK (gRPC)**，而非其 Chat Completions 或 Grok 订阅连接。

修改已有 Agent 的 Model 不会改写工具。切换协议或提供方时，应检查 Agent 的 Capability。`model_characteristics.capabilities` 描述 Model 接受的输入，不会启用 Agent 工具。

## Host Web Capability：所有内置提供方

本流程使用已配置的 Model 创建一个网页阅读 Agent。内置 Host 提供方无须单独的 Web API 密钥。

1. 运行 `a13n-harness-ui config path`，找到 `configuration_path` 指向的根 YAML。

2. 在该根目录的 `agents/` 下创建 `web-reader.yaml`。将 `model-primary` 替换为已保存的 Model ID。以下是完整 Agent 文件：

   ```yaml title="agents/web-reader.yaml"
   schema_version: "1"
   kind: agent
   id: agent-web-reader
   name: Web reader
   model: model-primary
   instructions: Read the requested sources and cite the URLs used.
   capabilities:
     - capability: web
       configuration:
         search: {mode: host, backend: default}
         scrape: {mode: host, backend: default}
   ```

3. 验证所选配置树：

   ```bash
   a13n-harness-ui config validate
   ```

   使用自定义根目录时，在两个 `config` 命令前都加上 `--config /absolute/path/a13n-harness-ui.yaml`，启动 Harness UI 时也使用同一个根配置。

4. 在新对话中选择 **Web reader**。发送“读取 https://example.com 并告诉我页面标题”，查看获取或抓取结果，再单独测试搜索。

5. 测试下载时，让 Agent 将一个公开文件下载到 Thread `tmp/` 并返回保存路径。打开文件检查结果。下载需要所选 Environment 提供可写文件权限。

修改已有 Agent 时，将 `web` 条目合并到其 `capabilities` 列表，或编辑已有的 `web` 条目。保留其他 Capability。接受后的修改用于后续 Run。

| 组件      | 内置提供方                                 | 结果                        |
| --------- | ------------------------------------------ | --------------------------- |
| 搜索      | `DuckDuckGoSearchProvider`，后端 `default` | 无密钥 HTML 搜索结果        |
| 获取/下载 | `HttpxWebClient` 配合 `HttpWebPolicy`      | 有界 HTTP(S) 读取或保存文件 |
| 抓取      | `HtmlScrapeProvider`，后端 `default`       | HTML/文本转换为 Markdown    |

抓取直接读取 HTTP 响应，不渲染 JavaScript，也不使用 Browser 的登录状态。交互式站点使用 [Browser Use](webui.md#browse-on-the-host)。Host 请求遵循捕获的主机允许策略；未设置策略时允许私有目标。

需要调整限制时，将以下字段合并到 `web` 条目的 `configuration`。这是设置片段，不是独立文件：

```yaml
deadline_seconds: 60
max_redirects: 8
max_search_results: 10
max_text_bytes: 262144
max_scrape_bytes: 524288
max_download_bytes: 268435456
download_concurrency: 4
```

### 搜索模式与后端选择

手写 Agent 时，显式选择搜索模式：

| `search.mode` | 效果                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------ |
| `host`        | 提供已绑定的 Host 搜索实现。标准 Harness UI 绑定 `default`。                               |
| `off`         | 移除此 Capability 的搜索功能，适合搭配独立原生搜索条目。                                   |
| `native`      | 注册上游原生 Web Search 工具。此快捷方式提供 `search_context_size`，而非完整原生工具选项。 |
| `auto`        | 注册原生搜索，并提供已绑定的 Host 搜索。不会探测兼容性，也不会在原生工具被拒后重试。       |

schema 默认值为 `auto`；设置向导会显式写入模式。抓取使用独立的 `host` 或 `off` 模式。向导写入以下组合：

| 所选原生工具 | `search.mode` | `scrape.mode` | Host 获取/下载 |
| ------------ | ------------- | ------------- | -------------- |
| 两者均未选   | `host`        | `host`        | 开启           |
| Web Search   | `off`         | `host`        | 开启           |
| Web Fetch    | `host`        | `off`         | 开启           |
| 两者均选择   | `off`         | `off`         | 开启           |

移除整个 `web` Capability 会移除其 Host 工具集。修改搜索或抓取模式后，普通获取/下载仍按各自配置工作。

`backend` 选择已绑定的实现。`backend_priority` 指定已绑定实现间的优先顺序，不能与 `backend` 同时使用。标准 Harness UI 只提供 `default`；在 YAML 中填写其他供应商名称不会安装提供方。自定义 Host 通过 `WebBinding` 提供实现，并负责其凭据和生命周期；见 [Harness Capability](../a13n-harness/capabilities.md)。不要在 Agent Capability 值中存放凭据。

嵌入代码未传配置而直接构造 `WebCapability()` 时，可以读取 `A13N_HARNESS_WEB_SEARCH_MODE`、`..._SEARCH_BACKEND`、`..._SEARCH_BACKEND_PRIORITY`、`..._SEARCH_CONTEXT_SIZE`，以及对应的抓取模式/后端变量。Harness UI 传入显式配置，因此应编辑 Agent YAML 来改变行为。

## 原生搜索与图像生成

本流程要求 Model 连接支持两种工具。先使用已正常工作的 Agent，确认身份验证已通过。

1. 打开所选配置树 `agents/` 下的 Agent 文件。

2. 添加下面两个原生条目。用示例中的 `web` 条目替换已有同名条目，并保留 Agent 的其他 Capability。以下是 Agent 片段：

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
         search: {mode: "off"}
         scrape: {mode: host}
   ```

3. 运行 `a13n-harness-ui config validate`；使用自定义根配置时带上同一个 `--config`。

4. 选择该 Agent，发送新消息要求生成一张简单图像。生成完成后应返回当前 Thread `tmp/` 中的图像路径。

5. 打开图像。将重要结果复制到 Project 的保留位置；Thread 临时文件可能被清理。

6. 用另一条消息测试原生搜索。Host `web` 条目保留网页抓取和下载，不再提供第二个搜索功能。

此流程显式开启图像生成；Codex 设置并不默认勾选它。配置验证检查声明，实际测试请求检查提供方和账户是否接受所选工具组合。

## 原生工具配置

以下各节是已有 Agent `capabilities` 列表的参考片段。合并所需条目，不要替换整个 Agent 文件。重复使用 `NativeTool` 可添加不同工具。`configuration: {kind: web_search}` 和 `configuration: {tool: {kind: web_search}}` 都有效。

### Web Search

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_search
      search_context_size: medium
```

原生搜索使用 Model 的身份验证。只需原生搜索时，将 Host `web.search.mode` 设为 `off`；需要同时提供两者时设为 `host`。Codex 设置添加 `external_web_access: true`；兼容提供方可接受 `false` 来请求缓存搜索。域名过滤等选项因适配器而异，添加前先查阅上游参数参考。

### Web Fetch

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_fetch
```

原生获取将 URL 内容带入提供方上下文。将 Host `web.scrape.mode` 设为 `off` 可避免同时提供两种网页读取方式。保留 Host 下载来保存文件。仅在适配器实现对应功能时，才添加 `max_uses`、`enable_citations`、`max_content_tokens` 等 Anthropic 专用选项。

### 图像生成

```yaml
capabilities:
  - capability: native_image_generation
    configuration:
      output_format: png
      quality: auto
```

Harness UI 自动提供保存器。使用此 Capability，不要再添加第二个原始原生图像工具。它将完成的图像保存到当前 Thread 的 `tmp/`，包括子级 Run 生成的图像，并指示模型在回复中展示保存位置。

历史保留保存引用而非原始图像字节。预览帧不保存。保存失败会使 Run 失败。Agent 之后可通过 `view` 读取图像；仅保存路径不会将像素发送给其他 Model。生成文件会在普通 Run 完成和重启后保留，但仍属于临时存储。自定义 Host 通过 [Harness 图像 Capability](../a13n-harness/capabilities.md)提供自己的异步保存器。

### X Search 与 Code Execution

以下片段适用于 xAI 原生 SDK 连接：

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

X Search 搜索提供方侧的 X 内容。其他兼容适配器也提供 Code Execution。它使用提供方侧文件和计算资源，不在所选 Environment 内，也不受 Host shell 审查策略控制。设置不会上传 Project 文件或下载生成的代码产物。处理本地 Project 文件时使用 Environment 工具。

### File Search

1. 在与 Model 相同的提供方账户/项目中创建搜索存储。

2. 上传或导入文件，并等待索引完成。

3. 将提供方存储标识添加到 Agent：

   ```yaml
   capabilities:
     - capability: NativeTool
       configuration:
         kind: file_search
         file_store_ids: [vs_your_store_id]
   ```

4. 提出一个已索引文件中包含答案的问题，检查结果是否引用该来源。

Responses 使用向量存储 ID，Google 使用文件搜索存储名称，xAI 使用 Collection ID。这些值指向提供方资源，而非本地路径。Google 选择器要求先取消其他原生工具，再选择 File Search；Host Web 可保持开启。索引、保留、访问和费用规则以提供方为准。

### Remote MCP

使用 Model 提供方能够访问的公开服务器：

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: mcp_server
      id: public-docs
      url: https://your-public-mcp.example/mcp
      allowed_tools: [search_docs]
```

将 URL 和工具名称替换为服务器实际值。连接服务器的是提供方；你的本地 stdio 服务器或 Host localhost URL 无法通过此声明访问。提供方执行的调用不使用 Host 工具审批；Responses 适配器会发送 `require_approval: never`。

需要 Harness UI 管理连接以及环境变量/header 凭据引用时，使用 [Host MCP](mcp.md)。原生工具的 `authorization_token` 和 `headers` 是普通值，这里不会解析 `${TOKEN}` 或 `{env: TOKEN}`。需要身份验证的原生 MCP 必须由可信 Host 集成在运行时加载凭据。不要在 Agent 文件和捕获的 Capability 配置中存放密钥。

### Advisor

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: advisor
      model: claude-opus-4-6
      max_tokens: 4096
```

将 `model` 替换为执行提供方接受的 advisor ID。Anthropic 使用自身模型 ID，OpenRouter 使用带提供方前缀的 slug。这是提供方侧咨询，不是 Harness UI Model 资源或子 Agent。配对规则、选项和计费由提供方负责。

### 原生 memory 工具：需要 Host 集成

原生 `kind: memory` 声明要求一个名称为 `memory` 的可执行函数工具，实现提供方的记忆命令。自定义 Host 集成必须提供该处理器和存储。标准选择器不提供此选项；Harness UI 的状态、任务和历史是独立功能。实现处理器时遵循上游原生 memory 集成契约。

## 验证与故障排查

按顺序检查三步：保存的配置已接受、对话选择了预期 Agent/Model、小型工具请求产生了预期结果。

| 现象                           | 下一步检查                                         |
| ------------------------------ | -------------------------------------------------- |
| 配置验证通过，但提供方拒绝工具 | 实际适配器、模型 ID、账户权限和工具组合            |
| Codex/Grok 没有原生 Fetch 选项 | 使用已有的 Host Web 获取/抓取工具                  |
| 搜索成功但没有文件             | 请求 Host 下载，并指定可写目的地                   |
| 生成图像之后消失               | 将重要结果移出 Thread `tmp/`                       |
| File Search 无结果             | 提供方存储 ID、索引状态和账户/项目权限             |
| Remote MCP 无法连接            | 先确定 Host 还是提供方发起连接，再检查可达性和凭据 |
| 原生 memory 报告缺少处理器     | 提供必需的 Host `memory` 函数                      |

[上游原生工具参考](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/)介绍适配器选项并链接提供方要求。设置只提供选项，不发送原生工具请求；使用上述小型测试检查实际连接。
