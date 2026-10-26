"""Chromium page operations over cdp-use; the embedding Host owns the process."""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpx2
from a13n_logging import get_logger
from websockets.exceptions import ConnectionClosed

from a13n_harness.capabilities.browser import (
    BrowserAction,
    BrowserElement,
    BrowserError,
    BrowserObservation,
)

if TYPE_CHECKING:
    from .scope import CdpBrowserBackend

_LOGGER = get_logger(__name__)


async def websocket_url(cdp_url: str) -> str:
    """Resolve a trusted Host endpoint; never expose it as an Agent tool input."""
    parsed = urlsplit(cdp_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname or parsed.fragment:
        raise BrowserError("Use an HTTP(S) CDP base URL or a WS(S) debugger URL.")
    if parsed.scheme in {"ws", "wss"}:
        return cdp_url
    try:
        async with httpx2.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(parsed._replace(path=parsed.path.rstrip("/") + "/json/version").geturl())
            response.raise_for_status()
            endpoint = response.json()["webSocketDebuggerUrl"]
        result = urlsplit(endpoint)
        if result.scheme not in {"ws", "wss"} or not result.hostname or result.fragment:
            raise ValueError("Invalid debugger URL")
        return endpoint
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise BrowserError("Cannot discover the browser CDP endpoint.") from exc


@dataclass(frozen=True)
class Frame:
    identity: str
    session: str
    parent: str = ""
    url: str = ""


@dataclass(frozen=True)
class Node:
    backend: int
    frame: str
    session: str


@dataclass
class Page:
    target: str
    session: str
    url: str = "about:blank"
    revision: str = ""
    elements: dict[str, Node] = field(default_factory=dict)
    frames: dict[str, Frame] = field(default_factory=dict)
    frame_sessions: dict[str, str] = field(default_factory=dict)
    frame_parents: dict[str, str] = field(default_factory=dict)
    dialog: dict | None = None
    dialog_opened: asyncio.Event = field(default_factory=asyncio.Event)
    epoch: int = 0
    deferred_objects: list[tuple[Frame, str]] = field(default_factory=list)
    requires_observation: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loaded: asyncio.Event = field(default_factory=asyncio.Event)

    def invalidate(self) -> None:
        self.epoch += 1
        self.revision = ""
        self.elements.clear()


class Chromium:
    """One browser connection, explicit page sessions and bounded protocol calls."""

    @classmethod
    async def connect(cls, endpoint: str) -> Chromium:
        from cdp_use import CDPClient

        client = CDPClient(await websocket_url(endpoint))
        try:
            await client.start()
            browser = cls(client)
            await browser.send("Target.setDiscoverTargets", {"discover": True})
            return browser
        except BaseException:
            await client.stop()
            raise

    def __init__(self, client: Any) -> None:
        self.client = client
        self.disconnected = False
        self.owners: dict[str, CdpBrowserBackend] = {}
        self.scopes: set[CdpBrowserBackend] = set()
        self.pages: dict[str, Page] = {}
        self.attach_locks: dict[str, asyncio.Lock] = {}
        self.download_directory: str | None = None
        self.download_records: dict[str, dict] = {}
        self.download_started: Callable[[dict], None] = lambda item: None
        self.tasks: set[asyncio.Task] = set()
        self.opened: Callable[[dict], None] = lambda target: None
        self.closed: Callable[[str], None] = lambda target: None
        client.register.Page.frameNavigated(self._navigated)
        client.register.Page.domContentEventFired(self._loaded)
        client.register.Page.navigatedWithinDocument(self._within_document)
        client.register.Target.targetCreated(self._created)
        client.register.Target.targetDestroyed(self._destroyed)
        client.register.Page.frameAttached(self._frame_attached)
        client.register.Page.frameDetached(self._frame_detached)
        client.register.Page.javascriptDialogOpening(self._dialog_opened)
        client.register.Page.javascriptDialogClosed(self._dialog_closed)
        client.register.Browser.downloadWillBegin(self._download_started)
        client.register.Browser.downloadProgress(self._download_progress)

    def background(self, operation) -> None:
        task = operation if isinstance(operation, asyncio.Task) else asyncio.create_task(operation)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        def completed(done: asyncio.Task) -> None:
            if not done.cancelled() and (error := done.exception()) is not None:
                _LOGGER.warning("Background CDP operation failed: %s", error)

        task.add_done_callback(completed)

    async def send(self, method: str, params: dict | None = None, page: Page | Frame | None = None) -> dict:
        try:
            async with asyncio.timeout(15):
                return await self.client.send_raw(method, params or {}, page.session if page else None)
        except ConnectionClosed as exc:
            for future in self.client.pending_requests.values():
                if not future.done():
                    future.set_exception(ConnectionError("Browser connection closed"))
                    # send_raw may fail before it starts awaiting this future.
                    future.exception()
            self.client.pending_requests.clear()
            raise BrowserError("Browser connection closed; observe before retrying an uncertain action.") from exc
        except (TimeoutError, RuntimeError, ConnectionError) as exc:
            raise BrowserError(
                "Browser operation failed; its outcome may be uncertain. Observe before retrying."
            ) from exc
        finally:
            for identity, future in tuple(self.client.pending_requests.items()):
                if future.cancelled():
                    self.client.pending_requests.pop(identity, None)

    def _page(self, session: str | None) -> Page | None:
        return next(
            (
                page
                for page in self.pages.values()
                if page.session == session or session in page.frame_sessions.values()
            ),
            None,
        )

    def _created(self, event: dict, session: str | None) -> None:
        info = event["targetInfo"]
        if info["type"] == "page" and (owner := self.owners.get(info.get("openerId", ""))):
            owner._claim(info["targetId"])
        self.opened(info)

    def _destroyed(self, event: dict, session: str | None) -> None:
        target = event["targetId"]
        if owner := self.owners.get(target):
            owner._removed(target)
        self.pages.pop(target, None)
        self.attach_locks.pop(target, None)
        for page in self.pages.values():
            if target in page.frame_sessions:
                page.frame_sessions.pop(target, None)
                page.frames.pop(target, None)
                page.invalidate()
        self.closed(target)

    def _frame_attached(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.frame_parents[event["frameId"]] = event["parentFrameId"]
            page.invalidate()

    def _frame_detached(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            if event.get("reason") != "swap":
                removed = {event["frameId"]}
                while children := {key for key, parent in page.frame_parents.items() if parent in removed} - removed:
                    removed.update(children)
                for identity in removed:
                    page.frame_parents.pop(identity, None)
                    page.frames.pop(identity, None)
                    page.frame_sessions.pop(identity, None)
            page.invalidate()

    def _dialog_opened(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.dialog = {**event, "session": session}
            page.dialog_opened.set()
            page.invalidate()

    def _dialog_closed(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.dialog = None
            page.dialog_opened.clear()
            pending, page.deferred_objects = page.deferred_objects, []
            for frame, identity in pending:
                self.background(self.send("Runtime.releaseObject", {"objectId": identity}, frame))
            page.invalidate()

    def _download_started(self, event: dict, session: str | None) -> None:
        page = next((page for page in self.pages.values() if event["frameId"] in page.frames), None)
        if page is None:
            return
        if len(self.download_records) >= 100:
            completed = next(
                (key for key, item in self.download_records.items() if item["state"] != "inProgress"), None
            )
            if completed:
                self.download_records.pop(completed)
            else:
                return
        self.download_records[event["guid"]] = {**event, "page": page.target, "state": "inProgress"}
        self.download_started(self.download_records[event["guid"]])

    def _download_progress(self, event: dict, session: str | None) -> None:
        if item := self.download_records.get(event["guid"]):
            item.update(state=event["state"], received=event.get("receivedBytes", 0))

    async def configure_downloads(self, directory: str) -> None:
        await self.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": directory, "eventsEnabled": True},
        )
        self.download_directory = directory

    def downloads(self, targets: set[str]) -> str:
        if self.download_directory is None:
            return "Download tracking is not configured by this browser's Host."
        records = []
        for guid, item in self.download_records.items():
            if item["page"] in targets:
                records.append(
                    {
                        "name": item.get("suggestedFilename", ""),
                        "state": item["state"],
                        "path": self.download_directory.rstrip("/") + "/" + guid
                        if item["state"] == "completed"
                        else None,
                    }
                )
        return json.dumps(records, ensure_ascii=False)

    def _loaded(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            page.loaded.set()

    async def navigate(self, page: Page, url: str, *, wait: bool = True) -> None:
        page.loaded.clear()
        page.invalidate()
        result = await self.send("Page.navigate", {"url": url}, page)
        if result.get("errorText"):
            raise BrowserError("Navigation failed: " + result["errorText"])
        page.url = url
        if wait and "loaderId" in result:
            try:
                async with asyncio.timeout(25):
                    await page.loaded.wait()
            except TimeoutError as exc:
                raise BrowserError("Navigation did not settle. Observe before retrying.") from exc

    def _navigated(self, event: dict, session: str | None) -> None:
        page = self._page(session)
        frame = event["frame"]
        if page is not None:
            page.frames[frame["id"]] = Frame(
                frame["id"], session or page.session, frame.get("parentId", ""), frame["url"]
            )
            if session == page.session and not frame.get("parentId"):
                page.url = frame["url"]
            page.invalidate()

    def _within_document(self, event: dict, session: str | None) -> None:
        if page := self._page(session):
            if session == page.session and (
                not page.frames or not page.frames.get(event.get("frameId", ""), Frame("", "")).parent
            ):
                page.url = event["url"]
            page.invalidate()

    async def attach(self, target: str, url: str = "about:blank") -> Page:
        async with self.attach_locks.setdefault(target, asyncio.Lock()):
            if target in self.pages:
                return self.pages[target]
            attached = await self.send("Target.attachToTarget", {"targetId": target, "flatten": True})
            page = Page(target, attached["sessionId"], url)
            self.pages[target] = page
            try:
                await self.send("Page.enable", page=page)
                await self.send("Runtime.enable", page=page)
                return page
            except BaseException:
                self.pages.pop(target, None)
                try:
                    await self.send("Target.detachFromTarget", {"sessionId": page.session})
                except BrowserError:
                    pass
                raise

    async def create(self, *, new_window: bool = False) -> Page:
        target = await self.send("Target.createTarget", {"url": "about:blank", "newWindow": new_window})
        try:
            return await self.attach(target["targetId"])
        except BaseException:
            try:
                await self.send("Target.closeTarget", {"targetId": target["targetId"]})
            except BrowserError:
                pass
            raise

    async def window(self, page: Page) -> dict:
        return await self.send("Browser.getWindowForTarget", {"targetId": page.target})

    async def window_bounds(self, identity: int, bounds: dict) -> None:
        await self.send("Browser.setWindowBounds", {"windowId": identity, "bounds": bounds})

    async def activate(self, page: Page) -> None:
        await self.send("Page.bringToFront", page=page)

    async def resize_window(self, page: Page, width: int, height: int) -> None:
        identity = (await self.window(page))["windowId"]
        await self.window_bounds(identity, {"windowState": "normal"})
        # Native window chrome stays in the VNC desktop. Size the content area
        # without emulating an unrelated viewport or changing browser focus.
        content = await self.evaluate(page, "({width:innerWidth,height:innerHeight})")
        bounds = (await self.window(page))["bounds"]
        await self.window_bounds(
            identity,
            {
                "left": 0,
                "top": 0,
                "width": max(200, width + bounds["width"] - content["width"]),
                "height": max(200, height + bounds["height"] - content["height"]),
            },
        )

    async def evaluate(self, page: Page, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            page,
        )
        if "exceptionDetails" in result:
            raise BrowserError("The page script failed. Observe before retrying.")
        return result.get("result", {}).get("value")

    async def documents(self, page: Page) -> tuple[Frame, ...]:
        self.require_document(page)
        tree = (await self.send("Page.getFrameTree", page=page))["frameTree"]
        infos = (await self.send("Target.getTargets"))["targetInfos"]
        iframe_targets = {info["targetId"] for info in infos if info["type"] == "iframe"}
        frames: dict[str, Frame] = {}

        async def visit(node: dict, inherited_session: str, parent: str = "") -> None:
            if len(frames) >= 32:
                return
            info = node["frame"]
            identity = info["id"]
            session = inherited_session
            if identity in iframe_targets:
                if identity not in page.frame_sessions:
                    attached = await self.send("Target.attachToTarget", {"targetId": identity, "flatten": True})
                    page.frame_sessions[identity] = attached["sessionId"]
                    child = Frame(identity, attached["sessionId"], parent, info.get("url", ""))
                    await self.send("Page.enable", page=child)
                    await self.send("Runtime.enable", page=child)
                session = page.frame_sessions[identity]
            frame = Frame(identity, session, parent, info.get("url", ""))
            if parent:
                page.frame_parents[identity] = parent
            frames[identity] = frame
            for child in node.get("childFrames", []):
                await visit(child, session, identity)

        await visit(tree, page.session)
        # Chromium omits out-of-process children from the parent's frame tree.
        # Only attach iframe targets linked by this page's frameAttached ancestry.
        for _ in range(32):
            pending = [
                (key, parent)
                for key, parent in page.frame_parents.items()
                if key not in frames and parent in frames and key in iframe_targets
            ]
            if not pending or len(frames) >= 32:
                break
            for identity, parent in pending[: max(0, 32 - len(frames))]:
                if identity not in page.frame_sessions:
                    attached = await self.send("Target.attachToTarget", {"targetId": identity, "flatten": True})
                    page.frame_sessions[identity] = attached["sessionId"]
                    frame = Frame(identity, attached["sessionId"], parent)
                    await self.send("Page.enable", page=frame)
                    await self.send("Runtime.enable", page=frame)
                frame = Frame(identity, page.frame_sessions[identity], parent)
                child = (await self.send("Page.getFrameTree", page=frame))["frameTree"]
                await visit(child, frame.session, parent)
        page.frames = frames
        return tuple(frames.values())

    @staticmethod
    def require_document(page: Page) -> None:
        if page.dialog is not None:
            raise BrowserError(
                "A JavaScript dialog is pending. Use browser_dialog or human takeover before continuing."
            )

    async def observe(self, page: Page) -> BrowserObservation:
        page.invalidate()
        if page.dialog:
            page.requires_observation = False
            page.revision = secrets.token_hex(12)
            return BrowserObservation(await self.dialog(page, "inspect"), page.revision, ())
        epoch = page.epoch
        frames = await self.documents(page)
        lines, elements = [f"URL: {page.url}"], []
        count = 0
        for frame in frames[:32]:
            if count >= 1200:
                break
            nodes = (
                await self.send(
                    "Accessibility.getFullAXTree",
                    {"frameId": frame.identity} if frame.identity != page.target else {},
                    frame,
                )
            )["nodes"]
            lines.append(f"Frame: {frame.url}")
            for node in nodes:
                count += 1
                if count > 1200:
                    break
                if node.get("ignored"):
                    continue
                role = node.get("role", {}).get("value", "")
                name = str(node.get("name", {}).get("value", ""))[:300]
                lines.append(f"{role}: {name}")
                if role in {
                    "button",
                    "link",
                    "textbox",
                    "combobox",
                    "checkbox",
                    "switch",
                    "radio",
                    "menuitem",
                    "slider",
                    "tab",
                    "region",
                    "listbox",
                    "grid",
                }:
                    if backend := node.get("backendDOMNodeId"):
                        key = secrets.token_hex(8)
                        page.elements[key] = Node(backend, frame.identity, frame.session)
                        elements.append(BrowserElement(key, name[:240]))
        if len(frames) >= 32 or count >= 1200:
            lines.append("Observation is limited to 32 frames and 1200 accessibility nodes.")
        if page.epoch != epoch:
            page.invalidate()
            raise BrowserError("Page or frame changed during observation. Observe again.")
        page.revision = secrets.token_hex(12)
        page.requires_observation = False
        return BrowserObservation("\n".join(lines)[:24000], page.revision, tuple(elements))

    def reference(self, page: Page, target: str, revision: str) -> tuple[Node, Frame]:
        self.require_document(page)
        node = page.elements.get(target)
        if not revision or revision != page.revision or node is None:
            raise BrowserError("Element reference expired. Observe again.")
        frame = page.frames.get(node.frame)
        if frame is None or frame.session != node.session:
            raise BrowserError("Frame changed. Observe again.")
        return node, frame

    @asynccontextmanager
    async def node(self, page: Page, target: str, revision: str):
        node, frame = self.reference(page, target, revision)
        result = await self.send("DOM.resolveNode", {"backendNodeId": node.backend}, frame)
        identity = result["object"]["objectId"]
        try:
            yield node, frame, identity
        finally:
            if page.dialog:
                # Runtime commands can wait behind a modal JavaScript dialog.
                # Release this handle after the dialog closes, without delaying its tool.
                page.deferred_objects.append((frame, identity))
            else:
                await self.send("Runtime.releaseObject", {"objectId": identity}, frame)

    async def settle_rendering(self, frame: Frame) -> None:
        # Input hit testing uses compositor data, which can trail layout after a
        # scroll. Background tabs can suspend animation frames, so never wait
        # indefinitely for their renderer or turn this into a focus operation.
        await self.send(
            "Runtime.evaluate",
            {
                "expression": """document.hidden ? undefined : new Promise(resolve=>{
                    let frame; const finish=()=>{cancelAnimationFrame(frame);clearTimeout(timer);resolve();};
                    const timer=setTimeout(finish,100);
                    frame=requestAnimationFrame(()=>{frame=requestAnimationFrame(finish);});
                })""",
                "awaitPromise": True,
                "returnByValue": True,
            },
            frame,
        )

    async def point(self, page: Page, frame: Frame, x: float, y: float) -> tuple[float, float]:
        seen = set()
        while frame.parent:
            if frame.identity in seen or frame.parent not in page.frames:
                raise BrowserError("Frame ancestry changed. Observe again.")
            seen.add(frame.identity)
            parent = page.frames[frame.parent]
            owner = await self.send("DOM.getFrameOwner", {"frameId": frame.identity}, parent)
            backend = owner["backendNodeId"]
            await self.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend}, parent)
            await self.settle_rendering(parent)
            resolved = await self.send("DOM.resolveNode", {"backendNodeId": backend}, parent)
            identity = resolved["object"]["objectId"]
            try:
                result = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": """function(x,y){const r=this.getBoundingClientRect();
                    for(let a=this;a;a=a.parentElement){const m=getComputedStyle(a).transform;
                    if(m!=='none'){const t=new DOMMatrix(m);if(t.b||t.c||!t.is2D)return null;}}
                    const px=r.left+(this.clientLeft+x)*r.width/this.offsetWidth;
                    const py=r.top+(this.clientTop+y)*r.height/this.offsetHeight;
                    let hit=this.ownerDocument.elementFromPoint(px,py);
                    while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(px,py);if(!inner||inner===hit)break;hit=inner;}
                    for(let a=hit;a;a=a.parentElement||a.getRootNode()?.host){if(a===this)return {x:px,y:py};}
                    return null;}""",
                        "arguments": [{"value": x}, {"value": y}],
                    },
                    parent,
                )
                bounds = result.get("result", {}).get("value")
                if not bounds:
                    raise BrowserError(
                        "Iframe is covered, clipped, rotated or unavailable. Observe again or take over."
                    )
                x, y = bounds["x"], bounds["y"]
            finally:
                await self.send("Runtime.releaseObject", {"objectId": identity}, parent)
            frame = parent
        return x, y

    async def dispatch_input(self, page: Page, method: str, params: dict) -> bool:
        """A modal may pause the command reply after input has already been delivered."""
        operation = asyncio.create_task(self.send(method, params, page))
        dialog = asyncio.create_task(page.dialog_opened.wait())
        retained = False
        try:
            done, _ = await asyncio.wait((operation, dialog), return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                await operation
            else:
                self.background(operation)
                retained = True
            return page.dialog is not None
        finally:
            dialog.cancel()
            if not retained and not operation.done():
                operation.cancel()
            await asyncio.gather(dialog, *(() if retained else (operation,)), return_exceptions=True)

    async def element(self, page: Page, target: str, revision: str, *, fill: str | None = None) -> None:
        async with self.node(page, target, revision) as (node, frame, identity):
            await self.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": node.backend}, frame)
            if fill is None:
                await self.settle_rendering(frame)
            checked = await self.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "returnByValue": True,
                    "functionDeclaration": """function(){if(!this.isConnected||this.disabled)return null;
                const r=this.getBoundingClientRect();if(!r.width||!r.height)return null;
                const x=r.left+r.width/2,y=r.top+r.height/2;
                let hit=this.ownerDocument.elementFromPoint(x,y);
                while(hit?.shadowRoot){const inner=hit.shadowRoot.elementFromPoint(x,y);if(!inner||inner===hit)break;hit=inner;}
                for(let node=hit;node;node=node.parentElement||node.getRootNode()?.host){if(node===this)return {x,y,type:this.type};}
                return null;}""",
                },
                frame,
            )
            point = checked.get("result", {}).get("value")
            if not point:
                raise BrowserError("Element is detached, hidden, disabled or covered. Observe again.")
            if fill is not None:
                if point.get("type") == "file":
                    raise BrowserError("Use browser_upload to select files.")
                await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "functionDeclaration": """function(){
                            this.focus();
                            if(this.isContentEditable){
                                const range=this.ownerDocument.createRange();
                                range.selectNodeContents(this);
                                const selection=this.ownerDocument.getSelection();
                                selection.removeAllRanges();
                                selection.addRange(range);
                            }else{this.select?.();}
                        }""",
                    },
                    frame,
                )
                await self.send("Input.insertText", {"text": fill}, page)
            else:
                x, y = await self.point(page, frame, point["x"], point["y"])
                for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                    await self.dispatch_input(
                        page,
                        "Input.dispatchMouseEvent",
                        {
                            "type": kind,
                            "x": x,
                            "y": y,
                            "button": "left" if kind != "mouseMoved" else "none",
                            **({"clickCount": 1} if kind != "mouseMoved" else {}),
                        },
                    )

    async def scroll(self, page: Page, *, x: int, y: int, target: str = "", revision: str = "") -> str:
        self.require_document(page)
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if abs(x) > 10000 or abs(y) > 10000:
            raise BrowserError("Scroll distance exceeds 10000 CSS pixels.")
        if target:
            async with self.node(page, target, revision) as (_, frame, identity):
                result = await self.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": "function(x,y){if(!this.isConnected)return null;const before=[this.scrollLeft,this.scrollTop];this.scrollBy({left:x,top:y,behavior:'instant'});return {before,after:[this.scrollLeft,this.scrollTop]};}",
                        "arguments": [{"value": x}, {"value": y}],
                    },
                    frame,
                )
                outcome = result.get("result", {}).get("value")
                if outcome is None:
                    raise BrowserError("Scroll target is detached. Observe again.")
        else:
            outcome = await self.evaluate(
                page,
                "(()=>{const before=[scrollX,scrollY];scrollBy({left:"
                + str(x)
                + ",top:"
                + str(y)
                + ",behavior:'instant'});return {before,after:[scrollX,scrollY]}})()",
            )
        return "Scroll positions: " + json.dumps(outcome)

    async def dialog(self, page: Page, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        if action != "inspect" and page.requires_observation:
            raise BrowserError("Observe after handback before answering a dialog.")
        current = page.dialog
        if current is None:
            return "No JavaScript dialog is pending."
        if action == "inspect":
            return "JavaScript dialog: " + json.dumps(
                {key: current.get(key, "") for key in ("type", "message", "defaultPrompt")}, ensure_ascii=False
            )
        session = Frame("", current["session"] or page.session)
        await self.send("Page.handleJavaScriptDialog", {"accept": action == "accept", "promptText": text}, session)
        page.dialog = None
        page.invalidate()
        return "Dialog answered. Observe before continuing."

    async def upload(self, page: Page, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if not 1 <= len(paths) <= 16 or any(
            "\x00" in path
            or len(path) > 4096
            or not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
            for path in paths
        ):
            raise BrowserError("Supply 1-16 absolute file paths on the browser Host.")
        async with self.node(page, target, revision) as (node, frame, identity):
            checked = await self.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "returnByValue": True,
                    "functionDeclaration": "function(){return this.isConnected&&this.type==='file'&&!this.disabled&&!this.webkitdirectory?{multiple:this.multiple}:null;}",
                },
                frame,
            )
            field = checked.get("result", {}).get("value")
            if field is None or (len(paths) > 1 and not field["multiple"]):
                raise BrowserError(
                    "Select an enabled file input supporting this number of files. Directory uploads require takeover."
                )
            await self.send("DOM.setFileInputFiles", {"backendNodeId": node.backend, "files": list(paths)}, frame)
        return "Host files selected. Observe the form before submitting."

    async def new_tab(self, source: Page) -> Page:
        self.require_document(source)
        frames = await self.documents(source)
        root = next(frame for frame in frames if not frame.parent)
        world = await self.send("Page.createIsolatedWorld", {"frameId": root.identity, "worldName": "a13n-tab"}, root)
        name = "a13n-" + secrets.token_hex(12)
        before = {info["targetId"] for info in (await self.send("Target.getTargets"))["targetInfos"]}
        await self.send(
            "Runtime.evaluate",
            {
                "expression": f"!!window.open('about:blank',{json.dumps(name)})",
                "contextId": world["executionContextId"],
                "returnByValue": True,
                "userGesture": True,
            },
            root,
        )
        try:
            async with asyncio.timeout(5):
                while True:
                    infos = (await self.send("Target.getTargets"))["targetInfos"]
                    for info in infos:
                        if (
                            info["type"] == "page"
                            and info["targetId"] not in before
                            and info.get("openerId") == source.target
                        ):
                            page = await self.attach(info["targetId"])
                            if await self.evaluate(page, "window.name") == name:
                                if (await self.window(page))["windowId"] != (await self.window(source))["windowId"]:
                                    await self.close_page(page)
                                    raise BrowserError("The browser opened a different window instead of an owned tab.")
                                await self.evaluate(page, "window.name=''")
                                return page
                    await asyncio.sleep(0.025)
        except TimeoutError as exc:
            raise BrowserError(
                "Tab creation did not settle. List tabs before retrying; the tab may already be open."
            ) from exc

    async def screenshot(self, page: Page) -> bytes:
        self.require_document(page)
        result = await self.send("Page.captureScreenshot", {"format": "jpeg", "quality": 75}, page)
        return base64.b64decode(result["data"], validate=True)

    async def close_page(self, page: Page) -> None:
        await self.send("Target.closeTarget", {"targetId": page.target})
        self._destroyed({"targetId": page.target}, None)

    async def close_browser(self) -> None:
        """Explicit process-owner operation; disconnecting never calls this."""
        await self.send("Browser.close")

    async def release_keys(self, page: Page) -> None:
        for key, code, number in (
            ("Shift", "ShiftLeft", 16),
            ("Control", "ControlLeft", 17),
            ("Alt", "AltLeft", 18),
            ("Meta", "MetaLeft", 91),
        ):
            await self.send(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": number, "modifiers": 0},
                page,
            )

    async def act(
        self, page: Page, action: BrowserAction, *, target: str = "", value: str = "", revision: str = ""
    ) -> str:
        self.require_document(page)
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if action == "navigate":
            await self.navigate(page, value)
        elif action in {"click", "fill"}:
            await self.element(page, target, revision, fill=value if action == "fill" else None)
        elif action == "press":
            codes = {
                "Enter": 13,
                "Tab": 9,
                "Escape": 27,
                "Backspace": 8,
                "ArrowDown": 40,
                "ArrowUp": 38,
                "Space": 32,
            }
            if value not in codes:
                raise BrowserError("Unsupported browser key.")
            for kind in ("keyDown", "keyUp"):
                pending_dialog = await self.dispatch_input(
                    page,
                    "Input.dispatchKeyEvent",
                    {
                        "type": kind,
                        "key": " " if value == "Space" else value,
                        "windowsVirtualKeyCode": codes[value],
                        "text": "\r" if value == "Enter" and kind == "keyDown" else "",
                    },
                )
                if pending_dialog:
                    if kind == "keyDown":
                        self.background(
                            self.send(
                                "Input.dispatchKeyEvent",
                                {"type": "keyUp", "key": value, "windowsVirtualKeyCode": codes[value]},
                                page,
                            )
                        )
                    break
        elif action == "wait":
            try:
                async with asyncio.timeout(10):
                    if target:
                        async with self.node(page, target, revision) as (_, frame, identity):
                            while True:
                                self.reference(page, target, revision)
                                response = await self.send(
                                    "Runtime.callFunctionOn",
                                    {
                                        "objectId": identity,
                                        "returnByValue": True,
                                        "functionDeclaration": """function(state){const r=this.getBoundingClientRect();
                                    const visible=this.isConnected&&r.width>0&&r.height>0&&getComputedStyle(this).visibility!=='hidden';
                                    return state==='hidden'?!visible:state==='enabled'?visible&&!this.disabled:visible;}""",
                                        "arguments": [{"value": value}],
                                    },
                                    frame,
                                )
                                if response.get("result", {}).get("value"):
                                    break
                                await asyncio.sleep(0.1)
                    else:
                        while True:
                            found = False
                            for frame in await self.documents(page):
                                world = await self.send(
                                    "Page.createIsolatedWorld",
                                    {"frameId": frame.identity, "worldName": "a13n-wait"},
                                    frame,
                                )
                                response = await self.send(
                                    "Runtime.evaluate",
                                    {
                                        "expression": f"document.body?.innerText.includes({json.dumps(value)}) || false",
                                        "returnByValue": True,
                                        "contextId": world["executionContextId"],
                                    },
                                    frame,
                                )
                                if response.get("result", {}).get("value"):
                                    found = True
                                    break
                            if found:
                                break
                            await asyncio.sleep(0.1)
            except TimeoutError as exc:
                raise BrowserError("Timed out waiting for the page condition. Observe before continuing.") from exc
        if page.dialog:
            return (
                f"Browser {action} dispatched. JavaScript dialog pending; use browser_dialog, then observe the outcome."
            )
        return f"Browser {action} completed. Observe to verify the result."

    async def close(self) -> None:
        """Disconnect this client; tabs and the browser process remain Host-owned."""
        self.disconnected = True
        for scope in self.scopes:
            scope.retire()
        self.scopes.clear()
        self.owners.clear()
        for page in self.pages.values():
            page.invalidate()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.pages.clear()
        self.attach_locks.clear()
        await self.client.stop()
