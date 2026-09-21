from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import combinations, permutations

import pytest
from a13n_harness import AgentSpec, HarnessModelCharacteristics, ModelCapability


@pytest.mark.parametrize(
    "capabilities",
    [
        ordering
        for size in range(4)
        for subset in combinations(ModelCapability, size)
        for ordering in permutations(subset)
    ],
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


def test_capabilities_json_is_stable_across_hash_seeds():
    script = """
from a13n_harness import HarnessModelCharacteristics, ModelCapability
print(HarnessModelCharacteristics(capabilities=frozenset(ModelCapability)).model_dump_json())
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        for seed in (0, 1, 2)
    ]
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])["capabilities"] == sorted(capability.value for capability in ModelCapability)
