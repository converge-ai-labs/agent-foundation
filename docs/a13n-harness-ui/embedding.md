---
title: Embed the Harness UI App
sidebarTitle: Embed the Python App
description: Import and drive the Harness UI Python App programmatically to build custom frontends or automation scripts over shared configurations and SQLite threads.
---

Use `HarnessUiApp` when your Python application needs Harness UI's YAML configurations, SQLite Threads, staged attachments, and Run controls. Choose [Harness directly](../a13n-harness/hosting.md) when your application owns configuration and storage, or [Service](../a13n-service/index.md) for worker-recoverable managed execution.

Use [ACP](agent-client-protocol.md) to launch Harness UI as a subprocess from an editor or an application written in another language.

## Open, submit, and wait

Install `a13n-harness-ui` in your application's Python environment, complete [setup](setup.md), run `a13n-harness-ui config path` to locate the root `a13n-harness-ui.yaml`, and save `embedded_chat.py`:

```python title="embedded_chat.py"
import argparse
import asyncio
from pathlib import Path

from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings_loader import load_harness_ui_settings


async def run_once(configuration_file: Path, prompt: str, thread_id: str | None):
    source = await load_harness_ui_settings(configuration_file)
    if source.candidate_error is not None:
        raise source.candidate_error

    async with open_harness_ui_app(
        source.settings,
        configuration_path=source.path,
    ) as app:
        if thread_id is None:
            thread = await app.create_thread(title="Embedded conversation")
            thread_id = thread.thread_id
        else:
            await app.get_thread(thread_id)

        print(f"Thread: {thread_id}")
        receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt)
        operation = await app.wait_root_operation(receipt.receipt_id)
        print(operation.model_dump_json(indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--thread")
    parser.add_argument("prompt")
    args = parser.parse_args()
    asyncio.run(run_once(args.config, args.prompt, args.thread))
```

Run a first turn and continue the same Thread:

```bash
python embedded_chat.py --config /path/to/a13n-harness-ui.yaml "Reply with one short sentence."
python embedded_chat.py --config /path/to/a13n-harness-ui.yaml --thread THREAD_ID "Explain your previous answer."
```

Pass `data_root=Path(...)` to `load_harness_ui_settings(...)` when isolating storage, and pass `host_mode="webui"` to `open_harness_ui_app(...)` when enabling browser-mode collaboration features without starting an [HTTP listener](http-api.md).

## Track receipts, steering, and retained tools

`submit_thread()` returns a process-local `RootRunReceipt(receipt_id, thread_id, submitted_at)`. Inspect all three sections of the operation outcome (`execution`, `continuation`, and `environment`) separately:

| Task                                  | `HarnessUiApp` method                                                                                                 |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Inspect, wait, steer, or cancel a Run | `get_root_operation`, `wait_root_operation`, `active_root_operation`, `steer_root_operation`, `cancel_root_operation` |
| Inspect or cancel retained tools      | `tool_executions(thread_id, offset=0, limit=20)`, `cancel_tool_execution(thread_id, execution_id)`                    |
| Create, list, or read Threads         | `create_thread` (`NewThreadDefaults`), `get_thread`, `list_threads`, `get_thread_transcript`                          |
| Edit Thread metadata or selections    | `update_thread_metadata`, `update_thread_configuration`, `patch_thread_configuration`                                 |
| Control delegated subagents           | `query_child_executions`, `wait_child_executions`, `steer_child_execution`, `cancel_child_execution`                  |

Trusted hosts can also persist per-Thread business metadata and request headers across restarts without polluting conversation history:

```python
from a13n_harness_ui.external_context import ExternalContext

await app.set_thread_external_context(
    thread_id=thread_id,
    context=ExternalContext(metadata={"tenant": "acme"}, headers={"x-trace-id": "trace-123"}),
)
```

## Answer the complete pending set

When an operation suspends (`status == "suspended"`), answer every pending question, approval, and external tool call together:

1. Call `await app.thread_decisions(thread_id=...)` and read `expected_continuation_id`.
2. Build a `DecisionResponseBatch` (`a13n_harness_ui.surfaces`) containing a `QuestionResponse`, `ApprovalDecision`, or `ExternalToolResult` for every pending item (omitted approvals are denied and omitted external calls fail).
3. Submit via `await app.respond_decisions(thread_id=..., response=...)` (or lower-level `respond_thread()`) and wait on the returned receipt.

For inline MCP forms and URL confirmations during an active Run, open the App with `mcp_input_enabled=True`, poll `mcp_input_requests(thread_id)` (or watch `ThreadWatch.snapshot.mcp_inputs`), and submit `McpInputResponse` (`accept`, `decline`, or `cancel`) to `respond_mcp_input(thread_id, request_id, response)`. Manage retained MCP sessions with `mcp_status(thread_id)` and `close_mcp_integration(thread_id, server_id)` ([MCP](mcp.md#connection-lifetime-and-protocol)).

## Stage attachments, watch live streams, and register integrations

Stage uploads on a Thread (up to 8 files, 10 MiB each, 20 MiB total) and reference their IDs on submission:

```python
from a13n_harness_ui.thread_files import AttachmentUpload

attachment = await app.stage_thread_attachment(
    thread_id=thread_id,
    upload=AttachmentUpload(
        name="notes.txt",
        data=b"The release needs a setup guide and a smoke test.",
        media_type="text/plain",
    ),
)
receipt = await app.submit_thread(
    thread_id=thread_id,
    prompt="Summarize the attached notes.",
    attachment_ids=(attachment.attachment_id,),
)
```

For race-free live UI updates, enter `async with app.watch_thread(root_thread_id=...) as watch:`—it subscribes before capturing `watch.snapshot` so you can consume `watch.events` without missing transitions (`live_events` and `summary_events` support cursor reconnects).

Pass `HarnessUiIntegrations` to `open_harness_ui_app(..., integrations=...)` to register trusted [Connectors](connectors.md#develop-a-trusted-connector), Host Capabilities, Environment providers, and [Plugins](extensions-and-mcp.md), and see [Tracing](observation.md) for OpenTelemetry integration. For isolated tests, use a temporary `data_root`, disable `pricing_auto_update`, and set `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0`.

## Develop an OAuth Model Provider

Put `AcmeProvider` in `acme_provider.py`. This runnable SDK example uses a fake grant and a deterministic model. It exercises Harness UI's setup and model selection without an upstream service. Replace the device and refresh methods with your OAuth requests through `ctx.http_client`; return your native Pydantic AI Model from `build_model`. Use `ctx.bearer_auth()` with the supplied client for bearer authentication.

```python
from a13n_harness_ui.model_providers import (
    AuthorizedAccount, BuildModelContext, DeviceChallenge,
    ModelProviderDeclaration, ModelProviderPlugin, OAuthContext, PluginModelSuggestion,
)
from pydantic_ai.models.test import TestModel


class AcmeProvider(ModelProviderPlugin):
    def declaration(self) -> ModelProviderDeclaration:
        return ModelProviderDeclaration(
            provider_key="acme-cloud",
            display_name="Acme Cloud",
            login_methods=("device",),
            allowed_origins=("https://models.acme.example",),
            models=(PluginModelSuggestion(model_id="demo", label="Acme Demo"),),
            default_model="demo",
        )

    async def start_device_login(self, ctx: OAuthContext) -> DeviceChallenge:
        return DeviceChallenge(
            verification_url="https://models.acme.example/activate",
            user_code="DEMO-1234", expires_in=60, poll_interval=2, state={},
        )

    async def poll_device_login(
        self, ctx: OAuthContext, challenge: DeviceChallenge
    ) -> AuthorizedAccount | None:
        return AuthorizedAccount(
            upstream_account_id="demo-user", credentials={"access_token": "demo-token"}
        )

    async def refresh_credentials(
        self, ctx: OAuthContext, current: AuthorizedAccount
    ) -> AuthorizedAccount:
        return current

    def build_model(self, ctx: BuildModelContext) -> TestModel:
        return TestModel(custom_output_text="Hello from Acme")
```

Choose one registration path. To distribute the plugin, add this entry to the plugin package's `pyproject.toml`, install it alongside Harness UI, and restart any open app:

```toml
[project.entry-points."a13n_harness_ui.model_providers"]
acme-cloud = "acme_provider:AcmeProvider"
```

```bash
uv tool install a13n-harness-ui --with acme-provider
a13n-harness-ui extensions
a13n-harness-ui login acme-cloud
a13n-harness-ui auth status acme-cloud
```

For embedded apps, register the same class directly:

```python
from a13n_harness_ui.app import HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings
from acme_provider import AcmeProvider

app_context = open_harness_ui_app(
    HarnessUiSettings(),
    integrations=HarnessUiIntegrations(model_providers=(AcmeProvider(),)),
)
```

Both registration paths expose Acme Cloud in Setup and in **Settings → Models**. The plugin runs as trusted Python in the Harness UI process; keep import, construction, and declaration free of I/O. Use `configuration_model` for nonsecret settings. For browser or pasted callbacks, declare `LoopbackCallbackSpec` and build the authorization URL with the supplied redirect, PKCE, and state.
