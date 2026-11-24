---
title: Model authentication and HTTP clients
sidebarTitle: Authentication and HTTP clients
description: Integrate OAuth subscription flows (Codex, ChatGPT, Grok, GitHub Copilot), coordinate credential refresh, and manage custom HTTP client lifecycles.
---

Use this guide when embedding OAuth subscription login and custom model HTTP clients into your own Host. For ready-made local CLI/WebUI login, use [Harness UI Models and authentication](../a13n-harness-ui/models-and-authentication.md).

Keep access, refresh, and ID tokens in Host-owned storage—never in `AgentSpec`, `HarnessState`, or trace attributes—and return authenticated Models from your [Run resolver](models.md#model-selection).

## Codex OAuth and `CodexRequestModel`

Use `CodexLoginFlow` (local callback) or `CodexDeviceAuthorizationFlow` (headless device code), then build `CodexRequestModel` with an `OpenAICodexCredentialSource`:

```python
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.models.codex import CodexRequestModel
from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow


async def device_login(show_device_code, publish_login):
    auth = await CodexDeviceAuthorizationFlow.start()
    await show_device_code(auth.verification_uri, auth.user_code)
    await publish_login(await auth.wait_for_login())


async def run_codex(prompt: str, *, model_name: str, source) -> str:
    async def resolve(context, model_id):
        return CodexRequestModel(
            model_name,
            credential_source=source,
            thread_id=context.deps.thread_id,
        )

    executable = HarnessBuilder().build(AgentSpec(model="logical:codex"), output_type=str)
    result = await executable.run(prompt, bindings=RunBindings.embedded(model_resolver=resolve))
    return result.output_or_raise()
```

Pass the raw `thread_id` (Harness derives [UUID v5 session affinity](models.md#model-request-affinity) automatically). Set `openai_transport="websocket"` in Model settings to opt into Responses WebSocket transport with HTTP fallback.

## Grok, GitHub Copilot, and ChatGPT subscriptions

- **Grok (`a13n_harness.providers.model.oauth`):** Authenticate via `GrokOAuthFlow.discover(...)` or `GrokDeviceAuthorizationFlow.start(...)`, wrap your `load`/`save` store in `ProcessGrokCredentialSource(store)` (or implement atomic `load`/`rotate` across processes), and construct `build_grok_model(model_name, credential_source=source)`.
- **GitHub Copilot:** Run `GitHubCopilotOAuthFlow` (see [Copilot login](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription)), convert the grant once via `CopilotCredentials.issued(grant, account_id=..., client_id=...)`, wrap in `ProcessCopilotCredentialSource`, and call `build_copilot_model(model_name, credential_source=source)`.
- **ChatGPT:** Run `OpenAIChatGPTOAuthFlow(app_name=..., host_id=..., redirect_uri="http://127.0.0.1:1456/auth/callback")` and pass `OpenAIChatGPTProvider(credential_source=source)` to `OpenAIResponsesModel`.

| Public auth exception        | Meaning                                                    |
| ---------------------------- | ---------------------------------------------------------- |
| `ModelAuthenticationError`   | Provider rejected authentication (safe for UI display)     |
| `CredentialRefreshError`     | Refresh grant was rejected or expired                      |
| `DeviceAuthorizationError`   | Device flow ended as `expired`, `denied`, or `unsupported` |
| `CredentialPersistenceError` | Rotated credentials failed to persist before use           |

## Manage a Model HTTP client and retry layers

Use `create_model_http_client()` to own an `httpx2.AsyncClient` with bounded timeouts and transport retries (`429, 502, 503, 504`):

```python
from a13n_harness.models import ModelHttpRetryConfig, create_model_http_client

async with create_model_http_client(
    timeout=120,
    connect=5,
    retry=ModelHttpRetryConfig(attempts=3, max_wait_seconds=30.0),
) as client:
    # Construct your native Provider with client and await the Run inside this scope.
    ...
```

See [outbound HTTP proxies](models.md#outbound-http-proxies) and [TLS verification](models.md#outbound-tls-verification) for network routing. Budget HTTP transport retries (`ModelHttpRetryConfig`), Agent-loop validation retries (`AgentSpec.retries`), and interrupted-stream recovery (`ModelRecoveryPolicy`) deliberately so a single failure does not multiply retries across layers.
