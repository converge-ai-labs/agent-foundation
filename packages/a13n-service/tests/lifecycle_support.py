"""Explicit lifecycle delivery composition for interaction integration tests."""

from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.interactions.lifecycle import LifecycleWriter


def test_lifecycle_writer(*, a2a_enabled: bool = True) -> LifecycleWriter:
    return LifecycleWriter((append_matching_a2a_push_outbox,) if a2a_enabled else ())


test_lifecycle_writer.__test__ = False
