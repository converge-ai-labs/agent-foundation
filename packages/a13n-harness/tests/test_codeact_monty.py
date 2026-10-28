"""Exercise Monty's supported Python features through the actual Harness runners."""

from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from a13n_harness import RunBindings
from pydantic_ai.messages import ToolReturnPart

from .test_codeact import _codeact_tools, _local_environment
from .test_codeact_state import _code, _run, _values

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
async def test_monty_data_processing_crosses_tools_as_explicit_json(runner: str, tmp_path: Path) -> None:
    received: list[Any] = []

    def echo(value: Any) -> Any:
        received.append(value)
        return value

    source = dedent("""\
        import csv
        import hashlib
        import tomllib
        from bisect import bisect_left
        from dataclasses import dataclass
        from decimal import Decimal

        @dataclass
        class Item:
            price: str
            quantity: int = 2

            def total(self):
                return Decimal(self.price) * self.quantity

        rows = list(csv.DictReader(["price", "0.10"]))
        item = Item(rows[0]["price"])
        config = tomllib.loads('name = "report"')
        values = [1, 4]
        values[1:1] = [2, 3]
        match config:
            case {"name": name}:
                label = name
        value = {
            "total": str(item.total()),
            "name": label,
            "position": bisect_left(values, 3),
            "digest": hashlib.sha256(b"abc").hexdigest(),
        }
        value = await echo(value=value)
        await store(key="report", value=value)
    """)
    if runner == "run_program":
        (tmp_path / "report.codeact.py").write_text(
            "async def main(inputs):\n"
            + "".join(f"    {line}\n" for line in source.splitlines())
            + "    return value\n"
        )
        calls = [(runner, {"path": "report.codeact.py"})]
    else:
        calls = [_code(source + "value")]
    result, seen, _ = await _run(
        [*calls, _code('await load(key="report")')],
        capabilities=(_codeact_tools(echo, allowed=("echo",)),),
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )
    expected = {
        "total": "0.20",
        "name": "report",
        "position": 2,
        "digest": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    }
    assert received == [expected], [part.content for part in seen]
    assert [part.content for part in seen] == [expected, expected]
    assert _values(result.state) == {"report": expected}


@pytest.mark.parametrize("boundary", ["output", "argument", "store"])
async def test_decimal_requires_explicit_json_conversion(boundary: str) -> None:
    invoked: list[Any] = []

    def echo(value: Any) -> Any:
        invoked.append(value)
        return value

    expression = {
        "output": "amount",
        "argument": "await echo(value=amount)",
        "store": 'await store(key="invalid", value=amount)',
    }[boundary]
    result, seen, _ = await _run(
        [
            _code('from decimal import Decimal\namount = Decimal("0.1")\nstr(amount)'),
            _code('await store(key="saved", value=7)\n' + expression),
            _code("amount"),
            _code('await load(key="saved")'),
        ],
        capabilities=(_codeact_tools(echo, allowed=("echo",)),),
    )
    assert seen[0].content == "0.1"
    for part in seen[1:3]:
        assert isinstance(part, ToolReturnPart) and part.outcome == "failed"
    assert invoked == []
    assert seen[3].content == 7
    assert _values(result.state) == {"saved": 7}
