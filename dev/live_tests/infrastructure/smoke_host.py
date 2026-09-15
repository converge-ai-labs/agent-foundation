"""Short model backoff in the explicitly selected smoke Worker only."""

import logging
from dataclasses import replace

from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.interactions import worker_preparation

logger = logging.getLogger("a13n_service.live_tests.smoke")


class SmokeReconstructor(AgentReconstructor):
    def _definition(self, *args, **kwargs):
        definition = super()._definition(*args, **kwargs)
        return replace(
            definition,
            model_recovery=replace(
                definition.model_recovery,
                backoff_initial_seconds=0.01,
                backoff_max_seconds=0.05,
            ),
        )


def install():
    # The same process-local Host seam is used by long_session_host. Production
    # entrypoints never import this module; root and child budgets remain intact.
    worker_preparation.AgentReconstructor = SmokeReconstructor
    logger.info(
        "live_smoke_model_backoff",
        extra={"event": "live_smoke_model_backoff", "backoff_initial_seconds": 0.01, "backoff_max_seconds": 0.05},
    )
