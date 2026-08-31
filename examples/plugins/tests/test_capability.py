from __future__ import annotations

import asyncio

import pytest
from a13n_harness import (
    AgentSpec,
    DefinitionError,
    HarnessBuilder,
)
from pydantic_ai.agent.spec import CapabilitySpec

from a13n_plugin_examples.capability import CAPABILITY_SERIALIZATION_NAME
from a13n_plugin_examples.demo_capability import run_capability_demo


def test_custom_capability_requires_host_catalog_authorization() -> None:
    agent_spec = AgentSpec(
        model="logical:test",
        capabilities=[
            CapabilitySpec(
                name=CAPABILITY_SERIALIZATION_NAME,
                arguments={"instructions": "Use the selected extension."},
            )
        ],
    )

    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder(configured_plugins_enabled=False).build(
            agent_spec,
            output_type=str,
        )
    assert exc_info.value.code == "agent_build_failed"


def test_agentspec_capability_demo_reaches_the_model() -> None:
    result = asyncio.run(run_capability_demo(selection_mode="agent-spec"))

    assert result.capability_name == CAPABILITY_SERIALIZATION_NAME
    assert result.instructions in result.model_instructions
    assert result.output == "offline capability response"


def test_code_capability_demo_reaches_the_model() -> None:
    result = asyncio.run(run_capability_demo(selection_mode="code"))

    assert result.capability_name == CAPABILITY_SERIALIZATION_NAME
    assert result.instructions in result.model_instructions
    assert result.output == "offline capability response"
