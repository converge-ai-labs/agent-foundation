from pathlib import Path

from a13n_service.process.runner_settings import runner_settings_payload
from a13n_service.settings import Settings
from pydantic import SecretStr


def test_runner_receives_complete_effective_settings_without_ambient_overrides(monkeypatch):
    settings = Settings(
        _env_file=None,
        plugin_runtime_mode="runner",
        auto_migrate=True,
        database_url="postgresql+psycopg://test:test-only@localhost/test",
        plugin_runtime_index_urls=("https://test:test-only@example.invalid/simple",),
        filesystem_root=Path("relative-runner-files"),
        worker_concurrency=3,
    )
    payload = runner_settings_payload(settings)
    assert set(payload) == set(Settings.model_fields)
    monkeypatch.setenv("FOUNDATION_WORKER_CONCURRENCY", "99")
    monkeypatch.setenv("FOUNDATION_PRICING_AUTO_UPDATE", "false")
    restored = Settings(**{"_env_file": None, **payload})
    assert restored.role == "worker"
    assert restored.auto_migrate is False
    assert settings.auto_migrate is True
    for name in Settings.model_fields:
        if name in {"role", "auto_migrate"}:
            continue
        original = getattr(settings, name)
        actual = getattr(restored, name)
        if isinstance(original, SecretStr):
            assert actual.get_secret_value() == original.get_secret_value()
        elif isinstance(original, Path):
            assert actual == original.resolve()
        else:
            assert actual == original
    assert restored.plugin_runtime_index_urls[0].get_secret_value() == "https://test:test-only@example.invalid/simple"
