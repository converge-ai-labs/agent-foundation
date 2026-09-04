"""Shared interactive input and credential-safe HTTP diagnostics for manual checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from getpass import getpass

import httpx2
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError


def show(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


async def report_http_error(response: httpx2.Response) -> None:
    if response.is_success:
        return
    # Do not read the body here: the module owns response size limits and error mapping.
    # Queries, headers, and upstream bodies can contain account or authorization secrets.
    request = response.request
    key = request.headers.get("x-api-key", "") or request.headers.get("authorization", "").removeprefix("Bearer ")
    path = request.url.path.replace(key, "[REDACTED]") if key else request.url.path
    show({"http_status": response.status_code, "method": request.method, "path": path})
    hints = {
        401: "The endpoint rejected authentication. Use an active credential issued for this provider deployment.",
        403: "The endpoint denied access. Check the key's project permissions and the provider's access restrictions.",
        404: "The endpoint did not find this resource. Check the deployment, API profile, and resource identifier.",
        429: "The provider rate limit was reached. Wait before retrying.",
    }
    hint = hints.get(response.status_code)
    if hint:
        print(hint, file=sys.stderr, flush=True)


def required_input(value: str | None, label: str) -> str:
    result = (value or input(f"{label}: ")).strip()
    if not result:
        raise ValueError(f"{label} must not be empty")
    return result


def run_cli(
    args: argparse.Namespace, run: Callable[[argparse.Namespace, str, httpx2.AsyncClient], Awaitable[int]]
) -> int:
    key = ""
    try:
        variable = f"{args.provider.upper()}_API_KEY"
        key = os.environ.get(variable, "").strip()
        if not key:
            if not sys.stdin.isatty():
                raise ValueError(f"Set {variable} or run in an interactive terminal for hidden key input")
            key = getpass(f"{args.provider} API key (hidden input): ").strip()
        if not key:
            raise ValueError("API key must not be empty")

        async def execute() -> int:
            async with httpx2.AsyncClient(
                timeout=30, follow_redirects=False, event_hooks={"response": [report_http_error]}
            ) as client:
                return await run(args, key, client)

        return asyncio.run(execute())
    except (KeyboardInterrupt, EOFError):
        print("\nExited. If execution had started, check the provider before retrying.", file=sys.stderr)
        return 130
    except ConnectorProviderError as error:
        show(
            {
                "error": error.code,
                "retryable": error.retryable,
                "outcome_unknown": error.outcome_unknown,
                "retry_after_seconds": error.retry_after_seconds,
            }
        )
        print(
            "Check provider credentials, permissions, endpoint/profile compatibility, and connection state.",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        message = str(error).replace(key, "[REDACTED]") if key else str(error)
        print(f"Failed: {type(error).__name__}: {message}", file=sys.stderr)
        return 1
