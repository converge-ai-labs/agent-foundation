"""Compose a host's direct transport with native environment proxy routing."""

from typing import Any

import httpx2


class EnvironmentProxyClient(httpx2.AsyncClient):
    """Use the host transport for direct/NO_PROXY routes, native transports for proxies.

    Operator-configured proxies own final DNS and destination network policy.
    httpx2 owns environment parsing, bypass matching, proxy TLS and lifecycle.
    """

    def __init__(self, direct_transport: httpx2.AsyncBaseTransport, **kwargs: Any) -> None:
        self._direct_transport = direct_transport
        # Passing transport= disables httpx2 environment proxy discovery.
        # Supply only its direct transport through the construction hook instead.
        super().__init__(**kwargs)

    def _init_transport(self, *args: Any, **kwargs: Any) -> httpx2.AsyncBaseTransport:
        return self._direct_transport
