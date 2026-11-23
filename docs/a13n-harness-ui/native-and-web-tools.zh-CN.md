---
title: 原生工具与 Web 提供方
description: 在 Harness UI 中启用联网搜索、网页抓取、模型提供方原生搜索以及自动保存到工作区的 AI 图像生成。
---

需要由 Harness UI 发起联网搜索、抓取公开网页或下载文件时，使用 **Host Web**；需要由模型提供方直接执行搜索、抓取、代码执行、文件检索或图像生成时，使用**原生工具**。两者均在智能体上配置。

| 任务                   | 工具 / 能力                       | 执行位置                          |
| ---------------------- | --------------------------------- | --------------------------------- |
| 搜索并阅读公开网页     | Host `web` 或原生搜索/抓取        | Harness UI 或模型提供方           |
| 将 URL 下载到本地磁盘  | Host `web` 下载                   | 所选环境的可写存储                |
| 生成并保存图像         | `native_image_generation`         | 提供方生成，自动保存到线程 `tmp/` |
| 针对项目文件执行代码   | 环境 Shell                        | 所选运行环境                      |
| 提供方侧代码或文件检索 | 原生 Code Execution / File Search | 模型提供方环境                    |

## 引导式配置

1. 运行 `a13n-harness-ui setup`（或在 WebUI 中点击 **Add Agent**）并连接模型。
2. 在 **Choose native Agent tools** 中勾选当前连接支持的原生工具，并按需填写资源 ID（如 File Search 存储 ID）。
3. 保存智能体后，发送一条会触发所选工具的简短测试消息，确认端到端可用。

默认情况下，启用原生 Web Search 会关闭 Host 搜索（保留抓取/获取/下载），启用原生 Web Fetch 会关闭 Host 抓取。后续更换智能体模型时不会自动改写工具列表，切换提供方后请检查能力配置。

## Host Web 能力：所有内置提供方

内置 Host Web 后端（`DuckDuckGoSearchProvider` 搜索、`HtmlScrapeProvider` 转 Markdown、`HttpxWebClient` 获取与下载）无需任何 API 密钥。

在智能体的 `capabilities` 中加入 `web`：

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

- `search.mode` 支持 `host`、`native`、`auto` 或 `off`；`scrape.mode` 支持 `host` 或 `off`。
- `configuration` 支持的可选限额：`deadline_seconds`（60）、`max_redirects`（8）、`max_search_results`（10）、`max_text_bytes`（262144）、`max_scrape_bytes`（524288）、`max_download_bytes`（268435456）。
- 抓取仅读取静态 HTTP 响应，不执行 JavaScript；需要交互式页面请使用[宿主机浏览器](host-browser.md#browse-on-the-host)。

### Web 提供方账号

在 **Settings → Connections → Web providers → Add Web provider** 添加第三方搜索/抓取服务（Tavily、Exa、Firecrawl、Brave Search、Jina、Perplexity、SerpApi、Keenable 等），或在 `web_providers/*.yaml` 中定义：

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

随后在智能体的 **Web options** 中勾选该账号，或在 YAML 中设置 `backend: web-provider-research`（支持 `backend_priority: [web-provider-research, default]` 作为回退顺序）。

## 原生搜索与图像生成

在智能体的 `capabilities` 中直接启用提供方原生工具：

```yaml title="agents/creative.yaml — capabilities 片段"
capabilities:
  - capability: native_web_search
  - capability: native_image_generation
```

- 生成的图像会自动保存到当前线程的 `tmp/` 目录，并以工作区路径返回。
- 可通过 `provider_settings` 传递提供方专属参数（例如 `provider_settings: {openrouter: {engine: exa}}`）。
- 各模型支持的具体原生工具组合请以对应提供方文档为准。
