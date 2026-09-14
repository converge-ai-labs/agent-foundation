# a13n

Python SDK package for a13n Service.

## Status

This SDK implements Web Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

The generated low-level API covers every ordinary Native `/api/v1` HTTP operation in the shared Service OpenAPI contract. The Web facade exposes typed `AgentConfig.toolsets` and `AgentRunOverride.toolsets` wrappers, while complete request/resource models live in `generated`. Generated HTTP bindings do not implement Run SSE or notification WebSocket recovery.

## Installation

```bash
uv add a13n
```

```python
import a13n

print(a13n.__version__)
```

## Web Provider accounts

Bind API Key operations with `await client.workspace()`. This reads `/api/v1/auth/context` once and returns search operations without a Workspace argument. The binding uses the immutable Workspace ID and shares the parent transport and shutdown. The parent client retains explicit `WebProviderScope` operations; Service always enforces the credential boundary.

```python
from a13n import AgentRunOverride, Client, SearchToolConfiguration, ToolSelection, ToolsetSelection


async def accounts(base_url, token):
    async with Client(base_url, token) as client:
        workspace = await client.workspace()
        page = await workspace.web_providers()
        return page.items


inherit = AgentRunOverride().to_wire()  # {}
disable = AgentRunOverride(toolsets={"web": ToolsetSelection(enabled=False)}).to_wire()
replace = AgentRunOverride(
    toolsets={
        "web": ToolsetSelection(
            enabled=True,
            tools={
                "search": ToolSelection(
                    permission="inherit",
                    config=SearchToolConfiguration(provider_id="wprov_example").model_dump(exclude_none=True),
                )
            },
        )
    }
).to_wire()
```

Use `CreateWebProviderRequest` / `UpdateWebProviderRequest` and `pydantic.SecretStr` for write-only credential input. Ordinary model diagnostics redact the key; the client reveals it only while serializing an authorized request. `test_web_provider` sends one quota-consuming probe only when called. Use `aclose()` or an async context manager to release the transport.

## Generated HTTP operations

```python
from a13n import Client
from a13n.generated.api.identity import get_auth_context


async def context(base_url: str, token: str):
    async with Client(base_url, token) as client:
        return await client.execute(lambda api: get_auth_context.asyncio_detailed(client=api))
```

`Response.parsed` is a typed success/error union; `status_code`, `headers`, and `content` retain HTTP evidence. Models use attrs rather than the Web facade's Pydantic models. Use generated enums when constructing requests; `UNSET` means omitted and `None` means JSON null. `Client.execute` shares authentication, timeout, cancellation, and the existing httpx2 pool; it does not apply the Web facade's 1 MiB response limit or exception mapping.

For uploads, generated methods accept `a13n.generated.types.File` with a caller-owned binary file and stream bounded chunks through the async transport. For downloads, use `async with client.stream(operation.build_request(...)) as response` and iterate `response.aiter_bytes()`. Do not use buffered generated `asyncio_detailed` downloads for large files. Low-level generated synchronous clients are separately owned, not another mode of the async facade.

## Development

Run the repository-wide Python checks from the repository root:

```bash
make sdk-python-check
```

## License

Licensed under the Apache License 2.0.
