"""Bounded loopback transport; protocol validation and token exchange stay elsewhere."""

from collections.abc import Callable
from urllib.parse import urlsplit

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, fail_after, move_on_after
from anyio.abc import SocketStream
from pydantic_ai.exceptions import UserError

from a13n_harness_ui.errors import HarnessUiError


async def receive_callback(stream: SocketStream, redirect_uri: str, submit: Callable[[str], object]) -> None:
    redirect = urlsplit(redirect_uri)
    async with stream:
        status = b"400 Bad Request"
        try:
            with fail_after(5):
                raw = b""
                while b"\r\n\r\n" not in raw and len(raw) <= 16384:
                    raw += await stream.receive(4096)
                head = raw.split(b"\r\n\r\n", 1)[0].decode("ascii").split("\r\n")
                method, target, version = head[0].split(" ")
                hosts = [
                    value.strip()
                    for line in head[1:]
                    for key, _, value in [line.partition(":")]
                    if key.lower() == "host"
                ]
                if (
                    method == "GET"
                    and version.startswith("HTTP/1.")
                    and hosts == [redirect.netloc]
                    and target.startswith(redirect.path + "?")
                    and len(raw) <= 16384
                ):
                    submit(f"{redirect.scheme}://{redirect.netloc}{target}")
                    status = b"200 OK"
        except (ValueError, UnicodeError, EndOfStream, TimeoutError, UserError, HarnessUiError):
            pass
        except (BrokenResourceError, ClosedResourceError):
            return
        body = b"Return to Harness UI to check sign-in status. You can close this tab."
        try:
            with move_on_after(1, shield=True):
                await stream.send(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
        except (BrokenResourceError, ClosedResourceError):
            return
