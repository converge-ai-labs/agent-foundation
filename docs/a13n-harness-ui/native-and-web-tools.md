# Native tools and Web providers

Native tools execute through the selected Model provider. Host tools execute through Harness UI and its Environment. They are independent: native search is not a local crawler, provider code execution is not your workspace shell, and provider file search does not index your Thread files.

Configure provider-native tools on the Agent. Available choices depend on its provider, Model and account. Native image generation saves a completed result to the current Thread's `tmp/` and returns a path; it does not switch models or providers.

| Selected native tool | Host Web behavior                                     |
| -------------------- | ----------------------------------------------------- |
| Native Web Search    | Host search off; Host fetch/download remain available |
| Native Web Fetch     | Host scrape off; Host fetch/download remain available |
| Neither              | Host search and scrape use their configured providers |

These are creation-time setup choices; [Host Web configuration](#host-web-capability-every-built-in-provider) can be edited separately.

## Guided setup

1. Run `a13n-harness-ui setup`, or follow the first-launch landing screen. Choose a subscription or an API provider, authenticate, and choose a model.
2. Review **Choose native Agent tools** (skipped when the connection has no native candidates). Use Up/Down to move, Space to toggle, and Enter to confirm. You can also type comma-separated numbers or names, or `none`. Recommended choices start checked; unchecking every item selects no native tools. Host Web remains enabled, including fetch/download, keyless search and HTML-to-Markdown scraping. Native Search disables only Host search; native Web Fetch disables only Host scrape. No separate web credential step is needed for the built-in providers.
3. Selecting File Search asks for existing provider store IDs. Remote MCP asks for a provider-accessible URL and server label. Advisor asks for its provider model ID. Setup does not create those remote resources or check account entitlement.
4. Review the final selection. Esc goes back; changing the connection or Model resets its tool suggestions and resource inputs.
5. Setup saves ordinary Agent `capabilities`. Edit the YAML later and run `a13n-harness-ui config validate`. Validation checks configuration, not provider entitlement.

**Add Agent** uses the same tool picker, including when reusing an existing Model. **Add Model** saves only the connection, then offers to create an Agent for that Model and configure its tools. Keeping only the Model or cancelling the subsequent Agent setup leaves the saved Model in place. Existing Agents and defaults are not silently changed. Model input modalities in `model_characteristics.capabilities` are separate from Agent tools.

### Recommendations and connection differences

Host Web fetch/download is included for every connection below, even where the table lists only native tools. Its search and scrape functions remain enabled only when the corresponding native tool is not selected.

| Connection                                                       | Preselected tools                                                     | Other guided native options                                                              |
| ---------------------------------------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Codex subscription                                               | Live native Web Search, Host Web Fetch, saved native Image Generation | Only these reviewed subscription tools                                                   |
| Grok subscription                                                | Native Web Search, Host Web Fetch                                     | No X Search or image-generation claim for this subscription transport                    |
| OpenAI Responses, reviewed GPT models                            | Native Web Search, saved native Image Generation, Host Web Fetch      | Code Execution, File Search, Remote MCP                                                  |
| Anthropic, reviewed Claude models                                | Native Web Search and Web Fetch                                       | Code Execution, Remote MCP, Advisor where the upstream model profile permits it          |
| Gemini 3 coding models                                           | Native Web Search and Web Fetch                                       | Code Execution, File Search with existing stores and without other native tools          |
| OpenRouter                                                       | Native Web Search for reviewed model IDs, Host Web Fetch              | Advisor                                                                                  |
| xAI native SDK (`xai:`)                                          | Native Web Search, Host Web Fetch                                     | X Search, Code Execution, File Search, Remote MCP                                        |
| Chat Completions, older Gemini combinations, other API providers | Host Web Search + Fetch                                               | No unsupported native tools inferred from an OpenAI-compatible API label                 |
| Unknown model IDs                                                | Host Web Search + Fetch                                               | Official adapter candidates may be selected explicitly; no model entitlement is inferred |
| Custom HTTP endpoints                                            | Host Web Search + Fetch                                               | Adapter candidates remain selectable; none are preselected. Verify endpoint support      |

API-key connections use the same picker as subscriptions. Selecting **OpenAI Responses** offers native Web Search and saved Image Generation even for custom model IDs or gateways; a non-default URL does not prove those tools are unsupported. Unreviewed model IDs and custom endpoints start with no native tools checked, so enabling them is an explicit choice rather than a claim of gateway compatibility. OpenAI Chat Completions does not expose these native tool objects; choose the Responses protocol only if your endpoint implements it.

The picker derives native candidates from the installed upstream adapter and relevant model profiles, not an independent runtime compatibility engine. A model need not appear in the short suggestions menu to expose adapter candidates: for example, OpenAI documents image-generation tool support for `gpt-4.1` and `gpt-4o` as well as newer models. Check [OpenAI image-generation model support](https://developers.openai.com/api/docs/guides/tools-image-generation#supported-models) and [web-search support](https://developers.openai.com/api/docs/guides/tools-web-search) for actual provider requirements. Provider restrictions, account access and billing still apply. Recommendations are creation-time defaults, not a migration or a policy automatically recomputed when you switch Models.

Both subscription connections currently use a Responses adapter, which has **no independent `WebFetchTool`**. Their always-enabled **Host Web** provides HTTP fetch, HTML scraping and downloads. xAI search may itself browse pages, but that does not implement a separate WebFetch tool.

For xAI's full native tool set, choose **xAI · Native SDK (gRPC)**, not **xAI · Chat Completions** and not Grok subscription. It uses `XAI_API_KEY` or the selected stored key with the upstream SDK's default endpoint; the wizard does not ask for an HTTP base URL. Example Model:

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

Gemini image-only models cannot run the coding template's function tools. Add such a Model separately and author a dedicated Agent with compatible capabilities rather than enabling it on the coding starter. Earlier Gemini models also cannot combine native tools with the starter's function tools. See [Google tool combinations](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#google-tool-combinations).

## Native tool configuration

Put each selection under an Agent's `capabilities`. Repeated `NativeTool` entries compose different native tools. Both flat `configuration: {kind: web_search}` and explicit `configuration: {tool: {kind: web_search}}` use upstream deserialization. Do not add a second raw `ImageGenerationTool` alongside `native_image_generation`.

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

The second entry retains Host fetch/scrape/download without a duplicate search function. Native search uses Model authentication, not a separate search-provider API key. Provider-specific options include `allowed_domains`, `blocked_domains`, `max_uses`, `user_location`, and `external_web_access`; not every provider implements every option. Codex recommendations set `external_web_access: true` so current web content is available out of the box. Set it to `false` only when you explicitly want cached search. OpenRouter uses its server-side web-search tool and provider-managed search billing. Consult the [upstream parameter support table](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#web-search-tool) before adding filters.

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

This configured example is for Anthropic. Google maps `WebFetchTool` to URL context and does not implement these Anthropic parameters; use only `kind: web_fetch` there. Fetch brings URL content into the model's context. It does not recursively crawl a site, run a browser, save a local file, or provide the Host's HTML-to-Markdown API. References: [Anthropic Web Fetch](https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-fetch-tool), [Google URL context](https://ai.google.dev/gemini-api/docs/url-context).

### Image Generation

```yaml
capabilities:
  - capability: native_image_generation
    configuration:
      output_format: png
      quality: auto
```

Harness UI injects its saver; no saver path or import is authored in YAML. The capability instructs the model to present saved images in replies using `![brief description](<saved path or URL>)`, preserving the saver-provided location rather than inventing a URL. Completed images are saved under the **current Thread's `tmp/`**, including child Runs. Replies, transcripts and resumed history contain saved paths instead of raw image bytes. Preview frames are not saved as final images. A save failure fails the Run rather than reporting an unavailable image. The Agent can later `view` the file, but a stored path does not automatically resend its pixels to another Model.

Thread scratch survives ordinary Run completion and restart, but may be pruned. Copy important images to a retained Project destination. Generated images are not user attachments. Custom Hosts supply their own async saver to [Harness's native image capability](../a13n-harness/capabilities.md). Native options and supported image models remain defined [upstream](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#image-generation-tool).

### X Search and Code Execution

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

X Search requires the xAI native SDK route. The native tool also accepts date limits and image/video-understanding options; see [X Search](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#x-search-tool).

Code Execution is available through compatible Responses, Anthropic, Google and xAI adapters. It runs in the provider's environment, not the selected local/sandbox Environment, and is not governed by Host shell review. Optional `files` are provider-uploaded file references on supported adapters. Setup neither uploads workspace files nor copies provider-generated code artifacts to Thread tmp. The image saver is not a general provider-artifact downloader. See [Code Execution](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#code-execution-tool).

### File Search

Create the provider resources first, upload/import the files, and wait for indexing to complete. Then select the tool and enter its store identifiers:

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: file_search
      file_store_ids: [vs_your_store_id]
```

| Provider          | Value in `file_store_ids`                              | Resource setup                                                                  |
| ----------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------- |
| OpenAI Responses  | Vector store IDs such as `vs_...`                      | [OpenAI File Search](https://platform.openai.com/docs/guides/tools-file-search) |
| Google Gemini API | File search store names such as `fileSearchStores/...` | [Gemini File Search](https://ai.google.dev/gemini-api/docs/file-search)         |
| xAI native SDK    | Collection IDs                                         | [xAI Collections Search](https://docs.x.ai/developers/tools/collections-search) |

These are not filenames or Thread paths. Use the same provider account/project as the Model connection. Google File Search must not be combined with other native tools in this picker; Host web can remain selected. Google's temporary uploaded file object and its imported search-store data have different lifetimes: do not assume the search store expires with the temporary upload. xAI additionally maps `max_num_results`, `instructions` and `retrieval_mode`; do not assume these fields have identical effects in the other adapters. Provider resource access, retention and charges remain provider-owned.

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

Supported adapters are Responses, Anthropic and xAI. The **provider** connects to the remote server; a local stdio server or a localhost URL on your machine is not reachable that way. The wizard configures public servers only. Review the server and its tool permissions: these are provider-executed tools, not Host tool calls, and the Responses adapter sends `require_approval: never`.

Upstream `authorization_token` and `headers` are ordinary strings; `${TOKEN}` or `{env: TOKEN}` in a native-tool specification is **not** resolved by Harness UI. Do not place secrets in Agent capabilities, which are captured in Run compositions. For authenticated native MCP, a trusted Host capability must load credentials at runtime and construct the upstream tool. Alternatively, use Harness UI's [Host MCP integration](mcp.md), which already owns environment/header credential resolution. That integration uses Agent `mcp_servers` and is distinct from provider-hosted `MCPServerTool`.

### Advisor

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: advisor
      model: claude-opus-4-6
      max_tokens: 4096
```

Use an Anthropic advisor model ID, or an OpenRouter model slug such as `anthropic/claude-opus-4.6` on the OpenRouter route. This is not a Harness UI Model resource ID, a child Agent, or a second Host connection. The executor's provider handles consultation and billing. Anthropic executor/advisor pairing restrictions still apply. OpenRouter maps `max_tokens` to `max_completion_tokens`, fixes `forward_transcript` to false, and ignores Anthropic-only `max_uses` and `caching`. See [Advisor](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#advisor-tool).

### Memory: a Host integration prerequisite

`NativeTool` can deserialize `kind: memory`, but **that alone is not an implementation**. Anthropic requires an executable function tool named exactly `memory` implementing its memory commands. Harness UI's ordinary state/task/history features are not that handler. Therefore Memory is explained but not selectable in the stock wizard. A trusted Host integration must provide storage and the handler, then compose `NativeTool(MemoryTool())` with it. Follow the [upstream Memory example and command contract](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/#memory-tool); no parallel memory subsystem is introduced here.

## Native search and image generation

Agent capabilities compose independently. For a compatible Model, add the following entries to its `capabilities` list:

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

`NativeTool` accepts the upstream native tool specification, including the explicit `tool: {kind: web_search}` form. Multiple entries can select different native tools. The example enables live search, matching the Codex setup default; set `external_web_access: false` to explicitly request cached search on compatible providers. Options and availability depend on the actual Model/provider, not merely its brand or API compatibility label.

Generated images are saved in Thread scratch storage. Copy important results to your Project; see [image saving and retention](#image-generation).

The `web` entry above keeps fetch, scrape, and download available without registering a second search tool. Set its `search.mode` to `host` to expose the independent keyless DuckDuckGo search implementation, even alongside native search. Native search uses the selected Model provider's account and billing; Host search uses the UI's Web transport. Neither is implied merely by installing a Capability.

Setup writes reviewed starter choices into **new Agent resources**: Codex uses live native search plus native image generation; Grok subscription uses native search. Compatible API templates use native tools where reviewed, otherwise Host search. Custom endpoints and older incompatible tool combinations are not assumed to support native tools. Existing resources and Agents are not migrated, and changing Models does not rewrite their tool selections. Edit the Agent YAML and run `a13n-harness-ui config validate` to change them.

## Host Web Capability: every built-in provider

New Agents include `web` by default. Each Run gets fresh instances of the following Host providers; native search/fetch selections control which Host functions are exposed.

| Component          | UI implementation                       | Credentials / configuration                                                                                                                                                                      |
| ------------------ | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Search             | `DuckDuckGoSearchProvider`              | Keyless DuckDuckGo HTML search; select `search.mode: host`. Backend ID is `default`. Subject to public endpoint availability and rate limits.                                                    |
| Fetch and download | `HttpxWebClient` with `PublicWebPolicy` | Public HTTP/S requests, no browser login or separate API key. Bounds come from `WebConfiguration`. Private network targets are rejected.                                                         |
| Scrape             | `HtmlScrapeProvider`                    | Fetches public HTML/text through the same client and converts to Markdown. Select `scrape.mode: host`; backend ID is `default`. Does not render JavaScript, bypass logins, or recursively crawl. |

A complete explicit Host configuration:

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

Fetch supports bounded inline reads and download writes through the current Environment's file authority. A Model/provider account does not grant additional filesystem or network access to these Host tools.

### Search modes and backend selection

- `off`: no search in this Capability; fetch, scrape and download remain. Use with a separate native search capability to avoid duplicate search.
- `host`: use a bound Host search provider. The stock UI binds only `default` (DuckDuckGo).
- `native`: register upstream `WebSearchTool`; do not expose Host search. Unlike an explicit `NativeTool` entry, this convenience path offers only `search_context_size` (`low`, `medium`, `high`).
- `auto`: register native search and make bound Host search available. It is not a provider-compatibility probe or automatic retry after native-tool rejection. Use explicit selections for known connections.

Scrape mode is `host` or `off`. Disabling scrape does not disable ordinary fetch/download. Setup writes the two modes independently:

| Selected native tools | `search.mode` | `scrape.mode` | Host fetch/download |
| --------------------- | ------------- | ------------- | ------------------- |
| Neither               | `host`        | `host`        | Enabled             |
| Web Search            | `off`         | `host`        | Enabled             |
| Web Fetch             | `host`        | `off`         | Enabled             |
| Both                  | `off`         | `off`         | Enabled             |

This is creation-time configuration, not a runtime fallback. Existing authored YAML is not rewritten; removing the entire `web` capability explicitly removes the Host Web toolset.

`backend` selects one exact bound backend ID. `backend_priority` lists preferences among bound backends and cannot be combined with `backend`. These values select existing implementations, not vendor names to install. A custom embedding Host can supply async `WebSearchProvider` / `WebScrapeProvider` implementations through `WebBinding(search_backends=..., scrape_backends=...)`; that Host owns API credentials, client lifetime, policy and error translation. See [Harness capabilities](../a13n-harness/capabilities.md) for the provider ports and execution contract. Stock Harness UI does not offer YAML-only third-party backend installation. Its built-in providers need no keys, so there is no `api_key` field on `WebConfiguration`. Model credentials do not configure Host search/scrape. A key-based provider requires a real Host integration that resolves its own credential references (for example, an environment variable) and binds the provider; writing `backend: tavily` or an unused secret field cannot enable that service. Do not put literal keys in Agent capability configuration.

For embedding code constructing `WebCapability()` with no explicit configuration, `WebConfiguration.from_environment()` recognizes `A13N_HARNESS_WEB_SEARCH_MODE`, `A13N_HARNESS_WEB_SEARCH_BACKEND`, `A13N_HARNESS_WEB_SEARCH_BACKEND_PRIORITY`, `A13N_HARNESS_WEB_SEARCH_CONTEXT_SIZE`, and the corresponding `A13N_HARNESS_WEB_SCRAPE_MODE`, `..._BACKEND`, `..._BACKEND_PRIORITY`. Explicit Agent YAML is authoritative in Harness UI; do not expect these process defaults to override saved configuration.

## Validation and troubleshooting

- Configuration accepted but provider rejects a tool: check the actual adapter, model ID, account and tool combination. “OpenAI-compatible” does not imply Responses-native tools.
- Native fetch absent on Codex/Grok: expected; Host Web is included by default. Do not add an unsupported raw `web_fetch` entry to those subscription Agents.
- Search works but a download is missing: native search/fetch returns provider context, not a local file. Use the included Host `web` tools and request a download destination.
- Memory fails with a missing `memory` tool: provide the Host handler; enabling its native schema alone is insufficient.
- File Search has no results: verify provider store IDs, indexing status and account/project access, not local filesystem paths.
- MCP cannot connect: determine whether the Host or the provider is making the connection before debugging URLs or credentials.
- Saved image disappears later: Thread `tmp/` is scratch storage. Retain important output elsewhere.

The implementation is checked against the locked model adapters and simulated provider responses. Setup itself makes no native provider request. Consult the linked upstream/provider docs for current limits and account entitlements rather than treating recommendations as a live capability check.
