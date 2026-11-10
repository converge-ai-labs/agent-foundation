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

from a13n_service.infra.db import Storage
from a13n_service.runs.environments.desktop.context import project


@dataclass(frozen=True, slots=True)
class ModelContext:
    run_id: str
    storage: Storage

    async def wrap_model_context(
        self,
        ctx: AgentContext,
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        computer = await project(self.storage, ctx.environment.dump_states())
        payload = json.dumps({"service_run_id": self.run_id, **({"computer": computer} if computer else {})})
        block = ModelContextBlock(
            source_id="a13n.service-context",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content=f'<service-context source="a13n-service">\n{payload}\n</service-context>',
        )
        if request.history.latest(block.source_id, block.key) == block.content:
            return projection
        return ModelContextProjection(blocks=(*projection.blocks, block))
