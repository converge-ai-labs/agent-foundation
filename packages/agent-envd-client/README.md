# Agent Envd Client

`a13n-envd-client` is the Python client for the Agent Environment Interaction Protocol (EIP). It belongs to the agent-envd release group and is versioned and published together with `agent-envd`.

## Available surface

The package currently provides:

- generated EIP 1.0 Pydantic wire models, canonical codecs, method metadata, and typed `EIPClient` methods for the complete protocol surface;
- `RequestCoordinator` for bounded request IDs, concurrent response correlation, data-frame routing, typed errors, and no automatic ambiguous retry;
- `StdioTransport` for multiplexed content-length control and binary data frames over trusted parent-supplied asyncio process pipes;
- `HttpTransport` for authenticated Host-dialed control requests and raw streaming transfer bodies over HTTP(S);
- `AcceptedWebSocketTransport` for EIP framing over an already-authenticated reverse-WebSocket `ServerConnection` accepted by the Host;
- `EIPSession` for initialization, exact required-method/generation/descriptor validation, monotonic method-and-limit refresh that rejects topology/posture/feature changes or widening, and session close;
- high-level `EIPFileReader` and `EIPFileWriter` async context managers, exposed by `EIPSession.open_reader()` and `EIPSession.open_writer()`, for bounded streaming file transfer.

Configured daemons can expose resource reads, atomic mutations, find, search, receipts, and port observation through the generated client. Trusted stdio, Host-dialed HTTP(S), and Host-accepted reverse WebSocket carry the same EIP method/session contract. Provider process creation, HTTP credential issuance, reverse-WebSocket listener authentication, and lifecycle policy remain outside this package.

## Example

The process launch below is illustrative fixture code. Production launch configuration belongs to the provider/Host boundary.

```python
import asyncio
import os

from a13n_envd_client import EIPSession, StdioTransport


async def main() -> None:
    process = await asyncio.create_subprocess_exec(
        "agent-envd",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "PATH": os.environ["PATH"],
            "AGENT_ENVD_ENVIRONMENT_ID": "env-provider-owned-id",
            # Use this only when an outer sandbox owns containment.
            "AGENT_ENVD_EXECUTION_ISOLATION": "disabled",
        },
    )
    session = await EIPSession.initialize(
        StdioTransport.from_process(process),
        expected_environment_id="env-provider-owned-id",
        required_methods=("environment.describe", "session.close"),
    )
    descriptor = await session.describe()
    print(descriptor.generation)
    await session.close()
    await process.wait()


asyncio.run(main())
```

An `EIPMethodError` contains the generated typed `EIPError`. Carrier timeouts and cancellation never claim that an already sent operation failed or was absent; the client does not retry a possibly dispatched mutation automatically. Mutation callers reconcile by reusing the same operation ID and semantic request while evidence remains, querying its receipt, or observing native state before starting another operation.

## Versioning

The Python package and daemon artifacts share one stable `X.Y.Z` or RC `X.Y.Z-rc.N` agent-envd release identity. Python package metadata represents the RC as the equivalent PEP 440 version `X.Y.ZrcN`; Cargo, binary archives, and container tags retain the canonical SemVer spelling. The negotiated EIP major and minor version remains an independent wire-compatibility identity.

The accepted protocol, generation, ownership, and compatibility design is documented in the [agent-envd specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-envd/08-protocol-source-client-and-generation.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
