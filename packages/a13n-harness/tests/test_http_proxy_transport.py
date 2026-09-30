"""Real socket coverage for native proxy routing composed with a host-owned direct transport."""

from __future__ import annotations

import asyncio
import os
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from a13n_harness.providers.http_transport import EnvironmentProxyClient
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method"}:
            monkeypatch.delenv(key)


@pytest.fixture
def tls_contexts(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "origin.test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("origin.test"), x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_file, key_file)
    client = ssl.create_default_context(cafile=str(cert_file))
    return server, client


@asynccontextmanager
async def endpoint(
    *, context: ssl.SSLContext | None = None, proxy: bool = False, reject: bool = False
) -> AsyncIterator[tuple[int, list[bytes]]]:
    requests: list[bytes] = []
    writers: list[asyncio.StreamWriter] = []
    tasks: set[asyncio.Task[None]] = set()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        try:
            async with asyncio.timeout(5):
                while True:
                    headers = await reader.readuntil(b"\r\n\r\n")
                    requests.append(headers)
                    if proxy:
                        assert headers.startswith(b"CONNECT ")
                        if reject:
                            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                            await writer.drain()
                            return
                        authority = headers.split(b" ")[1].decode()
                        host, port = authority.rsplit(":", 1)
                        assert host in {"origin.test", "other.test"}
                        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", int(port))
                        writers.append(upstream_writer)
                        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                        await writer.drain()

                        async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
                            try:
                                while data := await source.read(65536):
                                    destination.write(data)
                                    await destination.drain()
                            finally:
                                destination.close()

                        await asyncio.gather(copy(reader, upstream_writer), copy(upstream_reader, writer))
                        return
                    length = next(
                        (
                            int(line.split(b":", 1)[1])
                            for line in headers.split(b"\r\n")
                            if line.lower().startswith(b"content-length:")
                        ),
                        0,
                    )
                    await reader.readexactly(length)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(serve(reader, writer))
        tasks.add(task)

    server = await asyncio.start_server(accept, "127.0.0.1", 0, ssl=context)
    async with server:
        try:
            yield server.sockets[0].getsockname()[1], requests
        finally:
            for writer in writers:
                writer.close()
            if tasks:
                await asyncio.gather(*tasks)


@pytest.mark.parametrize("variable", ["http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"])
async def test_native_http_proxy_bypasses_custom_direct_transport(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    def direct(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Proxy route must not use the direct transport")

    async with endpoint() as (port, seen):
        monkeypatch.setenv(variable, f"http://127.0.0.1:{port}")
        async with EnvironmentProxyClient(httpx2.MockTransport(direct), timeout=2) as client:
            response = await client.post("http://origin.test/path", content=b"mutation")
            assert response.text == "ok"
        assert len(seen) == 1
        assert seen[0].startswith(b"POST http://origin.test/path HTTP/1.1")


@pytest.mark.parametrize("bypass", ["origin.test", "*"])
async def test_no_proxy_keeps_host_direct_transport(monkeypatch: pytest.MonkeyPatch, bypass: str) -> None:
    direct = []

    def send(request: httpx2.Request) -> httpx2.Response:
        direct.append(str(request.url))
        return httpx2.Response(200)

    async with endpoint() as (port, seen):
        monkeypatch.setenv("ALL_PROXY", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("NO_PROXY", bypass)
        async with EnvironmentProxyClient(httpx2.MockTransport(send), timeout=2) as client:
            assert (await client.get("https://origin.test/path")).status_code == 200
        assert direct == ["https://origin.test/path"]
        assert not seen


@pytest.mark.parametrize("variable", ["https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"])
async def test_native_tunnel_keeps_hostname_tls_and_proxy_auth(
    monkeypatch: pytest.MonkeyPatch, tls_contexts: tuple[ssl.SSLContext, ssl.SSLContext], variable: str
) -> None:
    server_tls, client_tls = tls_contexts

    def direct(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Proxy route must not use the direct transport")

    async with endpoint(context=server_tls) as (port, origin), endpoint(proxy=True) as (proxy, seen):
        monkeypatch.setenv(variable, f"http://user:password@127.0.0.1:{proxy}")
        async with EnvironmentProxyClient(httpx2.MockTransport(direct), timeout=2, verify=client_tls) as client:
            for _ in range(2):
                response = await client.post(f"https://origin.test:{port}/path", content=b"mutation")
                assert response.text == "ok"
            with pytest.raises(httpx2.ConnectError, match="certificate verify failed"):
                await client.get(f"https://other.test:{port}/path")
        assert client.is_closed
        assert len(seen) == 2
        assert seen[0].startswith(f"CONNECT origin.test:{port} HTTP/1.1".encode())
        assert b"Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==" in seen[0]
        assert len(origin) == 2
        assert all(f"Host: origin.test:{port}\r\n".encode() in request for request in origin)
        assert all(b"Proxy-Authorization" not in request for request in origin)


async def test_rejected_proxy_does_not_fall_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    def direct(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("A failed proxy must not fall back to direct")

    async with endpoint(proxy=True, reject=True) as (proxy, seen):
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy}")
        async with EnvironmentProxyClient(httpx2.MockTransport(direct), timeout=2) as client:
            with pytest.raises(httpx2.ProxyError):
                await client.post("https://origin.test/path", content=b"mutation")
        assert len(seen) == 1


async def test_direct_transport_closed_alongside_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []

    class Direct(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200)

        async def aclose(self) -> None:
            closed.append(True)

    async with endpoint() as (port, seen):
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{port}")
        client = EnvironmentProxyClient(Direct(), timeout=2)
        await client.get("http://origin.test/")
        await client.aclose()
        await client.aclose()
        assert closed == [True]
        assert len(seen) == 1


@pytest.mark.parametrize("kind", ["web", "connector", "memory"])
async def test_default_provider_clients_honor_proxy_without_adding_retries(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from dataclasses import replace

    from a13n_harness.providers.connector.builtins import COMPOSIO
    from a13n_harness.providers.memory import MEM0_PLATFORM
    from a13n_harness.providers.web.transport import provider_client

    @asynccontextmanager
    async def connector(configuration, credential, http):
        yield http._http_client

    @asynccontextmanager
    async def memory(configuration, credential, namespace, http):
        yield http

    @asynccontextmanager
    async def open_client():
        if kind == "connector":
            async with replace(COMPOSIO, open_provider=connector).open({}, {"api_key": "test"}) as client:
                yield client
        elif kind == "memory":
            async with replace(MEM0_PLATFORM, open_store=memory).open(
                {}, {"api_key": "test"}, namespace="test"
            ) as client:
                yield client
        else:
            async with provider_client() as client:
                yield client

    async with endpoint(proxy=True, reject=True) as (proxy, seen):
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy}")
        async with open_client() as client:
            with pytest.raises(httpx2.ProxyError):
                await client.post("https://origin.test/write", content=b"mutation")
        assert len(seen) == 1
        assert seen[0].startswith(b"CONNECT origin.test:443 HTTP/1.1")
