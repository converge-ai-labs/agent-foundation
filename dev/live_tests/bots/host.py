"""Redirect only native Slack and Feishu HTTP transport to the lab-owned TLS peer.

Production clients still serialize requests, parse receipts, and use their fixed
platform origins. The override exists only in explicitly selected test processes.
"""

from contextlib import asynccontextmanager

import httpx2
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from a13n_service.connectivity.providers.slack.client import SlackNativeClient

from ..infrastructure.fixture_peer import certificate_context


class BotPeerTransport(httpx2.AsyncBaseTransport):
    def __init__(self, config):
        self.origin = httpx2.URL(config["peer_url"])
        if self.origin.scheme != "https" or self.origin.host != "127.0.0.1":
            raise ValueError("Bot fixture requires an owned loopback TLS peer")
        self.transport = httpx2.AsyncHTTPTransport(verify=certificate_context(config))

    async def handle_async_request(self, request):
        allowed = {"slack.com": "/api/", "open.feishu.cn": "/open-apis/"}
        prefix = allowed.get(request.url.host)
        if request.url.scheme != "https" or prefix is None or not request.url.path.startswith(prefix):
            raise AssertionError("Native Bot attempted an unexpected upstream")
        url = request.url.copy_with(scheme=self.origin.scheme, host=self.origin.host, port=self.origin.port)
        headers = request.headers.copy()
        headers["host"] = self.origin.netloc.decode("ascii")
        forwarded = httpx2.Request(
            request.method, url, headers=headers, stream=request.stream, extensions=request.extensions
        )
        return await self.transport.handle_async_request(forwarded)

    async def aclose(self):
        await self.transport.aclose()


class BotHost:
    def __init__(self, config):
        self.http = httpx2.AsyncClient(transport=BotPeerTransport(config), timeout=10, trust_env=False)

    def install(self, app):
        lifespan = app.router.lifespan_context
        originals = {kind: kind.__init__ for kind in (SlackNativeClient, LarkNativeClient, LarkTenantTokenProvider)}
        http = self.http

        def initializer(original):
            def initialize(client, _http, *args, **options):
                original(client, http, *args, **options)

            return initialize

        @asynccontextmanager
        async def with_peer(app):
            for kind, original in originals.items():
                kind.__init__ = initializer(original)
            try:
                async with http, lifespan(app) as state:
                    yield state
            finally:
                for kind, original in originals.items():
                    kind.__init__ = original

        app.router.lifespan_context = with_peer
