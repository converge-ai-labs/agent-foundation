"""Offline guards for the opt-in image journey and container endpoint translation."""

from types import SimpleNamespace

import pytest

from ..harness_integration import plugin_image, test_19_plugin_image


@pytest.mark.anyio
async def test_plugin_image_requires_opt_in_before_build_or_lab_start(monkeypatch):
    def forbidden():
        raise AssertionError("Offline selection must not build images or start infrastructure")

    monkeypatch.setattr(test_19_plugin_image, "plugin_image", forbidden)
    monkeypatch.setattr(test_19_plugin_image, "open_lab", forbidden)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda option: False))
    fixture = test_19_plugin_image.packaged_plugin.__wrapped__(request)
    with pytest.raises(pytest.skip.Exception, match="live-test-plugin-image"):
        await anext(fixture)


@pytest.mark.parametrize(
    ("platform", "url", "expected"),
    [
        ("linux", "http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1"),
        ("darwin", "http://127.0.0.1:8000/v1", "http://host.docker.internal:8000/v1"),
        ("darwin", "http://localhost/v1", "http://host.docker.internal/v1"),
        ("darwin", "redis://[::1]:6379/0", "redis://host.docker.internal:6379/0"),
        (
            "darwin",
            "postgresql+psycopg://fixture:fixture@127.0.0.1:5432/db",
            "postgresql+psycopg://fixture:fixture@host.docker.internal:5432/db",
        ),
        ("darwin", "https://objects.example.test:9443/bucket", "https://objects.example.test:9443/bucket"),
    ],
)
def test_container_endpoint_preserves_credentials_ports_and_paths(monkeypatch, platform, url, expected):
    monkeypatch.setattr(plugin_image.sys, "platform", platform)
    assert plugin_image.container_url(url) == expected
