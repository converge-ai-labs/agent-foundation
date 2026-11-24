---
title: Native tools and Web providers
description: Enable Host web search and scraping, third-party Web providers, provider-native tools, and AI image generation with workspace saving in Harness UI.
---

Use **Host Web** (`capability: web`) to search, scrape, or download public pages from the Harness UI host, and **provider-native tools** (`NativeTool` and `native_image_generation`) when the Model's upstream provider should run search, fetch, code execution, file search, or image generation directly.

| Task                             | Capability                                     | Execution owner                             |
| -------------------------------- | ---------------------------------------------- | ------------------------------------------- |
| Search and read public web pages | Host `web` or `NativeTool`                     | Harness UI or the Model provider            |
| Download a URL to disk           | Host `web` download                            | The selected Environment's writable storage |
| Generate and save an image       | `native_image_generation`                      | Provider generation, saved to Thread `tmp/` |
| Run code against Project files   | Environment shell                              | The selected Environment                    |
| Provider-side code / file search | `NativeTool` (`code_execution`, `file_search`) | The Model provider's environment            |

Run `a13n-harness-ui setup` (or **Add Agent** in WebUI) for guided selection, or configure Agent YAML directly and check with `a13n-harness-ui config validate`.

## Host Web Capability: every built-in provider

Built-in Host Web backends (`DuckDuckGoSearchProvider`, `HtmlScrapeProvider`, and `HttpxWebClient`) require no API key. For JavaScript-rendered interactive sites, use [Host browser](host-browser.md#browse-on-the-host):

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

`search.mode` accepts `host`, `native`, `auto` (with optional `provider_settings`, such as `{openrouter: {engine: exa, max_results: 5}}`), or `"off"`; `scrape.mode` accepts `host` or `"off"`. Optional limits include `deadline_seconds` (`60`), `max_redirects` (`8`), `max_search_results` (`10`), `max_text_bytes` (`262144`), `max_scrape_bytes` (`524288`), and `max_download_bytes` (`268435456`).

### Web provider accounts

Register third-party search/scrape accounts (`tavily`, `exa`, `firecrawl`, `brave`, `jina`, `perplexity`, `serpapi`, `keenable`, etc.) under **Settings → Connections → Web providers** or `web_providers/*.yaml`, then reference the ID via `backend: web-provider-research` or `backend_priority: [web-provider-research, default]`:

```yaml title="web_providers/research.yaml"
schema_version: "1"
kind: web_provider
id: web-provider-research
name: Research search
type: tavily
configuration: {}
credential:
  api_key: {env: TAVILY_API_KEY} # or {credential_ref: key-research}
```

## Native search and image generation

Merge `NativeTool` entries and `native_image_generation` into an Agent's `capabilities` list (setting Host `web.search.mode: "off"` so the Model sees one search tool while keeping Host scraping and file downloads):

```yaml title="agents/creative.yaml — capabilities fragment"
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

Generated images are automatically saved into the Thread's `tmp/` directory (copy important outputs into a Project directory for long-term retention). Repeat `NativeTool` to enable additional provider-executed tools supported by your Model's adapter:

| Capability / `NativeTool` `kind`      | Key `configuration` fields                                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `NativeTool` (`kind: web_search`)     | Optional `search_context_size`, `external_web_access`                                                         |
| `NativeTool` (`kind: web_fetch`)      | Fetches URLs on the provider side (set Host `web.scrape.mode: "off"` if desired)                              |
| `native_image_generation`             | `output_format`, `provider_settings` (Harness UI saves images to Thread `tmp/`)                               |
| `NativeTool` (`kind: code_execution`) | Provider-side sandbox compute (separate from local Project files)                                             |
| `NativeTool` (`kind: x_search`)       | xAI native X search (such as `allowed_x_handles`)                                                             |
| `NativeTool` (`kind: file_search`)    | `file_store_ids: [vs_your_store_id]` (provider vector/file store IDs)                                         |
| `NativeTool` (`kind: mcp_server`)     | `id`, public `url`, `allowed_tools` (provider-dialed; use [Host MCP](mcp.md) for local/authenticated servers) |
| `NativeTool` (`kind: advisor`)        | `model` (provider advisor model ID) and `max_tokens`                                                          |
