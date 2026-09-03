from __future__ import annotations

from io import BytesIO
from typing import Any, cast

import httpcore2
import pytest
from a13n_harness.capabilities import (
    DocumentConversionRequest,
    DocumentsRunCapability,
    WebProviderError,
    WebRunCapability,
)
from a13n_ui.capability_runtime import (
    LocalDocumentConverter,
    PublicWebPolicy,
    _PinnedNetworkBackend,
    production_run_capabilities,
)
from openpyxl import Workbook

pytestmark = pytest.mark.anyio


async def test_public_web_policy_rejects_loopback_destinations() -> None:
    policy = PublicWebPolicy()

    with pytest.raises(WebProviderError) as denied:
        await policy.authorize("http://127.0.0.1/private", purpose="fetch")

    assert denied.value.code == "web_destination_denied"


async def test_authorized_dns_result_is_pinned_for_the_actual_connection() -> None:
    connected: list[str] = []

    class _Backend:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any):
            del port, kwargs
            connected.append(host)
            return cast(httpcore2.AsyncNetworkStream, object())

    backend = _PinnedNetworkBackend()
    backend._delegate = cast(httpcore2.AsyncNetworkBackend, _Backend())
    backend.pin("public.example", 443, ("93.184.216.34",))

    await backend.connect_tcp("public.example", 443)

    assert connected == ["93.184.216.34"]


async def test_local_document_converter_converts_a_real_workbook() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Metrics"
    sheet.append(["name", "value"])
    sheet.append(["latency", 42])
    source = BytesIO()
    workbook.save(source)
    workbook.close()

    result = await LocalDocumentConverter().convert(
        DocumentConversionRequest(
            kind="office",
            source_name="metrics.xlsx",
            source_bytes=source.getvalue(),
            max_markdown_bytes=1024 * 1024,
            max_asset_bytes=1024 * 1024,
            max_total_asset_bytes=1024 * 1024,
            max_assets=10,
            deadline_seconds=30,
        )
    )

    assert "## Metrics" in result.markdown
    assert "| latency | 42 |" in result.markdown


def test_production_capabilities_bind_only_required_fresh_collaborators() -> None:
    none = production_run_capabilities(frozenset())
    web = production_run_capabilities(frozenset({"a13n.web"}))
    documents = production_run_capabilities(frozenset({"a13n.documents"}))

    assert none == ()
    assert len(web) == 1 and isinstance(web[0], WebRunCapability)
    assert len(documents) == 1 and isinstance(documents[0], DocumentsRunCapability)
    assert web[0] is not production_run_capabilities(frozenset({"a13n.web"}))[0]
