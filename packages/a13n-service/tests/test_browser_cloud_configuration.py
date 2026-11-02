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
    with pytest.raises(pytest.UsageError, match="browserbase or kernel"):
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
    assert set(definitions) == {"browserbase", "kernel"}
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
