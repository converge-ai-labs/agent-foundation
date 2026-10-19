"""Model-visible Service identity, separate from the embedded Harness execution."""

import json
from dataclasses import dataclass

from a13n_harness import AgentContext
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)


@dataclass(frozen=True, slots=True)
class ModelContext:
    run_id: str

    async def wrap_model_context(
        self,
        ctx: AgentContext,
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        payload = json.dumps({"service_run_id": self.run_id})
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="a13n.service-context",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=(
                        '<service-context source="a13n-service">\n'
                        f"{payload}\n"
                        "Service queries use service_run_id as run_id; harness_run_id is internal to Harness.\n"
                        "</service-context>\n"
                        "Automatic metadata, not user input: continue only unfinished work; do not acknowledge."
                    ),
                ),
            )
        )
