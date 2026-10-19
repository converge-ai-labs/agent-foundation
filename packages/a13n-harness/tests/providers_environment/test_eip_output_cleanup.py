"""Deferred cleanup tolerates retired output, but preserves uncertain failures."""

from types import SimpleNamespace

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.eip.output import EIPOutputRegistry


@pytest.mark.anyio
async def test_pending_cleanup_settles_absence_and_keeps_unavailable_output(monkeypatch):
    registry = EIPOutputRegistry(
        session=SimpleNamespace(), environment_id="env-one", execution_id="exec-one", generation="generation-one"
    )
    # Wire references are opaque strings; cleanup must not reconstruct a producer.
    missing = eip.OutputReference("out-missing")
    uncertain = eip.OutputReference("out-uncertain")
    registry.defer_cleanup(missing)
    registry.defer_cleanup(uncertain)
    attempts = []

    async def release(reference):
        attempts.append(reference)
        raise EnvironmentError(
            "fixture", code="environment_not_found" if reference == missing else "environment_unavailable"
        )

    monkeypatch.setattr(registry, "release_raw", release)
    with pytest.raises(EnvironmentError) as error:
        await registry.cleanup_pending()
    assert error.value.code == "environment_unavailable"
    assert set(attempts) == {missing, uncertain}
    attempts.clear()
    with pytest.raises(EnvironmentError):
        await registry.cleanup_pending()
    assert attempts == [uncertain]
