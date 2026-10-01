"""Bounded loopback transport; protocol validation and token exchange stay elsewhere."""

from collections.abc import Callable

from anyio import EndOfStream, fail_after
from anyio.abc import SocketStream
from pydantic_ai.exceptions import UserError

from a13n_harness_ui.errors import HarnessUiError


async def receive_callback(stream: SocketStream, redirect_uri: str, submit: Callable[[str], object]) -> None:
    async with stream:
        status = b"400 Bad Request"
        try:
            with fail_after(5):
                raw = b""
                while b"\r\n\r\n" not in raw and len(raw) <= 16384:
                    raw += await stream.receive(4096)
                method, target, version = raw.split(b"\r\n", 1)[0].decode("ascii").split(" ")
                if (
                    method == "GET"
                    and version.startswith("HTTP/1.")
                    and target.startswith("/auth/callback?")
                    and len(raw) <= 16384
                ):
                    submit(redirect_uri.removesuffix("/auth/callback") + target)
                    status = b"200 OK"
        except (ValueError, UnicodeError, EndOfStream, TimeoutError, UserError, HarnessUiError):
            pass
        body = b"Return to Harness UI to check sign-in status. You can close this tab."
        await stream.send(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
