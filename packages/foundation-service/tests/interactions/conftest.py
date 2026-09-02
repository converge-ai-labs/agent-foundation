from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_service.agents.domain import (
    AgentConfig,
    EffectiveAgentConfig,
    ResolvedAgentModel,
    canonical_digest,
)
from a13n_service.interactions import HostContinuationState, RunStateEnvelope
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.storage.object_store import LocalObjectStore

RUN_ID = "run_1234567890abcdef"
AGENT_ID = "agt_1234567890abcdef"
AGENT_REVISION_ID = "agtr_1234567890abcdef"
MODEL_ID = "mdl_1234567890abcdef"
MODEL_REVISION_ID = "mdlr_1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
TENANT_ID = "org_1234567890abcdef"


def effective_agent_config() -> EffectiveAgentConfig:
    base = AgentConfig.model_validate(
        {
            "model": {
                "model_revision_id": MODEL_REVISION_ID,
                "settings": {"temperature": 0.2},
                "characteristics": {"context_window": 128000},
            },
            "instructions": "Be helpful.",
            "input_adapter": {"adapter_key": "native", "config": {}},
            "protocol": {
                "schema_version": "1",
                "public_name": "Support",
                "output_modes": ["text"],
                "limits": {},
            },
        }
    )
    execution = ModelExecutionSnapshot(
        model_id=MODEL_ID,
        model_revision_id=MODEL_REVISION_ID,
        provider_type="openai",
        model_name="gpt-5.6-terra",
        base_url=None,
        credential={"source": "none"},
        provider_config={},
        adapter_key="openai",
        adapter_version="1",
    )
    candidate = EffectiveAgentConfig(
        resolved_model=ResolvedAgentModel(
            execution=execution,
            settings=base.model.settings,
            characteristics=base.model.characteristics,
        ),
        runtime_lock_digest="a" * 64,
        instructions=base.instructions,
        input_adapter=base.input_adapter,
        client_tools=base.client_tools,
        output_spec=base.output_spec,
        retries=base.retries,
        secret_requirements=base.secret_requirements,
        asset_publication=base.asset_publication,
        protocol=base.protocol,
        content_digest="0" * 64,
    )
    payload = candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return candidate.model_copy(update={"content_digest": canonical_digest(payload)})


def initial_state() -> RunStateEnvelope:
    harness = HarnessState.new()
    return RunStateEnvelope(
        run_id=RUN_ID,
        thread_id=harness.thread_id,
        checkpoint_seq=0,
        checkpoint_kind="initial",
        input_disposition="pending",
        last_checkpoint_run_attempt_id=None,
        last_checkpoint_fence=0,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
        runtime_lock_digest="a" * 64,
        harness_schema_version="1",
        harness=harness,
        host=HostContinuationState(),
        outcome_candidate=None,
    )


def progress_state(
    previous: RunStateEnvelope,
    *,
    fence: int = 1,
    run_attempt_id: str = ATTEMPT_ID,
) -> RunStateEnvelope:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=previous.checkpoint_seq + 1,
        checkpoint_kind="progress",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=fence,
        outcome_candidate=None,
    )
    return RunStateEnvelope.model_validate(payload)


@pytest.fixture
async def interaction_object_store(tmp_path: Path):
    return await LocalObjectStore.create(tmp_path / "interaction-objects")
