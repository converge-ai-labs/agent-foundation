"""Smoke shortens waits without changing recovery semantics or other Hosts."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_harness import AgentDefinition, AgentSpec, ModelRecoveryPolicy
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_service.agents.reconstruction import AgentReconstructor

from ..infrastructure import host, smoke_host


@pytest.mark.parametrize("enabled,attempts", [(False, 1), (True, 5)])
def test_smoke_preserves_reconstructed_definition_and_attempt_budget(monkeypatch, enabled, attempts):
    definition = AgentDefinition(
        agent=AgentSpec(model="openai:live-fixture"),
        output_type=str,
        model_recovery=ModelRecoveryPolicy(enabled=enabled, max_attempts=attempts),
    )
    monkeypatch.setattr(AgentReconstructor, "_definition", lambda *args, **kwargs: definition)
    reconstructor = smoke_host.SmokeReconstructor(build_harness_plugin_factory_catalog())
    shortened = reconstructor._definition()
    assert replace(shortened, model_recovery=definition.model_recovery) == definition
    assert shortened.model_recovery == replace(
        definition.model_recovery, backoff_initial_seconds=0.01, backoff_max_seconds=0.05
    )
    assert definition.model_recovery.backoff_initial_seconds == 1
    assert definition.model_recovery.backoff_max_seconds == 30


@pytest.mark.parametrize("smoke,role", [(False, "worker"), (True, "control"), (True, "worker")])
def test_only_explicit_smoke_workers_install_short_backoff(monkeypatch, smoke, role):
    install = Mock()
    monkeypatch.setattr(smoke_host, "install", install)
    # Stop before application construction; this check must not need services.
    settings = SimpleNamespace(objects=SimpleNamespace(backend=host.ObjectBackend.local))
    monkeypatch.setattr(host, "settings_for", lambda *args: settings)
    with pytest.raises(RuntimeError, match="shared S3"):
        host.local_app({"smoke": smoke}, role)
    assert install.call_count == int(smoke and role == "worker")
