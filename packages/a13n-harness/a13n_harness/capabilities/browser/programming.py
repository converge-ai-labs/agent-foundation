"""Selected-page programming primitives, sharing native browser admission."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from pydantic import JsonValue

from . import BrowserError

if TYPE_CHECKING:
    from .cdp import Chromium, Page

# Browser/Target/session management and Host file I/O remain with their owners.
# These are page protocol domains, not a second action-schema catalog.
CDP_DOMAINS = frozenset(
    {
        "Accessibility",
        "Animation",
        "CSS",
        "DOM",
        "DOMDebugger",
        "DOMSnapshot",
        "Debugger",
        "Emulation",
        "Input",
        "Log",
        "Network",
        "Overlay",
        "Page",
        "Performance",
        "Runtime",
    }
)
CDP_EXCLUDED = frozenset(
    {
        "DOM.setFileInputFiles",  # Host-authorized browser_files owns file transfer.
        "DOM.getFileInfo",  # Service does not expose browser-Host filesystem paths.
        "Page.setDownloadBehavior",  # Runtime-owned browser-wide download tracking.
        "Page.disable",  # Navigation/frame/dialog events maintain native references.
        "Runtime.disable",
    }
)
CDP_DESCRIPTION = (
    "Send a CDP method with JSON params to the selected page session; return its protocol result. "
    "Supported domains: " + ", ".join(sorted(CDP_DOMAINS)) + ". "
    "Excluded methods: " + ", ".join(sorted(CDP_EXCLUDED)) + ". "
    "Input.dispatchDragEvent cannot supply DragData.files. "
    "No endpoint, target or session override; use browser_tabs and browser_files for their operations. "
    "Expires element/screenshot refs even on failure. Raw commands do not verify business outcomes, "
    "release held input, or undo installed page effects. Never replay an uncertain mutation."
)


def _json_value(value: JsonValue) -> JsonValue:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise BrowserError("The browser result is not a JSON value.", dispatch="dispatched") from None
    return value


async def _send(browser: Chromium, page: Page, method: str, params: dict | None) -> dict:
    """Yield a known modal interruption so the next tool can handle the dialog."""
    operation = asyncio.create_task(browser.send(method, params, page))
    dialog = None if method == "Page.handleJavaScriptDialog" else asyncio.create_task(page.dialog_opened.wait())
    retained = False
    try:
        if dialog is None:
            return await operation
        done, _ = await asyncio.wait((operation, dialog), return_when=asyncio.FIRST_COMPLETED)
        if operation in done:
            return await operation
        # Like native input, the protocol reply may wait for the modal to close.
        browser.background(operation)
        retained = True
        raise BrowserError(
            "A JavaScript dialog interrupted the command; its result is unavailable and page effects may continue. "
            "Use browser_dialog, then observe the outcome. Do not replay the command.",
            dispatch="dispatched",
        )
    except BrowserError as error:
        cause = error.__cause__
        if (
            isinstance(cause, RuntimeError)
            and cause.args
            and isinstance(cause.args[0], dict)
            and cause.args[0].get("code") in {-32601, -32602}
        ):
            # This one programming command was rejected; native composites retain uncertainty.
            raise BrowserError("CDP rejected the method or parameters. Correct the request before retrying.") from error
        raise
    finally:
        if dialog is not None:
            dialog.cancel()
        if not retained and not operation.done():
            operation.cancel()
        await asyncio.gather(
            *((dialog,) if dialog is not None else ()),
            *(() if retained else (operation,)),
            return_exceptions=True,
        )


async def evaluate(browser: Chromium, page: Page, expression: str) -> JsonValue:
    browser.require_document(page)
    response = await _send(
        browser,
        page,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True, "timeout": 10000},
    )
    if "exceptionDetails" in response:
        raise BrowserError(
            "Page JavaScript failed after dispatch; earlier effects may have occurred. Observe before retrying.",
            dispatch="dispatched",
        )
    result = response.get("result", {})
    if "value" in result:
        return _json_value(result["value"])
    if result.get("type") == "undefined":
        return None
    raise BrowserError(
        "JavaScript did not return a JSON value. Return serializable data instead of handles or non-finite values.",
        dispatch="dispatched",
    )


async def command(
    browser: Chromium, page: Page, method: str, params: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    domain, separator, name = method.partition(".")
    if not separator or not name.isidentifier() or domain not in CDP_DOMAINS or method in CDP_EXCLUDED:
        raise BrowserError("This CDP method is outside the selected-page tool contract. " + CDP_DESCRIPTION)
    if method == "Input.dispatchDragEvent" and params is not None:
        data = params.get("data")
        if isinstance(data, dict) and data.get("files"):
            raise BrowserError("Use browser_files for Host-authorized file transfer; DragData.files is unavailable.")
    if method != "Page.handleJavaScriptDialog":
        browser.require_document(page)
    result = await _send(browser, page, method, params)
    _json_value(result)
    return result
