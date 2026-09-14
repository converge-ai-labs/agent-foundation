"""Small protocol handoff page, independent of the Console application."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter()
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
}


@router.get("/connection-authorizations/browser", include_in_schema=False)
async def authorization_browser() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Connection authorization</title><body><main><h1>Connection authorization</h1><p id="status" role="status">Continuing authorization…</p></main><script src="/connection-authorizations/browser.js" defer></script></body></html>""",
        headers=_HEADERS,
    )


@router.get("/connection-authorizations/browser.js", include_in_schema=False)
async def authorization_browser_script() -> Response:
    return Response(_SCRIPT, media_type="text/javascript", headers=_HEADERS)


_SCRIPT = r"""
(async () => {
  const status = document.getElementById('status');
  const storageKey = 'a13n.connection-browser-binding';
  const launch = new URLSearchParams(location.hash.slice(1));
  const response = new URLSearchParams(location.search);
  history.replaceState(null, '', location.pathname);
  async function post(id, command, body) {
    const result = await fetch('/api/v1/connection-authorizations/' + encodeURIComponent(id) + '/' + command, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'omit',
      body: JSON.stringify(body), redirect: 'error', cache: 'no-store',
    });
    if (!result.ok) throw new Error('Authorization could not continue. Return to your application and check its status.');
    return result.json();
  }
  try {
    const id = launch.get('authorization_id');
    const token = launch.get('token');
    if (id && token) {
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      const browser_nonce = Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
      sessionStorage.setItem(storageKey, JSON.stringify({id, browser_nonce}));
      const result = await post(id, 'launch', {token, browser_nonce});
      location.replace(result.url);
      return;
    }
    const context = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (!context || typeof context.id !== 'string' || typeof context.browser_nonce !== 'string') {
      throw new Error('Open the authorization link from your application in this tab.');
    }
    if (response.has('error') || response.getAll('session_uri').length > 1) {
      throw new Error('Authorization was not completed. Return to your application.');
    }
    const body = {browser_nonce: context.browser_nonce};
    const sessionUri = response.get('session_uri');
    if (sessionUri) body.session_uri = sessionUri;
    const result = await post(context.id, 'receive', body);
    sessionStorage.removeItem(storageKey);
    location.replace(result.url);
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : 'Authorization could not continue.';
  }
})();
"""
