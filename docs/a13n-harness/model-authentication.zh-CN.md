---
title: 模型身份验证与 HTTP 客户端
sidebarTitle: 身份验证与 HTTP 客户端
description: 将订阅凭据接入 Run，协调刷新，并关闭模型 HTTP 客户端。
---

已认证的 Model 需要账号来源和明确的客户端生命周期。原生 Provider 处理 API key 认证。订阅集成则从 `a13n_harness.providers.model.oauth` 选择对应流程和凭据来源，再由 [Run 解析器](models.md#model-selection)返回已认证模型。

本页用于在自己的 Host 中嵌入认证流程。需要现成的本地登录界面时，使用 [Harness UI 模型与身份验证](../a13n-harness-ui/models-and-authentication.md)。下方登录 helper 被调用时会联系外部身份服务；应在用户主动请求登录且具备所需账号权限时调用。末尾的 HTTP 客户端示例可在[快速入门环境](getting-started.md)中离线运行。

## 分离登录、存储与模型使用

按四步构建集成：

1. 执行 provider 登录流程，取得完整凭据值。
2. 验证哪个 Host 用户可选择该账号，然后原子保存完整值。
3. 实现 provider 凭据来源，加载当前凭据，并按该来源契约保存轮换结果。
4. 为 Run 选择新的已认证 Model，并让资源存活至模型使用结束。

Host 管理账号存储、授权和登录 UI。不要将 access、refresh、ID token 放入 `AgentSpec`、`HarnessState`、工具元数据或追踪属性。保存的对话恢复历史；账号选择和客户端来自当前 Host 数据。

API key 认证使用所选原生 Provider 的凭据机制。内置 `anthropic` 适配器接收一个已保存 key，并同时放入 `X-Api-Key` 和 `Authorization: Bearer`，连接测试和自定义网关也一样。这两个请求头名称保留给适配器。升级时移除旧的自定义 Authorization 条目，无需填写两次 key。

## Codex 浏览器与设备流程

登录可访问进程的本地回调监听器时，选择浏览器流程。`CodexLoginFlow` 使用上游 PKCE/回调处理，并保留共享原生 Codex 存储需要的真实 ID token：

```python
from a13n_harness.providers.model.oauth import CodexLoginFlow


async def browser_login(show_authorization_url, publish_login):
    flow = CodexLoginFlow()
    await show_authorization_url(flow.authorization_url())
    login = await flow.exchange_login_from_callback()
    await publish_login(login)
```

提供两个应用管理的异步回调：`show_authorization_url` 向请求登录的用户展示 URL；`publish_login` 保存完整凭据集和 ID token，不记录到日志。继承的回调 API 没有超时参数，因此 Host 需要限制整体等待时间并保留取消语义。

无界面 Host 可改为展示设备代码：

```python
from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow


async def device_login(show_device_code, publish_login):
    authorization = await CodexDeviceAuthorizationFlow.start()
    await show_device_code(authorization.verification_uri, authorization.user_code)
    login = await authorization.wait_for_login()
    await publish_login(login)
```

授权对象提供验证 URI、用户代码、过期时间和轮询间隔，私有设备 token 留在进程中。`wait_for_login()` 执行有界轮询和 Codex 两阶段交换。取消或保存结果不确定后，先核对结果，再发起另一轮登录。

两条路径均返回 `CodexLoginResult`：上游 `OpenAICodexCredentials` 加登录 ID token。推理使用原生凭据值，ID token 用于与存储互操作。Codex 刷新仍由原生 provider 管理；其设备流程与 Grok 的 RFC 8628 grant 不同。

### 构建模型

Host 选定获授权的账号后，提供实现上游 `OpenAICodexCredentialSource` 的来源，包含 `async load()` 和 `async save(credentials)`。以下 helper 使用该来源及账号可访问的模型名执行一次提示：

```python
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.models.codex import CodexRequestModel


async def run_codex(prompt, *, model_name, source):
    async def resolve(context, model_id):
        return CodexRequestModel(
            model_name,
            credential_source=source,
            thread_id=context.deps.thread_id,
        )

    executable = HarnessBuilder().build(
        AgentSpec(model="logical:codex"), output_type=str
    )
    result = await executable.run(
        prompt, bindings=RunBindings.embedded(model_resolver=resolve)
    )
    return result.output_or_raise()
```

登录并保存完成后，在应用中调用 `await run_codex(prompt, model_name=..., source=...)`。该 helper 会执行真实推理。续接时遵循 [State 流程](state-and-resume.md)，再次提供新 bindings。

传入当前上下文的原始 Thread ID。`CodexRequestModel` 为流式和普通请求的原生会话请求头派生 [UUID v5 亲和性](models.md#model-request-affinity)；提前派生会导致二次派生。显式原生请求头仍优先。子 Thread 和分叉必须使用自己的当前上下文，不能捕获父 ID。`x-session-id` 等网关请求头不提供 Codex 会话身份。

Pydantic AI 管理 Codex 认证、刷新、重试和 Responses 渲染。适配器管理自行创建的客户端，注入客户端仍由调用者负责。注入 OAuth 客户端时，为该集成提供专用生命周期，且不预设认证。请求/响应 hook 应留在这个生命周期内。切换账号时开始新 Run，不在活跃请求背后替换账号。

## Grok 浏览器与设备流程

使用应用配置的 issuer、公开 client ID 和 scopes。浏览器流程如下：

1. 等待 `GrokOAuthFlow.discover(issuer=..., client_id=..., scopes=...)`。
2. 向请求登录的用户展示 `flow.authorization_url()`。
3. 等待 `flow.exchange_code_from_callback(timeout_seconds=...)`。
4. 原子保存返回的 `GrokCredentials`。

从 OAuth 模块导入 `GrokOAuthFlow`。发现步骤也接受 `redirect_uri`、`referrer` 和 `http_client`；省略 redirect 时选择可用的本地回环回调。`allow_insecure_loopback=True` 用于显式允许的本地 fixture。抽象 `OAuthFlow` 管理 PKCE/回调接口，具体 Grok flow 管理对应协议。

不适合本地浏览器回调时，使用设备流程：

```python
from a13n_harness.providers.model.oauth import GrokDeviceAuthorizationFlow


async def grok_device_login(issuer, client_id, scopes, show_device_code, source):
    authorization = await GrokDeviceAuthorizationFlow.start(
        issuer=issuer, client_id=client_id, scopes=scopes
    )
    await show_device_code(authorization.verification_uri, authorization.user_code)
    credentials = await authorization.wait_for_credentials()
    await source.save(credentials)
```

这里的 `show_device_code` 是应用的异步 UI 回调，`source` 是提供异步 `save` 的 Host 登录保存对象。运行期 `GrokCredentialSource` 使用下文的 `load`/`rotate` 接口。授权对象提供验证 URI、可选完整 URI、用户代码、过期时间和轮询间隔。私有设备代码不进入模型上下文。轮询在期限内处理等待、降速、拒绝和过期。

### 凭据来源与刷新

`GrokCredentials` 包含账号身份、认证模式、创建/过期时间、issuer、client ID、access token 和可选 refresh token。实现 `GrokCredentialSource.load()` 和 `rotate(expected, exchange)`。轮换需要把当前读取、消费 grant 和持久保存协调为一次操作。

使用 `build_grok_model(model_name, credential_source=source)` 构建推理模型。可选输入包括 `refresh`、`refresh_window`（默认五分钟）和 `http_client`。`refresh_grok_credentials(credentials, http_client=...)` 执行独立交换并返回凭据，由 Host 负责保存。Model 在重新加载和轮换时检查账号身份，保存成功后才使用替换值。

简单存储只有 `load()` 和 `save()` 时，创建一个 `ProcessGrokCredentialSource(store)`，在模型间共享。包装器持有进程内锁并记住不确定的 grant。响应丢失、取消或保存失败可能意味着 grant 已被消费；修改其元数据不能让再次消费变得安全。通过新授权的 grant 恢复。只有 `RefreshNotDispatched` 能证明 token 请求未发出。

跨进程和重启需要 Host 的持久协调。Harness UI 提供共享文件锁和不含秘密的 grant 指纹 sidecar，无需第二份 token 副本。其他 CLI 写入者不使用该锁；Harness UI 在替换文件前检查其编辑，检测到变更便使保存失败。注入模型客户端仍由调用者管理；适配器关闭自行创建的客户端。

## 构建 Copilot Model

使用公开 OAuth client ID 启动原生 `GitHubCopilotOAuthFlow`。展示 `start()` 返回的验证信息，等待 `wait_for_authorization()`，识别获授权的 GitHub 账号，再保存完整 grant。交互式 Host 流程见 [Copilot 登录](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription)。

取得新签发的 `GitHubCopilotCredentials` 后，以下 helper 创建进程内来源和原生模型：

```python
from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials
from a13n_harness.providers.model.oauth import (
    CopilotCredentials,
    ProcessCopilotCredentialSource,
    build_copilot_model,
)


class MemoryCredentialStore:
    def __init__(self, credentials: CopilotCredentials):
        self.credentials = credentials

    async def load(self) -> CopilotCredentials:
        return self.credentials

    async def save(self, credentials: CopilotCredentials) -> None:
        self.credentials = credentials


def copilot_model(grant: GitHubCopilotCredentials, *, account_id: str, client_id: str, model_name: str):
    credentials = CopilotCredentials.issued(grant, account_id=account_id, client_id=client_id)
    source = ProcessCopilotCredentialSource(MemoryCredentialStore(credentials))
    return build_copilot_model(model_name, credential_source=source), source
```

使用已保存的账号身份和支持 Chat Completions 的模型调用 `copilot_model(grant, account_id=..., client_id=..., model_name=...)`。构造不执行凭据 I/O。通过 `HarnessBuilder.build(..., model=model)` 传入返回的 Model，保持 `AgentSpec.model` 未设置。为该账号的模型保留并共享返回的 source。

`issued()` 只在签发时将有效期转换为绝对时间。重新加载已保存时间，不重启有效期时钟。`MemoryCredentialStore` 演示进程内存储；凭据和同一 grant 的协调需在重启后保留时，替换该存储并实现持久的 `CopilotCredentialSource.load()`/`rotate()`。轮换在使用前保存，遵循前文 Grok 的不确定 grant 规则。

显式导入并调用 `discover_copilot_models(credential_source=source)` 读取已认证目录。它返回第一页中声明支持 Chat Completions 的 ID。目录存在和登录成功不代表有推理使用资格。认证失败需要显式决定恢复方式，不会自动登录或切换账号。

## ChatGPT 登录与原生 Model

使用原生流程和 Host 管理的来源。每次安装持久保存一次 `uuid.uuid4().urn`，将该值作为 `host_id` 传入：

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTProvider,
)


def prepare_chatgpt_login(host_id):
    return OpenAIChatGPTOAuthFlow(
        app_name="My Agent",
        host_id=host_id,
        redirect_uri="http://127.0.0.1:1456/auth/callback",
    )


def build_chatgpt_model(model_name, source):
    return OpenAIResponsesModel(
        model_name,
        provider=OpenAIChatGPTProvider(credential_source=source),
    )
```

用户请求登录时，调用 `prepare_chatgpt_login(host_id)`，展示流程的授权 URL，处理回调并保存完整凭据。然后以账号可见的模型 slug 和实现原生 `async load()`/`async save(credentials)` 的 source 调用 `build_chatgpt_model(model_name, source)`。从 `pydantic_ai.providers.openai_chatgpt` 导入 `OpenAIChatGPTCredentials` 和 `OpenAIChatGPTCredentialSource` 类型。

Provider 加载凭据，在刷新前采用存储中的新 token，处理到期刷新以及 401 后的一次认证重试。锁属于单个 provider 实例，多个进程仍可能同时消费同一 grant。保存失败会抛出错误，而轮换后的凭据仍留在 provider 内存中；解决存储冲突后开始新 Run。

Harness UI 和 Service 使用 `providers.model.oauth.chatgpt.ChatGPTAuthorization` 保留公开流程属性并验证粘贴的回调。有效待完成授权在交换前被消费。原生流程管理 PKCE 和 token 验证。`providers.model.chatgpt.discover_chatgpt_models()` 按服务器顺序返回账号可见的 slug 和显示名，也允许手动输入 ID。可见不等于获准推理。

原生 Responses profile 强制流式响应和 `store=false`，普通请求使用其流收集器。工具、支持的设置和流结束行为遵循原生 Model 规则。

### 预配置的 client

再次登录时，提供已签发的公开 `client_id` 和保留的 `id_token_hint`。回调使用 `http://127.0.0.1:<port>/auth/callback`，只有端口可变。升级前将机密 client 或托管 HTTPS 回调配置迁移为公开回环流程。已有公开 client 凭据仍可读取。

用户曾拒绝订阅额度使用时，在新登录中请求同意，由用户批准。本地退出清除已保存 token，但既不撤销远端 OpenAI 访问，也不会停止使用缓存 token 的活跃 provider。远端访问在 ChatGPT 设置中管理。另见 [Service 回调配置](../a13n-service/models.md#self-hosted-callback)。

## 身份验证失败

使用有界公开异常决定下一步 Host 操作：

| 异常                         | 含义                                               |
| ---------------------------- | -------------------------------------------------- |
| `ModelAuthenticationError`   | 可安全公开的模型认证失败，继承原生 `ModelAPIError` |
| `CredentialRefreshError`     | 刷新被拒绝或结果无效                               |
| `DeviceAuthorizationError`   | 设备终结结果：`expired`、`denied` 或 `unsupported` |
| `CredentialPersistenceError` | 轮换凭据未能在使用前保存                           |

公开错误中不包含原始响应和秘密细节。由 Host 决定重试已证明安全的操作、请用户重新登录，或核对存储。Provider 拒绝不授权选择其他账号。原生 Codex 和 ChatGPT 来源还遵循前述上游持久化语义。

## 管理模型 HTTP 客户端

`create_model_http_client()` 创建调用者管理的 `httpx2.AsyncClient`。将其注入兼容原生 Provider，并在模型工作结束后关闭。下面的完整离线程序检查客户端生命周期和一次有界 HTTP 重试。保存为 `http_client_demo.py`，运行 `uv run python http_client_demo.py`：

```python
import asyncio

import httpx2
from a13n_harness.models import ModelHttpRetryConfig, create_model_http_client


async def main():
    attempts = 0

    async def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(503, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"status": "ready"})

    async with create_model_http_client(
        timeout=120,
        connect=5,
        transport=httpx2.MockTransport(respond),
        retry=ModelHttpRetryConfig(attempts=2, backoff_multiplier=0),
    ) as client:
        response = await client.get("https://example.test/model")
        response.raise_for_status()
        assert response.json() == {"status": "ready"}
        assert attempts == 2
        print(response.json()["status"])
    assert client.is_closed


if __name__ == "__main__":
    asyncio.run(main())
```

程序打印 `ready`。`MockTransport` 在内存中提供两次响应；程序验证 HTTP 重试和清理，不执行 provider 推理或账号认证。

真实请求省略 mock transport，并将客户端提供给 Provider。以下可复用作用域会等待应用的模型工作完成，再关闭客户端：

```python
from a13n_harness.models import create_model_http_client


async def use_provider_client(build_and_run):
    async with create_model_http_client(timeout=120, connect=5, retry=None) as client:
        # Application callback constructs a compatible Provider and awaits its Run.
        return await build_and_run(client)
```

`build_and_run` 是应用的异步回调，负责构造兼容 Provider 并等待 Run。请求头放入 `ModelSettings.extra_headers`。Helper 提供传输策略，不提供凭据或 provider 配置。进程级路由和证书策略参阅[代理](models.md#outbound-http-proxies)与 [TLS](models.md#outbound-tls-verification)设置。

默认请求超时为 600 秒，连接建立超时为五秒。两个构造参数均要求正整数。可选 `transport` 应为 `httpx2.AsyncBaseTransport`；`retry=None` 关闭自动传输重试。

| `ModelHttpRetryConfig` 字段    | 默认值             |
| ------------------------------ | ------------------ |
| `attempts`                     | 总共 5 次尝试      |
| `backoff_multiplier`           | 1.0                |
| `max_wait_seconds`             | 30.0               |
| `retry_after_max_wait_seconds` | 300.0              |
| `status_codes`                 | 429、502、503、504 |

Helper 也重试支持的超时、连接和读取错误。尝试次数必须为正，等待有限且非负，状态码须为有效 HTTP 整数。`DEFAULT_MODEL_HTTP_RETRY_CONFIG` 和 `DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES` 提供默认值。独立 Service SDK 保留自己的传输契约。

## 避免意外叠加重试预算

在负责相应失败的层级选择重试：

| 机制                       | 重复或修复的工作                         |
| -------------------------- | ---------------------------------------- |
| 原生工具/输出验证重试      | Agent 循环验证和重试提示                 |
| 模型 HTTP 重试             | 一次 provider HTTP 操作                  |
| 中断历史修复               | 保留消息结构；无法撤销结果不确定的副作用 |
| `ModelRecoveryPolicy`      | 同一逻辑 Harness Run 内的额外模型尝试    |
| Host worker 替换或用户重试 | 新的 Host 管理执行                       |

`ModelRecoveryPolicy` 默认关闭。启用后，默认允许五次连续失败尝试，退避从一秒开始，上限 30 秒。接受主模型响应后重置计数和退避。只有已识别的临时失败符合条件；永久或未知 provider 错误直接抛出。

恢复接受续接提示或提示工厂。内部尝试共享 Run 上下文和用量，不成为新的持久 worker 尝试。构建与运行 API 见 [Agent 与执行](agents-and-runs.md)。一起规划传输重试与逻辑恢复预算，避免一次操作意外累积延迟，或重复结果不确定的 provider 工作。
