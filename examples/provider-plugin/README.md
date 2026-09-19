# Installed Model, Web and Memory Provider example

This independent package depends on Harness, Pydantic, and the native SDK used by its Model implementation. It exports an immutable `ProviderManifest` through the `a13n_harness.providers.plugins` entry-point group. Installation makes it discoverable; a host must explicitly select `acme` to load it. Importing its definition does not import Service, Agent orchestration, or vendor SDKs.

A Web author supplies two input models, an async operation, and a `WebProviderDefinition`. There is no forwarding backend, factory, or empty cleanup method. The same definition works directly with a transport for the vendor:

```python
from acme_provider.plugin import acme_web
from a13n_harness.providers.web import WebSearchRequest

async with acme_web.open({"index": "guides"}, {"token": "example-token"}, transport=transport) as web:
    result = await web.search(WebSearchRequest(query="plugins", limit=2))
```

Acme is fictional. Tests supply a `WebProviderTransport` backed by `httpx2.MockTransport`, validating the outgoing index and bearer credential without a vendor account. Real integrations replace the fictional endpoint and use the default bounded transport, whose HTTP clients are owned and closed per exchange. An explicitly supplied HTTP client remains caller-owned. Operations that acquire other resources own their cleanup inside the callback.

Service selects the same installed manifest at application setup:

```toml
[provider_plugins]
enabled = ["acme"]
```

Create a Web account with type `acme_web`, configuration `{"index": "guides"}`, and credential `{"token": "example-token"}`. Console renders these fields from the definition's schemas. Service owns encrypted persistence, scope authorization, ETags, enabled state, fresh credential acquisition, and disclosure checks. Neither configuration nor an installed plugin grants runtime authority.

`WebProviderResponseError` is reserved for an explicit retry-safe upstream response. Timeouts, transport loss, and unknown effects must not be converted to this error to obtain a retry.

Run the installed entry-point tests:

```bash
uv sync --project examples/provider-plugin --locked
uv run --project examples/provider-plugin --locked pytest examples/provider-plugin/tests
```

## Model contribution

The same manifest exports `acme_model`. Its configuration contains `index`; credentials contain a nested `authorization.token` secret and integer `revision`. Its callback constructs a native OpenAI Provider. The definition validates inputs, chooses the declared native Chat Completions API, and returns a native Pydantic AI Model:

```python
import httpx2
from acme_provider.plugin import acme_model

async with httpx2.AsyncClient() as client:
    model = await acme_model.build(
        "acme-small",
        configuration={"index": "guides"},
        credential={"authorization": {"token": "example-token"}, "revision": 1},
        http_client=client,
    )
    async with model:
        # Pass model directly to an Agent or HarnessBuilder.
        ...
```

Create a Service Model Provider with type `acme_model` and the same objects, then a Model referencing that account. No Service subclass, IDs in provider inputs, or duplicate metadata are needed. Service HTTP tests install this package, encrypt the nested credential, call the native Model against fixture HTTP, rotate the saved values, and verify that disabling the account prevents further calls.

## Memory

The same manifest contributes `acme_memory`, with nested secret and integer inputs, configuration-dependent Authentication, setup help and document support. Direct use needs no Service identity or Agent:

```python
from acme_provider.plugin import acme_memory
from a13n_harness.providers.memory.contracts import MemoryScope, MemorySubject

async with acme_memory.open(
    {"collection": "facts"},
    {"authorization": {"token": "example-token"}, "revision": 1},
) as backend:
    result = await backend.search("preferences", subjects=(MemorySubject(MemoryScope.USER, "trusted-user"),), limit=5)
```

The fictional endpoint speaks the Mem0 OSS protocol, so this implementation reuses the OSS adapter without importing the Platform SDK. Tests replace only HTTP and retain native subject/result validation. `access: "public"` forbids credentials; `"optional"` accepts their absence. Hosted credential removal is separate from runtime eligibility: removing a required credential succeeds but later content calls fail until it is restored.

## Connector use without Service

The same manifest includes `acme_connector`, a fictional CRM adapter. Its credential schema has a nested secret token, numeric revision and a sandbox/live choice; configuration selects a region and required/optional/forbidden authentication. Service persists these typed values as an encrypted bundle and supplies current-account guards at dispatch.

```python
from a13n_harness.providers.plugins import load_provider_plugins
from a13n_harness.providers.connector.contracts import ConnectionBinding, SetupContext

connector = load_provider_plugins(("acme",))[0].manifest.connector[0]
async with connector.open(
    {"region": "eu"},
    {"authorization": {"token": "your-key"}, "revision": 1, "tier": "sandbox"},
) as provider:
    await provider.test()
    apps = await provider.discover_connectors()
    context = SetupContext(connector_key="crm", external_user_correlation="your-user")
    started = await provider.start_setup(setup={}, context=context)
    account = await provider.inspect_setup(setup_ref=started.setup_ref, context=context)
    connection = provider.connect(
        ConnectionBinding(
            connector_key="crm",
            external_ref=account.external_ref,
            external_user_correlation=context.external_user_correlation,
        )
    )
    tools = await connection.discover_tools(cursor=None)
    result = await connection.execute_tool(
        tool_key="lookup",
        provider_version=tools.provider_version,
        arguments={},
        request_id="your-operation-id",
    )
    # Remote revoke is explicit, never a side effect of leaving the context.
    await connection.revoke(operation_id="your-revoke-id")
```

The URLs are illustrative; tests supply a bounded fixture transport. `open` owns its client unless the host supplies `http`. A borrowed client remains open. Custom provider contexts own any additional resources and must implement bounded cancellation-safe teardown within their own structured scopes.
