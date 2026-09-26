from __future__ import annotations

import json

import pytest
from a13n_harness import AgentSpec, HarnessModelCharacteristics, ModelCapability


@pytest.mark.parametrize(
    "capabilities",
    [(), (ModelCapability.VIDEO_UNDERSTANDING,), tuple(ModelCapability)],
)
def test_capabilities_have_canonical_json_without_changing_python_sets(capabilities):
    characteristics = HarnessModelCharacteristics(capabilities=frozenset(capabilities))
    expected = sorted(capability.value for capability in capabilities)

    assert characteristics.model_dump()["capabilities"] == frozenset(capabilities)
    assert isinstance(characteristics.model_dump()["capabilities"], frozenset)
    assert characteristics.model_dump(mode="json")["capabilities"] == expected
    assert json.loads(characteristics.model_dump_json())["capabilities"] == expected
    restored = HarnessModelCharacteristics.model_validate_json(characteristics.model_dump_json())
    assert restored == characteristics
    assert restored.model_dump_json() == characteristics.model_dump_json()
    spec = AgentSpec(model_characteristics=characteristics)
    assert spec.model_dump(mode="json")["model_characteristics"]["capabilities"] == expected


def test_capabilities_json_order_is_independent_of_set_iteration_order():
    # Set iteration order follows the hash seed; an explicitly unsorted input makes this check deterministic.
    unsorted = HarnessModelCharacteristics.model_construct(capabilities=list(reversed(ModelCapability)))

    assert json.loads(unsorted.model_dump_json())["capabilities"] == sorted(
        capability.value for capability in ModelCapability
    )
