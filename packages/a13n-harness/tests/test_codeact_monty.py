"""Exercise explicit JSON conversion across Harness runner and persistence boundaries."""

import json
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
        from dataclasses import dataclass
        from decimal import Decimal

        @dataclass
        class Item:
            price: str
            quantity: int = 2

            def total(self):
                return Decimal(self.price) * self.quantity

        item = Item("0.10")
        value = {"total": str(item.total())}
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
    expected = {"total": "0.20"}
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


async def test_program_supports_module_dataclasses_annotations_and_initialization(tmp_path: Path) -> None:
    source = dedent("""\
        from dataclasses import dataclass
        from decimal import Decimal
        from datetime import date
        import fnmatch
        import json

        @dataclass
        class Report:
            name: str
            total: str

        def build(price: str = str(Decimal("0.10"))) -> Report:
            return Report("report.csv", price)

        def to_json(report: Report) -> dict[str, str]:
            return {"name": report.name, "total": report.total}

        initialized = await load(key="initializations")
        await store(key="initializations", value=initialized + 1)
        report = build()

        async def main(inputs: dict[str, str]) -> dict[str, str]:
            assert fnmatch.fnmatchcase(report.name, "*.csv")
            assert date.fromtimestamp(0).isoformat() == "1970-01-01"
            encoded = json.dumps(report, default=to_json)
            return json.loads(encoded)
    """)
    (tmp_path / "report.codeact.py").write_text(source)
    _, seen, _ = await _run(
        [
            _code('await store(key="initializations", value=0)'),
            ("run_program", {"path": "report.codeact.py"}),
            ("run_program", {"path": "report.codeact.py"}),
            _code('await load(key="initializations")'),
        ],
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )
    assert [part.content for part in seen[1:]] == [
        {"name": "report.csv", "total": "0.10"},
        {"name": "report.csv", "total": "0.10"},
        2,
    ]


async def test_program_initialization_effect_survives_failure_without_resetting_inline(tmp_path: Path) -> None:
    (tmp_path / "failed.codeact.py").write_text(
        'await store(key="saved", value=7)\n'
        'raise ValueError("private-init-value")\n'
        "async def main(inputs):\n    return 42\n"
    )
    _, seen, _ = await _run(
        [
            _code("previous = 3"),
            ("run_program", {"path": "failed.codeact.py"}),
            _code('previous + await load(key="saved")'),
        ],
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )
    payload = json.loads(seen[1].content)
    assert payload["codeact"]["side_effect_uncertain"] is True
    assert payload["error"]["inline_state"] == "unchanged"
    assert payload["error"]["location"]["line"] == 2
    assert "private-init-value" not in seen[1].content
    assert seen[2].content == 10


@pytest.mark.parametrize(
    "source,exception_type,line",
    [
        ("x =", "SyntaxError", 1),
        ("value = 1\nvalue / 0", "ZeroDivisionError", 2),
        ("missing_value + 1", "NameError", 1),
        ('raise ValueError("private-error-value")', "ValueError", 1),
    ],
)
async def test_failure_reports_exception_and_location_without_exception_values(source, exception_type, line) -> None:
    _, seen, _ = await _run([_code(source)])
    payload = json.loads(seen[0].content)
    assert payload["error"]["exception_type"] == exception_type
    assert payload["error"]["location"]["line"] == line
    assert payload["error"]["location"]["column"] > 0
    assert payload["error"]["inline_state"] == "reset"
    assert "private-error-value" not in seen[0].content
    assert "missing_value" not in seen[0].content
    assert len(seen[0].content.encode("utf-8")) <= 4096


async def test_nested_failure_diagnostics_separate_arguments_execution_and_output() -> None:
    def checked(values: list[int]) -> list[int]:
        return values

    def fails() -> None:
        raise ValueError("private-host-error")

    def invalid_output() -> Any:
        return object()

    _, seen, _ = await _run(
        [_code('await checked(values=["private-input"])'), _code("await fails()"), _code("await invalid_output()")],
        capabilities=(_codeact_tools(checked, fails, invalid_output, allowed=("checked", "fails", "invalid_output")),),
    )
    failures = [json.loads(part.content) for part in seen]
    arguments, execution, output = [payload["error"]["nested_errors"][0] for payload in failures]
    assert arguments["boundary"] == "arguments"
    assert "values:" in arguments["message"]
    assert "int_parsing" in arguments["message"]
    assert failures[0]["codeact"]["tool_call_count"] == 0
    assert execution["boundary"] == "tool_execution"
    assert execution["type"] == "ValueError"
    assert output["boundary"] == "tool_output"
    assert output["type"] == "TypeError"
    assert all("private-" not in part.content for part in seen)


@pytest.mark.parametrize("catch_error", [False, True])
@pytest.mark.parametrize(
    "call",
    [
        'await checked(values={"private-key-secret": "private-value"})',
        'await checked(values={}, **{"private-argument": 1})',
    ],
)
async def test_validation_diagnostics_omit_caller_controlled_locations(call: str, catch_error: bool) -> None:
    invoked = []

    def checked(values: dict[str, int]) -> dict[str, int]:
        invoked.append(True)
        return values

    source = f"try:\n    {call}\nexcept TypeError:\n    pass\n1 / 0" if catch_error else call
    _, seen, _ = await _run([_code(source)], capabilities=(_codeact_tools(checked, allowed=("checked",)),))
    payload = json.loads(seen[0].content)
    error = payload["error"]["nested_errors"][0]
    assert error["boundary"] == "arguments"
    assert "validation failed" in error["message"]
    assert "private-" not in seen[0].content
    assert payload["codeact"]["tool_call_count"] == 0
    assert invoked == []
    if catch_error:
        assert payload["error"]["exception_type"] == "ZeroDivisionError"


async def test_final_output_failure_is_labeled_and_resets_bindings() -> None:
    _, seen, _ = await _run([_code('from decimal import Decimal\nvalue = 3\nDecimal("0.1")'), _code("value")])
    assert json.loads(seen[0].content)["error"]["boundary"] == "final_output"
    assert json.loads(seen[1].content)["error"]["exception_type"] == "NameError"


async def test_awaited_host_call_returns_its_completed_result() -> None:
    invoked = []

    async def record() -> int:
        invoked.append(True)
        return 7

    _, seen, _ = await _run([_code("await record()")], capabilities=(_codeact_tools(record, allowed=("record",)),))
    assert seen[0].content == 7
    assert invoked == [True]


async def test_inplace_list_and_deque_operations_preserve_aliases_across_feeds() -> None:
    _, seen, _ = await _run(
        [
            _code("from collections import deque\na = [1]\nb = a\nd = deque([2])\ne = d\n0"),
            _code("a *= 2\nd *= 2\na += [3]\nd += [4]\n[b, list(e), a is b, d is e]"),
        ]
    )
    assert seen[1].content == [[1, 1, 3], [2, 2, 4], True, True]
