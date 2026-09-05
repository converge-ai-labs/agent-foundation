from __future__ import annotations

from contextlib import nullcontext

import pytest
from pydantic_ai import prices


@pytest.fixture(autouse=True)
def no_background_price_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "update_in_background", nullcontext)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
