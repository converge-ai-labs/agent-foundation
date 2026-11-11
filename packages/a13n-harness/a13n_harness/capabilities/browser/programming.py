"""Selected-page programming primitives, sharing native browser admission."""

from __future__ import annotations

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


async def evaluate(browser: Chromium, page: Page, expression: str) -> JsonValue:
    browser.require_document(page)
    response = await browser.send(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True, "timeout": 10000},
        page,
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
    result = await browser.send(method, params, page)
    _json_value(result)
    return result
