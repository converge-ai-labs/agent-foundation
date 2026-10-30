"""Screenshot geometry and one-shot visual pointer fallback on the selected page."""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import BrowserError, BrowserScreenshot
from .actions import Click, Clip, Hover, Visual

if TYPE_CHECKING:
    from .cdp import Chromium, Page


@dataclass(frozen=True)
class View:
    token: str
    epoch: int
    viewport: dict
    clip: dict


async def capture(
    browser: Chromium, page: Page, *, full_page: bool = False, clip: Clip | None = None
) -> BrowserScreenshot:
    browser.require_document(page)
    if full_page and clip is not None:
        raise BrowserError("Choose full_page or clip, not both.")
    page.visual = None
    epoch = page.visual_epoch
    metrics = await browser.send("Page.getLayoutMetrics", page=page)
    viewport = metrics["cssVisualViewport"]
    if viewport["scale"] != 1:
        raise BrowserError("Reset pinch zoom before capturing a coordinate screenshot.")
    if full_page:
        area = metrics["cssContentSize"]
    elif clip is not None:
        area = clip.model_dump()
        if area["x"] + area["width"] > viewport["clientWidth"] or area["y"] + area["height"] > viewport["clientHeight"]:
            raise BrowserError("Clip must fit the current viewport in CSS pixels.")
        area["x"] += viewport["pageX"]
        area["y"] += viewport["pageY"]
    else:
        area = {
            "x": viewport["pageX"],
            "y": viewport["pageY"],
            "width": viewport["clientWidth"],
            "height": viewport["clientHeight"],
        }
    if area["width"] * area["height"] > 16_000_000:
        raise BrowserError("Screenshot exceeds 16 million CSS pixels. Use a viewport or clip.")
    # Keep background targets producing frames without taking native focus.
    # The short-lived screencast belongs to our page session; its frames are
    # unused, and stopping it leaves other CDP sessions' streams alone.
    try:
        await browser.send(
            "Page.startScreencast", {"format": "jpeg", "maxWidth": 64, "maxHeight": 64, "everyNthFrame": 1}, page
        )
        result = await browser.send(
            "Page.captureScreenshot",
            {"format": "jpeg", "quality": 75, "clip": {**area, "scale": 1}, "captureBeyondViewport": full_page},
            page,
        )
    finally:
        await browser.send("Page.stopScreencast", page=page)
    current = (await browser.send("Page.getLayoutMetrics", page=page))["cssVisualViewport"]
    if page.visual_epoch != epoch or (not full_page and current != viewport):
        raise BrowserError("Page or viewport changed during capture. Take a fresh screenshot.")
    # Full-page capture can change scrollbar layout. It is observation-only;
    # pointer fallback uses a viewport capture with a stable coordinate space.
    token = "" if full_page else secrets.token_hex(12)
    if token:
        page.visual = View(token, epoch, viewport, area)
    return BrowserScreenshot(base64.b64decode(result["data"], validate=True), token, area["width"], area["height"])


async def pointer(browser: Chromium, page: Page, action: Click | Hover, target: Visual) -> None:
    view = page.visual
    if view is None or view.token != target.screenshot or view.epoch != page.visual_epoch:
        raise BrowserError("Screenshot reference expired. Take a fresh browser_screenshot.")
    current = (await browser.send("Page.getLayoutMetrics", page=page))["cssVisualViewport"]
    if current != view.viewport or page.visual is not view:
        raise BrowserError("Viewport changed. Take a fresh screenshot before using coordinates.")
    x = view.clip["x"] + target.x * view.clip["width"] - current["pageX"]
    y = view.clip["y"] + target.y * view.clip["height"] - current["pageY"]
    if not (0 <= x < current["clientWidth"] and 0 <= y < current["clientHeight"]):
        raise BrowserError("Point is outside the visible viewport. Scroll, then take a fresh screenshot.")
    await browser.dispatch_input(
        page, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"}
    )
    if isinstance(action, Hover):
        return
    for count in range(1, action.count + 1):
        current = (await browser.send("Page.getLayoutMetrics", page=page))["cssVisualViewport"]
        if current != view.viewport or page.visual is not view or page.visual_epoch != view.epoch:
            raise BrowserError(
                "Page or viewport changed during the gesture. Take a fresh screenshot; remaining clicks were not sent.",
                dispatch="dispatched" if count > 1 else "not_dispatched",
            )
        params = {"x": x, "y": y, "button": action.button, "clickCount": count}
        try:
            await browser.dispatch_input(page, "Input.dispatchMouseEvent", {"type": "mousePressed", **params})
        finally:
            await browser.dispatch_input(page, "Input.dispatchMouseEvent", {"type": "mouseReleased", **params})
