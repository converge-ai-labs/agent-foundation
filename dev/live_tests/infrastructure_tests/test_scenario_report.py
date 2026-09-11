"""Report outcome separation and honest performance gates."""

import json

import pytest

from ..performance.scenario_report import HEADERS, generate, markdown
from ..performance.scenario_report_rows import Row, rows, verdict


def cell(*, n=128, status="verified", budgets=None):
    return {
        "scenario": "queue.enqueue_full",
        "concurrency": 8,
        "peak_inflight": 8,
        "configuration": {"capacity": 256},
        "status": status,
        "errors": [],
        "budgets_ms": budgets or {},
        "summary": {"expected_conflict": {"n": n, "p50_ms": 0, "p95_ms": 20, "p99_ms": 30}},
    }


def document(*cells):
    return {
        "schema": "bounded-operations-v1",
        "status": "verified",
        "environment": {"s3": "owned RustFS"},
        "cells": list(cells),
    }


@pytest.mark.parametrize(
    "n,budgets,expected",
    [
        (128, {}, "Not evaluated"),
        (99, {"p95": 20}, "Not evaluated"),
        (100, {"p95": 20}, "Pass"),
        (100, {"p95": 19}, "Fail"),
        (999, {"p99": 30}, "Not evaluated"),
        (1000, {"p99": 30}, "Pass"),
        (1000, {"p99": 29}, "Fail"),
        (100, {"p95": 20, "p99": 30}, "Not evaluated"),
        (100, {"p95": 19, "p99": 30}, "Fail"),
    ],
)
def test_eligible_latency_verdicts(n, budgets, expected):
    selected = cell(n=n, budgets=budgets)
    assert verdict(selected, selected["summary"]["expected_conflict"]).startswith(expected)


def test_success_rejection_and_empty_failure_remain_separate():
    selected = cell()
    selected["summary"]["success"] = {"n": 1, "p50_ms": 7, "p95_ms": 7, "p99_ms": 7}
    empty = cell(status="failed")
    empty["summary"] = {}
    result = rows(document(selected, empty))
    assert len(result) == 3
    assert result[0].p50 == 0 and "N=128" in result[0].configuration
    assert result[1].p50 == 7 and "N=1;" in result[1].configuration
    assert result[2].p50 is None and result[2].result.startswith("Fail")


def test_one_six_column_table_escapes_values_and_preserves_missing_zero():
    text = markdown([Row("A|B", "C\nD", 0, None, 12, "Not evaluated")])
    assert len(HEADERS) == 6
    assert text.count("| Scenario |") == 1
    assert "A\\|B" in text and "C D" in text and "0.00 | — | 12.00" in text


def test_explicit_inputs_and_invalid_legacy_report_do_not_overwrite(tmp_path):
    source = tmp_path / "operations.json"
    source.write_text(json.dumps(document(cell())))
    output = tmp_path / "report.md"
    assert len(generate([source, source], output)) == 1
    before = output.read_text()
    source.write_text(json.dumps({"scenarios": {"many_threads": {}}}))
    with pytest.raises(ValueError, match="obsolete E2E"):
        generate([source], output)
    assert output.read_text() == before
    assert output.with_suffix(".csv").exists()


def test_failure_never_passes_after_retaining_valid_samples():
    selected = cell(n=1000, status="failed", budgets={"p99": 100})
    assert rows(document(selected))[0].result.startswith("Fail")
