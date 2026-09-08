"""Small same-origin forms for local account initialization and login."""

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


def account_page(*, invitation: bool) -> HTMLResponse:
    nonce = secrets.token_urlsafe(24)
    title = "Accept invitation" if invitation else "Sign in"
    fields = (
        '<label>Name <input name="name" maxlength="128" autocomplete="name"></label>'
        if invitation
        else '<label>Email <input name="email" type="email" required autocomplete="username"></label>'
    )
    script = """
const form = document.querySelector('form');
const status = document.querySelector('[role=status]');
const token = new URLSearchParams(location.hash.slice(1)).get('token');
history.replaceState(null, '', location.pathname);
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  const body = Object.fromEntries(new FormData(form));
  if (token) body.token = token;
  if (body.name === '') delete body.name;
  try {
    const response = await fetch(location.pathname, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin', body: JSON.stringify(body)
    });
    form.reset();
    status.textContent = response.ok
      ? 'Signed in. You may close this page and return to your application.'
      : 'Unable to sign in. Check your credentials and invitation, then try again.';
  } catch {
    status.textContent = 'Unable to reach the service. Please try again.';
  } finally { button.disabled = false; }
});
"""
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><main><h1>{title}</h1>"
        "<p>Use your existing password if you already have an account. "
        "New passwords require 15-128 printable ASCII characters without spaces.</p>"
        f'<form>{fields}<label>Password <input name="password" type="password" '
        'required minlength="15" maxlength="128" autocomplete="current-password"></label>'
        f'<button>{title}</button></form><p role="status"></p></main>'
        f'<script nonce="{nonce}">{script}</script></html>',
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


@router.get("/api/v1/auth/login")
async def login_page() -> HTMLResponse:
    return account_page(invitation=False)


@router.get("/api/v1/invitations/{invitation_id}/accept")
async def invitation_page(invitation_id: str) -> HTMLResponse:
    return account_page(invitation=True)
