---
title: 模型身份验证与 HTTP 客户端
sidebarTitle: 身份验证与 HTTP 客户端
description: 为模型使用 API key 或订阅登录，并管理模型使用的 HTTP 客户端。
---

API key 模型使用原生 provider 凭据。应用需要已实现的订阅登录和凭据来源集成时，使用 `a13n_harness.providers.model.oauth`。Harness 提供协议和模型组件，不提供账号数据库、浏览器 UI，也不授予替换用户账号的许可。

直接可用的本地登录体验见 [Harness UI 模型与身份验证](../a13n-harness-ui/models-and-authentication.md)。下面示例展示 Python API；登录函数调用时会联系外部服务，必须由用户发起。这些示例不是离线测试，也不保证账号符合 provider 资格。

内置 `anthropic` 适配器在模型请求和连接测试中，会将同一个已保存的 API key 同时放入 `X-Api-Key` 和 `Authorization: Bearer`，自定义网关地址也使用这一行为。只需填写一次 key，无需额外填写 Authorization 请求头。这两个名称均由 provider 管理，升级时请移除已有的自定义 Authorization 条目。

## 分离登录、存储与模型使用

1. 登录流程生成完整凭据值。
2. Host 验证用户，决定可选账号，并原子持久保存凭据。
3. 凭据来源加载当前凭据并持久保存轮换。
4. 新选择的模型在原生生命周期中使用该来源。

绝不能将 access/refresh/ID token 保存到 `AgentSpec`、`HarnessState`、工具元数据或追踪属性。原生模型和 HTTP 客户端生命周期独立于不可变 Harness 构建结果。

## Codex 浏览器与设备流程

`CodexLoginFlow` 特化上游 Pydantic AI 浏览器流程，只为保留真实 ID token，因为共享的原生 Codex 凭据存储需要该 token。它继承 PKCE/回调处理，提供 `authorization_url()` 和 `exchange_login_from_callback()`：

```python
from a13n_harness.providers.model.oauth import CodexLoginFlow


async def browser_login(show_authorization_url, publish_login):
    flow = CodexLoginFlow()
    await show_authorization_url(flow.authorization_url())
    login = await flow.exchange_login_from_callback()
    await publish_login(login)
```

两个回调都是应用管理的异步函数。将 URL 展示给请求登录的用户；`publish_login` 必须保存凭据集和共享原生 Codex 凭据存储所需的 ID token，不能记录到日志。浏览器回调是否可用取决于本地监听器和环境。继承的 Codex API 没有回调超时参数。

无界面环境中，`CodexDeviceAuthorizationFlow.start()` 返回 `CodexDeviceAuthorization`：

```python
from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow


async def device_login(show_device_code, publish_login):
    authorization = await CodexDeviceAuthorizationFlow.start()
    await show_device_code(authorization.verification_uri, authorization.user_code)
    login = await authorization.wait_for_login()
    await publish_login(login)
```

授权对象提供验证 URI、用户代码、过期时间和轮询间隔；私有设备 token 仅保留在进程内。`wait_for_login()` 负责有界轮询。`CodexDeviceAuthorizationFlow` 实现的是 Codex 两阶段设备流程，不是通用 RFC 8628 grant。取消或持久化结果不确定后，不要自动重复登录。

`CodexLoginResult` 包含上游 `OpenAICodexCredentials` 和共享原生 Codex 凭据存储所需的 ID token。模型请求使用上游凭据值；Harness 不引入第二套 Codex 刷新存储。

### 构建模型

`a13n_harness.models.codex.CodexRequestModel(model_name, *, credential_source, http_client=None, thread_id=None)` 接受上游 `OpenAICodexCredentialSource` 协议（`async load()` / `async save(credentials)`）。模型解析器传入 `thread_id=context.deps.thread_id`，为流式和非流式请求绑定原生 Codex 会话请求头。适配器对原始线程 ID 应用共享 [UUID v5 亲和性派生](models.md#model-request-affinity)；不要预先派生。显式原生请求头不变。Codex 会话请求头不从 `x-session-id` 或其他网关请求头派生。子线程和分叉根据当前上下文重新绑定，不捕获父 ID。上游 Pydantic AI 负责身份验证、刷新、重试和 Responses 渲染。

适配器只管理自己创建的 HTTP 客户端。注入客户端仍由调用者管理。Harness 为模型限定执行范围；适配器的请求/响应 hook 不得超过所属模型使用的生命周期。新执行重新选择账号，不要在活跃请求背后更换账号。

## Grok 浏览器与设备流程

提供应用配置集成所要求的 issuer、client ID 和 scopes；它们不是通用 Harness 账号默认值。

`GrokOAuthFlow.discover(issuer=..., client_id=..., scopes=..., redirect_uri=None, referrer=None, http_client=None, allow_insecure_loopback=False)` 发现并验证浏览器授权端点。省略 redirect URI 时选择可用的本地回环回调。展示 `authorization_url()`，等待 `exchange_code_from_callback(timeout_seconds=...)`，再持久保存返回的 `GrokCredentials`。

`OAuthFlow` 是抽象 PKCE/回调边界；使用具体 Grok 流程，不要在产品 UI 中实现 token 交换。不安全回环选项仅用于显式允许的本地 fixture，不能用来允许任意非 HTTPS 身份服务器。

设备流程替代方案如下：

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

`GrokDeviceAuthorization` 提供验证 URI、可选完整 URI、用户代码、过期时间和轮询间隔。设备代码对模型不可见。流程按有界的等待、降速、拒绝和过期结果处理。

### 凭据来源与刷新

`GrokCredentials` 保存账号身份、验证模式、创建/过期时间、issuer/client ID、access token 和可选 refresh token。`GrokCredentialSource` 要求 `async load()` 和 `async rotate(expected, exchange)`。Host 协调整个读取、消费 grant 和持久发布过程。

`build_grok_model(model_name, *, credential_source, refresh=None, refresh_window=timedelta(minutes=5), http_client=None)` 构建由该来源支持的原生 Responses Model。`refresh_grok_credentials(credentials, *, http_client=None)` 是独立刷新操作；它返回凭据，不代替你发布到 Host 存储。

`build_grok_model` 构建的 Model 在刷新和重新加载前后检查账号身份，并在使用轮换后的凭据前将其持久保存。持久化失败应视为凭据转换失败，不能仅因远程 token 端点响应就认定成功。注入的 HTTP 客户端仍由调用者管理；适配器管理自己创建的客户端。

Grok 凭据存储只提供 `load()` 和 `save()` 时，用 `ProcessGrokCredentialSource` 包装一次，在模型间共享包装器。其锁和不确定 grant 证据只在当前进程存在。响应丢失、取消或保存失败后，可能已消费的 grant 会被阻止；修改元数据不会让重试安全。使用新 grant 重新验证。`RefreshNotDispatched` 区分已证实在 token 分派前发生的失败。

Harness UI 提供更强的文件存储协调：协作进程共享一个锁和持久、不含秘密的 grant 指纹 sidecar。无需保存第二份 token 副本，就能保护新执行和重启。写入同一 auth 文件的其他 CLI 程序不使用该锁。Harness UI 在替换文件前检查这些程序的编辑；检测到编辑时，发布失败，不会覆盖该编辑。

## 构建 Copilot Model

向原生 `GitHubCopilotOAuthFlow` 提供应用的公开 OAuth client ID：展示 `start()` 返回的验证信息，再等待 `wait_for_authorization()`。Host 识别获授权的 GitHub 账号并保存完整授权。交互式 Host 流程参阅 [Copilot 登录](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription)。

取得新签发的原生 `GitHubCopilotCredentials` 后，构建进程内 source 和 Model：

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

构造不执行凭据 I/O。签发时用 `issued()` 将有效期转换一次；加载时恢复已保存的绝对过期时间。进程内 source 协调单进程轮换。需要共享或可重启存储时，实现 `CopilotCredentialSource.load()` 和 `rotate()`，持久协调同一授权，并在使用前完成发布。

显式调用 `await discover_copilot_models(credential_source=source)` 查询已认证目录。它返回第一页中声明支持 Chat Completions 的 ID；发现结果不代表模型使用资格。该 helper 也从同一 OAuth 模块导入。身份验证失败需要显式恢复，不会自动发起交互登录或切换账号。

## ChatGPT 登录与原生 Model

使用官方集成，并由 Host 管理存储：

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTProvider,
)

flow = OpenAIChatGPTOAuthFlow(
    app_name="My Agent",
    host_id=host_id,  # 每次安装生成一次 uuid.uuid4().urn，并持久保存。
    redirect_uri="http://127.0.0.1:1456/auth/callback",
)
# 显示 flow.authorization_url()，接收回调并保存凭据。
# source 实现 async load() 和 async save(credentials)。
model = OpenAIResponsesModel(
    "YOUR_ACCOUNT_MODEL_SLUG",
    provider=OpenAIChatGPTProvider(credential_source=source),
)
```

从 `pydantic_ai.providers.openai_chatgpt` 导入 `OpenAIChatGPTCredentials` 和 `OpenAIChatGPTCredentialSource`。每次一起保存所有凭据字段。Provider 处理到期刷新，刷新前采用存储中的较新 token，并在 401 后重试一次身份认证。锁只属于单个 provider 实例，多进程仍可能同时刷新同一授权。保存失败会抛出错误，但轮换后的凭据仍留在 provider 内存中。解决持久化冲突后应启动新的 Run。

Harness UI 和 Service 使用 `providers.model.oauth.chatgpt.ChatGPTAuthorization` 持久保存公开流程属性并验证粘贴的回调。有效待完成授权在交换前被消费。官方流程负责 PKCE 和 token 验证。`providers.model.chatgpt.discover_chatgpt_models()` 按服务器顺序返回账户可见 slug 和显示名。仍可手动输入 ID；可见不代表有推理权限。

官方 Responses profile 强制流式响应和 `store=false`，普通请求使用原生流收集器。工具序列化、支持的设置和流结束行为遵循官方 Model，不再叠加 Harness 自定义 ChatGPT 方言。

### 预配置的 client

再次登录可传入已签发的公共 `client_id` 和保留的 `id_token_hint`。所有回调都使用 `http://127.0.0.1:<port>/auth/callback`，只能变更端口。不支持机密 client 和 HTTPS 托管回调；升级前需将这些配置改为公共 client 回环流程。已有公共 client 凭据仍可读取。

若曾拒绝订阅额度使用权限，请重新登录并批准；Host 在这次重试中显式请求同意。本地退出会清除已存 token，但不会撤销 OpenAI 授权，也不会停止使用缓存 token 的活动 provider。请在 ChatGPT 设置中管理远端访问。参见 [Service 回调配置](../a13n-service/models.md#self-hosted-callback)。

## 身份验证失败

| 公开异常                     | 含义                                                   |
| ---------------------------- | ------------------------------------------------------ |
| `ModelAuthenticationError`   | 可安全公开的有界模型验证失败；继承原生 `ModelAPIError` |
| `CredentialRefreshError`     | 凭据刷新被拒绝或无效                                   |
| `DeviceAuthorizationError`   | 设备的终结 `expired`、`denied` 或 `unsupported` 结果   |
| `CredentialPersistenceError` | 轮换凭据未能在使用前保存                               |

公开错误展示不能包含详细秘密或原始 provider 响应。Host 决定重试安全操作、要求重新验证，或核对存储。provider 拒绝不授权悄悄切换到其他账号。

## 管理模型 HTTP 客户端

`a13n_harness.models.create_model_http_client()` 构建调用者管理的 `httpx2.AsyncClient`。将其注入兼容原生 provider；请求头仍通过 `ModelSettings.extra_headers` 设置，不是 Harness 虚构的额外传输设置。

```python
from a13n_harness.models import create_model_http_client


async def use_provider_client(build_and_run):
    async with create_model_http_client(timeout=120, connect=5, retry=None) as client:
        # Application callback constructs a compatible Provider and awaits its Run.
        return await build_and_run(client)
```

默认超时 600 秒，连接超时五秒；两个参数都要求正整数。`transport` 可选提供 `httpx2.AsyncBaseTransport`。`retry=None` 禁用自动传输重试。

`ModelHttpRetryConfig` 默认值：

| 字段                           | 默认值             |
| ------------------------------ | ------------------ |
| `attempts`                     | 总共 5 次尝试      |
| `backoff_multiplier`           | 1.0                |
| `max_wait_seconds`             | 30.0               |
| `retry_after_max_wait_seconds` | 300.0              |
| `status_codes`                 | 429, 502, 503, 504 |

helper 也重试支持的超时/连接/读取错误。尝试次数必须为正数，等待有限且非负，状态码必须是有效 HTTP 整数。`DEFAULT_MODEL_HTTP_RETRY_CONFIG` 和 `DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES` 提供默认值。这些是模型传输策略；每个独立 Service SDK 有自己的传输和重试契约。

## 避免意外叠加重试预算

| 机制                        | 范围                                           |
| --------------------------- | ---------------------------------------------- |
| 原生工具/输出验证重试       | agent 循环验证和重试提示                       |
| 模型 HTTP 重试              | provider HTTP 操作                             |
| 中断历史修复                | 使保留历史结构可消费，不声称缺失副作用从未发生 |
| `ModelRecoveryPolicy`       | 一次逻辑 Harness 执行内的额外模型尝试          |
| Host worker 替换 / 用户重试 | Host 选择并管理的新执行                        |

`ModelRecoveryPolicy` 默认禁用，启用时 `max_attempts=5` 限制连续失败尝试，初始退避一秒，最多 30 秒。接受主模型响应后重置计数和退避。只有已识别临时失败符合恢复条件；永久或未知 provider 错误不重试。它接受续接提示或提示工厂。内部模型尝试共享执行上下文和用量，不是新的持久 worker 尝试。构建/执行 API 和恢复行为见 [Agent 与执行](agents-and-runs.md)。
