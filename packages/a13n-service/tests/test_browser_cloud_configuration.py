"""Hermetic checks for explicitly selected cloud-test credentials."""

import pytest

from . import conftest


def test_cloud_credentials_use_selected_checkout_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A13N_CLOUD_BROWSER_TEST", "browserbase")
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    seen = []

    def values(path, *, interpolate):
        seen.append((path, interpolate))
        return {"BROWSERBASE_API_KEY": "fixture", "KERNEL_API_KEY": "other"}

    monkeypatch.setattr("dotenv.dotenv_values", values)
    provider, credential = conftest.cloud_browser_credentials.__wrapped__()
    assert provider == "browserbase"
    assert credential.get_secret_value() == "fixture"
    assert seen == [(conftest.Path(__file__).resolve().parents[3] / ".env", False)]


def test_cloud_credentials_preserve_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A13N_CLOUD_BROWSER_TEST", "kernel")
    monkeypatch.setenv("KERNEL_API_KEY", "inherited")
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **kw: pytest.fail("must not read .env"))
    provider, credential = conftest.cloud_browser_credentials.__wrapped__()
    assert provider == "kernel"
    assert credential.get_secret_value() == "inherited"


def test_cloud_credentials_fail_for_missing_selection_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A13N_CLOUD_BROWSER_TEST", "invalid")
    with pytest.raises(pytest.UsageError, match="browserbase, kernel or browser_use"):
        conftest.cloud_browser_credentials.__wrapped__()
    monkeypatch.setenv("A13N_CLOUD_BROWSER_TEST", "kernel")
    monkeypatch.delenv("KERNEL_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **kw: {})
    with pytest.raises(pytest.UsageError, match="KERNEL_API_KEY"):
        conftest.cloud_browser_credentials.__wrapped__()


def test_cloud_definitions_available_without_local_browser_root() -> None:
    from a13n_service.providers.browser.builtins import builtins
    from a13n_service.settings import Browsers

    definitions = {definition.type: definition for definition in builtins(Browsers(root=None))}
    assert set(definitions) == {"browserbase", "kernel", "browser_use"}
    for definition in definitions.values():
        definition.validate_domain()
        assert definition.credential_model is not None
        assert not definition.supports_stop and not definition.supports_viewer and not definition.supports_files
    bb = definitions["browserbase"]
    config = bb.configuration_model.model_validate({"project_id": "project"})
    assert bb.backend_identity(config) == {"project_id": "project"}
    kernel = definitions["kernel"]
    config = kernel.configuration_model.model_validate({"organization_id": "org"})
    assert kernel.backend_identity(config) == {"organization_id": "org", "project_id": None}
    browser_use = definitions["browser_use"]
    config = browser_use.configuration_model.model_validate({"project_id": "project"})
    assert browser_use.backend_identity(config) == {"project_id": "project"}


@pytest.mark.anyio
async def test_cloud_optional_operations_fail_explicitly() -> None:
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from a13n_service.providers.browser.cloud import (
        Browserbase,
        BrowserbaseConfiguration,
        CloudAPI,
        Kernel,
        KernelConfiguration,
    )

    providers = [
        Browserbase(BrowserbaseConfiguration(project_id="project"), MagicMock(spec=CloudAPI)),
        Kernel(KernelConfiguration(organization_id="org"), MagicMock(spec=CloudAPI)),
    ]
    for provider in providers:
        operations = [
            provider.stop("allocation", {}),
            provider.start("allocation", {}, generation=1),
            provider.ensure_viewer({}, "grant", episode=1, expires_at=datetime.now(UTC)),
            provider.revoke_viewer({}, "grant", episode=1),
            provider.upload_file({}, "file", b"data"),
            provider.download_file({}, "guid", max_bytes=1),
        ]
        for operation in operations:
            with pytest.raises(NotImplementedError, match="not supported"):
                await operation
