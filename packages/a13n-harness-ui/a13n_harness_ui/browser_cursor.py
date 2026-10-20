"""A non-interactive Agent pointer rendered into the Host browser's VNC pixels."""

from __future__ import annotations

import asyncio
import base64
import secrets
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Page


@cache
def _logo() -> str:
    # The bundled installation icon uses the shared a13n mark without changing its geometry or colors.
    icon = files("a13n_harness_ui").joinpath("static/icons/icon-192.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(icon).decode("ascii")


_SHOW = """(target, {id, logo}) => {
    let host = document.getElementById(id);
    if (!host) {
        host = document.createElement('span');
        host.id = id;
        host.setAttribute('aria-hidden', 'true');
        host.inert = true;
        host.setAttribute('popover', 'manual');
        host.style.cssText = 'all:initial!important;position:fixed!important;inset:auto!important;'
            + 'margin:0!important;padding:0!important;border:0!important;width:28px!important;'
            + 'height:34px!important;overflow:visible!important;background:transparent!important;'
            + 'pointer-events:none!important;visibility:hidden!important;z-index:2147483647!important;';
        const root = host.attachShadow({mode: 'open'});
        root.innerHTML = `<style>
            :host { color-scheme: light; }
            .pointer { position:absolute; left:0; top:0; width:28px; height:34px; }
            svg { display:block; filter:drop-shadow(0 1px 2px #0006); }
            .brand { position:absolute; left:20px; top:24px; width:36px; height:36px;
                overflow:hidden; border:1px solid #e5e5ee; border-radius:11px;
                background:#fbfbfd; box-shadow:0 2px 6px #0002; }
            .brand img { display:block; width:36px; height:36px; }
            .pulse { position:absolute; left:-18px; top:-18px; width:36px; height:36px;
                border:2px solid #6366f1; border-radius:50%; opacity:0; }
            .pulse.active { animation:click 700ms ease-out; }
            @keyframes click { from { transform:scale(.35); opacity:.9; }
                to { transform:scale(1.6); opacity:0; } }
            @media (prefers-reduced-motion: reduce) {
                .pulse.active { animation:none; }
            }
        </style><span class="pulse"></span><span class="pointer">
        <svg width="28" height="34" viewBox="0 0 28 34" aria-hidden="true">
            <path d="M2 2 3 27 10 21 15 32 20 30 15 19 25 19Z"
                fill="#4f46e5" stroke="white" stroke-width="2" stroke-linejoin="round"/>
        </svg><span class="brand"><img src="${logo}" alt="" width="36" height="36"></span></span>`;
        document.documentElement.append(host);
        host.showPopover();
        const abort = new AbortController();
        const options = {capture:true, signal:abort.signal};
        host.addEventListener('a13n-remove', () => abort.abort(), {once:true});
        const pointer = root.querySelector('.pointer');
        const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
        let movement;
        const move = (x, y) => {
            const visible = host.style.visibility === 'visible';
            const previous = pointer.getBoundingClientRect();
            movement?.cancel();
            host.style.setProperty('left', `${x}px`, 'important');
            host.style.setProperty('top', `${y}px`, 'important');
            host.style.setProperty('visibility', 'visible', 'important');
            if (visible && !reducedMotion.matches && Math.hypot(previous.left - x, previous.top - y) > .5) {
                movement = pointer.animate([
                    {transform:`translate(${previous.left - x}px, ${previous.top - y}px)`},
                    {transform:'translate(0, 0)'}
                ], {duration:150, easing:'cubic-bezier(.2, 0, .2, 1)'});
            }
        };
        const focus = element => {
            if (!(element instanceof Element) || element === document.body) return;
            const rect = element.getBoundingClientRect();
            move(rect.left + Math.min(rect.width / 2, 24), rect.top + rect.height / 2);
        };
        host.addEventListener('a13n-target', event => focus(event.detail));
        document.addEventListener('mousemove', event => move(event.clientX, event.clientY), options);
        document.addEventListener('focusin', event => focus(event.target), options);
        document.addEventListener('click', event => {
            if (event.detail) move(event.clientX, event.clientY);
            else focus(event.target);
            const pulse = root.querySelector('.pulse');
            pulse.classList.remove('active');
            void pulse.offsetWidth;
            pulse.classList.add('active');
        }, options);
        document.addEventListener('scroll', () => {
            movement?.cancel();
            host.style.setProperty('visibility', 'hidden', 'important');
        }, options);
    }
    host.dispatchEvent(new CustomEvent('a13n-target', {detail:target}));
}"""

_HIDE = """id => {
    const host = document.getElementById(id);
    host?.dispatchEvent(new Event('a13n-remove'));
    host?.remove();
}"""


class Cursor:
    def __init__(self) -> None:
        self.id = "a13n-agent-cursor-" + secrets.token_hex(8)
        self.page: Page | None = None

    async def show(self, page: Page, target: ElementHandle | None = None) -> None:
        if self.page is not page:
            await self.hide()
        self.page = page
        try:
            async with asyncio.timeout(1):
                options = {"id": self.id, "logo": _logo()}
                if target is not None:
                    await target.scroll_into_view_if_needed(timeout=750)
                    await target.evaluate(_SHOW, options)
                else:
                    await page.evaluate(f"options => ({_SHOW})(document.activeElement, options)", options)
        except Exception:
            # Cosmetic feedback must not turn a successful browser operation into a failure.
            pass

    async def hide(self) -> None:
        page, self.page = self.page, None
        if page is not None:
            try:
                async with asyncio.timeout(1):
                    await page.evaluate(_HIDE, self.id)
            except Exception:
                # Navigation or renderer termination may already have removed the pointer.
                pass
