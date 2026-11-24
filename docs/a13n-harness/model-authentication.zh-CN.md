---
title: 模型认证与 HTTP 客户端
sidebarTitle: 认证与 HTTP 客户端
description: 在自有宿主中集成 OAuth 订阅流程（Codex、ChatGPT、Grok、GitHub Copilot），协调凭据刷新并管理自定义 HTTP 客户端生命周期。
---

本指南适用于在自有宿主应用中集成 OAuth 订阅登录与自定义模型 HTTP 客户端。若直接使用本地 CLI/WebUI 登录，请参阅 [Harness UI 模型与认证](../a13n-harness-ui/models-and-authentication.md)。

访问令牌、刷新令牌与 ID Token 应保存在宿主存储中——切勿放入 `AgentSpec`、`HarnessState` 或追踪属性——并通过[运行解析器](models.md#model-selection)返回已认证的 Model 实例。

## Codex OAuth 与 `CodexRequestModel`

使用 `CodexLoginFlow`（本地浏览器回调）或 `CodexDeviceAuthorizationFlow`（无头设备码）完成授权，随后配合 `OpenAICodexCredentialSource` 构造 `CodexRequestModel`：

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

直接传入原始 `thread_id`（Harness 会自动派生 [UUID v5 会话亲和键](models.md#model-request-affinity)）。在模型设置中指定 `openai_transport="websocket"` 可启用带 HTTP 回退的 Responses WebSocket 传输。

## Grok、GitHub Copilot 与 ChatGPT 订阅

- **Grok（`a13n_harness.providers.model.oauth`）：** 通过 `GrokOAuthFlow.discover(...)` 或 `GrokDeviceAuthorizationFlow.start(...)` 登录，使用 `ProcessGrokCredentialSource(store)` 包装存储（或多进程下实现原子的 `load`/`rotate`），并调用 `build_grok_model(model_name, credential_source=source)`。
- **GitHub Copilot：** 运行 `GitHubCopilotOAuthFlow`（交互流程见 [Copilot 登录](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription)），调用 `CopilotCredentials.issued(grant, account_id=..., client_id=...)` 转换授权，经 `ProcessCopilotCredentialSource` 包装后传入 `build_copilot_model(model_name, credential_source=source)`。
- **ChatGPT：** 使用 `OpenAIChatGPTOAuthFlow(app_name=..., host_id=..., redirect_uri="http://127.0.0.1:1456/auth/callback")` 登录，并将 `OpenAIChatGPTProvider(credential_source=source)` 传给 `OpenAIResponsesModel`。

| 公开认证异常                 | 含义                                                 |
| ---------------------------- | ---------------------------------------------------- |
| `ModelAuthenticationError`   | 提供方拒绝认证（可安全展示给前端）                   |
| `CredentialRefreshError`     | 刷新令牌被拒绝或已失效                               |
| `DeviceAuthorizationError`   | 设备码流程终止（`expired`、`denied`、`unsupported`） |
| `CredentialPersistenceError` | 轮换后的凭据在使用前持久化失败                       |

## 管理模型 HTTP 客户端与重试层级

使用 `create_model_http_client()` 创建带超时和传输重试（`429, 502, 503, 504`）的 `httpx2.AsyncClient`：

```python
from a13n_harness.models import ModelHttpRetryConfig, create_model_http_client

async with create_model_http_client(
    timeout=120,
    connect=5,
    retry=ModelHttpRetryConfig(attempts=3, max_wait_seconds=30.0),
) as client:
    # 在该上下文内构造原生 Provider 并等待 Run 完成
    ...
```

网络路由与证书配置见[出站 HTTP 代理](models.md#outbound-http-proxies)与 [TLS 验证](models.md#outbound-tls-verification)。请统筹规划 HTTP 传输重试（`ModelHttpRetryConfig`）、智能体循环校验重试（`AgentSpec.retries`）与流中断恢复（`ModelRecoveryPolicy`），避免单次故障在多层之间叠加重试。
