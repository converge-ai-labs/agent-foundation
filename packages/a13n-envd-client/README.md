# a13n Envd Client

`a13n-envd-client` is the low-level Python client for Envd's Environment Interaction Protocol (EIP). The import package is `a13n_envd_client`. Python 3.13 or later is required.

Use this package when implementing an Environment Provider or a trusted EIP integration. Most applications should start with [`a13n-harness`](../a13n-harness/README.md), whose Environment Providers own Provider adaptation and target lifecycle.

## Public surface

- Generated EIP 0.1 models, codecs, method metadata, and typed `EIPClient` operations for all 35 protocol methods in `a13n_envd_client.eip.v1`.
- `EIPSession`: identity/protocol/descriptor validation, initialization and readiness, monotonic descriptor refresh, clean close, abort, and transfer helpers.
- `StdioTransport`, `HttpTransport`, and `AcceptedWebSocketTransport` over trusted Host-provided pipes, an authenticated HTTP(S) endpoint, or an already-authenticated reverse-WebSocket connection.
- `EIPFileReader` and `EIPFileWriter`: bounded binary transfer, integrity evidence, explicit commit, and staged-writer abort.
- `EIPOutputReader` and `EIPOutputPage`: contiguous retained-output paging and producer/completeness evidence.
- `RequestCoordinator`: bounded concurrent request correlation, data-frame routing, cancellation, and typed errors.
- `EIPTransport`, frame types, and `WebSocketConnection` for custom carrier adapters.

The package does not install or launch Envd, allocate its required private runtime directory, issue credentials, or start a WebSocket listener. Those responsibilities belong to the Provider or Host.

## Connect to an existing daemon

```python
import asyncio
import os

from a13n_envd_client import EIPSession, HttpTransport


async def main() -> None:
    transport = HttpTransport(
        endpoint=os.environ["ENVD_ENDPOINT"],
        credential=os.environ["ENVD_CREDENTIAL"],
    )
    session = await EIPSession.initialize(
        transport,
        expected_environment_id=os.environ["ENVD_ENVIRONMENT_ID"],
        required_methods=("environment.describe", "session.close"),
    )
    async with session:
        descriptor = await session.describe()
        print(descriptor.generation)


if __name__ == "__main__":
    asyncio.run(main())
```

This example requires an existing configured daemon and its credential. The endpoint is its base URL. Initialization performs the initial readiness check; closing the client does not destroy the external workspace or daemon.

The [Python EIP client guide](../../docs/a13n-envd/python-client.md) covers every transport/session option, all error classes, explicit writer commit, output paging, timeout semantics, and the complete generated method reference. The [Envd operator guide](../../docs/a13n-envd/configuration.md) owns standalone bootstrap, including `A13N_ENVD_RUNTIME_DIR`.

## Failure semantics

A carrier timeout, cancellation, or connection failure does not prove an already dispatched mutation failed or was absent. The client does not automatically retry ambiguous operations. Reconcile with the same operation ID and semantic request where permitted, a receipt, or native-state inspection.

Operations with `context.timeout_ms` get their operation budget plus response allowance; without a configured coordinator timeout the allowance is 30 seconds. HTTP control reads follow the same operation-plus-allowance model while retaining their separate connect/write/pool timeouts. Initialization and readiness have their own enclosing deadlines.

## Validate

```console
uv run --locked pytest packages/a13n-envd-client/tests
```

## Versioning and generation

The client and native daemon share one Envd stable `X.Y.Z` or RC `X.Y.Z-rc.N` release identity. Python metadata uses PEP 440 `X.Y.ZrcN`; native artifacts retain SemVer spelling. EIP's negotiated major/minor is independent of the package release.

The IDL and repository generator own the wire artifacts. Do not hand-edit generated Python models or methods. See [protocol source and generation](../../spec/a13n-envd/08-protocol-source-client-and-generation.md).

## License

[Apache License 2.0](LICENSE).
