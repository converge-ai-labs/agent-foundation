---
title: Model authentication and HTTP clients
sidebarTitle: Authentication and HTTP clients
description: Connect subscription credentials to a Run, coordinate refresh, and close Model HTTP clients.
---

An authenticated Model needs both an account source and a client lifetime. Native Providers handle API-key authentication. For subscription integration, choose the matching flow and credential source from `a13n_harness.providers.model.oauth`, then return the authenticated Model from your [Run resolver](models.md#model-selection).

This page is for embedding an authentication workflow in your own Host. For an existing local login interface, use [Harness UI Models and authentication](../a13n-harness-ui/models-and-authentication.md). Login helpers below contact external identity services when called; invoke them for a user-requested login with the required account access. The HTTP-client example at the end runs offline in the [quickstart environment](getting-started.md).

## Separate login, storage, and model use

Build the integration in four steps:

1. Run the provider's login flow to obtain a complete credential value.
2. Verify which Host user may select that account, then atomically save the complete value.
3. Implement the provider's credential source so it loads current credentials and publishes rotation according to that source's contract.
4. Select a fresh authenticated Model for the Run and keep its resources alive until model use ends.

The Host owns account storage, authorization, and the login UI. Keep access, refresh, and ID tokens out of `AgentSpec`, `HarnessState`, tool metadata, and tracing attributes. A saved conversation restores history; account selection and clients come from current Host data.

For API-key authentication, use the selected native Provider's credential mechanism. The built-in `anthropic` adapter takes one saved key and sends it in both `X-Api-Key` and `Authorization: Bearer`, including connection tests and custom gateways. Both header names are reserved. Remove an old custom Authorization entry when upgrading rather than entering the key twice.

## Codex browser and device flows

Choose the browser flow when the login can reach the process's local callback listener. `CodexLoginFlow` uses upstream PKCE/callback handling and preserves the real ID token required by shared native Codex stores:

```python
from a13n_harness.providers.model.oauth import CodexLoginFlow


async def browser_login(show_authorization_url, publish_login):
    flow = CodexLoginFlow()
    await show_authorization_url(flow.authorization_url())
    login = await flow.exchange_login_from_callback()
    await publish_login(login)
```

Supply two application-owned async callbacks: `show_authorization_url` presents the URL to the requesting user; `publish_login` saves the complete credential set and ID token without logging them. The inherited callback API has no timeout parameter, so the Host must bound the overall wait and preserve cancellation.

For a headless Host, present a device code instead:

```python
from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow


async def device_login(show_device_code, publish_login):
    authorization = await CodexDeviceAuthorizationFlow.start()
    await show_device_code(authorization.verification_uri, authorization.user_code)
    login = await authorization.wait_for_login()
    await publish_login(login)
```

The authorization object exposes the verification URI, user code, expiry, and polling interval. Its private device token stays in the process. `wait_for_login()` performs bounded polling and the Codex two-stage exchange. After cancellation or uncertain saving, reconcile the result before starting another login.

Both paths return `CodexLoginResult`: upstream `OpenAICodexCredentials` plus the login ID token. Use the native credential value for inference and retain the ID token for store interoperability. Codex refresh remains owned by the native provider; this is not Grok's RFC 8628 device grant.

### Build the Model

After the Host has selected an authorized account, supply a source implementing upstream `OpenAICodexCredentialSource` with `async load()` and `async save(credentials)`. This helper runs one prompt using that source and a model name available to the account:

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

Call `await run_codex(prompt, model_name=..., source=...)` from your application after login and publication. The helper performs real inference. For continuation, follow the [State workflow](state-and-resume.md) and supply fresh bindings again.

Pass the current context's raw Thread ID. `CodexRequestModel` derives [UUID v5 affinity](models.md#model-request-affinity) for native session headers on streaming and ordinary requests; pre-deriving it would derive twice. Explicit native headers retain precedence. Child Threads and forks must use their own current context, not a captured parent's ID. Gateway headers such as `x-session-id` do not supply Codex session identity.

Pydantic AI owns Codex authentication, refresh, retries, and Responses rendering. The adapter owns clients it creates; injected clients remain caller-owned. Give an injected OAuth client a dedicated integration lifetime and no existing authentication. Keep request/response hooks within that lifetime. Select another account in a new Run instead of changing the account behind an active request.

## Grok browser and device flows

Use the issuer, public client ID, and scopes configured for your application. The browser procedure is:

1. Await `GrokOAuthFlow.discover(issuer=..., client_id=..., scopes=...)`.
2. Present `flow.authorization_url()` to the requesting user.
3. Await `flow.exchange_code_from_callback(timeout_seconds=...)`.
4. Atomically save the returned `GrokCredentials`.

Import `GrokOAuthFlow` from the OAuth module. Discovery also accepts `redirect_uri`, `referrer`, and `http_client`; omitting the redirect selects an available local loopback callback. `allow_insecure_loopback=True` is for explicitly permitted local fixtures. The abstract `OAuthFlow` owns the PKCE/callback interface; the concrete Grok flow owns its protocol.

Use the device flow when a local browser callback is unsuitable:

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

Here `show_device_code` is your async UI callback and `source` is the Host's login publication store with an async `save`. A runtime `GrokCredentialSource` instead has the `load`/`rotate` interface below. The authorization exposes the verification URI, optional complete URI, user code, expiry, and polling interval. Keep the private device code outside model context. Polling handles pending, slow-down, denial, and expiry within its deadline.

### Credential sources and refresh

`GrokCredentials` contains account identity, auth mode, creation/expiry times, issuer, client ID, access token, and optional refresh token. Implement `GrokCredentialSource.load()` and `rotate(expected, exchange)`. Rotation must coordinate the current read, grant spend, and durable save as one operation.

Construct inference with `build_grok_model(model_name, credential_source=source)`. Optional inputs are `refresh`, `refresh_window` (default five minutes), and `http_client`. `refresh_grok_credentials(credentials, http_client=...)` performs a standalone exchange and returns credentials; the Host must publish them. Models check account identity on reload and rotation, and use the replacement only after publication succeeds.

For a simple store with `load()` and `save()`, create one `ProcessGrokCredentialSource(store)` and share it across Models. The wrapper holds a process-local lock and remembers uncertain grants. Response loss, cancellation, or save failure can leave a grant possibly spent; its metadata cannot make another spend safe. Recover with a newly authorized grant. Only `RefreshNotDispatched` proves that no token request was sent.

Shared processes and restarts need durable Host coordination. Harness UI supplies a shared file lock and non-secret grant-fingerprint sidecar, avoiding a second token copy. Other CLI writers do not take that lock; Harness UI checks for their edits before replacing the file and fails publication if it detects a change. An injected Model client remains caller-owned; the adapter closes clients it creates.

## Build a Copilot Model

Start the native `GitHubCopilotOAuthFlow` with your public OAuth client ID. Present the challenge returned by `start()`, await `wait_for_authorization()`, identify the authorized GitHub account, and save the complete grant. See [Copilot login](../a13n-harness-ui/models-and-authentication.md#github-copilot-subscription) for the interactive Host workflow.

Given the newly issued `GitHubCopilotCredentials`, this helper creates a process-local source and native Model:

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

Call `copilot_model(grant, account_id=..., client_id=..., model_name=...)` with the saved account identity and a supported Chat Completions model. Construction performs no credential I/O. Pass the returned Model through `HarnessBuilder.build(..., model=model)` with `AgentSpec.model` unset. Retain and share the returned source for that account's Models.

`issued()` converts issuance durations to absolute expiry once. Reload saved absolute times without restarting the clock. `MemoryCredentialStore` illustrates process-local storage; replace it and implement durable `CopilotCredentialSource.load()`/`rotate()` when credentials and same-grant coordination must survive restart. Rotation publishes before use, with the same uncertain-grant rule described for Grok.

Import and call `discover_copilot_models(credential_source=source)` explicitly to read the authenticated catalog. It returns first-page IDs advertising Chat Completions. Catalog presence and successful login do not establish inference entitlement. Authentication failures require an explicit recovery decision; they never trigger automatic login or account switching.

## ChatGPT sign-in and native Model

Use the native flow and a Host-owned source. Persist `uuid.uuid4().urn` once per installation and pass that saved value as `host_id`:

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

Call `prepare_chatgpt_login(host_id)` for a user-requested login. Present the flow's authorization URL, handle its callback, and save the complete credential value. Then call `build_chatgpt_model(model_name, source)` with an account-visible model slug and a source implementing native `async load()`/`async save(credentials)`. Import the credential and source types as `OpenAIChatGPTCredentials` and `OpenAIChatGPTCredentialSource` from `pydantic_ai.providers.openai_chatgpt`.

The provider loads credentials, adopts newer saved tokens before refresh, and handles expiry refresh plus one authentication retry after a 401. Its lock belongs to one provider instance. Multiple processes can still race on the same grant. A save failure surfaces while rotated credentials remain in provider memory; resolve the storage conflict and begin a new Run.

Harness UI and Service use `providers.model.oauth.chatgpt.ChatGPTAuthorization` to retain public flow attributes and validate pasted callbacks. A valid pending attempt is consumed before exchange. The native flow owns PKCE and token validation. `providers.model.chatgpt.discover_chatgpt_models()` returns account-visible slugs and display names in server order; a manually entered ID is also allowed. Visibility alone does not grant inference access.

The native Responses profile forces streaming and `store=false`. Ordinary requests use its stream collector. Native Model rules govern tools, supported settings, and stream completion.

### Preconfigured client

For returning sign-in, supply the issued public `client_id` and retained `id_token_hint`. Callbacks use `http://127.0.0.1:<port>/auth/callback`, with only the port variable. Migrate confidential-client or hosted HTTPS callback configurations to a public loopback flow before upgrading. Existing public-client credentials remain readable.

If the user declined plan usage, request consent on a new sign-in and let the user approve it. Local logout clears saved tokens; it neither revokes remote OpenAI access nor stops active providers using cached tokens. Manage remote access in ChatGPT settings. See [Service callback configuration](../a13n-service/models.md#self-hosted-callback).

## Authentication failures

Use the bounded public exception to choose the next Host action:

| Exception                    | Meaning                                                                                        |
| ---------------------------- | ---------------------------------------------------------------------------------------------- |
| `ModelAuthenticationError`   | Model-authentication failure safe for public presentation; derives from native `ModelAPIError` |
| `CredentialRefreshError`     | Refresh was rejected or invalid                                                                |
| `DeviceAuthorizationError`   | Terminal device outcome: `expired`, `denied`, or `unsupported`                                 |
| `CredentialPersistenceError` | Rotated credentials could not be saved before use                                              |

Keep raw responses and secret details out of public errors. Decide whether to retry a proven safe operation, ask the user to sign in again, or reconcile storage. A provider rejection does not authorize selecting a different account. Native Codex and ChatGPT sources also follow their upstream persistence semantics described above.

## Own a Model HTTP client

`create_model_http_client()` creates a caller-owned `httpx2.AsyncClient`. Inject it into a compatible native Provider and close it after the Model's work. This complete offline program checks the client lifetime and one bounded HTTP retry. Save it as `http_client_demo.py` and run `uv run python http_client_demo.py`:

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

It prints `ready`. `MockTransport` serves both responses in memory; the program tests HTTP retry and cleanup, not provider inference or account authentication.

For real requests, omit the mock transport and supply the client to your Provider. This reusable scope awaits the application's Model work before closing the client:

```python
from a13n_harness.models import create_model_http_client


async def use_provider_client(build_and_run):
    async with create_model_http_client(timeout=120, connect=5, retry=None) as client:
        # Application callback constructs a compatible Provider and awaits its Run.
        return await build_and_run(client)
```

`build_and_run` is your async callback that constructs a compatible Provider and awaits the Run. Request headers belong in `ModelSettings.extra_headers`. The helper supplies transport policy, not credentials or provider configuration. See [proxy](models.md#outbound-http-proxies) and [TLS](models.md#outbound-tls-verification) setup for process-wide routing and certificate policy.

Defaults are 600 seconds for request timeouts and five seconds for connection setup. Both constructor arguments require positive integers. An optional `transport` must be an `httpx2.AsyncBaseTransport`; `retry=None` disables automatic transport retry.

| `ModelHttpRetryConfig` field   | Default            |
| ------------------------------ | ------------------ |
| `attempts`                     | 5 total attempts   |
| `backoff_multiplier`           | 1.0                |
| `max_wait_seconds`             | 30.0               |
| `retry_after_max_wait_seconds` | 300.0              |
| `status_codes`                 | 429, 502, 503, 504 |

The helper also retries supported timeout, connection, and read errors. Attempts must be positive, waits finite and nonnegative, and status codes valid HTTP integers. `DEFAULT_MODEL_HTTP_RETRY_CONFIG` and `DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES` expose the defaults. Independent Service SDKs retain their own transport contracts.

## Do not combine retry budgets accidentally

Choose retries at the layer that owns the failure:

| Mechanism                             | Work it repeats or repairs                                          |
| ------------------------------------- | ------------------------------------------------------------------- |
| Native tool/output validation retries | Agent-loop validation and retry prompts                             |
| Model HTTP retries                    | A provider HTTP operation                                           |
| Interrupted-history repair            | Retained message structure; it cannot undo an uncertain side effect |
| `ModelRecoveryPolicy`                 | Additional model attempts within one logical Harness Run            |
| Host worker replacement or user retry | A new Host-owned execution                                          |

`ModelRecoveryPolicy` is off by default. When enabled, its defaults allow five consecutive failed attempts, starting at one-second backoff and capped at 30 seconds. An accepted primary response resets the count and backoff. Only recognized transient failures qualify; permanent or unknown provider errors propagate.

Recovery accepts a continuation prompt or prompt factory. Internal attempts share Run context and usage, rather than becoming new durable worker attempts. Follow [Agents and Runs](agents-and-runs.md) for the build/run API. Budget transport retries and logical recovery together so a single operation does not accumulate unintended latency or repeat uncertain provider work.
