# Installed Web Provider example

This independent package depends on Harness and Pydantic. It exports an immutable `ProviderManifest` through the `a13n.providers` entry-point group. Installation makes it discoverable; a host must explicitly select `acme` to load it. Importing its definition does not import Service, Agent orchestration, or vendor SDKs.

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
