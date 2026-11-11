"""Disposable Linux guest for the credential journey; never contacts a public HTTPS server.

The public address exists only on this container's loopback interface. Envd resolves the fixture
hostnames through an outer DNS server; commands use its real controlled DNS and TLS proxy.
"""

import asyncio
import hashlib
import json
import os
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ADDRESS = "93.184.216.34"
ALLOWED = "allowed.egress.test"
OTHER = "other.egress.test"
ROOT = Path("/fixture")


class Resolver(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        query, transport = self.request
        cursor, labels = 12, []
        if len(query) < 12 or query[4:6] != b"\x00\x01":
            return
        while cursor < len(query) and (length := query[cursor]):
            if length > 63 or cursor + length + 1 >= len(query):
                return
            labels.append(query[cursor + 1 : cursor + length + 1])
            cursor += length + 1
        end = cursor + 5
        if end > len(query):
            return
        known = b".".join(labels).lower() in {ALLOWED.encode(), OTHER.encode()}
        answer = known and query[cursor + 1 : end] == b"\x00\x01\x00\x01"
        header = (
            query[:2] + bytes([0x81, 0x80 if known else 0x83]) + b"\x00\x01" + bytes([0, int(answer)]) + b"\x00" * 4
        )
        response = header + query[12:end]
        if answer:
            response += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x1e\x00\x04" + socket.inet_aton(ADDRESS)
        transport.sendto(response, self.client_address)


class Receiver(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # Record proof of the transmitted value, never the value itself. Do not echo authentication.
        digest = hashlib.sha256(self.headers.get("authorization", "").encode()).hexdigest()
        with (ROOT / "requests.jsonl").open("a") as output:
            output.write(json.dumps({"path": self.path, "host": self.headers.get("host"), "digest": digest}) + "\n")
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", "9")
        self.end_headers()
        self.wfile.write(b"received\n")

    def log_message(self, format: str, *args: object) -> None:
        pass


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
    """Keep the Control URL's loopback identity and certificate while crossing the Docker boundary."""
    remote_reader, remote_writer = await asyncio.open_connection("host.docker.internal", port)

    async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
        try:
            while data := await source.read(65536):
                destination.write(data)
                await destination.drain()
        finally:
            destination.close()
            await destination.wait_closed()

    await asyncio.gather(copy(reader, remote_writer), copy(remote_reader, writer))


async def main() -> None:
    configuration = json.loads((ROOT / "launch.json").read_text())
    # Managed broker processes clear launch environment variables, so trust must use the guest's system store.
    shutil.copyfile(ROOT / "upstream.pem", "/usr/local/share/ca-certificates/egress-journey.crt")
    subprocess.run(["update-ca-certificates"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["ip", "address", "add", ADDRESS + "/32", "dev", "lo"], check=True)
    resolver = socketserver.UDPServer(("127.0.0.1", 53), Resolver)
    threading.Thread(target=resolver.serve_forever, daemon=True).start()
    server = HTTPServer((ADDRESS, 443), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ROOT / "server.pem", ROOT / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.chown("/workspace", 1000, 1000)
    environment = {**os.environ, **configuration["environment"]}
    port = configuration.get("control_port")
    listener = None
    if port is not None:
        listener = await asyncio.start_server(lambda r, w: relay(r, w, port), "127.0.0.1", port)
    try:
        process = await asyncio.create_subprocess_exec("a13n-envd", env=environment)
        (ROOT / "ready").touch()
        status = await process.wait()
        raise SystemExit(status)
    finally:
        if listener is not None:
            listener.close()
            await listener.wait_closed()
        server.shutdown()
        server.server_close()
        resolver.shutdown()
        resolver.server_close()


if __name__ == "__main__":
    asyncio.run(main())
