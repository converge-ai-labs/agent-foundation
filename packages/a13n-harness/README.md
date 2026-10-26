# Agent Harness

Harness (`a13n-harness`) is the embeddable execution SDK behind [Harness UI](../a13n-harness-ui/README.md) and [Service](../a13n-service/README.md). It builds reusable agents, runs them with current tools and Environments, streams observations, and returns state for continuation. Start with the [offline quickstart](../../docs/a13n-harness/getting-started.md); the Python import is `a13n_harness`.

## Capability composition

Build an `AgentSpec` with only the behavior your application needs. For example, to expose Environment tools and working state:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import WorkingStateCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration

executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
    capabilities=(
        DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
        WorkingStateCapability(),
    ),
)
```

Supply a Host-owned `EnvironmentSource` to each `run(..., environment=...)`, or use `environments={...}` for named mounts. Harness invokes the source’s readiness preparation on first use, then opens and closes fresh fixed-target executions; your application selects their configuration, current state, access policy, and when to destroy a backing target. See [Environments](../../docs/a13n-harness/environments.md) and [Capabilities](../../docs/a13n-harness/capabilities.md).

## Browser tools

Connect to an existing Chromium instance through a trusted CDP endpoint. The application prepares Chromium and its profile; Harness does not install or launch a browser. `cdp-use` is included in the normal Python dependencies.

```python
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend


async def inspect_page(cdp_url: str) -> str:
    executable = HarnessBuilder().build(
        AgentSpec(model="openai-responses:gpt-5"),
        capabilities=(BrowserUseCapability(),),
        output_type=str,
    )
    async with CdpBrowserBackend.connect(cdp_url) as browser:
        result = await executable.run(
            "Navigate to https://example.com and describe the page.",
            bindings=RunBindings.embedded(browser_backend=browser),
        )
        return result.output_or_raise()
```

The context manager creates an owned tab scope, closes its tabs on exit, and disconnects its CDP client while leaving the browser process and unrelated tabs alive. The endpoint can be an HTTP(S) CDP base URL or WS(S) debugger URL. Keep CDP private: it grants broad browser control.

For a long-lived Host, connect once with `Chromium.connect(cdp_url)` from `a13n_harness.capabilities.browser.cdp`, create independent `CdpBrowserBackend(runtime)` scopes, and open a tab with `await scope.tabs("new")`. Reuse a scope across Runs, supplying fresh authorization through each Run binding. `await scope.attach(target_id)` admits an exact surviving page during Host-directed reattachment. A scope is tab ownership, not account isolation; all scopes on one profile share login/storage state.

Before human input, await `scope.pause()` for every scope on the controlled browser and prevent new scope admission. After revoking viewer input, call `scope.resume()`. Pending tools return a fresh-observation instruction without replaying their original action. `scope.retire()` revokes access; `await scope.close()` also closes owned tabs; `await runtime.close()` disconnects without destroying Chromium. Run completion performs none of these Host lifecycle operations.

The model receives nine tools: `browser_observe`, `browser_read`, `browser_navigate`, `browser_act`, `browser_wait`, `browser_tabs`, `browser_files`, `browser_dialog`, and `browser_screenshot`.

A typical task navigates, observes a focused region, acts on an `eN` reference or a unique semantic locator, and reads or waits to verify the outcome. `browser_act` takes one action at a time, including fill/type, click/hover, key chords, check, native select, and scroll. For example, its arguments can be:

```json
{"action": {"kind": "fill", "target": {"kind": "locator", "role": "textbox", "name": "Email"}, "text": "ada@example.com"}}
```

Observation filters and pagination keep large pages manageable; reading text, links, tables, or forms preserves the existing refs. Both support same-process and out-of-process frames. State values distinguish disabled, readonly, checked, selected, and expanded controls; password values are redacted. Inspect `dispatch` separately from `verification`: input delivery alone does not establish that a form submitted or a purchase succeeded.

Screenshots return JPEG content plus an `sN` identifier and CSS dimensions. For visual-only controls, click or hover accepts normalized coordinates in the latest viewport/clip screenshot; the reference is consumed once and checked for stale page/viewport state. Prefer semantic targets for moving content. Full-page screenshots are for observation. Use `browser_files` for Host-path uploads and scoped download status, and `browser_dialog` for JavaScript prompts.

Browser access is outside the Agent Environment's sandbox and cannot enforce restrictive `allowed_hosts`. The Host must authorize the captured Run rather than relying on its Thread's defaults. See the [browser contract](../../spec/a13n-harness/23-browser-use.md).

Run the opt-in native fixtures with an already installed Chromium executable:

```bash
uv run --package a13n-harness --group test pytest \
  packages/a13n-harness/tests/test_browser_cdp_native.py \
  packages/a13n-harness/tests/test_browser_workflows_native.py \
  --browser-executable=/path/to/chromium
```

The fixtures use disposable profiles and a basic password store to avoid desktop keyring prompts. If the test environment requires disabling Chromium's sandbox, explicitly add `--browser-no-sandbox`; it is never selected automatically for root.

## Model construction

Pass a native model directly through `HarnessBuilder.build(model=...)`, or select one in `AgentSpec.model`. `a13n_harness.infer_model()` constructs a model from supported aliases and caller-owned provider factories. `create_model_http_client()` builds a caller-owned `httpx2` client for provider requests. Configure credentials and close owned clients in your application. See [Models](../../docs/a13n-harness/models.md) and [Model authentication](../../docs/a13n-harness/model-authentication.md).

## Execution boundary and filters

Harness prepares images for the selected Model without changing saved history or original files. Configure `HarnessModelCharacteristics.image_input` with `ImageInputPolicy`, or set `image_input=None` to disable preparation. See [Context](../../docs/a13n-harness/context.md#filters).

Recognized provider-history repairs are on by default; configure them through `SelfHealingModelCapability` or disable automatic installation with `HarnessBuilder(self_healing_enabled=False)`. For long Threads, opt into [history compaction](../../docs/a13n-harness/context.md#compact-a-long-thread). Add other Capabilities for policy, memory, or delegation.

A completed Run returns a `HarnessState` with a stable `thread_id`. Pass it as `previous_state` to continue the Thread; each new Run receives a fresh `run_id`. The Host persists accepted state and reconstructs credentials and other current authority on resume. [State and resume](../../docs/a13n-harness/state-and-resume.md) covers serialization and interrupted calls.

## Runnable examples and guides

- [Agent application](../../examples/agent-app/README.md): offline streaming turns, checkpointing, and restart recovery.
- [Environment Providers](../../examples/environment-provider/README.md): construction, re-entry, and lifecycle.
- [Plugins and extensions](../../examples/plugins/README.md): packaged middleware, Capabilities, and Environment integrations.
- [MCP tools](../../docs/a13n-harness/mcp.md#run-an-mcp-tool-offline): an in-process server and an offline Model, followed by Host-owned clients and per-Run headers.
- [Harness guide](../../docs/a13n-harness/index.md): the public API by task.

## Versioning

Harness and `a13n-stream-protocol` publish together at one exact version. Harness UI releases independently. The [Harness specification](../../spec/a13n-harness/README.md) owns the accepted contracts.
