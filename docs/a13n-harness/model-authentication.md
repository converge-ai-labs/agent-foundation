# Model authentication and HTTP clients

Use native Provider credentials for API-key Models. Use `a13n_harness.providers.model.oauth` when your application needs the implemented subscription login and credential-source integration. Harness supplies protocol/model building blocks, not an account database, browser UI, or permission to replace a user's account.

For a ready-to-use local login experience, follow [Harness UI Models and authentication](../a13n-harness-ui/models-and-authentication.md). The examples below describe source APIs; login functions contact external services when called and must be initiated by the user. They are not offline tests or a guarantee of provider account eligibility.

## Separate login, storage, and model use

1. A login flow produces a complete credential value.
2. The Host authenticates the user, decides which account may be selected, and persists credentials atomically.
3. A credential source loads current credentials and persists rotation.
4. A freshly selected Model uses that source during its native lifecycle.

Never store access/refresh/ID tokens in `AgentSpec`, `HarnessState`, tool metadata, or tracing attributes. Native model and HTTP client lifetime is separate from immutable Harness build output.

## Codex browser and device flows

`CodexLoginFlow` specializes the upstream browser flow only where native-store publication needs the real ID token. It inherits PKCE/callback handling and exposes `authorization_url()` and `exchange_login_from_callback()`:

```python
from a13n_harness.providers.model.oauth import CodexLoginFlow


async def browser_login(show_authorization_url, publish_login):
    flow = CodexLoginFlow()
    await show_authorization_url(flow.authorization_url())
    login = await flow.exchange_login_from_callback()
    await publish_login(login)
```

Both callbacks are application-owned async functions. Present the URL to the user who requested login; `publish_login` must save the credential set and required native-store ID token without logging them. Browser callback availability depends on the local listener and environment. Do not invent a callback-timeout parameter on the inherited Codex API.

For a headless environment, `CodexDeviceAuthorizationFlow.start()` returns a `CodexDeviceAuthorization`:

```python
from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow


async def device_login(show_device_code, publish_login):
    authorization = await CodexDeviceAuthorizationFlow.start()
    await show_device_code(authorization.verification_uri, authorization.user_code)
    login = await authorization.wait_for_login()
    await publish_login(login)
```

The authorization exposes verification URI, user code, expiry, and polling interval; its private device token remains process-local. `wait_for_login()` owns bounded polling. It is the implemented Codex two-stage device flow, not a generic RFC 8628 grant. Do not repeat login automatically after cancellation or an uncertain persistence result.

`CodexLoginResult` contains upstream `OpenAICodexCredentials` and the ID token needed for login publication. Model requests use the upstream credential value; Harness does not introduce a second Codex refresh store.

### Build the Model

`a13n_harness.models.codex.CodexRequestModel(model_name, *, credential_source, http_client=None, thread_id=None)` accepts the upstream `OpenAICodexCredentialSource` protocol (`async load()` / `async save(credentials)`). In your Model resolver, pass `thread_id=context.deps.thread_id` to bind native Codex session headers for both streaming and non-streaming requests. The adapter applies the shared [UUID v5 affinity derivation](models.md#automatic-model-request-affinity) to that raw Thread ID; do not pre-derive it. Explicit native headers remain unchanged. These no longer derive from `x-session-id` or any other gateway header. Rebind from the current context for child Threads and forks; do not capture a parent's ID. Upstream Pydantic AI owns authentication, refresh, retries, and Responses rendering.

The wrapper owns its HTTP client only when it creates one. An injected client stays caller-owned. Harness scopes the Model for a Run; the wrapper's request/response hooks must not outlive their owning model use. Reconstruct account selection for a new Run instead of swapping accounts behind an active request.

## Grok browser and device flows

Supply the issuer, client ID, and scopes required by the application's configured integration; they are not universal Harness account defaults.

`GrokOAuthFlow.discover(issuer=..., client_id=..., scopes=..., redirect_uri=None, referrer=None, http_client=None, allow_insecure_loopback=False)` discovers and validates the browser authorization endpoints. Omitted redirect URI selects an available local loopback callback. Present `authorization_url()` and await `exchange_code_from_callback(timeout_seconds=...)`, then persist the returned `GrokCredentials`.

`OAuthFlow` is the abstract PKCE/callback boundary; use the concrete Grok flow rather than implementing token exchange in product UI code. An insecure-loopback option is for explicitly permitted local fixtures, not a way to allow arbitrary non-HTTPS identity servers.

The device alternative is:

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

`GrokDeviceAuthorization` exposes the verification URI, optional complete URI, user code, expiry, and polling interval. The device code is not model-visible. The flow follows its bounded pending/slow-down/denial/expiry outcomes.

### Credential sources and refresh

`GrokCredentials` holds account identity, auth mode, creation/expiry times, issuer/client ID, access token, and optional refresh token. `GrokCredentialSource` requires `async load()` and `async rotate(expected, exchange)`. The host coordinates the complete read, grant spend, and durable publication interval.

`build_grok_model(model_name, *, credential_source, refresh=None, refresh_window=timedelta(minutes=5), http_client=None)` constructs a native Responses Model backed by that source. `refresh_grok_credentials(credentials, *, http_client=None)` is the standalone refresh operation; it returns credentials and does not publish them to a Host store for you.

The model credential manager checks identity across refresh/reload and persists rotation before using it. Handle persistence failure as a failed credential transition, not success merely because the remote token endpoint answered. An injected HTTP client remains caller-owned; the adapter owns clients it creates.

## Authentication failures

| Public exception             | Meaning                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------ |
| `ModelAuthenticationError`   | Safe bounded model-authentication failure; derives from native `ModelAPIError` |
| `CredentialRefreshError`     | Rejected or invalid credential refresh                                         |
| `DeviceAuthorizationError`   | Terminal `expired`, `denied`, or `unsupported` device outcome                  |
| `CredentialPersistenceError` | Rotated credentials could not be saved before use                              |

Keep detailed secrets and raw provider responses out of public error presentation. The Host decides whether to retry a safe operation, ask for reauthentication, or reconcile its store. A provider rejection does not authorize silently switching to another account.

## Own a Model HTTP client

`a13n_harness.models.create_model_http_client()` constructs a caller-owned `httpx2.AsyncClient`. Inject it into a compatible native Provider; request headers remain `ModelSettings.extra_headers`, not extra transport settings invented by Harness.

```python
from a13n_harness.models import create_model_http_client


async def use_provider_client(build_and_run):
    async with create_model_http_client(timeout=120, connect=5, retry=None) as client:
        # Application callback constructs a compatible Provider and awaits its Run.
        return await build_and_run(client)
```

The default timeout is 600 seconds with a five-second connect timeout; both arguments require positive integers. `transport` optionally supplies an `httpx2.AsyncBaseTransport`. `retry=None` disables automatic transport retries.

`ModelHttpRetryConfig` defaults:

| Field                          | Default            |
| ------------------------------ | ------------------ |
| `attempts`                     | 5 total attempts   |
| `backoff_multiplier`           | 1.0                |
| `max_wait_seconds`             | 30.0               |
| `retry_after_max_wait_seconds` | 300.0              |
| `status_codes`                 | 429, 502, 503, 504 |

The helper also retries supported timeout/connect/read errors. Attempt count must be positive; waits are finite and nonnegative; status codes must be valid HTTP integers. `DEFAULT_MODEL_HTTP_RETRY_CONFIG` and `DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES` expose these defaults. Do not assume this Model transport has the same policy as the [Service SDK transport](../a13n-service/sdks.md).

## Do not combine retry budgets accidentally

| Mechanism                             | Scope                                                                                               |
| ------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Native tool/output validation retries | Agent-loop validation and retry prompts                                                             |
| Model HTTP retries                    | A provider HTTP operation                                                                           |
| Interrupted-history repair            | Make retained history structurally consumable, without claiming missing side effects never happened |
| `ModelRecoveryPolicy`                 | Additional model attempts within one logical Harness Run                                            |
| Host worker replacement / user retry  | A new execution selected and owned by the Host                                                      |

`ModelRecoveryPolicy` is disabled by default, with `max_attempts=5` consecutive failed attempts when enabled, initial backoff 1 second, and maximum 30 seconds. An accepted primary model response resets the count and backoff. Only recognized transient failures are eligible; permanent or unknown provider errors are not retried. It accepts a continuation prompt or a prompt factory. Internal model attempts share Run context and usage; they are not new durable worker attempts. [Agents and Runs](agents-and-runs.md) owns the build/run API and recovery behavior.

For a simple embedding store exposing `load()` and `save()`, wrap it once in `ProcessGrokCredentialSource` and share the wrapper across Models. Its lock and uncertain-grant evidence last only for that process. A possibly consumed grant is blocked after response loss, cancellation, or save failure; changing its metadata does not make it safe to retry. Reauthenticate with a new grant. `RefreshNotDispatched` distinguishes a proven failure before token dispatch.

Harness UI supplies stronger file-store coordination: cooperating processes share one lock and a durable non-secret grant-fingerprint sidecar. This protects fresh Runs and restarts without storing a second token copy. Product CLI writers do not participate in that lock; their detected edits cause a conflict rather than an overwrite.
