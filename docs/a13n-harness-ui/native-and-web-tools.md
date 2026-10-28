---
title: Native tools and Web providers
description: Give an Agent web access, choose provider-native tools, and verify saved image output.
---

Use **Host Web** to search, read public pages, or download files through Harness UI. Use **native tools** when you want the selected Model's provider to perform a supported operation, such as search or image generation. Both are configured on the Agent.

Start with [guided setup](#guided-setup) for a new Agent, or the [Host Web recipe](#host-web-capability-every-built-in-provider) for an existing Model. Add native tools after checking the choices offered for that connection.

| Task                             | Choose                             | Where the work happens                                      |
| -------------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| Search and read public web pages | Host `web`, or native search/fetch | Harness UI, or the Model provider                           |
| Save a URL as a file             | Host `web` download                | The selected Environment's writable storage                 |
| Generate and save an image       | `native_image_generation`          | Provider generation, then Harness UI saves to Thread `tmp/` |
| Run code against Project files   | Environment shell                  | The selected Environment                                    |
| Run provider-side code           | Native Code Execution              | The provider's environment                                  |
| Search indexed provider files    | Native File Search                 | An existing provider store                                  |

## Guided setup

1. Run `a13n-harness-ui setup`, or open the first-launch setup screen.
2. Choose a connection, authenticate, and select a Model. See [Models and authentication](models-and-authentication.md) if the connection is not ready.
3. Review **Choose native Agent tools**. In the terminal, move with Up/Down, toggle with Space, and confirm with Enter. You can also enter comma-separated numbers or names, or `none`. Connections without native candidates skip this step.
4. Supply resources for the tools you select: existing store IDs for File Search, a public server URL and label for Remote MCP, or a provider model ID for Advisor.
5. Review and save the Agent. Esc returns to the preceding step; changing the connection or Model resets its tool suggestions and resource inputs.
6. Select the saved Agent in a conversation and send a small task that uses one selected tool. Inspect the tool result, rather than judging configuration success from the answer alone.

Selecting no native tools still gives the new Agent Host search, scraping, fetch, and download. Selecting native Web Search turns off only Host search; selecting native Web Fetch turns off only Host scraping. Fetch and download remain available in both cases.

**Add Agent** uses the same picker, including with an existing Model. **Add Model** first saves the connection, then offers to create an Agent. Cancelling that later step keeps the Model. These actions leave existing Agents and their tool choices unchanged.

### Recommendations and connection differences

The picker uses the installed adapter and model profiles to determine candidates. Its checked items are starter suggestions, not an account-access test.

| Connection                                  | Picker behavior                                                                                                           |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Codex subscription                          | Web Search starts checked, with live web access. Image Generation is optional and starts unchecked.                       |
| Grok subscription                           | Web Search starts checked.                                                                                                |
| Reviewed API model on its standard endpoint | Supported recommended tools start checked. Other adapter candidates can be selected.                                      |
| Unknown model ID or custom endpoint         | Adapter candidates remain available, with none checked initially.                                                         |
| Chat Completions                            | Host Web is available; the picker does not infer Responses-native tools.                                                  |
| Google coding connection                    | Candidates also depend on the model's function/native-tool combination support. Image-only models need a dedicated Agent. |

Codex and Grok subscription connections use Host Web for separate URL fetches and downloads; their picker has no independent native Web Fetch. For xAI's native SDK tools, choose **xAI · Native SDK (gRPC)** rather than its Chat Completions or Grok subscription connection.

Changing an existing Agent's Model does not rewrite its tools. Review its Capabilities when switching protocols or providers. Model input declarations in `model_characteristics.capabilities` describe accepted input; they do not enable Agent tools.

## Host Web Capability: every built-in provider

This recipe creates a web-reading Agent using a Model you have already configured. The built-in Host providers need no separate web API key.

1. Run `a13n-harness-ui config path`. Locate the root YAML shown as `configuration_path`.

2. Create `agents/web-reader.yaml` beside that root's `agents/` peers. Replace `model-primary` with your saved Model ID. This is a complete Agent file:

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

3. Validate the selected configuration tree:

   ```bash
   a13n-harness-ui config validate
   ```

   If you use a custom root, put `--config /absolute/path/a13n-harness-ui.yaml` before both `config` commands and use that same root when starting Harness UI.

4. Select **Web reader** in a new conversation. Ask: “Read https://example.com and report its page title.” Inspect the fetch or scrape result. Then try a search separately.

5. To test downloads, ask the Agent to download a public file to its Thread `tmp/` and return the saved path. Open that file to check the result. Downloads require writable file authority in the selected Environment.

For an existing Agent, merge the `web` entry into its `capabilities` list, or edit its existing `web` entry. Keep the other Capabilities. Accepted edits apply to subsequent Runs.

| Component      | Built-in provider                             | Result                               |
| -------------- | --------------------------------------------- | ------------------------------------ |
| Search         | `DuckDuckGoSearchProvider`, backend `default` | Keyless HTML search results          |
| Fetch/download | `HttpxWebClient` with `HttpWebPolicy`         | Bounded HTTP(S) reads or saved files |
| Scrape         | `HtmlScrapeProvider`, backend `default`       | HTML/text converted to Markdown      |

Scraping reads the HTTP response without rendering JavaScript or using a Browser login. Use [Browser Use](webui.md#browse-on-the-host) for interactive sites. Host requests follow the captured allowed-host policy; an unset policy permits private destinations.

To adjust limits, merge the following fields into the `web` entry's `configuration`. These are settings, not a standalone file:

```yaml
deadline_seconds: 60
max_redirects: 8
max_search_results: 10
max_text_bytes: 262144
max_scrape_bytes: 524288
max_download_bytes: 268435456
download_concurrency: 4
```

### Search modes and backend selection

Choose an explicit search mode for a hand-authored Agent:

| `search.mode` | Effect                                                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `host`        | Exposes the bound Host search provider. Stock Harness UI binds `default`.                                                       |
| `off`         | Removes this Capability's search function; useful with a separate native search entry.                                          |
| `native`      | Registers the upstream native Web Search tool. This shortcut exposes `search_context_size`, not the full native-tool options.   |
| `auto`        | Registers native search and makes bound Host search available. It does not probe compatibility or retry a rejected native tool. |

The schema default is `auto`; guided setup writes explicit modes. Scraping has its own `host` or `off` mode. Setup writes these combinations:

| Selected native tools | `search.mode` | `scrape.mode` | Host fetch/download |
| --------------------- | ------------- | ------------- | ------------------- |
| Neither               | `host`        | `host`        | Enabled             |
| Web Search            | `off`         | `host`        | Enabled             |
| Web Fetch             | `host`        | `off`         | Enabled             |
| Both                  | `off`         | `off`         | Enabled             |

Removing the entire `web` Capability removes its Host toolset. Changing a search or scrape mode leaves ordinary fetch/download independently configured.

`backend` selects an already bound implementation. `backend_priority` specifies preferences among bound implementations and cannot be combined with `backend`. Stock Harness UI provides only `default`; naming another vendor in YAML does not install a provider. A custom Host supplies providers through `WebBinding` and owns their credentials and lifecycle; see [Harness Capabilities](../a13n-harness/capabilities.md). Keep credentials out of Agent Capability values.

Embedding code that constructs `WebCapability()` without configuration can read `A13N_HARNESS_WEB_SEARCH_MODE`, `..._SEARCH_BACKEND`, `..._SEARCH_BACKEND_PRIORITY`, `..._SEARCH_CONTEXT_SIZE`, and the corresponding scrape mode/backend variables. Harness UI supplies explicit configuration, so edit the Agent YAML to change its behavior.

## Native search and image generation

Use this recipe with a Model whose connection supports both tools. Start from a working Agent so authentication is already tested.

1. Open the Agent file under `agents/` in the selected configuration tree.

2. Add the two native entries below. Replace any existing `web` entry with the one shown, keeping the Agent's other Capabilities. This is an Agent fragment:

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

3. Run `a13n-harness-ui config validate`, using the same `--config` root if applicable.

4. Select that Agent and send a new message asking for a small generated image. A completed generation should return a saved image path in the current Thread's `tmp/`.

5. Open the image. Copy important output to a retained Project destination; Thread temporary files may be pruned.

6. Test native search in a separate message. The Host `web` entry keeps page scraping and downloads available without a second search function.

This recipe opts into image generation. Codex setup does not preselect it. Validation checks the configuration; the test request checks whether the provider and account accept the chosen tool combination.

## Native tool configuration

The sections below are reference fragments for an existing Agent's `capabilities` list. Merge the needed entries rather than replacing the whole Agent file. Repeat `NativeTool` to add different tools. Both `configuration: {kind: web_search}` and `configuration: {tool: {kind: web_search}}` are accepted.

### Web Search

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_search
      search_context_size: medium
```

Use the Model's authentication for native search. Set Host `web.search.mode: off` if you want only native search, or `host` to expose both independently. Codex setup adds `external_web_access: true`; compatible providers can accept `false` to request cached search. Domain filters and other options vary by adapter; consult the upstream parameter reference before adding them.

### Web Fetch

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: web_fetch
```

Native fetch brings a URL into provider context. Set Host `web.scrape.mode: off` to avoid offering both forms of page reading. Keep Host download for saving files. Anthropic-specific options such as `max_uses`, `enable_citations`, and `max_content_tokens` should only be added for an adapter that implements them.

### Image Generation

```yaml
capabilities:
  - capability: native_image_generation
    configuration:
      output_format: png
      quality: auto
```

Harness UI supplies the saver automatically. Use this Capability instead of adding a second raw native image tool. It saves completed images under the current Thread's `tmp/`, including for child Runs, and instructs the model to present the saved location in its reply.

History retains saved references instead of raw image bytes. Preview frames are excluded. A save failure fails the Run. An Agent can later read the image with `view`; storing its path alone does not send its pixels to another Model. Generated files survive ordinary Run completion and restart, but remain temporary storage. Custom Hosts supply their own async saver through [Harness's image Capability](../a13n-harness/capabilities.md).

### X Search and Code Execution

This fragment is for an xAI native SDK connection:

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

X Search searches provider-side X content. Code Execution is also offered by other compatible adapters. It uses provider-side files and compute, outside the selected Environment and Host shell-review policy. Setup does not upload Project files or download generated code artifacts. Use Environment tools for work on local Project files.

### File Search

1. Create a search store in the same provider account/project as the Model.

2. Upload or import your files there and wait for indexing.

3. Add the provider's store identifiers to the Agent:

   ```yaml
   capabilities:
     - capability: NativeTool
       configuration:
         kind: file_search
         file_store_ids: [vs_your_store_id]
   ```

4. Ask a question whose answer appears in one indexed file. Check that the result references that source.

Use vector-store IDs for Responses, file-search store names for Google, or collection IDs for xAI. The values identify provider resources, not local paths. The Google picker requires deselecting other native tools before File Search; Host Web can remain enabled. Consult the provider for indexing, retention, access, and charges.

### Remote MCP

Use a public server reachable by the Model provider:

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: mcp_server
      id: public-docs
      url: https://your-public-mcp.example/mcp
      allowed_tools: [search_docs]
```

Replace the URL and tool names with the server's actual values. The provider connects to this server; your local stdio server or Host localhost URL is not reachable through this declaration. Provider-executed calls do not use Host tool approval; the Responses adapter sends `require_approval: never`.

Use [Host MCP](mcp.md) for Harness UI-managed connections and environment/header credential references. Native-tool `authorization_token` and `headers` are plain values: `${TOKEN}` and `{env: TOKEN}` are not resolved here. Authenticated native MCP needs a trusted Host integration that loads credentials at runtime. Keep secrets out of the Agent file and captured Capability configuration.

### Advisor

```yaml
capabilities:
  - capability: NativeTool
    configuration:
      kind: advisor
      model: claude-opus-4-6
      max_tokens: 4096
```

Replace `model` with an advisor ID accepted by the execution provider. Anthropic uses its model IDs; OpenRouter uses provider-qualified slugs. This is a provider-side consultation, not a Harness UI Model resource or child Agent. Pairing rules, options, and billing belong to the provider.

### Native memory tool: a Host integration prerequisite

The native `kind: memory` declaration requires an executable function tool named `memory` that implements the provider's memory commands. A custom Host integration must supply that handler and storage. The standard picker omits this option; Harness UI's state, tasks, and history are separate features. Follow the upstream native-memory integration contract when implementing the handler.

## Validation and troubleshooting

Check three stages in order: the saved configuration is accepted, the conversation selects the intended Agent/Model, and a small tool request produces the expected result.

| Symptom                                            | Check next                                                                               |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Configuration validates, provider rejects the tool | Actual adapter, model ID, account access, and tool combination                           |
| Codex/Grok has no native Fetch choice              | Use the included Host Web fetch/scrape tools                                             |
| Search succeeds but no file appears                | Request a Host download with an explicit writable destination                            |
| Generated image disappears later                   | Copy important output out of Thread `tmp/`                                               |
| File Search returns nothing                        | Provider store ID, indexing state, and account/project access                            |
| Remote MCP cannot connect                          | Determine whether the Host or provider connects, then check reachability and credentials |
| Native memory reports a missing handler            | Supply the required Host `memory` function                                               |

The [upstream native-tool reference](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/) describes adapter options and links to provider requirements. Setup offers choices without issuing a native-tool request; use the small test above to check your actual connection.
