"""Single-action orchestration over Chromium's scoped page primitives."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import BrowserError, BrowserQuery
from .actions import (
    Action,
    Check,
    Click,
    Condition,
    ElementCondition,
    Fill,
    Hover,
    LoadCondition,
    Locator,
    Press,
    Ref,
    Scroll,
    Select,
    Target,
    TextCondition,
    TypeText,
    UrlCondition,
    Visual,
)
from .cdp import Node

if TYPE_CHECKING:
    from .cdp import Chromium, Page


async def matches(browser: Chromium, page: Page, target: Locator) -> set[Node]:
    frames, rows = await browser.semantic(page, BrowserQuery(role=target.role, name=target.name, text=target.text))
    if target.frame_index is not None:
        if target.frame_index >= len(frames):
            raise BrowserError("Locator frame is unavailable. Observe again.")
        rows = [(frame, row) for frame, row in rows if frame == frames[target.frame_index]]
    return {Node(row.backend, frame.identity, frame.session) for frame, row in rows if row.backend}


async def resolve(browser: Chromium, page: Page, target: Target, revision: str) -> tuple[str, str]:
    if isinstance(target, Ref):
        browser.reference(page, target.ref, revision)
        return target.ref, revision
    nodes = await matches(browser, page, target)
    if len(nodes) != 1:
        raise BrowserError(
            f"Locator matched {len(nodes)} targets; require exactly one. Narrow role/name/text/frame_index."
        )
    if not page.revision:
        page.revision = secrets.token_hex(12)
    key = secrets.token_hex(8)
    page.elements[key] = nodes.pop()
    return key, page.revision


async def state(browser: Chromium, page: Page, target: str, revision: str, expected: str | None = None) -> dict:
    async with browser.node(page, target, revision) as (_, frame, identity):
        response = await browser.send(
            "Runtime.callFunctionOn",
            {
                "objectId": identity,
                "returnByValue": True,
                "functionDeclaration": """function(expected){
                const value=this.value??this.innerText??'';
                return {connected:this.isConnected,type:this.type||this.tagName.toLowerCase(),
                    value:this.type==='password'?'[redacted]':String(value).slice(0,1000),
                    matches:expected===null?null:value===expected,
                    checked:typeof this.checked==='boolean'?this.checked:this.getAttribute('aria-checked'),
                    disabled:this.matches(':disabled')||this.getAttribute('aria-disabled')==='true',
                    readonly:!!this.readOnly,
                    selected:this.tagName==='SELECT'?Array.from(this.selectedOptions).map(o=>o.value):null};
            }""",
                "arguments": [{"value": expected}],
            },
            frame,
        )
        result = response.get("result", {}).get("value")
        if not isinstance(result, dict):
            raise BrowserError("Target state is unavailable. Observe again.")
        return result


async def focus(
    browser: Chromium, page: Page, target: str, revision: str, *, editable: bool = False, require_current: bool = False
) -> None:
    async with browser.node(page, target, revision) as (_, frame, identity):
        if not require_current:
            await browser.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "functionDeclaration": "function(){this.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}",
                },
                frame,
            )
        response = await browser.send(
            "Runtime.callFunctionOn",
            {
                "objectId": identity,
                "returnByValue": True,
                "functionDeclaration": """function(editable,requireCurrent){
                if(!this.isConnected||this.matches(':disabled')||!this.checkVisibility({visibilityProperty:true}))return false;
                for(let n=this;n;n=n.parentElement||n.getRootNode()?.host){if(n.inert)return false;}
                if(editable&&(this.readOnly||!(this.isContentEditable||this.tagName==='TEXTAREA'||
                    this.tagName==='INPUT'&&['text','search','tel','url','email','password','number'].includes(this.type))))return false;
                if(!requireCurrent)this.focus({preventScroll:true});let active=this.ownerDocument.activeElement;
                while(active?.shadowRoot?.activeElement)active=active.shadowRoot.activeElement;
                return active===this;
            }""",
                "arguments": [{"value": editable}, {"value": require_current}],
            },
            frame,
        )
        browser.reference(page, target, revision)
        if response.get("result", {}).get("value") is not True:
            raise BrowserError("Target cannot receive keyboard input; remaining keys were not sent.")


async def press(browser: Chromium, page: Page, chord: str, *, literal: bool = False) -> None:
    modifiers = {"Alt": (1, 18), "Control": (2, 17), "Meta": (4, 91), "Shift": (8, 16)}
    aliases = {"Ctrl": "Control", "Cmd": "Meta", "Command": "Meta"}
    parts = [chord] if literal else chord.split("+")
    held = [aliases.get(part, part) for part in parts[:-1]]
    if any(part not in modifiers for part in held) or len(set(held)) != len(held):
        raise BrowserError("Use a key or chord such as Enter, Shift+Tab, Control+A or Meta+A.")
    key = parts[-1]
    codes = {
        "Enter": 13,
        "Tab": 9,
        "Escape": 27,
        "Backspace": 8,
        "Delete": 46,
        "ArrowLeft": 37,
        "ArrowUp": 38,
        "ArrowRight": 39,
        "ArrowDown": 40,
        "Home": 36,
        "End": 35,
        "PageUp": 33,
        "PageDown": 34,
        "Space": 32,
    }
    if key not in codes and len(key) != 1:
        raise BrowserError("Unsupported browser key.")
    # Character code points are not virtual key codes (for example, '!' is not PageUp).
    number = codes.get(key, ord(key.upper()) if len(key) == 1 and key.isascii() and key.isalnum() else 0)
    key = " " if key == "Space" else key
    if "Shift" in held and len(key) == 1 and key.isascii():
        key = key.upper().translate(str.maketrans("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?'))
    mask = 0
    pressed = []
    try:
        for modifier in held:
            flag, code = modifiers[modifier]
            mask |= flag
            pressed.append((modifier, code))
            await browser.dispatch_input(
                page,
                "Input.dispatchKeyEvent",
                {"type": "rawKeyDown", "key": modifier, "windowsVirtualKeyCode": code, "modifiers": mask},
            )
        pressed.append((key, number))
        text = ("\r" if key == "Enter" else key if len(key) == 1 else "") if not mask & 7 else ""
        await browser.dispatch_input(
            page,
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": key, "windowsVirtualKeyCode": number, "modifiers": mask, "text": text},
        )
    finally:
        failures = []
        for released, code in reversed(pressed):
            if released in modifiers:
                mask &= ~modifiers[released][0]
            try:
                await browser.dispatch_input(
                    page,
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": released, "windowsVirtualKeyCode": code, "modifiers": mask},
                )
            except BrowserError as exc:
                failures.append(f"{released}: {exc}")
        if failures:
            raise BrowserError("Key release failed: " + "; ".join(failures), dispatch="unknown")


async def interact(browser: Chromium, page: Page, action: Action, *, revision: str = "") -> str:
    target, temporary, dispatch = "", False, "not_dispatched"
    initial_navigation = page.navigation
    result: dict = {"dispatch": dispatch, "verification": "unverified"}
    try:
        browser.require_document(page)
        if page.requires_observation:
            raise BrowserError("Observe after handback before continuing.")
        if action.target is not None and not isinstance(action.target, Visual):
            target, revision = await resolve(browser, page, action.target, revision)
            temporary = isinstance(action.target, Locator)
        if isinstance(action, (Click, Hover)) and isinstance(action.target, Visual):
            from .visual import pointer

            await pointer(browser, page, action, action.target)
            dispatch = "dispatched"
        elif isinstance(action, Scroll):
            result["detail"] = await browser.scroll(page, x=action.x, y=action.y, target=target, revision=revision)
            dispatch = "dispatched"
            result["verification"] = "passed"
        elif isinstance(action, Press):
            if target:
                await focus(browser, page, target, revision)
            await press(browser, page, action.key)
            dispatch = "dispatched"
        elif isinstance(action, TypeText):
            await focus(browser, page, target, revision, editable=True)
            for character in action.text:
                await focus(browser, page, target, revision, editable=True, require_current=True)
                await press(
                    browser,
                    page,
                    "Enter" if character == "\n" else "Space" if character == " " else character,
                    literal=True,
                )
                dispatch = "dispatched"
        elif isinstance(action, (Click, Hover, Fill)):
            if isinstance(action, Click):
                for count in range(1, action.count + 1):
                    await browser.element(page, target, revision, button=action.button, count=count)
                    dispatch = "dispatched"
            else:
                await browser.element(
                    page,
                    target,
                    revision,
                    fill=action.text if isinstance(action, Fill) else None,
                    hover=isinstance(action, Hover),
                )
                dispatch = "dispatched"
        elif isinstance(action, Check):
            before = await state(browser, page, target, revision)
            if (
                before["disabled"]
                or before["readonly"]
                or before["checked"] not in (True, False, "true", "false", "mixed")
            ):
                raise BrowserError("Check requires an enabled checkbox, radio or ARIA checkable control.")
            if before["checked"] not in (action.checked, str(action.checked).lower()):
                await browser.element(page, target, revision)
                dispatch = "dispatched"
        elif isinstance(action, Select):
            async with browser.node(page, target, revision) as (_, frame, identity):
                browser.reference(page, target, revision)
                response = await browser.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": identity,
                        "returnByValue": True,
                        "functionDeclaration": """function(values,by){
                        if(!this.isConnected||this.tagName!=='SELECT'||this.matches(':disabled')||this.closest('[inert]'))return {error:'Select requires an enabled native select.'};
                        if(!this.multiple&&values.length!==1)return {error:'Single select requires one option.'};
                        const selected=[];
                        for(const value of values){const matches=Array.from(this.options).filter(o=>o[by]===value);
                            if(matches.length!==1||matches[0].disabled||matches[0].parentElement.disabled)return {error:'Option is missing, ambiguous or disabled.'};
                            selected.push(matches[0]);}
                        for(const option of this.options)option.selected=selected.includes(option);
                        this.dispatchEvent(new Event('input',{bubbles:true}));this.dispatchEvent(new Event('change',{bubbles:true}));
                        return {values:selected.map(o=>o.value)};
                    }""",
                        "arguments": [{"value": action.values}, {"value": action.by}],
                    },
                    frame,
                )
                selected = response.get("result", {}).get("value")
                if not isinstance(selected, dict):
                    raise BrowserError("Select outcome is unknown. Read the form before retrying.", dispatch="unknown")
                if "error" in selected:
                    raise BrowserError(selected["error"])
                dispatch = "dispatched"
                result["selected"] = selected["values"]
        if target in page.elements and page.revision == revision and not page.dialog:
            after = await state(browser, page, target, revision, action.text if isinstance(action, Fill) else None)
            result["state"] = after
            if isinstance(action, Fill):
                result["verification"] = "passed" if after["matches"] else "failed"
            elif isinstance(action, Check):
                result["verification"] = (
                    "passed" if after["checked"] in (action.checked, str(action.checked).lower()) else "failed"
                )
            elif isinstance(action, Select):
                result["verification"] = "passed" if set(after["selected"]) == set(result["selected"]) else "failed"
    except BrowserError as exc:
        result["error"] = str(exc)
        if dispatch == "not_dispatched":
            dispatch = exc.dispatch
    finally:
        page.visual = None
        if temporary:
            page.elements.pop(target, None)
    result.update(
        dispatch=dispatch,
        url=page.url,
        navigated=page.navigation != initial_navigation,
        dialog_pending=page.dialog is not None,
    )
    return json.dumps(result, ensure_ascii=False)


async def condition_met(browser: Chromium, page: Page, condition: Condition, revision: str) -> bool:
    browser.require_document(page)
    if isinstance(condition, UrlCondition):
        return condition.text in page.url
    if isinstance(condition, LoadCondition):
        ready = await browser.evaluate(page, "document.readyState")
        return ready == "complete" or (condition.state == "interactive" and ready == "interactive")
    if isinstance(condition, TextCondition):
        found = False
        for frame in await browser.documents(page):
            world = await browser.send(
                "Page.createIsolatedWorld", {"frameId": frame.identity, "worldName": "a13n-wait"}, frame
            )
            response = await browser.send(
                "Runtime.evaluate",
                {
                    "expression": "(document.body?.innerText||'').includes(" + json.dumps(condition.text) + ")",
                    "contextId": world["executionContextId"],
                    "returnByValue": True,
                },
                frame,
            )
            found |= response.get("result", {}).get("value") is True
        return found == condition.present
    target = condition.target
    temporary = isinstance(target, Locator)
    if temporary:
        nodes = await matches(browser, page, target)
        if not nodes:
            return condition.state == "hidden"
        if len(nodes) != 1:
            raise BrowserError("Wait locator is ambiguous; narrow role/name/text/frame_index.")
        if not page.revision:
            page.revision = secrets.token_hex(12)
        revision = page.revision
        key = secrets.token_hex(8)
        page.elements[key] = nodes.pop()
    else:
        key = target.ref
    try:
        async with browser.node(page, key, revision) as (_, frame, identity):
            response = await browser.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": identity,
                    "returnByValue": True,
                    "functionDeclaration": """function(state){
                    const visible=this.isConnected&&this.checkVisibility({visibilityProperty:true});
                    let disabled=this.matches(':disabled')||this.getAttribute('aria-disabled')==='true';
                    for(let n=this;n;n=n.parentElement||n.getRootNode()?.host){disabled ||= !!n.inert;}
                    const checked=typeof this.checked==='boolean'?this.checked:this.getAttribute('aria-checked');
                    return state==='hidden'?!visible:state==='visible'?visible:
                        state==='enabled'?visible&&!disabled:state==='disabled'?visible&&disabled:
                        state==='checked'?visible&&(checked===true||checked==='true'):
                        visible&&(checked===false||checked==='false');
                 }""",
                    "arguments": [{"value": condition.state}],
                },
                frame,
            )
        browser.reference(page, key, revision)
        return response.get("result", {}).get("value") is True
    finally:
        if temporary:
            page.elements.pop(key, None)


async def wait(
    browser: Chromium, page: Page, condition: Condition, *, revision: str = "", check_access: Callable[[], None]
) -> str:
    while True:
        check_access()
        epoch = page.epoch
        try:
            met = await condition_met(browser, page, condition, revision)
        except BrowserError:
            # Only repeat a read-only poll disrupted by a document event.
            # A supplied ref cannot be rediscovered after it expires.
            if page.epoch == epoch or (isinstance(condition, ElementCondition) and isinstance(condition.target, Ref)):
                raise
            met = False
        check_access()
        # Element polls revalidate their exact frame-bound ref after reading.
        # Unrelated subframe events must not discard a verified result.
        if met and (isinstance(condition, ElementCondition) or page.epoch == epoch):
            return json.dumps({"verification": "passed", "dispatch": "not_dispatched", "url": page.url})
        await asyncio.sleep(0.1)
