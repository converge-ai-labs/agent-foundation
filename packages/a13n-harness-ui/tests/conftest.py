from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic_ai import prices


@pytest.fixture(autouse=True)
def isolated_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("GROK_HOME", str(home / ".grok"))
    monkeypatch.delenv("GROK_AUTH_PATH", raising=False)
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))


@pytest.fixture(autouse=True)
def no_background_price_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "update_in_background", nullcontext)
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")


@pytest.fixture(autouse=True)
def no_model_directory_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui import model_catalog

    async def bundled():
        return model_catalog.bundled_models()

    monkeypatch.setattr(model_catalog, "fetch_directory", bundled)


@pytest.fixture(autouse=True)
def no_background_subscription_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage
    from anyio import sleep_forever

    async def idle(self) -> None:
        await sleep_forever()

    async def unavailable(self, account):
        raise ValueError("Subscription usage network is disabled in unit tests")

    monkeypatch.setattr(SubscriptionUsage, "poll", idle)
    monkeypatch.setattr(SubscriptionUsage, "_read_provider", unavailable)


@pytest.fixture(autouse=True)
def no_automatic_host_browser(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Native browser scenarios opt in themselves; ordinary App tests never launch Host programs."""
    if request.node.path.stem in {"test_host_browser_linux", "test_host_browser_macos"}:
        return
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.browser import BrowserError
    from a13n_harness_ui import browser

    monkeypatch.setattr(
        browser, "require_browser", AsyncMock(side_effect=BrowserError("Host components are unavailable in unit tests"))
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def terminal_app():
    """App contract for terminal-only tests, including the live input subscription."""
    from unittest.mock import Mock

    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.live import HarnessUiSummaryHub

    app = Mock(spec=HarnessUiApp)
    app.summary_events = HarnessUiSummaryHub(epoch="terminal-test").subscribe
    return app
