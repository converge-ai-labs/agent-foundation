# a13n

Python SDK package for a13n Service.

## Status

This SDK implements Search Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

Other Service operations are not implemented yet. `AgentConfig` and `AgentRunOverride` type the search selection while preserving other Service-owned configuration fields; they are not complete local validators for Agent configuration. Source compatibility is with the repository's current `/api/v1` Search Provider contract.

## Installation

```bash
uv add a13n
```

```python
import a13n

print(a13n.__version__)
```

## Search accounts

```python
from a13n import Client, SearchScope, AgentRunOverride, SearchSelection


async def accounts(base_url, token, workspace_id):
    async with Client(base_url, token) as client:
        page = await client.search_providers(SearchScope("workspace", workspace_id))
        return page.items


inherit = AgentRunOverride().to_wire()  # {}
disable = AgentRunOverride(search=None).to_wire()  # {"search": None}
replace = AgentRunOverride(search=SearchSelection(provider_id="sprov_example")).to_wire()
```

Use `CreateSearchProviderRequest` / `UpdateSearchProviderRequest` and `pydantic.SecretStr` for write-only credential input. Ordinary model diagnostics redact the key; the client reveals it only while serializing an authorized request. `test_search_provider` sends one quota-consuming probe only when called. Use `aclose()` or an async context manager to release the transport.

## Development

Run the repository-wide Python checks from the repository root:

```bash
make sdk-python-check
```

## License

Licensed under the Apache License 2.0.
