# Local live tests

These opt-in tests send real HTTP requests to separate local Control and Worker processes. Control accepts Native API requests; Worker readiness is checked over HTTP, and execution is dispatched through the real queue. The tests never call Worker execution internals or use an in-process ASGI transport for live journeys.

| File                                               | Journey                                                                                                        |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `harness_integration/test_01_basic_run.py`         | Acceptance, execution, result, attempts, usage and retained items                                              |
| `harness_integration/test_02_continuation.py`      | Successor Run reconstructs conversation context                                                                |
| `harness_integration/test_03_tools_environment.py` | Real local shell writes and reads a file                                                                       |
| `protocol/test_04_stream_reconnect.py`             | Disconnect during execution and resume with Last-Event-ID                                                      |
| `control/test_05_idempotency.py`                   | Concurrent duplicate submission and changed-intent conflict                                                    |
| `control/test_06_steer.py`                         | Idempotent mid-tool input has one durable inbox row and one occurrence in the actual model request; no new Run |
| `control/test_07_interrupt.py`                     | Interrupt model I/O and tool execution; verify teardown                                                        |
| `control/test_08_approval.py`                      | Approve/reject pending work through a successor Run                                                            |

The first round has ten live cases because interrupt and approval each have two variants. The deterministic OpenAI-compatible model fixture controls timing and expected answers. This tests Foundation orchestration and real tool execution, not external model quality or provider compatibility. The approval tool comes from a trusted plugin loaded into the Worker at startup.

## Test organization

Tests are grouped by their primary feature, independently of their execution opt-in. The original filenames and case numbers are retained for traceability; numbers are historical labels, not a global sequence or an execution dependency.

| Directory               | Primary responsibility                                                           | Modules | Collected cases |
| ----------------------- | -------------------------------------------------------------------------------- | ------: | --------------: |
| `environment/`          | Environment selection, access, files, providers, lifecycle and shared Worker use |      22 |             366 |
| `control/`              | Run commands, waiting, inbox, queue, branches and acceptance races               |      21 |             193 |
| `run_recovery/`         | Worker ownership, persistence, budgets, dependency failures and drain            |       8 |             653 |
| `harness_integration/`  | Service configuration and resources reaching real Harness execution              |      11 |              29 |
| `model/`                | Frozen Model settings and current Provider settings between requests             |       1 |               3 |
| `protocol/`             | Native and Hosted streams, reconnect and recovery projection                     |       3 |              13 |
| `iam/`                  | Workspace isolation, current authority and revocation                            |       3 |              10 |
| `providers/`            | Optional real search, model, environment and Connector accounts                  |       1 |              15 |
| `observability/`        | Cross-layer evidence and telemetry failure isolation                             |       1 |               2 |
| `performance/`          | Long-session operation latency and retained-history correctness                  |       1 |               1 |
| `infrastructure_tests/` | Offline tests of fixtures, configuration, parsers and cleanup                    |      12 |             220 |

Counts are a collection snapshot: 84 modules and 1,505 parameterized cases, including inherited lifecycle cases and cases skipped unless explicitly enabled. A category does not enable infrastructure: the existing `--live*` fixture gates still apply. Mixed modules remain intact: `test_26_output_and_client_tools.py` and `test_32_multiworker_resources.py` live under `harness_integration/`, while `providers/test_31_real_providers.py` retains its real-account matrix.

Subpackages inherit the root `conftest.py`. Feature-owned helpers live beside their tests; shared lab infrastructure lives in `infrastructure/`. The isolated launcher recursively selects the same first-round filenames and keeps their filename order.

```sh
# Offline support and full discovery checks; no opt-in infrastructure:
make live-test-check
# Control includes both core and fault-lab tests; enable the desired gate:
uv run --locked python -m pytest dev/live_tests/control --live-round-two
# A focused management category:
uv run --locked python -m pytest dev/live_tests/model --live-management
```

## Helper ownership

Use the primary responsibility of a helper to choose its location:

- Put feature-specific scenarios, model scripts, plugins, native observers and host extensions beside that feature's tests. For example, Control owns its inbox and fork helpers, Environment owns lifecycle/file fixtures and its fixture Dockerfile, and Protocol owns SSE parsers and contract oracles even when other suites consume them.
- Put shared clients, lab composition, storage setup, resource provisioning and fault barriers in `infrastructure/`. Its `host.py` composes feature extensions; this is deliberate test-host composition, not a requirement that feature code be independent.
- Keep tests of all those helpers in `infrastructure_tests/`. This directory contains offline test cases, not the helper implementations themselves.
- Keep `conftest.py`, `manage.py` and `isolated.py` at the root as the common pytest and command entry points. Private `.state/` and `providers.local.toml` remain at their existing ignored locations; the example configuration stays beside them.

| Location               | Helpers                                                                                                                                                                                                                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `control/`             | `approval_plugin.py`, `async_children_model.py`, `control_children.py`, `control_fault_host.py`, `control_support.py`, `fixture_inbox.py`, `fork_fault_host.py`, `fork_support.py`, `queue_fault_host.py`                                                                                                                       |
| `environment/`         | `docker_lifecycle_host.py`, `e2b_host.py`, `e2b_support.py`, `environment_backends.py`, `environment_host.py`, `environment_worker_host.py`, `environment_workers.py`, `file_backends.py`, `file_contract.py`, `file_resource_worker.py`, `lifecycle_cases.py`, `lifecycle_host.py`, `lifecycle_support.py`, `service_cases.py` |
| `harness_integration/` | `fixture_connectivity.py`, `management_model.py`, `plugin_image.py`, `plugin_package/`, `plugin_worker.Dockerfile`                                                                                                                                                                                                              |
| `iam/`                 | `native_iam.py`, `run_fault_identity.py`                                                                                                                                                                                                                                                                                        |
| `infrastructure/`      | `client.py`, `config.py`, `fixture_model.py`, `fixture_peer.py`, `host.py`, `local_storage.py`, `management_packages.py`, `management_support.py`, `round_two_lab.py`, `round_two_model.py`, `round_two_resources.py`, `run_faults.py`, `tcp_proxy.py`                                                                          |
| `observability/`       | `fixture_telemetry.py`                                                                                                                                                                                                                                                                                                          |
| `performance/`         | `long_session_host.py`, `long_session_model.py`                                                                                                                                                                                                                                                                                 |
| `protocol/`            | `hosted_client.py`, `stream.py`, `stream_contract.py`                                                                                                                                                                                                                                                                           |
| `providers/`           | `provider_config.py`, `real_providers.py`                                                                                                                                                                                                                                                                                       |
| `run_recovery/`        | `resilience_plugin.py`, `run_fault_evidence.py`, `run_fault_host.py`, `run_fault_mcp.py`, `run_fault_model.py`, `run_fault_plugin.py`, `run_fault_support.py`                                                                                                                                                                   |

The Environment fixture image is built from `environment/file_resources.Dockerfile` using the repository root as its build context. It copies `environment/file_resource_worker.py` into the existing standalone container entrypoint. Model scripts follow the scenario they drive; a mock model does not belong in `model/` merely because it emits model responses.

## Local state

`.state/` and `providers.local.toml` are local-only paths excluded by the root `.gitignore`. They contain generated test state and optional credentials, and must remain untracked. Existing local files stay on disk for test reuse and diagnosis. Never force-add these paths. Store custom Provider configuration selected with `LIVE_TEST_PROVIDERS_CONFIG` under `.state/` or outside the repository.

Before submitting changes, `git ls-files -- dev/live_tests/.state dev/live_tests/providers.local.toml` must return no paths. Only the blank example configuration belongs in Git; each developer creates their own local copy.

## Installed test plugins

The explicit live-test Worker host builds one immutable factory catalog from `control/approval_plugin.py` and `run_recovery/resilience_plugin.py` before serving. It supplies this catalog through the Service's trusted `Components.plugin_factory_catalog` composition boundary. Control does not import these factories. This uses plugin code already present in the checkout; setup never builds, uploads, or installs code through HTTP. Restart the Worker after changing a fixture plugin.

Agent configuration selects `live.approval` or `live.resilience` using `instance_name`, `plugin_key`, and `config`. Control stores the authored selection; the Worker validates and normalizes it before execution. A missing factory or invalid configuration fails the Run before model or tool effects. See the [installed plugin contract](../../spec/a13n-service/36-installed-harness-plugins.md).

### Packaged plugin image journey

Case 19, [`harness_integration/test_19_plugin_image.py`](harness_integration/test_19_plugin_image.py), covers the build-to-execution path separately from the explicit factory fixtures:

1. Build the current checkout with the production Service Dockerfile, then build the standalone `harness_integration/plugin_package` into a wheel.
2. Install that wheel into a derived Worker image at build time. The package declares its `live.packaged` factory through `a13n_harness.plugins`; it imports no live-test helpers and is never installed in the host/Control environment.
3. Start the image with its inherited production entrypoint and command, selecting the factory through `A13N_SERVICE_PLUGIN_KEYS`. No explicit catalog, source mount, or Worker monkeypatch is supplied.
4. Create an Agent through Control HTTP and execute the plugin's capability tool and Harness result middleware. Independently read the file effects, the model's actual tool result, and the persisted Attempt. Check the configured label's hash, wheel metadata, installed module path, non-root UID, Harness Run ID, and Worker build identity.
5. Change the plugin configuration through an Agent Revision and verify the changed effect; remove the binding and verify both the tool and middleware disappear. Invalid configuration and an installed-but-unselected factory must fail before any model call or effect.

```sh
make live-test-plugin-image
```

This explicit `--live-plugin-image` opt-in builds images and owns disposable PostgreSQL, Redis, object storage, Control, and Worker resources. It requires a local Docker daemon and build access to the base images and Python build dependencies. Linux uses host networking; Docker Desktop uses `host.docker.internal` and a loopback-published Worker probe port. It does not require Docker Desktop host networking to be enabled. On Docker Desktop, Control disables save-time DNS resolution for the container-only hostname; the Worker retains normal request-time DNS and endpoint allowlist checks. Only a fixture-owned effects directory is mounted into the Worker. Containers and uniquely tagged images are removed on exit; build logs and wheels remain under `.state/plugin-images/`, while private service logs and execution evidence remain under `.state/management/`.

The model is the deterministic HTTP fixture and returns observed tool results. This case covers installation, startup discovery, Agent binding, configuration, capability execution, and result middleware; it does not cover registry publication, rolling upgrades, cross-version state migration, dependency conflicts, or concurrent plugin instance isolation. Existing case 18 and recovery tests retain their narrower, faster coverage. Without this opt-in, the image journey skips before building or contacting services.

## Optional real Provider configuration

Create `dev/live_tests/providers.local.toml` from the committed blank example:

```sh
cp -n dev/live_tests/providers.example.toml dev/live_tests/providers.local.toml
chmod 600 dev/live_tests/providers.local.toml
```

The local file is gitignored. Every section is optional and independent. A missing default file or an entirely blank section leaves the current deterministic tests unchanged and skips that section's additional integration journey. Fill only the sections you want to exercise. A partially filled, invalid or unsupported section fails instead of silently falling back. `LIVE_TEST_PROVIDERS_CONFIG` can select a different TOML file; an explicitly selected missing file is an error. Keep custom files outside Git too. Keys are never taken implicitly from application `.env` settings, existing Provider records or previous test state.

If a trusted local proxy resolves a configured model hostname to a private or reserved address, explicitly set `LIVE_TEST_MODEL_PRIVATE_ENDPOINT_DOMAINS` to a JSON array of those operator-approved domains, such as `["openrouter.ai"]`. This uses the normal Service endpoint allowlist only in disposable lab processes; the default is empty and HTTPS validation remains enabled. Real Provider journeys allow 90 seconds per Control HTTP request for cloud catalog discovery; local deterministic journeys retain their shorter timeout.

| Parameter                   | Meaning when enabled                                                     | Empty/default behavior                                                                                             |
| --------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `environment.type`          | `a13n.e2b`, the native E2B Environment implementation                    | No additional cloud Environment test; existing direct-local and explicit Docker cases keep their current providers |
| `environment.api_key`       | E2B account API key; required with `type`                                | No credentials required by the existing local cases                                                                |
| `environment.template`      | Optional E2B template ID or alias                                        | `base` when E2B is enabled                                                                                         |
| `connector.provider`        | `composio` or `openconnector`                                            | No additional external Connector test; case 27 keeps its local TLS Composio fixture and local MCP server           |
| `connector.api_key`         | Composio project API key; required with `provider`                       | Existing connectivity fixtures use a generated lab-only credential                                                 |
| `connector.toolkits`        | Optional nonempty list of Composio toolkit keys to discover              | `["github"]` when Composio is enabled; leave the example line commented when the section is disabled               |
| `connector.project_api_key` | OOMOL Project API key; required for `openconnector`                      | No default; create under Console → Projects → your project → API Keys                                              |
| `connector.catalog_api_key` | OOMOL personal API key for catalog reads; required for `openconnector`   | No default; create at <https://console.oomol.com/api-key>                                                          |
| `connector.services`        | Optional nonempty list of OpenConnector service keys                     | `["slack"]` when OpenConnector is enabled                                                                          |
| `model.provider`            | `openrouter` or `openai`                                                 | No additional external Model test; existing cases keep their scripted local model                                  |
| `model.api_key`             | Model provider API key; required with `provider`                         | Existing scripted model uses a generated lab-only credential                                                       |
| `model.model`               | Upstream model ID supporting Chat Completions; required for `configured` | Fixed OpenRouter matrix cases ignore this field and use their own model IDs                                        |
| `model.base_url`            | Required HTTPS API base URL for `openai`                                 | Leave blank for `openrouter`, which uses the service's built-in endpoint                                           |
| `search.provider`           | `exa`                                                                    | No external search journey; requires no Model or Connector API key                                                 |
| `search.api_key`            | Exa API key with Search access                                           | No implicit key lookup; fill together with `search.provider`                                                       |
| `brave_search.provider`     | `brave`                                                                  | No additional Brave search journey; independent of the Exa section                                                 |
| `brave_search.api_key`      | Brave API key with Web Search access                                     | Fill together with `brave_search.provider`; never reuse the Exa key                                                |

OpenRouter uses the native `openrouter` Provider and `openrouter.chat_completions` API. Its endpoint is `https://openrouter.ai/api/v1`, as described in the [OpenRouter quickstart](https://openrouter.ai/docs/quickstart). A custom compatible endpoint uses `openai` and `openai.chat_completions`; URLs must not contain credentials, query strings or fragments.

```sh
# Run the noninteractive Provider journeys (blank sections skip):
make live-test-providers
# Run management cases plus the configured Provider journeys:
make live-test-management
# Select just one configured Provider:
make live-test-providers LIVE_TEST_ARGS='-k configured_model'
# Run only the three OpenRouter model cases:
make live-test-providers LIVE_TEST_ARGS='-k "configured_model and openrouter"'
# Run only the four Exa search cases:
make live-test-providers LIVE_TEST_ARGS='-k configured_search_exa'
# Run only the four Brave search cases:
make live-test-providers LIVE_TEST_ARGS='-k configured_search_brave'
# Run Brave's single-result boundary with the full mock LLM round trip:
make live-test-providers LIVE_TEST_ARGS='-k "configured_search_brave and single-result"'
# Select Exa's corresponding single-result boundary:
make live-test-providers LIVE_TEST_ARGS='-k "configured_search_exa and single-result"'
```

`providers/test_31_real_providers.py` is also collected by `make live-test` and `make live-test-round-two`. `make live-test-local` runs the first-round files only; run `make live-test-providers` alongside it for external integration coverage. Existing timing, fault injection and management assertions always retain their deterministic dependencies, even when all sections are configured. `make live-test-check` never reads this private file or contacts these providers.

Each enabled section starts its own disposable PostgreSQL, Redis, object-storage bucket, Control and Worker lab. Initialization creates the Provider and associated resources through Control HTTP, persisting credentials encrypted in that lab's database. No external credentials are copied into retained lab configuration.

### Exa and Brave search

Fill either or both sections in `providers.local.toml` (the example remains blank):

```toml
[search]
provider = "exa"
api_key = "YOUR_EXA_API_KEY"

[brave_search]
provider = "brave"
api_key = "YOUR_BRAVE_API_KEY"
```

Exa (`configured_search_exa`) and Brave (`configured_search_brave`) each run the same four cases using their production Search Provider adapter against its fixed official HTTPS endpoint. Brave requires an API key with Web Search access. The two sections are independent: each case uses only its selected account's key and never falls back to the other provider. Each case creates a saved Workspace account through Control in its own disposable lab. The three Run cases create a local direct-local Environment and an Agent with an explicit `search.provider_id`; the scripted local model selects the real Harness `search` tool. No paid Model, cloud Environment, Connector or browser authorization is required.

| Case                          | Flow and acceptance checks                                                                                                                                                                                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `account_probe`               | Read the saved account, require `credential_configured=true` without credential disclosure, call its `/test` endpoint, and require `success=true`, null error code and a timezone-aware `checked_at`. Reading again must return the unchanged account.                                        |
| `run[requested-limit]`        | Search for `Python asyncio documentation` with `num=2` and Agent `max_results=3`; require a completed Run and 1–2 actual tool results.                                                                                                                                                        |
| `run[domain-and-agent-limit]` | Use the same query with `num=10`, Agent `max_results=2` and `include_domains=["python.org"]`; require 1–2 results, all from `python.org` or its subdomains.                                                                                                                                   |
| `run[single-result]`          | Submit a user request through a real Run. Require the mock LLM's streamed `search` call with the intended query and `num=1`, a matching `tool_call_id` on the real search response, one official Python source, and a final public Run output equal to the search result received by the LLM. |

Run assertions read the actual tool result from the subsequent model request, require `ok=true`, a matching `showing` count, nonempty titles, HTTP(S) URLs with hostnames and string snippets. An empty result fails these known-query smoke cases; a failed tool cannot pass merely because the scripted model completed. Ranking, exact titles, snippet wording and an exact result count are deliberately not fixed because the upstream index changes. Domain checks verify the final disclosed results; the adapter's offline contract tests separately verify the outgoing Exa `includeDomains` request field. Brave applies the domain restriction locally to its returned candidates; this live assertion covers the final disclosed results for both providers, without claiming Brave sends an upstream domain filter.

Every search Run parameter verifies the full mock LLM cycle: the first Chat Completions request advertises `search` and contains no tool result; the next model request contains the assistant's tool call and its matching response. The mock LLM echoes that response in its streamed final answer, and the test decodes the public Run output and compares it with the actual tool result. None of the selected source URLs may already appear in the first model request. Only tool selection and arguments are scripted; provider transport, search results, Harness execution and Run persistence are real. This verifies protocol and result propagation, not LLM search judgment or answer quality.

With no failures this suite makes four upstream search requests per configured provider (eight when both are configured), which can consume provider quota: one account probe and one per Run. The production Run path may retry transient errors under its existing bounded policy; these tests add no retries. Probe failures, quota errors and timeouts fail explicitly when configured. Missing or entirely blank `[search]` or `[brave_search]` skips that provider's four cases before starting infrastructure; partial or invalid configuration fails. `make live-test-check` verifies configuration and opt-in guards offline without reading the private configuration or contacting Exa or Brave.

INFO logs report the Provider/Run IDs, probe timestamp, result counts and applied limits/domains. Private lab logs and `workspace/<case_id>/observations.jsonl` remain under the printed lab directory for inspection; the observations contain actual model requests and search results. Cleanup cancels owned Runs and removes the disposable database and its encrypted credentials even on failure. Direct Local has no destroy lifecycle action; its lab-owned directory remains alongside the private evidence. Cloud Environments still receive their normal delete commands. Keep the private configuration mode `600`.

### Other Providers

The Environment journey executes a real Shell write and file read in E2B, using the scripted model to control tool selection. The Connector journey performs real authentication and catalog discovery. An API key alone does not authorize a user's OAuth accounts: this journey creates no account connection and executes no business tools unless the separate Slack OAuth journey is explicitly enabled below. The Model journey sends `Hello` through a real Run and requires successful completion with nonempty text; it does not assert exact wording or a fixed attempt count. Each request has a 128-token output limit. In addition to `model.model`, an OpenRouter configuration runs three separately reported cases:

| Pytest case ID      | Upstream model                 |
| ------------------- | ------------------------------ |
| `openrouter-gpt`    | `openai/gpt-4.1-nano`          |
| `openrouter-gemini` | `google/gemini-2.5-flash-lite` |
| `openrouter-claude` | `anthropic/claude-haiku-4.5`   |

These cases reuse the configured OpenRouter credential, each in its own lab, without changing the local TOML. They ignore `model.model` and apply their fixed model IDs before configuration validation, so that field can be omitted or left blank when selecting only the matrix. Provider, credential and endpoint validation still applies. The `configured` case still requires `model.model`; select only the fixed cases with `LIVE_TEST_ARGS='-k "configured_model and openrouter"'` when it is absent. They skip for `openai`; the configured model case continues to cover that provider. Logs identify the upstream model, Run ID and output length without printing credentials or model output. External usage can consume credits.

On success, failure, partial provisioning or normal cancellation, cleanup interrupts owned Runs and deletes owned remote Environments while the Worker is still running. Cleanup failures fail the test and identify the Environment ID. E2B sandboxes also have a five-minute timeout as a bound if the test process is forcibly killed. The lab then removes its containers and database, deleting all Provider rows, encrypted keys, templates, models and Agents created there. It never deletes or rotates existing account credentials or touches an existing installation's data. The local TOML remains available for later runs; ignored private process logs and test evidence remain under `.state/management/<random-id>/`.

### OpenConnector and interactive Slack authorization

The built-in `openconnector` adapter uses OOMOL's hosted Project API at `https://connector.oomol.com`; no OpenConnector deployment is needed. In [OOMOL Console](https://console.oomol.com), create a test Project and a Slack Provider config using OAuth2 and **System Client**. Create a Project API key in that Project and obtain a personal API key from the separate [API Keys page](https://console.oomol.com/api-key). `catalog_api_key` is our local field name for that personal key, not a separate OOMOL key type. See the [OOMOL SaaS setup guide](https://oomol.com/en/docs/connector-saas/).

Use the following `[connector]` section in a private TOML file. Omit the Composio `api_key` and `toolkits` fields when selecting OpenConnector:

```toml
[connector]
provider = "openconnector"
project_api_key = "<OOMOL Project API key>"
catalog_api_key = "<OOMOL personal API key>"
services = ["slack"]
```

For example, save it as `dev/live_tests/.state/openconnector.toml` with mode `0600` to preserve an existing Composio configuration. Then run:

```sh
# Noninteractive: verify catalog credentials and discover configured services.
LIVE_TEST_PROVIDERS_CONFIG=dev/live_tests/.state/openconnector.toml \
  make live-test-providers LIVE_TEST_ARGS='-k configured_connector'

# Interactive: authorize a new Slack account binding and execute a read-only tool.
LIVE_TEST_PROVIDERS_CONFIG=dev/live_tests/.state/openconnector.toml \
  make live-test-providers LIVE_TEST_ARGS='--live-slack -k openconnector_slack'
```

The interactive journey creates a fresh Workspace ConnectorConnection through Control. Its log identifies a mode-`0600` `slack-authorization.json` file inside the private lab directory. Open its `redirect_url` in a browser and authorize the test Slack workspace within ten minutes. Control polls OOMOL and verifies the exact account before publishing readiness; this flow needs no public Service callback origin. The temporary authorization file is removed when waiting ends. Review the actual Slack consent screen: OOMOL's System Client can request read and write permissions even though this test executes only a read-only action. Use a dedicated test workspace with permission to install the app.

The scripted model then calls only `slack.list_channels` with `limit=1`, through a real Agent Run and Worker. Assertions check readiness, tool selection, a successful provider outcome, and the returned channel schema. Logs report connection/Run IDs and the channel count, without printing keys or channel contents. A completed Run with a failed or unknown tool outcome does not pass. The catalog-only check proves only `catalog_read`; the Project key is exercised by this OAuth/execution journey.

Each interactive run uses a fresh isolated Workspace and therefore requires a new authorization. Ordinary live-test targets never start Slack OAuth without `--live-slack`. OOMOL's published Project API has no account-revoke operation: local lab teardown does not remove remote test accounts. Manage those in the test Project's **Connected accounts** page after testing.

## Disposable local setup

With Docker running, execute the first round without preparing `.env` or starting service processes manually:

```sh
make live-test-local
# Run cases 3 and 4, in file order:
make live-test-local LIVE_TEST_ARGS='-k "environment_tool or stream_disconnect"'
```

This entry point creates fresh PostgreSQL, Redis and RustFS containers, applies committed migrations, provisions the first-round resources through Control HTTP, and starts separate Control and Worker processes on new loopback ports. It does not use an existing installation's database, credentials or service listeners. Test subprocesses explicitly bypass proxies for loopback, including macOS system proxies, so interrupt checks observe the Worker's actual model connection. RustFS uses the digest pinned in `infrastructure/local_storage.py`; the first invocation may need to download images. Startup allows 10 seconds for its loopback port mapping and replaces a container with a missing mapping, up to three startup attempts. This handles Docker Desktop host-port collisions; each failed owned container is removed before retrying. Once mapped, startup waits for the authenticated S3 API, then runs the Service's unchanged conditional-write/delete and concurrent-write probes.

For Environment failures, private Worker and Control logs include `managed_tool_resource_resolution_failed`, `environment_lifecycle_failed`, or `run_attempt_execution_failed`. Their `exception_chain` fields retain exception types, stack locations, numeric HTTP status/OS error codes when available, and causes suppressed by provider wrappers. Run, tool-call, Environment and operation IDs identify the relevant boundary. Routine diagnostics omit exception messages, locals, source lines and payloads; model-visible errors remain bounded.

The Environment uses the `default` Shell profile expected by Harness, with `/bin/sh` and no extra `-c` argument. Only first-round resources are provisioned; fault relays and the second identity are omitted. On completion, failure or interruption, the runner cleans up its own services and containers. Private configuration, process/test logs, file evidence and `results.json` remain under ignored `.state/core/<random-id>/`. These configuration files describe disposable resources; rerun the command to create a new installation instead of reusing them.

## Manual setup with existing dependencies

Run commands from the repository root after the normal development setup. Configure `.env` with the actual PostgreSQL and Redis endpoints and apply committed migrations using `make db-upgrade`. Check Docker's published PostgreSQL port if the configured port is not reachable.

Separate processes require the same compatible S3 bucket. Configure `A13N_SERVICE_OBJECT_BACKEND=s3`, `A13N_SERVICE_OBJECT_BUCKET`, `A13N_SERVICE_OBJECT_ENDPOINT_URL`, `A13N_SERVICE_OBJECT_REGION`, and, if required, `A13N_SERVICE_OBJECT_FORCE_PATH_STYLE=true`, plus the backend's AWS credentials. The endpoint must pass the service's conditional-write/delete compatibility probe. The default local object backend is not supported for these separate processes.

```sh
make live-test-init
# Terminal 1
make live-test-control
# Terminal 2
make live-test-worker
# Terminal 3, after both roles are ready
make live-test-setup
make live-test
```

The initializer creates a dedicated Organization, Workspace, User and admin role bindings in the configured database. Re-running it retains the same identity. Setup creates the Model Provider, Model, Environment Provider, Environment and Agents through Control HTTP. It records each successful creation for reuse. The approval Agent selects the Worker's installed `live.approval` factory.

The explicit test host installs a private bearer authenticator and the fixture routes on Control. It binds to loopback and does not modify production startup. Default origins are `http://127.0.0.1:18000` and `http://127.0.0.1:18001`; `LIVE_TEST_CONTROL_URL` and `LIVE_TEST_WORKER_URL` override them. Set overrides consistently for both processes, setup and tests before provisioning resources. Control and Worker must run on this machine because the direct-local Environment and fixture evidence use the same absolute workspace path.

Credentials, resource IDs and test files live in ignored `.state/` under this directory. The configuration file is private (mode 0600). Keep it to reuse setup; do not publish it. Tests retain settled Runs and evidence for diagnosis and interrupt only their own active Runs during cleanup. Stop the two test processes with Ctrl-C when finished. No automatic database/resource deletion is performed.

## Selection and validation

```sh
make live-test LIVE_TEST_ARGS='-k basic'
make live-test LIVE_TEST_ARGS='-k interrupt'
make live-test-check
```

Use `LIVE_TEST_ARGS='-k interrupt'` to select interrupt cases. Without `--live`, the ten live cases skip and do not contact services. `live-test-check` checks formatting, lint and offline support tests; it does not prove the live journeys pass.

To authenticate an existing local Control installation, stop its current Control process and run `make live-test-auth-control`. This uses the ordinary `.env` settings and the configured Control port, adding the private test bearer identity and `/__live__` fixture routes. It does not change the Worker's queue, storage or encryption settings, and requires the model endpoint loopback allowlist below. The regular service executable remains unchanged. Missing, incorrect or duplicate bearer credentials are rejected. Plugin journeys require the explicit live-test Worker host described above.

Waiting Run responses expose `sealed_state_digest_sha256`; case 8 passes this public value to Native feedback without reading private state from the database.

For the local model fixture, set `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS='["127.0.0.1/32"]'` in `.env` and restart both roles so Worker model calls can reach the loopback fixture.

## Second round: persistence, concurrency and faults

| File                                         | Variants and acceptance evidence                                                                                                                                                                                |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_recovery/test_09_failures.py`           | Model authentication failure, model read timeout, and exhausted tool retries: bounded failed Run, diagnostic failure, no tool effect, released capacity                                                         |
| `run_recovery/test_10_worker_recovery.py`    | SIGKILL and suspended stale owner with one or three simultaneous replacement contenders: one successor Attempt, same Run/input/revision, checkpointed effect retained once, immutable replaced Attempt and seal |
| `run_recovery/test_11_graceful_shutdown.py`  | SIGTERM: clean process exit, no new claims, ordinary completion or planned handoff, replacement Worker drains pending work                                                                                      |
| `run_recovery/test_12_worker_competition.py` | Four ready Workers released together for one Run, plus two one-slot Workers and four slow Runs: one successful Attempt per Run, one checkpointed effect, bounded admission                                      |
| `control/test_13_queue_retry_fork.py`        | Busy-Thread FIFO consumption, explicit Retry after repairing the upstream, Fork retaining context in a separate Thread; original history remains immutable                                                      |
| `control/test_14_async_subagents.py`         | Two durable children; independently gated arrival at accepted/running/completed/waiting (approval and client tool)/failed/cancelled parents; durable consumption or suppression                                 |
| `run_recovery/test_15_dependency_outages.py` | Worker-only PostgreSQL, Redis and object-store connection cuts: prove the cut was exercised, restore and restart, validate terminal state and continued service availability                                    |
| `iam/test_16_workspace_isolation.py`         | Another User in the same Organization and a different Workspace: valid own access, denied reads/stream/control, no unauthorized state changes                                                                   |

Case 14 fixes the destination status before releasing either child's model response. The accepted destination is an explicit continuation of the completed spawning Run; draining child Workers finish their existing work without claiming that continuation. Waiting cases retain pending actions and child inbox entries until explicit feedback, then check that the successor's first model request contains no child results. Failed and cancelled spawning Runs suppress results while their independent children finish normally. Assertions read real HTTP model observations and an authenticated, read-only test inbox endpoint backed by the actual PostgreSQL records. The test never writes database state to manufacture a Run status. These cases cover all six Run statuses; they do not enumerate every selected-head, queue, crash, or child-outcome combination in the async-subagent contract.

These are 24 additional live variants in eight files. They use the real Control, Worker and embedded Harness, with HTTP model responses and an installed test tool plugin controlling errors and checkpoint timing. The plugin records non-idempotent file effects so duplicate execution is visible. It does not install Environment tools or replace the core round's Environment integration checks. The recovery assertion concerns a **completed checkpoint**; effects that happened before a crash without a completed checkpoint can be replayed and require tool-owned idempotency in applications.

Run this round separately:

```sh
make live-test-round-two
make live-test-round-two LIVE_TEST_ARGS='-k worker_replacement'
make live-test-round-two LIVE_TEST_ARGS='-k workspace'
```

Only `--live-round-two` enables these tests. `make live-test` continues to run the first round. Neither default collection nor `make live-test-check` starts Foundation, containers, or dependency faults; support tests may open short-lived loopback echo sockets to validate the fault relay itself.

Docker is required. Without `A13N_SERVICE_OBJECT_ENDPOINT_URL`, each lab starts the same pinned, compatible RustFS container used by `live-test-local`. To use an existing loopback S3 server, configure that endpoint, `A13N_SERVICE_OBJECT_REGION` and its AWS credentials in `.env`. The endpoint must pass the Service's storage compatibility probe; an arbitrary MinIO version is not sufficient. The test creates its own random bucket, never the bucket named by `A13N_SERVICE_OBJECT_BUCKET`. For an external endpoint, cleanup deletes that temporary bucket and its objects. For owned RustFS, cleanup removes the container and its anonymous volumes directly, without enumerating every object first. An explicitly configured but incompatible endpoint fails setup rather than silently switching storage or skipping enabled cases. Run only one selection at a time; these journeys do not use pytest-xdist.

Each test owns new PostgreSQL and Redis containers, fresh migrated schemas, identities, loopback ports and service subprocesses. It never discovers or kills an existing Worker PID, never resets a developer database and never stops a shared dependency. Network faults close only connections through a fixture-owned TCP listener used by that test's Workers; Control keeps its independent healthy connection for observation. Cleanup restores proxies and terminates only owned process groups before removing owned containers and any temporary bucket on an external endpoint. Child cancellation is tested against the currently exposed default `independent` policy; this suite does not fabricate a private cancellation-policy selection.

Private configurations, process logs and model/tool evidence are retained under `.state/round-two/<random-id>/`; their database and bucket are disposable. Logs identify accepted Run/Thread IDs and failed assertions retain API observations. An explicit `LIVE_TEST_CONFIG` path lets child processes share their lab's private configuration without changing the existing first-round installation.

Static/support checks establish fixture correctness and collection only. Report live results separately; neither collected cases nor skipped cases prove that Foundation passes the corresponding contract.

### Run persistence and dependency fault matrix

Cases 37–42 add 66 P0/P1 variants under the same `--live-round-two` opt-in. Select them without the earlier journeys:

```sh
uv run --locked python -m pytest dev/live_tests/run_recovery/test_3[79]_run_*.py dev/live_tests/run_recovery/test_42_run_*.py \
  dev/live_tests/control/test_38_run_*.py dev/live_tests/control/test_40_run_*.py \
  dev/live_tests/iam/test_41_run_*.py \
  --live-round-two -v --tb=short -o log_cli=true -o log_cli_level=INFO
```

| File                                                 | Priority and acceptance evidence                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_recovery/test_37_run_persistence_faults.py`     | P0: crash before/after a real tool effect with and without tool-owned idempotency; lost initial/progress/completed object-write responses; adopt a completed candidate after Worker death before SQL sealing. P1: missing, malformed, incompatible-schema and integrity-invalid recovery state; removed plugin factory and incompatible installed plugin state.                                      |
| `control/test_38_run_control_faults.py`              | P0: crash before/after receipt publication, exactly one incorporated Steer, late Steer and async child results continue the same Run, and Interrupt supersedes pending receipts despite published candidate state.                                                                                                                                                                                   |
| `run_recovery/test_39_run_budgets_and_drain.py`      | P0: zero/exhausted Attempt budgets, accepted deadline expiry, persisted model usage and retry backoff across replacement. P1: transient conditional-write/unavailability errors, bounded preparation failure, forced planned drain, drain timeout and checkpoint latency across lease renewal.                                                                                                       |
| `control/test_40_run_acceptance_and_queue_faults.py` | P1: Control death before/after acceptance with the same idempotency key; queue handoff before COMMIT, rollback after SQL mutations and lost COMMIT response; permanently invalid head is failed and the next queued intent progresses.                                                                                                                                                               |
| `iam/test_41_run_authority_faults.py`                | P0: disabled/downgraded Service Account or deleted bound Secret before first claim and replacement preparation; MCP/Connector revocation between model request and tool dispatch prevents remote calls.                                                                                                                                                                                              |
| `run_recovery/test_42_run_dependency_faults.py`      | P1: actual model HTTP 429/503 retry limits, truncated/malformed streams and timeout; real plugin exceptions/timeouts; repeated PostgreSQL/object disconnects, sustained object outage, lost database renewal cancelling an unreleased tool, and Redis Steer/Interrupt while retaining the original Worker process; MCP error and external effect followed by a failed response without blind replay. |

Model failure assertions include Service transport retries and the five total Harness ModelAttempts enabled by Service reconstruction. Persistent 429/503 rejection therefore stops after 15 HTTP requests in one Service RunAttempt; stream/timeout recovery stops after five. Protocol cases use a 60-second lease to separate protocol recovery from the short-lease takeover cases. Truncation uses the independent TLS peer so an upstream connection failure reaches the real model client without Service HTTP middleware. The Redis Steer case confirms durable acceptance during the outage, then restores transport before completion because Redis also owns live publication leases; Interrupt is checked while the relay remains cut.

These tests use the management lab's HTTP resource setup, local model/MCP peers, and independent Control/Worker processes with real PostgreSQL, Redis and RustFS. Their private artifacts are in `.state/management/<random-id>/`. No external model credentials or paid provider calls are needed.

The opt-in Host wraps specific production operations with lab-owned fault barriers. Each hit records its process, Run/fence/checkpoint facts and a bounded release marker under `faults/`. Barriers run outside SQL transactions; the queue rollback hook raises immediately inside the real transaction and pauses only after rollback. Hooks never fabricate Run, Attempt, Thread or inbox lifecycle records. The file created by the effect tool is the actual business effect, so its presence does not depend on a second receipt write.

The setup seams are explicit: the test Host supplies accepted execution policy limits, which have no public request setter, and seeds/deletes encrypted Secret fixtures because generic Secret CRUD has no public router. Service Accounts and connection changes use public APIs; the principal tests also compose native IAM management alongside the custom authenticator, because a custom authenticator normally disables that management runtime. A test bearer only authenticates the real principal, and production code still resolves current execution authorization. Workspace-scoped fixture routes expose read-only state/usage/lease evidence and permit deliberate damage only to owned active Run objects. These routes and hooks are absent unless the fault lab is enabled.

The matrix verifies named transitions and observable effects; it is not a line-coverage or mutation score, an exhaustive ordering proof, or a promise of exactly-once external effects. Real provider-specific retry behavior, generic Secret management APIs and all combinations of concurrent revocation/drain/ dependency faults remain outside this matrix. Offline support checks validate fault claiming, isolation, release and cancellation; run the live command above to validate Service behavior.

### Agent control transition matrix

Cases 45–52 provide 97 variants for the control boundaries in the table below; the queue suites described afterward add 29, and the Fork independence suites add 42. They share the isolated fault lab and `--live-round-two` opt-in. Run the control suites with:

```sh
uv run --locked python -m pytest dev/live_tests/control/test_4[56789]_control_*.py dev/live_tests/control/test_5[0123456]_control_*.py \
  --live-round-two -v --tb=short -o log_cli=true -o log_cli_level=INFO
```

| File                                       | Variants | Boundary and acceptance evidence                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `control/test_45_control_acceptance.py`    | 22       | Steer/Interrupt before and after first claim; completed sealing wins against late control; stale Interrupt versions fail without helper retries; Continue/Fork/Retry/Feedback/waiting Continue lose responses before and after commit; ineligible lifecycle states reject commands without mutation.                                                   |
| `control/test_46_control_waiting.py`       | 13       | Steer on both sides of waiting sealing; default resolution plus new input preserves queue and defers inbox; multiple waiting rounds with Worker death before first response or after checkpoint; applied input/effect recovery; partial, reversed, empty and null mixed feedback; stale/invalid feedback and forbidden execution overrides are atomic. |
| `control/test_47_control_concurrency.py`   | 12       | Different commands prepare from the same Thread version, with exactly one committed advancement; same-key successors reconcile one receipt; Fork and Continue advance independently from the same historical state while preserving separate Environment choices.                                                                                      |
| `control/test_48_control_branches.py`      | 15       | Historical Continue abandons waiting inbox while preserving queue; root/continued cancelled Retry retains its original parent and discards old Steer; failed/cancelled × absent/completed/waiting head × empty/nonempty queue admission matrix.                                                                                                        |
| `control/test_49_control_queue.py`         | 12       | Late Steer invalidates prepared handoff; PATCH/DELETE/reorder win before consumption or conflict after it; concurrent explicit consumers reconcile against background recovery; concurrent enqueue allocates unique FIFO positions; stale versions and recoverable revision blockers preserve editable intent.                                         |
| `control/test_50_control_inbox.py`         | 9        | Identical text with distinct Steer identities remains duplicated in FIFO; concurrent count overflow has no partial admission and capacity is reclaimed by consumption/cancellation; byte limit minus one/exact/plus one; deleted binary source fails before the model; lost Steer/Interrupt replies replay across Control restart.                     |
| `control/test_51_control_child_results.py` | 5        | Alternating Steer/child-result FIFO reaches real model context; cancelled-origin results arriving before cancellation or after Retry remain suppressed while fresh children deliver; blocked queue takes precedence over automatic child-result continuation; historical Continue supersedes child results and Steer together.                         |
| `control/test_52_control_steer_races.py`   | 9        | Both commit orders for Steer versus Feedback/waiting Continue and Interrupt/final failure; consumption commits before Interrupt without losing the consumed status or exact checkpoint receipt.                                                                                                                                                        |

`control/test_53_control_queue_races.py` adds 18 variants: background recovery and explicit consume both publish candidate initial state before either commits; enqueue competes with completed/waiting/failed/cancelled sealing in both orders; a new submission cannot bypass an existing queue during completed-Thread recovery; Interrupt competes with prepared completion handoff; and Retry or historical Continue competes with background consumption. Each race checks both winning orders, the selected parent, queue and Thread versions, and absence of the losing candidate Run. A rejected stale submission is also retried with the current Thread version to check immediate acceptance versus queue admission.

`control/test_54_control_queue_edges.py` adds 11 variants: three observed recovery scans leave a waiting head's queue untouched, including after failed/cancelled Feedback; a recoverable queue head blocks its tail while another Thread drains, then PATCH or DELETE unblocks it; two requests compete for the last queue slot, with replay at capacity and reuse after deletion or consumption; and consumed entries remain immutable after their Runs fail/cancel and Control restarts, while the next entry continues from the preserved completed or null head.

Run the queue additions independently with:

```sh
make live-test-round-two LIVE_TEST_ARGS='-k "test_53_control_queue_races or test_54_control_queue_edges" --log-disable=httpx2'
```

The Fork independence suites test both directions: one operation remains at an observed barrier while its peer makes independent progress. Assertions check that the barrier is still active, including its timeout/cancellation marker; accepting both HTTP requests alone does not satisfy the test.

Most variants require the peer to finish execution before release. When a checkpoint publication or replacement state read/writer claim is paused, Fork must finish HTTP acceptance before release; both Runs must execute successfully afterward. These object operations have bounded request or Run-local reconciliation deadlines, so those variants measure acceptance independence without turning the pause into a dependency-timeout test.

| File                                       | Variants | Independent operations and evidence                                                                                                                                                                                                                                                                                            |
| ------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `control/test_54_control_fork_commands.py` | 16       | Fork versus Continue, historical ContinueFrom, Steer, Interrupt, Feedback, waiting Continue and Retry; two distinct Forks sharing an Environment, with inherited and overridden execution settings. Each direction checks lineage, separate Thread identity, unchanged source state and isolated inbox/model context.          |
| `control/test_55_control_fork_recovery.py` | 20       | Fork versus model/tool/checkpoint I/O and completed/waiting/failed sealing; replacement Attempt state reads and writer claims before/after publication; planned Worker drain/handoff. Real Worker death must recover with the next writer fence, while drain must yield and hand off once; both retain the actual tool effect. |
| `control/test_56_control_fork_queue.py`    | 6        | Fork versus explicit queue consumption and completion handoff, including a shared Environment. Both queued entries must drain in FIFO order with the correct parent, while the Fork retains a separate empty queue and source Thread state remains unchanged while paused.                                                     |

Run these suites independently with:

```sh
make live-test-round-two LIVE_TEST_ARGS='-k "test_54_control_fork_commands or test_55_control_fork_recovery or test_56_control_fork_queue" --log-disable=httpx2'
```

Forks use a completed historical source while its Thread advances. Independent Workers distinguish operation-level blocking from a busy single Worker. The additional hooks pause before Fork initial-state publication, around replacement writer claims, before planned yield, and after queue-handoff verification; none holds a SQL transaction. The handoff pause is outside the bounded preparation and commit calls, so those timeouts cannot silently release the peer. Explicit queue-consumer isolation pauses background recovery until the explicit first consumption commits, whereas completion-handoff cases use the real Worker path. These cases check progress at named boundaries, not latency guarantees under arbitrary resource exhaustion.

Queue and planned-yield independence cases start Workers with a 60-second lease so a deliberately paused Run-local gate leaves time for peer execution. This uses normal Worker settings; the crash-recovery cases retain the short lab lease and the production state-admission request bounds.

Each transition uses public HTTP commands and independent real Control/Worker processes. Barriers surround first claim, initial state publication, acceptance commit, model requests, checkpoints, and queue handoff. Tests observe the barrier before issuing the competing command; they do not assume a sleep is a transaction boundary. Assertions combine HTTP receipts/conflicts with Run and Thread versions, lineage, Attempt ownership, queue/inbox records, checkpoint receipts, captured model inputs, and actual tool effects. Negative cases verify that rejected commands leave the owned state unchanged.

Only this lab lowers constructor-owned inbox and queued-submission limits to make exact capacity boundaries practical. Queue scan observations run after the real scan returns, including empty scans; waiting and FIFO-blocker assertions do not disable recovery. Prepared queue candidates identify their HTTP or background consumer through callback-local context, without replacing either production path. No public limit setter or lifecycle-record mutation is invented. Child barriers can release each child separately to prove mixed FIFO order. Offline checks exercise repeated model batch requests, complete feedback advancement, and fault isolation; they do not count as live validation.

The current client-tool surface accepts arbitrary JSON feedback, including explicit null, and exposes neither a result schema nor a caller-selected expiration. The matrix therefore tests missing results, wrong call/action, duplicate resolutions, stale digests and already-finalized feedback; it does not claim schema-validation or configurable-expiration coverage. Existing cases 14, 21, and 37–42 continue to own independent child cancellation, Environment inheritance, dependency failure, and persistence recovery coverage. These named orderings are a regression matrix, not an exhaustive interleaving proof or a guarantee of exactly-once external effects.

The Steer lifecycle cases reuse existing coverage where the same boundary is already exercised. The following map distinguishes process-level live tests from Harness integration tests:

| Boundary                                                                    | Owning test                                                                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Final model answer exists, but completed sealing has not committed          | `test_38_run_control_faults.py::test_steer_after_terminal_candidate_continues_same_run_before_sealing`                                                                                                                                                                                             |
| Waiting Steer and Feedback/waiting Continue overlap, in either commit order | `test_52_control_steer_races.py::test_waiting_steer_and_successor_commit_order_controls_binding`                                                                                                                                                                                                   |
| Steer and Interrupt/final failure overlap, in either commit order           | `test_52_control_steer_races.py::test_steer_admission_and_terminal_seal_preserve_the_winning_order`                                                                                                                                                                                                |
| Worker dies after incorporation, before checkpoint publication              | `test_38_run_control_faults.py::test_worker_loss_around_inbox_receipt_publication_preserves_single_consumption[checkpoint.before]`                                                                                                                                                                 |
| Worker dies after receipt publication, before SQL consumption               | The same test with `[checkpoint.after]`                                                                                                                                                                                                                                                            |
| Interrupt wins after receipt publication, before SQL consumption            | `test_38_run_control_faults.py::test_interrupt_wins_after_object_publication_without_consuming_stale_receipt[receipt]`                                                                                                                                                                             |
| SQL consumption wins before Interrupt                                       | `test_52_control_steer_races.py::test_interrupt_after_committed_consumption_preserves_receipt_and_status`                                                                                                                                                                                          |
| Compaction removes incorporated input before receipt publication            | `packages/a13n-service/tests/interactions/test_run_control.py::test_compaction_precedes_receipt_publication_without_losing_incorporation`: real Harness/compaction and object-store integration with recording relational ports; success, compaction failure, and internal model recovery variants |

The new admission barriers run after input preparation but before the final transaction, or after successful commit. The final-failure barrier precedes the real failure transaction, and the consumption barrier follows the real receipt-confirmation transaction. None pauses while holding a SQL transaction or fabricates Run/inbox state. Waiting-race assertions also verify the isolated first model request, direct-successor binding, and rejection of an old target after Thread advancement.

## Management integration: Service configuration to Harness execution

This round adds 45 live variants in 13 independently selectable files. Cases 28, 29 and 30 are intentionally excluded. Case 19 has its own [packaged plugin image opt-in](#packaged-plugin-image-journey). All management resources are created through public HTTP APIs, and each enabled test uses its own isolated lab with the same automatic RustFS setup and optional loopback S3 override as round two.

| File                                                     | Acceptance evidence                                                                                                                                                                                                                                          |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `harness_integration/test_17_agent_revisions.py`         | Update after Run acceptance but before Worker claim; current and explicit old revisions reach the model; per-Run overrides do not alter stored revisions; stale writes are rejected                                                                          |
| `harness_integration/test_18_plugin_execution.py`        | Installed factory selections produce a real file effect; an unbound Agent has no plugin tool; missing factories and invalid configuration fail Worker preparation before model/tool execution                                                                |
| `environment/test_20_environment_templates.py`           | Explicit old/current recipes read different files; `on_run` versus `on_use` is observed before tool execution; simultaneous first reads use one logical target/generation; unused lazy selection stays unprepared; explicit null overrides the Agent default |
| `environment/test_21_environment_lifecycle.py`           | Stop/reopen preserves backing generation; delete/rebuild advances it; stale process handles fail; Continue, Retry, Fork and Feedback inherit correctly; changing the Thread default does not change a historical Fork source                                 |
| `environment/test_22_environment_access.py`              | No environment, read-only, read-write and full access have the expected file/Shell surface; actual read/write/Shell bytes agree; forged unadvertised writes produce no file                                                                                  |
| `model/test_23_model_updates.py`                         | Accepted Model settings/upstream remain frozen; a new Run receives new settings; the next request of a running Agent uses rotated Provider credentials/new endpoint or is denied after disable                                                               |
| `harness_integration/test_24_skill_execution.py`         | Real ZIP document and attachment uploads; accepted latest version freezes before update; later current and explicit pinned bindings materialize/read correct files; cross-Workspace package reads and absent Environment are denied                          |
| `harness_integration/test_25_asset_execution.py`         | File bytes materialize into the Environment and PNG bytes enter the model request unchanged; deletion before Worker claim blocks delivery; explicit publication produces an immutable downloadable Asset linked to its Run, with cross-Workspace denial      |
| `harness_integration/test_26_output_and_client_tools.py` | Structured output advertises the authored schema and enforces JSON Schema validation and native retry bounds; external client tool waits and resumes with actual supplied data; malformed, duplicate and stale feedback cannot create extra successors       |
| `harness_integration/test_27_connectivity_execution.py`  | Production MCP client and Composio adapter connect to local HTTP peers; selected tools, arguments and credential hashes agree; disabling/revoking the connection prevents later dispatch                                                                     |
| `observability/test_31_observability.py`                 | Actual OTLP/HTTP exports correlate Service Attempt, Harness, model and tool spans; durable Items, SSE and model usage agree; rejecting trace exports with HTTP 503 does not change the result or repeat the effect                                           |
| `harness_integration/test_32_multiworker_resources.py`   | Three Workers retain accepted Agent/Model/Template selections while live resource disablement or connection revocation blocks later dispatch, including after Worker replacement                                                                             |
| `iam/test_33_multiworker_iam.py`                         | Native session/key revocation denies reads, downloads, commands, approval and SSE continuation; three replacement Workers reauthorize the persisted User; regrant does not resurrect revoked keys                                                            |

```sh
make image-sandbox
make live-test-management
make live-test-management LIVE_TEST_ARGS='-k revision'
make live-test-management LIVE_TEST_ARGS='-k asset'
make live-test-check
```

The two backing-lifecycle cases in case 21 use the Docker Provider and require the locally built `a13n-sandbox:local` image. Set `LIVE_TEST_SANDBOX_IMAGE` to use another locally built image; these cases never pull an implicit remote image. Their workspace is a fixture-owned bind directory, so its proof file survives backing-container deletion. This does not assert recovery of deleted ephemeral container files. Cleanup stops the lab's Workers and removes only containers labelled with that test's exact Environment ID. Other management cases use Direct Local, which does not support backing stop or deletion.

Use `--live-management` only for this round. The other entry points retain their own opt-ins. The lab also retains private model request observations, Composio/MCP dispatch evidence and OTLP exports beneath `.state/management/<random-id>/`. Ambient OpenTelemetry destinations are removed from lab subprocess environments; case 31 enables only its local receiver. Composio requires HTTPS, so the lab starts an owned TLS peer and appends its one-day certificate to a private CA bundle used only by lab subprocesses. It does not install a system trust root or disable certificate verification.

The deterministic model chooses tools and returns **observed tool results**. Expected Skill/file bytes never substitute for a missing tool result. MCP and Composio peers replace external services, while the Service adapters, management, authorization, queue, storage, Worker and Harness remain real. Support checks exercise the Composio peer through the production adapter and validate MCP and OTLP wire bodies without starting Service processes.

Missing product integration is an assertion failure when live tests are enabled, not an automatic skip or a fixture-installed capability. Environment/Skill file-tool and output-publication journeys exercise the Service's reconstructed `DynamicEnvironmentCapability` and `publish_asset` tool. Their availability in source does not establish a passing end-to-end result; run the enabled journeys against the configured lab to validate the complete path.

These tests do not exercise external model inference quality, hosted OAuth user interaction, Runner activation, plugin rollout compatibility or instance isolation, Secret bindings or IAM grant revocation. The client-tool stale-feedback check concerns a superseded sealed state, not a fabricated wall-clock expiry. Direct Local lifecycle checks concern its retained directory and process resources, not a remote VM provider. Trace checks use received OTLP evidence; they do not claim coverage of a hosted trace-query backend or UI.

## Five-backend Environment matrix

`environment/test_28_environment_backends.py` adds 15 separately selected journeys for `a13n.local-envd`, `a13n.docker`, `a13n.e2b`, `a13n.http-envd`, and `a13n.websocket-envd`. Each backend runs tools/access, template/preparation, and lifecycle/continuity journeys. The access journey checks read-only, read-write, and full ceilings, actual file/Shell results, and forged tool calls whose prohibited filesystem effects must remain absent. The existing case 22 continues to cover explicit no-environment selection.

| Backend        | Template and preparation                                               | Lifecycle                                                                                                          |
| -------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Local Envd     | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Fresh per-Run daemon scopes preserve the caller-owned workspace; stop/delete are rejected                          |
| Docker         | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Stop/resume retains generation; delete/rebuild advances it and preserves the fixture's bind directory              |
| E2B            | Pinned/current revisions, `on_run`/`on_use`, concurrent first use      | Stop/resume preserves files; delete/rebuild advances generation and removes ephemeral files                        |
| HTTP Envd      | Managed templates are rejected; explicit external registration is used | Fresh sessions preserve files; Service stop/delete are rejected without terminating the external daemon            |
| WebSocket Envd | Managed templates are rejected; explicit external registration is used | Fresh reverse attachments preserve files; Service stop/delete are rejected without terminating the external daemon |

```sh
cargo build --locked -p a13n-envd
make image-sandbox
make live-test-management LIVE_TEST_ARGS='--live-environments -k test_environment_backend'
# Select one backend, or supply a different exact-version native executable.
A13N_ENVD_TEST_BINARY=/absolute/path/to/a13n-envd \
  make live-test-management LIVE_TEST_ARGS='--live-environments -k "test_environment_backend and http-envd"'
```

The matrix requires the explicit `--live-environments` opt-in. Default checks do not start its processes or read private Provider configuration. E2B uses the existing optional `environment` section and skips when absent. The other backends require no external account. Envd defaults to the source-built `target/debug/a13n-envd`; Docker uses the locally built sandbox image above.

Every backend owns a disposable Service lab. Managed Docker/E2B targets are deleted before teardown. The HTTP and reverse WebSocket daemons are real native processes over fixture-owned workspaces. Their external-operator configuration disables native isolation; the separate Local Envd journey retains mandatory native isolation. Reverse WebSocket uses an explicit, authenticated test Host listener and a connection SDK injected into its single Worker. This verifies Service/Harness execution through the production Provider without claiming cross-Worker connection routing or a production WebSocket ingress deployment.

## Multi-worker resource changes and native IAM

Select the nine additional cases with:

```sh
make live-test-management LIVE_TEST_ARGS='-k multiworker'
```

Each case owns three one-slot Workers and requires no cloud credentials. Concurrent execution is established by gated model requests and Worker execution-start logs; stream-maintenance log mentions do not establish task ownership. Template recovery uses one shared Direct Local Environment because independent Environments cannot claim the same backing directory. Assertions check observed tool results, files, and remote dispatch counts as well as terminal Run state.

Case 33 adds a separate Control process using unmodified native IAM over the same owned lab storage. Only the initial administrator session is seeded; the test User joins through manual invitation acceptance and creates its Personal API Key through HTTP. The fixture's model endpoint remains on its original Control process. Native cookie validation and CSRF are exercised with explicit cookie transport over loopback HTTP; browser Secure-cookie transport and Console rendering are outside this case. Stream revocation follows the standard 30-second authorization interval. All clients, processes and containers close with the lab, and generated credentials remain in private files under its ignored `.state/management/<random-id>/` directory.

## E2B lifecycle and Sandbox SDK coverage

The opt-in E2B suite adds 28 cases alongside the five-backend matrix. It uses our native E2B adapter and the installed official `e2b` SDK. Lifecycle effects use the E2B cloud API; command, process and file operations use E2B's own envd through that SDK. These tests do not use the `a13n-envd` daemon.

| Test file                                      | Cases | Observable contract                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `environment/test_32_e2b_lifecycle.py`         |    13 | Inert entry/close; concurrent and repeated prepare; exact/idempotent destroy; real TTL extension without shortening and execution past the old expiry; pause/resume of an in-memory process value; passive paused-state observation; stale facets after close; reconnect with retained output offsets; managed rebuild versus external missing target; metadata recovery and conflicts; native timeout deletion |
| `environment/test_33_e2b_faults.py`            |     9 | Lost create response recovered by metadata; known state retained after readiness failure; lost pause/kill/set-timeout responses reconciled without repeating effects; prepare/recover serialized with close, including cancellation                                                                                                                                                                             |
| `environment/test_34_e2b_service_lifecycle.py` |     6 | Worker renewal during an active Run; automatic idle pause/delete without waking; two concurrent first-use Runs sharing one target; Worker killed after create before state publication; stop/delete racing with new use across publication                                                                                                                                                                      |

Run the complete suite or one layer:

```sh
make live-test-management LIVE_TEST_ARGS='--live-environments -k test_e2b'
# Direct adapters and fault injection need an E2B account but no Docker lab.
uv run --locked python -m pytest dev/live_tests/environment/test_32_e2b_lifecycle.py \
  dev/live_tests/environment/test_33_e2b_faults.py --live-environments
# Service cases additionally start disposable PostgreSQL, Redis, Control and Worker.
uv run --locked python -m pytest dev/live_tests/environment/test_34_e2b_service_lifecycle.py --live-environments
```

Use the private `[environment]` configuration described above. Without `--live-environments`, these cases skip before reading credentials or starting infrastructure. An absent environment section also skips them. Service Runs use the deterministic local model; no paid model account is required.

Independent SDK `get_info`/metadata-list observations check native sandbox IDs, state and expiry. They do not connect or resume a sandbox. Read-only `get_info` probes retry transport failures at most twice with warnings; adapter actions and assertions are not retried by the fixture. Logs include sandbox IDs, expiry changes and cleanup outcomes without credentials.

Fault cases deliberately inject local response loss or a readiness failure after real cloud work. The opt-in test Host also provides an authenticated, Workspace-scoped lifecycle evidence route and one-shot barriers after real create/pause/kill effects but before Service publication. The crash case kills the actual Worker and waits for its successor to reconcile the abandoned operation. The E2B and Docker lifecycle labs explicitly install these shared evidence and publication-barrier hooks.

Cleanup tracks test-owned Environment identities and exact sandbox IDs, discovers targets whose create result was lost (including paused targets and multiple discovery pages), attempts all owned deletions, and verifies cloud absence. Cleanup errors fail the test. `infrastructure_tests/test_e2b_support.py` checks opt-in, ownership, retry bounds and cleanup failure behavior offline.

This covers the lifecycle SDK surface used by our adapter: `list`, `get_info`, `create`, `connect`, `is_running`, `pause`, `set_timeout` and `kill`. It does not claim coverage of every E2B SDK feature, template, region or account quota. Deterministic credential denial, malformed state and policy/error mapping remain covered in `packages/a13n-environment/tests/test_e2b_lifecycle.py`; cloud outages, rate limits and permission failures are not induced against the real account.

## Worker races and long-session latency

Run the process races independently:

```sh
make live-test-round-two LIVE_TEST_ARGS='-k "worker_replacement or four_workers or respect_capacity" --log-disable=httpx2'
```

Four ready Worker processes are paused before accepting one Run, then released together. The test requires exactly one successful Attempt and exactly one non-idempotent tool effect. Recovery tests pause one or three ready replacement Workers, kill or suspend the current owner after a complete checkpoint, wait a full lease duration, and release all replacements together. Exactly two Attempts must exist: the failed predecessor and one successful successor. Resuming the stale owner must not alter the seal or repeat the checkpointed tool effect. These are real scheduling races, not a deterministic assertion that every process reaches the same SQL statement simultaneously. They do not establish exactly-once effects for work outside a complete checkpoint.

Every recovery variant, the four-Worker claim race, and the two-Worker capacity case run 100 times by default (600 cases total). Each numbered pytest case owns a fresh lab, Worker processes, and Runs, with independent cleanup and retained process logs. Test IDs use `round-001` through `round-100` to identify failures; no separate repetition flag or stress mode is required.

`performance/test_36_long_session.py` measures one Thread accumulating real sequential Runs through separate Control/Worker processes, PostgreSQL, Redis and S3. It runs only with `--live-performance`. The lab uses the default 30-second Worker lease.

```sh
# Grow one Thread to 1, 1,000 and 10,000 real Runs, measuring at each checkpoint.
# Each real input/output has 1 KiB padding; five samples follow one warmup.
make live-test-performance

# Short validation including several context compactions.
make live-test-performance LIVE_TEST_ARGS='--session-runs=1,100 --session-samples=2'

# More acceptance samples at the same depths.
make live-test-performance LIVE_TEST_ARGS='--session-runs=1,1000,10000 --session-message-bytes=1024 --session-samples=20'
```

No synthetic history or fabricated Run rows are inserted. Every continuation uses the preceding sealed Run, and every checkpoint is written by production code. At each requested depth the test forks independent branches for operation measurements, preserving the main Thread for further sequential growth. The complete paginated Run listing must match the actual chain depth; pages contain 200 entries. This replaces the earlier synthetic-message benchmark and its `--session-turns` option.

The opt-in Worker selects the real `CompactionCapability` in the reconstructed Agent definition. The deterministic model estimates tokens as request JSON UTF-8 bytes divided by four, including tools, plus output bytes divided by four. It rejects requests exceeding the 32,768-token fixture context window and answers actual Harness compaction requests with a bounded summary. The built-in 90% threshold decides when to compact. This validates compaction persistence and storage scaling, not tokenizer accuracy or natural-language summarization quality. The model emits the padded answer immediately; paid-model inference and artificial per-chunk delays are excluded.

Every sequential Run must complete, preserve parent/Thread lineage, return the expected output and retain a memory introduced only in the first Run. A read-only probe validates its real persisted state and sequence. Compaction must reduce object size and preserve the memory in the saved summary. The test checks fork, continue, retry, queue and steer behavior, including durable consumption and source immutability. These probes are outside operation latency measurements. Successful Runs with multiple Attempts are recorded and logged as recoveries; a passing journey does not imply recovery-free execution.

Each lab retains process logs, `progress.json` (workload completion status), `trajectory.jsonl` (one persisted state size, message count, compaction count, Attempt count and completion latency per sequential Run), and `latency-<runs>.json` under `.state/round-two/<random-id>/`. Reports retain raw samples, median, nearest-rank p95, maximum, workload and environment settings. An unfinished report remains `incomplete`. Pytest also reports fixture teardown errors separately after the workload has completed. Small sample counts do not establish stable tail latency. State sizes describe each Run's latest checkpoint; summing sequential checkpoint sizes gives their retained state-body storage, excluding Item objects and other database/object-store overhead.

| Metric                                                   | Measured boundary                                                                             |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `pg_run_read`                                            | A short scoped SQL query, ORM-to-domain conversion and session close                          |
| `s3_read_bytes`                                          | Open and fully consume the exact state object, including body assembly                        |
| `state_decode`                                           | Decode canonical state and validate its schema in memory                                      |
| `s3_read_validate`                                       | Production state read, integrity checks and relational seal validation; includes S3 I/O again |
| `run_get`, `runs_first_page`, `runs_all_pages`           | Public HTTP reads including authentication and response decoding                              |
| `fork_accept`, `continue_accept`, `retry_accept`         | POST through acceptance response; prerequisite Thread version reads excluded for all three    |
| `fork_complete`, `continue_complete`, `retry_complete`   | Full client operation through observed terminal completion, including version reads           |
| `queue_accept`, `steer_accept`                           | Public admission response while a model response is held at a barrier                         |
| `steer_release_to_consumed`                              | Barrier release through observed durable steer consumption                                    |
| `queue_release_to_consumed`, `queue_release_to_complete` | Barrier release through observed queue consumption or successor completion                    |

Chain creation, evidence probes, failure injection/repair and deliberate barrier waits are outside acceptance samples. Completion uses 100 ms polling and includes scheduling, HTTP and fixture overhead. Storage samples reuse warmed live clients and caches. This measures local storage/execution scaling, not remote S3 latency, real-model inference, network faults under load, or a production capacity limit. Run the performance matrix alone when comparing numbers; concurrent suites distort them. Ten thousand sequential Runs can take hours.

### E2B file boundaries and OS failures

`environment/test_35_e2b_files.py` adds 26 real-sandbox cases, selected with the same `--live-environments` opt-in and private E2B configuration. No Service lab or model account is required:

```sh
uv run --locked python -m pytest dev/live_tests/environment/test_35_e2b_files.py --live-environments
```

The cases cover:

- malformed/traversing paths and symlinks that leave the configured root, across byte/text/stream reads and mutation destinations; moving/removing the symlink entry itself must preserve its outside target;
- a fresh `read_only=True` adapter over the same sandbox rejecting file writes, copy, move, remove, mkdir and patch, without consuming a streamed upload;
- missing-file errors for stat, list, byte/text/stream reads, remove, move, copy, replace and patch, plus successful append to a new file;
- file/directory type mismatches, preserving both source and destination;
- real filesystem permissions, with root-owned fixtures and operations executed as the configured non-root user, including native SDK uploads/downloads;
- real ENOSPC on a dedicated 1 MiB tmpfs inside the owned sandbox: failed create, replace and append preserve existing content and leave no staging files; freeing capacity permits a later write.

Independent native snapshots include file contents, modes and hidden staging entries without following symlinks. Privileged setup is confined to the owned sandbox; permission and tmpfs tests require the standard base template's root command access and mount capability. The full sandbox disk is never filled. Tmpfs is unmounted in a `finally` block, and the existing fixture independently verifies destruction of the exact sandbox.

Guest-helper missing, denied and wrong-type failures assert their specific `environment_*` codes. Permission checks must reject denied transfers before SDK dispatch: SDK file I/O alone does not enforce the configured user's POSIX permissions. SDK upload ENOSPC retains a bounded `provider_unknown_outcome` error; tests verify the native SDK exception, OS errno and unchanged files rather than inferring OS errno from that generic public code. These file-API boundary checks do not claim shell confinement or protection against a hostile process racing symlink changes.

### File and lifecycle boundaries across environment providers

`environment/test_42_environment_files.py` runs 25 file cases against each of Direct Local, Local Envd, HTTP Envd, reverse-WebSocket Envd and Docker (125 cases). It covers traversal, outside-root symlinks, read-only mutations, missing paths, file/directory mismatches, real POSIX permissions, writable capabilities and aborted create/replace/append streams. Independent native snapshots include hidden staging files and outside-root sentinels.

Expectations follow each provider's semantics:

- Append requires an existing file for these five providers. The E2B suite separately verifies its append-to-new-file behavior.
- EIP replacement of a symlink destination replaces the link entry and preserves its outside target; Direct Local rejects that destination.
- Read-only EIP mutations are unadvertised and return unsupported; Direct Local returns denied. Both reject before consuming streamed input.
- Wrong-type errors use each provider's defined error family, while every case checks that neither source nor destination was changed.

`environment/test_44_environment_lifecycle.py` adds 21 real-backend cases: closed file facets, fresh scopes, close racing successful/cancelled preparation, Docker stop/start, metadata recovery without waking a target, managed versus external target loss, and lost create/stop/delete responses reconciled against the real Docker engine. Direct Local and Local Envd start a fresh generation over the retained workspace; closing a remote adapter leaves its external daemon alive. Docker stop/start does not assert E2B's process-memory preservation. E2B-specific TTL renewal remains in the dedicated cloud suite.

`environment/test_50_local_docker_lifecycle.py` adds 23 native lifecycle cases:

| Provider     | Cases | Observable contract                                                                                                                                                                                                                                                                                                                                            |
| ------------ | ----: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Direct Local |     4 | Inert entry and unused close; one preparation under concurrent callers; missing roots are not created; root replacement changes identity; close terminates a live parent/child process tree and fences retained output                                                                                                                                         |
| Local Envd   |     8 | The same local boundaries; cancellation/error after actual daemon launch cleans private resources; externally killed idle or active daemon cleans its child tree, fences process/output references and permits a fresh generation                                                                                                                              |
| Docker       |    11 | Inert and concurrent preparation; failed/cancelled readiness preserves the exact created target; forged container identity and ambiguous real metadata cannot redirect ownership; repeated destruction through fresh adapters preserves bind data and other containers; close/rebind preserves process/stdin/output while stop/start changes native generation |

`environment/test_51_docker_service_lifecycle.py` adds five Service cases over real Docker, PostgreSQL, Redis, Control and Worker processes: active-use protection followed by idle stop/delete without waking or resetting the idle clock; concurrent access to an exclusively admitted EIP Session; Worker death after native creation but before state publication; and stop/delete publication racing new Run use. The successor Worker must recover the same unpublished container and backing generation. A stopped container is reused; a deleted one is replaced with one new backing generation.

E2B permits concurrent SDK attachments; Docker HTTP EIP admits one Session. Docker contention must preserve the current owner and unique container, report unavailability to the competing tool call, and allow reuse after owner close. It does not assert that two independent Runs can simultaneously operate one Docker daemon. Shared retention and crash scenarios live in `environment/lifecycle_cases.py` with native identity/state observations supplied by each provider fixture.

Local Envd close after an external SIGKILL reports `provider_cleanup_failed` when clean EIP closure cannot be confirmed; the test independently checks process exit, private-directory removal, workspace preservation and successful fresh preparation. Closed EIP process and port facets must expose public Environment errors rather than leaking client Session exceptions.

Build the current native daemon and sandbox image, then opt in:

```sh
make rust-build
make image-sandbox SANDBOX_IMAGE=a13n-sandbox:file-tests
LIVE_TEST_SANDBOX_IMAGE=a13n-sandbox:file-tests uv run --locked python -m pytest \
  dev/live_tests/environment/test_42_environment_files.py \
  dev/live_tests/environment/test_44_environment_lifecycle.py \
  dev/live_tests/environment/test_50_local_docker_lifecycle.py \
  dev/live_tests/environment/test_51_docker_service_lifecycle.py --live-environments
```

`A13N_ENVD_TEST_BINARY` can select a daemon binary instead of `target/debug/a13n-envd`. Tests own temporary workspaces, daemon processes and uniquely labelled Docker targets; cleanup verifies that owned containers are absent. Permission cases require POSIX and non-root execution. Network file-only fixtures use authenticated loopback connections with command execution disabled; they do not assert shell isolation or resistance to hostile concurrent namespace changes.

`environment/test_43_environment_storage.py` exercises real kernel ENOSPC for Direct Local, Local Envd, HTTP and reverse-WebSocket transfers. Each case uses an owned non-root Linux container with a 1 MiB tmpfs, no network, bounded memory and process count. It verifies failed create/replace/append publication, original content, hidden-stage cleanup and successful writing after capacity is freed. No host filesystem is filled; the four cases require Docker and this separate fixture image:

```sh
docker build -f dev/live_tests/environment/file_resources.Dockerfile \
  --build-arg SANDBOX_IMAGE=a13n-sandbox:file-tests \
  -t a13n-file-resources:local .
uv run --locked python -m pytest dev/live_tests/environment/test_43_environment_storage.py --live-environments
```

`LIVE_TEST_FILE_RESOURCE_IMAGE` overrides the fixture image. The container logs emit JSON evidence with capacity, kernel errno, provider errors, preservation and recovery results. All four retain a usable session after the failed transfer; the test explicitly submits a new write after releasing capacity. HTTP transfer responses carry no acknowledged byte offset, so a failed HTTP response must not be converted into a synthetic acknowledged offset or a successful commit.

`environment/test_52_remote_envd_failures.py` adds six real HTTP/reverse-WebSocket cases: clean close/rebind retains a native process, stdin and byte-offset output; SIGKILL/restart fences old process and output identities while retaining files; and cutting an owned TCP proxy after a command changes native state returns an error without replaying that command. Reverse WebSocket reconnects with the same daemon generation and permits process rebind. An abandoned HTTP Session remains exclusively admitted, so a fresh adapter must reject takeover until the external operator restarts the daemon.

`environment/test_53_docker_boundaries.py` adds 21 real Docker cases. Missing bootstrap directories/credentials and corrupt manifests/configuration are exercised against prepare, reconcile, stop and destroy. Each rejects the operation without changing the exact running target, then recovers after the fixture restores its material. Four Engine transport-loss cases distinguish unknown reachability from absence. The final case destroys and recreates a container over the same external named volume and independently verifies its retained contents. Docker Desktop bind inspection translates its VM `/host_mnt` prefix back to the macOS Host path; volume identity remains the exact external volume name.

`environment/test_54_docker_storage.py` adds full Docker Provider ENOSPC over a fixture-owned 1 MiB tmpfs named volume. Its create/replace/append failures, native errno 28, unchanged file hashes, staging cleanup and successful retry are checked through the production Provider plus independent native evidence. Build its image target and run both storage suites:

```sh
docker build -f dev/live_tests/environment/file_resources.Dockerfile \
  --build-arg SANDBOX_IMAGE=a13n-sandbox:file-tests \
  --target docker-sandbox -t a13n-file-resources:docker .
uv run --locked python -m pytest dev/live_tests/environment/test_43_environment_storage.py \
  dev/live_tests/environment/test_54_docker_storage.py --live-environments
```

`LIVE_TEST_DOCKER_RESOURCE_IMAGE` overrides the Docker Provider fixture image. The Local Envd storage container permits nested user/PID namespaces and proc mounts with fixture-only unconfined seccomp/AppArmor and empty masked/read-only system path lists. The native Local Envd isolation requirement remains enabled.

`environment/test_55_remote_envd_service_failures.py` runs four corresponding remote faults through real Control, Worker, PostgreSQL, Redis and Harness tool calls. It cuts the carrier or kills the external daemon only after observing the command's native effect, checks a typed tool error and failed Run when cleanup cannot be confirmed, then verifies a fresh Run against the same registered Environment. The side effect occurs once, workspace files remain, and Service does not stop an externally owned daemon. Reverse WebSocket recovery waits for an actual new carrier after bounded daemon backoff.

```sh
LIVE_TEST_SANDBOX_IMAGE=a13n-sandbox:file-tests uv run --locked python -m pytest \
  dev/live_tests/environment/test_52_remote_envd_failures.py \
  dev/live_tests/environment/test_53_docker_boundaries.py \
  dev/live_tests/environment/test_55_remote_envd_service_failures.py --live-environments
```

### Multiple Workers sharing one Environment

Cases 56–60 start two independent Worker processes and verify their distinct persisted Worker IDs for each assigned Run. PostgreSQL, Redis, object storage, Harness execution and native targets remain real. Lifecycle barriers pause outside database transactions; authenticated observation routes only read fixture-owned records. Crashes use process-group SIGKILL, and resurrection cases use SIGSTOP/SIGCONT across an actual lease expiry.

| Suite                                                    | Applicable cases | Coverage                                                                                                                                                                                                                                     |
| -------------------------------------------------------- | ---------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `environment/test_56_environment_worker_sharing.py`      |               26 | Sequential reuse, both preparation modes racing for first use, concurrent scopes versus exclusive Sessions, cancellation/crash isolation, six consecutive handoffs                                                                           |
| `environment/test_57_environment_worker_lifecycle.py`    |               21 | Two maintainers racing with new use; create-before-effect, native-created and committed crash boundaries; stale publication and stale stop dispatch; renewal beyond native expiry; simultaneous resume/rebuild; stopped-target capacity      |
| `environment/test_58_environment_worker_policy.py`       |                8 | Shared active capacity, last-slot admission, aggregate approval waiting and idle time, Provider disable, access ceilings on previously writable Workers, Host affinity, Run-local process/stdin/output references, unrelated-target progress |
| `environment/test_59_environment_worker_dependencies.py` |                3 | One-Worker and simultaneous database partitions without native replay, then current HTTP Envd credential rotation on both Workers                                                                                                            |
| `environment/test_60_environment_worker_authority.py`    |                4 | Current service-account status/role, historical Environment selection after Thread-default changes, concurrent native atomic renames                                                                                                         |

The sharing matrix covers Direct Local, Local Envd, Docker, E2B, HTTP Envd and reverse-WebSocket Envd. Ten combinations are inapplicable: externally registered daemons have no managed first-create operation, and single-Session providers cannot exercise two concurrently admitted users. Docker additionally has no native TTL renewal. These eleven combinations are explicitly skipped; missing E2B configuration produces separate, explicit skips.

Docker and HTTP Envd contenders must respect exclusive Session ownership. Reverse-WebSocket rendezvous is process-local: the wrong Worker must fail without stealing the connection, and the owning Worker remains usable. Host-affinity coverage supplies a different Host identity to one owned process; it does not pretend to run on a second physical machine. Access tests use the public frozen Environment ceiling and current IAM grants; no unsupported per-request access override is added. Shared file tests assert native atomic rename outcomes, not application-level transaction isolation. Six handoff cycles are a bounded resource-release regression, not an indefinite soak test.

The stale-stop regression reproduces an owner resuming after another Worker has stopped and resumed the target for a new active Run. Execution must reject that obsolete or expired lease before dispatch, as well as fence stale publication. This does not turn database fencing into cancellation of an external call that was already dispatched.

Build the Rust daemon and sandbox image as above, configure E2B in the private provider file, then run:

```sh
LIVE_TEST_SANDBOX_IMAGE=a13n-sandbox:file-tests uv run --locked python -m pytest \
  dev/live_tests/environment/test_56_environment_worker_sharing.py \
  dev/live_tests/environment/test_57_environment_worker_lifecycle.py \
  dev/live_tests/environment/test_58_environment_worker_policy.py \
  dev/live_tests/environment/test_59_environment_worker_dependencies.py \
  dev/live_tests/environment/test_60_environment_worker_authority.py --live-environments
```

Use `-k docker`, `-k e2b`, or another provider name to select matrix cases. Dependency and authority suites have provider-independent names and should be run explicitly when using those filters. Exact owned labels/metadata drive Docker/E2B cleanup, including unpublished native targets; no shared infrastructure or unrelated target is stopped.

## Native SSE and Hosted AG-UI protocol contracts

`protocol/test_04_protocol_streams.py` consumes real Native and Hosted HTTP SSE with the scripted OpenAI-compatible model, separate Control/Worker processes, and real storage. It covers Unicode/newline text deltas, two tool calls and their results, model failure, explicit cancellation, disconnect/reconnect, approval/rejection, waiting feedback under a new external Run ID, idempotent replay, and invalid input/conflicting reuse without extra accepted Runs.

The consumer oracle in `protocol/stream_contract.py` imports the pinned upstream `ag_ui.core.Event` schema, never the Service event model, observer, serializer, projector, or visibility registry. Its assertions derive from:

- [AG-UI event semantics](https://docs.ag-ui.com/concepts/events) and the upstream schema version pinned by the Harness release group;
- [Native Streaming](../../spec/a13n-service/21-native-streaming-and-notifications.md): SSE framing, heartbeat checkpoints, and exclusive cursor replay;
- [Hosted AG-UI](../../spec/a13n-service/22-hosted-ag-ui.md): external identities, durable lifecycle, waiting, recovery, and default visibility;
- [Lifecycle and Stream Persistence](../../spec/a13n-service/24-lifecycle-and-stream-persistence.md): required versioned envelopes, provenance, and ordered recovery boundaries;
- [Stream Protocol](../../spec/a13n-stream-protocol/00-overview.md): explicit text/tool lifecycles and model-only input visibility.

`infrastructure_tests/test_stream_contract.py` uses hand-authored wire examples and deliberate corruptions to prove that missing fields, unsupported versions, malformed AG-UI payloads, broken message/tool order, identity changes, duplicate terminals, private execution data, and replay gaps cannot pass the oracle. Additive Native fields remain valid; Hosted cursors are opaque and never parsed as Redis IDs.

`protocol/test_10_protocol_recovery.py` kills an owned Worker after a real tool checkpoint, then verifies the replacement boundary in both protocols, exclusive replay on either side, stable source event identity, one external lifecycle, and no repeated tool effect. The model is mocked; the process failure and recovery are real.

```bash
make live-test-local LIVE_TEST_ARGS='-k "event_contracts or hosted"'
make live-test-round-two LIVE_TEST_ARGS='-k test_hosted_and_native_recovery_boundary --log-disable=httpx2'
make live-test-check
```

For an existing manually managed installation, rerun `make live-test-setup` to provision the protocol fixture Agent and restart Control/Worker to load the new model scenarios. The isolated target provisions these automatically.

These are contracts for the selected Service profile, not a claim of exhaustive upstream AG-UI coverage. Optional state, activity, message-snapshot, subagent, and reasoning-summary visibility profiles, client-executed tools, and structured user-input feedback need their own scenario matrices. Native reasoning fields are schema-checked and the mock emits a private reasoning sentinel that must not appear in the default Hosted stream. A waiting attachment follows the Service custom-event contract rather than assuming every attachment ends in AG-UI success/error. EOF alone never establishes a Run outcome.
