---
title: 原生工具与 Web 服务提供方
description: 在 Harness UI 中启用宿主机网页搜索与抓取、第三方 Web 服务提供方、模型厂商原生工具，以及自动落盘到工作区的 AI 图像生成。
---

通过**宿主机 Web**（`capability: web`）由 Harness UI 执行公网搜索、正文抓取与文件下载；当希望由上游模型提供商直接执行搜索、网页读取、代码运行、文件检索或图像生成时，启用**厂商原生工具**（`NativeTool` 与 `native_image_generation`）。

| 任务                        | 能力声明                                        | 执行位置                      |
| --------------------------- | ----------------------------------------------- | ----------------------------- |
| 搜索并阅读公开网页          | 宿主机 `web` 或 `NativeTool`                    | Harness UI 或模型提供商       |
| 将 URL 文件下载到磁盘       | 宿主机 `web` 下载工具                           | 当前执行环境的可写存储        |
| 生成图像并保存文件          | `native_image_generation`                       | 提供商生成，保存至会话 `tmp/` |
| 针对项目代码执行命令        | 执行环境 Shell                                  | 当前选定的执行环境            |
| 提供商侧代码运行 / 文件检索 | `NativeTool`（`code_execution`、`file_search`） | 模型提供商托管环境            |

可运行 `a13n-harness-ui setup`（或在 WebUI 点击 **Add Agent**）完成向导式勾选，也可直接编辑智能体 YAML 并运行 `a13n-harness-ui config validate` 校验。

## 宿主机 Web 能力与内置后端

宿主机内置 Web 后端（`DuckDuckGoSearchProvider`、`HtmlScrapeProvider` 与 `HttpxWebClient`）无需 API 密钥即可开箱即用。对于依赖 JavaScript 动态渲染的交互式站点，请改用[宿主机浏览器](host-browser.md#browse-on-the-host)：

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

`search.mode` 支持 `host`、`native`、`auto`（可附加 `provider_settings`，如 `{openrouter: {engine: exa, max_results: 5}}`）或 `"off"`；`scrape.mode` 支持 `host` 或 `"off"`。常用上限参数包括 `deadline_seconds`（`60`）、`max_redirects`（`8`）、`max_search_results`（`10`）、`max_text_bytes`（`262144`）、`max_scrape_bytes`（`524288`）与 `max_download_bytes`（`268435456`）。

### Web 服务提供方账号

在 **Settings → Connections → Web providers** 或 `web_providers/*.yaml` 中注册第三方搜索/抓取账号（支持 `tavily`、`exa`、`firecrawl`、`brave`、`jina`、`perplexity`、`serpapi`、`keenable` 等），随后通过 `backend: web-provider-research` 或带回退顺序的 `backend_priority: [web-provider-research, default]` 引用：

```yaml title="web_providers/research.yaml"
schema_version: "1"
kind: web_provider
id: web-provider-research
name: Research search
type: tavily
configuration: {}
credential:
  api_key: {env: TAVILY_API_KEY} # 或 {credential_ref: key-research}
```

## 原生搜索与图像生成

在智能体的 `capabilities` 列表中合并 `NativeTool` 与 `native_image_generation`（同时将宿主机 `web.search.mode` 设为 `"off"`，避免重复暴露搜索工具，同时保留宿主机网页抓取与下载）：

```yaml title="agents/creative.yaml — capabilities 片段"
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_search
      external_web_access: true
  - capability: native_image_generation
    configuration:
      output_format: png
      provider_settings:
        openai:
          quality: auto
  - capability: web
    configuration:
      search: {mode: "off"}
      scrape: {mode: host}
```

生成的图片会自动保存到当前会话的 `tmp/` 目录下（重要产物建议复制到项目目录长期保存）。重复添加 `NativeTool` 条目即可按模型适配器支持情况启用更多厂商原生工具：

| 能力 / `NativeTool` `kind`             | 核心 `configuration` 字段说明                                                       |
| -------------------------------------- | ----------------------------------------------------------------------------------- |
| `NativeTool`（`kind: web_search`）     | 可选 `search_context_size`、`external_web_access`                                   |
| `NativeTool`（`kind: web_fetch`）      | 由模型提供商直接拉取 URL（可按需将宿主机 `web.scrape.mode` 设为 `"off"`）           |
| `native_image_generation`              | `output_format`、`provider_settings`（Harness UI 自动落盘至会话 `tmp/`）            |
| `NativeTool`（`kind: code_execution`） | 提供商侧沙箱计算（与本地项目文件相互独立）                                          |
| `NativeTool`（`kind: x_search`）       | xAI 原生 X 检索（如 `allowed_x_handles`）                                           |
| `NativeTool`（`kind: file_search`）    | `file_store_ids: [vs_your_store_id]`（提供商侧向量库/文件库 ID）                    |
| `NativeTool`（`kind: mcp_server`）     | `id`、公网可达 `url`、`allowed_tools`（本地或需鉴权服务请改用[宿主机 MCP](mcp.md)） |
| `NativeTool`（`kind: advisor`）        | `model`（提供商顾问模型 ID）与 `max_tokens`                                         |
