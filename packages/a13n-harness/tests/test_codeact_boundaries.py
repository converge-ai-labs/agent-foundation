"""Value, state, and authoring boundaries independent of the interpreter scheduler."""

import asyncio
import json
import tracemalloc
from copy import deepcopy
from typing import Any

import pytest
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.programs import ProgramValidationError, validate_program_source
from a13n_harness.codeact.state import CodeActStateOwner, CodeActStoredValues
from a13n_harness.codeact.values import JsonLimitExceeded, bounded_json_size


@pytest.mark.parametrize(
    "value", [None, True, -12, 1.25, "", '汉字\n"\\' * 1025, [], {}, [1, {"x": [False, None]}], (1, 2)]
)
def test_json_size_matches_compact_encoding_at_exact_limit(value: Any) -> None:
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert bounded_json_size(value, size) == size
    with pytest.raises(JsonLimitExceeded):
        bounded_json_size(value, size - 1)


@pytest.mark.parametrize("kind", ["list", "map", "string"])
def test_oversized_values_are_rejected_without_materializing_children(kind: str) -> None:
    value = {
        "list": lambda: [None] * 100_000,
        "map": lambda: dict.fromkeys(map(str, range(100_000))),
        "string": lambda: "x" * 1_000_000,
    }[kind]()
    tracemalloc.start()
    try:
        with pytest.raises(JsonLimitExceeded):
            bounded_json_size(value, 1024)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: 2}, b"bytes", object(), "\ud800"])
def test_non_json_values_fail_closed(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        bounded_json_size(value, 1024)


def test_json_cycles_aliases_and_deep_values() -> None:
    shared = [1]
    assert bounded_json_size([shared, shared], 9) == 9
    shared.append(shared)
    with pytest.raises(ValueError, match="Circular"):
        bounded_json_size(shared, 1024)
    value: Any = None
    for _ in range(1500):
        value = [value]
    assert bounded_json_size(value, 3004) == 3004


@pytest.mark.anyio
async def test_state_owner_copies_only_selected_values_and_keeps_snapshots_detached(monkeypatch) -> None:
    from a13n_harness.codeact import state as state_module

    owner = CodeActStateOwner()
    initial = CodeActStoredValues(values={"large": [None] * 10_000, "small": [1]})
    await owner.initialize("thread-1", initial)
    copies = []

    def track(value):
        copies.append(value)
        return deepcopy(value)

    monkeypatch.setattr(state_module, "deepcopy", track)
    assert await owner.keys() == ["large", "small"]
    assert copies == []
    loaded = await owner.load("small")
    loaded.append(2)
    assert copies == [[1]]
    before = await owner.snapshot()
    new_value = [3]
    await owner.store("new", new_value, CodeActConfig())
    assert copies == [[1], [3]]
    new_value.append(4)
    assert await owner.load("new") == [3]
    assert "new" not in before.values
    before.values["small"].append(5)
    assert await owner.load("small") == [1]
    initial.values.clear()
    assert await owner.keys() == ["large", "new", "small"]
    with pytest.raises(ValueError, match="another Thread"):
        await owner.initialize("thread-2", CodeActStoredValues())


@pytest.mark.anyio
async def test_state_owner_rejected_and_concurrent_updates_preserve_previous_values() -> None:
    owner = CodeActStateOwner()
    await owner.initialize("thread", CodeActStoredValues(values={"saved": None}))
    config = CodeActConfig(max_state_bytes=128, max_state_entries=4)
    await asyncio.gather(owner.store("a", [1], config), owner.store("b", [2], config))
    with pytest.raises(ValueError, match="max_state_bytes"):
        await owner.store("saved", "x" * 1000, config)
    assert (await owner.snapshot()).values == {"saved": None, "a": [1], "b": [2]}
    assert await owner.load("saved") is None
    with pytest.raises(KeyError):
        await owner.load("missing")


@pytest.mark.parametrize(
    "source",
    [
        "def main(inputs): pass",
        "async def main(other): pass",
        "async def main(inputs=None): pass",
        "async def main(inputs, extra): pass",
        "@decorator\nasync def main(inputs): pass",
        "async def main(inputs): pass\nasync def main(inputs): pass",
        "__a13n_codeact_inputs__ = {}\nasync def main(inputs): pass",
        "class __a13n_codeact_result__: pass\nasync def main(inputs): pass",
        "match 1:\n    case __a13n_codeact_inputs__: pass\nasync def main(inputs): pass",
        "match []:\n    case [*__a13n_codeact_result__]: pass\nasync def main(inputs): pass",
        "match {}:\n    case {**__a13n_codeact_inputs__}: pass\nasync def main(inputs): pass",
    ],
)
def test_program_entrypoint_and_runtime_names_remain_checked(source: str) -> None:
    with pytest.raises(ProgramValidationError):
        validate_program_source(source)


def test_program_syntax_error_retains_location_without_source() -> None:
    with pytest.raises(ProgramValidationError) as raised:
        validate_program_source("secret =\nasync def main(inputs): pass")
    assert raised.value.line == 1
    assert raised.value.column is not None
    assert "secret" not in str(raised.value)
