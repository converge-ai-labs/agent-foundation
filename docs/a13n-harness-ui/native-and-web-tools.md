---
title: Native tools and Web providers
description: Enable web search, page scraping, provider-native search, and AI image generation with automatic workspace file saving in Harness UI.
---

Use **Host Web** to search, scrape, or download public pages through Harness UI. Use **native tools** when you want the Model's provider to run search, fetch, code execution, file search, or image generation directly. Both are configured on the Agent.

| Task                             | Tool / Capability                   | Where it runs                               |
| -------------------------------- | ----------------------------------- | ------------------------------------------- |
| Search and read public web pages | Host `web` or native search/fetch   | Harness UI or the Model provider            |
| Download a URL to disk           | Host `web` download                 | The selected Environment's writable storage |
| Generate and save an image       | `native_image_generation`           | Provider generation, saved to Thread `tmp/` |
| Run code against Project files   | Environment shell                   | The selected Environment                    |
| Provider-side code / file search | Native Code Execution / File Search | The Model provider's environment            |

## Guided setup

1. Run `a13n-harness-ui setup` (or **Add Agent** in WebUI) and connect your Model.
2. In **Choose native Agent tools**, toggle the provider-native tools supported by your connection and supply any required resource IDs (such as File Search store IDs).
3. Save the Agent and run a small prompt that invokes one selected tool to verify it end to end.

By default, enabling native Web Search disables Host search (keeping Host scrape/fetch/download), and enabling native Web Fetch disables Host scrape. Changing an Agent's Model later does not automatically rewrite its tools—review its Capabilities when switching providers.

## Host Web Capability: every built-in provider

The built-in Host Web backends (`DuckDuckGoSearchProvider` for search, `HtmlScrapeProvider` for Markdown scraping, and `HttpxWebClient` for fetch/download) require no API key.

Add `web` to an Agent's `capabilities`:

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

- `search.mode` accepts `host`, `native`, `auto`, or `off`; `scrape.mode` accepts `host` or `off`.
- Optional limits under `configuration`: `deadline_seconds` (60), `max_redirects` (8), `max_search_results` (10), `max_text_bytes` (262144), `max_scrape_bytes` (524288), `max_download_bytes` (268435456).
- Scraping reads static HTTP responses without executing JavaScript; use [Host browser](host-browser.md#browse-on-the-host) for interactive sites.

### Web provider accounts

Add third-party search/scrape providers (Tavily, Exa, Firecrawl, Brave Search, Jina, Perplexity, SerpApi, Keenable, etc.) under **Settings → Connections → Web providers → Add Web provider**, or create `web_providers/*.yaml`:

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

Select the account in the Agent's **Web options**, or reference it in YAML with `backend: web-provider-research` (or `backend_priority: [web-provider-research, default]` for fallback).

## Native search and image generation

Enable provider-native tools directly in an Agent's `capabilities`:

```yaml title="agents/creative.yaml — capabilities fragment"
capabilities:
  - capability: native_web_search
  - capability: native_image_generation
```

- Generated images are automatically saved into the Thread's `tmp/` directory and returned as workspace paths.
- Pass provider-specific native search options via `provider_settings` (for example, `provider_settings: {openrouter: {engine: exa}}`).
- Check your provider's documentation for supported native tool combinations on each model.
